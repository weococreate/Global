"""run_cloud.py 回歸測試（2026-10-02）。雲端每次執行的第一步；用自製小資料，不依賴真資料檔或網路。
執行：python3 -B -m pytest -q apps/GlobalRates/cloud/test_run_cloud.py"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_cloud as rc  # noqa: E402


def base():
    hist = [{"date": f"2026-01-{i:02d}", "rate": 1.0} for i in range(1, 101)]
    return {
        "generated_at": "2026-10-01 06:30",
        "policy_summary": [{"currency": c, "as_of": "2026-10-01", "last_change": {"days_since": 5}} for c in ("USD", "EUR")],
        "policy_history": {"USD": hist, "EUR": hist},
        "yield_curves": {"US": {"as_of": "2026-09-30", "curve": []}, "DE": {"as_of": "2026-10-01", "curve": []}},
        "yield_series": {"US": {"10Y": hist}, "DE": {"10Y": hist}},
        "macro": {"available": True},
        "speeches": [{"title": "x", "summary": "鷹派"}],
    }


def test_identical_passes():
    assert rc.check_data(base(), base()) == []


@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d["policy_summary"].pop(), "缺少貨幣"),
    (lambda d: d["policy_summary"][0].update(as_of="2026-09-30"), "日期倒退"),
    (lambda d: d["policy_history"].update(USD=d["policy_history"]["USD"][:97]), "政策利率歷史筆數明顯變少"),
    (lambda d: d["yield_curves"].pop("DE"), "缺少殖利率國家"),
    (lambda d: d["yield_curves"]["US"].update(as_of="2026-09-29"), "殖利率日期倒退"),
    (lambda d: d["yield_series"]["US"].update({"10Y": d["yield_series"]["US"]["10Y"][:90]}), "殖利率歷史筆數明顯變少"),
    (lambda d: d["macro"].update(available=False), "FRED"),
    (lambda d: d["speeches"].append({"title": "y", "summary": "[待摘要]"}), "待摘要"),
])
def test_each_rule_blocks(mutate, expect):
    new = base()
    mutate(new)
    bad = rc.check_data(new, base())
    assert any(expect in b for b in bad), bad


def test_small_revision_allowed():
    new = base()
    new["policy_history"]["USD"] = new["policy_history"]["USD"][:99]   # 少 1%（來源修訂）放行
    assert rc.check_data(new, base()) == []


def page(extra=""):
    return ("<html><head>" + rc.MARKERS["referrer"] + f'<script async src="{rc.GA4_SRC}"></script>'
            + f'<script src="{rc.CHART_SRC}" integrity="sha384-x"></script>' + extra + "</head><body>"
            + f"<script>\nconst GLOBALRATES_DATA = {json.dumps(base(), ensure_ascii=False)};\n</script>"
            + '<details data-privacy-notice="v1"></details></body></html>')


def test_html_rules():
    assert rc.check_html(page()) == []
    for name, marker in rc.MARKERS.items():
        cut = f'<script async src="{rc.GA4_SRC}"></script>' if name == "GA4" else marker
        assert any("缺少" + name in b for b in rc.check_html(page().replace(cut, ""))), name
    assert any("不允許的外部腳本" in b for b in rc.check_html(page('<script src="https://evil.example/x.js"></script>')))
    home = "/" + "Users/" + "someone/x"          # 拆開寫：測試資料本身不可被防洩漏掃描當成本機路徑
    assert any("本機路徑" in b for b in rc.check_html(page(f"<!-- {home} -->")))


def test_data_of_reads_page_and_data_file():
    assert rc.data_of(page())["policy_summary"][0]["currency"] == "USD"
    file_text = "// 自動產生，勿手動編輯。\nconst GLOBALRATES_DATA = " + json.dumps(base()) + ";"
    assert rc.data_of(file_text + "\n")["macro"]["available"] is True
    with pytest.raises(rc.Stop):
        rc.data_of("<html></html>")


def test_normalize_ignores_only_generated_time():
    a = page()
    b = a.replace('"generated_at": "2026-10-01 06:30"', '"generated_at": "2026-10-01 18:30"')
    assert rc.normalize(json.dumps(base()).replace(" ", "")) == rc.normalize(json.dumps(dict(base(), generated_at="x")).replace(" ", ""))
    d = base()
    d["policy_summary"][0]["last_change"]["days_since"] = 6                # 天數變了要更新
    assert rc.normalize(json.dumps(d).replace(" ", "")) != rc.normalize(json.dumps(base()).replace(" ", ""))


def test_shipped_cloud_inputs_exist():
    """雲端要讀的兩個本機推上檔：新台幣利率與演講摘要；放進網站 repo 後同樣在 output/。"""
    out = rc.APP / "output"
    assert (out / "twd_policy.json").is_file()
    # 原始摘要檔可含尚未摘要的篇目（gen_site_data 產生網頁資料時會濾掉）；網頁資料的「待摘要」由 check_data 把關。
    assert isinstance(json.loads((out / "speeches_summary.json").read_text(encoding="utf-8")), list)


def test_site_workflow_settings():
    """網站工作流程關鍵設定（專案內讀正本；放進網站 repo 後讀 .github/workflows/site.yml）。"""
    local = Path(__file__).resolve().parent / "site-workflow.yml"
    wf = (local if local.exists() else rc.ROOT.parent / ".github/workflows/site.yml").read_text(encoding="utf-8")
    assert "cron: '30 22,10 * * *'" in wf                                    # 台北 06:30、18:30
    assert wf.count("vars.PAGES_FROM_ACTIONS == 'true'") == 2                 # 切換前更新與發布都不動作
    assert "vars.CLOUD_SCHEDULE_ENABLED == 'true'" in wf
    assert "update:" in wf and "contents: write" in wf and "pages: write" in wf and "id-token: write" in wf
    assert 'git add GlobalRates.html\n          test "$(git diff --cached --name-only)" = "GlobalRates.html"' in wf
    assert "--exclude=.github --exclude=.cloud" in wf and "find _site -type f ! -name '*.html'" in wf
    assert "NODE_PATH:" in wf and "TZ: Asia/Taipei" in wf
    assert '-k "not cbc_twd and not real_history"' in wf
    assert wf.index("playwright install") < wf.index('python -B "$CLOUD"/run_cloud.py')          # 澳洲央行抓取前瀏覽器要先裝好
    assert wf.count("install --with-deps chromium") == 1 and "actions/cache@" in wf
