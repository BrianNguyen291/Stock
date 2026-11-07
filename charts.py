"""股價圖表：收盤價走勢圖、成交量圖（含 20 日均量）及 K 線圖（含 MA5/MA20/MA60）（matplotlib），
以及 K 線、成交量與技術指標的互動圖（Plotly，``interactive_chart``）。

app.py 匯入後以 ``st.pyplot`` / ``st.plotly_chart`` 顯示，也可直接執行輸出 PNG：
    python charts.py --input 2327.csv --output-dir data/charts

傳入的資料應為 ``data_processor.load_data`` 處理後的 DataFrame；均線、均量與技術指標直接使用其
``ma_*``、``volume_ma_20``、``rsi_14`` 等欄位，不在此重算（裁切日期前已用完整前期資料計算）。
顏色採台股慣例：紅漲、綠跌。
"""

import argparse
import logging
import math
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import plotly.graph_objects as go
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

# 互動圖可勾選的指標；布林通道疊在 K 線上，其餘各佔一列，依此順序排在成交量下方。
INDICATORS = {"bollinger": "布林通道", "rsi": "RSI", "macd": "MACD", "kd": "KD"}
PANEL_INDICATORS = ("rsi", "macd", "kd")
INDICATOR_COLUMNS = {
    "bollinger": ("bb_upper", "bb_middle", "bb_lower"),
    "rsi": ("rsi_14",),
    "macd": ("macd", "macd_signal", "macd_hist"),
    "kd": ("kd_k", "kd_d"),
}
# 期間切換：以最後交易日往前推的月數，None 為全部。
PERIODS = {"1 個月": 1, "3 個月": 3, "6 個月": 6, "1 年": 12, "全部": None}
BOLLINGER_COLOR = "#17becf"
SECOND_LINE_COLOR = "#ff7f0e"
GUIDE_COLOR = "#b0b3b8"
PRICE_ROW_WEIGHT = 3
ROW_GAP = 0.03
BASE_HEIGHT = 520
PANEL_HEIGHT = 150
OSCILLATOR_GUIDES = {"rsi": (30, 70), "kd": (20, 80)}

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


def recent_period(data: pd.DataFrame, months: int | None) -> pd.DataFrame:
    """保留最後交易日往前 months 個月內的資料；months 為 None 時原樣回傳。"""
    if months is None:
        return data
    start = data["date"].max() - pd.DateOffset(months=months)
    return data[data["date"] > start]


def _row_domains(rows: int) -> list[list[float]]:
    """由上而下各列的 y 範圍：K 線佔 PRICE_ROW_WEIGHT 份，其餘每列 1 份，列間留 ROW_GAP。"""
    weights = [PRICE_ROW_WEIGHT] + [1] * (rows - 1)
    usable = 1 - ROW_GAP * (rows - 1)
    top, domains = 1.0, []
    for weight in weights:
        bottom = max(top - usable * weight / sum(weights), 0.0)
        domains.append([round(bottom, 4), round(top, 4)])
        top = bottom - ROW_GAP
    return domains


def _line(frame: pd.DataFrame, column: str, name: str, color: str, axis: str, dash: str | None = None):
    return go.Scatter(
        x=frame["date"], y=_values(frame, column), name=name, mode="lines",
        line={"color": color, "width": 1.2, "dash": dash}, xaxis="x", yaxis=axis, yhoverformat=".2f",
    )


def _price_traces(frame: pd.DataFrame, ma_windows, show_bollinger: bool) -> list:
    traces = [go.Candlestick(
        x=frame["date"], open=frame["open"], high=frame["high"], low=frame["low"], close=frame["close"],
        name="K 線", xaxis="x", yaxis="y",
        increasing={"line": {"color": UP_COLOR}, "fillcolor": UP_COLOR},
        decreasing={"line": {"color": DOWN_COLOR}, "fillcolor": DOWN_COLOR},
    )]
    traces += [
        _line(frame, f"ma_{window}", f"MA{window}", MA_COLORS.get(window, LINE_COLOR), "y")
        for window in ma_windows if f"ma_{window}" in frame.columns
    ]
    if show_bollinger:
        for column, name, dash in (("bb_upper", "布林上軌", "dot"), ("bb_middle", "布林中軌", None),
                                   ("bb_lower", "布林下軌", "dot")):
            traces.append(_line(frame, column, name, BOLLINGER_COLOR, "y", dash))
    return traces


def _volume_traces(frame: pd.DataFrame) -> list:
    change = frame["price_change"] if "price_change" in frame.columns else pd.Series(0, index=frame.index)
    traces = [go.Bar(
        x=frame["date"], y=_values(frame, "trade_volume"), name="成交量", xaxis="x", yaxis="y2", yhoverformat=",.0f",
        marker={"color": list(_change_colors(change))},
    )]
    if VOLUME_MA_COLUMN in frame.columns:
        traces.append(_line(frame, VOLUME_MA_COLUMN, "20 日均量", MA_COLOR, "y2").update(yhoverformat=",.0f"))
    return traces


