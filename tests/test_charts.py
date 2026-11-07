"""charts.py 測試：python -m pytest tests/test_charts.py"""

import warnings
from io import BytesIO
from itertools import pairwise

import pandas as pd
import plotly.graph_objects as go
import pytest
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure

from charts import (
    DOWN_COLOR,
    FLAT_COLOR,
    INDICATORS,
    UP_COLOR,
    candlestick_chart,
    interactive_chart,
    price_trend_chart,
    recent_period,
    volume_chart,
)
from data_processor import load_data, process_data

CHARTS = (price_trend_chart, volume_chart, candlestick_chart)


@pytest.fixture
def sample():
    raw = pd.DataFrame(
        {
            "date": ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"],
            "open": [100, 105, 103, None],  # 最後一列缺開盤價：K 線略過，其他圖照畫
            "high": [106, 107, 104, 105],
            "low": [99, 102, 100, 101],
            "close": [105, 103, 103, 104],
            "trade_volume": ["1,000", "2,000", "1,500", "1,200"],
        }
    )
    return process_data(raw)


def render(figure: Figure) -> None:
    """實際輸出 PNG；缺中文字型時 matplotlib 會發出 Glyph missing 警告，這裡轉成錯誤。"""
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=".*Glyph.*missing.*")
        figure.savefig(BytesIO(), format="png")


@pytest.mark.parametrize("chart", CHARTS)
def test_returns_renderable_figure_without_mutating_input(chart, sample):
    before = sample.copy()
    figure = chart(sample)
    assert isinstance(figure, Figure)
    render(figure)
    pd.testing.assert_frame_equal(sample, before)


@pytest.mark.parametrize("chart", CHARTS)
def test_single_trading_day(chart, sample):
    render(chart(sample.iloc[:1]))


@pytest.mark.parametrize("chart", CHARTS)
def test_missing_column_raises(chart):
    with pytest.raises(ValueError, match="缺少必要欄位"):
        chart(pd.DataFrame({"date": pd.to_datetime(["2026-01-02"])}))


@pytest.mark.parametrize("chart", CHARTS)
def test_empty_data_raises(chart, sample):
    with pytest.raises(ValueError, match="沒有可繪製的資料"):
        chart(sample.iloc[:0])


def test_candlestick_colors_follow_taiwan_convention(sample):
    bodies = candlestick_chart(sample).axes[0].patches
    assert len(bodies) == 3  # 缺開盤價的那天不畫
    colors = [patch.get_facecolor() for patch in bodies]
    assert colors == [to_rgba(UP_COLOR), to_rgba(DOWN_COLOR), to_rgba(FLAT_COLOR)]
    assert all(patch.get_height() > 0 for patch in bodies)  # 十字線也看得到


def test_candlestick_keeps_margin_around_price_range(sample):
    bottom, top = candlestick_chart(sample).axes[0].get_ylim()
    assert bottom < 99 and top > 107  # 最低價 99、最高價 107 都不貼邊


def test_volume_chart_has_twenty_day_average(sample):
    axes = volume_chart(sample).axes[0]
    assert len(axes.patches) == 4
    assert [line.get_label() for line in axes.get_lines()] == ["20 日均量（股）"]
    assert "股" in axes.get_ylabel()


def test_volume_average_can_be_hidden(sample):
    assert volume_chart(sample, show_average=False).axes[0].get_lines() == []


@pytest.fixture
def long_sample():
    """65 個交易日：MA60 只有最後 6 天完整；第 30 天缺開盤價，K 線會略過該列。"""
    days = 65
    raw = pd.DataFrame(
        {
            "date": pd.bdate_range("2026-01-05", periods=days).strftime("%Y-%m-%d"),
            "open": [100 + i for i in range(days)],
            "high": [110 + i for i in range(days)],
            "low": [95 + i for i in range(days)],
            "close": [101 + i for i in range(days)],
            "trade_volume": [1000 * (i + 1) for i in range(days)],
        }
    )
    raw.loc[29, "open"] = None
    return process_data(raw)


