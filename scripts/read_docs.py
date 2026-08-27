# -*- coding: utf-8 -*-
"""把使用者上傳到 doc-inbox/ 的 PDF 或圖片讀成文字。

三層，由準到不準、由快到慢，能在前一層解決就不往下走：

  1. PDF 的文字層。公文、報告、投影片這類「電腦產生的 PDF」裡本來就有文字，
     直接抽出來是 100% 正確而且瞬間完成的——這種檔根本不需要 OCR。
  2. Groq 視覺模型。掃描件和手機拍的照片才走這裡。繁體中文準確度比傳統 OCR
     好很多，還讀得懂表格版面。
  3. Tesseract。Groq 掛掉、額度滿、或那個模型哪天被下架時墊底用的。
     （llama-3.1-8b-instant 就在 2026-08-16 被下架過，整組摘要靜靜失效了一週，
     所以視覺這條路一定要有不依賴任何雲端服務的後路。）

讀完之後跟逐字稿走同一條路：摘要、分類、短標題，寫成 out/*.md 與 *.txt，
發佈到 gh-pages 就出現在「📄 下載」分頁，可以搜尋、打包、下載。
"""
import os, re, io, sys, glob, json, time, base64, shutil, tempfile, subprocess
import urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grab_transcripts import OUT, safe_name, groq_summary, GROQ_API_KEY
import progress as progress_mod
import classify as cls

INBOX = os.environ.get("DOC_INBOX", "doc-inbox")
PDF_EXT = (".pdf",)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif")
# 本來就是文字的檔案：直接讀進來，什麼辨識都不用。
# Google 文件從雲端硬碟匯出就是這種，所以這條路一定要有。
TXT_EXT = (".txt", ".md", ".markdown", ".csv")
EXTS = PDF_EXT + IMG_EXT + TXT_EXT
# 文字層抽出來少於這麼多字，就當作「這是掃描件」改走 OCR。
# 掃描的 PDF 常常還是有零星幾個字（頁碼、浮水印），不能只看有沒有。
MIN_TEXT = int(os.environ.get("DOC_MIN_TEXT", "120") or "120")
MAX_PAGES = int(os.environ.get("DOC_MAX_PAGES", "40") or "40")
# 一份文件裡最多讀幾張圖。技術分析講義一頁一張圖是常態，四十頁就是四十次
# 視覺辨識——會很慢也很花額度。超過就只標出「這裡有圖」，不硬讀。
MAX_CHARTS = int(os.environ.get("DOC_MAX_CHARTS", "12") or "12")
# 太小的圖是 logo、項目符號、分隔線，讀了也沒東西。走勢圖不會這麼小。
MIN_CHART_PX = int(os.environ.get("DOC_MIN_CHART_PX", "240") or "240")
# 掃描頁重繪的解析度。150 對 K 線的上下影線和成交量數字太糊，拉到 220。
SCAN_DPI = int(os.environ.get("DOC_SCAN_DPI", "220") or "220")
# 這個模型在 Groq 目前是 preview 狀態，可能被換掉；換掉時會 404，
# 程式會自動退到 Tesseract，不會整批失敗。要換模型改這裡就好。
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b").strip()
MAX_IMG_B64 = 3_500_000          # Groq 內嵌圖片的大小上限，留點餘裕

OCR_SYS = (
    "你是文件辨識工具。把圖片裡的文字原封不動抄出來，用繁體中文輸出。\n"
    "只輸出文字本身：不要加開場白、不要說明、不要摘要、不要改寫或補字。\n"
    "保留原本的段落與換行；表格請用 Markdown 表格；標題保留成一行。\n"
    "看不清楚的字用 □ 代替，不要猜。\n"
    "如果頁面上有走勢圖或圖表，抄完文字後另起一段，用「【圖表】」開頭描述它。\n"
    "整張圖沒有任何內容就只回一行：（沒有文字）"
)
# 走勢圖要的是另一種輸出：不是抄字，是把視覺資訊翻成文字。
# 最後那條規則是重點——視覺模型很會認型態，卻很不會讀座標軸，
# 讓它自由發揮就會把 152.5 的頸線講成 150，而且講得非常有自信。
CHART_SYS = (
    "你在看一張技術分析圖表。用繁體中文寫 2～5 句連貫的描述，講圖上看得到的東西。\n"
    "\n"
    "要涵蓋（圖上沒有就別提，不要硬湊）：\n"
    "圖表種類與時間框架（日K／週K／分K／折線／柱狀）；走勢與K線型態\n"
    "（長紅棒突破、槌子線、十字星、頭肩頂、W 底、箱型整理…）；均線排列與交叉；\n"
    "成交量放大或萎縮、量價有沒有背離；圖上有明確標註的價位、頸線、支撐壓力與文字。\n"
    "\n"
    "規則：\n"
    "・寫成一段順的文字。**不要條列、不要編號、不要把上面那些項目名稱抄進答案裡。**\n"
    "・座標軸的數字看不清楚時，用相對描述（「約在近期高點附近」），\n"
    "  **絕對不要自己編一個精確價格**。寧可說看不清楚。\n"
    "・只描述圖上有的，不要推測後續走勢，不要給投資建議。\n"
    "・如果這不是走勢圖（照片、logo、表格截圖），就直接說它是什麼。\n"
    "・不要開場白，直接寫描述。"
)


