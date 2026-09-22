# -*- coding: utf-8 -*-
"""Podcast：從 RSS 抓單集音檔，丟進 audio-inbox/ 交給既有的轉錄產線。

為什麼 Podcast 比 YouTube 好處理：
    Podcast 的發布機制就是 RSS，每一集的 <enclosure> 裡直接寫著 mp3 的網址，
    檔案放在一般的 CDN 上。**沒有機器人驗證、不會擋機房 IP、不需要 cookies。**
    YouTube 那邊折騰了很久的問題，在這裡一個都不存在——抓下來就是抓下來。

怎麼認網址：
    直接給 RSS 網址      → 就用它
    Apple Podcasts 連結  → 用 iTunes Lookup API（免費、免金鑰）換成 RSS
    Spotify 連結         → 沒辦法。Spotify 不公開音檔網址，只能在它的 App 裡聽。
                           這件事要講明白，不然使用者會以為是程式壞了而一直重試。
    單集的 mp3 網址      → 直接下載

    python scripts/podcast.py search "財經"        找節目（回 RSS 網址）
    python scripts/podcast.py list <網址>          列出最近幾集
    python scripts/podcast.py fetch                照 podcast.json 抓新集數
    python scripts/podcast.py selftest             離線自我測試
"""
import os, re, sys, json, html, time
import urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

AUDIO_DIR = os.environ.get("AUDIO_INBOX", "audio-inbox")
WANT_FILE = os.environ.get("PODCAST_FILE", "podcast.json")
STATE_FILE = os.environ.get("PODCAST_STATE", ".podcast-state.json")
# 一輪最多抓幾集。抓太多的話這一輪全花在下載上，轉錄那邊一集都輪不到，
# 而且每 2 小時跑一次，慢慢消化本來就追得上。
MAX_PER_RUN = int(os.environ.get("PODCAST_MAX", "6") or "6")
# 單集上限。Podcast 很少超過這個大小；真的超過多半是抓到整季的合輯，
# 那種東西轉錄要好幾小時，寧可略過也不要卡住整條產線。
MAX_MB = float(os.environ.get("PODCAST_MAX_MB", "500") or "500")

UA = "Mozilla/5.0 (compatible; transcript-tool/1.0)"
AUDIO_TYPES = ("audio/", "video/mp4", "video/x-m4a")
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0/"
ATOM_NS = "http://www.w3.org/2005/Atom"
MEDIA_NS = "http://search.yahoo.com/mrss/"


class PodcastError(RuntimeError):
    pass


