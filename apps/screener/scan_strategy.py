"""Parallel multi-strategy stock scanner.

Pipeline per symbol:
  1. Download OHLCV (lean, yfinance batch)  — ThreadPoolExecutor(6 workers)
  2. Quality gate check (SMA20>SMA200, volume spike, SR break) per strategy
  3. Lean enrichment — only KPIs needed for strategy C3/C4
  4. C3 onset detection (last 3 bars)
  5. Write survivors to configs/lists/scan_list.csv (union of all strategies, clean-slate replace)
  6. Background subprocess: `python -m trading_dashboard dashboard refresh`

Usage (CLI):
  python -m apps.screener.scan_strategy --strategy trend --tf 1D
  python -m apps.screener.scan_strategy --strategy stoof --tf 2W
  python -m apps.screener.scan_strategy --strategy all --tf 1D
  python -m apps.screener.scan_strategy --strategy trend --tf all
  python -m apps.screener.scan_strategy --strategy all --tf all

Streaming (SSE via serve_dashboard /api/scan):
  Yields {"type": "progress", "pct": int, "msg": str}
  Yields {"type": "done", "count": int, "elapsed": float}
  Yields {"type": "error", "msg": str}
"""

from __future__ import annotations

import csv
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Generator

import pandas as pd
import yfinance as yf

from apps.screener.scan_enrichment import (
    check_quality_gates,
    check_quality_gates_raw,
    compute_scan_indicators,
    compute_scan_kpi_states,
    detect_c3_onset,
    min_bars_for_combo,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "apps" / "dashboard" / "configs" / "config.json"
_LISTS_DIR = _REPO_ROOT / "apps" / "dashboard" / "configs" / "lists"
_UNIVERSE_CSV = _REPO_ROOT / "apps" / "screener" / "configs" / "universe.csv"
_INDICATOR_CONFIG = _REPO_ROOT / "apps" / "dashboard" / "configs" / "indicator_config.json"

# How many tickers to download in one yfinance batch call
_BATCH_SIZE = 50
_MAX_WORKERS = 1
_BATCH_DELAY = 1.0   # seconds between batches (matches production downloader)
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 10  # exponential backoff base (10s, 20s, 40s)
# How many calendar days of history to download (enough for longest warmup ~235 bars)
# 235 trading days ≈ 11.5 months; use 15 months to be safe; 1W needs fewer bars
_TF_DOWNLOAD_DAYS: dict[str, int] = {
    "4H": 120,    # ~500 4H bars @ 0.25 bar/day
    "1D": 450,    # ~320 bars (235 min + buffer)
    "1W": 3650,   # ~521 native weekly bars (was 700→~100, needed 202 for swing/trend)
    "2W": 1500,   # ~107 2W bars (sufficient)
    "1M": 7300,   # ~240 native monthly bars (was 3650→~120, needed 202 for swing/trend)
}
_TF_INTERVAL: dict[str, str] = {
    "4H": "1h",   # resample 1H → 4H
    "1D": "1d",
    "1W": "1wk",
    "2W": "1d",   # resample daily → 2W
    "1M": "1mo",
}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def _get_strategy_def(config: dict, strategy_key: str) -> dict:
    setups = config.get("strategy_setups", {})
    if strategy_key not in setups:
        raise ValueError(f"Unknown strategy '{strategy_key}'. Available: {list(setups)}")
    return setups[strategy_key]


def _get_combos(strat_def: dict, tf: str) -> tuple[list[str], list[int], list[str], list[int]]:
    """Return (c3_kpis, c3_pols, c4_kpis, c4_pols) for the given TF.

    Respects combos_by_tf[tf] first, falls back to flat combos.
    Stoof (threshold) returns empty lists — handled separately.
    """
    if strat_def.get("entry_type") == "threshold":
        return [], [], [], []

    tf_key = tf.upper()
    by_tf = strat_def.get("combos_by_tf", {})
    tf_combos = by_tf.get(tf_key) if tf_key in by_tf else None
    combos = tf_combos if tf_combos else strat_def.get("combos", {})

    c3 = combos.get("c3", {})
    c4 = combos.get("c4", {})
    return (
        c3.get("kpis", []),
        c3.get("pols", []),
        c4.get("kpis", []),
        c4.get("pols", []),
    )


def _all_kpis(c3_kpis: list[str], c4_kpis: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for k in c3_kpis + c4_kpis:
        seen[k] = None
    return list(seen)


def _load_universe() -> list[str]:
    """Load scan universe from apps/screener/configs/universe.csv."""
    if not _UNIVERSE_CSV.exists():
        logger.warning("universe.csv not found at %s — run _build_universe.py first", _UNIVERSE_CSV)
        return []
    symbols: list[str] = []
    seen: set[str] = set()
    try:
        with open(_UNIVERSE_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = (row.get("ticker") or row.get("symbol") or "").strip().upper()
                if sym and sym not in seen:
                    symbols.append(sym)
                    seen.add(sym)
    except Exception as exc:
        logger.warning("Could not read universe.csv: %s", exc)
    return symbols


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    df = df_1h.copy()
    df.index = pd.to_datetime(df.index)
    resampled = df.resample("4h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])
    return resampled


def _resample_to_1w(df_1d: pd.DataFrame) -> pd.DataFrame:
    df = df_1d.copy()
    df.index = pd.to_datetime(df.index)
    resampled = df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])
    return resampled


def _resample_to_2w(df_1d: pd.DataFrame) -> pd.DataFrame:
    df = df_1d.copy()
    df.index = pd.to_datetime(df.index)
    resampled = df.resample("2W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"])
    return resampled


def _yf_download_with_retry(**kwargs) -> pd.DataFrame:
    """Mirror of production downloader: retry with exponential backoff on failure."""
    for attempt in range(_MAX_RETRIES):
        try:
            df = yf.download(**kwargs)
            if df is not None and not df.empty:
                return df
        except Exception as exc:
            logger.debug("yf.download raised %s (attempt %d/%d)", type(exc).__name__, attempt + 1, _MAX_RETRIES)
        if attempt < _MAX_RETRIES - 1:
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            logger.debug("Retrying in %ds…", delay)
            time.sleep(delay)
    return pd.DataFrame()


def _download_batch(symbols: list[str], tf: str, period_days: int) -> dict[str, pd.DataFrame]:
    """Download a batch of symbols using the same pattern as the production downloader."""
    import datetime
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeout

    interval = _TF_INTERVAL[tf]
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=period_days)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _yf_download_with_retry,
                tickers=symbols,
                start=str(start_date),
                end=str(end_date),
                interval=interval,
                auto_adjust=False,
                progress=False,
                group_by="column",
                threads=True,
            )
            try:
                raw = future.result(timeout=300)
            except FuturesTimeout:
                logger.warning("Download timed out for batch of %d symbols", len(symbols))
                return {}
    except Exception as exc:
        logger.warning("Download batch failed: %s", exc)
        return {}

    if raw is None or raw.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}

    if not isinstance(raw.columns, pd.MultiIndex):
        # Single ticker returned flat
        if len(symbols) == 1:
            df = _normalize_and_resample(raw, tf)
            if not df.empty:
                result[symbols[0]] = df
        return result

    try:
        available = raw.columns.get_level_values("Ticker").unique()
    except KeyError:
        available = raw.columns.get_level_values(0).unique()

    for sym in available:
        try:
            sym_df = raw.xs(sym, axis=1, level="Ticker", drop_level=True)
            sym_df = _normalize_and_resample(sym_df, tf)
            if not sym_df.empty:
                result[sym] = sym_df
        except Exception:
            continue

    return result


