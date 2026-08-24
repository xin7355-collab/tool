# -*- coding: utf-8 -*-
"""改逐字稿檔案開頭那幾行「- 欄位：值」。

那幾行同時給人看、也是 build_index.py 的索引來源，所以補欄位的程式不只一支
（補分類、補上片日期），共用同一套規則才不會各改各的。

之前是直接把新欄位 append 到「--- 之前的所有內容」尾巴。但前言裡還有
「## 摘要」和摘要正文，於是 `- 分類：` 會掉到摘要底下；重跑一次又多一份，
實際檔案裡就出現過重複兩次的 `- 關鍵字：` / `- 短標題：`。
這裡改成：認得開頭那個 metadata 區塊，有就就地更新、沒有才插在區塊後面。
"""
import re

META = re.compile(r"^-\s*([^：]+)：\s*(.*)$")


def split_head(text):
    """拆成 (前言行, 逐字稿本體)。前言是第一個 --- 之前的部分。"""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip().startswith("---"):
            return lines[:i], "\n".join(lines[i + 1:])
    return lines, ""


def _block_end(head):
    """開頭那段連續 metadata 的最後一行索引；沒有就回 -1。

    中間夾的空行不算中斷（產出的 .md 是用 "\\n\\n".join 寫的，每行之間都有空行），
    但遇到「## 摘要」這種真正的內容就停。
    """
    last = -1
    for i, ln in enumerate(head):
        s = ln.strip()
        if not s or s.startswith("# "):
            continue
        if META.match(s):
            last = i
            continue
        break
    return last


def set_fields(head, fields):
    """回傳新的前言行。fields 是 {欄位名: 值}，值是空的就跳過。

    所有 `- 欄位：` 一律集中到開頭那個區塊：已經有的就地更新，重複的只留第一個，
    掉到摘要底下的（先前 append 造成的）搬回上面來，新的接在最後。
    """
    head = list(head)
    end = _block_end(head)
    spaced = end > 0 and not head[end - 1].strip()   # 原本每行之間有沒有空行

    order, vals, drop = [], {}, set()
    for i, ln in enumerate(head):
        m = META.match(ln.strip())
        if not m:
            continue
        drop.add(i)
        k = m.group(1).strip()
        if k not in vals:
            order.append(k)
        vals[k] = m.group(2).strip()       # 同名的以最後一次為準（跟索引一致）
    for k, v in fields.items():
        if not v:
            continue
        if k not in vals:
            order.append(k)
        vals[k] = v

    block = []
    for k in order:
        if spaced and block:
            block.append("")
        block.append("- %s：%s" % (k, vals[k]))

    out, put = [], False
    for i, ln in enumerate(head):
        if i in drop:
            if not put:                     # 第一個欄位的位置就是整個區塊的位置
                out.extend(block); put = True
            continue
        out.append(ln)
    if not put and block:                   # 本來一個欄位都沒有：放在標題後面
        at = 1 if out and out[0].startswith("# ") else 0
        out[at:at] = ([""] + block) if spaced else block

    # 拿掉欄位後可能留下連續空行，收成最多兩行
    tidy, blanks = [], 0
    for ln in out:
        blanks = blanks + 1 if not ln.strip() else 0
        if blanks <= 2:
            tidy.append(ln)
    return tidy


def apply(text, fields):
    """把 fields 寫進整份文字，回傳新的文字。"""
    head, body = split_head(text)
    head = set_fields(head, fields)
    while head and not head[-1].strip():
        head.pop()
    return "\n".join(head) + "\n\n---\n" + body
