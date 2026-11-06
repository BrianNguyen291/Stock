"""Google 登入與目前 Streamlit 工作階段的使用者識別。"""

import os
import sqlite3
from dataclasses import dataclass

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from database import StockDatabase

TEST_SESSION_KEY = "_stock_test_logged_in"


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str
    email: str | None = None
    display_name: str | None = None


def _local_test_enabled() -> bool:
    return (
        os.environ.get("STOCK_ENABLE_TEST_LOGIN") == "1"
        and not os.environ.get("WEBSITE_SITE_NAME")
        and st.get_option("server.address") in ("localhost", "127.0.0.1", "::1")
    )


def current_identity() -> Identity | None:
    user = st.user
    if user.get("is_logged_in"):
        subject = user.get("sub")
        if user.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
            return None
        if not isinstance(subject, str) or not subject.strip():
            return None
        return Identity(
            "google", subject, email=user.get("email"), display_name=user.get("name")
        )
    if _local_test_enabled() and st.session_state.get(TEST_SESSION_KEY):
        return Identity("local_test", "test", display_name="測試帳號")
    return None


def current_user_id() -> int | None:
    """由目前驗證過的工作階段取得資料庫 ID，訪客回傳 None。"""
    identity = current_identity()
    if identity is None:
        return None
    return StockDatabase().upsert_user(
        identity.provider, identity.subject,
        email=identity.email, display_name=identity.display_name,
    )


def _render_test_login(on_success) -> None:
    with st.form("local_test_login", clear_on_submit=True):
        st.caption("本機測試登入")
        username = st.text_input("帳號")
        password = st.text_input("密碼", type="password")
        submitted = st.form_submit_button("登入測試帳號")
    if not submitted:
        return
    if username != "test" or password != "1234":
        st.error("帳號或密碼錯誤")
        return
    st.session_state[TEST_SESSION_KEY] = True
    try:
        current_user_id()
    except (OSError, sqlite3.Error):
        st.session_state.pop(TEST_SESSION_KEY, None)
        st.error("目前無法載入使用者資料，請稍後再試。")
        return
    if on_success is not None:
        on_success()
    st.rerun()


def _google_is_configured() -> bool:
    try:
        settings = st.secrets.get("auth", {})
    except StreamlitSecretNotFoundError:
        return False
    google = settings.get("google", {})
    return bool(
        settings.get("redirect_uri") and settings.get("cookie_secret")
        and google.get("client_id") and google.get("client_secret")
        and google.get("server_metadata_url")
    )


def render_logout() -> None:
    if st.user.get("is_logged_in"):
        st.button("登出", on_click=st.logout)
    else:
        st.button("登出", on_click=st.session_state.pop, args=(TEST_SESSION_KEY, None))


def render_auth(on_success=None) -> None:
    """在目前的容器顯示登入狀態，訪客仍可使用公開行情功能。"""
    google_logged_in = bool(st.user.get("is_logged_in"))
    local_test = _local_test_enabled()
    test_logged_in = local_test and st.session_state.get(TEST_SESSION_KEY, False)
    if not google_logged_in and not test_logged_in:
        if _google_is_configured():
            st.button("使用 Google 登入", on_click=st.login, args=("google",))
        elif not local_test:
            st.caption("Google 登入尚未設定")
        if local_test:
            _render_test_login(on_success)
        return

    render_logout()
    try:
        user_id = current_user_id()
    except (OSError, sqlite3.Error):
        st.error("目前無法載入使用者資料，請稍後再試。")
        return
    if user_id is None:
        st.warning("無法識別 Google 帳號，請登出後重新登入。")
    else:
        st.caption("已使用 Google 登入" if google_logged_in else "已登入測試帳號 test")
