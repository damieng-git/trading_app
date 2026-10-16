"""Currency symbols, kept tickers, OTC symbols, exchange probing, ticker collisions.
All offline: Search and fetch are injected fakes."""

import copy
import datetime as _dt
import json
import os
import sys

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, HERE)

import build_hub as H           # noqa: E402
import build_workbook as B      # noqa: E402
import enrich as E              # noqa: E402
import identity as I            # noqa: E402
import parse_swst as P          # noqa: E402
from fakes import _synth_history  # noqa: E402
from test_hub import _clone, base, CFG, TODAY  # noqa: E402,F401

REAL = os.path.join(HERE, "fixtures", "real")


# ------------------------------------------------------------------ Fix 1
@pytest.mark.parametrize("token,code,value", [
    ("₪12.5", "ILS", 12.5), ("Ft1,250", "HUF", 1250.0), ("د.أ3.25", "JOD", 3.25),
    ("K6.89", "ZMW", 6.89), ("XOF500", "XOF", 500.0), ("LK₨45.5", "LKR", 45.5),
    ("PK₨210", "PKR", 210.0), ("TSh2,500", "TZS", 2500.0), ("ر.ع0.45", "OMR", 0.45),
    ("Mex$18.2", "MXN", 18.2), ("KSh90.00", "KES", 90.0), ("د.م105", "MAD", 105.0),
])
def test_new_currency_symbols_price(token, code, value):
    assert P.parse_price(token) == (code, value)


def test_new_currency_symbols_market_cap_and_k_vs_ksh():
    assert P.parse_money("KSh1.5b") == ("KES", 1500.0)
    assert P.parse_money("K2.0m") == ("ZMW", 2.0)
    assert P.parse_money("Mex$3.1t") == ("MXN", 3_100_000.0)
    assert P.parse_price("K6.89")[0] == "ZMW" and P.parse_price("KSh90.00")[0] == "KES"
    # existing behaviour intact
    assert P.parse_price("R$5.5")[0] == "BRL" and P.parse_price("R12")[0] == "ZAR"
    syms = [s for s, _ in P.CURRENCY_SYMBOLS]
    assert syms.index("KSh") < syms.index("K")


def test_new_currency_countries_and_fx():
    cc = P.CURRENCY_COUNTRY
    for code, c in {"ILS": "il", "HUF": "hu", "JOD": "jo", "ZMW": "zm", "LKR": "lk",
                    "PKR": "pk", "TZS": "tz", "OMR": "om", "MXN": "mx", "KES": "ke",
                    "MAD": "ma"}.items():
        assert cc[code] == c
    assert cc.get("XOF") is None
    fx = CFG["fx_per_eur"]
    assert fx["ILS"] == 3.4812 and fx["MXN"] == 19.6855 and "HUF" in fx
    assert "il" not in CFG["region_sets"]["us_eu"] and "mx" not in CFG["region_sets"]["europe"]


# ------------------------------------------------------------------ Fix 2
def _real(name):
    with open(os.path.join(REAL, name), encoding="utf-8") as fh:
        return fh.read()


def test_every_record_becomes_a_row_solid_balance_sheet():
    text = _real("solid_balance_sheet_table.txt")
    recs = P._vertical_records(text, P.extract_header_count(text))
    assert len(recs) == 130
    src = P.parse_paste(text, None, "solid")
    assert len(src.rows) == 130
    tick = {r["ticker"] for r in src.rows}
    assert {"NAM-INDIA", "PE&OLES *"} <= tick
    assert not [n for n in src.notes if n.startswith("dropped")]


def test_tl_r_kept_in_biotech():
    src = P.parse_paste(_real("biotech_table.txt"), None, "biotech")
    assert "TL-R" in {r["ticker"] for r in src.rows}
    assert len(src.rows) == 184


def test_unparseable_ticker_is_noted_not_silent():
    # pipe table: one good row, one with a ticker made of an illegal character
    text = ("| Company | Last Price | 7D | 1Y | Market Cap | Target | Val | Growth | Div | Ind |\n"
            "|---|---|---|---|---|---|---|---|---|---|\n"
            "| ABC | US$10 | 1% | 2% | US$1b | n/a | 1 | 1% | 1% | Banks |\n"
            "| A?B | US$10 | 1% | 2% | US$1b | n/a | 1 | 1% | 1% | Banks |\n")
    src = P.parse_us_table(text, None, "t")
    assert [r["ticker"] for r in src.rows] == ["ABC"]
    assert any(n.startswith("dropped A?B:") for n in src.notes)


