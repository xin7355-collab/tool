# -*- coding: utf-8 -*-
"""在 iPhone（a-Shell）上一鍵：下載 YouTube 音訊 → 上傳到逐字稿抽取台。

為什麼要在手機上跑：YouTube 會擋 GitHub 機房的 IP，但你手機是住宅 IP，
yt-dlp 在這裡跑幾乎都會成功。下載完直接呼叫 GitHub API 上傳，
產線收到就自動辨識（Groq/Whisper）、產生摘要並發佈到網站。

一次性設定（在 a-Shell 執行）：
    pip install -U yt-dlp
    python3 -c "import urllib.request as u;open('yt2deck.py','wb').write(u.urlopen('https://raw.githubusercontent.com/xin7355-collab/tool/main/scripts/yt2deck.py').read())"
    echo 'github_pat_你的Token' > deck_token.txt

之後每次（可以一次給多支，會依序抓完）：
    python3 yt2deck.py XXXXXXXXXXX YYYYYYYYYYY ZZZZZZZZZZZ
"""
import os, re, sys, json, time, base64, urllib.request, urllib.parse, urllib.error

VERSION = "8"
OWNER, REPO = "xin7355-collab", "tool"
API = "https://api.github.com/repos/%s/%s" % (OWNER, REPO)
RAW = ("https://raw.githubusercontent.com/%s/%s/main/scripts/yt2deck.py"
       % (OWNER, REPO))
MAX_MB = 45
TOKEN_FILES = ["deck_token.txt", os.path.expanduser("~/Documents/deck_token.txt")]
COOKIE_FILES = ["deck_cookies.txt", os.path.expanduser("~/Documents/deck_cookies.txt")]


HTTPONLY = "#HttpOnly_"


def normalize_netscape(text):
    """把瀏覽器外掛匯出的 cookies.txt 修成 Python 的解析器吃得下的樣子。

    Cookie-Editor 這類擴充在 `.youtube.com` 這種開頭有點的網域上，第二欄
    （include-subdomains）照樣寫 FALSE。但 Python 的 http.cookiejar 對這兩欄有
    `assert domain_specified == initial_dot`，對不上就**整個檔案拒收**，
    yt-dlp 於是連公開影片都抓不了——看起來像被 YouTube 擋，其實是自己沒讀進來。

    只改那一欄（和空的到期時間），不動任何 cookie 內容。
    回傳 (修好的文字, 改了幾行, 丟掉幾行, 還剩幾筆)。
    """
    out, fixed, dropped, kept = [], 0, 0, 0
    for raw in (text or "").splitlines():
        raw = raw.rstrip("\r")
        s = raw.strip()
        if not s or (s.startswith("#") and not s.startswith(HTTPONLY)):
            out.append(raw)
            continue
        pre, body = "", raw
        if body.startswith(HTTPONLY):
            pre, body = HTTPONLY, body[len(HTTPONLY):]
        f = body.split("\t")
        if len(f) != 7:
            dropped += 1
            continue
        bad = False
        want = "TRUE" if f[0].startswith(".") else "FALSE"
        if f[1] != want:
            f[1], bad = want, True
        if not f[4].strip().isdigit():
            f[4], bad = "0", True
        fixed += bad
        kept += 1
        out.append(pre + "\t".join(f))
    return "\n".join(out) + "\n", fixed, dropped, kept


def loadable(path):
    """真的用 yt-dlp 底下那個解析器讀一次。「看起來像」不算數——
    之前就是粗檢查放行、yt-dlp 卻拒收，結果整批下載被拖垮。"""
    try:
        import http.cookiejar
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(path, ignore_discard=True, ignore_expires=True)
        return len(jar)
    except Exception:
        return 0


