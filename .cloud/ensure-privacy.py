#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deploy/ensure-privacy.py — 公開頁的隱私聲明「單一來源」＋冪等注入器（2026-09-02 建立）。

為什麼要有（2026-09-02 GitHub／Cloudflare 流程體檢）：
  GitHub Pages 上的 25 支公開頁**每一支都掛著 GA4**，卻**沒有任何一支**寫了隱私權說明。
  GA4 會蒐集訪客的頁面路徑、概略地區、裝置與參照來源；這些站是全球可連的
  （GlobalPeaks／JapanOnsen／GlobalMichelin 等本來就有境外訪客），
  等於在沒有告知的情況下做訪客統計——個資告知義務與 ePrivacy 都缺這一塊。

  同一次注入也補上 `<meta name="referrer" content="no-referrer">`：
  GitHub Pages 不能自訂 HTTP 標頭（Cloudflare 那站由 _worker.js 加），
  meta 是那邊唯一能設 Referrer-Policy 的地方，避免站內路徑被帶到外部連結的對方伺服器。

設計比照 deploy/ensure-ga4.py：本檔是聲明內容的唯一定義處，要改文字只改這裡，不必動任何 HTML。
冪等：已含標記者原樣跳過，永不重複注入。純 HTML／inline style，
不引入任何外部資源，相容「單一自足」鐵則。

⚠ 只處理 deploy-manifest.json 白名單（GitHub 三 repo 的公開頁）。
   Cloudflare 銀行資料站是 Access 限人的自用站、且不掛 GA4，不在此列。

用法：
    python3 deploy/ensure-privacy.py                 # 對白名單全檔注入（缺才補，冪等）
    python3 deploy/ensure-privacy.py a.html b.html   # 只對指定檔注入
    python3 deploy/ensure-privacy.py --check         # 只檢查不改；有檔缺則 exit 1
    python3 deploy/ensure-privacy.py --selftest      # 對抗驗證（冪等與注入正確性）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ── 唯一定義區：要改隱私聲明只動這裡 ────────────────────────────
MARKER = 'data-privacy-notice="v1"'
REFERRER_META = '<meta name="referrer" content="no-referrer">'

NOTICE = f"""<details {MARKER} style="max-width:960px;margin:24px auto 40px;padding:0 16px;font-size:12px;line-height:1.7;color:#6b7280;">
<summary style="cursor:pointer;">隱私權說明</summary>
<p style="margin:8px 0;">本頁為個人製作的公開資料頁：不需登入、不要求填寫任何個人資料、不投放廣告、不設定廣告追蹤，也不會將資料出售或提供給第三方。</p>
<p style="margin:8px 0;">為了解使用概況，本頁使用 Google Analytics 4 記錄匿名的瀏覽統計（頁面路徑、概略地區、裝置類型、參照來源），並由該服務放置必要的統計用 Cookie。這些資料由 Google 保存並適用其隱私權政策，本站無法從中識別個別訪客身分。</p>
<p style="margin:8px 0;">不希望被統計，可安裝 <a href="https://tools.google.com/dlpage/gaoptout" rel="noopener noreferrer" target="_blank" style="color:inherit;">Google Analytics 停用瀏覽器外掛</a>，或在瀏覽器停用第三方 Cookie；停用不影響本頁任何功能。</p>
</details>
"""
# ────────────────────────────────────────────────────────────

AGENT_DIR = Path(__file__).resolve().parents[1]


def has_notice(html: str) -> bool:
    return MARKER in html


def has_referrer(html: str) -> bool:
    return re.search(r'<meta[^>]+name=["\']referrer["\']', html, flags=re.I) is not None


def inject(html: str) -> str:
    """在 </body> 前插入聲明、在 <head> 後插入 referrer meta。已有者各自跳過。"""
    if not has_referrer(html):
        m = re.search(r"<head[^>]*>", html, flags=re.IGNORECASE)
        if not m:
            raise ValueError("找不到 <head>")
        html = html[:m.end()] + "\n" + REFERRER_META + html[m.end():]

    if not has_notice(html):
        m = re.search(r"</body\s*>", html, flags=re.IGNORECASE)
        if not m:
            raise ValueError("找不到 </body>")
        html = html[:m.start()] + NOTICE + html[m.start():]
    return html


def manifest_local_files() -> list[Path]:
    mani = json.loads((AGENT_DIR / "deploy-manifest.json").read_text(encoding="utf-8"))
    return [Path(e["local"]) for repo in mani["repos"] for e in repo["files"]]


def selftest() -> int:
    """對抗驗證：注入要真的發生、跑第二次不能變兩份、找不到錨點要拒絕而不是默默略過。"""
    bad = []
    base = "<html><head><title>t</title></head><body><p>x</p></body></html>"

    once = inject(base)
    if not has_notice(once) or not has_referrer(once):
        bad.append("第一次注入後缺聲明或缺 referrer meta")
    if once.count(MARKER) != 1 or once.lower().count('name="referrer"') != 1:
        bad.append("第一次注入就重複了")

    twice = inject(once)
    if twice != once:
        bad.append("第二次注入不是 no-op（冪等失效，會越積越多份）")

    # 已有自訂 referrer 政策的頁面不該被覆蓋
    custom = '<html><head><meta name="referrer" content="origin"></head><body></body></html>'
    if inject(custom).lower().count('name="referrer"') != 1:
        bad.append("覆蓋了頁面原有的 referrer 設定")

    for broken, why in [("<html><body></body></html>", "缺 <head>"),
                        ("<html><head></head></html>", "缺 </body>")]:
        try:
            inject(broken)
            bad.append(f"{why} 的檔案應該丟例外（不能默默當成已處理）")
        except ValueError:
            pass

    if bad:
        print("✗ selftest FAIL：")
        for b in bad:
            print(f"    {b}")
        return 1
    print("✓ selftest 通過（注入／冪等／不覆蓋既有設定／壞檔拒絕）")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check_only = "--check" in sys.argv
    if "--selftest" in sys.argv:
        return selftest()

    files = [Path(a) for a in args] if args else manifest_local_files()
    missing, changed, skipped = [], [], []

    for p in files:
        if not p.exists():
            print(f"  ⚠ 找不到 {p}")
            continue
        html = p.read_text(encoding="utf-8")
        if has_notice(html) and has_referrer(html):
            skipped.append(p.name)
            continue
        if check_only:
            missing.append(p.name)
            continue
        try:
            p.write_text(inject(html), encoding="utf-8")
        except ValueError as e:
            print(f"  ✗ {p.name}：{e}")
            return 1
        changed.append(p.name)

    if check_only:
        if missing:
            print(f"✗ 有 {len(missing)} 支公開頁缺隱私聲明或 referrer 設定：{'、'.join(missing)}")
            return 1
        print(f"✓ {len(skipped)} 支公開頁皆已含隱私聲明與 referrer 設定")
        return 0

    print(f"✓ 注入 {len(changed)} 支、原已有 {len(skipped)} 支"
          + (f"（本次：{'、'.join(changed)}）" if changed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
