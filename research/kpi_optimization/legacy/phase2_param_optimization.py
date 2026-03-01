"""
Phase 2 — Parameter Optimization (Weekly, Long-Only)

For each KPI with tunable parameters:
1. Time-split: IS = first 70% of bars, OOS = last 30%
2. Random-search parameter combos on IS data
3. Evaluate top-5 IS combos on OOS
4. Select the combo with the smallest IS→OOS degradation

Outputs: optimized indicator_config.json + report.
"""

from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from tf_config import parse_timeframe_arg, output_dir_for, ENRICHED_DIR
from trading_dashboard.kpis.catalog import compute_kpi_state_map
from trading_dashboard.kpis.rules import STATE_BULL
from trading_dashboard.indicators import (
    supertrend, macd, wavetrend_lazybear, squeeze_momentum_lazybear,
    stoch_momentum_index, bollinger_bands, turtle_trade_channels,
    crsi, adx_di, dema, parabolic_sar, ut_bot_alert,
    ichimoku, obv_oscillator, donchian_trend_ribbon,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IS_FRACTION = 0.70
N_TRIALS = 60
TOP_K = 5
SEED = 42
MAX_STOCKS = 150


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_ohlcv_data(enriched_dir: Path, timeframe: str) -> Dict[str, pd.DataFrame]:
    data: Dict[str, pd.DataFrame] = {}
    for f in sorted(enriched_dir.glob(f"*_{timeframe}.csv")):
        symbol = f.stem.rsplit(f"_{timeframe}", 1)[0]
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=[0])
            df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
            df = df.sort_index()
            if len(df) >= 100 and "Close" in df.columns:
                ohlcv = df[["Open", "High", "Low", "Close"] +
                           (["Volume"] if "Volume" in df.columns else [])].copy()
                data[symbol] = ohlcv
        except Exception:
            continue
    return data


# ---------------------------------------------------------------------------
# Per-indicator compute wrappers
# ---------------------------------------------------------------------------

