#!/usr/bin/env python
"""One command per SWS paste: parse -> merge -> filter US+EU -> enrich -> score
-> write the xlsx and a self-contained HTML dashboard side by side.

  run_pipeline.py <paste-file> [--idea NAME] [--out-dir DIR] [--region us_eu]
                  [--offline-fixtures DIR] [--snapshot-date YYYY-MM-DD] [--no-hub]

Also persists the scored rows to <out-dir>/data/<slug>_<stamp>.rows.json and rebuilds the
hub page <out-dir>/index.html (one tab per list + "All lists"), unless --no-hub.

New, timestamped files only: nothing is ever overwritten (index.html is the one derived
file that is rebuilt on purpose). Screening aid, not
investment advice.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build_hub as H        # noqa: E402
import build_workbook as B   # noqa: E402
import dashboard as D        # noqa: E402
import enrich as E           # noqa: E402
import parse_swst as P       # noqa: E402
import score as S            # noqa: E402
import verify as V           # noqa: E402


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "screen"


def offline_fetch(dirpath: str):
    """Fetch backed by saved snapshots (Snapshot.dump format); missing -> not ok."""
    import pandas as pd

    def fetch(symbol: str):
        try:
            return E.load_snapshot(dirpath, symbol)
        except (FileNotFoundError, OSError):
            return E.Snapshot(symbol=symbol,
                              history=pd.DataFrame(columns=["close", "volume"]),
                              info={}, ok=False,
                              calendar={"earnings": None, "ex_div": None})
    return fetch


def run_pipeline(paste_paths, idea=None, out_dir=".", region="us_eu", fetch=None,
                 snapshot_date=None, now=None, cfg=None, log=print,
                 takes=False, take_writer=None, headline_fetch=None,
                 take_model=None, take_cache_dir=None, hub=False) -> dict:
    """takes=False and hub=False keep library/tests offline and simple. The CLI turns
    takes and the hub on by default."""
    cfg = cfg or B.load_config()
    now = now or _dt.datetime.now()
    snapshot_date = snapshot_date or now.date().isoformat()
    if isinstance(paste_paths, str):
        paste_paths = [paste_paths]

    sources = []
    for p in paste_paths:
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        stem = os.path.splitext(os.path.basename(p))[0]
        sources.append(P.parse_paste(text, idea, stem))
    title = idea or sources[0].title or sources[0].investing_idea or sources[0].source
    slug = slugify(title)
    stamp = now.strftime("%Y%m%d-%H%M")

    os.makedirs(out_dir, exist_ok=True)
    xlsx = os.path.join(out_dir, f"{slug}_{stamp}.xlsx")
    html_path = os.path.join(out_dir, f"{slug}_{stamp}.html")
    codes = B.resolve_region(region, cfg)
    rows_path = H.rows_json_path(out_dir, slug, stamp)
    planned = [xlsx, html_path, rows_path]
    if codes is not None:
        base, ext = os.path.splitext(xlsx)
        planned.append(f"{base}__filtered_{'-'.join(sorted(set(codes)))[:40]}{ext}")
    for path in planned:
        if os.path.exists(path):
            raise FileExistsError(f"refusing to overwrite {path}")

    res = B.run(sources, cfg, xlsx, region=region, snapshot_date=snapshot_date,
                enrich=True, fetch=fetch, enrich_scope="filtered")
    scored = S.score_frame(res.frame, cfg)
    if takes:
        import takes as T
        kw = {"model": take_model or T.DEFAULT_MODEL, "log": log}
        if take_writer is not None:
            kw["writer"] = take_writer
        if headline_fetch is not None:
            kw["headline_fetch"] = headline_fetch
        if take_cache_dir is not None:
            kw["cache_dir"] = take_cache_dir
        scored = T.add_takes(scored, **kw)

    checks = V.verify_build(res.filtered_path or res.unfiltered_path, res.frame, cfg)
    meta = {"title": title, "snapshot_date": snapshot_date,
            "today": now.date().isoformat(),
            "region_label": "US+EU" if region == "us_eu" else str(region),
            "take_model": take_model or "sonnet"}
    page = D.render_html(scored, res.report, cfg, meta)
    with open(html_path, "x", encoding="utf-8") as fh:   # "x": never overwrite
        fh.write(page)

    H.write_rows_json(out_dir, slug, stamp, title, snapshot_date,
                      now.isoformat(timespec="seconds"), region, res.report, scored,
                      take_model=meta["take_model"])
    hub_path = None
    if hub:
        hub_path = H.build_hub(out_dir, cfg, today=now.date())

    banner = D.banner_line(scored, res.report, meta)
    log(banner)
    rep_enrich = res.report.get("enrich", {})
    log(f"Enrichment: {rep_enrich}")
    log("Workbook checks: " + ("all passed" if V.all_passed(checks) else
        "FAILED: " + "; ".join(m for ok, m in checks if not ok)))
    log(f"XLSX (unfiltered): {res.unfiltered_path}")
    if res.filtered_path:
        log(f"XLSX (filtered):   {res.filtered_path}")
    log(f"HTML:              {html_path}")
    log(f"Rows:              {rows_path}")
    if hub_path:
        log(f"Hub:               {hub_path}")
    return {"result": res, "scored": scored, "html_path": html_path,
            "xlsx_path": res.unfiltered_path, "filtered_xlsx_path": res.filtered_path,
            "rows_path": rows_path, "hub_path": hub_path, "banner": banner, "counts": D.banner_counts(scored, res.report),
            "checks": checks, "html_bytes": len(page.encode("utf-8"))}


def main(argv=None, fetch=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paste", nargs="+", help="path(s) to pasted SWS page text")
    ap.add_argument("--idea", default=None, help="Investing Idea label override")
    # Default to the folder served at http://the-damiverse:3090 (swst-reports.service),
    # so every run shows up at /latest without a copy step.
    ap.add_argument("--out-dir", default=os.path.expanduser("~/swst_reports"))
    ap.add_argument("--region", default="us_eu")
    ap.add_argument("--offline-fixtures", default=None,
                    help="directory of saved snapshots; no network")
    ap.add_argument("--snapshot-date", default=None)
    ap.add_argument("--no-takes", action="store_true",
                    help="skip Claude's per-stock takes (no headlines, no Claude calls)")
    ap.add_argument("--no-hub", action="store_true",
                    help="do not rebuild index.html (the multi-list hub) after this run")
    ap.add_argument("--take-model", default="sonnet",
                    help="Claude model alias for takes (default: sonnet)")
    a = ap.parse_args(argv)
    if a.offline_fixtures:
        fetch = offline_fetch(a.offline_fixtures)
    run_pipeline(a.paste, idea=a.idea, out_dir=a.out_dir, region=a.region,
                 fetch=fetch, snapshot_date=a.snapshot_date,
                 takes=not a.no_takes, take_model=a.take_model,
                 hub=not a.no_hub)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
