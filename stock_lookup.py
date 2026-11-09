"""On-demand TWSE listed-stock lookup for the Streamlit dashboard.

Successful TWSE lookups are enrolled in the shared SQLite auto-update registry.
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
STOCK_ID_RE = re.compile(r"(?:[0-9]{4}|00[0-9]{3}|[0-9]{6}|[0-9]{5}[A-Z])")
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



# NAME_SEARCH_V1
TWSE_CATALOG_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"


@st.cache_data(ttl=3600, max_entries=2, show_spinner=False)
def _twse_search_catalog() -> list[dict[str, str]]:
    """Read TWSE's latest-day listed securities catalog, not intraday quotes."""
    response = requests.get(TWSE_CATALOG_URL, timeout=(5, 20),
                            headers={"User-Agent": "StockSignal/1.0", "Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("TWSE 商品目錄格式錯誤")
    result = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("Code") or "").strip().upper()
        name = str(item.get("Name") or "").strip()
        if STOCK_ID_RE.fullmatch(code) and name:
            result[code] = {"stock_id": code, "stock_name": name}
    if not result:
        raise ValueError("TWSE 商品目錄沒有有效商品")
    return list(result.values())


def search_stock_candidates(query: str, limit: int = 12) -> list[dict[str, str]]:
    """Find code/name substrings in the current TWSE catalog and local SQLite.

    A catalog failure does not stop lookups already present in SQLite.
    A missing/expired instrument is never fabricated or silently padded.
    """
    from unicodedata import normalize
    term = normalize("NFKC", str(query)).strip().upper().replace("臺", "台")
    if not term:
        return []
    known = {}
    try:
        for row in _twse_search_catalog():
            known[row["stock_id"]] = row
    except (requests.RequestException, ValueError) as exc:
        LOGGER.warning("TWSE search catalog unavailable: %s", exc)
    for row in StockDatabase().list_available_stocks():
        known.setdefault(row["stock_id"], {
            "stock_id": row["stock_id"], "stock_name": row["stock_name"]
        })
    found = []
    for row in known.values():
        name = normalize("NFKC", row["stock_name"]).upper().replace("臺", "台")
        if term in name or term in row["stock_id"].upper():
            priority = (0 if term == row["stock_id"] or term == name else
                        1 if row["stock_id"].startswith(term) or name.startswith(term) else 2)
            found.append((priority, row["stock_id"], row))
    return [r for _, _, r in sorted(found)[:limit]]


def resolve_stock_query(query: str) -> str:
    """Return a supported stock code; ambiguous names require user selection."""
    text = str(query).strip().upper()
    if STOCK_ID_RE.fullmatch(text):
        return text
    # Allow '2327 國巨' from a suggestion.
    code = text.split(" ", 1)[0]
    if STOCK_ID_RE.fullmatch(code):
        return code
    found = search_stock_candidates(text, limit=50)
    exact = [row for row in found if row["stock_name"].upper() == text]
    if len(exact) == 1:
        return exact[0]["stock_id"]
    if len(found) == 1:
        return found[0]["stock_id"]
    if len(found) > 1:
        raise ValueError("有多檔符合的股票，請先點選下方建議項目。")
    raise ValueError("找不到相符的 TWSE 上市股票或 ETF，請確認名稱或代號。")

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
    # Register only after the lookup has confirmed real TWSE rows and the
    # database has persisted/previously contained valid daily prices.
    # A lookup without source data must not enter the scheduled-update queue.
    if all_rows:
        db.register_auto_update_stock(stock_id)
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


# Shared TWSE search for the homepage and all dashboard pages.
# One implementation means every entry point also enrolls a successfully
# queried stock in the persistent, Azure-WebJob auto-update registry.
SEARCH_WAIT_STYLE = """
<style>
@keyframes stock-wait-turn {to {transform: rotate(360deg)}}
@keyframes stock-wait-move {0% {transform:translateX(-110%)} 100% {transform:translateX(310%)}}
.stock-wait-overlay {position:fixed;inset:0;z-index:999999;
 background:rgba(21,36,49,.23);backdrop-filter:blur(2px);
 display:flex;justify-content:center;align-items:center;pointer-events:auto;}
.stock-wait-card {background:#fff;border:1px solid #dce5eb;border-radius:22px;
 box-shadow:0 18px 60px rgba(20,34,46,.18);width:min(390px,86vw);
 padding:28px 30px;text-align:center;color:#223a49;font-family:inherit;}
.stock-wait-spinner {margin:0 auto 16px;width:46px;height:46px;
 border:5px solid #d8e7ed;border-top-color:#4f7083;border-radius:50%;
 animation:stock-wait-turn 1s linear infinite;}
.stock-wait-title {font-size:1.16rem;font-weight:700;margin:0 0 8px;}
.stock-wait-subtitle {font-size:.92rem;color:#667f8e;line-height:1.5;margin:0 0 17px;}
.stock-wait-track {height:7px;overflow:hidden;background:#e7f0f4;border-radius:999px;}
.stock-wait-bar {height:100%;width:32%;border-radius:999px;background:#4f7083;
 animation:stock-wait-move 1.45s ease-in-out infinite;}
@media (prefers-reduced-motion:reduce) {
 .stock-wait-spinner,.stock-wait-bar {animation:none}
}
</style>
<div class="stock-wait-overlay" role="status" aria-live="polite" aria-label="正在查詢股票">
  <div class="stock-wait-card">
    <div class="stock-wait-spinner" aria-hidden="true"></div>
    <div class="stock-wait-title">正在取得股票資料</div>
    <p class="stock-wait-subtitle">正在向 TWSE 查詢、整理交易資料並同步儀表板…</p>
    <div class="stock-wait-track"><div class="stock-wait-bar"></div></div>
  </div>
</div>
"""


def perform_stock_search(query: str, navigate, *, days: int = 180,
                         error_key: str = "unified_search_error",
                         available_stock_labels=None) -> None:
    """Run one real TWSE lookup, preserve the tracking side effect, then navigate.

    The animation is indeterminate because TWSE does not report progress.
    Both success and error trigger a rerun, naturally removing the overlay.
    """
    try:
        stock_id = resolve_stock_query(query)
        st.html(SEARCH_WAIT_STYLE)
        result = fetch_and_store_stock(stock_id, days=days)
        if not result.get("source_rows"):
            raise ValueError(result.get("warning") or "TWSE 未回傳有效交易資料。")
        rows = [item for item in StockDatabase().list_available_stocks()
                if item["stock_id"] == stock_id]
        if not rows:
            raise RuntimeError("資料查詢成功，但找不到 SQLite 股票紀錄。")
        label = f'{stock_id} {rows[0]["stock_name"]}'.strip()
        if available_stock_labels:
            label = next((value for value in available_stock_labels
                          if value.split(" ", 1)[0] == stock_id), label)
        st.session_state["twse_lookup_id"] = stock_id
        st.session_state["twse_lookup_days"] = days
        st.session_state["twse_lookup_result"] = result
        cached_stock_prices.clear()
        navigate("個股儀表板", label)
        st.rerun()
    except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
        LOGGER.warning("Unified stock search failed: %s", exc)
        st.session_state[error_key] = f"股票查詢失敗：{exc}"
        st.rerun()


# Scoped styling for the lookup page; other pages are unaffected.
LOOKUP_STYLE = """
<style>
/* Only the stock lookup view; does not affect the other dashboard tabs. */
.st-key-lookup_shell { margin: .65rem auto 0; width: 100%; }
.st-key-lookup_shell .st-key-lookup_hero {
  background: linear-gradient(125deg, #f9fbfd 0%, #eaf1f6 100%) !important;
  border: 1px solid #dce5eb !important;
  border-radius: 24px !important;
  padding: clamp(22px, 3vw, 40px) !important;
  box-shadow: 0 14px 34px rgba(54, 84, 103, .07);
  gap: 1rem !important;
}
.st-key-lookup_hero h1 {
  font-size: clamp(1.8rem, 3vw, 2.35rem) !important;
  font-weight: 700 !important; color: #172c3a !important;
  margin: 0 0 .5rem !important; padding: 0 !important;
}
.st-key-lookup_hero .lookup-lede {
  font-size: 1rem !important; color: #587081 !important;
  margin: 0 !important; line-height: 1.65;
}
.st-key-lookup_hero [data-testid="stTextInput"] input {
  min-height: 58px !important; background: #fff !important;
  color: #172c3a !important; font-size: 1.12rem !important;
  border-radius: 14px !important; padding: 12px 17px !important;
}
.st-key-lookup_hero [data-testid="stTextInput"] input:focus {
  outline: 2px solid #4f7083 !important; outline-offset: 1px;
}
.st-key-lookup_hero [data-testid="stSelectbox"] [data-baseweb="select"] > div,
.st-key-lookup_hero [data-testid="stSelectbox"] [role="combobox"] {
  min-height: 51px !important; background: #fff !important;
  color: #243846 !important; border-radius: 13px !important;
}
/* Style only the primary search button, never suggestion options. */
.st-key-lookup_hero [data-testid="stButton"] button[kind="primary"],
.st-key-lookup_hero [data-testid="stButton"] button[data-testid="stBaseButton-primary"] {
  min-height: 51px !important; background: #4f7083 !important;
  border: 1px solid #4f7083 !important; border-radius: 13px !important;
  color: #fff !important; -webkit-text-fill-color: #fff !important;
  font-size: 1rem !important; font-weight: 650 !important;
}
.st-key-lookup_hero [data-testid="stButton"] button[kind="primary"] *,
.st-key-lookup_hero [data-testid="stButton"] button[data-testid="stBaseButton-primary"] * {
  color: #fff !important; -webkit-text-fill-color: #fff !important;
  fill: currentColor !important; opacity: 1 !important;
}
.st-key-lookup_hero [data-testid="stButton"] button[kind="primary"]:hover,
.st-key-lookup_hero [data-testid="stButton"] button[data-testid="stBaseButton-primary"]:hover {
  background: #39596a !important; border-color: #39596a !important;
}
.st-key-lookup_hero [data-testid="stButton"] button:focus-visible {
  outline: 3px solid #9dc0d1 !important; outline-offset: 2px;
}
.st-key-lookup_suggestions { margin: 0 0 .6rem; }
.st-key-lookup_suggestions [data-testid="stButton"] button {
  background: #fff !important; border: 1px solid #dde5eb !important;
  color: #253d4d !important; border-radius: 12px !important;
  min-height: 43px !important; font-weight: 500 !important;
  justify-content: flex-start !important; text-align: left !important;
}
.st-key-lookup_suggestions [data-testid="stButton"] button:hover {
  background: #f1f7fa !important; border-color: #a4bcc9 !important;
}
@media (max-width: 620px) {
  .st-key-lookup_hero [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  .st-key-lookup_hero [data-testid="stColumn"] { min-width: 100%; }
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

def _lookup_submit_request(code: str | None = None) -> None:
    """Queue the selected security without mutating an active widget tree."""
    query = code if code is not None else st.session_state.get("lookup_search_input", "")
    st.session_state["lookup_pending_request"] = str(query).strip()
    st.session_state["lookup_suggestions_hidden"] = str(
        st.session_state.get("lookup_search_input", "")
    ).strip()


def render_stock_lookup(navigate=None, available_stock_labels: list[str] | None = None) -> None:
    """A search-only screen. Show the selected instrument on the stock dashboard."""
    # Run the queued action before building any Streamlit layout, avoiding
    # duplicate keys / transient UI cleanup / front-end ElementNode errors.
    pending = st.session_state.pop("lookup_pending_request", None)
    if pending:
        try:
            stock_id = resolve_stock_query(pending)
            days = st.session_state.get("lookup_search_days", 180)
            with st.spinner(f"正在查詢 {stock_id} 的資料"):
                result = fetch_and_store_stock(stock_id, days)
            if not result.get("source_rows"):
                st.session_state["lookup_search_error"] = (
                    result.get("warning") or "TWSE 未回傳有效交易資料，未切換個股。"
                )
            else:
                # The downloaded stock may not be in the old toolbar options yet.
                # Read the newly persisted label from SQLite before the next rerun.
                matches = [
                    item for item in StockDatabase().list_available_stocks()
                    if item["stock_id"] == stock_id
                ]
                if not matches:
                    raise RuntimeError("股票資料未出現在 SQLite，無法開啟個股儀表板。")
                stock_label = f'{stock_id} {matches[0]["stock_name"]}'.strip()
                # Keep the label synchronized with the CSV-based dashboard entry
                # when it uses the same stock ID but an older display name.
                if available_stock_labels:
                    stock_label = next(
                        (label for label in available_stock_labels
                         if label.split(" ", 1)[0] == stock_id),
                        stock_label,
                    )
                st.session_state["twse_lookup_id"] = stock_id
                st.session_state["twse_lookup_days"] = days
                st.session_state["twse_lookup_result"] = result
                cached_stock_prices.clear()
                if navigate is not None:
                    navigate("個股儀表板", stock_label)
                else:
                    st.session_state["active_page"] = "個股儀表板"
                    st.session_state["selected_stock"] = stock_label
                    st.session_state["scroll_to_top"] = True
                st.rerun()
        except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
            LOGGER.warning("TWSE stock lookup failed: %s", exc)
            st.session_state["lookup_search_error"] = f"查詢失敗：{exc}"

    st.html(LOOKUP_STYLE)
    with st.container(key="lookup_shell"):
        with st.container(key="lookup_hero"):
            st.html("""
                <h1>探索股票，掌握市場趨勢</h1>
                <p class="lookup-lede">輸入股票中文名稱或代號，查詢 TWSE 已公布的日交易資料。</p>
            """)
            st.markdown("**搜尋上市股票或 ETF**")
            entered = st.text_input(
                "股票名稱或代號",
                key="lookup_search_input",
                placeholder="例如：國巨、國泰、2330、0050、009816…",
                label_visibility="collapsed",
                max_chars=32,
            )
            typed = entered.strip()
            hidden = st.session_state.get("lookup_suggestions_hidden")
            if hidden is not None and hidden != typed:
                st.session_state.pop("lookup_suggestions_hidden", None)
            if typed and st.session_state.get("lookup_suggestions_hidden") != typed:
                try:
                    suggestions = search_stock_candidates(typed, limit=8)
                except (ValueError, OSError, RuntimeError, requests.RequestException) as exc:
                    LOGGER.warning("Stock suggestions unavailable: %s", exc)
                    suggestions = []
                    st.caption("相關搜尋暫時無法載入，仍可使用股票代號查詢。")
                if suggestions:
                    with st.container(key="lookup_suggestions", height=244, border=False):
                        st.caption("相關股票／ETF・點選後直接前往個股儀表板")
                        for item in suggestions:
                            st.button(
                                f'{item["stock_id"]}　{item["stock_name"]}',
                                key=f'lookup_suggest_{item["stock_id"]}',
                                use_container_width=True,
                                on_click=_lookup_submit_request,
                                args=(item["stock_id"],),
                            )

            range_col, button_col = st.columns([1, 1.5], vertical_alignment="bottom")
            with range_col:
                st.selectbox(
                    "查詢資料期間", [90, 180, 365],
                    index=1,
                    format_func=lambda value: {
                        90: "最近 90 天", 180: "最近 180 天", 365: "最近一年"
                    }[value],
                    key="lookup_search_days",
                )
            with button_col:
                st.button(
                    "查詢 TWSE 並前往個股儀表板",
                    type="primary",
                    use_container_width=True,
                    on_click=_lookup_submit_request,
                )
            st.caption("資料來源：臺灣證券交易所 TWSE（盤後日交易資料，非即時報價）。")
            error = st.session_state.pop("lookup_search_error", None)
            if error:
                st.error(error)
