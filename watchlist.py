"""登入使用者的個股最愛清單與側邊欄介面。"""

import sqlite3

import pandas as pd
import streamlit as st

import auth
from database import StockDatabase


def current_watchlist(database: StockDatabase | None = None) -> tuple[int | None, list[str]]:
    """回傳目前登入者的資料庫 ID 與最愛代號。訪客回傳 (None, [])。"""
    try:
        user_id = auth.current_user_id()
        if user_id is None:
            return None, []
        watchlist = (database or StockDatabase()).list_watchlist(user_id)
        return user_id, watchlist
    except (OSError, sqlite3.Error, ValueError):
        st.sidebar.error("目前無法載入我的最愛")
        return None, []


def favorite_first(labels: list[str], favorite_ids: list[str]) -> list[str]:
    """將最愛股票排在一般股票前面，並保留原排序。"""
    return sorted(
        labels,
        key=lambda label: (label.split(" ", 1)[0] not in favorite_ids, label),
    )


def render_watchlist_toggle(stock_id: str, latest_name: str) -> None:
    """在個股頁顯示加入／移除最愛按鈕。"""
    watchlist_col, watchlist_note = st.columns([1, 5], vertical_alignment="center")

    def open_login() -> None:
        st.session_state["active_page"] = "登入"
        st.session_state["scroll_to_top"] = True

    def add_favorite() -> None:
        try:
            user_id = auth.current_user_id()
            if user_id is None:
                st.session_state["watchlist_action_message"] = "請先登入後再加入最愛"
                return
            database = StockDatabase()
            if database.add_to_watchlist(user_id, stock_id):
                st.session_state["watchlist_action_message"] = f"已加入最愛：{stock_id} {latest_name}"
            else:
                st.session_state["watchlist_action_message"] = f"{stock_id} 已在最愛清單中"
        except (OSError, sqlite3.Error, ValueError):
            st.session_state["watchlist_action_message"] = "目前無法加入最愛，請稍後再試"

    def remove_favorite() -> None:
        try:
            user_id = auth.current_user_id()
            if user_id is None:
                st.session_state["watchlist_action_message"] = "請先登入後再移除最愛"
                return
            database = StockDatabase()
            database.remove_from_watchlist(user_id, stock_id)
            st.session_state["watchlist_action_message"] = f"已從最愛移除：{stock_id} {latest_name}"
        except (OSError, sqlite3.Error, ValueError):
            st.session_state["watchlist_action_message"] = "目前無法移除最愛，請稍後再試"

    try:
        user_id = auth.current_user_id()
    except (OSError, sqlite3.Error, ValueError):
        user_id = None
        st.error("目前無法載入收藏狀態，請稍後再試。")

    if user_id is None:
        watchlist_col.button(
            "☆ 加入最愛",
            key=f"watchlist_guest_{stock_id}",
            use_container_width=True,
            on_click=open_login,
        )
        watchlist_note.caption("尚未登入；點按鈕前往登入頁")
        return

    try:
        database = StockDatabase()
        in_watchlist = stock_id in database.list_watchlist(user_id)
        if in_watchlist:
            watchlist_col.button(
                "⭐ 移除最愛",
                key=f"watchlist_remove_{stock_id}",
                use_container_width=True,
                on_click=remove_favorite,
            )
        else:
            watchlist_col.button(
            "☆ 加入最愛",
            key=f"watchlist_add_{stock_id}",
            use_container_width=True,
            on_click=add_favorite,
        )
    except (OSError, sqlite3.Error, ValueError):
        st.error("目前無法更新收藏狀態，請稍後再試。")


