"""Company names, analyst consensus, headlines and Claude's takes. All offline:
the Claude writer and the headline feed are injected fakes."""

import io
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
import enrich as E           # noqa: E402
import parse_swst as P       # noqa: E402
import run_pipeline as R     # noqa: E402
import score as S            # noqa: E402
import takes as T            # noqa: E402
from fakes import fake_fetch_for  # noqa: E402

CFG = B.load_config()
DIV = os.path.join(HERE, "fixtures", "dividend_table.txt")
GOOD = ("PepsiCo is a large consumer-staples name on this dividend screen. It carries "
        "net debt but positive free cash flow. The forward P/E of 17.2 sits below the "
        "list median, with the mean target 12% above the last price. Recent headlines "
        "point to an analyst downgrade ahead of Q3 results, worth checking.")


def fake_heads(sym, name=None):
    return [{"title": f"{sym} headline one", "date": "2026-09-29",
             "link": f"https://example.com/{sym}/1"},
            {"title": "bad link", "date": None, "link": "javascript:alert(1)"}]


class Writer:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(scope="module")
def scored():
    with open(DIV, encoding="utf-8") as fh:
        src = P.parse_paste(fh.read(), None, "dividend")
    merged, _ = B.merge_sources([src], CFG)
    import tempfile
    out = os.path.join(tempfile.mkdtemp(), "d.xlsx")
    res = B.run([src], CFG, out, region="us_eu", enrich=True,
                fetch=fake_fetch_for(merged), enrich_scope="filtered",
                snapshot_date="2026-09-20")
    return S.score_frame(res.frame, CFG)


# --------------------------------------------------------------- checks
@pytest.mark.parametrize("text,ok", [
    (GOOD, True),
    (GOOD.replace("worth checking", "a clear buy"), False),
    (GOOD + " Investors should wait.", False),
    (GOOD.replace("worth checking", "so hold for now"), False),
    ("Too short.", False),
    ("- " + GOOD, False),
    (GOOD.replace("sits below", "holds below"), True),   # "holds" is not a verdict
])
def test_check_take(text, ok):
    assert T.check_take(text)[0] is ok


def test_write_one_retries_once_then_accepts():
    w = Writer(["This is a strong buy for anyone who likes cash flow and dividends today.", GOOD])
    take, status = T.write_one({"ticker": "PEP"}, w)
    assert take == GOOD and status == "ok" and len(w.prompts) == 2
    assert "rejected (verdict word 'buy')" in w.prompts[1]


def test_write_one_drops_after_two_bad_drafts():
    w = Writer(["Investors should simply buy this one now while it is still cheap enough."])
    take, status = T.write_one({"ticker": "PEP"}, w)
    assert take is None and status.startswith("take rejected")


def test_write_one_reports_auth_error():
    w = Writer([T.TakeError("Claude not authenticated on this server (run claude setup-token)")])
    take, status = T.write_one({"ticker": "PEP"}, w)
    assert take is None and "setup-token" in status


# --------------------------------------------------------------- consensus / facts
def test_consensus_fields_convert_pence_targets():
    info = {"recommendationKey": "buy", "recommendationMean": 1.8,
            "numberOfAnalystOpinions": 12, "targetMeanPrice": 1200.0,
            "targetLowPrice": 900.0, "targetHighPrice": 1500.0}
    f = E.consensus_fields(info, "GBp")
    assert f["tgt_mean"] == 12.0 and f["tgt_low"] == 9.0 and f["n_analysts"] == 12
    assert E.consensus_fields({"recommendationKey": "none"}, "USD")["rec_key"] is None


def test_consensus_line_is_attributed_and_computes_upside():
    row = {"n_analysts": 18, "rec_key": "buy", "rec_mean": 2.1, "tgt_mean": 112.0,
           "tgt_low": 90.0, "tgt_high": 130.0, "yf_price": 100.0}
    line = T.consensus_line(row)
    assert line.startswith("Analysts (Yahoo, 18):")
    assert "consensus 'buy' (2.1 on a 1 to 5 scale" in line and "+12% vs last price" in line
    assert T.consensus_line({}) is None


def test_fact_pack_prunes_blanks_and_keeps_headlines(scored):
    row = scored[scored["score_0_100"].notna()].iloc[0]
    facts = T.fact_pack(row, fake_heads("PEP")[:1])
    blob = json.dumps(facts)
    assert "null" not in blob and "NaN" not in blob
    assert facts["name"].endswith("Holdings (fake)")
    assert facts["headlines"][0]["title"] == "PEP headline one"
    assert "link" not in facts["headlines"][0]          # model gets titles, not URLs
    assert set(facts["dashboard_scores_0_100"]) >= {"composite", "value", "quality"}


# --------------------------------------------------------------- headlines feed
RSS = b"""<?xml version="1.0"?><rss><channel><title>Yahoo! Finance: PEP News</title>
<item><title>PepsiCo downgraded</title><link>https://finance.yahoo.com/a</link>
<pubDate>Tue, 29 Sep 2026 09:43:31 GMT</pubDate></item>
<item><title>Second</title><link>ftp://x</link><pubDate>bad date</pubDate></item>
</channel></rss>"""


def test_fetch_headlines_parses_rss_and_drops_off_topic(monkeypatch):
    feeds = {"feeds.finance.yahoo.com": RSS, "news.google.com": GNEWS}
    def fake_open(req, timeout):
        host = req.full_url.split("/")[2]
        return io.BytesIO(feeds[host])
    monkeypatch.setattr(T.urllib.request, "urlopen", fake_open)
    h = T.fetch_headlines("PEP", "PepsiCo, Inc.")
    titles = [x["title"] for x in h]
    assert titles[0] == "PepsiCo in talks - Reuters"          # newest first, from Google
    assert "PepsiCo downgraded" in titles
    assert "Iran sanctions widen" not in titles               # off-topic dropped
    assert titles.count("PepsiCo downgraded") == 1            # de-duplicated
    assert all(x["link"] is None or x["link"].startswith("https://") for x in h)


