"""我的最愛模組驗收。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

import auth
from watchlist import favorite_first
from database import StockDatabase


def test_favorite_first_keeps_favorites_first_and_updates_sort():
    labels = ["1101 台泥", "2330 台積電", "2327 廣達"]

    assert favorite_first(labels, ["2327"]) == [
        "2327 廣達",
        "1101 台泥",
        "2330 台積電",
    ]

    assert favorite_first(labels, ["2330", "1101"]) == [
        "1101 台泥",
        "2330 台積電",
        "2327 廣達",
    ]


def test_comparison_watchlist_update_keeps_existing_and_adds_missing(tmp_path):
    database = StockDatabase(tmp_path / "watchlist.sqlite3")
    user_id = database.upsert_user("google", "comparison-subject")
    assert database.add_to_watchlist(user_id, "1101")
    assert database.add_to_watchlist(user_id, "2330")

    existing = database.list_watchlist(user_id)
    selected_ids = ["1101", "1216"]
    for stock_id in selected_ids:
        if stock_id not in existing:
            database.add_to_watchlist(user_id, stock_id)

    assert database.list_watchlist(user_id) == ["1101", "1216", "2330"]


def test_app_dashboard_can_add_and_open_watchlist(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_DB_PATH", str(tmp_path / "app-watchlist.sqlite3"))
    monkeypatch.setattr(auth.st, "user", {})

    app = AppTest.from_file(
        str(Path(__file__).parent.parent / "app.py"),
        default_timeout=60,
    )
    app.session_state[auth.TEST_SESSION_KEY] = True
    app.session_state["active_page"] = "個股儀表板"
    app.session_state["selected_stock"] = "2492 華新科"
    app.run()
    assert not app.exception

    add_button = app.button(key="watchlist_add_2492")
    assert add_button is not None
    add_button.click().run()
    assert not app.exception

    database = StockDatabase(tmp_path / "app-watchlist.sqlite3")
    user_id = database.upsert_user("local_test", "test")
    assert database.list_watchlist(user_id) == ["2492"]
