#!/usr/bin/env python
"""Refresh one list from a new SWS paste and say what changed since its last run.

  update_list.py <paste-file> [--out-dir DIR] [--move-threshold N] [--diff-only]

1. Works out the list's slug from the paste title (same rule as run_pipeline).
2. Moves older pastes of the same list from <out-dir>/inputs/ to inputs/archive/,
   so run_all.sh runs each list once.
3. Runs the pipeline (takes only when the Claude token file exists; hub rebuilt).
4. Diffs the new run against the previous run of that slug: names added, dropped,
   score moves of N points or more, gate changes.

--diff-only skips steps 2 and 3 and diffs the two newest runs of the paste's list.
Screening aid, not investment advice.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build_hub as H        # noqa: E402
import parse_swst as P       # noqa: E402
import run_pipeline as R     # noqa: E402

TOKEN = os.path.expanduser("~/.config/swst-screener/claude_oauth_token")


def slug_for_paste(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        src = P.parse_paste(fh.read(), None, os.path.splitext(os.path.basename(path))[0])
    return R.slugify(src.title or src.investing_idea or src.source)


def archive_older_pastes(paste: str, slug: str, inputs_dir: str) -> list[str]:
    """Move every other inputs/*.txt whose slug matches into inputs/archive/."""
    moved = []
    keep = os.path.abspath(paste)
    for f in sorted(glob.glob(os.path.join(inputs_dir, "*.txt"))):
        if os.path.abspath(f) == keep:
            continue
        try:
            same = slug_for_paste(f) == slug
        except Exception as e:  # unparseable old paste: leave it where it is
            print(f"update: skipping {f}: {e}", file=sys.stderr)
            continue
        if same:
            dest_dir = os.path.join(inputs_dir, "archive")
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(f))
            if os.path.exists(dest):
                raise FileExistsError(f"refusing to overwrite {dest}")
            shutil.move(f, dest)
            moved.append(dest)
    return moved


def runs_for_slug(out_dir: str, slug: str) -> list[dict]:
    """Every readable run of a slug, oldest first."""
    runs = []
    for path in glob.glob(os.path.join(out_dir, "data", f"{slug}_*.rows.json")):
        try:
            d = H.load_rows_json(path)
        except (OSError, ValueError, KeyError) as e:
            print(f"update: skipping {path}: {e}", file=sys.stderr)
            continue
        if d["slug"] == slug:
            runs.append(d)
    return sorted(runs, key=lambda d: (d["generated_at"], d["path"]))


def _key(row: dict) -> str:
    return f"{row.get('ticker')}|{row.get('currency')}"


def _name(row: dict) -> str:
    return row.get("company") or row.get("yf_name") or row.get("ticker") or "?"


def diff_runs(prev: dict, new: dict, move_threshold: int = 10) -> dict:
    """Compare two rows.json payloads of the same list (rows keyed by ticker|currency)."""
    a = {_key(r): r for r in prev["rows"]}
    b = {_key(r): r for r in new["rows"]}
    added = [b[k] for k in b if k not in a]
    dropped = [a[k] for k in a if k not in b]
    moves, gates = [], []
    for k in b.keys() & a.keys():
        s0, s1 = a[k].get("score_0_100"), b[k].get("score_0_100")
        if s0 is not None and s1 is not None and abs(s1 - s0) >= move_threshold:
            moves.append((b[k], s0, s1))
        if a[k].get("gate") != b[k].get("gate"):
            gates.append((b[k], a[k].get("gate"), b[k].get("gate")))
    score = lambda r: r.get("score_0_100") if r.get("score_0_100") is not None else -1  # noqa: E731
    added.sort(key=score, reverse=True)
    dropped.sort(key=score, reverse=True)
    moves.sort(key=lambda m: abs(m[2] - m[1]), reverse=True)
    gates.sort(key=lambda g: _name(g[0]))
    return {"prev_rows": len(a), "new_rows": len(b), "added": added,
            "dropped": dropped, "moves": moves, "gates": gates}


def format_diff(d: dict, prev: dict, new: dict, move_threshold: int) -> str:
    def s(r):
        v = r.get("score_0_100")
        return "n/s" if v is None else str(v)
    out = [f"{new['title']}: {prev['generated_at']} -> {new['generated_at']}",
           f"rows {d['prev_rows']} -> {d['new_rows']}: {len(d['added'])} new, "
           f"{len(d['dropped'])} dropped, {len(d['moves'])} moved {move_threshold}+ pts, "
           f"{len(d['gates'])} gate changes"]
    if d["added"]:
        out.append("New:")
        out += [f"  {_name(r)} ({r.get('ticker')}) score {s(r)}" for r in d["added"]]
    if d["dropped"]:
        out.append("Dropped:")
        out += [f"  {_name(r)} ({r.get('ticker')}) last score {s(r)}" for r in d["dropped"]]
    if d["moves"]:
        out.append("Score moves:")
        out += [f"  {_name(r)} ({r.get('ticker')}) {s0} -> {s1} ({s1 - s0:+d})"
                for r, s0, s1 in d["moves"]]
    if d["gates"]:
        out.append("Gate changes:")
        out += [f"  {_name(r)} ({r.get('ticker')}) {g0 or 'none'} -> {g1 or 'none'}"
                for r, g0, g1 in d["gates"]]
    return "\n".join(out)


def report(out_dir: str, slug: str, move_threshold: int) -> str:
    runs = runs_for_slug(out_dir, slug)
    if len(runs) < 2:
        return f"{slug}: {len(runs)} run(s) on file, nothing to compare (new list?)"
    prev, new = runs[-2], runs[-1]
    return format_diff(diff_runs(prev, new, move_threshold), prev, new, move_threshold)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paste", help="path to the new pasted SWS page text")
    ap.add_argument("--out-dir", default=os.path.expanduser("~/swst_reports"))
    ap.add_argument("--move-threshold", type=int, default=10,
                    help="report score moves of at least this many points (default 10)")
    ap.add_argument("--diff-only", action="store_true",
                    help="no archive, no run: diff the two newest runs of this list")
    a = ap.parse_args(argv)

    slug = slug_for_paste(a.paste)
    print(f"List: {slug}")
    if not a.diff_only:
        for dest in archive_older_pastes(a.paste, slug, os.path.join(a.out_dir, "inputs")):
            print(f"Archived old paste: {dest}")
        pipe_args = [a.paste, "--out-dir", a.out_dir]
        if not (os.path.isfile(TOKEN) and os.path.getsize(TOKEN)):
            pipe_args.append("--no-takes")
        R.main(pipe_args)
    print()
    print(report(a.out_dir, slug, a.move_threshold))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
