"""
OHLCV data download, resampling, and ticker resolution.

Extracted from build_dashboard.py — provides a clean API for:
- Downloading daily/hourly candles from yfinance
- Resampling to weekly (W-FRI) and 4H
- Loading TradingView CSV exports
- Resolving display symbols to valid yfinance tickers
"""

from __future__ import annotations

import logging
import threading
import time as _time
from pathlib import Path
from typing import Callable as _Callable
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

_log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 10


def _yf_download_with_retry(**kwargs) -> pd.DataFrame:
    """Wrapper around yf.download that retries on rate-limit and transient errors."""
    for attempt in range(_MAX_RETRIES):
        try:
            df = yf.download(**kwargs)
            if df is not None and not df.empty:
                return df
        except Exception as exc:
            _log.debug("yf.download raised %s for %s (attempt %d/%d)",
                       type(exc).__name__, kwargs.get("tickers"), attempt + 1, _MAX_RETRIES)
        if attempt < _MAX_RETRIES - 1:
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            _log.debug("yf.download returned empty for %s, retry in %ds", kwargs.get("tickers"), delay)
            _time.sleep(delay)
    return pd.DataFrame()


_DOWNLOAD_TIMEOUT = 60

def _download_with_timeout(**kwargs) -> pd.DataFrame:
    """Wraps _yf_download_with_retry with a thread-based timeout."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeout
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_yf_download_with_retry, **kwargs)
        try:
            return future.result(timeout=_DOWNLOAD_TIMEOUT)
        except FuturesTimeout:
            _log.warning("yf.download timed out after %ds for %s", _DOWNLOAD_TIMEOUT, kwargs.get("tickers"))
            return pd.DataFrame()


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def download_daily_ohlcv(
    ticker: str,
    start: str,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Download daily OHLCV from yfinance.  Returns empty DF on failure."""
    df = _download_with_timeout(
        tickers=ticker,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = _flatten_multiindex(df, ticker)
    df = df.rename_axis("Date").reset_index().set_index("Date")
    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in df.columns]
    return df[keep].copy()


def download_hourly_ohlcv(
    ticker: str,
    period: str = "729d",
) -> pd.DataFrame:
    """Download hourly data for building 4H candles."""
    periods_to_try = [str(period or "").strip()] if str(period or "").strip() else []
    for p in ["729d", "700d", "365d"]:
        if p not in periods_to_try:
            periods_to_try.append(p)

    df = None
    for p in periods_to_try:
        df = _download_with_timeout(
            tickers=ticker,
            period=p,
            interval="60m",
            auto_adjust=False,
            progress=False,
            group_by="column",
            threads=True,
        )
        if df is not None and not df.empty:
            break
    if df is None or df.empty:
        return pd.DataFrame()

    df = _flatten_multiindex(df, ticker)
    df = df.rename_axis("Date").reset_index().set_index("Date")
    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in df.columns]
    return df[keep].copy()


# ---------------------------------------------------------------------------
# Batch download helpers
# ---------------------------------------------------------------------------

