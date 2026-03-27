"""
build_dashboard.py

Purpose
-------
Download OHLCV via yfinance, compute indicator columns, and generate a standalone Plotly dashboard
with a tickbox indicator menu and a KPI traffic-lights panel.

Workflow (high level)
---------------------
For each symbol in `SYMBOLS`:
  - Resolve to a yfinance ticker (fast path for fully-qualified tickers like "KER.PA").
  - Download 1D candles (for 1D, and resample to 1W W-FRI).
  - Compute all indicator columns per timeframe and write CSVs to `output_data/`.

Then:
  - Build Plotly figures and serialize them to a searchable HTML dashboard (`dashboard_weekly_plotly.html`).
  - Write `pine_to_python_mapping.md` for traceability.

Notes on performance/RAM
------------------------
- This script processes symbols sequentially (RAM-friendly).
- The final HTML contains Plotly JSON for all symbols/timeframes, which can be large.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from apps.dashboard.config_loader import (
    CONFIG_JSON,
    DASHBOARD_ASSETS_DIR,
    DASHBOARD_SHELL_HTML,
    DATA_HEALTH_JSON,
    END_DATE,
    FEATURE_STORE_ENRICHED_DIR,
    INDICATOR_CONFIG_JSON_DEFAULT,
    LEGACY_OUTPUT_STOCK_DATA_DIR,
    LEGACY_PINESCRIPTS_DIR,
    OHLCV_CACHE_DIR,
    PINESCRIPTS_DIR,
    PROJECT_DIR,
    RUN_METADATA_JSON,
    SCREENER_SUMMARY_JSON,
    SCRIPT_DIR,
    START_DATE,
    SYMBOL_DISPLAY_OVERRIDES_JSON,
    TIMEFRAME_REGISTRY,
    TRADINGVIEW_DATA_DIR,
    USE_CACHED_OUTPUT_DATA,
    WEEKLY_RULE,
    YFINANCE_TICKER_MAP,
    BuildConfig,
    BuildPaths,
    load_build_config,
    resolve_paths,
)
from apps.dashboard.signal_logger import append_combo_signal_log, dispatch_notifications, export_alerts
from apps.dashboard.templates import (
    write_lazy_dashboard_shell_html,
    write_mapping_doc,
    write_readme,
)
from trading_dashboard.data.downloader import (
    download_daily_batch,
    download_daily_ohlcv,
    maybe_load_tradingview_ohlcv,
    resample_to_biweekly,
    resample_to_monthly,
    resample_to_weekly,
)
from trading_dashboard.data.downloader import (
    resolve_yfinance_ticker as _resolve_yfinance_ticker_impl,
)
from trading_dashboard.data.enrichment import (
    IndicatorSpec,
    apply_mtf_overlay,
    translate_and_compute_indicators,
)
from trading_dashboard.data.health import summarize_df_health
from trading_dashboard.data.incremental import IncrementalUpdater
from trading_dashboard.data.store import DataStore
from trading_dashboard.utils.pine_rtf import extract_pine_source_from_rtf

logger = logging.getLogger(__name__)


# =============================================================================
# Data utilities
# =============================================================================

def _utc_now_iso() -> str:
    """Return current UTC time as ISO string (no microseconds)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_strategy_setups() -> dict:
    """Load strategy_setups from config.json."""
    _cfg_path = Path(__file__).resolve().parent / "configs" / "config.json"
    try:
        raw = json.loads(_cfg_path.read_text(encoding="utf-8"))
        return raw.get("strategy_setups", {})
    except Exception:
        return {}


def _sanitize_json(obj):
    """Recursively replace inf/NaN floats with None for JSON compliance."""
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    if isinstance(obj, float) and (np.isinf(obj) or np.isnan(obj)):
        return None
    return obj


def _get_runtime_versions() -> dict:
    """Collect Python, platform, and key package versions for run metadata."""
    def _pkg_version(name: str) -> str | None:
        try:
            # Python 3.8+ stdlib
            from importlib.metadata import version as _v  # type: ignore

            return str(_v(name))
        except Exception as e:
            logger.warning("Could not get package version for %s: %s", name, e)
            return None

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": {
            "pandas": _pkg_version("pandas"),
            "numpy": _pkg_version("numpy"),
            "plotly": _pkg_version("plotly"),
            "yfinance": _pkg_version("yfinance"),
            "reportlab": _pkg_version("reportlab"),
        },
    }


def _dir_size_bytes(p: Path) -> int:
    """Return total size in bytes of a file or directory (recursive)."""
    try:
        if not p.exists():
            return 0
        if p.is_file():
            return int(p.stat().st_size)
        total = 0
        for fp in p.rglob("*"):
            try:
                if fp.is_file():
                    total += int(fp.stat().st_size)
            except Exception as e:
                logger.debug("Skipping file in dir size: %s", e)
                continue
        return int(total)
    except Exception as e:
        logger.warning("Could not compute dir size for %s: %s", p, e)
        return 0

def archive_dashboard_snapshot(*, version: str) -> dict:
    """
    Save a portable snapshot of the STATIC dashboard (shell + assets) under:
      PRIVATE/TRADING/archives/<version>/
    and create:
      PRIVATE/TRADING/archives/<version>.zip

    Notes:
    - We intentionally do NOT archive `output_data/stock_data/` (can be huge).
    - Goal is "openable dashboard artifact", not full dataset backup.
    """
    v = (version or "").strip()
    if not v:
        return {"ok": False, "reason": "empty_version"}

    archives_dir = PROJECT_DIR / "archives"
    dest_dir = archives_dir / v
    zip_path = archives_dir / f"{v}.zip"

    # Required artifacts for the static dashboard
    shell_src = DASHBOARD_SHELL_HTML
    assets_src = DASHBOARD_ASSETS_DIR

    if not shell_src.exists():
        return {"ok": False, "reason": "missing_shell_html", "path": str(shell_src)}
    if not assets_src.exists():
        return {"ok": False, "reason": "missing_assets_dir", "path": str(assets_src)}

    archives_dir.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Mirror structure inside archive (so relative links still work)
    out_dir = dest_dir / "output_data"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Copy shell + assets
    shutil.copy2(shell_src, out_dir / "dashboard_shell.html")
    shutil.copytree(assets_src, out_dir / "dashboard_assets", dirs_exist_ok=True)

    # Copy small run artifacts when present
    for p in [RUN_METADATA_JSON, DATA_HEALTH_JSON, SCREENER_SUMMARY_JSON]:
        try:
            if p.exists() and p.is_file() and p.stat().st_size > 0:
                shutil.copy2(p, out_dir / p.name)
        except Exception as e:
            logger.warning("Could not copy artifact to archive: %s", e)

    # Copy minimal config + generator for traceability
    try:
        sdir = dest_dir / "scripts"
        sdir.mkdir(parents=True, exist_ok=True)
        if CONFIG_JSON.exists():
            shutil.copy2(CONFIG_JSON, sdir / "config.json")
        shutil.copy2(Path(__file__).resolve(), sdir / "build_dashboard.py")
    except Exception as e:
        logger.warning("Could not copy config/scripts to archive: %s", e)

    manifest = [
        f"# {v} (dashboard snapshot)",
        "",
        "## Contents",
        "- `output_data/dashboard_shell.html`",
        "- `output_data/dashboard_assets/`",
        "- `output_data/run_metadata.json` (if present)",
        "- `output_data/data_health.json` (if present)",
        "- `output_data/screener_summary.json` (if present)",
        "- `scripts/config.json` (if present)",
        "- `scripts/build_dashboard.py`",
        "",
        "## How to open",
        "- Open `output_data/dashboard_shell.html` (static; no local server required).",
        "",
    ]
    (dest_dir / "MANIFEST.md").write_text("\n".join(manifest), encoding="utf-8")

    # Build zip (overwrite)
    if zip_path.exists():
        try:
            zip_path.unlink()
        except Exception as e:
            logger.warning("Could not remove existing archive zip: %s", e)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in dest_dir.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(dest_dir)))

    return {"ok": True, "archive_dir": str(dest_dir), "zip_path": str(zip_path)}


_summarize_df_health = summarize_df_health
_CRITICAL_MISSING_CLOSE_PCT = 10.0  # Block pipeline: skip symbol/tf when missing_close_pct exceeds this


def _maybe_load_tradingview_ohlcv(display_symbol: str, timeframe: str) -> pd.DataFrame | None:
    return maybe_load_tradingview_ohlcv(display_symbol, timeframe, TRADINGVIEW_DATA_DIR)


def _download_daily_ohlcv(ticker: str, start: str, end: Optional[str]) -> pd.DataFrame:
    return download_daily_ohlcv(ticker, start, end)


def _resample_to_weekly(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    return resample_to_weekly(daily, rule)


def _resample_to_biweekly(daily: pd.DataFrame, rule: str = "2W-FRI") -> pd.DataFrame:
    return resample_to_biweekly(daily, rule)


def _resample_to_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    return resample_to_monthly(daily)


_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"
_TICKER_CACHE_PATH = _CONFIGS_DIR / "ticker_cache.json"


def _load_ticker_cache() -> Dict[str, str]:
    """Build a display_symbol -> yfinance_ticker map from sector_map + persistent cache."""
    merged: Dict[str, str] = dict(YFINANCE_TICKER_MAP)

    sm_path = _CONFIGS_DIR / "sector_map.json"
    if sm_path.exists():
        try:
            sm = json.loads(sm_path.read_text())
            for sym, meta in sm.items():
                if sym not in merged and meta.get("name"):
                    merged[sym] = sym
        except Exception as exc:
            logger.debug("Failed to load sector_map.json for ticker cache: %s", exc)
            pass

    if _TICKER_CACHE_PATH.exists():
        try:
            cached = json.loads(_TICKER_CACHE_PATH.read_text())
            merged.update(cached)
        except Exception as exc:
            logger.debug("Failed to load ticker cache: %s", exc)
            pass

    return merged


def _save_ticker_cache(resolved: Dict[str, str]) -> None:
    """Persist newly resolved tickers so future builds skip probing."""
    existing: Dict[str, str] = {}
    if _TICKER_CACHE_PATH.exists():
        try:
            existing = json.loads(_TICKER_CACHE_PATH.read_text())
        except Exception as exc:
            logger.debug("Failed to load existing ticker cache for save: %s", exc)
            pass
    existing.update(resolved)
    _TICKER_CACHE_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True))


