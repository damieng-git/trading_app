"""
Random stock sampler for reproducible optimization experiments.

Creates and persists a random sample of N stocks from a universe,
with a fixed seed for reproducibility across runs.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import List, Optional


def draw_sample(
    universe: List[str],
    n: int = 100,
    seed: int = 42,
    *,
    exclude: List[str] | None = None,
) -> List[str]:
    """
    Draw a reproducible random sample of *n* symbols from *universe*.

    Parameters
    ----------
    universe : list[str]
        Full list of available symbols.
    n : int
        Sample size (clamped to len(universe)).
    seed : int
        Random seed for reproducibility.
    exclude : list[str], optional
        Symbols to exclude from sampling.

    Returns
    -------
    Sorted list of sampled symbols.
    """
    pool = sorted(set(universe) - set(exclude or []))
    n = min(n, len(pool))
    rng = random.Random(seed)
    return sorted(rng.sample(pool, n))


def save_sample(
    symbols: List[str],
    path: Path,
    *,
    seed: int = 42,
    universe_size: int | None = None,
    description: str = "",
) -> None:
    """Persist a sample to JSON with metadata."""
    payload = {
        "symbols": sorted(symbols),
        "n": len(symbols),
        "seed": seed,
        "universe_size": universe_size,
        "description": description,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_sample(path: Path) -> List[str]:
    """Load a previously saved sample."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return sorted(raw.get("symbols", []))
