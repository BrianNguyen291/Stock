"""股票比較與進階分析：多檔股票走勢比較、漲跌幅比較、期間績效分析與排行榜。

儀表板由 app.py 呼叫 ``render_comparison``，也可直接執行：
    python comparison.py --input 2327.csv 2492.csv --start 2026-01-01 --end 2026-10-21

注意：報酬率皆以未還原權值的收盤價計算，除權息日的價格落差會反映在漲跌幅中。
"""

import argparse
import logging
import math
import sqlite3
from datetime import date
from pathlib import Path
from typing import Callable

import pandas as pd

from data_processor import load_data

LOGGER = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252
# 排行榜可選指標：指標名稱 -> 是否由小到大排序（數值越小排名越前）
RANKING_METRICS = {
    "區間報酬率(%)": False,
    "年化波動度(%)": True,
    "最大回撤(%)": False,
    "上漲天數比例(%)": False,
    "平均成交量(股)": False,
}
DEFAULT_RANKING_METRIC = "區間報酬率(%)"


def filter_period(data: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """取出 start～end（含）之間的資料列，未指定則不限制。"""
    frame = data
    if start is not None:
        frame = frame[frame["date"] >= pd.Timestamp(start)]
    if end is not None:
        frame = frame[frame["date"] <= pd.Timestamp(end)]
    return frame.reset_index(drop=True)


def align_close_prices(stocks: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """將多檔股票的收盤價依日期對齊，只保留所有股票都有交易的日期。"""
    closes = pd.DataFrame(
        {label: data.set_index("date")["close"].astype(float) for label, data in stocks.items()}
    )
    return closes.sort_index().dropna()


def cumulative_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """以區間第一天為基準，計算每日累積報酬率（%）。"""
    return (closes / closes.iloc[0] - 1) * 100


def daily_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """以對齊後的收盤價計算每日漲跌幅（%），第一天沒有前一日故不列入。"""
    return closes.pct_change().iloc[1:] * 100


def monthly_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """計算每月漲跌幅（%）；第一個月以區間第一天收盤價為基準。"""
    month_end = closes.groupby(closes.index.to_period("M")).last()
    previous = month_end.shift(1)
    previous.iloc[0] = closes.iloc[0]
    result = (month_end / previous - 1) * 100
    result.index = result.index.astype(str)
    return result


def winning_days(returns: pd.DataFrame) -> pd.Series:
    """統計每檔股票在區間內「當日漲幅最高」的天數。"""
    return returns.idxmax(axis=1).value_counts().reindex(returns.columns, fill_value=0)


def period_performance(data: pd.DataFrame) -> dict:
    """計算單一股票在資料區間內的績效指標。"""
    frame = data.dropna(subset=["close"])
    if len(frame) < 2:
        raise ValueError("區間內至少需要兩個交易日才能計算績效")

    close = frame["close"].astype(float)
    returns = close.pct_change().dropna() * 100
    drawdown = (close / close.cummax() - 1) * 100
    high = frame["high"] if "high" in frame.columns else close
    low = frame["low"] if "low" in frame.columns else close
    volume = frame["trade_volume"] if "trade_volume" in frame.columns else pd.Series(dtype=float)

    return {
        "起始日": frame["date"].iloc[0].date(),
        "結束日": frame["date"].iloc[-1].date(),
        "交易日數": len(frame),
        "起始收盤價": close.iloc[0],
        "結束收盤價": close.iloc[-1],
        "區間報酬率(%)": (close.iloc[-1] / close.iloc[0] - 1) * 100,
        "區間最高價": float(high.max()),
        "區間最低價": float(low.min()),
        "平均日漲跌幅(%)": returns.mean(),
        "最大單日漲幅(%)": returns.max(),
        "最大單日跌幅(%)": returns.min(),
        "上漲天數比例(%)": (returns > 0).mean() * 100,
        "年化波動度(%)": returns.std() * math.sqrt(TRADING_DAYS_PER_YEAR),
        "最大回撤(%)": drawdown.min(),
        "平均成交量(股)": float(volume.mean()) if not volume.empty else math.nan,
    }


def performance_table(stocks: dict[str, pd.DataFrame], start=None, end=None) -> pd.DataFrame:
    """彙整多檔股票的期間績效，每列一檔股票。"""
    rows = {label: period_performance(filter_period(data, start, end)) for label, data in stocks.items()}
    table = pd.DataFrame.from_dict(rows, orient="index")
    table.index.name = "股票"
    return table


def rank_stocks(table: pd.DataFrame, metric: str = DEFAULT_RANKING_METRIC) -> pd.DataFrame:
    """依指定指標排序並加上名次。"""
    if metric not in RANKING_METRICS:
        raise ValueError(f"不支援的排名指標：{metric}，可用：{', '.join(RANKING_METRICS)}")
    ranked = table.sort_values(metric, ascending=RANKING_METRICS[metric], kind="stable").reset_index()
    ranked.insert(0, "排名", range(1, len(ranked) + 1))
    return ranked


def _format_cell(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(value):
            return "-"
        return f"{value:,.0f}" if float(value).is_integer() and abs(value) >= 1000 else f"{value:,.2f}"
    return str(value)


def render_comparison(
    stock_files: dict[str, Path],
    loader: Callable[[str], pd.DataFrame] = load_data,
) -> None:
    """在 Streamlit 頁面中繪製股票比較與進階分析區塊。"""
    import streamlit as st

    st.subheader("股票比較與進階分析")
    labels = list(stock_files)
    with st.sidebar:
        selected = st.multiselect("選擇比較股票", labels, default=labels[:2])
    if len(selected) < 2:
        st.info("請至少選擇兩檔股票進行比較。")
        return

    stocks = {}
    for label in selected:
        try:
            stocks[label] = loader(str(stock_files[label]))
        except (OSError, sqlite3.Error, ValueError, pd.errors.ParserError) as exc:
            st.error(f"無法載入 {label} 的資料：{exc}")
            return

    all_closes = align_close_prices(stocks)
    if len(all_closes) < 2:
        st.warning("所選股票沒有足夠的共同交易日可以比較。")
        return

    min_date = all_closes.index.min().date()
    max_date = all_closes.index.max().date()
    with st.sidebar:
        selected_dates = st.date_input(
            "比較區間",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            key="comparison_dates",
        )
    if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
        start_date, end_date = selected_dates
    else:
        st.info("請選擇比較區間的結束日期。")
        return

    closes = all_closes.loc[pd.Timestamp(start_date):pd.Timestamp(end_date)]
    if len(closes) < 2:
        st.warning("選定區間內共同交易日不足兩天，請重新選擇日期。")
        return
    period_start, period_end = closes.index[0], closes.index[-1]
    table = performance_table(stocks, period_start, period_end)

    metric_columns = st.columns(len(selected))
    for column, label in zip(metric_columns, selected):
        column.metric(
            f"{label} 區間報酬率",
            f"{table.loc[label, '區間報酬率(%)']:+.2f}%",
            f"收盤 {table.loc[label, '結束收盤價']:,.2f}",
            delta_color="off",
        )

    st.subheader("累積報酬率走勢（%）")
    st.caption("以區間第一個共同交易日為基準 0%，消除股價高低差異，直接比較漲跌表現。")
    st.line_chart(cumulative_returns(closes))

    returns = daily_returns(closes)
    st.subheader("漲跌幅比較")
    daily_tab, monthly_tab = st.tabs(["每日漲跌幅（%）", "每月漲跌幅（%）"])
    with daily_tab:
        st.line_chart(returns)
    with monthly_tab:
        st.bar_chart(monthly_returns(closes), stack=False)

    left, right = st.columns(2)
    with left:
        st.markdown("**日漲跌幅相關係數**")
        correlation = returns.corr()
        if len(selected) == 2:
            st.metric("相關係數", f"{correlation.iloc[0, 1]:.2f}", help="越接近 1 代表兩檔股票漲跌越同步")
        else:
            st.dataframe(correlation.round(2))
    with right:
        st.markdown("**當日漲幅最高的天數**")
        wins = winning_days(returns).rename("天數").to_frame()
        wins["比例(%)"] = (wins["天數"] / len(returns) * 100).round(1)
        st.dataframe(wins)

    st.subheader("期間績效分析")
    st.caption(f"{period_start:%Y-%m-%d} ～ {period_end:%Y-%m-%d}，以未還原權值的收盤價計算。")
    display = table.T.map(_format_cell)
    st.dataframe(display)

    st.subheader("排行榜")
    metric = st.selectbox("排名依據", list(RANKING_METRICS), key="ranking_metric")
    ranked = rank_stocks(table, metric)
    ranking_columns = ["排名", "股票", metric] + [
        name for name in ("區間報酬率(%)", "年化波動度(%)", "最大回撤(%)") if name != metric
    ]
    st.dataframe(ranked[ranking_columns].round(2), hide_index=True)

    st.markdown(f"**{period_end:%Y-%m-%d} 當日漲跌幅排行**")
    latest = returns.iloc[-1].sort_values(ascending=False).rename("漲跌幅(%)").round(2)
    latest_table = latest.reset_index().rename(columns={"index": "股票"})
    latest_table.insert(0, "排名", range(1, len(latest_table) + 1))
    st.dataframe(latest_table, hide_index=True)


def _stock_label(data: pd.DataFrame, path: Path) -> str:
    if {"stock_id", "stock_name"} <= set(data.columns) and not data.empty:
        return f"{data['stock_id'].iloc[0]} {data['stock_name'].iloc[0]}"
    return path.stem


def main() -> None:
    parser = argparse.ArgumentParser(description="多檔股票期間績效比較與排行")
    parser.add_argument("--input", type=Path, nargs="+", required=True, help="兩個以上的股票 CSV 路徑")
    parser.add_argument("--start", type=date.fromisoformat, help="開始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, help="結束日期 YYYY-MM-DD")
    parser.add_argument("--rank-by", choices=list(RANKING_METRICS), default=DEFAULT_RANKING_METRIC)
    parser.add_argument("--output", type=Path, help="將排行結果輸出成 CSV")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    if len(args.input) < 2:
        parser.error("至少需要兩檔股票才能比較")
    try:
        stocks = {}
        for path in args.input:
            data = load_data(path)
            stocks[_stock_label(data, path)] = data
        ranked = rank_stocks(performance_table(stocks, args.start, args.end), args.rank_by)
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        parser.error(str(exc))

    closes = align_close_prices({label: filter_period(data, args.start, args.end) for label, data in stocks.items()})
    with pd.option_context("display.unicode.east_asian_width", True, "display.width", 200):
        print(ranked.set_index("排名").round(2).T.to_string())
        print("\n日漲跌幅相關係數：")
        print(daily_returns(closes).corr().round(2).to_string())

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        ranked.to_csv(args.output, index=False, encoding="utf-8-sig")
        LOGGER.info("已輸出排行結果至 %s", args.output.resolve())


if __name__ == "__main__":
    main()
