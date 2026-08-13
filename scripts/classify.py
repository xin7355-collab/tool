# -*- coding: utf-8 -*-
"""把雜亂的影片標題洗乾淨，並判斷它屬於哪一類。

分兩層是有原因的：

  第一層「洗名稱」用規則就夠——標題有很強的固定結構（【系列】、｜分隔、
  第N集、日期），規則寫得出來、速度快、結果可預期，而且對已經存在的
  逐字稿也能直接套用，不用重跑轉錄。

  第二層「分主題」規則做不到——「這支在講籌碼還是當沖」得看內容講什麼，
  標題常常只是聳動的問句。這層交給 Groq，反正產線本來就會呼叫它做摘要。

Groq 掛掉時整個分類就留空，絕不擋住逐字稿產出。
"""
import os, re, json, time, urllib.request, urllib.error

# 想改分類就改這裡（或設環境變數 DECK_CATEGORIES，用逗號分隔）。
# 注意用 or 不能用 get 的預設值：工作流裡沒設定的 vars 會變成「空字串」傳進來，
# 這時 os.environ.get(key, 預設) 會回空字串而不是預設值，分類清單就整個空掉。
DEFAULT_CATEGORIES = "籌碼分析,當沖短線,波段操作,權證,ETF存股,盤勢解析,新手教學,訪談分享,其他"
CATEGORIES = [c.strip() for c in
              (os.environ.get("DECK_CATEGORIES") or DEFAULT_CATEGORIES).split(",")
              if c.strip()]

GROQ_API_KEY = (os.environ.get("GROQ_API_KEY") or "").strip()
GROQ_LLM = (os.environ.get("GROQ_LLM_MODEL") or "llama-3.1-8b-instant").strip()

# 標題結尾常見的雜訊：頻道名、來賓名、節目名、shorts 標籤之類
NOISE = re.compile(
    r"(feat\.?|ft\.?|#\S+|shorts|投資|股票|股市|台股|股票市場|官方頻道)",
    re.I)


def _is_credits(seg):
    """這一段是不是「來賓／頻道／節目名」而不是主題。"""
    if re.search(r"[《》]", seg):
        return True
    people = re.split(r"[、,，]", seg)
    return len(people) >= 2 and all(len(p.strip()) <= 5 for p in people if p.strip())


def tidy(raw):
    """把檔名／標題洗成人看得舒服的樣子，回傳 dict。

    只做「看得出來」的處理，看不出來就原樣保留——寧可留著也不要洗掉內容。
    """
    s = str(raw or "").strip()
    s = re.sub(r"__yt[A-Za-z0-9_-]{11}$", "", s)      # 尾巴的影片 ID 標記
    s = s.replace("_", " ")                            # safe_name 把空白換成了底線
    s = re.sub(r"\s+", " ", s).strip()

    # 日期：2020.12.21 / 2026-07-08 / 2020.3.4
    date = ""
    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if m:
        date = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
        s = (s[:m.start()] + " " + s[m.end():]).strip()

    # 系列名：【哥有籌必爆S2】
    series = ""
    m = re.match(r"^\s*[【\[]([^】\]]{1,20})[】\]]\s*", s)
    if m:
        series = m.group(1).strip()
        s = s[m.end():].strip()

    # 集數：第10集 / EP34 / ep30
    ep = ""
    m = re.search(r"第\s*(\d{1,3})\s*集|\bEP\s*(\d{1,3})\b", s, re.I)
    if m:
        ep = "EP" + (m.group(1) or m.group(2))
        s = (s[:m.start()] + " " + s[m.end():]).strip()

    # ｜之後多半是「來賓、頻道、節目名」。不能無腦取第一段——把「第N集」拿掉後，
    # 第一段常常只剩一個驚嘆號，真正的主題在下一段。
    # 取「最長」那段而不是第一段：頻道名可能在前（權證小哥｜主題）也可能在後
    # （主題｜李兆華、權證小哥），只有長度能穩定分辨哪一段才是內容。
    parts = [p.strip(" 　！？。，、") for p in re.split(r"[｜|]", s)]
    parts = [p for p in parts if len(p) >= 4 and not _is_credits(p)]
    topic = max(parts, key=len) if parts else s.strip(" 　！？。")

    # 句末標點後面若接著一串短詞，多半是被底線吃掉的 #標籤，砍掉
    m = re.search(r"[！？。]\s*(.+)$", topic)
    if m and all(len(w) <= 8 for w in m.group(1).split()) and len(m.group(1).split()) >= 1:
        topic = topic[:m.start() + 1]

    topic = NOISE.sub("", topic)
    topic = re.sub(r"[《》\-—·、,，]+$", "", topic).strip()
    topic = re.sub(r"\s{2,}", " ", topic).strip(" 　-·|｜")

    head = " ".join(x for x in (series, ep) if x)
    clean = (head + "｜" + topic).strip("｜ ") if head else topic
    return {"clean": clean[:70] or str(raw)[:70], "series": series,
            "ep": ep, "date": date, "topic": topic[:60]}


"""分類只需要看開頭一小段就夠了，不必整篇送。

一次送 6000 字時，跑到第 13 篇就撞上 Groq 的每分鐘 token 上限，
後面 100 篇全部靜靜失敗。改送 1800 字，再加上遇到 429 就照 Retry-After 等，
慢一點但會全部跑完。
"""
CLIP = int(os.environ.get("CLASSIFY_CHARS") or "1800")


def _groq_json(prompt, text, tries=5):
    body = json.dumps({
        "model": GROQ_LLM,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": text[:CLIP]}],
    }).encode()
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=body,
            headers={"Authorization": "Bearer " + GROQ_API_KEY,
                     "Content-Type": "application/json",
                     "User-Agent": "tool"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            return json.loads(j["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or attempt == tries:
                raise
            # 429 會在標頭給建議等待秒數，照做最準
            wait = 0
            try:
                wait = float(e.headers.get("retry-after") or 0)
            except Exception:
                pass
            time.sleep(min(max(wait, 3 * attempt), 65))
    raise RuntimeError("Groq 重試 %d 次都失敗" % tries)


def classify(title, text):
    """判斷主題分類與關鍵字。失敗就回空的，讓逐字稿照樣產出。"""
    if not (GROQ_API_KEY and text.strip()):
        return {"cat": "", "tags": []}
    prompt = (
        "你是中文影片分類助理。讀完內容後，只輸出 JSON："
        '{"cat":"分類","tags":["關鍵字1","關鍵字2","關鍵字3"]}。\n'
        "cat 必須從這個清單挑一個最貼近的：" + "、".join(CATEGORIES) + "。\n"
        "tags 給 2~4 個繁體中文關鍵字（個股名、工具名、操作手法等），不要加 #。"
    )
    try:
        j = _groq_json(prompt, "標題：%s\n\n內容：%s" % (title, text))
    except Exception as e:
        # 把原因帶回去，呼叫端才有辦法報告——之前一律吞掉，
        # 結果 100 篇分類失敗看起來跟「沒東西要分」一模一樣。
        return {"cat": "", "tags": [], "err": str(e)[:100]}
    cat = str(j.get("cat") or "").strip()
    if cat not in CATEGORIES:
        cat = CATEGORIES[-1] if CATEGORIES else ""     # 亂answer就丟「其他」
    tags = [str(t).strip().lstrip("#") for t in (j.get("tags") or [])][:4]
    return {"cat": cat, "tags": [t for t in tags if t]}
