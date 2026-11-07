"""Make Stock Signal homepage search fetch TWSE and persist to SQLite.

Run from a feature branch in the project's root:
    python fix_home_twse_search.py

Requires the previous stock-universe integration (refresh_stock_files /
StockDatabase.list_available_stocks). Safely refuses to edit if it is missing.
"""
from __future__ import annotations

import ast
from pathlib import Path
import shutil

ROOT = Path.cwd()
HOME = ROOT / "home.py"
LOOKUP = ROOT / "stock_lookup.py"
APP = ROOT / "app.py"
DATABASE = ROOT / "database.py"


def replace_one(source: str, old: str, new: str, file: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{file}: expected exactly 1 matching code block, got {count}. No files changed.")
    return source.replace(old, new, 1)


def main():
    for file in [HOME, LOOKUP, APP, DATABASE]:
        if not file.is_file():
            raise RuntimeError(f"Missing {file.name}. Run from D:\\Stock")

    app = APP.read_text(encoding="utf-8")
    db = DATABASE.read_text(encoding="utf-8")
    if "def refresh_stock_files(" not in app or "    refresh_stock_files()" not in app or "def list_available_stocks(" not in db:
        raise RuntimeError(
            "Your app does not yet load stock options dynamically from SQLite. "
            "Apply the previous 4-file stock-universe changes before running this patch."
        )

    old_home = HOME.read_text(encoding="utf-8")
    old_lookup = LOOKUP.read_text(encoding="utf-8")
    if 'key="home_twse_search"' in old_home:
        raise RuntimeError("Homepage TWSE search is already installed; no changes were made.")

    start_marker = 'def render_home(stock_labels, quotes, navigate):\n'
    end_marker = '\n\ndef render_login('
    start = old_home.find(start_marker)
    end = old_home.find(end_marker, start)
    if start < 0 or end < 0:
        raise RuntimeError("home.py: render_home function not found. No files changed.")
    homepage = old_home[start:end]
    old_callback = '''    def open_selected_stock():
        selected = st.session_state.get("home_stock")
        if selected:
            navigate("個股儀表板", selected)
'''
    new_callback = '''    def search_and_open_stock(query: str) -> None:
        """Only network-fetch after form submit, then open persisted dashboard data."""
        from database import StockDatabase
        from stock_lookup import fetch_and_store_stock, validate_stock_id

        query = query.strip()
        if not query:
            st.warning("請輸入股票代號，例如 2330、2327、009816。")
            return
        try:
            db = StockDatabase()
            if not query.isdigit():
                matched = [item for item in db.list_available_stocks()
                           if item["stock_name"] == query or
                           f'{item["stock_id"]} {item["stock_name"]}' == query]
                if len(matched) != 1:
                    raise ValueError("新股票請使用代號查詢；已收錄的股票可輸入完整中文名稱。")
                query = matched[0]["stock_id"]
            code = validate_stock_id(query)
            with st.spinner(f"正在向 TWSE 取得 {code} 已公布交易資料並同步 SQLite…"):
                result = fetch_and_store_stock(code, days=180)
            if not result["source_rows"]:
                st.error(result.get("warning") or "TWSE 沒有回傳此代號的有效交易資料，未新增到儀表板。")
                return
            stocks = [s for s in StockDatabase().list_available_stocks() if s["stock_id"] == code]
            if not stocks:
                raise RuntimeError("資料未出現在 SQLite，請檢查資料庫設定。")
            label = f'{code} {stocks[0]["stock_name"]}'.strip()
            st.session_state["twse_lookup_id"] = code
            st.session_state["twse_lookup_days"] = 180
            st.session_state["twse_lookup_result"] = result
            st.session_state["home_search_notice"] = f'{label}：已從 TWSE 同步資料（新增 {result["inserted_rows"]} 筆）'
            navigate("個股儀表板", label)
            st.rerun()
        except (ValueError, RuntimeError, OSError) as exc:
            st.error(f"股票查詢失敗：{exc}")
'''
    homepage = replace_one(homepage, old_callback, new_callback, "home.py")
    old_search = '''            with st.container(key="market_search"):
                st.selectbox(
                    "搜尋股票", stock_labels, index=None, key="home_stock",
                    placeholder="搜尋股票名稱或代號，例如台泥、1101",
                    label_visibility="collapsed", on_change=open_selected_stock,
                )
'''
    new_search = '''            with st.container(key="market_search"):
                with st.form("market_home_twse_form", border=False):
                    entered = st.text_input(
                        "搜尋股票代號", key="home_twse_search",
                        placeholder="輸入股票代號，例如 2330、2327、009816",
                        label_visibility="collapsed", max_chars=20,
                    )
                    submitted = st.form_submit_button("查詢 TWSE 並加入儀表板", use_container_width=True)
                if submitted:
                    search_and_open_stock(entered)
'''
    homepage = replace_one(homepage, old_search, new_search, "home.py")
    new_home = old_home[:start] + homepage + old_home[end:]

    # The old selectbox had a dark pill style. Restore that styling on text_input.
    css_marker = '/* Hero 與背景地圖 */'
    css = '''/* 首頁任意股票查詢（文字輸入，不是只能篩選既有選項的 selectbox） */
.st-key-market_search [data-testid="stTextInput"] input {{
  border-radius: 999px; min-height: 3.25rem; padding-inline: 1.3rem;
  background: #141416; color: #f5f5f5; border: 1px solid #2a2a2e;
}}
.st-key-market_search [data-testid="stTextInput"] input:focus {{
  border-color: #f2c14e; box-shadow: 0 0 0 1px #f2c14e;
}}
.st-key-market_search [data-testid="stFormSubmitButton"] button {{
  border: 1px solid #f2c14e; background: #141416; min-height: 2.7rem;
  color: #f5f5f5;
}}
'''
    new_home = replace_one(new_home, css_marker, css + css_marker, "home.py")

    # Numeric six-digit TWSE ETFs such as 009816 are also accepted; no claims
    # are made about alphanumeric ETFs or TPEx-listed products.
    new_lookup = replace_one(
        old_lookup,
        'STOCK_ID_RE = re.compile(r"[0-9]{4}")',
        'STOCK_ID_RE = re.compile(r"[0-9]{4,6}")',
        "stock_lookup.py",
    )
    new_lookup = replace_one(
        new_lookup,
        '請輸入四位數的 TWSE 上市股票代號，例如 2327 或 2330。',
        '請輸入 4 至 6 位數的 TWSE 股票或數字型 ETF 代號，例如 2327、2330、009816。',
        "stock_lookup.py",
    )
    # Existing stock lookup page also benefits from the corrected numeric length.
    new_lookup = new_lookup.replace(
        "支援任意四位數 TWSE 上市股票代號",
        "支援 4 至 6 位純數字 TWSE 上市股票或 ETF 代號",
    )
    new_lookup = new_lookup.replace(
        "請輸入四位數上市股票代號；名稱查詢僅支援已收錄且名稱完全相符的股票。",
        "請輸入 TWSE 股票／ETF 代號；名稱查詢僅支援已收錄且名稱完全相符的股票。",
    )

    for filename, value in [("home.py", new_home), ("stock_lookup.py", new_lookup)]:
        ast.parse(value, filename=filename)
    for filename, value in [(HOME, new_home), (LOOKUP, new_lookup)]:
        backup = filename.with_suffix(filename.suffix + ".bak")
        if not backup.exists():
            shutil.copy2(filename, backup)
        filename.write_text(value, encoding="utf-8")
        print(f"UPDATED {filename.name} (backup: {backup.name})")
    print("SUCCESS: homepage accepts text input; on submit fetches TWSE, writes SQLite, then navigates to dashboard.")
    print("TEST: python -m pytest tests -q; python -m pytest test_stock_lookup.py -q")


if __name__ == "__main__":
    main()
