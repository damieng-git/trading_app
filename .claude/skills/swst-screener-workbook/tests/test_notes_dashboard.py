"""Row notes and the static HTML dashboard (structure + embedded data)."""

import datetime as _dt
import json
import os
import re
import sys

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, HERE)

import build_workbook as B   # noqa: E402
import dashboard as D        # noqa: E402
import notes as N            # noqa: E402
import parse_swst as P       # noqa: E402
import score as S            # noqa: E402
from fakes import fake_fetch_for  # noqa: E402

CFG = B.load_config()
DIV = os.path.join(HERE, "fixtures", "dividend_table.txt")
BANNED = re.compile(r"buy|sell|strong|opportunity|should|undervalued|cheap", re.I)
STATS = {"median_pe_forward": 14.0, "n_pe_forward": 20, "median_pe_trailing": 18.0,
         "n_pe_trailing": 22, "snapshot_date": _dt.date(2026, 9, 20)}


# ---------------------------------------------------------------- notes
def test_note_non_financial_full():
    row = {"pe_forward": 12.34, "net_cash_eur_m": 300.0, "_eur_cap": 1000.0,
           "rsi_14": 27.4, "pct_from_high": -0.153,
           "next_earnings": _dt.datetime(2026, 9, 29), "composite": 0.6}
    assert N.row_note(row, STATS) == (
        "Fwd P/E 12.3 (list median 14.0, n=20). Net cash 30% of cap. RSI 27. "
        "15% below 52wk high. Earnings 2026-09-29 (9d after snapshot).")


def test_note_financial_and_loss_maker_and_caps():
    fin = {"is_fin": True, "pe_forward": None, "pe_trailing": 9.0, "pb_ratio": 1.234,
           "rsi_14": 55.0, "composite": 0.5}
    assert N.row_note(fin, STATS) == (
        "Trailing P/E 9.0 (list median 18.0, n=22). P/B 1.23. RSI 55. "
        "Financial: EV/EBITDA, P/FCF, net cash not used.")
    loss = {"pe_forward": None, "pe_trailing": None, "net_cash_eur_m": -100.0,
            "_eur_cap": 400.0, "runway_quarters": 5.2, "rsi_14": 41.0,
            "pct_from_high": -0.40, "next_earnings": _dt.datetime(2026, 12, 1),
            "composite": 0.4}
    assert N.row_note(loss, STATS) == (
        "Net debt 25% of cap. Loss-making: runway 5 qtrs. RSI 41. 40% below 52wk high.")
    reit = {"industry": "Real Estate", "pe_forward": 30.0, "composite": 0.5}
    assert N.row_note(reit, STATS).endswith("REIT/property: P/E not meaningful.")


def test_note_not_scored_keeps_reason_and_max_five_fragments():
    row = {"is_fin": False, "pe_forward": 12.0, "net_cash_eur_m": 100.0, "_eur_cap": 500.0,
           "runway_quarters": 3.0, "rsi_14": 30.0, "pct_from_high": -0.2,
           "next_earnings": _dt.datetime(2026, 9, 25), "composite": None,
           "not_scored_reason": "missing: timing"}
    note = N.row_note(row, STATS)
    frags = [f for f in re.split(r"(?<=\.) (?=[A-Z0-9])", note)]
    assert len(frags) == 5
    assert note.endswith("Not scored: missing: timing.")
    assert N.row_note({"composite": None, "not_scored_reason": "missing: value"},
                      STATS) == "Not scored: missing: value."
    assert N.row_note({"composite": 0.5}, STATS) == ""


def test_list_stats_median():
    df = pd.DataFrame({"pe_forward": [10.0, None, 20.0, float("nan"), 30.0],
                       "pe_trailing": [None] * 5})
    s = N.list_stats(df, "2026-09-20")
    assert s["median_pe_forward"] == 20.0 and s["n_pe_forward"] == 3
    assert s["median_pe_trailing"] is None and s["n_pe_trailing"] == 0
    assert s["snapshot_date"] == _dt.date(2026, 9, 20)