def find_cookies():
    """有 cookies.txt 就用。YouTube 現在對「沒有登入態的下載」會直接擋，
    住宅 IP 也一樣——手機能過的比例愈來愈低。產線那邊就是靠 cookies 才恢復的。

    格式不對的話 yt-dlp 不是忽略它，而是整個中止下載，所以先修、再實際讀一次。
    """
    for p in COOKIE_FILES:
        try:
            with open(p, encoding="utf-8") as f:
                txt = f.read()
        except OSError:
            continue
        good, fixed, dropped, kept = normalize_netscape(txt)
        if not kept:
            print("⚠️ %s 不是 Netscape cookies.txt 格式（欄位要 tab 分隔），略過。" % p, flush=True)
            continue
        if good != txt:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(good)
                print("ℹ️ %s 修正了 %d 行、略過 %d 行（外掛匯出的小差異），已存回。"
                      % (p, fixed, dropped), flush=True)
            except OSError:
                pass
        if loadable(p):
            return p
        print("⚠️ %s 修完還是讀不進去，略過。請重新匯出一份。" % p, flush=True)
    return ""


COOKIEFILE = find_cookies()


def self_update():
    """開跑前先確認自己是不是最新版，是舊的就換掉再跑。

    手機上要使用者自己重跑安裝指令太容易忘，忘了就會出現「新功能沒作用」
    （例如一次給多支網址卻只抓第一支）。抓不到更新就照舊跑，不擋事。
    """
    if os.environ.get("YT2DECK_NOUPDATE"):
        return
    os.environ["YT2DECK_NOUPDATE"] = "1"       # 換版後重跑時不要再更新一次
    try:
        me = os.path.abspath(__file__)
        with urllib.request.urlopen(RAW, timeout=20) as r:
            new = r.read()
        if not new.startswith(b"# -*- coding"):   # 抓到錯誤頁面就別亂寫
            return
        with open(me, "rb") as f:
            if f.read() == new:
                return
        with open(me, "wb") as f:
            f.write(new)
        print("腳本已自動更新到最新版，重新執行…\n", flush=True)
    except Exception:
        return
    # 直接在同一個行程裡跑新版：a-Shell 不保證支援 os.execv
    exec(compile(new, me, "exec"), {"__name__": "__main__", "__file__": me})
    sys.exit(0)


def _clean(t):
    """把貼上時常混進來的引號、空白、換行清掉。"""
    t = (t or "").strip().strip("'\"").strip()
    return "".join(t.split())          # 移除中間所有空白／換行


def get_token():
    raw, src = os.environ.get("DECK_TOKEN", ""), "環境變數 DECK_TOKEN"
    if not raw.strip():
        for p in TOKEN_FILES:
            try:
                with open(p, encoding="utf-8") as f:
                    raw, src = f.read(), p
                break
            except OSError:
                continue
    tok = _clean(raw)
    if not tok:
        sys.exit("找不到 Token。請先執行：\n"
                 "  echo '你的Token' > deck_token.txt")
    if not tok.startswith(("github_pat_", "ghp_", "gho_")):
        sys.exit("Token 格式看起來不對（來源：%s）\n"
                 "  讀到的開頭：%s…（長度 %d）\n"
                 "  正常應該以 github_pat_ 開頭、長度約 90 以上。\n"
                 "  請到網站「⚙ 設定 → 📋 複製 Token」重新複製，再執行：\n"
                 "  echo '貼上Token' > deck_token.txt" % (src, tok[:12], len(tok)))
    if len(tok) < 40:
        sys.exit("Token 長度只有 %d，看起來被截斷了（來源：%s）。\n"
                 "  請重新複製完整的 Token 再存一次。" % (len(tok), src))
    print("Token 已讀取（%s，長度 %d，開頭 %s…）" % (src, len(tok), tok[:10]), flush=True)
    return tok


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|#%&{}$!\'@+`=\s]+', "_", str(s or "audio"))
    return re.sub(r"_+", "_", s).strip("_")[:60] or "audio"


