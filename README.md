# 逐字稿抽取台 · Transcript Deck

從 YouTube 影片抽逐字稿，切成可讀段落，匯出 `.md`/`.txt`/`.srt`。
**沒有字幕時，會用 Whisper 語音辨識把逐字稿「聽」出來。**

**🌐 線上版（部署後）：https://xin7355-collab.github.io/tool/**

---

## 這個 repo 怎麼用

- 前端：`index.html`（部署到 GitHub Pages，含「雲端產出的逐字稿」面板）。
- 產線：在 `queue.txt` 一行貼一個 YouTube 網址/ID，commit 後 `pipeline.yml` 會自動抽取
  （有字幕→抓字幕；沒字幕→Whisper 語音辨識），把結果發佈到 `gh-pages/transcripts/`，前端就看得到。
- **`YT_COOKIES`**：GitHub 機房 IP 常被 YouTube 要求「登入確認不是機器人」，擋住之後
  連字幕清單都拿不到。設了這個就能通過驗證、恢復全自動；鎖區／會員影片也靠它。
  取得方式見 [如何取得cookies.md](如何取得cookies.md)。沒有的話改用前端的
  「🚀 用手機補抓」（手機是住宅 IP，不會被擋）。
  格式錯的話程式會印警告並略過，不會拖垮整批。
- 讀文件：前端「📑 文件」分頁上傳 PDF 或圖片，寫進 `doc-inbox/`，`docs.yml` 讀成文字。
  三層由準到不準：**PDF 內建文字層**（電腦產生的 PDF 都有，零錯字、不經辨識）→
  **Groq 視覺模型**（掃描件、照片；繁中最準，表格會轉成 Markdown）→
  **Tesseract**（視覺模型被下架或額度滿時的後路）。讀完就跟逐字稿放在一起。
- 雲端硬碟：「📡 追蹤」分頁貼一個 Google 雲端硬碟資料夾網址，`gdrive.yml` 每小時去看有沒有新檔案，
  下載後丟進 `audio-inbox/`（影音）或 `doc-inbox/`（文件），剩下交給既有的工作流。
  用服務帳號讀取——資料夾維持私人，只分享給一個機器人信箱，金鑰不會過期。
  設定見 [如何接Google雲端硬碟.md](如何接Google雲端硬碟.md)（`GDRIVE_KEY` secret）。
- 追蹤頻道：前端「📡 追蹤」分頁搜尋頻道名字或貼網址，寫進 `subs.json`。`watch.yml` 每天
  台灣時間 **05:30／12:00／21:30** 各跑一次：讀頻道 RSS 找新片排進產線，並把這段時間
  新完成的摘要收成一則 Issue 推到手機（沒有新東西就不發）。抓片與彙整刻意錯開一輪——
  這次排進去的片子會在下一個時段才被報出來。狀態在 `.watch-state.json`（機器維護，不用手動改）。

## 本機執行（最穩，用你自己的 IP）

```bash
pip install yt-dlp faster-whisper
python scripts/grab_transcripts.py "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

輸出在 `out/`。可調：`LANGS`、`WHISPER_MODEL`（tiny/base/small/medium）、`ALLOW_ASR=0` 關閉語音辨識。

---

## 檔案結構

```
.
├── index.html                        # 前端（含「雲端產出的逐字稿」面板）
├── queue.txt                         # 要抽的影片清單（改動即觸發產線）
├── .github/workflows/
│   ├── pages.yml                     # 發佈 index.html 到 gh-pages（首次自動建立）
│   └── pipeline.yml                  # 產線：PO-token + 字幕/ASR + 發佈到前端
└── scripts/grab_transcripts.py       # 抽取器：字幕優先，無字幕則 Whisper ASR
```
