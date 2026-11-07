"""On-demand TWSE listed-stock lookup for the Streamlit dashboard.

This module is deliberately independent of the configured 10-stock Azure WebJob.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from io import BytesIO
import logging
import re
import time as sleep_time
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

from crawler import create_session, fetch_month, month_starts
from database import StockDatabase

LOGGER = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
STOCK_ID_RE = re.compile(r"[0-9]{4}")
DISPLAY_COLUMNS = {
    "date": "日期", "stock_id": "股票代號", "stock_name": "股票名稱",
    "open": "開盤價", "high": "最高價", "low": "最低價",
    "close": "收盤價", "change": "漲跌價差", "trade_volume": "成交股數",
    "trade_value": "成交金額", "transaction_count": "成交筆數",
}


def validate_stock_id(stock_id: str) -> str:
    stock_id = stock_id.strip()
    if not STOCK_ID_RE.fullmatch(stock_id):
        raise ValueError("請輸入四位數的 TWSE 上市股票代號，例如 2327 或 2330。")
    return stock_id


def date_window(days: int, today: date | None = None) -> tuple[date, date]:
    if days not in (90, 180, 365):
        raise ValueError("查詢範圍僅支援 90、180 或 365 天。")
    current = today or datetime.now(TAIPEI).date()
    return current - timedelta(days=days), current


def normalize_twse_rows(rows: list[dict]) -> list[dict]:
    """Represent TWSE empty optional numeric fields as SQLite NULL, not zero.

    Keep required OHLC and volume validation in StockDatabase._records().
    """
    optional_numeric_columns = ("change", "trade_value", "transaction_count")
    normalized = []
    for raw in rows:
        row = raw.copy()
        for column in optional_numeric_columns:
            if row.get(column) is None or (isinstance(row.get(column), str) and row[column].strip() in ("", "--", "---")):
                row[column] = None
        normalized.append(row)
    return normalized


def fetch_and_store_stock(stock_id: str, days: int = 180, *, pause_seconds: float = 1.1) -> dict:
    """Fetch released TWSE daily rows then insert only missing (stock,date) keys.

    A request error fails closed: the batch isn't written on a partial failure.
    A zero-row response is not interpreted as a successful update.
    """
    stock_id = validate_stock_id(stock_id)
    start, end = date_window(days)
    current_time = datetime.now(TAIPEI)
    months = list(month_starts(start, end))
    all_rows: dict[str, dict] = {}
    if pause_seconds < 1.0:
        raise ValueError("TWSE 請求間隔至少一秒。")

    with create_session() as session:
        for index, month in enumerate(months):
            try:
                rows = fetch_month(session, month, stock_id)
            except (requests.RequestException, ValueError, KeyError) as exc:
                raise RuntimeError(f"TWSE {month:%Y-%m} 資料取得失敗，未寫入不完整批次：{exc}") from exc
            for row in rows:
                source_date = date.fromisoformat(str(row["date"]))
                if not start <= source_date <= end:
                    continue
                if source_date == current_time.date() and current_time.time() < time(17, 0):
                    # Conservative boundary for post-close publication; no intraday assumption.
                    continue
                if row.get("stock_id") != stock_id or row.get("close") in (None, ""):
                    continue
                all_rows[source_date.isoformat()] = row
            if index != len(months) - 1:
                sleep_time.sleep(pause_seconds)

    db = StockDatabase()  # Identical database resolution as Dashboard.
    existing = db.query_prices(stock_id, start=start, end=end, indicators=False)
    existing_dates = set(existing["date"].dt.strftime("%Y-%m-%d")) if not existing.empty else set()
    new_rows = [row for day, row in sorted(all_rows.items()) if day not in existing_dates]
    written = db.upsert_prices(pd.DataFrame(normalize_twse_rows(new_rows)), source=f"twse-dashboard:{stock_id}") if new_rows else 0
    return {
        "stock_id": stock_id,
        "source_rows": len(all_rows),
        "inserted_rows": written,
        "source_latest": max(all_rows, default=None),
        "db_path": str(db.path),
        "warning": ("TWSE 在指定區間沒有回傳已公布的日成交資料；請確認代號是否為上市股票。"
                    if not all_rows else None),
    }


@st.cache_data(ttl=60, max_entries=128, show_spinner=False)
def cached_stock_prices(stock_id: str, db_path: str, db_mtime_ns: int, db_size: int, days: int) -> pd.DataFrame:
    """Cache reads only, keyed by database file identity; never cache writes/network fetches."""
    del db_mtime_ns, db_size
    start, end = date_window(days)
    return StockDatabase(db_path).query_prices(stock_id, start=start, end=end, indicators=True)


def read_stock_prices(stock_id: str, days: int = 180) -> pd.DataFrame:
    db = StockDatabase()
    stat = db.path.stat()
    return cached_stock_prices(stock_id, str(db.path), stat.st_mtime_ns, stat.st_size, days)


def export_table(frame: pd.DataFrame) -> pd.DataFrame:
    names = {key: value for key, value in DISPLAY_COLUMNS.items() if key in frame.columns}
    result = frame[list(names)].rename(columns=names).copy()
    if "日期" in result:
        result["日期"] = pd.to_datetime(result["日期"]).dt.strftime("%Y-%m-%d")
    return result


def excel_bytes(frame: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="股票日交易資料", index=False)
        sheet = writer.sheets["股票日交易資料"]
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    return buffer.getvalue()


def render_stock_lookup() -> None:
    """Standalone page; existing comparison, picker and scheduled updater stay intact."""
    st.subheader("🔍 查詢股票｜TWSE 上市個股")
    st.caption("輸入任意四位數 TWSE 上市股票代號，按查詢才從證交所取得已公布的盤後日成交資料。非即時逐筆報價。")
    with st.form("twse_stock_lookup_form"):
        stock_id_input = st.text_input("股票代號", placeholder="例如 2327、2330、2603", max_chars=4)
        days = st.selectbox("查詢期間", options=[90, 180, 365], index=1,
                            format_func=lambda x: {90: "最近 90 天", 180: "最近 180 天", 365: "最近一年"}[x])
        submitted = st.form_submit_button("查詢 TWSE 並更新 SQLite", type="primary")

    if submitted:
        try:
            stock_id = validate_stock_id(stock_id_input)
            with st.spinner("從 TWSE 取得已公布的日交易資料，並檢查 SQLite 缺漏日期…"):
                result = fetch_and_store_stock(stock_id, days)
            st.session_state["twse_lookup_id"] = stock_id
            st.session_state["twse_lookup_days"] = days
            st.session_state["twse_lookup_result"] = result
            cached_stock_prices.clear()
        except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
            LOGGER.warning("TWSE manual lookup failed: %s", exc)
            st.error(f"查詢失敗：{exc}")
            return

    stock_id = st.session_state.get("twse_lookup_id")
    if not stock_id:
        st.info("請輸入股票代號並點擊查詢。")
        return
    result = st.session_state.get("twse_lookup_result")
    if result and result.get("warning"):
        st.warning(result["warning"])
    if result and submitted:
        st.success(f"股票 {stock_id}：TWSE 回傳 {result['source_rows']} 個交易日，新增 {result['inserted_rows']} 筆至 SQLite。")

    try:
        data = read_stock_prices(stock_id, st.session_state.get("twse_lookup_days", 180))
    except (OSError, ValueError) as exc:
        st.error(f"SQLite 讀取失敗：{exc}")
        return
    if data.empty:
        st.info("此代號目前沒有可供顯示的資料。若代號屬於上櫃股票，TWSE 不提供該市場的資料。")
        return

    data = data.sort_values("date").reset_index(drop=True)
    latest = data.iloc[-1]
    name = str(latest.get("stock_name") or "").strip()
    st.markdown(f"### {name or '股票'}（{stock_id}）")
    st.caption(f"SQLite 最新交易日期：{latest['date']:%Y-%m-%d}｜來源：TWSE 已公布日交易資料")
    one, two, three = st.columns(3)
    one.metric("最新收盤價", f"{float(latest['close']):,.2f}")
    two.metric("最新成交股數", f"{int(latest['trade_volume']):,}")
    three.metric("資料筆數", str(len(data)))
    st.line_chart(data.set_index("date")[["close"]].rename(columns={"close": "收盤價"}))
    st.subheader("查詢資料")
    table = export_table(data)
    st.dataframe(table.iloc[::-1], use_container_width=True, hide_index=True)

    left, right = st.columns(2)
    with left:
        st.download_button("下載 CSV", data=table.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"TWSE_{stock_id}_daily.csv", mime="text/csv",
                           use_container_width=True)
    with right:
        st.download_button("下載 Excel", data=excel_bytes(table),
                           file_name=f"TWSE_{stock_id}_daily.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
