"""持股損益（#28）：張數換算、損益與比重計算、登入者持股頁。"""

import math

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import auth
from database import StockDatabase
from portfolio import (
    OTHER_LABEL, PIE_COLORS, holding_summary, lots_to_shares, portfolio_totals, position_weights,
    weight_chart,
)


def quotes(stock_id, closes, name="測試"):
    return pd.DataFrame({
        "stock_id": stock_id, "stock_name": name,
        "date": pd.bdate_range("2026-01-01", periods=len(closes)),
        "open": closes, "high": closes, "low": closes, "close": closes,
        "trade_volume": [1000] * len(closes),
    })


def lot(id, stock_id, price, shares, day="2026-01-02"):
    return {"id": id, "stock_id": stock_id, "purchase_price": price, "shares": shares, "purchase_date": day}


@pytest.mark.parametrize("lots, shares", [(1, 1000), (2.5, 2500), (0.001, 1), (10.0, 10000)])
def test_lots_convert_to_shares(lots, shares):
    assert lots_to_shares(lots) == shares


@pytest.mark.parametrize("lots", [0, -1, 0.0005, 1.0004, math.nan, math.inf, True, "1"])
def test_invalid_lots_are_rejected(lots):
    with pytest.raises(ValueError):
        lots_to_shares(lots)


def test_unrealized_profit_and_return_use_latest_close():
    stocks = {"2330": ("2330 台積電", quotes("2330", [100, 105, 110]))}
    summary = holding_summary([lot(1, "2330", 100, 1000)], stocks)
    row = summary.iloc[0]
    assert row["latest_close"] == 110
    assert row["cost"] == 100_000
    assert row["market_value"] == 110_000
    assert row["unrealized"] == 10_000
    assert row["return_pct"] == pytest.approx(10.0)
    assert str(row["quote_date"]) == "2026-01-05"


def test_loss_is_negative():
    stocks = {"2330": ("2330 台積電", quotes("2330", [80]))}
    row = holding_summary([lot(1, "2330", 100, 500)], stocks).iloc[0]
    assert row["unrealized"] == -10_000
    assert row["return_pct"] == pytest.approx(-20.0)


def test_totals_skip_holdings_without_quotes():
    stocks = {"2330": ("2330 台積電", quotes("2330", [120]))}
    summary = holding_summary([lot(1, "2330", 100, 1000), lot(2, "9999", 50, 1000)], stocks)
    assert math.isnan(summary.iloc[1]["market_value"])
    totals = portfolio_totals(summary)
    assert totals == {
        "cost": 100_000, "market_value": 120_000, "unrealized": 20_000,
        "return_pct": pytest.approx(20.0), "unpriced": 1,
    }


def test_weights_group_lots_of_same_stock_and_sum_to_100():
    stocks = {
        "2330": ("2330 台積電", quotes("2330", [100])),
        "2317": ("2317 鴻海", quotes("2317", [50])),
    }
    summary = holding_summary([
        lot(1, "2330", 90, 1000), lot(2, "2330", 95, 1000), lot(3, "2317", 40, 2000),
    ], stocks)
    weights = position_weights(summary)
    assert weights["label"].tolist() == ["2330 台積電", "2317 鴻海"]
    assert weights["market_value"].tolist() == [200_000, 100_000]
    assert weights["weight_pct"].round(4).tolist() == [66.6667, 33.3333]
    assert weights["weight_pct"].sum() == pytest.approx(100)


def test_more_stocks_than_colors_fold_into_other():
    ids = [f"{1000 + i}" for i in range(10)]
    stocks = {stock_id: (stock_id, quotes(stock_id, [10 + i])) for i, stock_id in enumerate(ids)}
    summary = holding_summary([lot(i, stock_id, 10, 1000) for i, stock_id in enumerate(ids)], stocks)
    weights = position_weights(summary)
    assert len(weights) == 8
    assert weights["label"].iloc[-1] == OTHER_LABEL
    assert weights["market_value"].sum() == pytest.approx(summary["market_value"].sum())


def test_pie_colors_follow_stock_code_not_market_value_rank():
    stocks = {
        "2330": ("2330 台積電", quotes("2330", [10])),
        "2317": ("2317 鴻海", quotes("2317", [10])),
    }
    small_tsmc = holding_summary([lot(1, "2330", 10, 1000), lot(2, "2317", 10, 5000)], stocks)
    big_tsmc = holding_summary([lot(1, "2330", 10, 5000), lot(2, "2317", 10, 1000)], stocks)
    for summary in (small_tsmc, big_tsmc):
        scale = weight_chart(position_weights(summary)).to_dict()["encoding"]["color"]["scale"]
        assert dict(zip(scale["domain"], scale["range"])) == {
            "2317 鴻海": PIE_COLORS[0], "2330 台積電": PIE_COLORS[1],
        }


def _portfolio_page(db_path):
    from database import StockDatabase
    from portfolio import render_portfolio

    database = StockDatabase(db_path)
    render_portfolio({"2330 台積電": database.query_prices("2330")}, database=database)


@pytest.fixture
def page(monkeypatch, tmp_path):
    path = tmp_path / "portfolio.sqlite3"
    monkeypatch.setenv("STOCK_DB_PATH", str(path))
    monkeypatch.delenv("STOCK_ENABLE_TEST_LOGIN", raising=False)
    monkeypatch.setattr(auth.st, "user", {})
    database = StockDatabase(path)
    database.upsert_prices(quotes("2330", [100.0, 105.0, 110.0], name="台積電"))
    return database, AppTest.from_function(_portfolio_page, kwargs={"db_path": str(path)}, default_timeout=30)


def test_guest_is_asked_to_log_in(page):
    _, app = page
    app.run()
    assert not app.exception
    assert "登入後即可使用" in app.info[0].value
    assert not app.dataframe


def test_logged_in_user_adds_holding_and_sees_profit(page):
    database, app = page
    app.session_state[auth.TEST_SESSION_KEY] = True
    app.run()
    assert not app.exception
    assert "尚未記錄持股" in app.info[0].value

    app.number_input(key="portfolio_price").set_value(100.0)
    app.number_input(key="portfolio_lots").set_value(2.0)
    app.button(key="portfolio_submit").click().run()
    assert not app.exception
    assert "已加入 2330 台積電" in app.success[0].value

    user_id = database.upsert_user("local_test", "test")
    assert [(h["stock_id"], h["shares"], h["purchase_price"]) for h in database.list_holdings(user_id)] == [
        ("2330", 2000, 100.0)
    ]
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["總成本（元）"] == "200,000"
    assert metrics["目前市值（元）"] == "220,000"
    assert metrics["未實現損益（元）"] == "+20,000"
    assert metrics["報酬率"] == "+10.00%"
    assert len(app.get("vega_lite_chart")) == 1  # 持股比重圓餅圖

    app.button(key="portfolio_remove").click().run()
    assert database.list_holdings(user_id) == []


def test_missing_price_shows_error_without_saving(page):
    database, app = page
    app.session_state[auth.TEST_SESSION_KEY] = True
    app.run()
    app.button(key="portfolio_submit").click().run()
    assert app.error[0].value == "請輸入買進價。"
    assert database.list_holdings(database.upsert_user("local_test", "test")) == []
