"""Unit + integration tests for the Phase-1 yfinance enrichment module.

All offline: pure metric functions run on synthetic arrays, and the integration
path injects a fake fetch (no pickled yf objects, no network). One live smoke
test is marked @pytest.mark.network and skipped by default.

Run:  python -m pytest tests/test_enrich.py -q   (from the skill dir)
"""

import datetime as _dt
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import enrich as E          # noqa: E402
import build_workbook as B  # noqa: E402
import parse_swst as P      # noqa: E402
import verify as V          # noqa: E402


# ---------------------------------------------------------------------------
# Ticker mapping (decision #3)
# ---------------------------------------------------------------------------
def test_map_symbol_uk_and_us():
    assert E.map_symbol("SN.", "gb") == "SN.L"     # strip trailing dot, add .L
    assert E.map_symbol("HIK", "gb") == "HIK.L"
    assert E.map_symbol("SYK", "us") == "SYK"       # US: no suffix
    assert E.map_symbol("MTHH", "dk") == "MTHH.CO"


def test_map_symbol_unresolved_is_none():
    # Unknown / missing country -> None (caller blanks + logs), never a bare fetch.
    assert E.map_symbol("XYZ", None) is None
    assert E.map_symbol("XYZ", "zz") is None
    assert E.map_symbol("", "us") is None
    assert E.map_symbol(None, "us") is None


# ---------------------------------------------------------------------------
# GBX / pence normalization (decision #2)
# ---------------------------------------------------------------------------
def test_gbx_to_gbp_conversion():
    s = pd.Series([1000.0, 1027.5, 1600.0])
    out = E.to_major_units(s, "GBp")
    assert list(out) == pytest.approx([10.0, 10.275, 16.0])
    # GBX alias and non-pence pass through untouched
    assert list(E.to_major_units(s, "GBX")) == pytest.approx([10.0, 10.275, 16.0])
    assert list(E.to_major_units(s, "USD")) == pytest.approx(list(s))
    assert E.iso_currency("GBp") == "GBP"
    assert E.iso_currency("USD") == "USD"


# ---------------------------------------------------------------------------
# Momentum / price return
# ---------------------------------------------------------------------------
def test_price_return():
    close = pd.Series(np.linspace(100.0, 200.0, 260))
    # 252-bar return over a 260-long doubling ramp
    assert E.price_return(close, 252) == pytest.approx(
        close.iloc[-1] / close.iloc[-253] - 1)
    # insufficient data -> None, never 0
    assert E.price_return(pd.Series([1.0, 2.0]), 252) is None


# ---------------------------------------------------------------------------
# Max drawdown
# ---------------------------------------------------------------------------
def test_max_drawdown():
    # up to 100, down to 50 (=-50%), recover to 80
    close = pd.Series([50, 100, 50, 80], dtype=float)
    assert E.max_drawdown(close) == pytest.approx(-0.5)
    # monotonic up -> ~0 drawdown
    assert E.max_drawdown(pd.Series([1, 2, 3, 4], dtype=float)) == pytest.approx(0.0)
    assert E.max_drawdown(pd.Series([], dtype=float)) is None


# ---------------------------------------------------------------------------
# MA distance and 52wk high/low distance
# ---------------------------------------------------------------------------
def test_ma_distance():
    close = pd.Series([10.0] * 49 + [11.0])   # 50 bars; SMA50 = (49*10+11)/50
    sma = (49 * 10 + 11) / 50
    assert E.ma_distance(close, 50) == pytest.approx(11.0 / sma - 1.0)
    assert E.ma_distance(pd.Series([10.0] * 10), 50) is None  # too short


def test_52wk_distances():
    close = pd.Series(list(np.linspace(80, 120, 300)))  # low 80, high 120, last 120
    assert E.dist_from_high(close, 252) == pytest.approx(0.0, abs=1e-9)  # at high
    # last vs low over window
    lo = close.tail(252).min()
    assert E.dist_from_low(close, 252) == pytest.approx(close.iloc[-1] / lo - 1)