# ---------------------------------------------------------------- dashboard
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    with open(DIV, encoding="utf-8") as fh:
        src = P.parse_paste(fh.read(), None, "dividend")
    merged, _ = B.merge_sources([src], CFG)
    out = tmp_path_factory.mktemp("dash") / "d.xlsx"
    res = B.run([src], CFG, str(out), region="us_eu", enrich=True,
                fetch=fake_fetch_for(merged), enrich_scope="filtered",
                snapshot_date="2026-09-20")
    scored = S.score_frame(res.frame, CFG)
    meta = {"title": "Growing Dividend Payers", "snapshot_date": "2026-09-20",
            "today": "2026-09-21"}
    return scored, res.report, D.render_html(scored, res.report, CFG, meta)


def _scripts(html):
    return re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S)


def test_html_is_self_contained(built):
    _, _, html = built
    assert "<script src" not in html and "<link" not in html.lower()
    assert "<script" in html and "<img" not in html
    for body in _scripts(html):
        assert "http" not in body
    assert "http://" not in html and "https://" not in html
    assert "innerHTML" not in html


def test_embedded_json_valid_no_nan_and_row_count(built):
    scored, _, html = built
    raw = re.search(r'<script type="application/json" id="data">(.*?)</script>',
                    html, re.S).group(1)
    for bad in ("NaN", "Infinity", "<", ">"):
        assert bad not in raw
    data = json.loads(raw)
    assert len(data["rows"]) == len(scored) == 32
    assert data["rows"][0].keys() >= {"ticker", "tier", "score", "v", "q", "t", "r",
                                      "region", "note", "flags"}
    assert {r["region"] for r in data["rows"]} == {"US", "EU"}
    priced = [r for r in data["rows"] if r["price"] is not None]
    assert priced and all(isinstance(r["price"], (int, float)) and r["ccy"] for r in priced)
    # Country and Price are shown by default in the scored table (core columns)
    assert '{k:"country",l:"Country",core:1' in html and '{k:"price",l:"Price",core:1,num:1' in html
    assert '{k:"ps",l:"P/S",core:1' in html and '{k:"evf",l:"EV/FCF",core:1' in html and '{k:"evs",l:"EV/Sales"' in html
    assert "function vsPeer" in html and "function peerTip" in html
    assert all(r["region"] == "EU" for r in data["rows"] if r["country"] in ("eu", "gb", "se"))


def test_banner_footer_and_wording(built):
    scored, report, html = built
    text = re.sub(r"<[^>]+>", " ", html)
    assert re.search(r"Growing Dividend Payers - snapshot 2026-09-20\. Screen page claims "
                     r"190; 82 parsed; 32 kept by US\+EU filter; 27 enriched; 27 scored; "
                     r"5 not scored\.", text)
    assert "hand-set, not fitted or backtested" in text
    assert "Scores measure closeness to fixed anchors, not future returns." in text
    assert "Screening aid, not investment advice. A high score is not a buy signal; " \
           "review each name." in text
    assert "ECB reference rates" in text
    assert "page claims 190, 82 rows in the paste (108 missing)" in text
    assert "5 EUR-quoted row(s) kept with unknown exchange" in text
    assert "large: 0.35 / 0.35 / 0.10 / 0.20" in text
    assert "—" not in html          # no em dashes
    # no buy/sell language in our own wording: only the negated disclaimer and the
    # attributed third-party quote ("Analysts (Yahoo, n): consensus 'buy' ...") may carry it
    stripped = text.replace("A high score is not a buy signal", "")
    stripped = re.sub(r"Analysts \(Yahoo[^\"]*", "", stripped)
    assert not re.search(r"\b(buy|sell)\b", stripped, re.I)
    assert "stale" not in html.lower() and "days old" not in html


def test_stale_warning(built):
    scored, report, _ = built
    html = D.render_html(scored, report, CFG, {"title": "T", "snapshot_date": "2026-09-10",
                                               "today": "2026-09-20"})
    assert "Data snapshot is 10 days old (limit 3)" in html


