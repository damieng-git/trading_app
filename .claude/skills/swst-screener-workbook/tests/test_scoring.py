"""Flags and fixed-anchor scoring (pure functions, no network)."""

import math
import os
import sys

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import build_workbook as B  # noqa: E402
import flags as F           # noqa: E402
import score as S           # noqa: E402

CFG = B.load_config()


# ---------------------------------------------------------------- flags
def test_oversold_boundary():
    assert F.is_oversold({"rsi_14": 29.99}, CFG) is True
    assert F.is_oversold({"rsi_14": 30.0}, CFG) is False     # strictly below
    assert F.is_oversold({"rsi_14": None}, CFG) is False
    assert F.is_oversold({"rsi_14": float("nan")}, CFG) is False


def test_breakout_boundary():
    assert F.is_breakout({"pct_from_high": -0.03}, CFG) is True   # <= threshold
    assert F.is_breakout({"pct_from_high": -0.0301}, CFG) is False
    assert F.is_breakout({"pct_from_high": 0.0}, CFG) is True
    assert F.is_breakout({"pct_from_high": None}, CFG) is False


def test_pullback_boundary_and_uptrend_condition():
    assert F.is_pullback({"dist_ma50": 0.03, "dist_ma200": 0.05}, CFG) is True
    assert F.is_pullback({"dist_ma50": 0.0301, "dist_ma200": 0.05}, CFG) is False
    assert F.is_pullback({"dist_ma50": 0.02, "dist_ma200": 0.02}, CFG) is False  # not <
    assert F.is_pullback({"dist_ma50": -0.01, "dist_ma200": -0.05}, CFG) is False
    assert F.is_pullback({"dist_ma50": None, "dist_ma200": 0.05}, CFG) is False
    assert F.is_pullback({"dist_ma50": 0.01, "dist_ma200": None}, CFG) is False


def test_flags_follow_cfg_thresholds():
    cfg = {"thresholds": {"oversold_rsi": 40, "breakout_within_pct": 0.10,
                          "pullback_within_pct": 0.10}}
    assert F.is_oversold({"rsi_14": 35}, cfg)
    assert F.is_breakout({"pct_from_high": -0.09}, cfg)
    assert F.is_pullback({"dist_ma50": 0.09, "dist_ma200": 0.2}, cfg)


def test_downtrend_mirrors_flag_column():
    assert F.is_downtrend({"_flag_down": True}) is True
    assert F.is_downtrend({"_flag_down": False}) is False
    assert F.is_downtrend({}) is False
    assert F.is_downtrend({"_flag_down": float("nan")}) is False


# ---------------------------------------------------------------- scoring
def _row(**kw):
    base = {"industry": "Software", "yf_sector": None, "country": "us",
            "_eur_cap": 5000.0}
    base.update(kw)
    return base


def test_weights_sum_to_one_per_tier():
    for tier, w in CFG["scoring"]["weights"].items():
        assert len(w) == 4 and sum(w) == pytest.approx(1.0), tier


def test_hand_computed_financial_row():
    row = _row(industry="Banks", _eur_cap=20000.0, pe_forward=12.0, pb_ratio=1.65,
               ev_ebitda=3.0, p_fcf=5.0,                       # ignored for financials
               growth_sws=0.075, roe=0.10,
               beta=1.25, max_drawdown_3y=-0.425,
               avg_dollar_vol_eur_m=0.5 * math.sqrt(40.0), rsi_14=27.0)
    out = S.score_row(row, CFG)
    assert out["tier"] == "large" and out["is_fin"] is True
    # value: pe (30-12)/20 = 0.9, pb (2.5-1.65)/1.7 = 0.5 -> (1.4 + 0.5) / 3
    assert out["score_value"] == pytest.approx(1.9 / 3)
    # quality: growth 0.5, roe 0.5 -> (1.0 + 0.5) / 3
    assert out["score_quality"] == pytest.approx(0.5)
    # risk: beta 0.5, dd 0.5, liquidity 0.5 -> (1.5 + 0.5) / 4
    assert out["score_risk"] == pytest.approx(0.5)
    # timing: RSI 27 -> (45-27)/15 clipped to 1; no breakout/pullback inputs
    assert out["score_timing"] == pytest.approx(1.0)
    comp = 0.35 * (1.9 / 3) + 0.35 * 0.5 + 0.10 * 1.0 + 0.20 * 0.5
    assert out["composite"] == pytest.approx(comp)
    assert out["score_0_100"] == 60
    assert out["not_scored_reason"] is None
    assert out["gate"] is None          # large needs V>=0.5 and Q>=0.6; Q is 0.5
    assert out["coverage_pct"] == pytest.approx(1.0)


