"""On-demand TWSE listed-stock lookup for the Streamlit dashboard.

This module is deliberately independent of the configured 10-stock Azure WebJob.
"""
from __future__ import annotations
from html import escape

from datetime import date, datetime, time, timedelta
from io import BytesIO
import logging
import re
import time as sleep_time
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import altair as alt
from crawler import create_session, fetch_month, month_starts
from database import StockDatabase

LOGGER = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
STOCK_ID_RE = re.compile(r"(?:[0-9]{4}|[0-9]{6}|[0-9]{5}[A-Z])")
DISPLAY_COLUMNS = {
    "date": "日期", "stock_id": "股票代號", "stock_name": "股票名稱",
    "open": "開盤價", "high": "最高價", "low": "最低價",
    "close": "收盤價", "change": "漲跌價差", "trade_volume": "成交股數",
    "trade_value": "成交金額", "transaction_count": "成交筆數",
}


def validate_stock_id(stock_id: str) -> str:
    stock_id = stock_id.strip().upper()
    if not STOCK_ID_RE.fullmatch(stock_id):
        raise ValueError("請輸入 4 至 6 位數的 TWSE 股票或數字型 ETF 代號，例如 2327、2330、009816。")
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


# Scoped styling for the lookup page; other pages are unaffected.
LOOKUP_STYLE = """
<style>
/* ====================================
   1. 股票摘要卡片：統一尺寸
==================================== */

.st-key-lookup_shell [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {
    align-items: stretch !important;
}

.st-key-lookup_shell [data-testid="stColumn"]:has([data-testid="stMetric"]) {
    display: flex;
    flex-direction: column;
}

.st-key-lookup_shell [data-testid="stMetric"] {
    box-sizing: border-box;
    width: 100%;
    min-height: 145px !important;
    height: 100%;
    padding: 22px 24px !important;
    border-radius: 18px !important;
    background: #F6F8FA !important;
    border: 1px solid #E3E8ED !important;
    display: flex;
    flex-direction: column;
    justify-content: center;
}

.st-key-lookup_shell [data-testid="stMetricValue"] {
    font-size: 1.85rem !important;
    font-weight: 650 !important;
    font-variant-numeric: tabular-nums;
}


/* ====================================
   2. 查詢區塊：新視覺設計
==================================== */

.st-key-lookup_shell .st-key-lookup_hero {
    background: linear-gradient(
        135deg,
        #F8FAFC 0%,
        #EDF3F7 100%
    ) !important;

    border: 1px solid #DFE7ED !important;
    border-radius: 24px !important;
    padding: 36px !important;
}

/* 大搜尋框 */

.st-key-lookup_hero [data-testid="stTextInput"] input {
    min-height: 62px !important;
    font-size: 1.3rem !important;
    font-weight: 550 !important;

    background: #FFFFFF !important;
    color: #172433 !important;

    border-radius: 14px !important;
    padding: 14px 18px !important;
}

/* 搜尋框獲得焦點 */

.st-key-lookup_hero [data-testid="stTextInput"] input:focus {
    outline: 2px solid #4F7083 !important;
    outline-offset: 1px;
}

/* 查詢按鈕 */

.st-key-lookup_shell .st-key-lookup_hero
[data-testid="stFormSubmitButton"] button {

    background: #4F7083 !important;
    border: 1px solid #4F7083 !important;
    color: #FFFFFF !important;

    min-height: 55px !important;
    border-radius: 14px !important;

    font-weight: 650 !important;
    font-size: 1.05rem !important;

    opacity: 1 !important;
    transition: all 0.2s ease;
}

/* 確保按鈕內的文字和圖示是白色 */

.st-key-lookup_shell .st-key-lookup_hero
[data-testid="stFormSubmitButton"] button p,
.st-key-lookup_shell .st-key-lookup_hero
[data-testid="stFormSubmitButton"] button span,
.st-key-lookup_shell .st-key-lookup_hero
[data-testid="stFormSubmitButton"] button svg {
    color: #FFFFFF !important;
    fill: currentColor;
}

/* 滑鼠移入按鈕 */

.st-key-lookup_shell .st-key-lookup_hero
[data-testid="stFormSubmitButton"] button:hover {
    background: #38596B !important;
    border-color: #38596B !important;
    color: #FFFFFF !important;
}

/* 鍵盤操作的焦點提示 */

.st-key-lookup_shell .st-key-lookup_hero
[data-testid="stFormSubmitButton"] button:focus-visible {
    outline: 3px solid #91BBD0 !important;
    outline-offset: 2px;
}

/* 小螢幕適配 */

@media (max-width: 768px) {
    .st-key-lookup_shell .st-key-lookup_hero {
        padding: 20px !important;
    }

    .st-key-lookup_shell [data-testid="stMetric"] {
        min-height: 125px !important;
        padding: 16px !important;
    }
}

/* 查詢資料期間：白色背景 */
.st-key-lookup_hero [data-testid="stSelectbox"] [data-baseweb="select"] > div {
    background-color: #FFFFFF !important;
    border: 1px solid #E3E8ED !important;
    border-radius: 14px !important;
    color: #172433 !important;
}

/* 查詢資料期間：下拉選單改成純白色 */
.st-key-lookup_hero [data-testid="stSelectbox"] [data-baseweb="select"] > div,
.st-key-lookup_hero [data-testid="stSelectbox"] [role="combobox"] {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
    color: #172433 !important;
    border-radius: 14px !important;
}

/* 下拉選單內的箭頭 */
.st-key-lookup_hero [data-testid="stSelectbox"] svg {
    color: #172433 !important;
}
</style>
"""