_ticker_map: Dict[str, str] = _load_ticker_cache()


def resolve_yfinance_ticker(display_symbol: str) -> Tuple[Optional[str], List[str]]:
    """Resolve a display symbol to a yfinance ticker, with fallback suffix probing."""
    return _resolve_yfinance_ticker_impl(
        display_symbol,
        ticker_map=_ticker_map,
        start_date=START_DATE,
        end_date=END_DATE,
    )



def _load_symbol_display_overrides() -> Dict[str, str]:
    """
    Optional manual overrides:
      apps/dashboard/configs/symbol_display_overrides.json
    Shape:
      { "AAPL": "Apple", "SAP.DE": "SAP SE" }
    """
    try:
        p = SYMBOL_DISPLAY_OVERRIDES_JSON
        if not p.exists() or p.stat().st_size <= 0:
            return {}
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, str] = {}
        for k, v in raw.items():
            kk = str(k or "").strip().upper()
            vv = str(v or "").strip()
            if kk and vv:
                out[kk] = " ".join(vv.split())
        return out
    except Exception as e:
        logger.warning("Could not load symbol display overrides: %s", e)
        return {}


def _derive_symbol_display(sector_map: Dict[str, dict]) -> Dict[str, str]:
    """Derive {symbol: display_name} from sector_map + manual overrides."""
    from apps.dashboard.sector_map import get_display_names
    overrides = _load_symbol_display_overrides()
    return get_display_names(sector_map, overrides)



def _compute_indicators(df: pd.DataFrame, indicator_config_path: Path, *, timeframe: str = "1D", symbol: str = "", sector_info: dict | None = None) -> Tuple[pd.DataFrame, List[IndicatorSpec]]:
    """Compute all indicators on *df* using the given config."""
    return translate_and_compute_indicators(
        df,
        indicator_config_path=indicator_config_path,
        cache_dir=OHLCV_CACHE_DIR,
        feature_store_dir=FEATURE_STORE_ENRICHED_DIR,
        timeframe=timeframe,
        symbol=symbol,
        sector_info=sector_info,
    )


def _enrich_one_task(args: tuple) -> tuple:
    """Top-level function for ProcessPoolExecutor — must be picklable."""
    sym, tf, df_trim, sector_info, ind_cfg_path = args
    try:
        enriched, specs = _compute_indicators(df_trim, ind_cfg_path, timeframe=tf, symbol=sym, sector_info=sector_info)
        return sym, tf, enriched, specs
    except Exception:
        logger.exception("Failed to compute indicators for %s/%s", sym, tf)
        return sym, tf, None, []


def _extract_specs_from_enriched(df: pd.DataFrame, indicator_config_path: Path) -> List[IndicatorSpec]:
    """
    Build IndicatorSpec list from an already-enriched DataFrame without
    recomputing any indicators.  Falls back to full computation if needed.
    """
    try:
        from trading_dashboard.indicators.registry import get_all as _get_all_indicators
        all_indicators = _get_all_indicators()
        df_cols = set(df.columns)
        specs: List[IndicatorSpec] = []
        for ind in all_indicators:
            matched_cols = [c for c in (ind.columns or []) if c in df_cols]
            if matched_cols:
                specs.append(IndicatorSpec(
                    key=ind.key,
                    title=ind.title,
                    overlay=ind.overlay,
                    columns=matched_cols,
                ))
        if specs:
            return specs
    except Exception as exc:
        logger.debug("Failed to extract specs from enriched DataFrame registry: %s", exc)
        pass
    _, specs = _compute_indicators(df, indicator_config_path)
    return specs


# --- REMOVED: ~560 lines of inline indicator computation now live in ---
# --- trading_dashboard/data/enrichment.py                            ---


def _safe_path_component(s: str) -> str:
    """Sanitize a string for use as a filesystem path component."""
    s = (s or "").strip()
    if not s:
        return "_"
    # Keep it stable + filesystem-friendly across platforms.
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", ".", "@"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


