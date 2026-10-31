import math
import unittest

import pandas as pd

from comparison import (
    align_close_prices,
    annualized_volatility,
    cumulative_returns,
    performance_table,
    period_performance,
    rank_stocks,
    risk_return_chart,
    risk_return_points,
)


def _stock(dates, closes):
    return pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})


class ComparisonTests(unittest.TestCase):
    def test_align_keeps_common_dates_only(self):
        closes = align_close_prices(
            {
                "A": _stock(["2026-01-02", "2026-01-03"], [10.0, 11.0]),
                "B": _stock(["2026-01-03", "2026-01-04"], [20.0, 22.0]),
            }
        )
        self.assertEqual(list(closes.index.strftime("%Y-%m-%d")), ["2026-01-03"])

    def test_cumulative_returns_start_at_zero(self):
        closes = pd.DataFrame({"A": [100.0, 120.0]})
        result = cumulative_returns(closes)["A"]
        self.assertEqual(result.iloc[0], 0.0)
        self.assertAlmostEqual(result.iloc[1], 20.0)

    def test_period_performance(self):
        result = period_performance(_stock(["2026-01-02", "2026-01-03", "2026-01-05"], [100.0, 80.0, 110.0]))
        self.assertAlmostEqual(result["區間報酬率(%)"], 10.0)
        self.assertAlmostEqual(result["最大回撤(%)"], -20.0)
        self.assertEqual(result["交易日數"], 3)

    def test_period_performance_needs_two_days(self):
        with self.assertRaises(ValueError):
            period_performance(_stock(["2026-01-02"], [100.0]))

    def test_period_return_100_to_110_is_ten_percent(self):
        result = period_performance(_stock(["2026-01-02", "2026-01-05"], [100.0, 110.0]))
        self.assertEqual(f"{result['區間報酬率(%)']:.2f}", "10.00")

    def test_annualized_volatility_uses_sample_std(self):
        returns = pd.Series([1.0, -1.0, 2.0])
        expected = returns.std(ddof=1) * math.sqrt(252)
        self.assertAlmostEqual(annualized_volatility(returns), expected)

    def test_volatility_needs_two_daily_returns(self):
        self.assertTrue(math.isnan(annualized_volatility(pd.Series([5.0]))))
        result = period_performance(_stock(["2026-01-02", "2026-01-05"], [100.0, 110.0]))
        self.assertTrue(math.isnan(result["年化波動度(%)"]))

    def test_zero_volatility_is_zero(self):
        result = period_performance(_stock(["2026-01-02", "2026-01-05", "2026-01-06"], [100.0, 100.0, 100.0]))
        self.assertEqual(result["年化波動度(%)"], 0.0)

    def test_performance_table_uses_common_trading_days_only(self):
        # B 在 01-05 停牌，A 當日的大漲不可列入比較。
        stocks = {
            "A": _stock(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"], [100.0, 200.0, 110.0, 121.0]),
            "B": _stock(["2026-01-02", "2026-01-06", "2026-01-07"], [50.0, 55.0, 60.5]),
        }
        table = performance_table(stocks)
        self.assertEqual(table.loc["A", "交易日數"], 3)
        self.assertEqual(table.loc["B", "交易日數"], 3)
        self.assertAlmostEqual(table.loc["A", "區間報酬率(%)"], 21.0)
        self.assertAlmostEqual(table.loc["A", "年化波動度(%)"], 0.0)
        self.assertEqual(str(table.loc["A", "起始日"]), "2026-01-02")

    def test_risk_return_points_match_table_and_skip_missing_volatility(self):
        table = pd.DataFrame(
            {"年化波動度(%)": [20.0, math.nan], "區間報酬率(%)": [10.0, 5.0]},
            index=pd.Index(["2330 台積電", "2303 聯電"], name="股票"),
        )
        points = risk_return_points(table)
        self.assertEqual(points["股票"].tolist(), ["2330 台積電"])
        self.assertEqual(points.loc[0, "年化波動度(%)"], 20.0)
        self.assertEqual(points.loc[0, "區間報酬率(%)"], 10.0)
        spec = risk_return_chart(points).to_dict()
        self.assertEqual(spec["layer"][1]["mark"]["type"], "text")

    def test_rank_stocks(self):
        table = pd.DataFrame({"區間報酬率(%)": [5.0, 15.0]}, index=pd.Index(["A", "B"], name="股票"))
        ranked = rank_stocks(table)
        self.assertEqual(list(ranked["股票"]), ["B", "A"])
        self.assertEqual(list(ranked["排名"]), [1, 2])
        with self.assertRaises(ValueError):
            rank_stocks(table, "不存在")


if __name__ == "__main__":
    unittest.main()
