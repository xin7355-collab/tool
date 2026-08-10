# -*- coding: utf-8 -*-
"""替「已經存在的逐字稿」補上分類與短標題。

新的逐字稿在轉錄時就分好類了，但先前產出的那些沒有。它們的內容已經在
gh-pages 上，所以不必重新轉錄——直接讀文字送去分類就好，又快又便宜。

只補「還沒有分類」的；已經有的不動，重跑也不會重複花錢。
"""
import os, re, sys, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify as cls

SITE = os.environ.get("SITE_DIR", "site/transcripts")
LIMIT = int(os.environ.get("BACKFILL_LIMIT", "0") or "0")   # 0 = 不限


def split_head(text):
    """拆成 (前言行, 逐字稿本體)。前言是第一個 --- 之前的部分。"""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip().startswith("---"):
            return lines[:i], "\n".join(lines[i + 1:])
    return lines, ""


def main():
    files = sorted(glob.glob(os.path.join(SITE, "*.md")))
    if not files:
        print("找不到逐字稿"); return
    todo = []
    for p in files:
        head = open(p, encoding="utf-8").read(1500)
        if not re.search(r"^-\s*分類：", head, re.M):
            todo.append(p)
    print("共 %d 篇，其中 %d 篇還沒分類" % (len(files), len(todo)), flush=True)
    if LIMIT:
        todo = todo[:LIMIT]

    if not cls.GROQ_API_KEY:
        sys.exit("沒有 GROQ_API_KEY，無法分類。請確認 repo secrets 有設定。")
    print("分類清單：%s" % "、".join(cls.CATEGORIES), flush=True)

    done, catted, failed = 0, 0, 0
    for n, p in enumerate(todo, 1):
        text = open(p, encoding="utf-8").read()
        head, body = split_head(text)
        title = head[0][2:].strip() if head and head[0].startswith("# ") else os.path.basename(p)
        print("[%d/%d] %s" % (n, len(todo), title[:40]), flush=True)
        meta = cls.classify(title, body)
        if meta.get("err"):
            print("   分類失敗：%s" % meta["err"], flush=True)
            failed += 1
        tidy = cls.tidy(title)

        add = []
        if meta.get("cat"):
            add.append("- 分類：" + meta["cat"])
        if meta.get("tags"):
            add.append("- 關鍵字：" + "、".join(meta["tags"]))
        if tidy.get("clean"):
            add.append("- 短標題：" + tidy["clean"])
        if tidy.get("date"):
            add.append("- 日期：" + tidy["date"])
        if not add:
            print("   沒有可補的欄位", flush=True); continue

        # 插在前言最後一行「有內容的」之後，維持原本排版
        while head and not head[-1].strip():
            head.pop()
        new = "\n".join(head + add) + "\n\n---\n" + body
        open(p, "w", encoding="utf-8").write(new)
        print("   → %s / %s" % (meta.get("cat") or "-", "、".join(meta.get("tags") or [])), flush=True)
        done += 1
        if meta.get("cat"):
            catted += 1
    left = len(todo) - catted
    print("補完 %d 篇（分到類 %d、失敗 %d、還沒分到 %d）"
          % (done, catted, failed, left), flush=True)
    # 這個工作流曾經「成功」卻幾乎沒做事（先是分類清單被空環境變數洗掉，
    # 後來是撞 Groq 速率限制後靜靜失敗）。做不完就讓它紅燈，不要用綠燈掩蓋。
    if todo and not catted:
        sys.exit("一篇都沒分到類——分類清單或 Groq 呼叫有問題，不是正常結果。")
    if left:
        print("還有 %d 篇沒分到類，再跑一次這個工作流即可（已分好的會自動略過）。"
              % left, flush=True)


if __name__ == "__main__":
    main()
