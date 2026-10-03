#!/usr/bin/env python3
"""ensure-disclaimer.py — 免責聲明統一注入器（2026-09-01「上傳前雙查核」裁定）

沿用 ensure-ga4.py 範式：**免責聲明的唯一定義處就是本檔**。要改文案、改樣式、
或全站拿掉，只改這裡再跑一次。

為什麼用注入器而不是逐頁手改 footer：15 支頁面的頁尾結構完全不同——有的是
`<footer id="foot"></footer>` 靠 JS 填、有的根本沒有 footer（SPA 動態渲染）、
有的帶 data-i 的 i18n 屬性。逐頁手改既易破版又無法對新頁生效。本注入器插在
`</body>` 前的獨立區塊，自帶樣式、不依賴各頁 class，不動任何既有版面。

冪等：以 data-disclaimer 標記判斷，已注入即跳過，永不重複。
fail-fast：找不到 `</body>` 直接非零退出，不靜默略過（那會產出「以為補了其實沒補」的頁面）。

用法：
  python3 ensure-disclaimer.py                 # 對 TARGETS 清單全補（開發版正本）
  python3 ensure-disclaimer.py --manifest      # 對 deploy-manifest.json 全白名單「部署檔」全補
                                               #   （deploy_all.py 部署前跑這個模式，與 ensure-ga4 對稱）
  python3 ensure-disclaimer.py a.html b.html   # 指定檔
  python3 ensure-disclaimer.py --check [檔...]  # 只檢查，缺則 exit 1
  python3 ensure-disclaimer.py --force [檔...]  # 改過文案/樣式後，重寫既有區塊

兩種清單的差別：TARGETS 是**開發版正本**（改了要重跑 build-*-deploy.js 才會進部署檔）；
--manifest 是**部署檔產物**（deploy_all 部署前的最後保險，防止某支正本漏加）。
"""
import sys, os, re, json

DIR = os.path.dirname(os.path.abspath(__file__))
MARK = 'data-disclaimer="weoco"'

# 文案：通用於資料視覺化頁（不限投資類），故用「專業建議」而非「投資建議」。
# 需同時被 publish-gate.sh 的 regex（免責|非投資建議|僅供參考|不構成）認得。
# 顏色：#5b6472（＝既有 Bank-index 的 --mut）對各頁底色皆 ≥5:1，過 WCAG AA。
# ⛔ 初版用 #7a828e，21 支實測只有 3.6:1 全不合格——淺灰在淺底頁上看似「低調」，
#    實際是讀不清楚。改樣式後務必重跑 contrast 檢查，不要憑感覺挑顏色。
BLOCK = (
    # ⛔ box-sizing 一定要自己宣告：地圖頁（JapanPowder／TaiwanTrails）沒有全域
    #    `*{box-sizing:border-box}`，max-width 1100 + padding 40 會撐成 1140 造成整頁橫向捲動。
    '\n<div ' + MARK + ' style="box-sizing:border-box;max-width:1100px;margin:28px auto 20px;'
    'padding:14px 20px;'
    'border-top:1px solid rgba(128,128,128,.25);font-size:12.5px;line-height:1.75;'
    'color:#5b6472;text-align:left">'
    '<strong style="color:#39404b">免責聲明</strong>：本頁為個人整理之公開資料視覺化，'
    '僅供參考，不構成任何專業建議，亦不代表任何機關或機構立場。'
    '資料經自動彙整，可能有解析誤差或未及更新，實際資訊請以官方公布為準。'
    '</div>\n'
)