# ------------------------------------------------------------------ Fix 3
def test_otc_symbols():
    assert E.map_symbol("LKNC.Y", "us") == "LKNCY"
    assert E.map_symbol("ABCD.F", "us") == "ABCDF"
    assert E.map_symbol("BRK.B", "us") == "BRK-B"
    assert E.map_symbol("ABCD.B", "us") == "ABCD-B"      # last letter not Y/F
    assert E.map_symbol("LKNC.Y", "gb") == "LKNC-Y.L"    # only the US rule


# ------------------------------------------------------------------ Fix 4
def _snap(sym, price, ret, ccy="EUR"):
    return E.Snapshot(symbol=sym, history=_synth_history(price, ret, ccy),
                      info={"currency": ccy, "longName": sym},
                      calendar={"earnings": None, "ex_div": None})


def _dead(sym):
    return E.Snapshot(symbol=sym, history=pd.DataFrame(columns=["close", "volume"]),
                      info={}, ok=False, calendar={"earnings": None, "ex_div": None})


def _fetcher(listings, calls=None):
    def fetch(sym):
        if calls is not None:
            calls.append(sym)
        return listings[sym] if sym in listings else _dead(sym)
    return fetch


def _searcher(quotes_by_ticker, calls):
    def search(q):
        calls.append(q)
        return quotes_by_ticker.get(q, [])
    return search


def _row(ticker, price, ret, country="eu", ccy="EUR", cap=1000.0):
    return {"ticker": ticker, "country": country, "currency": ccy, "price_local": price,
            "ret_1y": ret, "market_cap_local_m": cap, "company": None}


SAN_QUOTES = [{"symbol": "SAN", "exchange": "NYQ"},            # US ADR: no EUR suffix
              {"symbol": "SAN.PA"}, {"symbol": "SAN.MC"},
              {"symbol": "SANF.DE"}]                            # other base


def test_san_resolves_by_price_to_two_companies():
    df = pd.DataFrame([_row("SAN", 71.60, -0.149), _row("SAN", 12.26, 0.30)])
    listings = {"SAN.PA": _snap("SAN.PA", 71.5, -0.15), "SAN.MC": _snap("SAN.MC", 12.3, 0.31)}
    scalls, fcalls = [], []
    out = E.enrich_rows(df, B.load_config(), fetch=_fetcher(listings, fcalls),
                        search=_searcher({"SAN": SAN_QUOTES}, scalls),
                        search_cache=E.SearchCache(None))
    assert list(out["_yf_symbol"]) == ["SAN.PA", "SAN.MC"]
    assert out["_enrich_note"].isna().all() or all(v is None for v in out["_enrich_note"])
    assert "SANF.DE" not in fcalls and "SAN" not in fcalls     # only EUR-venue, same base
    assert scalls == ["SAN"]                                   # second row hit the cache
    cov = out.attrs["enrich_coverage"]
    assert cov["probed"] == 2 and cov["resolved_by_probe"] == 2 and not cov["unresolved"]
    assert "exchange probe" in out.at[0, "_probe_note"]


def test_runner_up_named_and_smallest_price_diff_wins():
    df = pd.DataFrame([_row("SAP", 194.42, 0.1)])
    listings = {"SAP.DE": _snap("SAP.DE", 194.46, 0.1), "SAP.F": _snap("SAP.F", 195.5, 0.1)}
    q = {"SAP": [{"symbol": "SAP.F"}, {"symbol": "SAP.DE"}]}
    out = E.enrich_rows(df, B.load_config(), fetch=_fetcher(listings),
                        search=_searcher(q, []), search_cache=E.SearchCache(None))
    assert out.at[0, "_yf_symbol"] == "SAP.DE"
    assert "runner-up SAP.F" in out.at[0, "_probe_note"]


