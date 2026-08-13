# -*- coding: utf-8 -*-
"""把「這段時間新增的逐字稿」整理成一則每日重點，寫成 Issue 推到手機。

判斷「哪些是新的」不看時間，看 .watch-state.json 裡的 digest.reported 清單。
用時間窗（例如 24 小時內）看起來比較直覺，但產線跑多久是不一定的——直播檔可能
轉一個多小時，剛好卡在窗邊就整篇漏掉，而且漏掉不會有人發現。改成記名單之後，
不管產線什麼時候完成，下一次彙整一定會把它算進去，只會晚不會掉。

第一次執行時 reported 是空的，這時把現有的全部登記起來但不發通知——不然一開場
就丟 258 篇到通知裡。
"""
import json, os, re, sys, glob
from datetime import datetime, timezone, timedelta

SITE = os.environ.get("SITE_DIR") or "site/transcripts"
STATE = os.environ.get("WATCH_STATE") or ".watch-state.json"
OUT = os.environ.get("DIGEST_OUT") or "digest.md"
MAX_ITEMS = int(os.environ.get("DIGEST_MAX") or "12")   # 一則通知放幾篇，其餘只給連結
SITE_URL = os.environ.get("SITE_URL") or ""
STALE_DAYS = 2      # pending 超過這麼久還沒轉出來就一起回報，免得默默失敗


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_md(path):
    """回 (標題, 摘要, 分類, 影片連結)。摘要是「## 摘要」到下一個標題之間那段。"""
    title, cat, summary, link = os.path.basename(path), "", "", ""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return title, "", "", ""
    first = text.split("\n", 1)[0].strip()
    if first.startswith("# "):
        title = first[2:].strip()
    m = re.search(r"^-\s*分類：\s*(.+)$", text, re.M)
    if m:
        cat = m.group(1).strip()
    m = re.search(r"^-\s*(?:影片|來源)：\s*(\S+)", text, re.M)
    if m:
        link = m.group(1).strip()
    m = re.search(r"^##\s*摘要\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if m:
        summary = m.group(1).strip()
    return title, summary, cat, link


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

    lines, title_txt = [], ""
    if new:
        # 同一類的排在一起，一次讀比較不跳
        items = sorted(((read_md(os.path.join(SITE, f)), f) for f in new),
                       key=lambda x: (x[0][2] or "zz", x[0][0]))
        lines += ["### 📬 今天的新逐字稿（%d 篇）" % len(new), ""]
        for (title, summary, cat, link), _f in items[:MAX_ITEMS]:
            head = "#### %s%s" % (title, ("　`%s`" % cat) if cat else "")
            lines += [head, ""]
            lines.append(summary if summary else "_（這篇沒有摘要，可能是摘要服務當時忙線）_")
            if link:
                lines += ["", "[▶ 看原影片](%s)" % link]
            lines.append("")
        if len(new) > MAX_ITEMS:
            lines += ["_還有 %d 篇沒列出來，到網站看。_" % (len(new) - MAX_ITEMS), ""]
        title_txt = "📬 今天 %d 篇：%s" % (len(new), items[0][0][0][:40].replace("\n", " "))

    if unwarned:
        # 沒有新逐字稿卻有卡住的＝全都失敗了。這種時候更需要講，不然是徹底的靜默失敗
        if new:
            lines.append("---")
        lines += ["⏳ 這幾支還沒轉成功，系統每 3 小時會自動重試（超過 %d 天才放棄）：" % 7, ""]
        for it in unwarned[:8]:
            lines.append("- %s｜%s" % (it.get("c", "?"), (it.get("t") or it.get("v", ""))[:50]))
        lines += ["", "常見原因是影片還在 YouTube 那邊轉檔（直播剛結束），或需要登入才能看。", ""]
        dg["warned"] = sorted(warned | {it.get("v") for it in unwarned if it.get("v")})
        if not title_txt:
            title_txt = "⏳ 有 %d 支影片還沒轉成功" % len(unwarned)

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
