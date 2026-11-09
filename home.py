"""Landing and account pages for the stock dashboard."""

from base64 import b64encode
from html import escape

import streamlit as st
import requests

from auth import current_identity, render_auth, render_logout
from market_map import render_market_map


# 全站共用的設計語彙：膠囊按鈕、大圓角卡片；儀表板白底，首頁與登入頁在下方改用黑底。
DESIGN_TOKENS = """
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap");
:root {
  --ink: #17181a;
  --dim: #6f7177;
  --faint: #9a9ca2;
  --surface: #f4f4f5;
  --surface-hover: #ebebed;
  --line: #e7e7e9;
  --up: #e5484d;
  --down: #12a594;
}
"""

PAGE_STYLE = f"""
<style>
{DESIGN_TOKENS}
[data-testid="stAppViewContainer"]:has(.st-key-market_shell, .st-key-account_shell) {{ background: #000; }}
[data-testid="stHeader"], [data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"] {{ display: none; }}
/* 換頁時這份樣式會留到重新執行結束，所以只套用在首頁內容上，避免影響下一頁的版面。 */
.block-container:has(.st-key-market_shell, .st-key-account_shell) {{ max-width: none; padding: 0 !important; }}
.st-key-market_shell, .st-key-account_shell {{
  --ink: #f5f5f5;
  --dim: #a3a4aa;
  --faint: #6f7177;
  --surface: #141416;
  --surface-hover: #202023;
  --line: #2a2a2e;
  --up: #ff6b73;
  --down: #3ddbb8;
  min-height: 100svh; background: #000; color: var(--ink);
}}
.st-key-market_shell > div, .st-key-account_shell > div {{ gap: 0; }}

/* 頂部導覽 */
/* 頂部導覽：三個選單水平置中 */
/* 首頁導覽列相對於整個畫面置中 */
.st-key-market_header {{
    position: relative;
}}

.st-key-market_nav {{
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    width: max-content;
    z-index: 5;
}}

.st-key-market_nav [data-testid="stHorizontalBlock"] {{
    display: flex;
    justify-content: center !important;
    align-items: center;
    gap: 1.5rem;
}}

@media (max-width: 700px) {{
    .st-key-market_nav {{
        position: static;
        transform: none;
        width: 100%;
    }}
}}
.st-key-market_header {{
  position: relative; z-index: 3; padding: .8rem clamp(1rem, 3vw, 2.5rem);
  background: #000;
}}
.brand {{ display: flex; align-items: center; gap: .6rem; color: var(--ink); }}
.brand-mark {{
  display: grid; flex: none; place-items: center; width: 2.1rem; height: 2.1rem;
  border-radius: 11px; background: var(--ink); color: #000; font-size: 1.1rem;
}}
.brand-name {{ white-space: nowrap; font-weight: 600; font-size: .95rem; letter-spacing: -.01em; }}
.st-key-market_shell button, .st-key-account_shell button {{
  color: var(--ink); border: 0; background: var(--surface); border-radius: 999px;
  font-weight: 500; box-shadow: none;
  transition: background .18s ease, transform .18s ease;
}}
.st-key-market_shell button:is(:hover, :focus-visible),
.st-key-account_shell button:is(:hover, :focus-visible) {{
  color: var(--ink); background: var(--surface-hover);
}}
.st-key-market_shell button p, .st-key-account_shell button p {{ color: inherit; }}
.st-key-market_shell button:active, .st-key-account_shell button:active {{ transform: scale(.97); }}
.st-key-market_nav button {{ background: transparent; padding-inline: .7rem; }}
.st-key-market_nav button p {{ color: var(--dim); font-size: .87rem; }}
.st-key-market_nav button:hover p {{ color: var(--ink); }}
.st-key-market_action {{ align-items: flex-end; }}
.st-key-market_shell .st-key-market_action [data-testid="stButton"] button,
.st-key-account_shell .st-key-market_action [data-testid="stButton"] button {{
  color: #000; background: var(--ink); padding-inline: 1.05rem;
}}
.st-key-market_action [data-testid="stButton"] button p {{ color: #000; }}
.st-key-market_shell .st-key-market_action [data-testid="stButton"] button:is(:hover, :focus-visible),
.st-key-account_shell .st-key-market_action [data-testid="stButton"] button:is(:hover, :focus-visible) {{
  background: #d9d9dc;
}}
.st-key-market_profile [data-testid="stPopoverButton"] {{
  width: 2.5rem; height: 2.5rem; padding: 0; border-radius: 50%;
}}
.st-key-market_profile [data-testid="stPopoverButton"] [data-testid="stMarkdownContainer"] {{
  position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%);
}}
.st-key-market_profile [data-testid="stPopoverButton"] > div > [aria-hidden="true"] {{ display: none; }}
body:has(.st-key-market_profile) [data-testid="stPopoverBody"]:has(.profile-details) {{
  --ink: #f5f5f5; --dim: #a3a4aa; --surface: #202023;
  color: var(--ink); background: #141416; border: 1px solid #2a2a2e; border-radius: 16px;
}}
[data-testid="stPopoverBody"]:has(.profile-details) button {{
  color: var(--ink); background: #202023; border: 1px solid #2a2a2e; border-radius: 999px;
}}
.profile-details {{ min-width: min(210px, 65vw); overflow-wrap: anywhere; }}
.profile-details strong {{ display: block; color: var(--ink); font-size: 1.05rem; font-weight: 500; }}
.profile-provider {{
  display: inline-block; color: var(--ink); background: var(--surface);
  padding: .22rem .6rem; border-radius: 999px; font-size: .75rem; margin-bottom: .55rem;
}}
.profile-details p {{ color: var(--dim); margin: .2rem 0 0; font-size: .87rem; }}

/* 首頁任意股票查詢（文字輸入，不是只能篩選既有選項的 selectbox） */
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
/* 中文名稱/代號搜尋建議：限制高度，避免大量結果把首頁撐開 */
.st-key-market_suggestions {{
  width: 100%; margin: .55rem 0 .35rem;
  padding: .35rem; background: #141416; border: 1px solid #35363a;
  border-radius: 16px; overflow-y: auto;
}}
.st-key-market_suggestions [data-testid="stButton"] button {{
  width: 100%; text-align: left; justify-content: flex-start;
  background: transparent; border-radius: 10px; padding: .55rem .85rem;
  min-height: 2.6rem;
}}
.st-key-market_suggestions [data-testid="stButton"] button p {{
  color: #f5f5f5; text-align: left; font-size: .92rem;
}}
.st-key-market_suggestions [data-testid="stButton"] button:hover {{
  background: #27282c;
}}
/* Hero 與背景地圖 */
.st-key-market_hero {{
  position: relative; isolation: isolate; overflow: hidden;
  min-height: 680px; justify-content: center;
  padding: 3.5rem 1.2rem 4.5rem;
}}
.st-key-market_map {{
  position: absolute; inset: 0; z-index: -1; pointer-events: none;
  -webkit-mask-image: radial-gradient(ellipse 44% 38% at 50% 50%, rgb(0 0 0 / .32) 45%, #000 100%);
  mask-image: radial-gradient(ellipse 44% 38% at 50% 50%, rgb(0 0 0 / .32) 45%, #000 100%);
}}
.st-key-market_map > div, .st-key-market_map [data-testid="stElementContainer"],
.st-key-market_map [data-testid="stIFrame"] {{ height: 100%; width: 100%; }}
.st-key-market_map iframe {{ display: block; width: 100%; height: 100% !important; border: 0; }}
.hero-copy {{ text-align: center; margin: 0 auto 1.35rem; }}
.market-chip {{
  display: inline-block; padding: .42rem .95rem; margin-bottom: 1.25rem;
  background: #000; border: 1px solid var(--line); border-radius: 999px;
  color: var(--ink); font-size: .8rem; font-weight: 500;
}}
.hero-copy h1 {{
  color: var(--ink); font-size: clamp(2.25rem, 4.6vw, 3.4rem); font-weight: 500;
  line-height: 1.15; letter-spacing: -.025em; margin: 0; padding: 0;
}}
.hero-copy p {{ color: var(--dim); font-size: clamp(.95rem, 1.6vw, 1.06rem); line-height: 1.6; margin: .9rem 0 0; }}
.hero-copy p b {{ color: var(--ink); font-weight: 400; }}
.st-key-market_search {{ width: min(100%, 560px); margin: 0 auto; }}
.st-key-market_search [data-baseweb="select"] > div,
.st-key-market_search [role="group"]:has([role="combobox"]) {{
  min-height: 3.25rem; background: #141416; border: 1px solid var(--line); border-radius: 999px;
  transition: border-color .18s ease, box-shadow .18s ease;
}}
.st-key-market_search [data-baseweb="select"] > div:is(:hover, :focus-within),
.st-key-market_search [role="group"]:has([role="combobox"]):is(:hover, :focus-within) {{
  border-color: #f2c14e;
}}
.st-key-market_search [role="group"]:has([role="combobox"])::before {{
  content: "⌕"; color: var(--dim); font-size: 1.45rem; margin: 0 .3rem 0 1rem;
}}
.st-key-market_search [role="group"]:has([role="combobox"]) button {{
  background: transparent; border-radius: 50%;
}}
.st-key-market_search [data-baseweb="select"] input,
.st-key-market_search [role="combobox"],
.st-key-market_search [data-baseweb="select"] svg,
.st-key-market_search [role="group"] svg {{ color: var(--ink); }}
.st-key-market_search [role="combobox"]::placeholder {{ color: var(--dim); }}
.st-key-market_search [data-baseweb="select"] > div > div {{ color: var(--dim); }}
body:has(.st-key-market_shell) [role="listbox"] {{ background: #141416; color: #f5f5f5; }}
body:has(.st-key-market_shell) [role="option"] {{ color: #f5f5f5; }}
body:has(.st-key-market_shell) [role="option"]:is(:hover, [data-focused]) {{ background: #202023; }}
.st-key-market_shortcuts {{ width: fit-content; max-width: 100%; margin: .95rem auto 0; }}
.st-key-market_shortcuts [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap; gap: .4rem; }}
.st-key-market_shortcuts [data-testid="stColumn"] {{ min-width: 0; width: auto; flex: 0 0 auto; }}
.st-key-market_shortcuts button {{ padding: .28rem .85rem; min-height: 2rem; white-space: nowrap; }}
.st-key-market_shortcuts button p {{ font-size: .8rem; }}

/* 行情跑馬燈 */
.quote-band {{ padding: .1rem 0 2rem; }}
.quote-window {{
  overflow: hidden;
  -webkit-mask-image: linear-gradient(90deg, transparent, black 6%, black 94%, transparent);
  mask-image: linear-gradient(90deg, transparent, black 6%, black 94%, transparent);
}}
.quote-track {{ display: flex; width: max-content; animation: ticker-flow 36s linear infinite; }}
.quote-window:hover .quote-track {{ animation-play-state: paused; }}
.quote-group {{ display: flex; gap: .5rem; padding-right: .5rem; }}
.quote {{
  display: flex; align-items: baseline; gap: .6rem; padding: .5rem .95rem;
  background: var(--surface); border-radius: 999px;
  color: var(--ink); font-size: .84rem; font-weight: 500; white-space: nowrap;
  font-variant-numeric: tabular-nums;
}}
.quote .quote-label {{ color: var(--dim); font-weight: 400; }}
.up {{ color: var(--up); }}
.down {{ color: var(--down); }}
.flat {{ color: var(--dim); }}
@keyframes ticker-flow {{ to {{ transform: translateX(-50%); }} }}

/* 個股卡片 */
.st-key-market_cards {{ padding: 0 clamp(1rem, 3vw, 2.5rem) 3.5rem; }}
.section-title {{ margin: 0 0 1rem .15rem; }}
.section-title h2 {{ color: var(--ink); font-size: 1.5rem; font-weight: 500; letter-spacing: -.02em; padding: 0; margin: 0; }}
.section-title p {{ color: var(--dim); font-size: .9rem; margin: .25rem 0 0; }}
.st-key-market_cards [data-testid="stHorizontalBlock"] {{ gap: .75rem; }}
[class*="st-key-market_card_"] {{
  background: var(--surface); border-radius: 24px; padding: 1.1rem; gap: .7rem;
  transition: background .2s ease;
}}
[class*="st-key-market_card_"]:hover {{ background: var(--surface-hover); }}
.stock-card {{ background: #000; border-radius: 16px; padding: 1rem 1.1rem .8rem; }}
.stock-card-head {{ display: flex; justify-content: space-between; gap: .5rem; color: var(--dim); font-size: .84rem; }}
.stock-card-price {{
  display: flex; align-items: baseline; gap: .55rem; margin-top: .35rem; color: var(--ink);
  font-size: 1.7rem; font-weight: 500; letter-spacing: -.02em; font-variant-numeric: tabular-nums;
}}
.stock-card-price span {{ font-size: .88rem; font-weight: 500; letter-spacing: 0; }}
.stock-spark {{ height: 56px; margin-top: .6rem; background: center / 100% 100% no-repeat; }}
[class*="st-key-market_card_"] button {{ width: 100%; background: transparent; }}
[class*="st-key-market_card_"] button:is(:hover, :focus-visible) {{ background: #000; }}

/* 登入面板 */
.st-key-account_panel {{
  width: min(440px, calc(100% - 2rem)); margin: clamp(2rem, 8vh, 5rem) auto;
  padding: clamp(1.35rem, 4vw, 2rem); background: var(--surface); border-radius: 24px; gap: .85rem;
}}
.account-copy h1 {{ color: var(--ink); padding: 0; margin: 0; font-size: 2rem; font-weight: 500; letter-spacing: -.02em; }}
.account-copy p, .st-key-account_panel [data-testid="stCaptionContainer"] {{ color: var(--dim); }}
.account-copy p {{ margin: .35rem 0 0; }}
.st-key-account_panel [data-testid="stForm"] {{ background: #000; border: 0; border-radius: 16px; }}
.st-key-account_panel label {{ color: var(--ink); }}
.st-key-account_panel [data-baseweb="input"],
.st-key-account_panel [data-baseweb="base-input"],
.st-key-account_panel [data-testid="stTextInputRootElement"] {{
  background: #141416; border-color: var(--line); color: var(--ink);
}}
.st-key-account_panel input {{ color: var(--ink); caret-color: var(--ink); }}
.st-key-account_panel:not(:has(.profile-details)) :is(
  [data-testid="stButton"], [data-testid="stFormSubmitButton"]
) button {{ color: #000; background: var(--ink); }}
.st-key-account_panel:not(:has(.profile-details)) :is(
  [data-testid="stButton"], [data-testid="stFormSubmitButton"]
) button p {{ color: #000; }}
.st-key-account_panel:not(:has(.profile-details)) :is(
  [data-testid="stButton"], [data-testid="stFormSubmitButton"]
) button:is(:hover, :focus-visible) {{ background: #d9d9dc; }}
.st-key-account_panel [data-testid="stTextInput"] button {{ background: transparent; }}

@media (max-width: 640px) {{
  .st-key-market_header [data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap; justify-content: space-between; }}
  .st-key-market_header [data-testid="stColumn"] {{ min-width: 0; width: auto; flex: 0 0 auto; }}
  .st-key-market_header [data-testid="stColumn"]:nth-child(2) {{ display: none; }}
  .st-key-market_action button {{ white-space: nowrap; }}
  .st-key-market_hero {{ min-height: 600px; }}
  .st-key-market_map {{
    -webkit-mask-image: radial-gradient(ellipse 80% 34% at 50% 50%, rgb(0 0 0 / .14) 50%, #000 100%);
    mask-image: radial-gradient(ellipse 80% 34% at 50% 50%, rgb(0 0 0 / .14) 50%, #000 100%);
  }}
}}
@media (prefers-reduced-motion: reduce) {{
  .quote-track {{ animation: none; }}
  .quote-window {{ overflow-x: auto; -webkit-mask-image: none; mask-image: none; }}
  .quote-group[aria-hidden="true"] {{ display: none; }}
}}
</style>
"""


