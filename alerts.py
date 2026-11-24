"""價格提醒：股價跌破／突破指定價位、爆量，以最新交易日資料判斷是否觸發。

- 跌破：收盤價 < 門檻；突破：收盤價 > 門檻（等於門檻不觸發）。
- 爆量：成交量 > 20 日均量 × 門檻倍數；均量資料不足時不判斷，並說明原因。
提醒存在登入者自己的 alerts 資料表，只在頁面上顯示，不寄信或推播。
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from database import StockDatabase
from portfolio import LOT_SIZE, frames_by_stock_id, latest_row, signed_in_user_id

PAGE_NAME = "價格提醒"
KIND_LABELS = {"price_below": "股價跌破", "price_above": "股價突破", "volume_above": "爆量"}
DEFAULT_VOLUME_MULTIPLE = 2.0


# ---------- 判斷（不依賴 Streamlit，可直接測試） ----------

def describe_condition(kind: str, threshold: float) -> str:
    if kind == "price_below":
        return f"收盤價跌破 {threshold:,.2f} 元"
    if kind == "price_above":
        return f"收盤價突破 {threshold:,.2f} 元"
    if kind == "volume_above":
        return f"成交量超過 20 日均量 {threshold:g} 倍"
    raise ValueError(f"不支援的提醒類型：{kind}")


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def evaluate_alert(alert: dict, frame: pd.DataFrame | None) -> dict:
    """triggered 為 True／False；資料不足無法判斷時為 None，原因放在 note。"""
    result = {
        "id": alert["id"], "stock_id": alert["stock_id"], "kind": alert["kind"],
        "threshold": alert["threshold"], "condition": describe_condition(alert["kind"], alert["threshold"]),
        "date": None, "actual": "", "triggered": None, "note": "",
    }
    row = latest_row(frame)
    if row is None:
        result["note"] = "查無行情資料"
        return result
    result["date"] = pd.Timestamp(row["date"]).date()
    close = float(row["close"])
    if alert["kind"] in ("price_below", "price_above"):
        result["actual"] = f"收盤價 {close:,.2f} 元"
        below = alert["kind"] == "price_below"
        result["triggered"] = close < alert["threshold"] if below else close > alert["threshold"]
        return result

    volume, average = _number(row.get("trade_volume")), _number(row.get("volume_ma_20"))
    if volume is None or average is None or average <= 0:
        result["note"] = "20 日均量資料不足"
        return result
    ratio = volume / average
    result["actual"] = (
        f"成交量 {volume / LOT_SIZE:,.0f} 張，為 20 日均量 {average / LOT_SIZE:,.0f} 張的 {ratio:.2f} 倍"
    )
    result["triggered"] = volume > alert["threshold"] * average
    return result


def evaluate_alerts(alerts: list[dict], stocks: dict[str, pd.DataFrame]) -> list[dict]:
    by_id = frames_by_stock_id(stocks)
    results = []
    for alert in alerts:
        label, frame = by_id.get(alert["stock_id"], (alert["stock_id"], None))
        results.append({"label": label, **evaluate_alert(alert, frame)})
    return results


# ---------- 畫面 ----------

def _remove_alert(database: StockDatabase, user_id: int, alert_ids: list[int]) -> None:
    click = st.session_state.get("alert_remove_click")
    if click is None:
        return
    try:
        database.remove_alert(user_id, alert_ids[click["row"]])
    except (OSError, sqlite3.Error, ValueError):
        st.error("提醒刪除失敗，請稍後再試。")


def _render_add_alert(database: StockDatabase, user_id: int, stocks: dict[str, pd.DataFrame]) -> None:
    labels = [label for label, frame in stocks.items() if not frame.empty]
    if not labels:
        st.warning("目前沒有可選擇的股票行情，請先用上方搜尋框查詢股票。")
        return
    with st.container(border=True):
        st.markdown("**新增提醒**")
        stock_col, kind_col, threshold_col = st.columns([2, 2, 1.4], vertical_alignment="bottom")
        label = stock_col.selectbox("股票", labels, key="alert_stock")
        kind = kind_col.radio("條件", list(KIND_LABELS), format_func=KIND_LABELS.get,
                              horizontal=True, key="alert_kind")
        row = latest_row(stocks[label])
        stock_id = label.split(" ", 1)[0]
        if kind == "volume_above":
            threshold = threshold_col.number_input(
                "幾倍 20 日均量", min_value=1.0, value=DEFAULT_VOLUME_MULTIPLE, step=0.5,
                format="%.1f", key=f"alert_multiple_{stock_id}",
            )
        else:
            # 預設帶入最新收盤價，使用者只需微調；每檔股票各自一個輸入框，切換股票時才會更新預設值。
            threshold = threshold_col.number_input(
                "價位（元）", min_value=0.01, value=float(row["close"]) if row is not None else None,
                step=0.5, format="%.2f", key=f"alert_price_{kind}_{stock_id}",
            )
        if row is not None:
            average = _number(row.get("volume_ma_20"))
            st.caption(
                f'{pd.Timestamp(row["date"]):%Y-%m-%d} 收盤價 {float(row["close"]):,.2f} 元，'
                f'成交量 {float(row["trade_volume"]) / LOT_SIZE:,.0f} 張'
                + (f"，20 日均量 {average / LOT_SIZE:,.0f} 張" if average else "")
            )
        if not st.button("加入提醒", type="primary", key="alert_submit"):
            return
    if threshold is None:
        st.error("請輸入提醒門檻。")
        return
    try:
        database.add_alert(user_id, stock_id, kind=kind, threshold=float(threshold))
    except ValueError as exc:
        st.error(str(exc))
        return
    except (OSError, sqlite3.Error):
        st.error("提醒儲存失敗，請稍後再試。")
        return
    st.rerun()


def _status(result: dict) -> str:
    if result["triggered"] is None:
        return f'⚠️ 無法判斷：{result["note"]}'
    return "🔔 已觸發" if result["triggered"] else "未觸發"


def render_alerts(stocks: dict[str, pd.DataFrame], navigate=None, *, database: StockDatabase | None = None) -> None:
    """價格提醒頁，提供新增條件及查看、刪除已設定的提醒。"""
    st.subheader(PAGE_NAME)
    user_id = signed_in_user_id(navigate, PAGE_NAME)
    if user_id is None:
        return
    database = database or StockDatabase()
    _render_add_alert(database, user_id, stocks)
    try:
        results = evaluate_alerts(database.list_alerts(user_id), stocks)
    except (OSError, sqlite3.Error):
        st.error("目前無法讀取提醒，請稍後再試。")
        return

    if not results:
        st.info("尚未設定提醒。")
        return
    st.markdown("**我的提醒**")
    table = pd.DataFrame({
        "股票": [r["label"] for r in results],
        "提醒條件（門檻）": [r["condition"] for r in results],
        "資料日期": [f'{r["date"]:%Y-%m-%d}' if r["date"] else "-" for r in results],
        "實際數值": [r["actual"] or "-" for r in results],
        "狀態": [_status(r) for r in results],
        "刪除": ["刪除"] * len(results),
    })
    st.dataframe(
        table, hide_index=True, use_container_width=True,
        column_config={
            "刪除": st.column_config.ButtonColumn(
                "刪除", width="small", type="secondary",
                key="alert_remove_click", on_click=_remove_alert,
                args=(database, user_id, [r["id"] for r in results]),
            ),
        },
    )
    st.caption("以最新交易日的收盤價與成交量判斷；等於門檻不算觸發。")
