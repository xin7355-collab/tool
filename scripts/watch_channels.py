# -*- coding: utf-8 -*-
"""每天檢查訂閱的頻道有沒有新片，有就排進逐字稿產線。

為什麼用 RSS 而不是 YouTube API 或 yt-dlp：頻道的 feeds/videos.xml 不用金鑰、
沒有配額、也不會被機器人驗證擋下來，回最新約 15 支。對「一天看一次有沒有更新」
這件事綽綽有餘，而且是這三種做法裡最不會壞的一種。

狀態寫在 .watch-state.json：
  channels[頻道ID].seen  已經處理過的影片，避免重複排隊
  pending               最近幾天發現、但還沒轉出逐字稿的影片

每次檢查都用 pending 重寫 queue/watch.txt——不是往後追加。追加的話佇列會無限
長大（產線每 3 小時會把整個 queue/ 重跑一次），而重寫等於自動清掉已完成的，
還順便讓失敗的影片重試好幾天，直播結束轉檔慢的那種就是靠這個補回來。
"""
import json, os, re, sys, time, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SUBS = os.environ.get("SUBS_FILE") or "subs.json"
STATE = os.environ.get("WATCH_STATE") or ".watch-state.json"
QUEUE = os.environ.get("WATCH_QUEUE") or "queue/watch.txt"
HAVE = os.environ.get("HAVE_LIST") or ""      # 已發佈逐字稿的檔名清單（一行一個）

KEEP_DAYS = int(os.environ.get("WATCH_KEEP_DAYS") or "7")
SEEN_CAP = 80                                  # 每個頻道記住最近這麼多支就夠了
MAX_NEW = int(os.environ.get("WATCH_MAX_NEW") or "6")   # 單一頻道一次最多排幾支

NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015"}
CH_ID = re.compile(r"UC[0-9A-Za-z_-]{22}")
# 兩種舊檔名都要認。結尾允許一個引號：`git ls-tree` 預設會把含中文的檔名整個
# 用雙引號括起來（core.quotePath），`\.md$` 就永遠對不上——這裡的檔名幾乎都有中文，
# 等於一支都認不出來，pending 永遠清不掉、網站上「轉錄中」的數字一直不會動。
VID_IN_NAME = re.compile(r"(?:__yt|_)([0-9A-Za-z_-]{11})\.md\"?$")


def now():
    return datetime.now(timezone.utc)


def http(url, tries=3):
    for n in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept-Language": "zh-TW,zh;q=0.9"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            if n == tries:
                raise
        except Exception:
            if n == tries:
                raise
        time.sleep(2 * n)
    return b""


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def resolve(q):
    """把使用者貼的東西變成頻道 ID。貼什麼都吃：網址、@帳號、甚至影片連結。"""
    q = (q or "").strip()
    if not q:
        return ""
    if re.fullmatch(r"UC[0-9A-Za-z_-]{22}", q):
        return q
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", q)
    if m:
        return m.group(1)

    if q.startswith("http"):
        url = q
    elif q.startswith("@"):
        url = "https://www.youtube.com/" + q
    else:
        url = "https://www.youtube.com/@" + q

    # 頻道頁、影片頁都行：兩種頁面的 HTML 裡都有頻道 ID，只是藏的地方不只一種，
    # YouTube 改版時常常只剩其中一種還在，所以全部都試。
    try:
        html = http(url).decode("utf-8", "replace")
    except Exception:
        return ""
    for pat in (r'"channelId"\s*:\s*"(UC[0-9A-Za-z_-]{22})"',
                r'"externalId"\s*:\s*"(UC[0-9A-Za-z_-]{22})"',
                r'itemprop="identifier"\s+content="(UC[0-9A-Za-z_-]{22})"',
                r'youtube\.com/channel/(UC[0-9A-Za-z_-]{22})',
                r'channel_id=(UC[0-9A-Za-z_-]{22})'):
        m = re.search(pat, html)
        if m:
            return m.group(1)

    # 舊式 /user/xxx 帳號：RSS 有另一個入口吃得下，不用頻道 ID
    m = re.search(r"/user/([^/?#]+)", q)
    if m:
        try:
            raw = http("https://www.youtube.com/feeds/videos.xml?user=" + m.group(1))
            mm = CH_ID.search(raw.decode("utf-8", "replace"))
            if mm:
                return mm.group(0)
        except Exception:
            pass
    return ""


