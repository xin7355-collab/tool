# -*- coding: utf-8 -*-
"""替既有逐字稿補上「上片日期」（影片在 YouTube 的發布日）。

網站列表本來只有「標題裡剛好寫了日期」的那些看得到日期——375 篇裡只有 34 篇。
但影片 ID 就夾在檔名裡，跟 YouTube 問一次就有，不必重新轉錄。

兩條路，先輕的：
  1. 直接抓影片頁 HTML，讀裡面的 uploadDate。一個普通 GET，不碰 player API，
     最快、也最不容易被當成機器人。
  2. 拿不到才退回 yt-dlp（走 player API，慢一點也比較容易被擋，但有 cookies 時很穩）。

只補「還沒有日期」的；已經有的不動，重跑不會多花時間。
"""
import os, re, sys, glob, json, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frontmatter as fm

SITE = os.environ.get("SITE_DIR", "site/transcripts")
LIMIT = int(os.environ.get("BACKFILL_LIMIT", "0") or "0")     # 0 = 不限
PAUSE = float(os.environ.get("BACKFILL_PAUSE", "0.6") or "0")  # 每支之間喘一下
VID_IN_NAME = re.compile(r"(?:__yt|_)([A-Za-z0-9_-]{11})\.md$")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
DATE_PATS = [re.compile(p) for p in (
    r'"uploadDate"\s*:\s*"(\d{4}-\d{2}-\d{2})',
    r'"publishDate"\s*:\s*"(\d{4}-\d{2}-\d{2})',
    r'itemprop="uploadDate"[^>]*content="(\d{4}-\d{2}-\d{2})',
)]


def date_from_html(vid):
    req = urllib.request.Request(
        "https://www.youtube.com/watch?v=" + vid,
        headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=40) as r:
        html = r.read().decode("utf-8", "replace")
    for pat in DATE_PATS:
        m = pat.search(html)
        if m:
            return m.group(1)
    return ""


def date_from_ytdlp(vid):
    import yt_dlp
    import grab_transcripts as g          # 沿用同一份 cookies／代理設定
    with yt_dlp.YoutubeDL(g._ydl_opts({"skip_download": True,
                                       "ignore_no_formats_error": True})) as y:
        info = y.extract_info("https://www.youtube.com/watch?v=" + vid, download=False)
    d = (info or {}).get("upload_date") or ""
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 and d.isdigit() else ""


_html_miss = 0          # 連續失敗次數；機房 IP 上這條路是全滅，試幾次就別再試了


def lookup(vid):
    """回傳 (日期, 用了哪條路)。都拿不到就回 ("", 最後一個錯誤)。"""
    global _html_miss
    if _html_miss >= 3:
        last = "html: 這個環境走不通，已跳過"
    else:
        try:
            d = date_from_html(vid)
            if d:
                _html_miss = 0
                return d, "html"
        except Exception as e:
            last = "html: %s" % str(e)[:60]
        else:
            last = "html: 頁面裡找不到日期"
        _html_miss += 1
        if _html_miss == 3:
            print("   （影片頁這條路連續三次拿不到，後面只走 yt-dlp）", flush=True)
    try:
        d = date_from_ytdlp(vid)
        if d:
            return d, "yt-dlp"
        return "", "yt-dlp: 沒有 upload_date（%s）" % last
    except Exception as e:
        return "", "%s；yt-dlp: %s" % (last, str(e).replace("\n", " ")[:80])


def main():
    files = sorted(glob.glob(os.path.join(SITE, "*.md")))
    if not files:
        print("找不到逐字稿"); return 0
    todo = []
    for p in files:
        m = VID_IN_NAME.search(os.path.basename(p))
        if not m:
            continue                       # 上傳的音檔／文章沒有影片 ID，問不到日期
        if re.search(r"^-\s*日期：\s*\S", open(p, encoding="utf-8").read(1500), re.M):
            continue
        todo.append((p, m.group(1)))
    print("共 %d 篇，其中 %d 篇還沒有上片日期" % (len(files), len(todo)), flush=True)
    if not todo:
        return 0
    if LIMIT:
        todo = todo[:LIMIT]

    ok, bad, ways = 0, 0, {}
    for n, (p, vid) in enumerate(todo, 1):
        d, how = lookup(vid)
        if not d:
            bad += 1
            print("[%d/%d] %s 拿不到日期（%s）" % (n, len(todo), vid, how), flush=True)
        else:
            text = open(p, encoding="utf-8").read()
            open(p, "w", encoding="utf-8").write(fm.apply(text, {"日期": d}))
            ok += 1
            ways[how] = ways.get(how, 0) + 1
            print("[%d/%d] %s → %s" % (n, len(todo), vid, d), flush=True)
        if PAUSE:
            time.sleep(PAUSE)

    print("補上 %d 篇（%s），拿不到 %d 篇"
          % (ok, "、".join("%s %d" % kv for kv in ways.items()) or "-", bad), flush=True)
    # 一篇都補不到＝兩條路都被擋，不是正常結果，別用綠燈蓋過去
    if todo and not ok:
        print("::error::一篇都沒補到——YouTube 兩條路都拿不到日期", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
