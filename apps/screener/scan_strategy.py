"""Parallel multi-strategy stock scanner.

Pipeline per symbol:
  1. Download OHLCV (lean, yfinance batch)  — ThreadPoolExecutor(6 workers)
  2. Quality gate check (SMA20>SMA200, volume spike, SR break) per strategy
  3. Lean enrichment — only KPIs needed for strategy C3/C4
  4. C3 onset detection (last 3 bars)
  5. Write survivors to configs/lists/{strategy_key}.csv (clean-slate replace)
  6. Background subprocess: `python -m trading_dashboard dashboard refresh`

Usage (CLI):
  python -m apps.screener.scan_strategy --strategy trend --tf 1D
  python -m apps.screener.scan_strategy --strategy swing --tf 1W
  python -m apps.screener.scan_strategy --strategy dip_buy --tf 1D

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
    "1D": 450,    # 235 bars + buffer
    "1W": 700,    # 235 weeks → ~4500 days, capped by yfinance 2y limit
    "2W": 1500,
    "1M": 3650,
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

    try:
        enriched = compute_scan_indicators(
            df, all_kpis_list, indicator_config_path=_INDICATOR_CONFIG
        )
    except Exception as exc:
        logger.debug("%s enrichment failed: %s", sym, exc)
        return None

    if not check_quality_gates(enriched, scan_filters):
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
) -> Generator[dict, None, list[str]]:
    """Run the full scan. If yield_progress=True, yields SSE-compatible dicts.

    Returns list of passing symbols (also written to CSV).
    """
    t0 = time.time()
    config = _load_config()
    strat_def = _get_strategy_def(config, strategy_key)

    if strat_def.get("entry_type") == "threshold":
        msg = "Stoof threshold scanning not yet implemented."
        if yield_progress:
            yield {"type": "error", "msg": msg}
        return []

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
            pct = int(completed_batches / len(batches) * 40)  # download = 0-40%
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

    # Step 3: Write final CSV with only confirmed signals
    _write_strategy_csv(strategy_key, validated, tf)
    elapsed = time.time() - t0

    # Step 4: Trigger dashboard refresh
    _trigger_dashboard_refresh()

    if yield_progress:
        yield {"type": "done", "count": len(validated), "elapsed": round(elapsed, 1)}

    return validated


def _write_strategy_csv(strategy_key: str, symbols: list[str], tf: str) -> None:
    """Write (or replace) configs/lists/{strategy_key}.csv with scan results.

    Format matches the existing list CSVs: single 'ticker' column, one per line.
    The group name is derived from the filename stem by SymbolManager.from_lists_dir.
    """
    _LISTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _LISTS_DIR / f"{strategy_key}.csv"

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker"])
        for sym in sorted(symbols):
            writer.writerow([sym])

    logger.info("Wrote %d symbols to %s", len(symbols), out_path)


def _enrich_new_symbols(symbols: list[str]) -> None:
    """Enrich symbols that have no feature store data yet (synchronous, no refresh)."""
    from apps.dashboard.config_loader import load_build_config, resolve_paths
    cfg = load_build_config()
    paths = resolve_paths(cfg)
    stock_data_dir = paths.feature_store_enriched_dir / "stock_data"

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
    stock_data_dir = paths.feature_store_enriched_dir / "stock_data"

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
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Run strategy scan against full universe.")
    parser.add_argument("--strategy", required=True, help="Strategy key (trend, swing, dip_buy)")
    parser.add_argument("--tf", required=True, help="Timeframe (4H, 1D, 1W, 2W, 1M)")
    args = parser.parse_args()

    for event in run_scan(args.strategy, args.tf, yield_progress=True):
        if event["type"] == "progress":
            print(f"[{event['pct']:3d}%] {event['msg']}")
        elif event["type"] == "done":
            results_count = event["count"]
            elapsed = event["elapsed"]
            print(f"\nDone: {results_count} symbols in {elapsed}s")
        elif event["type"] == "error":
            print(f"ERROR: {event['msg']}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
