"""Multi-list hub: rows.json persistence, unification, per-tab context, page, server."""

import copy
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from functools import partial

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, HERE)

import build_hub as H            # noqa: E402
import build_workbook as B       # noqa: E402
import dashboard as D            # noqa: E402
import parse_swst as P           # noqa: E402
import run_pipeline as R         # noqa: E402
import serve_reports as SR       # noqa: E402
from fakes import fake_fetch_for  # noqa: E402

CFG = B.load_config()
DIV = os.path.join(HERE, "fixtures", "dividend_table.txt")
NOW = _dt.datetime(2026, 9, 20, 10, 0)
TODAY = _dt.date(2026, 9, 21)
BANNED = re.compile(r"buy|sell|strong|opportunity|should|undervalued|cheap", re.I)


def _fetch(path):
    with open(path, encoding="utf-8") as fh:
        src = P.parse_paste(fh.read(), None, "x")
    merged, _ = B.merge_sources([src], CFG)
    return fake_fetch_for(merged)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """One real (fake-fetch) pipeline run: its rows.json is the template for the others."""
    d = tmp_path_factory.mktemp("base")
    out = R.run_pipeline(DIV, out_dir=str(d), fetch=_fetch(DIV), now=NOW,
                         log=lambda m: None, idea="List A")
    return out, out["rows_path"]


