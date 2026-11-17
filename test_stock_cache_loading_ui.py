"""Regression checks for cache-dependent search loading UI."""
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import stock_lookup

TZ = ZoneInfo('Asia/Taipei')


def test_cache_hit_selects_dim_overlay_without_spinner():
    with patch.object(stock_lookup, '_use_cache_loading_ui', return_value=True):
        html = (stock_lookup.CACHE_HIT_DIM_STYLE if stock_lookup._use_cache_loading_ui('0050', 180)
                else stock_lookup.SEARCH_WAIT_STYLE)
    assert 'stock-cache-dim-overlay' in html
    assert 'stock-wait-spinner' not in html
    assert 'stock-wait-track' not in html


def test_cache_miss_selects_original_spinner():
    with patch.object(stock_lookup, '_use_cache_loading_ui', return_value=False):
        html = (stock_lookup.CACHE_HIT_DIM_STYLE if stock_lookup._use_cache_loading_ui('0050', 180)
                else stock_lookup.SEARCH_WAIT_STYLE)
    assert 'stock-wait-spinner' in html


def test_loading_ui_uses_existing_daily_cache_decision():
    with patch.object(stock_lookup, 'date_window', return_value=(date(2026, 5, 21), date(2026, 11, 17))), \
         patch.object(stock_lookup, 'StockDatabase', return_value=object()), \
         patch.object(stock_lookup, '_same_day_lookup_result', return_value={'cache_hit': True}) as hit:
        assert stock_lookup._use_cache_loading_ui('0050', 180)
        assert hit.call_args.args[1:3] == ('0050', date(2026, 5, 21))
        assert hit.call_args.args[3].tzinfo is not None


def test_loading_ui_cache_miss():
    with patch.object(stock_lookup, 'date_window', return_value=(date(2026, 5, 21), date(2026, 11, 17))), \
         patch.object(stock_lookup, 'StockDatabase', return_value=object()), \
         patch.object(stock_lookup, '_same_day_lookup_result', return_value=None):
        assert not stock_lookup._use_cache_loading_ui('0050', 180)
