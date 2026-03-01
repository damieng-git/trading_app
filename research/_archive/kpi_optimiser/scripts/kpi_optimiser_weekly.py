"""
kpi_optimiser_weekly.py

Optimise KPI parameters (one KPI at a time, weekly timeframe) on the cached 100-stock sample dataset.

Outputs (per KPI):
- data/kpi_optimisation/<kpi_slug>/report.md
- data/kpi_optimisation/<kpi_slug>/report.png

Notes:
- No KPI combos (single KPI only).
- No PDF output (markdown + png only).
- No lookahead: any repainting logic is disabled in optimiser computations.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")  # headless-safe
import matplotlib.pyplot as plt

import sys

REPO_DIR = Path(__file__).resolve().parents[3]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from trading_dashboard.indicators import (
    adx_di,
    atr_stop_loss_finder,
    bollinger_bands,
    crsi,
    dema,
    macd,
    nadaraya_watson_endpoint,
    nadaraya_watson_envelope_luxalgo,
    nadaraya_watson_envelope_luxalgo_std,
    obv_oscillator,
    parabolic_sar,
    rsi_strength_consolidation_zeiierman,
    squeeze_momentum_lazybear,
    stoch_momentum_index,
    supertrend,
    turtle_trade_channels,
    ut_bot_alert,
    wavetrend_lazybear,
)
from trading_dashboard.kpis.catalog import KPI_ORDER
from trading_dashboard.kpis.rules import (
    STATE_BEAR,
    STATE_BULL,
    STATE_NA,
    STATE_NEUTRAL,
    state_from_persistent_signals,
    state_from_regime,
    state_from_signals,
)


SplitName = Literal["IS", "OOS", "ALL"]
SideName = Literal["bull", "bear"]


@dataclass(frozen=True)
class OptimiserConfig:
    horizons_weeks: Tuple[int, ...] = (1, 2, 4, 8)
    is_fraction: float = 0.7
    min_trades: int = 50
    max_candidates_per_param: int = 9
    max_2d_values_per_param: int = 5
    target_horizon_for_heatmap: int = 4
    max_nd_params: int = 5
    max_nd_values_per_param: int = 3
    max_nd_trials: int = 2000
    random_seed: int = 42


def _slug(s: str) -> str:
    s0 = re.sub(r"\s+", "_", (s or "").strip())
    s0 = re.sub(r"[^A-Za-z0-9._-]+", "", s0)
    return s0[:120] if s0 else "kpi"


def _project_dir() -> Path:
    # This script lives in PRIVATE/TRADING/research/kpi_optimiser/scripts/
    return Path(__file__).resolve().parents[3]


def _default_data_dir(project_dir: Path) -> Path:
    return project_dir / "data" / "feature_store" / "enriched" / "sample_100" / "stock_data"


def _default_out_dir(project_dir: Path) -> Path:
    return project_dir / "data" / "research_runs" / "kpi_optimisation"


def _indicator_config_path(project_dir: Path) -> Path:
    return project_dir / "apps" / "dashboard" / "configs" / "indicator_config.json"


def _load_indicator_defaults(path: Path) -> Dict[str, Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for k, v in raw.items():
            if not isinstance(v, dict):
                continue
            params = v.get("params", v)
            if isinstance(params, dict):
                out[str(k)] = dict(params)
        return out
    except Exception:
        return {}


def _read_weekly_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Build/export uses Date index; tolerate common variants.
    for col in ("Date", "Datetime", "date", "datetime"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=False)
            df = df.set_index(col)
            break
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(f"Could not parse datetime index for {path.name}")
    df = df.sort_index()
    return df


def _split_index(idx: pd.DatetimeIndex, is_fraction: float) -> Tuple[pd.Timestamp, pd.Timestamp]:
    if len(idx) < 5:
        # Degenerate; treat all as IS.
        t0 = idx.min()
        return t0, t0
    cut = int(math.floor(len(idx) * float(is_fraction)))
    cut = max(1, min(len(idx) - 1, cut))
    split_ts = idx[cut]
    return split_ts, idx.max()


def _entries_from_state(state: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """
    Convert a KPI state series into entry events (no overlap logic here):
    - bull entry: state becomes BULL this bar (prev != BULL)
    - bear entry: state becomes BEAR this bar (prev != BEAR)
    """
    s = state.astype(float)  # allow NaN
    prev = s.shift(1)
    bull_entry = (s == STATE_BULL) & (prev != STATE_BULL)
    bear_entry = (s == STATE_BEAR) & (prev != STATE_BEAR)
    bull_entry = bull_entry.fillna(False)
    bear_entry = bear_entry.fillna(False)
    return bull_entry, bear_entry


def _forward_returns(close: pd.Series, h: int) -> pd.Series:
    return close.shift(-h) / close - 1.0


def _collect_trade_returns(
    df: pd.DataFrame,
    state: pd.Series,
    horizons: Iterable[int],
    *,
    is_fraction: float,
) -> Dict[Tuple[SplitName, SideName, int], np.ndarray]:
    close = pd.to_numeric(df["Close"], errors="coerce")
    idx = df.index
    split_ts, _ = _split_index(idx, is_fraction)

    bull_entry, bear_entry = _entries_from_state(state)
    out: Dict[Tuple[SplitName, SideName, int], List[float]] = {}
    for h in horizons:
        fwd = _forward_returns(close, int(h))
        for side, entry_mask in (("bull", bull_entry), ("bear", bear_entry)):
            r = fwd[entry_mask].dropna()
            if side == "bear":
                r = -r

            is_mask = r.index < split_ts
            oos_mask = ~is_mask
            out[("IS", side, int(h))] = r.loc[is_mask].to_list()
            out[("OOS", side, int(h))] = r.loc[oos_mask].to_list()
            out[("ALL", side, int(h))] = r.to_list()

    return {k: np.asarray(v, dtype=float) for k, v in out.items()}


def _metrics(returns: np.ndarray) -> Dict[str, float]:
    if returns.size == 0:
        return {"n": 0.0, "win_rate": float("nan"), "mean": float("nan"), "median": float("nan")}
    wins = (returns > 0).sum()
    return {
        "n": float(returns.size),
        "win_rate": float(wins) / float(returns.size),
        "mean": float(np.mean(returns)),
        "median": float(np.median(returns)),
    }


def _param_candidates(name: str, default: Any, *, max_n: int) -> List[Any]:
    """
    Generate sensitivity candidates around the default.
    Keeps this intentionally small and generic; can be refined per-KPI later.
    """
    if isinstance(default, bool):
        return [True, False]

    if isinstance(default, int):
        base = int(default)
        mults = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
        vals = sorted({max(2, int(round(base * m))) for m in mults})
        # Add +-2 as local sensitivity if not already present
        vals = sorted(set(vals + [max(2, base - 2), base, base + 2]))
        return vals[:max_n]

    if isinstance(default, float):
        base = float(default)
        mults = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
        vals = sorted({round(base * m, 6) for m in mults})
        return vals[:max_n]

    if name.lower() in {"smoothing"}:
        return ["RMA", "SMA", "EMA", "WMA"][:max_n]

    if name.lower() in {"ma_type"}:
        return ["SMA", "EMA", "SMMA (RMA)", "WMA", "VWMA"][:max_n]

    return [default]


def _kpi_to_indicator_key(kpi_name: str) -> Optional[str]:
    """
    Map KPI display name (kpi_catalog) -> indicator_config key (indicator_config.json).
    """
    m = {
        "Nadaraya-Watson Smoother": "NW_LuxAlgo",
        "Nadaraya-Watson Envelop": "NWE_Envelope",  # legacy
        "Nadaraya-Watson Envelop (MAE)": "NWE_Envelope_MAE",
        "Nadaraya-Watson Envelop (STD)": "NWE_Envelope_STD",
        "BB 30": "BB",
        "ATR": "ATR",
        "SuperTrend": "SuperTrend",
        "UT Bot Alert": "UT_Bot",
        "TuTCI": "TuTCI",
        "Madrid Ribbon": "MadridRibbon",
        "Donchian Ribbon": "DonchianRibbon",
        "CM_P-SAR": "PSAR",
        "DEMA": "DEMA",
        "WT_LB": "WT_LB",
        "ADX & DI": "ADX_DI",
        "OBVOSC_LB": "OBVOSC",
        "SQZMOM_LB": "SQZMOM_LB",
        "Stoch_MTM": "SMI",
        "CM_Ult_MacD_MFT": "MACD",
        "Volume + MA20": "VOL_MA",
        "cRSI": "cRSI",
        "RSI Strength & Consolidation Zones (Zeiierman)": "RSI Strength & Consolidation Zones (Zeiierman)",
        "RSI Strength & Consolidation Zones (Zeiierman) (breakout)": "RSI Strength & Consolidation Zones (Zeiierman)",
    }
    return m.get(kpi_name)


def _compute_minimal_df_for_kpi(df: pd.DataFrame, kpi_name: str, params: Dict[str, Any]) -> pd.DataFrame:
    """
    Return a minimal dataframe containing Close plus the columns needed for compute_kpi_state_map().
    Enforces no-lookahead by disabling repainting logic for NW Envelope / NW Smoother.
    """
    base = pd.DataFrame(index=df.index)
    base["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    if "High" in df.columns:
        base["High"] = pd.to_numeric(df["High"], errors="coerce")
    if "Low" in df.columns:
        base["Low"] = pd.to_numeric(df["Low"], errors="coerce")
    if "Open" in df.columns:
        base["Open"] = pd.to_numeric(df["Open"], errors="coerce")
    if "Volume" in df.columns:
        base["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")

    # Compute only what's needed for the KPI
    if kpi_name == "WT_LB":
        wt1, wt2, wt_hist = wavetrend_lazybear(base, n1=int(params.get("n1", 10)), n2=int(params.get("n2", 21)))
        base["WT_LB_wt1"] = wt1
        base["WT_LB_wt2"] = wt2
        base["WT_LB_hist"] = wt_hist
        return base

    if kpi_name == "DEMA":
        base["DEMA_9"] = dema(base["Close"], int(params.get("length", 9)))
        return base

    if kpi_name == "CM_P-SAR":
        base["PSAR"] = parabolic_sar(
            base,
            start=float(params.get("start", 0.02)),
            increment=float(params.get("increment", 0.02)),
            maximum=float(params.get("maximum", 0.2)),
        )
        return base

    if kpi_name == "ATR":
        atr_sl = atr_stop_loss_finder(
            base,
            length=int(params.get("length", 14)),
            smoothing=str(params.get("smoothing", "RMA")),
            mult=float(params.get("mult", 1.5)),
        )
        base = base.join(atr_sl)
        return base

    if kpi_name == "BB 30":
        vol = base["Volume"] if "Volume" in base.columns else None
        bb_basis, bb_upper, bb_lower = bollinger_bands(
            base["Close"],
            length=int(params.get("length", 20)),
            mult=float(params.get("mult", 2.0)),
            ma_type=str(params.get("ma_type", "SMA")),
            volume=vol,
        )
        base["BB_basis"] = bb_basis
        base["BB_upper"] = bb_upper
        base["BB_lower"] = bb_lower
        return base

    if kpi_name == "SuperTrend":
        st_line, st_trend, _atr = supertrend(
            base,
            periods=int(params.get("periods", 12)),
            multiplier=float(params.get("multiplier", 3.0)),
            change_atr_method=bool(params.get("change_atr_method", True)),
        )
        base["SuperTrend_line"] = st_line
        base["SuperTrend_trend"] = st_trend
        return base

    if kpi_name == "UT Bot Alert":
        ut = ut_bot_alert(base, a=float(params.get("a", 1.0)), c=int(params.get("c", 10)))
        base = base.join(ut)
        return base

    if kpi_name == "TuTCI":
        tut = turtle_trade_channels(base, length=int(params.get("length", 20)), exit_length=int(params.get("exit_length", 10)))
        base = base.join(tut)
        return base

    if kpi_name in {"Nadaraya-Watson Envelop (MAE)", "Nadaraya-Watson Envelop"}:
        # Enforce no-lookahead by using repaint=False even if config default is repaint=True.
        env = nadaraya_watson_envelope_luxalgo(
            base["Close"],
            bandwidth=float(params.get("bandwidth", 8.0)),
            window=int(params.get("window", 500)),
            mult=float(params.get("mult", 3.0)),
            repaint=False,
        ).rename(
            columns={
                "NWE_env_mid": "NWE_MAE_env_mid",
                "NWE_env_mae": "NWE_MAE_env_mae",
                "NWE_env_upper": "NWE_MAE_env_upper",
                "NWE_env_lower": "NWE_MAE_env_lower",
            }
        )
        base = base.join(env)
        base["NWE_MAE_env_crossunder"] = (base["Close"] < base["NWE_MAE_env_lower"]) & (
            base["Close"].shift(1) >= base["NWE_MAE_env_lower"].shift(1)
        )
        base["NWE_MAE_env_crossover"] = (base["Close"] > base["NWE_MAE_env_upper"]) & (
            base["Close"].shift(1) <= base["NWE_MAE_env_upper"].shift(1)
        )
        return base

    if kpi_name == "Nadaraya-Watson Envelop (STD)":
        env = nadaraya_watson_envelope_luxalgo_std(
            base["Close"],
            bandwidth=float(params.get("bandwidth", 8.0)),
            window=int(params.get("window", 500)),
            mult=float(params.get("mult", 3.0)),
            repaint=False,
        ).rename(
            columns={
                "NWE_env_mid": "NWE_STD_env_mid",
                "NWE_env_std": "NWE_STD_env_std",
                "NWE_env_upper": "NWE_STD_env_upper",
                "NWE_env_lower": "NWE_STD_env_lower",
            }
        )
        base = base.join(env)
        base["NWE_STD_env_crossunder"] = (base["Close"] < base["NWE_STD_env_lower"]) & (
            base["Close"].shift(1) >= base["NWE_STD_env_lower"].shift(1)
        )
        base["NWE_STD_env_crossover"] = (base["Close"] > base["NWE_STD_env_upper"]) & (
            base["Close"].shift(1) <= base["NWE_STD_env_upper"].shift(1)
        )
        return base

    if kpi_name == "Nadaraya-Watson Smoother":
        # Enforce no-lookahead by using endpoint-only series and omitting NW_LuxAlgo_color/value.
        base["NW_LuxAlgo_endpoint"] = nadaraya_watson_endpoint(
            base["Close"], bandwidth=float(params.get("bandwidth", 8.0)), window=int(params.get("window", 500))
        )
        return base

    if kpi_name == "ADX & DI":
        adx, dip, dim = adx_di(base, length=int(params.get("length", 14)))
        base["ADX"] = adx
        base["DI_plus"] = dip
        base["DI_minus"] = dim
        return base

    if kpi_name == "OBVOSC_LB":
        if "Volume" in base.columns:
            obv, obv_osc = obv_oscillator(base, length=int(params.get("length", 20)))
            base["OBV"] = obv
            base["OBV_osc"] = obv_osc
        return base

    if kpi_name == "SQZMOM_LB":
        if "Volume" in base.columns:
            sqz = squeeze_momentum_lazybear(
                base,
                length=int(params.get("length", 20)),
                mult=float(params.get("mult", 2.0)),
                length_kc=int(params.get("length_kc", 20)),
                mult_kc=float(params.get("mult_kc", 1.5)),
                use_true_range=bool(params.get("use_true_range", True)),
            )
            base["SQZ_val"] = sqz["SQZ_val"]
        return base

    if kpi_name == "Stoch_MTM":
        smi, se = stoch_momentum_index(
            base,
            a=int(params.get("a", 10)),
            b=int(params.get("b", 3)),
            c=int(params.get("c", 10)),
            smooth_period=int(params.get("smooth_period", 5)),
        )
        base["SMI"] = smi
        base["SMI_ema"] = se
        return base

    if kpi_name == "CM_Ult_MacD_MFT":
        ml, ms, mh = macd(
            base["Close"],
            fast=int(params.get("fast", 12)),
            slow=int(params.get("slow", 26)),
            signal=int(params.get("signal", 9)),
        )
        base["MACD"] = ml
        base["MACD_signal"] = ms
        base["MACD_hist"] = mh
        return base

    if kpi_name == "Volume + MA20":
        if "Volume" in base.columns:
            length = int(params.get("length", 20))
            base["Vol_MA20"] = base["Volume"].rolling(window=length, min_periods=length).mean()
            base["Vol_gt_MA20"] = (base["Volume"] > base["Vol_MA20"]).fillna(False)
        return base

    if kpi_name == "cRSI":
        out = crsi(
            base["Close"],
            domcycle=int(params.get("domcycle", 20)),
            vibration=int(params.get("vibration", 10)),
            leveling=float(params.get("leveling", 10.0)),
        )
        base = base.join(out)
        return base

    if kpi_name.startswith("RSI Strength & Consolidation Zones (Zeiierman)"):
        zei = rsi_strength_consolidation_zeiierman(
            base,
            rsi_length=int(params.get("rsi_length", 14)),
            dmi_length=int(params.get("dmi_length", 14)),
            adx_smoothing=int(params.get("adx_smoothing", 14)),
            filter_strength=float(params.get("filter_strength", 0.1)),
        )
        base = base.join(zei)
        return base

    # KPI not yet parameterised in optimiser. Return Close-only base (will yield NA state).
    return base


def _state_for_kpi(df: pd.DataFrame, kpi_name: str) -> pd.Series:
    """
    Compute state for ONE KPI only (much faster than compute_kpi_state_map()).
    Must match kpi_catalog.py logic for that KPI.
    """
    idx = df.index
    close = pd.to_numeric(df["Close"], errors="coerce") if "Close" in df.columns else None

    if kpi_name == "Nadaraya-Watson Smoother":
        if "NW_LuxAlgo_color" in df.columns:
            cond = df["NW_LuxAlgo_color"].astype(str).str.strip().str.lower().eq("green")
            avail = df["NW_LuxAlgo_color"].notna()
            return state_from_regime(idx, cond, avail)
        nw = (
            df["NW_LuxAlgo_value"]
            if "NW_LuxAlgo_value" in df.columns
            else (df["NW_LuxAlgo_endpoint"] if "NW_LuxAlgo_endpoint" in df.columns else None)
        )
        if nw is None:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        cond = nw >= nw.shift(1)
        avail = nw.notna() & nw.shift(1).notna()
        return state_from_regime(idx, cond, avail)

    if kpi_name in {"Nadaraya-Watson Envelop (MAE)", "Nadaraya-Watson Envelop"}:
        if close is None or not all(c in df.columns for c in ["NWE_MAE_env_upper", "NWE_MAE_env_lower"]):
            return pd.Series(STATE_NA, index=idx, dtype=int)
        upper = pd.to_numeric(df["NWE_MAE_env_upper"], errors="coerce")
        lower = pd.to_numeric(df["NWE_MAE_env_lower"], errors="coerce")
        avail = close.notna() & upper.notna() & lower.notna()
        bull_sig = (
            df["NWE_MAE_env_crossunder"].fillna(False)
            if "NWE_MAE_env_crossunder" in df.columns
            else ((close < lower) & (close.shift(1) >= lower.shift(1)))
        )
        bear_sig = (
            df["NWE_MAE_env_crossover"].fillna(False)
            if "NWE_MAE_env_crossover" in df.columns
            else ((close > upper) & (close.shift(1) <= upper.shift(1)))
        )
        return state_from_signals(idx, bull_sig, bear_sig, avail)

    if kpi_name == "Nadaraya-Watson Envelop (STD)":
        if close is None or not all(c in df.columns for c in ["NWE_STD_env_upper", "NWE_STD_env_lower"]):
            return pd.Series(STATE_NA, index=idx, dtype=int)
        upper = pd.to_numeric(df["NWE_STD_env_upper"], errors="coerce")
        lower = pd.to_numeric(df["NWE_STD_env_lower"], errors="coerce")
        avail = close.notna() & upper.notna() & lower.notna()
        bull_sig = (
            df["NWE_STD_env_crossunder"].fillna(False)
            if "NWE_STD_env_crossunder" in df.columns
            else ((close < lower) & (close.shift(1) >= lower.shift(1)))
        )
        bear_sig = (
            df["NWE_STD_env_crossover"].fillna(False)
            if "NWE_STD_env_crossover" in df.columns
            else ((close > upper) & (close.shift(1) <= upper.shift(1)))
        )
        return state_from_signals(idx, bull_sig, bear_sig, avail)

    if kpi_name == "BB 30":
        if close is None or not all(c in df.columns for c in ["BB_upper", "BB_lower"]):
            return pd.Series(STATE_NA, index=idx, dtype=int)
        upper = pd.to_numeric(df["BB_upper"], errors="coerce")
        lower = pd.to_numeric(df["BB_lower"], errors="coerce")
        avail = close.notna() & upper.notna() & lower.notna()
        out = pd.Series(STATE_NEUTRAL, index=idx, dtype=int)
        out.loc[((close < lower) & avail).to_numpy(dtype=bool)] = STATE_BULL
        out.loc[((close > upper) & avail).to_numpy(dtype=bool)] = STATE_BEAR
        out.loc[(~avail).to_numpy(dtype=bool)] = STATE_NA
        return out

    if kpi_name == "ATR":
        if close is None or "ATR_long_stop" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        stop = pd.to_numeric(df["ATR_long_stop"], errors="coerce")
        avail = close.notna() & stop.notna()
        return state_from_regime(idx, close >= stop, avail)

    if kpi_name == "SuperTrend":
        if "SuperTrend_trend" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        t = pd.to_numeric(df["SuperTrend_trend"], errors="coerce")
        avail = t.notna()
        return state_from_regime(idx, t == 1, avail)

    if kpi_name == "UT Bot Alert":
        if "UT_pos" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        p = pd.to_numeric(df["UT_pos"], errors="coerce")
        avail = p.notna()
        return state_from_regime(idx, p == 1, avail)

    if kpi_name == "TuTCI":
        if close is None or "TuTCI_trend" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        t = pd.to_numeric(df["TuTCI_trend"], errors="coerce")
        avail = close.notna() & t.notna()
        return state_from_regime(idx, close >= t, avail)

    if kpi_name == "WT_LB":
        if not all(c in df.columns for c in ["WT_LB_wt1", "WT_LB_wt2"]):
            return pd.Series(STATE_NA, index=idx, dtype=int)
        w1 = pd.to_numeric(df["WT_LB_wt1"], errors="coerce")
        w2 = pd.to_numeric(df["WT_LB_wt2"], errors="coerce")
        avail = w1.notna() & w2.notna()
        return state_from_regime(idx, w1 > w2, avail)

    if kpi_name == "ADX & DI":
        if not all(c in df.columns for c in ["DI_plus", "DI_minus"]):
            return pd.Series(STATE_NA, index=idx, dtype=int)
        dip = pd.to_numeric(df["DI_plus"], errors="coerce")
        dim = pd.to_numeric(df["DI_minus"], errors="coerce")
        avail = dip.notna() & dim.notna()
        return state_from_regime(idx, dip > dim, avail)

    if kpi_name == "OBVOSC_LB":
        if "OBV_osc" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        o = pd.to_numeric(df["OBV_osc"], errors="coerce")
        avail = o.notna()
        return state_from_regime(idx, o >= 0, avail)

    if kpi_name == "SQZMOM_LB":
        if "SQZ_val" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        v = pd.to_numeric(df["SQZ_val"], errors="coerce")
        avail = v.notna()
        return state_from_regime(idx, v > 0, avail)

    if kpi_name == "Stoch_MTM":
        if not all(c in df.columns for c in ["SMI", "SMI_ema"]):
            return pd.Series(STATE_NA, index=idx, dtype=int)
        smi = pd.to_numeric(df["SMI"], errors="coerce")
        se = pd.to_numeric(df["SMI_ema"], errors="coerce")
        avail_val = smi.notna() & se.notna()
        avail_sig = avail_val & smi.shift(1).notna() & se.shift(1).notna()

        ob = 40.0
        os_ = -40.0
        long_thr = -35.0
        short_thr = 35.0

        os_end = avail_sig & (smi >= os_) & (smi.shift(1) < os_)
        ob_end = avail_sig & (smi <= ob) & (smi.shift(1) > ob)

        cross_up = avail_sig & (smi > se) & (smi.shift(1) <= se.shift(1))
        cross_dn = avail_sig & (smi < se) & (smi.shift(1) >= se.shift(1))

        long_entry = os_end & cross_up & (smi <= long_thr)
        short_entry = ob_end & cross_dn & (smi >= short_thr)

        return state_from_persistent_signals(idx, long_entry, short_entry, avail_val)

    if kpi_name == "CM_Ult_MacD_MFT":
        if not all(c in df.columns for c in ["MACD", "MACD_signal"]):
            return pd.Series(STATE_NA, index=idx, dtype=int)
        m = pd.to_numeric(df["MACD"], errors="coerce")
        s = pd.to_numeric(df["MACD_signal"], errors="coerce")
        avail = m.notna() & s.notna()
        return state_from_regime(idx, m >= s, avail)

    if kpi_name == "Volume + MA20":
        if "Vol_gt_MA20" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        v = df["Vol_gt_MA20"]
        avail = v.notna()
        return state_from_regime(idx, v.astype(bool), avail)

    if kpi_name == "cRSI":
        if not all(c in df.columns for c in ["cRSI", "cRSI_lb", "cRSI_ub"]):
            return pd.Series(STATE_NA, index=idx, dtype=int)
        cr = pd.to_numeric(df["cRSI"], errors="coerce")
        lb = pd.to_numeric(df["cRSI_lb"], errors="coerce")
        ub = pd.to_numeric(df["cRSI_ub"], errors="coerce")
        avail_val = cr.notna() & lb.notna() & ub.notna()
        avail_sig = avail_val & cr.shift(1).notna() & lb.shift(1).notna() & ub.shift(1).notna()
        bull_sig = avail_sig & (cr > lb) & (cr.shift(1) <= lb.shift(1))
        bear_sig = avail_sig & (cr < ub) & (cr.shift(1) >= ub.shift(1))
        return state_from_persistent_signals(idx, bull_sig, bear_sig, avail_val)

    if kpi_name == "RSI Strength & Consolidation Zones (Zeiierman)":
        if "Zei_bullish" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        zb = df["Zei_bullish"]
        avail_val = zb.notna()
        return state_from_regime(idx, zb.astype(bool), avail_val)

    if kpi_name == "RSI Strength & Consolidation Zones (Zeiierman) (breakout)":
        if "Zei_bullish" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        zb = df["Zei_bullish"]
        avail_val = zb.notna()
        avail_sig = avail_val & zb.shift(1).notna()
        bull_sig = avail_sig & zb.astype(bool) & (~zb.shift(1).astype(bool))
        bear_sig = avail_sig & (~zb.astype(bool)) & (zb.shift(1).astype(bool))
        return state_from_signals(idx, bull_sig, bear_sig, avail_sig)

    if kpi_name == "DEMA":
        if close is None or "DEMA_9" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        d = pd.to_numeric(df["DEMA_9"], errors="coerce")
        avail = close.notna() & d.notna()
        return state_from_regime(idx, close > d, avail)

    if kpi_name == "CM_P-SAR":
        if close is None or "PSAR" not in df.columns:
            return pd.Series(STATE_NA, index=idx, dtype=int)
        p = pd.to_numeric(df["PSAR"], errors="coerce")
        avail = close.notna() & p.notna()
        return state_from_regime(idx, p < close, avail)

    return pd.Series(STATE_NA, index=idx, dtype=int)


def _evaluate_kpi(
    kpi_name: str,
    symbol_dfs: Dict[str, pd.DataFrame],
    params: Dict[str, Any],
    cfg: OptimiserConfig,
) -> Dict[str, Any]:
    """
    Evaluate KPI across all symbols; returns per horizon metrics for IS/OOS/ALL and bull/bear.
    """
    all_returns: Dict[Tuple[SplitName, SideName, int], List[float]] = {}
    for sym, df in symbol_dfs.items():
        mini = _compute_minimal_df_for_kpi(df, kpi_name, params)
        state = _state_for_kpi(mini, kpi_name)
        # Drop NA bars
        state = state.where(state != STATE_NA)
        trades = _collect_trade_returns(mini, state, cfg.horizons_weeks, is_fraction=cfg.is_fraction)
        for k, arr in trades.items():
            all_returns.setdefault(k, []).extend(arr.tolist())

    metrics: Dict[str, Any] = {"kpi": kpi_name, "params": dict(params), "by": {}}
    for split in ("IS", "OOS", "ALL"):
        for side in ("bull", "bear"):
            for h in cfg.horizons_weeks:
                arr = np.asarray(all_returns.get((split, side, int(h)), []), dtype=float)
                m = _metrics(arr)
                metrics["by"].setdefault(split, {}).setdefault(side, {})[str(h)] = m
    return metrics


def _score_trial(metrics: Dict[str, Any], *, horizon: int, side: SideName, min_trades: int) -> float:
    """
    Primary objective: OOS win rate at selected horizon + side.
    Secondary: OOS mean return (small weight).
    """
    m = metrics["by"]["OOS"][side].get(str(horizon), {})
    wr = float(m.get("win_rate", float("nan")))
    mu = float(m.get("mean", float("nan")))
    n = float(m.get("n", 0.0))
    if not np.isfinite(wr) or n < float(min_trades):
        return -1e9
    return wr + 0.05 * (mu if np.isfinite(mu) else 0.0)


def _run_sweeps_for_kpi(
    kpi_name: str,
    symbol_dfs: Dict[str, pd.DataFrame],
    default_params: Dict[str, Any],
    cfg: OptimiserConfig,
) -> Dict[str, Any]:
    # Baseline
    baseline = _evaluate_kpi(kpi_name, symbol_dfs, default_params, cfg)

    # If no parameters, nothing to sweep.
    if not default_params:
        return {"kpi": kpi_name, "baseline": baseline, "sweeps_1d": {}, "sweep_2d": None}

    # 1D sweeps
    sweeps_1d: Dict[str, Any] = {}
    for pname, p0 in default_params.items():
        candidates = _param_candidates(pname, p0, max_n=cfg.max_candidates_per_param)
        rows: List[Dict[str, Any]] = []
        for v in candidates:
            trial_params = dict(default_params)
            trial_params[pname] = v
            met = _evaluate_kpi(kpi_name, symbol_dfs, trial_params, cfg)
            rows.append({"value": v, "metrics": met})
        sweeps_1d[pname] = {"default": p0, "candidates": candidates, "trials": rows}

    # 2D sweep: choose top 2 params by best achievable OOS score at target horizon (bull side)
    ranked: List[Tuple[float, str]] = []
    for pname, sweep in sweeps_1d.items():
        best = max(
            (
                _score_trial(
                    t["metrics"], horizon=cfg.target_horizon_for_heatmap, side="bull", min_trades=cfg.min_trades
                )
                for t in sweep["trials"]
            ),
            default=-1e9,
        )
        ranked.append((best, pname))
    ranked.sort(reverse=True)
    top2 = [p for _, p in ranked[:2]]

    sweep_2d = None
    if len(top2) == 2:
        p1, p2 = top2
        v1 = _param_candidates(p1, default_params[p1], max_n=cfg.max_2d_values_per_param)
        v2 = _param_candidates(p2, default_params[p2], max_n=cfg.max_2d_values_per_param)
        grid: List[Dict[str, Any]] = []
        for a in v1:
            for b in v2:
                trial_params = dict(default_params)
                trial_params[p1] = a
                trial_params[p2] = b
                met = _evaluate_kpi(kpi_name, symbol_dfs, trial_params, cfg)
                grid.append({"p1": a, "p2": b, "metrics": met})
        sweep_2d = {"params": [p1, p2], "values": {p1: v1, p2: v2}, "grid": grid}

    # ND sweep (up to 5 params): full grid if small, else random sample.
    top_params = [p for _, p in ranked[: cfg.max_nd_params]]
    sweep_nd = None
    if len(top_params) >= 3:
        values: Dict[str, List[Any]] = {
            p: _param_candidates(p, default_params[p], max_n=cfg.max_nd_values_per_param) for p in top_params
        }
        sizes = [len(values[p]) for p in top_params]
        total = int(np.prod(sizes)) if sizes else 0

        rng = np.random.default_rng(int(cfg.random_seed))
        trials: List[Dict[str, Any]] = []

        if total > 0 and total <= int(cfg.max_nd_trials):
            # Full cartesian grid
            def _recurse(i: int, cur: Dict[str, Any]) -> None:
                if i >= len(top_params):
                    met = _evaluate_kpi(kpi_name, symbol_dfs, cur, cfg)
                    trials.append({"params": dict(cur), "metrics": met})
                    return
                pn = top_params[i]
                for v in values[pn]:
                    cur[pn] = v
                    _recurse(i + 1, cur)

            _recurse(0, dict(default_params))
        elif total > 0:
            # Random sample without replacement over the cartesian product index space.
            k = int(min(cfg.max_nd_trials, total))
            flat_idx = rng.choice(total, size=k, replace=False)

            # Mixed radix decode
            rad = sizes
            mult = [1]
            for s in rad[:-1]:
                mult.append(mult[-1] * s)

            for fi in flat_idx.tolist():
                cur = dict(default_params)
                x = int(fi)
                for j, pn in enumerate(top_params):
                    base = mult[j]
                    digit = (x // base) % rad[j]
                    cur[pn] = values[pn][digit]
                met = _evaluate_kpi(kpi_name, symbol_dfs, cur, cfg)
                trials.append({"params": cur, "metrics": met})

        sweep_nd = {
            "params": top_params,
            "values": values,
            "total_combos": total,
            "n_trials": len(trials),
            "strategy": "full_grid" if (total > 0 and total <= int(cfg.max_nd_trials)) else "random_sample",
            "trials": trials,
        }

    return {"kpi": kpi_name, "baseline": baseline, "sweeps_1d": sweeps_1d, "sweep_2d": sweep_2d, "sweep_nd": sweep_nd}


def _best_trial_for_param(sweep: Dict[str, Any], cfg: OptimiserConfig, *, side: SideName) -> Dict[str, Any]:
    trials = sweep.get("trials", [])
    best = None
    best_score = -1e9
    for t in trials:
        score = _score_trial(t["metrics"], horizon=cfg.target_horizon_for_heatmap, side=side, min_trades=cfg.min_trades)
        if score > best_score:
            best_score = score
            best = t
    return best or {"value": None, "metrics": None}


def _render_report_png(
    out_png: Path,
    kpi_name: str,
    result: Dict[str, Any],
    cfg: OptimiserConfig,
) -> None:
    sweeps_1d = result.get("sweeps_1d", {})
    sweep_nd = result.get("sweep_nd")

    params_all = list(sweeps_1d.keys())
    max_params_to_show = 6
    show_params = params_all[:max_params_to_show]
    n_params = len(show_params)

    # Layout: title + per-parameter row (line + heatmap) + ND sensitivity table
    height = 2.6 + (3.2 * max(n_params, 1)) + 2.4
    fig = plt.figure(figsize=(18, height), dpi=160)
    gs = fig.add_gridspec(
        nrows=2 + max(n_params, 1),
        ncols=2,
        height_ratios=[0.9] + ([1.0] * max(n_params, 1)) + [0.95],
        width_ratios=[1.25, 1.0],
    )

    ax_title = fig.add_subplot(gs[0, :])
    ax_title.axis("off")
    ax_title.text(
        0.0,
        0.82,
        f"KPI optimisation (weekly) — {kpi_name}",
        fontsize=18,
        fontweight="bold",
        ha="left",
        va="center",
    )
    ax_title.text(
        0.0,
        0.52,
        f"Horizons (weeks): {', '.join(map(str, cfg.horizons_weeks))} | IS/OOS split: {int(cfg.is_fraction*100)}% / {int((1-cfg.is_fraction)*100)}% | No-lookahead: enabled",
        fontsize=10,
        ha="left",
        va="center",
    )
    ax_title.text(
        0.0,
        0.23,
        f"Per-parameter 1D sensitivity (bull entries). Min trades for ranking: {cfg.min_trades} (OOS, per horizon).",
        fontsize=10,
        ha="left",
        va="center",
    )

    colors = {1: "#1f77b4", 2: "#ff7f0e", 4: "#2ca02c", 8: "#d62728"}
    heat_axes: List[plt.Axes] = []
    last_im = None

    if n_params == 0:
        ax = fig.add_subplot(gs[1, :])
        ax.axis("off")
        ax.text(0.0, 0.5, "No tunable parameters detected for this KPI.", fontsize=12, ha="left", va="center")
    else:
        for i, pname in enumerate(show_params):
            sweep = sweeps_1d[pname]
            candidates = sweep["candidates"]
            trials = sweep["trials"]

            ax_line = fig.add_subplot(gs[1 + i, 0])
            ax_hm = fig.add_subplot(gs[1 + i, 1])
            heat_axes.append(ax_hm)

            # Line chart: OOS win rate vs value (bull), one line per horizon
            ax_line.set_title(f"{pname} — OOS win rate (bull entries)", fontsize=11, loc="left")
            xs = np.arange(len(candidates))
            for h in cfg.horizons_weeks:
                ys = []
                for t in trials:
                    m = t["metrics"]["by"]["OOS"]["bull"][str(h)]
                    ys.append(float(m.get("win_rate", np.nan)))
                ax_line.plot(xs, ys, marker="o", linewidth=1.2, color=colors.get(h, "black"), alpha=0.85, label=f"H{h}")

            ax_line.set_ylim(0.0, 1.0)
            ax_line.set_ylabel("Win rate")
            ax_line.set_xticks(xs)
            ax_line.set_xticklabels([str(v) for v in candidates], rotation=0)
            ax_line.grid(True, axis="y", alpha=0.25)
            ax_line.legend(loc="upper right", fontsize=8, frameon=True, ncol=min(4, len(cfg.horizons_weeks)))

            # Heatmap: horizons (rows) × candidate values (cols) of OOS win rate (bull)
            z = np.full((len(cfg.horizons_weeks), len(candidates)), np.nan, dtype=float)
            for hi, h in enumerate(cfg.horizons_weeks):
                for vi, t in enumerate(trials):
                    m = t["metrics"]["by"]["OOS"]["bull"][str(h)]
                    z[hi, vi] = float(m.get("win_rate", np.nan))
            last_im = ax_hm.imshow(z, vmin=0.0, vmax=1.0, aspect="auto", cmap="viridis")
            ax_hm.set_title(f"{pname} — heatmap (OOS win rate)", fontsize=11, loc="left")
            ax_hm.set_yticks(np.arange(len(cfg.horizons_weeks)))
            ax_hm.set_yticklabels([f"H{h}" for h in cfg.horizons_weeks])
            ax_hm.set_xticks(np.arange(len(candidates)))
            ax_hm.set_xticklabels([str(v) for v in candidates], rotation=45, ha="right")

        if heat_axes and last_im is not None:
            fig.colorbar(last_im, ax=heat_axes, fraction=0.015, pad=0.01).set_label("OOS win rate")

    # ND sensitivity table (bottom, full width)
    ax_tbl = fig.add_subplot(gs[-1, :])
    ax_tbl.axis("off")
    ax_tbl.set_title(
        f"Multi-parameter sensitivity (N-D) — top sets by OOS win rate (bull, H{cfg.target_horizon_for_heatmap})",
        fontsize=12,
        loc="left",
    )
    if sweep_nd and sweep_nd.get("trials"):
        rows: List[Tuple[float, float, float, Dict[str, Any]]] = []
        for t in sweep_nd["trials"]:
            m = t["metrics"]["by"]["OOS"]["bull"][str(cfg.target_horizon_for_heatmap)]
            n = float(m.get("n", 0.0))
            wr = float(m.get("win_rate", float("nan")))
            mu = float(m.get("mean", float("nan")))
            if n < float(cfg.min_trades) or not np.isfinite(wr):
                continue
            rows.append((wr, n, mu, t["params"]))
        rows.sort(key=lambda x: (x[0], x[2]), reverse=True)
        top = rows[:10]
        if not top:
            ax_tbl.text(0.0, 0.5, "No N-D trials met the minimum trades threshold.", fontsize=10, ha="left", va="center")
        else:
            cell_text = [
                [str(i + 1), str(int(n)), f"{wr:.3f}", f"{mu:.4f}", json.dumps(p, sort_keys=True)]
                for i, (wr, n, mu, p) in enumerate(top)
            ]
            table = ax_tbl.table(
                cellText=cell_text,
                colLabels=["Rank", "Trades", "Win rate", "Mean", "Params"],
                loc="upper left",
                cellLoc="left",
                colLoc="left",
                colWidths=[0.06, 0.08, 0.10, 0.10, 0.66],
            )
            table.auto_set_font_size(False)
            table.set_fontsize(8)
            table.scale(1.0, 1.2)
    else:
        ax_tbl.text(0.0, 0.5, "N-D sweep not available for this KPI.", fontsize=10, ha="left", va="center")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    # `Axes.table` can conflict with tight_layout; use a manual layout to keep output stable.
    fig.subplots_adjust(top=0.97, bottom=0.03, left=0.05, right=0.98, hspace=0.65, wspace=0.18)
    fig.savefig(out_png, bbox_inches="tight")
    plt.close(fig)


def _write_report_md(out_md: Path, kpi_name: str, result: Dict[str, Any], png_name: str, cfg: OptimiserConfig) -> None:
    baseline = result["baseline"]
    sweeps_1d = result.get("sweeps_1d", {})
    sweep_2d = result.get("sweep_2d")
    sweep_nd = result.get("sweep_nd")

    def _best_from_trials(trials: List[Dict[str, Any]], *, side: SideName, h: int) -> Tuple[Any, float, float]:
        """
        Return (value, win_rate, n) for the best OOS win rate among trials.
        Enforces cfg.min_trades; if none qualify returns (None, nan, 0).
        """
        best_v = None
        best_wr = float("nan")
        best_n = 0.0
        for t in trials:
            m = t["metrics"]["by"]["OOS"][side][str(int(h))]
            n = float(m.get("n", 0.0))
            wr = float(m.get("win_rate", float("nan")))
            if n < float(cfg.min_trades) or not np.isfinite(wr):
                continue
            if (best_v is None) or (wr > best_wr):
                best_v = t.get("value")
                best_wr = wr
                best_n = n
        return best_v, best_wr, best_n

    lines: List[str] = []
    lines.append(f"## KPI optimisation — {kpi_name}")
    lines.append("")
    lines.append(f"- **Timeframe**: weekly (`1W`)")
    lines.append(f"- **Horizons**: {', '.join(f'H{h}' for h in cfg.horizons_weeks)} weeks forward returns")
    lines.append(f"- **IS/OOS split**: {int(cfg.is_fraction*100)}% / {int((1-cfg.is_fraction)*100)}% by time index")
    lines.append(f"- **No-lookahead**: enabled (repainting disabled in optimiser computations)")
    lines.append(f"- **Minimum trades for ranking**: {cfg.min_trades} (OOS, per side+horizon)")
    lines.append("")
    lines.append(f"![report]({png_name})")
    lines.append("")

    # Baseline table (OOS, bull+bear)
    lines.append("### Baseline performance (OOS)")
    lines.append("")
    lines.append("| Side | Horizon | Trades | Win rate | Mean | Median |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for side in ("bull", "bear"):
        for h in cfg.horizons_weeks:
            m = baseline["by"]["OOS"][side][str(h)]
            lines.append(
                f"| {side} | H{h} | {int(m['n']) if np.isfinite(m['n']) else 0} | {m['win_rate']:.3f} | {m['mean']:.4f} | {m['median']:.4f} |"
            )
    lines.append("")

    if sweeps_1d:
        lines.append("### Best 1D settings (OOS win rate, min trades enforced)")
        lines.append("")
        for side in ("bull", "bear"):
            lines.append(f"#### Best per parameter — `{side}`")
            lines.append("")
            lines.append("| Parameter | Horizon | Best value | Trades | Win rate |")
            lines.append("|---|---:|---:|---:|---:|")
            for pname, sweep in sweeps_1d.items():
                for h in cfg.horizons_weeks:
                    v, wr, n = _best_from_trials(sweep.get("trials", []), side=side, h=h)
                    if v is None:
                        lines.append(f"| `{pname}` | H{h} | _n/a_ | 0 | _n/a_ |")
                    else:
                        lines.append(f"| `{pname}` | H{h} | `{v}` | {int(n)} | {wr:.3f} |")
            lines.append("")

    lines.append("### Parameter defaults")
    lines.append("")
    params = baseline.get("params", {})
    if params:
        lines.append("```json")
        lines.append(json.dumps(params, indent=2, sort_keys=True))
        lines.append("```")
    else:
        lines.append("_No tunable parameters detected for this KPI in `indicator_config.json`._")
    lines.append("")

    if sweeps_1d:
        lines.append("### 1D sensitivity (one parameter at a time)")
        lines.append("")
        for pname, sweep in sweeps_1d.items():
            lines.append(f"#### `{pname}`")
            lines.append("")
            lines.append(f"- Default: `{sweep['default']}`")
            lines.append(f"- Candidates: `{sweep['candidates']}`")
            lines.append("")
        lines.append("")

    if sweep_2d:
        p1, p2 = sweep_2d["params"]
        lines.append("### 2D sensitivity (two parameters)")
        lines.append("")
        lines.append(f"- Parameters: `{p1}` × `{p2}`")
        lines.append(f"- Values: `{p1}={sweep_2d['values'][p1]}`, `{p2}={sweep_2d['values'][p2]}`")
        lines.append("")

    if sweep_nd:
        lines.append("### Multi-parameter sensitivity (up to 5D)")
        lines.append("")
        lines.append(
            f"- Parameters: `{', '.join(sweep_nd['params'])}`"
            f" | Total combos: {sweep_nd.get('total_combos', 0)}"
            f" | Trials run: {sweep_nd.get('n_trials', 0)}"
            f" | Strategy: `{sweep_nd.get('strategy', '')}`"
        )
        lines.append("")
        lines.append("#### Top parameter sets (OOS win rate, bull, H4)")
        lines.append("")
        lines.append("| Rank | Trades | Win rate | Mean | Params |")
        lines.append("|---:|---:|---:|---:|---|")
        rows: List[Tuple[float, float, float, Dict[str, Any]]] = []
        for t in sweep_nd.get("trials", []):
            m = t["metrics"]["by"]["OOS"]["bull"][str(cfg.target_horizon_for_heatmap)]
            n = float(m.get("n", 0.0))
            wr = float(m.get("win_rate", float("nan")))
            mu = float(m.get("mean", float("nan")))
            if n < float(cfg.min_trades) or not np.isfinite(wr):
                continue
            rows.append((wr, n, mu, t["params"]))
        rows.sort(key=lambda x: (x[0], x[2]), reverse=True)
        for i, (wr, n, mu, p) in enumerate(rows[:20], start=1):
            lines.append(f"| {i} | {int(n)} | {wr:.3f} | {mu:.4f} | `{json.dumps(p, sort_keys=True)}` |")
        if not rows:
            lines.append("| 1 | 0 | _n/a_ | _n/a_ | _n/a_ |")
        lines.append("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    project_dir = _project_dir()
    parser = argparse.ArgumentParser(description="Weekly KPI optimiser (one KPI at a time; markdown + png reports).")
    parser.add_argument("--data_dir", type=str, default=str(_default_data_dir(project_dir)))
    parser.add_argument("--out_dir", type=str, default=str(_default_out_dir(project_dir)))
    parser.add_argument("--kpi", type=str, default="", help="If set, run only this KPI name (must match kpi_catalog).")
    parser.add_argument("--limit_symbols", type=int, default=0, help="Optional: limit number of symbols for smoke tests.")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    ind_cfg_path = _indicator_config_path(project_dir)
    defaults = _load_indicator_defaults(ind_cfg_path)

    csvs = sorted(data_dir.glob("*_1W.csv"))
    if not csvs:
        raise SystemExit(f"No weekly CSVs found under: {data_dir}")

    if args.limit_symbols and args.limit_symbols > 0:
        csvs = csvs[: int(args.limit_symbols)]

    symbol_dfs: Dict[str, pd.DataFrame] = {}
    for p in csvs:
        sym = p.name.replace("_1W.csv", "")
        symbol_dfs[sym] = _read_weekly_csv(p)

    cfg = OptimiserConfig()

    kpis = list(KPI_ORDER)
    if args.kpi:
        kpis = [args.kpi]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config_used.json").write_text(
        json.dumps(
            {
                "data_dir": str(data_dir),
                "n_symbols": len(symbol_dfs),
                "horizons_weeks": cfg.horizons_weeks,
                "is_fraction": cfg.is_fraction,
                "indicator_config_json": str(ind_cfg_path),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    for kpi in kpis:
        ind_key = _kpi_to_indicator_key(kpi)
        default_params = defaults.get(ind_key, {}) if ind_key else {}

        result = _run_sweeps_for_kpi(kpi, symbol_dfs, default_params, cfg)
        kpi_dir = out_dir / _slug(kpi)
        png_path = kpi_dir / "report.png"
        md_path = kpi_dir / "report.md"

        _render_report_png(png_path, kpi, result, cfg)
        _write_report_md(md_path, kpi, result, png_name="report.png", cfg=cfg)

        # Save raw results for later programmatic use (not required, but useful).
        (kpi_dir / "results.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

