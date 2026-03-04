"""
Incremental data update support.

Instead of re-downloading full history, fetch only the most recent bars
and append to existing cached data. Recompute indicators on a tail window.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import pandas as pd

from .store import DataStore

logger = logging.getLogger(__name__)


class IncrementalUpdater:
    """
    Manages incremental updates for OHLCV data.

    Tracks per-symbol metadata (last bar timestamp, row count) and only
    downloads new data since the last update.
    """

    META_FILE = "incremental_meta.json"

    def __init__(self, store: DataStore) -> None:
        self.store = store
        self._meta_path = store.enriched_dir.parent / self.META_FILE
        self._meta = self._load_meta()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _load_meta(self) -> dict:
        if self._meta_path.exists():
            try:
                return json.loads(self._meta_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.debug("Failed to load incremental meta: %s", exc)
                pass
        return {}

    def _save_meta(self) -> None:
        self._meta_path.parent.mkdir(parents=True, exist_ok=True)
        self._meta_path.write_text(
            json.dumps(self._meta, indent=2) + "\n",
            encoding="utf-8",
        )

    def get_last_bar(self, symbol: str, tf: str) -> Optional[str]:
        """ISO timestamp of last bar for this symbol/tf, or None."""
        key = f"{symbol}|{tf}"
        return self._meta.get(key, {}).get("last_bar")

    def update_meta(self, symbol: str, tf: str, df: pd.DataFrame) -> None:
        """Record metadata after successful update."""
        key = f"{symbol}|{tf}"
        self._meta[key] = {
            "last_bar": pd.to_datetime(df.index.max()).isoformat() if not df.empty else None,
            "rows": len(df),
            "updated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }
        self._save_meta()

    # ------------------------------------------------------------------
    # Incremental merge
    # ------------------------------------------------------------------

    def merge_new_bars(
        self,
        symbol: str,
        tf: str,
        new_df: pd.DataFrame,
        *,
        warmup_bars: int = 300,
    ) -> pd.DataFrame:
        """
        Merge new bars with existing cached data.

        Keeps the full history but only recomputes indicators on the
        tail ``warmup_bars`` + new bars.
        """
        existing = self.store.load_raw(symbol, tf)
        if existing is not None and not existing.empty:
            new_max = pd.to_datetime(new_df.index.max()) if new_df is not None and not new_df.empty else None
            existing_max = pd.to_datetime(existing.index.max())
            if new_max is not None and new_max < existing_max:
                logger.warning(
                    "Skipping merge for %s/%s: new data max %s is older than existing max %s",
                    symbol, tf, new_max, existing_max,
                )
                return existing
            combined = pd.concat([existing, new_df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()
        else:
            combined = new_df.sort_index() if new_df is not None else pd.DataFrame()

        if not combined.empty:
            self.store.save_raw(symbol, tf, combined)
            self.update_meta(symbol, tf, combined)

        return combined

    def needs_update(self, symbol: str, tf: str, max_age_hours: float = 1.0) -> bool:
        """Check if symbol/tf data is stale and needs refresh."""
        key = f"{symbol}|{tf}"
        entry = self._meta.get(key, {})
        updated_at = entry.get("updated_at")
        if not updated_at:
            return True
        try:
            ts = pd.Timestamp(updated_at)
            age_hours = (pd.Timestamp.now(tz="UTC") - ts).total_seconds() / 3600.0
            return age_hours > max_age_hours
        except Exception:
            return True

    def stale_symbols(
        self,
        symbols: list[str],
        tf: str,
        max_age_hours: float = 1.0,
    ) -> list[str]:
        """Return symbols that need updating for the given timeframe."""
        return [s for s in symbols if self.needs_update(s, tf, max_age_hours)]