# ---------------------------------------------------------------------------
# Wilder RSI (local implementation, decision #9)
# ---------------------------------------------------------------------------
def test_wilder_rsi_all_gains_is_100():
    close = pd.Series(np.arange(1, 40, dtype=float))  # strictly rising
    assert E.wilder_rsi(close, 14) == pytest.approx(100.0)


def test_wilder_rsi_known_series():
    # Classic Wilder textbook sequence -> RSI ~ 70.46 on the first computed bar.
    data = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    val = E.wilder_rsi(pd.Series(data), 14)
    assert val == pytest.approx(70.46, abs=0.5)
    assert E.wilder_rsi(pd.Series([1.0, 2.0]), 14) is None  # too short -> None


# ---------------------------------------------------------------------------
# Median $-volume with FX (decision #6)
# ---------------------------------------------------------------------------
def test_median_dollar_volume_with_fx():
    close = pd.Series([10.0] * 30)      # already major units
    volume = pd.Series([100.0] * 30)    # turnover = 1000 local/day, median 1000
    # fx = 2 local per EUR -> 500 EUR
    assert E.median_dollar_volume(close, volume, fx_rate=2.0, window=30) == pytest.approx(500.0)
    # missing fx -> None, never 0
    assert E.median_dollar_volume(close, volume, fx_rate=None) is None


# ---------------------------------------------------------------------------
# Multiples blank on non-positive denominator (decision #5) + blank-not-zero
# ---------------------------------------------------------------------------
def test_pe_blank_on_negative_eps():
    assert E.pe_trailing({"trailingEps": -1.2, "trailingPE": 15.0}) is None
    assert E.pe_trailing({"trailingEps": 0.0, "trailingPE": 15.0}) is None
    assert E.pe_trailing({"trailingEps": 5.0, "trailingPE": 15.0}) == pytest.approx(15.0)
    assert E.pe_forward({"forwardEps": -0.3, "forwardPE": 10.0}) is None
    # missing entirely -> None (not 0)
    assert E.pe_trailing({}) is None


def test_other_multiples_blank_rules():
    assert E.ev_ebitda({"enterpriseToEbitda": -4.0}) is None       # negative EBITDA
    assert E.ev_ebitda({"enterpriseToEbitda": 12.0}) == pytest.approx(12.0)
    assert E.ps_ratio({"priceToSalesTrailing12Months": 0.0}) is None
    assert E.p_fcf({"marketCap": 1000.0, "freeCashflow": -50.0}) is None  # cash burn
    assert E.p_fcf({"marketCap": 1000.0, "freeCashflow": 50.0}) == pytest.approx(20.0)


def test_runway_only_for_burn():
    # positive operating cash flow -> not a burn -> None
    assert E.runway_quarters({"totalCash": 100.0, "operatingCashflow": 20.0}) is None
    # burn of -25/yr on 100 cash -> 4 years -> 16 quarters
    assert E.runway_quarters(
        {"totalCash": 100.0, "operatingCashflow": -25.0}) == pytest.approx(16.0)
    assert E.runway_quarters({"totalCash": 100.0}) is None  # missing statement


def test_cap_tier():
    cfg = {"thresholds": {"cap_large_eur_m": 10000, "cap_mid_eur_m": 2000}}
    assert E.cap_tier(50000, cfg) == "large"
    assert E.cap_tier(3000, cfg) == "mid"
    assert E.cap_tier(500, cfg) == "small"
    assert E.cap_tier(None, cfg) is None


# ---------------------------------------------------------------------------
# Cross-check guard (decision #4)
# ---------------------------------------------------------------------------
def test_cross_check_passes_on_agreement():
    ok, reason = E.cross_check("USD", 1.049, 65.11, "USD", 1.049, 65.11)
    assert ok and reason is None
    # GBp yf currency reconciles to GBP paste currency
    ok, _ = E.cross_check("GBp", -0.25, 10.28, "GBP", -0.251, 10.275)
    assert ok


def test_cross_check_blanks_on_mismatch():
    ok, reason = E.cross_check("EUR", 0.0, 10.0, "USD", 0.0, 10.0)
    assert not ok and "currency" in reason
    ok, reason = E.cross_check("USD", -0.30, 65.0, "USD", 1.049, 65.0)  # wrong listing
    assert not ok and "1y-return" in reason
    ok, reason = E.cross_check("USD", 0.0, 200.0, "USD", 0.0, 65.0)     # wrong price
    assert not ok and "price" in reason


