---
name: swst-screener-workbook
description: >-
  Turn pasted Simply Wall St "Investing Ideas" screener pages into a US+EU filtered,
  enriched and scored Excel workbook plus an interactive dashboard (one entry per list in
  its side rail, served at http://the-damiverse:3090/). Use this whenever the user pastes stock
  screener results, mentions "Simply Wall St", "SWS", "screener", an "investing idea"
  (e.g. "Undervalued based on future growth", "PEG < 0.8"), or says things like
  "export these stocks", "merge these stock lists", "add this list to the dashboard",
  "update the X list", "refresh this list", "what changed in X",
  "filter these to US and European stocks", or pastes lists of companies with market
  caps and 7D/1Y returns, even if they don't say "Excel" or "dashboard".
---

# SWS Screener -> Workbook + Dashboard

A screening aid, not investment advice. Flags, scores and gates are prompts to review,
never buy/sell calls. Say so in every report.

Python: `/home/damien/damiverse_apps/trading_app/main/.venv/bin/python`. Run commands
from this skill folder. Design rules and their reasons: `references/design.md`.
Paste formats and gotchas: `references/formats.md`.

## Adding a list

1. Save the raw paste to `~/swst_reports/inputs/<short-name>_<YYYYMMDD>.txt`.
2. Quick offline check before anything else:
   `parse_swst.parse_paste(text)` gives title, rows parsed vs header count, countries,
   unknown currencies, and `notes` (rows that could not be parsed). Report the header gap
   prominently; if the paste ends mid-sized (not at tiny caps), tell the user to scroll to
   the bottom of the SWS page and re-copy.
3. Run it:
   ```
   python scripts/run_pipeline.py ~/swst_reports/inputs/<file>.txt [--no-takes]
   ```
   This parses, merges, filters to US + EU, enriches with yfinance, scores, writes
   `<slug>_<stamp>.xlsx` (+ `__filtered_` copy), `<slug>_<stamp>.html` and
   `data/<slug>_<stamp>.rows.json` into `~/swst_reports/`, then rebuilds the hub.
4. Refresh every list on fresh data: `bash scripts/run_all.sh` (one list at a time,
   about 2 min each; log in `~/swst_reports/data/run_all.log`). Data older than 3 days
   shows a stale banner.

## Updating a list

When the user pastes a list that is already in the hub ("update buy-the-dip", "here's the
new semiconductor list", or a paste whose title matches an existing list):

1. Run the step 2 parse check from "Adding a list" and confirm the slug
   (`scripts/update_list.py` prints it as "List: <slug>"). If it does not match an existing
   list slug in `~/swst_reports/data/`, the SWS title changed: tell the user before running,
   because the run would create a second entry in the hub.
2. Save the paste to `~/swst_reports/inputs/<short-name>_<YYYYMMDD>.txt`, reusing the
   short name of the list's existing paste in `inputs/`.
3. Run:
   ```
   python scripts/update_list.py ~/swst_reports/inputs/<file>.txt
   ```
   It moves older pastes of the same list to `inputs/archive/` (so `run_all.sh` runs each
   list once), runs the pipeline (takes only if the token file exists, hub rebuilt), then
   diffs against the previous run: new names, dropped names, score moves of 10+ points
   (`--move-threshold N`), gate changes. `--diff-only` re-prints the diff of the two newest
   runs without running anything.
4. Report the change first (counts, then the notable new/dropped/moved names with one
   line each), then the usual run report below, then the hub link
   http://the-damiverse:3090/. Score moves can come from fresh market data rather than from
   SWS changing the list; say so when the paste is unchanged and only scores moved.

## Reporting a run

Short and quantitative, recommendations first:
- header count vs parsed vs kept (US+EU) vs enriched vs scored, per list;
- rows not scored and why (mostly loss-makers with no valuation metric);
- EUR names matched by exchange probing, and any left unresolved;
- top names (score, tier, V/Q/T/R, gate, P/E and EV/FCF vs peer median) and stocks on 2+ lists;
- caveats: timing saturation, stale data, generated takes can be wrong, not advice.

## Serving

`swst-reports.service` (systemd, Tailscale IP only, port 3090; unit source in `infra/`):
`/` is the hub (All lists + one entry per list in the left rail), `/files` the file listing, `/latest` the newest
single-list page. After editing `scripts/serve_reports.py`: `sudo systemctl restart
swst-reports`, then `curl -s http://100.115.107.24:3090/` (the hostname resolves to
127.0.1.1 on the box itself). Rebuild the hub without network: `python scripts/build_hub.py
[--exclude SLUG]`. To drop a list, move its files out of `~/swst_reports` and rebuild.

## Claude's takes

On by default; need a token: `claude setup-token`, saved to
`~/.config/swst-screener/claude_oauth_token` (chmod 600). Without it, run with `--no-takes`.

## Config (`config/defaults.yaml`)

Region sets, FX (ECB, `fx_asof`), flag thresholds, cap-tier cutoffs, `scoring` anchors and
weights, `symbol_overrides` (`"TICKER|CCY": SYMBOL`). Changing a threshold needs a re-run.

## Peer comparison

The hub and per-list pages show P/E, P/S, EV/Sales and EV/FCF with the % against the
industry peer median (sector if the industry has fewer than 3 names; hover a cell for the
median and group). Peers come from the stocks already in the hub, so the comparison gets
better as lists are added. In a report, quote it as "x% below/above its peer median (n
peers)", never as cheap or expensive. Rows from runs before EV/Sales and EV/FCF existed
show n/a for those two until `run_all.sh` refreshes them.

## The "Valuation (SWS)" trap

The SWS Valuation column is not a comparable P/E (banks ~1, software ~29). Use the real
multiples from enrichment. Analyst target, growth, yield and industry exist only where the
table view was pasted; blank means "not in paste", not zero.

## Testing

`python -m pytest tests/ -q -p no:cacheprovider` (offline; saved Yahoo snapshots in
`tests/fixtures/enrich/`, fakes in `tests/fakes.py`, page JS checked with Node stubs).
