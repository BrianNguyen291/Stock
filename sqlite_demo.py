"""Sprint 2 SQLite isolated demo. NEVER target the website's production database.

Usage:
 python sqlite_demo.py init
 python sqlite_demo.py import --source data/raw
 python sqlite_demo.py import --source data/pending
 python sqlite_demo.py status
 python sqlite_demo.py simulate-missing --stock 2327
"""
import argparse
import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_DB = Path('data/test_stock_updates.sqlite3')
COLS = ('date','stock_id','stock_name','trade_volume','trade_value','open','high','low','close','change','transaction_count','note')


def connect(db):
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('''CREATE TABLE IF NOT EXISTS daily_prices (
        stock_id TEXT NOT NULL,
        date TEXT NOT NULL,
        stock_name TEXT,
        trade_volume INTEGER,
        trade_value INTEGER,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        change REAL,
        transaction_count INTEGER,
        note TEXT,
        PRIMARY KEY(stock_id, date)
    )''')
    return conn


def parse_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as fh:
        reader=csv.DictReader(fh)
        if not set(COLS).issubset(reader.fieldnames or []):
            raise ValueError(f'{path}: 欄位不足')
        rows=[]
        for row in reader:
            d=date.fromisoformat(row['date'])
            if d > datetime.now(ZoneInfo('Asia/Taipei')).date():
                raise ValueError(f'{path}: 不接受未來日期 {d}')
            if not row['stock_id'].isdigit() or not row['close']:
                raise ValueError(f'{path}: 股票代號或收盤價無效')
            for key in ('trade_volume','trade_value','transaction_count'):
                row[key] = int(row[key]) if row[key] else None
            for key in ('open','high','low','close','change'):
                row[key] = float(row[key]) if row[key] else None
            rows.append(row)
    return rows


def ingest(conn, folder):
    paths=sorted(folder.glob('*.csv'))
    if not paths:
        raise ValueError(f'找不到 CSV：{folder}')
    before=conn.total_changes
    # Ignore duplicates by composite primary key; don't alter pre-existing verified prices.
    sql='''INSERT INTO daily_prices
    (date,stock_id,stock_name,trade_volume,trade_value,open,high,low,close,change,transaction_count,note)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(stock_id,date) DO NOTHING'''
    with conn:
        for path in paths:
            rows=parse_rows(path)
            conn.executemany(sql, ([row[k] for k in COLS] for row in rows))
    print(f'CSV 檔案：{len(paths)}，實際新增：{conn.total_changes-before} 筆')


def show(conn):
    print('stock_id | 筆數 | 最早日期 | 最新日期')
    for row in conn.execute('SELECT stock_id,COUNT(*),MIN(date),MAX(date) FROM daily_prices GROUP BY stock_id ORDER BY stock_id'):
        print(' | '.join(map(str,row)))
    dup=conn.execute('SELECT COUNT(*) FROM (SELECT stock_id,date,COUNT(*) n FROM daily_prices GROUP BY stock_id,date HAVING n>1)').fetchone()[0]
    print('重複股票+日期組合：',dup)


def main():
    p=argparse.ArgumentParser(description='隔離式 SQLite 驗證工具，不接正式資料庫')
    p.add_argument('command',choices=['init','import','status','simulate-missing'])
    p.add_argument('--db',type=Path,default=DEFAULT_DB)
    p.add_argument('--source',type=Path,default=Path('data/raw'))
    p.add_argument('--stock',default='2327')
    args=p.parse_args()
    if args.db.resolve()==Path('stocks.db').resolve():
        p.error('不要使用可能屬於網站的資料庫')
    with connect(args.db) as conn:
        if args.command=='init': print('建立測試資料庫：',args.db)
        elif args.command=='import': ingest(conn,args.source)
        elif args.command=='status': show(conn)
        elif args.command=='simulate-missing':
            row=conn.execute('SELECT date FROM daily_prices WHERE stock_id=? ORDER BY date LIMIT 1',(args.stock,)).fetchone()
            if not row: p.error('該股票尚無資料')
            with conn:
                conn.execute('DELETE FROM daily_prices WHERE stock_id=? AND date=?',(args.stock,row[0]))
            print(f'測試資料庫模擬刪除：{args.stock} {row[0]}；重新 import raw 應補回一筆')

if __name__=='__main__':
    main()