def _direction(change):
    return "up" if change > 0 else "down" if change < 0 else "flat"


def _profile_details(identity):
    local_test = identity.provider == "local_test"
    provider = "測試帳號" if local_test else "Google 帳號"
    name = escape(identity.display_name or provider)
    identifier = escape(identity.subject if local_test else identity.email or "")
    st.html(
        f'<div class="profile-details"><span class="profile-provider">{provider}</span>'
        f'<strong>{name}</strong><p>{identifier}</p></div>'
    )
    render_logout()


def _header(navigate, *, back=False):
    with st.container(key="market_header"):
        brand, links, action = st.columns([1, 2, 1], vertical_alignment="center")
        brand.html("""
        <div class="brand">
          <span class="brand-mark" aria-hidden="true">⌁</span>
          <div class="brand-name">Stock Signal</div>
        </div>
        """)
        if not back:
            with links.container(key="market_nav", horizontal=True):
                for page, label in (("個股儀表板", "個股"), ("條件選股", "條件選股"), ("股票比較", "股票比較")):
                    st.button(label, on_click=navigate, args=(page,), key=f"market_nav_{page}")
        with action.container(key="market_action"):
            if back:
                st.button("← 返回首頁", on_click=navigate, args=("首頁",))
            elif identity := current_identity():
                with st.container(key="market_profile"):
                    with st.popover("個人資訊", icon=":material/person:"):
                        _profile_details(identity)
            else:
                st.button("登入", on_click=navigate, args=("登入",))