def test_candlestick_moving_averages_match_data_processor(long_sample):
    axes = candlestick_chart(long_sample).axes[0]
    lines = {line.get_label(): line.get_ydata() for line in axes.get_lines()}
    assert list(lines) == ["MA5", "MA20", "MA60"]
    shown = long_sample.dropna(subset=["open"]).reset_index(drop=True)
    for position in (10, 40, 63):  # 抽查缺值列前後的日期，確認沒有錯位
        for window in (5, 20, 60):
            expected = shown.loc[position, f"ma_{window}"]
            actual = lines[f"MA{window}"][position]
            assert (pd.isna(expected) and pd.isna(actual)) or actual == pytest.approx(expected)
    # 視窗不足時保留空缺，不畫成 0。
    assert pd.isna(lines["MA60"][:58]).all() and pd.isna(lines["MA5"][:4]).all()
    assert lines["MA5"][4] == pytest.approx(103)  # 收盤 101~105 的平均
    render(axes.figure)


def test_candlestick_moving_averages_can_be_toggled(long_sample):
    labels = [line.get_label() for line in candlestick_chart(long_sample, ma_windows=(20,)).axes[0].get_lines()]
    assert labels == ["MA20"]
    assert candlestick_chart(long_sample, ma_windows=()).axes[0].get_lines() == []
    assert candlestick_chart(long_sample, ma_windows=()).axes[0].get_legend() is None


def test_volume_average_matches_data_processor(long_sample):
    line = volume_chart(long_sample).axes[0].get_lines()[0]
    expected = long_sample["volume_ma_20"].to_numpy(dtype=float, na_value=float("nan"))
    assert pd.isna(line.get_ydata()[:19]).all()
    assert line.get_ydata()[19:] == pytest.approx(expected[19:])
    assert line.get_ydata()[19] == pytest.approx(10500)  # 1000~20000 的平均


@pytest.mark.parametrize("path", ["2327.csv", "2492.csv"])
def test_repository_data(path):
    data = load_data(path)
    for chart in CHARTS:
        render(chart(data))


# ---- Plotly 互動圖表 ----

PANEL_AXES = {"rsi": ["RSI(14)"], "macd": ["DIF", "MACD", "OSC"], "kd": ["K(9)", "D(9)"]}


def traces(figure, axis=None):
    return {trace.name: trace for trace in figure.data if axis is None or trace.yaxis == axis}


def axis_ids(figure):
    return sorted({trace.yaxis for trace in figure.data}, key=lambda name: int(name[1:] or 1))


def test_interactive_chart_shows_candles_and_volume_on_shared_dates(long_sample):
    before = long_sample.copy()
    figure = interactive_chart(long_sample)
    assert isinstance(figure, go.Figure)
    pd.testing.assert_frame_equal(long_sample, before)
    assert axis_ids(figure) == ["y", "y2"]
    candle, volume = traces(figure)["K 線"], traces(figure)["成交量"]
    assert candle.type == "candlestick" and candle.yaxis == "y"
    assert volume.type == "bar" and volume.yaxis == "y2"
    shown = long_sample.dropna(subset=["open"]).reset_index(drop=True)  # 缺開盤價的那天不畫
    expected_dates = shown["date"].to_numpy()
    for trace in figure.data:
        assert trace.xaxis == "x"  # 同一條日期軸，縮放與游標會一起移動
        assert (pd.to_datetime(trace.x).to_numpy() == expected_dates).all()
    assert list(volume.y) == shown["trade_volume"].tolist()
    figure.to_json()  # plotly 驗證整份設定


def test_interactive_chart_uses_taiwan_colors(sample):
    figure = interactive_chart(sample)
    candle, volume = traces(figure)["K 線"], traces(figure)["成交量"]
    assert candle.increasing.line.color == UP_COLOR and candle.increasing.fillcolor == UP_COLOR
    assert candle.decreasing.line.color == DOWN_COLOR and candle.decreasing.fillcolor == DOWN_COLOR
    # 成交量依當日漲跌（與前一日收盤比）上色；缺開盤價的那天不畫。
    assert list(volume.marker.color) == [FLAT_COLOR, DOWN_COLOR, FLAT_COLOR]


def test_interactive_chart_has_crosshair_unified_hover_and_zoom(sample):
    layout = interactive_chart(sample).layout
    assert layout.hovermode == "x unified" and layout.hoversubplots == "axis"
    assert layout.xaxis.showspikes and "across" in layout.xaxis.spikemode
    assert layout.xaxis.rangeslider.visible is False
    assert layout.dragmode == "zoom"


