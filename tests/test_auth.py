from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import auth


def test_google_login_and_logout_use_current_session(monkeypatch, tmp_path):
    user = dict(
        is_logged_in=False, iss="https://accounts.google.com", sub="google-user",
        email="person@example.com",
    )
    login = Mock()
    logout = Mock(side_effect=lambda: user.update(is_logged_in=False))
    monkeypatch.setattr(auth.st, "user", user)
    monkeypatch.setattr(auth.st, "login", login)
    monkeypatch.setattr(auth.st, "logout", logout)
    monkeypatch.setenv("STOCK_DB_PATH", str(tmp_path / "auth.sqlite3"))

    app = AppTest.from_string("from auth import render_auth\nrender_auth()")
    app.secrets["auth"] = {
        "redirect_uri": "http://localhost:8501/oauth2callback",
        "cookie_secret": "test-only",
        "google": {
            "client_id": "test-client",
            "client_secret": "test-only",
            "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
        },
    }
    app.run()
    assert not app.exception
    app.button[0].click().run()
    login.assert_called_once_with("google")
    assert auth.current_user_id() is None

    user["is_logged_in"] = True
    app.run()
    assert not app.exception
    assert auth.current_user_id() is not None
    assert app.button[0].label == "登出"
    app.button[0].click().run()
    logout.assert_called_once()
    assert auth.current_user_id() is None
    assert app.button[0].label == "使用 Google 登入"


def test_incomplete_or_non_google_identity_has_no_user_id(monkeypatch):
    user = dict(is_logged_in=True, iss="https://accounts.google.com")
    monkeypatch.setattr(auth.st, "user", user)
    database = Mock()
    monkeypatch.setattr(auth, "StockDatabase", database)
    assert auth.current_user_id() is None
    user.update(iss="https://other.example.com", sub="some-subject")
    assert auth.current_user_id() is None
    database.assert_not_called()


def test_guest_can_use_sidebar_before_google_is_configured(monkeypatch):
    monkeypatch.setattr(auth.st, "user", {})
    monkeypatch.setattr(auth.st, "secrets", {})
    app = AppTest.from_string("from auth import render_auth\nrender_auth()").run()
    assert not app.exception
    assert app.caption[0].value == "Google 登入尚未設定"


def test_local_test_login_rejects_wrong_password_and_logs_out(monkeypatch, tmp_path):
    monkeypatch.setattr(auth.st, "user", {})
    monkeypatch.setattr(auth.st, "secrets", {})
    monkeypatch.setattr(auth, "_local_test_enabled", lambda: True)
    monkeypatch.setenv("STOCK_DB_PATH", str(tmp_path / "local.sqlite3"))
    app = AppTest.from_string("from auth import render_auth\nrender_auth()").run()
    app.text_input[0].set_value("test")
    app.text_input[1].set_value("wrong")
    app.button[0].click().run()
    assert app.error[0].value == "帳號或密碼錯誤"
    app.text_input[0].set_value("test")
    app.text_input[1].set_value("1234")
    app.button[0].click().run()
    assert not app.exception
    assert app.caption[0].value == "已登入測試帳號 test"
    app.button[0].click().run()
    assert app.button[0].label == "登入測試帳號"


@pytest.mark.parametrize("address,azure", [("0.0.0.0", False), ("127.0.0.1", True)])
def test_test_login_stays_disabled_on_public_or_azure_server(monkeypatch, address, azure):
    monkeypatch.setenv("STOCK_ENABLE_TEST_LOGIN", "1")
    if azure:
        monkeypatch.setenv("WEBSITE_SITE_NAME", "stock")
    else:
        monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.setattr(auth.st, "get_option", lambda _: address)
    assert not auth._local_test_enabled()
