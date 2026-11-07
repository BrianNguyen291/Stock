台股儀表板新增「查詢股票」功能包（不變更原本固定10檔自動更新）

1. 將 stock_lookup.py 放到 D:\Stock\（與 app.py 同資料夾）。
2. 依照 app_changes.md 修改 app.py、requirements.txt、deploy-azure-test.yml。
3. 在本機執行：
     python -m pip install -r requirements.txt
     python -m pytest tests -v
     python -m pytest test_stock_lookup.py -v
     streamlit run app.py
4. 在 sidebar 開啟「查詢股票」，輸入 2330，選 90 天，按查詢。
5. 驗證 SQLite 新資料、CSV/Excel 下載、資料日期及不重複。
6. 通過後在同一個 feature 分支 Commit & Push，建立 PR 合併到 dev。
7. Azure 上同步測試：資料庫路徑必須和 WebJob 一致（見 app_changes.md）。

提醒：此功能是 TWSE 盤後資料，不是交易中即時報價；只適用台灣證交所上市個股，不含上櫃 TPEx。
