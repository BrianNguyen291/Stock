"""持股損益：記錄買進成本，依最新收盤價計算未實現損益、報酬率與持股比重。

損益以資料庫中最新一個交易日的收盤價（未還原）計算，未計手續費、證交稅與股利。
持股存在登入者自己的 holdings 資料表；資料庫以「股」為單位，畫面以「張」輸入（1 張 = 1000 股）。
"""

from __future__ import annotations

from datetime import date
import math
import sqlite3

import altair as alt
import pandas as pd
import streamlit as st

import auth
from database import StockDatabase

PAGE_NAME = "持股損益"
LOT_SIZE = 1000
# 類別色依固定順序指派（dataviz 參考色盤，已通過色盲辨識檢查）；超過的股票併入「其他」，不另生顏色。
PIE_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
OTHER_LABEL = "其他"
OTHER_COLOR = "#a3a4aa"
UP_COLOR, DOWN_COLOR = "#e5484d", "#12a594"  # 與首頁一致：台股漲紅跌綠

SUMMARY_COLUMNS = (
    "id", "stock_id", "label", "purchase_date", "purchase_price", "shares", "cost",
    "latest_close", "quote_date", "market_value", "unrealized", "return_pct",
)


# ---------- 計算（不依賴 Streamlit，可直接測試） ----------

def lots_to_shares(lots: float) -> int:
    """張數換算為股數；最小單位 0.001 張（1 股），可記錄零股。"""
    if isinstance(lots, bool) or not isinstance(lots, (int, float)) or not math.isfinite(lots):
        raise ValueError("張數必須是數字")
    shares = round(lots * LOT_SIZE)
    if shares <= 0 or abs(lots * LOT_SIZE - shares) > 1e-6:
        raise ValueError("張數必須大於 0，最小單位為 0.001 張（1 股）")
    return shares


def frames_by_stock_id(stocks: dict[str, pd.DataFrame]) -> dict[str, tuple[str, pd.DataFrame]]:
    """app 以「代號 名稱」為鍵，這裡改成以代號查詢 (顯示名稱, 行情)。"""
    return {label.split(" ", 1)[0]: (label, frame) for label, frame in stocks.items()}


def latest_row(frame: pd.DataFrame | None) -> pd.Series | None:
    """最新一個有收盤價的交易日。"""
    if frame is None or frame.empty or "close" not in frame:
        return None
    priced = frame.dropna(subset=["close"]).sort_values("date")
    return None if priced.empty else priced.iloc[-1]