def http(url, timeout=60, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


# ---------- 認網址 ----------

APPLE = re.compile(r"podcasts?\.apple\.com/.*?/id(\d+)", re.I)
SPOTIFY = re.compile(r"open\.spotify\.com/(show|episode)/", re.I)
AUDIO_URL = re.compile(r"\.(mp3|m4a|aac|ogg|opus|wav|flac)(\?|#|$)", re.I)


def resolve(url):
    """把使用者給的任何網址換成 RSS 網址。回 (種類, 值)。

    種類：'feed' RSS 網址／'audio' 單集音檔網址。
    """
    u = (url or "").strip()
    if not u:
        raise PodcastError("沒有給網址")
    if SPOTIFY.search(u):
        raise PodcastError(
            "Spotify 的節目沒辦法抓。它不公開音檔網址，只能在 Spotify 自己的 App 裡聽。"
            "同一個節目通常在 Apple Podcasts 或原本的 RSS 也找得到，改貼那個連結就可以。")
    m = APPLE.search(u)
    if m:
        return "feed", apple_feed(m.group(1))
    if AUDIO_URL.search(u):
        return "audio", u
    if not u.lower().startswith(("http://", "https://")):
        raise PodcastError("看不懂這個網址：%s" % u[:80])
    return "feed", u


def apple_feed(pid):
    """Apple Podcasts 的節目 ID → RSS 網址。用官方的 Lookup API，免費免金鑰。"""
    try:
        raw = http("https://itunes.apple.com/lookup?id=%s&entity=podcast" % pid)
        j = json.loads(raw)
    except Exception as e:
        raise PodcastError("問不到這個 Apple Podcasts 節目（%s）" % str(e)[:60])
    for r in j.get("results", []):
        if r.get("feedUrl"):
            return r["feedUrl"]
    raise PodcastError("這個 Apple Podcasts 節目沒有公開 RSS（可能是 Apple 獨家）")


def search(term, limit=8, country="TW"):
    """用節目名字找 RSS。iTunes 的搜尋 API 免費免金鑰，涵蓋台灣的節目。"""
    q = urllib.parse.urlencode({"term": term, "media": "podcast",
                                "country": country, "limit": limit})
    try:
        j = json.loads(http("https://itunes.apple.com/search?" + q))
    except Exception as e:
        raise PodcastError("搜尋失敗（%s）" % str(e)[:60])
    out = []
    for r in j.get("results", []):
        if r.get("feedUrl"):
            out.append({"name": r.get("collectionName", ""),
                        "author": r.get("artistName", ""),
                        "feed": r["feedUrl"],
                        "count": r.get("trackCount") or 0,
                        "art": r.get("artworkUrl100", "")})
    return out


# ---------- 解析 RSS ----------

def _text(el, *paths):
    for p in paths:
        v = el.findtext(p)
        if v and v.strip():
            return html.unescape(v.strip())
    return ""


def _enclosure(item):
    """找出這一集的音檔網址。

    三種寫法都要認：標準的 <enclosure>、Atom 的 <link rel="enclosure">、
    以及有些平台用的 <media:content>。只認一種的話，隨便換個代管商就抓不到了，
    而且錯誤訊息會是「這個節目沒有任何集數」，完全看不出是解析的問題。
    """
    for e in item.findall("enclosure"):
        u = (e.get("url") or "").strip()
        t = (e.get("type") or "").lower()
        if u and (not t or t.startswith(AUDIO_TYPES) or AUDIO_URL.search(u)):
            return u, int(e.get("length") or 0)
    for e in item.findall("{%s}link" % ATOM_NS) + item.findall("link"):
        if (e.get("rel") or "") == "enclosure" and (e.get("href") or "").strip():
            return e.get("href").strip(), int(e.get("length") or 0)
    for e in item.findall("{%s}content" % MEDIA_NS):
        u = (e.get("url") or "").strip()
        if u and ((e.get("type") or "").startswith(AUDIO_TYPES) or AUDIO_URL.search(u)):
            return u, int(e.get("length") or 0)
    return "", 0


def _when(item):
    """發布時間 → YYYY-MM-DD。解析不出來就回空字串，不要亂猜一個日期。"""
    raw = _text(item, "pubDate") or _text(item, "{%s}published" % ATOM_NS) \
        or _text(item, "{%s}updated" % ATOM_NS)
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:
        pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)     # ISO 格式的
    return m.group(0) if m else ""


def episodes(xml_text, limit=50):
    """RSS 文字 → (節目名稱, [單集...])，新的在前。"""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        raise PodcastError("這個網址回來的不是 RSS（%s）" % str(e)[:60])
    # 網頁本身常常也是合法的 XML，所以「解析成功」不代表拿到的是 RSS。
    # 不擋的話會一路走到底回「這個節目沒有任何集數」——而真正的問題是
    # 使用者貼了節目的網頁而不是 RSS 網址，照那個訊息去查永遠查不到。
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag not in ("rss", "feed", "rdf"):
        raise PodcastError("這個網址回來的是網頁（<%s>），不是 RSS。"
                           "請找節目頁面上的 RSS 連結，或改貼 Apple Podcasts 的連結。"
                           % tag)
    ch = root.find("channel")
    if ch is None:
        # Atom：整個 feed 就是 root，每一集是 <entry>
        ch, items = root, root.findall("{%s}entry" % ATOM_NS)
    else:
        items = ch.findall("item")
    name = _text(ch, "title", "{%s}title" % ATOM_NS)
    out = []
    for it in items[:limit]:
        url, size = _enclosure(it)
        if not url:
            continue                                   # 純文字的貼文，不是音檔
        title = _text(it, "title", "{%s}title" % ATOM_NS) or "未命名單集"
        gid = _text(it, "guid", "{%s}id" % ATOM_NS) or url
        out.append({"guid": gid, "title": title, "url": url, "size": size,
                    "date": _when(it),
                    "dur": _text(it, "{%s}duration" % ITUNES_NS)})
    return name, out


def feed_episodes(url, limit=50):
    try:
        raw = http(url, timeout=90)
    except Exception as e:
        raise PodcastError("抓不到這個 RSS（%s）" % str(e)[:80])
    return episodes(raw, limit)


# ---------- 下載 ----------

