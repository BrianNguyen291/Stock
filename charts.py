"""股價圖表：收盤價走勢圖、成交量圖（含 20 日均量）及 K 線圖（含 MA5/MA10/MA20/MA60）（matplotlib），
以及 K 線、成交量與技術指標的互動圖（TradingView lightweight-charts，``tradingview_chart``）。

app.py 匯入後以 ``st.pyplot`` / ``components.html`` 顯示，也可直接執行輸出 PNG：
    python charts.py --input 2327.csv --output-dir data/charts

傳入的資料應為 ``data_processor.load_data`` 處理後的 DataFrame；均線、均量與技術指標直接使用其
``ma_*``、``volume_ma_20``、``rsi_14`` 等欄位，不在此重算（裁切日期前已用完整前期資料計算）。
顏色採台股慣例：紅漲、綠跌。
"""

import argparse
import json
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
MA_WINDOWS = (5, 10, 20, 60)
MA_COLORS = {5: "#ff7f0e", 10: "#e377c2", 20: "#9467bd", 60: "#8c564b"}
VOLUME_MA_COLUMN = "volume_ma_20"
FIGSIZE = (10, 4)
MAX_DATE_TICKS = 8
CANDLE_WIDTH = 0.6
# 開盤價等於收盤價（十字線）時，實體至少保留價格區間的此比例高度，避免看不到。
MIN_BODY_RATIO = 0.002
PRICE_PADDING_RATIO = 0.05
OHLC_COLUMNS = ("open", "high", "low", "close")

# 互動 K 線圖（TradingView lightweight-charts）：滾輪縮放、拖曳平移、價格軸自動貼合可見區間。
# 由 CDN 載入固定版本並以 SRI 驗證檔案內容。
LIGHTWEIGHT_CHARTS_SRC = "https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js"
LIGHTWEIGHT_CHARTS_SRI = "sha384-KkYqTZlM13Zya6fVUF3IGCBOQ1ehFeJVVgxaVvD/0MebineF9EI8Jkd3NnWNChqN"
BOLLINGER_COLOR = "#17becf"
SECOND_LINE_COLOR = "#ff7f0e"
THIRD_LINE_COLOR = "#9467bd"
TRADINGVIEW_HEIGHT = 520
# 指標分頁（橫向切換，一次顯示一個）：(分頁名稱, ((線名, 欄位, 顏色), ...), 參考線)。
# OSC 等柱狀圖以 None 當顏色，依正負上色（紅正、綠負）。
INDICATOR_TABS = (
    ("MACD", (("DIF", "macd", LINE_COLOR), ("MACD", "macd_signal", SECOND_LINE_COLOR), ("OSC", "macd_hist", None)), ()),
    ("KD", (("K", "kd_k", LINE_COLOR), ("D", "kd_d", SECOND_LINE_COLOR)), (20, 80)),
    ("RSI", (("RSI14", "rsi_14", LINE_COLOR),), (30, 70)),
    ("BIAS", (("BIAS6", "bias_6", LINE_COLOR), ("BIAS12", "bias_12", SECOND_LINE_COLOR),
              ("BIAS24", "bias_24", THIRD_LINE_COLOR)), (0,)),
    ("W%R", (("%R14", "willr_14", LINE_COLOR),), (-20, -80)),
    ("OBV", (("OBV", "obv", LINE_COLOR),), ()),
    ("DMI", (("+DI", "plus_di_14", UP_COLOR), ("-DI", "minus_di_14", DOWN_COLOR), ("ADX", "adx_14", LINE_COLOR)), ()),
    ("ATR", (("ATR14", "atr_14", LINE_COLOR),), ()),
)

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


def _times(frame: pd.DataFrame) -> list[str]:
    return frame["date"].dt.strftime("%Y-%m-%d").tolist()


def _points(times: list[str], values: np.ndarray, colors=None) -> list[dict]:
    """轉成 lightweight-charts 資料點；缺值只留時間（空白點），圖上留白且日期不錯位。"""
    points = []
    for index, (time, value) in enumerate(zip(times, values)):
        if np.isnan(value):
            points.append({"time": time})
            continue
        point = {"time": time, "value": float(value)}
        if colors is not None:
            point["color"] = str(colors[index])
        points.append(point)
    return points


