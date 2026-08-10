# -*- coding: utf-8 -*-
"""從逐字稿檔案本身重建 index.json。

前言那幾行「- 分類：…」是寫給人看的，也順便當索引來源，
不用另外維護一份 metadata 檔案。

原本這段程式在 audio.yml、classify.yml 各抄了一份，現在要再加 article.yml，
三份會開始不同步，所以抽出來共用。
"""
import json, glob, os, re, sys

FIELDS = {"分類": "cat", "關鍵字": "tags", "短標題": "clean",
          "日期": "date", "來源": "src"}


def build(site_dir="site/transcripts"):
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
        items.append(it)
    out = os.path.join(site_dir, "index.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print("索引 %d 篇；有分類的 %d 篇"
          % (len(items), sum(1 for x in items if x.get("cat"))), flush=True)
    return items


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "site/transcripts")
