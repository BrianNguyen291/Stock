"""Streamlit stock dashboard for the Taiwan stock data in this repository."""

from datetime import date, timedelta
from pathlib import Path
import re
import requests
import sqlite3
import tempfile
from uuid import uuid4

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from charts import INDICATORS, MA_WINDOWS, PERIODS, interactive_chart, recent_period, volume_chart
from comparison import render_comparison
from crawler import crawl
from database import StockDatabase
from home import DESIGN_TOKENS, render_home, render_login
from stock_lookup import render_stock_lookup


DATA_DIR = Path(__file__).parent
STOCK_FILE_PATTERN = re.compile(r"\d{4,6}")
DASHBOARD_PAGES = ["個股儀表板", "查詢股票", "條件選股", "股票比較"]
DASHBOARD_STOCK_PAGE = DASHBOARD_PAGES[0]
ANALYSIS_PANEL_HEIGHT = 416
ANALYSIS_PANELS = {
    "收盤價走勢": ":material/show_chart:",
    "成交量走勢": ":material/bar_chart:",
    "交易資料": ":material/table_rows:",
}


def discover_stock_files(data_dir: Path = DATA_DIR) -> dict[str, Path]:
    """Find stock CSVs in the project root and data/raw directory."""
    files = {}
    seen_stock_ids = set()
    candidates = sorted({
        path for directory in (data_dir, data_dir / "data" / "raw")
        for path in directory.glob("*.csv")
    }, key=lambda path: (path.stem, path.parent != data_dir))
    for path in candidates:
        if not STOCK_FILE_PATTERN.fullmatch(path.stem):
            continue
        if path.stem in seen_stock_ids:
            continue
        try:
            first = pd.read_csv(path, encoding="utf-8-sig", dtype=str, nrows=1, keep_default_na=False)
            name = str(first["stock_name"].iloc[0]).strip()
        except (OSError, KeyError, IndexError, ValueError, pd.errors.ParserError):
            name = ""
        label = f"{path.stem} {name}".strip()
        files[label] = path
        seen_stock_ids.add(path.stem)
    return files


STOCK_FILES = discover_stock_files()


def refresh_stock_files() -> None:
    """Include stocks inserted by manual TWSE lookup, even without a local CSV.

    CSV-backed entries retain their existing import path and display label.
    DB-only entries use a nonexisting path only as an ID-bearing handle; reads
    always use SQLite and never try to create a CSV.
    """
    global STOCK_FILES
    files = discover_stock_files()
    known_ids = {path.stem for path in files.values()}
    for stock in StockDatabase().list_available_stocks():
        stock_id = stock["stock_id"]
        if stock_id not in known_ids:
            label = f"{stock_id} {stock['stock_name']}".strip()
            files[label] = DATA_DIR / "data" / "raw" / f"{stock_id}.csv"
    STOCK_FILES = files

st.set_page_config(page_title="台股投資儀表板", page_icon="📈", layout="wide")


def format_number(value: float | int | None, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.2f}{suffix}"


def taiwan_time(value: str | None) -> str:
    if not value:
        return "-"
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("Asia/Taipei").strftime("%Y-%m-%d %H:%M")


def read_stock_data(path: str) -> pd.DataFrame:
    """Seed each stock once; subsequent reads preserve database updates."""
    database = StockDatabase()
    if Path(path).is_file():
        database.import_csv(path, only_if_empty=True)
    return database.query_prices(Path(path).stem)


@st.cache_data(ttl=60, max_entries=128, show_spinner=False)
def load_dashboard_data(path_text: str, db_stamp: tuple[int, int] | None = None) -> tuple[pd.DataFrame, dict | None]:
    path = Path(path_text)
    database = StockDatabase()
    if path.is_file():
        database.import_csv(path, only_if_empty=True)
    return database.query_prices(path.stem), database.latest_update(path)


def load_all_stocks() -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    stocks: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for label, path in STOCK_FILES.items():
        try:
            db = StockDatabase()
            stat = db.path.stat()
            stocks[label], _ = load_dashboard_data(str(path), (stat.st_mtime_ns, stat.st_size))
        except (OSError, sqlite3.Error, ValueError, pd.errors.ParserError) as exc:
            errors[label] = str(exc)
    return stocks, errors