def test_loss_maker_one_value_metric_is_not_scored():
    row = _row(pe_forward=None, pe_trailing=None, ev_ebitda=None, p_fcf=None,
               ps_ratio=4.0, growth_sws=0.10, net_cash_eur_m=500.0, fcf_positive=False,
               runway_quarters=6.0, beta=1.0, max_drawdown_3y=-0.5,
               avg_dollar_vol_eur_m=5.0, rsi_14=50.0)
    out = S.score_row(row, CFG)
    assert out["score_value"] is None            # 1 metric < min 2
    assert out["composite"] is None and out["score_0_100"] is None
    assert out["not_scored_reason"] == "missing: value"
    # quality still computed: net cash 0.1 -> 1.0, growth 0.667, fcf 0.0, runway 0.5
    q = (1.0 + 10 / 15 + 0.0 + 0.5 + 0.5) / 5
    assert out["score_quality"] == pytest.approx(q)


def test_unresolved_eu_row_has_tier_but_no_score():
    row = _row(country="eu", _eur_cap=165900.0, growth_sws=0.04, industry="Insurance")
    out = S.score_row(row, CFG)
    assert out["tier"] == "large"
    assert out["composite"] is None
    assert out["not_scored_reason"] == "missing: value, quality, timing, risk"
    assert out["coverage_pct"] < 0.5


def test_missing_market_cap_has_no_tier_and_is_flagged():
    row = _row(_eur_cap=None, market_cap_local_m=None, currency=None, pe_forward=15.0,
               ev_ebitda=10.0, growth_sws=0.1, roe=0.1, pb_ratio=2.0, beta=1.0,
               max_drawdown_3y=-0.3, avg_dollar_vol_eur_m=5.0, rsi_14=40.0)
    out = S.score_row(row, CFG)
    assert out["tier"] is None and out["composite"] is None
    assert "market cap" in out["not_scored_reason"]


def test_anchor_clipping():
    va = CFG["scoring"]["value_anchors"]["pe"]
    assert S._multiple(va[0] - 5, va) == 1.0       # better than the good anchor
    assert S._multiple(va[1] + 50, va) == 0.0      # worse than the bad anchor
    assert S._multiple(-3.0, va) is None           # negative denominator
    assert S._high_better(5.0, [0, 0.2]) == 1.0
    assert S._high_better(-5.0, [0, 0.2]) == 0.0
    assert S.clip01(1.7) == 1.0 and S.clip01(-0.2) == 0.0 and S.clip01(None) is None


def test_nan_inputs_are_none_never_zero():
    row = _row(pe_forward=float("nan"), pe_trailing=float("nan"), ev_ebitda=float("nan"),
               p_fcf=float("nan"), ps_ratio=float("nan"), growth_sws=float("nan"),
               roe=float("nan"), beta=float("nan"), max_drawdown_3y=float("nan"),
               avg_dollar_vol_eur_m=float("nan"), rsi_14=float("nan"))
    out = S.score_row(row, CFG)
    for k in ("score_value", "score_quality", "score_timing", "score_risk", "composite"):
        assert out[k] is None, k
    assert out["coverage_pct"] == 0.0


def test_pe_falls_back_to_trailing_and_ps_only_without_pe_and_ev():
    a = S.score_row(_row(pe_forward=None, pe_trailing=20.0, ev_ebitda=10.0), CFG)
    b = S.score_row(_row(pe_forward=20.0, pe_trailing=None, ev_ebitda=10.0), CFG)
    assert a["score_value"] == pytest.approx(b["score_value"])
    # ps ignored when pe present
    c = S.score_row(_row(pe_forward=20.0, ev_ebitda=10.0, ps_ratio=1.0), CFG)
    assert c["score_value"] == pytest.approx(b["score_value"])


def test_roe_requires_positive_pb_and_fin_skips_ev_pfcf():
    no_pb = S.score_row(_row(growth_sws=0.15, roe=0.2, pb_ratio=None, fcf_positive=True),
                        CFG)
    # roe dropped -> quality metrics: growth 1.0, fcf 1.0 -> (2 + .5) / 3
    assert no_pb["score_quality"] == pytest.approx(2.5 / 3)
    fin = S.score_row(_row(industry="Banks", pe_forward=10.0, pb_ratio=0.8,
                           ev_ebitda=50.0, p_fcf=500.0), CFG)
    assert fin["score_value"] == pytest.approx((1.0 + 1.0 + 0.5) / 3)


