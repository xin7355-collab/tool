# -*- coding: utf-8 -*-
"""把使用者上傳到 audio-inbox/ 的音檔轉成逐字稿。

為什麼要這條路：YouTube 會擋機房 IP，但「音檔」不會——不管聲音是手機錄的、
直播錄下來的、還是別處匯出的，只要有檔案就能轉。這也是手機使用者唯一能
「整場自動轉逐字稿」的方式。

流程：audio-inbox/*.（m4a/mp3/wav…）→ ffmpeg 轉 16k 單聲道 →
      Groq 或本機 Whisper 辨識 → 分段 → Groq 摘要 → out/*.md/.txt/.srt
"""
import os, re, sys, glob, time, pathlib, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grab_transcripts import (OUT, safe_name, hhmmss, srt_time, to_paragraphs,
                              groq_summary, groq_polish, groq_asr_cues, asr_cues,
                              ASR_BACKEND, GROQ_API_KEY, WHISPER_MODEL, GROQ_MODEL)
import progress as progress_mod
import classify as cls

INBOX = os.environ.get("AUDIO_INBOX", "audio-inbox")
EXTS = (".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".mp4", ".webm", ".m4b")


def clean_title(path):
    """檔名 → (標題, 影片ID)：去掉時間戳前綴與 yt2deck 夾帶的 __yt 影片 ID 標記。

    影片 ID 要留著寫進逐字稿檔名，網站才能標出「這支已經抓過了」。
    """
    name = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"^\d{8}-\d{6}[_-]", "", name)    # 20260801-235959_
    vid = ""
    m = re.search(r"__yt([A-Za-z0-9_-]{11})$", name)
    if m:
        vid, name = m.group(1), name[:m.start()]
    return (name.strip() or "上傳的音檔"), vid


def main():
    files = sorted(f for f in glob.glob(os.path.join(INBOX, "*"))
                   if f.lower().endswith(EXTS))
    OUT.mkdir(exist_ok=True)
    if not files:
        # 定時重試跑到空 inbox 是常態，寫下 0/0 讓工作流知道「不用發通知」。
        (OUT / "_status.txt").write_text("0 0\n", encoding="utf-8")
        print("audio-inbox/ 沒有音檔"); return 0
    use_groq = ASR_BACKEND == "groq" and GROQ_API_KEY
    print("待處理 %d 個音檔（引擎：%s）" %
          (len(files), ("Groq " + GROQ_MODEL) if use_groq else ("本機 Whisper " + WHISPER_MODEL)),
          flush=True)
    if not use_groq and GROQ_API_KEY:
        # 有金鑰卻沒用到，多半是 repo Variable `ASR_BACKEND` 沒設。機房只有兩核 CPU，
        # 本機 Whisper 跑一小時的節目要好幾小時，整批一定撞到逾時，一支都出不來。
        print("⚠️ 有 GROQ_API_KEY 卻用本機 Whisper（ASR_BACKEND=%r）——這在機房上慢到會逾時。"
              % ASR_BACKEND, flush=True)

    # 進度發佈到 gh-pages，網站就能畫出每個檔案自己的讀取條
    titles = [(p, clean_title(p)[0]) for p in files]
    pg = progress_mod.make(titles)

    ok, done = 0, []
    try:
        ok, done = run_all(files, use_groq, pg)
    finally:
        pg.finish()          # 不管怎麼結束，都別讓進度條永遠停在「進行中」
    print("完成：成功 %d / 共 %d" % (ok, len(files)), flush=True)
    save_done(done, len(files), ok)
    # 一支都沒轉成功就回非零，讓工作流變紅並寄信。之前是永遠回 None＝綠燈，
    # inbox 卡了一整天、每兩小時重試一次全掛，畫面上還是一片綠。
    return 1 if ok == 0 else 0


# 一輪最多花這麼久。工作流上限 300 分鐘，這裡留 60 分鐘餘裕。
# audio 和 pipeline 共用同一個 concurrency group（避免兩邊同時推 gh-pages），
# 所以這一輪跑太久會擋住 YouTube 那條線——4 小時是「一次消化夠多」和
# 「別佔著鎖不放」之間的折衷。
# 實測一集財經節目要 35～40 分鐘，120 分鐘的上限一輪只做得完 3 個，29 個要跑十輪；
# 公開 repo 的 Actions 時數不計費，拉長遠比讓佇列拖兩天划算。
BUDGET_MIN = float(os.environ.get("ASR_BUDGET_MIN", "240") or "240")


def save_done(done, total, ok):
    """寫下「哪些真的轉好了」。

    每轉好一個就寫一次，不是整批跑完才寫——這是關鍵。整批才寫的話，工作流一旦
    撞到 120 分鐘上限被砍掉，這個檔案根本沒被寫出來，兩小時的辨識成果全部作廢，
    下一輪又從第一個檔案重來。實測 32 個音檔就是這樣卡死：每次跑滿兩小時、
    每次都被砍、每次都從頭再來，佇列永遠不會變短。
    """
    (OUT / "_processed.txt").write_text("\n".join(done) + ("\n" if done else ""),
                                        encoding="utf-8")
    (OUT / "_status.txt").write_text("%d %d\n" % (total, ok), encoding="utf-8")


