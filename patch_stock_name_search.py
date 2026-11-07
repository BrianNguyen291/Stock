"""Apply TWSE name/partial-match suggestions to existing stock_lookup.py and home.py.

Run from D:\\Stock. Keeps a timestamped backup. Does not alter CSV, SQLite,
existing WebJob, auth or chart components. Fails without writing if anchors differ.
"""
from pathlib import Path
import ast
import re
import shutil
from datetime import datetime

M = '# NAME_SEARCH_V1'

HELPERS = '''
# NAME_SEARCH_V1
TWSE_CATALOG_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"


@st.cache_data(ttl=3600, max_entries=2, show_spinner=False)
def _twse_search_catalog() -> list[dict[str, str]]:
    """Read TWSE's latest-day listed securities catalog, not intraday quotes."""
    response = requests.get(TWSE_CATALOG_URL, timeout=(5, 20),
                            headers={"User-Agent": "StockSignal/1.0", "Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("TWSE 商品目錄格式錯誤")
    result = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("Code") or "").strip().upper()
        name = str(item.get("Name") or "").strip()
        if STOCK_ID_RE.fullmatch(code) and name:
            result[code] = {"stock_id": code, "stock_name": name}
    if not result:
        raise ValueError("TWSE 商品目錄沒有有效商品")
    return list(result.values())


def search_stock_candidates(query: str, limit: int = 12) -> list[dict[str, str]]:
    """Find code/name substrings in the current TWSE catalog and local SQLite.

    A catalog failure does not stop lookups already present in SQLite.
    A missing/expired instrument is never fabricated or silently padded.
    """
    from unicodedata import normalize
    term = normalize("NFKC", str(query)).strip().upper().replace("臺", "台")
    if not term:
        return []
    known = {}
    try:
        for row in _twse_search_catalog():
            known[row["stock_id"]] = row
    except (requests.RequestException, ValueError) as exc:
        LOGGER.warning("TWSE search catalog unavailable: %s", exc)
    for row in StockDatabase().list_available_stocks():
        known.setdefault(row["stock_id"], {
            "stock_id": row["stock_id"], "stock_name": row["stock_name"]
        })
    found = []
    for row in known.values():
        name = normalize("NFKC", row["stock_name"]).upper().replace("臺", "台")
        if term in name or term in row["stock_id"].upper():
            priority = (0 if term == row["stock_id"] or term == name else
                        1 if row["stock_id"].startswith(term) or name.startswith(term) else 2)
            found.append((priority, row["stock_id"], row))
    return [r for _, _, r in sorted(found)[:limit]]


def resolve_stock_query(query: str) -> str:
    """Return a supported stock code; ambiguous names require user selection."""
    text = str(query).strip().upper()
    if STOCK_ID_RE.fullmatch(text):
        return text
    # Allow '2327 國巨' from a suggestion.
    code = text.split(" ", 1)[0]
    if STOCK_ID_RE.fullmatch(code):
        return code
    found = search_stock_candidates(text, limit=50)
    exact = [row for row in found if row["stock_name"].upper() == text]
    if len(exact) == 1:
        return exact[0]["stock_id"]
    if len(found) == 1:
        return found[0]["stock_id"]
    if len(found) > 1:
        raise ValueError("有多檔符合的股票，請先點選下方建議項目。")
    raise ValueError("找不到相符的 TWSE 上市股票或 ETF，請確認名稱或代號。")

'''

# Use helper in both areas without adding an extra production module.

