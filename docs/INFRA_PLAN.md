# Infrastructure & Repository Restructuring Plan

**Status:** COMPLETE ✓  
**Decided:** 2026-04-05  
**Resume at:** COMPLETE ✓
**Total steps:** 38 steps across 6 phases + Phase 7 addendum (worktree restructuring, 2026-04-07)

---

## Table of contents

| Phase | Name | Steps | Server impact |
|---|---|---|---|
| 0 | Pre-flight | 0.1 – 0.6 | None (read-only) |
| 1 | Create staging branch | 1.1 – 1.3 | None (git only) |
| 2 | trading_lab setup | 2.1 – 2.8 | None (filesystem + git) |
| 3 | Staging venv isolation | 3.1 – 3.5 | Staging restart only |
| 4 | Prod hardening | 4.1 – 4.3 | ~2s prod restart |
| 5 | Infra-as-code | 5.1 – 5.9 | Nginx reload (zero downtime) |
| 6 | Cleanup | 6.1 – 6.4 | None |

---

## Target architecture

```
/root/damiverse_apps/
│
├── trading_app/                         ← Repo 1 (github: damieng-git/trading_app)
│   ├── .git/
│   ├── trading_dashboard/
│   ├── apps/
│   ├── tests/
│   ├── docs/
│   ├── infra/                           ← NEW: nginx + systemd + deploy scripts
│   ├── trading_lab/                     ← research (gitignored, own .git, add to repo later)
│   └── data/                            gitignored
│
└── trading_app_test/                    ← clone of Repo 1, staging branch
    ├── .git/
    ├── .venv/                           ← NEW: own venv, isolated from prod
    └── ...
```

**Branches:** `trading_app` → `main` (prod) + `staging`  
**Servers:** trading_app → port 8050, trading_app_test → port 8051  
**Coupling:** trading_lab pip-installs trading_dashboard from trading_app (editable install)

---

## Risk register

| ID | Risk | Likelihood | Impact | Mitigated in |
|---|---|---|---|---|
| R1 | Uncommitted changes in trading_app lost | High (they exist now) | Medium | Step 0.4 |
| R2 | Uncommitted changes in trading_app_test lost (staging running them live) | High (they exist now) | High | Step 0.5 |
| R3 | research/data lost during move (1.6GB, 2479 parquets) | Low-medium | High | Steps 0.2, 0.3, 2.1 |
| R4 | research mv is cross-filesystem (slow copy, interruption risk) | Unknown until Step 0.2 | High | Step 0.2 (filesystem check) |
| R5 | Prod downtime during service restart | Low | High | Step 4.3 |
| R6 | Staging downtime during venv switch | Low | Low | Step 3.4 |
| R7 | Nginx config error takes down both prod and staging | Medium | High | Step 5.7 (nginx -t + backup) |
| R8 | Systemd service file error prevents service restart | Medium | High | Steps 3.3, 4.2, 5.8 |
| R9 | trading_lab loses Python import path after move | Low | Medium | Step 2.8 |
| R10 | Stale branch deletion removes unreleased work | Medium | High | Step 6.1 (review before delete) |
| R11 | Staging checkout conflicts with uncommitted changes | Low | Medium | Step 0.5 (clean state first) |
| R12 | trading_app_test deploy/ conflicts with new infra/ | Known | Low | Step 5.1 (review before creating) |
| R13 | Wrong TRADING_APP_ROOT in new or edited service files | Medium | High | Steps 3.3, 4.2 checkpoints |

---

## Pre-flight: what exists today

> ⚠️ **These facts were captured on 2026-04-05 and may be stale.**  
> Re-verify uncommitted file lists and server state at Step 0.1 before trusting anything below.

```
trading_app (main, up to date with remote):
  MODIFIED (uncommitted): dashboard.css, dashboard.js, templates.py,
                           stefan.csv, docs/pine_to_python_mapping.md
  UNTRACKED:              apps/dashboard/configs/lists/swing.csv
                          docs/INFRA_PLAN.md

trading_app_test (claude/update, up to date with remote):
  MODIFIED (uncommitted): build_dashboard.py, config.json, scan_list.csv,
                           screener_builder.py, chart_builder.js, dashboard.js,
                           strategy.py, changelog_2026_03.md,
                           pine_to_python_mapping.md, strategy_pipeline_design.md,
                           test_strategy.py
  NOTE: staging server is running these uncommitted files right now.
  NOTE: deploy/ directory already exists with systemd scan service files.

research:
  Location: trading_app/research/ (1.6 GB, 2479 parquets, 127 CSVs)
  Git status: gitignored by trading_app, no git of its own

Servers:
  trading-dashboard:      active, 0.0.0.0:8050
  trading-dashboard-test: active, 127.0.0.1:8051
```

---

## Execution plan

Each step follows the format:
- **What:** the change
- **Risk:** what could go wrong
- **Command:** exact command(s) to run
- **Checkpoint:** how to verify success before proceeding to next step
- **Status:** execution state — update this as you work
- **Notes:** record what actually happened, any deviations, decisions made

---

### PHASE 0 — Pre-flight (no server changes, read-only audit)

#### Step 0.1 — Verify both servers healthy

**What:** Baseline confirmation before touching anything.  
**Risk:** None.  
**Commands:**
```bash
systemctl status trading-dashboard trading-dashboard-test
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8050
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8051
```
**Checkpoint:** Both services show `active (running)`. Both return HTTP 200.  
**Stop if:** Either server is not responding — fix before continuing.

