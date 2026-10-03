#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GlobalRates/tests/test_fetch_yields.py — fetch_yields.py 單元測試（原本零測試覆蓋，2026-07-08 補上）
重點鎖定兩個已修復的真實 bug：
  ①Bundesbank API 回應為分號分隔而非逗號（fetch_de 用 delimiter=";"）
  ②日本財務省歷史全量檔有時滯，須疊加當月檔補最新缺口（rows_by_key 後蓋前）
執行：python3 -m pytest GlobalRates/tests/test_fetch_yields.py -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_yields as fy  # noqa: E402


def _fake_response(text, encoding='utf-8-sig'):
    resp = MagicMock()
    resp.read.return_value = text.encode(encoding)
    resp.__enter__ = lambda self: resp
    resp.__exit__ = lambda self, *a: False
    return resp


def test_fetch_de_parses_semicolon_delimited_csv():
    """Bundesbank 回應非逗號分隔——若解析器仍用預設逗號 delimiter 會 0 筆"""
    csv_text = 'TIME_PERIOD;OBS_VALUE\n2026-01-01;2.50\n2026-01-02;2.55\n'
    with patch.object(fy.urllib.request, 'urlopen', return_value=_fake_response(csv_text)):
        rows = fy.fetch_de()
    de_rows = [r for r in rows if r[0] == 'DE']
    assert len(de_rows) > 0
    assert ('DE', '2026-01-01', '1Y', 2.50) in rows


def test_fetch_de_filters_dot_placeholder_and_before_cutoff():
    csv_text = f'TIME_PERIOD;OBS_VALUE\n2000-01-01;1.0\n.;.\n{fy.CUTOFF_DATE[:4]}-06-01;2.0\n'
    with patch.object(fy.urllib.request, 'urlopen', return_value=_fake_response(csv_text)):
        rows = fy.fetch_de()
    dates = [d for _, d, _, _ in rows]
    assert '2000-01-01' not in dates  # 早於 CUTOFF_DATE
    assert '.' not in dates


def test_parse_mof_csv_extracts_correct_tenor_columns():
    """欄位順序 Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y——col_idx 對映錯位會抓錯年期"""
    year = int(fy.CUTOFF_DATE[:4]) + 1
    line = f'{year}/1/5,' + ','.join(str(i) for i in range(1, 16))
    rows = fy._parse_mof_csv(line)
    by_tenor = {tenor: v for _, _, tenor, v in rows}
    assert by_tenor['1Y'] == 1.0
    assert by_tenor['2Y'] == 2.0
    assert by_tenor['5Y'] == 5.0
    assert by_tenor['10Y'] == 10.0
    assert by_tenor['20Y'] == 12.0  # col_idx['20Y']=12（第 13 欄，非第 21 欄）
    assert by_tenor['30Y'] == 14.0


def test_parse_mof_csv_skips_rows_before_cutoff_and_malformed():
    old_line = '2000/1/5,' + ','.join(str(i) for i in range(1, 16))
    short_line = '2026/1/5,1,2'
    no_date_line = ',1,2,3,4,5,6,7,8,9,10,11,12,13,14'
    rows = fy._parse_mof_csv(f'{old_line}\n{short_line}\n{no_date_line}\n')
    assert rows == []


def test_fetch_jp_current_month_file_overrides_historical_stale_gap():
    """日本歷史全量檔時滯約一週，當月檔須覆蓋同鍵值補最新缺口，而非被歷史檔蓋掉"""
    year = int(fy.CUTOFF_DATE[:4]) + 1
    historical = f'{year}/1/5,' + ','.join(['9.99'] * 15)  # 假裝歷史檔尚未更新到最新值
    current = f'{year}/1/5,' + ','.join(['1.11'] * 15)     # 當月檔已有最新真實值

    def fake_urlopen(url, timeout=30):
        if url == fy.MOF_ALL_URL:
            return _fake_response(historical, encoding='utf-8')
        return _fake_response(current, encoding='utf-8')

    with patch.object(fy.urllib.request, 'urlopen', side_effect=fake_urlopen):
        rows = fy.fetch_jp()

    by_tenor = {tenor: v for _, _, tenor, v in rows}
    assert by_tenor['1Y'] == 1.11  # 當月檔覆蓋歷史檔，而非維持歷史檔的 9.99


def test_fetch_jp_falls_back_to_historical_when_current_month_fails():
    year = int(fy.CUTOFF_DATE[:4]) + 1
    historical = f'{year}/1/5,' + ','.join(['1.11'] * 15)

    def fake_urlopen(url, timeout=30):
        if url == fy.MOF_ALL_URL:
            return _fake_response(historical, encoding='utf-8')
        raise OSError('timeout')

    with patch.object(fy.urllib.request, 'urlopen', side_effect=fake_urlopen):
        rows = fy.fetch_jp()

    assert len(rows) > 0
