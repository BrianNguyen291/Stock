"""Sprint 2 offline acceptance checks. Run with: python -m pytest test_sprint2_acceptance.py -v

All tests use temporary SQLite databases and mocked TWSE responses. Nothing writes to Azure.
"""
from datetime import date
from unittest.mock import Mock

import pandas as pd
import requests

import update_data
from database import StockDatabase


def sample_row(day='2026-10-01', stock_id='2327'):
    return {
        'date': day,
        'stock_id': stock_id,
        'stock_name': '國巨',
        'trade_volume': 1000,
        'trade_value': 100000,
        'open': 100.0,
        'high': 105.0,
        'low': 98.0,
        'close': 102.0,
        'change': 2.0,
        'transaction_count': 50,
        'note': '',
    }


def run_one(tmp_path, db, start, end, attempts=2):
    return update_data.update_one(
        session=Mock(),
        stock_id='2327',
        start=start,
        end=end,
        raw_dir=tmp_path / 'raw',
        pending_dir=tmp_path / 'pending',
        attempts=attempts,
        retry_seconds=1,
        request_delay=1,
        db=db,
        write_db=True,
    )


def test_success_and_second_run_are_idempotent(tmp_path, monkeypatch):
    db = StockDatabase(tmp_path / 'acceptance.sqlite3')
    monkeypatch.setattr(update_data, 'fetch_month', lambda *args: [sample_row()])
    first = run_one(tmp_path, db, date(2026, 10, 1), date(2026, 10, 1))
    second = run_one(tmp_path, db, date(2026, 10, 1), date(2026, 10, 1))
    assert (first['status'], first['written_rows']) == ('updated', 1)
    assert (second['status'], second['written_rows']) == ('no_new_rows', 0)
    assert len(db.query_prices('2327', indicators=False)) == 1
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM daily_prices WHERE stock_id='2327' AND date='2026-10-01'").fetchone()[0] == 1


def test_empty_source_never_reports_success_or_invents_trades(tmp_path, monkeypatch):
    db = StockDatabase(tmp_path / 'acceptance.sqlite3')
    fetch = Mock(return_value=[])
    monkeypatch.setattr(update_data, 'fetch_month', fetch)
    monkeypatch.setattr(update_data.time, 'sleep', lambda *_: None)
    record = run_one(tmp_path, db, date(2026, 10, 3), date(2026, 10, 4), attempts=3)
    assert record['status'] in ('failed', 'unverified_no_data')
    assert record['written_rows'] == 0
    assert record['retry_attempts_total'] == 3
    assert fetch.call_count == 3
    assert db.query_prices('2327', indicators=False).empty


def test_timeout_then_success_recovers_within_limit(tmp_path, monkeypatch):
    db = StockDatabase(tmp_path / 'acceptance.sqlite3')
    fetch = Mock(side_effect=[requests.Timeout('simulated timeout'), [sample_row()]])
    monkeypatch.setattr(update_data, 'fetch_month', fetch)
    monkeypatch.setattr(update_data.time, 'sleep', lambda *_: None)
    record = run_one(tmp_path, db, date(2026, 10, 1), date(2026, 10, 1), attempts=3)
    assert record['status'] == 'updated'
    assert record['retry_attempts_total'] == 2
    assert record['written_rows'] == 1
    assert fetch.call_count == 2


def test_permanent_timeout_preserves_existing_valid_data(tmp_path, monkeypatch):
    db = StockDatabase(tmp_path / 'acceptance.sqlite3')
    db.upsert_prices(pd.DataFrame([sample_row()]), source='offline-seed')
    fetch = Mock(side_effect=requests.Timeout('simulated timeout'))
    monkeypatch.setattr(update_data, 'fetch_month', fetch)
    monkeypatch.setattr(update_data.time, 'sleep', lambda *_: None)
    record = run_one(tmp_path, db, date(2026, 10, 1), date(2026, 10, 1), attempts=3)
    assert record['status'] == 'failed'
    assert record['written_rows'] == 0
    assert record['retry_attempts_total'] == 3
    assert fetch.call_count == 3
    kept = db.query_prices('2327', indicators=False)
    assert len(kept) == 1 and float(kept.iloc[0]['close']) == 102.0
    assert record['errors']


def test_malformed_row_does_not_overwrite_existing_data(tmp_path, monkeypatch):
    db = StockDatabase(tmp_path / 'acceptance.sqlite3')
    original = sample_row()
    db.upsert_prices(pd.DataFrame([original]), source='offline-seed')
    malformed = sample_row(day='2026-10-02')
    malformed['close'] = ''
    monkeypatch.setattr(update_data, 'fetch_month', lambda *args: [malformed])
    record = run_one(tmp_path, db, date(2026, 10, 1), date(2026, 10, 2))
    assert record['status'] == 'failed'
    assert record['written_rows'] == 0
    assert record['errors']
    kept = db.query_prices('2327', indicators=False)
    assert len(kept) == 1 and float(kept.iloc[0]['close']) == 102.0
