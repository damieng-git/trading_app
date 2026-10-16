#!/usr/bin/env python
"""Multi-list hub: one dashboard page with a tab per list plus an "All lists" tab.

  build_hub.py [--reports-dir DIR] [--exclude SLUG ...]

Every run_pipeline run persists its scored rows to
<reports_dir>/data/<slug>_<YYYYMMDD-HHMM>.rows.json (write_rows_json). build_hub reads
those files, keeps the latest run per list slug, and writes <reports_dir>/index.html
(atomically; this file is a derived view and is overwritten on purpose).

Rules (see references/design.md):
- a stock is keyed by its resolved yfinance symbol, else ticker|currency (a reused ticker
  with a very different price/cap is a different company, key ...#2; see identity.py); when it sits on several lists every tab shows the row
  from the run with the newest snapshot_date (tie: newest generated_at), so one stock has one
  score, take and headline set everywhere;
- scores are not recomputed (anchors are fixed), but ranks and the note statistics are
  recomputed per tab because they depend on the tab's row set.

No network. Screening aid, not investment advice.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pandas as pd  # noqa: E402

import dashboard as D  # noqa: E402
import identity as I   # noqa: E402
import notes as N      # noqa: E402
import peers as PR     # noqa: E402

SCHEMA = 1
DATE_COLS = ("next_earnings", "ex_div")
ALL_ID = "all"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _report_subset(report: dict) -> dict:
    m, f = report.get("merge", {}), report.get("filter", {})
    return {
        "merge": {"per_source": m.get("per_source", {}), "header_gaps": m.get("header_gaps", [])},
        "filter": {k: f[k] for k in ("applied", "kept", "judgment_calls") if k in f},
        "enrich": report.get("enrich", {}),
    }


def rows_json_path(out_dir: str, slug: str, stamp: str) -> str:
    return os.path.join(out_dir, "data", f"{slug}_{stamp}.rows.json")


def write_rows_json(out_dir: str, slug: str, stamp: str, title: str, snapshot_date: str,
                    generated_at: str, region: str, report: dict, scored: pd.DataFrame,
                    take_model: str = "sonnet") -> str:
    """Never overwrites (mode "x")."""
    path = rows_json_path(out_dir, slug, stamp)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = D.sanitize({
        "schema": SCHEMA, "slug": slug, "title": title, "snapshot_date": snapshot_date,
        "generated_at": generated_at, "region": region, "take_model": take_model,
        "report": _report_subset(report),
        "rows": scored.to_dict(orient="records"),
    })
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(payload, fh, allow_nan=False, ensure_ascii=False)
    return path


def _rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).astype(object)
    df = df.where(df.notna(), None)
    for c in DATE_COLS:
        if c in df.columns:
            df[c] = pd.Series([_dt.datetime.fromisoformat(v) if isinstance(v, str) and v
                               else None for v in df[c]], index=df.index, dtype=object)
    return df


def load_rows_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: unsupported schema {data.get('schema')!r}")
    data["frame"] = _rows_to_frame(data["rows"])
    data["path"] = path
    return data


def latest_per_slug(reports_dir: str, exclude=()) -> list[dict]:
    """Newest run per slug (by generated_at, then filename), unreadable files skipped."""
    best: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(reports_dir, "data", "*.rows.json"))):
        try:
            d = load_rows_json(path)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
            print(f"hub: skipping {path}: {e}", file=sys.stderr)
            continue
        if d["slug"] in exclude:
            continue
        cur = best.get(d["slug"])
        if cur is None or (d["generated_at"], path) > (cur["generated_at"], cur["path"]):
            best[d["slug"]] = d
    return list(best.values())


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def _keys_for_lists(lists: list[dict]) -> tuple[dict[str, list[str]], set[str]]:
    """Identity key per row of every list (identity.assign_keys over all lists at once,
    in tab order, so SAN|EUR from two lists stays two companies when price and cap differ).
    Returns ({slug: keys}, {keys of rows whose ticker|currency is shared by 2+ companies})."""
    flat, spans = [], []
    for d in lists:
        fr = d["frame"]
        cols = {c: (fr[c].tolist() if c in fr.columns else [None] * len(fr))
                for c in ("ticker", "currency", "_yf_symbol", "price_local",
                          "market_cap_local_m")}
        spans.append((d["slug"], len(fr)))
        flat += [{c: cols[c][i] for c in cols} for i in range(len(fr))]
    keys, collided = I.assign_keys(flat)
    out, flagged, pos = {}, set(), 0
    for slug, n in spans:
        out[slug] = keys[pos:pos + n]
        flagged.update(k for k, c in zip(keys[pos:pos + n], collided[pos:pos + n]) if c)
        pos += n
    return out, flagged


def _rerank(df: pd.DataFrame) -> pd.DataFrame:
    """rank_in_list / rank_in_tier over this tab's rows (1 = best, ties min, scored only)."""
    out = df.copy()
    comp = pd.to_numeric(out["composite"], errors="coerce")
    rl = comp.rank(method="min", ascending=False)
    rt = comp.groupby(out["tier"].astype(object)).rank(method="min", ascending=False)
    out["rank_in_list"] = pd.Series([None if pd.isna(v) else int(v) for v in rl],
                                    index=out.index, dtype=object)
    out["rank_in_tier"] = pd.Series([None if pd.isna(v) else int(v) for v in rt],
                                    index=out.index, dtype=object)
    return out


