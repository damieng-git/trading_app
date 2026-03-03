# Phase 20B — Expanded Strategy Matrix Validation

## Objective

Fill the **strategy type × timeframe matrix** with validated entry/exit combos:

|                    | 4H | 1D | 1W | 2W |
|--------------------|----|----|----|----|
| **Buy the Dip**    | ?  | ?  | ✓  | ?  |
| **Swing Trading**  | —  | ?  | ✓  | ?  |
| **Trend Position** | —  | —  | ?  | ?  |

Phase 20 (v1) found 2 strong 1W strategies. Phase 20B expands coverage to
4H, 1D, and 2W using:
- Broader candidate pool (all archetypes, 3 ranking axes)
- Dual validation: 4-fold + 2-fold
- READY / CANDIDATE / GAP classification instead of binary pass/fail

---

## Inputs

| Source | Contents | Est. combos |
|---|---|---|
| `phase18_1_combos.json` | All P18 stage-1 combos | 211 total |
| `supplement_validated.json` | P18 supplement (mixed-polarity gaps) | 24 |
| `phase19_validated.json` | P19 MTF combos | 7 |

---

## Candidate Selection (expanded)

### P18 combos — 3-axis balanced shortlist per TF

For each TF, build candidates from the union of:
1. **Top N by trades** (statistical weight)
2. **Top N by HR × avg_ret** (per-trade quality)
3. **Top N by worst-trade** (tail-risk safety, least negative worst)

De-duplicate by (kpis, pols). This gives behavioral diversity across archetypes.

| TF | N per axis | HR floor | Expected unique |
|---|---|---|---|
| 4H | 20 | ≥ 80% | ~45–53 |
| 1D | 20 | ≥ 80% | ~40–49 |
| 1W | 18 | ≥ 80% | ~35–45 |
| 2W | 15 | ≥ 75% | ~30–46 |

### P18 supplement — add all validated combos per TF

### P19 MTF — add all 7 validated combos (tested as overlays in later phase)

### Total expected: ~180–220 unique combos

---

## Validation: 4-fold + 2-fold (dual)

### 4-fold (regime robustness)

TF-aware windows (same as Phase 20 v2):

| TF | F1 | F2 | F3 | F4 |
|---|---|---|---|---|
| 4H | 2020-07→2021-07 | 2022-01→2023-01 | 2023-07→2024-07 | 2025-01→2025-07 |
| 1D | same | same | same | same |
| 1W | same | same | same | 2024-01→2025-07 |
| 2W | 2019-07→2021-01 | 2021-01→2022-07 | 2022-01→2023-07 | 2023-07→2025-07 |

### 2-fold (practical viability)

Simple 50/50 split of total data:

| TF | H1 (train) | H2 (test) |
|---|---|---|
| All | first 50% of data | last 50% of data |

### Per-fold thresholds

| Metric | 4H | 1D | 1W | 2W |
|---|---|---|---|---|
| Min trades | 40 | 30 | 20 | 10 |
| Min HR | 65% | 65% | 70% | 70% |
| Min avg_ret | > 0 | > 0 | > 0 | > 0 |
| Worst trade | > −30% | > −30% | > −25% | > −25% |
| Min bars (slice) | 50 | 50 | 20 | 10 |

---

## Classification (after validation)

### Status per combo

| Status | 4-fold | 2-fold | Meaning |
|---|---|---|---|
| **READY** | ≥ 3/4 pass | pass | Deploy candidate |
| **CANDIDATE** | ≥ 2/4 pass | pass | Needs tuning or regime gate |
| **TACTICAL** | < 2/4 pass | pass | Works recently, not regime-safe |
| **GAP** | < 2/4 pass | fail | Not viable |

### Behavior classification (post-validation)

Applied to all combos with status READY or CANDIDATE:
- **Buy the Dip**: bearish momentum KPI(s) + bullish trend KPI
- **Swing Trading**: medium hold, medium frequency
- **Trend Position**: all-bull polarity, longer hold

---

## Pipeline Phases

### 20B.0 — Candidate extraction (expanded 3-axis)
### 20B.1 — 4-fold validation (all TFs)
### 20B.2 — 2-fold validation (all TFs)
### 20B.3 — Exit mode re-optimization (for READY/CANDIDATE combos)
### 20B.4 — Baseline comparison (random entry + buy-and-hold)
### 20B.5 — Classification + matrix assembly

---

## Memory Management

- Process ONE timeframe at a time (load → validate → free)
- No simultaneous TF loading
- `_check_memory()` before each load, threshold 70%
- `gc.collect()` after each TF

---

## Expected Runtime

| Phase | Operations | Est. time |
|---|---|---|
| 20B.0 | JSON load + 3-axis filter | < 1 min |
| 20B.1 | ~200 combos × 4 folds × ~268 stocks | ~15 min |
| 20B.2 | ~200 combos × 2 folds × ~268 stocks | ~8 min |
| 20B.3 | ~50 combos × 5 exits × 5 TMK × 6 folds | ~20 min |
| 20B.4 | ~50 combos × 3 baselines × 6 folds | ~10 min |
| 20B.5 | Classification + report | < 1 min |
| **Total** | | **~55 min** |

---

## Execution

```bash
cd /root/damiverse_apps/trading_app
nohup .venv/bin/python3 research/kpi_optimization/phase20b_master.py \
  > research/kpi_optimization/phase20b.log 2>&1 &
```