def safe(s, dflt="podcast"):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", str(s or "")).strip()
    return (s or dflt)[:60]


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def download(ep, show="", lang=""):
    """把一集抓進 audio-inbox/。回本機路徑。

    檔名要帶日期與節目名，因為轉錄完之後只剩下檔名可以認人。
    語言標記 __L<code> 跟手機上傳走同一套，台語的節目才能走對引擎。
    """
    url = ep["url"]
    ext = (re.search(r"\.([A-Za-z0-9]{2,4})(?:\?|#|$)", url) or [None, "mp3"])[1].lower()
    if ext not in ("mp3", "m4a", "aac", "ogg", "opus", "wav", "flac", "mp4"):
        ext = "mp3"
    base = "_".join(x for x in (ep.get("date", ""), safe(show, ""), safe(ep["title"])) if x)
    name = "%s_%s%s.%s" % (stamp(), safe(base), ("__L" + lang) if lang else "", ext)
    os.makedirs(AUDIO_DIR, exist_ok=True)
    dest = os.path.join(AUDIO_DIR, name)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    n = 0
    # 邊下載邊寫檔：一集兩小時的節目上百 MB，整份讀進記憶體沒必要，
    # 而且中途失敗連一個位元組都留不下來。
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        cl = int(r.headers.get("Content-Length") or 0)
        if cl and cl > MAX_MB * 1048576:
            raise PodcastError("這一集 %.0fMB，超過上限 %.0fMB" % (cl / 1048576.0, MAX_MB))
        while True:
            buf = r.read(1 << 20)
            if not buf:
                break
            f.write(buf); n += len(buf)
            if n > MAX_MB * 1048576:
                f.close(); os.remove(dest)
                raise PodcastError("這一集超過 %.0fMB，中途停掉" % MAX_MB)
    if n < 1024:
        os.remove(dest)
        raise PodcastError("下載回來只有 %d 位元組，不是音檔" % n)
    return dest, n


# ---------- 照設定抓 ----------

def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def fetch():
    """讀 podcast.json，把還沒處理過的新集數抓進 audio-inbox/。

    抓下來的先記在 pending，**不算 done**。要等轉錄真的成功了（commit()）
    才升級成 done。中間差一步很重要：抓完就記 done 的話，轉錄失敗時檔案隨著
    容器消失、狀態卻寫著「做完了」，那一集就永遠不會再抓——而且畫面上什麼
    都看不到。Podcast 的 mp3 放在一般 CDN，重抓幾乎沒有成本，寧可重抓。
    """
    want = load(WANT_FILE, {})
    state = load(STATE_FILE, {})
    done = set(state.get("done") or [])
    pending = {}
    shows = want.get("shows") or []
    once = want.get("once") or []
    if not shows and not once:
        print("podcast.json 裡沒有訂閱，也沒有待抓的單集"); return 0

    got, fails = 0, []

    # 1. 使用者一次性貼進來的連結（單集音檔，或「這個節目抓最新幾集」）
    for job in list(once):
        if got >= MAX_PER_RUN:
            break
        u = job.get("url") if isinstance(job, dict) else job
        lang = (job.get("lang") or "") if isinstance(job, dict) else ""
        n_want = int(job.get("n") or 1) if isinstance(job, dict) else 1
        try:
            kind, target = resolve(u)
            if kind == "audio":
                ep = {"guid": target, "title": job.get("title") if isinstance(job, dict) else "",
                      "url": target, "date": "", "size": 0}
                ep["title"] = ep["title"] or os.path.basename(
                    urllib.parse.urlparse(target).path) or "podcast"
                if ep["guid"] in done:
                    print("  已經抓過：%s" % ep["title"][:40]); once.remove(job); continue
                dest, n = download(ep, "", lang)
                print("  ⬇ %s（%.1fMB）" % (os.path.basename(dest), n / 1048576.0), flush=True)
                pending[ep["guid"]] = {"path": dest, "once": job}; got += 1
            else:
                show, eps = feed_episodes(target)
                fresh = [e for e in eps if e["guid"] not in done and e["guid"] not in pending][:n_want]
                if not fresh:
                    print("  《%s》沒有新的集數" % show)
                for e in fresh:
                    if got >= MAX_PER_RUN:
                        break
                    dest, n = download(e, show, lang)
                    print("  ⬇ %s（%.1fMB）" % (os.path.basename(dest), n / 1048576.0), flush=True)
                    pending[e["guid"]] = {"path": dest, "once": job}; got += 1
            once.remove(job)
        except PodcastError as e:
            fails.append("%s：%s" % (str(u)[:50], e))
        except Exception as e:
            fails.append("%s：%s" % (str(u)[:50], str(e)[:80]))

    # 2. 訂閱的節目：每次只抓「上次之後新出的」
    for sh in shows:
        if got >= MAX_PER_RUN:
            print("這一輪抓夠了（%d 集），其餘留到下一輪" % got); break
        url, lang = sh.get("feed") or "", sh.get("lang") or ""
        if not url:
            continue
        try:
            kind, target = resolve(url)
            show, eps = feed_episodes(target)
        except PodcastError as e:
            fails.append("%s：%s" % ((sh.get("name") or url)[:40], e)); continue
        except Exception as e:
            fails.append("%s：%s" % ((sh.get("name") or url)[:40], str(e)[:80])); continue
        sh["name"] = sh.get("name") or show
        first = not sh.get("seen")
        fresh = [e for e in eps if e["guid"] not in done]
        if first:
            # 第一次訂閱只抓 want 集，其餘登記成「看過了」。
            # 不這樣做的話，訂一個播了三年的節目會一口氣灌進三百集。
            n_want = int(sh.get("want") or 1)
            take, skip = fresh[:n_want], fresh[n_want:]
            for e in skip:
                done.add(e["guid"])
            if skip:
                print("  《%s》第一次訂閱：抓最新 %d 集，其餘 %d 集登記為已看過"
                      % (show, len(take), len(skip)))
            fresh = take
            sh["seen"] = True
        for e in fresh:
            if got >= MAX_PER_RUN:
                break
            try:
                dest, n = download(e, show, lang)
                print("  ⬇ %s（%.1fMB）" % (os.path.basename(dest), n / 1048576.0), flush=True)
                pending[e["guid"]] = {"path": dest}; got += 1
            except Exception as e2:
                fails.append("%s：%s" % (e["title"][:40], str(e2)[:80]))

    state["done"] = sorted(done)
    state["pending"] = pending
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    want["once"] = once
    with open(WANT_FILE, "w", encoding="utf-8") as f:
        json.dump(want, f, ensure_ascii=False, indent=1)

    for m in fails:
        print("  ✗ %s" % m, flush=True)
    print("這一輪抓了 %d 集%s" % (got, ("，失敗 %d" % len(fails)) if fails else ""), flush=True)
    return 0