def which(name):
    return shutil.which(name)


# ---------- 第 1 層：PDF 的文字層與圖表 ----------

def _heading(size, body):
    """字級換算標題階層。

    PDF 沒有「標題」這個概念，只有字級。但重建階層很值得：AI 讀的時候靠它
    切段落、抓上下文，人在手機上看也才不是一整片沒有段落的字牆。
    """
    if not body:
        return ""
    r = size / body
    return "# " if r >= 1.6 else "## " if r >= 1.34 else "### " if r >= 1.14 else ""


def pdf_parts(path):
    """把 PDF 拆成依閱讀順序排好的片段。

    回傳 [("text", markdown), ("image", bytes, 寬, 高, 是不是整頁), ...]。
    圖片要留在原本的位置：技術分析講義的「以下圖為例，頸線位置在 152.5 元」
    跟那張圖必須黏在一起，拆開之後兩邊都沒有意義。
    """
    import pymupdf
    doc = pymupdf.open(path)
    pages = list(doc)[:MAX_PAGES]

    sizes = {}
    for page in pages:                       # 先看整份文件最常出現的字級＝內文
        for blk in page.get_text("dict")["blocks"]:
            for ln in blk.get("lines") or []:
                for sp in ln["spans"]:
                    if sp["text"].strip():
                        k = round(sp["size"])
                        sizes[k] = sizes.get(k, 0) + len(sp["text"].strip())
    body = max(sizes, key=sizes.get) if sizes else 0

    out = []
    for page in pages:
        parea = abs(page.rect) or 1
        parts, txt = [], ""
        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") == 1:
                w, h = blk.get("width") or 0, blk.get("height") or 0
                full = (abs(pymupdf.Rect(blk["bbox"])) / parea) > 0.7
                parts.append(("image", blk.get("image") or b"", w, h, full))
                continue
            # 同一個區塊裡的行合成一段；跨行斷掉的中文句子接回去
            top, lines = 0, []
            for ln in blk.get("lines") or []:
                s = "".join(sp["text"] for sp in ln["spans"]).strip()
                if not s:
                    continue
                top = max(top, max((sp["size"] for sp in ln["spans"]), default=0))
                lines.append(s)
            if not lines:
                continue
            head = _heading(top, body)
            body_txt = head + (" ".join(lines) if head else "".join(lines))
            txt += body_txt
            parts.append(("text", body_txt))
        out.append({"parts": _join_wraps(parts), "bad": len(PUA.findall(txt)),
                    "chars": len(re.sub(r"\s", "", txt)), "no": page.number})
    return out


# 造字區（Private Use Area）與取代字元。字型子集化做壞時，數字和某些字會被
# 對應到這個範圍——那是字型內部編號，不是 Unicode，任何程式都還原不回來。
# 實際踩過：一本技術分析講義的每個數字都變成 U+F6BE~U+F6C3，
# 「20 日均線」「60 日均線」全成了空白方塊，而數字正好是那份文件的重點。
PUA = re.compile(r"[\ue000-\uf8ff\ufffd]")
# 一頁裡壞掉的字超過這個數量，就別信文字層，整頁重繪去辨識。
# 一兩個可能只是裝飾符號，不值得為它多打一次視覺模型。
BAD_CHARS = int(os.environ.get("DOC_BAD_CHARS", "3") or "3")


# 一句話被排版換行切斷時，前半段不會有句尾標點。有的話就是真的分段了。
ENDS = "。！？：；…」』）】》!?:;.)]"


