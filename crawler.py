"""臺灣證券交易所個股歷史日交易資料爬蟲（預設國巨 2327）。

使用範例：
    python crawler.py --start 2026-09-01 --end 2026-09-30
    python crawler.py --start 2026-01-01 --end 2026-10-21
    python crawler.py --stock 2492 --start 2026-01-01 --end 2026-10-21 --output 2492.csv
"""

import argparse
import csv
import logging
import random
import time
from datetime import date, datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
DEFAULT_STOCK_ID = "2327"
FIELDS = [
    "date", "stock_id", "stock_name", "trade_volume", "trade_value",
    "open", "high", "low", "close", "change", "transaction_count", "note",
]
LOGGER = logging.getLogger(__name__)


def parse_date(text):
    return datetime.strptime(text, "%Y-%m-%d").date()


def month_starts(start, end):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield date(year, month, 1)
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def roc_to_iso(value):
    parts = str(value).strip().split("/")
    if len(parts) != 3:
        raise ValueError(f"非預期民國日期格式：{value!r}")
    year, month, day = map(int, parts)
    return date(year + 1911, month, day).isoformat()


def parse_number(value, integer=False):
    cleaned = str(value).strip().replace(",", "").replace("+", "")
    # 除權息等「不比價」交易日，漲跌價差會以 X 開頭（例如 X0.00），無實際漲跌意義。
    if cleaned in ("", "--", "---", "N/A") or cleaned[0] in ("X", "x"):
        return ""
    number = float(cleaned)
    return int(number) if integer else number


def parse_stock_name(title, stock_id):
    # title 範例："115年10月 2327 國巨           各日成交資訊"
    parts = str(title or "").split()
    if stock_id in parts:
        index = parts.index(stock_id)
        if index + 1 < len(parts) and parts[index + 1] != "各日成交資訊":
            # 部分月份名稱後會帶 * 註記，例如「國巨*」
            return parts[index + 1].rstrip("*")
    return ""


def create_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; AcademicStockResearch/1.0)",
        "Accept": "application/json",
    })
    return session


def fetch_month(session, month, stock_id):
    params = {
        "date": month.strftime("%Y%m%d"),
        "stockNo": stock_id,
        "response": "json",
    }
    response = session.get(API_URL, params=params, timeout=(10, 30))
    response.raise_for_status()
    payload = response.json()
    status = str(payload.get("stat", ""))
    if status != "OK":
        LOGGER.warning("%s：證交所回應 %s", month.strftime("%Y-%m"), status)
        return []
    fields = payload.get("fields")
    raw_rows = payload.get("data")
    if not isinstance(fields, list) or not isinstance(raw_rows, list):
        raise ValueError("證交所資料格式錯誤：缺少 fields 或 data")
    required = ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數"]
    missing = [name for name in required if name not in fields]
    if missing:
        raise ValueError(f"證交所欄位變更，缺少：{missing}")
    idx = {name: fields.index(name) for name in required}
    note_idx = fields.index("註記") if "註記" in fields else None
    stock_name = parse_stock_name(payload.get("title"), stock_id)
    result = []
    for raw in raw_rows:
        if len(raw) < len(required):
            LOGGER.warning("跳過不完整資料列：%s", raw)
            continue
        try:
            row = {
                "date": roc_to_iso(raw[idx["日期"]]),
                "stock_id": stock_id,
                "stock_name": stock_name,
                "trade_volume": parse_number(raw[idx["成交股數"]], integer=True),
                "trade_value": parse_number(raw[idx["成交金額"]], integer=True),
                "open": parse_number(raw[idx["開盤價"]]),
                "high": parse_number(raw[idx["最高價"]]),
                "low": parse_number(raw[idx["最低價"]]),
                "close": parse_number(raw[idx["收盤價"]]),
                "change": parse_number(raw[idx["漲跌價差"]]),
                "transaction_count": parse_number(raw[idx["成交筆數"]], integer=True),
                "note": str(raw[note_idx]).strip() if note_idx is not None and note_idx < len(raw) else "",
            }
            result.append(row)
        except (ValueError, IndexError) as exc:
            LOGGER.warning("跳過無法解析的資料列 %s：%s", raw, exc)
    return result


def crawl(start, end, output, delay, stock_id=DEFAULT_STOCK_ID):
    if not stock_id.isdigit():
        raise ValueError(f"股票代號格式錯誤：{stock_id!r}")
    if start > end:
        raise ValueError("起始日期不得晚於結束日期")
    if end > date.today():
        raise ValueError("結束日期不可晚於今天")
    if delay < 1:
        raise ValueError("為降低伺服器負擔，請求間隔至少 1 秒")
    results = {}
    failures = []
    with create_session() as session:
        months = list(month_starts(start, end))
        for index, month in enumerate(months, 1):
            LOGGER.info("[%d/%d] 取得 %s %s 資料", index, len(months), stock_id, month.strftime("%Y-%m"))
            try:
                for row in fetch_month(session, month, stock_id):
                    if start.isoformat() <= row["date"] <= end.isoformat():
                        results[row["date"]] = row
            except (requests.RequestException, ValueError) as exc:
                LOGGER.error("%s 取得失敗：%s", month.strftime("%Y-%m"), exc)
                failures.append(month.strftime("%Y-%m"))
            if index != len(months):
                time.sleep(delay + random.uniform(0, 0.7))
    rows = [results[key] for key in sorted(results)]
    if not rows:
        raise RuntimeError("沒有取得任何資料，請檢查網路、查詢區間與證交所回應")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    LOGGER.info("完成！共 %d 筆，已儲存至 %s", len(rows), output.resolve())
    if failures:
        LOGGER.warning("以下月份抓取失敗，需要重新執行檢查：%s", ", ".join(failures))
    return rows, failures


def main():
    parser = argparse.ArgumentParser(description="臺灣證券交易所個股歷史資料爬蟲")
    parser.add_argument("--stock", default=DEFAULT_STOCK_ID, help="股票代號，例如 2327、2492")
    parser.add_argument("--start", type=parse_date, default=date(2026, 9, 1), help="開始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, default=date(2026, 9, 30), help="結束日期 YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=None, help="CSV 輸出路徑，預設 data/raw/<股票代號>.csv")
    parser.add_argument("--delay", type=float, default=2.0, help="每月請求之間的間隔秒數，至少 1 秒")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    output = args.output or Path("data/raw") / f"{args.stock}.csv"
    try:
        _, failures = crawl(args.start, args.end, output, args.delay, args.stock)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