def _render_ticker(quotes):
    if not quotes:
        return
    items = []
    for label, close, change, trade_date, _ in quotes:
        items.append(
            f'<div class="quote" title="{trade_date:%Y-%m-%d}">'
            f'<span class="quote-label">{escape(label)}</span><span>{close:,.2f}</span>'
            f'<span class="{_direction(change)}">{change:+.2f}%</span></div>'
        )
    cards = "".join(items)
    st.html(
        '<div class="quote-band"><div class="quote-window"><div class="quote-track">'
        f'<div class="quote-group">{cards}</div>'
        f'<div class="quote-group" aria-hidden="true">{cards}</div>'
        '</div></div></div>'
    )


def _sparkline(closes, color):
    if len(closes) < 2:
        return ""
    low, high = min(closes), max(closes)
    spread = (high - low) or 1
    step = 100 / (len(closes) - 1)
    points = " ".join(
        f"{index * step:.2f},{36 - (value - low) / spread * 32:.2f}" for index, value in enumerate(closes)
    )
    # st.html 會濾掉內嵌 <svg>，所以改成 CSS 背景圖。
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40" preserveAspectRatio="none">'
        f'<polygon points="0,40 {points} 100,40" fill="{color}" opacity=".08"/>'
        f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.6" '
        'vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>'
    )
    source = b64encode(svg.encode()).decode()
    return f'<div class="stock-spark" style="background-image:url(data:image/svg+xml;base64,{source})"></div>'


