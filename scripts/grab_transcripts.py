# -*- coding: utf-8 -*-
"""逐字稿抽取器（yt-dlp 版）。

為什麼改用 yt-dlp：
  公開 Invidious 節點已大量失效（YouTube 擋掉 /search、/captions），前端因此常常
  「搜尋失敗」或誤報「沒有字幕軌」。yt-dlp 直接跟 YouTube 對話，最穩。

在哪裡跑：
  - 本機（住宅／行動網路 IP）跑最穩，通常不需要任何額外設定。
  - GitHub Actions 等資料中心 IP 常被 YouTube 要求「登入確認不是機器人」。此時提供
    cookies（環境變數 YT_COOKIES＝Netscape cookies.txt 的內容，或 YT_COOKIES_FILE＝
    檔案路徑）或代理（YT_PROXY）即可繞過。

用法：
  python scripts/grab_transcripts.py "<網址或ID>" "<網址或ID>" ...
  # 或用環境變數：IDS="a,b,c" python scripts/grab_transcripts.py

輸出：out/NN_<標題>.md / .srt / .txt，外加 out/_index.csv。逐支寫檔，不累積記憶體。
"""
import os, re, csv, sys, json, glob, time, tempfile, pathlib, urllib.request
from concurrent.futures import ThreadPoolExecutor

# yt_dlp 刻意不在這裡 import：轉錄「已上傳音檔」根本不碰 YouTube，
# 那條路的工作流也就不裝 yt-dlp。放模組層會讓 transcribe_upload 一 import 就炸。
# 真正要用的兩個函式自己 import（見 download_audio / get_transcript）。

OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)
LANGS = [s.strip() for s in os.environ.get(
    "LANGS", "zh-TW,zh-Hant,zh-HK,zh,en").split(",") if s.strip()]


def usable_proxy(url):
    """代理位址要有 scheme 和主機名，例如 http://1.2.3.4:8080、socks5://user:pw@host:1080。

    設錯的話 yt-dlp 每一支都會在連線前就炸（Python 解析主機名的
    「label empty or too long」），跟 cookie 一樣是「選填設定弄死全部」。
    寧可不走代理也不要全滅——公開影片本來就不需要代理。
    """
    from urllib.parse import urlparse
    try:
        u = urlparse((url or "").strip())
    except Exception:
        return False
    if u.scheme not in ("http", "https", "socks4", "socks4a", "socks5", "socks5h"):
        return False
    host = u.hostname or ""
    if not host or not re.fullmatch(r"[A-Za-z0-9._:\[\]-]+", host):
        return False
    return all(lb and len(lb) <= 63 for lb in host.split("."))


PROXY = os.environ.get("YT_PROXY", "").strip()
if PROXY and not usable_proxy(PROXY):
    print("⚠️ YT_PROXY 不是可用的代理位址（要像 http://主機:埠 或 socks5://主機:埠），這次忽略它。\n"
          "   公開影片不受影響；用不到就到 Settings → Secrets 把 YT_PROXY 刪掉。", flush=True)
    PROXY = ""
PLAYER_CLIENTS = [s.strip() for s in os.environ.get(
    "YT_PLAYER_CLIENTS", "tv,web_safari,web").split(",") if s.strip()]
# YouTube 給不同的「播放器客戶端」不同的串流。帶著有效 cookies 時，預設那組常常
# 一個可用格式都不回——錯誤是 `Requested format is not available`，**不是**被機器人
# 驗證擋下。也就是說 cookies 有過關，只是拿不到音訊網址。
#
# 這種時候要做的是換一個客戶端、**把 cookies 留著**。原本的退路是把 cookies 拿掉重試，
# 那等於自己走回機器人驗證那道牆：實測 29 支全部第一次「格式不可用」、第二次「Sign in
# to confirm you're not a bot」，看起來像被擋，其實第一次根本沒被擋。
CLIENT_TRIES = [s.strip() for s in os.environ.get(
    "YT_CLIENT_FALLBACKS",
    "tv,web_safari,mweb,tv_embedded,web_embedded,android_vr,web").split(",") if s.strip()]
# 連續掃這麼多支都找不到可用客戶端就放棄掃描（這批整個不行，不是個別影片的事）。
MAX_SWEEPS = int(os.environ.get("YT_MAX_SWEEPS", "3") or "3")
ID_RE = re.compile(r"(?:v=|/shorts/|/live/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})|([A-Za-z0-9_-]{11})")
GAP, MAX_CHARS = 1.6, 180
# 已發佈目錄：若某支影片的逐字稿已存在其中，本次就略過（不重抽、少打 YouTube）
SKIP_DIR = os.environ.get("SKIP_DIR", "").strip()

def looks_netscape(text):
    """粗略檢查是不是 Netscape cookies.txt：至少要有一行是 6 個以上 tab 分隔的欄位。

    格式不對時 yt-dlp 不是「忽略 cookie」而是整個拒收，於是**每一支**影片都失敗——
    連根本不需要登入的公開影片也一起死。瀏覽器外掛匯出成 JSON、或複製貼上時
    tab 被轉成空白，都會踩到。寧可不用 cookie 也不要全滅。
    """
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if len(ln.split("\t")) >= 6:
            return True
    return False


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
            dropped += 1        # 欄位數不對的救不回來，丟掉總比整份被拒收好
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


