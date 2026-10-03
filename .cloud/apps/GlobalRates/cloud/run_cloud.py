#!/usr/bin/env python3
"""GlobalRates 雲端更新（AutoDeploy 計劃書 #github-cloud-update 第六節，2026-10-02）。

在網站 repo（weococreate/Global）的 GitHub Actions 內執行；本機也可預演（加 --site 指向含 GlobalRates.html 的資料夾）。
1. 抓政策利率、殖利率（必須成功）、FRED（失敗沿用舊檔，與本機日檢查相同）。三者每次都抓完整多年歷史、整份重寫，雲端不需保存歷史。
2. gen_site_data.py 產生資料；新台幣利率讀 output/twd_policy.json、演講摘要讀 output/speeches_summary.json（兩者由本機推上）。
3. 與網站 repo 現有 GlobalRates.html 的資料比對把關：貨幣與國家不得缺、日期不得倒退、筆數不得明顯變少、演講不得出現待摘要。
4. 打包 → 依本機部署順序跑 GA4／免責／隱私注入器 → 以注入器 --check 確認、GA4／隱私／referrer 固定字串必在、外部腳本只放行 jsDelivr 的 Chart.js 與 GA4、不得含本機路徑。
5. 除產生時間外與現有版本相同就不寫；否則寫回網站 repo 根目錄。結果寫 GITHUB_OUTPUT 的 changed。
任何一步失敗即非零結束，不寫回、不發布，線上保留上一版。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]          # apps/GlobalRates（雲端為 .cloud/apps/GlobalRates）
ROOT = APP.parents[1]                               # 專案根（雲端為 .cloud）
INJECTORS = ["ensure-ga4.py", "ensure-disclaimer.py", "ensure-privacy.py"]   # 與 deploy_all 步驟 0 同順序
GA4_SRC = "https://www.googletagmanager.com/gtag/js?id=G-JVW9ZBDT3Y"
CHART_SRC = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
# 固定字串的三項；免責聲明 GlobalRates 用自己的頁尾（注入器認得、不另加），改以注入器自己的 --check 判斷（單一規則）。
MARKERS = {"GA4": "gtag/js?id=G-JVW9ZBDT3Y", "隱私說明": 'data-privacy-notice="v1"',
           "referrer": '<meta name="referrer" content="no-referrer">'}
INJECTOR_NAMES = {"ensure-ga4.py": "GA4", "ensure-disclaimer.py": "免責聲明", "ensure-privacy.py": "隱私說明／referrer"}
MIN_KEEP = 0.98                                     # 歷史筆數至少保留基準的 98%（容許來源修訂少量刪除）


class Stop(Exception):
    pass


def data_of(html):
    m = re.search(r"const GLOBALRATES_DATA = (\{.*?\});?\s*\n", html, re.S)
    if not m:
        raise Stop("網頁內找不到 GLOBALRATES_DATA")
    return json.loads(m.group(1))


def check_data(new, old):
    """新資料不得比網站現有版本差；回傳問題清單（空＝通過）。"""
    bad = []
    nc = {p["currency"]: p for p in new["policy_summary"]}
    oc = {p["currency"]: p for p in old["policy_summary"]}
    if set(oc) - set(nc):
        bad.append("缺少貨幣：" + "、".join(sorted(set(oc) - set(nc))))
    for cur, o in oc.items():
        n = nc.get(cur)
        if not n:
            continue
        if o.get("as_of") and (n.get("as_of") or "") < o["as_of"]:
            bad.append(f"{cur} 資料日期倒退（{o['as_of']} → {n.get('as_of')}）")
        if len(new["policy_history"].get(cur, [])) < len(old["policy_history"].get(cur, [])) * MIN_KEEP:
            bad.append(f"{cur} 政策利率歷史筆數明顯變少")
    if set(old["yield_curves"]) - set(new["yield_curves"]):
        bad.append("缺少殖利率國家：" + "、".join(sorted(set(old["yield_curves"]) - set(new["yield_curves"]))))
    for c, o in old["yield_curves"].items():
        n = new["yield_curves"].get(c)
        if n and (n.get("as_of") or "") < (o.get("as_of") or ""):
            bad.append(f"{c} 殖利率日期倒退（{o['as_of']} → {n.get('as_of')}）")
        for tenor, rows in old["yield_series"].get(c, {}).items():
            if len(new["yield_series"].get(c, {}).get(tenor, [])) < len(rows) * MIN_KEEP:
                bad.append(f"{c} {tenor} 殖利率歷史筆數明顯變少")
    if old.get("macro", {}).get("available") and not new.get("macro", {}).get("available"):
        bad.append("FRED 總經資料由有變無")
    if "待摘要" in json.dumps(new.get("speeches", []), ensure_ascii=False):
        bad.append("演講區塊出現待摘要（摘要由本機產生後推上，不在雲端放佔位）")
    return bad


def check_html(html):
    bad = [f"缺少{name}" for name, mk in MARKERS.items() if mk not in html]
    others = re.findall(r'<script[^>]+\bsrc\s*=\s*"([^"]+)"', html, re.I)
    for src in others:
        if src not in (GA4_SRC, CHART_SRC):
            bad.append("不允許的外部腳本：" + src)
    if re.search(r"/Users/|/home/|-----BEGIN .*PRIVATE KEY-----", html):
        bad.append("含不應公開的內容（本機路徑或私鑰）")
    return bad


def normalize(html):
    """比對用：去掉產生時間（其餘含「距上次調整幾天」都算資料，天數變了也要更新）。"""
    return re.sub(r'"generated_at":"[^"]*"', '"generated_at":""', html)


def run(cmd, cwd, required=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)
    print(f"$ {' '.join(map(str, cmd))} → {r.returncode}")
    tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
    for line in tail:
        print("  " + line)
    if required and r.returncode != 0:
        raise Stop(f"{Path(cmd[-1]).name} 失敗")
    return r.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=os.environ.get("SITE_REPO_ROOT"), help="含現有 GlobalRates.html 的網站資料夾")
    ap.add_argument("--skip-fetch", action="store_true", help="不連網，用現有 output/（測試與預演）")
    a = ap.parse_args(argv)
    if not a.site:
        raise Stop("缺少 --site 或 SITE_REPO_ROOT")
    site_file = Path(a.site) / "GlobalRates.html"
    if not site_file.is_file() or site_file.is_symlink():
        raise Stop("網站資料夾沒有可更新的 GlobalRates.html")
    baseline = site_file.read_text(encoding="utf-8")
    old = data_of(baseline)
    py = sys.executable
    if not a.skip_fetch:
        run([py, "-B", "fetch_policy_rates.py"], APP)
        run([py, "-B", "fetch_yields.py"], APP)
        run([py, "-B", "fetch_fred.py"], APP, required=False)   # 本機日檢查同樣不因 FRED 失敗停
    run([py, "-B", "gen_site_data.py"], APP)
    new = data_of((APP / "globalrates-data.js").read_text(encoding="utf-8") + "\n")
    bad = check_data(new, old)
    if bad:
        raise Stop("資料把關未過：" + "；".join(bad))
    run(["node", "build-globalrates-deploy.js"], APP)
    built = APP / "GlobalRates.html"
    for inj in INJECTORS:
        run([py, "-B", str(ROOT / inj), str(built)], ROOT)
    for inj in INJECTORS:   # 注入後再用注入器自己的檢查模式確認一次（與本機部署相同的判斷）
        if not run([py, "-B", str(ROOT / inj), "--check", str(built)], ROOT, required=False):
            raise Stop("網頁把關未過：缺少" + INJECTOR_NAMES[inj])
    html = built.read_text(encoding="utf-8")
    bad = check_html(html)
    if bad:
        raise Stop("網頁把關未過：" + "；".join(bad))
    changed = normalize(html) != normalize(baseline)
    if changed:
        if site_file.read_text(encoding="utf-8") != baseline:
            raise Stop("網站檔在更新期間被改動，停止覆蓋")
        site_file.write_text(html, encoding="utf-8")
    print(json.dumps({"changed": changed, "generated_at": new.get("generated_at")}, ensure_ascii=False))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as e:
        print("停止：" + str(e), file=sys.stderr)
        sys.exit(1)
