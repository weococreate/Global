#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GlobalRates/tests/test_gen_site_data.py — gen_site_data.py 單元測試（原本零測試覆蓋，2026-07-08 補上）
執行：python3 -m pytest GlobalRates/tests/test_gen_site_data.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gen_site_data as gsd  # noqa: E402


# ── find_last_change：bps 變動偵測 ─────────────────────────

def test_find_last_change_detects_last_rate_change_direction_and_delta():
    rows = [
        {'date': '2024-01-01', 'rate': '5.50'},
        {'date': '2024-06-01', 'rate': '5.50'},  # 不變
        {'date': '2024-09-01', 'rate': '5.25'},  # 降息
        {'date': '2024-12-01', 'rate': '5.25'},  # 不變（維持到最新）
    ]
    change = gsd.find_last_change(rows)
    assert change['direction'] == 'down'
    assert change['delta'] == -0.25
    assert change['date'] == '2024-09-01'


def test_find_last_change_ignores_empty_placeholder_values():
    """SGD 兜底情形：rate 為空字串，不應被當成有效觀測值"""
    rows = [{'date': '2026-01-01', 'rate': ''}]
    assert gsd.find_last_change(rows) is None


def test_find_last_change_none_when_rate_never_changes():
    rows = [{'date': '2024-01-01', 'rate': '3.0'}, {'date': '2024-06-01', 'rate': '3.0'}]
    assert gsd.find_last_change(rows) is None


# ── build_policy_summary：latest 值與 has_live_data 旗標 ──

def test_build_policy_summary_flags_no_live_data_for_empty_series():
    by_currency = {'USD': [{'date': '2026-01-01', 'rate': '5.0', 'source': 'BIS'}]}
    summary = gsd.build_policy_summary(by_currency)
    by_ccy = {s['currency']: s for s in summary}
    assert by_ccy['USD']['has_live_data'] is True
    assert by_ccy['USD']['current_rate'] == 5.0
    assert by_ccy['SGD']['has_live_data'] is False  # 未提供資料的幣別誠實標記無即時資料
    assert by_ccy['SGD']['current_rate'] is None


# ── build_yield_curves：取最新交易日完整曲線 ───────────────

def test_build_yield_curves_uses_latest_date_across_tenors():
    by_country = {
        'US': {
            '1Y': [{'date': '2026-01-01', 'yield': 4.0}, {'date': '2026-01-02', 'yield': 4.1}],
            '10Y': [{'date': '2026-01-01', 'yield': 4.5}, {'date': '2026-01-02', 'yield': 4.6}],
        }
    }
    curves = gsd.build_yield_curves(by_country)
    assert curves['US']['as_of'] == '2026-01-02'
    tenor_map = {c['tenor']: c['yield'] for c in curves['US']['curve']}
    assert tenor_map['1Y'] == 4.1
    assert tenor_map['10Y'] == 4.6


def test_build_yield_curves_skips_country_with_no_data():
    assert gsd.build_yield_curves({'XX': {}}) == {}


# ── compute_spreads：期限利差與倒掛判斷 ────────────────────

def test_compute_spreads_detects_inversion():
    by_country = {
        'US': {
            '2Y': [{'date': '2026-01-01', 'yield': 5.0}],
            '10Y': [{'date': '2026-01-01', 'yield': 4.5}],  # 10Y < 2Y → 倒掛
            '30Y': [{'date': '2026-01-01', 'yield': 4.8}],
        }
    }
    spreads = gsd.compute_spreads(by_country)
    s2s10s = spreads['US']['2s10s'][0]
    assert s2s10s['spread_bp'] == -50.0
    assert s2s10s['inverted'] is True

    s10s30s = spreads['US']['10s30s'][0]
    assert s10s30s['spread_bp'] == 30.0
    assert s10s30s['inverted'] is False


def test_compute_spreads_skips_dates_missing_required_tenor():
    by_country = {'US': {'2Y': [{'date': '2026-01-01', 'yield': 5.0}]}}  # 缺 10Y／30Y
    spreads = gsd.compute_spreads(by_country)
    assert spreads['US']['2s10s'] == []
    assert spreads['US']['10s30s'] == []


# ── load_speeches：缺檔時安全回傳空陣列 ────────────────────

def test_load_speeches_returns_empty_list_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(gsd, 'OUT_DIR', tmp_path)
    assert gsd.load_speeches() == []


# ── build_policy_spread_matrix：Phase 3 carry 利差矩陣 ─────

def test_build_policy_spread_matrix_computes_bp_vs_usd():
    by_currency = {
        'USD': [{'date': '2026-06-30', 'rate': '3.625', 'source': 'BIS'}],
        'CHF': [{'date': '2026-06-30', 'rate': '0.0', 'source': 'BIS'}],
        'ZAR': [{'date': '2026-06-29', 'rate': '7.0', 'source': 'BIS'}],
    }
    matrix = gsd.build_policy_spread_matrix(by_currency)
    by_ccy = {r['currency']: r for r in matrix}
    assert by_ccy['USD']['spread_bp'] == 0.0
    assert by_ccy['CHF']['spread_bp'] == -362.5
    assert by_ccy['ZAR']['spread_bp'] == 337.5
    # 真實資料回測：CHF/ZAR 對美元利差皆已超過預設極端門檻 300bp
    assert by_ccy['CHF']['is_extreme'] is True
    assert by_ccy['ZAR']['is_extreme'] is True


