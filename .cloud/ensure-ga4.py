#!/usr/bin/env python3
"""ensure-ga4.py — GA4 追蹤碼「單一來源」＋冪等注入器。

這是全站 GA4 內容的唯一定義處。要換 Measurement ID、加自訂事件、
或哪天想全站拔掉 GA，只改本檔即可，不必動任何 HTML。

用法：
    python3 ensure-ga4.py                 # 對 deploy-manifest.json 全部白名單檔注入（缺才補，冪等）
    python3 ensure-ga4.py a.html b.html   # 只對指定檔注入
    python3 ensure-ga4.py --check         # 只檢查不改；有檔缺 GA4 則 exit 1（列出缺的）

被 deploy_all.py 於部署前呼叫（無參數模式），確保每個要推的檔都已含 GA4。
冪等：已含者原樣跳過，永不重複注入。單一自足鐵則相容——注入的是 inline script，
不引入任何外部 .js 依賴。
"""
import json
import re
import sys
from pathlib import Path

# ── 唯一定義區：要改 GA4 只動這裡 ──────────────────────────────
GA4_ID = "G-JVW9ZBDT3Y"

# 冪等判斷標記：HTML 內含此字串即視為已裝，跳過
MARKER = f"gtag/js?id={GA4_ID}"

SNIPPET = f"""<!-- Google tag (gtag.js) GA4 {GA4_ID} — injected by GA4 single-source injector -->
<script async src="https://www.googletagmanager.com/gtag/js?id={GA4_ID}"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date());
gtag('config', '{GA4_ID}');
</script>
"""
# ────────────────────────────────────────────────────────────

AGENT_DIR = Path(__file__).resolve().parent


def has_ga4(html: str) -> bool:
    return MARKER in html


def inject(html: str) -> str:
    """在 <head> 開標籤後插入 snippet。已含則原樣回傳；找不到 <head> 則丟 ValueError。"""
    if has_ga4(html):
        return html
    m = re.search(r"<head[^>]*>", html, flags=re.IGNORECASE)
    if not m:
        raise ValueError("找不到 <head>")
    idx = m.end()
    return html[:idx] + "\n" + SNIPPET + html[idx:]


def manifest_local_files() -> list[Path]:
    mani = json.loads((AGENT_DIR / "deploy-manifest.json").read_text(encoding="utf-8"))
    return [Path(e["local"]) for repo in mani["repos"] for e in repo["files"]]


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    named = [Path(a) for a in argv if not a.startswith("-")]
    paths = named if named else manifest_local_files()

    missing, injected, skipped, nohead, absent = [], [], [], [], []
    for p in paths:
        if not p.exists():
            absent.append(p.name)
            continue
        html = p.read_text(encoding="utf-8")
        if has_ga4(html):
            skipped.append(p.name)
            continue
        if check_only:
            missing.append(p.name)
            continue
        try:
            p.write_text(inject(html), encoding="utf-8")
            injected.append(p.name)
        except ValueError:
            nohead.append(p.name)

    if check_only:
        if missing:
            print("缺 GA4：" + "、".join(missing))
            return 1
        print(f"GA4 檢查通過：{len(skipped)} 檔皆已含 {GA4_ID}")
        return 0

    print(f"[ensure-ga4] 注入 {len(injected)} 檔" + ("：" + "、".join(injected) if injected else "（無需注入）"))
    if skipped:
        print(f"[ensure-ga4] 已含跳過 {len(skipped)} 檔")
    if nohead:
        print("[ensure-ga4] ⚠ 無 <head> 略過：" + "、".join(nohead))
    if absent:
        print("[ensure-ga4] ⚠ 本機不存在略過：" + "、".join(absent))
    return 1 if nohead else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