def run_stock_export(
    *,
    cfg: BuildConfig,
    paths: BuildPaths,
    indicator_config_path: Path,
    export_phase: str = "all",
    force_recompute_indicators: bool = False,
    force_download: bool = False,
    use_parallel_enrich: bool = True,
    on_progress: "Callable[[dict], None] | None" = None,
) -> tuple[dict, dict, dict, dict, list]:
    """
    Export enriched CSVs to paths.output_stock_data_dir.

    export_phase:
      - "all": download (as needed) + compute indicators
      - "download": download/resample OHLCV and persist raw CSVs only (no indicator computation)
      - "compute": compute indicators from raw OHLCV CSVs only (no yfinance)

    force_recompute_indicators:
      - If True, bypass cached enriched CSV reuse and recompute indicator columns.
    Returns: (all_data, symbol_display, symbol_meta, data_health, indicator_specs)
    """
    export_phase = str(export_phase or "all").strip().lower()
    if export_phase not in {"all", "download", "compute"}:
        export_phase = "all"
    symbols = cfg.symbols
    timeframes = cfg.timeframes

    paths.output_data_dir.mkdir(parents=True, exist_ok=True)
    paths.output_stock_data_dir.mkdir(parents=True, exist_ok=True)
    paths.output_raw_ohlcv_dir.mkdir(parents=True, exist_ok=True)

    store = DataStore(
        enriched_dir=paths.output_stock_data_dir,
        raw_dir=paths.output_raw_ohlcv_dir,
        fmt="parquet",
        cache_ttl_hours=float(cfg.cache_ttl_hours),
        legacy_dirs=[LEGACY_OUTPUT_STOCK_DATA_DIR, paths.output_data_dir],
    )
    incremental = IncrementalUpdater(store)

    def _load_cached_symbol(sym: str) -> Dict[str, pd.DataFrame]:
        return store.load_all_enriched(sym, timeframes)

    def _load_raw_symbol(sym: str) -> Dict[str, pd.DataFrame]:
        return store.load_all_raw(sym, timeframes)

    all_data: Dict[str, Dict[str, pd.DataFrame]] = {}
    symbol_meta: Dict[str, Dict[str, object]] = {}
    data_health: Dict[str, Dict[str, dict]] = {}
    indicator_specs: List[IndicatorSpec] = []

    # Single source of truth: sector_map.json holds name, sector, industry,
    # and fundamentals for every symbol (fetched via yf.Ticker().info).
    # Always refresh fundamentals during a full run so P/E, targets, etc. stay current.
    try:
        from apps.dashboard.sector_map import fetch_sector_map as _fetch_sm
        _enrichment_sector_map = _fetch_sm(symbols, refresh_fundamentals=True)
    except Exception:
        try:
            from apps.dashboard.sector_map import load_sector_map as _load_sm
            _enrichment_sector_map = _load_sm()
        except Exception:
            _enrichment_sector_map = {}

    symbol_display: Dict[str, str] = _derive_symbol_display(_enrichment_sector_map)

    # Separate symbols into: cached (skip download), compute-only (offline), and needs-download
    symbols_to_download: list[str] = []

    for sym in symbols:
        if export_phase == "compute":
            raw_map = _load_raw_symbol(sym)
            if not raw_map:
                continue

            enrich_tasks = [
                (sym, tf, raw_df, _enrichment_sector_map.get(sym), indicator_config_path)
                for tf, raw_df in raw_map.items()
                if raw_df is not None and not raw_df.empty and tf in timeframes
            ]
            if not enrich_tasks:
                continue

            tf_map_enriched: Dict[str, pd.DataFrame] = {}
            if use_parallel_enrich and len(enrich_tasks) > 1:
                _workers = min(os.cpu_count() or 4, len(enrich_tasks))
                with ProcessPoolExecutor(max_workers=_workers) as pool:
                    for _sym, _tf, enriched, specs in pool.map(_enrich_one_task, enrich_tasks, chunksize=2, timeout=5400):
                        if enriched is not None:
                            health = _summarize_df_health(
                                enriched,
                                tf=_tf,
                                min_bars=int(cfg.min_bars_by_tf.get(_tf, 0) or 0) or None,
                                max_missing_close_pct=float(cfg.max_missing_close_pct),
                                max_missing_volume_pct=float(cfg.max_missing_volume_pct),
                            )
                            if health.get("missing_close_pct") is not None and health["missing_close_pct"] > _CRITICAL_MISSING_CLOSE_PCT:
                                logger.warning("Skipping %s/%s: missing_close_pct %.1f%% exceeds %.0f%%",
                                               _sym, _tf, health["missing_close_pct"], _CRITICAL_MISSING_CLOSE_PCT)
                            else:
                                indicator_specs = specs
                                store.save_enriched(_sym, _tf, enriched)
                                tf_map_enriched[_tf] = enriched
                                data_health.setdefault(_sym, {})
                                data_health[_sym][_tf] = health
            else:
                for _sym, _tf, raw_df, sector_info, _ind_cfg in enrich_tasks:
                    enriched, specs = _compute_indicators(raw_df, _ind_cfg, timeframe=_tf, symbol=_sym, sector_info=sector_info)
                    health = _summarize_df_health(
                        enriched,
                        tf=_tf,
                        min_bars=int(cfg.min_bars_by_tf.get(_tf, 0) or 0) or None,
                        max_missing_close_pct=float(cfg.max_missing_close_pct),
                        max_missing_volume_pct=float(cfg.max_missing_volume_pct),
                    )
                    if health.get("missing_close_pct") is not None and health["missing_close_pct"] > _CRITICAL_MISSING_CLOSE_PCT:
                        logger.warning("Skipping %s/%s: missing_close_pct %.1f%% exceeds %.0f%%",
                                       _sym, _tf, health["missing_close_pct"], _CRITICAL_MISSING_CLOSE_PCT)
                    else:
                        indicator_specs = specs
                        store.save_enriched(_sym, _tf, enriched)
                        tf_map_enriched[_tf] = enriched
                        data_health.setdefault(_sym, {})
                        data_health[_sym][_tf] = health

            if tf_map_enriched:
                apply_mtf_overlay(tf_map_enriched)
                all_data[sym] = tf_map_enriched
            continue

        if USE_CACHED_OUTPUT_DATA and not force_download and (export_phase == "all") and (not force_recompute_indicators):
            cached = _load_cached_symbol(sym)
            if cached and set(timeframes).issubset(cached.keys()):
                data_health.setdefault(sym, {})
                filtered_cached: Dict[str, pd.DataFrame] = {}
                for tf, df_cached in cached.items():
                    health = _summarize_df_health(
                        df_cached,
                        tf=tf,
                        min_bars=int(cfg.min_bars_by_tf.get(tf, 0) or 0) or None,
                        max_missing_close_pct=float(cfg.max_missing_close_pct),
                        max_missing_volume_pct=float(cfg.max_missing_volume_pct),
                    )
                    data_health[sym][tf] = health
                    if health.get("missing_close_pct") is not None and health["missing_close_pct"] > _CRITICAL_MISSING_CLOSE_PCT:
                        logger.warning("Skipping cached %s/%s: missing_close_pct %.1f%% exceeds %.0f%%",
                                       sym, tf, health["missing_close_pct"], _CRITICAL_MISSING_CLOSE_PCT)
                    else:
                        filtered_cached[tf] = df_cached
                if filtered_cached:
                    all_data[sym] = filtered_cached
                weekly_override = _maybe_load_tradingview_ohlcv(sym, "1W")
                weekly_source = "tradingview_csv" if (weekly_override is not None and not weekly_override.empty) else "yfinance_resample_or_cached"
                symbol_meta.setdefault(sym, {})
                symbol_meta[sym]["weekly_source"] = weekly_source

                if filtered_cached and not indicator_specs:
                    try:
                        df0 = filtered_cached.get("1W") or next(iter(filtered_cached.values()))
                        _, indicator_specs = _compute_indicators(df0, indicator_config_path)
                    except Exception as e:
                        logger.warning("Could not extract indicator specs from cached data: %s", e)
                        indicator_specs = indicator_specs or []
                continue

        symbols_to_download.append(sym)

    # --- Batch yfinance download for symbols that need fresh data ---
    if symbols_to_download:
        # Resolve display symbols to yfinance tickers
        yf_tickers: List[str] = []
        sym_to_ticker: Dict[str, str] = {}
        ticker_to_sym: Dict[str, str] = {}
        for sym in symbols_to_download:
            ticker, _attempts = resolve_yfinance_ticker(sym)
            if ticker is None:
                logger.warning("Ticker resolution failed for %s (tried: %s)", sym, _attempts)
                continue
            if ticker != sym:
                _ticker_map[sym] = ticker
            yf_tickers.append(ticker)
            sym_to_ticker[sym] = ticker
            ticker_to_sym[ticker] = sym

        # Batch download: daily candles only
        if on_progress:
            on_progress({"phase": "download", "label": "Downloading\u2026",
                         "detail": f"{len(yf_tickers)} symbols", "pct": 5})
        t_dl_start = time.time()
        daily_batch = download_daily_batch(yf_tickers, START_DATE, END_DATE)
        t_dl_end = time.time()
        logger.info("  [timing] Batch download: %.1fs (%d daily)", t_dl_end - t_dl_start, len(daily_batch))
        if on_progress:
            on_progress({"phase": "download_done", "label": "Download complete",
                         "detail": f"{len(daily_batch)} daily",
                         "pct": 30})

        # Process each symbol: resample, merge incremental, enrich
        dl_enrich_tasks: list[tuple] = []
        dl_enrich_meta: Dict[Tuple[str, str], Tuple] = {}  # (sym, tf) -> (raw_hash, cfg_hash)

        for sym in symbols_to_download:
            ticker = sym_to_ticker.get(sym)
            if ticker is None:
                continue

            daily_1d = daily_batch.get(ticker, pd.DataFrame())

            if daily_1d.empty:
                logger.warning("Skipping %s (no data in batch result)", sym)
                continue

            weekly_override = _maybe_load_tradingview_ohlcv(sym, "1W")
            weekly_1w = weekly_override if (weekly_override is not None and not weekly_override.empty) else (
                _resample_to_weekly(daily_1d, WEEKLY_RULE) if not daily_1d.empty else pd.DataFrame()
            )
            weekly_source = "tradingview_csv" if (weekly_override is not None and not weekly_override.empty) else "yfinance_resample"
            biweekly_2w = _resample_to_biweekly(daily_1d) if not daily_1d.empty else pd.DataFrame()
            monthly_1m = _resample_to_monthly(daily_1d) if not daily_1d.empty else pd.DataFrame()

            tf_map_raw: dict[str, pd.DataFrame] = {}
            for tf, raw_df in {"1D": daily_1d, "1W": weekly_1w, "2W": biweekly_2w, "1M": monthly_1m}.items():
                if raw_df is not None and not raw_df.empty:
                    tf_map_raw[tf] = incremental.merge_new_bars(sym, tf, raw_df)
                else:
                    tf_map_raw[tf] = raw_df

            symbol_meta.setdefault(sym, {})
            symbol_meta[sym]["weekly_source"] = weekly_source

            tf_map_enriched_dl: Dict[str, pd.DataFrame] = {}
            for tf, raw_df in tf_map_raw.items():
                if tf not in timeframes:
                    continue
                if raw_df is None or raw_df.empty:
                    continue

                if export_phase == "download":
                    continue

                raw_hash = store.compute_raw_hash(raw_df)
                cfg_hash = store.compute_config_hash(indicator_config_path)
                if not force_recompute_indicators and store.enrichment_is_current(sym, tf, raw_hash, cfg_hash):
                    cached_enriched = store.load_enriched(sym, tf, respect_ttl=False)
                    if cached_enriched is not None:
                        health = _summarize_df_health(
                            cached_enriched,
                            tf=tf,
                            min_bars=int(cfg.min_bars_by_tf.get(tf, 0) or 0) or None,
                            max_missing_close_pct=float(cfg.max_missing_close_pct),
                            max_missing_volume_pct=float(cfg.max_missing_volume_pct),
                        )
                        if health.get("missing_close_pct") is not None and health["missing_close_pct"] > _CRITICAL_MISSING_CLOSE_PCT:
                            logger.warning("Skipping cached %s/%s: missing_close_pct %.1f%% exceeds %.0f%%",
                                           sym, tf, health["missing_close_pct"], _CRITICAL_MISSING_CLOSE_PCT)
                        else:
                            tf_map_enriched_dl[tf] = cached_enriched
                            data_health.setdefault(sym, {})
                            data_health[sym][tf] = health
                            continue

                dl_enrich_tasks.append((sym, tf, raw_df, _enrichment_sector_map.get(sym), indicator_config_path))
                dl_enrich_meta[(sym, tf)] = (raw_hash, cfg_hash)

            if tf_map_enriched_dl:
                all_data.setdefault(sym, {}).update(tf_map_enriched_dl)

        # Run download-phase enrichment (parallel or sequential)
        if dl_enrich_tasks:
            _enrich_total = len(dl_enrich_tasks)
            _enrich_done = 0
            if on_progress:
                on_progress({"phase": "enrich", "label": "Enriching\u2026",
                             "detail": f"0/{_enrich_total} tasks", "pct": 30,
                             "completed": 0, "total": _enrich_total})
            if use_parallel_enrich and len(dl_enrich_tasks) > 1:
                _workers = min(os.cpu_count() or 4, len(dl_enrich_tasks))
                with ProcessPoolExecutor(max_workers=_workers) as pool:
                    for _sym, _tf, enriched, specs in pool.map(_enrich_one_task, dl_enrich_tasks, chunksize=2):
                        if enriched is not None:
                            health = _summarize_df_health(
                                enriched,
                                tf=_tf,
                                min_bars=int(cfg.min_bars_by_tf.get(_tf, 0) or 0) or None,
                                max_missing_close_pct=float(cfg.max_missing_close_pct),
                                max_missing_volume_pct=float(cfg.max_missing_volume_pct),
                            )
                            if health.get("missing_close_pct") is not None and health["missing_close_pct"] > _CRITICAL_MISSING_CLOSE_PCT:
                                logger.warning("Skipping %s/%s: missing_close_pct %.1f%% exceeds %.0f%%",
                                               _sym, _tf, health["missing_close_pct"], _CRITICAL_MISSING_CLOSE_PCT)
                            else:
                                raw_hash, cfg_hash = dl_enrich_meta.get((_sym, _tf), (None, None))
                                store.save_enriched(_sym, _tf, enriched, raw_hash=raw_hash, indicator_config_hash=cfg_hash)
                                all_data.setdefault(_sym, {})[_tf] = enriched
                                data_health.setdefault(_sym, {})[_tf] = health
                                if specs:
                                    indicator_specs = specs
                        _enrich_done += 1
                        if on_progress:
                            on_progress({"phase": "enrich", "label": "Enriching\u2026",
                                         "detail": f"{_sym} {_tf} ({_enrich_done}/{_enrich_total})",
                                         "pct": 30 + round((_enrich_done / _enrich_total) * 60),
                                         "completed": _enrich_done, "total": _enrich_total})
            else:
                for _sym, _tf, raw_df, sector_info, _ind_cfg in dl_enrich_tasks:
                    enriched, specs = _compute_indicators(raw_df, _ind_cfg, timeframe=_tf, symbol=_sym, sector_info=sector_info)
                    health = _summarize_df_health(
                        enriched,
                        tf=_tf,
                        min_bars=int(cfg.min_bars_by_tf.get(_tf, 0) or 0) or None,
                        max_missing_close_pct=float(cfg.max_missing_close_pct),
                        max_missing_volume_pct=float(cfg.max_missing_volume_pct),
                    )
                    if health.get("missing_close_pct") is not None and health["missing_close_pct"] > _CRITICAL_MISSING_CLOSE_PCT:
                        logger.warning("Skipping %s/%s: missing_close_pct %.1f%% exceeds %.0f%%",
                                       _sym, _tf, health["missing_close_pct"], _CRITICAL_MISSING_CLOSE_PCT)
                    else:
                        raw_hash, cfg_hash = dl_enrich_meta.get((_sym, _tf), (None, None))
                        store.save_enriched(_sym, _tf, enriched, raw_hash=raw_hash, indicator_config_hash=cfg_hash)
                        all_data.setdefault(_sym, {})[_tf] = enriched
                        data_health.setdefault(_sym, {})[_tf] = health
                        if specs:
                            indicator_specs = specs
                    _enrich_done += 1
                    if on_progress:
                        on_progress({"phase": "enrich", "label": "Enriching\u2026",
                                     "detail": f"{_sym} {_tf} ({_enrich_done}/{_enrich_total})",
                                     "pct": 30 + round((_enrich_done / _enrich_total) * 60),
                                     "completed": _enrich_done, "total": _enrich_total})

    # Derive indicator_specs from already-enriched data (no recomputation needed).
    if not indicator_specs and all_data:
        try:
            _any_tf_map = next(iter(all_data.values()))
            _any_df = next(iter(_any_tf_map.values()))
            indicator_specs = _extract_specs_from_enriched(_any_df, indicator_config_path)
        except Exception as e:
            logger.warning("Could not extract indicator specs post-download: %s", e)

    if symbols_to_download:
        _save_ticker_cache(_ticker_map)

    return all_data, symbol_display, symbol_meta, data_health, indicator_specs