def _clone(src_path, dst_dir, slug, title, snap, gen, keep=None, mutate=None, extra=0):
    with open(src_path, encoding="utf-8") as fh:
        d = json.load(fh)
    d["slug"], d["title"], d["snapshot_date"], d["generated_at"] = slug, title, snap, gen
    rows = d["rows"] if keep is None else [r for r in d["rows"] if keep(r)]
    rows = copy.deepcopy(rows)
    if mutate:
        for r in rows:
            mutate(r)
    for i in range(extra):
        r = copy.deepcopy(d["rows"][i])
        r["ticker"] = f"NEW{i}"
        r["_yf_symbol"] = None   # a different company: must not merge by the cloned symbol
        rows.append(r)
    d["rows"] = rows
    os.makedirs(os.path.join(dst_dir, "data"), exist_ok=True)
    path = os.path.join(dst_dir, "data", f"{slug}_{gen[:10].replace('-', '')}-0000.rows.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return path


@pytest.fixture()
def two_lists(base, tmp_path):
    """List A (all 32 rows, 2026-09-20) and List B (first 10 of A re-scored + 2 new, 09-25)."""
    _, src = base
    keys = []

    def keep_first10(r):
        if len(keys) < 10:
            keys.append(r["ticker"] + "|" + r["currency"])
            return True
        return False

    def bump(r):
        r["score_0_100"], r["composite"] = 99, 0.99

    a = _clone(src, tmp_path, "list-a", "List A", "2026-09-20", "2026-09-20T10:00:00")
    b = _clone(src, tmp_path, "list-b", "List B", "2026-09-25", "2026-09-25T10:00:00",
               keep=keep_first10, mutate=bump, extra=2)
    return str(tmp_path), a, b, keys


# ------------------------------------------------------------ persistence
def test_rows_json_round_trip(base):
    out, path = base
    d = H.load_rows_json(path)
    assert d["schema"] == 1 and d["slug"] == "list-a" and d["title"] == "List A"
    assert d["snapshot_date"] == "2026-09-20" and d["region"] == "us_eu"
    assert d["generated_at"] == "2026-09-20T10:00:00"
    assert set(d["report"]) == {"merge", "filter", "enrich"}
    got, want = d["frame"], out["scored"]
    assert len(got) == len(want) == 32 and set(got.columns) == set(want.columns)
    for col in ("score_0_100", "composite", "score_value", "ticker", "tier", "gate"):
        for a, b in zip(got[col], want[col]):
            assert (a is None and (b is None or pd.isna(b))) or a == b, col
    ne = [v for v in got["next_earnings"] if v is not None]
    assert ne and all(isinstance(v, _dt.datetime) for v in ne)
    # the loaded frame feeds build_records and the same page code
    recs = D.build_records(got, H.N.list_stats(got, d["snapshot_date"]))
    assert len(recs) == 32 and sum(r["score"] is not None for r in recs) == 27


def test_rows_json_is_not_overwritten_and_headlines_survive(tmp_path):
    df = pd.DataFrame({"ticker": ["A"], "currency": ["USD"],
                       "headlines": [[{"title": "t", "date": "2026-01-02", "link": "https://x/y"}]],
                       "next_earnings": [pd.Timestamp("2026-10-01")], "composite": [float("nan")]})
    p = H.write_rows_json(str(tmp_path), "s", "20260101-0000", "S", "2026-01-01",
                          "2026-01-01T00:00:00", "us_eu", {}, df)
    with pytest.raises(FileExistsError):
        H.write_rows_json(str(tmp_path), "s", "20260101-0000", "S", "2026-01-01",
                          "2026-01-01T00:00:00", "us_eu", {}, df)
    d = H.load_rows_json(p)
    assert d["frame"].iloc[0]["headlines"] == [{"title": "t", "date": "2026-01-02",
                                                "link": "https://x/y"}]
    assert d["frame"].iloc[0]["composite"] is None
    assert d["frame"].iloc[0]["next_earnings"] == _dt.datetime(2026, 10, 1)


# ------------------------------------------------------------ selection
def test_latest_per_slug_and_exclude(base, tmp_path):
    _, src = base
    _clone(src, tmp_path, "list-a", "List A", "2026-09-10", "2026-09-10T10:00:00")
    _clone(src, tmp_path, "list-a", "List A new", "2026-09-20", "2026-09-20T10:00:00")
    _clone(src, tmp_path, "list-b", "List B", "2026-09-15", "2026-09-15T10:00:00", extra=0)
    got = {d["slug"]: d for d in H.latest_per_slug(str(tmp_path))}
    assert set(got) == {"list-a", "list-b"}
    assert got["list-a"]["title"] == "List A new"
    assert set(d["slug"] for d in H.latest_per_slug(str(tmp_path), {"list-b"})) == {"list-a"}
    with open(os.path.join(tmp_path, "data", "junk.rows.json"), "w") as fh:
        fh.write("{not json")
    assert len(H.latest_per_slug(str(tmp_path))) == 2          # unreadable file skipped
    page = open(H.build_hub(str(tmp_path), CFG, today=TODAY, exclude=("list-b",)),
                encoding="utf-8").read()
    data = json.loads(re.search(r'id="data">(.*?)</script>', page, re.S).group(1))
    assert [v["id"] for v in data["views"]] == ["all", "list-a"]


# ------------------------------------------------------------ unification
def test_unification_membership_and_newest_row_wins(two_lists):
    rdir, a, b, keys = two_lists
    views = H.build_views(H.latest_per_slug(rdir), CFG, TODAY)
    byid = {v["id"]: v for v in views}
    assert [v["id"] for v in views] == ["all", "list-b", "list-a"]    # newest snapshot first
    allrecs, arecs, brecs = (byid[k]["records"] for k in ("all", "list-a", "list-b"))
    assert len(arecs) == 32 and len(brecs) == 12 and len(allrecs) == 34

    def find(recs, ticker):
        return [r for r in recs if r["ticker"] == ticker][0]

    shared_tickers = [k.split("|")[0] for k in keys]
    for t in shared_tickers:
        ra, rb, rall = find(arecs, t), find(brecs, t), find(allrecs, t)
        assert ra["score"] == rb["score"] == rall["score"] == 99       # B is newer
        assert rall["n_lists"] == ra["n_lists"] == rb["n_lists"] == 2
        assert rall["lists"] == ["List A", "List B"]
        assert ra["lists"] == ["List B"] and rb["lists"] == ["List A"]
        assert ra["data_from_other_list"] is True and rb["data_from_other_list"] is False
        assert ra["data_date"] == rb["data_date"] == "2026-09-25"
    # peer fields are on every record, and one stock shows the same peer comparison on every tab
    for t in shared_tickers:
        ra, rall = find(arecs, t), find(allrecs, t)
        assert "peer_group" in rall and "pe_vs" in rall and "evf" in rall
        assert (ra["peer_group"], ra["pe_vs"], ra["ps_vs"]) == (rall["peer_group"], rall["pe_vs"], rall["ps_vs"])
    only_a = [r for r in arecs if r["ticker"] not in shared_tickers]
    assert only_a and all(not r["data_from_other_list"] and r["n_lists"] == 1
                          and r["lists"] == [] and r["data_date"] == "2026-09-20"
                          for r in only_a)
    new = find(brecs, "NEW0")
    assert new["n_lists"] == 1 and new["lists"] == [] and not new["data_from_other_list"]
    assert sum(1 for r in allrecs if r["n_lists"] == 2) == 10
    assert byid["all"]["banner"] == ("All lists - 2 lists, 34 unique stocks (10 on 2+ lists); "
                                     "data dates 2026-09-20 to 2026-09-25.")
    assert byid["list-a"]["banner"].startswith(
        "List A - snapshot 2026-09-20. Screen page claims 190; 82 parsed; 32 kept by US+EU")
    assert byid["list-a"]["stale"] is False and byid["all"]["stale"] is False  # 1 day old


def test_stale_flag_follows_oldest_data_date(two_lists):
    rdir, *_ = two_lists
    views = {v["id"]: v for v in
             H.build_views(H.latest_per_slug(rdir), CFG, _dt.date(2026, 9, 26))}
    # List B's rows are all from 09-25 (1 day); List A has rows from 09-20 (6 days > 3)
    assert views["list-b"]["stale"] is False
    assert views["list-a"]["stale"] is True and views["all"]["stale"] is True
    assert "6 days old (limit 3)" in views["list-a"]["warn_text"]


def test_per_tab_ranks_and_note_scope(two_lists):
    rdir, *_ = two_lists
    byid = {v["id"]: v["records"] for v in H.build_views(H.latest_per_slug(rdir), CFG, TODAY)}
    for name, recs in byid.items():
        scored = [r for r in recs if r["score"] is not None]
        assert sorted(r["rank_list"] for r in scored)[0] == 1
        best = max(r["score"] for r in scored)       # 99s share rank 1 (ties min)
        assert all(r["rank_list"] == 1 for r in scored if r["score"] == best)
        assert max(r["rank_list"] for r in scored) <= len(scored)
        assert all(r["rank_list"] is None for r in recs if r["score"] is None)
    # ranks depend on the row set: List A rank of a non-shared stock differs from All
    rank = lambda recs, t: [r for r in recs if r["ticker"] == t][0]["rank_list"]
    only_a = [r["ticker"] for r in byid["list-a"] if r["n_lists"] == 1 and r["score"] is not None]
    assert any(rank(byid["list-a"], t) != rank(byid["all"], t) for t in only_a)
    notes_all = [r["note"] for r in byid["all"] if "P/E" in r["note"]]
    notes_a = [r["note"] for r in byid["list-a"] if "P/E" in r["note"]]
    assert notes_all and all("all-lists median" in n and "list median" not in
                             n.replace("all-lists median", "") for n in notes_all)
    assert notes_a and all("(list median" in n and "all-lists" not in n for n in notes_a)
    assert not [r["note"] for rs in byid.values() for r in rs if BANNED.search(r["note"])]


# ------------------------------------------------------------ page
def _hub_page(two_lists, today=TODAY):
    rdir, *_ = two_lists
    return open(H.build_hub(rdir, CFG, today=today), encoding="utf-8").read(), rdir


def test_hub_page_structure_and_self_contained(two_lists):
    page, rdir = _hub_page(two_lists)
    assert os.path.exists(os.path.join(rdir, "index.html"))
    assert not os.path.exists(os.path.join(rdir, "index.html.tmp"))
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", page, re.S)
    assert len(scripts) == 2
    raw = re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1)
    for bad in ("NaN", "Infinity", "<", ">"):
        assert bad not in raw
    data = json.loads(raw)
    assert [v["id"] for v in data["views"]] == ["all", "list-b", "list-a"]
    assert [len(v["records"]) for v in data["views"]] == [34, 12, 32]
    assert data["views"][0]["records"][0].keys() >= {"lists", "n_lists", "data_date",
                                                     "data_from_other_list"}
    assert 'id="tabs"' in page
    assert "<script src" not in page and "<link" not in page.lower() and "<img" not in page
    for body in scripts:
        assert "http" not in body
    assert "http://" not in page and "https://" not in page
    assert "innerHTML" not in page
    assert "\u2014" not in page
    text = re.sub(r"<[^>]+>", " ", page)
    assert "All lists - 2 lists, 34 unique stocks (10 on 2+ lists)" in text
    stripped = text.replace("A high score is not a buy signal", "")
    stripped = re.sub(r"Analysts \(Yahoo[^\"]*", "", stripped)
    assert not re.search(r"\b(buy|sell)\b", stripped, re.I)
    assert len(page.encode("utf-8")) < 1_000_000


