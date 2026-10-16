"""Deterministic, fixed-anchor scoring for the dashboard.

Each metric is scored 0..1 against hand-set anchors from config (scoring.*), not
against the list's own distribution. A missing input gives a None metric score,
never 0. Parts (value / quality / timing / risk) are combined with cap-tier
weights. The anchors and weights are hand-set, not fitted or backtested.

Public API:
  score_row(row, cfg)    -> dict of score columns for one row
  score_frame(df, cfg)   -> new DataFrame with the score columns appended (pure)
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

import enrich as _enrich
from flags import _num, compute_flags

PARTS = ("value", "quality", "timing", "risk")
SCORE_COLUMNS = [
    "tier", "is_fin", "score_value", "score_quality", "score_timing", "score_risk",
    "composite", "score_0_100", "rank_in_list", "rank_in_tier", "coverage_pct",
    "gate", "downtrend_label", "not_scored_reason",
    "flag_oversold", "flag_breakout", "flag_pullback",
]


def clip01(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    return min(1.0, max(0.0, float(x)))


def _low_better(x, anchors) -> Optional[float]:
    x = _num(x)
    if x is None:
        return None
    lo, hi = anchors
    return clip01((hi - x) / (hi - lo))


def _high_better(x, anchors) -> Optional[float]:
    x = _num(x)
    if x is None:
        return None
    lo, hi = anchors
    return clip01((x - lo) / (hi - lo))


def _multiple(x, anchors) -> Optional[float]:
    """Lower-better multiple; non-positive denominators are not scoreable."""
    x = _num(x)
    if x is None or x <= 0:
        return None
    return _low_better(x, anchors)


def _part(scores: list, min_n: int) -> Optional[float]:
    vals = [s for s in scores if s is not None]
    if len(vals) < min_n:
        return None
    return (sum(vals) + 0.5) / (len(vals) + 1)


def _eur_cap_m(row, cfg: dict) -> Optional[float]:
    v = _num(row.get("_eur_cap"))
    if v is not None:
        return v
    cap, ccy = _num(row.get("market_cap_local_m")), row.get("currency")
    fx = {k.upper(): float(x) for k, x in cfg.get("fx_per_eur", {}).items()}
    if cap is None or not ccy or str(ccy).upper() not in fx:
        return None
    return cap / fx[str(ccy).upper()]


def is_financial(row, cfg: dict) -> bool:
    sc = cfg["scoring"]
    return (row.get("industry") in sc["financial_industries_sws"]
            or row.get("yf_sector") in sc["financial_sector_yf"])


def _timing(row, cfg: dict) -> Optional[float]:
    rsi = _num(row.get("rsi_14"))
    if rsi is None:
        return None
    sc = cfg["scoring"]
    tm = sc["timing"]
    th = cfg.get("thresholds", {})
    osold = float(th.get("oversold_rsi", 30))
    bw = float(th.get("breakout_within_pct", 0.03))
    pw = float(th.get("pullback_within_pct", 0.03))
    cands = [clip01((float(tm["rsi_zero"]) - rsi) / (float(tm["rsi_zero"]) - osold))]

    pfh, d50, d200 = (_num(row.get("pct_from_high")), _num(row.get("dist_ma50")),
                      _num(row.get("dist_ma200")))
    if pfh is not None and d200 is not None and d200 > 0:
        z = float(tm["breakout_zero_pct"])
        a = abs(pfh)
        cands.append(1.0 if a <= bw else clip01(1 - (a - bw) / (z - bw)))
    if d50 is not None and d200 is not None and d50 < d200:
        z = float(tm["pullback_zero_pct"])
        a = abs(d50)
        cands.append(1.0 if a <= pw else clip01(1 - (a - pw) / (z - pw)))
    return max(cands)


def score_row(row, cfg: dict) -> dict:
    sc = cfg["scoring"]
    va, qa, ra = sc["value_anchors"], sc["quality_anchors"], sc["risk_anchors"]
    minn = sc["min_metrics"]

    eur_cap = _eur_cap_m(row, cfg)
    tier = _enrich.cap_tier(eur_cap, cfg)
    fin = is_financial(row, cfg)

    pe = _num(row.get("pe_forward"))
    if pe is None:
        pe = _num(row.get("pe_trailing"))
    pb = _num(row.get("pb_ratio"))
    ev, pfcf, ps = (_num(row.get("ev_ebitda")), _num(row.get("p_fcf")),
                    _num(row.get("ps_ratio")))
    growth = _num(row.get("growth_sws"))
    roe = _num(row.get("roe"))
    roe_s = _high_better(roe, qa["roe"]) if (pb is not None and pb > 0) else None

    # ---- Value ----
    if fin:
        v_scores = [_multiple(pe, va["pe"]), _multiple(pb, va["pb"])]
    else:
        v_scores = [_multiple(pe, va["pe"]), _multiple(ev, va["ev_ebitda"]),
                    _multiple(pfcf, va["p_fcf"])]
        if _num(pe) is None and _num(ev) is None:
            v_scores.append(_multiple(ps, va["ps"]))
    value = _part(v_scores, int(minn["value"]))

    # ---- Quality ----
    g_s = _high_better(growth, qa["growth_sws"])
    if fin:
        q_scores = [g_s, roe_s]
    else:
        nc = _num(row.get("net_cash_eur_m"))
        nc_ratio = nc / eur_cap if (nc is not None and eur_cap and eur_cap > 0) else None
        fp = row.get("fcf_positive")
        fp_s = None if fp is None or (isinstance(fp, float) and np.isnan(fp)) \
            else (1.0 if bool(fp) else 0.0)
        rq = _num(row.get("runway_quarters"))
        runway_s = None if rq is None else clip01(rq / float(sc["runway_full_qtrs"]))
        q_scores = [_high_better(nc_ratio, qa["net_cash_to_cap"]), g_s, roe_s, fp_s,
                    runway_s]
    quality = _part(q_scores, int(minn["quality"]))

    # ---- Risk ----
    beta_s = _low_better(row.get("beta"), ra["beta"])
    dd_s = _high_better(row.get("max_drawdown_3y"), ra["max_dd"])
    adv = _num(row.get("avg_dollar_vol_eur_m"))
    lo, hi = ra["adv_eur_m"]
    liq_s = None
    if adv is not None and adv > 0:
        liq_s = clip01(math.log10(adv / lo) / math.log10(hi / lo))
    risk = _part([beta_s, dd_s, liq_s], int(minn["risk"]))

    timing = _timing(row, cfg)

    parts = {"value": value, "quality": quality, "timing": timing, "risk": risk}
    missing = [p for p in PARTS if parts[p] is None]
    composite, reason = None, None
    if not missing and tier is not None:
        w = sc["weights"][tier]
        composite = sum(float(wi) * parts[p] for wi, p in zip(w, PARTS))
    else:
        bits = list(missing) + (["market cap"] if tier is None else [])
        reason = "missing: " + ", ".join(bits)

    # ---- coverage: populated applicable metric slots / applicable slots ----
    if fin:
        slots = [_multiple(pe, va["pe"]), _multiple(pb, va["pb"]), g_s, roe_s]
    else:
        slots = v_scores[:3] + q_scores[:4]
    slots += [timing, beta_s, dd_s, liq_s]
    coverage = sum(s is not None for s in slots) / len(slots)

    flags = compute_flags(row, cfg)
    gate = None
    if composite is not None:
        if tier == "large":
            if value >= 0.5 and quality >= 0.6:
                gate = "DCA candidate (review)"
        elif value >= 0.5 and quality >= 0.5 and (
                flags["oversold"] or flags["breakout"] or flags["pullback"]):
            gate = "Entry setup (review)"
    down_label = None
    if flags["downtrend"]:
        if quality is not None and quality >= 0.6:
            down_label = "Downtrend + quality holds (review)"
        elif quality is None:
            down_label = "Downtrend, quality not scored"
        else:
            down_label = "Downtrend, quality weak"

    return {
        "tier": tier, "is_fin": fin,
        "score_value": value, "score_quality": quality,
        "score_timing": timing, "score_risk": risk,
        "composite": composite,
        "score_0_100": None if composite is None else int(round(100 * composite)),
        "coverage_pct": coverage, "gate": gate, "downtrend_label": down_label,
        "not_scored_reason": reason,
        "flag_oversold": flags["oversold"], "flag_breakout": flags["breakout"],
        "flag_pullback": flags["pullback"],
    }


def score_frame(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Return a new frame with SCORE_COLUMNS appended. Input is not mutated."""
    out = df.copy()
    recs = [score_row(r, cfg) for r in (row.to_dict() for _, row in df.iterrows())]
    for col in SCORE_COLUMNS:
        if col in ("rank_in_list", "rank_in_tier"):
            continue
        out[col] = pd.Series([r[col] for r in recs], index=out.index, dtype=object)

    comp = pd.to_numeric(out["composite"], errors="coerce")
    rl = comp.rank(method="min", ascending=False)
    rt = comp.groupby(out["tier"].astype(object)).rank(method="min", ascending=False)
    out["rank_in_list"] = pd.Series(
        [None if pd.isna(v) else int(v) for v in rl], index=out.index, dtype=object)
    out["rank_in_tier"] = pd.Series(
        [None if pd.isna(v) else int(v) for v in rt], index=out.index, dtype=object)
    return out


__all__ = ["score_row", "score_frame", "clip01", "is_financial", "SCORE_COLUMNS",
           "PARTS"]