def _fetch_fx_rates_and_currencies(sector_map: dict) -> tuple[dict[str, float], dict[str, str]]:
    """Download EUR FX rates for all unique currencies in sector_map.
    Returns: (fx_to_eur dict, symbol_currencies dict)"""
    symbol_currencies: dict[str, str] = {}
    unique_currencies: set[str] = set()
    for sym, info in sector_map.items():
        fund = info.get("fundamentals") or {}
        ccy = (fund.get("currency") or info.get("currency") or "").upper()
        if ccy:
            symbol_currencies[sym] = ccy
            unique_currencies.add(ccy)

    fx_to_eur: dict[str, float] = {"EUR": 1.0}
    non_eur = [c for c in unique_currencies if c != "EUR"]
    if non_eur:
        try:
            import yfinance as yf
            pairs = [f"{c}EUR=X" for c in non_eur]
            # Handle GBp (pence) specially
            gbp_in_list = "GBP" in non_eur or "GBP" in unique_currencies or any(
                (v.get("fundamentals", {}).get("currency") or v.get("currency", "")) == "GBp" for v in sector_map.values()
            )
            if "GBP" not in non_eur and gbp_in_list:
                pairs.append("GBPEUR=X")

            for c in non_eur:
                pair = f"{c}EUR=X"
                try:
                    tk = yf.Ticker(pair)
                    hist = tk.history(period="5d")
                    if hist is not None and not hist.empty:
                        rate = float(hist["Close"].iloc[-1])
                        fx_to_eur[c] = rate
                        if c == "GBP":
                            fx_to_eur["GBp"] = rate / 100.0
                except Exception as exc:
                    logger.warning("FX rate fetch failed for %s: %s", pair, exc)
        except Exception as exc:
            logger.warning("FX rate download failed: %s", exc)

    return fx_to_eur, symbol_currencies


def enrich_symbols(
    tickers: list[str],
    *,
    on_progress: "Callable[[dict], None] | None" = None,
    update_screener: bool = True,
) -> dict:
    """Download, resample, enrich, and update screener for a batch of new tickers.

    Returns {"ok": True/False, "enriched": [...], "failed": [...], "total": N}.
    Much faster than a full rebuild — only processes the given tickers.

    update_screener: if False, skip accumulating enriched data in memory and
      skip the _rebuild_screener_json call.  Use this when enriching scan
      candidates (parquets on disk are all that's needed); it dramatically
      reduces peak memory when the candidate list is large (hundreds of symbols).
    """
    cfg = load_build_config()
    paths = resolve_paths(cfg)
    timeframes = cfg.timeframes
    indicator_config_path = Path(INDICATOR_CONFIG_JSON_DEFAULT)

    paths.output_data_dir.mkdir(parents=True, exist_ok=True)
    paths.output_stock_data_dir.mkdir(parents=True, exist_ok=True)
    paths.output_raw_ohlcv_dir.mkdir(parents=True, exist_ok=True)

    store = DataStore(
        enriched_dir=paths.output_stock_data_dir,
        raw_dir=paths.output_raw_ohlcv_dir,
        fmt="parquet",
        cache_ttl_hours=float(cfg.cache_ttl_hours),
        legacy_dirs=[LEGACY_OUTPUT_STOCK_DATA_DIR, paths.output_data_dir],
    )
    incremental = IncrementalUpdater(store)

    enriched_syms: list[str] = []
    failed_syms: list[str] = []
    all_data: Dict[str, Dict[str, pd.DataFrame]] = {}
    total = len(tickers)

    if on_progress:
        on_progress({"phase": "resolve", "label": "Resolving tickers…", "pct": 5,
                      "completed": 0, "total": total})

    yf_tickers: list[str] = []
    sym_to_ticker: dict[str, str] = {}
    ticker_to_sym: dict[str, str] = {}

    for sym in tickers:
        sym = sym.strip().upper()
        ticker, _attempts = resolve_yfinance_ticker(sym)
        if ticker is None:
            logger.warning("Ticker resolution failed for %s", sym)
            failed_syms.append(sym)
            continue
        yf_tickers.append(ticker)
        sym_to_ticker[sym] = ticker
        ticker_to_sym[ticker] = sym

    if not yf_tickers:
        return {"ok": False, "enriched": [], "failed": failed_syms, "total": total,
                "error": "No valid tickers resolved"}

    if on_progress:
        on_progress({"phase": "download", "label": "Downloading…",
                      "detail": f"{len(yf_tickers)} symbols", "pct": 15,
                      "completed": 0, "total": total})

    daily_batch = download_daily_batch(yf_tickers, START_DATE, END_DATE)

    if on_progress:
        on_progress({"phase": "enrich", "label": "Enriching…", "pct": 40,
                      "completed": 0, "total": total})

    done = 0
    for sym in list(sym_to_ticker.keys()):
        ticker = sym_to_ticker[sym]
        daily_1d = daily_batch.get(ticker, pd.DataFrame())

        if daily_1d.empty:
            logger.warning("No data for %s", sym)
            failed_syms.append(sym)
            done += 1
            continue

        weekly_override = _maybe_load_tradingview_ohlcv(sym, "1W")
        weekly_1w = weekly_override if (weekly_override is not None and not weekly_override.empty) else (
            _resample_to_weekly(daily_1d, WEEKLY_RULE) if not daily_1d.empty else pd.DataFrame()
        )
        biweekly_2w = _resample_to_biweekly(daily_1d) if not daily_1d.empty else pd.DataFrame()
        monthly_1m = _resample_to_monthly(daily_1d) if not daily_1d.empty else pd.DataFrame()

        tf_map_raw: dict[str, pd.DataFrame] = {}
        for tf, raw_df in {"1D": daily_1d, "1W": weekly_1w,
                            "2W": biweekly_2w, "1M": monthly_1m}.items():
            if raw_df is not None and not raw_df.empty:
                tf_map_raw[tf] = incremental.merge_new_bars(sym, tf, raw_df)
            else:
                tf_map_raw[tf] = raw_df

        tf_map_enriched: Dict[str, pd.DataFrame] = {}
        for tf, raw_df in tf_map_raw.items():
            if tf not in timeframes or raw_df is None or raw_df.empty:
                continue
            try:
                enriched, _specs = _compute_indicators(
                    raw_df, indicator_config_path, timeframe=tf, symbol=sym)
                health = _summarize_df_health(
                    enriched,
                    tf=tf,
                    min_bars=int(cfg.min_bars_by_tf.get(tf, 0) or 0) or None,
                    max_missing_close_pct=float(cfg.max_missing_close_pct),
                    max_missing_volume_pct=float(cfg.max_missing_volume_pct),
                )
                if health.get("missing_close_pct") is not None and health["missing_close_pct"] > _CRITICAL_MISSING_CLOSE_PCT:
                    logger.warning("Skipping %s/%s: missing_close_pct %.1f%% exceeds %.0f%%",
                                   sym, tf, health["missing_close_pct"], _CRITICAL_MISSING_CLOSE_PCT)
                else:
                    raw_hash = store.compute_raw_hash(raw_df)
                    cfg_hash = store.compute_config_hash(indicator_config_path)
                    store.save_enriched(sym, tf, enriched, raw_hash=raw_hash, indicator_config_hash=cfg_hash)
                    tf_map_enriched[tf] = enriched
            except Exception as exc:
                logger.warning("Enrichment failed for %s/%s: %s", sym, tf, exc)

        if tf_map_enriched:
            if update_screener:
                all_data[sym] = tf_map_enriched
            enriched_syms.append(sym)
        else:
            failed_syms.append(sym)

        done += 1
        if on_progress:
            pct = 40 + int(50 * done / total)
            on_progress({"phase": "enrich", "label": f"Enriching {done}/{total}…",
                          "detail": sym, "pct": pct,
                          "completed": done, "total": total})

    if update_screener:
        if on_progress:
            on_progress({"phase": "sector_map", "label": "Updating metadata…", "pct": 92,
                          "completed": done, "total": total})

        try:
            from apps.dashboard.sector_map import fetch_sector_map
            fetch_sector_map(enriched_syms, refresh_fundamentals=True)
        except Exception as exc:
            logger.warning("sector_map update failed: %s", exc)

        if on_progress:
            on_progress({"phase": "screener", "label": "Updating screener…", "pct": 95,
                          "completed": done, "total": total})

        try:
            _rebuild_screener_json(cfg, paths, all_data_override=all_data)
        except Exception as exc:
            logger.warning("Screener JSON update failed: %s", exc)

    return {"ok": len(enriched_syms) > 0, "enriched": enriched_syms,
            "failed": failed_syms, "total": total}