# cookies：YT_COOKIES（cookies.txt 內容）優先寫成暫存檔；或直接給 YT_COOKIES_FILE 路徑。
# COOKIE_NOTE 記著「這次到底有沒有吃到 cookies」。這幾行印在最前面，可是一批
# 三十支影片的日誌有一千多行，要往回捲很久才看得到——設完 cookies 最想知道的
# 就是這一件事，所以最後的結算也再講一次。
COOKIE_NOTE = ""
COOKIEFILE = os.environ.get("YT_COOKIES_FILE", "").strip()
if not COOKIEFILE and os.environ.get("YT_COOKIES", "").strip():
    _raw = os.environ["YT_COOKIES"]
    if looks_netscape(_raw):
        _txt, _fixed, _dropped, _kept = normalize_netscape(_raw)
        if _fixed or _dropped:
            print("ℹ️ YT_COOKIES 修正了 %d 行、略過 %d 行不合格的（多半是外掛匯出的小差異），"
                  "還有 %d 筆可用。" % (_fixed, _dropped, _kept), flush=True)
        if not _kept:
            COOKIE_NOTE = "❌ 修完之後一筆都不剩，這次沒用它"
            print("⚠️ YT_COOKIES 修完之後一筆都不剩，這次不用它。", flush=True)
        else:
            _t = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
            _t.write(_txt); _t.close()
            # 最後真的用同一個解析器讀一次。之前只做「像不像」的粗檢查，
            # 檢查過了 yt-dlp 卻讀不進去，結果整批被拖垮——那正是要避免的事。
            try:
                import http.cookiejar as _cjmod
                _cj = _cjmod.MozillaCookieJar()
                _cj.load(_t.name, ignore_discard=True, ignore_expires=True)
                COOKIEFILE = _t.name
                COOKIE_NOTE = "✅ 讀進 %d 筆，有帶著跑" % len(_cj)
                print("cookies：可用 %d 筆" % len(_cj), flush=True)
            except Exception as _e:
                COOKIE_NOTE = "❌ 讀不進去（%s），這次沒用它" % str(_e).replace("\n"," ")[:60]
                print("⚠️ YT_COOKIES 修完還是讀不進去（%s），這次不用它。\n"
                      "   請重新匯出一份 Netscape 格式的 cookies.txt。"
                      % str(_e).replace("\n", " ")[:140], flush=True)
    else:
        COOKIE_NOTE = "❌ 不是 Netscape 格式（多半是 Cookie-Editor 匯出時選到 JSON），這次沒用它"
        print("⚠️ YT_COOKIES 不是 Netscape cookies.txt 格式（欄位要用 tab 分隔），這次忽略它。\n"
              "   公開影片不受影響；要抓會員／鎖區影片請重新匯出 cookies.txt，\n"
              "   或到 Settings → Secrets 把 YT_COOKIES 刪掉。", flush=True)


def parse_ids(raw):
    ids, seen = [], set()
    for token in re.split(r"[,\s]+", raw or ""):
        if not token:
            continue
        m = ID_RE.search(token)
        vid = (m.group(1) or m.group(2)) if m else None
        if vid and vid not in seen:
            seen.add(vid); ids.append(vid)
    return ids


def to_paragraphs(cues):
    """把數千條字幕合成可讀段落——長片的可用性全看這一步。"""
    out, cur = [], None
    for c in cues:
        s, dur, txt = c["start"], c.get("duration", 0), c["text"].replace("\n", " ").strip()
        if not txt:
            continue
        if cur is None:
            cur = {"s": s, "e": s + dur, "t": txt}; continue
        too_long = len(cur["t"]) + len(txt) > MAX_CHARS
        if s - cur["e"] > GAP or too_long:
            out.append(cur); cur = {"s": s, "e": s + dur, "t": txt}
        else:
            joiner = "" if re.search(r"[㐀-鿿]$", cur["t"]) else " "
            cur["t"] += joiner + txt; cur["e"] = s + dur
    if cur:
        out.append(cur)
    return out


def hhmmss(sec):
    sec = int(sec); h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return (f"{h}:" if h else "") + f"{m:02d}:{s:02d}"


