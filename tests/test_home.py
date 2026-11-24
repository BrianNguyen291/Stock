"""首頁與背景地圖的渲染測試。"""

import json
import re
from pathlib import Path

import market_map
from streamlit.testing.v1 import AppTest


def test_map_document_embeds_land_without_placeholder(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        market_map.components, "html", lambda document, height: captured.update(document=document)
    )
    market_map.render_market_map()
    assert "__LAND__" not in captured["document"]
    land = json.loads(re.search(r"const LAND = (.*);", captured["document"]).group(1))
    assert len(land) == len(market_map.LAND)


def test_home_page_renders_stock_cards(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_DB_PATH", str(tmp_path / "stock.sqlite3"))
    app = AppTest.from_file(str(Path(__file__).parent.parent / "app.py"), default_timeout=60).run()
    assert not app.exception
    labels = [button.label for button in app.button]
    assert labels.count("查看個股 →") >= 1
    assert "登入" in labels


def test_stock_picker_clears_previous_page_slot_on_navigation(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_DB_PATH", str(tmp_path / "stock.sqlite3"))
    app = AppTest.from_file(str(Path(__file__).parent.parent / "app.py"), default_timeout=60).run()

    next(button for button in app.button if button.label == "條件選股").click().run()
    assert not app.exception
    assert app.main.children[0].type == "empty"
    assert app.subheader[0].value == "條件選股"

    app.checkbox[0].uncheck().run()
    assert not app.exception
    assert app.main.children[0].type == "empty"
    assert app.checkbox[0].value is False

    app.radio(key="dashboard_page").set_value("個股儀表板").run()
    assert not app.exception
    assert app.main.children[1].type == "empty"

    app.radio(key="dashboard_page").set_value("條件選股").run()
    assert not app.exception
    assert app.main.children[0].type == "empty"
    assert app.subheader[0].value == "條件選股"
