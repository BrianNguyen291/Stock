"""清理台股日交易 CSV，並計算價格與成交量指標。

可從其他模組呼叫 ``process_data``，或直接執行：
    python data_processor.py --input 2327.csv --output data/processed/2327.csv
"""

import argparse
import logging
from pathlib import Path

import numpy as np
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
RSI_WINDOW = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
KD_WINDOW = 9
KD_START = 50.0
BOLLINGER_WINDOW = 20
BOLLINGER_WIDTH = 2


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
    keys = ["stock_id", "date"] if "stock_id" in frame.columns else ["date"]
    frame = frame.sort_values(keys, kind="stable")
    # 同一交易日若重複，保留檔案中最後一筆，避免重複計算日指標。
    frame = frame.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)
    return frame


def _rsi(close: pd.Series) -> pd.Series:
    """RSI(14)：Wilder 平滑的平均漲幅占平均漲跌幅比例；完全沒有漲跌時視為 50。"""
    diff = close.diff()
    smooth = {"alpha": 1 / RSI_WINDOW, "adjust": False, "min_periods": RSI_WINDOW}
    gain = diff.clip(lower=0).ewm(**smooth).mean()
    loss = (-diff).clip(lower=0).ewm(**smooth).mean()
    total = gain + loss
    return (gain / total.where(total.ne(0)) * 100).mask(total.eq(0), 50.0)


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """MACD(12, 26, 9)：回傳 DIF（快慢 EMA 差）與其 9 日 EMA 訊號線。"""
    fast = close.ewm(span=MACD_FAST, adjust=False, min_periods=MACD_FAST).mean()
    slow = close.ewm(span=MACD_SLOW, adjust=False, min_periods=MACD_SLOW).mean()
    dif = fast - slow
    return dif, dif.ewm(span=MACD_SIGNAL, adjust=False, min_periods=MACD_SIGNAL).mean()


def _kd(close: pd.Series, high: pd.Series, low: pd.Series) -> tuple[pd.Series, pd.Series]:
    """KD(9, 3, 3)，台股算法：K、D 由 50 起算，K = 2/3 前日 K + 1/3 RSV，D 同理平滑 K。

    9 日區間沒有高低差時 RSV 視為 50；視窗內有缺值的日子 K、D 留空，下一個有效日接續前值。
    """
    highest = high.rolling(KD_WINDOW, min_periods=KD_WINDOW).max()
    lowest = low.rolling(KD_WINDOW, min_periods=KD_WINDOW).min()
    span = highest - lowest
    rsv = ((close - lowest) / span.where(span.ne(0)) * 100).mask(span.eq(0), 50.0)
    k_values, d_values = [], []
    k, d = KD_START, KD_START
    for value in rsv.to_numpy(dtype=float, na_value=np.nan):
        if np.isnan(value):
            k_values.append(np.nan)
            d_values.append(np.nan)
            continue
        k = k * 2 / 3 + value / 3
        d = d * 2 / 3 + k / 3
        k_values.append(k)
        d_values.append(d)
    return pd.Series(k_values, index=close.index), pd.Series(d_values, index=close.index)


def calculate_indicators(data: pd.DataFrame) -> pd.DataFrame:
    """依股票計算完整交易日視窗的均線與技術指標；資料不足時保留缺值。

    技術指標：RSI(14)、MACD(12, 26, 9)、KD(9, 3, 3，需有 high/low 欄位)、
    布林通道（20 日均線 ± 2 倍母體標準差）。
    """
    frame = data.copy()
    if "close" not in frame.columns:
        raise ValueError("缺少必要欄位：close")
    if "date" in frame.columns:
        keys = ["stock_id", "date"] if "stock_id" in frame.columns else ["date"]
        frame = frame.sort_values(keys, kind="stable").reset_index(drop=True)
    if "stock_id" in frame.columns and not frame.empty:
        return pd.concat(
            [calculate_indicators(group.drop(columns="stock_id")).assign(stock_id=stock_id)
             for stock_id, group in frame.groupby("stock_id", sort=False)],
            ignore_index=True,
        )

    close = pd.to_numeric(frame["close"], errors="coerce")
    previous_close = close.shift(1)
    frame["previous_close"] = previous_close
    frame["price_change"] = close - previous_close
    frame["change_percent"] = (frame["price_change"] / previous_close.where(previous_close.ne(0))) * 100
    for window in (5, 20, 60):
        frame[f"ma_{window}"] = close.rolling(window=window, min_periods=window).mean()

    frame["rsi_14"] = _rsi(close)
    frame["macd"], frame["macd_signal"] = _macd(close)
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]
    rolling = close.rolling(window=BOLLINGER_WINDOW, min_periods=BOLLINGER_WINDOW)
    middle, std = rolling.mean(), rolling.std(ddof=0)
    frame["bb_middle"] = middle
    frame["bb_upper"] = middle + BOLLINGER_WIDTH * std
    frame["bb_lower"] = middle - BOLLINGER_WIDTH * std
    if {"high", "low"} <= set(frame.columns):
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        frame["kd_k"], frame["kd_d"] = _kd(close, high, low)

    if "trade_volume" in frame.columns:
        volume = pd.to_numeric(frame["trade_volume"], errors="coerce")
        previous_volume = volume.shift(1)
        frame["volume_change"] = volume - previous_volume
        frame["volume_change_percent"] = (
            frame["volume_change"] / previous_volume.where(previous_volume.ne(0))
        ) * 100
        for window in (5, 20):
            frame[f"volume_ma_{window}"] = volume.rolling(window=window, min_periods=window).mean()

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