def test_hub_hostile_title_is_escaped(base, tmp_path):
    _, src = base
    evil = "</script><img src=x onerror=1>"
    _clone(src, tmp_path, "evil", evil, "2026-09-20", "2026-09-20T10:00:00",
           mutate=lambda r: r.update(industry=evil, company=evil))
    _clone(src, tmp_path, "ok", "OK", "2026-09-20", "2026-09-20T11:00:00", extra=1)
    page = open(H.build_hub(str(tmp_path), CFG, today=TODAY), encoding="utf-8").read()
    assert evil not in page and "<img" not in page
    assert len(re.findall(r"<script\b[^>]*>(.*?)</script>", page, re.S)) == 2
    raw = re.search(r'id="data">(.*?)</script>', page, re.S).group(1)
    data = json.loads(raw)
    assert evil in [v["label"] for v in data["views"]]
    assert evil in data["views"][0]["records"][0]["lists"] or \
        any(evil in r["lists"] for r in data["views"][0]["records"])


def test_render_hub_html_single_view_has_no_tab_bar_and_validates():
    views = [{"id": "all", "label": "All lists", "records": [], "banner": "b",
              "footer_paras": [("H", "t")], "stale": False}]
    page = D.render_hub_html(views, CFG, {"title": "T"})
    assert 'id="tabs"' not in page
    with pytest.raises(ValueError):
        D.render_hub_html([], CFG, {})