# YouTube 對不同「播放器客戶端」發不同的媒體網址，有些會直接回 403。
# 換一種客戶端重試通常就過了，所以這裡排一串依序試。
PLAYER_CLIENTS = [None, ["ios"], ["tv"], ["android_vr"], ["web_safari"], ["mweb"]]


def _blocked(e):
    """判斷是不是「被擋」型的錯誤（換客戶端有機會救回來）。"""
    s = str(e)
    return ("403" in s or "Forbidden" in s or "unable to download video data" in s
            or "nsig" in s or "player response" in s or "Sign in to confirm" in s)


def _explain(err):
    """把 yt-dlp 的英文錯誤翻成「這支影片到底怎麼了」。

    重點是分辨「影片本身有限制」和「工具太舊」——前者叫使用者更新 yt-dlp
    只是浪費時間，後者才需要更新。
    """
    s = str(err).lower()
    for key, msg in (
        ("sign in to confirm your age", "這支是年齡限制影片，必須登入 YouTube 帳號才能下載"),
        ("age-restricted", "這支是年齡限制影片，必須登入 YouTube 帳號才能下載"),
        ("available to this channel's members", "這支是頻道會員限定影片"),
        ("members-only", "這支是頻道會員限定影片"),
        ("join this channel", "這支是頻道會員限定影片"),
        ("private video", "這支是私人影片，沒有權限的人看不到"),
        ("removed by the uploader", "這支影片已被上傳者刪除"),
        ("terminated", "這個頻道已被關閉"),
        ("video unavailable", "這支影片已被移除，或你所在地區看不到"),
        ("not available in your country", "這支影片在你所在地區看不到"),
        ("live event will begin", "這場直播還沒開始"),
        ("premieres in", "這支是首播影片，時間還沒到"),
        ("sign in to confirm you're not a bot", "YouTube 要求驗證你不是機器人（同一個 IP 抓太多次）"),
        ("confirm you're not a bot", "YouTube 要求驗證你不是機器人（同一個 IP 抓太多次）"),
    ):
        if key in s:
            return msg
    return ""


def _sweep():
    for f in os.listdir("."):
        if f.startswith("deck_tmp."):
            try:
                os.remove(f)
            except OSError:
                pass


def download(url):
    """下載最佳音軌，回傳 (檔案路徑, 標題)。不轉檔，a-Shell 沒有 ffmpeg。"""
    import yt_dlp
    print("下載音訊中…", flush=True)
    last = None
    for n, clients in enumerate(PLAYER_CLIENTS):
        opts = {
            # 挑「夠用就好」的低位元率音軌，不要 bestaudio。
            # 語音辨識 48~64kbps 就綽綽有餘，但 bestaudio 常拿到 128kbps 以上，
            # 檔案大兩三倍——在 4G 上傳又慢又容易斷，還會害雲端那邊要多切一次段。
            "format": "bestaudio[abr<=80]/worstaudio/bestaudio",
            "outtmpl": "deck_tmp.%(ext)s",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 5,
            "fragment_retries": 5,
        }
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
        if COOKIEFILE:
            opts["cookiefile"] = COOKIEFILE
        try:
            _sweep()                      # 上一輪的半成品先清掉
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=True)
                path = y.prepare_filename(info)
            if not os.path.exists(path):  # 少數情況副檔名會被改掉
                for f in os.listdir("."):
                    if f.startswith("deck_tmp."):
                        path = f
                        break
            return path, (info.get("title") or "audio")
        except Exception as e:
            last = e
            if not _blocked(e) or n == len(PLAYER_CLIENTS) - 1:
                break
            print("   被 YouTube 擋下（%s），換一種下載方式再試…"
                  % str(e).split("\n")[0][:60], flush=True)
    _sweep()
    ver = getattr(yt_dlp.version, "__version__", "?")
    first = str(last).split("\n")[0][:120]
    why = _explain(last)
    if why:
        # 影片本身的限制，換工具或更新版本都救不了，直接說清楚就好
        raise RuntimeError("%s。\n   這是影片本身的限制，不是程式問題，同批其他影片不受影響。\n"
                           "   原始訊息：%s" % (why, first))
    raise RuntimeError(
        "YouTube 擋住了這支影片（試過 %d 種下載方式都不行%s）。\n"
        "   （yt-dlp %s）失敗很多支的話看最後的總結，那裡會說該調什麼。\n"
        "   原始錯誤：%s"
        % (len(PLAYER_CLIENTS), "、有帶 cookies" if COOKIEFILE else "、沒帶 cookies",
           ver, first))