def _rebuild_screener_json(
    cfg: BuildConfig,
    paths: BuildPaths,
    *,
    all_data_override: dict | None = None,
) -> None:
    """Rebuild screener_summary.json using existing enriched data + optional overrides."""
    timeframes = cfg.timeframes
    store = DataStore(
        enriched_dir=paths.output_stock_data_dir,
        raw_dir=paths.output_raw_ohlcv_dir,
        fmt="parquet",
        cache_ttl_hours=float(cfg.cache_ttl_hours),
        legacy_dirs=[LEGACY_OUTPUT_STOCK_DATA_DIR, paths.output_data_dir],
    )

    all_syms = set(cfg.symbols)
    if all_data_override:
        all_syms |= set(all_data_override.keys())

    all_data: Dict[str, Dict[str, pd.DataFrame]] = {}
    for sym in sorted(all_syms):
        if all_data_override and sym in all_data_override:
            all_data[sym] = all_data_override[sym]
        else:
            cached = store.load_all_enriched(sym, timeframes)
            if cached:
                all_data[sym] = cached

    try:
        from apps.dashboard.sector_map import load_sector_map
        smap = load_sector_map()
    except Exception:
        smap = {}
    symbol_display = _derive_symbol_display(smap)

    run_started_utc = _utc_now_iso()
    try:
        from apps.dashboard.screener_builder import build_screener_rows
        from apps.screener.scan_strategy import _load_scan_list as _load_scan_date_map
        rows_by_tf, by_symbol, _ = build_screener_rows(
            all_data=all_data,
            timeframes=timeframes,
            cfg_kpi_weights=dict(cfg.kpi_weights),
            cfg_alerts_lookback_bars=int(cfg.alerts_lookback_bars),
            cfg_combo_kpis_by_tf=cfg.combo_kpis_by_tf,
            cfg_combo_3_kpis=cfg.combo_3_kpis,
            cfg_combo_4_kpis=cfg.combo_4_kpis,
            symbol_display=symbol_display,
            symbol_meta={},
            data_health={},
            stoch_mtm_thresholds=getattr(cfg, "stoch_mtm_thresholds", None),
            strategy_setups=_load_strategy_setups(),
            scan_date_map=_load_scan_date_map(),
        )
        screener_summary = _sanitize_json({
            "generated_utc": run_started_utc,
            "alerts_lookback_bars": int(cfg.alerts_lookback_bars),
            "rows_by_tf": rows_by_tf,
            "by_symbol": by_symbol,
        })
        paths.screener_summary_json.write_text(
            json.dumps(screener_summary, indent=2, allow_nan=False), encoding="utf-8")
    except Exception:
        logger.exception("Failed to rebuild screener JSON")

    try:
        fx_cache = paths.dashboard_shell_html.parent / "fx_rates.json"
        smap_for_fx = smap
        fx_to_eur, symbol_currencies = _fetch_fx_rates_and_currencies(smap_for_fx)
        fx_cache.write_text(json.dumps(fx_to_eur, allow_nan=False), encoding="utf-8")
    except Exception:
        logger.debug("Failed to update FX cache during incremental rebuild")


