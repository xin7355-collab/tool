# -*- coding: utf-8 -*-
"""台語羅馬字的兩套寫法互轉：白話字（POJ）↔ 教育部台羅（Tâi-lô）。

為什麼需要這個：
    台語的羅馬字不是只有一套。教會傳下來的**白話字 POJ**（chia̍h、oa、eng、kiaⁿ）
    和教育部公告的**台羅 Tâi-lô**（tsia̍h、ua、ing、kiann）拼法不同，但唸起來
    一模一樣。模型有時候回 POJ、有時候回台羅，混在同一份檔案裡看起來就像拼錯。

    TW-Hokkien-LLM 那篇論文（Lu et al. 2024, arXiv:2403.12024）整個題目就是
    「四種書寫系統的標準化」——POJ／漢羅／全漢／台羅。這支程式做的是其中最
    機械、最不需要模型的那一段：POJ 與台羅之間是一組固定的對應規則，
    查表就能轉得準，不必動用 7B 模型。

轉換規則（兩邊發音完全相同，只是拼法不同）：

    POJ        台羅      例
    ch         ts        chia̍h  → tsia̍h（食）
    chh        tsh       chhù   → tshù （厝）
    oa         ua        oa̍h    → ua̍h  （活）
    oe         ue        oe     → ue   （鍋）
    o͘ / ou     oo        o͘      → oo   （烏的母音）
    eng        ing       pêng   → pîng （平）
    ek         ik        te̍k    → ti̍k  （敵）
    ⁿ          nn        kiaⁿ   → kiann（驚）

聲調符號兩套完全一樣（á à â ā a̍），所以不用碰——但**不能直接對字串做
replace**：「oá」在 NFC 裡是 o 加上單一字元 á，字串裡根本找不到 "oa"。
先拆成 NFD 再換，聲調就會自己跟著新的母音走。

而且拆開還不夠。「pêng」拆成 p e ◌̂ n g，調號夾在 e 和 ng 中間，所以
eng 這種跨母音的規則要用 regex 把中間那一段放進去。第一版我漏了這點，
selftest 當場抓到：chia̍h 轉對了，pêng 原封不動留在那裡——這種漏法最危險，
因為轉對的那些會讓人以為整批都對了。
"""
import re, sys, unicodedata

NASAL = "ⁿ"        # ⁿ  上標 n（POJ 的鼻化符號）
DOT = "͘"          # ◌͘  右上點（POJ 的 o͘）
# 聲調符號在 NFD 裡是接在母音後面的組合字元（U+0300–U+036F，右上點也在範圍內）。
M = r"[̀-ͯ]*"

# (pattern, replacement)。先長後短：chh 一定要排在 ch 前面，
# 不然只會換到 ch，留下一個孤單的 h。
POJ_TO_TL = [
    (DOT, "o"),                          # o͘ → oo（前面已經有一個 o 了）
    ("chh", "tsh"), ("ch", "ts"),
    ("o(" + M + ")a", r"u\1a"),          # oa → ua
    ("o(" + M + ")e", r"u\1e"),          # oe → ue
    ("e(" + M + ")ng", r"i\1ng"),        # eng → ing（調號壓在 e 上）
    ("e(" + M + ")k", r"i\1k"),          # ek  → ik （同上）
    (NASAL, "nn"),
]

TL_TO_POJ = [
    ("oo", "o" + DOT),
    ("tsh", "chh"), ("ts", "ch"),
    ("u(" + M + ")a", r"o\1a"),
    ("u(" + M + ")e", r"o\1e"),
    ("i(" + M + ")ng", r"e\1ng"),
    ("i(" + M + ")k", r"e\1k"),
    ("nn", NASAL),
]

# 判斷一段文字是哪一套寫的。只看兩邊獨有的拼法，共通的（k、p、a、i…）不算。
POJ_MARK = re.compile("chh?|" + NASAL + "|" + DOT + "|o" + M + "[ae]|e" + M + "(ng|k)", re.I)
TL_MARK = re.compile("tsh?|nn|oo|u" + M + "[ae]|i" + M + "(ng|k)", re.I)
# 有沒有羅馬字。純漢字的句子不該被當成拼音去轉。
HAS_LATIN = re.compile(r"[A-Za-z]")


def _case_like(src, dst):
    """把 dst 調成跟 src 一樣的大小寫。句首大寫的 Chhù 要變成 Tshù，不是 tshù。"""
    letters = [c for c in src if c.isalpha()]
    if not letters:
        return dst
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return dst.upper()
    if letters[0].isupper():
        return dst[:1].upper() + dst[1:]
    return dst


def _sub(text, table):
    """在 NFD 狀態下換拼法，聲調符號才會跟著新的母音走。"""
    s = unicodedata.normalize("NFD", text)
    for pat, rep in table:
        if len(pat) == 1 and not pat.isalpha():        # 單一組合字元，直接換
            s = s.replace(pat, rep)
        elif "(" in pat:                                # 帶調號的跨母音規則
            s = re.sub(pat, rep, s, flags=re.I)
        else:                                           # 純子音，不會夾調號
            s = re.sub(pat, lambda m, _r=rep: _case_like(m.group(0), _r),
                       s, flags=re.I)
    return unicodedata.normalize("NFC", s)


