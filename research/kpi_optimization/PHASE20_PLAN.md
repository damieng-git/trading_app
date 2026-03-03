# Phase 20 — Strategy Validation & Selection

## Objective

Validate the best entry combos for each timeframe using **4-fold walk-forward**
testing across diverse market regimes. Select the best combo per trading
frequency (Daily / Swing / Position) by **per-trade quality (HR × Avg Ret)**
with a minimum trade count floor.

Phase 20 does NOT discover new combos — it recycles the ~5 000 combos already
found in Phase 18 (`phase18_1_combos.json`) and re-evaluates them with stricter,
multi-regime validation.

---

## Inputs

| Source | Path | Contents |
|---|---|---|
| P18 combos | `outputs/all/phase18/phase18_1_combos.json` | All combos that passed HR floor in P18 (all archetypes × TFs) |
| P19 MTF combos | `outputs/all/phase19/phase19_validated.json` | Validated MTF entry/exit combos |
| Enriched data | `research/data/feature_store/enriched/sample_300/stock_data/` | Parquet files per symbol × TF |

---

## Data Range & 4-Fold Windows

Full data: **2018-01 to ~2025-06** (~7.5 years, ~1 875 trading days on 1D).

### Rolling walk-forward folds (TF-aware)

Phase 20 now uses **timeframe-specific fold windows** so higher timeframes
(`1W`, `2W`) have enough bars in each fold.

#### 1D folds (short windows, high bar density)

| Fold | Test period | Market regime in test |
|---|---|---|
| F1 | **2020-07 → 2021-07** | Post-COVID recovery, stimulus bull |
| F2 | **2022-01 → 2023-01** | 2022 bear market (rates + inflation) |
| F3 | **2023-07 → 2024-07** | Recovery, AI/tech bull |
| F4 | **2025-01 → 2025-07** | Recent market conditions |

#### 1W folds (extended recent fold)

| Fold | Test period | Market regime in test |
|---|---|---|
| F1 | **2020-07 → 2021-07** | Post-COVID recovery |
| F2 | **2022-01 → 2023-01** | Bear market |
| F3 | **2023-07 → 2024-07** | Recovery |
| F4 | **2024-01 → 2025-07** | Recent 18-month period |

#### 2W folds (longer windows required)

| Fold | Test period | Market regime in test |
|---|---|---|
| F1 | **2019-07 → 2021-01** | Pre/Post-COVID transition |
| F2 | **2021-01 → 2022-07** | Late bull to early bear |
| F3 | **2022-01 → 2023-07** | Bear market stress period |
| F4 | **2023-07 → 2025-07** | Recovery + recent conditions |

> **Critical**: Fold 2 covers the 2022 bear. Strategies that fail here are
> regime-dependent and eliminated.

### Implementation via fractional windows

The `sim_combo` engine uses `start_frac` / `end_frac` (0.0 – 1.0). In Phase 20,
we convert fold dates per symbol, then **slice each symbol's arrays to the fold
window first** before simulation. This avoids cross-symbol date misalignment and
prevents `no_result` artifacts on higher timeframes.

---

## Phase 20.0 — Candidate Extraction

**Goal:** Build the candidate pool from P18 combos, archetype-agnostic.

### Algorithm

1. Load `phase18_1_combos.json` (all ~5 000 combos from all archetypes)
2. For each timeframe (1D, 1W, 2W):
   - Filter: `hr > 85%` in search window
   - Filter by TF-dependent minimum trades: 1D≥300, 1W≥100, 2W≥50
   - Rank by **trades descending** (statistical robustness)
   - De-duplicate by KPI+polarity combination
   - Take **top 20** candidates
3. Also load `phase19_validated.json` for MTF overlay candidates (used in 20.3)

### Output

- `phase20_candidates.json` — ~60 combos (20 per TF)
- Console log with candidate list per TF

### Why archetype-agnostic?

P18 archetype labels (A_trend, B_dip, etc.) are based on KPI categories, not
trading behavior. The best "dip buy" combo might be labeled E_mixed. We
select by performance, label by behavior later (Phase 20.6).

---

## Phase 20.1 — 4-Fold Walk-Forward Validation

**Goal:** Eliminate combos that only work in one market regime.

### Algorithm

For each candidate combo × each of the 4 folds:

