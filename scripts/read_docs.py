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
import os, re, sys, glob, json, time, base64, shutil, tempfile, subprocess
import urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grab_transcripts import OUT, safe_name, groq_summary, GROQ_API_KEY
import progress as progress_mod
import classify as cls

INBOX = os.environ.get("DOC_INBOX", "doc-inbox")
PDF_EXT = (".pdf",)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif")
EXTS = PDF_EXT + IMG_EXT
# 文字層抽出來少於這麼多字，就當作「這是掃描件」改走 OCR。
# 掃描的 PDF 常常還是有零星幾個字（頁碼、浮水印），不能只看有沒有。
MIN_TEXT = int(os.environ.get("DOC_MIN_TEXT", "120") or "120")
MAX_PAGES = int(os.environ.get("DOC_MAX_PAGES", "40") or "40")
# 這個模型在 Groq 目前是 preview 狀態，可能被換掉；換掉時會 404，
# 程式會自動退到 Tesseract，不會整批失敗。要換模型改這裡就好。
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b").strip()
MAX_IMG_B64 = 3_500_000          # Groq 內嵌圖片的大小上限，留點餘裕

OCR_SYS = (
    "你是文件辨識工具。把圖片裡的文字原封不動抄出來，用繁體中文輸出。\n"
    "只輸出文字本身：不要加開場白、不要說明、不要摘要、不要改寫或補字。\n"
    "保留原本的段落與換行；表格請用 Markdown 表格；標題保留成一行。\n"
    "看不清楚的字用 □ 代替，不要猜。整張圖沒有文字就只回一行：（沒有文字）"
)


def which(name):
    return shutil.which(name)


# ---------- 第 1 層：PDF 文字層 ----------

def pdf_text(path):
    """抽 PDF 內嵌的文字層。沒有 pypdf 或抽不到就回空字串。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(path)
    except Exception as e:
        print("    PDF 讀不開（%s）" % str(e)[:60], flush=True)
        return ""
    out = []
    for page in reader.pages[:MAX_PAGES]:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return "\n\n".join(t.strip() for t in out if t.strip())


def pdf_to_images(path, tmpdir):
    """把 PDF 每頁轉成 JPEG 給 OCR 用。150dpi 對印刷字體夠了，檔案也小。"""
    if not which("pdftoppm"):
        raise RuntimeError("沒有 pdftoppm（poppler-utils），無法把掃描 PDF 轉成圖片")
    pat = os.path.join(tmpdir, "page")
    subprocess.run(["pdftoppm", "-jpeg", "-r", "150", "-l", str(MAX_PAGES), path, pat],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sorted(glob.glob(pat + "*.jpg"))


# ---------- 第 2 層：Groq 視覺 ----------

def shrink(path):
    """太大的圖縮到 Groq 吃得下。回傳可以直接讀的檔案路徑。"""
    if os.path.getsize(path) * 4 // 3 <= MAX_IMG_B64:
        return path
    try:
        from PIL import Image
    except ImportError:
        return path                      # 沒 Pillow 就照原樣送，失敗再退 Tesseract
    try:
        im = Image.open(path).convert("RGB")
        tmp, out = path + ".small.jpg", path
        for w in (2200, 1700, 1300, 1000):
            small = im.copy()
            small.thumbnail((w, w * 4), Image.LANCZOS)
            small.save(tmp, "JPEG", quality=85, optimize=True)
            out = tmp
            if os.path.getsize(tmp) * 4 // 3 <= MAX_IMG_B64:
                break
        return out
    except Exception:
        return path                      # 縮不成就照原樣送，失敗再退 Tesseract


def ocr_groq(path):
    """送一張圖給 Groq 的視覺模型。失敗回 ("", 原因)。"""
    if not GROQ_API_KEY:
        return "", "沒有 GROQ_API_KEY"
    p = shrink(path)
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    if len(b64) > MAX_IMG_B64:
        return "", "圖片太大（%d KB）" % (len(b64) // 1024)
    body = json.dumps({
        "model": VISION_MODEL, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": OCR_SYS},
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
            txt = (j.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
            return txt, ""
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

def ocr_tesseract(path):
    if not which("tesseract"):
        return "", "機器上沒有 tesseract"
    try:
        r = subprocess.run(["tesseract", path, "stdout", "-l", "chi_tra+eng", "--psm", "3"],
                           capture_output=True, timeout=180)
        return r.stdout.decode("utf-8", "replace").strip(), ""
    except Exception as e:
        return "", str(e)[:80]


def ocr_page(path):
    """一張圖 → (文字, 用了哪條路)。Groq 優先，失敗退 Tesseract。"""
    txt, why = ocr_groq(path)
    if txt and "（沒有文字）" not in txt:
        return txt, "groq"
    if txt:
        return "", "groq"                       # 模型說這張沒有文字，就別再花時間
    print("      Groq 讀不到（%s），改用 Tesseract" % why, flush=True)
    txt, why2 = ocr_tesseract(path)
    if txt:
        return txt, "tesseract"
    print("      Tesseract 也讀不到（%s）" % why2, flush=True)
    return "", "none"


# ---------- 整份文件 ----------

def read_doc(path, on_page=None):
    """回傳 (文字, 怎麼讀到的)。讀不出來就回 ("", 原因)。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXT:
        text = pdf_text(path)
        if len(re.sub(r"\s", "", text)) >= MIN_TEXT:
            return text, "PDF 內建文字層"
        print("    沒有文字層（或幾乎是空的），當成掃描件做辨識…", flush=True)
        with tempfile.TemporaryDirectory() as td:
            pages = pdf_to_images(path, td)
            if not pages:
                return "", "PDF 轉不出圖片"
            got, ways = [], set()
            for i, p in enumerate(pages, 1):
                if on_page:
                    on_page(i, len(pages))
                t, how = ocr_page(p)
                ways.add(how)
                if t:
                    got.append(t)
            if not got:
                return "", "每一頁都辨識不出文字"
            ways.discard("none")
            return "\n\n".join(got), "辨識 %d 頁（%s）" % (len(pages), "＋".join(sorted(ways)) or "?")
    if on_page:
        on_page(1, 1)
    t, how = ocr_page(path)
    if not t:
        return "", "圖片辨識不出文字"
    return t, {"groq": "圖片辨識（Groq 視覺）", "tesseract": "圖片辨識（Tesseract）"}.get(how, "圖片辨識")


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