def run_refresh_dashboard(
    *,
    cfg: BuildConfig,
    paths: BuildPaths,
    indicator_config_path: Path,
    run_started_utc: str,
    all_data: dict,
    symbol_display: dict,
    symbol_meta: dict,
    data_health: dict,
    indicator_specs: list,
    skip_figures: bool = False,
) -> tuple[dict, dict | None]:
    """
    Refresh dashboard artifacts from cached enriched CSVs only.
    Returns: (screener_summary, symbol_to_asset or None)
    """
    symbols = cfg.symbols
    timeframes = cfg.timeframes

    # Mapping docs require Pine sources
    pine_sources: Dict[str, str] = {}
    pines_dir = PINESCRIPTS_DIR if PINESCRIPTS_DIR.exists() else (LEGACY_PINESCRIPTS_DIR if LEGACY_PINESCRIPTS_DIR.exists() else PINESCRIPTS_DIR)
    for rtf_path in sorted(pines_dir.glob("*.rtf")):
        rtf_raw = rtf_path.read_text(encoding="utf-8", errors="replace")
        pine = extract_pine_source_from_rtf(rtf_raw)
        pine_sources[rtf_path.name] = pine

    # Best-effort: symbol resolution doc is informative only in refresh builds
    symbol_resolution: Dict[str, Dict[str, object]] = {s: {"used": s, "attempts": [s], "cached": True} for s in symbols}
    write_mapping_doc(pine_sources, symbol_resolution, paths.output_mapping)
    write_readme(paths.output_readme)

    screener_summary: dict = {"rows_by_tf": {}, "by_symbol": {}, "alerts_lookback_bars": int(cfg.alerts_lookback_bars), "generated_utc": run_started_utc}
    state_cache: dict = {}
    try:
        from apps.dashboard.screener_builder import build_screener_rows
        from trading_dashboard.kpis.catalog import KPI_BREAKOUT_ORDER

        try:
            paths.alert_files_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Could not create alert_files dir: %s", e)

        from apps.screener.scan_strategy import _load_scan_list as _load_scan_date_map
        rows_by_tf, by_symbol, state_cache = build_screener_rows(
            all_data=all_data,
            timeframes=timeframes,
            cfg_kpi_weights=dict(cfg.kpi_weights),
            cfg_alerts_lookback_bars=int(cfg.alerts_lookback_bars),
            cfg_combo_kpis_by_tf=cfg.combo_kpis_by_tf,
            cfg_combo_3_kpis=cfg.combo_3_kpis,
            cfg_combo_4_kpis=cfg.combo_4_kpis,
            symbol_display=symbol_display,
            symbol_meta=symbol_meta,
            data_health=data_health,
            stoch_mtm_thresholds=getattr(cfg, "stoch_mtm_thresholds", None),
            strategy_setups=_load_strategy_setups(),
            scan_date_map=_load_scan_date_map(),
        )

        screener_summary = _sanitize_json(  # inf/NaN → None for JSON compliance
            {
                "generated_utc": run_started_utc,
                "alerts_lookback_bars": int(cfg.alerts_lookback_bars),
                "rows_by_tf": rows_by_tf,
                "by_symbol": by_symbol,
            }
        )
        try:
            paths.screener_summary_json.write_text(json.dumps(screener_summary, indent=2, allow_nan=False), encoding="utf-8")
        except Exception:
            logger.exception("Failed to write screener summary JSON")

        export_alerts(
            state_cache=state_cache,
            all_data=all_data,
            alerts_lookback_bars=int(cfg.alerts_lookback_bars),
            kpi_breakout_order=KPI_BREAKOUT_ORDER,
            alert_files_dir=paths.alert_files_dir,
        )

        signal_log_path = paths.alert_files_dir / "combo_signal_log.csv"
        append_combo_signal_log(
            signal_log_path=signal_log_path,
            rows_by_tf=rows_by_tf,
            run_started_utc=run_started_utc,
        )

        dispatch_notifications(signal_log_path)
    except Exception:
        logger.exception("Failed to run KPI/screener refresh")

    # Figures / assets — parallelized via ThreadPoolExecutor
    figs: Dict[str, Dict[str, dict]] = {}
    symbol_to_asset: Dict[str, str] = {}
    if skip_figures:
        logger.info("Skipping figure generation (--skip_figures)")
    elif cfg.dashboard_mode in {"lazy_static", "monolithic"}:
        if cfg.dashboard_mode == "lazy_static":
            try:
                if paths.dashboard_assets_dir.exists():
                    shutil.rmtree(paths.dashboard_assets_dir)
            except Exception as e:
                logger.warning("Could not remove dashboard assets dir: %s", e)
            try:
                paths.dashboard_assets_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning("Could not create dashboard assets dir: %s", e)

        # Prepare work items and directory structure
        fig_tasks: list[tuple] = []
        try:
            from apps.dashboard.sector_map import load_sector_map
            _fig_sector_map = load_sector_map()
        except Exception:
            _fig_sector_map = {}
        for sym, tf_map in all_data.items():
            figs[sym] = {}
            sym_dir = _safe_path_component(sym)
            symbol_to_asset[sym] = sym_dir
            if cfg.dashboard_mode == "lazy_static":
                try:
                    (paths.dashboard_assets_dir / sym_dir).mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    logger.warning("Could not create symbol assets dir: %s", e)
            for tf, df in tf_map.items():
                df_plot = df

                # Pre-compute SMA200 + SMA20 from full data before truncation (1D/1W)
                sma200_ok_plot = None
                sma200_vals_plot = None
                sma20_vals_plot = None
                sma200_ok_full = None
                sma200_vals_full = None
                sma20_vals_full = None
                if tf.upper() in ("1D", "1W") and df is not None and len(df) >= 200:
                    cl_full = df["Close"].to_numpy(float)
                    sma200_raw = pd.Series(cl_full).rolling(200, min_periods=200).mean()
                    sma200_vals_full = sma200_raw.to_numpy()
                    sma20_raw = pd.Series(cl_full).rolling(20, min_periods=20).mean()
                    sma20_vals_full = sma20_raw.to_numpy()
                    # v5: entry gate is SMA20 >= SMA200 (was Close >= SMA200)
                    sma200_ok_full = sma20_vals_full >= sma200_vals_full

                try:
                    if df_plot is not None and not df_plot.empty and int(cfg.plot_lookback_months) > 0:
                        x_end = pd.to_datetime(df_plot.index.max())
                        x_start = x_end - pd.DateOffset(months=int(cfg.plot_lookback_months))
                        df_plot = df_plot.loc[df_plot.index >= x_start].copy()
                except Exception as e:
                    logger.warning("Could not apply plot lookback window: %s", e)
                    df_plot = df

                max_n = int(cfg.max_plot_bars_per_tf.get(tf, 0) or 0)
                if max_n > 0 and df_plot is not None and len(df_plot) > max_n:
                    df_plot = df_plot.tail(max_n).copy()

                # Slice pre-computed SMA200/SMA20 to match truncated df_plot
                if sma200_ok_full is not None and df_plot is not None:
                    offset = len(df) - len(df_plot)
                    sma200_ok_plot = sma200_ok_full[offset:].tolist()
                    sma200_slice = sma200_vals_full[offset:]
                    sma200_vals_plot = [round(float(v), 2) if np.isfinite(v) else None for v in sma200_slice]
                if sma20_vals_full is not None and df_plot is not None:
                    offset = len(df) - len(df_plot)
                    sma20_slice = sma20_vals_full[offset:]
                    sma20_vals_plot = [round(float(v), 2) if np.isfinite(v) else None for v in sma20_slice]

                kpi_state = state_cache.get((sym, tf)) if state_cache else None
                _tf_combos = cfg.combo_kpis_by_tf.get(tf, {})
                _c3 = _tf_combos.get("combo_3", cfg.combo_3_kpis)
                _c4 = _tf_combos.get("combo_4", cfg.combo_4_kpis)
                _plot_offset = len(df) - len(df_plot) if df_plot is not None else 0
                fig_tasks.append((sym, tf, df, df_plot, _plot_offset, symbol_display.get(sym, ""), kpi_state, _c3, _c4, sma200_ok_plot, sma200_vals_plot, sma20_vals_plot))

        def _export_one_data(task: tuple) -> tuple[str, str, str]:
            sym, tf, df_full, df_plot, plot_offset, disp_name, kpi_st, c3_kpis, c4_kpis, sma200_ok, sma200_vals, sma20_vals = task

            # Compute position events on FULL data (single source of truth)
            n_plot = len(df_plot) if df_plot is not None else len(df_full)

            def _remap_events(raw_events):
                out = []
                for ev in raw_events:
                    mapped = dict(ev)
                    mapped["signal_idx"] -= plot_offset
                    mapped["entry_idx"] -= plot_offset
                    mapped["exit_idx"] -= plot_offset
                    if mapped["scale_idx"] is not None:
                        mapped["scale_idx"] -= plot_offset
                    if mapped["exit_idx"] < 0:
                        continue
                    if mapped["entry_idx"] >= n_plot:
                        continue
                    mapped["signal_idx"] = max(mapped["signal_idx"], 0)
                    mapped["entry_idx"] = max(mapped["entry_idx"], 0)
                    out.append(mapped)
                return out

            pos_events_by_strategy: dict[str, list[dict]] = {}
            try:
                from apps.dashboard.strategy import compute_polarity_position_events
                _strat_setups = _load_strategy_setups()
                if kpi_st and df_full is not None and not df_full.empty:
                    for skey, sdef in _strat_setups.items():
                        if sdef.get("entry_type") != "polarity_combo":
                            continue
                        # BUG-T1: resolve per-TF combos before flat fallback
                        _cbytf = sdef.get("combos_by_tf", {})
                        combos = _cbytf.get(tf) or sdef.get("combos", {})
                        c3d = combos.get("c3", {})
                        c4d = combos.get("c4")
                        s_c3_kpis = c3d.get("kpis", [])
                        s_c3_pols = c3d.get("pols", [])
                        s_c4_kpis = c4d.get("kpis") if c4d else None
                        s_c4_pols = c4d.get("pols") if c4d else None
                        exit_def = sdef.get("exit_combos")
                        ex_kpis = exit_def.get("kpis") if exit_def else None
                        ex_pols = exit_def.get("pols") if exit_def else None
                        # BUG-D4: pass per-strategy entry gates
                        _gates = sdef.get("entry_gates")
                        try:
                            raw = compute_polarity_position_events(
                                df_full, kpi_st,
                                s_c3_kpis, s_c3_pols,
                                s_c4_kpis, s_c4_pols, tf,
                                exit_kpis=ex_kpis, exit_pols=ex_pols,
                                entry_gates=_gates,
                            )
                            pos_events_by_strategy[skey] = _remap_events(raw)
                        except Exception as exc:
                            logger.debug("Polarity events failed for %s/%s/%s: %s", sym, tf, skey, exc)
            except Exception as exc:
                logger.warning("Strategy position events failed for %s/%s: %s", sym, tf, exc)

            # Stoof threshold-based position events
            try:
                from apps.dashboard.strategy import compute_atr as _stoof_catr
                from apps.dashboard.strategy import compute_stoof_position_events
                from trading_dashboard.indicators.registry import get_kpi_trend_order as _gkto
                _stoof_def = _load_strategy_setups().get("stoof", {})
                _stoof_active_tfs = _stoof_def.get("active_tfs")
                if (_stoof_def and kpi_st and df_full is not None and not df_full.empty
                        and (not _stoof_active_tfs or tf in _stoof_active_tfs)):
                    _stoof_kpis = _gkto("stoof")
                    _stoof_thresh = int(_stoof_def.get("threshold", 5))
                    _stoof_exit_thresh = int(_stoof_def.get("exit_threshold", _stoof_thresh - 2))
                    _stoof_K = float(_stoof_def.get("atr_multiplier", 3.0))
                    _stoof_atr_tf = _stoof_def.get("atr_tf", "1W")
                    _stoof_req_kpi = _stoof_def.get("required_kpi", "MACD_BL")
                    _stoof_c4_kpi = _stoof_def.get("c4_kpi", "WT_MTF")
                    _stoof_atr_override = None
                    if _stoof_atr_tf and _stoof_atr_tf != tf:
                        _stoof_atr_df = tf_map.get(_stoof_atr_tf)
                        if _stoof_atr_df is not None and not _stoof_atr_df.empty:
                            _stoof_atr_override = _stoof_catr(_stoof_atr_df)
                    raw_stoof = compute_stoof_position_events(
                        df_full, kpi_st, _stoof_kpis, _stoof_thresh, tf,
                        exit_threshold=_stoof_exit_thresh,
                        atr_override=_stoof_atr_override,
                        K_override=_stoof_K,
                        required_kpi=_stoof_req_kpi,
                        c4_kpi=_stoof_c4_kpi,
                    )
                    if raw_stoof:
                        pos_events_by_strategy["stoof"] = _remap_events(raw_stoof)
            except Exception as exc:
                logger.debug("Stoof events failed for %s/%s: %s", sym, tf, exc)

            # Architecture A position events
            try:
                from apps.dashboard.strategy import compute_arch_a_position_events
                _arch_a_def = _load_strategy_setups().get("arch_a", {})
                _arch_a_active_tfs = _arch_a_def.get("active_tfs")
                if (_arch_a_def and df_full is not None and not df_full.empty
                        and (not _arch_a_active_tfs or tf in _arch_a_active_tfs)):
                    _arch_a_K = float(_arch_a_def.get("atr_multiplier", 2.5))
                    _arch_a_weekly = tf_map.get("1W") if tf != "1W" else None
                    raw_arch_a = compute_arch_a_position_events(
                        df_full, tf, K=_arch_a_K, weekly_df=_arch_a_weekly
                    )
                    if raw_arch_a:
                        pos_events_by_strategy["arch_a"] = _remap_events(raw_arch_a)
            except Exception as exc:
                logger.debug("Arch A events failed for %s/%s: %s", sym, tf, exc)

            # Per-bar C3/C4 arrays for every strategy — single source of truth
            c3_states: dict = {}
            try:
                from apps.dashboard.strategy import compute_c3_states_by_strategy
                _strat_setups_c3 = _load_strategy_setups()
                c3_states = compute_c3_states_by_strategy(
                    df_full, kpi_st, _strat_setups_c3, tf, plot_offset)
            except Exception as exc:
                logger.debug("c3_states_by_strategy failed for %s/%s: %s", sym, tf, exc)

            from apps.dashboard.data_exporter import export_symbol_data_json
            data_json = export_symbol_data_json(
                sym, tf, df_plot,
                display_name=disp_name,
                precomputed_kpi_state=kpi_st,
                combo_3_kpis=c3_kpis,
                combo_4_kpis=c4_kpis,
                kpi_weights=dict(cfg.kpi_weights),
                sma200_ok=sma200_ok,
                sma200_vals=sma200_vals,
                sma20_vals=sma20_vals,
                position_events_by_strategy=pos_events_by_strategy,
                c3_states_by_strategy=c3_states,
            )
            return sym, tf, data_json

        _MAX_FIG_WORKERS = min(8, max(1, len(fig_tasks)))
        with ThreadPoolExecutor(max_workers=_MAX_FIG_WORKERS) as pool:
            for sym, tf, data_json in pool.map(_export_one_data, fig_tasks):
                figs[sym][tf] = {}
                if cfg.dashboard_mode == "lazy_static":
                    try:
                        sym_dir = symbol_to_asset[sym]
                        from apps.dashboard.data_exporter import write_symbol_data_asset
                        write_symbol_data_asset(
                            paths.dashboard_assets_dir, sym_dir, tf,
                            data_json, key=f"{sym}|{tf}",
                        )
                    except Exception:
                        logger.exception("Failed to write dashboard data asset")

    # FX rates for EUR toggle
    try:
        from apps.dashboard.sector_map import load_sector_map as _load_sm_fx
        _fx_sector_map = _load_sm_fx()
    except Exception:
        _fx_sector_map = _fig_sector_map if "_fig_sector_map" in dir() else {}
    fx_to_eur, symbol_currencies = _fetch_fx_rates_and_currencies(_fx_sector_map)

    # Persist FX rates for live API serving
    try:
        fx_cache = paths.dashboard_shell_html.parent / "fx_rates.json"
        fx_cache.write_text(json.dumps(fx_to_eur, allow_nan=False), encoding="utf-8")
    except Exception:
        logger.debug("Failed to write FX rates cache")

    # Load exit_params once from config.json (single source of truth)
    _exit_params: dict = {}
    try:
        _exit_params = json.loads(CONFIG_JSON.read_text(encoding="utf-8")).get("exit_params") or {}
    except Exception:
        logger.debug("Failed to load exit_params from config.json")

    # Dashboard shell/HTML
    if cfg.dashboard_mode == "lazy_server":
        try:
            write_lazy_dashboard_shell_html(
                output_path=paths.dashboard_shell_html,
                fig_source="server",
                assets_rel_dir=None,
                symbols=symbols,
                symbol_groups=getattr(cfg, "symbol_groups", None),
                timeframes=timeframes,
                symbol_display=symbol_display,
                symbol_to_asset=None,
                run_metadata=None,
                data_health=data_health,
                symbol_meta=symbol_meta,
                screener_summary=screener_summary,
                exit_params=_exit_params,
                fx_rates=fx_to_eur,
                symbol_currencies=symbol_currencies,
            )
        except Exception:
            logger.exception("Failed to write dashboard shell HTML (lazy_server)")
    else:
        _should_write_shell = bool(figs) or skip_figures
        if _should_write_shell:
            if skip_figures and not symbol_to_asset:
                for sym in all_data:
                    symbol_to_asset[sym] = _safe_path_component(sym)
            if cfg.dashboard_mode == "lazy_static":
                try:
                    write_lazy_dashboard_shell_html(
                        output_path=paths.dashboard_shell_html,
                        fig_source="static_js",
                        assets_rel_dir="dashboard_assets",
                        symbols=symbols,
                        symbol_groups=getattr(cfg, "symbol_groups", None),
                        timeframes=timeframes,
                        symbol_display=symbol_display,
                        symbol_to_asset=symbol_to_asset,
                        run_metadata=None,
                        data_health=data_health,
                        symbol_meta=symbol_meta,
                        screener_summary=screener_summary,
                        exit_params=_exit_params,
                        fx_rates=fx_to_eur,
                        symbol_currencies=symbol_currencies,
                    )
                except Exception:
                    logger.exception("Failed to write dashboard shell HTML (lazy_static)")
    return screener_summary, (symbol_to_asset if symbol_to_asset else None)


