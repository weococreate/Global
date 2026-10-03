#!/usr/bin/env python3
"""彙整 output/*.csv → beyybot-agent/apps/globalrates/globalrates-data.js（純前端讀取的資料檔）。
比照 LCR/CAMELS 模式:抽取腳本產出 CSV,本檔只讀 CSV 不重新對外抓資料。
"""
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "output"
# 與本檔同資料夾（2026-10-02：原寫小寫 apps/globalrates 在 Mac 不分大小寫才可用，雲端 Linux 會失敗；
# 並移除寫死的本機絕對路徑，雲端 repo 公開不可含本機帳號）。
SITE_JS = BASE / "globalrates-data.js"
# 新台幣政策利率來自本機 FinAtlas 共用央行庫（雲端沒有）：本機讀到時存一份小檔，雲端改讀此檔。
TWD_FILE = OUT_DIR / "twd_policy.json"

CURRENCY_META = {
    "USD": ("美國", "Federal Reserve"),
    "EUR": ("歐元區", "European Central Bank"),
    "GBP": ("英國", "Bank of England"),
    "AUD": ("澳洲", "Reserve Bank of Australia"),
    "CHF": ("瑞士", "Swiss National Bank"),
    "NZD": ("紐西蘭", "Reserve Bank of New Zealand"),
    "CAD": ("加拿大", "Bank of Canada"),
    "THB": ("泰國", "Bank of Thailand"),
    "TWD": ("台灣", "中央銀行"),
    "ZAR": ("南非", "South African Reserve Bank"),
    "SEK": ("瑞典", "Sveriges Riksbank"),
    "SGD": ("新加坡", "Monetary Authority of Singapore"),
}

TENOR_ORDER = ["1Y", "2Y", "5Y", "10Y", "20Y", "30Y"]
COUNTRY_NAME = {"US": "美國", "DE": "德國", "JP": "日本"}

# Phase 3 規則型提示門檻（集中設定區，改門檻只改這裡，不用動計算邏輯）
THRESHOLDS = {
    "inversion_alert_min_days": 20,   # 期限利差連續倒掛達此交易日數才觸發提示
    "carry_extreme_bp": 300,          # 政策利率對美元利差達 ±此 bp 才標記為極端carry
}

FRED_LABELS = {
    "CPI YoY": "cpi_yoy",
    "核心 PCE YoY": "core_pce_yoy",
    "失業率": "unemployment_rate",
    "SOFR": "sofr",
}


def load_twd():
    """本機：讀 FinAtlas 共用央行庫並更新 twd_policy.json；雲端（無 FinAtlas）：讀 twd_policy.json。"""
    import sys
    sys.path.insert(0, str(BASE.parent.parent))
    try:
        from finatlas.policy_export import twd_rows
    except ImportError:
        if not TWD_FILE.exists():
            raise SystemExit("缺少新台幣政策利率：沒有 FinAtlas 共用庫，也沒有 output/twd_policy.json")
        return json.loads(TWD_FILE.read_text(encoding="utf-8"))
    rows = twd_rows()
    text = json.dumps(rows, ensure_ascii=False, indent=1) + "\n"
    if not TWD_FILE.exists() or TWD_FILE.read_text(encoding="utf-8") != text:
        TWD_FILE.write_text(text, encoding="utf-8")
    return rows


def load_policy_rates():
    path = OUT_DIR / "policy_rates.csv"
    by_currency = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["currency"] == "TWD":
                continue
            by_currency.setdefault(row["currency"], []).append(
                {"date": row["date"], "rate": row["rate"], "source": row["source"]}
            )
    by_currency["TWD"] = load_twd()
    for rows in by_currency.values():
        rows.sort(key=lambda r: r["date"])
    return by_currency


def load_yields():
    path = OUT_DIR / "yields.csv"
    by_country = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_country.setdefault(row["country"], {}).setdefault(row["tenor"], []).append(
                {"date": row["date"], "yield": float(row["yield"])}
            )
    for tenors in by_country.values():
        for rows in tenors.values():
            rows.sort(key=lambda r: r["date"])
    return by_country


def find_last_change(rows):
    """由最新值往回掃，找最後一次數值變動的日期/方向/幅度。忽略空值（SGD 兜底情形）。"""
    valid = [r for r in rows if r["rate"] not in ("", None)]
    if len(valid) < 2:
        return None
    valid = [{"date": r["date"], "rate": float(r["rate"])} for r in valid]
    current = valid[-1]["rate"]
    for i in range(len(valid) - 2, -1, -1):
        if valid[i]["rate"] != current:
            diff = round(current - valid[i]["rate"], 4)
            direction = "up" if diff > 0 else "down"
            change_date = valid[i + 1]["date"]
            days_since = (datetime.now() - datetime.strptime(change_date, "%Y-%m-%d")).days
            return {
                "date": change_date,
                "direction": direction,
                "delta": diff,
                "days_since": days_since,
            }
    return None