def _tab_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).astype(object)
    df = df.where(df.notna(), None)
    return df.reset_index(drop=True)


def _warn_text(oldest: str, today: _dt.date, stale_days: int):
    d = N._as_date(oldest)
    if d is None or (today - d).days <= stale_days:
        return False, None
    return True, (f"Oldest data in this tab is {(today - d).days} days old "
                  f"(limit {stale_days}). Prices and indicators may have moved; "
                  f"re-run before relying on this tab.")


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def build_views(lists: list[dict], cfg: dict, today: _dt.date) -> list[dict]:
    stale_days = int(cfg["scoring"].get("stale_days", 3))
    # newest snapshot first; that is also the tab order
    lists = sorted(lists, key=lambda d: (d["snapshot_date"], d["generated_at"]), reverse=True)

    members: dict[str, list[str]] = {}          # key -> titles
    winner: dict[str, tuple] = {}               # key -> (rank, slug, row dict)
    own_keys: dict[str, list[str]] = {}         # slug -> keys in that list's own order
    keys_by_slug, collided = _keys_for_lists(lists)
    for d in lists:
        keys = keys_by_slug[d["slug"]]
        own_keys[d["slug"]] = []
        rank = (d["snapshot_date"], d["generated_at"])
        for key, (_, row) in zip(keys, d["frame"].iterrows()):
            if key in own_keys[d["slug"]]:
                continue
            own_keys[d["slug"]].append(key)
            members.setdefault(key, []).append(d["title"])
            rec = row.to_dict()
            rec["_snapshot_date"] = d["snapshot_date"]
            if key in collided:
                prev = rec.get("_merge_note")
                if I.COLLISION_NOTE not in str(prev or ""):
                    rec["_merge_note"] = f"{prev}; {I.COLLISION_NOTE}" if prev else I.COLLISION_NOTE
            if key not in winner or rank > winner[key][0]:
                winner[key] = (rank, d["slug"], rec)

    def records_for(df: pd.DataFrame, keys: list[str], tab_slug, scope_label, snap):
        stats = N.list_stats(df, snap, scope_label)
        recs = D.build_records(df, stats)
        for rec, key in zip(recs, keys):
            _, wslug, wrow = winner[key]
            titles = sorted(set(members[key]))
            if tab_slug is None:
                rec["lists"] = titles
            else:
                own = next(d["title"] for d in lists if d["slug"] == tab_slug)
                rec["lists"] = [t for t in titles if t != own]
            rec["n_lists"] = len(titles)
            rec["data_date"] = wrow["_snapshot_date"]
            rec["data_from_other_list"] = bool(tab_slug is not None and wslug != tab_slug)
        return recs

    views = []

    # ---- peer medians over the whole hub universe (one row per company), written onto the
    # winning rows so every tab shows the same peer comparison for a stock
    all_keys = list(winner.keys())
    for k, pf in zip(all_keys, PR.peer_fields([winner[k][2] for k in all_keys])):
        winner[k][2].update(pf)

    # ---- All lists
    all_df = _rerank(_tab_frame([winner[k][2] for k in all_keys]))
    all_recs = records_for(all_df, all_keys, None, "all-lists median",
                           max(d["snapshot_date"] for d in lists))
    dates = [w[2]["_snapshot_date"] for w in winner.values()]
    gaps, jcalls = [], []
    for d in lists:
        for g in d["report"].get("merge", {}).get("header_gaps", []):
            gaps.append(dict(g, source=f"{d['title']} / {g['source']}"))
        jcalls += d["report"].get("filter", {}).get("judgment_calls", [])
    all_report = {"merge": {"header_gaps": gaps},
                  "filter": {"judgment_calls": _dedupe(jcalls)}}
    multi = sum(1 for k in all_keys if len(set(members[k])) >= 2)
    all_banner = (f"All lists - {len(lists)} lists, {len(all_keys)} unique stocks "
                  f"({multi} on 2+ lists); data dates {min(dates)} to {max(dates)}.")
    stale, wtxt = _warn_text(min(dates), today, stale_days)
    views.append({
        "id": ALL_ID, "label": "All lists", "records": all_recs, "banner": all_banner,
        "footer_paras": D.footer_paragraphs(all_df, all_report, cfg,
                                            {"take_model": lists[0].get("take_model", "sonnet")}),
        "stale": stale, "warn_text": wtxt})

    # ---- one tab per list
    for d in lists:
        keys = own_keys[d["slug"]]
        df = _rerank(_tab_frame([winner[k][2] for k in keys]))
        meta = {"title": d["title"], "snapshot_date": d["snapshot_date"],
                "region_label": "US+EU" if d.get("region") == "us_eu" else str(d.get("region")),
                "take_model": d.get("take_model", "sonnet")}
        recs = records_for(df, keys, d["slug"], "list median", d["snapshot_date"])
        oldest = min(r["data_date"] for r in recs)
        stale, wtxt = _warn_text(oldest, today, stale_days)
        views.append({
            "id": d["slug"], "label": d["title"], "records": recs,
            "banner": D.banner_line(df, d["report"], meta),
            "footer_paras": D.footer_paragraphs(df, d["report"], cfg, meta),
            "stale": stale, "warn_text": wtxt})
    return views


def build_hub(reports_dir: str, cfg: dict, today=None, exclude=()) -> str:
    today_d = N._as_date(today) or _dt.date.today()
    lists = latest_per_slug(reports_dir, set(exclude))
    if not lists:
        raise ValueError(f"no *.rows.json found under {os.path.join(reports_dir, 'data')}")
    views = build_views(lists, cfg, today_d)
    snap = max(d["snapshot_date"] for d in lists)
    page = D.render_hub_html(views, cfg, {"title": "SWS screener lists", "snapshot_date": snap})
    path = os.path.join(reports_dir, "index.html")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(page)
    os.replace(tmp, path)
    return path


def main(argv=None) -> int:
    import build_workbook as B
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reports-dir", default=os.path.expanduser("~/swst_reports"))
    ap.add_argument("--exclude", action="append", default=[], metavar="SLUG",
                    help="list slug to leave out (repeatable)")
    a = ap.parse_args(argv)
    path = build_hub(a.reports_dir, B.load_config(), exclude=a.exclude)
    print(f"Hub: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
