# -*- coding: utf-8 -*-
"""把 Google 雲端硬碟資料夾裡的新檔案收進來，交給既有的產線處理。

為什麼用「服務帳號」而不是一般的 Google 登入：
  一般 OAuth 要跑同意畫面，而個人專案的同意畫面停在「測試」狀態時，
  拿到的 refresh token **每 7 天就失效**——等於每週都要重新登入一次。
  服務帳號是一個機器人 Google 帳號，金鑰不會過期，你只要把資料夾
  「分享」給它的信箱就好，資料夾本身維持私人，沒有變成公開連結。

收進來之後不自己處理，而是丟進既有的收件匣，讓原本的工作流接手：
  影音 → audio-inbox/  → audio.yml 轉逐字稿
  文件 → doc-inbox/    → docs.yml  讀成文字
Google 文件／試算表／簡報會先匯出成 PDF 或純文字再丟進去。

每個檔案只收一次（記在 .gdrive-state.json），你在雲端硬碟裡放著不會被重複處理。
"""
import os, re, io, sys, json, time, base64, mimetypes
import urllib.request, urllib.parse, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inbox

CONF = os.environ.get("GDRIVE_CONF", "gdrive.json")
STATE = os.environ.get("GDRIVE_STATE", ".gdrive-state.json")
AUDIO_DIR = os.environ.get("AUDIO_INBOX", "audio-inbox")
DOC_DIR = os.environ.get("DOC_INBOX", "doc-inbox")
# 一次最多收幾個。雲端硬碟裡塞了幾十個大檔時，一次全下載會把 repo 撐爆、
# 工作流也會逾時。分批收，剩下的下一輪自然會補上。
MAX_FILES = int(os.environ.get("GDRIVE_MAX_FILES", "10") or "10")
MAX_DEPTH = int(os.environ.get("GDRIVE_MAX_DEPTH", "3") or "3")
AUDIO_MB = float(os.environ.get("GDRIVE_AUDIO_MB", "45") or "45")
DOC_MB = float(os.environ.get("GDRIVE_DOC_MB", "20") or "20")

API = "https://www.googleapis.com/drive/v3"
GAPP = "application/vnd.google-apps."
# Google 原生格式沒有檔案本體，要指定匯出成什麼。
# 文件匯出純文字最乾淨；試算表和簡報匯出 PDF——PDF 有文字層，
# 讀取端直接抽就好，不用辨識，版面和多個工作表也都留得住。
EXPORT = {
    GAPP + "document":     ("text/plain", ".txt"),
    GAPP + "spreadsheet":  ("application/pdf", ".pdf"),
    GAPP + "presentation": ("application/pdf", ".pdf"),
    GAPP + "drawing":      ("application/pdf", ".pdf"),
}
TEXT_TYPES = ("text/plain", "text/markdown", "text/csv", "text/x-markdown")
DOC_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
           ".tif", ".tiff", ".heic", ".heif", ".txt", ".md", ".csv")
AUDIO_EXT = (".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac",
             ".mp4", ".webm", ".m4b", ".mov", ".mkv")


def load(path, dflt):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return dflt


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def token(sa):
    """用服務帳號金鑰換一張 access token。只要唯讀權限。"""
    import jwt                                    # PyJWT[crypto]
    now = int(time.time())
    claim = {"iss": sa["client_email"],
             "scope": "https://www.googleapis.com/auth/drive.readonly",
             "aud": "https://oauth2.googleapis.com/token",
             "iat": now, "exp": now + 3600}
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt.encode(claim, sa["private_key"], algorithm="RS256"),
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["access_token"]


def api(tok, path, params=None, raw=False):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read() if raw else json.loads(r.read().decode("utf-8", "replace"))


def folder_name(tok, fid):
    try:
        return api(tok, "/files/" + fid,
                   {"fields": "name", "supportsAllDrives": "true"}).get("name") or fid
    except Exception:
        return fid


