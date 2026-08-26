# -*- coding: utf-8 -*-
"""從逐字稿檔案本身重建 index.json。

前言那幾行「- 分類：…」是寫給人看的，也順便當索引來源，
不用另外維護一份 metadata 檔案。

原本這段程式在 audio.yml、classify.yml 各抄了一份，現在要再加 article.yml，
三份會開始不同步，所以抽出來共用。
"""
import json, glob, os, re, sys
from datetime import datetime, timezone

FIELDS = {"分類": "cat", "關鍵字": "tags", "短標題": "clean",
          "日期": "date", "來源": "src"}


def prev_added(out_path):
    """讀上一版 index.json，把每篇的「進站日」記下來。

    這個檔是 sorted(glob) 產生的＝檔名字母序，不是時間序。前端的「最新在前」
    直接沿用那個順序，所以新抓進來的逐字稿不會出現在最上面——看起來像排序壞了。
    影片本身的發布日多數拿不到（標題有日期的只有 18%），但「什麼時候進到這個庫」
    我們自己就知道，記下來就夠拿來排序了。

    只認上一版已經有的；沒有的就是這次新進來的，蓋今天的日期。
    """
    try:
        with open(out_path, encoding="utf-8") as f:
            return {x.get("file"): x.get("added") for x in json.load(f) if x.get("added")}
    except (OSError, ValueError):
        return {}


def build(site_dir="site/transcripts"):
    out = os.path.join(site_dir, "index.json")
    seen = prev_added(out)
    today = datetime.now(timezone.utc).date().isoformat()
    items = []
    for p in sorted(glob.glob(os.path.join(site_dir, "*.md"))):
        name = os.path.basename(p)
        it = {"file": "transcripts/" + name, "title": name}
        with open(p, encoding="utf-8") as f:
            for n, line in enumerate(f):
                line = line.strip()
                if n == 0 and line.startswith("# "):
                    it["title"] = line[2:].strip()
                    continue
                if line.startswith("---"):
                    break                      # 前言結束，後面是內文
                m = re.match(r"^-\s*([^：]+)：\s*(.+)$", line)
                if m and m.group(1).strip() in FIELDS:
                    k = FIELDS[m.group(1).strip()]
                    v = m.group(2).strip()
                    it[k] = [x for x in re.split(r"[、,]", v) if x] if k == "tags" else v
        it["added"] = seen.get(it["file"], today)
        # 只有逐字稿有 .srt（字幕要對時間軸）。文章和上傳的文件沒有，
        # 但前端本來每張卡都畫三個下載鈕，那個 .srt 按下去就是 404。
        if os.path.exists(p[:-3] + ".srt"):
            it["srt"] = True
        items.append(it)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    fresh = sum(1 for x in items if x["added"] == today and x["file"] not in seen)
    print("索引 %d 篇；有分類的 %d 篇；這次新進 %d 篇"
          % (len(items), sum(1 for x in items if x.get("cat")), fresh), flush=True)
    return items


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "site/transcripts")
