"""價格提醒通知，依帳號保存已讀及刪除狀態。"""

from html import escape
import sqlite3

import streamlit as st

import auth
from alerts import evaluate_alerts
from database import StockDatabase


def sync_notifications(database: StockDatabase, user_id: int) -> None:
    alerts = database.list_alerts(user_id)
    stocks = {}
    for stock_id in {alert["stock_id"] for alert in alerts}:
        stocks[stock_id] = database.query_prices(stock_id)
    events = []
    for result in evaluate_alerts(alerts, stocks):
        if result["triggered"]:
            result["event_key"] = (
                f'{result["id"]}|{result["stock_id"]}|{result["kind"]}|'
                f'{result["threshold"]}|{result["date"]}'
            )
            frame = stocks[result["stock_id"]]
            name = frame.iloc[-1].get("stock_name") or ""
            result["label"] = f'{result["stock_id"]} {name}'.strip()
            events.append(result)
    database.save_alert_notifications(user_id, events)


def _read_notifications(database: StockDatabase, user_id: int, ids: list[int]) -> None:
    if database is not None and st.session_state.get("notifications_popover"):
        try:
            database.read_alert_notifications(user_id, ids)
        except (OSError, sqlite3.Error):
            st.error("目前無法更新通知狀態，請稍後再試。")


def _dismiss_notification(database: StockDatabase, user_id: int, notification_id: int) -> None:
    try:
        database.dismiss_alert_notification(user_id, notification_id)
    except (OSError, sqlite3.Error):
        st.error("通知刪除失敗，請稍後再試。")


def render_notification_bell() -> None:
    notifications = []
    database = None
    user_id = None
    error = False
    try:
        user_id = auth.current_user_id()
        if user_id is not None:
            database = StockDatabase()
            sync_notifications(database, user_id)
            notifications = database.list_alert_notifications(user_id)
    except (OSError, sqlite3.Error, ValueError):
        error = True

    unread = sum(not notice["is_read"] for notice in notifications)
    badge = ""
    if unread:
        badge = f"""
        .st-key-notification_bell::after {{
            content: "{unread}"; position: absolute; top: -3px; right: -3px;
            min-width: 18px; height: 18px; padding: 0 4px;
            box-sizing: border-box; border-radius: 999px;
            background: #e5484d; color: white; font: 600 11px/18px Inter, sans-serif;
            text-align: center; pointer-events: none; z-index: 2;
        }}
        """
    st.html(f"""
        <style>
        .st-key-notification_bell {{position: relative; width: 2.5rem; flex: 0 0 2.5rem;}}
        .st-key-notification_bell [data-testid="stPopoverButton"] {{
            width: 2.5rem; min-height: 2.5rem; padding: 0; border-radius: 999px;
            display: flex; align-items: center; justify-content: center;
        }}
        .st-key-notification_bell [data-testid="stPopoverButton"] > div {{
            margin: 0; gap: 0; max-width: 100%; justify-content: center;
        }}
        .st-key-notification_bell [data-testid="stPopoverButton"]:hover {{background: #ebebed;}}
        .st-key-notification_bell [data-testid="stPopoverButton"] [data-testid="stMarkdownContainer"] {{
            position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%);
        }}
        .st-key-notification_bell [data-testid="stPopoverButton"] > div > [aria-hidden="true"] {{display: none;}}
        .st-key-notification_list {{
            min-width: min(340px, 70vw); max-width: 420px; max-height: 65vh;
            overflow-y: auto; padding: .5rem .75rem .75rem;
        }}
        .st-key-notification_list > [data-testid="stVerticalBlock"] {{gap: .5rem;}}
        [class*="st-key-notification_item_"] {{
            padding: 1rem .75rem; border-radius: 12px;
            transition: background-color .16s ease;
        }}
        [class*="st-key-notification_item_"]:hover,
        [class*="st-key-notification_item_"]:focus-within {{background-color: #f4f4f5;}}
        [class*="st-key-notification_item_"] > [data-testid="stVerticalBlock"] {{gap: .5rem;}}
        .notification-separator {{border-top: 1px solid #e9e9ec; margin: 0 .75rem;}}
        [class*="st-key-notification_item_"] [data-testid="stButton"] button {{
            border: 0; background: transparent; box-shadow: none; padding: .2rem;
            min-height: 1.8rem; width: 1.8rem;
        }}
        [class*="st-key-notification_item_"] [data-testid="stButton"] button:hover {{background: #f4f4f5;}}
        .notification-title {{font-weight: 600; margin: .25rem 0;}}
        .notification-detail {{font-size: .9rem; margin: .25rem 0;}}
        {badge}
        </style>
    """)
    with st.container(key="notification_bell"):
        with st.popover(
            "通知", icon=":material/notifications:", key="notifications_popover",
            help="查看通知",
            on_change=_read_notifications,
            args=(database, user_id, [notice["id"] for notice in notifications]),
        ):
            with st.container(key="notification_list", height="content", border=False):
                st.markdown("**通知**")
                if error:
                    st.error("目前無法載入通知，請稍後再試。")
                elif user_id is None:
                    st.caption("登入後可查看價格提醒通知。")
                elif not notifications:
                    st.caption("目前沒有通知。")
                for index, notice in enumerate(notifications):
                    if index:
                        st.html('<div class="notification-separator" role="separator"></div>')
                    with st.container(key=f'notification_item_{notice["id"]}', border=False):
                        title, close = st.columns([6, 1], vertical_alignment="top")
                        title.html(f'<p class="notification-title">{escape(notice["stock_label"])}</p>')
                        close.button(
                            "×", key=f'notification_dismiss_{notice["id"]}',
                            help="刪除這則通知", on_click=_dismiss_notification,
                            args=(database, user_id, notice["id"]),
                        )
                        st.html(
                            f'<p class="notification-detail">{escape(notice["condition"])}</p>'
                            f'<p class="notification-detail">{escape(notice["actual"])}</p>'
                        )
                        st.caption(notice["trade_date"])
