
import unittest
from unittest.mock import Mock, patch
from datetime import date

import requests
from update_data import fetch_with_retries


class TestUpdateRetry(unittest.TestCase):

    @patch("update_data.time.sleep")
    @patch("update_data.fetch_month")
    def test_timeout_retries_three_times(
        self, mock_fetch, mock_sleep
    ):
        # 模擬證交所連續三次逾時
        mock_fetch.side_effect = requests.Timeout(
            "模擬 TWSE 連線逾時"
        )

        session = Mock()

        rows, error, attempts = fetch_with_retries(
            session=session,
            month=date(2026, 9, 1),
            stock_id="2327",
            attempts=3,
            retry_seconds=1
        )

        self.assertEqual(rows, [])
        self.assertEqual(attempts, 3)
        self.assertEqual(mock_fetch.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertIn("Timeout", error)

    @patch("update_data.time.sleep")
    @patch("update_data.fetch_month")
    def test_retry_then_success(
        self, mock_fetch, mock_sleep
    ):
        # 第一次逾時、第二次成功
        mock_fetch.side_effect = [
            requests.Timeout("第一次逾時"),
            [{"date": "2026-09-01",
              "stock_id": "2327",
              "close": 100.0}]
        ]

        rows, error, attempts = fetch_with_retries(
            session=Mock(),
            month=date(2026, 9, 1),
            stock_id="2327",
            attempts=3,
            retry_seconds=1
        )

        self.assertEqual(attempts, 2)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(error)
        self.assertEqual(mock_sleep.call_count, 1)


if __name__ == "__main__":
    unittest.main()