def test_hub_node_dom_stub_tabs(two_lists, tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    page, _ = _hub_page(two_lists)
    f = tmp_path / "hub.html"
    f.write_text(page, encoding="utf-8")
    proc = subprocess.run(["node", os.path.join(HERE, "dom_stub_hub.js"), str(f)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    o = json.loads(proc.stdout.strip().splitlines()[-1])
    assert o["nTabs"] == 3
    assert o["tabLabels"] == ["All lists (34)", "List B (12)", "List A (32)"]
    raw = re.search(r'id="data">(.*?)</script>', page, re.S).group(1)
    want = sum(r["score"] is not None for r in json.loads(raw)["views"][0]["records"])
    assert o["initialHash"] == "#all" and o["initialRows"] == want   # scored rows in All
    assert o["initialBanner"].startswith("All lists - 2 lists, 34 unique stocks")
    assert o["ringOnAll"] == 2                            # selection worked on All (ring on both charts)
    s = o["switched"]
    assert s["rows"] == s["wantRows"] and s["countryBoxes"] == s["wantCountries"]
    assert s["ring"] == 0                                 # selection cleared by the switch
    assert s["hash"] == s["wantHash"] == "#list-b"
    assert s["banner"] == s["wantBanner"]
    assert s["onClass"] == ["", "on", ""]
    assert o["inTab"] == {"ring": 2, "hl": True}          # row click -> bubble, in a non-default tab
    assert o["back"]["rows"] == o["back"]["wantRows"] and o["back"]["ring"] == 0
    assert o["back"]["hash"] == "#all"
    # tab counts follow the filters, and the filter survives a tab switch
    f = o["filtered"]
    before = [int(re.search(r"\((\d+)\)$", t).group(1)) for t in o["tabLabels"]]
    after = [int(re.search(r"\((\d+)\)$", t).group(1)) for t in f["tabLabels"]]
    assert all(a <= b for a, b in zip(after, before)) and sum(after) < sum(before)
    assert f["button"].startswith("Tier: ") and "of" in f["button"]
    assert f["afterSwitch"]["tabLabels"] == f["tabLabels"] and f["afterSwitch"]["button"] == f["button"]


def test_hub_opens_on_the_tab_named_in_the_hash(two_lists, tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    page, _ = _hub_page(two_lists)
    f = tmp_path / "hub.html"
    f.write_text(page, encoding="utf-8")
    proc = subprocess.run(["node", os.path.join(HERE, "dom_stub_hub.js"), str(f), "#list-a"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    o = json.loads(proc.stdout.strip().splitlines()[-1])
    assert o["initialHash"] == "#list-a" and o["initialBanner"].startswith("List A - snapshot")


# ------------------------------------------------------------ pipeline + server
def test_run_pipeline_hub_true_writes_rows_and_index(tmp_path):
    logs = []
    out = R.run_pipeline(DIV, out_dir=str(tmp_path), fetch=_fetch(DIV), now=NOW,
                         log=logs.append, hub=True)
    assert os.path.exists(out["rows_path"]) and out["rows_path"].endswith(".rows.json")
    assert os.path.dirname(out["rows_path"]) == str(tmp_path / "data")
    idx = tmp_path / "index.html"
    assert idx.exists() and out["hub_path"] == str(idx)
    assert logs[0] == out["banner"]
    page = idx.read_text(encoding="utf-8")
    data = json.loads(re.search(r'id="data">(.*?)</script>', page, re.S).group(1))
    assert len(data["views"]) == 2 and len(data["views"][1]["records"]) == 32
    # default (library) behaviour: rows.json yes, no hub page
    d2 = tmp_path / "second"
    R.run_pipeline(DIV, out_dir=str(d2), fetch=_fetch(DIV), now=NOW, log=lambda m: None)
    assert (d2 / "data").is_dir() and not (d2 / "index.html").exists()


@pytest.fixture()
def server(tmp_path):
    (tmp_path / "a_20260101-0000.html").write_text("<p>single</p>")
    (tmp_path / "a_20260101-0000.xlsx").write_bytes(b"x")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.rows.json").write_text("{}")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SR.Handler, directory=str(tmp_path)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield tmp_path, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _get(url):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(url, timeout=10)
        return r.status, r.read().decode(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers


def test_serve_root_is_listing_without_hub_then_hub_with_index(server):
    root, base_url = server
    st, body, _ = _get(base_url + "/")
    assert st == 200 and "SWS screener reports" in body and "a_20260101-0000.html" in body
    (root / "index.html").write_text("<p>THE HUB</p>")
    st, body, _ = _get(base_url + "/")
    assert st == 200 and "THE HUB" in body
    st, body, _ = _get(base_url + "/files")
    assert st == 200 and "SWS screener reports" in body
    assert "a_20260101-0000.html" in body and "a_20260101-0000.xlsx" in body
    assert "index.html" not in body and "rows.json" not in body
    st, _, hdr = _get(base_url + "/latest")
    assert st == 302 and hdr["Location"] == "/a_20260101-0000.html"
