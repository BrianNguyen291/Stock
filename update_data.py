"""Sprint 2: compare TWSE published daily rows with existing raw CSV files.

This is a STAGING adapter, not the final SQLite writer. It never changes the
existing CSV files or a production database. A DB teammate can consume the
per-stock pending CSV files after agreeing on the DB schema.

Examples:
  python update_data.py --start 2026-09-01 --end 2026-09-30
  python update_data.py --start 2026-10-01 --end 2026-10-28
"""

import argparse
import csv
import json
import logging
import random
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from crawler import FIELDS, fetch_month, month_starts, parse_date

LOG = logging.getLogger("update_data")
TAIPEI = ZoneInfo("Asia/Taipei")


def load_stock_ids(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    stocks = config.get("stocks")
    if not isinstance(stocks, list) or len(set(map(str, stocks))) < 10:
        raise ValueError("stocks.json 必須包含至少 10 檔不同股票")
    if any(not isinstance(stock, str) or not stock.isdigit() for stock in stocks):
        raise ValueError("股票代號必須是字串形式的數字")
    return list(dict.fromkeys(stocks))


def read_existing_dates(path, stock_id):
    """Only use existing local CSV for the *staging comparison*.

    IMPORTANT: before production, replace this with the database module's
    actual (stock_id, date) lookup. Missing local CSV is NOT proof of missing DB rows.
    """
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not {"date", "stock_id"}.issubset(reader.fieldnames or []):
            raise ValueError(f"既有 CSV 缺少 date / stock_id 欄位：{path}")
        existing = set()
        for row in reader:
            if row["stock_id"] == stock_id:
                existing.add(date.fromisoformat(row["date"]).isoformat())
        return existing


def write_csv_atomic(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def fetch_with_retries(session, month, stock_id, attempts, retry_seconds):
    """Retry boundedly even if no rows are returned.

    The existing crawler returns [] for both unpublished data and market
    closure, so we deliberately report 'unverified_no_data' rather than
    claiming the market was closed or an update succeeded.
    """
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            rows = fetch_month(session, month, stock_id)
            if rows:
                return rows, None, attempt
            last_error = "來源沒有回傳可驗證的交易資料（未公布、休市或來源狀態非 OK）"
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < attempts:
            LOG.warning("%s %s 第 %d/%d 次未成功：%s", stock_id, month, attempt, attempts, last_error)
            time.sleep(retry_seconds * attempt)
    return [], last_error, attempts

def update_one(session, stock_id, start, end, raw_dir, pending_dir, attempts, retry_seconds, request_delay):
    began = datetime.now(TAIPEI)
    record = {
        "stock_id": stock_id,
        "started_at": began.isoformat(),
        "finished_at": None,
        "status": "failed",
        "new_rows": 0,
        "latest_source_date": None,
        "latest_existing_date": None,
        "retry_attempts_total": 0,
        "errors": [],
        "pending_file": None,
    }
    try:
        existing = read_existing_dates(raw_dir / f"{stock_id}.csv", stock_id)
        record["latest_existing_date"] = max(existing, default=None)
        seen = {}
        month_list = list(month_starts(start, end))
        for idx, month in enumerate(month_list):
            rows, error, used = fetch_with_retries(session, month, stock_id, attempts, retry_seconds)
            record["retry_attempts_total"] += used
            if error:
                record["errors"].append({"month": month.strftime("%Y-%m"), "reason": error})
            for row in rows:
                source_date = date.fromisoformat(str(row["date"]))
                if source_date > datetime.now(TAIPEI).date():
                    record["errors"].append({"month": month.strftime("%Y-%m"), "reason": "來源包含未來日期"})
                    continue
                if row.get("stock_id") != stock_id:
                    record["errors"].append({"month": month.strftime("%Y-%m"), "reason": "股票代號不符"})
                    continue
                if row.get("close") in (None, ""):
                    record["errors"].append({"month": month.strftime("%Y-%m"), "reason": f"{source_date} 收盤價缺漏"})
                    continue
                if start <= source_date <= end:
                    seen[source_date.isoformat()] = row
            if idx + 1 < len(month_list):
                time.sleep(request_delay + random.uniform(0, 0.5))
        record["latest_source_date"] = max(seen, default=None)
        new_rows = [seen[d] for d in sorted(seen) if d not in existing]
        # A new pending CSV is always emitted, even if empty; it never alters source CSVs.
        pending = pending_dir / f"{stock_id}.csv"
        write_csv_atomic(pending, new_rows)
        record["pending_file"] = str(pending)
        record["new_rows"] = len(new_rows)
        if record["errors"]:
            record["status"] = "partial_failure" if seen else "failed"
        elif new_rows:
            record["status"] = "pending_write"
        elif seen:
            record["status"] = "no_new_rows"
        else:
            record["status"] = "unverified_no_data"
    except (OSError, ValueError, TypeError) as exc:
        record["errors"].append({"month": None, "reason": f"{type(exc).__name__}: {exc}"})
    finally:
        record["finished_at"] = datetime.now(TAIPEI).isoformat()
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description="台股每日增量更新預備作業（不寫入正式 DB）")
    parser.add_argument("--stocks-file", type=Path, default=Path("stocks.json"))
    parser.add_argument("--start", type=parse_date, default=None)
    parser.add_argument("--end", type=parse_date, default=None)
    parser.add_argument("--lookback-days", type=int, default=40)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--pending-dir", type=Path, default=Path("data/pending"))
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--retry-seconds", type=float, default=5)
    parser.add_argument("--delay", type=float, default=2)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    from datetime import timedelta
    today = datetime.now(TAIPEI).date()
    end = args.end or today
    start = args.start or end - timedelta(days=args.lookback_days)
    if start > end or end > today:
        parser.error("日期範圍必須有效，且結束日期不得超過台灣今天")
    if args.attempts < 1 or args.attempts > 5 or args.retry_seconds < 1 or args.delay < 1:
        parser.error("attempts 須為 1~5；重試間隔與股票請求間隔至少 1 秒")
    try:
        stocks = load_stock_ids(args.stocks_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    reports = []
    # Plain session: exactly `attempts` outer calls, avoiding nested automatic retries.
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0 (AcademicStockResearch/2.0)", "Accept": "application/json"})
        for idx, stock_id in enumerate(stocks):
            LOG.info("[%s/%s] 檢查股票 %s", idx + 1, len(stocks), stock_id)
            record = update_one(session, stock_id, start, end, args.raw_dir, args.pending_dir, args.attempts, args.retry_seconds, args.delay)
            reports.append(record)
            LOG.info("%s：%s，新資料 %d 筆，來源最新日期 %s", stock_id, record["status"], record["new_rows"], record["latest_source_date"])
            if idx + 1 < len(stocks):
                time.sleep(args.delay)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    run_at = datetime.now(TAIPEI).strftime("%Y%m%d_%H%M%S_%f")
    report_file = args.log_dir / f"update_{run_at}.json"
    report_file.write_text(json.dumps({"timezone": "Asia/Taipei", "start": start.isoformat(), "end": end.isoformat(), "stocks": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("執行紀錄：%s", report_file)
    if any(r["status"] in ("failed", "partial_failure", "unverified_no_data") for r in reports):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
