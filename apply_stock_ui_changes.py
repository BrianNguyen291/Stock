"""Apply Stock Signal UX + SQLite stock-universe integration to dev-based checkout.
Run from repository root: python apply_stock_ui_changes.py
Creates .bak backups and refuses to apply if key anchors have changed.
"""
from pathlib import Path
import shutil
import ast


def replace_once(text, before, after, path):
    count = text.count(before)
    if count != 1:
        raise RuntimeError(f'{path}: expected exactly one anchor, got {count}: {before[:100]!r}')
    return text.replace(before, after, 1)


def edit(path, fn):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f'{p} not found; run inside the Stock repo on latest dev-based branch')
    before = p.read_text(encoding='utf-8')
    after = fn(before)
    ast.parse(after, filename=path)
    if after != before:
        backup = p.with_suffix(p.suffix + '.bak')
        if not backup.exists():
            shutil.copy2(p, backup)
        p.write_text(after, encoding='utf-8')
        print(f'UPDATED {path} (backup {backup})')


def fix_home(s):
    return replace_once(s,
        '(("個股儀表板", "個股"), ("查詢股票", "查詢股票"), ("條件選股", "條件選股"), ("股票比較", "股票比較"))',
        '(("個股儀表板", "個股"), ("條件選股", "條件選股"), ("股票比較", "股票比較"))', 'home.py')


def fix_database(s):
    anchor = '    def import_csv(self, path: str | Path, *, only_if_empty: bool = False) -> int:\n'
    method = '''    def list_available_stocks(self) -> list[dict]:
        """Stock options come from actual persisted daily_prices, not deployed CSVs."""
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT s.stock_id, s.stock_name, MAX(p.date) AS latest_date
                FROM stocks s JOIN daily_prices p ON p.stock_id = s.stock_id
                GROUP BY s.stock_id, s.stock_name
                ORDER BY s.stock_id
            """).fetchall()
        return [
            {"stock_id": row[0], "stock_name": row[1], "latest_date": row[2]}
            for row in rows
        ]

'''
    return replace_once(s, anchor, method + anchor, 'database.py')


def fix_app(s):
    anchor = 'STOCK_FILES = discover_stock_files()\n'
    function = '''STOCK_FILES = discover_stock_files()


def refresh_stock_files() -> None:
    """Include stocks inserted by manual TWSE lookup, even without a local CSV.

    CSV-backed entries retain their existing import path and display label.
    DB-only entries use a nonexisting path only as an ID-bearing handle; reads
    always use SQLite and never try to create a CSV.
    """
    global STOCK_FILES
    files = discover_stock_files()
    known_ids = {path.stem for path in files.values()}
    for stock in StockDatabase().list_available_stocks():
        stock_id = stock["stock_id"]
        if stock_id not in known_ids:
            label = f"{stock_id} {stock['stock_name']}".strip()
            files[label] = DATA_DIR / "data" / "raw" / f"{stock_id}.csv"
    STOCK_FILES = files
'''
    s = replace_once(s, anchor, function, 'app.py')
    s = replace_once(s, '    database.import_csv(path, only_if_empty=True)\n    return database.query_prices(Path(path).stem)',
                        '    if Path(path).is_file():\n        database.import_csv(path, only_if_empty=True)\n    return database.query_prices(Path(path).stem)', 'app.py')
    s = replace_once(s, '    database.import_csv(path, only_if_empty=True)\n    return database.query_prices(path.stem), database.latest_update(path)',
                        '    if path.is_file():\n        database.import_csv(path, only_if_empty=True)\n    return database.query_prices(path.stem), database.latest_update(path)', 'app.py')
    # Cached reads for DB-only stocks must be invalidated when the underlying DB changes.
    s = replace_once(s,
        'def load_dashboard_data(path_text: str) -> tuple[pd.DataFrame, dict | None]:',
        'def load_dashboard_data(path_text: str, db_stamp: tuple[int, int] | None = None) -> tuple[pd.DataFrame, dict | None]:', 'app.py')
    s = replace_once(s,
        '            stocks[label], _ = load_dashboard_data(str(path))',
        '            db = StockDatabase()\n            stat = db.path.stat()\n            stocks[label], _ = load_dashboard_data(str(path), (stat.st_mtime_ns, stat.st_size))', 'app.py')
    # Newly inserted stocks should also work in the 'update all' control without trying
    # to create temporary CSV files inside a possibly read-only deployment directory.
    s = replace_once(s,
        '    for label, path in STOCK_FILES.items():\n        stock_id = path.stem\n        temporary_path = None',
        '    for label, path in STOCK_FILES.items():\n        stock_id = path.stem\n        if not path.is_file():\n            try:\n                from stock_lookup import fetch_and_store_stock\n                fetch_and_store_stock(stock_id, days=90)\n                updated.append(label)\n            except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:\n                errors[label] = str(exc)\n            continue\n        temporary_path = None', 'app.py')
    s = replace_once(s, 'import re\nimport sqlite3\n', 'import re\nimport requests\nimport sqlite3\n', 'app.py')
    # Connect lookup-page "view dashboard" to route navigation.
    s = replace_once(s, '        render_stock_lookup()\n', '        render_stock_lookup(navigate=navigate)\n', 'app.py')
    s = replace_once(s,
        'def main() -> None:\n    page = st.session_state.get("active_page", "首頁")',
        'def main() -> None:\n    refresh_stock_files()\n    page = st.session_state.get("active_page", "首頁")', 'app.py')
    return s