def _render_cards(quotes, navigate):
    if not quotes:
        return
    colors = {"up": "#ff6b73", "down": "#3ddbb8", "flat": "#a3a4aa"}
    with st.container(key="market_cards"):
        st.html(
            '<div class="section-title"><h2>市場焦點</h2>'
            '<p>近 60 個交易日走勢與最近交易日收盤。</p></div>'
        )
        columns = st.columns(3)
        for index, (label, close, change, trade_date, closes) in enumerate(quotes):
            code, _, name = label.partition(" ")
            direction = _direction(change)
            with columns[index % 3].container(key=f"market_card_{code}"):
                st.html(
                    '<div class="stock-card"><div class="stock-card-head">'
                    f'<span>{escape(name or code)} · {escape(code)}</span>'
                    f'<span>{trade_date:%m/%d}</span></div>'
                    f'<div class="stock-card-price">{close:,.2f}'
                    f'<span class="{direction}">{change:+.2f}%</span></div>'
                    f'{_sparkline(closes, colors[direction])}</div>'
                )
                st.button("查看個股 →", on_click=navigate, args=("個股儀表板", label), key=f"market_open_{code}")


def render_home(stock_labels, quotes, navigate):
    """Stable homepage stock search with Chinese-name suggestions."""
    from database import StockDatabase
    from stock_lookup import perform_stock_search, search_stock_candidates, resolve_exact_stock_query

    def queue_search(query=None):
        query = str(query if query is not None else st.session_state.get("home_twse_search", "")).strip()
        if not query:
            st.session_state["home_search_error"] = "請輸入股票中文名稱或代號。"
            return
        st.session_state["home_pending_search"] = query
        st.session_state["home_suggestions_hidden_for"] = str(
            st.session_state.get("home_twse_search", "")
        ).strip()

    def queue_exact_search():
        """Enter/blur: auto-submit only a known exact code or full name."""
        query = str(st.session_state.get("home_twse_search", "")).strip()
        if resolve_exact_stock_query(query):
            queue_search(query)

    # Use the SAME real TWSE query + SQLite auto-enrollment as dashboard searches.
    pending = st.session_state.pop("home_pending_search", None)
    if pending:
        perform_stock_search(pending, navigate, days=180,
                             error_key="home_search_error",
                             available_stock_labels=stock_labels)

    st.html(PAGE_STYLE)
    with st.container(key="market_shell"):
        _header(navigate)
        with st.container(key="market_hero"):
            with st.container(key="market_map"):
                render_market_map()
            chip = f"{len(stock_labels)} 檔台股"
            if quotes:
                chip += f" · 資料至 {max(quote[3] for quote in quotes):%Y-%m-%d}"
            st.html(f"""
            <div class="hero-copy">
              <span class="market-chip">{chip}</span>
              <h1>市場脈動，一搜即達</h1>
              <p>輸入股票名稱或代號，探索<b>個股行情</b>與<b>技術指標</b>。</p>
            </div>
            """)
            with st.container(key="market_search"):
                entered = st.text_input(
                    "搜尋上市股票／ETF",
                    key="home_twse_search",
                    placeholder="輸入中文名稱或代號，例如國巨、國泰、009816",
                    label_visibility="collapsed",
                    max_chars=32,
                    on_change=queue_exact_search,
                )
                typed = entered.strip()
                hidden_for = st.session_state.get("home_suggestions_hidden_for")
                if hidden_for and typed != hidden_for:
                    st.session_state.pop("home_suggestions_hidden_for", None)
                if (typed and not resolve_exact_stock_query(typed)
                    and st.session_state.get("home_suggestions_hidden_for") != typed):
                    try:
                        candidates = search_stock_candidates(typed, limit=8)
                    except (ValueError, OSError, RuntimeError, requests.RequestException):
                        candidates = []
                        st.caption("暫時無法載入相關搜尋，仍可輸入股票代號查詢。")
                    if candidates:
                        # No st.empty() or on-the-fly container clearing.
                        with st.container(key="market_suggestions", height=265, border=False):
                            st.caption("相關股票／ETF · 點選後直接查詢")
                            for item in candidates:
                                code = item["stock_id"]
                                label = f'{code}　{item["stock_name"]}'
                                st.button(
                                    label,
                                    key=f"home_suggest_{code}",
                                    use_container_width=True,
                                    on_click=queue_search,
                                    args=(code,),
                                )
                st.button(
                    "查詢 TWSE 並加入儀表板",
                    key="market_home_twse_go",
                    use_container_width=True,
                    on_click=queue_search,
                )
                error = st.session_state.pop("home_search_error", None)
                if error:
                    st.error(error)
                shortcuts = [
                    label for code in ("1101", "2330", "2317")
                    for label in stock_labels if label.startswith(f"{code} ")
                ]
                if shortcuts:
                    with st.container(key="market_shortcuts"):
                        for column, label in zip(st.columns(len(shortcuts)), shortcuts):
                            column.button(label, on_click=navigate, args=("個股儀表板", label))
        _render_ticker(quotes)
        _render_cards(quotes, navigate)


def render_login(navigate):
    st.html(PAGE_STYLE)
    with st.container(key="account_shell"):
        _header(navigate, back=True)
        with st.container(key="account_panel"):
            if identity := current_identity():
                st.html('<div class="account-copy"><h1>個人資訊</h1></div>')
                _profile_details(identity)
                return
            st.html("""
            <div class="account-copy">
              <h1>登入</h1>
              <p>登入後可使用個人股票資料功能。</p>
            </div>
            """)
            render_auth(on_success=lambda: navigate("首頁"))