# ---------------------------------------------------------------------------
# Fake fetch for the integration tests (no network, no pickled yf objects)
# ---------------------------------------------------------------------------
def _synth_history(last_price, ret_1y, currency, n=800):
    """Linear ramp so that close[-1]=last_price and close[-253]=last_price/(1+ret)."""
    raw_last = last_price * (100.0 if currency in ("GBp", "GBX") else 1.0)
    r = ret_1y if ret_1y is not None else 0.0
    raw_start = raw_last / (1.0 + r) if (1.0 + r) != 0 else raw_last
    close = np.linspace(raw_start * 0.9, raw_last, n)
    # pin the 12m anchor exactly for a clean cross-check
    close[-253] = raw_last / (1.0 + r)
    idx = pd.date_range(end=_dt.date(2026, 9, 19), periods=n, freq="D")
    return pd.DataFrame({"close": close, "volume": np.full(n, 1_000_000.0)}, index=idx)


def _fake_fetch_from_frame(frame):
    """Build a symbol->Snapshot map echoing each row's currency/price/1y return so
    the cross-check passes, with plausible info so multiples populate."""
    by_symbol = {}
    for _, row in frame.iterrows():
        sym = E.map_symbol(row["ticker"], row["country"])
        if sym is None:
            continue
        ccy = "GBp" if sym.endswith(".L") else "USD"
        price = row["price_local"] if row["price_local"] is not None else 50.0
        ret = row["ret_1y"]
        loss_maker = row["ticker"] in {"LEGN", "MMED", "CLOV"}
        info = {
            "currency": ccy,
            "financialCurrency": "GBP" if ccy == "GBp" else "USD",
            "trailingEps": -1.0 if loss_maker else 5.0,
            "trailingPE": 20.0,
            "forwardEps": 1.5,
            "forwardPE": 14.0,
            "priceToSalesTrailing12Months": 4.0,
            "enterpriseToEbitda": 15.0,
            "marketCap": 1.0e10,
            "freeCashflow": -2.0e8 if loss_maker else 5.0e8,
            "beta": 0.8,
            "totalCash": 8.0e8,
            "totalDebt": 1.0e8,
            "operatingCashflow": -2.0e8 if loss_maker else 6.0e8,
        }
        snap = E.Snapshot(
            symbol=sym,
            history=_synth_history(price, ret, ccy),
            info=info,
            calendar={"earnings": _dt.date(2026, 10, 29),
                      "ex_div": _dt.date(2026, 9, 30)},
        )
        by_symbol[sym] = snap

    def fetch(symbol):
        return by_symbol.get(
            symbol,
            E.Snapshot(symbol=symbol,
                       history=pd.DataFrame(columns=["close", "volume"]),
                       info={}, ok=False, calendar={"earnings": None, "ex_div": None}))
    return fetch


def _healthcare_sources():
    with open(os.path.join(HERE, "fixtures", "real", "healthcare_table.txt"),
              encoding="utf-8") as fh:
        text = fh.read()
    return [P.parse_paste(text, "Undervalued Healthcare Stocks", "healthcare")]


# ---------------------------------------------------------------------------
# Integration: enrich does not mutate protected columns or sort
# ---------------------------------------------------------------------------
def test_enrich_does_not_mutate_protected_columns_or_sort():
    cfg = B.load_config()
    sources = _healthcare_sources()
    merged, _ = B.merge_sources(sources, cfg)
    fetch = _fake_fetch_from_frame(merged)

    before = merged.copy(deep=True)
    out = E.enrich_rows(merged, cfg, fetch=fetch)

    # protected columns identical pre/post enrich (decision #8)
    for col in ("ret_1y", "ret_7d", "currency", "market_cap_local_m"):
        pd.testing.assert_series_equal(out[col], before[col], check_names=False)
    # order preserved (no re-sort): ticker sequence unchanged
    assert list(out["ticker"]) == list(before["ticker"])
    # new columns exist
    for f in E.NUMERIC_INPUT_FIELDS + E.DATE_FIELDS + ["cap_tier"]:
        assert f in out.columns
    # at least the valuation multiples populate for the profitable majority
    assert out["pe_forward"].notna().sum() >= 20
    assert out["mom_12m"].notna().sum() >= 20
    # blank-not-zero: loss-makers get None P/E, never 0
    lm = out[out["ticker"] == "LEGN"].iloc[0]
    assert lm["pe_trailing"] is None
    assert lm["runway_quarters"] is not None and lm["runway_quarters"] > 0


