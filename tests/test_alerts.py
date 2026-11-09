"""價格提醒（#28）：提醒資料表存取、觸發判斷與提醒頁。"""

import math
import sqlite3

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import auth
from alerts import describe_condition, evaluate_alert, evaluate_alerts
from database import StockDatabase


@pytest.fixture
def database(tmp_path):
    return StockDatabase(tmp_path / "alerts.sqlite3")


def frame(close=100.0, volume=3000, volume_ma_20=1000.0):
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-11-05", "2026-11-06"]),
        "close": [90.0, close], "trade_volume": [1000, volume], "volume_ma_20": [1000.0, volume_ma_20],
    })


def alert(kind, threshold, stock_id="2330"):
    return {"id": 1, "stock_id": stock_id, "kind": kind, "threshold": threshold}


# ---------- 資料表 ----------

def test_alerts_are_isolated_between_accounts(database):
    a = database.upsert_user("google", "subject-a")
    b = database.upsert_user("google", "subject-b")
    alert_a = database.add_alert(a, "2330", kind="price_below", threshold=500)
    alert_b = database.add_alert(b, "2330", kind="volume_above", threshold=2)

    assert database.list_alerts(a) == [
        {"id": alert_a, "stock_id": "2330", "kind": "price_below", "threshold": 500.0}
    ]
    assert [row["id"] for row in database.list_alerts(b)] == [alert_b]
    assert not database.remove_alert(a, alert_b)
    assert database.remove_alert(a, alert_a)
    assert database.list_alerts(a) == []
    assert [row["id"] for row in database.list_alerts(b)] == [alert_b]


@pytest.mark.parametrize("kind, threshold", [
    ("price_crash", 100), ("price_above", 0), ("price_above", -1),
    ("price_above", math.nan), ("price_above", math.inf), ("price_above", True), ("price_above", "100"),
])
def test_invalid_alerts_are_rejected(database, kind, threshold):
    user = database.upsert_user("google", "subject-a")
    with pytest.raises(ValueError):
        database.add_alert(user, "2330", kind=kind, threshold=threshold)
    assert database.list_alerts(user) == []


def test_alert_requires_registered_owner(database):
    with pytest.raises(sqlite3.IntegrityError):
        database.add_alert(999, "2330", kind="price_above", threshold=100)


# ---------- 觸發判斷 ----------

@pytest.mark.parametrize("kind, threshold, close, triggered", [
    ("price_below", 100, 99.9, True),
    ("price_below", 100, 100, False),   # 等於門檻不算跌破
    ("price_below", 100, 101, False),
    ("price_above", 100, 100.5, True),
    ("price_above", 100, 100, False),   # 等於門檻不算突破
    ("price_above", 100, 99, False),
])
def test_price_alerts_compare_latest_close(kind, threshold, close, triggered):
    result = evaluate_alert(alert(kind, threshold), frame(close=close))
    assert result["triggered"] is triggered
    assert str(result["date"]) == "2026-11-06"
    assert result["actual"] == f"收盤價 {close:,.2f} 元"


@pytest.mark.parametrize("volume, multiple, triggered", [
    (3000, 2, True), (2000, 2, False), (1999, 2, False), (1600, 1.5, True),
])
def test_volume_alert_uses_multiple_of_twenty_day_average(volume, multiple, triggered):
    result = evaluate_alert(alert("volume_above", multiple), frame(volume=volume, volume_ma_20=1000))
    assert result["triggered"] is triggered
    assert f"{volume / 1000:.2f} 倍" in result["actual"]


def test_volume_alert_without_average_is_not_judged():
    result = evaluate_alert(alert("volume_above", 2), frame(volume_ma_20=math.nan))
    assert result["triggered"] is None
    assert result["note"] == "20 日均量資料不足"


def test_alert_for_stock_without_data_is_not_judged():
    [result] = evaluate_alerts([alert("price_above", 100, stock_id="9999")], {"2330 台積電": frame()})
    assert result["triggered"] is None
    assert result["note"] == "查無行情資料"
    assert result["label"] == "9999"


def test_conditions_show_threshold():
    assert describe_condition("price_below", 1234.5) == "收盤價跌破 1,234.50 元"
    assert describe_condition("price_above", 50) == "收盤價突破 50.00 元"
    assert describe_condition("volume_above", 2.5) == "成交量超過 20 日均量 2.5 倍"


# ---------- 頁面 ----------

def _alerts_page(db_path):
    from alerts import render_alerts
    from database import StockDatabase

    database = StockDatabase(db_path)
    render_alerts({"2330 台積電": database.query_prices("2330")}, database=database)


@pytest.fixture
def page(monkeypatch, tmp_path):
    path = tmp_path / "page.sqlite3"
    monkeypatch.setenv("STOCK_DB_PATH", str(path))
    monkeypatch.delenv("STOCK_ENABLE_TEST_LOGIN", raising=False)
    monkeypatch.setattr(auth.st, "user", {})
    database = StockDatabase(path)
    closes = [100.0] * 24 + [120.0]
    database.upsert_prices(pd.DataFrame({
        "stock_id": "2330", "stock_name": "台積電",
        "date": pd.bdate_range("2026-10-01", periods=25),
        "open": closes, "high": closes, "low": closes, "close": closes,
        "trade_volume": [1_000_000] * 24 + [5_000_000],
    }))
    app = AppTest.from_function(_alerts_page, kwargs={"db_path": str(path)}, default_timeout=30)
    app.session_state[auth.TEST_SESSION_KEY] = True
    return database, app


def test_triggered_alert_shows_stock_condition_and_actual_value(page):
    database, app = page
    app.run()
    assert not app.exception
    # 價位預設帶入最新收盤價
    assert app.number_input(key="alert_price_price_below_2330").value == 120.0

    app.radio(key="alert_kind").set_value("price_above").run()
    app.number_input(key="alert_price_price_above_2330").set_value(110.0)
    app.button(key="alert_submit").click().run()
    assert not app.exception
    assert app.success[0].value == "已設定 2330 台積電：收盤價突破 110.00 元。"

    warning = app.warning[0].value
    assert "2330 台積電" in warning
    assert "收盤價突破 110.00 元" in warning
    assert "收盤價 120.00 元" in warning
    table = app.dataframe[0].value
    assert table["提醒條件（門檻）"].tolist() == ["收盤價突破 110.00 元"]
    assert table["狀態"].tolist() == ["🔔 已觸發"]


def test_volume_alert_and_untriggered_alert(page):
    database, app = page
    app.run()
    app.radio(key="alert_kind").set_value("volume_above").run()
    app.button(key="alert_submit").click().run()   # 預設 2 倍；最新量 5,000 張，均量 1,200 張
    app.radio(key="alert_kind").set_value("price_below").run()
    app.button(key="alert_submit").click().run()   # 預設 120 元，收盤 120 不算跌破
    assert not app.exception

    table = app.dataframe[0].value
    assert table["狀態"].tolist() == ["🔔 已觸發", "未觸發"]
    assert "成交量 5,000 張，為 20 日均量 1,200 張的 4.17 倍" in app.warning[0].value

    user_id = database.upsert_user("local_test", "test")
    app.button(key="alert_remove").click().run()
    assert len(database.list_alerts(user_id)) == 1
