import unittest
from pathlib import Path

import pandas as pd

from data_processor import calculate_indicators, clean_data, load_data

ROOT = Path(__file__).resolve().parent.parent


class CleanDataTests(unittest.TestCase):
    def test_parses_numbers_and_drops_bad_rows(self):
        raw = pd.DataFrame(
            {
                "Date": ["2026-01-03", "2026-01-02", "bad", "2026-01-05"],
                "close": ["1,010", "+1,000", "999", "--"],
                "change": ["10", "X", "0", "0"],
            }
        )
        result = clean_data(raw)
        self.assertEqual(list(result["date"].dt.strftime("%Y-%m-%d")), ["2026-01-02", "2026-01-03"])
        self.assertEqual(list(result["close"]), [1000, 1010])
        self.assertTrue(pd.isna(result.loc[0, "change"]))

    def test_keeps_last_duplicate_date(self):
        raw = pd.DataFrame({"date": ["2026-01-02", "2026-01-02"], "close": ["100", "101"]})
        self.assertEqual(list(clean_data(raw)["close"]), [101])

    def test_missing_required_column(self):
        with self.assertRaises(ValueError):
            clean_data(pd.DataFrame({"date": ["2026-01-02"]}))


class IndicatorTests(unittest.TestCase):
    def test_price_and_volume_changes(self):
        frame = pd.DataFrame({"close": [100.0, 110.0, 99.0], "trade_volume": [10.0, 20.0, 30.0]})
        result = calculate_indicators(frame)
        self.assertAlmostEqual(result.loc[1, "price_change"], 10.0)
        self.assertAlmostEqual(result.loc[1, "change_percent"], 10.0)
        self.assertAlmostEqual(result.loc[2, "change_percent"], -10.0)
        self.assertTrue(pd.isna(result.loc[2, "volume_ma_5"]))
        self.assertTrue(pd.isna(result.loc[0, "price_change"]))


class RepositoryDataTests(unittest.TestCase):
    def test_bundled_csv_files_load(self):
        for name in ("2327.csv", "2492.csv"):
            with self.subTest(name=name):
                data = load_data(ROOT / name)
                self.assertFalse(data.empty)
                self.assertTrue(data["date"].is_monotonic_increasing)


if __name__ == "__main__":
    unittest.main()