def commit(processed_file="out/_processed.txt"):
    """轉錄跑完之後呼叫：真的轉成功的才升級成 done。

    沒轉成功的從 pending 丟掉——檔案已經隨容器消失，但狀態沒留下記錄，
    下一輪自然會重抓。一次性的連結（once）也放回去，不然使用者按了一次、
    東西沒出來、清單上卻已經沒有那一筆，完全不知道發生什麼事。
    """
    state = load(STATE_FILE, {})
    pending = state.get("pending") or {}
    if not pending:
        print("沒有待確認的 Podcast 集數"); return 0
    try:
        with open(processed_file, encoding="utf-8") as f:
            ok_paths = set(x.strip() for x in f if x.strip())
    except OSError:
        ok_paths = set()
    done = set(state.get("done") or [])
    want = load(WANT_FILE, {})
    once = want.get("once") or []
    good, back = 0, 0
    for gid, info in pending.items():
        path = info.get("path") if isinstance(info, dict) else info
        if path in ok_paths:
            done.add(gid); good += 1
        else:
            job = info.get("once") if isinstance(info, dict) else None
            if job and job not in once:
                once.append(job); back += 1
    state["done"] = sorted(done)
    state["pending"] = {}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    if back:
        want["once"] = once
        with open(WANT_FILE, "w", encoding="utf-8") as f:
            json.dump(want, f, ensure_ascii=False, indent=1)
    miss = len(pending) - good
    print("Podcast：%d 集轉錄成功已記錄%s"
          % (good, ("，%d 集沒成功，下一輪會重抓" % miss) if miss else ""), flush=True)
    return 0


# ---------- 自我測試（不連網） ----------

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0/">
<channel>
  <title>股市&amp;人生</title>
  <item>
    <title>第 12 集：台積電怎麼看</title>
    <pubDate>Mon, 15 Sep 2026 08:00:00 +0800</pubDate>
    <guid isPermaLink="false">ep-12</guid>
    <itunes:duration>52:13</itunes:duration>
    <enclosure url="https://cdn.example.com/ep12.mp3" type="audio/mpeg" length="41234567"/>
  </item>
  <item>
    <title>第 11 集</title>
    <pubDate>Mon, 08 Sep 2026 08:00:00 +0800</pubDate>
    <enclosure url="https://cdn.example.com/ep11.m4a?token=abc" type="audio/x-m4a"/>
  </item>
  <item>
    <title>只是一篇公告，沒有音檔</title>
    <pubDate>Mon, 01 Sep 2026 08:00:00 +0800</pubDate>
  </item>
