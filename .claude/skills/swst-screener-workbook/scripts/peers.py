"""Peer comparison: each stock's multiples against the median of its industry peers.

Peers are the other stocks in the same row set (the hub's "All lists" universe, or one list
for a per-list page), one row per company. The peer group is the yfinance industry when it
has at least MIN_PEERS other stocks, else the yfinance sector, else none. A median needs
MIN_PEERS values; the stock itself is never in its own median.

Adds to each row: peer_group ("industry: Biotechnology" | "sector: Healthcare" | None),
peer_n (other stocks in the group), peer_med_<m> and vs_peer_<m> (row / median - 1, so
negative = cheaper than peers) for each metric in PEER_METRICS.
Screening aid, not investment advice: cheaper than peers is not a bargain by itself.
"""
from __future__ import annotations

from statistics import median

from flags import _num

PEER_METRICS = ("pe_trailing", "pe_forward", "ps_ratio", "ev_sales", "ev_fcf")
MIN_PEERS = 3


def _group_of(row: dict, by_ind: dict, by_sec: dict, idx: int):
    ind, sec = row.get("yf_industry"), row.get("yf_sector")
    if ind and len(by_ind.get(ind, [])) - 1 >= MIN_PEERS:
        return f"industry: {ind}", [j for j in by_ind[ind] if j != idx]
    if sec and len(by_sec.get(sec, [])) - 1 >= MIN_PEERS:
        return f"sector: {sec}", [j for j in by_sec[sec] if j != idx]
    return None, []


def peer_fields(rows: list[dict]) -> list[dict]:
    """One dict of peer fields per input row (same order). Pure; rows are not changed."""
    by_ind: dict = {}
    by_sec: dict = {}
    for i, r in enumerate(rows):
        if r.get("yf_industry"):
            by_ind.setdefault(r["yf_industry"], []).append(i)
        if r.get("yf_sector"):
            by_sec.setdefault(r["yf_sector"], []).append(i)
    out = []
    for i, r in enumerate(rows):
        label, others = _group_of(r, by_ind, by_sec, i)
        d = {"peer_group": label, "peer_n": len(others)}
        for m in PEER_METRICS:
            vals = [v for v in (_num(rows[j].get(m)) for j in others) if v is not None]
            med = float(median(vals)) if len(vals) >= MIN_PEERS else None
            mine = _num(r.get(m))
            d[f"peer_med_{m}"] = med
            d[f"vs_peer_{m}"] = None if med in (None, 0) or mine is None else mine / med - 1
        out.append(d)
    return out


def attach(rows: list[dict]) -> list[dict]:
    """Rows with the peer fields added (new dicts)."""
    return [{**r, **p} for r, p in zip(rows, peer_fields(rows))]