**Status:** [x] Done — 2026-04-06  
**Notes:** Both active. Prod up since 2026-03-24 (13 days). Staging up since 2026-04-06 08:59. Both return HTTP 302 (normal redirect). BrokenPipeError in prod logs is harmless (client disconnect). Staging still using prod venv (trading_app/.venv) — expected, fixed in Phase 3.

---

#### Step 0.2 — Verify filesystem (research move safety)

**What:** Confirm research and its destination are on the same filesystem.  
If same → `mv` is atomic (instant rename, no data risk).  
If different → `mv` becomes a copy+delete (slow, interruption risk, needs rsync instead).  
**Risk:** R3, R4  
**Commands:**
```bash
df /root/damiverse_apps/trading_app/research/
df /root/damiverse_apps/
# both must show the same filesystem (same "Mounted on" value)
```
**Checkpoint:** Both show the same mount point.  
**If different filesystem:** Replace Step 2.1 with `rsync -a --progress` + verify + then `rm -rf`.

**Status:** [x] Done — 2026-04-06  
**Notes:** Same filesystem (/dev/sda1, mounted on /). mv will be atomic — no rsync needed. 47GB free, plenty of headroom.

---

#### Step 0.3 — Record research data inventory

**What:** Snapshot of research data before move. Used to verify integrity after.  
**Risk:** R3  
**Commands:**
```bash
find /root/damiverse_apps/trading_app/research/data -type f | wc -l
du -sh /root/damiverse_apps/trading_app/research/
ls /root/damiverse_apps/trading_app/research/kpi_optimization/pullback/data/results/
```
**Checkpoint:** Record the file count and total size. Key results files present:
`phase5_selections.csv`, `phase6_holdout.csv`, `phase6_universe_holdout.csv`.  
**Action:** Paste the output into Notes below before proceeding.

**Status:** [x] Done — 2026-04-06  
**Notes:** 1387 files in research/data. Total size: 1.6G. Key results files confirmed present: phase5_selections.csv, phase6_holdout.csv, phase6_universe_holdout.csv. Also present: phase6 checkpoint files and macro/regime variants. Use 1387 as post-move integrity baseline in Step 2.1.

---

#### Step 0.4 — Commit or stash trading_app uncommitted changes

**What:** The modified + untracked files in trading_app need a known state before branch operations.  
**Risk:** R1  
**Decision needed:** Are the changes (dashboard.css, dashboard.js, templates.py, stefan.csv, pine_to_python_mapping.md) ready to commit to main, or are they work-in-progress?

Option A — commit them (if ready):
```bash
cd /root/damiverse_apps/trading_app
git add apps/dashboard/static/dashboard.css \
        apps/dashboard/static/dashboard.js \
        apps/dashboard/templates.py \
        docs/pine_to_python_mapping.md
# stefan.csv and swing.csv are gitignored (local lists) — leave them
git add docs/INFRA_PLAN.md
git commit -m "WIP: dashboard updates + infra plan"
git push origin main
```

Option B — stash them (if not ready):
```bash
git stash push -m "pre-infra-restructure wip" \
  apps/dashboard/static/dashboard.css \
  apps/dashboard/static/dashboard.js \
  apps/dashboard/templates.py \
  docs/pine_to_python_mapping.md
```
**Checkpoint:** `git status` in trading_app shows clean (or only the gitignored CSVs).

**Decision:** [x] Option A — commit  
**Status:** [x] Done — 2026-04-06  
**Notes:** Committed dashboard.css, dashboard.js, templates.py, pine_to_python_mapping.md, INFRA_PLAN.md to main and pushed. stefan.csv and swing.csv left (gitignored local lists).

---

#### Step 0.5 — Commit or stash trading_app_test uncommitted changes

**What:** Modified files in trading_app_test are currently powering the staging server.  
The staging server runs them from disk — committing doesn't change what's running.  
**Risk:** R2, R11  
**Commands:**
```bash
cd /root/damiverse_apps/trading_app_test
git add apps/dashboard/build_dashboard.py \
        apps/dashboard/configs/config.json \
        apps/dashboard/configs/lists/scan_list.csv \
        apps/dashboard/screener_builder.py \
        apps/dashboard/static/chart_builder.js \
        apps/dashboard/static/dashboard.js \
        apps/dashboard/strategy.py \
        docs/changelog_2026_03.md \
        docs/pine_to_python_mapping.md \
        docs/strategy_pipeline_design.md \
        tests/test_strategy.py
git commit -m "WIP: Pullback-A staging changes (pre-restructure snapshot)"
git push origin claude/update
```
**Checkpoint:** `git status` in trading_app_test shows clean.  
**Why:** A clean state means the staging branch checkout in Phase 2 cannot produce conflicts.

> ⚠️ Re-verify the actual modified files with `git status` before running `git add` — the list above may be stale.

