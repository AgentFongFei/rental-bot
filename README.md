# 租屋推薦 Telegram 機器人

每週六早上自動到 591 搜尋竹南、頭份、新竹市東區、北區的租屋（兩房整層＋獨立套房），
依條件過濾後，推薦 20 筆到你的 Telegram，並附上大門與停車位的坐向。

## 它會檢查什麼

| 條件 | 怎麼確認 |
|---|---|
| 地區、類型、月租 ≤ 25,000、電梯、可開伙、有床 | 591 搜尋條件先過濾 |
| 實際月付（含另計的管理費、車位費）≤ 25,000 | 讀物件頁的費用說明 |
| **平面車位**（機械式剔除） | 讀物件頁「車位」欄位 |
| 床、衣櫃、桌椅 | 讀物件頁設備，灰色（沒提供）的不算 |
| 標題或內文寫「空屋」 | 直接剔除 |
| 排序 | 依實際月付（租金＋管理費＋車位費）由低到高 |

條件都在 [`config.yaml`](config.yaml)，改那個檔案就好。

## 坐向怎麼來的

不採用屋主或仲介寫的坐向。改成依 591 上的「所屬社區」逐一查詢，每個社區只查一次，結果存在 `data/communities.json`：

1. **文字資料**：Claude 上網查樂居、樂屋網、591 社區、預售屋部落格等，找大門和車道出入口在哪條路。
2. **街景**：從社區四周每條路拍 Google 街景（鏡頭對著社區），Claude 判斷哪張照得到大門、哪張照得到車道斜坡，再用拍攝方向換算方位。
3. 兩者指向同一條路 → **確定**；只有其中一種 → **推估**；都沒有 → **查無資料**。

需要的金鑰（放在 GitHub Secrets）：`ANTHROPIC_API_KEY`（必要）、`GOOGLE_MAPS_API_KEY`（街景，沒有就只用文字資料）。

想先檢查某個社區：Actions → debug-dump → Run workflow，填社區名稱與鄉鎮，照片和結果會存到 `debug-dumps` 分支。

## 第一次設定

1. **建立 Telegram 機器人**：在 Telegram 搜尋 `@BotFather`，輸入 `/newbot`，照指示取名，拿到一串 token。
2. **取得你的 chat id**：先對你的機器人隨便傳一句話，再用瀏覽器打開
   `https://api.telegram.org/bot<你的token>/getUpdates`，找到 `"chat":{"id": 數字}`，那個數字就是 chat id。
3. **放進 GitHub**：repo 的 Settings → Secrets and variables → Actions → New repository secret，新增
   `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID`。token 只放這裡，不要寫進程式或貼到聊天室。
4. **試跑一次**：Actions 分頁 → weekly-rental-search → Run workflow。之後每週六 09:00 會自動跑。

## 在自己電腦上跑

如果 GitHub 的主機被 591 擋（Actions 紀錄裡看到列表 0 筆），可以改在自己電腦跑：

```bash
pip install -r requirements.txt
python -m playwright install chromium
python main.py --dry-run          # 只印出結果，不傳 Telegram
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python main.py
```

Windows 可以用「工作排程器」每週執行一次。

## 591 改版時

591 改網頁版面後，列表可能抓到 0 筆。用下面指令存一份網頁下來，比對 `rentbot/scraper.py` 裡的選擇器：

```bash
python main.py --dump "https://rent.591.com.tw/list?region=7&kind=1" debug/list.html
```

## 關於 FB 社團

Facebook 已停止提供社團 API，用帳號自動抓取違反使用條款、帳號可能被封，所以這個機器人不抓 FB。

## 注意

請低頻率、自用為主（預設每週一次、每頁間隔 3 秒），不要公開散布 591 的資料。
