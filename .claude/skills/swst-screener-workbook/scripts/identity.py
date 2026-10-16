"""Company identity keys shared by merge_sources (build_workbook) and the hub.

``ticker|currency`` is not unique: SAN|EUR is Sanofi in one list and Banco Santander in
another. Rule:

  * a row with a resolved yfinance symbol (``_yf_symbol``) is keyed by that symbol;
  * otherwise it is keyed ``ticker|currency``. Two such rows are the same company only if
    the price ratio AND the market-cap ratio (max/min) are both <= SAME_COMPANY_RATIO.
    A larger ratio on either means a different company: the later one gets ``#2``, ``#3``.
  * a missing or non-positive price or cap gives no evidence either way, so that
    dimension is skipped (rows are never split on missing data).

No network. Pure functions.
"""

from __future__ import annotations

import math

SAME_COMPANY_RATIO = 1.6
COLLISION_NOTE = "same ticker as another company; kept separate"


def _pos(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _ratio_ok(a, b) -> bool:
    a, b = _pos(a), _pos(b)
    if a is None or b is None:
        return True
    return max(a, b) / min(a, b) <= SAME_COMPANY_RATIO


def same_company(p1, c1, p2, c2) -> bool:
    return _ratio_ok(p1, p2) and _ratio_ok(c1, c2)


def _sym(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return s or None


def assign_keys(rows) -> tuple[list[str], list[bool]]:
    """rows: iterable of mappings with ticker, currency, _yf_symbol, price_local,
    market_cap_local_m. Returns (keys, collided) in input order; ``collided`` is True for
    every symbol-less row whose ticker|currency maps to 2+ distinct companies."""
    keys: list[str] = []
    base_of: list = []
    clusters: dict[str, list[list[tuple]]] = {}
    for r in rows:
        sym = _sym(r.get("_yf_symbol"))
        if sym:
            keys.append(sym)
            base_of.append(None)
            continue
        base = f"{r.get('ticker')}|{r.get('currency')}"
        cl = clusters.setdefault(base, [])
        px, cap = r.get("price_local"), r.get("market_cap_local_m")
        idx = None
        for n, members in enumerate(cl):
            if any(same_company(px, cap, mp, mc) for mp, mc in members):
                idx = n
                break
        if idx is None:
            cl.append([])
            idx = len(cl) - 1
        cl[idx].append((px, cap))
        keys.append(base if idx == 0 else f"{base}#{idx + 1}")
        base_of.append(base)
    collided = [b is not None and len(clusters[b]) > 1 for b in base_of]
    return keys, collided