def test_cross_check_blanks_row_in_pipeline():
    """A snapshot whose currency disagrees blanks the whole enrich row + logs it."""
    cfg = B.load_config()
    sources = _healthcare_sources()
    merged, _ = B.merge_sources(sources, cfg)

    def bad_fetch(symbol):
        return E.Snapshot(
            symbol=symbol,
            history=_synth_history(50.0, 0.0, "EUR"),
            info={"currency": "EUR", "trailingEps": 5.0, "trailingPE": 20.0},
            calendar={"earnings": None, "ex_div": None})

    out = E.enrich_rows(merged.head(3), cfg, fetch=bad_fetch)
    # US rows have paste currency USD; yf says EUR -> every row blanked
    assert out["pe_trailing"].isna().all()
    assert len(out.attrs["enrich_coverage"]["cross_check_failed"]) == 3


# ---------------------------------------------------------------------------
# Integration: verify_build still passes on an enriched workbook
# ---------------------------------------------------------------------------
def test_verify_passes_on_enriched_workbook(tmp_path):
    cfg = B.load_config()
    sources = _healthcare_sources()
    merged, _ = B.merge_sources(sources, cfg)
    fetch = _fake_fetch_from_frame(merged)

    out = str(tmp_path / "enriched.xlsx")
    res = B.run(sources, cfg, out, region=None, enrich=True, fetch=fetch)
    checks = V.verify_build(res.unfiltered_path, res.frame, cfg)
    assert V.all_passed(checks), "verify: " + "; ".join(m for ok, m in checks if not ok)

    # the enriched, blue INPUT columns and the three FLAG formulas are present
    from openpyxl import load_workbook
    wb = load_workbook(res.unfiltered_path)
    ws = wb["Screen"]
    header = [c.value for c in ws[1]]
    for name in ("P/E (forward)", "RSI (14)", "Cap tier", "Next earnings",
                 "Pullback flag", "Oversold flag", "Breakout flag"):
        assert name in header
    # flag cells are live formulas over the input columns
    osold_col = header.index("Oversold flag") + 1
    v = ws.cell(row=2, column=osold_col).value
    assert isinstance(v, str) and v.startswith("=IF(") and "oversold" in v


# ---------------------------------------------------------------------------
# Real captured fixture (offline): the snapshots saved by the live run reload
# and the pence name normalizes correctly. Proves the fixture-reuse path.
# ---------------------------------------------------------------------------
FIXDIR = os.path.join(HERE, "fixtures", "enrich")


@pytest.mark.skipif(not os.path.exists(os.path.join(FIXDIR, "SN.L.history.csv")),
                    reason="captured enrich fixtures not present")
def test_real_lse_fixture_normalizes_and_computes():
    snap = E.load_snapshot(FIXDIR, "SN.L")
    assert snap.info.get("currency") in ("GBp", "GBX")
    raw_last = float(snap.history["close"].iloc[-1])
    assert raw_last > 200  # quoted in pence (hundreds/thousands), not pounds
    major = E.to_major_units(snap.history["close"], snap.info["currency"])
    assert float(major.iloc[-1]) == pytest.approx(raw_last / 100.0)
    # a ratio metric is unit-invariant and finite
    assert -1.0 <= E.max_drawdown(major) <= 0.0
    assert E.pe_trailing(snap.info) is None or E.pe_trailing(snap.info) > 0


# ---------------------------------------------------------------------------
# Live smoke test — skipped by default (decision #11)
# ---------------------------------------------------------------------------
@pytest.mark.network
def test_live_smoke_us_and_lse():
    snap = E._fetch_snapshot("SYK")
    assert snap.ok
    assert snap.info.get("currency") == "USD"
    lse = E._fetch_snapshot("SN.L")
    assert lse.info.get("currency") in ("GBp", "GBX")


