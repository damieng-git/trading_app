"""Daily screener — scans a broad US+EU universe for C3/C4 combo signals.

Entry Strategy v5 pipeline:
  1. Download 1D OHLCV → quality filters
  2. SR Break N=10 pre-filter (computed on raw OHLCV, before lean enrichment)
  3. Lean enrichment (5 KPIs + SMA200 + SMA20)
  4. C3/C4 onset detection (transition only, not continuation)
  5. SMA20 > SMA200 entry gate
  6. Volume spike 1.5× N=5 confirmation
  7. Fresh-entry filter: run position model to discard candidates where an
     older open position exists (hold > 2 bars) — prevents stale HOLDs
  8. Rank → write to entry_stocks.csv → trigger full dashboard pipeline
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from apps.dashboard.config_loader import (
    CONFIG_JSON,
    DASHBOARD_ARTIFACTS_DIR,
    FEATURE_STORE_ENRICHED_DIR,
    LISTS_DIR,
    OHLCV_CACHE_DIR,
    VALID_TIMEFRAMES,
)

_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"
_OUTPUT_PATH = DASHBOARD_ARTIFACTS_DIR / "daily_screener.json"

MAX_C3_HITS = 0  # 0 = no limit
MAX_C4_HITS = 0  # 0 = no limit


def _load_screener_config() -> dict:
    """Load C3/C4 combo definitions and KPI weights from dashboard config."""
    cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    return {
        "combo_kpis_by_tf": cfg.get("combo_kpis_by_tf", {}),
        "combo_3_kpis": cfg.get("combo_3_kpis", []),
        "combo_4_kpis": cfg.get("combo_4_kpis", []),
        "kpi_weights": cfg.get("kpi_weights", {}),
        "stoch_mtm_thresholds": cfg.get("stoch_mtm_thresholds"),
        "strategy_setups": cfg.get("strategy_setups", {}),
    }


def _resolve_strategy_combos(strategy: str, tf: str, strategy_setups: dict) -> tuple[list[str], list[int]] | None:
    """Return (c3_kpis, c3_pols) for the given strategy and timeframe.

    Falls back to the strategy's global combos if no TF-specific combos are defined.
    Returns None if the strategy is not found or has no c3 combos.
    """
    setup = strategy_setups.get(strategy)
    if not setup:
        return None
    combos_by_tf = setup.get("combos_by_tf", {})
    tf_combos = combos_by_tf.get(tf, {})
    combos = tf_combos if tf_combos else setup.get("combos", {})
    c3 = combos.get("c3", {})
    kpis = c3.get("kpis", [])
    if not kpis:
        return None
    pols = c3.get("pols", [1] * len(kpis))
    return kpis, pols


# KPIs already computed by the default lean enrichment — no need to add as extras
_LEAN_DEFAULT_KPIS = frozenset({
    "Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20",
    "GK Trend Ribbon", "cRSI",
})


def _compute_trend_score(kpi_state_map: dict, kpi_weights: dict) -> float:
    """Weighted sum of KPI states for ranking."""
    score = 0.0
    for kpi_name, state_val in kpi_state_map.items():
        if state_val in (1, -1):
            w = float(kpi_weights.get(kpi_name, 1.0))
            score += w * float(state_val)
    return round(score, 4)


def _emit(cb, *, phase, phase_num, label, detail, step, step_total, t0,
           phase_weights=None):
    """Fire the on_progress callback with ETA estimation."""
    if cb is None:
        return
    elapsed = time.perf_counter() - t0
    pw = phase_weights or {"download": 0.60, "filter": 0.01, "enrich": 0.25,
                           "detect": 0.01, "dashboard": 0.13}
    phases = list(pw.keys())
    completed_w = sum(pw[p] for p in phases[:phases.index(phase)])
    phase_pct = step / step_total if step_total else 1.0
    overall = completed_w + pw[phase] * phase_pct
    eta = max(0, (elapsed / overall) - elapsed) if overall > 0.01 else None
    cb({
        "phase": phase, "phase_num": phase_num, "phase_total": 5,
        "label": label, "detail": detail,
        "step": step, "step_total": step_total,
        "pct": round(overall * 100, 1),
        "elapsed_s": round(elapsed, 1),
        "eta_s": round(eta, 0) if eta is not None else None,
    })


def run_screener(
    *,
    universe_csv: Path | None = None,
    indicator_config_path: Path | None = None,
    use_cached_ohlcv: bool = False,
    dry_run: bool = False,
    output_path: Path | None = None,
    max_c3: int = MAX_C3_HITS,
    max_c4: int = MAX_C4_HITS,
    on_progress=None,
    strategy: str | None = None,
    timeframe: str = "1D",
) -> dict:
    """Execute the daily screener pipeline.

    *on_progress*, if provided, is called with a dict containing phase info,
    step/total counts, overall percentage, elapsed/ETA seconds, and a
    human-readable label + detail string.

    *strategy* selects a named strategy from config.json ``strategy_setups``.
    When set, uses that strategy's C3 KPIs (polarity-aware) for matching instead
    of the default all-bullish combo. C4 logic is skipped.

    *timeframe* selects the OHLCV timeframe to scan (default "1D").
    For non-1D: quality filters are run on 1D data, then resampled to target TF.

    Returns a dict with c3_hits, c4_hits, and metadata.
    """
    from apps.screener.lean_enrichment import compute_lean_indicators
    from apps.screener.universe import (
        DEFAULT_MIN_BARS,
        DEFAULT_MIN_DOLLAR_VOLUME,
        DEFAULT_MIN_MARKET_CAP,
        DEFAULT_MIN_PRICE,
        apply_quality_filters,
        exclude_indices_and_leveraged,
        load_universe,
    )
    from trading_dashboard.data.downloader import (
        download_daily_batch,
        download_hourly_batch,
        resample_to_4h,
        resample_to_biweekly,
        resample_to_monthly,
        resample_to_weekly,
    )
    from trading_dashboard.kpis.catalog import KPI_TREND_ORDER, compute_kpi_state_map

    t0 = time.perf_counter()
    out_path = output_path or _OUTPUT_PATH
    tf = timeframe or "1D"

    _emit(on_progress, phase="download", phase_num=1,
          label="Loading config & universe", detail="",
          step=0, step_total=1, t0=t0)

    # ── Load config ──────────────────────────────────────────────────────
    scr_cfg = _load_screener_config()
    tf_combos = scr_cfg["combo_kpis_by_tf"].get("1D", {})
    c3_kpis = tf_combos.get("combo_3", scr_cfg["combo_3_kpis"])
    c4_kpis = tf_combos.get("combo_4", scr_cfg["combo_4_kpis"])
    kpi_weights = scr_cfg["kpi_weights"]

    # ── Resolve strategy combos ───────────────────────────────────────────
    strategy_c3_kpis: list[str] | None = None
    strategy_c3_pols: list[int] | None = None
    extra_kpis: list[str] = []
    if strategy:
        resolved = _resolve_strategy_combos(strategy, tf, scr_cfg["strategy_setups"])
        if resolved:
            strategy_c3_kpis, strategy_c3_pols = resolved
            extra_kpis = [k for k in strategy_c3_kpis if k not in _LEAN_DEFAULT_KPIS]
            logger.info("Strategy '%s' / %s: C3 KPIs=%s pols=%s extra=%s",
                        strategy, tf, strategy_c3_kpis, strategy_c3_pols, extra_kpis)
        else:
            logger.warning("Strategy '%s' not found or has no C3 combos for %s; "
                           "falling back to default combos", strategy, tf)

    # ── Load universe ────────────────────────────────────────────────────
    universe_df = load_universe(universe_csv, allowed_geo={"US", "EU"})
    all_tickers = exclude_indices_and_leveraged(universe_df["ticker"].tolist())
    logger.info("Universe after geo + index filter: %d tickers", len(all_tickers))

    if dry_run:
        return {
            "dry_run": True,
            "universe_size": len(universe_df),
            "after_filters": len(all_tickers),
            "tickers_sample": all_tickers[:20],
        }

    # ── Load sector map for market cap filter ────────────────────────────
    try:
        from apps.dashboard.sector_map import load_sector_map
        sector_map = load_sector_map()
    except Exception:
        sector_map = {}

    # ── Download OHLCV ────────────────────────────────────────────────────
    # Always download 1D first: quality filters require 1D price/volume data.
    # For non-1D timeframes, resample after filtering.
    start_date = "2023-06-01"
    logger.info("Downloading 1D OHLCV for %d tickers (TF target: %s)...", len(all_tickers), tf)

    _hourly_map: Dict[str, pd.DataFrame] = {}
    _skip_enrichment = False
    if use_cached_ohlcv:
        from trading_dashboard.data.store import DataStore
        store = DataStore(
            enriched_dir=FEATURE_STORE_ENRICHED_DIR / "dashboard" / "stock_data",
            raw_dir=OHLCV_CACHE_DIR / "dashboard",
            fmt="parquet",
            cache_ttl_hours=0,
        )
        ohlcv_map: Dict[str, pd.DataFrame] = {}
        for sym in all_tickers:
            enriched = store.load_enriched(sym, tf, respect_ttl=False)
            if enriched is None or enriched.empty:
                enriched = store.load_enriched(sym, "1D", respect_ttl=False)
            if enriched is not None and not enriched.empty:
                ohlcv_map[sym] = enriched
        _skip_enrichment = True
    elif tf == "4H":
        # Download 1H hourly data, then resample to 4H after quality filters
        chunk_sz = 50
        n_batches = (len(all_tickers) + chunk_sz - 1) // chunk_sz

        def _dl_progress_4h(done, total, chunk):
            batch_num = (done + chunk_sz - 1) // chunk_sz
            sample = ", ".join(chunk[:4])
            if len(chunk) > 4:
                sample += f"\u2026 ({len(chunk)})"
            _emit(on_progress, phase="download", phase_num=1,
                  label=f"Downloading 1H batch {batch_num}/{n_batches}",
                  detail=sample, step=done, step_total=total, t0=t0)

        hourly_map = download_hourly_batch(
            all_tickers, period="730d", chunk_size=chunk_sz,
            on_chunk=_dl_progress_4h,
        )
        # Build a 1D proxy for quality filtering (resample hourly → daily)
        ohlcv_map = {}
        for sym, h_df in hourly_map.items():
            if h_df is not None and not h_df.empty:
                try:
                    daily_proxy = h_df.resample("1D").agg({
                        "Open": "first", "High": "max", "Low": "min",
                        "Close": "last", "Volume": "sum",
                    }).dropna(subset=["Close"])
                    ohlcv_map[sym] = daily_proxy
                except Exception:
                    pass
        # Keep hourly data for resampling after filters
        _hourly_map = hourly_map
    else:
        chunk_sz = 50
        n_batches = (len(all_tickers) + chunk_sz - 1) // chunk_sz

        def _dl_progress(done, total, chunk):
            batch_num = (done + chunk_sz - 1) // chunk_sz
            sample = ", ".join(chunk[:4])
            if len(chunk) > 4:
                sample += f"\u2026 ({len(chunk)})"
            _emit(on_progress, phase="download", phase_num=1,
                  label=f"Downloading batch {batch_num}/{n_batches}",
                  detail=sample, step=done, step_total=total, t0=t0)

        ohlcv_map = download_daily_batch(
            all_tickers, start_date, chunk_size=chunk_sz,
            on_chunk=_dl_progress,
        )

    logger.info("Downloaded %d / %d tickers", len(ohlcv_map), len(all_tickers))

    # ── Quality filters ──────────────────────────────────────────────────
    _emit(on_progress, phase="filter", phase_num=2,
          label="Applying quality filters", detail="price, volume, market cap",
          step=0, step_total=1, t0=t0)

    filtered = apply_quality_filters(
        list(ohlcv_map.keys()),
        ohlcv_map,
        sector_map=sector_map,
    )

    _emit(on_progress, phase="filter", phase_num=2,
          label="Quality filters done",
          detail=f"{len(filtered)}/{len(ohlcv_map)} passed",
          step=1, step_total=1, t0=t0)

    # ── SR Break N=10 pre-filter (v5 — raw OHLCV, before enrichment) ────
    _SR_BREAK_LOOKBACK = 10
    sr_passed = set(filtered)
    try:
        from trading_dashboard.indicators.sr_breaks_retests import sr_breaks_retests
        sr_checked = 0
        sr_blocked = 0
        for sym in filtered:
            sr_checked += 1
            df_raw = ohlcv_map[sym]
            if df_raw is None or df_raw.empty or len(df_raw) < 60:
                continue
            try:
                sr_df = sr_breaks_retests(df_raw, lookback=20, atr_len=200)
                sr_state = sr_df["SR_state"].to_numpy()
                tail = sr_state[-_SR_BREAK_LOOKBACK:]
                sr_state[max(0, len(sr_state) - _SR_BREAK_LOOKBACK - 1):-_SR_BREAK_LOOKBACK] if len(sr_state) > _SR_BREAK_LOOKBACK else sr_state[:0]
                has_recent_break = False
                for j in range(len(tail)):
                    abs_idx = len(sr_state) - _SR_BREAK_LOOKBACK + j
                    prev_val = sr_state[abs_idx - 1] if abs_idx > 0 else 0
                    if int(tail[j]) == 1 and int(prev_val) != 1:
                        has_recent_break = True
                        break
                if not has_recent_break:
                    sr_passed.discard(sym)
                    sr_blocked += 1
            except Exception as exc:
                logger.warning("SR Break check failed for %s: %s", sym, exc)
        logger.info("SR Break pre-filter: %d/%d passed (%d blocked)",
                    len(sr_passed), sr_checked, sr_blocked)
    except ImportError:
        logger.warning("sr_breaks_retests not available, skipping SR pre-filter")

    filtered_sr = [s for s in filtered if s in sr_passed]

    # ── Resample to target timeframe (if non-1D) ──────────────────────────
    if not _skip_enrichment and tf != "1D":
        _resample_map: Dict[str, pd.DataFrame] = {}
        if tf == "4H":
            for sym in filtered_sr:
                h_df = _hourly_map.get(sym)
                if h_df is not None and not h_df.empty:
                    try:
                        _resample_map[sym] = resample_to_4h(h_df)
                    except Exception:
                        pass
        elif tf == "1W":
            for sym in filtered_sr:
                d_df = ohlcv_map.get(sym)
                if d_df is not None and not d_df.empty:
                    try:
                        _resample_map[sym] = resample_to_weekly(d_df)
                    except Exception:
                        pass
        elif tf == "2W":
            for sym in filtered_sr:
                d_df = ohlcv_map.get(sym)
                if d_df is not None and not d_df.empty:
                    try:
                        _resample_map[sym] = resample_to_biweekly(d_df)
                    except Exception:
                        pass
        elif tf == "1M":
            for sym in filtered_sr:
                d_df = ohlcv_map.get(sym)
                if d_df is not None and not d_df.empty:
                    try:
                        _resample_map[sym] = resample_to_monthly(d_df)
                    except Exception:
                        pass
        for sym in filtered_sr:
            if sym in _resample_map:
                ohlcv_map[sym] = _resample_map[sym]
        logger.info("Resampled %d/%d tickers from 1D to %s",
                    len(_resample_map), len(filtered_sr), tf)

    # ── Lean enrichment + C3/C4 onset detection (v5) ──────────────────
    logger.info("Enriching %d tickers (lean mode, post-SR filter)...", len(filtered_sr))

    c3_candidates: List[dict] = []
    c4_candidates: List[dict] = []
    _enriched_cache: Dict[str, tuple] = {}  # sym → (df, kpi_state)

    _VOL_SPIKE_MULT = 1.5
    _VOL_SPIKE_LOOKBACK = 5

    n_filtered = len(filtered_sr)
    for loop_i, sym in enumerate(filtered_sr):
        if loop_i % 10 == 0:
            _emit(on_progress, phase="enrich", phase_num=3,
                  label=f"Enriching {loop_i}/{n_filtered}",
                  detail=sym, step=loop_i, step_total=n_filtered, t0=t0)

        df_raw = ohlcv_map[sym]
        if _skip_enrichment:
            df = df_raw
        else:
            try:
                df = compute_lean_indicators(
                    df_raw,
                    indicator_config_path=indicator_config_path,
                    extra_kpis=extra_kpis if extra_kpis else None,
                )
            except Exception:
                logger.debug("Enrichment failed for %s, skipping", sym, exc_info=True)
                continue

        if df is None or df.empty or len(df) < 200:
            continue

        # v5: SMA20 > SMA200 entry gate (replaces Close > SMA200)
        sma200 = df.get("SMA200")
        sma20 = df.get("SMA20")
        if sma200 is not None and sma20 is not None:
            sma200_last = sma200.iloc[-1]
            sma20_last = sma20.iloc[-1]
            if not pd.isna(sma200_last) and not pd.isna(sma20_last):
                if float(sma20_last) < float(sma200_last):
                    continue

        # v5: Volume spike 1.5× within last 5 bars
        vol_spike_pass = True
        if "Volume" in df.columns and "Vol_MA20" in df.columns:
            vol_tail = df["Volume"].iloc[-_VOL_SPIKE_LOOKBACK:].to_numpy(float)
            ma_tail = df["Vol_MA20"].iloc[-_VOL_SPIKE_LOOKBACK:].to_numpy(float)
            valid = np.isfinite(vol_tail) & np.isfinite(ma_tail)
            if valid.any():
                vol_spike_pass = bool(
                    (vol_tail[valid] >= _VOL_SPIKE_MULT * ma_tail[valid]).any()
                )
            else:
                vol_spike_pass = False
        if not vol_spike_pass:
            continue

        st = compute_kpi_state_map(df, stoch_mtm_thresholds=scr_cfg.get("stoch_mtm_thresholds"))

        def _kpi_bull_at(kpi_list: list, idx: int) -> bool:
            for k in kpi_list:
                s = st.get(k)
                if s is None or len(s) <= abs(idx):
                    return False
                v = s.iloc[idx]
                if pd.isna(v) or int(v) != 1:
                    return False
            return True

        def _kpi_pol_at(kpi_list: list, pol_list: list, idx: int) -> bool:
            for k, p in zip(kpi_list, pol_list):
                s = st.get(k)
                if s is None or len(s) <= abs(idx):
                    return False
                v = s.iloc[idx]
                if pd.isna(v) or int(v) != p:
                    return False
            return True

        # Trend score for ranking
        kpi_state_map = {}
        for k in KPI_TREND_ORDER:
            s = st.get(k)
            if s is not None and len(s) > 0 and pd.notna(s.iloc[-1]):
                kpi_state_map[k] = int(s.iloc[-1])
            else:
                kpi_state_map[k] = -2
        trend_score = _compute_trend_score(kpi_state_map, kpi_weights)

        last_close = float(df["Close"].iloc[-1])
        prev_close = float(df["Close"].iloc[-2]) if len(df) >= 2 else None
        delta_pct = round(((last_close - prev_close) / prev_close) * 100, 2) if prev_close and prev_close != 0 else None

        # Avg dollar volume
        tail20 = df.tail(20)
        avg_dv = float((tail20["Close"] * tail20["Volume"]).mean()) if "Volume" in df.columns else None

        meta = sector_map.get(sym, {})
        fund = meta.get("fundamentals", {}) if isinstance(meta, dict) else {}

        base_rec = {
            "symbol": sym,
            "name": meta.get("name", ""),
            "sector": meta.get("sector", ""),
            "industry": meta.get("industry", ""),
            "geo": meta.get("geo", ""),
            "last_close": round(last_close, 2),
            "delta_pct": delta_pct,
            "avg_dollar_volume": round(avg_dv, 0) if avg_dv else None,
            "market_cap": fund.get("market_cap"),
            "trend_score": trend_score,
        }

        # C3 onset detection — 3-bar lookback, polarity-aware when strategy set
        if strategy_c3_kpis is not None:
            def _c3_at(idx: int) -> bool:
                return _kpi_pol_at(strategy_c3_kpis, strategy_c3_pols, idx)
            c3_kpis_used = strategy_c3_kpis
        else:
            def _c3_at(idx: int) -> bool:
                return _kpi_bull_at(c3_kpis, idx)
            c3_kpis_used = c3_kpis

        c3_now = _c3_at(-1)
        c3_prev = _c3_at(-2) if len(df) >= 2 else False
        c3_onset = c3_now and not c3_prev

        # Also check bar -2 for onset (3-bar lookback for recent onsets)
        if not c3_onset and len(df) >= 3:
            if _c3_at(-2) and not _c3_at(-3):
                c3_onset = True

        if c3_onset:
            rec = {**base_rec, "combo": "C3", "c3_kpis": c3_kpis_used}
            c3_candidates.append(rec)
            _enriched_cache[sym] = (df, st)

        # C4 onset detection — skip entirely when strategy-specific scan
        if strategy_c3_kpis is None:
            c4_now = _kpi_bull_at(c4_kpis, -1)
            c4_prev = _kpi_bull_at(c4_kpis, -2) if len(df) >= 2 else False
            c4_onset = c4_now and not c4_prev

            if not c4_onset and len(df) >= 3:
                if _kpi_bull_at(c4_kpis, -2) and not _kpi_bull_at(c4_kpis, -3):
                    c4_onset = True

            if c4_onset:
                rec = {**base_rec, "combo": "C4", "c4_kpis": c4_kpis}
                c4_candidates.append(rec)
                _enriched_cache.setdefault(sym, (df, st))

    # ── Fresh-entry filter: discard candidates with an older open position ─
    # The onset detection above uses a 2-bar lookback, but the full position
    # model may find an earlier entry that was never exited.  Only keep
    # candidates whose most recent position is genuinely fresh (≤2 bars old).
    _FRESH_ENTRY_MAX_HOLD = 2

    def _is_fresh_entry(sym: str, combo_kpis: list) -> bool:
        cached = _enriched_cache.get(sym)
        if cached is None:
            return True
        df_cached, st_cached = cached
        try:
            from apps.dashboard.strategy import compute_position_events
            events = compute_position_events(
                df_cached, st_cached, combo_kpis, c4_kpis, tf)
            if not events:
                return True
            last_ev = events[-1]
            if last_ev["exit_reason"] != "Open":
                return True
            return last_ev["hold"] <= _FRESH_ENTRY_MAX_HOLD
        except Exception:
            logger.debug("Fresh-entry check failed for %s", sym, exc_info=True)
            return True

    pre_c3 = len(c3_candidates)
    pre_c4 = len(c4_candidates)
    c3_candidates = [r for r in c3_candidates
                     if _is_fresh_entry(r["symbol"], r.get("c3_kpis", c3_kpis))]
    c4_candidates = [r for r in c4_candidates
                     if _is_fresh_entry(r["symbol"], r.get("c4_kpis", c4_kpis))]
    _hold_filtered = (pre_c3 - len(c3_candidates)) + (pre_c4 - len(c4_candidates))
    if _hold_filtered:
        logger.info("Fresh-entry filter removed %d stale HOLD candidates "
                    "(C3: %d→%d, C4: %d→%d)",
                    _hold_filtered, pre_c3, len(c3_candidates),
                    pre_c4, len(c4_candidates))

    _emit(on_progress, phase="enrich", phase_num=3,
          label="Enrichment complete",
          detail=f"{n_filtered} tickers processed",
          step=n_filtered, step_total=n_filtered, t0=t0)

    # ── Rank and cap ─────────────────────────────────────────────────────
    _emit(on_progress, phase="detect", phase_num=4,
          label="Ranking results",
          detail=f"{len(c3_candidates)} C3, {len(c4_candidates)} C4 hits",
          step=0, step_total=1, t0=t0)
    c3_candidates.sort(key=lambda r: -r["trend_score"])
    c4_candidates.sort(key=lambda r: -r["trend_score"])

    c3_hits = c3_candidates[:max_c3] if max_c3 else c3_candidates
    c4_hits = c4_candidates[:max_c4] if max_c4 else c4_candidates

    elapsed = round(time.perf_counter() - t0, 1)
    logger.info("Screener complete: %d C3 hits, %d C4 hits (%.1fs)",
                len(c3_hits), len(c4_hits), elapsed)

    result = {
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "timeframe": tf,
        "universe_size": len(universe_df),
        "after_filters": len(filtered),
        "elapsed_seconds": elapsed,
        "filters_applied": {
            "min_dollar_volume": DEFAULT_MIN_DOLLAR_VOLUME,
            "min_price": DEFAULT_MIN_PRICE,
            "min_market_cap": DEFAULT_MIN_MARKET_CAP,
            "min_bars": DEFAULT_MIN_BARS,
            "sma_gate": "SMA20 > SMA200",
            "vol_spike": "1.5x N=5",
            "sr_break_prefilter": "N=10",
            "onset_only": True,
            "fresh_entry_filter": f"hold <= {_FRESH_ENTRY_MAX_HOLD}",
            "hold_filtered": _hold_filtered,
            "geo": ["EU", "US"],
        },
        "c3_total_found": len(c3_candidates),
        "c4_total_found": len(c4_candidates),
        "c3_hits": c3_hits,
        "c4_hits": c4_hits,
    }

    # ── Write output ─────────────────────────────────────────────────────
    _emit(on_progress, phase="detect", phase_num=4,
          label="Saving results", detail=str(out_path.name),
          step=1, step_total=1, t0=t0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    logger.info("Results written to %s", out_path)

    return result


def _purge_stale_data(tickers: set[str]) -> int:
    """Delete enriched parquets and chart assets for tickers no longer needed."""
    import shutil

    stock_dir = FEATURE_STORE_ENRICHED_DIR / "dashboard" / "stock_data"
    assets_dir = DASHBOARD_ARTIFACTS_DIR / "dashboard_assets"
    purged = 0
    for sym in tickers:
        for tf in VALID_TIMEFRAMES:
            for ext in ("parquet", "csv"):
                p = stock_dir / f"{sym}_{tf}.{ext}"
                if p.exists():
                    p.unlink()
                    purged += 1
        asset_path = assets_dir / sym
        if asset_path.is_dir():
            shutil.rmtree(asset_path, ignore_errors=True)
            purged += 1
    if purged:
        logger.info("Purged %d stale files for %d tickers", purged, len(tickers))
    return purged


def inject_screener_groups(
    screener_result: dict,
    config_path: Path | None = None,
    group_name: str | None = None,
) -> list[str]:
    """Write screener combo hits to a group CSV file.

    When *group_name* is given, writes to ``{group_name}.csv`` instead of
    ``entry_stocks.csv``. Replaces the contents of the target file with the
    deduplicated list of C3/C4 combo tickers, excluding any already in
    portfolio.csv. Deletes enriched data and chart assets for tickers that
    were dropped and don't belong to any other group.

    Returns the deduplicated list of symbols written.
    """
    import csv

    lists_dir = LISTS_DIR
    csv_name = f"{group_name}.csv" if group_name else "entry_stocks.csv"
    entry_stocks_path = lists_dir / csv_name
    portfolio_path = lists_dir / "portfolio.csv"

    # Read old entry_stocks before overwriting
    old_tickers: set[str] = set()
    if entry_stocks_path.exists():
        with open(entry_stocks_path, encoding="utf-8") as f:
            old_tickers = {
                row[0].strip().upper()
                for row in csv.reader(f)
                if row and row[0].strip() and row[0].strip().lower() != "ticker"
            }

    portfolio_tickers: set[str] = set()
    if portfolio_path.exists():
        with open(portfolio_path, encoding="utf-8") as f:
            portfolio_tickers = {
                row[0].strip().upper()
                for row in csv.reader(f)
                if row and row[0].strip() and row[0].strip().lower() != "ticker"
            }

    c3_syms = [h["symbol"] for h in screener_result.get("c3_hits", [])]
    c4_syms = [h["symbol"] for h in screener_result.get("c4_hits", [])]

    seen: set[str] = set()
    merged: list[str] = []
    for s in c4_syms + c3_syms:
        s_upper = s.strip().upper()
        if s_upper not in seen and s_upper not in portfolio_tickers:
            seen.add(s_upper)
            merged.append(s)

    with open(entry_stocks_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker"])
        for t in sorted(merged):
            w.writerow([t])

    logger.info("%s updated: %d combo tickers (excluded %d portfolio)",
                csv_name, len(merged), len(portfolio_tickers))

    # Purge data for tickers dropped from entry_stocks and not in any other group
    new_tickers = {s.upper() for s in merged}
    dropped = old_tickers - new_tickers
    if dropped:
        other_group_tickers: set[str] = set()
        for csv_file in lists_dir.glob("*.csv"):
            if csv_file.name == csv_name:
                continue
            try:
                with open(csv_file, encoding="utf-8") as f:
                    for row in csv.reader(f):
                        if row and row[0].strip() and row[0].strip().lower() != "ticker":
                            other_group_tickers.add(row[0].strip().upper())
            except Exception:
                continue
        to_purge = dropped - other_group_tickers
        if to_purge:
            _purge_stale_data(to_purge)

    return merged