def test_no_passing_candidate_stays_unresolved_with_note():
    df = pd.DataFrame([_row("SAN", 71.60, -0.149)])
    listings = {"SAN.PA": _snap("SAN.PA", 12.0, -0.149),       # wrong price
                "SAN.MC": _snap("SAN.MC", 71.6, -0.149, ccy="USD")}   # wrong currency
    calls = []
    out = E.enrich_rows(df, B.load_config(), fetch=_fetcher(listings, calls),
                        search=_searcher({"SAN": SAN_QUOTES}, []),
                        search_cache=E.SearchCache(None))
    assert out.at[0, "_yf_symbol"] is None and out.at[0, "pe_trailing"] is None
    assert out.at[0, "_enrich_note"] == "no EUR listing matched price/1Y"
    cov = out.attrs["enrich_coverage"]
    assert cov["probed"] == 1 and cov["resolved_by_probe"] == 0 and cov["unresolved"] == ["SAN"]
    assert len(calls) <= E.MAX_PROBE_FETCHES


def test_blind_probe_when_search_unavailable_is_capped_and_ordered():
    df = pd.DataFrame([_row("ZZZ", 10.0, 0.0)])
    calls = []

    def broken_search(q):
        raise RuntimeError("search down")

    out = E.enrich_rows(df, B.load_config(), fetch=_fetcher({}, calls), search=broken_search,
                        search_cache=E.SearchCache(None))
    assert calls == ["ZZZ.DE", "ZZZ.F", "ZZZ.PA", "ZZZ.AS", "ZZZ.MI", "ZZZ.MC"]
    assert out.at[0, "_enrich_note"] == "no EUR listing matched price/1Y"
    # and it finds a listing inside the cap
    out = E.enrich_rows(df, B.load_config(),
                        fetch=_fetcher({"ZZZ.PA": _snap("ZZZ.PA", 10.0, 0.0)}),
                        search=broken_search, search_cache=E.SearchCache(None))
    assert out.at[0, "_yf_symbol"] == "ZZZ.PA"


def test_row_without_price_or_return_is_never_accepted_on_currency_alone():
    df = pd.DataFrame([_row("SAN", None, None)])
    out = E.enrich_rows(df, B.load_config(),
                        fetch=_fetcher({"SAN.PA": _snap("SAN.PA", 70.0, 0.0)}),
                        search=_searcher({"SAN": SAN_QUOTES}, []),
                        search_cache=E.SearchCache(None))
    assert out.at[0, "_yf_symbol"] is None
    assert out.at[0, "_enrich_note"] == "no EUR listing matched price/1Y"


def test_search_cache_used_on_second_call_and_persisted(tmp_path):
    path = str(tmp_path / "data" / "symbol_search_cache.json")
    df = pd.DataFrame([_row("SAN", 71.60, -0.149)])
    listings = {"SAN.PA": _snap("SAN.PA", 71.5, -0.15)}
    scalls = []
    s = _searcher({"SAN": SAN_QUOTES}, scalls)
    cfg = B.load_config()
    E.enrich_rows(df, cfg, fetch=_fetcher(listings), search=s,
                  search_cache=E.SearchCache(path))
    out = E.enrich_rows(df, cfg, fetch=_fetcher(listings), search=s,
                        search_cache=E.SearchCache(path))      # fresh object, same file
    assert scalls == ["SAN"] and out.at[0, "_yf_symbol"] == "SAN.PA"
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert [q["symbol"] for q in saved["SAN"]["quotes"]][:2] == ["SAN", "SAN.PA"]
    assert "_yf_symbol" not in json.dumps(saved)               # search results only, no decisions


def test_cache_does_not_decide_two_companies_share_one_ticker(tmp_path):
    # Same cached ticker, different row prices: each is re-validated on its own.
    cache = E.SearchCache(str(tmp_path / "c.json"))
    listings = {"SAN.PA": _snap("SAN.PA", 71.5, -0.15), "SAN.MC": _snap("SAN.MC", 12.3, 0.31)}
    for price, ret, want in ((12.26, 0.30, "SAN.MC"), (71.60, -0.149, "SAN.PA")):
        out = E.enrich_rows(pd.DataFrame([_row("SAN", price, ret)]), B.load_config(),
                            fetch=_fetcher(listings),
                            search=_searcher({"SAN": SAN_QUOTES}, []), search_cache=cache)
        assert out.at[0, "_yf_symbol"] == want