def upload(path, title, token, vid=""):
    size_mb = os.path.getsize(path) / 1048576.0
    print("音檔 %.1fMB：%s" % (size_mb, title), flush=True)
    if size_mb > MAX_MB:
        sys.exit("檔案太大（上限 %dMB）。請改用較短的影片。" % MAX_MB)
    with open(path, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(path)[1] or ".m4a"
    # 把影片 ID 用 __yt 標記夾在檔名裡：產線會原封不動帶到逐字稿檔名，
    # 網站就能靠它認出「這支已經抓過了」。safe_name 只作用在標題上，標記不會被吃掉。
    tag = ("__yt" + vid) if vid else ""
    remote = "audio-inbox/%s_%s%s%s" % (time.strftime("%Y%m%d-%H%M%S"),
                                        safe_name(title), tag, ext)

    body = json.dumps({"message": "Upload audio (a-Shell)",
                       "content": content, "branch": "main"}).encode()
    print("上傳中…", flush=True)
    # 手機網路（尤其 4G）傳十幾 MB 常會斷在半路（Broken pipe），重試幾次就過了
    for attempt in range(1, 5):
        req = urllib.request.Request(
            API + "/contents/" + urllib.parse.quote(remote),
            data=body, method="PUT",
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "User-Agent": "yt2deck/" + VERSION})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                r.read()
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if e.code == 401:
                sys.exit("Token 認證失敗（401 Bad credentials）——這代表 Token 字串本身不對，\n"
                         "不是權限問題。多半是存進 deck_token.txt 時被截斷或多了字元。\n"
                         "檢查：cat deck_token.txt   （應為單獨一行、以 github_pat_ 開頭）\n"
                         "重存：到網站「⚙ 設定 → 📋 複製 Token」再執行\n"
                         "      python3 yt2deck.py --set-token")
            if e.code == 403:
                # GitHub 也會用 403 回一些「跟權限無關」的暫時性狀況：
                # 規則驗證逾時、次級速率限制。那些重試就會過，不該叫使用者去改 Token。
                soft = ("Timed out validating", "please try again",
                        "secondary rate limit", "abuse detection", "try again later")
                if any(k.lower() in detail.lower() for k in soft):
                    if attempt < 4:
                        why = "GitHub 暫時忙線（403）"
                        wait = 5 * attempt
                        print("   %s，%d 秒後重試（第 %d/4 次）…" % (why, wait, attempt), flush=True)
                        time.sleep(wait)
                        continue
                    raise RuntimeError("GitHub 一直回「暫時忙線」（403），試了 4 次都沒過。\n"
                                       "   這不是權限問題，稍後再送一次通常就好。\n   %s" % detail)
                sys.exit("權限不足（403）。這把 Token 需要 Contents: Read and write，\n"
                         "且 Repository access 要包含 %s/%s。\n%s" % (OWNER, REPO, detail))
            if e.code == 422 and "sha" in detail:
                print("   這個檔名已經在排隊了，跳過。", flush=True)
                return
            if e.code < 500 or attempt == 4:
                raise RuntimeError("上傳失敗 HTTP %s：%s" % (e.code, detail))
            why = "HTTP %s" % e.code
        except Exception as e:                       # Broken pipe／逾時／連線中斷
            if attempt == 4:
                raise RuntimeError("上傳失敗（網路中斷 4 次）：%s" % e)
            why = str(e)[:60]
        wait = 5 * attempt
        print("   上傳中斷（%s），%d 秒後重試（第 %d/4 次）…" % (why, wait, attempt), flush=True)
        time.sleep(wait)
    print("✅ 上傳成功！產線開始辨識，完成後會出現在："
          "\n   https://%s.github.io/%s/" % (OWNER, REPO), flush=True)