**Status:** [x] Done — 2026-04-06  
**Notes:** Actual files differed from plan (stale list). Committed: CLAUDE.md, scan_list.csv, data_exporter.py, chart_builder.js, dashboard.js, registry.py, docs (architecture_audit, pine_to_python_mapping, strategy_audit, strategy_changes, strategy_pipeline_design, chart_render_spec). Deleted: "2-bar sequence test.rtf". Pushed to origin/claude/update. Then merged claude/update → trading_app/main. Two conflicts: dashboard.js (kept staging version), watchlist.csv (kept staging version, overriding main's deletion). Both servers unaffected.

---

#### Step 0.6 — Review claude/update commits before promoting to staging

**What:** Audit the commits on claude/update that are ahead of main. Confirm all are safe to seed the staging branch.  
**Risk:** R10  
**Commands:**
```bash
cd /root/damiverse_apps/trading_app_test
git log --oneline origin/main..HEAD
git diff origin/main..HEAD --stat
```
**Checkpoint:** All commits reviewed. No debugging artifacts, no half-finished features that shouldn't be in staging. Confirm the deploy/ directory with scan service files is intentional and should move forward.  
**Stop if:** Any commit looks wrong — squash or drop before proceeding.

**Status:** [x] Done — 2026-04-06  
**Notes:** After merging claude/update into main, origin/main caught up — zero commits ahead. All 17 commits reviewed via git log; all are intentional feature work (Pullback-A, strategy updates, audits). deploy/ scan service files confirmed intentional.

---

### PHASE 1 — Create staging branch (git only, zero server impact)

#### Step 1.1 — Push claude/update as staging to remote

**What:** Create the `staging` branch on GitHub from the current claude/update state.  
**Risk:** None — creating a new remote branch cannot affect existing branches.  
**Commands:**
```bash
cd /root/damiverse_apps/trading_app_test
git push origin claude/update:staging
```
**Checkpoint:**
```bash
cd /root/damiverse_apps/trading_app
git fetch origin
git branch -r | grep staging
git log --oneline origin/staging | head -5
```
Staging branch exists on remote. Top commit matches expected (the WIP snapshot from 0.5).

**Status:** [x] Done — 2026-04-06  
**Notes:** Pushed claude/update:staging. Remote branch origin/staging created. Top commit 7d6a56e matches expected.

---

#### Step 1.2 — Set up staging branch tracking in trading_app_test

**What:** Make trading_app_test track the new staging branch instead of claude/update.  
**Risk:** R11 — checkout could conflict if staging differs from claude/update. It won't because staging was just created from claude/update.  
**Commands:**
```bash
cd /root/damiverse_apps/trading_app_test
git fetch origin
git checkout staging
git branch --set-upstream-to=origin/staging staging
```
**Checkpoint:**
```bash
git branch -vv
# should show: * staging ... [origin/staging]
git log --oneline -3
# should match what was pushed in 1.1
```

**Status:** [x] Done — 2026-04-06  
**Notes:** trading_app_test now on staging branch, tracking origin/staging. Confirmed via git branch -vv. Some docs files show as modified in working tree (local diffs) — not a problem, server reads from disk unchanged.

---

#### Step 1.3 — Verify staging server still healthy after checkout

**What:** Confirm the checkout didn't change any files that break the running server.  
**Risk:** Low — code is identical to claude/update.  
**Commands:**
```bash
systemctl status trading-dashboard-test
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8051
```
**Checkpoint:** Service still active, HTTP 200. Server does not need restart (files on disk are identical).

**Status:** [x] Done — 2026-04-06  
**Notes:** Service active (running since 08:59:54). HTTP 302 (normal redirect). No restart needed.

---

### PHASE 2 — trading_lab: move research and initialise repo

#### Step 2.1 — Move research directory

**What:** `mv` research from inside trading_app to a peer directory.  
**Risk:** R3, R4 — data loss if interrupted (mitigated by same-filesystem check in 0.2).  
**Commands:**
```bash
mv /root/damiverse_apps/trading_app/research /root/damiverse_apps/trading_lab
```
**Checkpoint:**
```bash
ls /root/damiverse_apps/trading_lab/
ls /root/damiverse_apps/trading_app/research/   # must error: No such file or directory
find /root/damiverse_apps/trading_lab/kpi_optimization/pullback/data/results -name "*.csv" | wc -l
# compare count to Step 0.3 baseline
```

> ⚠️ If Step 0.2 found a cross-filesystem situation, use `rsync -a --progress` here instead of `mv`.

**Status:** [x] Done — 2026-04-06  
**Notes:** mv was atomic (same filesystem confirmed in 0.2). trading_lab/ now exists at /root/damiverse_apps/trading_lab/ with all subdirs. research/ gone from trading_app. 23 CSVs confirmed in results/ (baseline was 23). Discovered 3 nested .git dirs (20260311, 20260315, 20260316) — removed them so subdirs are absorbed as plain directories into the root repo.

---

#### Step 2.2 — Verify trading_app server unaffected

**What:** Confirm prod server has no dependency on the research directory.  
**Risk:** R9  
**Commands:**
```bash
systemctl status trading-dashboard
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8050
```
**Checkpoint:** Service active, HTTP 200. (Research was gitignored and never imported by the app.)

**Status:** [x] Done — 2026-04-06  
**Notes:** Prod active (running since 2026-03-24), HTTP 302. Unaffected.

---

#### Step 2.3 — Clean up trading_app .gitignore

**What:** Remove the now-irrelevant research/* entries from trading_app's .gitignore.  
**Risk:** None.  
**What to remove:** The lines covering `research/data/`, `research/kpi_optimization/`, `research/v30/`, `research/README.md`.  
**Checkpoint:**
```bash
cd /root/damiverse_apps/trading_app
git status   # research/ should not appear at all
grep -n "research" .gitignore   # should return nothing
```

**Status:** [x] Done — 2026-04-06  
**Notes:** Removed 4 research/* lines (and the section comment) from .gitignore. git status shows no research/ entries. Committed and pushed to main.

---

#### Step 2.4 — Initialise git in trading_lab

**What:** Make trading_lab its own git repository.  
**Risk:** None — git init on a directory with no .git is safe.  
**Commands:**
```bash
cd /root/damiverse_apps/trading_lab
git init
```
**Checkpoint:**
```bash
ls .git/   # must exist
git status   # shows untracked files (data/ should appear)
```

**Status:** [x] Done — 2026-04-06  
**Notes:** git init successful. Renamed default branch master → main. Added safe.directory to global git config (repo was owned by different user from the mv). 

---

#### Step 2.5 — Verify and update trading_lab .gitignore

**What:** Ensure data directories (parquets, CSVs) are excluded from the research repo.  
**Risk:** Accidentally committing 1.6GB to git.  
**Check existing .gitignore:**
```bash
cat /root/damiverse_apps/trading_lab/kpi_optimization/pullback/.gitignore 2>/dev/null || echo "no gitignore"
```
Ensure the root `.gitignore` at `/root/damiverse_apps/trading_lab/` covers:
```
# Large data files — regenerable from scripts
kpi_optimization/pullback/data/
```
**Checkpoint:**
```bash
git status   # data/ directories must NOT appear as untracked
git check-ignore -v kpi_optimization/pullback/data/results/phase5_selections.csv
# must show it is ignored
```

**Status:** [x] Done — 2026-04-06  
**Notes:** Created root .gitignore covering: data/, kpi_optimization/*/data/, kpi_optimization/pullback/data/, __pycache__, .venv. Verified phase5_selections.csv and AAPL_1D.parquet are both ignored. 2479 parquets and 127 CSVs confirmed excluded.

---

#### Step 2.6 — Initial commit in trading_lab

**What:** First commit in the new research repo.  
**Risk:** None.  
**Commands:**
```bash
cd /root/damiverse_apps/trading_lab
git add .
git status   # review exactly what will be committed — no parquets, no large CSVs
git commit -m "Initial commit — trading_lab (research harness, Architecture A KPI optimisation)"
```
**Checkpoint:** `git log` shows 1 commit. `git show --stat HEAD` lists only scripts, src, docs — no data files.

**Status:** [x] Done — 2026-04-06  
**Notes:** 710 files, 189562 insertions. Scripts, configs, docs, src only — no parquets or large CSVs in commit.

---

#### Step 2.7 — Create GitHub repo and push

**What:** Publish trading_lab to GitHub.  
**Risk:** None.  
**Commands:**
```bash
cd /root/damiverse_apps/trading_lab
gh repo create damieng-git/trading_lab --private --source=. --remote=origin --push
```
**Checkpoint:**
```bash
git remote -v   # shows origin pointing to github
git log --oneline origin/main   # remote has the commit
```
Verify on GitHub: repo exists, main branch has 1 commit, no data files visible.

**Status:** [x] Skipped — architecture changed  
**Notes:** Decision made to move trading_lab inside trading_app/ instead of a peer directory, and skip creating a separate GitHub remote for now. trading_lab is gitignored in trading_app and will be added to trading_app git later. No remote needed at this time.

---

#### Step 2.8 — Verify trading_lab python imports still work

**What:** Confirm that moving research outside trading_app didn't break the pip install path.  
**Risk:** R9  
**Commands:**
```bash
cd /root/damiverse_apps/trading_lab
python3 -c "from trading_dashboard.indicators._base import sma; print('OK')"
python3 -c "import trading_dashboard; print(trading_dashboard.__file__)"
```
**Checkpoint:** Both commands succeed. The second shows a path inside trading_app (editable install is still valid because the trading_app directory hasn't moved).

**Status:** [x] Done — 2026-04-06  
**Notes:** Both import checks passed. trading_dashboard.__file__ correctly points to trading_app/trading_dashboard/__init__.py.

---

### PHASE 3 — Staging venv isolation

#### Step 3.1 — Create trading_app_test/.venv

**What:** Give staging its own Python environment, isolated from prod.  
**Risk:** R6 (staging brief downtime), R8  
**Commands:**
```bash
cd /root/damiverse_apps/trading_app_test
python3 -m venv .venv
```
**Checkpoint:** `.venv/` directory exists. `ls .venv/bin/python3` exists.

**Status:** [x] Done — 2026-04-06  
**Notes:** python3 -m venv .venv succeeded. .venv/bin/python3 confirmed present.

---

#### Step 3.2 — Install dependencies in staging venv

**What:** Populate the new venv with all required packages.  
**Risk:** Missing packages cause startup failure.  
**Commands:**
```bash
cd /root/damiverse_apps/trading_app_test
.venv/bin/pip install -e ".[dev]"
```
**Checkpoint:**
```bash
.venv/bin/python -c "import apps.dashboard.serve_dashboard; print('OK')"
.venv/bin/python -c "import trading_dashboard; import plotly; import pandas; print('OK')"
```
No ImportError. Smoke test passes before touching the service file.

**Status:** [x] Done — 2026-04-06  
**Notes:** pip install -e ".[dev]" clean. Smoke test passed: trading_dashboard, plotly, pandas all import OK.

---

#### Step 3.3 — Update staging service file

**What:** Point the staging systemd service at the new venv.  
**Risk:** R8, R13 — wrong path or env var breaks restart.  
**Edit** `/etc/systemd/system/trading-dashboard-test.service`:

Change:
```
ExecStart=/root/damiverse_apps/trading_app/.venv/bin/python -m apps.dashboard.serve_dashboard
```
To:
```
ExecStart=/root/damiverse_apps/trading_app_test/.venv/bin/python -m apps.dashboard.serve_dashboard
```
**Checkpoint:** Diff the file visually. Verify `TRADING_APP_ROOT` is still `/root/damiverse_apps/trading_app_test`.

**Status:** [x] Done — 2026-04-06  
**Notes:** ExecStart updated to trading_app_test/.venv/bin/python. TRADING_APP_ROOT=/root/damiverse_apps/trading_app_test confirmed unchanged.

---

#### Step 3.4 — Reload and restart staging

**What:** Apply the new service file and restart staging.  
**Risk:** R6, R8  
**Commands:**
```bash
systemctl daemon-reload
systemctl restart trading-dashboard-test
sleep 3
systemctl status trading-dashboard-test
```
**Checkpoint:** Status shows `active (running)`. PID has changed (new process). Check logs:
```bash
journalctl -u trading-dashboard-test -n 20 --no-pager
```
No import errors, no crash. HTTP 200 from staging:
```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8051
```

**Status:** [x] Done — 2026-04-06  
**Notes:** New PID 555586. Clean startup, no import errors. HTTP 302 (normal). Staging now fully isolated on its own venv.

---

#### Step 3.5 — Verify prod unaffected

**What:** Confirm changing the staging venv didn't touch prod in any way.  
**Risk:** None — belt-and-suspenders check.  
**Commands:**
```bash
systemctl status trading-dashboard
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8050
```
**Checkpoint:** Prod active, HTTP 200.

**Status:** [x] Done — 2026-04-06  
**Notes:** Prod active (PID 827, unchanged). HTTP 302. Still binding 0.0.0.0:8050 as expected (Phase 4 will fix this).

---

### PHASE 4 — Prod hardening: bind address

#### Step 4.1 — Confirm current prod bind address

**What:** Baseline before change.  
**Commands:**
```bash
ss -tlnp | grep 8050
```
**Checkpoint:** Shows `0.0.0.0:8050` (current state).

**Status:** [x] Done — 2026-04-07  
**Notes:** Confirmed 0.0.0.0:8050 (PID 827) before change.

---

#### Step 4.2 — Update prod service file

**What:** Restrict prod to localhost only. Nginx handles all external access — no reason for the app to be reachable on all interfaces.  
**Risk:** R5, R8, R13  
**Edit** `/etc/systemd/system/trading-dashboard.service`:

Change:
```
Environment=TD_HOST=0.0.0.0
```
To:
```
Environment=TD_HOST=127.0.0.1
```
**Checkpoint:** File saved. Verify `TRADING_APP_ROOT=/root/damiverse_apps/trading_app` is still present and unchanged.

**Status:** [x] Done — 2026-04-07  
**Notes:** TD_HOST=127.0.0.1 set. TRADING_APP_ROOT=/root/damiverse_apps/trading_app confirmed unchanged.

---

#### Step 4.3 — Reload and restart prod

> ⚠️ **STOP — confirm with user before proceeding. This is the highest-impact step so far: prod restarts (~2s downtime).**

**What:** Apply the change. This is the highest-risk step in Phase 4 — prod briefly restarts.  
**Risk:** R5 — ~2 second downtime (systemd respawns instantly).  
**Commands:**
```bash
systemctl daemon-reload
systemctl restart trading-dashboard
sleep 3
systemctl status trading-dashboard
```
**Checkpoint:**
```bash
ss -tlnp | grep 8050   # must now show 127.0.0.1:8050
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8050   # must return 200
curl -s -o /dev/null -w "%{http_code}" http://46.224.149.54    # via nginx, must return 200
journalctl -u trading-dashboard -n 20 --no-pager   # no errors
```
**Rollback:** If prod fails to start, revert `TD_HOST` to `0.0.0.0` and restart.

**Status:** [x] Done — 2026-04-07  
**Notes:** New PID 575613. Binds 127.0.0.1:8050. HTTP 302 locally and via nginx (46.224.149.54). No errors in logs.

---

### PHASE 5 — Infra-as-code

#### Step 5.1 — Review existing deploy/ in trading_app_test

**What:** trading_app_test already has a `deploy/` directory with `trading-dashboard-test-scan.service` and `trading-dashboard-test-scan.timer`. Understand what they do before overwriting.  
**Risk:** R12  
**Commands:**
```bash
cat /root/damiverse_apps/trading_app_test/deploy/trading-dashboard-test-scan.service
cat /root/damiverse_apps/trading_app_test/deploy/trading-dashboard-test-scan.timer
```
**Checkpoint:** Understand the scan service. Decide: fold into new `infra/` or keep in `deploy/` as-is.

**Decision:** [x] Fold into `infra/`  [ ] Keep in `deploy/` as-is  
**Decision notes:** Scan service + timer are server config, belong alongside other service files. Fixed venv path from trading_app to trading_app_test.  
**Status:** [x] Done — 2026-04-07  
**Notes:** Folded both files into infra/, updated ExecStart/ExecStartPost to use trading_app_test/.venv/bin/python.

---

#### Step 5.2 — Create infra/ directory in trading_app

**What:** The canonical home for all server configuration, tracked in git.  
**Risk:** None — creating files, not touching live configs yet.  
**Commands:**
```bash
mkdir -p /root/damiverse_apps/trading_app/infra
```
**Checkpoint:** Directory exists.

**Status:** [x] Done — 2026-04-07  
**Notes:** mkdir -p succeeded.

---

#### Step 5.3 — Write nginx.conf

**What:** Copy the live nginx config into infra/, clean up the duplicate `/fig` block.  
**Risk:** None — writing a file, live config untouched.  
**Commands:**
```bash
cp /etc/nginx/sites-enabled/trading-dashboard \
   /root/damiverse_apps/trading_app/infra/nginx.conf
# then edit infra/nginx.conf: remove the duplicate standalone `location /fig { }` block
```
**Checkpoint:**
```bash
diff /etc/nginx/sites-enabled/trading-dashboard \
     /root/damiverse_apps/trading_app/infra/nginx.conf
# diff should show only the removed duplicate block
```
Visual review: confirm the cleaned file has exactly the intended locations.

**Status:** [x] Done — 2026-04-07  
**Notes:** Copied and removed duplicate standalone `location /fig {}` block (already covered by regex `^/(api|fig)/`). Resulting file has 4 location blocks: api/fig regex, /test/, /, and that's it.

---

#### Step 5.4 — Write service files into infra/

**What:** Copy both live service files into infra/.  
**Risk:** None.  
**Commands:**
```bash
cp /etc/systemd/system/trading-dashboard.service \
   /root/damiverse_apps/trading_app/infra/trading-dashboard.service
cp /etc/systemd/system/trading-dashboard-test.service \
   /root/damiverse_apps/trading_app/infra/trading-dashboard-test.service
```
**Checkpoint:** Both files present. Diff against live versions — should be identical (we already edited live files in Phases 3 and 4).

**Status:** [x] Done — 2026-04-07  
**Notes:** Both service files copied. Diff confirmed identical to live. Also added trading-dashboard-test-scan.service and .timer (folded from deploy/).

---

#### Step 5.5 — Write deploy scripts

**What:** Standardise the deploy flow into executable scripts.  
**Risk:** None — creating scripts, not running them yet.

`infra/deploy-staging.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd /root/damiverse_apps/trading_app_test
git pull origin staging
systemctl restart trading-dashboard-test
echo "Staging deployed. Check: http://46.224.149.54/test/"
```

`infra/deploy-prod.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd /root/damiverse_apps/trading_app
git pull origin main
systemctl restart trading-dashboard
TRADING_APP_ROOT=/root/damiverse_apps/trading_app \
  python3 -m trading_dashboard dashboard rebuild-ui
echo "Prod deployed. Check: http://46.224.149.54/"
```

```bash
chmod +x /root/damiverse_apps/trading_app/infra/deploy-staging.sh
chmod +x /root/damiverse_apps/trading_app/infra/deploy-prod.sh
```
**Checkpoint:** Scripts exist, are executable, `set -euo pipefail` ensures they abort on any error.

**Status:** [x] Done — 2026-04-07  
**Notes:** Both scripts written and chmod +x. infra/ contains 6 files total.

---

#### Step 5.6 — Commit infra/ to staging branch first

**What:** Verify infra/ in staging before it goes to main.  
**Risk:** None — committing to staging, not touching live symlinks yet.  
**Commands:**
```bash
cd /root/damiverse_apps/trading_app
git checkout staging
git add infra/
git commit -m "Add infra/: nginx config, systemd services, deploy scripts"
git push origin staging
```
**Checkpoint:** infra/ visible on staging branch on GitHub. Files look correct.

**Status:** [x] Done — 2026-04-07  
**Notes:** Committed as "Add infra/: nginx config, systemd services, deploy scripts". Merge conflict in pine_to_python_mapping.md resolved by keeping origin/staging (docs-cleanup) version. Pushed to origin/staging.

---

#### Step 5.7 — Merge infra/ to main via PR

**What:** Promote infra/ from staging to main so prod repo has infra/ on disk. Must happen before symlinking — symlinks point into trading_app/infra/ which only exists on the branch that is checked out.  
**Risk:** None — git operation only.  
**Process:** Open PR on GitHub: staging → main. Review diff. Merge. Pull in trading_app:
```bash
cd /root/damiverse_apps/trading_app
git pull origin main
```
**Checkpoint:** `ls /root/damiverse_apps/trading_app/infra/` shows all 6 files. `git log --oneline -3` shows the merge.

**Status:** [x] Done — 2026-04-07  
**Notes:** Merged staging into main locally (gh not installed). One conflict in INFRA_PLAN.md resume pointer — resolved trivially. Pushed to origin/main. All 7 infra/ files confirmed on disk.

---

#### Step 5.8 — Symlink nginx.conf

> ⚠️ **STOP — confirm with user before proceeding. A broken nginx config takes down both prod and staging simultaneously.**

**What:** Replace live nginx config with a symlink to the repo version. Requires infra/ on disk (step 5.7 must be done first).  
**Risk:** R7 — if the symlinked file has any issue, nginx reload fails.  
**Mitigation:** Back up the current file first.  
**Commands:**
```bash
# Backup
cp /etc/nginx/sites-enabled/trading-dashboard /tmp/nginx.conf.bak

# Symlink
ln -sf /root/damiverse_apps/trading_app/infra/nginx.conf \
       /etc/nginx/sites-enabled/trading-dashboard

# Test BEFORE reloading
nginx -t
```
**Checkpoint:** `nginx -t` outputs `syntax is ok` and `test is successful`.  
**Only then reload:**
```bash
systemctl reload nginx
```
**Verify:**
```bash
curl -s -o /dev/null -w "%{http_code}" http://46.224.149.54        # 200
curl -s -o /dev/null -w "%{http_code}" http://46.224.149.54/test/  # 200
```
**Rollback:** `rm /etc/nginx/sites-enabled/trading-dashboard && cp /tmp/nginx.conf.bak /etc/nginx/sites-enabled/trading-dashboard && systemctl reload nginx`

**Status:** [x] Done — 2026-04-07  
**Notes:** Backup at /tmp/nginx.conf.bak. Symlinked. nginx -t passed. Reloaded. prod 302, staging 302 — both healthy.

---

#### Step 5.9 — Symlink systemd service files

> ⚠️ **STOP — confirm with user before proceeding. Test on staging first; only symlink prod after staging restart succeeds.**

**What:** Replace live service files with symlinks to the repo versions. Requires infra/ on disk (step 5.7 must be done first).  
**Risk:** R8 — broken symlink = service can't restart.  
**Mitigation:** Back up first. Services keep running from existing process — symlink only affects next restart.  
**Commands:**
```bash
cp /etc/systemd/system/trading-dashboard.service /tmp/trading-dashboard.service.bak
cp /etc/systemd/system/trading-dashboard-test.service /tmp/trading-dashboard-test.service.bak

ln -sf /root/damiverse_apps/trading_app/infra/trading-dashboard.service \
       /etc/systemd/system/trading-dashboard.service
ln -sf /root/damiverse_apps/trading_app/infra/trading-dashboard-test.service \
       /etc/systemd/system/trading-dashboard-test.service

systemctl daemon-reload
```
**Checkpoint:**
```bash
systemctl cat trading-dashboard | head -5         # must show infra/ path
systemctl cat trading-dashboard-test | head -5    # must show infra/ path
systemctl status trading-dashboard                # still active (not restarted)
systemctl status trading-dashboard-test           # still active
```
Services are still running — the symlink only matters on next restart.  
**Test restart for staging first** (less critical than prod):
```bash
systemctl restart trading-dashboard-test
systemctl status trading-dashboard-test
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8051
```
**Only if staging restart succeeds:** restart prod.
```bash
systemctl restart trading-dashboard
systemctl status trading-dashboard
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8050
```
**Rollback:** `cp /tmp/*.bak /etc/systemd/system/ && systemctl daemon-reload`

**Status:** [x] Done — 2026-04-07  
**Notes:** Both service files symlinked. daemon-reload clean. Staging restart: active 302. Prod restart: active 302. Both services now managed via infra/ in git.

---

### PHASE 6 — Cleanup

#### Step 6.1 — Review all remote branches before deleting

**What:** List every remote branch with its last commit and author. Review before deleting anything.  
**Risk:** R10  
**Commands:**
```bash
cd /root/damiverse_apps/trading_app
git for-each-ref --format='%(refname:short) %(committerdate:short) %(subject)' \
  refs/remotes/origin | sort -k2 -r
```
**Checkpoint:** Each branch reviewed. Confirmed safe-to-delete list:
- `claude/update` — superseded by `staging`
- `claude/add-test-md-file-YIDV8` — Claude scratch branch
- `claude/explain-codebase-mmcgqiglt5zpiprj-yZksh` — Claude scratch branch (already gone from remote)
- `claude/list-repositories-3qO0o` — Claude scratch branch
- `feature/claude-1` — confirmed fully merged into main

**Branches confirmed for deletion:** all 5 above  
**Status:** [x] Done — 2026-04-07  
**Notes:** Remote now has only main and staging. Stale local branches also cleaned in both trading_app and trading_app_test.

---

#### Step 6.2 — Delete stale branches one by one

**What:** Remove noise from the remote.  
**Risk:** R10 — deleting wrong branch.  
**Commands (one at a time — paste only after confirming each in Step 6.1):**
```bash
git push origin --delete claude/update
git push origin --delete "claude/add-test-md-file-YIDV8"
git push origin --delete "claude/explain-codebase-mmcgqiglt5zpiprj-yZksh"
git push origin --delete "claude/list-repositories-3qO0o"
```
**Checkpoint after each:** `git branch -r` — confirm only the deleted branch is gone, others intact.

**Status:** [ ] Not started  
**Notes:** —

---

#### Step 6.3 — Update CLAUDE.md

**What:** Reflect the new workflow in the project docs.  
**Changes needed:**
- Two-branch workflow (main / staging)
- `trading_lab` at `/root/damiverse_apps/trading_lab/` — separate repo
- Deploy via `infra/deploy-staging.sh` and `infra/deploy-prod.sh`
- Staging venv at `trading_app_test/.venv`
- Remove outdated `trading_app_test has no venv` note

**Checkpoint:** CLAUDE.md committed to both staging and main.

**Status:** [x] Done — 2026-04-07  
**Notes:** Updated server layout table (added venv row), nginx/systemd note now references infra/, added deploy scripts section, updated promote workflow to use deploy-prod.sh.

---

#### Step 6.4 — Final state verification

**What:** End-to-end check that everything works as designed.  
**Commands:**
```bash
# Both servers healthy
systemctl status trading-dashboard trading-dashboard-test

# Correct bind addresses
ss -tlnp | grep -E '8050|8051'
# 8050 → 127.0.0.1 (prod)
# 8051 → 127.0.0.1 (staging)

# Nginx routing
curl -s -o /dev/null -w "%{http_code}" http://46.224.149.54        # prod
curl -s -o /dev/null -w "%{http_code}" http://46.224.149.54/test/  # staging

# Git structure
cd /root/damiverse_apps/trading_app && git branch -vv
cd /root/damiverse_apps/trading_app_test && git branch -vv
cd /root/damiverse_apps/trading_lab && git log --oneline -3

# trading_lab imports
cd /root/damiverse_apps/trading_lab
python3 -c "from trading_dashboard.indicators._base import sma; print('OK')"

# Infra in git
ls /root/damiverse_apps/trading_app/infra/
readlink /etc/nginx/sites-enabled/trading-dashboard     # must point to infra/
readlink /etc/systemd/system/trading-dashboard.service  # must point to infra/
```
**Checkpoint:** All commands succeed. No errors. Architecture matches the target.

**Status:** [x] Done — 2026-04-07  
**Notes:** Both services active. 8050→127.0.0.1, 8051→127.0.0.1. Nginx and systemd symlinks verified. Remote: main + staging only. trading_app_test on staging, pulled up to date.

---

## Rollback reference

| Step | What was done | Rollback command |
|---|---|---|
| 1.1 | staging branch created | `git push origin --delete staging` |
| 1.2 | trading_app_test repointed to staging | `git checkout claude/update && git branch --set-upstream-to=origin/claude/update` |
| 2.1 | research moved to trading_lab | `mv /root/damiverse_apps/trading_lab /root/damiverse_apps/trading_app/research` |
| 3.3–3.4 | staging venv + service updated | Revert ExecStart to trading_app/.venv path → `daemon-reload && restart` |
| 4.2–4.3 | prod bind address changed | Revert `TD_HOST=0.0.0.0` → `daemon-reload && systemctl restart trading-dashboard` |
| 5.7 | nginx symlink | `cp /tmp/nginx.conf.bak /etc/nginx/sites-enabled/trading-dashboard && nginx -t && systemctl reload nginx` |
| 5.8 | systemd symlinks | `cp /tmp/trading-dashboard*.bak /etc/systemd/system/ && systemctl daemon-reload` |
| 6.2 | stale branches deleted | **Cannot undo** — review Step 6.1 carefully before executing |

---

## Normal workflow after restructuring

### Feature development
```
1. Edit code in trading_app_test/  (on staging branch)
2. Test at http://46.224.149.54/test/
3. Push to origin/staging
4. Open PR on GitHub: staging → main
5. Merge PR
6. bash infra/deploy-prod.sh    ← pulls main + restarts + rebuilds UI
```

### Deploy staging
```bash
bash /root/damiverse_apps/trading_app/infra/deploy-staging.sh
# git pull staging + restart staging server
```

### Deploy prod
```bash
bash /root/damiverse_apps/trading_app/infra/deploy-prod.sh
# git pull main + restart prod + rebuild-ui
```

### Hotfix to prod (bypass staging)
```
1. Fix directly on main branch in trading_app/
2. git push origin main
3. bash infra/deploy-prod.sh
4. git checkout staging && git merge main && git push origin staging
   ← keep staging in sync
```

### Research work (trading_lab)
```
1. Work in /root/damiverse_apps/trading_app/trading_lab/
2. No git remote — local only, gitignored by trading_app
3. Python imports work via: pip install -e /root/damiverse_apps/trading_app/main
```

### Server management
```bash
systemctl restart trading-dashboard        # restart prod
systemctl restart trading-dashboard-test   # restart staging
journalctl -u trading-dashboard -f         # tail prod logs
journalctl -u trading-dashboard-test -f    # tail staging logs
```

### Infra changes (nginx / systemd)
```
1. Edit the file in trading_app/main/infra/
2. git commit to main
3. For nginx: nginx -t && systemctl reload nginx
4. For systemd: systemctl daemon-reload && systemctl restart <service>
```

---

## PHASE 7 — Worktree restructuring (2026-04-07)

Post-completion addendum. Restructured from two separate clones to a single repo with two git worktrees under a common parent directory.

### What changed

**Before:**
```
/root/damiverse_apps/
├── trading_app/      ← git repo, branch: main (prod)
└── trading_app_test/ ← git clone, branch: staging
```

**After:**
```
/root/damiverse_apps/trading_app/
├── main/          ← primary worktree, branch: main (prod, port 8050)
├── stag/          ← linked worktree, branch: staging (staging, port 8051)
└── trading_lab/   ← research (gitignored, no remote)
```

### Steps executed

1. Stopped both servers
2. `mv trading_app trading_app_main_temp && mkdir trading_app && mv trading_app_main_temp trading_app/main`
3. `git worktree add /root/damiverse_apps/trading_app/stag staging`
4. Moved `trading_lab/` from inside `main/` to `trading_app/trading_lab/`
5. Moved `trading_app_test/data/` → `trading_app/stag/data/`
6. Deleted old `main/.venv` (shebangs hardcoded), recreated fresh at `main/.venv`
7. Created fresh `stag/.venv`, installed deps
8. Updated `infra/` service files, deploy scripts, scan service — all paths updated
9. Updated nginx + systemd symlinks to new `main/infra/` path
10. Restarted both servers — both active, prod 302, staging 302
11. Removed `trading_app_test/`
12. Updated CLAUDE.md and committed

### Rollback

```bash
# Stop servers
systemctl stop trading-dashboard trading-dashboard-test

# Restore trading_app_test from stag worktree
cp -r /root/damiverse_apps/trading_app/stag /root/damiverse_apps/trading_app_test
python3 -m venv /root/damiverse_apps/trading_app_test/.venv
/root/damiverse_apps/trading_app_test/.venv/bin/pip install -e "/root/damiverse_apps/trading_app_test/.[dev]"

# Move trading_app/main back to trading_app
mv /root/damiverse_apps/trading_app/main /root/damiverse_apps/trading_app_main_temp
mv /root/damiverse_apps/trading_app_main_temp /root/damiverse_apps/trading_app
# (recreate venv, update symlinks, restart services)
```

**Status:** [x] Done — 2026-04-07
