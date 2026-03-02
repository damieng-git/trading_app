"""
Phase 18.0a — Fast re-enrichment of sample_300.

Reads existing OHLCV from parquets, re-runs enrichment (adds Stoof columns),
and generates 2W + 1M timeframes from 1D data. No yfinance download needed.

Uses multiprocessing (4 workers) for ~4x speedup over sequential processing.
"""
from __future__ import annotations
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

import pandas as pd
from trading_dashboard.data.downloader import (
    resample_to_weekly,
    resample_to_biweekly,
    resample_to_monthly,
    resample_to_4h,
)
from trading_dashboard.data.enrichment import translate_and_compute_indicators

DATA_DIR = REPO_DIR / "research" / "data" / "feature_store" / "enriched" / "sample_300" / "stock_data"
INDICATOR_CFG = REPO_DIR / "apps" / "dashboard" / "configs" / "indicator_config.json"
OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]
N_WORKERS = min(4, os.cpu_count() or 1)


def extract_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    keep = [c for c in OHLCV_COLS if c in df.columns]
    out = df[keep].copy()
    if "Adj Close" in df.columns:
        out["Adj Close"] = df["Adj Close"]
    return out


def _enrich(df: pd.DataFrame, tf: str, sym: str) -> pd.DataFrame | None:
    if df is None or df.empty or len(df) < 50:
        return None
    result = translate_and_compute_indicators(
        df.copy(),
        indicator_config_path=str(INDICATOR_CFG),
        timeframe=tf,
        symbol=sym,
    )
    enriched = result[0] if isinstance(result, tuple) else result
    if enriched is not None and not enriched.empty:
        return enriched
    return None


def _enrich_single_stock(sym: str) -> tuple[str, int, str]:
    """Enrich one stock across all timeframes. Returns (sym, n_tfs, error_or_empty)."""
    try:
        daily_path = DATA_DIR / f"{sym}_1D.parquet"
        daily_raw = extract_ohlcv(pd.read_parquet(daily_path))
        if daily_raw.empty or len(daily_raw) < 100:
            return (sym, 0, "too_short")

        if not isinstance(daily_raw.index, pd.DatetimeIndex):
            daily_raw.index = pd.to_datetime(daily_raw.index, errors="coerce")
        if hasattr(daily_raw.index, "tz") and daily_raw.index.tz is not None:
            daily_raw.index = daily_raw.index.tz_localize(None)
        daily_raw = daily_raw.sort_index()

        hourly_path = DATA_DIR / f"{sym}_4H.parquet"
        hourly_raw = None
        if hourly_path.exists():
            hourly_raw = extract_ohlcv(pd.read_parquet(hourly_path))
            if not isinstance(hourly_raw.index, pd.DatetimeIndex):
                hourly_raw.index = pd.to_datetime(hourly_raw.index, errors="coerce")
            if hasattr(hourly_raw.index, "tz") and hourly_raw.index.tz is not None:
                hourly_raw.index = hourly_raw.index.tz_localize(None)
            hourly_raw = hourly_raw.sort_index()

        tf_map = {
            "1D": daily_raw,
            "1W": resample_to_weekly(daily_raw),
            "2W": resample_to_biweekly(daily_raw),
            "1M": resample_to_monthly(daily_raw),
        }
        if hourly_raw is not None and len(hourly_raw) >= 50:
            tf_map["4H"] = hourly_raw

        count = 0
        for tf, df in tf_map.items():
            enriched = _enrich(df, tf, sym)
            if enriched is not None:
                out = DATA_DIR / f"{sym}_{tf}.parquet"
                enriched.to_parquet(out)
                count += 1

        return (sym, count, "")
    except Exception as e:
        return (sym, 0, str(e))


def main():
    t0 = time.time()
    daily_files = sorted(DATA_DIR.glob("*_1D.parquet"))
    symbols = [f.stem.rsplit("_1D", 1)[0] for f in daily_files]
    print(f"Phase 18.0a: Re-enriching {len(symbols)} symbols × 5 TFs "
          f"({N_WORKERS} workers)", flush=True)

    done, failed = 0, 0
    with mp.Pool(processes=N_WORKERS) as pool:
        for i, (sym, count, err) in enumerate(
            pool.imap_unordered(_enrich_single_stock, symbols), 1
        ):
            if err:
                failed += 1
                if err != "too_short":
                    print(f"  ERROR {sym}: {err}", flush=True)
            else:
                done += 1

            if i % 25 == 0:
                elapsed = time.time() - t0
                eta = (elapsed / i) * (len(symbols) - i)
                print(f"  [{i}/{len(symbols)}] {sym} → {count} TFs, "
                      f"elapsed {elapsed:.0f}s, ETA {eta:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s ({elapsed/60:.1f} min)", flush=True)
    print(f"  Success: {done}, Failed: {failed}", flush=True)

    for tf in ["4H", "1D", "1W", "2W", "1M"]:
        n = len(list(DATA_DIR.glob(f"*_{tf}.parquet")))
        print(f"  {tf}: {n} files", flush=True)


if __name__ == "__main__":
    main()