def run_all(files, use_groq, pg):
    ok, done = 0, []
    started = time.monotonic()
    for n, path in enumerate(files, 1):
        # 第一個一定要跑（不然完全沒進度）；之後每開一個新檔案前先算「跑得完嗎」。
        # 只看「現在還沒超時」不夠：一個檔案要 40 分鐘，在第 84 分鐘開下去就會
        # 做到一半被砍——那個檔案整個白做。用已完成的平均值預估，估不下就收工。
        spent = (time.monotonic() - started) / 60.0
        if n > 1:
            avg = spent / (n - 1)
            if spent + avg > BUDGET_MIN:
                print("已用掉 %.0f 分鐘（平均一個 %.0f 分），剩下 %d 個留到下一輪"
                      "（每 2 小時自動跑，不用重傳）"
                      % (spent, avg, len(files) - n + 1), flush=True)
                break
        t0 = time.monotonic()
        title, vid = clean_title(path)
        print("[%d/%d] %s" % (n, len(files), os.path.basename(path)), flush=True)
        i = n - 1
        try:
            pg.start(i, "準備音訊…")
            if use_groq:
                def report(cur, total, _i=i):
                    # cur 是「已完成」的段數（多段是平行跑的，沒有「正在第幾段」）
                    # 辨識佔進度條的 5~90%，剩下留給摘要與寫檔
                    label = ("轉錄中…" if total == 1
                             else "轉錄中 %d/%d 段完成" % (cur, total))
                    pg.step(_i, label, 5 + 85.0 * cur / max(1, total))
                cues = groq_asr_cues(path, on_progress=report)
            else:
                # 本機 Whisper 吃得下大多數格式，但先轉檔最保險
                wav = os.path.join("recordings", safe_name(title) + ".wav")
                os.makedirs("recordings", exist_ok=True)
                subprocess.run(["ffmpeg", "-y", "-i", path, "-vn", "-ac", "1",
                                "-ar", "16000", wav],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                def report(cur, total, _i=i):
                    pg.step(_i, "轉錄中 %s / %s" % (hhmmss(cur), hhmmss(total)),
                            5 + 85.0 * cur / max(1.0, total))
                cues = asr_cues(wav, on_progress=report)
            if not cues:
                # 光說「為空」查不出原因。音檔只有幾十 KB 多半是手機那端下載就斷了，
                # 留在 inbox 每兩小時重試一次也不會變好，要看得出來才能砍掉重抓。
                kb = os.path.getsize(path) // 1024
                why = "辨識結果為空（檔案只有 %d KB，多半是下載時就壞了）" % kb if kb < 64 \
                      else "辨識結果為空"
                pg.fail(i, why[:60])
                print("  " + why, flush=True); continue

            paras = to_paragraphs(cues)
            chars = sum(len(p["t"]) for p in paras)
            stem = safe_name(title) + ("__yt" + vid if vid else "_upload")
            pg.step(i, "整理摘要…", 92)
            body = "\n".join(p["t"] for p in paras)
            summary = groq_summary(title, body)
            pg.step(i, "分類中…", 96)
            meta = cls.classify(title, body)          # 主題分類要看內容，光看標題判斷不出來
            tidy = cls.tidy(title)                    # 短標題純靠規則，離線也算得出來
            lang = ("Groq:" + GROQ_MODEL) if use_groq else ("ASR:" + WHISPER_MODEL)

            src = (f"[YouTube 影片](https://www.youtube.com/watch?v={vid})" if vid
                   else f"上傳音檔（{os.path.basename(path)}）")
            md = [f"# {title}", "", f"- 來源：{src}",
                  f"- 辨識：{lang}", f"- 統計：{chars} 字 / {len(paras)} 段"]
            # 這幾行同時給人看、也給索引程式解析（網站靠它做分類與排序）
            if meta.get("cat"):
                md.append(f"- 分類：{meta['cat']}")
            if meta.get("tags"):
                md.append("- 關鍵字：" + "、".join(meta["tags"]))
            if tidy.get("clean"):
                md.append(f"- 短標題：{tidy['clean']}")
            if tidy.get("date"):
                md.append(f"- 日期：{tidy['date']}")
            md.append("")
            if summary:
                md += ["## 摘要", "", summary, ""]
            md += ["---", ""]
            for p in paras:
                md.append(f"**[{hhmmss(p['s'])}]** {p['t']}")
            (OUT / f"{stem}.md").write_text("\n\n".join(md), encoding="utf-8")

            # .txt 是拿來讀的，給 AI 校過錯字；.md 保留原始辨識結果，
            # 因為那份帶時間碼，字要跟聲音對得起來才有意義。
            pg.step(i, "AI 校對錯字…", 97)
            fixed = groq_polish(title, [p["t"] for p in paras])
            if fixed:
                print("  已校對 %d 段" % len(fixed), flush=True)
            read = fixed or [p["t"] for p in paras]

            txt = title + "\n"
            txt += ("（此版已用 AI 校對錯字；未校對的原始辨識在 .md）\n\n" if fixed
                    else "\n")
            if summary:
                txt += "【摘要】\n" + summary + "\n\n【逐字稿】\n\n"
            txt += "\n\n".join(read) + "\n"
            (OUT / f"{stem}.txt").write_text(txt, encoding="utf-8")

            srt = []
            for k, c in enumerate(cues, 1):        # 不能用 i，那是這一輪的檔案編號
                srt.append(f"{k}\n{srt_time(c['start'])} --> "
                           f"{srt_time(c['start'] + c.get('duration', 0))}\n{c['text']}\n")
            (OUT / f"{stem}.srt").write_text("\n".join(srt), encoding="utf-8")

            print("  完成：%d 字 / %d 段（花了 %.1f 分）"
                  % (chars, len(paras), (time.monotonic() - t0) / 60.0), flush=True)
            pg.done(i, chars)
            ok += 1
            done.append(path)
            save_done(done, len(files), ok)   # 每好一個就落地，被砍掉也不會白做
        except Exception as e:
            pg.fail(i, str(e).replace("\n", " ")[:60])
            print("  失敗：%s" % str(e).replace("\n", " ")[:200], flush=True)
    return ok, done


if __name__ == "__main__":
    sys.exit(main() or 0)