def patch_lookup(s):
    if M in s:
        raise ValueError('stock_lookup.py already patched')
    line = 'STOCK_ID_RE = re.compile(r"(?:[0-9]{4}|[0-9]{6}|[0-9]{5}[A-Z])")'
    if s.count(line)!=1: raise ValueError('stock_lookup.py stock ID pattern differs')
    s = s.replace(line, 'STOCK_ID_RE = re.compile(r"(?:[0-9]{4}|00[0-9]{3}|[0-9]{6}|[0-9]{5}[A-Z])")',1)
    anchor = 'def date_window('
    if s.count(anchor)!=1: raise ValueError('stock_lookup.py date_window anchor differs')
    s = s.replace(anchor, HELPERS + anchor,1)
    s = s.replace('''            with st.form("twse_stock_lookup_form", clear_on_submit=False, border=False):''','''            with st.container(key="twse_stock_lookup_form"):''',1)
    if s.count('submitted = st.form_submit_button(')!=1: raise ValueError('stock_lookup.py submit button differs')
    s=s.replace('submitted = st.form_submit_button(','submitted = st.button(',1)
    line = '                range_col, button_col = st.columns([1, 1.5], vertical_alignment="bottom")'
    if s.count(line)!=1: raise ValueError('stock_lookup.py search form layout differs')
    # Insert suggestions outside the search form (st.container does not batch widget changes).
    sug = '''                candidate_rows = search_stock_candidates(entered, limit=8) if entered.strip() else []
                candidate_code = None
                if candidate_rows:
                    st.caption("符合的上市股票／ETF：點選即可查詢")
                    for item in candidate_rows:
                        label = f'{item["stock_id"]}　{item["stock_name"]}'
                        if st.button(label, key=f'lookup_suggest_{item["stock_id"]}',
                                     use_container_width=True):
                            candidate_code = item["stock_id"]
'''
    s=s.replace(line,sug+line,1)
    s=s.replace('''        if submitted:
            try:
                stock_id = validate_stock_id(entered)''','''        if submitted or candidate_code:
            try:
                stock_id = resolve_stock_query(candidate_code or entered)''',1)
    if 'stock_id = resolve_stock_query(candidate_code or entered)' not in s: raise ValueError('lookup submit anchor differs')
    s=s.replace('placeholder="輸入代號，例如 2330、2603、009816、00990A"','placeholder="輸入國巨、國泰、2330、009816、00990A…"',1)
    return s


def patch_home(s):
    if M in s: raise ValueError('home.py already patched')
    old = '''        from stock_lookup import fetch_and_store_stock, validate_stock_id'''
    if s.count(old)!=1: raise ValueError('home.py import anchor differs')
    s=s.replace(old,'''        from stock_lookup import fetch_and_store_stock, resolve_stock_query''',1)
    first = s.index('            db = StockDatabase()\n            if not query.isdigit():',s.index('def search_and_open_stock'))
    last = s.index('            with st.spinner(',first)
    s=s[:first]+'''            code = resolve_stock_query(query)
'''+s[last:]
    first=s.index('                with st.form("market_home_twse_form", border=False):')
    last=s.index('                shortcuts = [',first)
    search_ui='''                from stock_lookup import search_stock_candidates
                entered = st.text_input(
                    "搜尋上市股票／ETF", key="home_twse_search",
                    placeholder="輸入股票中文名稱或代號，例如國巨、國泰、009816",
                    label_visibility="collapsed", max_chars=32,
                )
                candidates = search_stock_candidates(entered, limit=8) if entered.strip() else []
                if candidates:
                    for item in candidates:
                        label = f'{item["stock_id"]}　{item["stock_name"]}'
                        if st.button(label, key=f'home_suggest_{item["stock_id"]}',
                                     use_container_width=True):
                            search_and_open_stock(item["stock_id"])
                if st.button("查詢 TWSE 並加入儀表板", key="market_home_twse_go",
                             use_container_width=True):
                    search_and_open_stock(entered)
'''
    s=s[:first]+search_ui+s[last:]
    return s


