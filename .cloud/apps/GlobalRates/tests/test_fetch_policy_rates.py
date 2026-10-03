#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GlobalRates/tests/test_fetch_policy_rates.py — fetch_policy_rates.py 單元測試
（原本零測試覆蓋，2026-07-08 補上）。重點涵蓋已知真實 bug：BIS 用字面字串 "NaN"
標記缺值觀測，若未過濾會被 float() 成功轉換污染資料。
執行：python3 -m pytest GlobalRates/tests/test_fetch_policy_rates.py -v
"""
import io
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_policy_rates as fpr  # noqa: E402


def _fake_response(text):
    resp = MagicMock()
    resp.read.return_value = text.encode('utf-8-sig')
    resp.__enter__ = lambda self: resp
    resp.__exit__ = lambda self, *a: False
    return resp


def test_fetch_bis_filters_literal_nan_string():
    """BIS 用字面字串 "NaN"（非空字串）標記缺值，未過濾會讓 float("NaN") 成功轉換
    污染資料——這是已修復的真實 bug，此測試鎖定回歸。"""
    csv_text = (
        "TIME_PERIOD,OBS_VALUE\n"
        "2026-01-01,4.50\n"
        "2026-01-02,NaN\n"
        "2026-01-03,4.25\n"
    )
    with patch.object(fpr.urllib.request, 'urlopen', return_value=_fake_response(csv_text)):
        rows = fpr.fetch_bis('USD', 'US')

    assert rows == [('2026-01-01', 4.50), ('2026-01-03', 4.25)]
    assert not any(v != v for _, v in rows)  # 確認沒有任何 NaN float 混入（NaN != NaN）


def test_fetch_bis_sorts_ascending_by_date():
    csv_text = "TIME_PERIOD,OBS_VALUE\n2026-03-01,5.0\n2026-01-01,4.5\n2026-02-01,4.75\n"
    with patch.object(fpr.urllib.request, 'urlopen', return_value=_fake_response(csv_text)):
        rows = fpr.fetch_bis('USD', 'US')
    assert [d for d, _ in rows] == ['2026-01-01', '2026-02-01', '2026-03-01']


def test_fetch_bis_returns_empty_on_missing_columns():
    csv_text = "WRONG_COL,ANOTHER\n1,2\n"
    with patch.object(fpr.urllib.request, 'urlopen', return_value=_fake_response(csv_text)):
        rows = fpr.fetch_bis('USD', 'US')
    assert rows == []


def test_fetch_bis_returns_empty_on_network_failure():
    with patch.object(fpr.urllib.request, 'urlopen', side_effect=OSError('timeout')):
        rows = fpr.fetch_bis('USD', 'US')
    assert rows == []


def test_fetch_cbc_twd_reuses_shared_database_without_network():
    sys.path.insert(0, str(fpr.Path(__file__).resolve().parents[3]))
    from finatlas import policy_export
    fixture = [{'date':'2023-03-24','rate':1.875}, {'date':'2024-03-22','rate':2.0}]
    with patch.object(policy_export, 'twd_rows', return_value=fixture), patch.object(fpr.urllib.request, 'urlopen', side_effect=AssertionError('禁止重抓央行')):
        assert fpr.fetch_cbc_twd() == [('2023-03-24',1.875),('2024-03-22',2.0)]


def test_sgd_fallback_returns_none_rate_placeholder():
    rows = fpr.sgd_fallback()
    assert len(rows) == 1
    date, rate = rows[0]
    assert rate is None
    assert len(date) == 10  # YYYY-MM-DD
