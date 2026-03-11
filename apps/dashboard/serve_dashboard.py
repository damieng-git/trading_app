"""
serve_dashboard.py

Lightweight dashboard server with live list management.

Endpoints:
- GET  /                           -> serves the dashboard HTML
- GET  /dashboard_shell.html       -> serves data/dashboard_artifacts/dashboard_shell.html
- GET  /dashboard_assets/*         -> serves static JS assets
- GET  /fig?symbol=...&tf=...      -> returns Plotly figure JSON
- GET  /health                     -> status
- POST /api/move                   -> move a ticker between groups (persists to CSV)
- POST /api/delete                 -> remove a ticker from group + purge data files
- GET  /api/groups                 -> list all groups and their tickers
- GET  /api/scan                   -> SSE stream: runs screener with real-time progress
- GET  /api/refresh                -> SSE stream: re-download + re-enrich all symbols
- POST /api/resolve-ticker         -> search yfinance for ticker matches
- POST /api/add-symbol             -> add ticker to watchlist + enrich
- GET  /api/trades                 -> list trades
- POST /api/trades                 -> create trade
- POST /api/trades/close           -> close a trade
- POST /api/trades/update          -> update a trade
- POST /api/trades/delete          -> delete a trade
- GET  /api/trades/stats           -> trade summary statistics

Run:
  python3 -m apps.dashboard.serve_dashboard

Then open:
  http://localhost:8050
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

logger = logging.getLogger(__name__)


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def _configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)


from apps.dashboard.config_loader import (
    CONFIG_JSON,
    DASHBOARD_ARTIFACTS_DIR,
    DATA_DIR,
    FEATURE_STORE_ENRICHED_DIR,
    PROJECT_ROOT,
    VALID_TIMEFRAMES,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = PROJECT_ROOT
LISTS_DIR = SCRIPT_DIR / "configs" / "lists"

# Default dataset (can be overridden in config.json via `dataset_name`)
DEFAULT_DATASET_NAME = "dashboard"


def _read_config() -> dict:
    try:
        if CONFIG_JSON.exists() and CONFIG_JSON.stat().st_size > 0:
            return json.loads(CONFIG_JSON.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Failed to read config: %s", exc)
        return {}
    return {}


def _dataset_name_from_config() -> str:
    cfg = _read_config()
    dn = str(cfg.get("dataset_name") or DEFAULT_DATASET_NAME).strip()
    return dn or DEFAULT_DATASET_NAME


def _dataset_dir() -> Path:
    return FEATURE_STORE_ENRICHED_DIR / _dataset_name_from_config()


OUTPUT_DATASET_DIR = _dataset_dir()
OUTPUT_STOCK_DATA_DIR = OUTPUT_DATASET_DIR / "stock_data"
LEGACY_OUTPUT_STOCK_DATA_DIR = DATA_DIR / "Stock Data"
SHELL_HTML = DASHBOARD_ARTIFACTS_DIR / "dashboard_shell.html"


def _plot_window(df: pd.DataFrame, *, tf: str) -> pd.DataFrame:
    cfg = _read_config()
    max_plot_bars = cfg.get("max_plot_bars_per_tf") or {}
    lookback_months = int(cfg.get("plot_lookback_months") or 24)

    out = df
    try:
        if out is not None and not out.empty and lookback_months > 0:
            x_end = pd.to_datetime(out.index.max())
            x_start = x_end - pd.DateOffset(months=lookback_months)
            out = out.loc[out.index >= x_start].copy()
    except Exception as exc:
        logger.debug("Failed to apply lookback filter for %s: %s", tf, exc)
        out = df

    try:
        max_n = int((max_plot_bars.get(tf) if isinstance(max_plot_bars, dict) else 0) or 0)
        if max_n > 0 and out is not None and len(out) > max_n:
            out = out.tail(max_n).copy()
    except Exception as exc:
        logger.debug("Failed to apply max plot bars truncation for %s: %s", tf, exc)
        pass
    return out


_VALID_SYMBOL = re.compile(r"^[A-Z0-9^._-]{1,20}$")
_VALID_GROUP = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_MAX_PNL_TRADES = 500
_RATE_LIMIT_MAX = 60
_RATE_LIMIT_WINDOW = 60
_MAX_POST_BODY = 64 * 1024  # 64 KB


class _RateLimiter:
    """Simple per-IP token bucket rate limiter."""
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        import time
        now = time.time()
        with self._lock:
            reqs = self._requests.setdefault(ip, [])
            reqs[:] = [t for t in reqs if now - t < self._window]
            if len(reqs) >= self._max:
                return False
            reqs.append(now)
            return True


_RATE_LIMITER = _RateLimiter(_RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW)


def _is_any_task_running(exclude: str = "") -> bool:
    """Check if any background task (scan/refresh/rebuild_ui/enrich) is running."""
    if exclude != "scan" and SCAN.running:
        return True
    if exclude != "refresh" and REFRESH.running:
        return True
    if exclude != "rebuild_ui" and REBUILD_UI.running:
        return True
    if exclude != "enrich" and ENRICH.running:
        return True
    return False


class _ScanState:
    """Singleton tracking background scan state for SSE reconnection."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._listeners: list[threading.Event] = []
        self._event_log: list[tuple[str, dict]] = []
        self._finished_event = threading.Event()
        self._timeframe: str = "1D"

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, timeframe: str = "1D") -> bool:
        """Start a scan if nothing else is running. Returns False if busy."""
        if _is_any_task_running("scan"):
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._timeframe = timeframe
            self._event_log = []
            self._finished_event.clear()
            for ev in self._listeners:
                ev.set()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def subscribe(self) -> tuple[list[tuple[str, dict]], threading.Event]:
        """Return (snapshot of events so far, notification Event for new ones)."""
        notify = threading.Event()
        with self._lock:
            snapshot = list(self._event_log)
            if self._running:
                self._listeners.append(notify)
            else:
                notify.set()
        return snapshot, notify

    def unsubscribe(self, notify: threading.Event) -> None:
        with self._lock:
            try:
                self._listeners.remove(notify)
            except ValueError:
                pass

    def get_events_since(self, idx: int) -> list[tuple[str, dict]]:
        with self._lock:
            return list(self._event_log[idx:])

    def _emit(self, event: str, data: dict) -> None:
        with self._lock:
            self._event_log.append((event, data))
            for ev in self._listeners:
                ev.set()

    def _run(self) -> None:
        import time as _time

        _t0 = _time.perf_counter()

        def _elapsed() -> float:
            return round(_time.perf_counter() - _t0, 1)

        _saved_cwd = os.getcwd()
        try:
            import sys
            if str(REPO_DIR) not in sys.path:
                sys.path.insert(0, str(REPO_DIR))
            os.chdir(REPO_DIR)

            _timeframe = self._timeframe

            if _timeframe == "all":
                from apps.screener.scan_strategy import run_scan_all_tf as _scanner
                _scan_iter = _scanner(yield_progress=True)
            else:
                from apps.screener.scan_strategy import run_scan_all_strategies as _scanner
                _scan_iter = _scanner(_timeframe, yield_progress=True)

            for event in _scan_iter:
                etype = event.get("type")
                if etype == "progress":
                    self._emit("progress", {
                        "phase": "scan",
                        "pct": event.get("pct", 0),
                        "label": event.get("msg", ""),
                        "elapsed_s": _elapsed(),
                    })
                elif etype == "done":
                    by_strat = event.get("by_strategy") or {}
                    detail = " · ".join(f"{k}: {v}" for k, v in by_strat.items()) if by_strat else ""
                    self._emit("complete", {
                        "total": event.get("count", 0),
                        "detail": detail,
                        "elapsed_s": _elapsed(),
                    })
                elif etype == "error":
                    self._emit("failed", {
                        "message": event.get("msg", "Unknown error"),
                        "phase": "scan",
                        "failures": [{"phase": "scan", "error": event.get("msg", "")}],
                        "elapsed_s": _elapsed(),
                    })
                    return

        except Exception as exc:
            logger.exception("Scan failed")
            self._emit("failed", {
                "message": str(exc), "phase": "unknown",
                "failures": [{"phase": "unknown", "error": str(exc)}],
                "elapsed_s": _elapsed(),
            })
        finally:
            os.chdir(_saved_cwd)
            with self._lock:
                self._running = False
                self._finished_event.set()
                for ev in self._listeners:
                    ev.set()