def main(argv: list[str] | None = None, _on_export_progress=None) -> int:
    """
    Modes:
    - all (default): export enriched CSVs + refresh dashboard outputs
    - stock_export: export enriched CSVs only (no dashboard HTML/assets, no mapping/readme)
    - refresh_dashboard: generate dashboard HTML/assets + mapping/readme from cached enriched CSVs only (no yfinance)
    - rebuild_ui: same as refresh_dashboard but skips indicator recomputation (fastest — UI-only changes)
    - re_enrich: recompute indicators from cached raw OHLCV (no yfinance) + refresh dashboard
    """
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--mode",
        choices=["all", "stock_export", "refresh_dashboard", "rebuild_ui", "re_enrich"],
        default="all",
        help="Select which phase to run.",
    )
    parser.add_argument("--archive", dest="archive_version", default="")
    parser.add_argument(
        "--export_phase",
        choices=["all", "download", "compute"],
        default="all",
        help="Export phase for mode=all/stock_export. 'compute' runs offline from output_data/ohlcv_raw only.",
    )
    parser.add_argument(
        "--force_recompute_indicators",
        action="store_true",
        help="Bypass cached enriched CSV reuse and recompute indicator columns (useful after changing indicator_config).",
    )
    parser.add_argument(
        "--indicator_config",
        dest="indicator_config_path",
        default=str(INDICATOR_CONFIG_JSON_DEFAULT),
        help="Path to indicator_config JSON (defaults to apps/dashboard/configs/indicator_config.json).",
    )
    parser.add_argument(
        "--skip_figures",
        action="store_true",
        help="Skip Plotly figure generation (screener-only rebuild, much faster).",
    )
    parser.add_argument(
        "--no_parallel_enrich",
        action="store_true",
        help="Disable ProcessPoolExecutor for enrichment (use sequential enrichment).",
    )
    parser.add_argument(
        "--force_download",
        action="store_true",
        help="Bypass the enriched-data TTL cache and re-download all symbols from yfinance.",
    )
    parser.add_argument(
        "--move",
        nargs=3,
        metavar=("TICKER", "FROM_GROUP", "TO_GROUP"),
        help="Move a ticker between groups. E.g. --move IDIA.SW portfolio watchlist",
    )
    args, _ = parser.parse_known_args(argv)
    mode = str(getattr(args, "mode", "all") or "all").strip().lower()
    str(getattr(args, "archive_version", "") or "").strip()
    indicator_config_path_raw = str(getattr(args, "indicator_config_path", "") or "").strip()
    export_phase = str(getattr(args, "export_phase", "all") or "all").strip().lower()
    force_recompute_indicators = bool(getattr(args, "force_recompute_indicators", False))
    force_download = bool(getattr(args, "force_download", False))
    skip_figures = bool(getattr(args, "skip_figures", False))
    use_parallel_enrich = not bool(getattr(args, "no_parallel_enrich", False))
    # --move: move a ticker between groups and exit
    move_args = getattr(args, "move", None)
    if move_args:
        ticker, from_group, to_group = move_args
        from trading_dashboard.symbols.manager import SymbolManager
        _lists_dir = SCRIPT_DIR / "configs" / "lists"
        _sm = SymbolManager.from_lists_dir(_lists_dir, config_path=CONFIG_JSON)
        if _sm.move_symbol(ticker, from_group=from_group, to_group=to_group):
            _sm.sync_lists_dir(_lists_dir)
            logger.info("Moved %s: %s -> %s. CSVs updated.", ticker.upper(), from_group, to_group)
            print(f"Moved {ticker.upper()} from '{from_group}' to '{to_group}'.")
        else:
            logger.warning("Could not move %s from %s (not found in that group).", ticker, from_group)
            print(f"Error: {ticker.upper()} not found in group '{from_group}'.")
        return

    do_export = mode in {"all", "stock_export"}
    do_refresh = mode in {"all", "refresh_dashboard", "rebuild_ui", "re_enrich"}
    skip_recompute = mode == "rebuild_ui"
    # re_enrich: read raw OHLCV cache, recompute indicators, then refresh dashboard (no yfinance)
    do_re_enrich = mode == "re_enrich"
    if do_re_enrich:
        export_phase = "compute"
        force_recompute_indicators = True
        do_export = True
    # Refresh-only must be offline: do NOT call yfinance. Load cached enriched CSVs only.
    force_cached_only = mode in {"refresh_dashboard", "rebuild_ui"}

    # Configure which indicator_config.json to use for this run (default: repo's scripts/indicator_config.json).
    # Accept absolute paths, or relative paths (prefer cwd; fallback to scripts/ dir).
    p = Path(indicator_config_path_raw).expanduser()
    if not p.is_absolute() and not p.exists():
        alt = (SCRIPT_DIR / p).expanduser()
        if alt.exists():
            p = alt
    indicator_config_path = p.resolve()

    run_started_utc = _utc_now_iso()
    t0 = time.perf_counter()

    cfg = load_build_config()
    # Refresh-only should always use what's on disk, regardless of age.
    if force_cached_only:
        cfg = BuildConfig(**{**cfg.__dict__, "cache_ttl_hours": 0.0})

    paths = resolve_paths(cfg)
    paths.output_data_dir.mkdir(parents=True, exist_ok=True)
    paths.output_stock_data_dir.mkdir(parents=True, exist_ok=True)
    paths.output_raw_ohlcv_dir.mkdir(parents=True, exist_ok=True)
    paths.docs_dir.mkdir(parents=True, exist_ok=True)

    # Fast path: rebuild_ui only rewrites the HTML shell from cached metadata.
    # Skips all data loading and Plotly chart generation (~seconds vs ~5 min).
    if mode == "rebuild_ui":
        t0_ui = time.perf_counter()
        try:
            from apps.dashboard.sector_map import load_sector_map as _load_sm_ui
            _smap_ui = _load_sm_ui()
            _sym_display_ui = _derive_symbol_display(_smap_ui)
        except Exception:
            _sym_display_ui = {}
        _screener_ui: dict = {}
        if paths.screener_summary_json.exists():
            try:
                _screener_ui = json.loads(paths.screener_summary_json.read_text(encoding="utf-8"))
            except Exception:
                pass
        _health_ui: dict = {}
        if paths.data_health_json.exists():
            try:
                _health_ui = json.loads(paths.data_health_json.read_text(encoding="utf-8"))
            except Exception:
                pass
        _fx_cache = paths.dashboard_shell_html.parent / "fx_rates.json"
        _fx_ui: dict = {}
        if _fx_cache.exists():
            try:
                _fx_ui = json.loads(_fx_cache.read_text(encoding="utf-8"))
            except Exception:
                pass
        _symbols_ui = cfg.symbols
        _tfs_ui = cfg.timeframes
        _groups_ui = getattr(cfg, "symbol_groups", None)
        _sym_to_asset_ui = {s: _safe_path_component(s) for s in _symbols_ui}
        _exit_params_ui: dict = {}
        try:
            _exit_params_ui = json.loads(CONFIG_JSON.read_text(encoding="utf-8")).get("exit_params") or {}
        except Exception:
            logger.debug("Failed to load exit_params for rebuild-ui")
        write_lazy_dashboard_shell_html(
            output_path=paths.dashboard_shell_html,
            fig_source="server" if cfg.dashboard_mode == "lazy_server" else "static_js",
            assets_rel_dir=None if cfg.dashboard_mode == "lazy_server" else "dashboard_assets",
            symbols=_symbols_ui,
            symbol_groups=_groups_ui,
            timeframes=_tfs_ui,
            symbol_display=_sym_display_ui,
            symbol_to_asset=None if cfg.dashboard_mode == "lazy_server" else _sym_to_asset_ui,
            run_metadata=None,
            data_health=_health_ui,
            symbol_meta={},
            screener_summary=_screener_ui,
            exit_params=_exit_params_ui,
            fx_rates=_fx_ui,
            symbol_currencies={},
        )
        elapsed = time.perf_counter() - t0_ui
        print(f"Shell HTML rebuilt in {elapsed:.1f}s (UI-only fast path).")
        return

    # Export phase
    all_data: dict = {}
    symbol_display: dict = {}
    symbol_meta: dict = {}
    data_health: dict = {}
    indicator_specs: list = []
    if do_export:
        all_data, symbol_display, symbol_meta, data_health, indicator_specs = run_stock_export(
            cfg=cfg,
            paths=paths,
            indicator_config_path=indicator_config_path,
            export_phase=export_phase,
            force_recompute_indicators=force_recompute_indicators,
            force_download=force_download,
            use_parallel_enrich=use_parallel_enrich,
            on_progress=_on_export_progress,
        )
    else:
        try:
            from apps.dashboard.sector_map import load_sector_map as _load_sm
            symbol_display = _derive_symbol_display(_load_sm())
        except Exception as e:
            logger.warning("Could not derive symbol display from sector map: %s", e)

    # Persist health + run meta (always)
    try:
        paths.data_health_json.write_text(json.dumps(data_health, indent=2, allow_nan=False), encoding="utf-8")
    except Exception:
        logger.exception("Failed to write data health JSON")

    run_meta = {
        "started_utc": run_started_utc,
        "symbols": cfg.symbols,
        "timeframes": cfg.timeframes,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "weekly_rule": WEEKLY_RULE,
        "use_cached_output_data": bool(USE_CACHED_OUTPUT_DATA),
        "max_plot_bars_per_tf": cfg.max_plot_bars_per_tf,
        "plot_lookback_months": int(cfg.plot_lookback_months),
        "cache_ttl_hours": float(cfg.cache_ttl_hours),
        "dashboard_mode": cfg.dashboard_mode,
        "tradingview_data_dir": str(TRADINGVIEW_DATA_DIR),
        "config_json": str(CONFIG_JSON),
        "indicator_config_json": str(indicator_config_path),
        "output_data_dir": str(paths.output_data_dir),
        "data_health_gates": {
            "min_bars_by_tf": {k: int(v) for k, v in (cfg.min_bars_by_tf or {}).items()},
            "max_missing_close_pct": float(cfg.max_missing_close_pct),
            "max_missing_volume_pct": float(cfg.max_missing_volume_pct),
        },
        "runtime": _get_runtime_versions(),
    }
    try:
        paths.run_metadata_json.write_text(json.dumps(run_meta, indent=2, allow_nan=False), encoding="utf-8")
    except Exception:
        logger.exception("Failed to write run metadata JSON")

    if not do_refresh:
        elapsed_s = time.perf_counter() - t0
        run_meta["elapsed_seconds"] = float(elapsed_s)
        try:
            paths.run_metadata_json.write_text(json.dumps(run_meta, indent=2, allow_nan=False), encoding="utf-8")
        except Exception:
            logger.exception("Failed to write run metadata JSON")
        print(
            f"Stock export completed in {elapsed_s:.2f}s for {len(cfg.symbols)} symbols × {len(cfg.timeframes)} timeframes. (No dashboard refresh)"
        )
        return 0

    # Refresh phase always offline: load cached CSVs from the resolved output dir
    _t_phase = time.perf_counter()
    cfg_refresh = BuildConfig(**{**cfg.__dict__, "cache_ttl_hours": 0.0})
    paths = resolve_paths(cfg_refresh)
    store_refresh = DataStore(
        enriched_dir=paths.output_stock_data_dir,
        raw_dir=paths.output_raw_ohlcv_dir,
        fmt="parquet",
        cache_ttl_hours=0.0,
        legacy_dirs=[LEGACY_OUTPUT_STOCK_DATA_DIR, paths.output_data_dir],
    )

    # Sector map for enrichment (load cached — fetch was done during export phase if applicable)
    try:
        from apps.dashboard.sector_map import load_sector_map as _load_sm_refresh
        _enrichment_sector_map = _load_sm_refresh()
    except Exception:
        _enrichment_sector_map = {}

    if not symbol_display:
        symbol_display = _derive_symbol_display(_enrichment_sector_map)

    # Reuse already-computed data from export phase when available (avoids double computation)
    if do_export and all_data:
        all_data_refresh = all_data
    else:
        # Parallel parquet loading — I/O bound, so threads are effective.
        all_data_refresh = {}
        _LOAD_WORKERS = min(12, max(1, len(cfg_refresh.symbols)))

        def _load_one_sym(sym):
            return sym, store_refresh.load_all_enriched(sym, cfg_refresh.timeframes, respect_ttl=False)

        with ThreadPoolExecutor(max_workers=_LOAD_WORKERS) as pool:
            for sym, cached in pool.map(_load_one_sym, cfg_refresh.symbols):
                if cached:
                    all_data_refresh[sym] = cached

        _t_load = time.perf_counter() - _t_phase
        print(f"  [timing] Load cached data: {_t_load:.1f}s ({len(all_data_refresh)} symbols, {_LOAD_WORKERS} threads)")

        # Re-enrich cached DataFrames, trimming to ~2 years + lookback buffer for speed.
        # Skip when mode=rebuild_ui (UI-only changes, indicators already in cached CSVs).
        if not skip_recompute:
            _t_enrich_start = time.perf_counter()
            _ENRICH_YEARS = 2
            _ENRICH_WORKERS = min(8, max(1, len(all_data_refresh)))

            _enrich_tasks = []
            for sym, tf_map in all_data_refresh.items():
                for tf, df_cached in tf_map.items():
                    try:
                        tf_meta = TIMEFRAME_REGISTRY.get(tf)
                        buf = tf_meta.enrich_buffer_bars if tf_meta else 300
                        bpy = tf_meta.bars_per_year if tf_meta else 252
                        keep = max(int(bpy * _ENRICH_YEARS) + buf, tf_meta.min_enrich_bars if tf_meta else 0)
                        df_trim = df_cached.iloc[-keep:] if len(df_cached) > keep else df_cached
                        _enrich_tasks.append((sym, tf, df_trim, _enrichment_sector_map.get(sym), indicator_config_path))
                    except Exception:
                        logger.exception("Failed to prepare enrichment task for %s/%s", sym, tf)

            print(f"  [timing] Enriching {len(_enrich_tasks)} symbol/TF pairs with {_ENRICH_WORKERS} workers...")

            with ProcessPoolExecutor(max_workers=_ENRICH_WORKERS) as pool:
                _done = 0
                for sym, tf, enriched, specs in pool.map(_enrich_one_task, _enrich_tasks, chunksize=4, timeout=5400):
                    _done += 1
                    if enriched is not None:
                        all_data_refresh[sym][tf] = enriched
                        if not indicator_specs and specs:
                            indicator_specs = specs
                        try:
                            store_refresh.save_enriched(sym, tf, enriched)
                        except Exception as e:
                            logger.warning("Could not save enriched data: %s", e)
                    if _done % 100 == 0:
                        print(f"  [timing] Enriched {_done}/{len(_enrich_tasks)}...")

            _t_enrich = time.perf_counter() - _t_enrich_start
            print(f"  [timing] Enrichment complete: {_t_enrich:.1f}s ({len(_enrich_tasks)} tasks, {_ENRICH_WORKERS} workers)")

    if not indicator_specs and all_data_refresh:
        try:
            _first_tf_map = next(iter(all_data_refresh.values()))
            df0 = _first_tf_map.get("1W")
            if df0 is None or (hasattr(df0, "empty") and df0.empty):
                df0 = next(iter(_first_tf_map.values()))
            indicator_specs = _extract_specs_from_enriched(df0, indicator_config_path)
        except Exception as e:
            logger.warning("Could not extract indicator specs from refresh data: %s", e)
            indicator_specs = indicator_specs or []

    if _on_export_progress:
        _on_export_progress({"phase": "rebuild", "label": "Rebuilding dashboard\u2026",
                             "detail": "Screener + assets + HTML", "pct": 92})

    _t_dash_start = time.perf_counter()
    screener_summary, _ = run_refresh_dashboard(
        cfg=cfg_refresh,
        paths=paths,
        indicator_config_path=indicator_config_path,
        run_started_utc=run_started_utc,
        all_data=all_data_refresh,
        symbol_display=symbol_display,
        symbol_meta=symbol_meta,
        data_health=data_health,
        indicator_specs=indicator_specs,
        skip_figures=skip_figures,
    )
    _t_dash = time.perf_counter() - _t_dash_start
    print(f"  [timing] Dashboard generation (screener + assets + HTML): {_t_dash:.1f}s")

    elapsed_s = time.perf_counter() - t0
    run_meta["elapsed_seconds"] = float(elapsed_s)
    run_meta["output_html"] = str(paths.output_html)
    run_meta["output_shell_html"] = str(paths.dashboard_shell_html)
    run_meta["dashboard_assets_dir"] = str(paths.dashboard_assets_dir)
    try:
        run_meta["output_html_bytes"] = int(paths.output_html.stat().st_size) if paths.output_html.exists() else None
        run_meta["output_shell_html_bytes"] = int(paths.dashboard_shell_html.stat().st_size) if paths.dashboard_shell_html.exists() else None
        run_meta["dashboard_assets_bytes"] = int(_dir_size_bytes(paths.dashboard_assets_dir)) if paths.dashboard_assets_dir.exists() else None
        paths.run_metadata_json.write_text(json.dumps(run_meta, indent=2, allow_nan=False), encoding="utf-8")
    except Exception:
        logger.exception("Failed to write final run metadata JSON")

    print(f"Dashboard refreshed in {elapsed_s:.2f} seconds for {len(cfg_refresh.symbols)} symbols × {len(cfg_refresh.timeframes)} timeframes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

