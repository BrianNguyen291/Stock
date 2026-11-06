"""股價圖表：收盤價走勢圖、成交量圖（含 20 日均量）及 K 線圖（含 MA5/MA20/MA60）（matplotlib）。

app.py 匯入後以 ``st.pyplot`` 顯示，也可直接執行輸出 PNG：
    python charts.py --input 2327.csv --output-dir data/charts

傳入的資料應為 ``data_processor.load_data`` 處理後的 DataFrame；均線與均量直接使用其
``ma_*``、``volume_ma_20`` 欄位，不在此重算（裁切日期前已用完整前期資料計算）。
顏色採台股慣例：紅漲、綠跌。
"""

import argparse
import logging
import math
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from data_processor import load_data

LOGGER = logging.getLogger(__name__)

UP_COLOR = "#d62728"
DOWN_COLOR = "#2ca02c"
FLAT_COLOR = "#7f7f7f"
LINE_COLOR = "#1f77b4"
MA_COLOR = "#ff7f0e"
MA_WINDOWS = (5, 20, 60)
MA_COLORS = {5: "#ff7f0e", 20: "#9467bd", 60: "#8c564b"}
VOLUME_MA_COLUMN = "volume_ma_20"
FIGSIZE = (10, 4)
MAX_DATE_TICKS = 8
CANDLE_WIDTH = 0.6
# 開盤價等於收盤價（十字線）時，實體至少保留價格區間的此比例高度，避免看不到。
MIN_BODY_RATIO = 0.002
PRICE_PADDING_RATIO = 0.05
OHLC_COLUMNS = ("open", "high", "low", "close")

# 中文字型備援：Windows、macOS、Linux 依序嘗試，避免中文顯示成方框。
CJK_FONTS = ["Microsoft JhengHei", "PingFang TC", "Heiti TC", "Noto Sans CJK TC", "Arial Unicode MS"]
matplotlib.rcParams["font.sans-serif"] = CJK_FONTS + list(matplotlib.rcParams["font.sans-serif"])
matplotlib.rcParams["axes.unicode_minus"] = False
matplotlib.rcParams.update({
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": False,
    "axes.edgecolor": "#e7e7e9",
    "axes.labelcolor": "#6f7177",
    "axes.titlecolor": "#17181a",
    "axes.titleweight": "normal",
    "axes.titlelocation": "left",
    "grid.color": "#e7e7e9",
    "xtick.color": "#6f7177",
    "ytick.color": "#6f7177",
    "xtick.major.size": 0,
    "ytick.major.size": 0,
    "legend.frameon": False,
})


def _prepare(data: pd.DataFrame, columns) -> pd.DataFrame:
    """檢查欄位並移除缺值列，回傳依日期排序的新 DataFrame。"""
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data 必須是 pandas.DataFrame")
    required = ["date", *columns]
    missing = [column for column in required if column not in data.columns]
    if missing:
        raise ValueError(f"缺少必要欄位：{', '.join(missing)}")
    frame = data.dropna(subset=required).sort_values("date", kind="stable").reset_index(drop=True)
    if frame.empty:
        raise ValueError("沒有可繪製的資料")
    return frame


def _new_axes():
    figure = Figure(figsize=FIGSIZE, layout="constrained")
    return figure, figure.add_subplot()


def _set_date_ticks(axes, dates: pd.Series) -> None:
    """以交易日序號為 x 軸（不留假日空白），並挑選部分日期當刻度。"""
    step = max(1, math.ceil(len(dates) / MAX_DATE_TICKS))
    positions = list(range(0, len(dates), step))
    axes.set_xticks(positions)
    axes.set_xticklabels([dates.iloc[i].strftime("%Y-%m-%d") for i in positions], rotation=30, ha="right")
    axes.set_xlim(-1, len(dates))
    axes.grid(axis="y", alpha=0.3)


def _change_colors(change: pd.Series) -> np.ndarray:
    # data_processor 產生的欄位可能是可為空的 Float64，先轉成一般浮點數（NA 視為平盤）。
    values = change.to_numpy(dtype=float, na_value=np.nan)
    return np.select([values > 0, values < 0], [UP_COLOR, DOWN_COLOR], FLAT_COLOR)


def _values(frame: pd.DataFrame, column: str) -> np.ndarray:
    """可為空的 Float64 轉成浮點數，缺值為 NaN：matplotlib 會留空缺而不是畫成 0。"""
    return frame[column].to_numpy(dtype=float, na_value=np.nan)


