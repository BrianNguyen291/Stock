"""Regression tests for persistent auto-update enrollment and bounded schedule."""
import json
import sys
import types
from pathlib import Path

import pytest

# In CI the project's real modules are used. Tests do not contact TWSE or Azure.
from database import StockDatabase
from update_data import load_stock_ids, merge_stock_ids


def test_enrollment_requires_persisted_prices(tmp_path):
    db = StockDatabase(tmp_path / 'isolated.sqlite3')
    with pytest.raises(ValueError, match='尚無有效交易資料'):
        db.register_auto_update_stock('1101')
    assert db.list_auto_update_stocks() == []


def test_enrollment_is_idempotent_and_persistent(tmp_path):
    db_path = tmp_path / 'isolated.sqlite3'
    db = StockDatabase(db_path)
    with db.connect() as connection:
        connection.execute("INSERT INTO stocks(stock_id,stock_name) VALUES (?,?)", ('1101','台泥'))
        connection.execute('''INSERT INTO daily_prices(stock_id,date,trade_volume,open,high,low,close)
                              VALUES (?,?,?,?,?,?,?)''', ('1101','2026-11-09',100,10,11,9,10))
    assert db.register_auto_update_stock('1101') is True
    assert db.register_auto_update_stock('1101') is False
    assert StockDatabase(db_path).list_auto_update_stocks() == ['1101']


def test_merge_keeps_base_and_removes_duplicates():
    base = ['2327', '2492', '2330']
    result = merge_stock_ids(base, ['1101', '2327', '0050', '1101'])
    assert result == ['2327', '2492', '2330', '1101', '0050']


def test_merge_rejects_invalid_and_over_capacity():
    with pytest.raises(ValueError):
        merge_stock_ids(['2327'], ['../../bad'])
    with pytest.raises(ValueError):
        merge_stock_ids(['2327'], ['1101', '1216'], max_stocks=2)


def test_static_configuration_unaffected(tmp_path):
    path = tmp_path / 'stocks.json'
    ids = [str(2000 + i) for i in range(10)]
    path.write_text(json.dumps({'stocks': ids}), encoding='utf-8')
    assert load_stock_ids(path) == ids