def render_watchlist_sidebar(
    stock_labels: list[str],
    selected_stock: str,
    navigate,
    page: str,
    database: StockDatabase | None = None,
) -> tuple[str | None, list[str], str | None]:
    """渲染完整最愛清單、股票選單與更新後的選中股票。"""
    database = database or StockDatabase()
    user_id, favorite_ids = current_watchlist(database)
    sidebar_labels = favorite_first(stock_labels, favorite_ids)

    with st.sidebar:
        if user_id is not None:
            if favorite_ids:
                st.markdown(f"### 我的最愛清單（{len(favorite_ids)} 檔）")
                st.caption("新增或移除後，清單與股票選單會立即重新排序")
                for favorite_id in favorite_ids:
                    favorite_label = next(
                        (
                            label
                            for label in stock_labels
                            if label.split(" ", 1)[0] == favorite_id
                        ),
                        favorite_id,
                    )
                    with st.container(
                        border=True,
                        key=f"sidebar_watchlist_card_{favorite_id}",
                    ):
                        open_col, remove_col = st.columns(
                            [4, 1],
                            vertical_alignment="center",
                        )
                        if open_col.button(
                            favorite_label,
                            key=f"sidebar_watchlist_open_{favorite_id}",
                            use_container_width=True,
                        ):
                            navigate("個股儀表板", favorite_label)
                            st.rerun()
                        if remove_col.button(
                            "×",
                            key=f"sidebar_watchlist_remove_{favorite_id}",
                            help=f"從最愛移除 {favorite_id}",
                        ):
                            try:
                                database.remove_from_watchlist(user_id, favorite_id)
                                st.rerun()
                            except (OSError, sqlite3.Error, ValueError):
                                st.error("目前無法移除最愛，請稍後再試")

                        try:
                            favorite_data = database.query_prices(favorite_id)
                        except (OSError, sqlite3.Error, ValueError):
                            favorite_data = pd.DataFrame()
                        if favorite_data.empty:
                            st.caption("尚無價格資料")
                        else:
                            latest = favorite_data.iloc[-1]
                            close = latest.get("close")
                            change_percent = latest.get("change_percent")
                            st.metric(
                                "最新收盤",
                                "-" if pd.isna(close) else f"{float(close):,.2f}",
                                (
                                    None
                                    if pd.isna(change_percent)
                                    else f"{float(change_percent):+.2f}%"
                                ),
                            )
                            closes = favorite_data.tail(30).set_index("date")[["close"]]
                            st.line_chart(closes, height=70, use_container_width=True)
            else:
                st.markdown("### 我的最愛清單（0 檔）")
                st.caption("在個股頁按「加入最愛」即可建立清單")
        else:
            st.markdown("### 我的最愛清單")
            st.caption("登入後可使用個人化自選股")

        sidebar_choice = st.selectbox(
            "股票選單（最愛優先）",
            sidebar_labels,
            index=next(
                (
                    i
                    for i, label in enumerate(sidebar_labels)
                    if label == selected_stock
                ),
                0,
            ),
            key=f"dashboard_sidebar_{page}",
        )
        if st.button(
            "前往個股",
            key=f"dashboard_sidebar_go_{page}",
            use_container_width=True,
        ):
            navigate("個股儀表板", sidebar_choice)
            st.rerun()

    return sidebar_choice, favorite_ids, user_id


def render_watchlist_page(
    stock_labels: list[str],
    navigate,
    database: StockDatabase | None = None,
) -> None:
    """顯示目前登入者加入的所有最愛股票。"""
    database = database or StockDatabase()
    user_id, favorite_ids = current_watchlist(database)

    back_col, count_col = st.columns([1, 5], vertical_alignment="center")
    with back_col:
        st.button(
            "上一頁",
            key="watchlist_page_back",
            icon=":material/arrow_back:",
            on_click=navigate,
            args=("__back__",),
        )
    st.markdown("## ⭐ 我的最愛")
    if user_id is None:
        st.info("請先登入後查看個人化最愛清單")
        st.button("前往登入", type="primary", on_click=navigate, args=("登入",))
        return

    st.caption(f"目前共收藏 {len(favorite_ids)} 檔股票")
    if not favorite_ids:
        st.info("目前還沒有加入任何最愛股票，請在個股頁點「☆ 加入最愛」。")
        st.button(
            "前往個股儀表板",
            type="primary",
            on_click=navigate,
            args=("個股儀表板",),
        )
        return

    favorites = [
        next(
            (
                label
                for label in stock_labels
                if label.split(" ", 1)[0] == favorite_id
            ),
            favorite_id,
        )
        for favorite_id in favorite_ids
    ]
    columns = st.columns(2)
    for index, (favorite_id, favorite_label) in enumerate(zip(favorite_ids, favorites)):
        with columns[index % 2]:
            with st.container(border=True):
                open_button, remove_button = st.columns([4, 1], vertical_alignment="center")
                if open_button.button(
                    favorite_label,
                    key=f"watchlist_page_open_{favorite_id}",
                    use_container_width=True,
                    type="primary",
                ):
                    navigate("個股儀表板", favorite_label)
                    st.rerun()
                if remove_button.button(
                    "移除",
                    key=f"watchlist_page_remove_{favorite_id}",
                    use_container_width=True,
                    help=f"從最愛移除 {favorite_id}",
                ):
                    try:
                        database.remove_from_watchlist(user_id, favorite_id)
                        st.success(f"已移除最愛 {favorite_label}")
                        st.rerun()
                    except (OSError, sqlite3.Error, ValueError):
                        st.error("目前無法移除最愛，請稍後再試")

                try:
                    favorite_data = database.query_prices(favorite_id)
                except (OSError, sqlite3.Error, ValueError):
                    favorite_data = pd.DataFrame()
                if favorite_data.empty:
                    st.caption("尚無價格資料")
                    continue

                latest = favorite_data.iloc[-1]
                close = latest.get("close")
                change_percent = latest.get("change_percent")
                st.metric(
                    "最新收盤",
                    "-" if pd.isna(close) else f"{float(close):,.2f}",
                    None if pd.isna(change_percent) else f"{float(change_percent):+.2f}%",
                )