</channel></rss>"""

MEDIA_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>另一家代管商</title>
  <item><title>用 media:content 的</title>
    <pubDate>2026-09-20</pubDate>
    <media:content url="https://x.test/a.mp3" type="audio/mpeg"/>
  </item>
</channel></rss>"""

ATOM_SAMPLE = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 格式的節目</title>
  <entry><title>Atom 單集</title>
    <id>atom-1</id>
    <published>2026-09-19T10:00:00Z</published>
    <link rel="enclosure" type="audio/mpeg" href="https://x.test/atom1.mp3"/>
  </entry>
</feed>"""


def selftest():
    bad = [0]

    def ck(cond, label, extra=""):
        if cond:
            print("  ✓ " + label)
        else:
            bad[0] += 1; print("  ✗ " + label + ("  " + str(extra) if extra else ""))

    name, eps = episodes(RSS_SAMPLE)
    ck(name == "股市&人生", "節目名稱有還原 HTML 跳脫", name)
    ck(len(eps) == 2, "沒有音檔的那則不算一集", len(eps))
    ck(eps[0]["guid"] == "ep-12", "讀到 guid", eps[0]["guid"])
    ck(eps[0]["date"] == "2026-09-15", "RFC822 日期", eps[0]["date"])
    ck(eps[0]["dur"] == "52:13", "itunes:duration", eps[0]["dur"])
    ck(eps[0]["size"] == 41234567, "檔案大小", eps[0]["size"])
    ck(eps[1]["guid"] == eps[1]["url"], "沒有 guid 就拿網址當 guid")

    n2, e2 = episodes(MEDIA_SAMPLE)
    ck(len(e2) == 1 and e2[0]["url"].endswith("a.mp3"), "認得 media:content", e2)
    ck(e2[0]["date"] == "2026-09-20", "ISO 日期也要吃得下", e2[0]["date"])

    n3, e3 = episodes(ATOM_SAMPLE)
    ck(n3 == "Atom 格式的節目" and len(e3) == 1, "Atom 格式", (n3, len(e3)))
    ck(e3[0]["guid"] == "atom-1" and e3[0]["date"] == "2026-09-19", "Atom 的 id 與日期", e3[0])

    try:
        episodes("<html><body>這不是 RSS</body></html>")
        ck(False, "不是 RSS 要報錯")
    except PodcastError:
        ck(True, "不是 RSS 要報錯")

    try:
        resolve("https://open.spotify.com/show/abc123")
        ck(False, "Spotify 要明確拒絕")
    except PodcastError as e:
        ck("Spotify" in str(e), "Spotify 要明確拒絕並說明原因", e)

    ck(resolve("https://cdn.x.test/ep.mp3")[0] == "audio", "單集 mp3 網址")
    ck(resolve("https://feeds.x.test/rss")[0] == "feed", "一般 RSS 網址")
    try:
        resolve("這不是網址")
        ck(False, "亂打的字串要報錯")
    except PodcastError:
        ck(True, "亂打的字串要報錯")

    ck(safe("a/b:c*d?e") == "a_b_c_d_e", "檔名清乾淨", safe("a/b:c*d?e"))

    print("\n%s（%d 個沒過）" % ("全部通過 ✅" if not bad[0] else "有問題 ❌", bad[0]))
    return 1 if bad[0] else 0


def main():
    a = sys.argv[1:]
    if not a or a[0] == "selftest":
        return selftest()
    if a[0] == "fetch":
        return fetch()
    if a[0] == "commit":
        return commit(a[1] if len(a) > 1 else "out/_processed.txt")
    if a[0] == "search" and len(a) > 1:
        for r in search(" ".join(a[1:])):
            print("・%s（%s）%s 集\n    %s" % (r["name"], r["author"], r["count"], r["feed"]))
        return 0
    if a[0] == "list" and len(a) > 1:
        kind, target = resolve(a[1])
        if kind == "audio":
            print("這是單集音檔：" + target); return 0
        show, eps = feed_episodes(target)
        print("《%s》最近 %d 集：" % (show, len(eps)))
        for e in eps[:20]:
            print("  %s  %s  %s" % (e["date"] or "????-??-??", (e["dur"] or "").rjust(8),
                                    e["title"][:50]))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