# 預設清單：已上線但缺免責的頁面（2026-09-01 盤點）。
# 有 build-*-deploy.js 的 app 一律填「開發版 XXX1.html」——那才是正本，
# 改完要重跑打包器；部署版直接改會被下次打包覆蓋（feedback_edit_generator_not_artifact）。
TARGETS = [
    'apps/GlobalPeaks/GlobalPeaks1.html', 'apps/KpopTracker/KpopTracker1.html', 'apps/KoreaFood/KoreaFood1.html', 'apps/EduReality/EduReality1.html',
    'apps/TaiwanIssues/TaiwanIssues1.html', 'apps/TaiwanMind/TaiwanMind1.html', 'apps/TaiwanEnergy/TaiwanEnergy1.html', 'apps/TaiwanJobs/TaiwanJobs1.html',
    'apps/EduScience/EduScience1.html', 'apps/TaiwanNHI/TaiwanNHI1.html', 'apps/CharacterDecomp/CharacterDecomp1.html',
    'apps/TaiwanHeritage/TaiwanHeritage1.html', 'apps/TaiwanMedMap/TaiwanMedMap1.html',
    # 這 6 支初次盤點時被「僅供參考」誤判為已有免責（實際那四字出現在資料欄位裡），
    # 補列入正本清單；只補部署檔的話，下次重跑 build-*-deploy.js 就會掉。
    'apps/GlobalGolf/GlobalGolf1.html', 'apps/JapanPowder/JapanPowder1.html', 'apps/TaiwanTrails/TaiwanTrails1.html',
    'apps/TaiwanEnviro/TaiwanEnviro1.html', 'apps/TaiwanFraud/TaiwanFraud1.html', 'apps/EduTextbook/EduTextbook1.html',
    # 以下兩支無 build-*-deploy.js：
    #   MandarinIdioms 開發版與部署版都要注入（沒有打包器可重跑）
    #   TempleVsStore 只有單檔，它自己就是正本
    'apps/MandarinIdioms/MandarinIdioms1.html', 'apps/MandarinIdioms/MandarinIdioms.html', 'apps/TempleVsStore/TempleVsStore.html',
    # 2026-09-02 上 Cloudflare 銀行資料站的 7 支。前 6 支的 HTML 本身就是手寫正本
    # （cbcstats/fscstats 的 datasets/*.py 只把資料注入既有檔案，不產生 HTML），故直接列本檔；
    # BankPortfolioCons 是 make_web_cons.py 的產物，免責已寫進該產生器的頁尾，不列這裡。
    'finatlas/apps/Banking/Banking.html', 'finatlas/apps/Indicators/Indicators.html', 'finatlas/apps/LoanInvest/LoanInvest.html', 'finatlas/apps/Derivatives/Derivatives.html',
    'FSCCards.html', 'finatlas/apps/TWBills/TWBills.html',
]


# 頁面「已經自己寫了免責聲明」的判準。刻意不含「僅供參考」——2026-09-01 實測，
# EduTextbook／TaiwanFraud 的「僅供參考」出現在資料欄位與註解裡（"即時值僅供參考"、
# "僅供參考勿過度解讀"），把它當免責會誤判成「已有」而漏補真正的免責聲明。
OWN_DISCLAIMER = re.compile(r'免責|不構成任何專業建議|不構成投資建議|非投資建議|不構成任何投資')


def has_block(html):
    """已注入過，或頁面本來就有自己的免責聲明 → 都算「已有」，不重複補。"""
    return MARK in html or bool(OWN_DISCLAIMER.search(html))


BLOCK_RE = re.compile(r'\n?<div ' + re.escape(MARK) + r'.*?</div>\n?', re.S)


def inject(path, check_only=False, force=False):
    """回傳 'ok'（已有）／'added'（本次注入）／'would-add'（--check 發現缺）。

    force=True：把既有的 MARK 區塊整段換成當前 BLOCK（改文案／改樣式後用）。
    只重寫自己注入過的區塊；頁面本來就有的免責照樣不碰。
    """
    full = path if os.path.isabs(path) else os.path.join(DIR, path)
    if not os.path.exists(full):
        print(f"  ❌ 找不到檔案：{path}")
        return 'error'
    html = open(full, encoding='utf-8').read()
    if force and MARK in html:
        html = BLOCK_RE.sub('', html)          # 先拆掉舊的，下面照正常流程重新注入
    elif has_block(html):
        return 'ok'
    if check_only:
        return 'would-add'
    m = list(re.finditer(r'</body\s*>', html, re.I))
    if not m:
        print(f"  ❌ {path} 找不到 </body>，無法注入（不靜默略過）")
        return 'error'
    at = m[-1].start()
    open(full, 'w', encoding='utf-8').write(html[:at] + BLOCK + html[at:])
    return 'added'


def manifest_files():
    """deploy-manifest.json 白名單的部署檔（與 ensure-ga4.py 無參數時的範圍一致）。"""
    mf = json.load(open(os.path.join(DIR, 'deploy-manifest.json'), encoding='utf-8'))
    return [f['local'] for r in mf['repos'] for f in r['files']]


def main():
    flags = {'--check', '--manifest', '--force'}
    args = [a for a in sys.argv[1:] if a not in flags]
    check_only = '--check' in sys.argv
    force = '--force' in sys.argv
    files = manifest_files() if '--manifest' in sys.argv else (args or TARGETS)
    counts = {'ok': 0, 'added': 0, 'would-add': 0, 'error': 0}
    for f in files:
        r = inject(f, check_only, force)
        counts[r] += 1
        if r in ('added', 'would-add'):
            print(f"  {'⚠ 缺免責' if r == 'would-add' else '✓ 已注入'}：{f}")
    print(f"免責聲明{'檢查' if check_only else '注入'}：{len(files)} 檔 → "
          f"已有 {counts['ok']}・{'缺 ' + str(counts['would-add']) if check_only else '新增 ' + str(counts['added'])}"
          f"・錯誤 {counts['error']}")
    if counts['error'] or (check_only and counts['would-add']):
        sys.exit(1)


if __name__ == '__main__':
    main()
