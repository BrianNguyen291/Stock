import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import auth
import notifications
from database import StockDatabase
from notifications import sync_notifications


@pytest.fixture
def account(monkeypatch, tmp_path):
    path = tmp_path / "notifications.sqlite3"
    monkeypatch.setenv("STOCK_DB_PATH", str(path))
    database = StockDatabase(path)
    user_id = database.upsert_user("google", "notification-user")
    monkeypatch.setattr(auth, "current_user_id", lambda: user_id)
    database.upsert_prices(pd.DataFrame({
        "stock_id": ["1101"], "stock_name": ["台泥"], "date": ["2026-11-23"],
        "open": [25.2], "high": [25.2], "low": [25.2], "close": [25.2],
        "trade_volume": [1000],
    }))
    database.add_alert(user_id, "1101", kind="price_above", threshold=18.7)
    return database, user_id


def test_notifications_survive_reads_dismissals_and_repeated_sync(account):
    database, user_id = account
    sync_notifications(database, user_id)
    sync_notifications(database, user_id)
    [notice] = database.list_alert_notifications(user_id)
    assert notice["stock_label"] == "1101 台泥"
    assert not notice["is_read"]
    database.read_alert_notifications(user_id, [notice["id"]])
    sync_notifications(database, user_id)
    assert database.list_alert_notifications(user_id)[0]["is_read"]
    database.dismiss_alert_notification(user_id, notice["id"])
    reopened = StockDatabase(database.path)
    sync_notifications(reopened, user_id)
    assert reopened.list_alert_notifications(user_id) == []

    database.upsert_prices(pd.DataFrame({
        "stock_id": ["1101"], "stock_name": ["台泥"], "date": ["2026-11-24"],
        "open": [26.0], "high": [26.0], "low": [26.0], "close": [26.0],
        "trade_volume": [1000],
    }))
    sync_notifications(database, user_id)
    [next_notice] = database.list_alert_notifications(user_id)
    assert next_notice["trade_date"] == "2026-11-24"
    assert not next_notice["is_read"]


def test_notification_order_and_account_isolation(account):
    database, user_id = account
    other_user = database.upsert_user("google", "other-user")
    database.save_alert_notifications(user_id, [
        {"event_key": "new", "label": "1101 台泥", "condition": "突破", "date": "2026-11-24", "actual": "26"},
        {"event_key": "old", "label": "1101 台泥", "condition": "突破", "date": "2026-11-23", "actual": "25"},
    ])
    notices = database.list_alert_notifications(user_id)
    assert [notice["trade_date"] for notice in notices] == ["2026-11-24", "2026-11-23"]
    database.read_alert_notifications(other_user, [notices[0]["id"]])
    database.dismiss_alert_notification(other_user, notices[0]["id"])
    assert not database.list_alert_notifications(user_id)[0]["is_read"]
    assert database.list_alert_notifications(other_user) == []


def _notification_page():
    from notifications import render_notification_bell
    render_notification_bell()


def test_bell_can_dismiss_a_notification_without_recreating_it(account):
    database, user_id = account
    app = AppTest.from_function(_notification_page).run()
    assert not app.exception
    [notice] = database.list_alert_notifications(user_id)
    app.button(key=f'notification_dismiss_{notice["id"]}').click().run()
    assert not app.exception
    assert database.list_alert_notifications(user_id) == []
    assert "目前沒有通知。" in [caption.value for caption in app.caption]


def test_open_marks_notifications_read(account, monkeypatch):
    database, user_id = account
    sync_notifications(database, user_id)
    ids = [notice["id"] for notice in database.list_alert_notifications(user_id)]
    monkeypatch.setattr(notifications.st, "session_state", {"notifications_popover": True})
    notifications._read_notifications(database, user_id, ids)
    assert all(notice["is_read"] for notice in database.list_alert_notifications(user_id))
