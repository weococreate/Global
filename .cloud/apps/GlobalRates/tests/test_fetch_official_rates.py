#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GlobalRates/tests/test_fetch_official_rates.py — 央行官網補 BIS 延遲的合併邏輯。
重點：2026-09-30 RBA 升息到 4.60%，BIS 只到 09-24 仍是 4.35%，網站因此顯示舊值。
執行：python3 -m pytest apps/GlobalRates/tests/test_fetch_official_rates.py -v
"""
import os
import sys
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_official_rates as fo  # noqa: E402

fo.RETRY_WAITS = (0, 0)   # 測試不真的等；次數（共 3 次）不變

RBA_POINTS = [("2026-08-12", 4.35), ("2026-09-30", 4.60)]
BIS_AUD = [("2026-09-23", 4.35), ("2026-09-24", 4.35)]


def test_overlay_reflects_rba_hike_after_bis_lag():
    rows = fo.overlay("AUD", BIS_AUD, RBA_POINTS, today=date(2026, 10, 1))
    # 09-25(五)、09-28(一)、09-29(二) 仍 4.35；09-30 起 4.60；週末不補
    assert rows == [
        ("2026-09-25", 4.35), ("2026-09-28", 4.35), ("2026-09-29", 4.35),
        ("2026-09-30", 4.60), ("2026-10-01", 4.60),
    ]


def test_overlay_skips_when_official_disagrees_with_bis():
    """口徑不同（例如美國 BIS 用區間中值、官方給上限）時不可混入。"""
    bis = [("2026-09-28", 3.875)]
    official = [("2026-09-28", 4.00), ("2026-09-30", 4.00)]
    assert fo.overlay("USD", bis, official, today=date(2026, 10, 1)) == []


def test_overlay_never_overwrites_dates_bis_already_has():
    bis = [("2026-09-29", 2.5), ("2026-09-30", 2.5)]
    official = [("2026-09-29", 2.5), ("2026-09-30", 2.5), ("2026-10-01", 2.25)]
    rows = fo.overlay("EUR", bis, official, today=date(2026, 10, 1))
    assert rows == [("2026-10-01", 2.25)]


def test_overlay_empty_inputs():
    assert fo.overlay("AUD", [], RBA_POINTS) == []
    assert fo.overlay("AUD", BIS_AUD, []) == []


def test_official_rows_isolates_source_failure():
    """一個央行抓失敗只略過該幣別，其他照補。"""
    def boom():
        raise RuntimeError("403")
    fake = {
        "AUD": ("RBA-official", lambda: RBA_POINTS),
        "GBP": ("BoE-official", boom),
    }
    bis = {"AUD": BIS_AUD, "GBP": [("2026-09-28", 3.75)]}
    with patch.object(fo, "OFFICIAL", fake), \
         patch.object(fo, "date") as d:
        d.today.return_value = date(2026, 10, 1)
        d.fromisoformat = date.fromisoformat
        rows = fo.official_rows(bis)
    assert {r[0] for r in rows} == {"AUD"}
    assert ("AUD", "2026-09-30", 4.60, "RBA-official") in rows


# ── 2026-10-05：官方來源逾時重試（10/04 ECB 逾時一次，整班雲端更新作廢） ──

def test_retry_succeeds_after_two_timeouts():
    calls, waits = [], []
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("The read operation timed out")
        return [("2026-10-02", 2.5)]
    assert fo.fetch_with_retry(flaky, sleep=waits.append) == [("2026-10-02", 2.5)]
    assert len(calls) == 3 and waits == list(fo.RETRY_WAITS)


def test_retry_gives_up_and_raises_last_error():
    calls = []
    def dead():
        calls.append(1)
        raise TimeoutError(f"第 {len(calls)} 次")
    try:
        fo.fetch_with_retry(dead, sleep=lambda s: None)
        assert False, "應該丟出錯誤"
    except TimeoutError as e:
        assert str(e) == "第 3 次"
    assert len(calls) == len(fo.RETRY_WAITS) + 1


def test_official_rows_uses_retry_and_other_currencies_unaffected():
    """EUR 連三次逾時只略過 EUR；其他幣別照補。"""
    n = {"eur": 0}
    def eur():
        n["eur"] += 1
        raise TimeoutError("timed out")
    official = {"EUR": ("ECB-official", eur), "AUD": ("RBA-official", lambda: RBA_POINTS)}
    with patch.object(fo, "OFFICIAL", official), patch.object(fo, "RETRY_WAITS", (0, 0)):
        rows = fo.official_rows({"EUR": [("2026-09-29", 2.5)], "AUD": BIS_AUD})
    assert n["eur"] == 3
    assert {r[0] for r in rows} == {"AUD"}