def test_interactive_chart_skips_non_trading_days(long_sample):
    figure = interactive_chart(long_sample)
    breaks = set(figure.layout.xaxis.rangebreaks[0]["values"])
    assert "2026-01-10" in breaks  # 週六
    assert long_sample.loc[29, "date"].strftime("%Y-%m-%d") in breaks  # 缺開盤價而略過的那天
    shown = long_sample.dropna(subset=["open"])["date"].dt.strftime("%Y-%m-%d")
    assert breaks.isdisjoint(shown)


def test_interactive_chart_moving_averages_can_be_toggled(long_sample):
    assert [name for name in traces(interactive_chart(long_sample), "y") if name.startswith("MA")] == [
        "MA5", "MA20", "MA60"]
    assert "MA5" not in traces(interactive_chart(long_sample, ma_windows=(20,)))


@pytest.mark.parametrize("indicator", ["rsi", "macd", "kd"])
def test_indicator_panel_added_below_volume(indicator, long_sample):
    figure = interactive_chart(long_sample, indicators=(indicator,))
    assert axis_ids(figure) == ["y", "y2", "y3"]
    assert list(traces(figure, "y3")) == PANEL_AXES[indicator]


def test_bollinger_bands_overlay_price_axis(long_sample):
    figure = interactive_chart(long_sample, indicators=("bollinger",))
    assert axis_ids(figure) == ["y", "y2"]
    assert {"布林上軌", "布林中軌", "布林下軌"} <= set(traces(figure, "y"))


def test_all_indicators_stack_in_fixed_order_without_overlap(long_sample):
    figure = interactive_chart(long_sample, indicators=("kd", "bollinger", "rsi", "macd"))
    assert axis_ids(figure) == ["y", "y2", "y3", "y4", "y5"]
    assert list(traces(figure, "y3")) == PANEL_AXES["rsi"]
    assert list(traces(figure, "y5")) == PANEL_AXES["kd"]
    domains = [figure.layout[f"yaxis{n}" if n > 1 else "yaxis"].domain for n in range(1, 6)]
    for upper, lower in pairwise(domains):
        assert lower[1] < upper[0]  # 由上而下排列且不重疊
    figure.to_json()


def test_indicator_values_align_with_data_processor(long_sample):
    figure = interactive_chart(long_sample, indicators=("bollinger", "rsi", "macd", "kd"))
    shown = long_sample.dropna(subset=["open"]).reset_index(drop=True)
    columns = {"布林上軌": "bb_upper", "RSI(14)": "rsi_14", "DIF": "macd", "MACD": "macd_signal",
               "OSC": "macd_hist", "K(9)": "kd_k", "D(9)": "kd_d"}
    for name, column in columns.items():
        values = traces(figure)[name].y
        for position in (10, 28, 29, 40, 63):  # 第 29 列前後：略過的那天不能讓之後的指標錯位
            expected, actual = shown.loc[position, column], values[position]
            assert (pd.isna(expected) and pd.isna(actual)) or actual == pytest.approx(expected), name


def test_unknown_indicator_raises(sample):
    with pytest.raises(ValueError, match="不支援的指標"):
        interactive_chart(sample, indicators=("obv",))


def test_interactive_chart_rejects_bad_data(sample):
    with pytest.raises(ValueError, match="缺少必要欄位"):
        interactive_chart(pd.DataFrame({"date": pd.to_datetime(["2026-01-02"])}))
    with pytest.raises(ValueError, match="沒有可繪製的資料"):
        interactive_chart(sample.iloc[:0])


def test_interactive_chart_single_trading_day(sample):
    interactive_chart(sample.iloc[:1], indicators=tuple(INDICATORS)).to_json()


def test_recent_period_keeps_trailing_months(long_sample):
    before = long_sample.copy()
    result = recent_period(long_sample, 1)
    latest = long_sample["date"].max()
    assert result["date"].max() == latest
    assert result["date"].min() > latest - pd.DateOffset(months=1)
    assert len(result) < len(long_sample)
    pd.testing.assert_frame_equal(recent_period(long_sample, None), long_sample)
    pd.testing.assert_frame_equal(long_sample, before)


@pytest.mark.parametrize("path", ["2327.csv", "2492.csv"])
def test_repository_data_interactive(path):
    interactive_chart(load_data(path), indicators=tuple(INDICATORS)).to_json()