def price_trend_chart(data: pd.DataFrame, title: str = "收盤價走勢") -> Figure:
    """收盤價走勢圖。"""
    frame = _prepare(data, ["close"])
    figure, axes = _new_axes()
    axes.plot(range(len(frame)), frame["close"], color=LINE_COLOR, marker="o" if len(frame) == 1 else None)
    axes.set_title(title)
    axes.set_ylabel("收盤價（元）")
    _set_date_ticks(axes, frame["date"])
    return figure


def volume_chart(data: pd.DataFrame, title: str = "成交量", show_average: bool = True) -> Figure:
    """成交量長條圖，依當日漲跌上色；show_average 時疊加 20 日均量（需有 volume_ma_20 欄位）。"""
    frame = _prepare(data, ["trade_volume"])
    figure, axes = _new_axes()
    x = range(len(frame))
    change = frame["price_change"] if "price_change" in frame.columns else pd.Series(0, index=frame.index)
    axes.bar(x, frame["trade_volume"], width=CANDLE_WIDTH, color=_change_colors(change))
    if show_average and VOLUME_MA_COLUMN in frame.columns:
        axes.plot(x, _values(frame, VOLUME_MA_COLUMN), color=MA_COLOR, linewidth=1.5, label="20 日均量（股）")
        axes.legend(loc="upper left", fontsize="small")
    axes.set_title(title)
    axes.set_ylabel("成交量（股）")
    axes.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    _set_date_ticks(axes, frame["date"])
    return figure


def candlestick_chart(data: pd.DataFrame, title: str = "K 線圖", ma_windows=MA_WINDOWS) -> Figure:
    """日 K 線圖：收盤高於開盤為紅 K、低於開盤為綠 K，影線為當日最高與最低價。

    ma_windows 指定要疊加的均線（如 (5, 60)）；資料中沒有對應 ma_* 欄位的視窗會略過。
    """
    frame = _prepare(data, OHLC_COLUMNS)
    figure, axes = _new_axes()
    x = np.arange(len(frame))
    open_, high, low, close = (frame[column].to_numpy(dtype=float) for column in OHLC_COLUMNS)
    colors = _change_colors(pd.Series(close - open_))

    price_range = max(high.max() - low.min(), 0.01)
    min_body = price_range * MIN_BODY_RATIO
    axes.vlines(x, low, high, colors=colors, linewidth=1)
    axes.bar(
        x,
        np.maximum(np.abs(close - open_), min_body),
        bottom=np.minimum(open_, close),
        width=CANDLE_WIDTH,
        color=colors,
        edgecolor=colors,
    )
    bottom, top = low.min(), high.max()
    for window in ma_windows:
        column = f"ma_{window}"
        if column not in frame.columns:
            continue
        values = _values(frame, column)
        axes.plot(x, values, color=MA_COLORS.get(window, LINE_COLOR), linewidth=1.2, label=f"MA{window}")
        if not np.isnan(values).all():
            bottom, top = min(bottom, np.nanmin(values)), max(top, np.nanmax(values))
    if axes.get_lines():
        axes.legend(loc="upper left", ncols=len(axes.get_lines()), fontsize="small")
    # bar 的底部會黏住自動縮放邊界，手動留白，避免最低價貼齊圖框看起來像被截斷。
    padding = max(top - bottom, 0.01) * PRICE_PADDING_RATIO
    axes.set_ylim(bottom - padding, top + padding)
    axes.set_title(title)
    axes.set_ylabel("股價（元）")
    _set_date_ticks(axes, frame["date"])
    return figure


def main() -> None:
    parser = argparse.ArgumentParser(description="繪製股價走勢圖、成交量圖及 K 線圖")
    parser.add_argument("--input", type=Path, default=Path("2327.csv"), help="股票 CSV 路徑")
    parser.add_argument("--output-dir", type=Path, default=Path("data/charts"), help="PNG 輸出資料夾")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    try:
        data = load_data(args.input)
        charts = {
            "price": price_trend_chart(data),
            "volume": volume_chart(data),
            "candlestick": candlestick_chart(data),
        }
    except (FileNotFoundError, ValueError, pd.errors.ParserError) as exc:
        parser.error(str(exc))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, figure in charts.items():
        path = args.output_dir / f"{args.input.stem}_{name}.png"
        figure.savefig(path, dpi=120)
        LOGGER.info("已輸出 %s", path.resolve())


if __name__ == "__main__":
    main()
