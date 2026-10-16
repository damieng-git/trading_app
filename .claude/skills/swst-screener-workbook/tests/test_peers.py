import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import enrich as E   # noqa: E402
import peers as PR   # noqa: E402


def _row(t, ind, sec, pe=None, ps=None, evf=None):
    return {"ticker": t, "yf_industry": ind, "yf_sector": sec, "pe_trailing": pe, "ps_ratio": ps, "ev_fcf": evf}


def test_industry_median_excludes_the_stock_itself():
    rows = [_row("A", "Bio", "Health", pe=10), _row("B", "Bio", "Health", pe=20),
            _row("C", "Bio", "Health", pe=30), _row("D", "Bio", "Health", pe=40)]
    out = PR.peer_fields(rows)
    assert out[0]["peer_group"] == "industry: Bio" and out[0]["peer_n"] == 3
    assert out[0]["peer_med_pe_trailing"] == 30          # median of 20, 30, 40
    assert out[0]["vs_peer_pe_trailing"] == pytest.approx(10 / 30 - 1)
    assert out[3]["peer_med_pe_trailing"] == 20          # median of 10, 20, 30


def test_falls_back_to_sector_then_none():
    rows = [_row("A", "Bio", "Health", ps=2), _row("B", "Pharma", "Health", ps=4),
            _row("C", "Devices", "Health", ps=6), _row("D", "Tools", "Health", ps=8),
            _row("E", "Banks", "Financials", ps=1)]
    out = PR.peer_fields(rows)
    assert out[0]["peer_group"] == "sector: Health" and out[0]["peer_med_ps_ratio"] == 6
    assert out[4]["peer_group"] is None and out[4]["vs_peer_ps_ratio"] is None


def test_median_needs_three_values():
    rows = [_row("A", "Bio", "H", evf=10), _row("B", "Bio", "H", evf=12), _row("C", "Bio", "H"),
            _row("D", "Bio", "H")]
    out = PR.peer_fields(rows)
    assert out[2]["peer_n"] == 3 and out[2]["peer_med_ev_fcf"] is None       # only 2 peer values


def test_ev_ratios_from_info():
    info = {"enterpriseToRevenue": 3.4, "enterpriseValue": 15.8e9, "freeCashflow": 1.31e9}
    assert E.ev_sales(info) == pytest.approx(3.4)
    assert E.ev_fcf(info) == pytest.approx(15.8 / 1.31)
    assert E.ev_fcf({"enterpriseValue": 1e9, "freeCashflow": -5e6}) is None
    assert E.ev_sales({"enterpriseToRevenue": -1}) is None
