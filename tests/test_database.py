"""資料庫驗收：隔離的暫存資料庫，不觸碰正式資料。"""

import sqlite3
from pathlib import Path
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from database import StockDatabase, get_database_path
from data_processor import process_data

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def database(tmp_path):
    return StockDatabase(tmp_path / "test.sqlite3")


def prices(stock_id="2327", count=65):
    close = [10 * (i + 1) for i in range(count)]
    return pd.DataFrame({
        "stock_id": stock_id, "stock_name": "測試",
        "date": pd.bdate_range("2026-01-01", periods=count),
        "open": close, "high": close, "low": close, "close": close,
        "trade_volume": [100 * (i + 1) for i in range(count)],
    })


def test_import_bundled_csv_is_idempotent_and_matches_all_rows(database):
    for filename in ("2327.csv", "2492.csv"):
        path = ROOT / filename
        original = pd.read_csv(path, encoding="utf-8-sig", dtype={"stock_id": str})
        assert database.import_csv(path) == len(original)
        database.import_csv(path)
        result = database.query_prices(path.stem, indicators=False)
        assert len(result) == len(original)
        assert result["date"].dt.strftime("%Y-%m-%d").tolist() == original["date"].tolist()
        for column in ("open", "high", "low", "close", "trade_volume"):
            assert result[column].tolist() == original[column].tolist()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM update_logs").fetchone()[0] == 4


def test_upsert_keeps_stocks_separate_and_seed_does_not_overwrite(database):
    first = prices(count=1)
    database.upsert_prices(pd.concat([first, prices("2492", count=1)]))
    updated = first.assign(open=55, high=55, low=55, close=55)
    database.upsert_prices(updated)
    assert database.query_prices("2327")["close"].tolist() == [55]
    assert database.query_prices("2492")["close"].tolist() == [10]
    assert database.upsert_prices(first, only_if_empty=True) == 0
    assert database.query_prices("2327")["close"].tolist() == [55]


def test_full_windows_and_inclusive_query_use_previous_trading_days(database):
    raw = prices()
    database.upsert_prices(raw)
    all_days = database.query_prices("2327")
    for column, window in (("ma_5", 5), ("ma_20", 20), ("ma_60", 60), ("volume_ma_20", 20)):
        assert all_days[column].iloc[:window - 1].isna().all()
        scale = 100 if column.startswith("volume") else 10
        assert all_days[column].iloc[window - 1] == scale * (window + 1) / 2
    start, end = raw["date"].iloc[59], raw["date"].iloc[61]
    query = database.query_prices("2327", start.date(), end.date())
    assert len(query) == 3
    for column in ("ma_5", "ma_20", "ma_60", "volume_ma_20", "price_change"):
        assert query[column].tolist() == all_days[column].iloc[59:62].tolist()
    assert database.query_prices("9999", start.date(), end.date()).empty
    with pytest.raises(ValueError):
        database.query_prices("2327", end.date(), start.date())


@pytest.mark.parametrize("column,value", [
    ("date", "2026-02-30"), ("stock_id", "bad id"), ("close", float("inf")),
    ("open", None), ("trade_volume", -1), ("trade_volume", 1.5),
    ("trade_volume", 2**63), ("low", 999), ("high", "--"),
])
def test_invalid_batch_does_not_write_any_rows_or_log(database, column, value):
    valid = prices(count=1)
    invalid = valid.assign(stock_id="2492").astype({column: object})
    invalid.loc[0, column] = value
    with pytest.raises(ValueError):
        database.upsert_prices(pd.concat([valid, invalid]))
    with database.connect() as connection:
        for table in ("stocks", "daily_prices", "update_logs"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_sql_failure_rolls_back_prices_stock_metadata_and_log(database):
    database.upsert_prices(prices(count=1))
    with database.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_log BEFORE INSERT ON update_logs
            BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        database.upsert_prices(prices("2492", count=3))
    assert database.query_prices("2492").empty
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM update_logs").fetchone()[0] == 1


def test_multi_stock_processing_never_mixes_windows():
    raw = pd.concat([prices(count=5), prices("2492", count=5).assign(close=100, high=100)])
    result = process_data(raw.sample(frac=1, random_state=1))
    assert len(result) == 10
    assert result.groupby("stock_id")["ma_5"].last().to_dict() == {"2327": 30, "2492": 100}
    assert result.groupby("stock_id")["previous_close"].first().to_dict() == {"2327": 10, "2492": 100}


def test_azure_defaults_and_explicit_path_are_isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("STOCK_DB_PATH", raising=False)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "stock")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("WEBSITE_SLOT_NAME", raising=False)
    production = get_database_path()
    monkeypatch.setenv("WEBSITE_SLOT_NAME", "test")
    test = get_database_path()
    assert production == tmp_path / "data" / "stock" / "production.sqlite3"
    assert test == tmp_path / "data" / "stock" / "test.sqlite3"
    monkeypatch.setenv("STOCK_DB_PATH", str(tmp_path / "custom.sqlite3"))
    assert get_database_path() == tmp_path / "custom.sqlite3"
    monkeypatch.setenv("STOCK_DB_PATH", "relative.sqlite3")
    with pytest.raises(ValueError):
        get_database_path()


@pytest.mark.parametrize("stdio_encoding", ["utf-8", "cp1252"])
def test_local_restart_and_replaced_deployment_preserve_database(tmp_path, monkeypatch, stdio_encoding):
    # 本機模擬；不代表已在 Azure 執行重啟／部署驗收。
    monkeypatch.setenv("PYTHONIOENCODING", stdio_encoding)
    deployment = tmp_path / "site" / "wwwroot"
    deployment.mkdir(parents=True)
    path = tmp_path / "data" / "股票.sqlite3"
    for filename in ("database.py", "data_processor.py"):
        shutil.copy(ROOT / filename, deployment)
    imported = subprocess.run([
        sys.executable, str(deployment / "database.py"), "--db", str(path),
        "--import-csv", str(ROOT / "2327.csv"),
    ], capture_output=True)
    assert imported.returncode == 0, imported.stderr.decode("utf-8", errors="replace")
    assert str(path) in imported.stdout.decode("utf-8")
    before = StockDatabase(path).query_prices("2327")
    shutil.rmtree(deployment)
    deployment.mkdir()
    for filename in ("database.py", "data_processor.py"):
        shutil.copy(ROOT / filename, deployment)
    subprocess.run([
        sys.executable, "-c",
        "from database import StockDatabase; import sys; "
        "assert len(StockDatabase(sys.argv[1]).query_prices('2327')) == int(sys.argv[2])",
        str(path), str(len(before)),
    ], check=True, cwd=deployment, capture_output=True)
    pd.testing.assert_frame_equal(before, StockDatabase(path).query_prices("2327"))