def load_home_quotes() -> list[tuple]:
    quotes = []
    featured = {"1101", "2303", "2317", "2327", "2330", "2881"}
    for label, path in STOCK_FILES.items():
        if path.stem not in featured:
            continue
        try:
            data, _ = load_dashboard_data(str(path))
        except (OSError, sqlite3.Error, ValueError, pd.errors.ParserError):
            continue
        if not data.empty:
            row = data.iloc[-1]
            if pd.notna(row["change_percent"]):
                closes = pd.to_numeric(data["close"], errors="coerce").dropna().tail(60)
                quotes.append((label, row["close"], row["change_percent"], row["date"], closes.astype(float).tolist()))
    return quotes


def navigate(page: str, stock: str | None = None) -> None:
    st.session_state["active_page"] = page
    if stock is not None:
        st.session_state["selected_stock"] = stock
    if page in DASHBOARD_PAGES:
        st.session_state["scroll_to_top"] = True


def update_all_stocks() -> tuple[list[str], dict[str, str]]:
    """Fetch the configured stock universe, import it, and report per-stock failures."""
    database = StockDatabase()
    updated: list[str] = []
    errors: dict[str, str] = {}
    end = date.today()
    for label, path in STOCK_FILES.items():
        stock_id = path.stem
        if not path.is_file():
            try:
                from stock_lookup import fetch_and_store_stock
                fetch_and_store_stock(stock_id, days=90)
                updated.append(label)
            except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
                errors[label] = str(exc)
            continue
        temporary_path = None
        try:
            # 首次匯入抓一年；已有資料只抓最近兩個月，避免每次更新重抓完整歷史。
            start = end - timedelta(days=365)
            existing = None
            if path.is_file():
                existing = pd.read_csv(path, encoding="utf-8-sig", dtype={"stock_id": str})
                dates = pd.to_datetime(existing["date"], errors="coerce").dropna()
                if not dates.empty:
                    start = max(dates.max().date() - timedelta(days=62), end - timedelta(days=365))
            with tempfile.NamedTemporaryFile(
                prefix=f".{stock_id}.", suffix=".update.csv", dir=path.parent, delete=False
            ) as handle:
                temporary_path = Path(handle.name)
            crawl(start, end, temporary_path, delay=1.0, stock_id=stock_id)
            latest = pd.read_csv(temporary_path, encoding="utf-8-sig", dtype={"stock_id": str})
            if existing is not None:
                latest = pd.concat([existing, latest], ignore_index=True)
            latest["date"] = pd.to_datetime(latest["date"], errors="raise")
            latest = (
                latest.drop_duplicates(subset=["stock_id", "date"], keep="last")
                .sort_values(["stock_id", "date"])
            )
            latest["date"] = latest["date"].dt.strftime("%Y-%m-%d")
            latest.to_csv(path, index=False, encoding="utf-8-sig")
            database.import_csv(path)
            updated.append(label)
        except (OSError, sqlite3.Error, ValueError, RuntimeError, pd.errors.ParserError) as exc:
            errors[label] = str(exc)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    return updated, errors


