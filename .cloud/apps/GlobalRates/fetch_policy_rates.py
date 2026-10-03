#!/usr/bin/env python3
"""抽取 12 幣別央行政策利率，累加寫入 output/policy_rates.csv。
資料源：BIS SDMX API（10 幣別，其中 7 幣別再以央行官網補 BIS 延遲，見 fetch_official_rates.py）+ CBC 重貼現率頁（TWD）+ SGD 兜底靜態表。
單一來源失敗不擋其他來源（比照 lcr_weekly_check.sh 設計原則）。
"""
import csv
import io
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from fetch_official_rates import official_rows

OUT_DIR = Path(__file__).parent / "output"
OUT_CSV = OUT_DIR / "policy_rates.csv"

# BIS REF_AREA 代碼對照（幣別 → BIS 代碼、央行名稱、貨幣區顯示名）
BIS_CURRENCIES = {
    "USD": ("US", "Federal Reserve"),
    "EUR": ("XM", "European Central Bank"),
    "GBP": ("GB", "Bank of England"),
    "AUD": ("AU", "Reserve Bank of Australia"),
    "CHF": ("CH", "Swiss National Bank"),
    "NZD": ("NZ", "Reserve Bank of New Zealand"),
    "CAD": ("CA", "Bank of Canada"),
    "THB": ("TH", "Bank of Thailand"),
    "SEK": ("SE", "Sveriges Riksbank"),
    "ZAR": ("ZA", "South African Reserve Bank"),
}

BIS_URL = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.{code}?lastNObservations=1300&format=csv"
CBC_URL = "https://www.cbc.gov.tw/tw/lp-640-1-1-20.html"


def fetch_bis(currency, code):
    """回傳 [(date, rate), ...] 由舊到新排序，近 5 年（1300 個交易日約可覆蓋）。"""
    url = BIS_URL.format(code=code)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            text = resp.read().decode("utf-8-sig")
    except Exception as e:
        print(f"  [WARN] {currency}（BIS {code}）抓取失敗：{e}", file=sys.stderr)
        return []

    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return []
    try:
        date_idx = header.index("TIME_PERIOD")
        val_idx = header.index("OBS_VALUE")
    except ValueError:
        print(f"  [WARN] {currency}：CSV 欄位格式不符預期", file=sys.stderr)
        return []

    rows = []
    for row in reader:
        if len(row) <= max(date_idx, val_idx):
            continue
        d, v = row[date_idx], row[val_idx]
        if not d or not v or v.strip().lower() == "nan":
            continue
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    rows.sort(key=lambda x: x[0])
    return rows


def fetch_cbc_twd():
    """台灣利率由共用央行庫匯出，不重複抓取官網。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from finatlas.policy_export import twd_rows
    return [(r['date'], r['rate']) for r in twd_rows()]


def sgd_fallback():
    """MAS SORA 走 ASP.NET 動態表單，非簡單 CSV/API，暫兜底為單筆靜態記錄。
    後續若要即時化，需處理 domesticinterestrates.aspx 的 ViewState 表單提交。"""
    today = datetime.now().strftime("%Y-%m-%d")
    return [(today, None)]  # None 表示無即時數值，前端顯示「請查閱 MAS 官網」


def load_existing():
    if not OUT_CSV.exists():
        return {}
    existing = {}
    with open(OUT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            existing.setdefault(row["currency"], {})[row["date"]] = row["rate"]
    return existing


def main():
    OUT_DIR.mkdir(exist_ok=True)
    all_rows = []  # (currency, date, rate, source)

    bis_by_currency = {}
    for currency, (code, cb_name) in BIS_CURRENCIES.items():
        print(f"抓取 {currency}（{cb_name}）…")
        rows = fetch_bis(currency, code)
        bis_by_currency[currency] = rows
        for d, v in rows:
            all_rows.append((currency, d, v, "BIS"))
        print(f"  {len(rows)} 筆")

    # BIS 落後數天；用央行官網補到今天，央行剛調利率也能當天反映
    print("央行官網補 BIS 延遲…")
    all_rows.extend(official_rows(bis_by_currency))

    print("TWD 由建置端直接讀取共用央行庫，不另存重複 CSV。")

    print("SGD（MAS SORA）：改走 ASP.NET 動態表單，暫用兜底空值標記，不即時抓")
    for d, v in sgd_fallback():
        all_rows.append(("SGD", d, v if v is not None else "", "MAS-fallback"))

    if not all_rows:
        print("[FAIL] 全部來源皆抓取失敗，保留既有 CSV 不覆寫", file=sys.stderr)
        sys.exit(1)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["currency", "date", "rate", "source"])
        for currency, d, v, src in sorted(all_rows, key=lambda r: (r[0], r[1])):
            writer.writerow([currency, d, v, src])

    print(f"\n完成，共 {len(all_rows)} 筆寫入 {OUT_CSV}")


if __name__ == "__main__":
    main()
