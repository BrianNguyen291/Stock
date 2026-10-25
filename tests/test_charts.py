import unittest
from pathlib import Path

from matplotlib.figure import Figure

from charts import candlestick_chart, price_trend_chart, volume_chart
from data_processor import load_data

ROOT = Path(__file__).resolve().parent.parent


class ChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_data(ROOT / "2327.csv").tail(30)

    def test_charts_return_figures(self):
        for chart in (price_trend_chart, volume_chart, candlestick_chart):
            with self.subTest(chart=chart.__name__):
                figure = chart(self.data)
                self.assertIsInstance(figure, Figure)
                self.assertTrue(figure.axes)

    def test_missing_columns_raise(self):
        with self.assertRaises(ValueError):
            candlestick_chart(self.data.drop(columns=["open"]))


if __name__ == "__main__":
    unittest.main()