def srt_time(sec):
    sec = max(0.0, float(sec)); h = int(sec // 3600); m = int(sec % 3600 // 60)
    s = int(sec % 60); ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def safe_name(s):
    return (re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", str(s or "untitled")).strip() or "untitled")[:80]


def pick_lang(cap):
    """依 LANGS 優先序從 {lang: [fmt...]} 選一個語言。exact → prefix → 任何 zh → 第一個。"""
    if not cap:
        return None
    keys = list(cap.keys()); low = {k.lower(): k for k in keys}
    for want in LANGS:          # 逐一語言：先精確、再前綴，才換下一個偏好語言
        w = want.lower()
        if w in low:
            k = low[w]; return k, cap[k]
        for k in keys:
            kl = k.lower()
            if kl.startswith(w) or w.startswith(kl):
                return k, cap[k]
    for k in keys:
        if k.lower().startswith("zh"):
            return k, cap[k]
    return keys[0], cap[keys[0]]


def fetch_url(url):
    handlers = []
    if PROXY:
        handlers.append(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return opener.open(req, timeout=30).read().decode("utf-8", "replace")


def cues_from_json3(txt):
    data = json.loads(txt); cues = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000.0
        cues.append({"start": start, "duration": ev.get("dDurationMs", 0) / 1000.0, "text": text})
    return cues


def cues_from_vtt(txt):
    lines = txt.replace("\r", "").split("\n"); cues = []; i = 0
    def to_sec(t):
        m = re.match(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})", t.strip())
        if not m:
            return 0.0
        return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0
    while i < len(lines):
        if "-->" in lines[i]:
            a, b = lines[i].split("-->")[:2]
            start = to_sec(a.strip().split()[0]); end = to_sec(b.strip().split()[0])
            i += 1; buf = []
            while i < len(lines) and lines[i].strip() != "" and "-->" not in lines[i]:
                buf.append(lines[i]); i += 1
            text = re.sub(r"<[^>]*>", "", " ".join(buf))
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                cues.append({"start": start, "duration": max(0.0, end - start), "text": text})
        else:
            i += 1
    # 自動字幕滾動重複：後句包含前句 → 用後者取代
    out = []
    for c in cues:
        if out and (c["text"] == out[-1]["text"] or out[-1]["text"] in c["text"]):
            out[-1] = c
        else:
            out.append(c)
    return out


ALLOW_ASR = os.environ.get("ALLOW_ASR", "1") == "1"
FORCE_ASR = os.environ.get("FORCE_ASR", "0") == "1"   # 忽略現有字幕，強制用語音辨識
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
# ASR 後端切換：whisper（本機 faster-whisper，預設）或 groq（雲端 API，需 GROQ_API_KEY）
ASR_BACKEND  = os.environ.get("ASR_BACKEND", "whisper").strip().lower()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL   = os.environ.get("GROQ_MODEL", "whisper-large-v3-turbo").strip()
# 自動摘要：有 GROQ_API_KEY 時，用 Groq 的 LLM 幫每篇逐字稿產生重點摘要（失敗就略過，不影響產出）
GROQ_SUMMARY   = os.environ.get("GROQ_SUMMARY", "1") == "1"
# llama-3.1-8b-instant 在 2026-08-16 被 Groq 關掉，之後每次呼叫都回 404 ——
# 摘要與 AI 校對從那天起全部靜默失效。官方指定的接替就是 gpt-oss-20b。
GROQ_LLM_MODEL = os.environ.get("GROQ_LLM_MODEL", "openai/gpt-oss-20b").strip()
GROQ_SEG     = int(os.environ.get("GROQ_SEG_SECONDS", "600") or "600")   # 每段秒數，控制單檔 <25MB
# 小於這個大小就不切段、直接整份送 Groq（上限 25MB，留點餘裕）
GROQ_DIRECT_MB  = float(os.environ.get("GROQ_DIRECT_MB", "20") or "20")
# 同時送幾段。太高會撞 Groq 速率限制，4 對免費方案是安全又有感的值
GROQ_CONCURRENCY = int(os.environ.get("GROQ_CONCURRENCY", "4") or "4")


def _ydl_opts(extra=None):
    opts = {"quiet": True, "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": PLAYER_CLIENTS}}}
    if PROXY:
        opts["proxy"] = PROXY
    if COOKIEFILE:
        opts["cookiefile"] = COOKIEFILE
    if extra:
        opts.update(extra)
    return opts


def download_audio(vid, dest_dir):
    """下載最佳音軌到 dest_dir，回傳 (檔案路徑, info)。"""
    import yt_dlp
    opts = _ydl_opts({"skip_download": False, "format": "bestaudio/best",
                      "outtmpl": os.path.join(dest_dir, "%(id)s.%(ext)s")})
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info("https://www.youtube.com/watch?v=" + vid, download=True)
    files = [os.path.join(dest_dir, f) for f in os.listdir(dest_dir)
             if os.path.isfile(os.path.join(dest_dir, f))]
    if not files:
        raise RuntimeError("音訊下載失敗（沒有取得檔案）")
    path = max(files, key=os.path.getsize)
    print("    音訊：%s（%d bytes）" % (os.path.basename(path), os.path.getsize(path)), flush=True)
    return path, info


def _asr_lang():
    for l in LANGS:
        ll = l.lower()
        if ll.startswith("zh"):
            return "zh"
        if ll.startswith("en"):
            return "en"
    return None


def asr_cues(audio_path, on_progress=None):
    """沒有字幕時，用本機 Whisper（faster-whisper）把逐字稿「分析」出來。

    on_progress(已辨識秒數, 總秒數)：本機辨識沒有「段」的概念，改用時間軸推進度。
    """
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, language=_asr_lang(),
                                      beam_size=1, condition_on_previous_text=False)
    total = float(getattr(info, "duration", 0) or 0)
    cues = []
    for s in segments:
        t = (s.text or "").strip()
        if t:
            cues.append({"start": s.start, "duration": max(0.0, s.end - s.start), "text": t})
        if on_progress and total > 0:
            on_progress(s.end, total)
    if on_progress and total > 0:
        on_progress(total, total)
    return cues