@st.cache_data(ttl=1800, max_entries=64, show_spinner=False)
def cached_excel_export(csv_bytes: bytes) -> bytes:
    """Cache Excel serialization only. No database writes or TWSE calls are cached."""
    table = pd.read_csv(BytesIO(csv_bytes), encoding="utf-8-sig")
    return excel_bytes(table)


def _lookup_stock_name(frame: pd.DataFrame, stock_id: str) -> str:
    if "stock_name" not in frame or frame.empty:
        return stock_id
    names = frame["stock_name"].dropna().astype(str).str.strip()
    names = names[(names != "") & (names.str.lower() != "nan")]
    return names.iloc[-1] if not names.empty else stock_id


def _lookup_change(frame: pd.DataFrame):
    if len(frame) < 2:
        return None, None
    previous, latest = float(frame.iloc[-2]["close"]), float(frame.iloc[-1]["close"])
    movement = latest - previous
    percentage = movement / previous * 100 if previous else None
    return movement, percentage


# LOOKUP_SEARCH_PERIOD_V2

@st.cache_data(ttl=60, max_entries=128, show_spinner=False)
def _lookup_aggregate_chart_data(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """從真實日交易資料計算統計週期，不產生虛假的盤中資料。"""
    daily = frame[["date", "close", "trade_volume"]].copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date").dropna(subset=["date"])
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
    daily["trade_volume"] = pd.to_numeric(daily["trade_volume"], errors="coerce").fillna(0)
    if period == "每日":
        return daily.reset_index(drop=True)
    if period == "每 3 個交易日":
        daily["_bucket"] = pd.Series(range(len(daily)), index=daily.index) // 3
    elif period == "每週":
        daily["_bucket"] = daily["date"].dt.to_period("W-FRI").astype(str)
    elif period == "每月":
        daily["_bucket"] = daily["date"].dt.to_period("M").astype(str)
    else:
        raise ValueError("未知統計週期")
    return daily.groupby("_bucket", sort=True, as_index=False).agg(
        date=("date", "max"), close=("close", "last"), trade_volume=("trade_volume", "sum")
    )[["date", "close", "trade_volume"]]

def interactive_price_chart(chart_data: pd.DataFrame):
    df = chart_data.copy()

    hover = alt.selection_point(
        fields=["period_label"],
        nearest=True,
        on="pointerover",
        empty=False,
    )

    x = alt.X(
        "period_label:N",
        sort=df["period_label"].tolist(),
        title="日期",
        axis=alt.Axis(
            labelAngle=-35,
            labelLimit=100,
            labelOverlap=True,
        ),
    )

    y = alt.Y(
        "close:Q",
        title="收盤價（元）",
        scale=alt.Scale(zero=False),
    )

    base = alt.Chart(df).encode(
        x=x,
        y=y,
    )

    # 淺藍色漸層背景
    area = base.mark_area(
        color="#83C9F4",
        opacity=0.12,
    )

    # 淺藍色折線
    line = base.mark_line(
        color="#69B7E6",
        strokeWidth=2.5,
    )

    # 滑鼠感應區
    selectors = base.mark_point(
        opacity=0,
        size=100,
    ).add_params(hover)

    # 滑鼠移動時顯示垂直參考線
    rule = base.mark_rule(
        color="#729AB3",
        strokeWidth=1,
        strokeDash=[4, 4],
    ).encode(
        opacity=alt.condition(
            hover,
            alt.value(1),
            alt.value(0),
        ),
        tooltip=[
            alt.Tooltip(
                "period_label:N",
                title="日期／期間",
            ),
            alt.Tooltip(
                "close:Q",
                title="收盤價（元）",
                format=",.2f",
            ),
            alt.Tooltip(
                "trade_volume:Q",
                title="成交股數",
                format=",.0f",
            ),
        ],
    ).transform_filter(hover)

    # 滑鼠所指位置顯示圓點
    point = base.mark_circle(
        color="#69B7E6",
        size=90,
    ).encode(
        opacity=alt.condition(
            hover,
            alt.value(1),
            alt.value(0),
        )
    )

    return (
        (area + line + selectors + rule + point)
        .properties(height=340)
    )

def render_stock_lookup() -> None:
    """Modern search UI backed by the EXISTING TWSE-to-SQLite pipeline."""
    st.html(LOOKUP_STYLE)
    with st.container(key="lookup_shell"):
        with st.container(key="lookup_hero"):
            st.html("""<h1>探索股票，掌握市場趨勢</h1>
              <p>搜尋 TWSE 上市股票與 ETF · 查詢已公布日交易資料 · 自動同步至 SQLite</p>""")
            with st.form("twse_stock_lookup_form", clear_on_submit=False, border=False):
                st.markdown("#### ↓ 搜尋上市股票或 ETF ↓")
                entered = st.text_input(
                    "股票代號",
                    value=st.session_state.get("twse_lookup_id", ""),
                    placeholder="輸入代號，例如 2330、2603、009816、00990A",
                    max_chars=10,
                    label_visibility="collapsed",
                )
                range_col, button_col = st.columns([1, 1.5], vertical_alignment="bottom")
                with range_col:
                    days = st.selectbox(
                        "查詢資料期間", [90, 180, 365],
                        index=[90, 180, 365].index(st.session_state.get("twse_lookup_days", 180))
                            if st.session_state.get("twse_lookup_days", 180) in (90, 180, 365) else 1,
                        format_func=lambda x: {90: "最近 90 天", 180: "最近 180 天", 365: "最近一年"}[x],
                    )
                with button_col:
                    submitted = st.form_submit_button(
                        "查詢 TWSE 並同步到儀表板", type="primary", use_container_width=True,
                    )
            st.caption("資料來源：臺灣證券交易所 TWSE（盤後日交易資料，非即時報價）。僅支援資料來源可取得的代號。")

        if submitted:
            try:
                stock_id = validate_stock_id(entered)
                with st.spinner(f"正在查詢 {stock_id} 的 TWSE 歷史資料，並同步至 SQLite…"):
                    result = fetch_and_store_stock(stock_id, days)
                # Store the result only after successful completion; a failed search must not
                # accidentally show the preceding stock as the new search result.
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
            st.info("在上方輸入股票代號，按下「查詢並同步」即可查看行情、走勢與歷史資料。")
            return

        result = st.session_state.get("twse_lookup_result")
        if submitted and result:
            if result.get("warning"):
                st.warning(result["warning"])
            else:
                st.success(
                    f"{stock_id} 同步完成：來源共 {result['source_rows']} 個交易日，"
                    f"本次新增 {result['inserted_rows']} 筆至 SQLite。"
                )

        try:
            data = read_stock_prices(stock_id, st.session_state.get("twse_lookup_days", 180))
        except (OSError, ValueError) as exc:
            st.error(f"SQLite 讀取失敗：{exc}")
            return
        if data.empty:
            st.info("資料庫目前沒有此代號的交易紀錄。可能尚未公布、代號不支援或來源沒有回傳資料。")
            return

        data = data.sort_values("date").reset_index(drop=True)
        latest = data.iloc[-1]
        stock_name = _lookup_stock_name(data, stock_id)
        change, change_pct = _lookup_change(data)
        with st.container(key="lookup_result_header"):
            st.html(
                f"<h2>{escape(stock_name)} <span style='color:#717885;font-size:1rem'>"
                f"{escape(stock_id)}</span></h2>"
                f"<p>最後交易日：{latest['date']:%Y-%m-%d} · 已收錄 {len(data)} 筆 · TWSE 盤後資料</p>"
            )

        first, second, third, fourth = st.columns(4)
        price_delta = None if change is None else (
            f"{change:+,.2f} ({change_pct:+.2f}%)" if change_pct is not None else f"{change:+,.2f}"
        )
        first.metric("最新收盤價", f"{float(latest['close']):,.2f} 元", price_delta, delta_color="inverse")
        second.metric("區間最高價", f"{float(data['high'].max()):,.2f} 元")
        third.metric("區間最低價", f"{float(data['low'].min()):,.2f} 元")
        fourth.metric("最新成交量", f"{int(latest['trade_volume']):,} 股")

        tab_chart, tab_table = st.tabs(["走勢分析", "歷史交易資料"])
        with tab_chart:
            # CHART_CONFIRM_V3
            with st.form("lookup_chart_settings_form", border=True):
                st.markdown("#### 自訂圖表顯示")
                period_col, window_col, confirm_col = st.columns(
                    [2.4, 2.4, 1], vertical_alignment="bottom"
                )
                with period_col:
                    requested_period = st.selectbox(
                        "統計週期", ["每日", "每 3 個交易日", "每週", "每月"],
                        key="lookup_chart_period_draft",
                        help="每月收盤價為當月最後交易日價格；成交量為當月總量。",
                    )
                with window_col:
                    requested_window = st.selectbox(
                        "顯示範圍", ["全部已查詢資料", "最近 30 天", "最近 90 天", "最近 180 天"],
                        key="lookup_chart_window_draft",
                    )
                with confirm_col:
                    confirmed = st.form_submit_button(
                        "確定", type="primary", use_container_width=True
                    )
            if confirmed:
                st.session_state["lookup_chart_period_applied"] = requested_period
                st.session_state["lookup_chart_window_applied"] = requested_window
            chart_period = st.session_state.get("lookup_chart_period_applied", "每日")
            chart_window = st.session_state.get("lookup_chart_window_applied", "全部已查詢資料")

            chart_source = data.copy()
            if chart_window != "全部已查詢資料":
                window_days = {"最近 30 天": 30, "最近 90 天": 90, "最近 180 天": 180}[chart_window]
                cutoff = pd.Timestamp(chart_source["date"].max()) - pd.Timedelta(days=window_days)
                chart_source = chart_source[chart_source["date"] >= cutoff].copy()
            if chart_source.empty:
                st.info("選定範圍內沒有資料，請調整顯示範圍後按『確定』。")
            else:
                import altair as alt

                chart_data = _lookup_aggregate_chart_data(chart_source, chart_period)
                chart_data = chart_data.sort_values("date").copy()
                # Important: for monthly, use real month categories, not a temporal axis
                # whose ticks Streamlit/Altair may label as weeks regardless of data frequency.
                if chart_period == "每月":
                    chart_data["period_label"] = chart_data["date"].dt.strftime("%Y-%m")
                elif chart_period == "每週":
                    chart_data["period_label"] = chart_data["date"].dt.strftime("%Y-%m-%d")
                elif chart_period == "每 3 個交易日":
                    chart_data["period_label"] = chart_data["date"].dt.strftime("%m/%d")
                else:
                    chart_data["period_label"] = chart_data["date"].dt.strftime("%m/%d")
                chart_data["close"] = pd.to_numeric(chart_data["close"], errors="coerce")
                chart_data["trade_volume"] = pd.to_numeric(chart_data["trade_volume"], errors="coerce")
                ordered_labels = chart_data["period_label"].tolist()
                axis = alt.Axis(title=None, labelAngle=-35, labelLimit=95, labelOverlap=True)
                tooltip = [
                    alt.Tooltip("period_label:N", title="期間"),
                    alt.Tooltip("close:Q", title="期末收盤價", format=",.2f"),
                    alt.Tooltip("trade_volume:Q", title="期間成交股數", format=",.0f"),
                ]
                x = alt.X("period_label:N", sort=ordered_labels, axis=axis)
                st.subheader("收盤價走勢")

                st.altair_chart(
                    interactive_price_chart(chart_data),
                    use_container_width=True,
                )
                st.subheader("成交量")
                volume_chart = alt.Chart(chart_data).mark_bar().encode(
                    x=x,
                    y=alt.Y("trade_volume:Q", title="成交股數"),
                    tooltip=tooltip,
                ).properties(height=260)
                st.altair_chart(volume_chart, use_container_width=True)
                st.caption(
                    f"目前顯示：{chart_period}／{chart_window}。收盤價取期間最後交易日，"
                    "成交量為期間加總；資料來源為 TWSE 每日盤後資料（非每小時行情）。"
                )
        with tab_table:
            st.subheader("每日交易紀錄")
            table = export_table(data).iloc[::-1].reset_index(drop=True)
            st.dataframe(table, hide_index=True, use_container_width=True, height=430)

        st.divider()
        left, right, info = st.columns([1, 1, 2], vertical_alignment="center")
        table = export_table(data).iloc[::-1].reset_index(drop=True)
        csv_bytes = table.to_csv(index=False).encode("utf-8-sig")
        with left:
            st.download_button(
                "下載 CSV", data=csv_bytes, file_name=f"TWSE_{stock_id}_daily.csv",
                mime="text/csv", use_container_width=True,
            )
        with right:
            st.download_button(
                "下載 Excel", data=cached_excel_export(csv_bytes),
                file_name=f"TWSE_{stock_id}_daily.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with info:
            st.caption("下載內容與上方交易明細一致；已透過 SQLite 保留交易紀錄。")

