# -*- coding: utf-8 -*-
"""台語（台灣閩南語）辨識：用 Gemini 的多模態音訊，同時產出台文與華語兩版。

為什麼不能用原本那條 Groq Whisper：
    Whisper 支援的語言清單裡**沒有閩南語**。餵台語進去、language 設成 zh，
    它不會報錯，會吐出一段文法通順、聽起來很像話、但整句都不是講者原意的國語。
    這是最難發現的壞掉方式——逐字稿看起來完全正常，只有對過音檔的人才知道是錯的。
    所以台語不是「換個參數」的事，是要換一個聽得懂台語的模型。

為什麼是 Gemini：
    它的音訊輸入本來就吃得下台灣口音與台語／國語夾雜，而且是同一次呼叫就能
    要它「逐字寫成台文 + 翻成華語」，不必辨識完再翻一次（翻兩次會把辨識的錯
    再放大一次）。使用者給的 teamtaiwan 專案走的也是這條路。

輸出的形狀刻意跟 grab_transcripts 的 cues 一樣（start／duration／text），
多帶三個欄位：

    text   華語版（跟原本的逐字稿同一個位置，摘要、分類、搜尋全都照舊能用）
    nan    台文版（教育部台灣台語推薦用字；國語的段落原樣保留）
    tailo  台羅拼音（可能是空的，模型沒把握時寧可不給）
    lang   這一段實際在講什麼：nan 台語／cmn 華語／mix 夾雜／en 英語

為什麼華語版擺在 text：整條產線下游（摘要、分類、網站搜尋、分析師工具）讀的
都是 text。台文放進去會讓它們全部讀到看不懂的字；台文獨立出一份檔案反而好找。

    GEMINI_API_KEY   金鑰（Google AI Studio 免費申請）
    GEMINI_MODEL     指定模型；不設就自動挑（模型改名時不用改程式）
"""
import os, re, sys, json, time, base64, tempfile, subprocess, glob as _glob
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

