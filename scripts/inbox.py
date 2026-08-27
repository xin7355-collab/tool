# -*- coding: utf-8 -*-
"""待處理檔案的暫存區：放在 GitHub Release 附件，不進 git 歷史。

為什麼要這樣：音檔本來是 commit 進 `audio-inbox/`，處理完再 `git rm`。
但 git 只是「在最新版本裡看不到」，位元組永遠留在歷史裡——實測 173 個音檔
把 repo 撐到 1.2GB，其中 99.4% 是早就「刪掉」的檔案。GitHub 建議 repo 在 1GB 內。

Release 附件是官方給大檔案用的地方，**不算進 repo 歷史**，刪掉就是真的沒了。
但附件本身不會觸發工作流，所以每個附件搭配一個幾十位元組的標記檔 `inbox/<id>.json`，
推上去才會啟動產線。標記檔很小，一千次上傳也才幾十 KB。

    python scripts/inbox.py pull [audio|doc]   把待處理的附件抓下來放進本機收件匣
    python scripts/inbox.py clear              刪掉已處理好的附件與標記
"""
import os, re, sys, json, glob, time, subprocess
import urllib.request, urllib.parse, urllib.error

OWNER = os.environ.get("GITHUB_REPOSITORY", "xin7355-collab/tool").split("/")[0]
REPO = os.environ.get("GITHUB_REPOSITORY", "xin7355-collab/tool").split("/")[-1]
API = "https://api.github.com/repos/%s/%s" % (OWNER, REPO)
UPLOADS = "https://uploads.github.com/repos/%s/%s" % (OWNER, REPO)
TAG = os.environ.get("INBOX_TAG", "inbox")
MARK_DIR = "inbox"
AUDIO_DIR = os.environ.get("AUDIO_INBOX", "audio-inbox")
DOC_DIR = os.environ.get("DOC_INBOX", "doc-inbox")
MANIFEST = "out/_inbox.json"
AUDIO_EXT = (".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac",
             ".mp4", ".webm", ".m4b", ".mov", ".mkv")


def token():
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def req(url, method="GET", data=None, headers=None, tok=None, timeout=600):
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "transcript-tool/1.0"}
    t = tok or token()
    if t:
        h["Authorization"] = "Bearer " + t
    h.update(headers or {})
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read()
    return body


def jreq(url, method="GET", obj=None, tok=None):
    data = json.dumps(obj).encode() if obj is not None else None
    hdr = {"Content-Type": "application/json"} if obj is not None else None
    body = req(url, method, data, hdr, tok)
    return json.loads(body.decode("utf-8", "replace")) if body else {}


def release(tok=None, create=True):
    """拿到（必要時建立）當暫存區用的那個 Release。"""
    try:
        return jreq(API + "/releases/tags/" + TAG, tok=tok)
    except urllib.error.HTTPError as e:
        if e.code != 404 or not create:
            raise
    return jreq(API + "/releases", "POST", {
        "tag_name": TAG, "name": "待處理檔案暫存區",
        "prerelease": True, "make_latest": "false",
        "body": ("這個 Release 是系統用來暫存「還沒處理完」的音檔與文件的地方。\n"
                 "附件不算進 git 歷史，處理完就會自動刪掉。**請不要手動改這裡。**"),
    }, tok=tok)


def assets(rel=None, tok=None):
    rel = rel or release(tok=tok)
    return jreq(API + "/releases/%s/assets?per_page=100" % rel["id"], tok=tok)


def put_asset(local_path, name, tok=None, rel=None):
    """上傳一個附件。名稱要純 ASCII——中文檔名在附件網址上會被轉義得很難認。"""
    rel = rel or release(tok=tok)
    with open(local_path, "rb") as f:
        data = f.read()
    url = "%s/releases/%s/assets?name=%s" % (UPLOADS, rel["id"], urllib.parse.quote(name))
    return json.loads(req(url, "POST", data,
                          {"Content-Type": "application/octet-stream"}, tok).decode())


def get_asset(asset_id, dest, tok=None):
    data = req(API + "/releases/assets/%s" % asset_id, "GET", None,
               {"Accept": "application/octet-stream"}, tok)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def del_asset(asset_id, tok=None):
    try:
        req(API + "/releases/assets/%s" % asset_id, "DELETE", tok=tok)
        return True
    except urllib.error.HTTPError as e:
        return e.code == 404          # 已經不在了也算成功


def safe_id(prefix, n, ext):
    """附件名稱一律純 ASCII。中文留在標記檔裡，附件只要一個認得出來的編號。"""
    p = re.sub(r"[^0-9A-Za-z-]", "", prefix).strip("-")[:20] or "file"
    return "%s-%03d%s" % (p, n, (ext or "").lower())


