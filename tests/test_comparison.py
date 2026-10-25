import unittest

import pandas as pd

from comparison import align_close_prices, cumulative_returns, period_performance, rank_stocks


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

    def test_rank_stocks(self):
        table = pd.DataFrame({"區間報酬率(%)": [5.0, 15.0]}, index=pd.Index(["A", "B"], name="股票"))
        ranked = rank_stocks(table)
        self.assertEqual(list(ranked["股票"]), ["B", "A"])
        self.assertEqual(list(ranked["排名"]), [1, 2])
        with self.assertRaises(ValueError):
            rank_stocks(table, "不存在")


if __name__ == "__main__":
    unittest.main()