def _normalize_and_resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    df = df[df.index.notna()]
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    if not keep or "Close" not in keep:
        return pd.DataFrame()
    df = df[keep].dropna(subset=["Close"])
    if tf == "4H":
        df = _resample_to_4h(df)
    elif tf == "2W":
        df = _resample_to_2w(df)
    return df


# Keep legacy entry point used below


# ---------------------------------------------------------------------------
# Single-symbol scan
# ---------------------------------------------------------------------------

def _scan_symbol(
    sym: str,
    df: pd.DataFrame,
    c3_kpis: list[str],
    c3_pols: list[int],
    all_kpis_list: list[str],
    scan_filters: dict,
    min_bars: int,
) -> str | None:
    """Return symbol string if it passes C3 onset, else None.

    Returns:
      "c4" if C4 combo onset detected
      "c3" if only C3 onset detected
      None  if no signal
    """
    if len(df) < min_bars:
        return None

    # BUG-11 FIX: quality gate on raw OHLCV before expensive enrichment
    if not check_quality_gates_raw(df, scan_filters):
        return None

    try:
        enriched = compute_scan_indicators(
            df, all_kpis_list, indicator_config_path=_INDICATOR_CONFIG
        )
    except Exception as exc:
        logger.debug("%s enrichment failed: %s", sym, exc)
        return None

    if c3_kpis:
        c3_states = compute_scan_kpi_states(enriched, c3_kpis, c3_pols)
        if detect_c3_onset(c3_states):
            return "c3"

    return None


# ---------------------------------------------------------------------------
# Main scan engine
# ---------------------------------------------------------------------------