def set_token():
    """互動式存 Token：避開 shell 引號與 Ctrl+D（a-Shell 鍵盤沒有 Ctrl）。"""
    print("請長按貼上你的 GitHub Token，然後按 Enter：", flush=True)
    tok = _clean(input())
    if not tok.startswith(("github_pat_", "ghp_", "gho_")) or len(tok) < 40:
        sys.exit("看起來不是完整的 Token（開頭 %s…、長度 %d）。\n"
                 "請到網站「⚙ 設定 → 📋 複製 Token」重新複製後再試。"
                 % (tok[:12] or "(空)", len(tok)))
    with open("deck_token.txt", "w", encoding="utf-8") as f:
        f.write(tok)
    print("✅ 已存入 deck_token.txt（長度 %d，開頭 %s…）" % (len(tok), tok[:10]))


def set_cookies(src=""):
    """存 cookies.txt。給了路徑就讀檔，沒給就讀剪貼簿。

    不能用 input() 讓使用者貼：cookies 是多行、欄位靠 tab 分隔——終端機會把 tab
    當成自動補齊吃掉，貼進去就毀了。a-Shell 有 pbpaste 可以直接讀剪貼簿，
    Cookie-Editor 的「Export」剛好就是匯到剪貼簿。

    但剪貼簿很容易在中途被別的東西蓋掉（複製個網址就沒了），所以也接受檔案路徑：
        python3 yt2deck.py --set-cookies ~/Documents/cookies.txt
    """
    txt, where = "", ""
    if src:
        path = os.path.expanduser(src)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                txt = f.read()
            where = path
        except OSError as e:
            sys.exit("讀不到 %s（%s）。\n"
                     "   路徑可以用「檔案」App 長按檔案 → 拷貝路徑，或先把檔案\n"
                     "   搬到 a-Shell 的 Documents 再直接打檔名。" % (path, e.strerror or e))
    else:
        try:
            import subprocess
            txt = subprocess.run(["pbpaste"], capture_output=True, text=True,
                                 timeout=20).stdout
            where = "剪貼簿"
        except Exception:
            txt = ""

    rows = [ln for ln in txt.splitlines()
            if ln.strip() and not ln.strip().startswith("#") and len(ln.split("\t")) >= 6]
    if not rows:
        # 把實際讀到什麼講出來——「格式不對」四個字沒辦法讓人知道是拿錯東西還是選錯格式
        head = " ".join(txt.split())[:40]
        looks_json = txt.lstrip().startswith(("[", "{"))
        why = ("看起來是 JSON" if looks_json
               else "看起來不是 cookies" if txt.strip()
               else "是空的")
        sys.exit(
            "從%s讀到的東西不是 Netscape cookies.txt（每行要 6 個以上 tab 分隔的欄位）。\n"
            "   讀到 %d 個字元、%d 行，%s：%s\n"
            "\n"
            "   %s"
            % (where or "剪貼簿", len(txt), len(txt.splitlines()), why,
               (head + "…") if head else "(空白)",
               ("Cookie-Editor 匯出時要選 Netscape，不要選 JSON。" if looks_json else
                "常見原因：匯出之後又複製了別的東西，剪貼簿被蓋掉了。\n"
                "   → 重新 Export 一次，然後「馬上」切到 a-Shell 執行這個指令；\n"
                "   → 或把 cookies 存成檔案，再用：\n"
                "        python3 yt2deck.py --set-cookies 檔名.txt")))
    # 直接照抄會踩到 Cookie-Editor 的欄位小差異，yt-dlp 讀不進去（見 normalize_netscape）
    good, fixed, dropped, kept = normalize_netscape(txt)
    with open("deck_cookies.txt", "w", encoding="utf-8") as f:
        f.write(good)
    yt = sum(1 for r in good.splitlines()
             if r.strip() and len(r.split("\t")) == 7 and "youtube" in r.split("\t")[0].lower())
    n = loadable("deck_cookies.txt")
    if not n:
        sys.exit("存起來了，但 yt-dlp 的解析器還是讀不進去。請重新 Export 一份 Netscape 格式。")
    print("✅ 已存入 deck_cookies.txt（%d 筆 cookie，其中 youtube.com 的有 %d 筆）%s"
          % (kept, yt, ("；順手修正了 %d 行" % fixed) if fixed else ""))
    if not yt:
        print("⚠️ 裡面沒有 youtube.com 的 cookie —— 匯出時要停在 youtube.com 的分頁上。")