1. Convert fold date range to `start_frac` / `end_frac` for this symbol's data
2. Run `sim_combo(all_pc, kpis, pols, tf, start_frac=..., end_frac=...)`
   using the combo's **original exit_mode, gate, delay** from P18
3. Record: trades, HR, avg_ret, PF, worst_trade, avg_hold

### Pass criteria (EVERY fold must pass ALL)

| Metric | Threshold | Rationale |
|---|---|---|
| Trades | ≥ 30 (1D), ≥ 20 (1W), ≥ 10 (2W) | Statistical significance (TF-adjusted) |
| HR | ≥ 75% | Minimum quality |
| Avg Ret | > 0% | Must be profitable |
| Worst Trade | > −25% | Risk guard |

### Elimination rule

Any combo that fails ANY metric in ANY fold is **dropped**. No exceptions.

### Selection metric

Among survivors, rank by:

```
score = min(HR across folds) × mean(avg_ret across folds)
```

This optimizes for **worst-case quality per trade**.

### Output

- `phase20_fold_results.json` — per-combo × per-fold metrics
- `phase20_validated.json` — surviving combos with aggregated scores
- Console log with pass/fail per combo per fold

---

## Phase 20.2 — Regime Layer Test

**Goal:** Does adding a higher-TF "active position" filter improve per-trade
quality?

### What is "active position"?

A higher-TF combo has an active position = it is in **ENTRY or HOLD** state
(the combo's entry condition fired and hasn't exited yet). This is a binary
regime filter: active = favorable, not active = unfavorable.

### Test matrix

| Entry combo TF | Regime combo TF | Regime combo source |
|---|---|---|
| 1D | 1W | Best surviving 1W combo from 20.1 |
| 1W | 2W | Best surviving 2W combo from 20.1 |
| 2W | — | No regime test (2W is highest TF) |

### Algorithm

For each 1D / 1W surviving combo from 20.1:

1. **Flat variant**: Run combo on 4 folds with no regime filter (already done)
2. **Regime variant**: For each test fold:
   a. Run the regime-TF combo to get its entry/hold/exit signals per symbol
   b. Re-run the entry-TF combo but only allow entries when the regime combo
      has an active position
   c. Record: trades, HR, avg_ret

### Decision rule

Keep regime if:
```
regime_hr × regime_avg_ret > flat_hr × flat_avg_ret
AND regime_trades ≥ frequency_target
```

Where frequency_target:
- Daily trading (1D entry): ≥ 2 trades/day across 300 stocks
- Swing trading (1W entry): ≥ 2 trades/week across 300 stocks

### Output

- `phase20_regime_comparison.json` — flat vs regime metrics per combo
- Winner flag per combo

---

## Phase 20.3 — P19 MTF Overlay Test

**Goal:** Does adding 4H MTF confirmation (from P19) improve quality?

Only tested for **1D entry combos** (daily trading), where intra-day
confirmation is actionable.

### Algorithm

1. Load P19 validated MTF combos for (1D gate + 4H confirm)
2. For each surviving 1D combo from 20.2:
   a. **Base variant**: Best result from 20.2 (flat or regime-filtered)
   b. **+ MTF variant**: Also require P19 4H confirmation to be active
3. Run both on 4 folds

### Decision rule

Keep MTF if:
```
mtf_hr × mtf_avg_ret > base_hr × base_avg_ret
AND mtf_trades ≥ 2/day across 300 stocks
```

### Output

- `phase20_mtf_comparison.json`

---

## Phase 20.4 — Exit Mode Optimization

**Goal:** Find the best exit mode for each winning combo across all folds.

P18 tested 5 exit modes but only on a single IS/OOS split. We re-test across
all 4 folds to find the exit mode that works in every regime.

### Algorithm

For each winning combo from 20.3:

Test all 5 exit modes + TMK grid across all 4 folds:
- `standard`, `trend_anchor`, `momentum_governed`, `risk_priority`, `adaptive`
- TMK grid: (2,20,3), (2,20,4), (4,40,4), (4,48,4), (6,48,4)

### Selection criteria

Pick exit mode that maximizes:
```
mean(avg_ret across folds)
```
subject to:
- avg_hold within frequency band:
  - Daily: 5–15 bars
  - Swing: 3–8 bars (weekly)
  - Position: 2–6 bars (bi-weekly)
- worst_trade > −20% in every fold

### Output