def run_scan(
    strategy_key: str,
    tf: str,
    *,
    symbols: list[str] | None = None,
    yield_progress: bool = False,
    _skip_enrich: bool = False,
) -> Generator[dict, None, list[str]]:
    """Run the full scan. If yield_progress=True, yields SSE-compatible dicts.

    Returns list of passing symbols (also written to CSV).
    """
    t0 = time.time()

    # 4H is excluded from scanning
    if tf.upper() == "4H":
        msg = "4H is excluded from scanning (only 1D/1W/2W/1M supported)."
        if yield_progress:
            yield {"type": "error", "msg": msg}
        return []

    # Phase 1 — refresh stale dashboard stocks (TTL-gated)
    enrich_stats: dict = {"enriched": [], "failed": [], "total": 0}
    if not _skip_enrich:
        if yield_progress:
            yield {"type": "progress", "pct": 0, "msg": "Checking dashboard stocks…"}
        enrich_stats = _refresh_dashboard_stocks()
        _e_ok = len(enrich_stats.get("enriched", []))
        _e_fail = len(enrich_stats.get("failed", []))
        if yield_progress:
            if enrich_stats.get("all_fresh"):
                yield {"type": "progress", "pct": 8, "msg": "Dashboard stocks up to date"}
            else:
                _fail_str = f" ({_e_fail} failed)" if _e_fail else ""
                yield {"type": "progress", "pct": 8,
                       "msg": f"Refreshed {_e_ok} stocks{_fail_str}"}

    _dl_pct_base = 8 if not _skip_enrich else 0
    _dl_pct_range = 32 if not _skip_enrich else 40

    config = _load_config()
    strat_def = _get_strategy_def(config, strategy_key)

    # ── Threshold (stoof) path ───────────────────────────────────────────────
    if strat_def.get("entry_type") == "threshold":
        active_tfs = [t.upper() for t in strat_def.get("active_tfs", [])]
        if tf.upper() not in active_tfs:
            msg = f"Strategy '{strategy_key}' is not active on TF '{tf}' (active: {active_tfs})."
            if yield_progress:
                yield {"type": "error", "msg": msg}
            return []

        from trading_dashboard.indicators.registry import get_kpi_trend_order as _gkto
        stoof_kpis = _gkto("stoof")
        required_kpi = strat_def.get("required_kpi", "MACD_BL")
        c4_kpi = strat_def.get("c4_kpi", "WT_MTF")
        threshold = strat_def.get("threshold", 5)
        period_days = _TF_DOWNLOAD_DAYS.get(tf, 1500)
        universe = symbols if symbols is not None else _load_universe()

        if not universe:
            if yield_progress:
                yield {"type": "error", "msg": "No symbols in universe."}
            return []

        total = len(universe)
        if yield_progress:
            yield {"type": "progress", "pct": 0, "msg": f"Scanning {total} symbols ({strategy_key}/{tf})…"}

        batches = [universe[i:i + _BATCH_SIZE] for i in range(0, total, _BATCH_SIZE)]
        downloaded: dict[str, pd.DataFrame] = {}
        completed_batches = 0

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(_download_batch, b, tf, period_days): b for b in batches}
            for fut in as_completed(futures):
                downloaded.update(fut.result())
                completed_batches += 1
                time.sleep(_BATCH_DELAY)
                pct = int(completed_batches / len(batches) * 40)
                if yield_progress:
                    yield {"type": "progress", "pct": pct, "msg": f"Downloaded {len(downloaded)}/{total}…"}

        if yield_progress:
            yield {"type": "progress", "pct": 40, "msg": f"Pre-filtering {len(downloaded)} symbols (MACD_BL)…"}

        # Lean pre-filter: MACD_BL must be green (necessary condition for stoof C3)
        candidates: list[str] = []
        for idx, (sym, df) in enumerate(downloaded.items()):
            if len(df) < 50:
                continue
            try:
                lean = compute_scan_indicators(df, ["MACD_BL"], indicator_config_path=_INDICATOR_CONFIG)
                macd_states = compute_scan_kpi_states(lean, ["MACD_BL"], [1])
                if bool(macd_states.iloc[-1]):
                    candidates.append(sym)
            except Exception:
                pass
            if yield_progress and idx % 50 == 0:
                pct = 40 + int(idx / max(len(downloaded), 1) * 50)
                yield {"type": "progress", "pct": pct, "msg": f"Pre-filtered {idx+1}/{len(downloaded)} — {len(candidates)} candidates…"}

        if yield_progress:
            yield {"type": "progress", "pct": 91, "msg": f"{len(candidates)} candidates — full enrichment + stoof validation…"}

        _enrich_new_symbols(candidates)
        validated = _validate_stoof_on_enriched(candidates, strat_def, tf)
        _write_strategy_csv(strategy_key, validated, tf)
        elapsed = time.time() - t0
        _trigger_dashboard_refresh()

        if yield_progress:
            yield {"type": "done", "count": len(validated), "elapsed": round(elapsed, 1)}
        return validated

    # ── Polarity-combo path ──────────────────────────────────────────────────
    c3_kpis, c3_pols, _, _ = _get_combos(strat_def, tf)
    if not c3_kpis:
        msg = f"No C3 combos defined for strategy '{strategy_key}' on TF '{tf}'."
        if yield_progress:
            yield {"type": "error", "msg": msg}
        return []

    all_kpis_list = list(dict.fromkeys(c3_kpis))  # deduplicated C3 KPIs only
    scan_filters = strat_def.get("scan_filters", {})
    min_bars = min_bars_for_combo(all_kpis_list)
    period_days = _TF_DOWNLOAD_DAYS.get(tf, 450)

    universe = symbols if symbols is not None else _load_universe()
    if not universe:
        msg = "No symbols found in universe."
        if yield_progress:
            yield {"type": "error", "msg": msg}
        return []

    total = len(universe)
    if yield_progress:
        yield {"type": "progress", "pct": 0, "msg": f"Scanning {total} symbols ({strategy_key}/{tf})…"}

    # Split into batches
    batches = [universe[i : i + _BATCH_SIZE] for i in range(0, total, _BATCH_SIZE)]
    downloaded: dict[str, pd.DataFrame] = {}
    completed_batches = 0

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_download_batch, b, tf, period_days): b for b in batches}
        for fut in as_completed(futures):
            batch_result = fut.result()
            downloaded.update(batch_result)
            completed_batches += 1
            time.sleep(_BATCH_DELAY)
            pct = _dl_pct_base + int(completed_batches / len(batches) * _dl_pct_range)
            if yield_progress:
                yield {
                    "type": "progress",
                    "pct": pct,
                    "msg": f"Downloaded {len(downloaded)}/{total} symbols…",
                }

    if yield_progress:
        yield {"type": "progress", "pct": 40, "msg": f"Enriching and scanning {len(downloaded)} symbols…"}

    # Sequential enrichment + scan (CPU-bound, GIL prevents true parallelism)
    passing: list[str] = []
    for idx, (sym, df) in enumerate(downloaded.items()):
        result = _scan_symbol(
            sym, df,
            c3_kpis, c3_pols,
            all_kpis_list,
            scan_filters,
            min_bars,
        )
        if result:
            passing.append(sym)

        if yield_progress and idx % 50 == 0:
            pct = 40 + int(idx / max(len(downloaded), 1) * 55)
            yield {
                "type": "progress",
                "pct": pct,
                "msg": f"Scanned {idx + 1}/{len(downloaded)} — {len(passing)} signals so far…",
            }

    if yield_progress:
        yield {
            "type": "progress",
            "pct": 96,
            "msg": f"{len(passing)} lean candidates — enriching and re-validating…",
        }

    # Step 1: Enrich any candidates not yet in the feature store
    _enrich_new_symbols(passing)

    # Step 2: Re-validate C3 onset on real enriched data
    validated = _validate_c3_on_enriched(passing, c3_kpis, c3_pols, tf)
    _raw_passed = len(validated)

    # Step 3: Filter out dashboard stocks already in an open position
    validated = _filter_open_positions(validated, strat_def, tf)
    _filtered_open = _raw_passed - len(validated)

    # Step 4: Write final CSV with only confirmed signals
    _write_strategy_csv(strategy_key, validated, tf)
    elapsed = time.time() - t0

    # Step 5: Trigger dashboard refresh
    _trigger_dashboard_refresh()

    if yield_progress:
        yield {
            "type": "done",
            "count": len(validated),
            "elapsed": round(elapsed, 1),
            "raw_passed": _raw_passed,
            "filtered_open": _filtered_open,
            "enriched_ok": len(enrich_stats.get("enriched", [])),
            "enriched_fail": len(enrich_stats.get("failed", [])),
            "enriched_total": enrich_stats.get("total", 0),
        }

    return validated


_SCAN_LIST_CSV = _LISTS_DIR / "scan_list.csv"


def _write_scan_list(symbols: list[str], prev_dates: dict[str, str] | None = None) -> None:
    """Write (or replace) configs/lists/scan_list.csv with ticker,date_added columns.

    New symbols get today's date. Existing symbols preserve their original date_added.
    """
    import datetime
    today = datetime.date.today().isoformat()
    _LISTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_SCAN_LIST_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker", "date_added"])
        for sym in sorted(symbols):
            date = (prev_dates or {}).get(sym, today)
            writer.writerow([sym, date])
    logger.info("Wrote %d symbols to scan_list.csv", len(symbols))


