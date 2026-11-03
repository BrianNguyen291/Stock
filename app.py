"""Streamlit stock dashboard for the Taiwan stock data in this repository."""

from datetime import date, timedelta
from pathlib import Path
import re
import sqlite3
import tempfile

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from charts import MA_WINDOWS, candlestick_chart, volume_chart
from comparison import render_comparison
from crawler import crawl
from database import StockDatabase


DATA_DIR = Path(__file__).parent
STOCK_FILE_PATTERN = re.compile(r"\d{4,6}")


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
    database.import_csv(path, only_if_empty=True)
    return database.query_prices(Path(path).stem)


@st.cache_data(show_spinner=False)
def load_dashboard_data(path_text: str) -> tuple[pd.DataFrame, dict | None]:
    path = Path(path_text)
    database = StockDatabase()
    database.import_csv(path, only_if_empty=True)
    return database.query_prices(path.stem), database.latest_update(path)


def load_all_stocks() -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    stocks: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for label, path in STOCK_FILES.items():
        try:
            stocks[label], _ = load_dashboard_data(str(path))
        except (OSError, sqlite3.Error, ValueError, pd.errors.ParserError) as exc:
            errors[label] = str(exc)
    return stocks, errors


def update_all_stocks() -> tuple[list[str], dict[str, str]]:
    """Fetch the configured stock universe, import it, and report per-stock failures."""
    database = StockDatabase()
    updated: list[str] = []
    errors: dict[str, str] = {}
    end = date.today()
    for label, path in STOCK_FILES.items():
        stock_id = path.stem
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
        """
        <style>
        .block-container {padding-top: 1.5rem; padding-bottom: 3rem;}
        [data-testid="stMetric"] {
            background: var(--background-color);
            border: 1px solid rgba(128, 128, 128, .35); border-radius: 4px; padding: .8rem;
        }
        [data-testid="stMetricLabel"] {font-size: .85rem;}
        [data-testid="stMetricValue"] {font-size: 1.65rem; font-weight: 600;}
        .status-card {padding: .25rem .5rem; color: #475569; font-size: .9rem;
          border-bottom: 1px solid #e2e8f0;}
        .hero {padding: .2rem 0 .85rem; border-bottom: 1px solid rgba(128, 128, 128, .35);
          margin-bottom: 1.25rem;}
        .hero h1 {font-size: 1.8rem; font-weight: 650; letter-spacing: -.02em;}
        .hero p {color: #64748b;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def scroll_to_top() -> None:
    """Scroll the Streamlit content area to the dashboard heading."""
    components.html(
        """
        <script>
        const scrollToDashboardTop = () => {
            const parentWindow = window.parent;
            const parentDocument = parentWindow.document;
            const target = parentDocument.getElementById("dashboard-top");
            if (target) {
                target.scrollIntoView({ block: "start", inline: "nearest", behavior: "auto" });
            }
        };
        scrollToDashboardTop();
        window.setTimeout(scrollToDashboardTop, 100);
        window.setTimeout(scrollToDashboardTop, 500);
        window.setTimeout(scrollToDashboardTop, 1000);
        window.setTimeout(scrollToDashboardTop, 2000);
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