def _compute_supertrend(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    st_line, st_trend, st_buy, st_sell = supertrend(
        df, periods=int(params["periods"]), multiplier=float(params["multiplier"]),
        change_atr_method=True,
    )
    df["SuperTrend_line"] = st_line
    df["SuperTrend_trend"] = st_trend
    return df


def _compute_macd(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    m, s, h = macd(df["Close"], fast=int(params["fast"]), slow=int(params["slow"]),
                   signal=int(params["signal"]))
    df["MACD"] = m
    df["MACD_signal"] = s
    df["MACD_hist"] = h
    return df


def _compute_wavetrend(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    wt1, wt2, hist = wavetrend_lazybear(df, n1=int(params["n1"]), n2=int(params["n2"]))
    df["WT_LB_wt1"] = wt1
    df["WT_LB_wt2"] = wt2
    return df


def _compute_sqzmom(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    val, on, off, no_squeeze, bcolor, scolor = squeeze_momentum_lazybear(
        df, length=int(params["length"]), mult=float(params["mult"]),
        length_kc=int(params["length_kc"]), mult_kc=float(params["mult_kc"]),
    )
    df["SQZ_val"] = val
    df["SQZ_on"] = on
    return df


def _compute_smi(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    smi_val, smi_ema = stoch_momentum_index(
        df, a=int(params["a"]), b=int(params["b"]), c=int(params["c"]),
        smooth_period=int(params["smooth_period"]),
    )
    df["SMI"] = smi_val
    df["SMI_ema"] = smi_ema
    return df


def _compute_bb(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    vol = df["Volume"] if "Volume" in df.columns else None
    basis, upper, lower = bollinger_bands(
        df["Close"], length=int(params["length"]), mult=float(params["mult"]),
        ma_type=str(params.get("ma_type", "SMA")), volume=vol,
    )
    df["BB_basis"] = basis
    df["BB_upper"] = upper
    df["BB_lower"] = lower
    return df


def _compute_tutci(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    upper, lower, trend_line, exit_line = turtle_trade_channels(
        df, length=int(params["length"]), exit_length=int(params["exit_length"]),
    )
    df["TuTCI_upper"] = upper
    df["TuTCI_lower"] = lower
    df["TuTCI_trend"] = trend_line
    return df


def _compute_crsi(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    crsi_val, lb, ub = crsi(
        df["Close"], domcycle=int(params["domcycle"]), vibration=int(params["vibration"]),
        leveling=float(params["leveling"]),
    )
    df["cRSI"] = crsi_val
    df["cRSI_lb"] = lb
    df["cRSI_ub"] = ub
    return df


def _compute_adx(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    adx_val, dip, dim = adx_di(df, length=int(params["length"]))
    df["ADX"] = adx_val
    df["DI_plus"] = dip
    df["DI_minus"] = dim
    return df


def _compute_dema(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    d = dema(df["Close"], length=int(params["length"]))
    df[f"DEMA_{int(params['length'])}"] = d
    df["DEMA_9"] = d
    return df


def _compute_psar(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    p = parabolic_sar(df, start=float(params["start"]), increment=float(params["increment"]),
                      maximum=float(params["maximum"]))
    df["PSAR"] = p
    return df


def _compute_ut_bot(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    trail, pos, buy, sell = ut_bot_alert(df, a=float(params["a"]), c=int(params["c"]))
    df["UT_trailing_stop"] = trail
    df["UT_pos"] = pos
    return df


def _compute_ichimoku(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    result = ichimoku(df, tenkan=int(params["tenkan"]), kijun=int(params["kijun"]),
                      senkou_b=int(params["senkou_b"]), offset=int(params.get("offset", 26)))
    for col in result.columns:
        df[col] = result[col]
    return df


def _compute_donchian(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    result = donchian_trend_ribbon(df, dlen=int(params["dlen"]), depth=int(params["depth"]))
    for col in result.columns:
        df[col] = result[col]
    return df


def _compute_obvosc(ohlcv: pd.DataFrame, params: dict) -> pd.DataFrame:
    df = ohlcv.copy()
    obv, osc = obv_oscillator(df, length=int(params["length"]))
    df["OBV"] = obv
    df["OBV_osc"] = osc
    return df


# ---------------------------------------------------------------------------
# Indicator optimization configs
# ---------------------------------------------------------------------------

@dataclass
class IndicatorOptConfig:
    key: str
    kpi_name: str
    compute_fn: Callable
    param_ranges: Dict[str, Any]
    defaults: Dict[str, Any]


INDICATORS_TO_OPTIMIZE: List[IndicatorOptConfig] = [
    IndicatorOptConfig(
        key="SuperTrend", kpi_name="SuperTrend", compute_fn=_compute_supertrend,
        param_ranges={"periods": [8, 10, 12, 14, 16, 20], "multiplier": [2.0, 2.5, 3.0, 3.5, 4.0]},
        defaults={"periods": 12, "multiplier": 3.0},
    ),
    IndicatorOptConfig(
        key="MACD", kpi_name="CM_Ult_MacD_MFT", compute_fn=_compute_macd,
        param_ranges={"fast": [8, 10, 12, 14], "slow": [21, 24, 26, 30], "signal": [7, 9, 12]},
        defaults={"fast": 12, "slow": 26, "signal": 9},
    ),
    IndicatorOptConfig(
        key="WT_LB", kpi_name="WT_LB", compute_fn=_compute_wavetrend,
        param_ranges={"n1": [6, 8, 10, 14, 18], "n2": [15, 18, 21, 25, 30]},
        defaults={"n1": 10, "n2": 21},
    ),
    IndicatorOptConfig(
        key="SQZMOM_LB", kpi_name="SQZMOM_LB", compute_fn=_compute_sqzmom,
        param_ranges={"length": [14, 16, 20, 24, 28], "mult": [1.5, 2.0, 2.5],
                      "length_kc": [14, 16, 20, 24], "mult_kc": [1.0, 1.5, 2.0]},
        defaults={"length": 20, "mult": 2.0, "length_kc": 20, "mult_kc": 1.5},
    ),
    IndicatorOptConfig(
        key="BB", kpi_name="BB 30", compute_fn=_compute_bb,
        param_ranges={"length": [14, 16, 20, 24, 30], "mult": [1.5, 2.0, 2.5, 3.0]},
        defaults={"length": 20, "mult": 2.0},
    ),
    IndicatorOptConfig(
        key="TuTCI", kpi_name="TuTCI", compute_fn=_compute_tutci,
        param_ranges={"length": [14, 16, 20, 24, 30], "exit_length": [6, 8, 10, 12, 15]},
        defaults={"length": 20, "exit_length": 10},
    ),
    IndicatorOptConfig(
        key="cRSI", kpi_name="cRSI", compute_fn=_compute_crsi,
        param_ranges={"domcycle": [14, 16, 20, 24, 28], "vibration": [6, 8, 10, 14],
                      "leveling": [6.0, 8.0, 10.0, 12.0]},
        defaults={"domcycle": 20, "vibration": 10, "leveling": 10.0},
    ),
    IndicatorOptConfig(
        key="ADX_DI", kpi_name="ADX & DI", compute_fn=_compute_adx,
        param_ranges={"length": [8, 10, 14, 18, 21, 28]},
        defaults={"length": 14},
    ),
    IndicatorOptConfig(
        key="DEMA", kpi_name="DEMA", compute_fn=_compute_dema,
        param_ranges={"length": [5, 7, 9, 12, 15, 20, 26]},
        defaults={"length": 9},
    ),
    IndicatorOptConfig(
        key="PSAR", kpi_name="CM_P-SAR", compute_fn=_compute_psar,
        param_ranges={"start": [0.01, 0.015, 0.02, 0.025, 0.03],
                      "increment": [0.01, 0.015, 0.02, 0.025, 0.03],
                      "maximum": [0.1, 0.15, 0.2, 0.25, 0.3]},
        defaults={"start": 0.02, "increment": 0.02, "maximum": 0.2},
    ),
    IndicatorOptConfig(
        key="UT_Bot", kpi_name="UT Bot Alert", compute_fn=_compute_ut_bot,
        param_ranges={"a": [0.5, 1.0, 1.5, 2.0, 3.0], "c": [6, 8, 10, 12, 14]},
        defaults={"a": 1.0, "c": 10},
    ),
    IndicatorOptConfig(
        key="Ichimoku", kpi_name="Ichimoku", compute_fn=_compute_ichimoku,
        param_ranges={"tenkan": [7, 9, 12, 15], "kijun": [18, 22, 26, 30],
                      "senkou_b": [44, 52, 60, 78], "offset": [26]},
        defaults={"tenkan": 9, "kijun": 26, "senkou_b": 52, "offset": 26},
    ),
    IndicatorOptConfig(
        key="DonchianRibbon", kpi_name="Donchian Ribbon", compute_fn=_compute_donchian,
        param_ranges={"dlen": [14, 16, 20, 24, 30], "depth": [6, 8, 10, 12, 15]},
        defaults={"dlen": 20, "depth": 10},
    ),
    IndicatorOptConfig(
        key="OBVOSC", kpi_name="OBVOSC_LB", compute_fn=_compute_obvosc,
        param_ranges={"length": [10, 14, 20, 26, 30]},
        defaults={"length": 20},
    ),
    IndicatorOptConfig(
        key="SMI", kpi_name="Stoch_MTM", compute_fn=_compute_smi,
        param_ranges={"a": [8, 10, 13, 16], "b": [2, 3, 5],
                      "c": [8, 10, 13], "smooth_period": [3, 5, 7]},
        defaults={"a": 10, "b": 3, "c": 10, "smooth_period": 5},
    ),
]


# ---------------------------------------------------------------------------
# Parameter sampling
# ---------------------------------------------------------------------------

def _sample_params(ranges: Dict[str, Any], n: int, seed: int) -> List[Dict[str, Any]]:
    """Generate n random parameter combos from ranges (lists of discrete values)."""
    rng = random.Random(seed)
    keys = sorted(ranges.keys())
    all_combos = []

    from itertools import product
    lists = [ranges[k] if isinstance(ranges[k], list) else [ranges[k]] for k in keys]
    full_grid = [dict(zip(keys, combo)) for combo in product(*lists)]

    if len(full_grid) <= n:
        return full_grid

    return rng.sample(full_grid, min(n, len(full_grid)))


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def _hit_rate_bull(states: pd.Series, close: pd.Series, horizon: int) -> Tuple[float, int]:
    fwd = close.pct_change(horizon).shift(-horizon)
    bull = states == STATE_BULL
    valid = bull & fwd.notna()
    n = int(valid.sum())
    if n == 0:
        return np.nan, 0
    correct = int((fwd[valid] > 0).sum())
    return float(correct / n), n


@dataclass
class ParamResult:
    params: Dict[str, Any]
    is_hit_rate: float
    oos_hit_rate: float
    is_trades: int
    oos_trades: int
    degradation: float

    @property
    def is_robust(self) -> bool:
        return self.oos_hit_rate > 0.50 and self.oos_trades >= 20


def optimize_indicator(
    config: IndicatorOptConfig,
    all_ohlcv: Dict[str, pd.DataFrame],
    horizon: int,
    n_trials: int = N_TRIALS,
    seed: int = SEED,
) -> Tuple[Optional[ParamResult], ParamResult, List[ParamResult]]:
    """
    Returns (best_optimized, default_result, top_k_results).
    best_optimized is None if no combo passes the overfitting guard.
    """
    param_combos = _sample_params(config.param_ranges, n_trials, seed)
    all_combos = [config.defaults] + [c for c in param_combos if c != config.defaults]

    combo_scores: List[Tuple[Dict, float, int, float, int]] = []

    for params in all_combos:
        is_hr_sum = 0.0
        is_n_sum = 0
        oos_hr_sum = 0.0
        oos_n_sum = 0

        for symbol, ohlcv in all_ohlcv.items():
            try:
                enriched = config.compute_fn(ohlcv, params)
            except Exception:
                continue

            state_map = compute_kpi_state_map(enriched)
            states = state_map.get(config.kpi_name)
            if states is None:
                continue

            close = enriched["Close"]
            split_idx = int(len(enriched) * IS_FRACTION)

            is_states = states.iloc[:split_idx]
            is_close = close.iloc[:split_idx]
            oos_states = states.iloc[split_idx:]
            oos_close = close.iloc[split_idx:]

            is_hr, is_n = _hit_rate_bull(is_states, is_close, horizon)
            oos_hr, oos_n = _hit_rate_bull(oos_states, oos_close, horizon)

            if not np.isnan(is_hr):
                is_hr_sum += is_hr * is_n
                is_n_sum += is_n
            if not np.isnan(oos_hr):
                oos_hr_sum += oos_hr * oos_n
                oos_n_sum += oos_n

        is_hr_agg = is_hr_sum / is_n_sum if is_n_sum > 0 else np.nan
        oos_hr_agg = oos_hr_sum / oos_n_sum if oos_n_sum > 0 else np.nan
        combo_scores.append((params, is_hr_agg, is_n_sum, oos_hr_agg, oos_n_sum))

    default_entry = combo_scores[0]
    default_result = ParamResult(
        params=default_entry[0], is_hit_rate=default_entry[1], oos_hit_rate=default_entry[3],
        is_trades=default_entry[2], oos_trades=default_entry[4],
        degradation=default_entry[1] - default_entry[3] if not np.isnan(default_entry[1]) and not np.isnan(default_entry[3]) else 0,
    )

    valid_is = [(p, is_hr, is_n, oos_hr, oos_n) for p, is_hr, is_n, oos_hr, oos_n in combo_scores
                if not np.isnan(is_hr) and is_n >= 50]
    valid_is.sort(key=lambda x: x[1], reverse=True)
    top_k = valid_is[:TOP_K]

    top_results: List[ParamResult] = []
    for p, is_hr, is_n, oos_hr, oos_n in top_k:
        deg = is_hr - oos_hr if not np.isnan(oos_hr) else 1.0
        top_results.append(ParamResult(
            params=p, is_hit_rate=is_hr, oos_hit_rate=oos_hr,
            is_trades=is_n, oos_trades=oos_n, degradation=deg,
        ))

    robust = [r for r in top_results if r.is_robust]
    if not robust:
        return None, default_result, top_results

    robust.sort(key=lambda r: r.degradation)
    best = robust[0]

    if best.oos_hit_rate <= default_result.oos_hit_rate + 0.001:
        return None, default_result, top_results

    return best, default_result, top_results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(v: float, d: int = 3) -> str:
    if v is None or np.isnan(v):
        return "—"
    return f"{v:.{d}f}"


def generate_report(
    results: Dict[str, Tuple[Optional[ParamResult], ParamResult, List[ParamResult]]],
    configs: Dict[str, IndicatorOptConfig],
    output_dir: Path,
    timeframe: str,
    horizon_label: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Phase 2 — Parameter Optimization Report ({timeframe}, Long-Only)",
        "",
        f"**IS/OOS split:** {IS_FRACTION*100:.0f}% / {(1-IS_FRACTION)*100:.0f}% by time "
        f"| **Horizon:** {horizon_label} | **Trials:** {N_TRIALS} | **Top-K:** {TOP_K}",
        "",
        "## Summary",
        "",
        "| Indicator | Default HR (IS) | Default HR (OOS) | Best HR (IS) | Best HR (OOS) | Degradation | Verdict | Best Params |",
        "|-----------|----------------|------------------|-------------|---------------|-------------|---------|-------------|",
    ]

    optimized_config: Dict[str, Dict[str, Any]] = {}
    improved = 0
    kept_default = 0

    for key in sorted(results.keys()):
        best, default, top_k = results[key]
        cfg = configs[key]

        if best is not None:
            verdict = "OPTIMIZED"
            improved += 1
            params_str = ", ".join(f"{k}={v}" for k, v in best.params.items())
            optimized_config[key] = {"params": best.params}
            lines.append(
                f"| {key} | {_fmt(default.is_hit_rate)} | {_fmt(default.oos_hit_rate)} "
                f"| {_fmt(best.is_hit_rate)} | {_fmt(best.oos_hit_rate)} "
                f"| {_fmt(best.degradation)} | {verdict} | {params_str} |"
            )
        else:
            verdict = "KEEP DEFAULT"
            kept_default += 1
            params_str = ", ".join(f"{k}={v}" for k, v in cfg.defaults.items())
            optimized_config[key] = {"params": cfg.defaults}
            lines.append(
                f"| {key} | {_fmt(default.is_hit_rate)} | {_fmt(default.oos_hit_rate)} "
                f"| — | — | — | {verdict} | {params_str} |"
            )

    lines.extend([
        "",
        f"- **Improved:** {improved} indicators",
        f"- **Kept defaults:** {kept_default} indicators",
        "",
    ])

    for key in sorted(results.keys()):
        best, default, top_k = results[key]
        lines.append(f"### {key}")
        lines.append("")
        lines.append(f"Default: {configs[key].defaults}")
        lines.append("")
        if top_k:
            lines.append("| Rank | Params | IS HR | OOS HR | IS Trades | OOS Trades | Degrad. |")
            lines.append("|------|--------|-------|--------|-----------|------------|---------|")
            for i, r in enumerate(top_k, 1):
                ps = ", ".join(f"{k}={v}" for k, v in r.params.items())
                lines.append(
                    f"| {i} | {ps} | {_fmt(r.is_hit_rate)} | {_fmt(r.oos_hit_rate)} "
                    f"| {r.is_trades} | {r.oos_trades} | {_fmt(r.degradation)} |"
                )
            lines.append("")

    report = "\n".join(lines)
    (output_dir / "param_optimization_report.md").write_text(report, encoding="utf-8")

    config_path = output_dir / "indicator_config_optimised.json"
    config_path.write_text(json.dumps(optimized_config, indent=2) + "\n", encoding="utf-8")

    print(report)
    print(f"\nOptimized config saved to {config_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    tf = parse_timeframe_arg("Phase 2 — Parameter Optimization")
    OUTPUT_DIR = output_dir_for(tf.timeframe, "phase2")
    horizon_label = tf.horizon_labels[tf.horizons.index(tf.default_horizon)]

    print(f"Loading OHLCV data from {ENRICHED_DIR} ...")
    t0 = time.time()
    all_data = load_ohlcv_data(ENRICHED_DIR, tf.timeframe)

    if len(all_data) > MAX_STOCKS:
        rng = random.Random(SEED)
        keys = rng.sample(sorted(all_data.keys()), MAX_STOCKS)
        all_data = {k: all_data[k] for k in keys}

    print(f"Loaded {len(all_data)} stocks in {time.time() - t0:.1f}s")

    if not all_data:
        print("ERROR: No data found.")
        return 1

    results: Dict[str, Tuple[Optional[ParamResult], ParamResult, List[ParamResult]]] = {}
    configs: Dict[str, IndicatorOptConfig] = {}

    total = len(INDICATORS_TO_OPTIMIZE)
    for i, cfg in enumerate(INDICATORS_TO_OPTIMIZE, 1):
        print(f"\n[{i}/{total}] Optimizing {cfg.key} (KPI: {cfg.kpi_name}) ...", flush=True)
        t1 = time.time()

        best, default, top_k = optimize_indicator(cfg, all_data, horizon=tf.default_horizon)
        results[cfg.key] = (best, default, top_k)
        configs[cfg.key] = cfg

        elapsed = time.time() - t1
        if best:
            print(f"  OPTIMIZED: IS={_fmt(best.is_hit_rate)} OOS={_fmt(best.oos_hit_rate)} "
                  f"(default OOS={_fmt(default.oos_hit_rate)}) in {elapsed:.0f}s")
            print(f"  Best params: {best.params}")
        else:
            print(f"  KEEP DEFAULT: OOS={_fmt(default.oos_hit_rate)} in {elapsed:.0f}s")

    print(f"\n{'='*60}")
    generate_report(results, configs, OUTPUT_DIR, tf.timeframe, horizon_label)
    print(f"\nPhase 2 complete in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