@pytest.mark.parametrize("title,name,ticker,ok", [
    ("Smith & Nephew beats estimates", "Smith & Nephew plc", "SN.L", True),
    ("Owens & Minor names new CMO", "Smith & Nephew plc", "SN.L", False),
    ("Why PEP slipped today", "PepsiCo, Inc.", "PEP", True),
    ("Kearny Financial earnings preview", "Kearny Financial Corp.", "KRNY", True),
    ("Kearny Varsity Football @ Clairemont", "Kearny Financial Corp.", "KRNY", False),
    ("Why KRNY slipped", "Kearny Financial Corp.", "KRNY", True),
    ("Financial stocks rally", "Kearny Financial Corp.", "KRNY", False),
    ("Procter & Gamble lifts dividend", "The Procter & Gamble Company", "PG", True),
])
def test_is_relevant(title, name, ticker, ok):
    assert T.is_relevant(title, name, ticker) is ok


GNEWS = b"""<?xml version="1.0"?><rss><channel>
<item><title>PepsiCo in talks - Reuters</title><link>https://news.google.com/x</link>
<pubDate>Thu, 01 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>PepsiCo downgraded</title><link>https://news.google.com/y</link>
<pubDate>Tue, 29 Sep 2026 09:43:31 GMT</pubDate></item>
<item><title>Iran sanctions widen</title><link>https://news.google.com/z</link>
<pubDate>Wed, 30 Sep 2026 09:43:31 GMT</pubDate></item>
</channel></rss>"""


def test_fetch_headlines_network_failure_is_empty(monkeypatch):
    def boom(req, timeout):
        raise OSError("down")
    monkeypatch.setattr(T.urllib.request, "urlopen", boom)
    assert T.fetch_headlines("PEP", "PepsiCo") == []


# --------------------------------------------------------------- batch + cache
def test_add_takes_only_scored_and_cached(scored, tmp_path):
    w = Writer([GOOD])
    out = T.add_takes(scored, writer=w, headline_fetch=fake_heads,
                      cache_dir=str(tmp_path), log=lambda *_: None)
    sc = out[out["score_0_100"].notna()]
    ns = out[out["score_0_100"].isna()]
    assert (sc["take"] == GOOD).all() and len(w.prompts) == len(sc)
    assert set(ns["take_status"]) == {"no take (not scored)"} and ns["take"].isna().all()
    assert sc["name"].str.endswith("Holdings (fake)").all()
    # same facts again -> served from cache, no new Claude calls
    w2 = Writer([GOOD])
    again = T.add_takes(scored, writer=w2, headline_fetch=fake_heads,
                        cache_dir=str(tmp_path), log=lambda *_: None)
    assert len(w2.prompts) == 0
    assert set(again.loc[again["score_0_100"].notna(), "take_status"]) == {"ok (cached)"}


# --------------------------------------------------------------- names
def test_company_name_falls_back_to_yahoo_in_excel_and_page(scored):
    row = scored[scored["yf_name"].notna()].iloc[0]
    assert B._company_display(row.to_dict()) == row["yf_name"]
    assert B._company_display({"ticker": "X", "company": None, "yf_name": None}) \
        == "X (name not in paste)"
    assert B._company_display({"ticker": "X", "company": "Pasted Co",
                               "yf_name": "Yahoo Co"}) == "Pasted Co"


# --------------------------------------------------------------- page
def test_page_shows_take_consensus_and_safe_links(scored, tmp_path):
    out = T.add_takes(scored, writer=Writer([GOOD]), headline_fetch=fake_heads,
                      cache_dir=str(tmp_path), log=lambda *_: None)
    html = D.render_html(out, {"merge": {}, "filter": {}, "enrich": {}}, CFG,
                         {"title": "t", "snapshot_date": "2026-09-20", "today": "2026-09-21"})
    data = json.loads(re.search(r'id="data">(.*?)</script>', html, re.S).group(1)
                      .replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))
    rows = [r for r in data["rows"] if r["score"] is not None]
    assert all(r["take"] == GOOD for r in rows)
    assert all(r["company"] for r in rows)
    assert all(r["consensus"].startswith("Analysts (Yahoo") for r in rows)
    links = [h["u"] for r in rows for h in r["heads"]]
    assert links and all(u is None or u.startswith("https://") for u in links)
    assert "javascript:" not in html
    # URLs exist only as link data, never as something the page loads
    assert "<script src" not in html and "<link" not in html.lower()
    exec_scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert exec_scripts and all("http" not in s for s in exec_scripts)
    assert "Claude&#x27;s takes" in html and '{k:"company",l:"Name"' in html
    # our own deterministic notes still never carry verdict words
    assert not any(re.search(r"\b(buy|sell)\b", r["note"] or "", re.I) for r in data["rows"])


def test_run_pipeline_with_takes_end_to_end(tmp_path):
    with open(DIV, encoding="utf-8") as fh:
        merged, _ = B.merge_sources([P.parse_paste(fh.read(), None, "dividend")], CFG)
    res = R.run_pipeline([DIV], out_dir=str(tmp_path), fetch=fake_fetch_for(merged),
                         snapshot_date="2026-09-20", takes=True, take_writer=Writer([GOOD]),
                         headline_fetch=fake_heads, take_cache_dir=str(tmp_path / "c"),
                         log=lambda *_: None)
    html = open(res["html_path"], encoding="utf-8").read()
    assert GOOD.split(".")[0] in html
    assert res["scored"]["take"].notna().sum() == res["counts"]["scored"]
