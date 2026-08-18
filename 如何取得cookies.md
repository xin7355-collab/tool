# 怎麼拿到 YouTube 的 cookies.txt

## 為什麼需要

GitHub 機房 IP 會被 YouTube 要求「登入確認不是機器人」，擋在很前面——連字幕清單都拿不到，
所以每一支都失敗。給它一份已登入的 cookies，產線就能通過驗證、恢復全自動。

沒有也能用，只是每次都要按追蹤分頁的「🚀 用手機補抓」（手機是住宅 IP，不會被擋）。

> ⚠️ **要一台電腦**（Mac / Windows）。iPhone 上做不到——Safari 和 iOS 版 Chrome
> 都不能安裝這類擴充功能，也讀不到自己的 cookie 檔。

## 開始前：建議用小號

cookies 等於那個 Google 帳號的通行證，拿到的人可以用你的身分操作 YouTube。
**另外辦一個 Google 帳號專門給這個用**，不要用你的主帳號。

（存進 GitHub Secret 是加密的，日誌裡只會顯示 `***`，別人的 PR 也讀不到。
但既然可以用小號，就沒必要拿主帳號冒險。）

## 步驟

### 1. 裝擴充功能

| 瀏覽器 | 裝這個 |
|---|---|
| Chrome / Edge | 線上應用程式商店搜尋 **Get cookies.txt LOCALLY** |
| Firefox | 附加元件搜尋 **cookies.txt** |

選開源、名字有 **LOCALLY** 的那種——它只在你電腦上處理。
會「幫你同步到雲端」的不要裝，那等於把帳號交出去。

### 2. 用無痕視窗登入（這步很重要）

1. 開一個**無痕／私密視窗**
2. 到 youtube.com，用小號登入
3. 隨便開一支影片，確認真的是登入狀態

**為什麼要無痕**：YouTube 會不斷輪替 cookie。用一般視窗匯出的話，
你之後繼續逛 YouTube，剛匯出的那份很快就被作廢。
無痕視窗匯出完直接關掉，那份就凍在有效狀態。

### 3. 匯出

1. 保持在那個無痕視窗、停在 youtube.com 頁面上
2. 點擴充功能圖示 → **Export** 或 **Copy**（Netscape 格式，這類擴充預設就是）

### 4. 關掉無痕視窗——不要按登出

按「登出」會讓剛匯出的那份**立刻失效**。直接關視窗就好。

### 5. 先檢查格式再貼

正確的長這樣，每行是 **tab 分隔的 7 個欄位**：

```
# Netscape HTTP Cookie File
.youtube.com	TRUE	/	TRUE	1799999999	SID	xxxxxxxxxxxx
.youtube.com	TRUE	/	TRUE	1799999999	HSID	xxxxxxxxxxxx
```

看到 `[{"domain":".youtube.com",...}]` 這種 **JSON 就是選錯格式了**，重匯一次。

> 之前設定的那份就是格式不對。yt-dlp 遇到讀不懂的 cookie 檔不是忽略它，
> 而是**中止整個下載**——所以連根本不需要登入的公開影片也一起死。
> 現在程式會先驗證格式、不合格就印警告並略過，不會再全滅，但也就不會用它。

### 6. 貼進 GitHub

1. 開 https://github.com/xin7355-collab/tool/settings/secrets/actions
2. `YT_COOKIES` 已經存在 → 點鉛筆 **Update**；不在就按 **New repository secret**
3. Name：`YT_COOKIES`
4. Secret：貼整份內容（含開頭 `# Netscape HTTP Cookie File` 那行）
5. 存檔

順便：**`YT_PROXY` 直接刪掉**（理由見下面那一節）。

### 7. 驗證有沒有成功

Actions → **逐字稿產線** → Run workflow。跑完看日誌：

| 看到什麼 | 意思 |
|---|---|
| `⚠️ YT_COOKIES 不是 Netscape cookies.txt 格式` | 格式還是不對，回到第 5 步 |
| `Sign in to confirm you're not a bot` | 格式對了但 cookie 沒被接受（多半是匯出後又在瀏覽器動過 YouTube，重做一次） |
| `完成：成功 N，失敗 0` | 成功，恢復全自動 |

## 手機（yt2deck）也要同一份 cookies

`yt2deck.py` 一樣會被 YouTube 要求登入驗證——住宅 IP 只是比較不容易被盯上，
不是免疫。大量失敗（尤其看到 `No video formats found` 或 `403 Forbidden`）
通常就是這個原因，跟 yt-dlp 版本無關。

把同一份 cookies.txt 存到手機：

```
~/Documents/deck_cookies.txt
```

`yt2deck.py` 啟動時會自己找這個檔，開頭那行會顯示「有 cookies」或「沒有 cookies」。
格式不對會印警告並略過，不會拖垮整批。

> 從電腦傳到 iPhone：用 AirDrop、或存進 iCloud 雲端硬碟再用「檔案」App
> 搬到 a-Shell 的 Documents 資料夾。

## 關於 `YT_PROXY`——多半不需要，先看完再決定

現在設定的那個值是**壞的**（不是可用的代理位址），已經被程式擋下並忽略。
建議直接刪掉。要不要重設，先想清楚下面幾件事。

### 免費的代理沒有用

網路上找得到的免費代理清單、便宜 VPS 開的代理，全部是**機房 IP**——
那正是 YouTube 已經在擋的東西。把一個被擋的機房 IP 換成另一個被擋的機房 IP，
不會有任何改變。

更糟的是**安全問題**：如果 `YT_COOKIES` 和 `YT_PROXY` 同時設了，
你的 YouTube cookies 會**經過那台代理**。來路不明的免費代理可以直接把它們錄下來，
等於把帳號送人。

### 有用的是「住宅代理」，那要花錢

真正能繞過的是 residential / mobile proxy（用真實家戶或行動網路的 IP）。
這是付費服務，常見的按流量計價，大約 **每 GB 1～15 美元**。

流量會用多少要看情況：有字幕的影片只抓字幕，幾 KB 而已；
沒字幕的要下載音檔做語音辨識，一支一小時的節目大約 **30～60MB**。
一天幾支的話，一個月可能落在 1～3GB。

### 但你手機就是住宅 IP

住宅代理在賣的東西，你手機本來就有——而且免費。
這正是 `scripts/yt2deck.py` 的設計理由：在手機上跑 yt-dlp、抓完自動上傳。

所以代理只有在這個情況下才值得：**你要完全不用動手的自動化，而且願意付月費**。

### 真的要設的話

格式要有 scheme 和主機名，例如：

```
http://帳號:密碼@gate.供應商.com:7000
socks5://帳號:密碼@gate.供應商.com:1080
```

貼進 Secrets 的 `YT_PROXY`。程式會先驗證格式，不合格就印警告並略過，
不會再像之前那樣把整批影片拖垮。

> 光有代理不一定夠。YouTube 的判斷是 IP 信譽加上有沒有登入態，
> 實務上「住宅代理 + 有效 cookies」一起用才穩。只設代理仍可能被要求驗證。

## 會過期

YouTube 的 cookie 大概幾週到幾個月失效。哪天日誌又出現 bot 驗證錯誤，
重跑一次這份流程就好。

失效時 Actions 會變**紅燈並寄信**給你——不會再像以前那樣一片綠、壞了好幾天沒人知道。
