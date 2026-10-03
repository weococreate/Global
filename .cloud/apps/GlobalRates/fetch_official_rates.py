#!/usr/bin/env python3
"""央行官網即時利率，補 BIS 延遲的那幾天。

BIS WS_CBPOL 通常落後 2-7 天，央行剛調利率時網站會照舊顯示舊值（2026-09-30 RBA 升息即是）。
這裡直接讀各央行官方資料，只補「BIS 最後一天之後」到今天的平日；BIS 補上後隔天整份 CSV
重建，就自動改回 BIS 的數字。

安全閥：官方序列在 BIS 最後一天的值必須等於 BIS 的值，否則代表兩邊口徑不同（例如美國 BIS
用目標區間中值），該幣別不補並印警告。

涵蓋 7 幣別。NZD（RBNZ 擋瀏覽器）、THB、ZAR（頁面抓不到數字）暫無官方來源，仍只靠 BIS。
任一來源失敗只略過該幣別，不擋其他。
"""
import csv
import io
import json
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0"}


def _get(url, timeout=30, headers=UA):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8-sig")


def _since():
    return (date.today() - timedelta(days=60)).isoformat()


# ── 各央行：回傳 [(YYYY-MM-DD, rate), ...]，舊到新；可是逐日值或調整生效日 ──

def fetch_usd():
    """FRED 聯邦資金目標區間上下限取中值（與 BIS 美國口徑相同）。免 API key 的 fredgraph CSV。"""
    text = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU,DFEDTARL&cosd={_since()}",
                # FRED 對瀏覽器 User-Agent 會卡住不回應，改用 curl 的
                headers={"User-Agent": "curl/8.7.1"})
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            hi, lo = float(row["DFEDTARU"]), float(row["DFEDTARL"])
        except (ValueError, KeyError):
            continue
        out.append((row["observation_date"], (hi + lo) / 2))
    return out


def fetch_eur():
    """ECB 存款機制利率（DFR）。"""
    text = _get("https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"
                f"?startPeriod={_since()}&format=csvdata")
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            out.append((row["TIME_PERIOD"], float(row["OBS_VALUE"])))
        except (ValueError, KeyError):
            continue
    return out


def fetch_gbp():
    """英格蘭銀行 Bank Rate（IUDBEDR）。"""
    start = (date.today() - timedelta(days=60)).strftime("%d/%b/%Y")
    text = _get("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes"
                f"&Datefrom={start}&Dateto=now&SeriesCodes=IUDBEDR&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            d = datetime.strptime(parts[0].strip(), "%d %b %Y").date().isoformat()
            out.append((d, float(parts[1])))
        except ValueError:
            continue
    return out


def fetch_cad():
    """加拿大央行隔夜目標利率（Valet V39079）。"""
    data = json.loads(_get(f"https://www.bankofcanada.ca/valet/observations/V39079/json?start_date={_since()}"))
    out = []
    for o in data.get("observations", []):
        try:
            out.append((o["d"], float(o["V39079"]["v"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def fetch_chf():
    """瑞士央行政策利率（snbgwdzid 的 LZ）。"""
    text = _get(f"https://data.snb.ch/api/cube/snbgwdzid/data/csv/en?fromDate={_since()}")
    out = []
    for line in text.splitlines():
        parts = [p.strip('"') for p in line.split(";")]
        if len(parts) == 3 and parts[1] == "LZ":
            try:
                out.append((parts[0], float(parts[2])))
            except ValueError:
                continue
    return out


def fetch_sek():
    """瑞典央行政策利率（SECBREPOEFF）。"""
    data = json.loads(_get(f"https://api.riksbank.se/swea/v1/Observations/SECBREPOEFF/{_since()}"))
    return [(o["date"], float(o["value"])) for o in data if o.get("value") is not None]


def fetch_aud():
    """澳洲央行現金利率目標調整表；官網擋一般程式，交給 fetch_rba.js 用瀏覽器開。"""
    r = subprocess.run(["node", str(HERE / "fetch_rba.js")], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"exit {r.returncode}")
    return [(o["date"], float(o["rate"])) for o in json.loads(r.stdout)]


OFFICIAL = {
    "USD": ("FRED-official", fetch_usd),
    "EUR": ("ECB-official", fetch_eur),
    "GBP": ("BoE-official", fetch_gbp),
    "CAD": ("BoC-official", fetch_cad),
    "CHF": ("SNB-official", fetch_chf),
    "SEK": ("Riksbank-official", fetch_sek),
    "AUD": ("RBA-official", fetch_aud),
}


def rate_at(points, d):
    """points 舊到新；回傳生效日 ≤ d 的最後一個值，沒有則 None。"""
    val = None
    for pd, pv in points:
        if pd <= d:
            val = pv
        else:
            break
    return val


def overlay(currency, bis_rows, points, today=None):
    """回傳要補在 BIS 最後一天之後的 [(date, rate), ...]（只含平日、到今天為止）。
    官方在 BIS 最後一天的值與 BIS 不符時回傳 []，避免口徑不同的數字混進來。"""
    if not bis_rows or not points:
        return []
    points = sorted(points)
    last_d, last_v = bis_rows[-1]
    off_v = rate_at(points, last_d)
    if off_v is None or abs(off_v - last_v) > 1e-6:
        print(f"  [WARN] {currency}：官方 {last_d} 值 {off_v} 與 BIS {last_v} 不符，不補", file=sys.stderr)
        return []
    today = today or date.today()
    out = []
    d = date.fromisoformat(last_d) + timedelta(days=1)
    while d <= today:
        if d.weekday() < 5:
            v = rate_at(points, d.isoformat())
            if v is not None:
                out.append((d.isoformat(), v))
        d += timedelta(days=1)
    return out


def official_rows(bis_by_currency):
    """bis_by_currency: {幣別: [(date, rate), ...]}。回傳 [(幣別, date, rate, source), ...]。"""
    rows = []
    for currency, (source, fn) in OFFICIAL.items():
        bis = bis_by_currency.get(currency) or []
        if not bis:
            continue
        try:
            points = fn()
        except Exception as e:
            print(f"  [WARN] {currency} 官方來源抓取失敗，只用 BIS：{e}", file=sys.stderr)
            continue
        added = overlay(currency, bis, points)
        if added:
            changed = [v for _, v in added if abs(v - bis[-1][1]) > 1e-6]
            note = f"，含調整 {bis[-1][1]} → {changed[-1]}" if changed else ""
            print(f"  {currency}：官方補 {len(added)} 天（{added[0][0]}～{added[-1][0]}）{note}")
        rows.extend((currency, d, v, source) for d, v in added)
    return rows