def _groq_transcribe(path, tries=4):
    """把單一音檔送 Groq /audio/transcriptions，回傳 [{start,end,text}]。

    Groq 偶爾會回 500／502／429（伺服器忙），那是暫時性的，不是音檔壞掉，
    所以這裡會退避重試；只有 400/401 這種「重試也沒用」的錯才立刻放棄。
    """
    import uuid
    boundary = "----groq" + uuid.uuid4().hex
    with open(path, "rb") as f:
        audio = f.read()
    fields = {"model": GROQ_MODEL, "response_format": "verbose_json", "temperature": "0"}
    lang = _asr_lang()
    if lang:
        fields["language"] = lang
    parts = []
    for k, v in fields.items():
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode())
        parts.append((v + "\r\n").encode())
    parts.append(("--" + boundary + "\r\n").encode())
    parts.append(b'Content-Disposition: form-data; name="file"; filename="audio.flac"\r\n')
    parts.append(b"Content-Type: audio/flac\r\n\r\n")
    parts.append(audio)
    parts.append(b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode())
    body = b"".join(parts)
    j = None
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/audio/transcriptions", data=body,
            headers={"Authorization": "Bearer " + GROQ_API_KEY,
                     "User-Agent": "Mozilla/5.0 (transcript-tool)",
                     "Content-Type": "multipart/form-data; boundary=" + boundary})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            msg = "Groq HTTP %d：%s" % (e.code, detail or str(e.reason))
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries:
                raise RuntimeError(msg)
        except Exception as e:                    # 連線中斷／逾時也重試
            msg = "Groq 連線失敗：%s" % e
            if attempt == tries:
                raise RuntimeError(msg)
        wait = 5 * (2 ** (attempt - 1))           # 5s → 10s → 20s
        print("      %s，%d 秒後重試（第 %d/%d 次）…" % (msg, wait, attempt, tries), flush=True)
        time.sleep(wait)
    out = []
    for s in (j.get("segments") or []):
        t = (s.get("text") or "").strip()
        if t:
            out.append({"start": float(s.get("start", 0) or 0),
                        "end": float(s.get("end", 0) or 0), "text": t})
    if out:
        return out
    t = (j.get("text") or "").strip()
    return [{"start": 0.0, "end": 0.0, "text": t}] if t else []


