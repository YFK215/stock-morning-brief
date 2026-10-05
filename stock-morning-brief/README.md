# 股市晨報（每日 08:00 自動產生）

每個交易日台北時間 **08:00**，自動抓取：

- 全市場收盤資料（證交所 OpenAPI）→ 依你設定的觀察名單算出「熱門族群排行」
- 隔夜美股／費城半導體指數（Yahoo Finance）
- 財經新聞標題（工商時報、經濟日報、MoneyDJ、鉅亨網 RSS）
- 法人說明會(法說會)資訊（最佳努力爬取，失敗則提供查詢連結）
- 一段摘要文字：若設定了 Gemini API 金鑰，會用 AI 總結上述資料；沒設定就用規則式句子代替，**完全免費可用**。

產生結果是一個網頁，部署在 GitHub Pages，網址會是：
`https://<你的GitHub帳號>.github.io/<repo名稱>/`

---

## 設定步驟

### 1. 建立新的 GitHub Repository

到 GitHub 右上角 `+` → `New repository`，取個名字（例如 `stock-morning-brief`），
Public 或 Private 都可以，先不要勾選任何初始檔案。

### 2. 上傳檔案

把這個資料夾（連同 `.github`、`scripts`、`docs` 等子資料夾）全部上傳：

- 進入新 repo 頁面 → `Add file` → `Upload files`
- 直接把整個解壓縮後的資料夾內容拖進去（現在 GitHub 網頁版支援拖整個資料夾進去，
  會自動保留 `.github/workflows/...` 這種路徑）
- Commit（直接按綠色按鈕送出即可）

> 如果拖資料夫沒反應，也可以分次上傳：先上傳 `.github/workflows/morning-brief.yml`，
> 再上傳 `scripts/collect.py`、`requirements.txt`、`docs/index.html`，GitHub 會自動依路徑建資料夾。

### 3.（選用，但建議）申請免費的 Gemini API 金鑰，啟用 AI 摘要

1. 前往 https://aistudio.google.com/apikey ，用 Google 帳號登入
2. 點「Create API key」，複製產生的金鑰
3. 在你的 GitHub repo 裡：`Settings` → `Secrets and variables` → `Actions` → `New repository secret`
4. Name 填 `GEMINI_API_KEY`，Value 貼上剛剛的金鑰 → `Add secret`

不想設定也沒關係，程式會自動改用規則式摘要，其他功能都正常。

### 4. 啟用 GitHub Pages

`Settings` → `Pages` → Source 選 `Deploy from a branch` →
Branch 選 `main`，資料夾選 `/docs` → `Save`

稍等 1-2 分鐘，頁面網址會出現在同一個畫面上方。

### 5. 手動測試一次

`Actions` 分頁 → 左側選「股市晨報」→ 右側 `Run workflow` → `Run workflow`

等個 1 分鐘左右，重新整理頁面確認有跑完（綠色勾勾），
再打開 Pages 網址看看報告有沒有正常產生。

之後每個交易日台北時間 08:00 就會自動跑，不用再手動按。

---

## 之後想調整的話

- **想增減熱門族群的觀察股票**：打開 `scripts/collect.py`，找到最上面的
  `SECTOR_WATCHLIST` 這個字典，自己加減股票代號即可（不用懂程式，照格式加就好）。
- **新聞來源壞掉了**：把失敗的來源網址告訴 Claude，我可以幫你換一個。
- **想换時間**：改 `.github/workflows/morning-brief.yml` 裡的 `cron` 那一行
  （記得 GitHub Actions 用的是 UTC 時間，台北時間要減 8 小時）。

## 重要提醒

- 第一次實際上線執行時，如果某個資料來源的網站格式剛好改版、或防爬蟲擋住，
  對應區塊會顯示「暫時無法取得」而不會讓整份報告掛掉——把 Actions 執行紀錄
  (log) 貼給 Claude，可以很快定位並修正。
- 這份報告僅供個人參考，不構成投資建議。
