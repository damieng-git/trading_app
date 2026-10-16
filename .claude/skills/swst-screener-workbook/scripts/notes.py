"""Plain, factual one-line row notes for the dashboard. Deterministic, no prose.

row_note(row, list_stats) emits up to 5 fragments in a fixed order, each ending
in a period. list_stats comes from list_stats(df) and carries the list medians
and the snapshot date used for the earnings-window fragment.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

import numpy as np
import pandas as pd

from flags import _num

MAX_FRAGMENTS = 5
EARNINGS_WINDOW_DAYS = 30


def list_stats(df: pd.DataFrame, snapshot_date=None,
               scope_label: str = "list median") -> dict:
    def med(col):
        if col not in df.columns:
            return None, 0
        vals = [v for v in (_num(x) for x in df[col]) if v is not None]
        return (float(np.median(vals)) if vals else None), len(vals)

    mf, nf = med("pe_forward")
    mt, nt = med("pe_trailing")
    return {"median_pe_forward": mf, "n_pe_forward": nf,
            "median_pe_trailing": mt, "n_pe_trailing": nt,
            "snapshot_date": _as_date(snapshot_date),
            "scope_label": scope_label}


def _as_date(v) -> Optional[_dt.date]:
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _is_fin(row) -> bool:
    return bool(row.get("is_fin"))


def row_note(row, stats: dict) -> str:
    frags: list[str] = []

    # 1. P/E vs the list median (forward first, trailing fallback)
    pf, pt = _num(row.get("pe_forward")), _num(row.get("pe_trailing"))
    scope = stats.get("scope_label") or "list median"
    if pf is not None:
        m, n = stats.get("median_pe_forward"), stats.get("n_pe_forward", 0)
        frags.append(f"Fwd P/E {pf:.1f} ({scope} {m:.1f}, n={n}).")
    elif pt is not None:
        m, n = stats.get("median_pe_trailing"), stats.get("n_pe_trailing", 0)
        frags.append(f"Trailing P/E {pt:.1f} ({scope} {m:.1f}, n={n}).")

    # 2. balance sheet
    if _is_fin(row):
        pb = _num(row.get("pb_ratio"))
        if pb is not None:
            frags.append(f"P/B {pb:.2f}.")
    else:
        nc, cap = _num(row.get("net_cash_eur_m")), _num(row.get("_eur_cap"))
        if nc is not None and cap and cap > 0:
            r = nc / cap
            frags.append(f"Net cash {r:.0%} of cap." if r >= 0
                         else f"Net debt {abs(r):.0%} of cap.")
        rq = _num(row.get("runway_quarters"))
        if rq is not None:
            frags.append(f"Loss-making: runway {rq:.0f} qtrs.")

    # 3. trend / timing
    rsi = _num(row.get("rsi_14"))
    if rsi is not None:
        frags.append(f"RSI {rsi:.0f}.")
    pfh = _num(row.get("pct_from_high"))
    if pfh is not None and pfh <= -0.01:
        frags.append(f"{abs(pfh):.0%} below 52wk high.")

    # 4. catalyst
    ne, snap = _as_date(row.get("next_earnings")), stats.get("snapshot_date")
    if ne is not None and snap is not None:
        d = (ne - snap).days
        if 0 <= d <= EARNINGS_WINDOW_DAYS:
            frags.append(f"Earnings {ne.isoformat()} ({d}d after snapshot).")

    # 5. caveats
    if _is_fin(row):
        frags.append("Financial: EV/EBITDA, P/FCF, net cash not used.")
    if row.get("industry") == "Real Estate" or row.get("yf_sector") == "Real Estate":
        frags.append("REIT/property: P/E not meaningful.")

    not_scored = None
    if _num(row.get("composite")) is None and row.get("not_scored_reason"):
        not_scored = f"Not scored: {row['not_scored_reason']}."

    if not_scored:
        frags = frags[:MAX_FRAGMENTS - 1] + [not_scored]
    else:
        frags = frags[:MAX_FRAGMENTS]
    return " ".join(frags)


__all__ = ["row_note", "list_stats"]
