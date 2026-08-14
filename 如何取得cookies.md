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

順便：**`YT_PROXY` 直接刪掉**。那個值也是壞的（不是可用的代理位址），
現在會被程式忽略，留著沒有任何作用。

### 7. 驗證有沒有成功

Actions → **逐字稿產線** → Run workflow。跑完看日誌：

| 看到什麼 | 意思 |
|---|---|
| `⚠️ YT_COOKIES 不是 Netscape cookies.txt 格式` | 格式還是不對，回到第 5 步 |
| `Sign in to confirm you're not a bot` | 格式對了但 cookie 沒被接受（多半是匯出後又在瀏覽器動過 YouTube，重做一次） |
| `完成：成功 N，失敗 0` | 成功，恢復全自動 |

## 會過期

YouTube 的 cookie 大概幾週到幾個月失效。哪天日誌又出現 bot 驗證錯誤，
重跑一次這份流程就好。

失效時 Actions 會變**紅燈並寄信**給你——不會再像以前那樣一片綠、壞了好幾天沒人知道。
