"""Basic tests; run with python -m pytest test_stock_lookup.py."""
import sys
import types
import importlib
from datetime import date

import pytest

# Use the actual installed Streamlit inside the project; no network requests in these tests.
from stock_lookup import validate_stock_id, date_window, export_table, excel_bytes
import pandas as pd


def test_validate_listed_stock_code():
    assert validate_stock_id(" 2330 ") == "2330"
    for invalid in ("", "abc", "123", "12345", "../../", "２３３０"):
        with pytest.raises(ValueError):
            validate_stock_id(invalid)


def test_window_is_bounded():
    start, end = date_window(90, date(2026, 11, 3))
    assert end == date(2026, 11, 3)
    assert (end-start).days == 90
    with pytest.raises(ValueError):
        date_window(5000)


def test_csv_excel_exports():
    frame = pd.DataFrame([{"date":pd.Timestamp("2026-11-03"),"stock_id":"2330","stock_name":"台積電","open":100,"high":105,"low":99,"close":103,"trade_volume":1000}])
    t = export_table(frame)
    assert t.iloc[0]["日期"] == "2026-11-03"
    data = excel_bytes(t)
    assert data[:2] == b"PK"  # XLSX zip container


def test_normalize_twse_optional_numbers():
    from stock_lookup import normalize_twse_rows
    original = [{"stock_id": "2603", "change": "", "trade_value": "", "transaction_count": "", "close": 100}]
    output = normalize_twse_rows(original)
    assert output[0]["change"] is None
    assert output[0]["trade_value"] is None
    assert output[0]["transaction_count"] is None
    assert output[0]["close"] == 100
    assert original[0]["change"] == ""


def test_normalize_twse_preserves_numeric_change():
    from stock_lookup import normalize_twse_rows
    assert normalize_twse_rows([{"change": -0.25}])[0]["change"] == -0.25