def holding_summary(holdings: list[dict], stocks_by_id: dict[str, tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    """每筆買進一列；查不到行情的持股，市值與損益為 NaN（不當成 0）。"""
    rows = []
    for lot in holdings:
        label, frame = stocks_by_id.get(lot["stock_id"], (lot["stock_id"], None))
        quote = latest_row(frame)
        close = float(quote["close"]) if quote is not None else math.nan
        cost = lot["purchase_price"] * lot["shares"]
        value = close * lot["shares"]
        rows.append({
            "id": lot["id"], "stock_id": lot["stock_id"], "label": label,
            "purchase_date": lot["purchase_date"], "purchase_price": lot["purchase_price"],
            "shares": lot["shares"], "cost": cost, "latest_close": close,
            "quote_date": pd.Timestamp(quote["date"]).date() if quote is not None else None,
            "market_value": value, "unrealized": value - cost,
            "return_pct": (value - cost) / cost * 100,
        })
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def portfolio_totals(summary: pd.DataFrame) -> dict:
    """只加總有報價的持股，成本與市值才可比較。"""
    priced = summary.dropna(subset=["market_value"])
    cost = float(priced["cost"].sum())
    value = float(priced["market_value"].sum())
    pnl = value - cost
    return {
        "cost": cost, "market_value": value, "unrealized": pnl,
        "return_pct": pnl / cost * 100 if cost else math.nan,
        "unpriced": len(summary) - len(priced),
    }


def position_weights(summary: pd.DataFrame, max_slices: int = len(PIE_COLORS)) -> pd.DataFrame:
    """依股票加總市值算比重；超過 max_slices 檔時，市值最小的併入「其他」。"""
    priced = summary.dropna(subset=["market_value"])
    priced = priced[priced["market_value"] > 0]
    if priced.empty:
        return pd.DataFrame(columns=["stock_id", "label", "market_value", "weight_pct"])
    grouped = (
        priced.groupby(["stock_id", "label"], as_index=False)["market_value"].sum()
        .sort_values(["market_value", "stock_id"], ascending=[False, True])
    )
    if len(grouped) > max_slices:
        kept, rest = grouped.iloc[:max_slices - 1], grouped.iloc[max_slices - 1:]
        other = pd.DataFrame([{"stock_id": "", "label": OTHER_LABEL, "market_value": rest["market_value"].sum()}])
        grouped = pd.concat([kept, other], ignore_index=True)
    grouped["weight_pct"] = grouped["market_value"] / grouped["market_value"].sum() * 100
    return grouped.reset_index(drop=True)


def weight_chart(weights: pd.DataFrame) -> alt.Chart:
    """持股市值比重圓環圖。顏色依股票代號固定順序指派，不隨市值排名變動。"""
    stocks = sorted(label for label in weights["label"] if label != OTHER_LABEL)
    domain = stocks + ([OTHER_LABEL] if OTHER_LABEL in set(weights["label"]) else [])
    colors = list(PIE_COLORS[:len(stocks)]) + ([OTHER_COLOR] if OTHER_LABEL in domain else [])
    data = weights.assign(order=range(len(weights)))
    return alt.Chart(data).mark_arc(innerRadius=70, stroke="#ffffff", strokeWidth=2).encode(
        theta=alt.Theta("market_value:Q", stack=True),
        color=alt.Color(
            "label:N", scale=alt.Scale(domain=domain, range=colors),
            legend=alt.Legend(title=None, orient="right", labelFontSize=13),
        ),
        order=alt.Order("order:Q"),
        tooltip=[
            alt.Tooltip("label:N", title="股票"),
            alt.Tooltip("market_value:Q", title="市值（元）", format=",.0f"),
            alt.Tooltip("weight_pct:Q", title="比重（%）", format=".2f"),
        ],
    ).properties(height=300)


# ---------- 畫面 ----------

def signed_in_user_id(navigate=None, feature: str = PAGE_NAME) -> int | None:
    """回傳登入者的資料庫 ID；訪客或資料庫錯誤時顯示提示並回傳 None。"""
    try:
        user_id = auth.current_user_id()
    except (OSError, sqlite3.Error, ValueError):
        st.error("目前無法載入使用者資料，請稍後再試。")
        return None
    if user_id is None:
        st.info(f"登入後即可使用「{feature}」，資料只有你自己看得到。")
        if navigate is not None:
            st.button("前往登入", on_click=navigate, args=("登入",), key=f"login_for_{feature}")
    return user_id


def _money(value: float) -> str:
    return "-" if value is None or pd.isna(value) else f"{value:,.0f}"


def _pnl_color(value) -> str:
    if value is None or pd.isna(value) or value == 0:
        return ""
    return f"color: {UP_COLOR if value > 0 else DOWN_COLOR}"


def _render_add_form(database: StockDatabase, user_id: int, stock_labels: list[str]) -> None:
    with st.form("portfolio_add", clear_on_submit=True, border=True):
        st.markdown("**新增持股**")
        stock_col, price_col, lots_col, date_col = st.columns([2, 1, 1, 1])
        label = stock_col.selectbox("股票", stock_labels, key="portfolio_stock")
        price = price_col.number_input("買進價（元）", min_value=0.01, value=None, step=0.5,
                                       format="%.2f", key="portfolio_price")
        lots = lots_col.number_input("張數", min_value=0.001, value=1.0, step=1.0,
                                     format="%.3f", key="portfolio_lots",
                                     help="1 張 = 1000 股；零股請輸入小數，例如 0.25 張 = 250 股")
        bought = date_col.date_input("買進日期", value=date.today(), max_value=date.today(),
                                     key="portfolio_date")
        submitted = st.form_submit_button("加入持股", type="primary", key="portfolio_submit")
    if not submitted:
        return
    if not label:
        st.error("請選擇股票。")
        return
    if price is None:
        st.error("請輸入買進價。")
        return
    try:
        shares = lots_to_shares(lots)
        database.add_holding(user_id, label.split(" ", 1)[0], purchase_price=float(price),
                             shares=shares, purchase_date=bought)
    except ValueError as exc:
        st.error(str(exc))
        return
    except (OSError, sqlite3.Error):
        st.error("持股儲存失敗，請稍後再試。")
        return
    st.success(f"已加入 {label}：{shares / LOT_SIZE:,.3f} 張，買進價 {price:,.2f} 元。")


def _remove_selected(database: StockDatabase, user_id: int) -> None:
    holding_id = st.session_state.get("portfolio_remove_choice")
    if holding_id is not None:
        database.remove_holding(user_id, holding_id)


def _render_overview(summary: pd.DataFrame) -> None:
    if summary.empty:
        st.info("尚未記錄持股，請在下方輸入買進價、張數與日期。")
        return
    totals = portfolio_totals(summary)
    cost_col, value_col, pnl_col, return_col = st.columns(4)
    cost_col.metric("總成本（元）", _money(totals["cost"]))
    value_col.metric("目前市值（元）", _money(totals["market_value"]))
    # delta_color="inverse"：正數顯示紅色，符合台股漲紅跌綠。
    pnl_col.metric("未實現損益（元）", f'{totals["unrealized"]:+,.0f}',
                   f'{totals["return_pct"]:+.2f}%' if not pd.isna(totals["return_pct"]) else None,
                   delta_color="inverse")
    return_col.metric("報酬率", "-" if pd.isna(totals["return_pct"]) else f'{totals["return_pct"]:+.2f}%')
    if totals["unpriced"]:
        st.warning(f'{totals["unpriced"]} 筆持股查無行情，未計入總計與比重。')

    table = pd.DataFrame({
        "股票": summary["label"],
        "買進日期": summary["purchase_date"],
        "買進價": summary["purchase_price"],
        "張數": summary["shares"] / LOT_SIZE,
        "成本（元）": summary["cost"],
        "最新收盤價": summary["latest_close"],
        "收盤日": summary["quote_date"].astype(str).replace("None", "-"),
        "市值（元）": summary["market_value"],
        "未實現損益（元）": summary["unrealized"],
        "報酬率（%）": summary["return_pct"],
    })
    styled = table.style.format({
        "買進價": "{:,.2f}", "張數": "{:,.3f}", "成本（元）": "{:,.0f}", "最新收盤價": "{:,.2f}",
        "市值（元）": "{:,.0f}", "未實現損益（元）": "{:+,.0f}", "報酬率（%）": "{:+.2f}",
    }, na_rep="-").map(_pnl_color, subset=["未實現損益（元）", "報酬率（%）"])
    st.dataframe(styled, hide_index=True, use_container_width=True)
    st.caption("損益以最新收盤價（未還原）計算，未計手續費、證交稅與股利。")

    weights = position_weights(summary)
    if weights.empty:
        return
    st.markdown("**持股比重（依目前市值）**")
    chart_col, table_col = st.columns([3, 2], vertical_alignment="center")
    chart_col.altair_chart(weight_chart(weights), use_container_width=True)
    table_col.dataframe(
        pd.DataFrame({"股票": weights["label"], "市值（元）": weights["market_value"],
                      "比重（%）": weights["weight_pct"]})
        .style.format({"市值（元）": "{:,.0f}", "比重（%）": "{:.2f}"}),
        hide_index=True, use_container_width=True,
    )


def render_portfolio(stocks: dict[str, pd.DataFrame], navigate=None, *, database: StockDatabase | None = None) -> None:
    """持股損益頁。stocks 為 app 已載入的「代號 名稱」→ 行情（含技術指標）。"""
    st.subheader(PAGE_NAME)
    user_id = signed_in_user_id(navigate, PAGE_NAME)
    if user_id is None:
        return
    database = database or StockDatabase()
    by_id = frames_by_stock_id(stocks)
    stock_labels = [label for label, frame in stocks.items() if not frame.empty]

    # 總覽排在最上方，但等表單處理完才填入，剛加入的持股會立即出現。
    overview = st.container()
    if stock_labels:
        _render_add_form(database, user_id, stock_labels)
    else:
        st.warning("目前沒有可選擇的股票行情，請先用上方搜尋框查詢股票。")
    try:
        summary = holding_summary(database.list_holdings(user_id), by_id)
    except (OSError, sqlite3.Error):
        overview.error("目前無法讀取持股，請稍後再試。")
        return
    with overview:
        _render_overview(summary)

    if summary.empty:
        return
    labels = {
        row.id: f"{row.label}｜{row.purchase_date} 買 {row.shares / LOT_SIZE:,.3f} 張 @ {row.purchase_price:,.2f}"
        for row in summary.itertuples()
    }
    pick_col, button_col = st.columns([5, 1.2], vertical_alignment="bottom")
    pick_col.selectbox("刪除持股", list(labels), format_func=labels.get, key="portfolio_remove_choice")
    button_col.button("刪除", key="portfolio_remove", on_click=_remove_selected,
                      args=(database, user_id), use_container_width=True)