def groq_asr_cues(audio_path, on_progress=None):
    """Groq 雲端語音辨識：切段（每段 <25MB）→ 逐段轉 → 位移時間軸 → 合併成一份。

    on_progress(做完幾段, 總共幾段)：給網站畫讀取條用，可不給。
    """
    import subprocess, glob as _glob
    if not GROQ_API_KEY:
        raise RuntimeError("ASR_BACKEND=groq 但未設定 GROQ_API_KEY")
    with tempfile.TemporaryDirectory() as td:
        size_mb = os.path.getsize(audio_path) / 1048576.0
        if size_mb <= GROQ_DIRECT_MB:
            # 檔案本來就在 Groq 上限內，直接整份送：省掉 ffmpeg 轉檔（長音檔要幾十秒），
            # 時間軸也是原生的，不用自己位移。
            print("    音檔 %.1fMB，直接整份送 Groq（免切段）" % size_mb, flush=True)
            chunks, offsets = [audio_path], [0.0]
        else:
            pat = os.path.join(td, "chunk_%04d.flac")
            # 16kHz 單聲道 FLAC 並依時間切段，確保每段遠小於 Groq 的 25MB 上限
            subprocess.run(["ffmpeg", "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000",
                            "-c:a", "flac", "-f", "segment", "-segment_time", str(GROQ_SEG), pat],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            chunks = sorted(_glob.glob(os.path.join(td, "chunk_*.flac")))
            if not chunks:
                raise RuntimeError("音訊切段失敗")
            offsets = [i * GROQ_SEG for i in range(len(chunks))]

        n = len(chunks)
        done = [0]
        if on_progress:
            on_progress(0, n)

        def one(i):
            """轉一段。一段轉壞不該賠掉整支影片：回傳 None 表示這段失敗。"""
            try:
                return _groq_transcribe(chunks[i])
            except Exception as e:
                print("      第 %d 段放棄（%s）" % (i + 1, e), flush=True)
                return None
            finally:
                done[0] += 1
                print("    Groq 完成 %d/%d 段" % (done[0], n), flush=True)
                if on_progress:
                    on_progress(done[0], n)

        # 各段之間互不相依，平行送可以把等待時間疊在一起而不是一段段排隊。
        # 併發數壓在 GROQ_CONCURRENCY，太多會撞 Groq 的速率限制（429）。
        if n == 1:
            results = [one(0)]
        else:
            with ThreadPoolExecutor(max_workers=min(GROQ_CONCURRENCY, n)) as ex:
                results = list(ex.map(one, range(n)))

        cues, failed = [], []
        for i, segs in enumerate(results):
            off = offsets[i]
            if segs is None:
                failed.append(i + 1)
                cues.append({"start": off, "duration": 0.0,
                             "text": "〔此段辨識失敗，約 %s 起〕" % hhmmss(off)})
                continue
            for s in segs:
                cues.append({"start": s["start"] + off,
                             "duration": max(0.0, s["end"] - s["start"]),
                             "text": s["text"]})
        cues.sort(key=lambda c: c["start"])      # 平行回來的順序不保證，照時間軸排好
        if failed and len(failed) == n:
            raise RuntimeError("全部 %d 段都辨識失敗" % n)
        if failed:
            print("    ⚠ 有 %d/%d 段辨識失敗（第 %s 段），其餘已產出"
                  % (len(failed), n, "、".join(map(str, failed))), flush=True)
    return cues


def ymd(s):
    """yt-dlp 的 upload_date 是 20260821 這種字串，轉成 2026-08-21。"""
    s = str(s or "")
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 and s.isdigit() else ""


def get_transcript(vid):
    import yt_dlp
    # ignore_no_formats_error：有些影片對機房 IP 只回 SABR 串流（無可選格式），
    # 沒這個選項連「查字幕」都會炸；抓字幕根本不需要影片格式。
    with yt_dlp.YoutubeDL(_ydl_opts({"skip_download": True,
                                     "ignore_no_formats_error": True})) as y:
        info = y.extract_info("https://www.youtube.com/watch?v=" + vid, download=False)
    title = info.get("title") or vid
    # 上片日期：這裡已經跟 YouTube 要過完整資料了，順手帶出來，
    # 網站列表才有日期可以顯示（以前只能從標題猜，375 篇裡只猜得出 34 篇）
    up = ymd(info.get("upload_date"))
    picked = None if FORCE_ASR else (pick_lang(info.get("subtitles")) or pick_lang(info.get("automatic_captions")))
    if picked:
        lang, fmts = picked
        order = {"json3": 0, "srv3": 1, "vtt": 2, "srv1": 3, "ttml": 4}
        fmt = sorted(fmts, key=lambda f: order.get(f.get("ext"), 9))[0]
        content = fetch_url(fmt["url"])
        cues = cues_from_json3(content) if (fmt.get("ext") == "json3"
                                            or content.lstrip().startswith("{")) else cues_from_vtt(content)
        if cues:
            return title, lang, cues, up
    # 沒有字幕 → 語音辨識（ASR）
    ls = info.get("live_status")
    if ls in ("is_live", "is_upcoming"):
        raise RuntimeError("直播進行中／尚未開始，還沒有字幕。等直播結束轉成一般影片後再送一次")
    if ls == "post_live":
        raise RuntimeError("直播剛結束，YouTube 還在處理存檔。等影片頁顯示片長（非 LIVE）後再送一次")
    if not ALLOW_ASR:
        raise RuntimeError("此影片沒有字幕，且未開啟 ASR")
    use_groq = ASR_BACKEND == "groq"
    print("    無字幕，改用語音辨識（%s）…" %
          ("Groq " + GROQ_MODEL if use_groq else "本機 Whisper " + WHISPER_MODEL), flush=True)
    with tempfile.TemporaryDirectory() as td:
        audio_path, ainfo = download_audio(vid, td)
        title = ainfo.get("title") or title
        lang_tag = "ASR:" + WHISPER_MODEL
        if use_groq:
            try:
                cues = groq_asr_cues(audio_path)
                lang_tag = "Groq:" + GROQ_MODEL
            except Exception as e:   # Groq 失敗（額度滿/金鑰失效/網路）→ 自動退回本機 Whisper
                print("    Groq 失敗（%s），自動改用本機 Whisper %s…" % (e, WHISPER_MODEL), flush=True)
                cues = asr_cues(audio_path)
        else:
            cues = asr_cues(audio_path)
    if not cues:
        raise RuntimeError("語音辨識結果為空")
    return title, lang_tag, cues, (up or ymd(ainfo.get("upload_date")))


POLISH = (os.environ.get("GROQ_POLISH") or "1") not in ("0", "false", "")
POLISH_SEG = int(os.environ.get("POLISH_CHARS") or "2500")


def groq_polish(title, paras):
    """讓 LLM 修掉語音辨識的錯字，回傳修好的段落清單。

    只做「修」不做「寫」：同音錯字、標點、專有名詞（股票代號、人名、工具名）。
    刻意分段送並逐段比對長度——LLM 一次吃太多容易自己摘要起來，那會吃掉內容。
    任何一段有問題就沿用原文，寧可留著辨識錯字也不要弄丟話。
    """
    if not (GROQ_API_KEY and POLISH and paras):
        return None
    sys_msg = (
        "你是中文逐字稿校對員。任務是修正語音辨識造成的錯誤，不是改寫。\n"
        "可以做：同音錯字、缺漏標點、專有名詞（股票代號、人名、術語）。\n"
        "絕對不可以：刪句、縮寫、摘要、加入原文沒有的話、改變語意或語氣。\n"
        "逐行對應輸出，輸入幾行就輸出幾行，不要加編號或說明。"
    )
    out, buf, blen = [], [], 0
    fixed_any = []          # 真的被改過的段數；全部沿用原文就不能說「已校對」

    def flush(chunk):
        if not chunk:
            return
        raw = "\n".join(chunk)
        try:
            body = json.dumps({
                "model": GROQ_LLM_MODEL, "temperature": 0,
                "messages": [{"role": "system", "content": sys_msg},
                             {"role": "user", "content":
                              "影片主題：%s\n\n逐字稿：\n%s" % (title, raw)}],
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions", data=body,
                headers={"Authorization": "Bearer " + GROQ_API_KEY,
                         "Content-Type": "application/json",
                         "User-Agent": "transcript-tool/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            got = [x for x in j["choices"][0]["message"]["content"].split("\n") if x.strip()]
        except Exception as e:
            hint = ("（Groq 找不到模型 %s，多半已停用）" % GROQ_LLM_MODEL
                    if "404" in str(e) else "")
            print("    校對這段失敗（沿用原文）：%s%s" % (str(e)[:70], hint), flush=True)
            out.extend(chunk); return
        # 行數對不上，或長度掉超過三成 = 它自己摘要了，不能用
        if len(got) != len(chunk) or len("".join(got)) < len(raw) * 0.7:
            print("    校對結果不對齊（沿用原文）：%d 行 → %d 行" % (len(chunk), len(got)), flush=True)
            out.extend(chunk); return
        out.extend(got); fixed_any.append(len(got))

    for p in paras:
        buf.append(p); blen += len(p)
        if blen >= POLISH_SEG:
            flush(buf); buf, blen = [], 0
    flush(buf)
    # 每一段都退回原文時，之前照樣回 out（＝原文），呼叫端就印「已校對 N 段」、
    # 還在 .txt 開頭寫「此版已用 AI 校對錯字」。模型被下架後每篇都這樣，等於說謊。
    if not fixed_any:
        return None
    return out if len(out) == len(paras) else None


SUM_SEG = int(os.environ.get("SUMMARY_CHARS") or "9000")
SUM_PARTS = int(os.environ.get("SUMMARY_PARTS") or "8")

MAP_SYS = ("你是中文逐字稿的摘要助手。這是一段長逐字稿的其中一段。\n"
           "用繁體中文列出這一段真正講到的重點，2~4 點，每點一句話。\n"
           "有數字、標的、日期、結論就一定要留下來，那些才是有用的部分。\n"
           "沒有實質內容（開場招呼、業配、閒聊）就回一行「（無重點）」。\n"
           "直接輸出條列，不要開場白。")
FINAL_SYS = ("你是中文逐字稿的摘要助手。用繁體中文寫重點摘要，格式固定為：\n"
             "第一行「**一句話**：」加上一句總結；空一行；接著 4~8 點條列，每點一句話。\n"
             "忠於原文，不要加入原文沒有的資訊；重複的合併，沒有實質內容的略過。\n"
             "有數字、標的、日期、結論要保留。直接輸出，不要開場白。")


def _groq_chat(sys_msg, user_msg, temperature=0.3, timeout=120, tries=4):
    """呼叫 Groq 對話模型。壅塞就等一下再試，失敗回空字串。

    429／5xx 都是「等一下就好」的錯，不重試等於整篇摘要白白掉了；
    伺服器有給 Retry-After 就聽它的，這比自己亂猜等多久準。
    """
    if not GROQ_API_KEY:
        return ""
    body = json.dumps({
        "model": GROQ_LLM_MODEL, "temperature": temperature,
        "messages": [{"role": "system", "content": sys_msg},
                     {"role": "user", "content": user_msg}],
    }).encode("utf-8")
    for n in range(1, tries + 1):
        try:
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions", data=body,
                headers={"Authorization": "Bearer " + GROQ_API_KEY,
                         "Content-Type": "application/json",
                         "User-Agent": "transcript-tool/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            return (j.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 520, 524) or n == tries:
                # 404 幾乎都是模型被下架，不是網路問題。把模型名印出來，
                # 不然只看到「HTTP 404」會以為是暫時性錯誤，繼續等下一批。
                print("    摘要這段失敗：HTTP %s%s"
                      % (e.code, "（Groq 找不到模型 %s，多半已停用）" % GROQ_LLM_MODEL
                         if e.code == 404 else ""), flush=True)
                return ""
            wait = float(e.headers.get("Retry-After") or 0) or min(30, 3 * n)
        except Exception as e:
            if n == tries:
                print("    摘要這段失敗：%s" % str(e).replace("\n", " ")[:90], flush=True)
                return ""
            wait = 3 * n
        time.sleep(wait)
    return ""


def _chunks(text, size):
    """照段落切塊，不要切在句子中間。"""
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) > size:
            out.append(cur); cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


def groq_summary(title, text):
    """用 Groq LLM 產生繁中重點摘要。任何失敗都回空字串，不影響逐字稿產出。

    長片要先分段各摘一次，再把段摘要合成一份。以前是直接截前一萬字送出去——
    一支一小時的節目有三、四萬字，等於只摘到開場寒暄，後面真正在講的全沒讀到，
    而且看摘要的人完全不會發現漏了。
    """
    if not (GROQ_API_KEY and GROQ_SUMMARY):
        return ""
    text = (text or "").strip()
    if not text:
        return ""

    parts = _chunks(text, SUM_SEG)
    if len(parts) > SUM_PARTS:                     # 超長節目：加大每段，不要放棄後半
        parts = _chunks(text, -(-len(text) // SUM_PARTS))
    if len(parts) == 1:
        s = _groq_chat(FINAL_SYS, "影片標題：%s\n\n逐字稿：\n%s" % (title, text))
        if s:
            print("    已產生摘要（%s）" % GROQ_LLM_MODEL, flush=True)
        return s

    print("    摘要：全文 %d 字，分 %d 段讀" % (len(text), len(parts)), flush=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        got = list(ex.map(lambda a: _groq_chat(
            MAP_SYS, "影片標題：%s\n\n第 %d/%d 段：\n%s" % (title, a[0], len(parts), a[1])),
            list(enumerate(parts, 1))))
    notes = [g for g in got if g and "（無重點）" not in g]
    if not notes:
        print("    摘要失敗（略過）：每一段都沒讀到", flush=True)
        return ""

    merged = _groq_chat(FINAL_SYS, "影片標題：%s\n\n以下是各段整理出來的重點，"
                        "請合併成一份完整摘要：\n%s" % (title, "\n".join(notes)))
    if merged:
        print("    已產生摘要（%s，讀完 %d/%d 段）"
              % (GROQ_LLM_MODEL, len(notes), len(parts)), flush=True)
        return merged
    # 合併那一次掛掉就把各段重點接起來，資訊還在，只是沒整理過
    print("    摘要合併失敗，改用各段重點", flush=True)
    return "\n".join(notes)


def write_outputs(n, vid, title, lang, cues, up=""):
    paras = to_paragraphs(cues)
    chars = sum(len(p["t"]) for p in paras)
    # 影片 ID 用 __yt 夾在檔名裡：yt2deck／transcribe_upload、前端的 vidOf、
    # classify 去尾巴全都認這個標記。這裡以前只用底線，同一支影片經手機和
    # 產線各做一次就會產生兩個檔名不同、誰也認不出誰的重複檔。
    stem = f"{safe_name(title)}__yt{vid}"
    link = f"https://youtu.be/{vid}"
    summary = groq_summary(title, "\n".join(p["t"] for p in paras))

    md = [f"# {title}", "", f"- 影片：{link}", f"- 字幕：{lang}",
          f"- 統計：{chars} 字 / {len(paras)} 段"]
    if up:
        md.append(f"- 日期：{up}")     # 上片日期，網站列表要顯示
    md.append("")
    if summary:
        md += ["## 摘要", "", summary, ""]
    md += ["---", ""]
    for p in paras:
        md.append(f"**[{hhmmss(p['s'])}]({link}?t={int(p['s'])})** {p['t']}")
    (OUT / f"{stem}.md").write_text("\n\n".join(md), encoding="utf-8")

    txt = title + "\n" + link + "\n\n"
    if summary:
        txt += "【摘要】\n" + summary + "\n\n【逐字稿】\n\n"
    txt += "\n\n".join(p["t"] for p in paras) + "\n"
    (OUT / f"{stem}.txt").write_text(txt, encoding="utf-8")

    srt = []
    for i, c in enumerate(cues, 1):
        srt.append(f"{i}\n{srt_time(c['start'])} --> "
                   f"{srt_time(c['start'] + c.get('duration', 0))}\n{c['text']}\n")
    (OUT / f"{stem}.srt").write_text("\n".join(srt), encoding="utf-8")
    return chars, len(paras)


def main():
    global COOKIEFILE, PROXY, PLAYER_CLIENTS
    args = sys.argv[1:]
    ids = parse_ids(" ".join(args) if args else os.environ.get("IDS", ""))
    print(f"待處理 {len(ids)} 支影片"
          + ("（有 cookies）" if COOKIEFILE else "")
          + ("（有代理）" if PROXY else ""), flush=True)
    index, ok, fail = [], 0, 0
    sweeps = 0            # 已經整輪掃過幾支還是沒找到可用客戶端
    for n, vid in enumerate(ids, 1):
        print(f"[{n}/{len(ids)}] {vid}", flush=True)
        # 不要寫死分隔符：庫裡同時有 __yt<ID> 和 _<ID> 兩種舊檔名，
        # 綁死一種就會把另一種當成「還沒抓過」而重抓。11 碼 ID 夠獨特。
        if SKIP_DIR and glob.glob(os.path.join(SKIP_DIR, f"*{vid}.md")):
            print("  已存在，略過（先前已產出）", flush=True)
            index.append({"video_id": vid, "title": "", "lang": "",
                          "chars": 0, "paragraphs": 0, "status": "skip"})
            continue
        try:
            try:
                title, lang, cues, up = get_transcript(vid)
            except Exception as e1:   # YouTube 對機房 IP 偶發刁難（格式/5xx）→ 等一下重試一次
                m1 = str(e1)
                if not ("format is not available" in m1 or "HTTP Error 5" in m1
                        or "Connection" in m1 or "cookie" in m1.lower()
                        or "label empty or too long" in m1 or "proxy" in m1.lower()):
                    raise
                got = False
                # 先換播放器客戶端，cookies 留著。找到能用的就記下來，這一輪剩下的
                # 影片直接用它——不然三十支各試七個客戶端，光試就跑掉十幾分鐘。
                # 連續幾支都掃不出可用客戶端，就代表這批整個不行（cookie 過期、
                # 這個 IP 被盯上），別再對剩下的每一支重掃一次。
                if COOKIEFILE and "format is not available" in m1 and sweeps < MAX_SWEEPS:
                    sweeps += 1
                    for c in CLIENT_TRIES:
                        if [c] == PLAYER_CLIENTS:
                            continue
                        print("  拿不到音訊格式，換客戶端 %s 再試（cookies 留著）…"
                              % c, flush=True)
                        was, PLAYER_CLIENTS = PLAYER_CLIENTS, [c]
                        try:
                            title, lang, cues, up = get_transcript(vid)
                        except Exception as e2:
                            PLAYER_CLIENTS = was
                            if "not a bot" in str(e2) or "Sign in to confirm" in str(e2):
                                break          # 被擋就別再換了，換幾次都一樣
                            continue
                        got = True             # PLAYER_CLIENTS 就留在 [c]，後面沿用
                        sweeps = 0             # 找到了，額度歸零
                        print("  ✅ 客戶端 %s 可以用，這一輪接下來都用它" % c, flush=True)
                        break
                    if not got and sweeps >= MAX_SWEEPS:
                        print("  掃過所有客戶端都拿不到格式，這一批不再重掃", flush=True)
                if not got:
                    # 半失效的 cookie（瀏覽器輪換後）會讓 YouTube 回空格式清單；
                    # 這支影片改走無 cookie + PO-token 重試，下一支恢復先用 cookie。
                    saved_cookie, saved_proxy = COOKIEFILE, PROXY
                    dropped = [n for n, v in (("cookie", COOKIEFILE), ("代理", PROXY)) if v]
                    if dropped:
                        print("  暫時性失敗（%s），12 秒後改用無 %s 重試…"
                              % (m1.replace("\n", " ")[:90], "／".join(dropped)), flush=True)
                        COOKIEFILE = ""
                        PROXY = ""
                    else:
                        print("  暫時性失敗（%s），12 秒後重試一次…"
                              % m1.replace("\n", " ")[:90], flush=True)
                    time.sleep(12)
                    try:
                        title, lang, cues, up = get_transcript(vid)
                    finally:
                        COOKIEFILE, PROXY = saved_cookie, saved_proxy
            chars, npara = write_outputs(n, vid, title, lang, cues, up)
            print(f"  完成：{chars} 字 / {npara} 段 / {lang}", flush=True)
            index.append({"video_id": vid, "title": title, "lang": lang,
                          "chars": chars, "paragraphs": npara, "status": "ok"})
            ok += 1
            del cues
        except Exception as e:
            msg = str(e).replace("\n", " ")
            if "Sign in to confirm" in msg or "not a bot" in msg:
                # 只有「下載音檔」會被擋，查影片資訊和抓字幕都過得去——所以這個錯誤
                # 一定代表「這支沒有字幕、需要語音辨識」。原本的訊息只說「請在本機
                # 執行」，但真正該做的是手機那條路（住宅 IP），講清楚省得每次重猜。
                # cookies 已經在用了還是被擋，就別再叫人去補 cookies——實測 22 筆
                # 有效 cookies ＋ 七個播放器客戶端，機房 IP 一個音訊網址都拿不到。
                # 那時候唯一有用的建議只有手機那條路，其他都是浪費時間。
                nudge = ("     → 或補一份有效的 YT_COOKIES（Netscape cookies.txt）。\n"
                         if not COOKIEFILE else
                         "     → YT_COOKIES 已經在用了（這次也帶著跑），但機房 IP 連帶著\n"
                         "        登入態都拿不到音訊網址，所以補 cookies 不會有幫助。\n")
                msg = ("這支沒有字幕，要下載音檔做語音辨識，但 YouTube 擋掉 GitHub 機房 IP"
                       "（要求登入確認不是機器人）。\n"
                       "     → 用手機的 a-Shell 跑 `python3 yt2deck.py %s`，"
                       "住宅 IP 幾乎都過得去，抓完會自動上傳。\n"
                       "%s"
                       "     → 剛發布的影片可能只是自動字幕還沒生好，產線每 3 小時會再試。"
                       % (vid, nudge))
            elif "format is not available" in msg:
                msg = ("影片目前沒有可下載的內容——多半是直播中或直播剛結束仍在處理"
                       "（也可能是會員／地區限制）。等影片頁顯示片長（非 LIVE）後再送一次。")
            print(f"  失敗：{msg}", flush=True)
            index.append({"video_id": vid, "title": "", "lang": "",
                          "chars": 0, "paragraphs": 0, "status": f"fail:{msg}"})
            fail += 1
        time.sleep(1.2)

    with (OUT / "_index.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["video_id", "title", "lang", "chars", "paragraphs", "status"])
        w.writeheader(); w.writerows(index)
    skipped = sum(1 for x in index if x.get("status") == "skip")
    print(f"完成：成功 {ok}，失敗 {fail}，略過 {skipped}（先前已產出）", flush=True)
    if COOKIE_NOTE:
        note = COOKIE_NOTE
    elif COOKIEFILE:
        note = "✅ 用 YT_COOKIES_FILE：" + COOKIEFILE
    else:
        note = "沒有設定 YT_COOKIES（沒字幕的影片就會被擋）"
    print("cookies：" + note, flush=True)
    # 一支都沒成功、卻有失敗的＝這批整個壞了（cookie 壞掉、被擋、網路不通…）。
    # 以前這裡永遠回 0，工作流就永遠是綠的，壞了幾天也沒人知道。
    # 部分成功仍回 0：那些成功的還是要發佈出去。
    #
    # 但「略過 59＋失敗 1」不是整批壞掉，只是清單裡有一支難搞的（直播、會員限定…）。
    # 那種情況每 3 小時紅一次、寄一封信，只會讓人開始無視所有警報。
    # 有略過的＝產線本來就在動，失敗就當個別影片的事。
    return 1 if (ok == 0 and fail > 0 and skipped == 0) else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
