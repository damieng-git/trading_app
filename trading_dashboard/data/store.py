"""
DataStore: unified read/write for enriched and raw OHLCV DataFrames.

Supports CSV and Parquet formats with transparent caching and staleness checks.
Includes content-hash enrichment metadata for cache-aware skip decisions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _flock(f, *, exclusive: bool) -> None:
    """Portable file locking (Unix fcntl, Windows msvcrt, no-op fallback)."""
    try:
        import fcntl
        if exclusive:
            fcntl.flock(f, fcntl.LOCK_EX)
        else:
            fcntl.flock(f, fcntl.LOCK_UN)
    except ImportError:
        try:
            import msvcrt
            if exclusive:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError):
            pass


class DataStore:
    """
    Provides a consistent API for persisting and loading OHLCV DataFrames.

    Usage::

        store = DataStore(
            enriched_dir=Path("data/feature_store/enriched/dashboard/stock_data"),
            raw_dir=Path("data/cache/ohlcv_raw/dashboard"),
            fmt="parquet",
        )
        store.save_enriched("AAPL", "1W", df)
        df = store.load_enriched("AAPL", "1W")
    """

    SUPPORTED_FORMATS = {"csv", "parquet"}

    def __init__(
        self,
        enriched_dir: Path,
        raw_dir: Path,
        fmt: str = "csv",
        cache_ttl_hours: float = 24.0,
        legacy_dirs: List[Path] | None = None,
    ) -> None:
        self.enriched_dir = Path(enriched_dir)
        self.raw_dir = Path(raw_dir)
        self.fmt = fmt if fmt in self.SUPPORTED_FORMATS else "csv"
        self.cache_ttl_hours = cache_ttl_hours
        self.legacy_dirs = [Path(p) for p in (legacy_dirs or [])]

        self.enriched_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Enriched data (indicator-computed DataFrames)
    # ------------------------------------------------------------------

    def enriched_path(self, symbol: str, tf: str) -> Path:
        """Return the filesystem path for enriched data for a symbol/timeframe."""
        ext = ".parquet" if self.fmt == "parquet" else ".csv"
        return self.enriched_dir / f"{symbol}_{tf}{ext}"

    def save_enriched(
        self,
        symbol: str,
        tf: str,
        df: pd.DataFrame,
        *,
        raw_hash: str | None = None,
        indicator_config_hash: str | None = None,
    ) -> Path:
        """Persist enriched DataFrame to disk; optionally record hashes for cache validation."""
        p = self.enriched_path(symbol, tf)
        p.parent.mkdir(parents=True, exist_ok=True)
        if self.fmt == "parquet":
            df.to_parquet(p, index=True, engine="pyarrow")
            self._remove_stale_format(self.enriched_dir / f"{symbol}_{tf}.csv")
        else:
            df.to_csv(p, index=True)
            self._remove_stale_format(self.enriched_dir / f"{symbol}_{tf}.parquet")
        if raw_hash or indicator_config_hash:
            self._save_enrichment_meta(symbol, tf, raw_hash, indicator_config_hash, len(df))
        return p

    def load_enriched(
        self,
        symbol: str,
        tf: str,
        *,
        respect_ttl: bool = True,
    ) -> Optional[pd.DataFrame]:
        """Load enriched DataFrame from disk; returns None if missing or stale (when respect_ttl)."""
        p = self._find_enriched_path(symbol, tf)
        if p is None:
            return None
        if respect_ttl and not self._is_fresh(p):
            return None
        return self._read(p)

    def load_all_enriched(
        self,
        symbol: str,
        timeframes: List[str],
        *,
        respect_ttl: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """Load enriched DataFrames for all requested timeframes; returns only successfully loaded."""
        out: Dict[str, pd.DataFrame] = {}
        for tf in timeframes:
            df = self.load_enriched(symbol, tf, respect_ttl=respect_ttl)
            if df is not None:
                out[tf] = df
        return out

    # ------------------------------------------------------------------
    # Raw OHLCV data (pre-indicator)
    # ------------------------------------------------------------------

    def raw_path(self, symbol: str, tf: str) -> Path:
        """Return the filesystem path for raw OHLCV data for a symbol/timeframe."""
        ext = ".parquet" if self.fmt == "parquet" else ".csv"
        return self.raw_dir / f"{symbol}_{tf}_raw{ext}"

    def save_raw(self, symbol: str, tf: str, df: pd.DataFrame) -> Path:
        """Persist raw OHLCV DataFrame to disk."""
        p = self.raw_path(symbol, tf)
        p.parent.mkdir(parents=True, exist_ok=True)
        if self.fmt == "parquet":
            df.to_parquet(p, index=True, engine="pyarrow")
            self._remove_stale_format(self.raw_dir / f"{symbol}_{tf}_raw.csv")
        else:
            df.to_csv(p, index=True)
            self._remove_stale_format(self.raw_dir / f"{symbol}_{tf}_raw.parquet")
        return p

    def load_raw(self, symbol: str, tf: str) -> Optional[pd.DataFrame]:
        """Load raw OHLCV DataFrame from disk; returns None if missing."""
        p = self.raw_path(symbol, tf)
        if p.exists() and p.stat().st_size > 0:
            return self._read(p)
        alt_ext = ".csv" if self.fmt == "parquet" else ".parquet"
        alt_p = self.raw_dir / f"{symbol}_{tf}_raw{alt_ext}"
        if alt_p.exists() and alt_p.stat().st_size > 0:
            return self._read(alt_p)
        return None

    def load_all_raw(
        self,
        symbol: str,
        timeframes: List[str],
    ) -> Dict[str, pd.DataFrame]:
        """Load raw OHLCV DataFrames for all requested timeframes."""
        out: Dict[str, pd.DataFrame] = {}
        for tf in timeframes:
            df = self.load_raw(symbol, tf)
            if df is not None:
                out[tf] = df
        return out

    # ------------------------------------------------------------------
    # Symbols discovery
    # ------------------------------------------------------------------

    def list_enriched_symbols(self, tf: str | None = None) -> List[str]:
        """Return symbols that have enriched data on disk."""
        syms: set[str] = set()
        ext = ".parquet" if self.fmt == "parquet" else ".csv"
        for p in self.enriched_dir.glob(f"*{ext}"):
            name = p.stem
            if "_" not in name:
                continue
            parts = name.rsplit("_", 1)
            if len(parts) == 2:
                sym, file_tf = parts
                if tf is None or file_tf == tf:
                    syms.add(sym)
        return sorted(syms)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_enriched_path(self, symbol: str, tf: str) -> Path | None:
        p = self.enriched_path(symbol, tf)
        if p.exists() and p.stat().st_size > 0:
            return p
        csv_fallback = self.enriched_dir / f"{symbol}_{tf}.csv"
        if csv_fallback.exists() and csv_fallback.stat().st_size > 0:
            return csv_fallback
        for legacy in self.legacy_dirs:
            lp = legacy / f"{symbol}_{tf}.csv"
            if lp.exists() and lp.stat().st_size > 0:
                return lp
        return None

    def _is_fresh(self, p: Path) -> bool:
        if self.cache_ttl_hours <= 0:
            return True
        ttl_s = self.cache_ttl_hours * 3600.0
        age_s = time.time() - float(p.stat().st_mtime)
        return age_s <= ttl_s

    # ------------------------------------------------------------------
    # Enrichment metadata (content-hash skip)
    # ------------------------------------------------------------------

    def _enrichment_meta_path(self) -> Path:
        return self.enriched_dir / "_enrichment_meta.json"

    def _load_enrichment_meta(self) -> dict:
        p = self._enrichment_meta_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.debug("Failed to load enrichment metadata: %s", exc)
                pass
        return {}

    def _save_enrichment_meta(
        self,
        symbol: str,
        tf: str,
        raw_hash: str | None,
        config_hash: str | None,
        n_rows: int,
    ) -> None:
        p = self._enrichment_meta_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        lock_path = p.with_suffix(".lock")
        with open(lock_path, "w") as lock_f:
            _flock(lock_f, exclusive=True)
            try:
                meta = self._load_enrichment_meta()
                key = f"{symbol}|{tf}"
                meta[key] = {
                    "raw_hash": raw_hash,
                    "config_hash": config_hash,
                    "n_rows": n_rows,
                    "enriched_at": pd.Timestamp.now(tz="UTC").isoformat(),
                }
                try:
                    p.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
                except Exception as e:
                    logger.warning("Could not persist enrichment meta: %s", e)
            finally:
                _flock(lock_f, exclusive=False)

    def enrichment_is_current(
        self,
        symbol: str,
        tf: str,
        raw_hash: str,
        config_hash: str,
    ) -> bool:
        """True if the enriched file was built from the same raw data + config."""
        meta = self._load_enrichment_meta()
        entry = meta.get(f"{symbol}|{tf}", {})
        return (
            entry.get("raw_hash") == raw_hash
            and entry.get("config_hash") == config_hash
        )

    @staticmethod
    def compute_raw_hash(df: pd.DataFrame) -> str:
        """Fast fingerprint of a raw OHLCV DataFrame (row count + boundary timestamps + sampled close values)."""
        if df is None or df.empty:
            return "empty"
        n = len(df)
        first_ts = str(df.index[0])
        last_ts = str(df.index[-1])
        token = f"{n}|{first_ts}|{last_ts}"
        if "Close" in df.columns:
            indices = list(range(0, n, max(1, n // 20)))
            if (n - 1) not in indices:
                indices.append(n - 1)
            sampled = "|".join(f"{df['Close'].iloc[i]:.6f}" for i in indices)
            token += f"|{sampled}"
        return hashlib.md5(token.encode()).hexdigest()[:12]

    @staticmethod
    def compute_config_hash(config_path: Path) -> str:
        """Hash of indicator_config.json contents."""
        if config_path is None or not config_path.exists():
            return "default"
        try:
            content = config_path.read_bytes()
            return hashlib.md5(content).hexdigest()[:12]
        except Exception:
            return "error"

    @staticmethod
    def _remove_stale_format(old_path: Path) -> None:
        """Remove a leftover file from a previous format (csv↔parquet)."""
        try:
            if old_path.exists():
                old_path.unlink()
        except Exception as exc:
            logger.debug("Failed to remove stale format file %s: %s", old_path, exc)
            pass

    def _read(self, p: Path) -> Optional[pd.DataFrame]:
        try:
            if p.suffix == ".parquet":
                df = pd.read_parquet(p, engine="pyarrow")
            else:
                df = pd.read_csv(p, parse_dates=[0], index_col=0)
        except Exception as e:
            logger.warning("Failed to read %s: %s", p, e)
            return None
        if df is None or df.empty:
            return None
        df.index = pd.to_datetime(df.index, errors="coerce").tz_localize(None)
        return df.sort_index()