SCAN = _ScanState()


class _RefreshState:
    """Background refresh: re-download + re-enrich all dashboard symbols, then rebuild."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._listeners: list[threading.Event] = []
        self._event_log: list[tuple[str, dict]] = []
        self._finished_event = threading.Event()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> bool:
        """Start a refresh if nothing else is running. Returns False if busy."""
        if _is_any_task_running("refresh"):
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._event_log = []
            self._finished_event.clear()
            for ev in self._listeners:
                ev.set()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def subscribe(self) -> tuple[list[tuple[str, dict]], threading.Event]:
        notify = threading.Event()
        with self._lock:
            snapshot = list(self._event_log)
            if self._running:
                self._listeners.append(notify)
            else:
                notify.set()
        return snapshot, notify

    def unsubscribe(self, notify: threading.Event) -> None:
        with self._lock:
            try:
                self._listeners.remove(notify)
            except ValueError:
                pass

    def get_events_since(self, idx: int) -> list[tuple[str, dict]]:
        with self._lock:
            return list(self._event_log[idx:])

    def _emit(self, event: str, data: dict) -> None:
        with self._lock:
            self._event_log.append((event, data))
            for ev in self._listeners:
                ev.set()

    def _run(self) -> None:
        import time as _time

        _t0 = _time.perf_counter()

        def _elapsed() -> float:
            return round(_time.perf_counter() - _t0, 1)

        def _on_export_progress(info: dict) -> None:
            rescaled = dict(info)
            raw_pct = info.get("pct", 0)
            rescaled["pct"] = min(round(raw_pct * 70 / 100), 69)
            rescaled["elapsed_s"] = _elapsed()
            self._emit("progress", rescaled)

        _saved_cwd = os.getcwd()
        try:
            import sys
            if str(REPO_DIR) not in sys.path:
                sys.path.insert(0, str(REPO_DIR))
            os.chdir(REPO_DIR)

            self._emit("progress", {"phase": "init", "label": "Starting refresh\u2026",
                                     "pct": 0, "elapsed_s": 0})

            from apps.dashboard.build_dashboard import main as _build_main
            _build_main(["--mode", "all", "--force_download"], _on_export_progress=_on_export_progress)

            self._emit("complete", {
                "detail": "All data refreshed",
                "elapsed_s": _elapsed(),
            })

        except Exception as exc:
            logger.exception("Refresh failed")
            self._emit("failed", {
                "message": str(exc),
                "phase": "unknown",
                "failures": [{"phase": "unknown", "error": str(exc)}],
                "elapsed_s": _elapsed(),
            })
        finally:
            os.chdir(_saved_cwd)
            with self._lock:
                self._running = False
                self._finished_event.set()
                for ev in self._listeners:
                    ev.set()


REFRESH = _RefreshState()


class _RebuildUiState:
    """Background rebuild-ui: regenerate HTML shell from cached data (no download/re-enrich)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._listeners: list[threading.Event] = []
        self._event_log: list[tuple[str, dict]] = []
        self._finished_event = threading.Event()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> bool:
        # rebuild-ui only reads JS/CSS source files and writes dashboard_shell.html.
        # It does not touch OHLCV data or enriched parquets, so it is safe to run
        # concurrently with a scan or full refresh.  Only block a concurrent rebuild-ui.
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._event_log = []
            self._finished_event.clear()
            for ev in self._listeners:
                ev.set()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def subscribe(self) -> tuple[list[tuple[str, dict]], threading.Event]:
        notify = threading.Event()
        with self._lock:
            snapshot = list(self._event_log)
            if self._running:
                self._listeners.append(notify)
            else:
                notify.set()
        return snapshot, notify

    def unsubscribe(self, notify: threading.Event) -> None:
        with self._lock:
            try:
                self._listeners.remove(notify)
            except ValueError:
                pass

    def get_events_since(self, idx: int) -> list[tuple[str, dict]]:
        with self._lock:
            return list(self._event_log[idx:])

    def _emit(self, event: str, data: dict) -> None:
        with self._lock:
            self._event_log.append((event, data))
            for ev in self._listeners:
                ev.set()

    def _run(self) -> None:
        import time as _time
        _t0 = _time.perf_counter()

        def _elapsed() -> float:
            return round(_time.perf_counter() - _t0, 1)

        def _on_progress(info: dict) -> None:
            rescaled = dict(info)
            rescaled["elapsed_s"] = _elapsed()
            self._emit("progress", rescaled)

        _saved_cwd = os.getcwd()
        try:
            import sys
            if str(REPO_DIR) not in sys.path:
                sys.path.insert(0, str(REPO_DIR))
            os.chdir(REPO_DIR)
            self._emit("progress", {"phase": "init", "label": "Rebuilding UI\u2026", "pct": 0, "elapsed_s": 0})
            from apps.dashboard.build_dashboard import main as _build_main
            _build_main(["--mode", "rebuild_ui"], _on_export_progress=_on_progress)
            self._emit("complete", {"detail": "UI rebuilt", "elapsed_s": _elapsed()})
        except Exception as exc:
            logger.exception("Rebuild UI failed")
            self._emit("failed", {
                "message": str(exc),
                "phase": "unknown",
                "failures": [{"phase": "unknown", "error": str(exc)}],
                "elapsed_s": _elapsed(),
            })
        finally:
            os.chdir(_saved_cwd)
            with self._lock:
                self._running = False
                self._finished_event.set()
                for ev in self._listeners:
                    ev.set()


