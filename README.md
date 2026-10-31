# Stock

台股（TWSE / TPEx）爬蟲專案。

## 啟動儀表板

安裝相依套件：

```powershell
python -m pip install -r requirements.txt
```

啟動 Streamlit：

```powershell
streamlit run app.py
```

儀表板提供股票選擇、日期範圍篩選、最新股價資訊卡片、收盤價與成交量圖表，以及交易資料表。

## SQLite 與技術指標（#9）

儀表板改從 SQLite 查詢。首次讀取某支股票時會匯入隨附 CSV，之後不會用舊 CSV 覆蓋資料庫中的更新。若要主動新增或更新 CSV 資料，可執行：

```powershell
python database.py --import-csv 2327.csv 2492.csv
```

本機預設儲存在 `data/stock.sqlite3`。設定環境變數 `STOCK_DB_PATH`（絕對路徑），或在匯入時使用 `--db`，可指定其他位置。資料庫及其 journal 不應加入版控或部署包。

```python
from database import StockDatabase

db = StockDatabase()
db.import_csv("2327.csv")
data = db.query_prices("2327", "2026-03-01", "2026-04-30")
# 含起訖日；indicators=False 可只取得原始交易資料。
# db.upsert_prices(frame, source="crawler") 可原子新增／更新一批資料。
```

資料表為 `stocks`（股票名稱）、`daily_prices`（股票代號＋日期複合主鍵）及 `update_logs`（成功批次的 UTC 時間、來源、輸入筆數）。同股票同日期重複匯入會更新；不同股票同日期各自保留。每批先驗證股票代號、日期、有限數值、非負整數成交量及開高低收範圍，任何一列無效便拒絕整批；寫入或成功紀錄失敗也會回滾整批。

`ma_5`、`ma_20`、`ma_60` 分別是最近 5、20、60 個交易日收盤價的算術平均；`volume_ma_20` 是最近 20 個交易日成交股數平均。保留供既有圖表使用的 `volume_ma_5`，也採完整 5 日視窗。未滿視窗時為缺值。查詢日期區間時先額外讀取最多 59 個前期交易日，計算後再裁切，以免區間首日的均線失真。多股票資料分開計算，不共用視窗。

## Azure 持久儲存與驗收

Azure App Service 的 `%HOME%` 是持久儲存，程式預設使用 `%HOME%/data/stock/<slot>.sqlite3`（主站為 `production`、測試 slot 為 `test`），位於部署用 `site/wwwroot` 外。依據 [Microsoft App Service 檔案系統說明](https://learn.microsoft.com/en-us/azure/app-service/operating-system-functionality#file-access)，應以 `HOME` 環境變數定位，不假設磁碟機代號。兩個部署流程均會打包 `database.py`。

若使用 `STOCK_DB_PATH` 自訂路徑，請在 Azure 各環境分別設定不同的絕對持久路徑，並勾選 Deployment slot setting，避免 slot swap 後交換資料庫設定，參見 [Microsoft 部署位置設定](https://learn.microsoft.com/en-us/azure/app-service/deploy-staging-slots)。SQLite 使用 rollback journal 和 30 秒鎖定等待；此版本適用單一 App Service 執行個體。

本機驗證：

```powershell
python -m pip install pytest
python -m pytest tests -q
```

測試使用暫存資料庫，涵蓋兩份 CSV 全列比對、重複匯入、不同股票同日資料、含起訖日查詢、可手算指標、前期視窗、無效資料拒絕及寫入失敗回滾，並以新 Python 行程與替換部署目錄模擬資料保留。

Azure 實測可在 GitHub Actions 的「Deploy Stock to Azure test slot」選擇 Run workflow、分支 `dev`，並勾選 `verify_persistence` 執行。流程會使用既有 Azure 登入，透過 [Kudu command API](https://github.com/projectkudu/kudu/wiki/REST-API#command) 在 test slot 寫入獨立的隨機測試股票資料，保存所有歷史價格與股票名稱的 SHA-256，重啟後比對、重新部署後再比對，並確認正式資料庫沒有測試紀錄。正式環境僅讀取，不建立資料庫或寫入資料。

驗收成功後自動清除該次測試股票及更新紀錄；失敗則保留供追查。驗收證據保存在該次 Actions 的 `sqlite-persistence-<run_id>` artifact。預設 push 部署不執行這項驗收。沒有成功的實際執行紀錄前，本機模擬不代表 Azure 驗收完成。

若需手動核對：

1. 在 test slot 確認實際資料庫路徑位於 `%HOME%/data/stock/`，載入兩支股票，記下筆數、日期範圍及一筆歷史 OHLC／成交量。
2. 透過 `upsert_prices` 在測試庫加入一筆 CSV 沒有的測試紀錄，記下內容，確保後續驗證不會被自動匯入掩蓋。
3. 重新啟動 test slot，確認原始資料與新增測試紀錄均保留。
4. 重新部署 test slot，再確認筆數及上述紀錄不變。
5. 確認 production 的資料庫路徑與 test 不同，正式庫沒有測試紀錄。通過後才勾選 #9 的 Azure 重啟／重新部署驗收條件。
