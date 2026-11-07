# 修改 dev/app.py（3 個地方）

本功能只處理 TWSE 上市股票（目前支援 4 位數代號）；原本 10 檔股票的 Azure WebJob 完全不變。

**1. import 區域新增**：

```python
from stock_lookup import render_stock_lookup
```

**2. 將 `load_dashboard_data` 的快取裝飾器改成有時效的快取**：

原本：
```python
@st.cache_data(show_spinner=False)
```
改為：
```python
@st.cache_data(ttl=60, max_entries=128, show_spinner=False)
```
這能讓已存在的儀表板資料在 WebJob 更新後自動刷新（最長約 60 秒）；保留原本手動更新後的 `.clear()`。

**3. `main()` 原本功能選單**：

將：
```python
pages = ["個股儀表板", "條件選股", "股票比較"]
```
改為：
```python
pages = ["個股儀表板", "查詢股票", "條件選股", "股票比較"]
```

將原本：
```python
if page == "股票比較":
    render_comparison(STOCK_FILES, read_stock_data)
elif page == "條件選股":
    render_stock_picker(stocks)
else:
    render_single_stock(stocks)
```
改為：
```python
if page == "查詢股票":
    render_stock_lookup()
elif page == "股票比較":
    render_comparison(STOCK_FILES, read_stock_data)
elif page == "條件選股":
    render_stock_picker(stocks)
else:
    render_single_stock(stocks)
```

**4. `requirements.txt` 新增一行**：

```text
openpyxl>=3.1,<4
```

**5. `.github/workflows/deploy-azure-test.yml` 打包清單：**

找到 `Copy-Item app.py, charts.py, comparison.py, crawler.py, data_processor.py, database.py, serve.py, web.config, update_data.py, stocks.json dist\`，在列表加入 `stock_lookup.py`。

**6. Azure `test` slot 的 SQLite 路徑一致性（很重要）**：

`database.py` 的預設路徑依 `WEBSITE_SLOT_NAME` 決定，但先前 WebJob 日誌證實這個環境變數不一定存在。若網站因此使用 `production.sqlite3`，就無法看見 WebJob 寫入的 `test.sqlite3`。

在現有的 `Configure stock WebJob on test slot`（或原本設定 `--slot-settings WEBJOBS_DISABLE_SCHEDULE=1 STOCK_WEBJOB_ALLOWED_SLOT=test`）的步驟，將設定行改為：

```powershell
--slot-settings WEBJOBS_DISABLE_SCHEDULE=1 STOCK_WEBJOB_ALLOWED_SLOT=test 'STOCK_DB_PATH=C:\home\data\stock\test.sqlite3' `
```

此設定僅影響 `test` slot，需 Azure 管理者同意，並在部署後驗證 Dashboard 與 WebJob 使用同一資料庫。**正式站台不要套用此路徑**。

**注意**：TWSE `STOCK_DAY` 提供盤後日交易資料，非交易時段內的逐筆行情；非 4 位數代號、上櫃股票、無資料代號會收到訊息。查詢時間最多一年、每次跨月請求間隔至少 1 秒，避免大量擷取。