def build_policy_summary(by_currency):
    summary = []
    for currency, (name_zh, cb_name) in CURRENCY_META.items():
        rows = by_currency.get(currency, [])
        valid = [r for r in rows if r["rate"] not in ("", None)]
        latest = valid[-1] if valid else None
        change = find_last_change(rows)
        summary.append({
            "currency": currency,
            "name_zh": name_zh,
            "central_bank": cb_name,
            "current_rate": float(latest["rate"]) if latest else None,
            "as_of": latest["date"] if latest else None,
            "last_change": change,
            "has_live_data": latest is not None,
        })
    return summary


def build_history_series(by_currency):
    """近 5 年疊圖用序列（原始 daily 資料量已由 fetch 階段控制，直接沿用）。"""
    series = {}
    for currency in CURRENCY_META:
        rows = by_currency.get(currency, [])
        series[currency] = [
            {"date": r["date"], "rate": float(r["rate"])}
            for r in rows if r["rate"] not in ("", None)
        ]
    return series


def build_yield_curves(by_country):
    """各國最新一個交易日的完整曲線（1/2/5/10/20/30Y）。"""
    curves = {}
    for country, tenors in by_country.items():
        latest_date = max(
            (rows[-1]["date"] for rows in tenors.values() if rows), default=None
        )
        if not latest_date:
            continue
        curve = []
        for tenor in TENOR_ORDER:
            rows = tenors.get(tenor, [])
            match = next((r for r in rows if r["date"] == latest_date), None)
            if match:
                curve.append({"tenor": tenor, "yield": match["yield"]})
        curves[country] = {"as_of": latest_date, "curve": curve}
    return curves


def build_yield_series(by_country):
    """各國各年期近 5 年時間序列（給趨勢圖用）。"""
    series = {}
    for country, tenors in by_country.items():
        series[country] = {
            tenor: [{"date": r["date"], "yield": r["yield"]} for r in rows]
            for tenor, rows in tenors.items()
        }
    return series


def compute_spreads(by_country):
    """2s10s、10s30s 期限利差與倒掛判斷，各國近 5 年時間序列。"""
    spreads = {}
    for country, tenors in by_country.items():
        by_date = {}
        for tenor in ["2Y", "10Y", "30Y"]:
            for r in tenors.get(tenor, []):
                by_date.setdefault(r["date"], {})[tenor] = r["yield"]
        s2s10s, s10s30s = [], []
        for d in sorted(by_date):
            vals = by_date[d]
            if "2Y" in vals and "10Y" in vals:
                bp = round((vals["10Y"] - vals["2Y"]) * 100, 1)
                s2s10s.append({"date": d, "spread_bp": bp, "inverted": bp < 0})
            if "10Y" in vals and "30Y" in vals:
                bp = round((vals["30Y"] - vals["10Y"]) * 100, 1)
                s10s30s.append({"date": d, "spread_bp": bp, "inverted": bp < 0})
        spreads[country] = {"2s10s": s2s10s, "10s30s": s10s30s}
    return spreads


def build_policy_spread_matrix(by_currency):
    """12 幣別對美元政策利率利差矩陣（carry 視角），單位 bp，正值＝比美元高。"""
    usd_rows = [r for r in by_currency.get("USD", []) if r["rate"] not in ("", None)]
    if not usd_rows:
        return []
    usd_rate = float(usd_rows[-1]["rate"])
    matrix = []
    for currency in CURRENCY_META:
        rows = [r for r in by_currency.get(currency, []) if r["rate"] not in ("", None)]
        if not rows:
            matrix.append({"currency": currency, "spread_bp": None, "as_of": None})
            continue
        rate = float(rows[-1]["rate"])
        spread_bp = round((rate - usd_rate) * 100, 1)
        matrix.append({
            "currency": currency,
            "spread_bp": spread_bp,
            "as_of": rows[-1]["date"],
            "is_extreme": abs(spread_bp) >= THRESHOLDS["carry_extreme_bp"],
        })
    return matrix


def _value_at_or_before(rows, target_date):
    """rows 已依 date 由舊到新排序；回傳日期 <= target_date 的最後一筆（找不到回傳 None）。"""
    match = None
    for r in rows:
        if r["date"] <= target_date:
            match = r
        else:
            break
    return match


def build_yield_changes(by_country):
    """各國各年期殖利率 1週/1月/YTD 變動（bp）。以「小於等於目標日的最後一筆」對齊非交易日缺口。"""
    changes = {}
    for country, tenors in by_country.items():
        country_out = {}
        for tenor, rows in tenors.items():
            if not rows:
                continue
            rows_sorted = sorted(rows, key=lambda r: r["date"])
            latest = rows_sorted[-1]
            latest_dt = datetime.strptime(latest["date"], "%Y-%m-%d")
            targets = {
                "1w": (latest_dt - timedelta(days=7)).strftime("%Y-%m-%d"),
                "1m": (latest_dt - timedelta(days=30)).strftime("%Y-%m-%d"),
                "ytd": f"{latest_dt.year}-01-01",
            }
            entry = {"as_of": latest["date"], "latest_yield": latest["yield"]}
            for label, target_date in targets.items():
                base = _value_at_or_before(rows_sorted, target_date)
                entry[f"change_{label}_bp"] = (
                    round((latest["yield"] - base["yield"]) * 100, 1) if base else None
                )
            country_out[tenor] = entry
        changes[country] = country_out
    return changes