def test_build_policy_spread_matrix_no_data_currency_is_none():
    by_currency = {'USD': [{'date': '2026-06-30', 'rate': '3.625', 'source': 'BIS'}]}
    matrix = gsd.build_policy_spread_matrix(by_currency)
    by_ccy = {r['currency']: r for r in matrix}
    assert by_ccy['SGD']['spread_bp'] is None


# ── build_yield_changes：1週/1月/YTD 變動(bp) ───────────────

def test_build_yield_changes_computes_bp_deltas_with_nearest_prior_date():
    by_country = {
        'US': {
            '10Y': [
                {'date': '2026-01-01', 'yield': 4.00},
                {'date': '2026-06-01', 'yield': 4.20},
                {'date': '2026-06-23', 'yield': 4.30},
                {'date': '2026-06-30', 'yield': 4.50},
            ],
        }
    }
    changes = gsd.build_yield_changes(by_country)
    entry = changes['US']['10Y']
    assert entry['as_of'] == '2026-06-30'
    # 1週前(06-23)最接近的一筆是06-23本身：4.50-4.30=0.20 → 20bp
    assert entry['change_1w_bp'] == 20.0
    # 1月前(05-31)沒有 <= 05-31 的資料是 06-01（06-01 > 05-31 不合格），改抓01-01那筆：4.50-4.00=0.50 → 50bp
    assert entry['change_1m_bp'] == 50.0
    # YTD：對照 2026-01-01 那筆 4.00 → 4.50-4.00=0.50 → 50bp
    assert entry['change_ytd_bp'] == 50.0


def test_build_yield_changes_none_when_no_prior_data_available():
    by_country = {'US': {'10Y': [{'date': '2026-06-30', 'yield': 4.50}]}}
    changes = gsd.build_yield_changes(by_country)
    assert changes['US']['10Y']['change_1w_bp'] is None
    assert changes['US']['10Y']['change_ytd_bp'] is None


# ── compute_inversion_streaks／build_rule_hints：規則型提示 ─

def test_compute_inversion_streaks_counts_from_tail_only():
    spreads = {
        'US': {
            '2s10s': [
                {'date': '2026-01-01', 'spread_bp': 10.0, 'inverted': False},
                {'date': '2026-01-02', 'spread_bp': -5.0, 'inverted': True},
                {'date': '2026-01-03', 'spread_bp': -8.0, 'inverted': True},
                {'date': '2026-01-04', 'spread_bp': -3.0, 'inverted': True},
            ],
        }
    }
    streaks = gsd.compute_inversion_streaks(spreads)
    assert streaks['US']['2s10s'] == 3


def test_compute_inversion_streaks_real_history_backtest():
    """回測：US 2s10s 於 2022–2024 的真實資料中曾出現過長期倒掛（歷史上限遠超提示門檻）。"""
    by_country = gsd.load_yields()
    spreads = gsd.compute_spreads(by_country)
    rows = spreads.get('US', {}).get('2s10s', [])
    if not rows:
        return  # 環境缺 output/yields.csv 時略過，不視為失敗
    max_streak = 0
    streak = 0
    for r in rows:
        if r['inverted']:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    assert max_streak >= gsd.THRESHOLDS['inversion_alert_min_days']


def test_build_rule_hints_fires_when_streak_exceeds_threshold():
    streaks = {'US': {'2s10s': gsd.THRESHOLDS['inversion_alert_min_days'] + 5}}
    hints = gsd.build_rule_hints(streaks, [])
    assert any(h['type'] == 'inversion' for h in hints)
    assert '25' in hints[0]['text'] or '20' in hints[0]['text']


def test_build_rule_hints_silent_when_streak_below_threshold():
    streaks = {'US': {'2s10s': 1}}
    hints = gsd.build_rule_hints(streaks, [])
    assert hints == []


def test_build_rule_hints_fires_on_extreme_carry():
    matrix = [{'currency': 'CHF', 'spread_bp': -362.5, 'is_extreme': True}]
    hints = gsd.build_rule_hints({}, matrix)
    assert any(h['type'] == 'carry_extreme' for h in hints)


# ── load_fred／build_real_policy_rate：FRED 總經資料（可能缺金鑰）──

def test_load_fred_returns_unavailable_when_csv_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(gsd, 'OUT_DIR', tmp_path)
    fred = gsd.load_fred()
    assert fred['available'] is False
    assert fred['series'] == {}


def test_build_real_policy_rate_none_without_fred_data():
    by_currency = {'USD': [{'date': '2026-06-30', 'rate': '3.625', 'source': 'BIS'}]}
    fred = {'available': False, 'series': {}}
    assert gsd.build_real_policy_rate(by_currency, fred) is None


def test_build_real_policy_rate_computes_when_fred_available():
    by_currency = {'USD': [{'date': '2026-06-30', 'rate': '3.625', 'source': 'BIS'}]}
    fred = {'available': True, 'series': {'cpi_yoy': [{'date': '2026-06-01', 'value': 2.8}]}}
    result = gsd.build_real_policy_rate(by_currency, fred)
    assert result['real_rate'] == 0.83
