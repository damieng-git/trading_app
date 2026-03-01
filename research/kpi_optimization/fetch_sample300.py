"""
Fetch + enrich OHLCV data for sample_300 universe.

Reuses existing data from sample_100 where available,
fetches only the missing tickers via yfinance.
"""
from __future__ import annotations

import csv
import shutil
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

import yfinance as yf
import pandas as pd
from trading_dashboard.data.downloader import (
    resample_to_4h,
    resample_to_weekly,
    _flatten_multiindex,
)
from trading_dashboard.data.enrichment import translate_and_compute_indicators


def _safe_download(ticker, **kwargs):
    """Handle newer yfinance versions that return (df, extras) tuple."""
    result = yf.download(tickers=ticker, progress=False, **kwargs)
    if isinstance(result, tuple):
        result = result[0]
    if result is None or result.empty:
        return pd.DataFrame()
    result = _flatten_multiindex(result, ticker)
    result = result.rename_axis("Date").reset_index().set_index("Date")
    result.index = pd.to_datetime(result.index, errors="coerce").tz_localize(None)
    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in result.columns]
    return result[keep].copy()

SAMPLE_CSV = REPO_DIR / "research" / "sample_universe" / "sample_300.csv"
SAMPLE_100_DIR = REPO_DIR / "data" / "feature_store" / "enriched" / "sample_100" / "stock_data"
SAMPLE_300_DIR = REPO_DIR / "research" / "data" / "feature_store" / "enriched" / "sample_300" / "stock_data"
INDICATOR_CFG = REPO_DIR / "apps" / "dashboard" / "configs" / "indicator_config.json"

START_DATE = "2018-01-01"
TFS = ["1D", "1W", "4H"]


def load_tickers():
    with open(SAMPLE_CSV) as f:
        return [row["yfinance_ticker"] for row in csv.DictReader(f)]


def has_enriched(sym, tf, target_dir):
    return (target_dir / f"{sym}_{tf}.parquet").exists() or (target_dir / f"{sym}_{tf}.csv").exists()


def copy_existing(sym, tf, src_dir, dst_dir):
    for ext in [".parquet", ".csv"]:
        src = src_dir / f"{sym}_{tf}{ext}"
        if src.exists():
            shutil.copy2(src, dst_dir / src.name)
            return True
    return False


def fetch_and_enrich(sym, dst_dir):
    """Download OHLCV + enrich for all timeframes."""
    try:
        daily = _safe_download(sym, start=START_DATE, interval="1d", auto_adjust=False)
        if daily is None or daily.empty or len(daily) < 100:
            print(f"    SKIP {sym}: insufficient daily data", flush=True)
            return 0

        hourly = None
        for period in ["729d", "700d", "365d"]:
            try:
                hourly = _safe_download(sym, period=period, interval="60m", auto_adjust=False)
                if hourly is not None and not hourly.empty:
                    break
            except Exception:
                continue

        count = 0
        for tf in TFS:
            if tf == "1D":
                df = daily.copy()
            elif tf == "1W":
                df = resample_to_weekly(daily)
            elif tf == "4H":
                if hourly is None or hourly.empty:
                    continue
                df = resample_to_4h(hourly)
            else:
                continue

            if df is None or df.empty or len(df) < 50:
                continue

            result = translate_and_compute_indicators(
                df.copy(),
                indicator_config_path=str(INDICATOR_CFG),
                timeframe=tf,
                symbol=sym,
            )
            enriched = result[0] if isinstance(result, tuple) else result
            if enriched is not None and not enriched.empty:
                out = dst_dir / f"{sym}_{tf}.parquet"
                enriched.to_parquet(out)
                count += 1

        return count
    except Exception as e:
        print(f"    ERROR {sym}: {e}", flush=True)
        return 0


def main():
    t0 = time.time()
    SAMPLE_300_DIR.mkdir(parents=True, exist_ok=True)

    tickers = load_tickers()
    print(f"Sample 300: {len(tickers)} tickers", flush=True)

    copied, fetched, failed = 0, 0, 0

    for i, sym in enumerate(tickers):
        all_have = all(has_enriched(sym, tf, SAMPLE_300_DIR) for tf in TFS)
        if all_have:
            continue

        from_100 = 0
        for tf in TFS:
            if not has_enriched(sym, tf, SAMPLE_300_DIR):
                if copy_existing(sym, tf, SAMPLE_100_DIR, SAMPLE_300_DIR):
                    from_100 += 1

        all_have = all(has_enriched(sym, tf, SAMPLE_300_DIR) for tf in TFS)
        if all_have:
            copied += 1
            if (copied % 20) == 0:
                print(f"  Copied {copied} from sample_100...", flush=True)
            continue

        print(f"  [{i+1}/{len(tickers)}] Fetching {sym}...", flush=True)
        n = fetch_and_enrich(sym, SAMPLE_300_DIR)
        if n > 0:
            fetched += 1
        else:
            failed += 1

        if (fetched % 10) == 0 and fetched > 0:
            print(f"    Progress: {fetched} fetched, {failed} failed, {time.time()-t0:.0f}s", flush=True)

    total_available = {}
    for tf in TFS:
        n = sum(1 for sym in tickers if has_enriched(sym, tf, SAMPLE_300_DIR))
        total_available[tf] = n

    print(f"\nDone in {time.time()-t0:.0f}s", flush=True)
    print(f"  Copied from sample_100: {copied}", flush=True)
    print(f"  Fetched via yfinance: {fetched}", flush=True)
    print(f"  Failed: {failed}", flush=True)
    print(f"  Available per TF: {total_available}", flush=True)


if __name__ == "__main__":
    main()