def test_no_data_row_resolved_from_search_quote_currency():
    df = pd.DataFrame([_row("ABCD", 2.50, 0.1, country="us", ccy="GBP")])
    q = {"ABCD": [{"symbol": "ABCD.DE", "currency": "EUR"},     # wrong currency: not tried
                  {"symbol": "ABCD.L", "currency": "GBp"},      # GBp counts as GBP
                  {"symbol": "ABCDX"}]}                          # other base
    calls = []
    empty_ok = E.Snapshot(symbol="ABCD", history=pd.DataFrame(columns=["close", "volume"]),
                          info={}, ok=True, calendar={"earnings": None, "ex_div": None})
    listings = {"ABCD": empty_ok, "ABCD.L": _snap("ABCD.L", 2.5, 0.1, ccy="GBp"),
                "ABCD.DE": _snap("ABCD.DE", 2.5, 0.1)}
    out = E.enrich_rows(df, B.load_config(), fetch=_fetcher(listings, calls),
                        search=_searcher(q, []), search_cache=E.SearchCache(None))
    assert out.at[0, "_yf_symbol"] == "ABCD.L"
    assert calls == ["ABCD", "ABCD.L"]
    assert out.attrs["enrich_coverage"]["resolved_by_probe"] == 1


def test_no_data_row_that_finds_nothing_keeps_its_note():
    df = pd.DataFrame([_row("ABCD", 2.50, 0.1, country="us", ccy="USD")])
    empty_ok = E.Snapshot(symbol="ABCD", history=pd.DataFrame(columns=["close", "volume"]),
                          info={}, ok=True, calendar={"earnings": None, "ex_div": None})
    out = E.enrich_rows(df, B.load_config(), fetch=_fetcher({"ABCD": empty_ok}),
                        search=_searcher({"ABCD": []}, []), search_cache=E.SearchCache(None))
    assert out.at[0, "_yf_symbol"] is None
    assert out.at[0, "_enrich_note"].startswith("no data from Yahoo for ABCD")


def test_report_counts_reach_run_report(tmp_path):
    # B.run's report carries probed / resolved_by_probe (injected fetch: blind probing).
    cfg = B.load_config()
    src = P.parse_paste(_real("biotech_table.txt"), None, "biotech")
    res = B.run([src], cfg, str(tmp_path / "x.xlsx"), region="us_eu", enrich=True,
                fetch=_fetcher({}), enrich_scope="filtered")
    rep = res.report["enrich"]
    assert rep["probed"] > 0 and rep["resolved_by_probe"] == 0


# ------------------------------------------------------------------ Fix 5
def _src(rows, name):
    full = []
    for r in rows:
        d = {k: None for k in P.CANONICAL_FIELDS}
        d.update(new_on_screener=False, investing_idea=name, source=name,
                 paste_format="US_TABLE", **r)
        full.append(d)
    return P.ParsedSource("US_TABLE", name, name, None, full)


def test_san_rows_stay_separate_in_merge():
    a = _src([dict(ticker="SAN", currency="EUR", country="eu", price_local=71.60,
                   market_cap_local_m=85700.0)], "biotech")
    b = _src([dict(ticker="SAN", currency="EUR", country="eu", price_local=12.26,
                   market_cap_local_m=173100.0)], "hq")
    merged, rep = B.merge_sources([a, b], B.load_config())
    assert len(merged) == 2 and sorted(merged["price_local"]) == [12.26, 71.60]
    assert all(I.COLLISION_NOTE in n for n in merged["_merge_note"])
    assert rep["same_ticker_kept_separate"] == ["SAN|EUR", "SAN|EUR#2"]
    assert rep["duplicates_removed"] == 0


def test_688578_real_move_still_merges():
    a = _src([dict(ticker="688578", currency="CNY", country="cn", price_local=111.58,
                   market_cap_local_m=50200.0)], "one")
    b = _src([dict(ticker="688578", currency="CNY", country="cn", price_local=77.20,
                   market_cap_local_m=37100.0)], "two")
    merged, rep = B.merge_sources([a, b], B.load_config())
    assert len(merged) == 1 and rep["duplicates_removed"] == 1
    assert merged.iloc[0]["_merge_note"] is None
    assert rep["same_ticker_kept_separate"] == []


