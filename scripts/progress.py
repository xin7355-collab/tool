# -*- coding: utf-8 -*-
"""把轉錄進度即時發佈出去，讓網站能畫出每個檔案的讀取條。

為什麼不用 git push：進度一分鐘要更新好幾次，每次都 clone/commit/push 太慢，
而且會跟最後發佈逐字稿的那次 push 一直打架。改用 Contents API 單發 PUT，
一次 HTTP 就好，還能靠 sha 做樂觀鎖。

為什麼寫在獨立的 status 分支，而不是 gh-pages：gh-pages 是 GitHub Pages 的
來源分支，每推一次就觸發一次 Pages 重建，而網站又只能透過 Pages 的 CDN 讀回來——
CDN 落後好幾分鐘，等它更新完轉錄早就結束了，進度條等於永遠看不到。
放在不是 Pages 來源的分支，網站改用 GitHub API 直接讀，API 不經 CDN，永遠是最新的。

拿不到 Token 或網路不通時全部靜默略過——進度條是輔助，不該弄垮轉錄本身。
"""
import os, json, time, base64, urllib.request, urllib.error

REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
BRANCH = "status"        # 刻意不是 gh-pages：見上面說明
PATH = "progress.json"
MIN_GAP = 5.0            # 秒；同一個狀態最多這麼久才回報一次


class Progress:
    def __init__(self, files, run_id=None):
        """files：[(檔名, 顯示標題)]，順序就是處理順序。"""
        self.items = [{"name": os.path.basename(n), "title": t,
                       "state": "waiting", "step": "排隊中", "pct": 0}
                      for n, t in files]
        self.run = run_id or os.environ.get("GITHUB_RUN_ID", "")
        self.sha = None
        self.last = 0.0
        self._ensure_branch()
        self._get_sha()
        self.push(force=True)

    # ---- 對外：改狀態 ----
    def start(self, i, step="準備中"):
        self.items[i].update(state="running", step=step, pct=1)
        self.push(force=True)

    def step(self, i, step, pct=None):
        it = self.items[i]
        it["step"] = step
        if pct is not None:
            it["pct"] = max(it["pct"], min(99, int(pct)))
        self.push()

    def done(self, i, chars=0):
        self.items[i].update(state="done", step="完成（%d 字）" % chars, pct=100)
        self.push(force=True)

    def fail(self, i, why=""):
        self.items[i].update(state="failed", step="失敗：" + str(why)[:60], pct=0)
        self.push(force=True)

    def finish(self):
        for it in self.items:                    # 收尾：還掛在 running 的當作沒完成
            if it["state"] == "running":
                it.update(state="failed", step="中斷", pct=0)
        self.push(force=True, state="done")

    # ---- 內部：送出 ----
    def push(self, force=False, state="running"):
        now = time.time()
        if not force and now - self.last < MIN_GAP:
            return
        self.last = now
        body = {"run": self.run, "state": state,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": self.items}
        self._put(json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8"))

    def _req(self, url, method, data=None):
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Authorization": "Bearer " + TOKEN,
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "User-Agent": "transcript-tool-progress"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    def _api(self, method, data=None):
        if not (REPO and TOKEN):
            return None
        return self._req("https://api.github.com/repos/%s/contents/%s?ref=%s"
                         % (REPO, PATH, BRANCH) if method == "GET" else
                         "https://api.github.com/repos/%s/contents/%s" % (REPO, PATH),
                         method, data)

    def _ensure_branch(self):
        """status 分支不存在就開一個（從預設分支的 HEAD 切出來）。"""
        base = os.environ.get("GITHUB_REF_NAME") or "main"
        try:
            self._req("https://api.github.com/repos/%s/git/ref/heads/%s" % (REPO, BRANCH), "GET")
            return True
        except Exception:
            pass
        try:
            head = self._req("https://api.github.com/repos/%s/git/ref/heads/%s" % (REPO, base), "GET")
            self._req("https://api.github.com/repos/%s/git/refs" % REPO, "POST",
                      json.dumps({"ref": "refs/heads/" + BRANCH,
                                  "sha": head["object"]["sha"]}).encode())
            return True
        except Exception:
            return False

    def _get_sha(self):
        try:
            j = self._api("GET")
            self.sha = (j or {}).get("sha")
        except Exception:
            self.sha = None

    def _put(self, raw):
        payload = {"message": "Update transcription progress [skip ci]",
                   "branch": BRANCH,
                   "content": base64.b64encode(raw).decode()}
        if self.sha:
            payload["sha"] = self.sha
        try:
            j = self._api("PUT", json.dumps(payload).encode())
            self.sha = ((j or {}).get("content") or {}).get("sha") or self.sha
        except urllib.error.HTTPError as e:
            if e.code == 409:                    # 有人同時改了，下次再說
                return
            if e.code == 422:                    # sha 過期 → 重抓一次再試
                self._get_sha()
                try:
                    payload["sha"] = self.sha
                    j = self._api("PUT", json.dumps(payload).encode())
                    self.sha = ((j or {}).get("content") or {}).get("sha") or self.sha
                except Exception:
                    pass
        except Exception:
            pass


class Noop:
    """沒有 Token 時用的空殼，呼叫端不用到處寫 if。"""
    def start(self, *a, **k): pass
    def step(self, *a, **k): pass
    def done(self, *a, **k): pass
    def fail(self, *a, **k): pass
    def finish(self, *a, **k): pass


def make(files, run_id=None):
    if not (REPO and TOKEN):
        return Noop()
    try:
        return Progress(files, run_id)
    except Exception:
        return Noop()
