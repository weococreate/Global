#!/usr/bin/env python3
"""抽取美/德/日公債殖利率（1/2/5/10/20/30Y），累加寫入 output/yields.csv。
近 5 年資料（檔案體積控制）；單一國家來源失敗不擋其他國家。
"""
import csv
import io
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).parent / "output"
OUT_CSV = OUT_DIR / "yields.csv"

YEARS_BACK = 5
CUTOFF_DATE = (datetime.now() - timedelta(days=365 * YEARS_BACK)).strftime("%Y-%m-%d")

TENORS = ["1Y", "2Y", "5Y", "10Y", "20Y", "30Y"]

# 美國財政部欄名 → 標準年期
UST_COL_MAP = {"1 Yr": "1Y", "2 Yr": "2Y", "5 Yr": "5Y", "10 Yr": "10Y", "20 Yr": "20Y", "30 Yr": "30Y"}

# Bundesbank 各年期序列鍵（皆已於 Phase 0 逐一實測成功）
BBK_SERIES = {
    "1Y": "R01XX", "2Y": "R02XX", "5Y": "R05XX",
    "10Y": "R10XX", "20Y": "R20XX", "30Y": "R30XX",
}
BBK_URL = (
    "https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
    "D.I.ZST.ZI.EUR.S1311.B.A604.{code}.R.A.A._Z._Z.A?lastNObservations=1400"
)

MOF_ALL_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
MOF_CURRENT_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"


def fetch_us():
    """美國財政部逐年 CSV，抓 CUTOFF_DATE 起算的年度。"""
    rows = []
    current_year = datetime.now().year
    for year in range(current_year - YEARS_BACK, current_year + 1):
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
            f"&field_tdr_date_value={year}&_format=csv"
        )
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                text = resp.read().decode("utf-8-sig")
        except Exception as e:
            print(f"  [WARN] 美國 {year} 年抓取失敗：{e}", file=sys.stderr)
            continue
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            date_raw = row.get("Date", "")
            try:
                mo, d, y = date_raw.split("/")
                iso_date = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            except ValueError:
                continue
            if iso_date < CUTOFF_DATE:
                continue
            for col, tenor in UST_COL_MAP.items():
                val = row.get(col, "").strip()
                if val:
                    try:
                        rows.append(("US", iso_date, tenor, float(val)))
                    except ValueError:
                        pass
    return rows


def fetch_de():
    """Bundesbank SDMX API，逐年期查詢。"""
    rows = []
    for tenor, code in BBK_SERIES.items():
        url = BBK_URL.format(code=code)
        try:
            req = urllib.request.Request(url, headers={"Accept": "text/csv"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8-sig")
        except Exception as e:
            print(f"  [WARN] 德國 {tenor} 抓取失敗：{e}", file=sys.stderr)
            continue
        reader = csv.reader(io.StringIO(text), delimiter=";")
        header = next(reader, None)
        if not header:
            continue
        try:
            date_idx = header.index("TIME_PERIOD")
            val_idx = header.index("OBS_VALUE")
        except ValueError:
            print(f"  [WARN] 德國 {tenor}：CSV 欄位格式不符預期", file=sys.stderr)
            continue
        for row in reader:
            if len(row) <= max(date_idx, val_idx):
                continue
            d, v = row[date_idx], row[val_idx]
            if not d or not v or v == "." or d < CUTOFF_DATE:
                continue
            try:
                rows.append(("DE", d, tenor, float(v)))
            except ValueError:
                continue
    return rows


def _parse_mof_csv(text):
    """欄位順序：Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y。"""
    col_idx = {"1Y": 1, "2Y": 2, "5Y": 5, "10Y": 10, "20Y": 12, "30Y": 14}
    rows = []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 15:
            continue
        date_raw = parts[0].strip()
        if not date_raw or "/" not in date_raw:
            continue
        try:
            y, mo, d = date_raw.split("/")
            iso_date = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            continue
        if iso_date < CUTOFF_DATE:
            continue
        for tenor, idx in col_idx.items():
            if idx >= len(parts):
                continue
            val = parts[idx].strip()
            if not val:
                continue
            try:
                rows.append(("JP", iso_date, tenor, float(val)))
            except ValueError:
                continue
    return rows


def fetch_jp():
    """日本財務省歷史全量 CSV 有更新時滯（實測落後約一週），另抓當月檔補最新缺口。"""
    rows_by_key = {}  # (date, tenor) -> value，當月檔覆蓋歷史檔同鍵值

    try:
        with urllib.request.urlopen(MOF_ALL_URL, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        for country, d, tenor, v in _parse_mof_csv(text):
            rows_by_key[(d, tenor)] = v
    except Exception as e:
        print(f"  [WARN] 日本歷史檔抓取失敗：{e}", file=sys.stderr)

    try:
        with urllib.request.urlopen(MOF_CURRENT_URL, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        for country, d, tenor, v in _parse_mof_csv(text):
            rows_by_key[(d, tenor)] = v
    except Exception as e:
        print(f"  [WARN] 日本當月檔抓取失敗：{e}", file=sys.stderr)

    return [("JP", d, tenor, v) for (d, tenor), v in rows_by_key.items()]


def main():
    OUT_DIR.mkdir(exist_ok=True)
    all_rows = []

    print("抓取美國公債殖利率…")
    us_rows = fetch_us()
    all_rows.extend(us_rows)
    print(f"  {len(us_rows)} 筆")

    print("抓取德國公債殖利率…")
    de_rows = fetch_de()
    all_rows.extend(de_rows)
    print(f"  {len(de_rows)} 筆")

    print("抓取日本公債殖利率…")
    jp_rows = fetch_jp()
    all_rows.extend(jp_rows)
    print(f"  {len(jp_rows)} 筆")

    if not all_rows:
        print("[FAIL] 全部來源皆抓取失敗，保留既有 CSV 不覆寫", file=sys.stderr)
        sys.exit(1)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["country", "date", "tenor", "yield"])
        for country, d, tenor, v in sorted(all_rows, key=lambda r: (r[0], r[1], r[2])):
            writer.writerow([country, d, tenor, v])

    print(f"\n完成，共 {len(all_rows)} 筆寫入 {OUT_CSV}")


if __name__ == "__main__":
    main()