def feed(cid):
    """回 (頻道名稱, [{v,t,pub}...])，新的在前。"""
    raw = http("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid)
    if not raw:
        return "", []
    root = ET.fromstring(raw)
    name = (root.findtext("a:title", "", NS) or "").strip()
    out = []
    for e in root.findall("a:entry", NS):
        vid = (e.findtext("yt:videoId", "", NS) or "").strip()
        if not vid:
            continue
        out.append({"v": vid,
                    "t": (e.findtext("a:title", "", NS) or "").strip(),
                    "pub": (e.findtext("a:published", "", NS) or "").strip()})
    return name, out


SHORTS = re.compile(r"#\s*shorts?\b", re.I)


def wanted(ch, title):
    """這支要不要抓。回 (要不要, 為什麼不要)。

    只看得到標題——RSS 沒有影片長度，所以「略過 Shorts」是靠標題的 #shorts 標記。
    抓得到大部分，但不是全部；沒掛標記的短影片還是會進來，這點沒辦法騙人。
    """
    t = title or ""
    if ch.get("skip_shorts", True) and SHORTS.search(t):
        return False, "Shorts"
    only = [w.strip() for w in re.split(r"[,，、]", ch.get("only") or "") if w.strip()]
    if only and not any(w.lower() in t.lower() for w in only):
        return False, "標題沒有指定關鍵字"
    bad = [w.strip() for w in re.split(r"[,，、]", ch.get("not") or "") if w.strip()]
    for w in bad:
        if w.lower() in t.lower():
            return False, "標題含「%s」" % w
    return True, ""


def have_ids():
    """已經有逐字稿的影片 ID。檔名長這樣：標題_XXXXXXXXXXX.md"""
    if not HAVE:
        return set()
    ids = set()
    try:
        with open(HAVE, encoding="utf-8") as f:
            for line in f:
                m = VID_IN_NAME.search(line.strip())
                if m:
                    ids.add(m.group(1))
    except OSError:
        pass
    return ids


def main():
    subs = load(SUBS, {})
    chans = subs.get("channels") or []
    if not chans:
        print("沒有訂閱任何頻道（%s 是空的），這次不用做事" % SUBS)
        return 0

    state = load(STATE, {})
    cstate = state.setdefault("channels", {})
    pending = state.get("pending") or []
    done = have_ids()
    print("已發佈逐字稿 %d 支；訂閱 %d 個頻道" % (len(done), len(chans)), flush=True)

    fresh, subs_dirty = [], False
    for ch in chans:
        if ch.get("off"):
            continue
        cid = (ch.get("id") or "").strip()
        if not cid:
            cid = resolve(ch.get("q"))
            if not cid:
                print("  ✗ 認不出這個頻道：%s" % ch.get("q"), flush=True)
                # 講清楚下一步怎麼做。只說「認不出」的話，使用者只能一直重貼同一個東西
                ch["err"] = ("認不出這個頻道。改貼那個頻道的「任何一支影片」網址最保險，"
                             "或到頻道頁按分享複製連結。")
                subs_dirty = True
                continue
            ch["id"] = cid
            subs_dirty = True

        try:
            name, items = feed(cid)
        except Exception as e:
            print("  ✗ %s 讀取失敗：%s" % (ch.get("name") or cid, str(e)[:80]), flush=True)
            continue
        if name and ch.get("name") != name:
            ch["name"] = name
            subs_dirty = True
        if ch.pop("err", None) is not None:
            subs_dirty = True

        st = cstate.setdefault(cid, {})
        seen = set(st.get("seen") or [])
        st["checked"] = now().isoformat(timespec="seconds")

        # 被過濾掉的一樣記成看過，不然每天都要重新判斷一次同一支
        ok = [x for x in items if wanted(ch, x["t"])[0]]
        skipped = len(items) - len(ok)

        if not seen:
            # 第一次看這個頻道：把現有的影片當成「舊的」，不然一加入就抓十幾支。
            # backfill 是使用者自己選的「順便抓最新幾支」，只有這時候會用到。
            n = int(ch.get("backfill") or 0)
            take = [x for x in ok[:n] if x["v"] not in done]
            st["seen"] = [x["v"] for x in items][:SEEN_CAP]
            for x in take:
                fresh.append({"v": x["v"], "t": x["t"], "c": name or cid,
                              "d": now().date().isoformat()})
            print("  ＋ %s：第一次追蹤，記住現有 %d 支%s%s"
                  % (name or cid, len(items),
                     ("，順便抓最新 %d 支" % len(take)) if take else "",
                     ("，過濾掉 %d 支" % skipped) if skipped else ""), flush=True)
            continue

        new = [x for x in ok if x["v"] not in seen and x["v"] not in done]
        new = list(reversed(new))[-MAX_NEW:] if len(new) > MAX_NEW else list(reversed(new))
        for x in new:
            fresh.append({"v": x["v"], "t": x["t"], "c": name or cid,
                          "d": now().date().isoformat()})
        st["seen"] = ([x["v"] for x in items] + list(st.get("seen") or []))[:SEEN_CAP]
        st["last"] = [{"v": x["v"], "t": x["t"]} for x in new[-3:]] or st.get("last") or []
        print("  ・%s：新片 %d 支%s"
              % (name or cid, len(new),
                 ("（另過濾掉 %d 支）" % skipped) if skipped else ""), flush=True)

    # pending＝還沒轉出逐字稿的。太舊的放棄（多半是被鎖或已下架，一直重試只是浪費）
    cut = (now() - timedelta(days=KEEP_DAYS)).date().isoformat()
    keep, seen_v = [], set()
    for it in pending + fresh:
        if it["v"] in done or it["v"] in seen_v or it.get("d", "") < cut:
            continue
        seen_v.add(it["v"])
        keep.append(it)
    state["pending"] = keep
    state["checked"] = now().isoformat(timespec="seconds")

    os.makedirs(os.path.dirname(QUEUE) or ".", exist_ok=True)
    if keep:
        with open(QUEUE, "w", encoding="utf-8") as f:
            f.write("# 訂閱頻道的新片，由 watch_channels.py 自動維護，轉好會自動移除\n")
            for it in keep:
                f.write("%s  # %s｜%s\n" % (it["v"], it["c"], it["t"][:40]))
    elif os.path.exists(QUEUE):
        os.remove(QUEUE)

    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    if subs_dirty:
        with open(SUBS, "w", encoding="utf-8") as f:
            json.dump(subs, f, ensure_ascii=False, indent=1)

    with open(os.environ.get("GITHUB_OUTPUT") or os.devnull, "a") as f:
        f.write("new=%d\npending=%d\n" % (len(fresh), len(keep)))
    print("\n這次新發現 %d 支；待轉錄佇列 %d 支" % (len(fresh), len(keep)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