def walk(tok, fid, depth=0):
    """列出資料夾（含子資料夾）裡的檔案。"""
    out, page = [], None
    while True:
        p = {"q": "'%s' in parents and trashed=false" % fid,
             "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
             "pageSize": "200", "orderBy": "modifiedTime",
             "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if page:
            p["pageToken"] = page
        j = api(tok, "/files", p)
        for f in j.get("files") or []:
            if f["mimeType"] == GAPP + "folder":
                if depth < MAX_DEPTH:
                    out += walk(tok, f["id"], depth + 1)
            else:
                out.append(f)
        page = j.get("nextPageToken")
        if not page:
            return out


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|#%&{}$!\'@+`=\s]+', "_", str(s or "file"))
    return re.sub(r"_+", "_", s).strip("_")[:60] or "file"


def route(name, mime):
    """決定這個檔案該進哪個收件匣。回傳 (資料夾, 副檔名, 大小上限MB)，不收就回 None。"""
    ext = os.path.splitext(name)[1].lower()
    if mime in EXPORT:
        return DOC_DIR, EXPORT[mime][1], DOC_MB
    if mime.startswith(GAPP):
        return None                              # 表單、地圖之類的沒有內容可讀
    if mime.startswith("audio/") or mime.startswith("video/") or ext in AUDIO_EXT:
        return AUDIO_DIR, ext or ".m4a", AUDIO_MB
    if mime == "application/pdf" or mime.startswith("image/") or ext in DOC_EXT:
        return DOC_DIR, ext or ".pdf", DOC_MB
    if mime in TEXT_TYPES:
        return DOC_DIR, ".txt", DOC_MB
    return None


def fetch(tok, f):
    if f["mimeType"] in EXPORT:
        return api(tok, "/files/%s/export" % f["id"],
                   {"mimeType": EXPORT[f["mimeType"]][0]}, raw=True)
    return api(tok, "/files/" + f["id"],
               {"alt": "media", "supportsAllDrives": "true"}, raw=True)


def main():
    conf = load(CONF, {})
    folders = [x for x in (conf.get("folders") or []) if x.get("id")]
    st = load(STATE, {})
    seen = st.get("seen") or {}
    st["checked"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())

    raw = os.environ.get("GDRIVE_KEY", "").strip()
    if not raw:
        print("沒有設定 GDRIVE_KEY（服務帳號金鑰），跳過。", flush=True)
        return 0
    try:
        sa = json.loads(raw)
        tok = token(sa)
    except Exception as e:
        st["err"] = "服務帳號金鑰有問題：%s" % str(e).replace("\n", " ")[:120]
        save(STATE, st)
        print("::error::" + st["err"], flush=True)
        return 1
    st["account"] = sa.get("client_email", "")
    st.pop("err", None)
    print("服務帳號：%s；追蹤 %d 個資料夾" % (st["account"], len(folders)), flush=True)
    if not folders:
        st["seen"] = seen
        save(STATE, st)
        print("還沒加入任何資料夾。"); return 0

    try:
        rel = inbox.release()
    except Exception as e:
        print("::error::拿不到暫存用的 Release：%s" % str(e)[:120], flush=True)
        return 1
    took, skipped, errs = 0, [], {}
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())

    for i, fo in enumerate(folders):
        fid = fo["id"]
        try:
            if not fo.get("name"):
                fo["name"] = folder_name(tok, fid)       # 認出名字寫回設定檔
            files = walk(tok, fid)
        except urllib.error.HTTPError as e:
            why = ("這個資料夾沒有分享給 %s" % st["account"]) if e.code in (403, 404) \
                  else ("HTTP %s" % e.code)
            errs[fid] = why
            print("  ・%s：讀不到（%s）" % (fo.get("name") or fid, why), flush=True)
            continue
        except Exception as e:
            errs[fid] = str(e).replace("\n", " ")[:100]
            print("  ・%s：讀不到（%s）" % (fo.get("name") or fid, errs[fid]), flush=True)
            continue

        fresh = [f for f in files if f["id"] not in seen]
        print("  ・%s：%d 個檔案，其中 %d 個是新的"
              % (fo.get("name") or fid, len(files), len(fresh)), flush=True)

        for f in fresh:
            if took >= MAX_FILES:
                break
            dest = route(f["name"], f["mimeType"])
            if not dest:
                seen[f["id"]] = f["name"]            # 收不了的也記下來，不要每次重看
                skipped.append("%s（%s 不支援）" % (f["name"], f["mimeType"]))
                continue
            folder, ext, cap = dest
            mb = int(f.get("size") or 0) / 1048576.0
            if mb > cap:
                seen[f["id"]] = f["name"]
                skipped.append("%s（%.1fMB，超過 %.0fMB）" % (f["name"], mb, cap))
                continue
            base = os.path.splitext(f["name"])[0]
            filename = "%s-%03d_%s%s" % (stamp, took + 1, safe_name(base), ext)
            try:
                data = fetch(tok, f)
            except Exception as e:
                print("    下載失敗 %s：%s" % (f["name"], str(e)[:80]), flush=True)
                continue                              # 這次不記，下一輪再試
            # 檔案本體上傳成 Release 附件，repo 裡只留一個很小的標記檔。
            # 直接 commit 進 repo 的話，每個檔案都會永遠留在 git 歷史裡。
            tmp = os.path.join("out", filename)
            os.makedirs("out", exist_ok=True)
            with open(tmp, "wb") as fh:
                fh.write(data)
            try:
                asset = inbox.safe_id(stamp, took + 1, ext)
                inbox.put_asset(tmp, asset, rel=rel)
                inbox.mark(asset, filename, "audio" if folder == AUDIO_DIR else "doc")
            except Exception as e:
                print("    上傳附件失敗 %s：%s" % (f["name"], str(e)[:80]), flush=True)
                continue                              # 這次不記，下一輪再試
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            seen[f["id"]] = f["name"]
            took += 1
            print("    ⬇ %s → %s（%.1fMB）" % (f["name"], folder, len(data) / 1048576.0), flush=True)
        folders[i] = fo

    if skipped:
        print("略過 %d 個：%s" % (len(skipped), "、".join(skipped[:8])), flush=True)
    st["seen"] = seen
    st["skipped"] = skipped[-20:]
    st["folders"] = {x["id"]: x.get("name", "") for x in folders}
    st["errs"] = errs
    st["took"] = took
    save(STATE, st)
    conf["folders"] = folders
    save(CONF, conf)
    print("這次收進 %d 個檔案%s"
          % (took, "（還有更多，下一輪繼續）" if took >= MAX_FILES else ""), flush=True)
    # 資料夾讀不到是設定問題（沒分享），要讓人看得到；但別因為某一個壞掉就全紅
    if errs and len(errs) == len(folders):
        print("::error::每個資料夾都讀不到，多半是還沒分享給服務帳號", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