def test_timing_setups_and_zero_is_real():
    # no setup: RSI above 45, not near high, not in pullback -> a real 0.0
    r = _row(rsi_14=60.0, pct_from_high=-0.30, dist_ma200=0.1, dist_ma50=0.2)
    assert S.score_row(r, CFG)["score_timing"] == 0.0
    # breakout needs price above the 200d MA
    r = _row(rsi_14=60.0, pct_from_high=-0.02, dist_ma200=-0.05, dist_ma50=-0.04)
    assert S.score_row(r, CFG)["score_timing"] == 0.0
    r = _row(rsi_14=60.0, pct_from_high=-0.02, dist_ma200=0.05, dist_ma50=0.04)
    assert S.score_row(r, CFG)["score_timing"] == 1.0
    # decaying breakout: 6.5% from high -> 1 - (0.065-0.03)/(0.10-0.03) = 0.5
    r = _row(rsi_14=60.0, pct_from_high=-0.065, dist_ma200=0.05, dist_ma50=0.2)
    assert S.score_row(r, CFG)["score_timing"] == pytest.approx(0.5)
    # pullback: 5.5% from 50d MA in an uptrend -> 1 - (0.055-0.03)/(0.08-0.03) = 0.5
    r = _row(rsi_14=60.0, pct_from_high=-0.30, dist_ma50=0.055, dist_ma200=0.3)
    assert S.score_row(r, CFG)["score_timing"] == pytest.approx(0.5)
    assert S.score_row(_row(rsi_14=None), CFG)["score_timing"] is None


def test_gates_and_downtrend_label():
    base = dict(pe_forward=10.0, ev_ebitda=6.0, p_fcf=10.0, growth_sws=0.15,
                net_cash_eur_m=100.0, fcf_positive=True, roe=0.2, pb_ratio=1.0,
                beta=0.5, max_drawdown_3y=-0.15, avg_dollar_vol_eur_m=20.0, rsi_14=50.0)
    large = S.score_row(_row(_eur_cap=50000.0, **base), CFG)
    assert large["gate"] == "DCA candidate (review)"
    mid_no_setup = S.score_row(_row(_eur_cap=5000.0, **base), CFG)
    assert mid_no_setup["gate"] is None
    mid_setup = S.score_row(_row(_eur_cap=5000.0, rsi_14=20.0,
                                 **{k: v for k, v in base.items() if k != "rsi_14"}), CFG)
    assert mid_setup["gate"] == "Entry setup (review)"
    down = S.score_row(_row(_eur_cap=5000.0, _flag_down=True, **base), CFG)
    assert down["downtrend_label"] == "Downtrend + quality holds (review)"
    weak = S.score_row(_row(_flag_down=True, growth_sws=0.0, fcf_positive=False,
                            net_cash_eur_m=-5000.0), CFG)
    assert weak["downtrend_label"] == "Downtrend, quality weak"


def test_score_frame_is_pure_and_ranks_with_min_ties():
    good = dict(pe_forward=10.0, ev_ebitda=6.0, growth_sws=0.15, fcf_positive=True,
                beta=0.5, max_drawdown_3y=-0.15, avg_dollar_vol_eur_m=20.0, rsi_14=20.0)
    poor = dict(pe_forward=30.0, ev_ebitda=20.0, growth_sws=0.0, fcf_positive=False,
                beta=2.0, max_drawdown_3y=-0.7, avg_dollar_vol_eur_m=0.5, rsi_14=60.0)
    df = pd.DataFrame([_row(ticker="A", **good), _row(ticker="B", **good),
                       _row(ticker="C", **poor), _row(ticker="D", country="eu")])
    before = df.copy(deep=True)
    out = S.score_frame(df, CFG)
    pd.testing.assert_frame_equal(df, before)             # input untouched
    ranks = dict(zip(out["ticker"], out["rank_in_list"]))
    assert ranks["A"] == 1 and ranks["B"] == 1 and ranks["C"] == 3
    assert ranks["D"] is None
    assert out.loc[out.ticker == "D", "score_0_100"].iloc[0] is None
    assert list(out.loc[out.ticker == "A", "rank_in_tier"]) == [1]
    for col in S.SCORE_COLUMNS:
        assert col in out.columns
