"""清理台股日交易 CSV，並計算價格與成交量指標。

可從其他模組呼叫 ``process_data``，或直接執行：
    python data_processor.py --input 2327.csv --output data/processed/2327.csv
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

LOGGER = logging.getLogger(__name__)

NUMERIC_COLUMNS = (
    "trade_volume",
    "trade_value",
    "open",
    "high",
    "low",
    "close",
    "change",
    "transaction_count",
)
REQUIRED_COLUMNS = ("date", "close")
def clean_data(data: pd.DataFrame) -> pd.DataFrame:
    """整理欄位、日期及數值格式，移除無法用於分析的資料列。"""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data 必須是 pandas.DataFrame")

    frame = data.copy()
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"缺少必要欄位：{', '.join(missing)}")

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in NUMERIC_COLUMNS:
        if column not in frame.columns:
            continue
        values = frame[column].astype("string").str.strip()
        values = values.str.replace(",", "", regex=False).str.replace("+", "", regex=False)
        values = values.replace({"": pd.NA, "--": pd.NA, "---": pd.NA, "X": pd.NA, "x": pd.NA, "N/A": pd.NA})
        frame[column] = pd.to_numeric(values, errors="coerce")

    frame = frame.dropna(subset=list(REQUIRED_COLUMNS))
    frame = frame.sort_values("date", kind="stable")
    # 同一交易日若重複，保留檔案中最後一筆，避免重複計算日指標。
    frame = frame.drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    return frame


def calculate_indicators(data: pd.DataFrame) -> pd.DataFrame:
    """以收盤價及成交股數計算每日變化與五日均量。"""
    frame = data.copy()
    if "close" not in frame.columns:
        raise ValueError("缺少必要欄位：close")

    close = pd.to_numeric(frame["close"], errors="coerce")
    previous_close = close.shift(1)
    frame["previous_close"] = previous_close
    frame["price_change"] = close - previous_close
    frame["change_percent"] = (frame["price_change"] / previous_close.where(previous_close.ne(0))) * 100

    if "trade_volume" in frame.columns:
        volume = pd.to_numeric(frame["trade_volume"], errors="coerce")
        previous_volume = volume.shift(1)
        frame["volume_change"] = volume - previous_volume
        frame["volume_change_percent"] = (
            frame["volume_change"] / previous_volume.where(previous_volume.ne(0))
        ) * 100
        frame["volume_ma_5"] = volume.rolling(window=5, min_periods=1).mean()

    return frame


def process_data(data: pd.DataFrame) -> pd.DataFrame:
    """清理原始資料並附加價格及成交量指標。"""
    return calculate_indicators(clean_data(data))


def load_data(input_path: str | Path) -> pd.DataFrame:
    """讀入爬蟲輸出的 CSV（支援 UTF-8 BOM）。"""
    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到輸入檔案：{path}")
    return process_data(pd.read_csv(path, encoding="utf-8-sig"))


def save_data(data: pd.DataFrame, output_path: str | Path) -> Path:
    """將處理結果存成 UTF-8 BOM CSV，供 Excel 與後續圖表使用。"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    LOGGER.info("已輸出 %d 筆整理後資料至 %s", len(data), path.resolve())
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="清理股價資料並計算價格與成交量指標")
    parser.add_argument("--input", type=Path, default=Path("data/raw/2327.csv"), help="原始 CSV 路徑")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/2327.csv"),
        help="整理後 CSV 輸出路徑",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    try:
        result = load_data(args.input)
        save_data(result, args.output)
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