def _join_wraps(parts):
    """把被排版切斷的同一段接回去。

    PDF 常常一行就是一個區塊，照抄的話「…成交量須放」和「大。第三…」會變成
    兩段。中文的換行不等於分段，接不回去的話讀起來斷斷續續，摘要與切片也會
    在句子中間被切開。
    """
    out = []
    for part in parts:
        if (part[0] == "text" and out and out[-1][0] == "text"
                and not part[1].startswith("#") and not out[-1][1].startswith("#")
                and out[-1][1] and out[-1][1][-1] not in ENDS):
            out[-1] = ("text", out[-1][1] + part[1])
        else:
            out.append(part)
    return out


def page_image(path, pno):
    """把某一頁重繪成圖。掃描件、或文字層壞掉的頁，都靠這個。"""
    import pymupdf
    doc = pymupdf.open(path)
    return doc[pno].get_pixmap(dpi=SCAN_DPI).tobytes("jpeg")


# ---------- 第 2 層：Groq 視覺 ----------

def shrink(data):
    """太大的圖縮到 Groq 吃得下。進出都是 bytes——PDF 裡的圖是從文件直接
    取出來的，本來就沒有檔案，不要為了縮圖多繞一次磁碟。"""
    if len(data) * 4 // 3 <= MAX_IMG_B64:
        return data
    try:
        from PIL import Image
    except ImportError:
        return data                      # 沒 Pillow 就照原樣送，失敗再退 Tesseract
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        out = data
        for w in (2200, 1700, 1300, 1000):
            buf = io.BytesIO()
            small = im.copy()
            small.thumbnail((w, w * 4), Image.LANCZOS)
            small.save(buf, "JPEG", quality=85, optimize=True)
            out = buf.getvalue()
            if len(out) * 4 // 3 <= MAX_IMG_B64:
                break
        return out
    except Exception:
        return data                      # 縮不成就照原樣送，失敗再退 Tesseract


def strip_think(s):
    """拿掉模型自己的推理過程。

    qwen3 系列是「會思考」的模型，回覆常常先來一段 <think>…</think> 說明它打算
    怎麼讀這張圖。那不是圖片上的文字，留著就會被當成內文寫進逐字稿——實測一張
    只有 200 字的公文，辨識結果有 400 字是它在自言自語。
    """
    s = re.sub(r"(?is)<think>.*?</think>", "", s or "")
    s = re.sub(r"(?is)<think>.*$", "", s)        # 沒有收尾的（被截斷）整段都不要
    return s.strip()