def compute_inversion_streaks(spreads):
    """各國各期限利差目前連續倒掛的交易日數（由序列尾端往回數，遇非倒掛即停）。"""
    streaks = {}
    for country, pairs in spreads.items():
        streaks[country] = {}
        for pair_name, rows in pairs.items():
            streak = 0
            for r in reversed(rows):
                if r["inverted"]:
                    streak += 1
                else:
                    break
            streaks[country][pair_name] = streak
    return streaks


def build_rule_hints(streaks, spread_matrix):
    """規則型中性提示文字。門檻見 THRESHOLDS，每條規則皆已用真實歷史資料回測過至少一次觸發案例
    （見 tests/test_gen_site_data.py 對應測試；US 2s10s 於 2022–2024 曾連續倒掛逾 500 個交易日）。
    """
    hints = []
    min_days = THRESHOLDS["inversion_alert_min_days"]
    for country, pairs in streaks.items():
        name = COUNTRY_NAME.get(country, country)
        for pair_name, days in pairs.items():
            if days >= min_days:
                hints.append({
                    "type": "inversion",
                    "text": f"{name}公債 {pair_name} 利差已連續 {days} 個交易日倒掛，殖利率曲線呈現長端低於短端的異常型態。",
                })
    for row in spread_matrix:
        if row.get("is_extreme"):
            direction = "顯著高於" if row["spread_bp"] > 0 else "顯著低於"
            hints.append({
                "type": "carry_extreme",
                "text": f"{row['currency']} 政策利率{direction}美元 {abs(row['spread_bp']):.0f} bp，carry 利差處於極端區間。",
            })
    return hints


def load_fred():
    """讀取 FRED 總經資料（output/fred.csv）；檔案不存在時誠實回傳空結構，前端顯示「尚未設定」。"""
    path = OUT_DIR / "fred.csv"
    if not path.exists():
        return {"available": False, "series": {}}
    series = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = FRED_LABELS.get(row["metric"], row["metric"])
            series.setdefault(key, []).append({"date": row["date"], "value": float(row["value"])})
    for rows in series.values():
        rows.sort(key=lambda r: r["date"])
    return {"available": bool(series), "series": series}


def build_real_policy_rate(by_currency, fred):
    """美國實質政策利率 = 政策利率 − CPI YoY。無 FRED 資料時回傳 None（誠實標記，不臆測）。"""
    cpi_rows = fred.get("series", {}).get("cpi_yoy", [])
    usd_rows = [r for r in by_currency.get("USD", []) if r["rate"] not in ("", None)]
    if not cpi_rows or not usd_rows:
        return None
    latest_cpi = cpi_rows[-1]
    latest_policy = usd_rows[-1]
    return {
        "policy_rate": float(latest_policy["rate"]),
        "policy_as_of": latest_policy["date"],
        "cpi_yoy": latest_cpi["value"],
        "cpi_as_of": latest_cpi["date"],
        "real_rate": round(float(latest_policy["rate"]) - latest_cpi["value"], 2),
    }


def load_speeches():
    path = OUT_DIR / "speeches_summary.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        {
            "date": s["date"],
            "currency": s["currency"],
            "speaker": s["speaker"],
            "institution": s["institution"],
            "title": s["title"],
            "url": s["url"],
            "point": s.get("point", ""),
            "tag": s.get("tag", ""),
            "quote": s.get("quote", ""),
        }
        for s in raw
    ]


def main():
    by_currency = load_policy_rates()
    by_country = load_yields()
    spreads = compute_spreads(by_country)
    streaks = compute_inversion_streaks(spreads)
    spread_matrix = build_policy_spread_matrix(by_currency)
    fred = load_fred()

    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "policy_summary": build_policy_summary(by_currency),
        "policy_history": build_history_series(by_currency),
        "yield_curves": build_yield_curves(by_country),
        "yield_series": build_yield_series(by_country),
        "spreads": spreads,
        "speeches": load_speeches(),
        "policy_spread_matrix": spread_matrix,
        "yield_changes": build_yield_changes(by_country),
        "inversion_streaks": streaks,
        "rule_hints": build_rule_hints(streaks, spread_matrix),
        "macro": fred,
        "real_policy_rate": build_real_policy_rate(by_currency, fred),
        "thresholds": THRESHOLDS,
    }

    SITE_JS.parent.mkdir(parents=True, exist_ok=True)
    with open(SITE_JS, "w", encoding="utf-8") as f:
        f.write("// 自動產生，勿手動編輯。來源：GlobalRates/gen_site_data.py\n")
        f.write("const GLOBALRATES_DATA = ")
        json.dump(data, f, ensure_ascii=False, indent=None, separators=(",", ":"))
        f.write(";\n")

    print(f"完成，寫入 {SITE_JS}")
    print(f"  政策利率幣別：{len(data['policy_summary'])}")
    print(f"  殖利率國家：{list(data['yield_curves'].keys())}")


if __name__ == "__main__":
    main()
