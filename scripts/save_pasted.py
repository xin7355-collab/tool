# -*- coding: utf-8 -*-
"""把「貼上的逐字稿」整理成 .md/.txt/.srt 並存進 out/。

從外部免費工具（DownSub、YouTube 內建轉譯稿…）複製來的文字，格式五花八門：
可能帶時間碼、可能一行一句、可能整段。這裡統一處理成跟產線一致的輸出，
所以貼上的稿子和自動抽的稿子在前端長得一樣（可讀、可搜、可下載、有摘要）。

輸入：環境變數 ISSUE_BODY（前端送出的 Issue 內文，PASTE-TRANSCRIPT 格式）
輸出：out/<標題>_<影片ID或paste>.md/.txt/.srt
"""
import os, re, sys, json, pathlib, urllib.request

# 不從 grab_transcripts import，避免相依 yt-dlp（貼上模式根本不必下載影片）
OUT = pathlib.Path("out")
GAP, MAX_CHARS = 1.6, 180
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_SUMMARY = os.environ.get("GROQ_SUMMARY", "1") == "1"
GROQ_LLM_MODEL = os.environ.get("GROQ_LLM_MODEL", "llama-3.1-8b-instant").strip()


def safe_name(s):
    return (re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", str(s or "untitled")).strip() or "untitled")[:80]


def hhmmss(sec):
    sec = int(sec); h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return (f"{h}:" if h else "") + f"{m:02d}:{s:02d}"


def srt_time(sec):
    sec = max(0.0, float(sec)); h = int(sec // 3600); m = int(sec % 3600 // 60)
    s = int(sec % 60); ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_paragraphs(cues):
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


def groq_summary(title, text):
    if not (GROQ_API_KEY and GROQ_SUMMARY):
        return ""
    try:
        body = json.dumps({
            "model": GROQ_LLM_MODEL, "temperature": 0.3,
            "messages": [
                {"role": "system", "content":
                 "你是逐字稿摘要助手。用繁體中文輸出 3~6 點條列重點摘要，每點一句話，"
                 "忠於原文、不要添加原文沒有的資訊，直接輸出條列即可，不要開場白。"},
                {"role": "user", "content":
                 "影片標題：%s\n\n逐字稿（可能截斷）：\n%s" % (title, text[:10000])},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=body,
            headers={"Authorization": "Bearer " + GROQ_API_KEY,
                     "Content-Type": "application/json",
                     "User-Agent": "tool/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        s = (j.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if s:
            print("    已產生摘要（%s）" % GROQ_LLM_MODEL, flush=True)
        return s
    except Exception as e:
        print("    摘要失敗（略過）：%s" % str(e).replace("\n", " ")[:120], flush=True)
        return ""

TS = re.compile(r"^\s*\[?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?\]?\s*")


def parse_body(body):
    """從 Issue 內文取出 title / video / 逐字稿本體。"""
    title, vid, text = "", "", body
    m = re.search(r"PASTE-TRANSCRIPT(.*?)^---\s*$(.*)", body, re.S | re.M)
    if m:
        head, text = m.group(1), m.group(2)
        t = re.search(r"^\s*title:\s*(.+)$", head, re.M)
        v = re.search(r"^\s*video:\s*([A-Za-z0-9_-]{11})\s*$", head, re.M)
        if t:
            title = t.group(1).strip()
        if v:
            vid = v.group(1)
    return title, vid, text.strip()


def to_cues(text):
    """把貼上的文字轉成 cues。有時間碼就用它，沒有就用行序估時間。"""
    cues, cur = [], None
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = TS.match(line)
        rest = TS.sub("", line).strip() if m else line
        if m:
            h = int(m.group(1) or 0)
            start = h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if m.group(4):
                start += int(m.group(4)) / (1000.0 if len(m.group(4)) > 2 else 100.0)
            if cur:
                # duration 取「到下一句的間隔」但上限 8 秒，好讓長靜默能切出段落
                cur["duration"] = max(0.5, min(8.0, start - cur["start"]))
                cues.append(cur)
            cur = {"start": float(start), "duration": 3.0, "text": rest}
        elif rest:
            if cur:
                joiner = "" if re.search(r"[㐀-鿿]$", cur["text"]) else " "
                cur["text"] += joiner + rest
            else:
                cur = {"start": float(len(cues) * 4), "duration": 4.0, "text": rest}
                cues.append(cur); cur = None
    if cur:
        cues.append(cur)
    return [c for c in cues if c["text"].strip()]


def main():
    body = os.environ.get("ISSUE_BODY", "")
    if "PASTE-TRANSCRIPT" not in body:
        print("不是貼上的逐字稿，略過"); return
    title, vid, text = parse_body(body)
    if not text:
        print("內容為空"); return
    title = title or "貼上的逐字稿"
    cues = to_cues(text)
    if not cues:
        print("解析不到內容"); return

    OUT.mkdir(exist_ok=True)
    paras = to_paragraphs(cues)
    chars = sum(len(p["t"]) for p in paras)
    stem = f"{safe_name(title)}_{vid or 'paste'}"
    link = f"https://youtu.be/{vid}" if vid else ""
    summary = groq_summary(title, "\n".join(p["t"] for p in paras))

    md = [f"# {title}", ""]
    if link:
        md.append(f"- 影片：{link}")
    md += [f"- 來源：外部工具貼上", f"- 統計：{chars} 字 / {len(paras)} 段", ""]
    if summary:
        md += ["## 摘要", "", summary, ""]
    md += ["---", ""]
    for p in paras:
        stamp = f"[{hhmmss(p['s'])}]({link}?t={int(p['s'])})" if link else f"[{hhmmss(p['s'])}]"
        md.append(f"**{stamp}** {p['t']}")
    (OUT / f"{stem}.md").write_text("\n\n".join(md), encoding="utf-8")

    txt = title + "\n" + (link + "\n" if link else "") + "\n"
    if summary:
        txt += "【摘要】\n" + summary + "\n\n【逐字稿】\n\n"
    txt += "\n\n".join(p["t"] for p in paras) + "\n"
    (OUT / f"{stem}.txt").write_text(txt, encoding="utf-8")

    srt = []
    for i, c in enumerate(cues, 1):
        srt.append(f"{i}\n{srt_time(c['start'])} --> "
                   f"{srt_time(c['start'] + c.get('duration', 0))}\n{c['text']}\n")
    (OUT / f"{stem}.srt").write_text("\n".join(srt), encoding="utf-8")
    print(f"已存檔：{stem}（{chars} 字 / {len(paras)} 段）")


if __name__ == "__main__":
    main()
