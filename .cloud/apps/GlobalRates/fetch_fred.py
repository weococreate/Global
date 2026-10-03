#!/usr/bin/env python3
"""抽取美國總經數據（CPI YoY／核心 PCE YoY／失業率／SOFR），累加寫入 output/fred.csv。

資料源：FRED（St. Louis Fed）公開 CSV 下載端點 `fredgraph.csv`，**免申請 API 金鑰**。
（原 api.stlouisfed.org 的 JSON API 需 FRED_API_KEY；改用 fredgraph.csv 後零金鑰即可取得同一批序列。）

抓取失敗時的行為（比照 lcr_weekly_check.sh／SGD 兜底原則：抽不到=空值+警告，不中斷）：
- 保留既有 output/fred.csv 不覆寫（若從未抓過，則不產生此檔，gen_site_data.py 會誠實顯示「尚未設定」）。
- 全部序列皆失敗才 exit 1；部分成功仍寫出。
"""
import csv
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).parent / "output"
OUT_CSV = OUT_DIR / "fred.csv"

# FRED series_id → (顯示名稱, 是否需要換算 YoY)
SERIES = {
    "CPIAUCSL": ("CPI YoY", True),        # 消費者物價指數（季調），需自算年增率
    "PCEPILFE": ("核心 PCE YoY", True),    # 核心個人消費支出物價指數（季調），需自算年增率
    "UNRATE": ("失業率", False),           # 失業率本身即為比率，不需換算
    "SOFR": ("SOFR", False),               # 擔保隔夜融資利率，本身即為利率
}

YEARS_BACK = 3
# 免金鑰公開 CSV 端點：回傳兩欄 `observation_date,<SERIES_ID>`，缺值為空字串或 "."。
FREDGRAPH_URL = (
    "https://fred.stlouisfed.org/graph/fredgraph.csv"
    "?id={series_id}&cosd={start}"
)


def fetch_series(series_id):
    start = (datetime.now() - timedelta(days=365 * YEARS_BACK)).strftime("%Y-%m-%d")
    url = FREDGRAPH_URL.format(series_id=series_id, start=start)
    try:
        # 帶 UA 避免部分邊緣節點對無 UA 請求回 403
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (GlobalRates fetch_fred)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  [WARN] {series_id} 抓取失敗：{e}", file=sys.stderr)
        return []
    rows = []
    reader = csv.reader(text.splitlines())
    next(reader, None)  # 跳過表頭 observation_date,<SERIES_ID>
    for row in reader:
        if len(row) < 2:
            continue
        d, val = row[0].strip(), row[1].strip()
        if val in (".", "", "NaN", "nan"):
            continue
        try:
            rows.append((d, float(val)))
        except ValueError:
            continue
    return rows


def to_yoy(rows):
    """月頻指數序列 → 年增率（%）：與 12 個月前同月的值比較。"""
    by_date = {d: v for d, v in rows}
    dates_sorted = sorted(by_date)
    out = []
    for d in dates_sorted:
        dt = datetime.strptime(d, "%Y-%m-%d")
        prior = (dt.replace(year=dt.year - 1)).strftime("%Y-%m-%d")
        # FRED 月頻資料日期固定為每月第一天，直接查同月上一年
        if prior in by_date and by_date[prior] != 0:
            yoy = round((by_date[d] / by_date[prior] - 1) * 100, 2)
            out.append((d, yoy))
    return out


def main():
    OUT_DIR.mkdir(exist_ok=True)
    all_rows = []  # (metric, date, value)
    n_ok = 0

    for series_id, (label, needs_yoy) in SERIES.items():
        print(f"抓取 {label}（{series_id}）…")
        raw = fetch_series(series_id)
        if not raw:
            continue
        n_ok += 1
        rows = to_yoy(raw) if needs_yoy else raw
        for d, v in rows:
            all_rows.append((label, d, v))
        print(f"  {len(rows)} 筆")

    if not all_rows:
        print("[FAIL] 全部 FRED 序列皆抓取失敗，保留既有 CSV 不覆寫", file=sys.stderr)
        sys.exit(1)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "date", "value"])
        for metric, d, v in sorted(all_rows, key=lambda r: (r[0], r[1])):
            writer.writerow([metric, d, v])

    print(f"\n完成，{n_ok}/{len(SERIES)} 序列成功，共 {len(all_rows)} 筆寫入 {OUT_CSV}")


if __name__ == "__main__":
    main()
