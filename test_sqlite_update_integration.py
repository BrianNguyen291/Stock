"""Offline unit tests for update_data_sqlite.py; no TWSE calls / no production DB."""
import importlib.util
import sys
import types
import unittest
from datetime import date
from unittest.mock import patch
import pandas as pd

# The module is copied into this directory by users as update_data.py; tests can
# run in either case using an explicit file spec while mocking the DB integration.
sys.modules.setdefault('database', types.SimpleNamespace(StockDatabase=object))
SPEC = importlib.util.spec_from_file_location('update_integration', __import__('pathlib').Path(__file__).with_name('update_data.py'))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

FIELDS = dict(stock_id='2327',stock_name='國巨',date='2026-09-01',open=100.,high=102.,low=99.,close=101.,trade_volume=1000,trade_value=101000.,change=1.,transaction_count=10,note='')

class FakeDB:
    def __init__(self):
        self.rows = {}
    def query_prices(self, stock_id, start=None, end=None, indicators=False):
        v=[r for (sid,d),r in self.rows.items() if sid==stock_id and (not start or d>=str(start)) and (not end or d<=str(end))]
        return pd.DataFrame(v)
    def upsert_prices(self, df, source='api'):
        for r in df.to_dict('records'):
            self.rows[(r['stock_id'],r['date'])] = r
        return len(df)

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db=FakeDB()
        self.start=date(2026,9,1);self.end=date(2026,9,30)
    def run_one(self,source_rows,errors=None):
        with patch.object(mod,'fetch_with_retries',return_value=(source_rows,errors,1)),patch.object(mod,'time') as tm:
            return mod.update_one(None,'2327',self.start,self.end,None,None,1,1,1,db=self.db,write_db=True)
    def test_insert_then_rerun_then_backfill(self):
        first=self.run_one([FIELDS]);self.assertEqual(first['status'],'updated');self.assertEqual(first['written_rows'],1)
        second=self.run_one([FIELDS]);self.assertEqual(second['status'],'no_new_rows');self.assertEqual(second['written_rows'],0)
        self.assertEqual(len(self.db.rows),1)
        self.db.rows.clear()
        third=self.run_one([FIELDS]);self.assertEqual(third['written_rows'],1)
    def test_missing_source_preserves_valid_records(self):
        self.run_one([FIELDS]);before=dict(self.db.rows)
        record=self.run_one([],errors='network timeout')
        self.assertEqual(record['status'],'failed');self.assertEqual(self.db.rows,before)
    def test_market_closed_no_fabricated_row(self):
        record=self.run_one([])
        self.assertEqual(record['status'],'unverified_no_data')
        self.assertEqual(len(self.db.rows),0)

if __name__=='__main__': unittest.main()