DONE_FILE = "deck_done.txt"      # 手機上的備援名單，連不到 GitHub 時才派上用場
QUEUE_BRANCH, QUEUE_PATH = "status", "queue.txt"


def fetch_queue(token):
    """把網站放好的待抓清單讀回來。

    為什麼要繞這一圈：ashell:// 的指令是塞在網址裡的，影片一多就會變成
    三百多個字元，a-Shell 收到過長的網址會直接閃退。改成網站先把清單寫進
    repo、指令只留一個 --queue，不管幾支影片指令長度都一樣。
    """
    req = urllib.request.Request(
        "%s/contents/%s?ref=%s" % (API, QUEUE_PATH, QUEUE_BRANCH),
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github.raw",
                 "User-Agent": "yt2deck/" + VERSION})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit("找不到待抓清單。請回網站重新按一次「🚀 一鍵下載」。")
        raise
    ids = [x.strip() for x in raw.splitlines() if x.strip() and not x.startswith("#")]
    if not ids:
        sys.exit("待抓清單是空的。請回網站勾選影片後再按「🚀 一鍵下載」。")
    return ids


def _list_names(path, ref, token):
    """列出 repo 某個資料夾裡的檔名，回傳 (檔名列表, 有沒有問到)。

    空資料夾會回 404，那是「問到了、裡面沒東西」；連不上才算沒問到。
    """
    req = urllib.request.Request(
        "%s/contents/%s?ref=%s" % (API, urllib.parse.quote(path), ref),
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "yt2deck/" + VERSION})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        return ([x.get("name", "") for x in j] if isinstance(j, list) else []), True
    except urllib.error.HTTPError as e:
        return [], e.code == 404
    except Exception:
        return [], False


def _local_done():
    try:
        with open(DONE_FILE, encoding="utf-8") as f:
            return set(x.strip() for x in f if x.strip())
    except OSError:
        return set()


def remember_done(vid):
    """記下這支抓過了。只當作連不到 GitHub 時的備援。"""
    if not vid or vid in _local_done():
        return
    try:
        with open(DONE_FILE, "a", encoding="utf-8") as f:
            f.write(vid + "\n")
    except OSError:
        pass


def already_have(token):
    """哪些影片不用再抓：已經有逐字稿的、或已經在佇列等轉的。

    以 GitHub 上的實際檔案為準（靠檔名裡的 __yt<影片ID> 標記比對）——
    這樣你在網站上刪掉某篇逐字稿之後，那支就能重抓，符合直覺。
    只有連不上 GitHub 時才退而用手機上的備援名單。
    """
    ids, reachable = set(), True
    for path, ref in (("transcripts", "gh-pages"), ("audio-inbox", "main")):
        names, ok = _list_names(path, ref, token)
        reachable = reachable and ok
        for name in names:
            m = re.search(r"__yt([A-Za-z0-9_-]{11})", name)
            if m:
                ids.add(m.group(1))
    if not reachable:
        local = _local_done()
        if local:
            print("   （連不上 GitHub，改用手機上的紀錄比對 %d 筆）" % len(local), flush=True)
        return local
    return ids


