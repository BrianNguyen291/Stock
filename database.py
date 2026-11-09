"""SQLite 股票儲存：原子批次寫入、CSV 匯入與含前期指標的日期查詢。

    python database.py --import-csv 2327.csv 2492.csv
"""

import argparse
from contextlib import contextmanager
from datetime import date
import math
import os
from pathlib import Path
import re
import sqlite3
import sys

import pandas as pd

from data_processor import calculate_indicators

PRICE_COLUMNS = (
    "trade_volume", "trade_value", "open", "high", "low", "close",
    "change", "transaction_count", "note",
)
REQUIRED = ("date", "stock_id", "open", "high", "low", "close", "trade_volume")


def get_database_path() -> Path:
    """Azure 使用 HOME/data，避開部署目錄；路徑可用 STOCK_DB_PATH 覆寫。"""
    configured = os.environ.get("STOCK_DB_PATH")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise ValueError("STOCK_DB_PATH 必須為絕對路徑")
        return path
    if os.environ.get("WEBSITE_SITE_NAME"):
        home = os.environ.get("HOME")
        if not home:
            raise ValueError("Azure 必須設定 HOME 或 STOCK_DB_PATH")
        slot = os.environ.get("WEBSITE_SLOT_NAME", "production").lower()
        if not re.fullmatch(r"[a-z0-9_-]+", slot):
            raise ValueError("無效的 Azure slot 名稱")
        return Path(home) / "data" / "stock" / f"{slot}.sqlite3"
    return Path(__file__).resolve().parent / "data" / "stock.sqlite3"


def _date(value) -> str:
    text = str(value)
    # 不默默截掉時間或把無效日期變成缺值。
    if isinstance(value, (pd.Timestamp,)):
        if pd.isna(value) or value != value.normalize() or value.tzinfo is not None:
            raise ValueError("交易日期必須是無時區的日期")
        text = value.date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"無效交易日期：{value}")
    return date.fromisoformat(text).isoformat()


def _stock_id(value) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[0-9A-Za-z]{4,10}", value):
        raise ValueError(f"無效股票代號：{value}")
    return value


def _records(data: pd.DataFrame) -> list[dict]:
    missing = set(REQUIRED) - set(data.columns)
    if missing:
        raise ValueError(f"缺少必要欄位：{', '.join(sorted(missing))}")
    records = []
    for raw in data.to_dict("records"):
        row = {"stock_id": _stock_id(raw["stock_id"]), "date": _date(raw["date"])}
        for column in PRICE_COLUMNS:
            value = raw.get(column)
            if column == "note":
                row[column] = "" if pd.isna(value) else str(value)
                continue
            if value is None or pd.isna(value):
                if column in REQUIRED:
                    raise ValueError(f"{column} 不可為缺值")
                row[column] = None
                continue
            try:
                number = float(str(value).strip().replace(",", ""))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{column} 必須是有效數值") from exc
            if not math.isfinite(number) or (column != "change" and number < 0):
                raise ValueError(f"{column} 必須是有限且非負數值")
            if column in ("trade_volume", "transaction_count"):
                if not number.is_integer() or number > 2**63 - 1:
                    raise ValueError(f"{column} 必須是 SQLite 可儲存的非負整數")
                row[column] = int(number)
            else:
                row[column] = number
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]) or row["low"] > row["high"]:
            raise ValueError("開高低收價格範圍不一致")
        name = raw.get("stock_name")
        row["stock_name"] = "" if pd.isna(name) else str(name).strip()
        records.append(row)
    return records


