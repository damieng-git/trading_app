"""
SymbolManager — unified symbol loading, normalization, and group management.

Supports loading from:
- JSON config files (config.json)
- CSV files with automatic TradingView normalization and yahoo_ticker overrides
- A directory of CSVs (one file per group, filename stem = group name)
- Hardcoded lists
- Named groups with set operations
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Set

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, content: str) -> None:
    """Write to a temp file then rename for crash safety."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# TradingView exchange → Yahoo Finance suffix mapping
# ---------------------------------------------------------------------------

_TV_EXCHANGE_SUFFIX: Dict[str, str] = {
    "XETR": ".DE",
    "GETTEX": ".DE",
    "MIL": ".MI",
    "LSE": ".L",
    "LSIN": ".L",
    "NASDAQ": "",
    "NYSE": "",
    "AMEX": "",
    "OMXCOP": ".CO",
    "EURONEXT": ".PA",
    "VIE": ".VI",
    "SIX": ".SW",
    "BME": ".MC",
    "TSE": ".T",
    "OMXHEX": ".HE",
    "OSL": ".OL",
    "GPW": ".WA",
    "SSE": ".SS",
    "BMFBOVESPA": ".SA",
}


def _clean_cell(s: str) -> str:
    x = (s or "").strip()
    if not x:
        return ""
    if "#" in x:
        x = x.split("#", 1)[0].strip()
    x = x.split()[0].strip()
    return x.upper()


def normalize_symbol(raw: str) -> str:
    """
    Normalize a symbol input to a Yahoo Finance ticker.

    - Plain Yahoo tickers (e.g. ``ORA.PA``) pass through uppercased.
    - TradingView ``EXCHANGE:TICKER`` format is converted using the suffix map.
    - Everything else is returned uppercased.
    """
    s = _clean_cell(raw)
    if not s:
        return ""
    if "." in s:
        return s
    if ":" in s:
        ex, sym = (x.strip().upper() for x in s.split(":", 1))
        if not sym:
            return ""
        suf = _TV_EXCHANGE_SUFFIX.get(ex, "")
        return f"{sym}{suf}" if suf else sym
    return s


def read_symbols_csv(path: Path) -> List[str]:
    """
    Read symbols from a CSV, applying TradingView normalization.

    Supports:
    - Header with ``ticker`` and optional ``yahoo_ticker`` columns
    - No header (first column = ticker, optional second = yahoo override)
    - Comment lines starting with ``#``
    """
    if not path.exists() or path.stat().st_size == 0:
        return []

    lines: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(raw)

    if not lines:
        return []

    rows = list(csv.reader(lines))
    if not rows:
        return []

    header = [c.strip().lower() for c in (rows[0] or [])]
    has_header = any(h in {"ticker", "symbol", "yahoo_ticker", "yahoo"} for h in header)

    def _idx(name: str) -> int | None:
        try:
            return header.index(name)
        except ValueError:
            return None

    ticker_idx = _idx("ticker")
    if ticker_idx is None:
        ticker_idx = _idx("symbol")
    yahoo_idx = _idx("yahoo_ticker")
    if yahoo_idx is None:
        yahoo_idx = _idx("yahoo")

    out: List[str] = []
    start = 1 if has_header else 0
    for r in rows[start:]:
        if not r:
            continue
        yahoo_cell = ""
        ticker_cell = ""
        if has_header:
            if yahoo_idx is not None and yahoo_idx < len(r):
                yahoo_cell = r[yahoo_idx]
            if ticker_idx is not None and ticker_idx < len(r):
                ticker_cell = r[ticker_idx]
        else:
            ticker_cell = r[0]
            if len(r) >= 2:
                yahoo_cell = r[1]

        sym = normalize_symbol(yahoo_cell) or normalize_symbol(ticker_cell)
        if sym:
            out.append(sym)

    seen: set[str] = set()
    deduped: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


