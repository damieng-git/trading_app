"""Layout helpers, axis formatting, and styling utilities for Plotly figures."""

from __future__ import annotations

import json
import math
from typing import Any

import plotly.utils as plotly_utils


def _sanitize_for_json(obj: Any) -> Any:
    """
    Recursively replace non-finite floats (NaN/Inf) with None so the payload is valid JSON.
    Plotly tolerates nulls; browsers tolerate NaN in JS but it can break JSON parsing and some tooling.
    """
    if obj is None:
        return None
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    # numpy scalars
    if hasattr(obj, "item"):
        try:
            return _sanitize_for_json(obj.item())
        except Exception:
            return str(obj)
    return str(obj)


def _safe_plotly_json_dumps(obj: Any) -> str:
    """
    Dump Plotly figures to JSON with strict NaN/Inf handling.
    """
    try:
        return json.dumps(obj, cls=plotly_utils.PlotlyJSONEncoder, allow_nan=False, separators=(",", ":"))
    except ValueError:
        cleaned = _sanitize_for_json(obj)
        return json.dumps(cleaned, cls=plotly_utils.PlotlyJSONEncoder, allow_nan=False, separators=(",", ":"))