def fix_lookup(s):
    start = s.index('def render_stock_lookup() -> None:')
    s = s[:start] + '''def render_stock_lookup(navigate=None) -> None:
    """TWSE lookup: search, persist, review, and jump to dashboard."""
    st.markdown("## 股票探索")
    st.caption("搜尋臺灣證交所上市股票的已公布日成交資料，新增資料將儲存在 SQLite，並出現在個股儀表板選單。非盤中即時報價。")

    with st.container(border=True):
        st.markdown("#### 找到股票，延伸你的觀察清單")
        with st.form("twse_stock_lookup_form", border=False):
            left, right = st.columns([3, 1], vertical_alignment="bottom")
            with left:
                stock_id_input = st.text_input(
                    "股票代號或已收錄股票名稱", placeholder="例如 2327、2330、國巨、台積電",
                    max_chars=32,
                )
            with right:
                days = st.selectbox(
                    "歷史查詢區間", [90, 180, 365], index=1,
                    format_func=lambda x: {90: "最近 90 天", 180: "最近 180 天", 365: "最近 1 年"}[x],
                )
            submitted = st.form_submit_button("搜尋並同步至資料庫", type="primary", use_container_width=True)
        st.caption("支援任意四位數 TWSE 上市股票代號；中文名稱目前可搜尋資料庫已收錄的股票。")

    if submitted:
        try:
            query = stock_id_input.strip()
            if not STOCK_ID_RE.fullmatch(query):
                # Resolve stock names already in SQLite. For new unlisted names,
                # avoid guessing a ticker and ask for the authoritative 4-digit code.
                candidates = [item for item in StockDatabase().list_available_stocks()
                              if item["stock_name"] == query]
                if len(candidates) != 1:
                    raise ValueError("請輸入四位數上市股票代號；名稱查詢僅支援已收錄且名稱完全相符的股票。")
                query = candidates[0]["stock_id"]
            stock_id = validate_stock_id(query)
            with st.spinner("正在取得 TWSE 已公布日交易資料並同步 SQLite…"):
                result = fetch_and_store_stock(stock_id, days)
            st.session_state["twse_lookup_id"] = stock_id
            st.session_state["twse_lookup_days"] = days
            st.session_state["twse_lookup_result"] = result
            cached_stock_prices.clear()
        except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
            LOGGER.warning("TWSE manual lookup failed: %s", exc)
            st.error(f"查詢失敗：{exc}")
            return

    stock_id = st.session_state.get("twse_lookup_id")
    if not stock_id:
        st.info("在上方輸入股票代號並送出查詢，就能看到價格摘要、歷史趨勢與每日交易紀錄。")
        return

    result = st.session_state.get("twse_lookup_result")
    if result and result.get("warning"):
        st.warning(result["warning"])
    elif result and submitted:
        st.success(f"資料已同步至 SQLite：TWSE 回傳 {result['source_rows']} 個交易日，新增 {result['inserted_rows']} 筆；股票將出現在個股儀表板。")

    try:
        data = read_stock_prices(stock_id, st.session_state.get("twse_lookup_days", 180))
    except (OSError, ValueError) as exc:
        st.error(f"SQLite 讀取失敗：{exc}")
        return
    if data.empty:
        st.info("此代號目前沒有已公布的 TWSE 上市個股資料，因此不會加入儀表板。")
        return

    data = data.sort_values("date").reset_index(drop=True)
    latest = data.iloc[-1]
    name = str(latest.get("stock_name") or "").strip()
    stock_label = f"{stock_id} {name}".strip()
    title_col, action_col = st.columns([4, 1], vertical_alignment="center")
    title_col.markdown(f"### {name or '上市股票'} · {stock_id}")
    if navigate is not None:
        action_col.button("前往個股儀表板", type="primary", use_container_width=True,
                          on_click=navigate, args=("個股儀表板", stock_label), key="lookup_go_dashboard")
    st.caption(f"最新交易日 {latest['date']:%Y-%m-%d} · 資料來源：TWSE · 收盤後日交易資料")

    closes = pd.to_numeric(data["close"], errors="coerce")
    change = latest.get("change")
    if pd.isna(change):
        change = None
    cols = st.columns(4)
    cols[0].metric("最新收盤價", f"{float(latest['close']):,.2f}",
                   f"{float(change):+,.2f}" if change is not None else None)
    cols[1].metric("區間最高價", f"{float(data['high'].max()):,.2f}")
    cols[2].metric("區間最低價", f"{float(data['low'].min()):,.2f}")
    cols[3].metric("最新成交股數", f"{int(latest['trade_volume']):,}")

    price_tab, volume_tab, table_tab = st.tabs(["收盤價趨勢", "成交量趨勢", "歷史交易明細"])
    with price_tab:
        st.line_chart(data.set_index("date")[["close"]].rename(columns={"close": "收盤價"}), use_container_width=True)
    with volume_tab:
        st.bar_chart(data.set_index("date")[["trade_volume"]].rename(columns={"trade_volume": "成交股數"}), use_container_width=True)
    with table_tab:
        table = export_table(data).iloc[::-1]
        st.dataframe(table, use_container_width=True, hide_index=True)
        a, b = st.columns(2)
        with a:
            st.download_button("下載 CSV", data=table.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"TWSE_{stock_id}_daily.csv", mime="text/csv", use_container_width=True)
        with b:
            st.download_button("下載 Excel", data=excel_bytes(table),
                               file_name=f"TWSE_{stock_id}_daily.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
'''
    return s


def main():
    if not Path('app.py').exists() or not Path('home.py').exists():
        raise SystemExit('Run this script inside the Stock project root, not elsewhere.')
    for path, fn in [('home.py', fix_home), ('database.py', fix_database),
                     ('stock_lookup.py', fix_lookup), ('app.py', fix_app)]:
        edit(path, fn)
    print('DONE. Next: python -m pytest tests -q && python -m pytest test_stock_lookup.py -q')


if __name__ == '__main__':
    main()
