"""Filter-first pipeline: us_eu region, enrich_scope, run_pipeline end to end.

All offline: a fake fetch (tests/fakes.py) or the saved healthcare snapshots.
"""

import datetime as _dt
import json
import os
import re
import sys

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, HERE)

import build_workbook as B       # noqa: E402
import dashboard as D            # noqa: E402
import enrich as E               # noqa: E402
import parse_swst as P           # noqa: E402
import run_pipeline as R         # noqa: E402
import verify as V               # noqa: E402
from fakes import fake_fetch_for  # noqa: E402

DIV = os.path.join(HERE, "fixtures", "dividend_table.txt")
HC = os.path.join(HERE, "fixtures", "real", "healthcare_table.txt")
FIXDIR = os.path.join(HERE, "fixtures", "enrich")
NOW = _dt.datetime(2026, 9, 20, 10, 0)
BANNED = re.compile(r"buy|sell|strong|opportunity|should|undervalued|cheap", re.I)


def _sources(path):
    with open(path, encoding="utf-8") as fh:
        return [P.parse_paste(fh.read(), None, os.path.splitext(os.path.basename(path))[0])]


def test_us_eu_region_set():
    cfg = B.load_config()
    codes = B.resolve_region("us_eu", cfg)
    assert "us" in codes and "eu" in codes and "no" in codes
    assert set(cfg["region_sets"]["europe"]) <= set(codes)
    assert "tr" not in codes
    # existing sets untouched
    assert "eu" not in cfg["region_sets"]["europe"]
    assert cfg["symbol_overrides"] == {}


def test_filter_us_eu_dividend_fixture():
    cfg = B.load_config()
    merged, _ = B.merge_sources(_sources(DIV), cfg)
    kept, rep = B.filter_region(merged, B.resolve_region("us_eu", cfg), cfg)
    assert rep["kept"] == 32
    assert rep["kept_by_country"] == {"us": 21, "eu": 5, "gb": 4, "se": 2}
    assert any("5 EUR-quoted row(s) kept with unknown exchange" in j
               for j in rep["judgment_calls"])


def test_enrich_scope_filtered_fetches_only_survivors(tmp_path):
    cfg = B.load_config()
    sources = _sources(DIV)
    merged, _ = B.merge_sources(sources, cfg)
    calls = []
    fetch = fake_fetch_for(merged, calls)
    res = B.run(sources, cfg, str(tmp_path / "d.xlsx"), region="us_eu",
                enrich=True, fetch=fetch, enrich_scope="filtered")
    kept, _ = B.filter_region(merged, B.resolve_region("us_eu", cfg), cfg)
    want = {E.map_symbol(r["ticker"], r["country"], r["currency"])
            for _, r in kept.iterrows()} - {None}
    # the survivors are fetched once each; the only other fetches are exchange probes
    # of the 5 unresolved EUR ("eu") rows (their ticker + a EUR venue suffix)
    eu_bases = {E._norm_base(r["ticker"]) for _, r in kept.iterrows()
                if r["country"] == "eu"}
    extra = set(calls) - want
    assert want <= set(calls) and len(want) == 27
    assert extra and all(E._split_symbol(c)[0] in eu_bases
                         and E._split_symbol(c)[1] in E.EUR_VENUE_SUFFIXES for c in extra)
    assert res.report["enrich"] == {"enriched": 27, "unresolved": 5,
                                    "cross_check_failed": 0, "fetch_failed": 0,
                                    "probed": 5, "resolved_by_probe": 0}
    for path, frame in ((res.filtered_path, res.frame),):
        checks = V.verify_build(path, frame, cfg)
        assert V.all_passed(checks), [m for ok, m in checks if not ok]
    assert len(res.frame) == 32 and os.path.exists(res.unfiltered_path)


def test_enrich_scope_filtered_leaves_outside_rows_blank_and_protected_cols(tmp_path):
    cfg = B.load_config()
    sources = _sources(DIV)
    merged, _ = B.merge_sources(sources, cfg)
    res = B.run(sources, cfg, str(tmp_path / "d.xlsx"), region="us_eu", enrich=True,
                fetch=fake_fetch_for(merged), enrich_scope="filtered")
    # read the unfiltered workbook back: rows outside the region have no P/E
    from openpyxl import load_workbook
    ws = load_workbook(res.unfiltered_path)["Screen"]
    hdr = [c.value for c in ws[1]]
    pe_i, ctry_i = hdr.index("P/E (forward)"), hdr.index("Country")
    n_out = n_out_filled = 0
    for r in range(2, ws.max_row + 1):
        if str(ws.cell(row=r, column=ctry_i + 1).value).lower() not in B.resolve_region(
                "us_eu", cfg):
            n_out += 1
            n_out_filled += ws.cell(row=r, column=pe_i + 1).value is not None
    assert n_out == 50 and n_out_filled == 0
    # protected columns identical to the merged frame for the surviving rows
    kept, _ = B.filter_region(merged, B.resolve_region("us_eu", cfg), cfg)
    back = res.frame.set_index("ticker")
    for _, r in kept.iterrows():
        for col in ("ret_1y", "ret_7d", "currency", "market_cap_local_m"):
            a, b = r[col], back.loc[r["ticker"], col]
            assert (pd.isna(a) and pd.isna(b)) or a == b


