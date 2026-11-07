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


def ohlc(close):
    """以收盤價 ±1 當最高、最低價的測試資料。"""
    close = pd.Series(close, dtype=float)
    return pd.DataFrame({"close": close, "high": close + 1, "low": close - 1})


class TechnicalIndicatorTests(unittest.TestCase):
    def test_rsi_is_100_when_price_only_rises(self):
        result = calculate_indicators(ohlc(range(1, 41)))
        self.assertTrue(result["rsi_14"][:14].isna().all())
        self.assertTrue((result["rsi_14"][14:] == 100).all())

    def test_flat_price_gives_neutral_rsi_zero_macd_and_collapsed_bands(self):
        result = calculate_indicators(ohlc([50] * 40))
        last = result.iloc[-1]
        self.assertEqual(last["rsi_14"], 50)
        self.assertEqual((last["macd"], last["macd_signal"], last["macd_hist"]), (0, 0, 0))
        self.assertEqual((last["bb_upper"], last["bb_lower"]), (50, 50))

    def test_macd_warm_up_and_histogram(self):
        result = calculate_indicators(ohlc(range(1, 61)))
        self.assertTrue(result["macd"][:25].isna().all())
        self.assertFalse(pd.isna(result.loc[25, "macd"]))
        self.assertTrue(result["macd_signal"][:33].isna().all())
        self.assertFalse(pd.isna(result.loc[33, "macd_signal"]))
        valid = result.dropna(subset=["macd_signal"])
        pd.testing.assert_series_equal(
            valid["macd_hist"], valid["macd"] - valid["macd_signal"], check_names=False
        )
        self.assertTrue((valid["macd"] > 0).all())  # 持續上漲，快線在慢線上方

    def test_bollinger_bands_use_twenty_day_population_std(self):
        result = calculate_indicators(ohlc(range(1, 21)))
        self.assertTrue(result["bb_middle"][:19].isna().all())
        std = (399 / 12) ** 0.5  # 1~20 的母體標準差
        self.assertAlmostEqual(result.loc[19, "bb_middle"], 10.5)
        self.assertAlmostEqual(result.loc[19, "bb_upper"], 10.5 + 2 * std)
        self.assertAlmostEqual(result.loc[19, "bb_lower"], 10.5 - 2 * std)

    def test_kd_starts_from_fifty_and_smooths_rsv(self):
        result = calculate_indicators(ohlc(range(1, 12)))
        self.assertTrue(result["kd_k"][:8].isna().all())
        # 第 9 天：9 日最低 0、最高 10、收盤 9 → RSV 90。
        k = 50 * 2 / 3 + 90 / 3
        d = 50 * 2 / 3 + k / 3
        self.assertAlmostEqual(result.loc[8, "kd_k"], k)
        self.assertAlmostEqual(result.loc[8, "kd_d"], d)
        self.assertAlmostEqual(result.loc[9, "kd_k"], k * 2 / 3 + 90 / 3)

    def test_kd_flat_range_counts_as_neutral(self):
        result = calculate_indicators(pd.DataFrame({"close": [10.0] * 9, "high": [10.0] * 9, "low": [10.0] * 9}))
        self.assertAlmostEqual(result.loc[8, "kd_k"], 50)

    def test_kd_skips_days_with_missing_range_and_resumes(self):
        frame = ohlc(range(1, 21))
        frame.loc[12, ["high", "low"]] = None
        result = calculate_indicators(frame)
        self.assertTrue(result["kd_k"][12:21].isna().all())  # 含缺值的 9 日視窗無法計算
        self.assertFalse(pd.isna(result.loc[11, "kd_k"]))
        self.assertEqual(len(result), 20)  # 不刪列，日期維持對齊

    def test_kd_requires_high_and_low(self):
        result = calculate_indicators(pd.DataFrame({"close": range(1, 21)}))
        self.assertNotIn("kd_k", result.columns)
        self.assertIn("rsi_14", result.columns)

    def test_indicators_are_calculated_per_stock(self):
        first, second = ohlc(range(1, 31)), ohlc(range(100, 130))
        frame = pd.concat([first.assign(stock_id="A"), second.assign(stock_id="B")], ignore_index=True)
        frame["date"] = list(pd.bdate_range("2026-01-05", periods=30)) * 2
        result = calculate_indicators(frame)
        stock_b = result[result["stock_id"] == "B"].reset_index(drop=True)
        self.assertTrue(stock_b["rsi_14"][:14].isna().all())  # 沒有接續 A 的資料
        self.assertTrue(stock_b["bb_middle"][:19].isna().all())


