"""charts.py 測試：python -m pytest tests/test_charts.py"""

import json
import warnings
from io import BytesIO

import pandas as pd
import pytest
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure

from charts import (
    DOWN_COLOR,
    FLAT_COLOR,
    LIGHTWEIGHT_CHARTS_SRC,
    LIGHTWEIGHT_CHARTS_SRI,
    UP_COLOR,
    candlestick_chart,
    chart_payload,
    price_trend_chart,
    tradingview_chart,
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
    assert list(lines) == ["MA5", "MA10", "MA20", "MA60"]
    shown = long_sample.dropna(subset=["open"]).reset_index(drop=True)
    for position in (10, 40, 63):  # 抽查缺值列前後的日期，確認沒有錯位
        for window in (5, 10, 20, 60):
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


# ---- TradingView 互動 K 線圖 ----


def values(series):
    return [point.get("value") for point in series["data"]]


def test_payload_aligns_candles_volume_overlays_and_indicators(long_sample):
    before = long_sample.copy()
    payload = chart_payload(long_sample)
    pd.testing.assert_frame_equal(long_sample, before)
    shown = long_sample.dropna(subset=["open"]).reset_index(drop=True)  # 缺開盤價的那天不畫
    times = shown["date"].dt.strftime("%Y-%m-%d").tolist()
    assert [candle["time"] for candle in payload["candles"]] == times
    assert [point["time"] for point in payload["volume"]] == times
    for series in payload["overlays"] + [line for tab in payload["indicators"] for line in tab["series"]]:
        assert [point["time"] for point in series["data"]] == times, series["name"]
    assert payload["candles"][0] == {"time": times[0], "open": 100.0, "high": 110.0, "low": 95.0, "close": 101.0}
    assert values({"data": payload["volume"]}) == shown["trade_volume"].tolist()
    json.dumps(payload, allow_nan=False)  # 不能把 NaN 交給瀏覽器


def test_payload_uses_taiwan_colors(sample):
    payload = chart_payload(sample)
    assert payload["colors"] == {"up": UP_COLOR, "down": DOWN_COLOR}
    # 成交量依當日漲跌（與前一日收盤比）上色。
    assert [point["color"] for point in payload["volume"]] == [FLAT_COLOR, DOWN_COLOR, FLAT_COLOR]


def test_missing_values_become_whitespace_points(long_sample):
    ma60 = next(series for series in chart_payload(long_sample)["overlays"] if series["name"] == "MA60")
    assert all(point == {"time": point["time"]} for point in ma60["data"][:58])  # 視窗不足留白
    assert "value" in ma60["data"][-1]


def test_moving_averages_and_bollinger_can_be_toggled(long_sample):
    names = [series["name"] for series in chart_payload(long_sample)["overlays"]]
    assert names == ["MA5", "MA10", "MA20", "MA60"]
    names = [series["name"] for series in chart_payload(long_sample, ma_windows=(20,), show_bollinger=True)["overlays"]]
    assert names == ["MA20", "BB上", "BB中", "BB下"]


def test_indicator_tabs_in_order(long_sample):
    tabs = chart_payload(long_sample)["indicators"]
    assert [tab["name"] for tab in tabs] == ["MACD", "KD", "RSI", "BIAS", "W%R", "OBV", "DMI", "ATR"]
    macd = tabs[0]
    assert [line["type"] for line in macd["series"]] == ["line", "line", "histogram"]
    assert tabs[1]["guides"] == [20, 80]


def test_indicator_values_match_data_processor(long_sample):
    shown = long_sample.dropna(subset=["open"]).reset_index(drop=True)
    columns = {"DIF": "macd", "OSC": "macd_hist", "K": "kd_k", "RSI14": "rsi_14", "BIAS6": "bias_6",
               "%R14": "willr_14", "OBV": "obv", "ADX": "adx_14", "ATR14": "atr_14"}
    lines = {line["name"]: line for tab in chart_payload(long_sample)["indicators"] for line in tab["series"]}
    for name, column in columns.items():
        actual = values(lines[name])
        for position in (10, 28, 29, 40, 63):  # 第 29 列前後：略過的那天不能讓之後的指標錯位
            expected = shown.loc[position, column]
            assert (pd.isna(expected) and actual[position] is None) or actual[position] == pytest.approx(expected), name


def test_indicator_tab_skipped_when_columns_missing(long_sample):
    names = [tab["name"] for tab in chart_payload(long_sample.drop(columns=["bias_6"]))["indicators"]]
    assert "BIAS" not in names and "MACD" in names


def test_payload_rejects_bad_data(sample):
    with pytest.raises(ValueError, match="缺少必要欄位"):
        chart_payload(pd.DataFrame({"date": pd.to_datetime(["2026-01-02"])}))
    with pytest.raises(ValueError, match="沒有可繪製的資料"):
        chart_payload(sample.iloc[:0])


def test_html_loads_pinned_library_with_integrity_and_embeds_data(sample):
    html = tradingview_chart(sample, title="2330 台積電")
    assert f'src="{LIGHTWEIGHT_CHARTS_SRC}" integrity="{LIGHTWEIGHT_CHARTS_SRI}"' in html
    assert "@5.2.1/" in LIGHTWEIGHT_CHARTS_SRC
    assert "2330 台積電" in html
    assert "__" not in html.split("<body>")[1].split("const DATA")[0]  # 沒有遺留的佔位字


def test_html_escapes_script_breaking_title(sample):
    html = tradingview_chart(sample, title="</script><script>alert(1)</script>")
    assert "</script><script>alert(1)" not in html
    data = html.split("const DATA = ")[1].split(";\nfunction fail")[0]
    assert json.loads(data.replace("<\\/", "</"))["title"] == "</script><script>alert(1)</script>"


@pytest.mark.parametrize("path", ["2327.csv", "2492.csv"])
def test_repository_data_tradingview(path):
    payload = chart_payload(load_data(path))
    json.dumps(payload, allow_nan=False)
    assert len(payload["indicators"]) == 8