def to_url(arg):
    """把一個參數轉成 (網址, 影片ID)。也接受純影片 ID —— ashell:// 喚起時
    網址不含 :// ? & 等字元，最不容易在傳遞過程中被吃掉。"""
    arg = arg.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", arg):
        return "https://www.youtube.com/watch?v=" + arg, arg
    if "youtu" not in arg:
        raise ValueError("不是 YouTube 網址或 11 碼影片 ID：%s" % arg[:40])
    m = re.search(r"(?:v=|/shorts/|/live/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})", arg)
    return arg, (m.group(1) if m else "")


def one(arg, token):
    """處理一支影片。回傳標題（成功）或拋例外（失敗）。"""
    url, vid = to_url(arg)
    path = None
    try:
        path, title = download(url)
        upload(path, title, token, vid)
        remember_done(vid)          # 備援名單：連不上 GitHub 時還能靠它避免重抓
        return title
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _workdir():
    """切到腳本自己所在的資料夾（通常是 ~/Documents）。

    a-Shell 用 ashell:// 喚起時工作目錄是 ~group，而且它不支援 ; && 這類
    分隔符號（整串會被當成一個指令的參數），所以沒辦法先 cd 再執行。
    改由腳本自己切，deck_token.txt 和下載暫存檔才找得到、放得下。
    """
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        if d and os.path.isdir(d):
            os.chdir(d)
    except Exception:
        pass                                  # 用 -c 執行時沒有 __file__，維持原目錄