def patch_database(s):
    if 'CREATE TABLE IF NOT EXISTS stock_update_runs' in s:
        raise ValueError('database.py stock_update_runs already present')
    anchor = '                CREATE TABLE IF NOT EXISTS update_logs ('
    if s.count(anchor) != 1: raise ValueError('database.py schema anchor differs')
    schema = '''                CREATE TABLE IF NOT EXISTS stock_update_runs (
                    id INTEGER PRIMARY KEY,
                    stock_id TEXT NOT NULL,
                    checked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    status TEXT NOT NULL,
                    written_rows INTEGER NOT NULL DEFAULT 0,
                    latest_source_date TEXT,
                    latest_database_date TEXT,
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_stock_update_runs_stock
                    ON stock_update_runs(stock_id, id);
'''
    s = s.replace(anchor, schema + anchor, 1)
    anchor = '    def list_available_stocks(self) -> list[dict]:'
    if s.count(anchor) != 1: raise ValueError('database.py method anchor differs')
    methods = '''    def record_stock_run(self, record: dict) -> None:
        """Save every WebJob result, including no changes and failures."""
        errors = record.get("errors") or []
        message = "; ".join(str(x.get("reason", "")) for x in errors if isinstance(x, dict))
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO stock_update_runs (
                    stock_id, status, written_rows, latest_source_date,
                    latest_database_date, error_message
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                record["stock_id"], record["status"], int(record.get("written_rows") or 0),
                record.get("latest_source_date"), record.get("latest_database_date"), message,
            ))

    def latest_stock_run(self, stock_id: str) -> dict | None:
        """Return latest automatic check for ticker, not a historic CSV import."""
        with self.connect() as connection:
            row = connection.execute("""
                SELECT checked_at, status, written_rows, latest_source_date,
                       latest_database_date, error_message
                FROM stock_update_runs WHERE stock_id = ? ORDER BY id DESC LIMIT 1
            """, (stock_id,)).fetchone()
        if row is None:
            return None
        keys = ("checked_at", "status", "written_rows", "latest_source_date",
                "latest_database_date", "error_message")
        return dict(zip(keys, row))

'''
    return s.replace(anchor, methods + anchor, 1)


def patch_update(s):
    anchor = '            reports.append(record)'
    if s.count(anchor) != 1: raise ValueError('update_data.py reports anchor differs')
    return s.replace(anchor, anchor + '\n            if args.write_db:\n                db.record_stock_run(record)', 1)


def patch_app(s):
    start = s.find('        update = database.latest_update(path)', s.find('def render_status('))
    end = s.find('        status_rows.append({', start)
    if start < 0 or end < 0: raise ValueError('app.py render_status logic differs')
    new = '''        run = database.latest_stock_run(path.stem)
        update = database.latest_update(path) if run is None else None
        if label in errors:
            state, detail = "失敗", errors[label]
        elif run is not None:
            state = {
                "updated": "已同步", "no_new_rows": "無新資料",
                "not_published": "尚未公布", "unverified_no_data": "來源無資料",
                "partial_failure": "部分失敗", "failed": "失敗",
            }.get(run["status"], run["status"])
            detail = run["error_message"]
            if run["status"] in ("updated", "no_new_rows"):
                successful += 1
        elif update is not None:
            state, detail = "已匯入（未驗證 WebJob）", ""
            successful += 1
        else:
            state, detail = "尚無執行紀錄", ""
        updated_at = taiwan_time(run["checked_at"] if run else
                                 update["updated_at"] if update else None)
'''
    s = s[:start] + new + s[end:]
    old = '"最後更新（台灣）": taiwan_time(update["updated_at"] if update else None),\n            "訊息": errors.get(label, ""),'
    if s.count(old) != 1: raise ValueError('app.py table status anchor differs')
    return s.replace(old, '"最後檢查（台灣）": updated_at,\n            "訊息": detail,', 1)


def main():
    home=Path('home.py'); lookup=Path('stock_lookup.py')
    database=Path('database.py'); updater=Path('update_data.py'); app=Path('app.py')
    files=[home,lookup,database,updater,app]
    if not all(p.exists() for p in files):
        raise SystemExit('請從 D:\\Stock 執行，並確認五個既有 Python 檔案存在。')
    originals={p:p.read_text(encoding='utf-8-sig') for p in files}
    try:
        revisions={home:patch_home(originals[home]),lookup:patch_lookup(originals[lookup]),
                   database:patch_database(originals[database]),
                   updater:patch_update(originals[updater]),app:patch_app(originals[app])}
        for path,content in revisions.items():ast.parse(content, filename=str(path))
    except (ValueError,SyntaxError) as e:
        raise SystemExit(f'版本不符合預期，未修改任何檔案：{e}') from e
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    for path,content in revisions.items():
        backup=path.with_name(f'{path.name}.before_name_search_{stamp}.bak')
        shutil.copy2(path,backup)
        path.write_text(content,encoding='utf-8')
        print('UPDATED',path,'backup:',backup)
    print('完成：中文股票名稱搜尋及建議；WebJob 逐檔狀態寫入 SQLite、Dashboard 顯示。')
    print('注意：部署 Azure 後，下一次 WebJob 執行才會產生新紀錄。')

if __name__=='__main__':main()