def test_region_colours_and_chart_markup(built):
    _, _, html = built
    assert "--us:#84A98C" in html and "--eu:#2F3E46" in html     # muted teal / charcoal (palette)
    assert 'class="rail"' in html and 'id="detail"' in html         # C1-A: list rail + selected-stock panel
    assert "circle.US{fill:var(--us)" in html and "circle.EU{fill:var(--eu)" in html
    assert html.count('viewBox="0 0 720 480"') == 2                 # two charts side by side
    assert 'id="chart-q"' in html and 'id="chart-t"' in html
    assert "<h3>Value vs Quality</h3>" in html and "<h3>Value vs Timing</h3>" in html
    assert 'x:"v",y:"q"' in html and 'x:"v",y:"t"' in html          # Value on X for both
    assert "Math.log10(Math.max(r.cap_m,100)/100)" in html
    assert "Not plotted (missing scores)" in html
    assert 'id="xsel"' not in html                                   # fixed charts, no axis pickers
    assert "--sel:#b8860b" in html                                   # dark yellow selection
    assert "tr.hl td{background:#FBF6E6;box-shadow:" in html and "outline:2px" not in html


def test_hostile_industry_is_escaped(built):
    scored, report, _ = built
    evil = "</script><img src=x onerror=1>"
    df = scored.copy()
    df["industry"] = df["industry"].astype(object)
    df.loc[df.index[0], "industry"] = evil
    df.loc[df.index[0], "company"] = evil
    html = D.render_html(df, report, CFG, {"title": evil, "snapshot_date": "2026-09-20",
                                           "today": "2026-09-20"})
    assert evil not in html
    assert "<img" not in html
    assert len(_scripts(html)) == 2
    raw = re.search(r'id="data">(.*?)</script>', html, re.S).group(1)
    assert json.loads(raw)["rows"][0]["industry"] == evil     # round-trips intact


def test_sanitize_handles_nan_nat_dates_and_numpy():
    import numpy as np
    out = D.sanitize({"a": float("nan"), "b": pd.NaT, "c": pd.Timestamp("2026-01-02"),
                      "d": np.float64("inf"), "e": np.int64(3), "f": np.bool_(True),
                      "g": _dt.date(2026, 3, 4), "h": [float("nan"), 1.5]})
    assert out == {"a": None, "b": None, "c": "2026-01-02", "d": None, "e": 3, "f": True,
                   "g": "2026-03-04", "h": [None, 1.5]}
    json.dumps(out, allow_nan=False)


def test_size_under_300kb(built):
    _, _, html = built
    assert len(html.encode("utf-8")) < 300_000


def test_every_note_is_free_of_banned_words(built):
    scored, report, html = built
    stats = N.list_stats(scored, "2026-09-20")
    notes = [N.row_note(r.to_dict(), stats) for _, r in scored.iterrows()]
    assert sum(bool(n) for n in notes) >= 27
    assert not [n for n in notes if BANNED.search(n)]


# ------------------------------------------------ row click -> bubble highlight
def test_clicking_a_table_row_highlights_its_bubble(built, tmp_path):
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    df, _, html = built
    scored = df[df["score_0_100"].notna()]
    ticker = str(scored.iloc[0]["ticker"])
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")
    proc = subprocess.run(
        ["node", os.path.join(HERE, "dom_stub.js"), str(page), ticker],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ringBefore"] == [0, 0]               # nothing selected at load
    for ch in out["charts"]:                         # same bubble ringed on BOTH charts
        assert ch["ring"] == 1 and ch["label"] == [ticker]
        assert ch["selDots"] == 1 and ch["selIsTopmostDot"]
    assert out["rowHighlighted"]                     # row stays highlighted after redraw
    assert out["chartScrolledIntoView"]              # charts were off-screen, so we scroll up
    assert out["ringAfterSecondClick"] == [0, 0]     # second click clears it
    assert out["afterBubbleClick"] == {"rings": [1, 1], "rowHighlighted": True}  # bubble click: both charts + row
    assert ticker in out["detailAfterClick"] and "Value" in out["detailAfterClick"]   # panel shows the stock
    assert out["detailAfterClear"].startswith("Click a bubble")                       # and empties on clear