def poj_to_tailo(text):
    """白話字 → 教育部台羅。"""
    return _sub(text, POJ_TO_TL)


def tailo_to_poj(text):
    """教育部台羅 → 白話字。"""
    return _sub(text, TL_TO_POJ)


def guess(text):
    """這段羅馬字是 POJ 還是台羅？分不出來（或根本沒有羅馬字）回 None。

    分不出來很常見也很正常：「lí hó」兩套寫法一模一樣。這種情況回 None，
    讓呼叫端原樣保留——猜錯而去轉，會把本來就對的字轉壞。
    """
    s = unicodedata.normalize("NFD", text or "")
    if not HAS_LATIN.search(s):
        return None
    p, t = len(POJ_MARK.findall(s)), len(TL_MARK.findall(s))
    if p > t:
        return "poj"
    if t > p:
        return "tailo"
    return None


def to(text, system="tailo"):
    """把羅馬字統一成指定的那一套。認不出來就原樣還回去。

    這是給逐字稿用的入口：模型這一段回 POJ、下一段回台羅是常有的事，
    混在同一份檔案裡看起來就像拼錯字。統一之後才讀得下去。
    """
    got = guess(text)
    if got is None or got == system:
        return text
    return poj_to_tailo(text) if system == "tailo" else tailo_to_poj(text)


def selftest():
    bad = [0]

    def eq(got, want, label):
        if got != want:
            bad[0] += 1
            print("  ✗ %s\n     得到 %r\n     應該 %r" % (label, got, want))
        else:
            print("  ✓ %s → %s" % (label, got))

    # 取自 TW-Hokkien-LLM 的 README 範例（那句是 POJ）
    eq(poj_to_tailo("Thài-khong pêng-iú, lín hó! Lín chia̍h-pá--bē?"),
       "Thài-khong pîng-iú, lín hó! Lín tsia̍h-pá--bē?",
       "POJ→台羅（太空朋友，恁好！恁食飽未？）")
    eq(poj_to_tailo("chhù"), "tshù", "chhù 厝")
    eq(poj_to_tailo("oa̍h"), "ua̍h", "oa̍h 活（調號在 a 上）")
    eq(poj_to_tailo("pêng"), "pîng", "pêng 平（調號夾在 e 和 ng 中間）")
    eq(poj_to_tailo("te̍k"), "ti̍k", "te̍k 敵（調號夾在 e 和 k 中間）")
    eq(poj_to_tailo("kiaⁿ"), "kiann", "kiaⁿ 驚（鼻化）")
    eq(poj_to_tailo("toaⁿ"), "tuann", "toaⁿ 單（oa 加鼻化）")
    eq(poj_to_tailo("o͘-ba-sáng"), "oo-ba-sáng", "o͘ 右上點")
    eq(poj_to_tailo("Chhun-thiⁿ"), "Tshun-thinn", "句首大寫要跟著")

    # 反向：台羅 → POJ
    eq(tailo_to_poj("tsia̍h-pá"), "chia̍h-pá", "台羅→POJ 食飽")
    eq(tailo_to_poj("tshù"), "chhù", "台羅→POJ 厝")
    eq(tailo_to_poj("pîng"), "pêng", "台羅→POJ 平")
    eq(tailo_to_poj("kiann"), "kia" + NASAL, "台羅→POJ 驚")
    eq(tailo_to_poj("oo"), "o" + DOT, "台羅→POJ oo")

    # 來回一趟要回到原點
    for s in ["Thài-khong pîng-iú", "tsia̍h-pá--bē", "Tshun-thinn",
              "ua̍h-tāng", "ti̍k-jîn", "tuann-sin"]:
        eq(poj_to_tailo(tailo_to_poj(s)), s, "來回不變：" + s)

    # 判斷寫法
    eq(guess("chia̍h-pá"), "poj", "認出 POJ")
    eq(guess("tsia̍h-pá"), "tailo", "認出台羅")
    eq(guess("lí hó"), None, "兩套一樣的句子不亂猜")
    eq(guess("今仔日天氣真好"), None, "純漢字不碰")
    eq(guess(""), None, "空字串")
    eq(guess(None), None, "None")

    # to()：已經是目標寫法的不要動
    eq(to("tsia̍h-pá", "tailo"), "tsia̍h-pá", "已是台羅，原樣")
    eq(to("chia̍h-pá", "tailo"), "tsia̍h-pá", "POJ 轉成台羅")
    eq(to("tsia̍h-pá", "poj"), "chia̍h-pá", "台羅轉成 POJ")
    eq(to("今仔日", "tailo"), "今仔日", "純漢字不碰")
    eq(to("", "tailo"), "", "空字串")

    print("\n%s（%d 個沒過）" % ("全部通過 ✅" if not bad[0] else "有問題 ❌", bad[0]))
    return 1 if bad[0] else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    if len(sys.argv) > 2:
        print(to(sys.argv[2], sys.argv[1]))
        sys.exit(0)
    print(__doc__)