def test_enrich_scope_all_is_default_and_unchanged(tmp_path):
    cfg = B.load_config()
    sources = _sources(HC)
    merged, _ = B.merge_sources(sources, cfg)
    calls = []
    res = B.run(sources, cfg, str(tmp_path / "h.xlsx"), region=None, enrich=True,
                fetch=fake_fetch_for(merged, calls))
    assert len(calls) == 27
    assert res.report["enrich"]["enriched"] == 27


@pytest.mark.parametrize("path,eu_unresolved", [(DIV, 5), (HC, 0)])
def test_run_pipeline_end_to_end(tmp_path, path, eu_unresolved):
    cfg = B.load_config()
    merged, _ = B.merge_sources(_sources(path), cfg)
    logs = []
    out = R.run_pipeline(path, out_dir=str(tmp_path), fetch=fake_fetch_for(merged),
                         now=NOW, log=logs.append)
    assert os.path.exists(out["html_path"]) and out["html_path"].endswith(".html")
    assert out["html_path"].startswith(str(tmp_path))
    assert V.all_passed(out["checks"])
    c = out["counts"]
    assert c["scored"] + c["unscored"] == c["kept"]
    assert c["unscored"] >= eu_unresolved
    assert out["html_bytes"] < 300_000
    html = open(out["html_path"], encoding="utf-8").read()
    data = json.loads(re.search(r'id="data">(.*?)</script>', html, re.S).group(1))
    rows = data["rows"]
    assert len(rows) == c["kept"]
    eu = [r for r in rows if r["country"] == "eu"]
    assert len(eu) == eu_unresolved
    for r in eu:
        assert r["score"] is None
        assert r["reason"].startswith("missing:")
        assert r["data_note"] == "no EUR listing matched price/1Y"
        assert "Not scored:" in r["note"]
    if eu_unresolved:
        assert "EUR-quoted row(s) kept with unknown exchange" in html
    # no buy/sell language in any generated note
    assert not [r["note"] for r in rows if BANNED.search(r["note"])]
    assert logs[0] == out["banner"]


def test_run_pipeline_never_overwrites(tmp_path):
    cfg = B.load_config()
    merged, _ = B.merge_sources(_sources(HC), cfg)
    fetch = fake_fetch_for(merged)
    R.run_pipeline(HC, out_dir=str(tmp_path), fetch=fetch, now=NOW, log=lambda m: None)
    with pytest.raises(FileExistsError):
        R.run_pipeline(HC, out_dir=str(tmp_path), fetch=fetch, now=NOW,
                       log=lambda m: None)


def test_run_pipeline_with_saved_snapshots(tmp_path):
    """The captured healthcare snapshots drive the whole pipeline offline."""
    if not os.path.exists(os.path.join(FIXDIR, "SYK.history.csv")):
        pytest.skip("captured enrich fixtures not present")
    out = R.run_pipeline(HC, out_dir=str(tmp_path), fetch=R.offline_fetch(FIXDIR),
                         now=NOW, log=lambda m: None)
    c = out["counts"]
    assert c["kept"] == 27 and c["header"] == 27 and c["parsed"] == 27
    assert out["result"].report["enrich"]["enriched"] >= 20
    assert c["scored"] >= 15
    assert V.all_passed(out["checks"])


def test_currency_without_fx_rate_gets_blank_input_row_not_na(tmp_path):
    from openpyxl import load_workbook
    text = "\n".join(["Results", "2", "2 companies",
        "Company\tLast Price\t7D Return\t1Y Return\tMarket Cap\tAnalysts Target\t"
        "Valuation\tGrowth\tDiv Yield\tIndustry",
        "2286", "ر.س3.75", "-1.6%", "-2.3%", "ر.س2.0b", "ر.س4.60", "9.3", "4.8%", "6.9%", "Food",
        "KINS", "US$18.14", "-0.9%", "22.6%", "US$254.5m", "US$24.00", "7.4", "14.6%", "1.3%",
        "Insurance"])
    src = P.parse_paste(text, "t", "t")
    cfg = B.load_config()
    res = B.run([src], cfg, str(tmp_path / "x.xlsx"), enrich=False)
    wb = load_workbook(res.unfiltered_path)
    asm = wb["Assumptions"]
    rates = {asm.cell(r, 1).value: asm.cell(r, 2).value for r in range(1, asm.max_row + 1)}
    assert "SAR" in rates and rates["SAR"] is None           # empty yellow input
    assert rates["USD"] == cfg["fx_per_eur"]["USD"]
    assert any("No FX rate for: SAR" in str(asm.cell(r, 1).value or "")
               for r in range(1, asm.max_row + 1))
    ws = wb["Screen"]
    hdr = [c.value for c in ws[1]]
    f = ws.cell(2, hdr.index("Market Cap (EUR m, approx)") + 1).value
    assert f.startswith("=IF(") and 'IF(INDEX(' in f and "IFERROR" not in f.upper()
