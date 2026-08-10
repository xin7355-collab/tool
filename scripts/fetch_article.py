# -*- coding: utf-8 -*-
"""把網頁文章抓成純文字，走跟逐字稿同一條產線（摘要／分類／打包下載）。

為什麼預設不用 crawl4ai：它要 Playwright + Chromium，光裝就要幾十秒、幾百 MB，
而財經新聞、部落格這類「文章頁」用 trafilatura 直接解 HTML 就抓得到，快很多。
只有抓不到（內容全靠 JavaScript 生出來）時才退回 crawl4ai。

輸入：article-inbox/*.txt，一行一個網址（# 開頭是註解）。
輸出：out/*.md/.txt，跟逐字稿同格式，所以網站那邊完全不用改。
"""
import os, re, sys, glob, json, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grab_transcripts import OUT, safe_name, groq_summary, groq_polish
import classify as cls

INBOX = os.environ.get("ARTICLE_INBOX", "article-inbox")
MIN_CHARS = int(os.environ.get("ARTICLE_MIN_CHARS") or "200")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def fetch_html(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    enc = "utf-8"
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:4000], re.I)
    if m:
        enc = m.group(1).decode("ascii", "ignore") or "utf-8"
    return raw.decode(enc, "replace")


def extract_fast(html, url):
    """trafilatura：不用瀏覽器，一般文章頁這樣就夠了。"""
    try:
        import trafilatura
    except ImportError:
        return "", ""
    txt = trafilatura.extract(html, include_comments=False,
                              include_tables=False, favor_precision=True) or ""
    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        title = (getattr(meta, "title", "") or "") if meta else ""
    except Exception:
        pass
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else url
    return title, txt.strip()


def extract_browser(url):
    """crawl4ai：內容靠 JavaScript 生出來時才用，會啟動 Chromium。"""
    try:
        import asyncio
        from crawl4ai import AsyncWebCrawler
    except ImportError:
        return "", ""

    async def go():
        async with AsyncWebCrawler(verbose=False) as c:
            r = await c.arun(url=url)
            return (getattr(r, "metadata", {}) or {}).get("title", ""), \
                   (getattr(r, "markdown", "") or "")
    try:
        return asyncio.run(go())
    except Exception as e:
        print("   crawl4ai 失敗：%s" % str(e)[:80], flush=True)
        return "", ""


def to_paragraphs(text):
    # trafilatura 段落之間只隔一個換行（不是空行），所以要按單一換行切，
    # 否則整篇會黏成一大段，讀起來跟原文的分段對不起來。
    parts = [re.sub(r"[ \t]+", " ", p).strip() for p in text.split("\n")]
    return [p for p in parts if len(p) > 1]


def one(url):
    print("  抓取 %s" % url[:70], flush=True)
    title, text = "", ""
    try:
        html = fetch_html(url)
        title, text = extract_fast(html, url)
    except Exception as e:
        print("   讀取失敗：%s" % str(e)[:80], flush=True)
    if len(text) < MIN_CHARS:
        # 抓不到多半是內容由 JavaScript 產生，這時才值得啟動瀏覽器
        print("   內容太短（%d 字），改用瀏覽器再試…" % len(text), flush=True)
        t2, x2 = extract_browser(url)
        if len(x2) > len(text):
            title, text = (t2 or title), x2
    if len(text) < MIN_CHARS:
        print("   放棄：只抓到 %d 字" % len(text), flush=True)
        return False

    title = (title or url)[:120]
    paras = to_paragraphs(text)
    body = "\n".join(paras)
    chars = len(body)
    print("   取得「%s」%d 字" % (title[:40], chars), flush=True)

    summary = groq_summary(title, body)
    meta = cls.classify(title, body)
    tidy = cls.tidy(title)
    stem = safe_name(title) + "_web"

    md = ["# %s" % title, "", "- 來源：[原文](%s)" % url,
          "- 型態：網頁文章", "- 統計：%d 字 / %d 段" % (chars, len(paras))]
    if meta.get("cat"):
        md.append("- 分類：" + meta["cat"])
    if meta.get("tags"):
        md.append("- 關鍵字：" + "、".join(meta["tags"]))
    if tidy.get("clean"):
        md.append("- 短標題：" + tidy["clean"])
    if tidy.get("date"):
        md.append("- 日期：" + tidy["date"])
    md.append("")
    if summary:
        md += ["## 摘要", "", summary, ""]
    md += ["---", ""] + paras
    (OUT / (stem + ".md")).write_text("\n\n".join(md), encoding="utf-8")

    # 文章是打字打出來的，沒有辨識錯字，不需要 AI 校對——直接用原文
    txt = title + "\n\n"
    if summary:
        txt += "【摘要】\n" + summary + "\n\n【內文】\n\n"
    txt += "\n\n".join(paras) + "\n"
    (OUT / (stem + ".txt")).write_text(txt, encoding="utf-8")
    return True


def main():
    files = sorted(glob.glob(os.path.join(INBOX, "*.txt")))
    urls, seen = [], set()
    for f in files:
        for line in open(f, encoding="utf-8"):
            u = line.strip()
            if u and not u.startswith("#") and u.startswith("http") and u not in seen:
                seen.add(u); urls.append(u)
    OUT.mkdir(exist_ok=True)
    if not urls:
        (OUT / "_status.txt").write_text("0 0\n", encoding="utf-8")
        print("article-inbox/ 沒有網址"); return
    print("待處理 %d 個網址" % len(urls), flush=True)

    ok = 0
    for n, u in enumerate(urls, 1):
        print("[%d/%d]" % (n, len(urls)), flush=True)
        try:
            if one(u):
                ok += 1
        except Exception as e:
            print("   失敗：%s" % str(e).replace("\n", " ")[:150], flush=True)
    print("完成：成功 %d / 共 %d" % (ok, len(urls)), flush=True)
    (OUT / "_status.txt").write_text("%d %d\n" % (len(urls), ok), encoding="utf-8")
    # 抓完就把清單清掉（成功與否都清，失敗的重貼一次比較直覺）
    (OUT / "_processed.txt").write_text("\n".join(files) + ("\n" if files else ""),
                                        encoding="utf-8")


if __name__ == "__main__":
    main()