def _series(times: list[str], frame: pd.DataFrame, name: str, column: str, color: str | None) -> dict:
    values = _values(frame, column)
    if color is None:
        return {"name": name, "type": "histogram", "data": _points(times, values, _change_colors(pd.Series(values)))}
    return {"name": name, "type": "line", "color": color, "data": _points(times, values)}


def chart_payload(data: pd.DataFrame, title: str = "K 線圖", ma_windows=MA_WINDOWS, show_bollinger: bool = False) -> dict:
    """整理成互動 K 線圖的資料：K 線、成交量、均線/布林通道疊圖及各指標分頁，全部使用同一組交易日。

    缺少欄位的均線或指標分頁會略過。
    """
    frame = _prepare(data, [*OHLC_COLUMNS, "trade_volume"])
    times = _times(frame)
    change = frame["price_change"] if "price_change" in frame.columns else pd.Series(0, index=frame.index)
    overlays = [
        _series(times, frame, f"MA{window}", f"ma_{window}", MA_COLORS.get(window, LINE_COLOR))
        for window in ma_windows if f"ma_{window}" in frame.columns
    ]
    if show_bollinger:
        overlays += [
            _series(times, frame, name, column, BOLLINGER_COLOR)
            for name, column in (("BB上", "bb_upper"), ("BB中", "bb_middle"), ("BB下", "bb_lower"))
            if column in frame.columns
        ]
    indicators = [
        {"name": tab, "guides": list(guides),
         "series": [_series(times, frame, name, column, color) for name, column, color in lines]}
        for tab, lines, guides in INDICATOR_TABS
        if all(column in frame.columns for _, column, _ in lines)
    ]
    return {
        "title": title,
        "colors": {"up": UP_COLOR, "down": DOWN_COLOR},
        "candles": [
            {"time": time, "open": float(o), "high": float(h), "low": float(lo), "close": float(c)}
            for time, o, h, lo, c in zip(times, *(_values(frame, column) for column in OHLC_COLUMNS))
        ],
        "volume": _points(times, _values(frame, "trade_volume"), _change_colors(change)),
        "overlays": overlays,
        "indicators": indicators,
    }


def tradingview_chart(
    data: pd.DataFrame, title: str = "K 線圖", ma_windows=MA_WINDOWS, show_bollinger: bool = False
) -> str:
    """回傳可用 ``components.html`` 嵌入的互動 K 線圖 HTML（TradingView lightweight-charts）。

    上方為 K 線（含均線、布林通道），中間成交量，下方為以橫向分頁切換的指標；三者共用時間軸，
    十字游標移動時左上角顯示該日 OHLC、成交量、均線及目前指標的數值。滾輪縮放、拖曳平移，
    價格軸會自動貼合可見區間；期間按鈕可快速切換 1/3/6 個月、1 年或全部。
    """
    payload = json.dumps(chart_payload(data, title, ma_windows, show_bollinger), ensure_ascii=False, allow_nan=False)
    # 避免標題等字串中的 "</script>" 提早結束 script 區塊。
    payload = payload.replace("</", "<\\/")
    return (
        _TRADINGVIEW_TEMPLATE.replace("__SRC__", LIGHTWEIGHT_CHARTS_SRC)
        .replace("__SRI__", LIGHTWEIGHT_CHARTS_SRI)
        .replace("__HEIGHT__", str(TRADINGVIEW_HEIGHT))
        .replace("__PAYLOAD__", payload)
    )