REBUILD_UI = _RebuildUiState()


class _EnrichState:
    """Background batch enrichment for newly added tickers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._listeners: list[threading.Event] = []
        self._event_log: list[tuple[str, dict]] = []
        self._finished_event = threading.Event()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, tickers: list[str], group: str = "watchlist") -> bool:
        if _is_any_task_running("enrich"):
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._event_log = []
            self._finished_event.clear()
            for ev in self._listeners:
                ev.set()
            self._thread = threading.Thread(
                target=self._run, args=(tickers, group), daemon=True)
            self._thread.start()
            return True

    def subscribe(self) -> tuple[list[tuple[str, dict]], threading.Event]:
        notify = threading.Event()
        with self._lock:
            snapshot = list(self._event_log)
            if self._running:
                self._listeners.append(notify)
            else:
                notify.set()
        return snapshot, notify

    def unsubscribe(self, notify: threading.Event) -> None:
        with self._lock:
            try:
                self._listeners.remove(notify)
            except ValueError:
                pass

    def get_events_since(self, idx: int) -> list[tuple[str, dict]]:
        with self._lock:
            return list(self._event_log[idx:])

    def _emit(self, event: str, data: dict) -> None:
        with self._lock:
            self._event_log.append((event, data))
            for ev in self._listeners:
                ev.set()

    def _run(self, tickers: list[str], group: str) -> None:
        import time as _time

        _t0 = _time.perf_counter()

        def _elapsed() -> float:
            return round(_time.perf_counter() - _t0, 1)

        _saved_cwd = os.getcwd()
        try:
            import sys
            if str(REPO_DIR) not in sys.path:
                sys.path.insert(0, str(REPO_DIR))
            os.chdir(REPO_DIR)

            self._emit("progress", {"phase": "init", "label": "Starting enrichment…",
                                     "pct": 0, "elapsed_s": 0, "total": len(tickers)})

            from trading_dashboard.symbols.manager import SymbolManager
            sm = SymbolManager.from_lists_dir(LISTS_DIR, config_path=CONFIG_JSON)
            for t in tickers:
                t_upper = t.strip().upper()
                if group not in sm.find_groups(t_upper):
                    sm.add_symbol(t_upper, group=group)
            sm.sync_lists_dir(LISTS_DIR)

            def _on_progress(info: dict) -> None:
                info["elapsed_s"] = _elapsed()
                self._emit("progress", info)

            from apps.dashboard.build_dashboard import enrich_symbols
            result = enrich_symbols(tickers, on_progress=_on_progress)

            enriched = result.get("enriched", [])
            failed = result.get("failed", [])
            total = result.get("total", len(tickers))

            if failed and not enriched:
                self._emit("failed", {
                    "message": f"All {total} tickers failed",
                    "enriched": 0, "failed": len(failed), "total": total,
                    "failed_tickers": failed,
                    "elapsed_s": _elapsed(),
                })
            elif failed:
                self._emit("complete", {
                    "detail": f"{len(enriched)}/{total} stocks added to {group}",
                    "enriched": len(enriched), "failed": len(failed), "total": total,
                    "enriched_tickers": enriched, "failed_tickers": failed,
                    "group": group, "elapsed_s": _elapsed(),
                })
            else:
                self._emit("complete", {
                    "detail": f"{len(enriched)} stocks added to {group}",
                    "enriched": len(enriched), "total": total,
                    "enriched_tickers": enriched, "group": group,
                    "elapsed_s": _elapsed(),
                })

        except Exception as exc:
            logger.exception("Enrichment failed")
            self._emit("failed", {
                "message": str(exc), "phase": "unknown",
                "elapsed_s": _elapsed(),
            })
        finally:
            os.chdir(_saved_cwd)
            with self._lock:
                self._running = False
                self._finished_event.set()
                for ev in self._listeners:
                    ev.set()


ENRICH = _EnrichState()


_EXCHANGE_SUFFIXES = (
    ".PA", ".DE", ".L", ".MI", ".AS", ".SW", ".TO", ".ST", ".MC",
    ".IR", ".HE", ".OL", ".CO", ".IS", ".VI", ".WA", ".SA", ".HK",
    ".AX", ".SI", ".T", ".KS", ".KQ", ".MX", ".NS", ".BO",
)


def _resolve_ticker_search(query: str) -> list[dict]:
    """Search yfinance for ticker matches using yf.Search + suffix fallback."""
    import yfinance as yf

    seen: set[str] = set()
    results: list[dict] = []

    def _add_result(ticker_str: str) -> bool:
        if ticker_str in seen or len(results) >= 8:
            return False
        seen.add(ticker_str)
        try:
            tk = yf.Ticker(ticker_str)
            info = tk.info or {}
            name = info.get("shortName") or info.get("longName") or ""
            if not name and not info.get("regularMarketPrice"):
                return False
            results.append({
                "ticker": ticker_str,
                "name": name,
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "currency": info.get("currency", ""),
                "exchange": info.get("exchange", ""),
                "price": info.get("regularMarketPrice"),
                "quoteType": info.get("quoteType", ""),
            })
            return True
        except Exception:
            return False

    try:
        search = yf.Search(query, max_results=10)
        quotes = getattr(search, "quotes", None) or []
        for q in quotes[:8]:
            sym = q.get("symbol", "")
            if sym:
                _add_result(sym)
            if len(results) >= 5:
                break
    except Exception:
        logger.debug("yf.Search failed for %r, falling back to suffix probe", query)

    if len(results) < 3 and not any(c in query for c in ".=^"):
        _add_result(query.upper())
        for suffix in _EXCHANGE_SUFFIXES:
            _add_result(query.upper() + suffix)
            if len(results) >= 5:
                break

    return results[:8]


def _add_symbol_to_group(ticker: str, group: str) -> dict:
    """Add a ticker to a group CSV. Returns result dict."""
    from trading_dashboard.symbols.manager import SymbolManager
    sm = SymbolManager.from_lists_dir(LISTS_DIR, config_path=CONFIG_JSON)

    existing_groups = sm.find_groups(ticker)
    if group in existing_groups:
        return {"ok": False, "error": f"{ticker} already in {group}"}

    sm.add_symbol(ticker, group=group)
    sm.sync_lists_dir(LISTS_DIR)
    return {"ok": True, "ticker": ticker, "group": group, "groups": sm.groups}


def _compute_pnl_summary(group: str, tf: str, strategy: str = "legacy") -> dict:
    """Compute portfolio-level backtest P&L for all symbols in a group.

    BUG-PL3 fix: strategy-aware.  Pass strategy="trend"|"dip_buy"|"swing"|"stoof"
    to use the appropriate engine.  "legacy" (default) uses combo_kpis_by_tf.
    """
    from apps.dashboard.config_loader import load_build_config
    from apps.dashboard.strategy import (
        compute_polarity_position_events,
        compute_position_events,
        compute_stoof_position_events,
    )
    from trading_dashboard.kpis.catalog import compute_kpi_state_map
    from trading_dashboard.symbols.manager import SymbolManager

    sm = SymbolManager.from_lists_dir(LISTS_DIR, config_path=CONFIG_JSON)
    cfg = load_build_config()

    if group == "all":
        syms = sorted(set(s for g in sm.groups.values() for s in g))
    else:
        syms = sm.group(group) if group in sm.groups else []

    # Resolve combos/KPIs based on requested strategy
    _strat_setups = cfg.strategy_setups if hasattr(cfg, "strategy_setups") else {}
    _sdef = _strat_setups.get(strategy, {})
    _use_polarity = _sdef.get("entry_type") == "polarity_combo"
    _use_stoof = strategy == "stoof" and _sdef.get("entry_type") == "threshold"

    if _use_polarity:
        _cbytf = _sdef.get("combos_by_tf", {})
        _combos = _cbytf.get(tf) or _sdef.get("combos", {})
        s_c3_kpis = _combos.get("c3", {}).get("kpis", [])
        s_c3_pols = _combos.get("c3", {}).get("pols", [])
        _c4d = _combos.get("c4")
        s_c4_kpis = _c4d.get("kpis") if _c4d else None
        s_c4_pols = _c4d.get("pols") if _c4d else None
        _exit_def = _sdef.get("exit_combos")
        ex_kpis = _exit_def.get("kpis") if _exit_def else None
        ex_pols = _exit_def.get("pols") if _exit_def else None
        _gates = _sdef.get("entry_gates")
        # Only run if entry_tf matches or no entry_tf restriction
        _entry_tf = _sdef.get("entry_tf", tf)
    elif _use_stoof:
        from trading_dashboard.indicators.registry import get_kpi_trend_order as _gkto
        _stoof_kpis = _gkto("stoof")
        _stoof_thresh = int(_sdef.get("threshold", 7))
        _stoof_exit_thresh = int(_sdef.get("exit_threshold", _stoof_thresh - 2))
        _stoof_K = float(_sdef.get("atr_multiplier", 3.0))
        _stoof_atr_tf = _sdef.get("atr_tf", "1W")
    else:
        # Legacy: combo_kpis_by_tf
        combo_cfg = cfg.combo_kpis_by_tf.get(tf, cfg.combo_kpis_by_tf.get("1D", {}))
        c3_kpis = combo_cfg.get("combo_3", cfg.combo_3_kpis)
        c4_kpis = combo_cfg.get("combo_4", cfg.combo_4_kpis)

    try:
        from apps.dashboard.build_dashboard import _derive_symbol_display
        from apps.dashboard.sector_map import load_sector_map
        smap = load_sector_map()
        sym_display = _derive_symbol_display(smap)
    except Exception as exc:
        logger.debug("Failed to load sector map for PnL display: %s", exc)
        sym_display = {}

    per_symbol = []
    all_trades = []

    for sym in syms:
        df = CACHE.load_df(sym, tf)
        if df is None or df.empty or len(df) < 20:
            continue
        try:
            st = compute_kpi_state_map(df)
            if _use_polarity:
                if _entry_tf != tf:
                    continue
                events = compute_polarity_position_events(
                    df, st, s_c3_kpis, s_c3_pols, s_c4_kpis, s_c4_pols, tf,
                    exit_kpis=ex_kpis, exit_pols=ex_pols, entry_gates=_gates)
            elif _use_stoof:
                _stoof_atr_override = None
                if _stoof_atr_tf and _stoof_atr_tf != tf:
                    from apps.dashboard.strategy import compute_atr as _stoof_catr
                    _df_atr = CACHE.load_df(sym, _stoof_atr_tf)
                    if _df_atr is not None and not _df_atr.empty:
                        _stoof_atr_override = _stoof_catr(_df_atr)
                events = compute_stoof_position_events(
                    df, st, _stoof_kpis, _stoof_thresh, tf,
                    exit_threshold=_stoof_exit_thresh,
                    atr_override=_stoof_atr_override,
                    K_override=_stoof_K,
                )
            else:
                events = compute_position_events(df, st, c3_kpis, c4_kpis, tf)
        except Exception as exc:
            logger.debug("PnL compute skipped for %s: %s", sym, exc)
            continue

        closed = [e for e in events if e.get("exit_reason") != "Open" and e.get("ret_pct") is not None]
        open_pos = [e for e in events if e.get("exit_reason") == "Open"]
        if not closed and not open_pos:
            continue

        dates = [str(d) for d in df.index]
        # BUG-PL1 fix: ret_pct is now unweighted; apply 1.5x for scaled trades.
        weighted_pnls = [e["ret_pct"] * (1.5 if e.get("scaled") else 1.0) for e in closed]
        total_ret = sum(weighted_pnls)
        wins = [p for p in weighted_pnls if p >= 0]
        losses = [p for p in weighted_pnls if p < 0]
        hr = len(wins) / len(weighted_pnls) * 100 if weighted_pnls else 0

        display_name = sym_display.get(sym, sym)
        sym_summary = {
            "symbol": sym, "name": display_name,
            "trades": len(closed), "return": round(total_ret, 2),
            "hit_rate": round(hr, 1),
            "avg_gain": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "has_open": len(open_pos) > 0,
        }
        per_symbol.append(sym_summary)

        for e in closed:
            entry_date = dates[min(e["entry_idx"], len(dates) - 1)] if e.get("entry_idx") is not None else ""
            exit_date = dates[min(e["exit_idx"], len(dates) - 1)] if e.get("exit_idx") is not None else ""
            # BUG-PL1 fix: apply weight here; ret_pct is now raw (unweighted).
            w = 1.5 if e.get("scaled") else 1.0
            all_trades.append({
                "symbol": sym, "name": display_name,
                "entry": entry_date, "exit": exit_date,
                "ret": round(e["ret_pct"] * w, 2),
                "hold": e.get("hold", 0),
                "label": "C4" if e.get("scaled") else "C3",
                "reason": e.get("exit_reason", ""),
            })

    all_trades.sort(key=lambda t: t.get("exit", ""))

    eq_dates = []
    eq_values = []
    cum_ret = 0.0
    for t in all_trades:
        cum_ret += t["ret"]
        eq_dates.append(t["exit"])
        eq_values.append(round(cum_ret, 2))

    dd_values = []
    peak = float("-inf")
    for v in eq_values:
        if v > peak:
            peak = v
        dd_values.append(round(v - peak, 2))

    total_return = cum_ret
    total_trades = len(all_trades)
    wins_all = [t["ret"] for t in all_trades if t["ret"] >= 0]
    losses_all = [t["ret"] for t in all_trades if t["ret"] < 0]
    hr_all = len(wins_all) / total_trades * 100 if total_trades else 0
    avg_gain = sum(wins_all) / len(wins_all) if wins_all else 0
    avg_loss = sum(losses_all) / len(losses_all) if losses_all else 0
    max_dd = min(dd_values) if dd_values else 0
    pf = sum(wins_all) / abs(sum(losses_all)) if losses_all and sum(losses_all) != 0 else float("inf")
    best = max(t["ret"] for t in all_trades) if all_trades else 0
    worst = min(t["ret"] for t in all_trades) if all_trades else 0

    import math
    rets = [t["ret"] for t in all_trades]
    mean_ret = sum(rets) / len(rets) if rets else 0
    std_ret = math.sqrt(sum((r - mean_ret) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0
    sharpe = (mean_ret / std_ret) * math.sqrt(len(rets)) if std_ret > 0 else 0

    portfolio = {
        "total_return": round(total_return, 2),
        "total_trades": total_trades,
        "win_rate": round(hr_all, 1),
        "avg_gain": round(avg_gain, 2),
        "avg_loss": round(avg_loss, 2),
        "max_dd": round(max_dd, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else None,
        "sharpe": round(sharpe, 2),
        "best": round(best, 2),
        "worst": round(worst, 2),
        "equity_curve": {"dates": eq_dates, "values": eq_values},
    }

    return {
        "portfolio": portfolio,
        "per_symbol": sorted(per_symbol, key=lambda s: s["return"], reverse=True),
        "all_trades": all_trades[-_MAX_PNL_TRADES:],
    }


_CACHE_MAX_ENTRIES = 500


class _Caches:
    def __init__(self) -> None:
        self.df_by_key: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
        self.fig_by_key: dict[tuple[str, str], tuple[float, str]] = {}
        self._indicator_specs: list | None = None
        self._lock = threading.Lock()

    def _evict_if_full(self) -> None:
        """Remove oldest entries when cache exceeds max size. Must be called under lock."""
        if len(self.df_by_key) > _CACHE_MAX_ENTRIES:
            excess = len(self.df_by_key) - _CACHE_MAX_ENTRIES
            oldest = sorted(self.df_by_key, key=lambda k: self.df_by_key[k][0])[:excess]
            for k in oldest:
                self.df_by_key.pop(k, None)
                self.fig_by_key.pop(k, None)
        if len(self.fig_by_key) > _CACHE_MAX_ENTRIES:
            excess = len(self.fig_by_key) - _CACHE_MAX_ENTRIES
            oldest = sorted(self.fig_by_key, key=lambda k: self.fig_by_key[k][0])[:excess]
            for k in oldest:
                self.fig_by_key.pop(k, None)

    def _find_data_file(self, symbol: str, tf: str) -> Path | None:
        dataset_dir = _dataset_dir()
        for ext in ("parquet", "csv"):
            p = dataset_dir / "stock_data" / f"{symbol}_{tf}.{ext}"
            if p.exists():
                return p
        for p_legacy in (LEGACY_OUTPUT_STOCK_DATA_DIR / f"{symbol}_{tf}.csv",
                         dataset_dir / f"{symbol}_{tf}.csv"):
            if p_legacy.exists():
                return p_legacy
        return None

    def _get_indicator_specs(self, df: pd.DataFrame) -> list:
        if self._indicator_specs is not None:
            return self._indicator_specs
        try:
            from trading_dashboard.data.enrichment import translate_and_compute_indicators
            indicator_cfg = SCRIPT_DIR / "configs" / "indicator_config.json"
            _, specs = translate_and_compute_indicators(df, indicator_config_path=indicator_cfg)
            self._indicator_specs = specs
            return specs
        except Exception as exc:
            logger.debug("Failed to load indicator specs: %s", exc)
            return []

    def load_df(self, symbol: str, tf: str) -> pd.DataFrame:
        p = self._find_data_file(symbol, tf)
        if p is None:
            return pd.DataFrame()
        try:
            mt = float(p.stat().st_mtime)
        except Exception as exc:
            logger.debug("Could not get mtime for %s: %s", p, exc)
            mt = 0.0

        key = (symbol, tf)
        with self._lock:
            cached = self.df_by_key.get(key)
            if cached and cached[0] == mt:
                return cached[1]

        try:
            if str(p).endswith(".parquet"):
                df = pd.read_parquet(p)
            else:
                df = pd.read_csv(p, parse_dates=[0], index_col=0)
        except Exception as exc:
            logger.debug("Failed to read data file %s: %s", p, exc)
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
        df = df.sort_index()
        with self._lock:
            self.df_by_key[key] = (mt, df)
            self.fig_by_key.pop(key, None)
            self._evict_if_full()
        return df

    def load_fig_json(self, symbol: str, tf: str) -> str:
        df = self.load_df(symbol, tf)
        if df.empty:
            return json.dumps({"data": [], "layout": {"title": f"{symbol} — {tf} (no data)"}})

        p = self._find_data_file(symbol, tf)
        try:
            mt = float(p.stat().st_mtime) if p else 0.0
        except Exception as exc:
            logger.debug("Could not get mtime for fig %s: %s", p, exc)
            mt = 0.0

        key = (symbol, tf)
        with self._lock:
            cached = self.fig_by_key.get(key)
            if cached and cached[0] == mt:
                return cached[1]

        from apps.dashboard.figures import build_figure_for_symbol_timeframe
        from apps.dashboard.figures_layout import _safe_plotly_json_dumps

        cfg = _read_config()
        tf_combos = cfg.get("combo_kpis_by_tf", {}).get(tf, {})
        c3 = tf_combos.get("combo_3", cfg.get("combo_3_kpis"))
        c4 = tf_combos.get("combo_4", cfg.get("combo_4_kpis"))

        specs = self._get_indicator_specs(df)
        df_plot = _plot_window(df, tf=tf)
        fig = build_figure_for_symbol_timeframe(
            symbol, tf, df_plot, specs,
            combo_3_kpis=c3, combo_4_kpis=c4,
        )
        payload = _safe_plotly_json_dumps(fig.to_plotly_json())
        with self._lock:
            self.fig_by_key[key] = (mt, payload)
        return payload


CACHE = _Caches()

_pnl_cache: dict[str, tuple[float, dict]] = {}
_pnl_cache_lock = threading.Lock()
_PNL_CACHE_TTL = 60.0


class Handler(BaseHTTPRequestHandler):
    def _check_auth(self) -> bool:
        """Optional Basic Auth. Set AUTH_USER and AUTH_PASS env vars to enable."""
        user = os.environ.get("AUTH_USER", "")
        passwd = os.environ.get("AUTH_PASS", "")
        if not user:
            return True
        import base64
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Dashboard"')
            self.end_headers()
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            provided_user, provided_pass = decoded.split(":", 1)
            if provided_user == user and provided_pass == passwd:
                return True
        except Exception as exc:
            logger.debug("Auth decode failed: %s", exc)
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Dashboard"')
        self.end_headers()
        return False

    def _send(self, status: int, body: bytes, *, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", os.environ.get("CORS_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        self._send(HTTPStatus.OK, b"", content_type="text/plain")

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        client_ip = self.client_address[0]
        if not _RATE_LIMITER.allow(client_ip):
            self._send(HTTPStatus.TOO_MANY_REQUESTS, b'{"error":"Rate limit exceeded"}', content_type="application/json")
            return
        u = urlparse(self.path)
        path = u.path or "/"

        if path in ("/", "/index.html"):
            # Redirect to the shell (relative so it works under any path prefix, e.g. /test/).
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "dashboard_shell.html")
            self.end_headers()
            return

        if path == "/health":
            body = json.dumps({"ok": True}).encode("utf-8")
            self._send(HTTPStatus.OK, body, content_type="application/json")
            return

        if path == "/dashboard_shell.html":
            if not SHELL_HTML.exists():
                msg = (
                    "Missing data/dashboard_artifacts/dashboard_shell.html. "
                    "Run: python3 -m apps.dashboard.refresh_dashboard (with dashboard_mode=lazy_server)."
                )
                self._send(HTTPStatus.NOT_FOUND, msg.encode("utf-8"), content_type="text/plain; charset=utf-8")
                return
            body = SHELL_HTML.read_bytes()
            self._send(HTTPStatus.OK, body, content_type="text/html; charset=utf-8")
            return

        if path == "/fig":
            qs = parse_qs(u.query or "")
            symbol = (qs.get("symbol", [""])[0] or "").strip().upper()
            tf = (qs.get("tf", [""])[0] or "").strip().upper()
            if not symbol or tf not in VALID_TIMEFRAMES or not _VALID_SYMBOL.match(symbol):
                self._send(HTTPStatus.BAD_REQUEST, b"Bad request", content_type="text/plain; charset=utf-8")
                return
            payload = CACHE.load_fig_json(symbol, tf).encode("utf-8")
            self._send(HTTPStatus.OK, payload, content_type="application/json; charset=utf-8")
            return

        # Serve other small files from dashboard_artifacts/ if needed
        candidate = (DASHBOARD_ARTIFACTS_DIR / path.lstrip("/")).resolve()
        try:
            if DASHBOARD_ARTIFACTS_DIR.resolve() in candidate.parents and candidate.exists() and candidate.is_file():
                ct = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
                self._send(HTTPStatus.OK, candidate.read_bytes(), content_type=ct)
                return
        except Exception as exc:
            logger.debug("Failed to serve static file %s: %s", candidate, exc)
            pass

        if path == "/api/scan":
            self._handle_scan_sse()
            return

        if path == "/api/refresh":
            self._handle_refresh_sse()
            return

        if path == "/api/enrich":
            self._handle_enrich_sse()
            return

        if path == "/api/rebuild-ui":
            self._handle_rebuild_ui_sse()
            return

        if path == "/api/scan/status":
            status_obj = {
                "scan_running": SCAN.running,
                "refresh_running": REFRESH.running,
                "rebuild_ui_running": REBUILD_UI.running,
                "enrich_running": ENRICH.running,
            }
            body = json.dumps({"ok": True, "data": status_obj}).encode("utf-8")
            self._send(HTTPStatus.OK, body, content_type="application/json")
            return

        if path == "/api/groups":
            try:
                from trading_dashboard.symbols.manager import SymbolManager
                sm = SymbolManager.from_lists_dir(LISTS_DIR, config_path=CONFIG_JSON)
                body = json.dumps({"ok": True, "data": sm.groups}, indent=2).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/screener-data":
            try:
                screener_path = DASHBOARD_ARTIFACTS_DIR / "screener_summary.json"
                if screener_path.exists():
                    raw_data = json.loads(screener_path.read_text(encoding="utf-8"))
                    body = json.dumps({"ok": True, "data": raw_data}).encode("utf-8")
                    self._send(HTTPStatus.OK, body, content_type="application/json; charset=utf-8")
                else:
                    self._send(HTTPStatus.NOT_FOUND, b'{"error":"no screener data"}', content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/scan-log":
            try:
                log_path = DASHBOARD_ARTIFACTS_DIR / "scan_log.jsonl"
                entries: list[dict] = []
                if log_path.exists():
                    for line in log_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                body = json.dumps({"ok": True, "data": entries}).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/symbol-data":
            try:
                from trading_dashboard.symbols.manager import SymbolManager
                sm = SymbolManager.from_lists_dir(LISTS_DIR, config_path=CONFIG_JSON)
                symbols = sorted(set(s for g in sm.groups.values() for s in g))

                # Display names + currencies from sector_map
                sym_display = {}
                sym_currencies = {}
                fx_to_eur = {"EUR": 1.0}
                try:
                    from apps.dashboard.sector_map import load_sector_map
                    smap = load_sector_map()
                    from apps.dashboard.build_dashboard import _derive_symbol_display
                    sym_display = _derive_symbol_display(smap)
                    for s, info in smap.items():
                        fund = info.get("fundamentals") or {}
                        ccy = (fund.get("currency") or info.get("currency") or "").upper()
                        if ccy:
                            sym_currencies[s] = ccy
                    # FX cache file
                    fx_cache = DASHBOARD_ARTIFACTS_DIR / "fx_rates.json"
                    if fx_cache.exists():
                        fx_to_eur = json.loads(fx_cache.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("Failed to load symbol display / FX data: %s", exc)

                payload = {
                    "symbols": symbols,
                    "groups": sm.groups,
                    "symbol_display": sym_display,
                    "symbol_currencies": sym_currencies,
                    "fx_to_eur": fx_to_eur,
                }
                body = json.dumps({"ok": True, "data": payload}, allow_nan=False).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/pnl-summary":
            qs = parse_qs(u.query or "")
            group = (qs.get("group", ["all"])[0] or "all").strip()
            tf = (qs.get("tf", ["1D"])[0] or "1D").strip().upper()
            # BUG-PL3 fix: accept strategy param to use the correct engine
            pnl_strategy = (qs.get("strategy", ["legacy"])[0] or "legacy").strip()
            if tf not in VALID_TIMEFRAMES:
                tf = "1D"
            try:
                import time
                cache_key = f"{group}|{tf}|{pnl_strategy}"
                with _pnl_cache_lock:
                    cached = _pnl_cache.get(cache_key)
                    if cached and (time.time() - cached[0]) < _PNL_CACHE_TTL:
                        body = json.dumps({"ok": True, "data": cached[1]}, allow_nan=False).encode("utf-8")
                        self._send(HTTPStatus.OK, body, content_type="application/json; charset=utf-8")
                        return
                result = _compute_pnl_summary(group, tf, strategy=pnl_strategy)
                with _pnl_cache_lock:
                    _pnl_cache[cache_key] = (time.time(), result)
                body = json.dumps({"ok": True, "data": result}, allow_nan=False).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/trades":
            qs = parse_qs(u.query or "")
            status = qs.get("status", [None])[0]
            symbol = qs.get("symbol", [None])[0]
            try:
                from apps.dashboard.trades import list_trades
                rows = list_trades(status=status, symbol=symbol)
                body = json.dumps({"ok": True, "data": rows}).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/trades/stats":
            try:
                from apps.dashboard.trades import trade_stats
                stats = trade_stats()
                body = json.dumps({"ok": True, "data": stats}).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        self._send(HTTPStatus.NOT_FOUND, b"Not found", content_type="text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        client_ip = self.client_address[0]
        if not _RATE_LIMITER.allow(client_ip):
            self._send(HTTPStatus.TOO_MANY_REQUESTS, b'{"error":"Rate limit exceeded"}', content_type="application/json")
            return
        u = urlparse(self.path)
        path = u.path or "/"

        if path == "/api/move":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                ticker = str(data.get("ticker", "")).strip().upper()
                from_group = str(data.get("from", "")).strip()
                to_group = str(data.get("to", "")).strip()
                if not ticker or not from_group or not to_group:
                    body = json.dumps({"error": "Missing ticker, from, or to"}).encode("utf-8")
                    self._send(HTTPStatus.BAD_REQUEST, body, content_type="application/json")
                    return
                if not _VALID_SYMBOL.match(ticker):
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Invalid ticker"}).encode(), content_type="application/json")
                    return
                if not _VALID_GROUP.match(from_group) or not _VALID_GROUP.match(to_group):
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Invalid group name"}).encode(), content_type="application/json")
                    return

                from trading_dashboard.symbols.manager import SymbolManager
                sm = SymbolManager.from_lists_dir(LISTS_DIR, config_path=CONFIG_JSON)
                ok = sm.move_symbol(ticker, from_group=from_group, to_group=to_group)
                if not ok:
                    actual_groups = sm.find_groups(ticker)
                    if actual_groups:
                        from_group = actual_groups[0]
                        ok = sm.move_symbol(ticker, from_group=from_group, to_group=to_group)
                if ok:
                    sm.sync_lists_dir(LISTS_DIR)
                    result = {"ok": True, "moved": ticker.upper(), "from": from_group, "to": to_group, "groups": sm.groups}
                    logger.info("Moved %s: %s -> %s", ticker.upper(), from_group, to_group)
                else:
                    result = {"ok": False, "error": f"{ticker.upper()} not found in any group"}

                body = json.dumps(result).encode("utf-8")
                self._send(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/resolve-ticker":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                query = str(data.get("query", "")).strip()
                if not query:
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Missing query"}).encode(), content_type="application/json")
                    return
                results = _resolve_ticker_search(query)
                body = json.dumps({"ok": True, "data": {"results": results}}).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/add-symbol":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                ticker = str(data.get("ticker", "")).strip().upper()
                group = str(data.get("group", "watchlist")).strip() or "watchlist"
                if not ticker:
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Missing ticker"}).encode(), content_type="application/json")
                    return
                if not _VALID_SYMBOL.match(ticker):
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Invalid ticker"}).encode(), content_type="application/json")
                    return
                if not _VALID_GROUP.match(group):
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Invalid group name"}).encode(), content_type="application/json")
                    return
                result = _add_symbol_to_group(ticker, group)
                status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
                body = json.dumps(result).encode("utf-8")
                self._send(status, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/enrich-symbols":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                tickers = data.get("tickers", [])
                group = str(data.get("group", "watchlist")).strip() or "watchlist"
                if not tickers or not isinstance(tickers, list):
                    self._send(HTTPStatus.BAD_REQUEST,
                               json.dumps({"error": "Missing tickers array"}).encode(),
                               content_type="application/json")
                    return
                tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
                if not tickers:
                    self._send(HTTPStatus.BAD_REQUEST,
                               json.dumps({"error": "No valid tickers"}).encode(),
                               content_type="application/json")
                    return
                started = ENRICH.start(tickers, group)
                if not started:
                    self._send(HTTPStatus.CONFLICT,
                               json.dumps({"error": "Another task is already running"}).encode(),
                               content_type="application/json")
                    return
                body = json.dumps({"ok": True, "tickers": tickers, "group": group}).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/trades":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                from apps.dashboard.trades import add_trade
                trade = add_trade(
                    symbol=str(data.get("symbol", "")),
                    entry_price=float(data.get("entry_price", 0)),
                    entry_date=str(data.get("entry_date", "")),
                    timeframe=str(data.get("timeframe", "1D")),
                    direction=str(data.get("direction", "long")),
                    size=float(data.get("size", 1.0)),
                    stop_price=float(data["stop_price"]) if data.get("stop_price") else None,
                    notes=str(data.get("notes", "")),
                    currency=str(data.get("currency", "USD")),
                )
                body = json.dumps({"ok": True, "trade": trade}).encode("utf-8")
                self._send(HTTPStatus.OK, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/trades/close":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                from apps.dashboard.trades import close_trade
                trade = close_trade(
                    trade_id=str(data.get("id", "")),
                    exit_price=float(data.get("exit_price", 0)),
                    exit_date=str(data.get("exit_date", "")),
                )
                if trade:
                    body = json.dumps({"ok": True, "trade": trade}).encode("utf-8")
                    self._send(HTTPStatus.OK, body, content_type="application/json")
                else:
                    self._send(HTTPStatus.NOT_FOUND, json.dumps({"error": "Trade not found"}).encode(), content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/trades/update":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                from apps.dashboard.trades import update_trade
                tid = str(data.pop("id", ""))
                trade = update_trade(tid, data)
                if trade:
                    body = json.dumps({"ok": True, "trade": trade}).encode("utf-8")
                    self._send(HTTPStatus.OK, body, content_type="application/json")
                else:
                    self._send(HTTPStatus.NOT_FOUND, json.dumps({"error": "Trade not found"}).encode(), content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/trades/delete":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                from apps.dashboard.trades import delete_trade
                ok = delete_trade(str(data.get("id", "")))
                body = json.dumps({"ok": ok}).encode("utf-8")
                self._send(HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        if path == "/api/delete":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > _MAX_POST_BODY:
                    self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, b"Body too large", content_type="text/plain")
                    return
                raw = self.rfile.read(length) if length else b"{}"
                data = json.loads(raw)
                ticker = str(data.get("ticker", "")).strip().upper()
                group = str(data.get("group", "")).strip() or None
                if not ticker:
                    body = json.dumps({"error": "Missing ticker"}).encode("utf-8")
                    self._send(HTTPStatus.BAD_REQUEST, body, content_type="application/json")
                    return
                if not _VALID_SYMBOL.match(ticker):
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Invalid ticker"}).encode(), content_type="application/json")
                    return
                if group and not _VALID_GROUP.match(group):
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Invalid group name"}).encode(), content_type="application/json")
                    return

                from trading_dashboard.symbols.manager import SymbolManager
                sm = SymbolManager.from_lists_dir(LISTS_DIR, config_path=CONFIG_JSON)
                ok = sm.remove_symbol(ticker, group=group)
                if ok:
                    sm.sync_lists_dir(LISTS_DIR)

                still_in_groups = sm.find_groups(ticker) if ok else []
                purged = 0
                if ok and not still_in_groups:
                    purged = self._purge_ticker_data(ticker)

                result = {
                    "ok": ok,
                    "ticker": ticker,
                    "purged_files": purged,
                    "still_in_groups": still_in_groups,
                    "groups": sm.groups,
                }
                logger.info("Deleted %s (group=%s): ok=%s, purged=%d files",
                            ticker, group, ok, purged)
                status = HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST
                body = json.dumps(result).encode("utf-8")
                self._send(status, body, content_type="application/json")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, content_type="application/json")
            return

        self._send(HTTPStatus.NOT_FOUND, b"Not found", content_type="text/plain; charset=utf-8")

    @staticmethod
    def _purge_ticker_data(ticker: str) -> int:
        """Remove enriched data and chart assets for a ticker."""
        if not _VALID_SYMBOL.match(ticker):
            logger.warning("_purge_ticker_data: rejected invalid ticker %r", ticker)
            return 0
        import shutil
        purged = 0
        stock_dir = OUTPUT_STOCK_DATA_DIR
        for tf in VALID_TIMEFRAMES:
            for ext in ("parquet", "csv"):
                p = stock_dir / f"{ticker}_{tf}.{ext}"
                if p.exists():
                    p.unlink()
                    purged += 1
        asset_dir = DASHBOARD_ARTIFACTS_DIR / "dashboard_assets" / ticker
        if asset_dir.is_dir() and asset_dir.resolve().parent == (DASHBOARD_ARTIFACTS_DIR / "dashboard_assets").resolve():
            shutil.rmtree(asset_dir, ignore_errors=True)
            purged += 1
        return purged

    def _handle_scan_sse(self) -> None:
        """Stream scan progress via SSE.

        If no scan is running, starts one in a background thread.
        If a scan is already running (or just finished), replays all events
        so far and then streams new ones live — enabling page refresh
        without losing progress.
        """
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def _write_event(event: str, data: dict) -> bool:
            try:
                payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        def _write_keepalive() -> bool:
            try:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        u = urlparse(self.path)
        qs = parse_qs(u.query)
        _scan_timeframe = qs.get("timeframe", ["1D"])[0] or "1D"
        SCAN.start(timeframe=_scan_timeframe)

        snapshot, notify = SCAN.subscribe()
        try:
            cursor = 0
            for event, data in snapshot:
                if not _write_event(event, data):
                    return
                cursor += 1

            while True:
                fired = notify.wait(timeout=2.0)
                notify.clear()

                new_events = SCAN.get_events_since(cursor)
                for event, data in new_events:
                    if not _write_event(event, data):
                        return
                    cursor += 1

                if not new_events and not fired:
                    # Poll timed out with no new data — send keepalive to prevent
                    # proxy/browser from closing the long-lived SSE connection.
                    if not _write_keepalive():
                        return

                if not SCAN.running:
                    remaining = SCAN.get_events_since(cursor)
                    for event, data in remaining:
                        if not _write_event(event, data):
                            return
                    break
        finally:
            SCAN.unsubscribe(notify)

    def _handle_refresh_sse(self) -> None:
        """Stream refresh progress via SSE (same pattern as scan)."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def _write_event(event: str, data: dict) -> bool:
            try:
                payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        def _write_keepalive() -> bool:
            try:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        started = REFRESH.start()
        if not started and not REFRESH.running:
            # Another task type is blocking this refresh.
            _write_event("failed", {"message": "Another task is already running. Please wait for it to complete."})
            return

        snapshot, notify = REFRESH.subscribe()
        try:
            cursor = 0
            for event, data in snapshot:
                if not _write_event(event, data):
                    return
                cursor += 1

            while True:
                fired = notify.wait(timeout=2.0)
                notify.clear()

                new_events = REFRESH.get_events_since(cursor)
                for event, data in new_events:
                    if not _write_event(event, data):
                        return
                    cursor += 1

                if not new_events and not fired:
                    if not _write_keepalive():
                        return

                if not REFRESH.running:
                    remaining = REFRESH.get_events_since(cursor)
                    for event, data in remaining:
                        if not _write_event(event, data):
                            return
                    break
        finally:
            REFRESH.unsubscribe(notify)

    def _handle_rebuild_ui_sse(self) -> None:
        """Stream rebuild-ui progress via SSE (fast: no download/re-enrich)."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def _write_event(event: str, data: dict) -> bool:
            try:
                payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        def _write_keepalive() -> bool:
            try:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        REBUILD_UI.start()

        snapshot, notify = REBUILD_UI.subscribe()
        try:
            cursor = 0
            for event, data in snapshot:
                if not _write_event(event, data):
                    return
                cursor += 1

            while True:
                fired = notify.wait(timeout=2.0)
                notify.clear()
                new_events = REBUILD_UI.get_events_since(cursor)
                for event, data in new_events:
                    if not _write_event(event, data):
                        return
                    cursor += 1
                if not new_events and not fired:
                    if not _write_keepalive():
                        return
                if not REBUILD_UI.running:
                    remaining = REBUILD_UI.get_events_since(cursor)
                    for event, data in remaining:
                        if not _write_event(event, data):
                            return
                    break
        finally:
            REBUILD_UI.unsubscribe(notify)

    def _handle_enrich_sse(self) -> None:
        """Stream batch enrichment progress via SSE."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        def _write_event(event: str, data: dict) -> bool:
            try:
                payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        snapshot, notify = ENRICH.subscribe()
        try:
            cursor = 0
            for event, data in snapshot:
                if not _write_event(event, data):
                    return
                cursor += 1

            while True:
                notify.wait(timeout=2.0)
                notify.clear()

                new_events = ENRICH.get_events_since(cursor)
                for event, data in new_events:
                    if not _write_event(event, data):
                        return
                    cursor += 1

                if not ENRICH.running:
                    remaining = ENRICH.get_events_since(cursor)
                    for event, data in remaining:
                        if not _write_event(event, data):
                            return
                    break
        finally:
            ENRICH.unsubscribe(notify)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.debug(format, *args)


def main() -> int:
    _configure_logging()
    host = os.environ.get("TD_HOST", "127.0.0.1")
    port = int(os.environ.get("TD_PORT", "8050"))
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.allow_reuse_port = True
    import socket as _sock
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.socket.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    try:
        httpd.socket.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    print(f"Dashboard: http://{host}:{port}")
    print(f"API:       POST http://{host}:{port}/api/move")
    print("Stop with Ctrl+C", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