_BATCH_CHUNK_SIZE = 50
_BATCH_TIMEOUT_S = 300


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize index and columns after download."""
    df = df.rename_axis("Date").reset_index().set_index("Date")
    df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in df.columns]
    out = df[keep].copy()
    # Drop rows where Close is NaN (non-trading-day artifacts from multi-exchange batches)
    if "Close" in out.columns:
        out = out.dropna(subset=["Close"])
    if not out.empty:
        invalid_hl = out["High"] < out["Low"]
        if invalid_hl.any():
            _log.warning("Fixing %d bars where High < Low", invalid_hl.sum())
            out.loc[invalid_hl, ["High", "Low"]] = out.loc[invalid_hl, ["Low", "High"]].values
        if "Volume" in out.columns:
            neg_vol = out["Volume"] < 0
            if neg_vol.any():
                _log.warning("Fixing %d bars with negative Volume", neg_vol.sum())
                out.loc[neg_vol, "Volume"] = 0
    return out


def download_daily_batch(
    tickers: List[str],
    start: str,
    end: Optional[str] = None,
    chunk_size: int = _BATCH_CHUNK_SIZE,
    on_chunk: Optional[_Callable] = None,
) -> Dict[str, pd.DataFrame]:
    """Batch-download daily OHLCV for many tickers. Returns {ticker: DataFrame}.

    *on_chunk*, if provided, is called after each batch with
    ``(done_count, total_count, chunk_tickers)`` for progress reporting.
    """
    results: Dict[str, pd.DataFrame] = {}
    total = len(tickers)
    for i in range(0, total, chunk_size):
        chunk = tickers[i : i + chunk_size]
        try:
            from concurrent.futures import ThreadPoolExecutor
            from concurrent.futures import TimeoutError as FuturesTimeout
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    _yf_download_with_retry,
                    tickers=chunk,
                    start=start,
                    end=end,
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                    group_by="column",
                    threads=True,
                )
                df = future.result(timeout=_BATCH_TIMEOUT_S)
        except FuturesTimeout:
            _log.warning("download_daily_batch timed out after %ds for chunk of %d tickers",
                         _BATCH_TIMEOUT_S, len(chunk))
            df = pd.DataFrame()
        if df is None or df.empty:
            if on_chunk:
                on_chunk(min(i + chunk_size, total), total, chunk)
            continue
        if not isinstance(df.columns, pd.MultiIndex):
            if len(chunk) == 1:
                results[chunk[0]] = _normalize_ohlcv(df)
            if on_chunk:
                on_chunk(min(i + chunk_size, total), total, chunk)
            continue
        available = df.columns.get_level_values("Ticker").unique()
        for t in available:
            try:
                sym_df = df.xs(t, axis=1, level="Ticker", drop_level=True)
                sym_df = _normalize_ohlcv(sym_df)
                if not sym_df.empty and sym_df["Close"].dropna().shape[0] >= 2:
                    results[t] = sym_df
            except (KeyError, ValueError) as exc:
                _log.debug("Skipping ticker %s in daily batch (KeyError/ValueError): %s", t, exc)
                continue
        if on_chunk:
            on_chunk(min(i + chunk_size, total), total, chunk)
        if i + chunk_size < total:
            _time.sleep(1)
    return results


def download_hourly_batch(
    tickers: List[str],
    period: str = "729d",
    chunk_size: int = _BATCH_CHUNK_SIZE,
) -> Dict[str, pd.DataFrame]:
    """Batch-download hourly OHLCV for many tickers. Returns {ticker: DataFrame}."""
    periods_to_try = [str(period or "").strip()] if str(period or "").strip() else []
    for p in ["729d", "700d", "365d"]:
        if p not in periods_to_try:
            periods_to_try.append(p)

    results: Dict[str, pd.DataFrame] = {}
    remaining = list(tickers)

    for per in periods_to_try:
        if not remaining:
            break
        for i in range(0, len(remaining), chunk_size):
            chunk = remaining[i : i + chunk_size]
            try:
                from concurrent.futures import ThreadPoolExecutor
                from concurrent.futures import TimeoutError as FuturesTimeout
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        _yf_download_with_retry,
                        tickers=chunk,
                        period=per,
                        interval="60m",
                        auto_adjust=False,
                        progress=False,
                        group_by="column",
                        threads=True,
                    )
                    df = future.result(timeout=_BATCH_TIMEOUT_S)
            except FuturesTimeout:
                _log.warning("download_hourly_batch timed out after %ds for chunk of %d tickers",
                             _BATCH_TIMEOUT_S, len(chunk))
                df = pd.DataFrame()
            if df is None or df.empty:
                continue
            if not isinstance(df.columns, pd.MultiIndex):
                if len(chunk) == 1:
                    results[chunk[0]] = _normalize_ohlcv(df)
                continue
            available = df.columns.get_level_values("Ticker").unique()
            for t in available:
                try:
                    sym_df = df.xs(t, axis=1, level="Ticker", drop_level=True)
                    sym_df = _normalize_ohlcv(sym_df)
                    if not sym_df.empty and sym_df["Close"].dropna().shape[0] >= 2:
                        results[t] = sym_df
                except (KeyError, ValueError) as exc:
                    _log.debug("Skipping ticker %s in hourly batch (KeyError/ValueError): %s", t, exc)
                    continue
            if i + chunk_size < len(remaining):
                _time.sleep(1)
        remaining = [t for t in remaining if t not in results]

    return results


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------

def resample_to_weekly(daily: pd.DataFrame, rule: str = "W-FRI") -> pd.DataFrame:
    if daily.empty:
        return daily
    o = daily["Open"].resample(rule).first()
    h = daily["High"].resample(rule).max()
    l = daily["Low"].resample(rule).min()
    c = daily["Close"].resample(rule).last()
    v = daily["Volume"].resample(rule).sum() if "Volume" in daily.columns else None
    out = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c})
    if v is not None:
        out["Volume"] = v
    return out.dropna(subset=["Open", "High", "Low", "Close"], how="any")


def resample_to_biweekly(daily: pd.DataFrame, rule: str = "2W-FRI") -> pd.DataFrame:
    if daily.empty:
        return daily
    o = daily["Open"].resample(rule).first()
    h = daily["High"].resample(rule).max()
    l = daily["Low"].resample(rule).min()
    c = daily["Close"].resample(rule).last()
    v = daily["Volume"].resample(rule).sum() if "Volume" in daily.columns else None
    out = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c})
    if v is not None:
        out["Volume"] = v
    return out.dropna(subset=["Open", "High", "Low", "Close"], how="any")


def resample_to_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return daily
    o = daily["Open"].resample("ME").first()
    h = daily["High"].resample("ME").max()
    l = daily["Low"].resample("ME").min()
    c = daily["Close"].resample("ME").last()
    v = daily["Volume"].resample("ME").sum() if "Volume" in daily.columns else None
    out = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c})
    if v is not None:
        out["Volume"] = v
    return out.dropna(subset=["Open", "High", "Low", "Close"], how="any")


def resample_to_4h(hourly: pd.DataFrame) -> pd.DataFrame:
    if hourly.empty:
        return hourly
    o = hourly["Open"].resample("4h").first()
    h = hourly["High"].resample("4h").max()
    l = hourly["Low"].resample("4h").min()
    c = hourly["Close"].resample("4h").last()
    v = hourly["Volume"].resample("4h").sum() if "Volume" in hourly.columns else None
    out = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c})
    if v is not None:
        out["Volume"] = v
    return out.dropna(subset=["Open", "High", "Low", "Close"], how="any")


# ---------------------------------------------------------------------------
# TradingView CSV import
# ---------------------------------------------------------------------------

def load_tradingview_ohlcv_csv(path: Path, *, timeframe: str) -> pd.DataFrame:
    """Load an OHLCV export from TradingView."""
    df = pd.read_csv(path)
    if df is None or df.empty:
        return pd.DataFrame()

    colmap = {str(c).strip().lower(): c for c in df.columns}
    time_col = None
    for k in ["time", "date", "datetime", "timestamp"]:
        if k in colmap:
            time_col = colmap[k]
            break
    if time_col is None:
        raise ValueError(f"TradingView CSV is missing a time/date column: {path.name}")

    def _pick(*names: str) -> str | None:
        for n in names:
            if n in colmap:
                return colmap[n]
        return None

    open_col = _pick("open")
    high_col = _pick("high")
    low_col = _pick("low")
    close_col = _pick("close")
    vol_col = _pick("volume", "vol")
    if not (open_col and high_col and low_col and close_col):
        raise ValueError(f"TradingView CSV is missing OHLC columns: {path.name}")

    out = pd.DataFrame({
        "Open": pd.to_numeric(df[open_col], errors="coerce"),
        "High": pd.to_numeric(df[high_col], errors="coerce"),
        "Low": pd.to_numeric(df[low_col], errors="coerce"),
        "Close": pd.to_numeric(df[close_col], errors="coerce"),
    })
    if vol_col is not None:
        out["Volume"] = pd.to_numeric(df[vol_col], errors="coerce")

    idx = pd.to_datetime(df[time_col], errors="coerce", utc=False)
    out.index = idx
    out = out.dropna(subset=["Open", "High", "Low", "Close"], how="any").sort_index()

    if timeframe.upper() == "1W" and not out.empty:
        wkday = out.index.dayofweek
        if (wkday == 0).mean() >= 0.8:
            out.index = out.index + pd.Timedelta(days=4)
            out = out.sort_index()

    return out


def maybe_load_tradingview_ohlcv(
    display_symbol: str,
    timeframe: str,
    tradingview_data_dir: Path,
) -> pd.DataFrame | None:
    """Load a TradingView CSV override if available, else None."""
    if not tradingview_data_dir.exists():
        return None
    safe_sym = display_symbol.replace("/", "_")
    candidates = [
        tradingview_data_dir / f"{safe_sym}_{timeframe}.csv",
        tradingview_data_dir / f"{safe_sym}.csv",
    ]
    for p in candidates:
        if p.exists() and p.is_file() and p.stat().st_size > 0:
            try:
                return load_tradingview_ohlcv_csv(p, timeframe=timeframe)
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# Ticker resolution
# ---------------------------------------------------------------------------

def resolve_yfinance_ticker(
    display_symbol: str,
    *,
    ticker_map: Dict[str, str] | None = None,
    start_date: str = "2018-01-01",
    end_date: Optional[str] = None,
    min_bars: int = 20,
) -> Tuple[Optional[str], List[str]]:
    """
    Resolve a display symbol to a working yfinance ticker.

    Returns (chosen_ticker_or_None, list_of_attempted_tickers).
    """
    ticker_map = ticker_map or {}
    if display_symbol in ticker_map:
        return ticker_map[display_symbol], [ticker_map[display_symbol]]

    if "." in display_symbol or display_symbol.startswith("^") or display_symbol.endswith("=X"):
        tried = [display_symbol]
        probe = _probe_ticker(display_symbol)
        if probe is not None and not probe.empty and probe["Close"].dropna().shape[0] >= 2:
            return display_symbol, tried
        return None, tried

    candidates = [
        display_symbol,
        f"{display_symbol}.PA",
        f"{display_symbol}.AS",
        f"{display_symbol}.BR",
        f"{display_symbol}.L",
        f"{display_symbol}.DE",
    ]
    tried: List[str] = []
    for t in candidates:
        tried.append(t)
        probe = _probe_ticker(t)
        if probe is not None and not probe.empty and probe["Close"].dropna().shape[0] >= 2:
            return t, tried
    return None, tried


def _probe_ticker(ticker: str) -> Optional[pd.DataFrame]:
    """Lightweight probe: download only 5 days to verify a ticker exists."""
    try:
        df = yf.download(
            tickers=ticker,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="column",
            threads=True,
        )
        if df is None or df.empty:
            return None
        df = _flatten_multiindex(df, ticker)
        return df
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Benchmark loading
# ---------------------------------------------------------------------------

_BENCHMARK_CACHE: Dict[str, pd.Series] = {}
_BENCHMARK_LOCK = threading.Lock()


def load_benchmark_close(
    symbol: str,
    target_index: pd.DatetimeIndex,
    *,
    cache_dir: Path | None = None,
    feature_store_dir: Path | None = None,
) -> pd.Series | None:
    """Load benchmark close prices, caching across calls within the same run."""
    with _BENCHMARK_LOCK:
        if symbol in _BENCHMARK_CACHE:
            return _BENCHMARK_CACHE[symbol].reindex(target_index, method="ffill")

    bench_close: pd.Series | None = None

    def _try_file(p: Path) -> pd.Series | None:
        if not p.exists() or p.stat().st_size == 0:
            return None
        try:
            if p.suffix == ".parquet":
                bdf = pd.read_parquet(p)
            else:
                bdf = pd.read_csv(p, parse_dates=[0], index_col=0)
            if "Close" in bdf.columns and not bdf["Close"].dropna().empty:
                idx = pd.to_datetime(bdf.index, errors="coerce")
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                bdf.index = idx
                return bdf["Close"].sort_index()
        except Exception as exc:
            _log.debug("Failed to load benchmark from %s: %s", p, exc)
            pass
        return None

    if cache_dir is not None:
        for ext in ("parquet", "csv"):
            cache_path = cache_dir / f"{symbol}_1D_raw.{ext}"
            bench_close = _try_file(cache_path)
            if bench_close is not None:
                break

        if bench_close is None:
            glob_patterns: list[tuple[Path, str]] = []
            for ext in ("parquet", "csv"):
                glob_patterns.append((cache_dir, f"{symbol}_1D*.{ext}"))
                glob_patterns.append((cache_dir, f"{symbol}_1W*.{ext}"))
            if feature_store_dir is not None:
                for ext in ("parquet", "csv"):
                    glob_patterns.append((feature_store_dir, f"{symbol}_1D*.{ext}"))
                    glob_patterns.append((feature_store_dir, f"{symbol}_1W*.{ext}"))
            for base_dir, pat in glob_patterns:
                for match in sorted(base_dir.rglob(pat)):
                    bench_close = _try_file(match)
                    if bench_close is not None:
                        break
                if bench_close is not None:
                    break

    if bench_close is None:
        try:
            bdf = yf.download(symbol, period="10y", interval="1d", progress=False, auto_adjust=True)
            if bdf is not None and not bdf.empty:
                if isinstance(bdf.columns, pd.MultiIndex):
                    bdf.columns = bdf.columns.get_level_values(0)
                idx = pd.to_datetime(bdf.index, errors="coerce")
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                bdf.index = idx
                if "Close" in bdf.columns:
                    bench_close = bdf["Close"].sort_index()
                    if cache_dir is not None:
                        try:
                            cache_path = cache_dir / f"{symbol}_1D.csv"
                            cache_path.parent.mkdir(parents=True, exist_ok=True)
                            bdf.to_csv(cache_path)
                        except Exception as exc:
                            _log.debug("Failed to cache benchmark %s: %s", symbol, exc)
                            pass
        except Exception as exc:
            _log.debug("Failed to download/cache benchmark %s: %s", symbol, exc)
            pass

    if bench_close is not None:
        with _BENCHMARK_LOCK:
            _BENCHMARK_CACHE[symbol] = bench_close
        return bench_close.reindex(target_index, method="ffill")
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _flatten_multiindex(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Normalize yfinance MultiIndex columns to flat OHLCV."""
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    lvl_names = [n or "" for n in df.columns.names]
    if "Ticker" in lvl_names:
        return df.xs(ticker, axis=1, level="Ticker", drop_level=True)
    if ticker in df.columns.get_level_values(-1):
        return df.xs(ticker, axis=1, level=-1, drop_level=True)
    return df.xs(df.columns.get_level_values(-1)[0], axis=1, level=-1, drop_level=True)