def inject_styles() -> None:
    st.markdown(
        f"""
        <style>
        {DESIGN_TOKENS}
        [data-testid="stHeader"], [data-testid="stSidebarCollapsedControl"] {{display: none;}}
        .block-container {{padding: 1rem clamp(1rem, 3vw, 2.5rem) 3.5rem;}}
        /* 不佔版面的元素移出排版，工具列一開始就在最終位置，不會因間距而跳動。 */
        [data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] style),
        [data-testid="stLayoutWrapper"]:has(> :is(.st-key-dashboard_scroll, .st-key-analysis_scroll)) {{
            position: absolute; height: 0; overflow: hidden;
        }}
        h1, h2, h3 {{font-weight: 500 !important; letter-spacing: -.02em;}}
        [data-testid="stHeadingWithActionElements"] h3 {{font-size: 1.2rem; padding: 1.1rem 0 .15rem;}}
        [data-testid="stLayoutWrapper"]:has(> .st-key-dashboard_toolbar) {{
            position: sticky; top: .6rem; z-index: 20;
        }}
        .st-key-dashboard_toolbar {{
            padding: .4rem .55rem; border-radius: 999px;
            background: rgb(255 255 255 / .86);
            -webkit-backdrop-filter: blur(14px); backdrop-filter: blur(14px);
            box-shadow: 0 0 0 1px var(--line), 0 10px 30px rgb(23 24 26 / .06);
        }}
        .st-key-dashboard_filters {{
            border-radius: 20px; padding: .8rem 1.1rem; background: var(--surface);
        }}
        .st-key-dashboard_toolbar [data-testid="stHorizontalBlock"] {{gap: .6rem;}}
        .st-key-dashboard_toolbar [data-testid="stSelectbox"] [role="group"],
        .st-key-dashboard_toolbar [data-baseweb="select"] > div {{
            border-radius: 999px; border-color: transparent; background: var(--surface);
        }}
        .st-key-dashboard_toolbar [data-testid="stPopoverButton"] {{
            border: 0; border-radius: 999px; background: var(--surface); box-shadow: none;
        }}
        .st-key-dashboard_toolbar [data-testid="stPopoverButton"]:hover {{background: var(--surface-hover);}}
        .st-key-dashboard_date [data-testid="stPopoverButton"] {{width: 2.5rem; padding: 0;}}
        .st-key-dashboard_date [data-testid="stPopoverButton"] [data-testid="stMarkdownContainer"] {{
            position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%);
        }}
        .st-key-dashboard_date [data-testid="stPopoverButton"] > div > [aria-hidden="true"] {{
            display: none;
        }}
        .st-key-dashboard_toolbar [role="radiogroup"] {{gap: .25rem; justify-content: flex-end; flex-wrap: nowrap;}}
        .st-key-dashboard_toolbar [role="radiogroup"] label {{
            margin: 0; padding: .42rem .95rem; border-radius: 999px; cursor: pointer;
            white-space: nowrap; transition: background .18s ease;
        }}
        .st-key-dashboard_toolbar [role="radiogroup"] label > div > div:not([data-testid="stMarkdownContainer"]) {{display: none;}}
        .st-key-dashboard_toolbar [role="radiogroup"] label:has(input:focus-visible) {{
            outline: 2px solid var(--ink); outline-offset: 2px;
        }}
        .st-key-dashboard_toolbar [role="radiogroup"] label p {{color: var(--dim); font-size: .87rem; font-weight: 500;}}
        .st-key-dashboard_toolbar [role="radiogroup"] label:hover {{background: var(--surface);}}
        .st-key-dashboard_toolbar [role="radiogroup"] label:has(input:checked) {{background: var(--ink);}}
        .st-key-dashboard_toolbar [role="radiogroup"] label:has(input:checked) p {{color: #fff;}}
        .st-key-dashboard_filters [data-testid="stCheckbox"] {{padding: .25rem 0;}}
        @media (max-width: 860px) {{
            [data-testid="stLayoutWrapper"]:has(> .st-key-dashboard_toolbar) {{position: static;}}
            .st-key-dashboard_toolbar {{border-radius: 22px;}}
            .st-key-dashboard_toolbar [data-testid="stHorizontalBlock"],
            .st-key-dashboard_filters [data-testid="stHorizontalBlock"] {{flex-wrap: wrap;}}
            .st-key-dashboard_toolbar [data-testid="stColumn"]:nth-child(1) {{
                flex: 0 0 auto; width: auto; min-width: 0;
            }}
            .st-key-dashboard_toolbar [data-testid="stColumn"]:nth-child(2) {{
                flex: 1 1 12rem; width: auto; min-width: 0;
            }}
            .st-key-dashboard_toolbar [data-testid="stColumn"]:has(.st-key-dashboard_date) {{
                flex: 0 0 auto; width: auto; min-width: 0;
            }}
            .st-key-dashboard_toolbar [data-testid="stColumn"]:last-child {{
                flex: 1 1 100%; width: auto; min-width: 0;
            }}
            .st-key-dashboard_toolbar [role="radiogroup"] {{justify-content: flex-start;}}
            .st-key-dashboard_filters [data-testid="stColumn"] {{
                flex: 1 1 6rem; width: auto; min-width: 0;
            }}
            .st-key-dashboard_filters :is([data-testid="stDateInput"], [data-testid="stMultiSelect"]) {{
                min-width: 0;
            }}
            .st-key-dashboard_filters [data-testid="stColumn"]:has(
                [data-testid="stDateInput"], [data-testid="stMultiSelect"]
            ) {{flex-basis: 15rem;}}
        }}
        .st-key-kline_heading {{margin-top: 1rem;}}
        .st-key-kline_heading [data-testid="stHeadingWithActionElements"] h3 {{padding: 0;}}
        .st-key-analysis_cards [data-testid="stHorizontalBlock"] {{gap: .75rem;}}
        .st-key-analysis_cards button {{min-height: 4.5rem; border-radius: 20px; font-weight: 500;}}
        .st-key-analysis_cards button[kind="secondary"] {{border: 0; background: var(--surface);}}
        .st-key-analysis_cards button[kind="secondary"]:hover {{background: var(--surface-hover); color: var(--ink);}}
        .st-key-analysis_panel {{min-height: 30rem;}}
        .st-key-analysis_panel img {{max-height: 26rem; width: auto; max-width: 100%; margin: 0 auto;}}
        [data-testid="stMetric"] {{
            background: var(--surface); border-radius: 20px; padding: 1.05rem 1.2rem;
        }}
        [data-testid="stMetricLabel"] {{font-size: .84rem; color: var(--dim);}}
        [data-testid="stMetricValue"] {{
            font-size: 1.7rem; font-weight: 500; letter-spacing: -.02em; font-variant-numeric: tabular-nums;
        }}
        [data-testid="stDataFrame"] {{border-radius: 16px; overflow: hidden;}}
        [data-testid="stImage"] img, [data-testid="stPyplot"] img {{border-radius: 16px;}}
        [data-testid="stExpander"] details {{border: 0; border-radius: 16px; background: var(--surface);}}
        .status-card {{padding: .25rem .5rem; color: var(--dim); font-size: .9rem;
          border-bottom: 1px solid var(--line);}}
        .hero {{padding: 1.4rem 0 1.1rem;}}
        .hero h1 {{font-size: clamp(1.8rem, 3.4vw, 2.5rem); line-height: 1.15; padding: 0;}}
        .hero p {{color: var(--dim);}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def scroll_to_top(requested: bool) -> None:
    """Reset the content area before the new page renders.

    The container is always emitted so later elements keep the same position between reruns.
    """
    with st.container(key="dashboard_scroll"):
        if requested:
            components.html(
                f"<!-- {uuid4()} -->" + """
                <script>
                const main = window.parent.document.querySelector('[data-testid="stMain"]');
                if (main) main.scrollTop = 0;
                </script>
                """,
                height=1,
            )


def render_status(stocks: dict[str, pd.DataFrame], errors: dict[str, str]) -> None:
    database = StockDatabase()
    successful = 0
    status_rows = []
    for label, path in STOCK_FILES.items():
        data = stocks.get(label, pd.DataFrame())
        latest = data["date"].max().strftime("%Y-%m-%d") if not data.empty else "-"
        update = database.latest_update(path)
        if label in errors:
            state = "失敗"
        elif update is None:
            state = "尚未更新"
        else:
            state = "已同步"
            successful += 1
        status_rows.append({
            "股票": label,
            "狀態": state,
            "最新交易日": latest,
            "最後更新（台灣）": taiwan_time(update["updated_at"] if update else None),
            "訊息": errors.get(label, ""),
        })

    with st.expander(
        f"資料同步摘要　{successful}/{len(STOCK_FILES)} 檔已同步",
        expanded=False,
    ):
        st.dataframe(
            pd.DataFrame(status_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "訊息": st.column_config.TextColumn("訊息", width="large"),
            },
        )
        st.caption("資料狀態僅表示更新流程結果；選股條件請至「條件選股」查看。")


def scroll_to_analysis_panel() -> None:
    """Scroll to the opened panel unless it is already fully visible below the sticky toolbar."""
    with st.container(key="analysis_scroll"):
        components.html(
            f"<!-- {uuid4()} -->" + """
            <script>
            const page = window.parent.document;
            const main = page.querySelector('[data-testid="stMain"]');
            const panel = page.querySelector('.st-key-analysis_panel');
            const toolbar = page.querySelector('.st-key-dashboard_toolbar');
            if (main && panel) {
                const toolbarBottom = toolbar && getComputedStyle(toolbar.parentElement).position === "sticky"
                    ? toolbar.getBoundingClientRect().bottom : main.getBoundingClientRect().top;
                const box = panel.getBoundingClientRect();
                if (box.top < toolbarBottom || box.bottom > main.getBoundingClientRect().bottom) {
                    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
                    main.scrollTo({
                        top: main.scrollTop + box.top - toolbarBottom - 12,
                        behavior: calm ? "auto" : "smooth",
                    });
                }
            }
            </script>
            """,
            height=1,
        )


def toggle_analysis_panel(panel: str) -> None:
    active = st.session_state.get("analysis_panel")
    st.session_state["analysis_panel"] = None if active == panel else panel
    st.session_state["analysis_scroll"] = active != panel


@st.fragment
def render_analysis(filtered: pd.DataFrame, stock_label: str, volume_key: str) -> None:
    """點卡片時只重畫這一區，K 線圖不會被重新產生，畫面才不會跳動。"""
    st.subheader("分析")
    active_panel = st.session_state.get("analysis_panel")
    with st.container(key="analysis_cards"):
        for column, (panel, icon) in zip(st.columns(len(ANALYSIS_PANELS)), ANALYSIS_PANELS.items()):
            column.button(
                panel, icon=icon, key=f"analysis_card_{panel}", use_container_width=True,
                type="primary" if panel == active_panel else "secondary",
                on_click=toggle_analysis_panel, args=(panel,),
            )

    should_scroll = st.session_state.pop("analysis_scroll", False)
    if active_panel is None:
        return
    # 三種內容佔同樣高度，切換時版面不變，捲動位置也不會被拉走。
    with st.container(key="analysis_panel"):
        if active_panel == "收盤價走勢":
            st.line_chart(
                filtered.set_index("date")[["close"]].rename(columns={"close": "收盤價"}),
                height=ANALYSIS_PANEL_HEIGHT,
            )
        elif active_panel == "成交量走勢":
            show_volume_ma = st.checkbox("20 日均量", value=True, key=volume_key)
            st.pyplot(volume_chart(filtered, title=f"{stock_label} 成交量", show_average=show_volume_ma))
        elif active_panel == "交易資料":
            display_columns = {"date": "日期", "open": "開盤價", "high": "最高價", "low": "最低價",
                               "close": "收盤價", "change": "漲跌價差", "trade_volume": "成交量"}
            table = filtered[list(display_columns)].rename(columns=display_columns)
            table["日期"] = table["日期"].dt.strftime("%Y-%m-%d")
            st.dataframe(
                table.sort_values("日期", ascending=False), hide_index=True,
                use_container_width=True, height=ANALYSIS_PANEL_HEIGHT,
            )
    if should_scroll:
        scroll_to_analysis_panel()


def render_single_stock(stocks: dict[str, pd.DataFrame], stock_label: str | None, date_slot) -> None:
    if stock_label is None:
        st.warning("目前沒有股票資料。")
        return
    data = stocks.get(stock_label, pd.DataFrame())
    if data.empty:
        st.warning("目前沒有可顯示的資料。")
        return
    min_date, max_date = data["date"].min().date(), data["date"].max().date()
    dates = date_slot.popover("日期範圍", icon=":material/calendar_month:", help="日期範圍")
    selected_dates = dates.date_input(
        "日期範圍", value=(min_date, max_date), min_value=min_date, max_value=max_date,
        key=f"stock_dates_{STOCK_FILES[stock_label].stem}",
    )
    filtered = None
    if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
        start_date, end_date = selected_dates
        filtered = data[data["date"].dt.date.between(start_date, end_date)].copy()
        if filtered.empty:
            st.warning("選定日期範圍沒有資料，請重新選擇日期。")
    else:
        st.info("請選擇日期範圍的結束日期。")

    ma_keys = [f"show_ma_{window}" for window in MA_WINDOWS]
    indicator_keys = [f"show_indicator_{name}" for name in INDICATORS]
    period_key = "kline_period"
    volume_key = "show_volume_ma_20"
    if filtered is None or filtered.empty:
        # Keep the chart preferences while the range picker waits for the second date.
        st.session_state.update({
            key: st.session_state[key] for key in (*ma_keys, *indicator_keys, period_key, volume_key)
            if key in st.session_state
        })
        return

    latest = filtered.iloc[-1]
    previous = filtered.iloc[-2] if len(filtered) > 1 else None
    change = latest["price_change"] if previous is not None else None
    change_percent = latest["change_percent"] if previous is not None else None
    name = latest.get("stock_name") or stock_label
    st.markdown(
        f'<div class="hero"><h1 style="margin:0">{name}（{latest["stock_id"]}）</h1>'
        f'<p style="margin:.4rem 0 0">交易資料與技術指標總覽 · {start_date} ～ {end_date}</p></div>',
        unsafe_allow_html=True,
    )
    metric_columns = st.columns(4)
    metric_columns[0].metric("最新收盤價", format_number(latest["close"]),
                             f"{format_number(change)} ({format_number(change_percent, '%')})",
                             delta_color="inverse")
    metric_columns[1].metric("區間最高價", format_number(filtered["high"].max()))
    metric_columns[2].metric("區間最低價", format_number(filtered["low"].min()))
    metric_columns[3].metric("最新成交量（股）", format_number(latest.get("trade_volume")))

    with st.container(key="kline_heading"):
        heading, *indicators = st.columns([6, 1, 1, 1], vertical_alignment="center")
        heading.subheader("K 線圖")
        ma_windows = tuple(
            window for window, column, key in zip(MA_WINDOWS, indicators, ma_keys)
            if column.checkbox(f"MA{window}", value=True, key=key)
        )
    with st.container(key="kline_options"):
        period_column, *indicator_columns = st.columns([4, 1, 1, 1, 1], vertical_alignment="center")
        period = period_column.segmented_control(
            "期間", list(PERIODS), default="全部", key=period_key, label_visibility="collapsed",
        )
        indicators = tuple(
            name for (name, label), column, key in zip(INDICATORS.items(), indicator_columns, indicator_keys)
            if column.checkbox(label, value=False, key=key)
        )
    # 取消選取期間時 segmented_control 回傳 None，視為全部。
    chart_data = recent_period(filtered, PERIODS.get(period))
    st.plotly_chart(
        interactive_chart(chart_data, title=f"{stock_label} 日 K 線", ma_windows=ma_windows, indicators=indicators),
        config={"displaylogo": False, "scrollZoom": True},
    )
    st.caption("紅 K：收盤高於開盤；綠 K：收盤低於開盤。均線與指標資料不足完整天數時留白。"
               "滑鼠停留可查看當日價格、成交量與指標；拖曳框選可縮放，雙擊還原。")

    render_analysis(filtered, stock_label, volume_key)


def render_stock_picker(stocks: dict[str, pd.DataFrame]) -> None:
    with st.container(key="dashboard_filters"):
        first, second, third = st.columns(3)
        use_close_ma20 = first.checkbox("收盤價 > MA20", value=True)
        use_ma20_ma5 = second.checkbox("MA20 > MA5", value=True)
        use_volume = third.checkbox("成交量 > 20 日均量", value=True)

    st.subheader("條件選股")
    st.caption("以所有股票共同可用的最新交易日為基準；多個條件必須同時符合（AND）。")
    available_dates = [data["date"].max() for data in stocks.values() if not data.empty]
    if not available_dates:
        st.error("沒有可用的股票資料。")
        return
    benchmark = min(available_dates)
    st.info(f"共同資料基準日：{benchmark:%Y-%m-%d}")

    rows = []
    for label, data in stocks.items():
        row = data[data["date"] == benchmark]
        if row.empty:
            rows.append({"股票": label, "狀態": "排除：基準日缺資料"})
            continue
        record = row.iloc[-1]
        reasons = []
        if (use_close_ma20 or use_ma20_ma5) and (
            pd.isna(record.get("ma_20")) or pd.isna(record.get("ma_5"))
        ):
            reasons.append("均線資料不足")
        else:
            if use_close_ma20 and not record["close"] > record["ma_20"]:
                reasons.append("收盤價不高於 MA20")
            if use_ma20_ma5 and not record["ma_20"] > record["ma_5"]:
                reasons.append("MA20 不高於 MA5")
        if use_volume and pd.isna(record.get("volume_ma_20")):
            reasons.append("20日均量不足")
        elif use_volume and not record["trade_volume"] > record["volume_ma_20"]:
            reasons.append("成交量條件不符")
        rows.append({"股票": label, "代號": record["stock_id"], "收盤價": record["close"],
                     "MA5": record.get("ma_5"), "MA20": record.get("ma_20"),
                     "成交量": record["trade_volume"], "20日均量": record.get("volume_ma_20"),
                     "狀態": "符合" if not reasons else "排除：" + "、".join(reasons)})
    result = pd.DataFrame(rows)
    st.dataframe(result, hide_index=True, use_container_width=True)
    matched = result[result["狀態"] == "符合"]
    if matched.empty:
        st.warning("目前沒有符合所有條件的股票。")
        return
    selected = st.selectbox("點選符合股票查看個股", matched["股票"].tolist())
    if st.button("前往個股儀表板", type="primary"):
        navigate("個股儀表板", selected)
        st.rerun()


def render_dashboard_toolbar(page: str):
    stock_labels = list(STOCK_FILES)
    selected_stock = st.session_state.get("selected_stock")
    stock_index = None
    if page == DASHBOARD_STOCK_PAGE and stock_labels:
        stock_index = stock_labels.index(selected_stock) if selected_stock in STOCK_FILES else 0
    search_key = f"dashboard_search_{page}"

    def open_stock():
        stock = st.session_state[search_key]
        if stock:
            navigate(DASHBOARD_STOCK_PAGE, stock)

    date_slot = None
    with st.container(key="dashboard_toolbar"):
        if page in ("條件選股", "查詢股票"):
            menu, search, navigation = st.columns([1, 3, 6], vertical_alignment="center")
        else:
            menu, search, dates, navigation = st.columns([1, 3, .55, 5.45], vertical_alignment="center")
            date_slot = dates.container(key="dashboard_date")
        with menu:
            with st.popover("選單", icon=":material/menu:"):
                st.button("首頁", on_click=navigate, args=("首頁",))
                st.button("帳戶", on_click=navigate, args=("登入",))
                update_requested = st.button("更新全部資料", icon=":material/refresh:")
        stock_label = search.selectbox(
            "搜尋股票", stock_labels, index=stock_index, key=search_key,
            placeholder="搜尋股票名稱或代號", label_visibility="collapsed",
            on_change=open_stock,
        )
        if st.session_state.get("dashboard_page") != page:
            st.session_state["dashboard_page"] = page
        navigation.radio(
            "前往", DASHBOARD_PAGES, key="dashboard_page", horizontal=True,
            label_visibility="collapsed",
            on_change=lambda: navigate(st.session_state["dashboard_page"]),
        )

    if update_requested:
        with st.spinner(f"正在從證交所更新 {len(STOCK_FILES)} 檔股票資料，請稍候…"):
            updated, update_errors = update_all_stocks()
        load_dashboard_data.clear()
        if update_errors:
            st.error(f"更新完成，但 {len(update_errors)} 檔失敗。")
            for label, message in update_errors.items():
                st.caption(f"{label}：{message}")
        else:
            st.success(f"已更新 {len(updated)} 檔股票。")
    return stock_label, date_slot


def main() -> None:
    refresh_stock_files()
    page = st.session_state.get("active_page", "首頁")
    if page == "首頁":
        render_home(list(STOCK_FILES), load_home_quotes(), navigate)
        return
    if page == "登入":
        render_login(navigate)
        return

    inject_styles()
    scroll_to_top(st.session_state.pop("scroll_to_top", False))
    stock_label, date_slot = render_dashboard_toolbar(page)
    stocks, errors = load_all_stocks()
    if page == "查詢股票":
        render_stock_lookup()
    elif page == "股票比較":
        render_comparison(STOCK_FILES, read_stock_data, date_slot=date_slot)
    elif page == "條件選股":
        render_stock_picker(stocks)
    else:
        render_single_stock(stocks, stock_label, date_slot)
    render_status(stocks, errors)

if __name__ == "__main__":
    main()