def ocr_groq(data, sys_msg=OCR_SYS):
    """送一張圖給 Groq 的視覺模型。失敗回 ("", 原因)。

    sys_msg 決定要它做哪件事：抄文字（OCR_SYS）還是描述走勢圖（CHART_SYS）。
    同一張圖用錯提示詞，出來的東西會完全不一樣。
    """
    if not GROQ_API_KEY:
        return "", "沒有 GROQ_API_KEY"
    data = shrink(data)
    b64 = base64.b64encode(data).decode("ascii")
    if len(b64) > MAX_IMG_B64:
        return "", "圖片太大（%d KB）" % (len(b64) // 1024)
    body = json.dumps({
        "model": VISION_MODEL, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": sys_msg},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + b64}}]}],
    }).encode("utf-8")
    for n in range(1, 4):
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions", data=body,
            headers={"Authorization": "Bearer " + GROQ_API_KEY,
                     "Content-Type": "application/json",
                     "User-Agent": "transcript-tool/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            msg = (j.get("choices") or [{}])[0].get("message", {}) or {}
            return strip_think(msg.get("content") or ""), ""
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "", "Groq 找不到模型 %s（多半已停用）" % VISION_MODEL
            if e.code not in (429, 500, 502, 503, 520, 524) or n == 3:
                return "", "HTTP %s" % e.code
            time.sleep(float(e.headers.get("Retry-After") or 0) or 3 * n)
        except Exception as e:
            if n == 3:
                return "", str(e).replace("\n", " ")[:80]
            time.sleep(3 * n)
    return "", "重試三次都失敗"


# ---------- 第 3 層：Tesseract ----------

def ocr_tesseract(data):
    """Tesseract 只吃檔案，所以這裡才落地一次；上面兩層都在記憶體裡跑。"""
    if not which("tesseract"):
        return "", "機器上沒有 tesseract"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("wb", suffix=".png", delete=False) as f:
            f.write(data); tmp = f.name
        r = subprocess.run(["tesseract", tmp, "stdout", "-l", "chi_tra+eng", "--psm", "3"],
                           capture_output=True, timeout=180)
        return r.stdout.decode("utf-8", "replace").strip(), ""
    except Exception as e:
        return "", str(e)[:80]
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def ocr_page(data, sys_msg=OCR_SYS):
    """一張圖 → (文字, 用了哪條路)。Groq 優先，失敗退 Tesseract。"""
    txt, why = ocr_groq(data, sys_msg)
    if txt and "（沒有文字）" not in txt:
        return txt, "groq"
    if txt:
        return "", "groq"                       # 模型說這張沒有文字，就別再花時間
    print("      Groq 讀不到（%s），改用 Tesseract" % why, flush=True)
    # Tesseract 只會抄字，看不懂走勢圖。走勢圖讀不到就讀不到，
    # 硬用 OCR 只會抄回一堆座標軸數字，比沒有更糟。
    if sys_msg is CHART_SYS:
        return "", "none"
    txt, why2 = ocr_tesseract(data)
    if txt:
        return txt, "tesseract"
    print("      Tesseract 也讀不到（%s）" % why2, flush=True)
    return "", "none"


def read_pdf(path, on_page=None):
    """PDF：文字層重建成有標題階層的 Markdown，圖表就地翻成文字插回原位。

    以前只抽文字層就收工。對一般公文沒問題，但技術分析講義的內容大半在圖上——
    「以下圖為例，頸線位置在 152.5 元」抽得到，那張圖抽不到，等於整章白讀。

    文字層也不是永遠可信：字型子集化做壞的 PDF 會把數字對應到造字區，
    抽出來是一堆方塊。那種頁直接當掃描件重繪辨識，寧可慢一點也不要拿到假內容。
    """
    pages = pdf_parts(path)
    chars = sum(p["chars"] for p in pages)
    imgs = [x for p in pages for x in p["parts"] if x[0] == "image"]
    broken = [p for p in pages if p["bad"] >= BAD_CHARS]

    # 沒有圖可辨識、文字層也沒壞：文字有多少就是多少，就算只有兩行也是內容
    if not imgs and not broken:
        if not chars:
            return "", "這份 PDF 既沒有文字也沒有圖片"
        return ("\n\n".join(x[1] for p in pages for x in p["parts"] if x[0] == "text"),
                "PDF 文字層")
    # 有圖、文字卻少得可疑＝掃描件，整頁重繪去辨識
    scanned = chars < MIN_TEXT and imgs and all(x[4] for x in imgs)
    if scanned or len(broken) == len(pages):
        why = "沒有文字層" if scanned else "文字層的字對應壞了（數字變方塊）"
        print("    %s，整份重繪辨識…" % why, flush=True)
        got, ways = [], set()
        for i, p in enumerate(pages, 1):
            if on_page:
                on_page(i, len(pages))
            txt, how = ocr_page(page_image(path, p["no"]))
            ways.add(how)
            if txt:
                got.append(txt)
        if not got:
            return "", "每一頁都辨識不出文字"
        ways.discard("none")
        return "\n\n".join(got), "辨識 %d 頁（%s）" % (len(pages), "＋".join(sorted(ways)) or "?")

    # 逐頁處理：好的頁用文字層，壞的頁重繪辨識，圖表就地翻成文字
    big = [x for x in imgs if min(x[2], x[3]) >= MIN_CHART_PX and x[1]]
    todo = big[:MAX_CHARTS]
    out, done, skipped, fixed = [], 0, 0, 0
    for p in pages:
        if p["bad"] >= BAD_CHARS:
            # 這頁的文字層不可信。整頁重繪辨識——圖表也會一起被描述，
            # 所以這頁的圖片就不用再單獨跑一次。
            fixed += 1
            if on_page:
                on_page(fixed, len(broken))
            txt, _ = ocr_page(page_image(path, p["no"]))
            out.append(txt or "（這一頁的文字讀不出來）")
            continue
        for part in p["parts"]:
            if part[0] == "text":
                out.append(part[1])
                continue
            _, data, w, h, full = part
            if part not in todo:
                if data and min(w, h) >= MIN_CHART_PX:
                    skipped += 1
                    out.append("> **【圖表】**（這份文件圖表太多，這張沒有讀）")
                continue
            done += 1
            txt, how = ocr_page(data, OCR_SYS if full else CHART_SYS)
            if txt:
                out.append("> **【圖表】** " + txt.replace("\n", "\n> "))
            else:
                out.append("> **【圖表】**（讀不出來）")

    body = "\n\n".join(x for x in out if x and x.strip())
    how = "PDF 文字層"
    if fixed:
        how += "＋%d 頁改用辨識（文字層壞了）" % fixed
    if done:
        how += "＋讀了 %d 張圖" % done
    if skipped:
        how += "（另有 %d 張沒讀）" % skipped
    return body, how


# ---------- 整份文件 ----------

def read_doc(path, on_page=None):
    """回傳 (文字, 怎麼讀到的)。讀不出來就回 ("", 原因)。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in TXT_EXT:
        if on_page:
            on_page(1, 1)
        for enc in ("utf-8", "utf-8-sig", "big5", "cp950"):
            try:
                with open(path, encoding=enc) as f:
                    t = f.read()
                return t, "純文字檔" + ("" if enc.startswith("utf-8") else "（%s）" % enc)
            except (UnicodeDecodeError, LookupError):
                continue
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(), "純文字檔（編碼有問題，可能有亂碼）"
    if ext in PDF_EXT:
        return read_pdf(path, on_page)
    if on_page:
        on_page(1, 1)
    with open(path, "rb") as f:
        t, how = ocr_page(f.read())
    if not t:
        return "", "圖片辨識不出文字"
    return t, {"groq": "圖片辨識（Groq 視覺）",
               "tesseract": "圖片辨識（Tesseract）"}.get(how, "圖片辨識")


def clean_title(path):
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"^\d{8}-\d{6}[_-]", "", name)      # 前端加的時間戳前綴
    return name.strip() or "上傳的文件"


def to_paragraphs(text):
    """把辨識出來的文字收成段落。空行分段，順便去掉整份都是空白的行。"""
    parts = re.split(r"\n\s*\n", (text or "").strip())
    return [re.sub(r"[ \t]+\n", "\n", p).strip() for p in parts if p.strip()]


def main():
    files = sorted(f for f in glob.glob(os.path.join(INBOX, "*"))
                   if f.lower().endswith(EXTS))
    OUT.mkdir(exist_ok=True)
    if not files:
        (OUT / "_status.txt").write_text("0 0\n", encoding="utf-8")
        print("doc-inbox/ 沒有文件"); return 0
    print("待處理 %d 份文件（視覺模型：%s）" % (len(files), VISION_MODEL or "(未設定)"), flush=True)

    pg = progress_mod.make([(p, clean_title(p)) for p in files])
    ok, done = 0, []
    try:
        for n, path in enumerate(files, 1):
            i = n - 1
            title = clean_title(path)
            print("[%d/%d] %s" % (n, len(files), os.path.basename(path)), flush=True)
            try:
                pg.start(i, "讀取中…")

                def on_page(cur, total, _i=i):
                    pg.step(_i, "辨識中 %d/%d 頁" % (cur, total),
                            5 + 80.0 * cur / max(1, total))

                text, how = read_doc(path, on_page)
                if not text:
                    pg.fail(i, how[:60])
                    print("  失敗：%s" % how, flush=True); continue

                paras = to_paragraphs(text)
                chars = sum(len(p) for p in paras)
                print("  讀到 %d 字 / %d 段（%s）" % (chars, len(paras), how), flush=True)

                pg.step(i, "整理摘要…", 90)
                body = "\n\n".join(paras)
                summary = groq_summary(title, body)
                pg.step(i, "分類中…", 96)
                meta = cls.classify(title, body)
                tidy = cls.tidy(title)

                stem = safe_name(title) + "__doc"
                md = [f"# {title}", "",
                      f"- 來源：上傳文件（{os.path.basename(path)}）",
                      f"- 讀取：{how}",
                      f"- 統計：{chars} 字 / {len(paras)} 段"]
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
                (OUT / f"{stem}.md").write_text("\n\n".join(md), encoding="utf-8")

                txt = title + "\n\n"
                if summary:
                    txt += "【摘要】\n" + summary + "\n\n【內文】\n\n"
                txt += "\n\n".join(paras) + "\n"
                (OUT / f"{stem}.txt").write_text(txt, encoding="utf-8")

                pg.done(i, chars)
                ok += 1
                done.append(path)
            except Exception as e:
                pg.fail(i, str(e).replace("\n", " ")[:60])
                print("  失敗：%s" % str(e).replace("\n", " ")[:200], flush=True)
    finally:
        pg.finish()

    print("完成：成功 %d / 共 %d" % (ok, len(files)), flush=True)
    # 成功的才刪；失敗的留在 inbox 等下次重試，不要讓使用者辛苦傳的檔案憑空消失
    (OUT / "_processed.txt").write_text("\n".join(done) + ("\n" if done else ""),
                                        encoding="utf-8")
    (OUT / "_status.txt").write_text("%d %d\n" % (len(files), ok), encoding="utf-8")
    return 1 if ok == 0 else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
