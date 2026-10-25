"""Streamlit stock dashboard for the Taiwan stock data in this repository."""

from pathlib import Path

import pandas as pd
import streamlit as st

from comparison import render_comparison
from data_processor import load_data


DATA_DIR = Path(__file__).parent
STOCK_FILES = {
    "2327 國巨": DATA_DIR / "2327.csv",
    "2492 華新科": DATA_DIR / "2492.csv",
}


@st.cache_data
def read_stock_data(path: str) -> pd.DataFrame:
    """Load and process a stock CSV for the dashboard."""
    return load_data(path)


def format_number(value: float | int | None, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.2f}{suffix}"


def main() -> None:
    st.set_page_config(
        page_title="台股投資儀表板",
        page_icon="📈",
        layout="wide",
    )
    st.title("📈 台股投資儀表板")
    st.caption("使用台灣證券交易所日交易資料，快速查看股價與成交量變化。")

    with st.sidebar:
        st.header("查詢條件")
        page = st.radio("功能", ["個股儀表板", "股票比較"], horizontal=True)

    if page == "股票比較":
        render_comparison(STOCK_FILES, read_stock_data)
        return

    with st.sidebar:
        stock_label = st.selectbox("選擇股票", list(STOCK_FILES))

    data_path = STOCK_FILES[stock_label]
    try:
        data = read_stock_data(str(data_path))
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        st.error(f"無法載入 {stock_label} 的資料：{exc}")
        st.stop()

    if data.empty:
        st.warning("目前沒有可顯示的資料。")
        st.stop()

    min_date = data["date"].min().date()
    max_date = data["date"].max().date()
    with st.sidebar:
        selected_dates = st.date_input(
            "日期範圍",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )

    if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
        start_date, end_date = selected_dates
    else:
        start_date = end_date = selected_dates

    filtered = data[
        data["date"].dt.date.between(start_date, end_date)
    ].copy()
    if filtered.empty:
        st.warning("選定日期範圍沒有資料，請重新選擇日期。")
        st.stop()

    latest = filtered.iloc[-1]
    previous = filtered.iloc[-2] if len(filtered) > 1 else None
    close_change = latest["price_change"] if previous is not None else None
    close_change_percent = latest["change_percent"] if previous is not None else None

    st.subheader(f"{latest.get('stock_name', stock_label)}（{latest['stock_id']}）")
    metric_columns = st.columns(4)
    metric_columns[0].metric(
        "最新收盤價",
        format_number(latest["close"]),
        f"{format_number(close_change)} ({format_number(close_change_percent, '%')})",
    )
    metric_columns[1].metric("區間最高價", format_number(filtered["high"].max()))
    metric_columns[2].metric("區間最低價", format_number(filtered["low"].min()))
    metric_columns[3].metric(
        "最新成交量",
        format_number(latest.get("trade_volume")),
        "股",
    )

    chart_data = filtered.set_index("date")[["close"]].rename(columns={"close": "收盤價"})
    st.subheader("收盤價走勢")
    st.line_chart(chart_data)

    left, right = st.columns(2)
    with left:
        st.subheader("成交量走勢")
        volume_data = filtered.set_index("date")[["trade_volume"]].rename(
            columns={"trade_volume": "成交量"}
        )
        st.bar_chart(volume_data)
    with right:
        st.subheader("交易資料")
        display_columns = {
            "date": "日期",
            "open": "開盤價",
            "high": "最高價",
            "low": "最低價",
            "close": "收盤價",
            "change": "漲跌價差",
            "trade_volume": "成交量",
        }
        table = filtered[list(display_columns)].rename(columns=display_columns)
        table["日期"] = table["日期"].dt.strftime("%Y-%m-%d")
        st.dataframe(table.sort_values("日期", ascending=False), hide_index=True)


if __name__ == "__main__":
    main()