# ---------------------------------------------------------------------------
# Dashboard v1: symbol mapping rules, overrides, new input fields
# ---------------------------------------------------------------------------
def test_map_symbol_spaces_dots_and_eu():
    assert E.map_symbol("SEB A", "se") == "SEB-A.ST"
    assert E.map_symbol("ZZ B", "se") == "ZZ-B.ST"
    assert E.map_symbol("BT.A", "gb") == "BT-A.L"
    assert E.map_symbol("SN.", "gb") == "SN.L"
    assert E.map_symbol("ALV", "eu") is None
    assert E.map_symbol("ALV", "eu", "EUR") is None


def test_map_symbol_override_wins():
    ov = {"ALV|EUR": "ALV.DE"}
    assert E.map_symbol("ALV", "eu", "EUR", ov) == "ALV.DE"
    # override is keyed by ticker AND currency
    assert E.map_symbol("ALV", "eu", "USD", ov) is None
    assert E.map_symbol("SYK", "us", "USD", ov) == "SYK"


def _info(sym):
    import json
    with open(os.path.join(FIXDIR, f"{sym}.info.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_new_input_fields_from_real_snapshots():
    syk = _info("SYK")
    assert E.pb_ratio(syk) == pytest.approx(4.3992453)
    assert E.roe(syk) == pytest.approx(0.16508)
    assert E.fcf_positive(syk) is True
    assert E.fcf_positive(_info("AGL")) is False          # negative FCF
    assert E.fcf_positive(_info("MMED")) is None          # missing, not False
    assert E.pb_ratio({"priceToBook": -2.0}) is None
    assert E.pb_ratio({}) is None
    assert E.roe({}) is None


def test_enrich_one_sets_new_fields_and_screen_columns_unchanged():
    cfg = B.load_config()
    snap = E.load_snapshot(FIXDIR, "SYK")
    row = {"ticker": "SYK", "country": "us", "currency": "USD", "ret_1y": None,
           "price_local": None, "market_cap_local_m": 140000.0}
    out = E.enrich_one(row, snap, cfg, E._fx_map(cfg))
    assert out["pb_ratio"] == pytest.approx(4.3992453)
    assert out["fcf_positive"] is True
    assert out["yf_sector"] == "Healthcare" and out["yf_industry"] == "Medical Devices"
    for f in ("pb_ratio", "roe", "fcf_positive", "yf_sector", "yf_industry"):
        assert f in E.ENRICH_FIELDS
    # xlsx unchanged: none of the new fields are screen columns
    src = set(B.COLUMN_SOURCES.values())
    assert not ({"pb_ratio", "roe", "fcf_positive", "yf_sector", "yf_industry"} & src)
    assert not any(c in cfg["screen_columns"] for c in ("P/B", "ROE"))


def test_every_us_eu_country_has_a_yahoo_suffix():
    # Deliberate exceptions: "eu" (EUR-quoted, exchange unknown) and "lu"
    # (no Yahoo suffix we trust; better unresolved than guessed).
    import build_workbook as B
    cfg = B.load_config()
    for c in cfg["region_sets"]["us_eu"]:
        if str(c) in ("eu", "lu"):
            continue
        assert E.map_symbol("ABC", str(c), None) is not None, c
    assert E.map_symbol("NEU", "pl") == "NEU.WA"


def test_yahoo_with_no_data_is_reported_not_silent():
    import build_workbook as B
    cfg = B.load_config()
    df = pd.DataFrame([{"ticker": "IDHC", "country": "us", "currency": "USD",
                        "price_local": 0.55, "ret_1y": 0.049, "ret_7d": 0.054,
                        "market_cap_local_m": 303.5}])
    empty = lambda sym: E.Snapshot(symbol=sym, history=pd.DataFrame(columns=["close", "volume"]),
                                   info={}, ok=True, calendar={"earnings": None, "ex_div": None})
    out = E.enrich_rows(df, cfg, fetch=empty)
    assert out.loc[0, "_enrich_note"].startswith("no data from Yahoo for IDHC")
    assert out.attrs["enrich_coverage"]["fetch_failed"] == ["IDHC"]
