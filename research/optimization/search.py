"""
Parameter search for indicator optimization.

Supports grid search and random search over parameter spaces.
Results are saved as structured JSON for comparison across runs.
"""

from __future__ import annotations

import itertools
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd


@dataclass
class SearchResult:
    params: Dict[str, Any]
    scores: Dict[str, float]
    primary_score: float
    symbol_scores: Dict[str, float] = field(default_factory=dict)


def grid_search(
    param_grid: Dict[str, List[Any]],
) -> List[Dict[str, Any]]:
    """Generate all parameter combinations from a grid."""
    keys = sorted(param_grid.keys())
    values = [param_grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def random_search(
    param_ranges: Dict[str, tuple],
    n_trials: int = 50,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """
    Generate random parameter combinations.

    param_ranges values should be (min, max) tuples for continuous params,
    or lists for categorical params.
    """
    rng = random.Random(seed)
    trials: List[Dict[str, Any]] = []
    for _ in range(n_trials):
        params: Dict[str, Any] = {}
        for key, spec in param_ranges.items():
            if isinstance(spec, (list, tuple)) and len(spec) == 2 and all(isinstance(v, (int, float)) for v in spec):
                lo, hi = spec
                if isinstance(lo, int) and isinstance(hi, int):
                    params[key] = rng.randint(lo, hi)
                else:
                    params[key] = round(rng.uniform(lo, hi), 4)
            elif isinstance(spec, list):
                params[key] = rng.choice(spec)
            else:
                params[key] = spec
        trials.append(params)
    return trials


def run_search(
    *,
    indicator_key: str,
    compute_fn: Callable[[pd.DataFrame, Dict[str, Any]], pd.DataFrame],
    kpi_state_fn: Callable[[pd.DataFrame], pd.Series],
    objective_fn: Callable[[pd.Series, pd.Series], float],
    objective_name: str,
    param_combos: List[Dict[str, Any]],
    data: Dict[str, pd.DataFrame],
    horizon: int = 5,
) -> List[SearchResult]:
    """
    Run parameter search over all combinations and symbols.

    Parameters
    ----------
    indicator_key : str
        Indicator identifier.
    compute_fn : callable
        (df, params) -> enriched_df with indicator columns.
    kpi_state_fn : callable
        (enriched_df) -> pd.Series of states (1, 0, -1, -2).
    objective_fn : callable
        (states, close) -> float score.
    param_combos : list[dict]
        Parameter combinations to test.
    data : dict[str, pd.DataFrame]
        {symbol: ohlcv_df} mapping.

    Returns
    -------
    List of SearchResult sorted by primary_score descending.
    """
    results: List[SearchResult] = []

    for params in param_combos:
        sym_scores: Dict[str, float] = {}
        for sym, df in data.items():
            try:
                enriched = compute_fn(df, params)
                states = kpi_state_fn(enriched)
                score = objective_fn(states, df["Close"])
                sym_scores[sym] = score
            except Exception:
                continue

        if not sym_scores:
            continue

        scores_list = list(sym_scores.values())
        avg_score = sum(scores_list) / len(scores_list)

        results.append(SearchResult(
            params=params,
            scores={objective_name: avg_score},
            primary_score=avg_score,
            symbol_scores=sym_scores,
        ))

    results.sort(key=lambda r: r.primary_score, reverse=True)
    return results


def save_results(
    results: List[SearchResult],
    path: Path,
    *,
    indicator_key: str = "",
    objective_name: str = "",
    extra_meta: Dict[str, Any] | None = None,
) -> None:
    """Save search results to JSON."""
    payload = {
        "indicator": indicator_key,
        "objective": objective_name,
        "n_combos": len(results),
        "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
        **(extra_meta or {}),
        "results": [
            {
                "rank": i + 1,
                "params": r.params,
                "scores": r.scores,
                "primary_score": r.primary_score,
                "n_symbols": len(r.symbol_scores),
            }
            for i, r in enumerate(results[:50])
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
