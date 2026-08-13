# 逐字稿抽取台 · Transcript Deck

從 YouTube 影片抽逐字稿，切成可讀段落，匯出 `.md`/`.txt`/`.srt`。
**沒有字幕時，會用 Whisper 語音辨識把逐字稿「聽」出來。**

**🌐 線上版（部署後）：https://xin7355-collab.github.io/tool/**

---

## 這個 repo 怎麼用

- 前端：`index.html`（部署到 GitHub Pages，含「雲端產出的逐字稿」面板）。
- 產線：在 `queue.txt` 一行貼一個 YouTube 網址/ID，commit 後 `pipeline.yml` 會自動抽取
  （有字幕→抓字幕；沒字幕→Whisper 語音辨識），把結果發佈到 `gh-pages/transcripts/`，前端就看得到。
- 要抓鎖區/會員影片：repo → **Settings → Secrets and variables → Actions** 新增 `YT_COOKIES`
  （已登入 YouTube 的 `cookies.txt` 內容）；需要代理再加 `YT_PROXY`。
- 追蹤頻道：前端「📡 追蹤」分頁貼頻道網址，寫進 `subs.json`。`watch.yml` 每天
  台灣時間 07:00 讀頻道 RSS 找新片、排進產線，11:00 把當天完成的摘要收成一則
  Issue 推到手機。狀態在 `.watch-state.json`（機器維護，不用手動改）。

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