def mark(asset_name, filename, kind):
    """寫一個標記檔。推上去才會觸發工作流——附件本身不會。"""
    os.makedirs(MARK_DIR, exist_ok=True)
    mid = os.path.splitext(asset_name)[0]
    path = os.path.join(MARK_DIR, mid + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"asset": asset_name, "file": filename, "kind": kind},
                  f, ensure_ascii=False)
        f.write("\n")
    return path


def kind_of(name):
    return "audio" if name.lower().endswith(AUDIO_EXT) else "doc"


def pull(want=None):
    """把標記指到的附件抓下來，放進本機收件匣，並記下對應關係。

    收件匣是本機工作目錄（已在 .gitignore），不會再被 commit 進 repo。
    """
    tok = token()
    if not tok:
        print("沒有 GITHUB_TOKEN，跳過"); return 0
    marks = sorted(glob.glob(os.path.join(MARK_DIR, "*.json")))
    if not marks:
        print("inbox/ 沒有待處理的標記")
    by_name = {}
    if marks:
        try:
            by_name = {a["name"]: a for a in assets(tok=tok)}
        except Exception as e:
            print("::error::讀不到 Release 附件：%s" % str(e)[:120]); return 1

    got, man = 0, []
    for mp in marks:
        try:
            with open(mp, encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, ValueError):
            print("  標記壞了，略過：%s" % mp); continue
        kind = m.get("kind") or kind_of(m.get("file") or "")
        if want and kind != want:
            continue
        # 退路：瀏覽器如果被擋在 uploads.github.com（跨網域），會照舊把檔案
        # 直接寫進 repo，標記裡改記 path。那種情況檔案已經跟著 checkout 下來了。
        if m.get("path"):
            if not os.path.exists(m["path"]):
                print("  %s 不在了，清掉這個標記" % m["path"])
                os.remove(mp); continue
            got += 1
            man.append({"local": m["path"], "asset_id": None,
                        "mark": mp, "repo": m["path"]})
            print("  ・%s（在 repo 裡，沒走附件）" % m["path"], flush=True)
            continue
        a = by_name.get(m.get("asset"))
        if not a:
            # 附件不見了（手動刪掉、或上次刪附件成功但標記沒刪掉），標記留著沒意義
            print("  找不到附件 %s，清掉這個標記" % m.get("asset"))
            os.remove(mp); continue
        dest = os.path.join(AUDIO_DIR if kind == "audio" else DOC_DIR,
                            m.get("file") or m["asset"])
        try:
            n = get_asset(a["id"], dest, tok)
        except Exception as e:
            print("  下載失敗 %s：%s" % (m["asset"], str(e)[:80])); continue
        got += 1
        man.append({"local": dest, "asset_id": a["id"], "mark": mp})
        print("  ⬇ %s（%.1fMB）→ %s" % (m["asset"], n / 1048576.0, dest), flush=True)

    os.makedirs("out", exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print("取回 %d 個待處理檔案" % got, flush=True)
    return 0


def clear():
    """處理成功的：刪附件、刪標記。失敗的原封不動留著等下次重試。"""
    tok = token()
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError):
        man = []
    try:
        with open("out/_processed.txt", encoding="utf-8") as f:
            done = {ln.strip() for ln in f if ln.strip()}
    except OSError:
        done = set()
    if not done:
        print("沒有成功處理的檔案，附件全部保留待重試"); return 0

    gone, rm = 0, 0
    for it in man:
        if it["local"] not in done:
            continue
        if it.get("asset_id"):
            if del_asset(it["asset_id"], tok):
                gone += 1
        elif it.get("repo"):
            # 走退路直接進 repo 的那些，只能照舊 git rm（歷史還是會留著）
            subprocess.run(["git", "rm", "-q", "--ignore-unmatch", "--", it["repo"]],
                           check=False)
            rm += 1
        try:
            os.remove(it["mark"])
        except OSError:
            pass
    print("刪掉 %d 個已處理的附件%s"
          % (gone, ("；另有 %d 個是直接進 repo 的，只能 git rm" % rm) if rm else ""),
          flush=True)

    # 標記檔是唯一還在 git 裡的東西，刪掉要 commit 才算數
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
    subprocess.run(["git", "config", "user.email",
                    "41898282+github-actions[bot]@users.noreply.github.com"], check=False)
    subprocess.run(["git", "add", "-A", MARK_DIR], check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
        subprocess.run(["git", "commit", "-m", "Clear processed inbox markers [skip ci]"],
                       check=False)
        for k in range(3):
            if subprocess.run(["git", "push", "origin", "HEAD:main"]).returncode == 0:
                break
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
            time.sleep(3)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        sys.exit(pull(sys.argv[2] if len(sys.argv) > 2 else None))
    if cmd == "clear":
        sys.exit(clear())
    sys.exit("用法：inbox.py pull [audio|doc] | inbox.py clear")