GEMINI_KEY = (os.environ.get("GEMINI_API_KEY")
              or os.environ.get("GOOGLE_API_KEY") or "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip()
BASE = "https://generativelanguage.googleapis.com/v1beta"
# 每段幾秒。Gemini 的 inline 音訊連同 base64 要壓在 20MB 內；10 分鐘的
# 16kHz 單聲道 FLAC 約 5～8MB，轉 base64 約 10MB，留了一倍的餘裕。
SEG = int(os.environ.get("TAIGI_SEG_SECONDS", "600") or "600")
# 免費額度的每分鐘請求數不高，併發開太大只會整批撞 429 然後全部重試。
CONCURRENCY = int(os.environ.get("TAIGI_CONCURRENCY", "2") or "2")
TRIES = int(os.environ.get("TAIGI_TRIES", "5") or "5")

# 挑模型時的偏好順序（比對模型名稱的子字串，愈前面愈優先）。
# 寫成清單而不是寫死一個：Google 換代很快，寫死的那天就會整批 404。
PREFER = ("transcribe", "flash-preview", "flash-latest", "flash", "pro")


class TaigiError(RuntimeError):
    pass


def available():
    """這台機器有沒有辦法跑台語辨識。"""
    return bool(GEMINI_KEY)


def _post(url, obj, timeout=300):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": GEMINI_KEY,
                 "User-Agent": "transcript-tool/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


_model_cache = [None]


def pick_model():
    """決定要用哪個模型。指定了就用指定的，沒指定就去問 Google 現在有什麼。

    不直接寫死模型名：Gemini 的 preview 模型幾個月就改名／下架一次，
    寫死的結果是某天整批工作流 404，而錯誤訊息只寫「HTTP 404」，
    看的人會以為是網路問題而一直重跑。
    """
    if _model_cache[0]:
        return _model_cache[0]
    if GEMINI_MODEL:
        _model_cache[0] = GEMINI_MODEL if GEMINI_MODEL.startswith("models/") \
            else "models/" + GEMINI_MODEL
        return _model_cache[0]
    req = urllib.request.Request(
        BASE + "/models?pageSize=200",
        headers={"x-goog-api-key": GEMINI_KEY, "User-Agent": "transcript-tool/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        raise TaigiError("問不到 Gemini 有哪些模型（%s）。"
                         "請確認 GEMINI_API_KEY 正確，或用 GEMINI_MODEL 直接指定。"
                         % str(e)[:80])
    names = [m.get("name", "") for m in j.get("models", [])
             if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    # 排除一望即知不吃音訊的（純嵌入、純畫圖、純語音合成）
    names = [n for n in names
             if not re.search(r"embed|image|imagen|veo|aqa|tts", n, re.I)]
    if not names:
        raise TaigiError("這把金鑰底下找不到可用的 Gemini 模型")
    for key in PREFER:
        hit = [n for n in names if key in n.lower()]
        if hit:
            # 同一族取名字最短的：通常是穩定別名（…-flash）而不是釘死版號的快照
            _model_cache[0] = sorted(hit, key=len)[0]
            break
    else:
        _model_cache[0] = sorted(names, key=len)[0]
    print("    台語引擎：%s" % _model_cache[0], flush=True)
    return _model_cache[0]


SYSTEM = """你是台灣在地語音的逐字稿專家，專門處理台語（台灣閩南語）與台灣國語夾雜的音訊。

把這段音訊逐句寫成逐字稿。每一句都要同時給「台文」與「華語」兩種寫法。

規則：
1. lang 標這句實際在講什麼：nan＝台語、cmn＝華語（國語）、mix＝同一句裡夾雜、en＝英語。
   財經節目常常是台語主體但夾國語術語與英文代號，那種標 mix。
2. nan＝逐字的台文。**照講者實際講出來的話寫**，不要翻成國語。
   用「教育部臺灣台語常用詞辭典」的推薦用字，例如：
   是án-ne寫成「是按呢」、無、毋、袂、佮、遮、遐、啥物、彼、這、
   欲、會使、敢、囡仔、代誌、拍、揣、鬥相共、參詳。
   這句本來就是講國語的（lang=cmn），nan 就原樣照抄，不要硬改成台文。
3. tailo＝台羅拼音（教育部臺灣台語羅馬字拼音方案，含聲調數字）。
   沒把握就給空字串，**寧可留白也不要拼錯**——錯的拼音比沒有拼音更難用。
4. zh＝同一句的華語（台灣用語的繁體中文）翻譯，要通順、可讀，不是逐字硬翻。
   這句本來就是華語的，zh 就原樣照抄。
5. 專有名詞、股票名稱、數字、英文代號一律保留原樣（台積電、聯發科、2330、ETF、AI）。
   數字照講者講的寫成阿拉伯數字。
6. timestamp 用 MM:SS，相對於**這一段音訊**的開頭。
7. 聽不清楚就寫「〔聽不清楚〕」，**不要猜**。猜出來的句子看起來最正常，也最害人。
8. 只輸出 JSON。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "speaker": {"type": "string"},
                    "lang": {"type": "string", "enum": ["nan", "cmn", "mix", "en"]},
                    "nan": {"type": "string"},
                    "tailo": {"type": "string"},
                    "zh": {"type": "string"},
                },
                "required": ["start", "end", "lang", "nan", "zh"],
            },
        }
    },
    "required": ["segments"],
}


def _mmss(s):
    """'12:34' 或 '01:02:03' → 秒。壞掉的就當 0，不要讓一個格式怪的時間戳炸掉整段。"""
    parts = re.findall(r"\d+", str(s or ""))
    if not parts:
        return 0.0
    parts = [int(p) for p in parts[-3:]]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def _call(b64, mime, hint, timeout=600):
    """送一段音訊給模型。

    hint 是這支影片的標題。各段是平行送的，拿不到「前一段的結果」當前文，
    但標題本來就拿得到，而且它帶的正是最容易聽錯的東西——分析師的名字、
    股票名稱、節目名。給了它，「兆華」就不會變成「照華」。
    """
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": mime, "data": b64}},
            {"text": ("這段音訊來自標題為「%s」的節目，裡面出現的人名、股票名、"
                      "節目名請以標題為準。" % hint[:120]) if hint else "請開始。"},
        ]}],
        "generationConfig": {"temperature": 0.2,
                             "responseMimeType": "application/json",
                             "responseSchema": SCHEMA},
    }
    url = "%s/%s:generateContent" % (BASE, pick_model())
    last = ""
    for n in range(1, TRIES + 1):
        try:
            j = _post(url, body, timeout=timeout)
            break
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            last = "HTTP %s %s" % (e.code, detail)
            if e.code == 404 and not GEMINI_MODEL:
                # 模型被下架了：清掉快取、重挑一次，而不是把 404 當網路問題重試五遍
                _model_cache[0] = None
                url = "%s/%s:generateContent" % (BASE, pick_model())
            elif e.code not in (429, 500, 502, 503, 504) or n == TRIES:
                raise TaigiError(last)
            wait = float(e.headers.get("Retry-After") or 0) or min(60, 5 * n)
            time.sleep(wait)
        except Exception as e:
            last = str(e)[:150]
            if n == TRIES:
                raise TaigiError(last)
            time.sleep(min(60, 5 * n))
    else:
        raise TaigiError(last or "重試用盡")

    cands = j.get("candidates") or []
    if not cands:
        fb = (j.get("promptFeedback") or {}).get("blockReason")
        raise TaigiError("模型沒有回應內容" + ("（被擋：%s）" % fb if fb else ""))
    c = cands[0]
    txt = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts") or [])
    if not txt.strip():
        raise TaigiError("回應是空的（finishReason=%s）" % c.get("finishReason"))
    try:
        return json.loads(txt)
    except ValueError:
        a, b = txt.find("{"), txt.rfind("}")
        if a >= 0 and b > a:
            return json.loads(txt[a:b + 1])
        raise TaigiError("回應不是 JSON")


def _split(audio_path, td):
    """切成每段 SEG 秒的 16kHz 單聲道 FLAC。短檔就不切，省掉一次 ffmpeg。"""
    dur = 0.0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", audio_path],
            capture_output=True, text=True, timeout=120).stdout.strip()
        dur = float(out or 0)
    except Exception:
        pass
    pat = os.path.join(td, "seg_%04d.flac")
    cmd = ["ffmpeg", "-y", "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "flac"]
    if dur and dur <= SEG:
        one = os.path.join(td, "seg_0000.flac")
        subprocess.run(cmd + [one], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return [one], [0.0]
    subprocess.run(cmd + ["-f", "segment", "-segment_time", str(SEG), pat],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chunks = sorted(_glob.glob(os.path.join(td, "seg_*.flac")))
    if not chunks:
        raise TaigiError("音訊切段失敗")
    return chunks, [i * SEG for i in range(len(chunks))]


def taigi_cues(audio_path, on_progress=None, hint=""):
    """台語辨識主流程。回傳的 cues 每一段都帶 text／nan／tailo／lang。

    on_progress(做完幾段, 總共幾段)：給網站畫讀取條用，可不給。
    hint：影片標題，拿來穩住人名與股票名的寫法，可不給。

    各段是平行送的（一支三小時的節目有十八段，一段一段排隊要等太久）。
    代價是切在 10 分鐘邊界上的那一句兩邊都會斷掉——換來的是整體快五倍以上，
    而斷句的損失只有十幾句，划算。
    """
    if not GEMINI_KEY:
        raise TaigiError("要做台語逐字稿需要 GEMINI_API_KEY（Google AI Studio 免費申請）")
    with tempfile.TemporaryDirectory() as td:
        chunks, offsets = _split(audio_path, td)
        n = len(chunks)
        print("    台語辨識：%d 段（每段 %d 秒）" % (n, SEG), flush=True)
        if on_progress:
            on_progress(0, n)
        done = [0]

        def one(i):
            """一段壞掉不該賠掉整支影片：回 None 代表這段失敗。"""
            try:
                with open(chunks[i], "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                return _call(b64, "audio/flac", hint)
            except Exception as e:
                print("      第 %d 段放棄（%s）" % (i + 1, str(e)[:120]), flush=True)
                return None
            finally:
                done[0] += 1
                print("    台語完成 %d/%d 段" % (done[0], n), flush=True)
                if on_progress:
                    on_progress(done[0], n)

        if n == 1:
            results = [one(0)]
        else:
            with ThreadPoolExecutor(max_workers=min(CONCURRENCY, n)) as ex:
                results = list(ex.map(one, range(n)))

    cues, failed = [], []
    for i, j in enumerate(results):
        off = offsets[i]
        if j is None:
            failed.append(i + 1)
            cues.append({"start": off, "duration": 0.0,
                         "text": "〔此段辨識失敗〕", "nan": "〔此段辨識失敗〕",
                         "tailo": "", "lang": "cmn"})
            continue
        for s in (j.get("segments") or []):
            st = _mmss(s.get("start")) + off
            en = _mmss(s.get("end")) + off
            nan = (s.get("nan") or "").strip()
            zh = (s.get("zh") or "").strip()
            if not (nan or zh):
                continue
            cues.append({
                "start": st, "duration": max(0.0, en - st),
                # 下游讀 text，所以 text 放華語；模型偶爾漏給 zh 就退回台文，
                # 寧可有字也不要整句消失。
                "text": zh or nan,
                "nan": nan or zh,
                "tailo": (s.get("tailo") or "").strip(),
                "lang": (s.get("lang") or "nan").strip().lower(),
            })
    cues.sort(key=lambda c: c["start"])
    if failed and len(failed) == len(results):
        raise TaigiError("全部 %d 段都辨識失敗" % len(results))
    if failed:
        print("    ⚠ 有 %d/%d 段辨識失敗（第 %s 段），其餘已產出"
              % (len(failed), len(results), "、".join(map(str, failed))), flush=True)
    if not cues:
        raise TaigiError("辨識完沒有任何內容")
    return cues


def detect(audio_path, seconds=90):
    """聽開頭幾十秒，判斷這支到底是台語還是國語。

    給「自動」用的。整支跑完才發現選錯引擎的代價太大（台語走 Whisper 會
    產出一整篇很通順的錯字），先花一次小呼叫問清楚划算得多。
    回傳 'nan'（台語為主）／'cmn'（國語為主），問不出來就回 None 交給呼叫端決定。
    """
    if not GEMINI_KEY:
        return None
    with tempfile.TemporaryDirectory() as td:
        clip = os.path.join(td, "probe.flac")
        try:
            # 跳過開頭 30 秒：片頭音樂、廣告、罐頭開場白都不能代表整支的語言
            subprocess.run(["ffmpeg", "-y", "-ss", "30", "-t", str(seconds),
                            "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000",
                            "-c:a", "flac", clip], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if not os.path.getsize(clip):
                raise OSError("空的")
        except Exception:
            try:
                subprocess.run(["ffmpeg", "-y", "-t", str(seconds), "-i", audio_path,
                                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", clip],
                               check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except Exception:
                return None
        try:
            with open(clip, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            return None
    body = {
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "audio/flac", "data": b64}},
            {"text": "這段錄音主要是用什麼語言講的？只回答一個詞：台語、國語、或英語。"},
        ]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2000},
    }
    try:
        j = _post("%s/%s:generateContent" % (BASE, pick_model()), body, timeout=120)
        parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        ans = "".join(p.get("text", "") for p in parts)
    except Exception as e:
        print("    語言偵測失敗（%s），照指定的引擎跑" % str(e)[:80], flush=True)
        return None
    if re.search(r"台語|臺語|閩南|台灣話|Taiwanese|Hokkien|Min\s*Nan", ans, re.I):
        return "nan"
    if re.search(r"國語|華語|中文|普通話|Mandarin|Chinese", ans, re.I):
        return "cmn"
    return None


def selftest():
    ok = True
    assert _mmss("12:34") == 754, _mmss("12:34")
    assert _mmss("01:02:03") == 3723
    assert _mmss("") == 0.0
    assert _mmss(None) == 0.0
    assert _mmss("7") == 7
    print("・時間戳解析 OK")
    if not GEMINI_KEY:
        print("・沒有 GEMINI_API_KEY，跳過連線測試（台語功能會停用）")
        return 0
    try:
        print("・挑到的模型：%s" % pick_model())
    except Exception as e:
        print("・✗ 挑模型失敗：%s" % e); ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    if len(sys.argv) > 2 and sys.argv[1] == "detect":
        print(detect(sys.argv[2]) or "（判斷不出來）"); sys.exit(0)
    if len(sys.argv) > 1:
        cues = taigi_cues(sys.argv[1])
        for c in cues[:40]:
            print("[%7.1f] (%s) %s" % (c["start"], c["lang"], c["nan"]))
            print("          → %s" % c["text"])
        sys.exit(0)
    print(__doc__)