class StockDatabase:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else get_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS stocks (
                    stock_id TEXT PRIMARY KEY,
                    stock_name TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS daily_prices (
                    stock_id TEXT NOT NULL REFERENCES stocks(stock_id),
                    date TEXT NOT NULL,
                    trade_volume INTEGER NOT NULL CHECK(trade_volume >= 0),
                    trade_value REAL CHECK(trade_value >= 0),
                    open REAL NOT NULL CHECK(open >= 0),
                    high REAL NOT NULL CHECK(high >= open AND high >= close),
                    low REAL NOT NULL CHECK(low <= open AND low <= close AND low >= 0),
                    close REAL NOT NULL CHECK(close >= 0),
                    change REAL,
                    transaction_count INTEGER CHECK(transaction_count >= 0),
                    note TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (stock_id, date)
                );
                CREATE TABLE IF NOT EXISTS auto_update_stocks (
                    stock_id TEXT PRIMARY KEY REFERENCES stocks(stock_id),
                    registered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE TABLE IF NOT EXISTS stock_update_runs (
                    id INTEGER PRIMARY KEY,
                    stock_id TEXT NOT NULL,
                    checked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    status TEXT NOT NULL,
                    written_rows INTEGER NOT NULL DEFAULT 0,
                    latest_source_date TEXT,
                    latest_database_date TEXT,
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_stock_update_runs_stock
                    ON stock_update_runs(stock_id, id);
                CREATE TABLE IF NOT EXISTS update_logs (
                    id INTEGER PRIMARY KEY,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    source TEXT NOT NULL,
                    row_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL CHECK(provider IN ('google', 'local_test')),
                    subject TEXT NOT NULL,
                    email TEXT,
                    display_name TEXT,
                    UNIQUE (provider, subject)
                );
                CREATE TABLE IF NOT EXISTS watchlists (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    stock_id TEXT NOT NULL,
                    PRIMARY KEY (user_id, stock_id)
                );
                CREATE TABLE IF NOT EXISTS holdings (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    stock_id TEXT NOT NULL,
                    purchase_price REAL NOT NULL CHECK(purchase_price > 0),
                    shares INTEGER NOT NULL CHECK(shares > 0),
                    purchase_date TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS holdings_by_user ON holdings(user_id);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    stock_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('price_above', 'price_below', 'volume_above')),
                    threshold REAL NOT NULL CHECK(threshold > 0)
                );
                CREATE INDEX IF NOT EXISTS alerts_by_user ON alerts(user_id);
            """)

    @contextmanager
    def connect(self):
        # 使用預設 rollback journal，避免 Azure 網路磁碟不支援 WAL shared memory。
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def upsert_user(
        self, provider: str, subject: str, *, email: str | None = None, display_name: str | None = None
    ) -> int:
        """Register an authenticated identity and return its stable local ID."""
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("缺少帳號識別碼")
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO users(provider, subject, email, display_name) VALUES (?, ?, ?, ?)
                ON CONFLICT(provider, subject) DO UPDATE SET
                    email=excluded.email, display_name=excluded.display_name
                WHERE users.email IS NOT excluded.email
                   OR users.display_name IS NOT excluded.display_name
            """, (provider, subject, email, display_name))
            return connection.execute(
                "SELECT id FROM users WHERE provider = ? AND subject = ?", (provider, subject)
            ).fetchone()[0]

    # Personal data methods take the authenticated server-side ID from auth.current_user_id().
    def add_to_watchlist(self, user_id: int, stock_id: str) -> bool:
        with self.connect() as connection:
            result = connection.execute("""
                INSERT INTO watchlists(user_id, stock_id) VALUES (?, ?)
                ON CONFLICT(user_id, stock_id) DO NOTHING
            """, (user_id, _stock_id(stock_id)))
            return result.rowcount == 1

    def remove_from_watchlist(self, user_id: int, stock_id: str) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                "DELETE FROM watchlists WHERE user_id = ? AND stock_id = ?",
                (user_id, _stock_id(stock_id)),
            )
            return result.rowcount == 1

    def list_watchlist(self, user_id: int) -> list[str]:
        with self.connect() as connection:
            return [row[0] for row in connection.execute(
                "SELECT stock_id FROM watchlists WHERE user_id = ? ORDER BY stock_id",
                (user_id,),
            )]

    def add_holding(
        self, user_id: int, stock_id: str, *, purchase_price: float, shares: int,
        purchase_date: date | str,
    ) -> int:
        if isinstance(purchase_price, bool) or not math.isfinite(purchase_price) or purchase_price <= 0:
            raise ValueError("買進價必須是有限的正數")
        if type(shares) is not int or not 0 < shares <= 2**63 - 1:
            raise ValueError("股數必須是正整數")
        with self.connect() as connection:
            result = connection.execute("""
                INSERT INTO holdings(user_id, stock_id, purchase_price, shares, purchase_date)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, _stock_id(stock_id), purchase_price, shares, _date(purchase_date)))
            return result.lastrowid

    def remove_holding(self, user_id: int, holding_id: int) -> bool:
        with self.connect() as connection:
            result = connection.execute(
                "DELETE FROM holdings WHERE user_id = ? AND id = ?", (user_id, holding_id)
            )
            return result.rowcount == 1

    def list_holdings(self, user_id: int) -> list[dict]:
        with self.connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("""
                SELECT id, stock_id, purchase_price, shares, purchase_date
                FROM holdings WHERE user_id = ? ORDER BY purchase_date, id
            """, (user_id,))
            return [dict(row) for row in rows]

    def upsert_prices(self, data: pd.DataFrame, source: str = "api", *, only_if_empty: bool = False) -> int:
        """先驗證整批，再以單一交易寫入股票、股價及成功紀錄。"""
        records = _records(data)
        if not records:
            return 0
        columns = ("stock_id", "date", *PRICE_COLUMNS)
        updates = ", ".join(f"{column}=excluded.{column}" for column in PRICE_COLUMNS)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if only_if_empty:
                ids = {row["stock_id"] for row in records}
                existing = {stock_id for stock_id in ids if connection.execute(
                    "SELECT 1 FROM daily_prices WHERE stock_id=? LIMIT 1", (stock_id,)
                ).fetchone()}
                records = [row for row in records if row["stock_id"] not in existing]
            if not records:
                return 0
            connection.executemany("""
                INSERT INTO stocks(stock_id, stock_name) VALUES (:stock_id, :stock_name)
                ON CONFLICT(stock_id) DO UPDATE SET stock_name=excluded.stock_name
                WHERE excluded.stock_name != ''
            """, records)
            connection.executemany(
                f"INSERT INTO daily_prices ({', '.join(columns)}) VALUES "
                f"({', '.join(':' + column for column in columns)}) "
                f"ON CONFLICT(stock_id, date) DO UPDATE SET {updates}", records,
            )
            connection.execute("INSERT INTO update_logs(source, row_count) VALUES (?, ?)", (source, len(records)))
        return len(records)

    def register_auto_update_stock(self, stock_id: str) -> bool:
        """Track a valid TWSE lookup for subsequent scheduled updates.

        Only stocks with previously stored daily prices may be registered.
        Repeated lookups are idempotent; legacy CSV imports aren't enrolled.
        """
        stock_id = _stock_id(stock_id)
        with self.connect() as connection:
            found = connection.execute(
                "SELECT 1 FROM daily_prices WHERE stock_id=? LIMIT 1", (stock_id,)
            ).fetchone()
            if found is None:
                raise ValueError("股票尚無有效交易資料，不可加入自動更新清單")
            cursor = connection.execute("""
                INSERT INTO auto_update_stocks(stock_id) VALUES (?)
                ON CONFLICT(stock_id) DO NOTHING
            """, (stock_id,))
            return cursor.rowcount == 1

    def list_auto_update_stocks(self) -> list[str]:
        """Get persistent queried-stock IDs for the shared Azure WebJob."""
        with self.connect() as connection:
            return [row[0] for row in connection.execute(
                "SELECT stock_id FROM auto_update_stocks ORDER BY registered_at, stock_id"
            )]

    def record_stock_run(self, record: dict) -> None:
        """Save every WebJob result, including no changes and failures."""
        errors = record.get("errors") or []
        message = "; ".join(str(x.get("reason", "")) for x in errors if isinstance(x, dict))
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO stock_update_runs (
                    stock_id, status, written_rows, latest_source_date,
                    latest_database_date, error_message
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                record["stock_id"], record["status"], int(record.get("written_rows") or 0),
                record.get("latest_source_date"), record.get("latest_database_date"), message,
            ))

    def latest_stock_run(self, stock_id: str) -> dict | None:
        """Return latest automatic check for ticker, not a historic CSV import."""
        with self.connect() as connection:
            row = connection.execute("""
                SELECT checked_at, status, written_rows, latest_source_date,
                       latest_database_date, error_message
                FROM stock_update_runs WHERE stock_id = ? ORDER BY id DESC LIMIT 1
            """, (stock_id,)).fetchone()
        if row is None:
            return None
        keys = ("checked_at", "status", "written_rows", "latest_source_date",
                "latest_database_date", "error_message")
        return dict(zip(keys, row))

    def list_available_stocks(self) -> list[dict]:
        """Stock options come from actual persisted daily_prices, not deployed CSVs."""
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT s.stock_id, s.stock_name, MAX(p.date) AS latest_date
                FROM stocks s JOIN daily_prices p ON p.stock_id = s.stock_id
                GROUP BY s.stock_id, s.stock_name
                ORDER BY s.stock_id
            """).fetchall()
        return [
            {"stock_id": row[0], "stock_name": row[1], "latest_date": row[2]}
            for row in rows
        ]

    def import_csv(self, path: str | Path, *, only_if_empty: bool = False) -> int:
        data = pd.read_csv(path, encoding="utf-8-sig", dtype={"stock_id": str})
        return self.upsert_prices(data, source=str(path), only_if_empty=only_if_empty)

    def latest_update(self, source: str | Path | None = None) -> dict | None:
        """Return the most recent successful import record, optionally for one source."""
        with self.connect() as connection:
            if source is None:
                row = connection.execute(
                    "SELECT updated_at, source, row_count FROM update_logs "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
            else:
                source_text = str(source)
                row = connection.execute(
                    "SELECT updated_at, source, row_count FROM update_logs "
                    "WHERE source = ? OR source LIKE ? ORDER BY id DESC LIMIT 1",
                    (source_text, f"%{Path(source_text).name}"),
                ).fetchone()
        if row is None:
            return None
        return {"updated_at": row[0], "source": row[1], "row_count": row[2]}

    def query_prices(self, stock_id: str, start=None, end=None, *, indicators: bool = True) -> pd.DataFrame:
        """含起訖日；先讀前 59 個交易日計算 MA60，再裁切使用者區間。"""
        stock_id = _stock_id(stock_id)
        start = _date(start) if start is not None else None
        end = _date(end) if end is not None else None
        if start and end and start > end:
            raise ValueError("開始日期不可晚於結束日期")
        lower = start
        with self.connect() as connection:
            if indicators and start:
                previous = connection.execute("""
                    SELECT date FROM daily_prices WHERE stock_id=? AND date<?
                    ORDER BY date DESC LIMIT 59
                """, (stock_id, start)).fetchall()
                if previous:
                    lower = previous[-1][0]
            clauses, params = ["p.stock_id = ?"], [stock_id]
            for operator, value in ((">=", lower), ("<=", end)):
                if value:
                    clauses.append(f"p.date {operator} ?")
                    params.append(value)
            frame = pd.read_sql_query(
                "SELECT p.*, s.stock_name FROM daily_prices p JOIN stocks s USING(stock_id) WHERE "
                + " AND ".join(clauses) + " ORDER BY p.date", connection, params=params,
            )
        frame["date"] = pd.to_datetime(frame["date"])
        if indicators:
            frame = calculate_indicators(frame)
        if start:
            frame = frame[frame["date"] >= pd.Timestamp(start)]
        return frame.reset_index(drop=True)


def main():
    # Windows 的管線預設可能使用 cp1252，無法輸出中文狀態及路徑。
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="匯入股票 CSV 到 SQLite")
    parser.add_argument("--db", type=Path, help="覆寫資料庫路徑")
    parser.add_argument("--import-csv", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    database = StockDatabase(args.db)
    for path in args.import_csv:
        print(f"{path}: {database.import_csv(path)} 筆，資料庫：{database.path}")


if __name__ == "__main__":
    main()
