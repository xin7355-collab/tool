# -*- coding: utf-8 -*-
"""把「這段時間的進度」整理成一則通知，寫成 Issue 推到手機。

通知裡**只講數量，不講內容**。原因是這個 repo 是公開的，Issue 的標題與內文
會被搜尋引擎收錄——以前這裡會把每篇的標題和整段摘要寫進去，結果 Google 上
搜得到「📬 今天 1 篇：【…】」這種標題。要看是哪幾篇、摘要寫什麼，點進網站看。

判斷「哪些是新的」不看時間，看 .watch-state.json 裡的 digest.reported 清單。
用時間窗（例如 24 小時內）看起來比較直覺，但產線跑多久是不一定的——直播檔可能
轉一個多小時，剛好卡在窗邊就整篇漏掉，而且漏掉不會有人發現。改成記名單之後，
不管產線什麼時候完成，下一次彙整一定會把它算進去，只會晚不會掉。

第一次執行時 reported 是空的，這時把現有的全部登記起來但不發通知——不然一開場
就丟 258 篇到通知裡。
"""
import json, os, sys, glob
from datetime import datetime, timezone, timedelta

SITE = os.environ.get("SITE_DIR") or "site/transcripts"
STATE = os.environ.get("WATCH_STATE") or ".watch-state.json"
OUT = os.environ.get("DIGEST_OUT") or "digest.md"
SITE_URL = os.environ.get("SITE_URL") or ""
STALE_DAYS = 2      # pending 超過這麼久還沒轉出來就一起回報，免得默默失敗


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main():
    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(SITE, "*.md")))
    state = load(STATE, {})
    dg = state.setdefault("digest", {})
    reported = dg.get("reported") or []
    known = set(reported)
    first_run = not reported

    new = [f for f in files if f not in known]
    dg["reported"] = sorted(set(reported) | set(files))
    dg["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def save():
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

    if first_run:
        save()
        print("第一次彙整：先把現有 %d 篇登記起來，這次不發通知" % len(files))
        return 0

    # 追蹤中但遲遲沒轉出來的。卡住的名單只警告一次，不然同一批片子會天天洗版
    cut = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).date().isoformat()
    warned = set(dg.get("warned") or [])
    stuck = [it for it in (state.get("pending") or []) if it.get("d", "") <= cut]
    unwarned = [it for it in stuck if it.get("v") not in warned]

    # 已經不在待轉清單的（轉好了或放棄了）就不用再記著。這件事要在任何提早結束
    # 之前做完——擺在後面的話，安靜的那幾天永遠跑不到，名單就只進不出了。
    alive = {it.get("v") for it in (state.get("pending") or [])}
    warned &= alive
    dg["warned"] = sorted(warned)

    if not new and not unwarned:
        save()
        print("這段時間沒有新的逐字稿，也沒有新卡住的，不發通知")
        return 0

    # 通知是開一個 GitHub Issue 送出去的，而這個 repo 是公開的——Issue 的標題和
    # 內文會被搜尋引擎收錄。實測搜 site:xin7355-collab.github.io 就會看到
    # 「📬 今天 1 篇：【…台股崩盤主因?】」這種標題直接出現在搜尋結果裡。
    #
    # 所以這裡**只講數量，不講內容**：幾篇、幾支卡住、一個連結。
    # 要看是哪幾篇、摘要寫什麼，點進網站看——網站本身不會被這樣收錄。
    # 少了「在通知裡直接讀摘要」的方便，換的是標題和摘要不再進 Google。
    lines, title_txt = [], ""
    if new:
        lines += ["### 📬 有 %d 篇新的整理好了" % len(new), ""]
        title_txt = "📬 今天 %d 篇" % len(new)

    if unwarned:
        # 沒有新逐字稿卻有卡住的＝全都失敗了。這種時候更需要講，不然是徹底的靜默失敗
        if new:
            lines.append("---")
        lines += ["⏳ 有 %d 支還沒處理完，系統每 3 小時會自動重試（超過 %d 天才放棄）。"
                  % (len(unwarned), 7), "",
                  "常見原因是來源還在轉檔（剛結束的直播），或需要登入才能看。", ""]
        dg["warned"] = sorted(warned | {it.get("v") for it in unwarned if it.get("v")})
        if not title_txt:
            title_txt = "⏳ 有 %d 支還沒處理完" % len(unwarned)

    if SITE_URL:
        lines.append("👉 [開啟逐字稿抽取台](%s)" % SITE_URL)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    save()

    with open(os.environ.get("GITHUB_OUTPUT") or os.devnull, "a") as f:
        f.write("count=%d\n" % max(len(new), 1))
        f.write("title=%s\n" % title_txt)
    print("彙整 %d 篇、卡住 %d 支 → %s" % (len(new), len(unwarned), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