_TRADINGVIEW_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #fff; color: #17181a;
         font: 12px -apple-system, BlinkMacSystemFont, "PingFang TC", "Microsoft JhengHei", "Noto Sans TC", sans-serif; }
  .bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; height: 36px; }
  .group { display: flex; gap: 2px; flex-wrap: nowrap; overflow-x: auto; }
  .group button { border: 0; background: transparent; color: #6f7177; padding: 5px 10px; border-radius: 6px;
                  font: inherit; cursor: pointer; white-space: nowrap; }
  .group button:hover { background: #f1f2f4; color: #17181a; }
  .group button.on { background: #17181a; color: #fff; }
  .label { color: #9a9ca2; margin-right: 4px; align-self: center; white-space: nowrap; }
  #chart { position: relative; height: calc(__HEIGHT__px - 36px); }
  .legend { position: absolute; left: 8px; z-index: 3; pointer-events: none; line-height: 18px;
            background: rgba(255, 255, 255, .75); padding: 2px 4px; border-radius: 4px; }
  .legend b { font-weight: 600; }
  .legend span { margin-right: 10px; white-space: nowrap; }
  #error { padding: 24px; color: #b42318; }
</style></head>
<body>
<div class="bar">
  <div class="group" id="periods"><span class="label">期間</span></div>
  <div class="group" id="tabs"><span class="label">指標</span></div>
</div>
<div id="chart"><div class="legend" id="legend" style="top:4px"></div><div class="legend" id="sublegend"></div></div>
<script>
const DATA = __PAYLOAD__;
function fail(message) { document.body.innerHTML = '<div id="error"></div>'; document.getElementById("error").textContent = message; }
</script>
<script src="__SRC__" integrity="__SRI__" crossorigin="anonymous"
        onerror="fail('圖表程式庫載入失敗，請確認網路連線後重新整理。')"></script>
<script>
(function () {
  if (!window.LightweightCharts) return;
  const LC = window.LightweightCharts;
  const el = document.getElementById("chart");
  const chart = LC.createChart(el, {
    autoSize: true,
    layout: { background: { color: "#ffffff" }, textColor: "#6f7177", fontSize: 11,
              panes: { separatorColor: "#e7e7e9" } },
    grid: { vertLines: { color: "#f3f3f5" }, horzLines: { color: "#f3f3f5" } },
    crosshair: { mode: LC.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#e7e7e9" },
    timeScale: { borderColor: "#e7e7e9", rightOffset: 3, minBarSpacing: 2 },
    localization: { locale: "zh-TW", dateFormat: "yyyy-MM-dd" },
  });
  const quiet = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerRadius: 3 };
  const candles = chart.addSeries(LC.CandlestickSeries, {
    upColor: DATA.colors.up, downColor: DATA.colors.down, borderUpColor: DATA.colors.up,
    borderDownColor: DATA.colors.down, wickUpColor: DATA.colors.up, wickDownColor: DATA.colors.down,
  }, 0);
  candles.setData(DATA.candles);
  const overlays = DATA.overlays.map(function (o) {
    const s = chart.addSeries(LC.LineSeries, Object.assign({ color: o.color, lineWidth: 1 }, quiet), 0);
    s.setData(o.data);
    return { meta: o, api: s };
  });
  const volume = chart.addSeries(LC.HistogramSeries, { priceFormat: { type: "volume" }, priceLineVisible: false }, 1);
  volume.setData(DATA.volume);

  const fmt = function (v, digits) {
    return v === undefined || v === null ? "—" : v.toLocaleString("zh-TW", { maximumFractionDigits: digits });
  };
  const span = function (text, color) {
    const s = document.createElement("span"); s.textContent = text; if (color) s.style.color = color; return s;
  };
  const byTime = {}; DATA.candles.forEach(function (c, i) { byTime[c.time] = i; });

  let current = null;  // { meta, apis: [{meta, api}] }
  function layoutPanes() {
    const panes = chart.panes();
    [3, 1, 1.4].forEach(function (f, i) { if (panes[i]) panes[i].setStretchFactor(f); });
    const sub = document.getElementById("sublegend");
    sub.style.top = (panes[2] ? panes[0].getHeight() + panes[1].getHeight() + 6 : -999) + "px";
  }
  function showIndicator(name) {
    if (current) current.apis.forEach(function (s) { chart.removeSeries(s.api); });
    const meta = DATA.indicators.find(function (t) { return t.name === name; });
    const apis = meta.series.map(function (line) {
      const api = line.type === "histogram"
        ? chart.addSeries(LC.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, 2)
        : chart.addSeries(LC.LineSeries, Object.assign({ color: line.color, lineWidth: 1 }, quiet), 2);
      api.setData(line.data);
      return { meta: line, api: api };
    });
    meta.guides.forEach(function (level) {
      apis[0].api.createPriceLine({ price: level, color: "#b0b3b8", lineWidth: 1, lineStyle: LC.LineStyle.Dashed,
                                    axisLabelVisible: false });
    });
    current = { meta: meta, apis: apis };
    document.querySelectorAll("#tabs button").forEach(function (b) { b.classList.toggle("on", b.textContent === name); });
    requestAnimationFrame(layoutPanes);
    render(lastTime);
  }

  const last = DATA.candles[DATA.candles.length - 1].time;
  let lastTime = last;
  function valueAt(series, time) {
    const point = series.data[byTime[time]];
    return point ? point.value : undefined;
  }
  function render(time) {
    lastTime = time;
    const i = byTime[time], c = DATA.candles[i], prev = DATA.candles[i - 1];
    const legend = document.getElementById("legend");
    legend.replaceChildren();
    const head = document.createElement("div");
    const title = document.createElement("b"); title.textContent = DATA.title + "  "; head.appendChild(title);
    head.appendChild(span(time));
    const color = c.close >= c.open ? DATA.colors.up : DATA.colors.down;
    [["開", c.open], ["高", c.high], ["低", c.low], ["收", c.close]].forEach(function (p) {
      head.appendChild(span(p[0] + " " + fmt(p[1], 2), color));
    });
    if (prev) {
      const diff = c.close - prev.close;
      head.appendChild(span((diff >= 0 ? "+" : "") + fmt(diff, 2) + " (" + fmt(diff / prev.close * 100, 2) + "%)",
                            diff > 0 ? DATA.colors.up : diff < 0 ? DATA.colors.down : null));
    }
    head.appendChild(span("量 " + fmt(DATA.volume[i].value, 0)));
    legend.appendChild(head);
    if (overlays.length) {
      const row = document.createElement("div");
      overlays.forEach(function (o) { row.appendChild(span(o.meta.name + " " + fmt(valueAt(o.meta, time), 2), o.meta.color)); });
      legend.appendChild(row);
    }
    const sub = document.getElementById("sublegend");
    sub.replaceChildren();
    if (current) {
      const name = document.createElement("b"); name.textContent = current.meta.name + "  "; sub.appendChild(name);
      current.apis.forEach(function (s) {
        sub.appendChild(span(s.meta.name + " " + fmt(valueAt(s.meta, time), 2), s.meta.color || null));
      });
    }
  }
  chart.subscribeCrosshairMove(function (param) {
    render(param.time && byTime[param.time] !== undefined ? param.time : last);
  });

  const periods = [["1 個月", 1], ["3 個月", 3], ["6 個月", 6], ["1 年", 12], ["全部", null]];
  function showPeriod(months, button) {
    document.querySelectorAll("#periods button").forEach(function (b) { b.classList.toggle("on", b === button); });
    if (months === null) { chart.timeScale().fitContent(); return; }
    const start = new Date(last + "T00:00:00Z");
    start.setUTCMonth(start.getUTCMonth() - months);
    const first = DATA.candles.find(function (c) { return c.time > start.toISOString().slice(0, 10); });
    chart.timeScale().setVisibleRange({ from: first.time, to: last });
  }
  periods.forEach(function (p) {
    const b = document.createElement("button"); b.textContent = p[0];
    b.onclick = function () { showPeriod(p[1], b); };
    document.getElementById("periods").appendChild(b);
  });
  DATA.indicators.forEach(function (t) {
    const b = document.createElement("button"); b.textContent = t.name;
    b.onclick = function () { showIndicator(t.name); };
    document.getElementById("tabs").appendChild(b);
  });
  if (DATA.indicators.length) showIndicator(DATA.indicators[0].name); else render(last);
  showPeriod(6, document.querySelectorAll("#periods button")[2]);
  new ResizeObserver(layoutPanes).observe(el);
})();
</script>
</body></html>
"""


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