def test_either_ratio_over_limit_splits():
    assert I.same_company(100, 1000, 150, 1500)
    assert not I.same_company(100, 1000, 170, 1000)      # price only
    assert not I.same_company(100, 1000, 100, 1700)      # cap only
    assert I.same_company(100, None, 100, 5000)          # no evidence: not split


def test_resolved_symbol_merges_by_symbol_even_when_ticker_differs():
    a = _src([dict(ticker="SAN", currency="EUR", price_local=71.6,
                   market_cap_local_m=85700.0, _yf_symbol="SAN.PA")], "one")
    b = _src([dict(ticker="SANOFI", currency="EUR", price_local=99.0,
                   market_cap_local_m=999.0, _yf_symbol="SAN.PA")], "two")
    merged, _ = B.merge_sources([a, b], B.load_config())
    assert len(merged) == 1
    # and two symbols never merge on ticker|currency
    c = _src([dict(ticker="SAN", currency="EUR", price_local=71.6,
                   market_cap_local_m=85700.0, _yf_symbol="SAN.PA")], "one")
    d = _src([dict(ticker="SAN", currency="EUR", price_local=71.6,
                   market_cap_local_m=85700.0, _yf_symbol="SAN.MC")], "two")
    merged, _ = B.merge_sources([c, d], B.load_config())
    assert len(merged) == 2


def _san_lists(base, tmp_path, p1, c1, s1, p2, c2, s2):
    _, src = base

    def mut(price, cap, sym):
        def f(r):
            if r.pop("_first", False):
                r.update(ticker="SAN", currency="EUR", country="eu", price_local=price,
                         market_cap_local_m=cap, _yf_symbol=sym)
        return f

    paths = []
    for slug, title, snap, args in (("list-a", "List A", "2026-09-20", (p1, c1, s1)),
                                    ("list-b", "List B", "2026-09-25", (p2, c2, s2))):
        with open(src, encoding="utf-8") as fh:
            d = json.load(fh)
        d["rows"][0]["_first"] = True
        tmp = tmp_path / f"{slug}.src.json"
        tmp.write_text(json.dumps(d), encoding="utf-8")
        paths.append(_clone(str(tmp), tmp_path, slug, title, snap, snap + "T10:00:00",
                            mutate=mut(*args)))
    return str(tmp_path)


def _san_records(rdir):
    views = H.build_views(H.latest_per_slug(rdir), CFG, TODAY)
    allv = next(v for v in views if v["id"] == "all")
    return allv, [r for r in allv["records"] if r["ticker"] == "SAN"]


def test_hub_all_lists_shows_two_san_entries(base, tmp_path):
    rdir = _san_lists(base, tmp_path, 71.60, 85700.0, None, 12.26, 173100.0, None)
    allv, san = _san_records(rdir)
    assert len(san) == 2
    assert all(I.COLLISION_NOTE in (r["data_note"] or "") for r in san)
    assert len(allv["records"]) == 33                      # 31 shared + 2 SAN
    assert sorted(r["n_lists"] for r in san) == [1, 1]


def test_hub_san_with_resolved_symbols_stays_two_and_same_symbol_merges(base, tmp_path):
    rdir = _san_lists(base, tmp_path, 71.60, 85700.0, "SAN.PA", 12.26, 173100.0, "SAN.MC")
    allv, san = _san_records(rdir)
    assert len(san) == 2 and not any(I.COLLISION_NOTE in (r["data_note"] or "") for r in san)
    # same resolved symbol in both lists: one company, even with very different price/cap
    sub = tmp_path / "x"
    sub.mkdir()
    rdir2 = _san_lists(base, sub, 71.60, 85700.0, "SAN.PA", 12.26, 173100.0, "SAN.PA")
    allv2, san2 = _san_records(rdir2)
    assert len(san2) == 1 and san2[0]["n_lists"] == 2


def test_hub_688578_style_move_merges(base, tmp_path):
    rdir = _san_lists(base, tmp_path, 111.58, 50200.0, None, 77.20, 37100.0, None)
    allv, san = _san_records(rdir)
    assert len(san) == 1 and san[0]["n_lists"] == 2 and len(allv["records"]) == 32