def render_single_stock(stocks: dict[str, pd.DataFrame]) -> None:
    with st.sidebar:
        stock_labels = list(STOCK_FILES)
        default_stock = st.session_state.get("selected_stock", stock_labels[0])
        stock_label = st.selectbox(
            "選擇股票", stock_labels, index=stock_labels.index(default_stock)
        )
        data = stocks.get(stock_label, pd.DataFrame())
        if data.empty:
            st.warning("目前沒有可顯示的資料。")
            return
        min_date, max_date = data["date"].min().date(), data["date"].max().date()
        selected_dates = st.date_input(
            "日期範圍", value=(min_date, max_date), min_value=min_date, max_value=max_date
        )
    if not isinstance(selected_dates, tuple) or len(selected_dates) != 2:
        st.info("請選擇日期範圍的結束日期。")
        return
    start_date, end_date = selected_dates
    filtered = data[data["date"].dt.date.between(start_date, end_date)].copy()
    if filtered.empty:
        st.warning("選定日期範圍沒有資料，請重新選擇日期。")
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
                             f"{format_number(change)} ({format_number(change_percent, '%')})")
    metric_columns[1].metric("區間最高價", format_number(filtered["high"].max()))
    metric_columns[2].metric("區間最低價", format_number(filtered["low"].min()))
    metric_columns[3].metric("最新成交量（股）", format_number(latest.get("trade_volume")))

    st.subheader("收盤價走勢")
    st.line_chart(filtered.set_index("date")[["close"]].rename(columns={"close": "收盤價"}))
    st.subheader("K 線圖")
    st.caption("紅 K：收盤高於開盤；綠 K：收盤低於開盤。均線資料不足完整天數時留白。")
    toggle_columns = st.columns(len(MA_WINDOWS) + 1)
    ma_windows = tuple(
        window for window, column in zip(MA_WINDOWS, toggle_columns)
        if column.checkbox(f"MA{window}", value=True, key=f"show_ma_{window}")
    )
    show_volume_ma = toggle_columns[-1].checkbox("20 日均量", value=True, key="show_volume_ma_20")
    st.pyplot(candlestick_chart(filtered, title=f"{stock_label} 日 K 線", ma_windows=ma_windows))

    left, right = st.columns([1, 1.35])
    with left:
        st.subheader("成交量走勢")
        st.pyplot(volume_chart(filtered, title=f"{stock_label} 成交量", show_average=show_volume_ma))
    with right:
        st.subheader("交易資料")
        display_columns = {"date": "日期", "open": "開盤價", "high": "最高價", "low": "最低價",
                           "close": "收盤價", "change": "漲跌價差", "trade_volume": "成交量"}
        table = filtered[list(display_columns)].rename(columns=display_columns)
        table["日期"] = table["日期"].dt.strftime("%Y-%m-%d")
        st.dataframe(table.sort_values("日期", ascending=False), hide_index=True, use_container_width=True)


def render_stock_picker(stocks: dict[str, pd.DataFrame]) -> None:
    st.subheader("條件選股")
    st.caption("以所有股票共同可用的最新交易日為基準；多個條件必須同時符合（AND）。")
    if not stocks:
        st.error("沒有可用的股票資料。")
        return
    benchmark = min(data["date"].max() for data in stocks.values() if not data.empty)
    st.info(f"共同資料基準日：{benchmark:%Y-%m-%d}")
    first, second, third = st.columns(3)
    with first:
        use_close_ma20 = st.checkbox("收盤價 > MA20", value=True)
    with second:
        use_ma20_ma5 = st.checkbox("MA20 > MA5", value=True)
    with third:
        use_volume = st.checkbox("成交量 > 20 日均量", value=True)

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
    st.session_state["selected_stock"] = selected
    if st.button("前往個股儀表板", type="primary"):
        st.session_state["requested_page"] = "個股儀表板"
        st.session_state["scroll_to_top"] = True
        st.rerun()


def main() -> None:
    inject_styles()
    st.markdown('<div id="dashboard-top"></div>', unsafe_allow_html=True)
    st.markdown('<div class="hero"><h1 style="margin:0">台股市場資料</h1>'
                '<p style="margin:.4rem 0 0">台股日交易資料、技術指標與條件選股</p></div>',
                unsafe_allow_html=True)
    with st.sidebar:
        st.header("功能選單")
        if st.button("🔄 更新全部資料", use_container_width=True):
            with st.spinner(f"正在從證交所更新 {len(STOCK_FILES)} 檔股票資料，請稍候…"):
                updated, update_errors = update_all_stocks()
            load_dashboard_data.clear()
            if update_errors:
                st.error(f"更新完成，但 {len(update_errors)} 檔失敗。")
                for label, message in update_errors.items():
                    st.caption(f"{label}：{message}")
            else:
                st.success(f"已更新 {len(updated)} 檔股票，正在重新載入最新資料。")
            st.rerun()
        pages = ["個股儀表板", "條件選股", "股票比較"]
        requested_page = st.session_state.pop("requested_page", None)
        if requested_page in pages:
            st.session_state["page"] = requested_page
        elif "page" not in st.session_state:
            st.session_state["page"] = pages[0]
        page = st.radio("前往", pages, key="page")
    stocks, errors = load_all_stocks()
    render_status(stocks, errors)
    should_scroll_to_top = st.session_state.pop("scroll_to_top", False)
    if page == "股票比較":
        render_comparison(STOCK_FILES, read_stock_data)
    elif page == "條件選股":
        render_stock_picker(stocks)
    else:
        render_single_stock(stocks)
    if should_scroll_to_top:
        scroll_to_top()


if __name__ == "__main__":
    main()
