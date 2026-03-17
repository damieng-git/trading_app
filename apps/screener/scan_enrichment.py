"""Strategy-aware lean enrichment for the parallel stock scanner.

Computes only the indicator columns required by a given strategy's C3/C4 combos.
NW Smoother is approximated by EMA(20) for scan speed (full NW runs ~23s warmup
vs 503 bars; EMA(20) is mathematically close: 95% of NW weight sits in last 20
bars with bandwidth=8).

Survivors from C3 onset detection are later re-enriched by the full dashboard
pipeline which uses the real NW Smoother.

KPI_SCAN_MIN_BARS: minimum OHLCV bars required before each KPI is meaningful.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-KPI minimum warmup bars
# ---------------------------------------------------------------------------
KPI_SCAN_MIN_BARS: dict[str, int] = {
    "Nadaraya-Watson Smoother": 22,   # EMA(20) proxy: 20 + 2 buffer
    "Madrid Ribbon": 235,
    "Volume + MA20": 22,
    "GK Trend Ribbon": 222,
    "cRSI": 202,
    "DEMA": 20,
    "Stoch_MTM": 27,
    "ADX & DI": 30,
    "WT_LB": 55,
    "SQZMOM_LB": 25,
    # Stoof (Band Light) KPIs
    "MACD_BL": 50,     # EMA(23) slow → ~50 bars for stability
}


def min_bars_for_combo(kpis: list[str]) -> int:
    """Return minimum bars needed so all KPIs in a combo are warmed up."""
    return max((KPI_SCAN_MIN_BARS.get(k, 30) for k in kpis), default=30)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_scan_indicators(
    df: pd.DataFrame,
    kpis: list[str],
    indicator_config_path: Any = None,
) -> pd.DataFrame:
    """Compute only the indicator columns needed for the given KPI list.

    Returns a new DataFrame with OHLCV + indicator columns.
    Columns added per KPI:
      Nadaraya-Watson Smoother → NW_LuxAlgo_color  (via EMA(20) proxy)
      Madrid Ribbon            → MadridRibbon_state (from madrid_ma_ribbon_state)
      Volume + MA20            → Vol_gt_MA20, Vol_MA20
      GK Trend Ribbon          → GK_trend
      cRSI                     → cRSI_signal (or similar from crsi())
      DEMA                     → DEMA_9
      Stoch_MTM                → SMI, SMI_ema
      ADX & DI                 → ADX, DI_plus, DI_minus
      WT_LB                    → WT_LB_wt1, WT_LB_wt2
      SQZMOM_LB                → SQZ_val
    """
    from pathlib import Path

    cfg = Path(indicator_config_path) if indicator_config_path else None
    kpi_set = set(kpis)

    base_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    out = df[base_cols].copy()

    def _load_params(key: str) -> dict:
        if cfg is None or not cfg.exists():
            return {}
        try:
            import json
            raw = json.loads(cfg.read_text(encoding="utf-8"))
            node = raw.get(key, {})
            params = node.get("params", node)
            return params if isinstance(params, dict) else {}
        except Exception:
            return {}

    # ── NW Smoother proxy (EMA 20) ───────────────────────────────────────────
    # Catalog else-branch uses NW_LuxAlgo_value: bull when value >= value.shift(1)
    if "Nadaraya-Watson Smoother" in kpi_set:
        out["NW_LuxAlgo_value"] = _ema(out["Close"], span=20)

    # ── Madrid Ribbon ────────────────────────────────────────────────────────
    if "Madrid Ribbon" in kpi_set:
        from trading_dashboard.indicators import madrid_ma_ribbon_state
        p = _load_params("MadridRibbon")
        mm = madrid_ma_ribbon_state(out, exponential=bool(p.get("exponential", True)))
        out = out.join(mm)

    # ── Volume + MA20 ────────────────────────────────────────────────────────
    if "Volume + MA20" in kpi_set and "Volume" in out.columns:
        p = _load_params("VOL_MA")
        vol_len = int(p.get("length", 20))
        out["Vol_MA20"] = out["Volume"].rolling(window=vol_len, min_periods=vol_len).mean()
        out["Vol_gt_MA20"] = (out["Volume"] > out["Vol_MA20"]).fillna(False)

    # ── GK Trend Ribbon ──────────────────────────────────────────────────────
    if "GK Trend Ribbon" in kpi_set:
        from trading_dashboard.indicators import gk_trend_ribbon
        p = _load_params("GK_Trend")
        _, _, _, gk_trend = gk_trend_ribbon(
            out,
            length=int(p.get("length", 200)),
            band_mult=float(p.get("band_mult", 2.0)),
            atr_length=int(p.get("atr_length", 21)),
            confirm_bars=int(p.get("confirm_bars", 2)),
        )
        out["GK_trend"] = gk_trend

    # ── cRSI ─────────────────────────────────────────────────────────────────
    if "cRSI" in kpi_set:
        from trading_dashboard.indicators import crsi
        p = _load_params("cRSI")
        cr = crsi(
            out["Close"],
            domcycle=int(p.get("domcycle", 20)),
            vibration=int(p.get("vibration", 10)),
            leveling=float(p.get("leveling", 10.0)),
        )
        out = out.join(cr)

    # ── DEMA ─────────────────────────────────────────────────────────────────
    if "DEMA" in kpi_set:
        from trading_dashboard.indicators import dema as _dema
        p = _load_params("DEMA")
        out["DEMA_9"] = _dema(out["Close"], int(p.get("length", 9)))

    # ── Stoch_MTM ────────────────────────────────────────────────────────────
    if "Stoch_MTM" in kpi_set:
        from trading_dashboard.indicators import stoch_momentum_index as _smi
        p = _load_params("SMI")
        smi_val, smi_ema = _smi(
            out,
            a=int(p.get("a", 10)),
            b=int(p.get("b", 3)),
            c=int(p.get("c", 10)),
            smooth_period=int(p.get("smooth_period", 5)),
        )
        out["SMI"] = smi_val
        out["SMI_ema"] = smi_ema

    # ── ADX & DI ─────────────────────────────────────────────────────────────
    if "ADX & DI" in kpi_set:
        from trading_dashboard.indicators import adx_di as _adx_di
        p = _load_params("ADX_DI")
        adx_val, di_p, di_m = _adx_di(out, length=int(p.get("length", 14)))
        out["ADX"] = adx_val
        out["DI_plus"] = di_p
        out["DI_minus"] = di_m

    # ── WT_LB ────────────────────────────────────────────────────────────────
    if "WT_LB" in kpi_set:
        from trading_dashboard.indicators import wavetrend_lazybear as _wt
        p = _load_params("WT_LB")
        wt1, wt2, wt_hist = _wt(out, n1=int(p.get("n1", 10)), n2=int(p.get("n2", 21)))
        out["WT_LB_wt1"] = wt1
        out["WT_LB_wt2"] = wt2
        out["WT_LB_hist"] = wt_hist

    # ── SQZMOM_LB ────────────────────────────────────────────────────────────
    if "SQZMOM_LB" in kpi_set and "Volume" in out.columns:
        from trading_dashboard.indicators import squeeze_momentum_lazybear as _sqz
        p = _load_params("SQZMOM_LB")
        sqz = _sqz(
            out,
            length=int(p.get("length", 20)),
            mult=float(p.get("mult", 2.0)),
            length_kc=int(p.get("length_kc", 20)),
            mult_kc=float(p.get("mult_kc", 1.5)),
            use_true_range=bool(p.get("use_true_range", True)),
        )
        out["SQZ_val"] = sqz["SQZ_val"]

    # ── MACD_BL (Band Light — required KPI for stoof) ────────────────────────
    if "MACD_BL" in kpi_set:
        from trading_dashboard.indicators import macd as _macd
        p = _load_params("MACD_BL")
        macd_line, signal_line, hist = _macd(
            out["Close"],
            fast=int(p.get("fast", 15)),
            slow=int(p.get("slow", 23)),
            signal=int(p.get("signal", 5)),
            signal_ma="EMA",
        )
        out["MACD_BL"] = macd_line
        out["MACD_BL_signal"] = signal_line
        out["MACD_BL_hist"] = hist

    # ── Quality gate helpers ─────────────────────────────────────────────────
    out["SMA20"] = out["Close"].rolling(window=20, min_periods=20).mean()
    out["SMA200"] = out["Close"].rolling(window=200, min_periods=200).mean()

    return out


def compute_scan_kpi_states(
    df: pd.DataFrame,
    kpis: list[str],
    pols: list[int],
) -> pd.Series:
    """Return a boolean Series: True where ALL KPIs in (kpis, pols) are in expected state."""
    from trading_dashboard.kpis.catalog import compute_kpi_state_map

    state_map = compute_kpi_state_map(df)
    result = pd.Series(True, index=df.index)

    for kpi, pol in zip(kpis, pols):
        states = state_map.get(kpi)
        if states is None:
            logger.debug("KPI '%s' not in state_map — treating as no signal", kpi)
            result[:] = False
            break
        if pol == 1:
            result &= states == 1
        elif pol == -1:
            result &= states == -1
        # pol 0 = always passes

    return result


def check_quality_gates_raw(
    df: pd.DataFrame,
    scan_filters: dict,
) -> bool:
    """Check quality gates on raw OHLCV without pre-computed indicator columns.

    Computes SMA20/SMA200 and Vol_MA20 inline so this can be called BEFORE
    lean enrichment, allowing cheap pre-filtering of the universe.
    """
    if not scan_filters:
        return True

    close = df["Close"]
    last = df.iloc[-1]

    if scan_filters.get("sma20_gt_sma200"):
        sma20 = close.rolling(20, min_periods=20).mean().iloc[-1]
        sma200 = close.rolling(200, min_periods=200).mean().iloc[-1]
        if not (pd.notna(sma20) and pd.notna(sma200) and sma20 > sma200):
            return False

    if scan_filters.get("volume_spike") and "Volume" in df.columns:
        vol = last.get("Volume", np.nan)
        vol_ma = df["Volume"].rolling(20, min_periods=20).mean().iloc[-1]
        if not (pd.notna(vol) and pd.notna(vol_ma) and vol_ma > 0 and vol > 1.5 * vol_ma):
            return False

    if scan_filters.get("sr_break"):
        if len(df) >= 21 and "High" in df.columns and "Close" in df.columns:
            prior_high = df["High"].iloc[-21:-1].max()
            close_last = last.get("Close", np.nan)
            if not (pd.notna(close_last) and pd.notna(prior_high) and close_last > prior_high):
                return False
        else:
            return False

    return True


def check_quality_gates(
    df: pd.DataFrame,
    scan_filters: dict,
) -> bool:
    """Return True if the latest bar passes all enabled quality gates.

    Gates:
      sma20_gt_sma200 — SMA20 > SMA200 on latest bar
      volume_spike    — latest volume > 1.5× Vol_MA20
      sr_break        — close > highest high of prior 20 bars (simplified proxy)
    """
    if not scan_filters:
        return True

    last = df.iloc[-1]

    if scan_filters.get("sma20_gt_sma200"):
        sma20 = last.get("SMA20", np.nan)
        sma200 = last.get("SMA200", np.nan)
        if not (pd.notna(sma20) and pd.notna(sma200) and sma20 > sma200):
            return False

    if scan_filters.get("volume_spike"):
        vol = last.get("Volume", np.nan)
        vol_ma = last.get("Vol_MA20", np.nan)
        if not (pd.notna(vol) and pd.notna(vol_ma) and vol_ma > 0 and vol > 1.5 * vol_ma):
            return False

    if scan_filters.get("sr_break"):
        if len(df) >= 21 and "High" in df.columns and "Close" in df.columns:
            prior_high = df["High"].iloc[-21:-1].max()
            close = last.get("Close", np.nan)
            if not (pd.notna(close) and pd.notna(prior_high) and close > prior_high):
                return False
        else:
            return False

    return True


def detect_c3_onset(
    all_states: pd.Series,
    lookback: int = 3,
) -> bool:
    """Return True if C3 combo just turned on within the last `lookback` bars.

    'Just turned on' means: the combo is True on the latest bar AND was False
    at some point in the prior (lookback-1) bars.
    """
    if len(all_states) < lookback:
        return bool(all_states.iloc[-1]) if len(all_states) else False
    tail = all_states.iloc[-lookback:]
    return bool(tail.iloc[-1]) and not bool(tail.iloc[:-1].all())