def main():
    if len(sys.argv) < 2:
        sys.exit('用法：python3 yt2deck.py 影片ID [影片ID ...]\n'
                 '     （也可以直接給完整 YouTube 網址，可一次給多支）\n'
                 '     已經有逐字稿的會自動跳過；要重抓請加 --force\n'
                 '設定 Token：python3 yt2deck.py --set-token\n'
                 '設定 cookies：python3 yt2deck.py --set-cookies（先用 Cookie-Editor 匯出到剪貼簿）')
    _workdir()
    if sys.argv[1] in ("--set-token", "-t"):
        set_token()
        return
    if sys.argv[1] in ("--set-cookies", "-c"):
        set_cookies(sys.argv[2] if len(sys.argv) > 2 else "")
        return
    self_update()
    args = [a for a in sys.argv[1:] if a not in ("--force", "-f")]
    force = len(args) != len(sys.argv) - 1
    try:
        import yt_dlp
        ydl_ver = getattr(yt_dlp.version, "__version__", "?")
    except Exception:
        sys.exit("找不到 yt-dlp。請先在 a-Shell 執行：pip install -U yt-dlp")
    token = get_token()
    if args and args[0] in ("--queue", "-q"):
        args = fetch_queue(token)
    print("yt2deck v%s（yt-dlp %s，%s）— 這次收到 %d 支"
          % (VERSION, ydl_ver,
             ("有 cookies" if COOKIEFILE else "沒有 cookies ← 大量失敗多半是這個"),
             len(args)), flush=True)

    # 同一批裡重複勾到的先去掉（純 ID 和完整網址算同一支）
    seen, uniq, dup = set(), [], 0
    for a in args:
        try:
            vid = to_url(a)[1]
        except ValueError:
            uniq.append(a); continue
        if vid and vid in seen:
            dup += 1; continue
        if vid:
            seen.add(vid)
        uniq.append(a)
    if dup:
        print("⏭  同一批裡有 %d 支重複，已合併" % dup, flush=True)
    args = uniq

    # 抓過的就別再抓：省下載、省上傳、省雲端轉錄
    if not force:
        have = already_have(token)
        keep, skip = [], []
        for a in args:
            try:
                vid = to_url(a)[1]
            except ValueError:
                keep.append(a); continue          # 格式不對，留給後面報錯
            (skip if (vid and vid in have) else keep).append(a)
        if skip:
            print("⏭  跳過 %d 支已經抓過的（要重抓請加 --force）：" % len(skip), flush=True)
            for a in skip:
                print("     %s" % a[:60], flush=True)
        if not keep:
            print("\n這些全部都抓過了，沒有新的要下載。", flush=True)
            return
        if len(keep) != len(args):
            print("實際要抓 %d 支\n" % len(keep), flush=True)
        args = keep

    ok, bad = [], []
    for n, arg in enumerate(args, 1):
        if len(args) > 1:
            print("\n=== 第 %d/%d 支 ===" % (n, len(args)), flush=True)
        try:
            ok.append(one(arg, token))
        except SystemExit:
            raise                        # Token 之類的致命錯誤，不用繼續跑
        except Exception as e:
            # 一支抓不到不該讓整批停擺，記下來最後一起報告
            bad.append((arg, str(e).replace("\n", " ")[:120]))
            print("✗ 這支失敗：%s" % bad[-1][1], flush=True)
    if len(args) > 1:
        print("\n===== 總結：成功 %d／失敗 %d =====" % (len(ok), len(bad)), flush=True)
        for t in ok:
            print("  ✅ %s" % t[:50], flush=True)
        for a, e in bad:
            print("  ✗ %s → %s" % (a[:30], e.split("\n")[0], ), flush=True)
        # 診斷要看比例，不能只看「有沒有人成功」。舊版只要有 1 支成功就斷言
        # 「工具本身正常、是那幾支影片自己的限制」——20 支失敗 14 支時照樣這樣講，
        # 使用者去看影片發現根本能看、也不是會員，就白白繞遠路。
        # 過期的 yt-dlp 典型症狀正是「大部分掛、少數還能過」。
        n = len(ok) + len(bad)
        # 大量失敗時最常見的原因是「沒有登入態」，不是版本太舊——YouTube 現在
        # 對匿名下載愈擋愈兇，住宅 IP 也一樣。先前這裡叫人去更新 yt-dlp，
        # 但版本明明已經是最新的，只是把人帶去繞遠路。
        if bad and (not ok or (len(bad) >= 3 and len(bad) * 3 >= n)):
            head = ("整批都失敗" if not ok
                    else "失敗 %d/%d 支（%d%%）" % (len(bad), n, round(len(bad) * 100 / n)))
            if not COOKIEFILE:
                print("\n  ⚠️ %s → 最可能是 YouTube 要求登入驗證（這台沒有帶 cookies）。\n"
                      "     把 cookies.txt 存成 ~/Documents/deck_cookies.txt 就會自動使用，\n"
                      "     取法見 repo 裡的「如何取得cookies.md」。\n"
                      "     （yt-dlp %s；版本通常不是原因，真的要排除再跑 pip install -U yt-dlp）"
                      % (head, ydl_ver), flush=True)
            else:
                print("\n  ⚠️ %s → 已經有帶 cookies（%s），所以多半是：\n"
                      "     ・cookies 過期了 → 重新匯出一份\n"
                      "     ・這個 IP 被 YouTube 暫時限制 → 等一陣子，或切換行動網路／Wi-Fi\n"
                      "     （yt-dlp %s）"
                      % (head, os.path.basename(COOKIEFILE), ydl_ver), flush=True)
        elif bad:
            print("\n  其他 %d 支都成功，失敗的只有 %d 支 → 多半是那幾支影片自己的限制\n"
                  "  （會員限定／年齡限制／已下架）。真的不確定就更新一次 yt-dlp 再試：\n"
                  "  pip install -U yt-dlp" % (len(ok), len(bad)), flush=True)
    if bad and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