def _load_scan_list() -> dict[str, str]:
    """Return ticker → date_added map from scan_list.csv."""
    if not _SCAN_LIST_CSV.exists():
        return {}
    try:
        result: dict[str, str] = {}
        with open(_SCAN_LIST_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row.get("ticker", "").strip().upper()
                if ticker:
                    result[ticker] = row.get("date_added", "")
        return result
    except Exception:
        return {}


# Legacy alias kept for single-strategy scan path (writes to scan_list.csv)
def _write_strategy_csv(strategy_key: str, symbols: list[str], tf: str) -> None:
    """Append confirmed symbols to scan_list.csv (single-strategy scan path)."""
    prev = _load_scan_list()
    merged = sorted(set(prev.keys()) | set(symbols))
    _write_scan_list(merged, prev_dates=prev)


def _load_strategy_csv(strategy_key: str) -> list[str]:
    """Return current scan_list.csv contents (strategy_key ignored)."""
    return list(_load_scan_list().keys())


def _enrich_new_symbols(symbols: list[str]) -> None:
    """Enrich symbols that have no feature store data yet (synchronous, no refresh)."""
    from apps.dashboard.config_loader import load_build_config, resolve_paths
    cfg = load_build_config()
    paths = resolve_paths(cfg)
    stock_data_dir = paths.output_stock_data_dir

    if stock_data_dir.is_dir():
        existing = set(f.stem.split("_")[0] for f in stock_data_dir.glob("*.parquet"))
        new_tickers = [s for s in symbols if s not in existing]
    else:
        new_tickers = list(symbols)

    if new_tickers:
        logger.info("Enriching %d new tickers: %s", len(new_tickers), new_tickers[:10])
        try:
            from apps.dashboard.build_dashboard import enrich_symbols
            enrich_symbols(new_tickers)
        except Exception as exc:
            logger.warning("enrich_symbols failed: %s", exc)


_SCAN_REFRESH_TTL_HOURS: float = 4.0  # skip symbols enriched within this window


def _refresh_dashboard_stocks() -> dict:
    """Phase 1 of scan: incrementally re-enrich stale dashboard stocks.

    Skips symbols whose 1D enriched parquet is already fresh
    (< _SCAN_REFRESH_TTL_HOURS old) to avoid redundant downloads on
    back-to-back scans.  Downloads and recomputes indicators only for
    symbols whose data is actually stale.
    Returns {"enriched": [...], "failed": [...], "total": N}.
    """
    import time as _t

    from apps.dashboard.build_dashboard import enrich_symbols
    from apps.dashboard.config_loader import load_build_config, resolve_paths

    try:
        cfg = load_build_config()
        symbols = list(cfg.symbols)
        paths = resolve_paths(cfg)
    except Exception as exc:
        logger.warning("Could not load dashboard symbols for pre-scan refresh: %s", exc)
        return {"enriched": [], "failed": [], "total": 0}

    if not symbols:
        return {"enriched": [], "failed": [], "total": 0}

    # Filter to symbols whose 1D enriched parquet is missing or stale.
    stock_data_dir = paths.output_stock_data_dir
    now = _t.time()
    stale: list[str] = []
    for sym in symbols:
        p = stock_data_dir / f"{sym}_1D.parquet"
        if not p.exists() or (now - p.stat().st_mtime) / 3600.0 > _SCAN_REFRESH_TTL_HOURS:
            stale.append(sym)

    if not stale:
        logger.info(
            "Phase 1: all %d dashboard stocks are fresh (< %.0fh), skipping refresh",
            len(symbols), _SCAN_REFRESH_TTL_HOURS,
        )
        return {"enriched": [], "failed": [], "total": 0, "all_fresh": True}

    logger.info(
        "Phase 1: refreshing %d/%d stale dashboard stocks before scan…",
        len(stale), len(symbols),
    )
    try:
        result = enrich_symbols(stale)
        result["total"] = len(symbols)
        return result
    except Exception as exc:
        logger.warning("Dashboard stock refresh failed: %s", exc)
        return {"enriched": [], "failed": list(stale), "total": len(symbols)}


def _filter_open_positions(
    symbols: list[str],
    strat_def: dict,
    tf: str,
) -> list[str]:
    """Remove ALL dashboard stocks that already have an open position for this strategy/TF.

    Applies to both polarity_combo and threshold (stoof) strategies. Symbols not
    present in the dashboard symbol list pass through — they are implicitly FLAT.
    """
    entry_type = strat_def.get("entry_type")

    try:
        from apps.dashboard.config_loader import load_build_config, resolve_paths
        from trading_dashboard.kpis.catalog import compute_kpi_state_map

        cfg = load_build_config()
        paths = resolve_paths(cfg)
        dashboard_syms = set(cfg.symbols)
        stock_data_dir = paths.output_stock_data_dir
    except Exception as exc:
        logger.warning("Position filter skipped (config load failed): %s", exc)
        return symbols

    result: list[str] = []
    for sym in symbols:
        if sym not in dashboard_syms:
            result.append(sym)
            continue
        parquet_path = stock_data_dir / f"{sym}_{tf}.parquet"
        if not parquet_path.exists():
            result.append(sym)
            continue
        try:
            df = pd.read_parquet(parquet_path)
            st = compute_kpi_state_map(df)
            if entry_type == "polarity_combo":
                from apps.dashboard.strategy import compute_polarity_position_status
                ps = compute_polarity_position_status(df, st, strat_def, tf)
                is_open = ps["signal_action"] != "FLAT"
            elif entry_type == "threshold":
                from apps.dashboard.strategy import compute_stoof_position_status
                from trading_dashboard.indicators.registry import get_kpi_trend_order as _gkto
                stoof_kpis = _gkto("stoof")
                ps = compute_stoof_position_status(
                    df, st, stoof_kpis,
                    strat_def.get("threshold", 5), tf,
                    required_kpi=strat_def.get("required_kpi", "MACD_BL"),
                    c4_kpi=strat_def.get("c4_kpi", "WT_MTF"),
                )
                is_open = ps["signal_action"] != "FLAT"
            else:
                result.append(sym)
                continue
            if not is_open:
                result.append(sym)
            else:
                logger.debug(
                    "%s: already in open position (%s) — excluded from scan results",
                    sym, ps["signal_action"],
                )
        except Exception as exc:
            logger.debug("%s: position check failed (%s) — including", sym, exc)
            result.append(sym)
    return result


def _validate_c3_on_enriched(
    symbols: list[str],
    c3_kpis: list[str],
    c3_pols: list[int],
    tf: str,
) -> list[str]:
    """Re-validate C3 onset on fully-enriched Parquet data.

    Returns only symbols where C3 onset is confirmed using real indicators.
    """
    from apps.dashboard.config_loader import load_build_config, resolve_paths
    from trading_dashboard.kpis.catalog import compute_kpi_state_map

    cfg = load_build_config()
    paths = resolve_paths(cfg)
    stock_data_dir = paths.output_stock_data_dir

    validated = []
    for sym in symbols:
        parquet_path = stock_data_dir / f"{sym}_{tf}.parquet"
        if not parquet_path.exists():
            logger.debug("%s: no enriched parquet — skipping", sym)
            continue
        try:
            df = pd.read_parquet(parquet_path)
            state_map = compute_kpi_state_map(df)

            combo = pd.Series(True, index=df.index)
            for kpi, pol in zip(c3_kpis, c3_pols):
                states = state_map.get(kpi)
                if states is None:
                    combo[:] = False
                    break
                if pol == 1:
                    combo &= states == 1
                elif pol == -1:
                    combo &= states == -1

            if detect_c3_onset(combo):
                validated.append(sym)
                logger.debug("%s: C3 onset confirmed on real data", sym)
            else:
                logger.debug("%s: C3 onset not confirmed on real data — removed", sym)
        except Exception as exc:
            logger.debug("%s: validation failed: %s", sym, exc)

    logger.info("C3 re-validation: %d/%d confirmed on real data", len(validated), len(symbols))
    return validated


def _validate_stoof_on_enriched(
    symbols: list[str],
    stoof_def: dict,
    tf: str,
) -> list[str]:
    """Re-validate stoof C3 onset on fully-enriched Parquet data.

    C3 = required_kpi (MACD_BL) bull AND score (count of bull score_kpis) >= threshold.
    Onset = C3 true on last bar AND was false at some point in prior 2 bars.
    Returns only symbols where stoof C3 onset is confirmed.
    """
    from apps.dashboard.config_loader import load_build_config, resolve_paths
    from trading_dashboard.indicators.registry import get_kpi_trend_order as _gkto
    from trading_dashboard.kpis.catalog import compute_kpi_state_map

    required_kpi = stoof_def.get("required_kpi", "MACD_BL")
    c4_kpi = stoof_def.get("c4_kpi", "WT_MTF")
    threshold = stoof_def.get("threshold", 5)
    stoof_kpis = _gkto("stoof")
    score_kpis = [k for k in stoof_kpis if k not in {required_kpi, c4_kpi}]

    cfg = load_build_config()
    paths = resolve_paths(cfg)
    stock_data_dir = paths.output_stock_data_dir

    validated = []
    for sym in symbols:
        parquet_path = stock_data_dir / f"{sym}_{tf}.parquet"
        if not parquet_path.exists():
            logger.debug("%s: no enriched parquet for %s — skipping", sym, tf)
            continue
        try:
            df = pd.read_parquet(parquet_path)
            state_map = compute_kpi_state_map(df)

            req_states = state_map.get(required_kpi, pd.Series(0, index=df.index, dtype=int))
            score = sum(
                (state_map.get(k, pd.Series(0, index=df.index, dtype=int)) == 1).astype(int)
                for k in score_kpis
            )
            c3 = (req_states == 1) & (score >= threshold)

            if detect_c3_onset(c3):
                validated.append(sym)
                logger.debug("%s: stoof C3 onset confirmed on %s enriched data", sym, tf)
            else:
                logger.debug("%s: stoof C3 not confirmed on %s enriched data — removed", sym, tf)
        except Exception as exc:
            logger.debug("%s: stoof validation failed: %s", sym, exc)

    logger.info("Stoof C3 re-validation (%s): %d/%d confirmed", tf, len(validated), len(symbols))
    return validated


def _trigger_dashboard_refresh() -> None:
    """Fire-and-forget background dashboard refresh."""
    try:
        subprocess.Popen(
            [sys.executable, "-m", "trading_dashboard", "dashboard", "refresh"],
            cwd=str(_REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Dashboard refresh subprocess started.")
    except Exception as exc:
        logger.warning("Could not trigger dashboard refresh: %s", exc)


# ---------------------------------------------------------------------------
# All-strategy scan (single TF — one download, all strategies)
# ---------------------------------------------------------------------------

def _get_artifacts_dir() -> Path:
    """Return DASHBOARD_ARTIFACTS_DIR, respecting TRADING_APP_ROOT env var."""
    try:
        from apps.dashboard.config_loader import DASHBOARD_ARTIFACTS_DIR
        return DASHBOARD_ARTIFACTS_DIR
    except Exception:
        return _REPO_ROOT / "data" / "dashboard_artifacts"



def _append_scan_log(
    tf: str,
    validated: dict[str, list[str]],
    prev_list: list[str],
    raw_passed: int = 0,
    filtered_open: int = 0,
) -> None:
    """Append one JSONL entry for the unified scan_list to scan_log.jsonl."""
    import datetime
    ts = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    artifacts_dir = _get_artifacts_dir()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    log_path = artifacts_dir / "scan_log.jsonl"
    prev = set(prev_list)
    all_confirmed: set[str] = set()
    for syms in validated.values():
        all_confirmed.update(syms)
    entry = {
        "ts": ts,
        "tf": tf,
        "strategy": "scan_list",
        "by_strategy": {k: len(v) for k, v in validated.items()},
        "added": sorted(all_confirmed - prev),
        "removed": sorted(prev - all_confirmed),
        "total": len(all_confirmed),
        "raw_passed": raw_passed,
        "filtered_open": filtered_open,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    logger.info("Scan log appended: %s %s — %d symbols total (%d raw, %d filtered open)",
                tf, ts, len(all_confirmed), raw_passed, filtered_open)


def _scan_symbol_all_strategies(
    sym: str,
    df: pd.DataFrame,
    strategy_defs: list[dict],
    global_kpis: list[str],
) -> dict[str, str | None]:
    """Enrich once, evaluate all strategies. Returns {key: 'c3'|None}.

    BUG-11 FIX: quality gate is checked on raw OHLCV first (per strategy),
    then enrichment happens only for strategies that passed.
    """
    max_min_bars = max((s["min_bars"] for s in strategy_defs), default=50)
    if len(df) < max_min_bars:
        return {s["key"]: None for s in strategy_defs}

    # Per-strategy quality gate on raw OHLCV before enrichment
    passing_strats = [s for s in strategy_defs if check_quality_gates_raw(df, s["scan_filters"])]
    if not passing_strats:
        return {s["key"]: None for s in strategy_defs}

    passing_kpis = list(dict.fromkeys(kpi for s in passing_strats for kpi in s["c3_kpis"]))
    try:
        enriched = compute_scan_indicators(df, passing_kpis, indicator_config_path=_INDICATOR_CONFIG)
    except Exception as exc:
        logger.debug("%s enrichment failed: %s", sym, exc)
        return {s["key"]: None for s in strategy_defs}

    results: dict[str, str | None] = {s["key"]: None for s in strategy_defs}
    for strat in passing_strats:
        c3_kpis, c3_pols = strat["c3_kpis"], strat["c3_pols"]
        if c3_kpis:
            c3_states = compute_scan_kpi_states(enriched, c3_kpis, c3_pols)
            if detect_c3_onset(c3_states):
                results[strat["key"]] = "c3"
    return results


def _scan_symbol_stoof(
    sym: str,
    df: pd.DataFrame,
    min_bars: int = 50,
) -> bool:
    """Lean stoof pre-filter: returns True if MACD_BL is green on last bar.

    MACD_BL being green is a necessary (but not sufficient) condition for stoof C3.
    Symbols passing here are later fully validated on enriched data.
    """
    if len(df) < min_bars:
        return False
    try:
        lean = compute_scan_indicators(df, ["MACD_BL"], indicator_config_path=_INDICATOR_CONFIG)
        macd_states = compute_scan_kpi_states(lean, ["MACD_BL"], [1])
        return bool(macd_states.iloc[-1])
    except Exception as exc:
        logger.debug("%s stoof pre-filter failed: %s", sym, exc)
        return False


def run_scan_all_strategies(
    tf: str,
    *,
    symbols: list[str] | None = None,
    yield_progress: bool = False,
    _skip_refresh: bool = False,
    _skip_enrich: bool = False,
    _skip_write: bool = False,
    _prev_scan_dates: dict[str, str] | None = None,
) -> Generator[dict, None, dict[str, list[str]]]:
    """Download OHLCV once for *tf*, check ALL strategies in a single enrichment pass.

    Yields SSE-compatible dicts when yield_progress=True.
    Returns {strategy_key: [validated_symbols]}.

    _skip_write: if True, do not write scan_list.csv (caller handles it).
    _prev_scan_dates: pre-loaded scan_list snapshot; avoids re-reading from disk
        and ensures the "added/removed" diff in scan_log uses the correct baseline
        when called from run_scan_all_tf (which writes scan_list only at the end).
    """
    # 4H is excluded from scanning
    if tf.upper() == "4H":
        if yield_progress:
            yield {"type": "error", "msg": "4H is excluded from scanning."}
        return {}

    t0 = time.time()

    # Phase 1 — refresh stale dashboard stocks (TTL-gated)
    enrich_stats: dict = {"enriched": [], "failed": [], "total": 0}
    if not _skip_enrich:
        if yield_progress:
            yield {"type": "progress", "pct": 0, "msg": "Checking dashboard stocks…"}
        enrich_stats = _refresh_dashboard_stocks()
        _e_ok = len(enrich_stats.get("enriched", []))
        _e_fail = len(enrich_stats.get("failed", []))
        if yield_progress:
            if enrich_stats.get("all_fresh"):
                yield {"type": "progress", "pct": 8, "msg": "Dashboard stocks up to date"}
            else:
                _fail_str = f" ({_e_fail} failed)" if _e_fail else ""
                yield {"type": "progress", "pct": 8,
                       "msg": f"Refreshed {_e_ok} stocks{_fail_str}"}

    _dl_pct_base = 8 if not _skip_enrich else 0
    _dl_pct_range = 32 if not _skip_enrich else 40

    config = _load_config()
    all_setups = config.get("strategy_setups", {})

    # Build polarity-combo strategy defs for this TF
    strategy_defs: list[dict] = []
    for key, strat_def in all_setups.items():
        if strat_def.get("entry_type") == "threshold":
            continue
        c3_kpis, c3_pols, _, _ = _get_combos(strat_def, tf)
        if not c3_kpis:
            continue
        strategy_defs.append({
            "key": key,
            "c3_kpis": c3_kpis,
            "c3_pols": c3_pols,
            "scan_filters": strat_def.get("scan_filters", {}),
            "min_bars": min_bars_for_combo(c3_kpis),
            "strat_def": strat_def,
        })

    # Build threshold (stoof) strategy defs for this TF
    stoof_defs: list[dict] = []
    for key, strat_def in all_setups.items():
        if strat_def.get("entry_type") != "threshold":
            continue
        active_tfs = [t.upper() for t in strat_def.get("active_tfs", [])]
        if tf.upper() not in active_tfs:
            continue
        stoof_defs.append({"key": key, "strat_def": strat_def})

    if not strategy_defs and not stoof_defs:
        msg = f"No strategies defined for TF '{tf}'."
        if yield_progress:
            yield {"type": "error", "msg": msg}
        return {}

    # Union of all combo KPIs — enrich once per symbol
    global_kpis = list(dict.fromkeys(kpi for s in strategy_defs for kpi in s["c3_kpis"]))
    all_strat_names = [s["key"] for s in strategy_defs] + [s["key"] for s in stoof_defs]
    strat_names = ", ".join(all_strat_names)
    period_days = _TF_DOWNLOAD_DAYS.get(tf, 450)
    universe = symbols if symbols is not None else _load_universe()

    if not universe:
        if yield_progress:
            yield {"type": "error", "msg": "No symbols in universe."}
        return {}

    total = len(universe)
    if yield_progress:
        yield {"type": "progress", "pct": _dl_pct_base, "msg": f"Scanning {total} symbols on {tf} [{strat_names}]…"}

    # ── Download once ──────────────────────────────────────────────────────
    batches = [universe[i:i + _BATCH_SIZE] for i in range(0, total, _BATCH_SIZE)]
    downloaded: dict[str, pd.DataFrame] = {}
    completed_batches = 0

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_download_batch, b, tf, period_days): b for b in batches}
        for fut in as_completed(futures):
            downloaded.update(fut.result())
            completed_batches += 1
            time.sleep(_BATCH_DELAY)
            pct = _dl_pct_base + int(completed_batches / len(batches) * _dl_pct_range)
            if yield_progress:
                yield {"type": "progress", "pct": pct, "msg": f"Downloaded {len(downloaded)}/{total}…"}

    if yield_progress:
        yield {"type": "progress", "pct": 40, "msg": f"Enriching {len(downloaded)} symbols for {len(all_strat_names)} strategies…"}

    # ── Multi-strategy combo scan (BUG-11 fixed: quality gate before enrichment) ──
    passing: dict[str, list[str]] = {s["key"]: [] for s in strategy_defs}
    stoof_candidates: dict[str, list[str]] = {s["key"]: [] for s in stoof_defs}

    for idx, (sym, df) in enumerate(downloaded.items()):
        # Polarity-combo strategies
        if strategy_defs:
            sym_results = _scan_symbol_all_strategies(sym, df, strategy_defs, global_kpis)
            for key, result in sym_results.items():
                if result:
                    passing[key].append(sym)

        # Stoof pre-filter (MACD_BL necessary condition)
        if stoof_defs:
            macd_ok = _scan_symbol_stoof(sym, df)
            if macd_ok:
                for sd in stoof_defs:
                    stoof_candidates[sd["key"]].append(sym)

        if yield_progress and idx % 50 == 0:
            pct = 40 + int(idx / max(len(downloaded), 1) * 50)
            total_sigs = sum(len(v) for v in passing.values()) + sum(len(v) for v in stoof_candidates.values())
            yield {"type": "progress", "pct": pct, "msg": f"Scanned {idx+1}/{len(downloaded)} — {total_sigs} signals…"}

    if yield_progress:
        yield {"type": "progress", "pct": 91, "msg": "Re-validating candidates on enriched data…"}

    # ── Validate combo strategies ─────────────────────────────────────────
    # Use caller-supplied snapshot (from run_scan_all_tf) so the log diff reflects
    # the pre-run state, not whatever a previous TF already wrote.
    prev_dates = _prev_scan_dates if _prev_scan_dates is not None else _load_scan_list()
    prev_list = list(prev_dates.keys())
    validated: dict[str, list[str]] = {}
    _raw_passed = 0
    _filtered_open = 0

    for strat in strategy_defs:
        key = strat["key"]
        _enrich_new_symbols(passing[key])
        confirmed = _validate_c3_on_enriched(passing[key], strat["c3_kpis"], strat["c3_pols"], tf)
        raw = len(confirmed)
        confirmed = _filter_open_positions(confirmed, strat["strat_def"], tf)
        _raw_passed += raw
        _filtered_open += raw - len(confirmed)
        validated[key] = confirmed
        if yield_progress:
            yield {"type": "progress", "pct": 93, "msg": f"{key}: {len(confirmed)} confirmed ({raw - len(confirmed)} open pos filtered)"}

    # ── Validate stoof strategies ─────────────────────────────────────────
    for sd in stoof_defs:
        key = sd["key"]
        _enrich_new_symbols(stoof_candidates[key])
        confirmed = _validate_stoof_on_enriched(stoof_candidates[key], sd["strat_def"], tf)
        raw = len(confirmed)
        confirmed = _filter_open_positions(confirmed, sd["strat_def"], tf)
        _raw_passed += raw
        _filtered_open += raw - len(confirmed)
        validated[key] = confirmed
        if yield_progress:
            yield {"type": "progress", "pct": 94, "msg": f"{key}: {len(confirmed)} confirmed ({raw - len(confirmed)} open pos filtered)"}

    # ── Write union to scan_list.csv, log ────────────────────────────────
    all_confirmed: set[str] = set()
    for syms in validated.values():
        all_confirmed.update(syms)

    if not _skip_write:
        _write_scan_list(sorted(all_confirmed), prev_dates=prev_dates)

    _append_scan_log(tf, validated, prev_list, raw_passed=_raw_passed, filtered_open=_filtered_open)

    if not _skip_refresh:
        _trigger_dashboard_refresh()

    elapsed = time.time() - t0
    total_sigs = sum(len(v) for v in validated.values())
    if yield_progress:
        # BUG-13 FIX: include actual results in done event so run_scan_all_tf can read them
        yield {
            "type": "done",
            "count": total_sigs,
            "elapsed": round(elapsed, 1),
            "by_strategy": {k: len(v) for k, v in validated.items()},
            "results": validated,
            "raw_passed": _raw_passed,
            "filtered_open": _filtered_open,
            "enriched_ok": len(enrich_stats.get("enriched", [])),
            "enriched_fail": len(enrich_stats.get("failed", [])),
            "enriched_total": enrich_stats.get("total", 0),
        }
    return validated


# History depths for the hybrid all-TF download.
# Daily covers 1D, 1W (resample), 2W (resample).  Monthly is native for 1M.
# 3650 daily days  → ~320 1D bars, ~521 1W bars, ~260 2W bars  (fixes 1W for 202-bar KPIs)
# 7300 monthly days → ~240 1M bars  (satisfies 235-bar Madrid Ribbon / 202-bar cRSI)
_HYBRID_DAILY_DAYS = 3650
_HYBRID_MONTHLY_DAYS = 7300


def run_scan_all_tf(
    *,
    symbols: list[str] | None = None,
    yield_progress: bool = False,
) -> Generator[dict, None, dict[str, dict[str, list[str]]]]:
    """Hybrid all-TF scan: 2 downloads per batch instead of 4.

    Each batch of symbols is downloaded ONCE as daily (3650 days) and ONCE as
    native monthly (7300 days).  1D, 1W and 2W are derived from the daily data
    via in-memory resampling; 1M uses the native monthly bars.

    This gives:
      1D  ~320 bars   (sufficient for all KPIs)
      1W  ~521 weeks  (was 100 — fixes swing/trend which need 202 bars)
      2W  ~260 bars   (sufficient for all KPIs)
      1M  ~240 months (was 120 — fixes swing/trend which need 202 bars)

    Yields SSE-compatible progress dicts when yield_progress=True.
    Returns {tf: {strategy_key: [validated_symbols]}}.
    """
    t0 = time.time()
    tfs = ["1D", "1W", "2W", "1M"]

    # ── Phase 1: TTL-gated dashboard refresh ──────────────────────────────
    enrich_stats: dict = {"enriched": [], "failed": [], "total": 0}
    if yield_progress:
        yield {"type": "progress", "pct": 0, "msg": "Checking dashboard stocks…"}
    enrich_stats = _refresh_dashboard_stocks()
    _e_ok = len(enrich_stats.get("enriched", []))
    _e_fail = len(enrich_stats.get("failed", []))
    if yield_progress:
        if enrich_stats.get("all_fresh"):
            yield {"type": "progress", "pct": 3, "msg": "Dashboard stocks up to date"}
        else:
            _fail_str = f" ({_e_fail} failed)" if _e_fail else ""
            yield {"type": "progress", "pct": 3, "msg": f"Refreshed {_e_ok} stocks{_fail_str}"}

    # ── Build strategy defs per TF ────────────────────────────────────────
    config = _load_config()
    all_setups = config.get("strategy_setups", {})

    tf_strategy_defs: dict[str, list[dict]] = {tf: [] for tf in tfs}
    tf_stoof_defs: dict[str, list[dict]] = {tf: [] for tf in tfs}

    for key, strat_def in all_setups.items():
        if strat_def.get("entry_type") == "threshold":
            active_tfs = [t.upper() for t in strat_def.get("active_tfs", [])]
            for tf in tfs:
                if tf in active_tfs:
                    tf_stoof_defs[tf].append({"key": key, "strat_def": strat_def})
        else:
            for tf in tfs:
                c3_kpis, c3_pols, _, _ = _get_combos(strat_def, tf)
                if c3_kpis:
                    tf_strategy_defs[tf].append({
                        "key": key,
                        "c3_kpis": c3_kpis,
                        "c3_pols": c3_pols,
                        "scan_filters": strat_def.get("scan_filters", {}),
                        "min_bars": min_bars_for_combo(c3_kpis),
                        "strat_def": strat_def,
                    })

    # ── Universe ──────────────────────────────────────────────────────────
    universe = symbols if symbols is not None else _load_universe()
    if not universe:
        if yield_progress:
            yield {"type": "error", "msg": "No symbols in universe."}
        return {}

    total = len(universe)
    batches = [universe[i:i + _BATCH_SIZE] for i in range(0, total, _BATCH_SIZE)]

    # Accumulators: {tf: {strategy_key: [sym, ...]}}
    passing: dict[str, dict[str, list[str]]] = {
        tf: {s["key"]: [] for s in tf_strategy_defs[tf]} for tf in tfs
    }
    stoof_cands: dict[str, dict[str, list[str]]] = {
        tf: {s["key"]: [] for s in tf_stoof_defs[tf]} for tf in tfs
    }

    # ── Hybrid download + lean scan ───────────────────────────────────────
    if yield_progress:
        yield {"type": "progress", "pct": 5,
               "msg": f"Scanning {total} symbols on all TFs (hybrid download)…"}

    downloaded_count = 0
    for batch in batches:
        daily = _download_batch(batch, "1D", _HYBRID_DAILY_DAYS)
        monthly = _download_batch(batch, "1M", _HYBRID_MONTHLY_DAYS)

        for sym in batch:
            daily_df = daily.get(sym, pd.DataFrame())
            monthly_df = monthly.get(sym, pd.DataFrame())

            # Derive all TFs from the two downloads
            tf_data: dict[str, pd.DataFrame] = {}
            if not daily_df.empty:
                tf_data["1D"] = daily_df
                w = _resample_to_1w(daily_df)
                if not w.empty:
                    tf_data["1W"] = w
                bw = _resample_to_2w(daily_df)
                if not bw.empty:
                    tf_data["2W"] = bw
            if not monthly_df.empty:
                tf_data["1M"] = monthly_df

            for tf, df in tf_data.items():
                strats = tf_strategy_defs.get(tf, [])
                stoofs = tf_stoof_defs.get(tf, [])
                if strats:
                    results = _scan_symbol_all_strategies(sym, df, strats, [])
                    for key, result in results.items():
                        if result:
                            passing[tf][key].append(sym)
                if stoofs and _scan_symbol_stoof(sym, df):
                    for sd in stoofs:
                        stoof_cands[tf][sd["key"]].append(sym)

        downloaded_count += len(batch)
        time.sleep(_BATCH_DELAY)
        pct = 5 + int(downloaded_count / total * 68)
        if yield_progress:
            raw_sigs = sum(len(v) for td in passing.values() for v in td.values())
            yield {"type": "progress", "pct": pct,
                   "msg": f"Downloaded {downloaded_count}/{total} — {raw_sigs} raw signals…"}

    # ── Validation: full enrich + C3 confirm + open-pos filter ───────────
    if yield_progress:
        yield {"type": "progress", "pct": 74, "msg": "Validating candidates on enriched data…"}

    prev_scan_dates = _load_scan_list()
    prev_list = list(prev_scan_dates.keys())
    all_results: dict[str, dict[str, list[str]]] = {tf: {} for tf in tfs}
    _total_raw = 0
    _total_filtered = 0

    for tf in tfs:
        validated_tf: dict[str, list[str]] = {}
        tf_raw = 0
        tf_filtered = 0

        for strat in tf_strategy_defs[tf]:
            key = strat["key"]
            _enrich_new_symbols(passing[tf].get(key, []))
            confirmed = _validate_c3_on_enriched(
                passing[tf].get(key, []), strat["c3_kpis"], strat["c3_pols"], tf)
            raw = len(confirmed)
            confirmed = _filter_open_positions(confirmed, strat["strat_def"], tf)
            validated_tf[key] = confirmed
            tf_raw += raw
            tf_filtered += raw - len(confirmed)
            if yield_progress:
                yield {"type": "progress", "pct": 76,
                       "msg": f"{tf}/{key}: {len(confirmed)} confirmed ({raw - len(confirmed)} filtered)"}

        for sd in tf_stoof_defs[tf]:
            key = sd["key"]
            _enrich_new_symbols(stoof_cands[tf].get(key, []))
            confirmed = _validate_stoof_on_enriched(
                stoof_cands[tf].get(key, []), sd["strat_def"], tf)
            raw = len(confirmed)
            confirmed = _filter_open_positions(confirmed, sd["strat_def"], tf)
            validated_tf[key] = confirmed
            tf_raw += raw
            tf_filtered += raw - len(confirmed)
            if yield_progress:
                yield {"type": "progress", "pct": 76,
                       "msg": f"{tf}/{key}: {len(confirmed)} confirmed ({raw - len(confirmed)} filtered)"}

        all_results[tf] = validated_tf
        _total_raw += tf_raw
        _total_filtered += tf_filtered
        _append_scan_log(tf, validated_tf, prev_list,
                         raw_passed=tf_raw, filtered_open=tf_filtered)

    # ── Write union + refresh ─────────────────────────────────────────────
    all_confirmed_union: set[str] = set()
    for tf_res in all_results.values():
        for syms in tf_res.values():
            all_confirmed_union.update(syms)
    _write_scan_list(sorted(all_confirmed_union), prev_dates=prev_scan_dates)
    _trigger_dashboard_refresh()

    elapsed = time.time() - t0
    total_sigs = sum(len(v) for tf_res in all_results.values() for v in tf_res.values())
    if yield_progress:
        yield {
            "type": "done",
            "count": total_sigs,
            "elapsed": round(elapsed, 1),
            "all_tf": True,
            "enriched_ok": _e_ok,
            "enriched_fail": _e_fail,
            "enriched_total": enrich_stats.get("total", 0),
        }
    return all_results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Run strategy scan against full universe.")
    parser.add_argument(
        "--strategy", required=True,
        help="Strategy key (trend, swing, stoof, …) or 'all' for all strategies",
    )
    parser.add_argument(
        "--tf", required=True,
        help="Timeframe (1D, 1W, 2W, 1M) or 'all' for all timeframes. 4H is excluded.",
    )
    args = parser.parse_args()

    strategy = args.strategy.strip().lower()
    tf = args.tf.strip().upper()

    def _print_event(event: dict) -> None:
        if event["type"] == "progress":
            print(f"[{event['pct']:3d}%] {event['msg']}")
        elif event["type"] == "done":
            print(f"\nDone: {event['count']} symbols in {event.get('elapsed', '?')}s")
        elif event["type"] == "error":
            print(f"ERROR: {event['msg']}", file=sys.stderr)

    if tf == "ALL" and strategy == "all":
        # All TFs × all strategies
        for event in run_scan_all_tf(yield_progress=True):
            _print_event(event)
            if event["type"] == "error":
                sys.exit(1)
    elif tf == "ALL":
        # All TFs, single strategy — run each TF separately
        tfs = [t for t in _TF_DOWNLOAD_DAYS.keys() if t != "4H"]
        for t in tfs:
            print(f"\n── {t} ──")
            for event in run_scan(strategy, t, yield_progress=True):
                _print_event(event)
                if event["type"] == "error":
                    break
    elif strategy == "all":
        # Single TF, all strategies
        for event in run_scan_all_strategies(tf, yield_progress=True):
            _print_event(event)
            if event["type"] == "error":
                sys.exit(1)
    else:
        # Single TF, single strategy
        for event in run_scan(strategy, tf, yield_progress=True):
            _print_event(event)
            if event["type"] == "error":
                sys.exit(1)


if __name__ == "__main__":
    main()