class SymbolManager:
    """
    Manages stock symbols from multiple sources with named groups.

    Usage::

        sm = SymbolManager.from_lists_dir(Path("apps/dashboard/configs/lists"))
        all_symbols = sm.symbols
        picks = sm.group("portfolio")
        sm.add_symbol("AAPL", group="watchlist")
        sm.save_config(Path("apps/dashboard/configs/config.json"))
    """

    def __init__(self) -> None:
        self._all: Set[str] = set()
        self._groups: Dict[str, Set[str]] = {}
        self._display_names: Dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config_path: Path) -> "SymbolManager":
        """Load symbols and groups from a config.json file."""
        sm = cls()
        if not config_path.exists():
            return sm
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return sm

        if not isinstance(cfg, dict):
            return sm

        symbols = cfg.get("symbols", [])
        if isinstance(symbols, list):
            sm.add_symbols([str(s).strip() for s in symbols if str(s).strip()])

        groups = cfg.get("symbol_groups", {})
        if isinstance(groups, dict):
            for name, members in groups.items():
                name = str(name).strip()
                if not name or not isinstance(members, list):
                    continue
                sm.add_symbols(
                    [str(s).strip() for s in members if str(s).strip()],
                    group=name,
                )

        return sm

    @classmethod
    def from_csv(cls, csv_path: Path, *, group: str | None = None) -> "SymbolManager":
        """Create from a single CSV file."""
        sm = cls()
        sm.add_csv(csv_path, group=group)
        return sm

    @classmethod
    def from_lists_dir(
        cls,
        lists_dir: Path,
        *,
        config_path: Path | None = None,
    ) -> "SymbolManager":
        """
        Auto-discover CSV files in *lists_dir* and load each as a group.

        The filename stem becomes the group name (e.g. ``portfolio.csv`` -> group ``portfolio``).
        If *config_path* is given, non-CSV config keys are preserved.
        """
        sm = cls()
        if config_path and config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(cfg, dict):
                    sm._extra_config = {
                        k: v for k, v in cfg.items()
                        if k not in ("symbols", "symbol_groups")
                    }
            except Exception as exc:
                logger.debug("Failed to load extra config from %s for lists dir: %s", config_path, exc)
                pass

        if not lists_dir.exists():
            return sm

        for csv_file in sorted(lists_dir.glob("*.csv")):
            group_name = csv_file.stem
            sm.add_csv(csv_file, group=group_name)

        return sm

    # ------------------------------------------------------------------
    # Adding symbols
    # ------------------------------------------------------------------

    def add_symbols(
        self,
        symbols: List[str],
        *,
        group: str | None = None,
    ) -> None:
        """Add symbols to the universe and optionally to a named group.

        Enforces mutual exclusivity between portfolio and watchlist.
        """
        normed = [str(s).strip().upper() for s in symbols if str(s).strip()]
        self._all.update(normed)
        if group:
            g = str(group).strip()
            self._groups.setdefault(g, set()).update(normed)
            if g in self._EXCLUSIVE_GROUPS:
                for rival in self._EXCLUSIVE_GROUPS - {g}:
                    rival_set = self._groups.get(rival)
                    if rival_set:
                        overlap = rival_set & set(normed)
                        if overlap:
                            rival_set -= overlap
                            logger.info("Dedup: removed %d symbol(s) from '%s' (added to '%s')",
                                        len(overlap), rival, g)

    def add_symbol(self, symbol: str, *, group: str | None = None) -> str:
        """Add a single symbol (with TradingView normalization). Returns normalized ticker."""
        normed = normalize_symbol(symbol)
        if normed:
            self.add_symbols([normed], group=group)
        return normed

    def add_csv(
        self,
        csv_path: Path,
        *,
        group: str | None = None,
        ticker_column: str | None = None,
        name_column: str | None = None,
    ) -> int:
        """
        Load symbols from a CSV file with full TradingView normalization.

        Returns the number of symbols added.
        """
        symbols = read_symbols_csv(Path(csv_path))
        if not symbols:
            if group:
                self._groups.setdefault(group, set())
            return 0
        self.add_symbols(symbols, group=group)
        return len(symbols)

    # ------------------------------------------------------------------
    # Removing / moving symbols
    # ------------------------------------------------------------------

    def remove_symbol(self, symbol: str, *, group: str | None = None) -> bool:
        """
        Remove a symbol from a group (or from all groups if group is None).

        Returns True if the symbol was found and removed.
        """
        sym = normalize_symbol(symbol)
        if not sym:
            return False

        with self._lock:
            removed = False
            if group:
                g = self._groups.get(group)
                if g and sym in g:
                    g.discard(sym)
                    removed = True
            else:
                for g in self._groups.values():
                    if sym in g:
                        g.discard(sym)
                        removed = True

            in_any = any(sym in g for g in self._groups.values())
            if not in_any:
                self._all.discard(sym)

        return removed

    _EXCLUSIVE_GROUPS = {"damien", "watchlist"}

    def move_symbol(self, symbol: str, *, from_group: str, to_group: str) -> bool:
        """Move a symbol from one group to another.

        Enforces mutual exclusivity between damien and watchlist:
        moving to one automatically removes from the other.
        """
        sym = normalize_symbol(symbol)
        if not sym:
            return False
        with self._lock:
            src = self._groups.get(from_group)
            if not src or sym not in src:
                return False
            src.discard(sym)
            self._groups.setdefault(to_group, set()).add(sym)
            self._all.add(sym)

            if to_group in self._EXCLUSIVE_GROUPS:
                for rival in self._EXCLUSIVE_GROUPS - {to_group}:
                    rival_set = self._groups.get(rival)
                    if rival_set and sym in rival_set:
                        rival_set.discard(sym)
                        logger.info("Dedup: removed %s from '%s' (moved to '%s')",
                                    sym, rival, to_group)
        return True

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    @property
    def symbols(self) -> List[str]:
        """All symbols, sorted and deduplicated."""
        return sorted(self._all)

    @property
    def groups(self) -> Dict[str, List[str]]:
        """All groups as {name: sorted_list}."""
        return {k: sorted(v) for k, v in self._groups.items()}

    @property
    def group_names(self) -> List[str]:
        return sorted(self._groups.keys())

    @property
    def display_names(self) -> Dict[str, str]:
        return dict(self._display_names)

    def group(self, name: str) -> List[str]:
        """Get symbols in a specific group."""
        return sorted(self._groups.get(name, set()))

    def find_groups(self, symbol: str) -> List[str]:
        """Return all group names that contain this symbol."""
        sym = normalize_symbol(symbol)
        return sorted(g for g, members in self._groups.items() if sym in members)

    def intersection(self, *group_names: str) -> List[str]:
        """Symbols common to all named groups."""
        sets = [self._groups.get(g, set()) for g in group_names]
        if not sets:
            return []
        return sorted(set.intersection(*sets))

    def difference(self, group_a: str, group_b: str) -> List[str]:
        """Symbols in group_a but not in group_b."""
        a = self._groups.get(group_a, set())
        b = self._groups.get(group_b, set())
        return sorted(a - b)

    def __len__(self) -> int:
        return len(self._all)

    def __contains__(self, symbol: str) -> bool:
        return normalize_symbol(str(symbol)) in self._all

    # ------------------------------------------------------------------
    # Export / persist
    # ------------------------------------------------------------------

    def to_config_dict(self) -> dict:
        """Export as a dict suitable for config.json."""
        return {
            "symbols": self.symbols,
            "symbol_groups": self.groups,
        }

    def save_config(self, path: Path) -> None:
        """Write symbols and groups to a config.json file (atomic, merges with existing)."""
        existing: dict = {}
        if path.exists() and path.stat().st_size > 0:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        existing.update(self.to_config_dict())
        if hasattr(self, "_extra_config"):
            for k, v in self._extra_config.items():
                if k not in existing:
                    existing[k] = v

        _atomic_write(path, json.dumps(existing, indent=2, ensure_ascii=False) + "\n")

    def save_group_csv(self, group: str, csv_path: Path) -> int:
        """Write a group's symbols to a CSV file (atomic). Returns symbol count."""
        syms = self.group(group)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(csv_path, "ticker\n" + "\n".join(syms) + "\n")
        return len(syms)

    def sync_lists_dir(self, lists_dir: Path) -> Dict[str, int]:
        """Write all groups as CSVs to *lists_dir*. Returns {group: count}."""
        lists_dir.mkdir(parents=True, exist_ok=True)
        result: Dict[str, int] = {}
        for g in sorted(self._groups.keys()):
            p = lists_dir / f"{g}.csv"
            result[g] = self.save_group_csv(g, p)
        return result