class MoreIndicatorTests(unittest.TestCase):
    def test_ma10(self):
        result = calculate_indicators(ohlc(range(1, 11)))
        self.assertTrue(pd.isna(result.loc[8, "ma_10"]))
        self.assertAlmostEqual(result.loc[9, "ma_10"], 5.5)

    def test_bias_is_percent_distance_from_moving_average(self):
        result = calculate_indicators(ohlc(range(1, 25)))
        self.assertTrue(result["bias_6"][:5].isna().all())
        self.assertAlmostEqual(result.loc[5, "bias_6"], (6 - 3.5) / 3.5 * 100)
        self.assertAlmostEqual(result.loc[11, "bias_12"], (12 - 6.5) / 6.5 * 100)
        self.assertTrue(result["bias_24"][:23].isna().all())
        self.assertAlmostEqual(result.loc[23, "bias_24"], 92)

    def test_williams_r(self):
        result = calculate_indicators(ohlc(range(1, 15)))
        self.assertTrue(result["willr_14"][:13].isna().all())
        # 14 日最高 15、最低 0、收盤 14。
        self.assertAlmostEqual(result.loc[13, "willr_14"], (15 - 14) / 15 * -100)
        flat = calculate_indicators(pd.DataFrame({"close": [10.0] * 14, "high": [10.0] * 14, "low": [10.0] * 14}))
        self.assertAlmostEqual(flat.loc[13, "willr_14"], -50)

    def test_obv_adds_volume_on_up_days_and_subtracts_on_down_days(self):
        frame = pd.DataFrame({"close": [10.0, 11, 10, 10, 12], "trade_volume": [100.0, 200, 300, 400, 500]})
        self.assertEqual(list(calculate_indicators(frame)["obv"]), [0, 200, -100, -100, 400])

    def test_atr_and_dmi_on_steady_uptrend(self):
        result = calculate_indicators(ohlc(range(1, 41)))
        # 每日高低差 2、與前收盤的差也不超過 2 → 真實波幅恆為 2。
        self.assertTrue(result["atr_14"][:13].isna().all())
        self.assertAlmostEqual(result.loc[13, "atr_14"], 2)
        self.assertTrue(result["plus_di_14"][:14].isna().all())
        self.assertAlmostEqual(result.loc[14, "plus_di_14"], 50)  # 每天上漲 1，平均波幅 2
        self.assertAlmostEqual(result.loc[14, "minus_di_14"], 0)
        self.assertTrue(result["adx_14"][:27].isna().all())
        self.assertAlmostEqual(result.loc[27, "adx_14"], 100)

    def test_range_and_volume_indicators_need_their_columns(self):
        result = calculate_indicators(pd.DataFrame({"close": range(1, 30)}))
        for column in ("willr_14", "atr_14", "adx_14", "obv"):
            self.assertNotIn(column, result.columns)
        self.assertIn("bias_6", result.columns)


class RepositoryDataTests(unittest.TestCase):
    def test_bundled_csv_files_load(self):
        for name in ("2327.csv", "2492.csv"):
            with self.subTest(name=name):
                data = load_data(ROOT / name)
                self.assertFalse(data.empty)
                self.assertTrue(data["date"].is_monotonic_increasing)


if __name__ == "__main__":
    unittest.main()
