"""Regression tests for the SQLite-backed daily TWSE lookup checkpoint."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import stock_lookup
from database import StockDatabase

TZ = ZoneInfo('Asia/Taipei')


def _existing_db(tmp_path):
    db = StockDatabase(tmp_path / 'daily-cache-test.sqlite3')
    # Use ordinary well-formed TWSE-like OHLC data.
    db.upsert_prices(pd.DataFrame([{
        'stock_id': '0050', 'stock_name': '元大台灣50', 'date': '2026-11-17',
        'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.5,
        'trade_volume': 12345,
    }]), source='unit-test')
    return db


def test_reuse_same_day_but_refresh_next_day(tmp_path):
    db = _existing_db(tmp_path)
    now = datetime(2026, 11, 17, 18, 0, tzinfo=TZ)
    stock_lookup._record_daily_lookup(db, '0050', date(2026, 5, 1), now, '2026-11-17', 121)
    hit = stock_lookup._same_day_lookup_result(db, '0050', date(2026, 6, 1), now)
    assert hit and hit['cache_hit'] and hit['inserted_rows'] == 0
    assert hit['source_rows'] == 121
    assert stock_lookup._same_day_lookup_result(db, '0050', date(2026, 6, 1),
        datetime(2026, 11, 18, 9, 0, tzinfo=TZ)) is None


def test_refresh_after_close_when_initial_lookup_before_close(tmp_path):
    db = _existing_db(tmp_path)
    morning = datetime(2026, 11, 17, 10, 0, tzinfo=TZ)
    stock_lookup._record_daily_lookup(db, '0050', date(2026, 5, 1), morning, '2026-11-16', 121)
    assert stock_lookup._same_day_lookup_result(db, '0050', date(2026, 6, 1), morning)
    assert stock_lookup._same_day_lookup_result(db, '0050', date(2026, 6, 1),
        datetime(2026, 11, 17, 18, 0, tzinfo=TZ)) is None


def test_wider_period_requires_fetch(tmp_path):
    db = _existing_db(tmp_path)
    now = datetime(2026, 11, 17, 18, 0, tzinfo=TZ)
    stock_lookup._record_daily_lookup(db, '0050', date(2026, 8, 1), now, '2026-11-17', 65)
    assert stock_lookup._same_day_lookup_result(db, '0050', date(2026, 6, 1), now) is None


def test_no_checkpoint_for_no_source_rows(tmp_path):
    db = _existing_db(tmp_path)
    now = datetime(2026, 11, 17, 18, 0, tzinfo=TZ)
    stock_lookup._record_daily_lookup(db, '0050', date(2026, 5, 1), now, '', 0)
    assert stock_lookup._same_day_lookup_result(db, '0050', date(2026, 6, 1), now) is None


def test_cached_result_does_not_open_twse_session(tmp_path, monkeypatch):
    db = _existing_db(tmp_path)
    now = datetime.now(TZ)
    start, _ = stock_lookup.date_window(180, today=now.date())
    stock_lookup._record_daily_lookup(db, '0050', start, now,
                                     now.date().isoformat(), 121)
    monkeypatch.setattr(stock_lookup, 'StockDatabase', lambda: db)
    def unexpected_network():
        raise AssertionError('不得再次呼叫 TWSE')
    monkeypatch.setattr(stock_lookup, 'create_session', unexpected_network)
    result = stock_lookup.fetch_and_store_stock('0050', days=180)
    assert result['cache_hit'] is True
    assert result['inserted_rows'] == 0


def test_stale_weekday_after_close_does_not_cache(tmp_path):
    db = _existing_db(tmp_path)
    now = datetime(2026, 11, 17, 18, 0, tzinfo=TZ)
    stock_lookup._record_daily_lookup(db, '0050', date(2026, 5, 1), now, '2026-11-16', 121)
    assert stock_lookup._same_day_lookup_result(db, '0050', date(2026, 6, 1), now) is None