- `phase20_exit_optimization.json`
- `phase20_best_exits.json` — selected exit mode per combo

---

## Phase 20.5 — Baseline Comparison

**Goal:** Prove the strategies deliver alpha above random/naive approaches.

### Baselines tested

| Baseline | Description |
|---|---|
| Random entry | Enter on random days (same number of trades), use same exit mode and hold duration |
| Buy and hold | Buy at start of each test window, sell at end |
| Entry only, random exit | Same entry signal, but exit randomly after avg_hold bars |

### Algorithm

For each winning combo × each fold:

1. Run the combo normally → record HR × avg_ret
2. Run 3 baselines → record their HR × avg_ret
3. Combo must beat ALL 3 baselines in at least 3 of 4 folds

### Output

- `phase20_baseline_comparison.json`

---

## Phase 20.6 — Behavior Classification & Strategy Config

**Goal:** Label each winner by its actual trading behavior and assemble into
`strategy_config.json`.

### Classification logic

| Behavior | Detection criteria |
|---|---|
| **Dip Buy** | Contains ≥1 bearish-polarity momentum/MR KPI alongside a bullish trend KPI. Higher frequency, shorter holds. Mean-reversion mechanics. |
| **Trend Entry** | All KPIs same polarity (all bull or all bear). Fires during directional moves. Lower frequency, longer holds. |
| **Breakout** | Contains BB30, SQZ, or NW Envelope KPIs. Fires at range boundaries. |

If a combo doesn't clearly match one behavior, it is labeled by its dominant
KPI category.

### Assembly

For each surviving combo, create a strategy entry:

```json
{
  "strategy_id": "dip_buy_daily",
  "combo_kpis": ["Nadaraya-Watson Smoother", "ADX & DI", "CM_Ult_MacD_MFT"],
  "combo_pols": [1, -1, -1],
  "entry_tf": "1D",
  "regime_tf": "1W",
  "regime_combo_kpis": [...],
  "regime_combo_pols": [...],
  "regime_active": true,
  "mtf_overlay": false,
  "exit_mode": "momentum_governed",
  "exit_params": {"T": 4, "M": 40, "K": 4.0},
  "gate": "none",
  "delay": 1,
  "validation": {
    "F1": {"trades": 145, "hr": 88.2, "avg_ret": 5.1, "worst": -12.3},
    "F2": {"trades": 98,  "hr": 82.1, "avg_ret": 3.9, "worst": -18.5},
    "F3": {"trades": 134, "hr": 91.0, "avg_ret": 5.8, "worst": -9.1},
    "F4": {"trades": 67,  "hr": 85.3, "avg_ret": 4.5, "worst": -15.2}
  },
  "beats_baseline": true,
  "behavior": "dip_buy",
  "trading_frequency": "daily",
  "expected_signals_per_day": 3.5
}
```

### Output

- `strategy_config.json` — final strategy definitions
- `PHASE20_REPORT.md` — full report with tables, comparisons, recommendations

---

## Memory Management

Following `MEMORY_GUIDELINES.md`:

1. Process ONE timeframe at a time
2. Delete raw DataFrames after precompute (`del data; gc.collect()`)
3. Call `_check_memory()` before every `load_data()`
4. Memory threshold: 70%
5. For regime tests (20.2): load regime-TF and entry-TF sequentially, not together.
   Precompute regime signals first, store as lightweight arrays, free regime data,
   then load entry-TF data.

---

## Runtime Estimate

| Phase | Operations | Estimated time |
|---|---|---|
| 20.0 | JSON load + filter | < 1 min |
| 20.1 | ~60 combos × 4 folds × 300 stocks | ~30 min |
| 20.2 | ~40 combos × 2 variants × 4 folds | ~20 min |
| 20.3 | ~15 combos × 2 variants × 4 folds | ~10 min |
| 20.4 | ~20 combos × 5 exits × 5 TMK × 4 folds | ~40 min |
| 20.5 | ~20 combos × 3 baselines × 4 folds | ~15 min |
| 20.6 | Classification + report | < 1 min |
| **Total** | | **~2 hours** |

---

## Execution

```bash
cd /root/damiverse_apps/trading_app
nohup .venv/bin/python3 research/kpi_optimization/phase20_master.py \
  > research/kpi_optimization/phase20.log 2>&1 &
```

Monitor:
```bash
tail -50 research/kpi_optimization/phase20.log
```