def _panel_traces(frame: pd.DataFrame, indicator: str, axis: str) -> list:
    if indicator == "rsi":
        return [_line(frame, "rsi_14", "RSI(14)", LINE_COLOR, axis)]
    if indicator == "kd":
        return [_line(frame, "kd_k", "K(9)", LINE_COLOR, axis), _line(frame, "kd_d", "D(9)", SECOND_LINE_COLOR, axis)]
    histogram = _values(frame, "macd_hist")
    return [
        _line(frame, "macd", "DIF", LINE_COLOR, axis),
        _line(frame, "macd_signal", "MACD", SECOND_LINE_COLOR, axis),
        go.Bar(x=frame["date"], y=histogram, name="OSC", xaxis="x", yaxis=axis, yhoverformat=".2f",
               marker={"color": list(_change_colors(pd.Series(histogram)))}),
    ]


def _guide_lines(levels, axis: str) -> list[dict]:
    """RSI 30/70、KD 20/80 超買超賣參考線，橫跨整個圖寬。"""
    return [
        {"type": "line", "xref": "paper", "x0": 0, "x1": 1, "yref": axis, "y0": level, "y1": level,
         "line": {"color": GUIDE_COLOR, "width": 1, "dash": "dash"}}
        for level in levels
    ]


def interactive_chart(
    data: pd.DataFrame, title: str = "K 線圖", ma_windows=MA_WINDOWS, indicators=()
) -> go.Figure:
    """Plotly 互動圖：K 線、成交量及勾選的技術指標上下排列，共用同一條交易日軸。

    indicators 可含 INDICATORS 的鍵：bollinger 疊在 K 線上，rsi/macd/kd 依序各加一列。
    所有列共用 x 軸，縮放與十字游標會一起移動，游標停留時同時顯示該日價格、成交量與指標；
    休市日以 rangebreaks 略過，K 線不會出現空白。
    """
    unknown = [name for name in indicators if name not in INDICATORS]
    if unknown:
        raise ValueError(f"不支援的指標：{', '.join(unknown)}")
    frame = _prepare(data, [*OHLC_COLUMNS, "trade_volume"])
    needed = [column for name in indicators for column in INDICATOR_COLUMNS[name]]
    missing = [column for column in needed if column not in frame.columns]
    if missing:
        raise ValueError(f"缺少必要欄位：{', '.join(missing)}")

    panels = [name for name in PANEL_INDICATORS if name in indicators]
    domains = _row_domains(2 + len(panels))
    axes = ["y", "y2", *(f"y{row}" for row in range(3, 3 + len(panels)))]
    traces = _price_traces(frame, ma_windows, "bollinger" in indicators) + _volume_traces(frame)
    shapes = []
    for name, axis in zip(panels, axes[2:]):
        traces += _panel_traces(frame, name, axis)
        shapes += _guide_lines(OSCILLATOR_GUIDES.get(name, ()), axis)

    titles = ["股價（元）", "成交量（股）", *(INDICATORS[name] for name in panels)]
    layout_axes = {
        ("yaxis" if axis == "y" else f"yaxis{axis[1:]}"): {
            "domain": domain, "anchor": "x", "title": {"text": axis_title}, "showspikes": axis == "y",
            "spikemode": "across", "spikethickness": 1, "spikedash": "dot", "spikecolor": GUIDE_COLOR,
        }
        for axis, domain, axis_title in zip(axes, domains, titles)
    }
    layout_axes["yaxis2"]["tickformat"] = ",.0f"
    for name, axis in zip(panels, axes[2:]):
        if name in OSCILLATOR_GUIDES:
            layout_axes[f"yaxis{axis[1:]}"]["range"] = [0, 100]

    dates = pd.DatetimeIndex(frame["date"]).normalize()
    closed = pd.date_range(dates.min(), dates.max(), freq="D").difference(dates)
    figure = go.Figure(data=traces)
    figure.update_layout(
        title={"text": title},
        height=BASE_HEIGHT + PANEL_HEIGHT * len(panels),
        hovermode="x unified",
        hoversubplots="axis",
        dragmode="zoom",
        bargap=0.2,
        shapes=shapes,
        # 指標多時圖例會換行；放在圖下方由 plotly 自動加大下邊界，不會壓到標題。
        legend={"orientation": "h", "yanchor": "top", "y": -0.04, "xanchor": "left", "x": 0},
        margin={"l": 60, "r": 20, "t": 50, "b": 40},
        xaxis={
            "type": "date", "anchor": axes[-1], "rangeslider": {"visible": False},
            "rangebreaks": [{"values": [day.strftime("%Y-%m-%d") for day in closed]}],
            "showspikes": True, "spikemode": "across", "spikesnap": "cursor",
            "spikethickness": 1, "spikedash": "dot", "spikecolor": GUIDE_COLOR,
            "tickformat": "%Y-%m-%d", "hoverformat": "%Y-%m-%d",
        },
        **layout_axes,
    )
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
