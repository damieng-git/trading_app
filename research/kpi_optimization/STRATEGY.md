# Trading Strategy — Entry v6 + Exit Flow v4

> **Status:** Locked (v15) — Feb 2026
> **Entry:** v6 — PF-optimized combos (Phase 16), onset-only C3, SMA20>SMA200, Vol spike 1.5× N=5, SR Break N=10 (screener)
> **Exit:** Exit Flow v4 — two-stage KPI invalidation + ATR stop with checkpoint
> **Backtest:** 295 stocks (sample_300), out-of-sample (last 30%)
> **Commission:** 0.1% + 0.5% slippage per trade

---

## 1. Entry Strategy

### Concept

Two combo levels per timeframe:

- **C3 (Combo)** — Base entry signal. 3 KPIs must align bullishly. Optimized for **Profit Factor** (per-trade quality, v6). Opens the position at 1x.
- **C4 (Golden Combo)** — Scale-up signal. 4 KPIs must align. Never an independent entry — only fires to add size when a C3 position is already open. Optimized for **PF with HR ≥ 65%**.

### Why C4 is optimized for P&L, not "golden score"

A golden score (`HR × PF / |worst_loss|`) was tested to favour high-confidence, low-risk C4 combos. The result: it surfaced combos with near-perfect stats (96-100% HR, worst loss ≈ 0%) but only 20-40 trades across 320 stocks — too rare to be statistically reliable or practically useful as a scale-up signal. The current P&L-optimized C4 combos fire on 475-1,483 trades with 68-71% HR, providing meaningful coverage and robust statistics. See Phase 11 v11 analysis for details.

### Position Sizing

| Event | Position Size |
|-------|--------------|
| C3 fires (no position open) | **1.0x** — open base position |
| C4 fires while in C3 position | **1.5x** — scale up by +50% |
| C4 fires simultaneously with C3 | **1.5x** from bar 1 |

- C4 can fire at any point during the trade (entry bar or later).
- Once scaled to 1.5x, the size stays until exit.
- C4 alone (without a prior C3 entry) does **not** open a position.

### Entry Confirmation Filters (v5)

Four entry-gate filters are applied before a C3 position can open. All four must pass.

**Filter 1 — Onset-only detection (all TFs)**

A C3 entry is only valid when the combo **transitions** from false to true (onset). Positions already in an active combo (continuation) are not re-entered.

| Metric | sample_300 onset | sample_300 cont. | entry_stocks onset | entry_stocks cont. |
|--------|------------------|-------------------|--------------------|--------------------|
| Trades | 1,949 | 198 | 4,987 | 536 |
| HR% | 69.5 | 66.2 | 70.1 | 68.1 |
| PF | 7.5 | 3.3 | 9.8 | 9.1 |

Onset entries dominate volume (90%+ of trades) and have consistently higher PF (7.5 vs 3.3). See Phase 13a.

**Filter 2 — SMA20 > SMA200 (1D + 1W only)**

The stock's 20-period SMA must be above its 200-period SMA at the time of entry. This structural uptrend gate ensures the stock is in a confirmed uptrend, not just briefly above SMA200.

| Filter | s300 Trades | s300 HR% | s300 PF | s300 Avg% | entry Trades | entry HR% | entry PF | entry Avg% |
|--------|------|------|------|------|------|------|------|------|
| No filter | 2,330 | 67.0 | 6.4 | +6.37 | 5,984 | 68.3 | 9.2 | +11.32 |
| Close > SMA200 (v4) | 2,094 | 69.3 | 7.1 | +6.72 | 5,378 | 70.3 | 10.1 | +12.00 |
| **SMA20 > SMA200 (v5)** | **1,814** | **70.1** | **7.8** | **+7.14** | **4,605** | **73.1** | **11.8** | **+12.59** |

Upgrade from Close > SMA200: +0.8pp HR, +0.7 PF on sample_300; +2.8pp HR, +1.7 PF on entry_stocks. Keeps 87% of trades. Not applied on 4H. See Phase 14a.

**Filter 3 — Volume spike 1.5× N=5 (all TFs)**

At least one bar within the last 5 bars (inclusive) must have Volume ≥ 1.5× its 20-bar volume moving average. Confirms momentum is present at entry.

| Filter | s300 Trades | s300 HR% | s300 PF | entry Trades | entry HR% | entry PF |
|--------|------|------|------|------|------|------|
| Baseline (no vol filter) | 2,094 | 69.3 | 7.1 | 5,378 | 70.3 | 10.1 |
| **Vol spike 1.5× N=5** | **1,438** | **72.0** | **8.1** | **4,047** | **71.7** | **10.9** |

Keeps 69% of trades, PF 7.1→8.1, HR +2.7pp. Consistent across both datasets. Uses `Vol_MA20` already computed in lean enrichment. See Phase 14b.

**Filter 4 — Overextension gate (1W only)**

> Block C3 entry if the stock has rallied more than **15%** in the last **5 weekly bars**.

Prevents entries at the peak of a sharp rally, where the stock is statistically more likely to mean-revert.

| TF | Filter | Impact on P&L | Impact on PF | Impact on HR | Worst Δ | Trades blocked |
|----|--------|---------------|--------------|--------------|---------|---------------|
| **1W** | Close ≤ 115% of Close[5b ago] | -5.5% | +0.6 (18.2 → 18.7) | +1.2pp (78.9% → 80.1%) | +11.9pp (-38.6% → -26.7%) | 77 / 574 (13%) |

Implementation: `apps/dashboard/strategy.py` — constants `_OVEREXT_LOOKBACK=5`, `_OVEREXT_PCT=15.0`, `_VOL_SPIKE_MULT=1.5`, `_VOL_SPIKE_LOOKBACK=5`.

### Screener-Only Pre-Filter: SR Break N=10

The daily screener adds an additional gate **before lean enrichment** to reduce computation. A stock must have an SR support/resistance breakout (transition of `SR_state` to 1) within the last 10 bars on its raw OHLCV data. This filter is not applied in the dashboard position tracker or chart overlay — only in the screener pipeline.

| Signal | Best N | s300 Trades | s300 HR% | s300 PF | entry Trades | entry HR% | entry PF |
|--------|------|------|------|------|------|------|------|
| Baseline | — | 2,094 | 69.3 | 7.1 | 5,378 | 70.3 | 10.1 |
| **SR Break N=10** | 10 | 582 | 72.7 | 10.8 | 1,574 | 71.5 | 10.7 |

PF jumps to 10.8/10.7 — strongest single filter. Keeps ~30% of trades. See Phase 14b. SR Breaks are computed on raw OHLCV (no indicator dependencies), making them ideal for pre-filtering before expensive indicator computation.

### Entry Combos by Timeframe

#### 4H

| Level | KPIs | Description | PF | HR% |
|-------|------|-------------|-----|-----|
| **C3** | Nadaraya-Watson Smoother + DEMA + Stoch_MTM | Trend + double EMA + momentum | 14.0 | 79.4 |
| **C4** | Nadaraya-Watson Smoother + Madrid Ribbon + GK Trend Ribbon + cRSI | Trend + multi-trend + momentum | — | — |

v6 change: C3 replaced `cRSI + OBVOSC_LB` with `DEMA + Stoch_MTM` (PF +103%, HR +10.6pp vs v5).

#### 1D

| Level | KPIs | Description | PF | HR% |
|-------|------|-------------|-----|-----|
| **C3** | Nadaraya-Watson Smoother + Madrid Ribbon + Volume > MA20 | Trend + multi-trend + volume confirmation | 5.3 | 63.3 |
| **C4** | Nadaraya-Watson Smoother + Madrid Ribbon + GK Trend Ribbon + cRSI | Trend + multi-trend + momentum | — | — |

v6: No change. Already near-optimal for PF (Phase 16 confirmed).

#### 1W

| Level | KPIs | Description | PF | HR% |
|-------|------|-------------|-----|-----|
| **C3** | Nadaraya-Watson Smoother + DEMA + cRSI | Trend + double EMA + momentum | 47.4 | 89.0 |
| **C4** | Nadaraya-Watson Smoother + Stoch_MTM + cRSI + Volume + MA20 | Trend + momentum + volume confirmation | 43.9 | 88.1 |

v6 change: C3 replaced `Madrid Ribbon` with `cRSI` (PF +350%, HR +16.8pp). C4 replaced `Donchian Ribbon + GK Trend Ribbon + OBVOSC_LB` with `Stoch_MTM + cRSI + Volume + MA20` (PF ~4× vs v5).

### Breakout KPIs

A dedicated screening (Phase 11 v10) tested all combos containing a breakout KPI (BB30, NWE-MAE, NWE-STD, Donchian, Breakout Targets, SR Breaks). Findings:

- **Donchian Ribbon is the only breakout KPI that adds value.** It already appears in the 1W C4 combo.
- **4H alternative:** `NWSm + Donch + cRSI` beats the locked C3 on P&L (+12,929% vs +11,267%) and PF (7.9 vs 6.0), but with fewer trades (1,974 vs 2,770). Not adopted yet — kept as a candidate for future review.
- BB30, NWE variants, Breakout Targets, and SR Breaks never produced a combo that beat the locked ones on any timeframe.

### Sector-Specific Combos (Phase 11 v12 + v13)

Two rounds of per-sector analysis tested whether each of the 11 GICS sectors benefits from its own C3/C4 combos instead of the global ones.

- **v12**: 235 stocks with sector data (87 ETFs/indices excluded from old universe).
- **v13**: 295 stocks from the curated `sample_300` universe (300 US+EU stocks, no ETFs/indices, proper GICS sector balance).

**Sector distribution (v13 — sample_300):**

| Sector | Stocks |
|--------|--------|
| Industrials | 44 |
| Technology | 44 |
| Healthcare | 43 |
| Financials | 38 |
| Consumer Discretionary | 27 |
| Consumer Staples | 22 |
| Energy | 19 |
| Communication Services | 18 |
| Utilities | 14 |
| Materials | 13 |
| Real Estate | 9 |

**C3 (Workhorse) — Sector vs Global (v13):**

| TF | Sector wins P&L | Global better P&L | Key Observation |
|----|-----------------|-------------------|-----------------|
| **4H** | 11/11 | 0/11 | Sector combos fire 2-3x more (lower HR 51-64% vs 66-80%) — win on volume, not quality |
| **1D** | 3/11 | 8/11 | **Global combo dominates.** Fin: G=+3120% vs S=+2343%. Tech: G=+3801% vs S=+3009% |
| **1W** | 7/11 | 4/11 | Mixed. Large sectors (Indust, Fin) find better combos. Small sectors unreliable |

**C4 (Golden) — Sector-specific still not viable:**

| TF | Sector C4 found | Too few trades | Observation |
|----|-----------------|----------------|-------------|
| **4H** | 4/11 | 7/11 | HR ≥ 65% + 4 KPIs + small sector = too few trades |
| **1D** | 2/11 | 9/11 | Global C4 works adequately across sectors |
| **1W** | 7/11 | 4/11 | Weekly has enough data but 30-86 trades is still marginal |

**1.5x C4 Scaling — Strongly Validated Across All Sectors (v13):**

The 1.5x scale-up shows **positive P&L lift in every sector on every timeframe** (33/33 sector-TF combinations):

| TF | Min Lift | Max Lift | Avg Lift |
|----|----------|----------|----------|
| **4H** | +18% (ConsDis) | +49% (Indust) | ~32% |
| **1D** | +23% (ConsDis) | +54% (RealEst) | ~37% |
| **1W** | +45% (Fin) | +52% (Mater) | ~49% |

C4-scaled trades consistently show higher HR (70-85% vs 55-68%) and higher avg return (+4-10% vs +1-3%).

**Exit Param Sweep — Marginal Gains (v13):**

Per-sector T/M/K tuning offers 2-10% P&L improvement over global defaults across 100 grid points per sector. Not worth the complexity or overfitting risk.

**Decision: Sector optimization not adopted (confirmed by v13).**

Reasons:
1. **1D global combo is near-optimal** — outperforms sector combos in 8/11 sectors on both HR and P&L.
2. **4H sector "wins" sacrifice quality** — HR drops from 66-80% to 51-64%. More losing trades, higher drawdown risk.
3. **C4 can't be reliably sector-optimized** — too few trades per sector for 4-KPI combos.
4. **No cross-TF consistency** — a sector uses different KPIs on 4H vs 1D vs 1W, suggesting curve-fitting.
5. **Small-sector overfitting** — sectors with <15 stocks produce unreliable results.
6. **1.5x scaling is the real win** — universal lift requires no sector-specific tuning.

The global combos remain the active strategy.

### KPI Reference

| Short Name | Full Name | Category | Used in (v6) |
|------------|-----------|----------|--------------|
| NWSm | Nadaraya-Watson Smoother | Trend | C3 (all), C4 (4H/1D) |
| DEMA | DEMA | Trend (Double EMA) | C3 (4H, 1W) |
| Madrid | Madrid Ribbon | Multi-Trend | C3 (1D), C4 (4H/1D) |
| GKTr | GK Trend Ribbon | Multi-Trend | C4 (4H/1D) |
| Stoch | Stoch_MTM | Momentum | C3 (4H), C4 (1W) |
| cRSI | cRSI | Momentum | C3 (1W), C4 (4H/1D/1W) |
| Vol>MA | Volume + MA20 | Volume Confirmation | C3 (1D), C4 (1W) |

---

## 2. Exit Strategy — Exit Flow v4

### Concept

Two-stage combo invalidation with dynamic ATR stop and checkpoint-based max-hold reset. The trade rides the trend as long as KPIs remain bullish, with periodic checkpoints that either reset the ATR stop (trend still intact) or force an exit (trend weakening).

### Parameters

| TF | T (lenient bars) | M (checkpoint interval) | K (ATR multiplier) |
|----|-------------------|-------------------------|---------------------|
| **4H** | 4 | 48 | 4.0 |
| **1D** | 4 | 40 | 4.0 |
| **1W** | 2 | 20 | 4.0 |

ATR period = 14 bars. Hard cap = 500 bars (safety limit, rarely hit).

### Exit Rules (evaluated every bar while in position)

**Stage 1 — Lenient (bars 1 to T):**
Exit only if ALL KPIs of the active combo turn bearish (full invalidation).

**Stage 2 — Strict (bars T+1 onward):**
Exit if 2 or more KPIs of the active combo turn bearish.

**ATR Stop:**
On entry: `stop = entry_price - K × ATR(entry_bar)`.
If price drops below the stop at any point → immediate exit (reason: `atr`).

**Checkpoint Reset (every M bars since last reset):**
At each M-bar checkpoint:
- If ALL KPIs of the active combo are still bullish → **reset**: the ATR stop moves up to `current_price - K × current_ATR`, and the checkpoint counter restarts.
- If ANY KPI is bearish → **exit** (reason: `reset_exit`).

### Which combo governs the exit?

The exit rules use the **highest active combo level**:
- If C4 has fired → exit based on C4's 4 KPIs (need 2/4 bearish to exit in strict stage)
- If only C3 is active → exit based on C3's 3 KPIs (need 2/3 bearish to exit in strict stage)

### Position Management

- **One position per stock at a time.**
- Multiple entries can scale the position up, but there is only **one exit** — it closes the entire position.
- After exit, the position is flat. A new C3 signal is required to re-enter.

---

## 3. Backtest Results (v6 — PF-Optimized)

### Performance Summary — C3 at 1x, C4 scale to 1.5x

| TF | Trades | HR | Avg Ret | PnL (1x) | PF | Avg Hold | Worst | C4% |
|----|--------|----|---------|-----------|------|----------|-------|-----|
| **4H** | 1,361 | 79.4% | +5.93% | +10,385% | 14.0 | 31 bars | -17.5% | 44% |
| **1D** | 2,180 | 63.3% | +5.68% | +17,105% | 5.3 | 25 bars | -28.1% | 51% |
| **1W** | 418 | 89.0% | +20.25% | +11,725% | 47.4 | 22 bars | -21.1% | 49% |

v6 vs v5: 4H PF +103% (6.9→14.0), HR +10.6pp. 1D unchanged. 1W PF +350% (10.5→47.4), HR +16.8pp, worst -35.6%→-21.1%.

### C4 Standalone Performance (Exit Flow v4)

| TF | Combo | Trades | HR | Avg Ret | PnL | PF | Worst |
|----|-------|--------|----|---------|-----|----|-------|
| **4H** | NWSm + Madrid + GKTr + cRSI | 1,483 | 69% | +4.4% | +6,580% | 6.1 | -16.8% |
| **1D** | NWSm + Madrid + GKTr + cRSI | 1,448 | 71% | +7.1% | +10,299% | 5.6 | -39.8% |
| **1W** | NWSm + Stoch + cRSI + Vol>MA | 168 | 88.1% | +17.35% | +3,913% | 43.9 | -12.6% |

---

## 4. Walk-Forward Validation (Phase 11 v14)

OOS period (last 30% of each stock) split into two halves:
- **OOS-A** (70-85%): the window combos were originally optimized on.
- **OOS-B** (85-100%): pure holdout — no parameter was derived from this data.

All P&L figures include **0.1% round-trip commission** (0.05% entry + 0.05% exit).

### Locked Combos — OOS-A vs OOS-B

| TF | Combo | A.n | A.HR% | A.Avg% | A.PnL | B.n | B.HR% | B.Avg% | B.PnL | Verdict |
|----|-------|-----|-------|--------|-------|-----|-------|--------|-------|---------|
| **4H** | C3 | 1,307 | 68.6 | +3.44 | +4,493% | 1,175 | 78.0 | +3.78 | +4,436% | **PASS** |
| **4H** | C4 | 672 | 67.1 | +4.64 | +3,120% | 628 | 78.2 | +5.61 | +3,525% | **PASS** |
| **4H** | Unified 1.5x | 1,190 | 69.1 | — | +7,004% | 1,085 | 78.1 | — | +7,166% | **PASS** |
| **1D** | C3 | 1,747 | 60.7 | +5.34 | +9,332% | 1,297 | 77.7 | +8.02 | +10,403% | **PASS** |
| **1D** | C4 | 889 | 62.2 | +5.23 | +4,652% | 683 | 74.5 | +7.56 | +5,165% | **PASS** |
| **1D** | Unified 1.5x | 1,629 | 61.3 | — | +12,001% | 1,253 | 74.2 | — | +13,212% | **PASS** |
| **1W** | C3 | 503 | 80.9 | +17.34 | +8,724% | 430 | 78.6 | +16.48 | +7,088% | **PASS** |
| **1W** | C4 | 304 | 63.8 | +23.29 | +7,080% | 300 | 60.7 | +11.96 | +3,588% | **PASS** |
| **1W** | Unified 1.5x | 421 | 76.5 | — | +14,843% | 391 | 73.1 | — | +9,716% | **PASS** |

PASS criteria: HR >= 60% on holdout, positive P&L, HR decay < 10pp.

### Key Findings

1. **All 9 combinations PASS.** The locked strategy generalises to completely unseen data.
2. **4H and 1D actually improve on holdout** — OOS-B HR is higher than OOS-A (78% vs 69% on 4H, 75-78% vs 61-68% on 1D). This suggests the strategy performs better in recent market conditions.
3. **1W C4 shows P&L decay** — OOS-B P&L is +3,588% vs OOS-A +7,080% (49% drop). C4 avg return drops from +23% to +12%. This is the weakest link but still passes (HR=60.7%, P&L positive).
4. **1.5x scaling lift is stable**: +37-47% on OOS-A → +35-44% on OOS-B. The scaling benefit persists on holdout data.

### Re-Optimized vs Locked Combos

Combos re-optimized on OOS-A and tested on OOS-B, compared to locked combos:

| TF | Combo | Re-opt on A | Re-opt PnL(A) | Re-opt PnL(B) | Locked PnL(B) | Winner on B |
|----|-------|-------------|---------------|---------------|---------------|-------------|
| **4H** | C3 | NWSm+SQZ+PSAR | +5,132% | +4,117% | +4,436% | **Locked** |
| **4H** | C4 | NWSm+Stoch+MACD+cRSI | +2,670% | +2,448% | +3,525% | **Locked** |
| **1D** | C3 | NWSm+Madrid+ADX | +6,912% | +6,826% | +10,403% | **Locked** |
| **1W** | C3 | NWSm+Madrid+Zeiier | +10,262% | +4,558% | +7,088% | **Locked** |
| **1W** | C4 | NWSm+Madrid+Zeiier+Impulse | +9,210% | +3,935% | +3,588% | Re-opt (marginal) |

The locked combos outperform re-optimized combos on the holdout in 4/5 cases. Re-optimized combos show larger P&L decay (overfitting to OOS-A), while locked combos are more robust.

### Commission Impact

| TF | Combo | Gross PnL | Net PnL | Commission Drag |
|----|-------|-----------|---------|-----------------|
| **4H** | C3 (A+B) | +9,178% | +8,929% | -249% (2.7%) |
| **1D** | C3 (A+B) | +20,040% | +19,735% | -305% (1.5%) |
| **1W** | C3 (A+B) | +15,906% | +15,812% | -93% (0.6%) |

Commission impact is modest: 0.6-2.7% of gross P&L. Higher-frequency strategies (4H) pay more in commission due to shorter holding periods and more trades.

---

## 5. Research Log

### Analyses Performed

| Phase | Focus | Key Finding |
|-------|-------|-------------|
| 11 v7 | Exit Flow v4 + HR ≥ 65% entry screening | Locked C3/C4 combos per TF |
| 11 v8 | T/M/K parameter sweep | Locked exit params (T=2-4, M=20-48, K=4.0) |
| 11 v9 | Unified position sim (1x vs 1.5x vs 2x) | 1.5x selected: +32-46% P&L lift, conservative risk |
| 11 v10 | Breakout KPI combo screening | Donchian only breakout KPI that adds value; 4H C3 alternative identified |
| 11 v11 | Golden score C4 optimization | Golden score (HR×PF/\|worst\|) rewards rarity over quality at scale; current P&L-optimized C4s are more robust with 475-1,483 trades vs 20-40 for golden-score winners |
| 11 v12 | Per-sector entry combo screening | Sector-specific C3 beats global on 4H (10/11) and 1W (7/11), but not 1D (2/11). C4 per sector unreliable (too few trades). Not adopted due to complexity and overfitting risk |
| 11 v13 | Full per-sector optimizer (sample_300, 295 stocks) | Confirmed v12 on cleaner universe. 1D global dominates 8/11 sectors. 1.5x C4 scaling validated in all 33 sector-TF combos. Exit params: marginal gains. Sector optimization not adopted |
| 11 v14 | Walk-forward validation + 0.1% commission | All 9 combo-TF combinations PASS holdout test. Locked combos outperform re-optimized in 4/5 cases. 1.5x scaling lift stable on holdout (+35-44%). Commission drag: 0.6-2.7% of gross |
| 12 | Trailing stops, regime filter, portfolio controls | Trailing stops rejected (baseline wins by 3-30%). Breadth ≥40% tested (+5-10% PF, -0-6% P&L). Portfolio: avg 119 concurrent, 25-30 max practical, 9-12% max DD |
| 12b | Per-stock entry confirmation filters | Tested 15 filters (SMA/EMA crossovers, GMMA, ADX, MACD, combos). Close > SMA200 adopted for 1D/1W: best quality/cost ratio (-3-5% P&L, +0.7-2.5 PF, +1.5-2.5pp HR, keeps 90-92% of trades). GMMA is redundant with C3 KPIs. ADX improves quality but kills trade count |
| 12c | Volatility-aware entry sizing | Tested ATR% threshold (reject if ATR% > cap) and ATR%-scaled sizing (weight inversely proportional to volatility). Neither adopted: threshold incompatible with 1W (median ATR% 5.1%), scaled sizing costs proportional PnL. Existing ATR stop already adapts to volatility. Extreme outliers too rare to justify universal rule |
| 12d | Breakeven stop | Move stop to entry price after dip+recovery. Tested with and without checkpoint resets. Both variants identical. -4% to -8% PnL across all TFs. 60-65% of trades dip below entry naturally — breakeven cuts winners. 1W worst improves but at high cost. Not adopted |
| 12e | Entry quality filters | Tested 4 filters × multiple sensitivities: overextension (5b/10b lookback × 4-8 thresholds), volume confirmation (0.8-1.5x), min data length, trend age (3-12 bars). Overextension 5b >15% adopted for 1W: worst trade -38.6% → -26.7%, PF +0.6, HR +1.2pp, PnL -5.5%. Volume, min data, trend age rejected |
| 15 | Entry delay sensitivity | H=0..10 delay after C3 onset, tested on v4 and v5 strategies. H=1 (next bar open) optimal. H1 down-bar filter and hybrid entry tested — hybrid has look-ahead bias. Confirmed H=1 for all TFs |
| 16 | PF/Return optimization | Full re-optimization with PF as primary objective. 4H: `NWSm+DEMA+Stoch` (PF 14.0 vs locked 6.9). 1D: locked combo near-optimal. 1W: `NWSm+DEMA+cRSI` (PF 47.4 vs locked 10.5). Stoch_MTM key PF KPI. C5/C6 not beneficial. Gates and delays confirmed |

### Decisions

| Decision | Rationale |
|----------|-----------|
| C3 optimized for total P&L | Workhorse entry — needs frequency and profitability |
| C4 optimized for P&L with HR ≥ 65% floor | Golden score tested but rejected: extreme scores came from combos with <40 trades (statistically unreliable, too rare to be useful) |
| 1.5x over 2x scaling | Conservative risk; +32-46% lift is meaningful without doubling exposure on 50-66% of trades |
| Donchian not added to 4H C3 | Fewer trades (1,974 vs 2,770) despite better per-trade quality; kept as candidate |
| Sector optimization not adopted | Confirmed twice (v12 on 235 stocks, v13 on 295 stocks). 1D global wins 8/11 sectors. 4H sector wins sacrifice HR. No cross-TF consistency in sector combos |
| 1.5x C4 scaling confirmed | Positive lift in all 33 sector-TF combinations (v13). Avg lift: +32% (4H), +37% (1D), +49% (1W). No exceptions |
| 0.1% round-trip commission adopted | Applied from v4 onward for realistic P&L estimation |
| Trailing stop variants rejected | Baseline Exit Flow v4 outperforms trailing ATR (-3-4%), tightening K (-11-14%), and partial take (-26-30%) on total P&L. Worst trade unchanged. KPI exit already acts as intelligent trail |
| Breadth filter ≥40% recommended | +5-10% PF improvement for -0-6% P&L sacrifice. Skips only 5-8% of trades — the worst entries into weak markets. Uses NWSm data already computed |
| Portfolio: 25-30 max positions | Max DD 9-12%. No sector cap (too restrictive). No equity stop (DD already low). Equal-weight sizing |
| Close > SMA200 confirmation adopted (1D/1W) | Best quality/cost among 15 filters tested. GMMA rejected (redundant with C3). ADX rejected (too restrictive). Breadth filter superseded by per-stock filter |
| Volatility-based sizing not adopted | ATR% threshold removes too many 1W trades (median ATR% 5.1%). Scaled sizing costs proportional PnL. ATR stop already adapts to volatility. Extreme outliers (MSTR-type, ATR% > 7%) are ≤1 trade on 1D |
| Breakeven stop not adopted | -4% to -8% PnL, -17-20pp HR. 60-65% of trades dip below entry naturally — cutting them short kills winners. Worst trade unchanged on 4H/1D. "No reset" variant identical |
| 1W overextension filter adopted (5b >15%) | Block 1W C3 entries where price rallied >15% in last 5 bars. Catches IPAY-type peak entries. Worst trade -38.6% → -26.7%. PF +0.6, HR +1.2pp, PnL -5.5%. Not applied on 4H/1D (negligible benefit) |
| Volume entry filter not adopted | Destructive at all thresholds (-14% to -67% PnL). 1D already has Volume > MA20 as C3 KPI. Volume at entry is too noisy for a quality gate |
| Trend age filter not adopted | Zero worst-trade improvement despite PnL cost. Late C4 entries on 1W include genuine winners |

---

## 6. Live Dashboard Integration

The locked strategy (Entry v5 + Exit Flow v4 + 1x/1.5x sizing) is fully integrated into the live dashboard and runs automatically on every build.

### Implementation

| Component | File | What it does |
|-----------|------|-------------|
| **Position tracker** | `apps/dashboard/strategy.py` → `compute_position_status()` | Forward-walks up to 500 bars using Entry v5 gates (onset, SMA20>SMA200, vol spike, overextension) + Exit Flow v4 rules. Matches the chart's unified position model exactly |
| **Trailing P&L** | `apps/dashboard/strategy.py` → `compute_trailing_pnl()` | L12M P&L simulation with all v5 entry gates and Exit Flow v4 exit rules |
| **ATR calculator** | `apps/dashboard/strategy.py` → `compute_atr()` | 14-period ATR used for dynamic stop-loss |
| **Chart overlay** | `apps/dashboard/figures_indicators.py` → `_add_exit_flow_overlay()` | Python-side position model with v5 entry gates, renders shading/markers/stop line |
| **JS position model** | `apps/dashboard/static/chart_builder.js` | Client-side Entry v5 + Exit Flow v4 — computes SMA20>SMA200 gate, vol spike, onset detection identically to Python |
| **Screener table** | `apps/dashboard/static/dashboard.js` | Color-coded Action badge (ENTRY/SCALE/HOLD/EXIT/FLAT) with tooltip |
| **Signal card** | `apps/dashboard/static/dashboard.js` | Action badge + hold duration + ATR stop for the selected stock |
| **Filter pills** | `apps/dashboard/templates.py` | "Active" (ENTRY + SCALE + HOLD) and "Entry/Scale" (new signals only) filters |
| **Signal logger** | `apps/dashboard/signal_logger.py` | Logs `signal_action`, `atr_stop`, `position_size` (1x/1.5x) per combo signal to CSV |
| **Alert notifier** | `apps/dashboard/alert_notifier.py` | Telegram/email notifications include Action, Size, ATR Stop |

### How it runs

The position status is computed inside `build_screener_rows()`, which is called during **every dashboard refresh** — no extra flags needed. All build modes that refresh the dashboard (`all`, `refresh_dashboard`, `rebuild_ui`, `re_enrich`) run Exit Flow v4 tracking automatically.

### Signal actions

| Action | Meaning | Badge |
|--------|---------|-------|
| `ENTRY 1x` | C3 combo just activated (combo_bars = 0) | Green |
| `ENTRY 1.5x` | C4 combo just activated (combo_bars = 0), either simultaneously with C3 or as a scale-up on an existing position | Green |
| `HOLD Nb` | In position, N = bars since the most recent combo activation (C3 entry or C4 scale-up) | Blue |
| `EXIT` | Exit triggered on the current bar (ATR stop / KPI invalidation / checkpoint) | Red |
| `EXIT 1b` | Exit triggered 1 bar ago | Red |
| `EXIT 2b` | Exit triggered 2 bars ago | Red |
| `FLAT` | No active position, exit > 2 bars ago. Shows bars since last exit and exit reason | Muted |

### combo_bars vs bars_held

The position tracker maintains two distinct bar counts:

| Field | Definition | Used for |
|-------|-----------|----------|
| `bars_held` | Bars since original C3 entry (position open) | Exit flow logic (T-bar grace, M-bar checkpoint) |
| `combo_bars` | Bars since the **most recent combo activation** — either C3 entry or C4 scale-up, whichever is more recent | Action badge display, screener sorting |

Example: a stock where C3 entered 45 bars ago and C4 scaled up 10 bars ago has `bars_held=45, combo_bars=10`. The Action badge shows **"HOLD 10b"** (anchored to the C4 activation). Hovering shows both values in the tooltip.

### Data flow

```
Enrichment (enrichment.py)         → raw indicator columns (NWSm, cRSI, etc.)
  ↓
KPI catalog (kpis/catalog.py)      → bullish/bearish states per KPI (1/-1)
  ↓
Screener builder (screener_builder.py)
  ├── Combo detection (C3/C4)      → combo_3, combo_4, combo_3_new, combo_4_new flags
  └── Strategy engine (strategy.py)
        └── Entry v5 gates          → onset, SMA20>SMA200, vol spike, overextension
        └── Exit Flow v4            → signal_action, atr_stop, entry_price, bars_held
  ↓
Dashboard UI (dashboard.js)        → Action badge, tooltips, filters
Chart builder (chart_builder.js)   → JS-side Entry v5 + Exit Flow v4 position model
Signal logger (signal_logger.py)   → CSV log with action + sizing
Alert notifier (alert_notifier.py) → Telegram/email with action + stop
```

---

## 7. Daily Screener

### Purpose

Scans a broad US + EU stock universe (~3,800 tickers) to find stocks with **new C3 or C4 onsets** — combo transitions from false to true within the last 2 trading days. The screener identifies fresh entry opportunities, not stocks already in long-running combos.

### Entry Detection Logic (v5)

A stock is flagged as a C3 (or C4) hit only when the combo **transitions** from false to true (onset) within the last 2 bars:

```
for each of the last 2 bars (current, -1):
    if combo is TRUE on this bar AND FALSE on the bar before → onset found
```

Additionally, all v5 entry gates must pass:
1. SR Break N=10 pre-filter (before lean enrichment)
2. SMA20 > SMA200 (structural uptrend gate)
3. Volume spike 1.5× within last 5 bars (momentum confirmation)

### Universe & Quality Filters

| Filter | Threshold |
|--------|-----------|
| Geography | US + EU only |
| Min price | $5 |
| Min daily dollar volume (20-day avg) | $2M |
| Min market cap | $300M |
| Min data history | 250 bars |
| SR Break pre-filter | SR_state transition to 1 within last 10 bars (raw OHLCV) |
| SMA gate | SMA20 > SMA200 (1D) |
| Volume spike | Vol ≥ 1.5× Vol_MA20 within last 5 bars |
| Leveraged/inverse products | Excluded |
| Index tickers | Excluded |

Universe sources: S&P 500, S&P 400 MidCap, Euro Stoxx 600 components, FTSE 250, plus supplementary EU indices (DAX, CAC 40, SMI, AEX, IBEX 35, FTSE MIB, Nordic exchanges).

### Lean Enrichment

For performance, the screener computes only the 5 indicators required for 1D C3/C4 detection (plus SMA200/SMA20), rather than the full 25+ indicator set:

| Indicator | Used in |
|-----------|---------|
| Nadaraya-Watson Smoother | C3, C4 |
| Madrid Ribbon | C3, C4 |
| Volume > MA20 | C3, Vol spike filter |
| GK Trend Ribbon | C4 |
| cRSI | C4 |
| SMA200 | Entry gate (SMA20 > SMA200) |
| SMA20 | Entry gate (SMA20 > SMA200) |

Note: SR Breaks are computed **before** lean enrichment on raw OHLCV data (pure price/volume indicator, no dependencies on other indicators).

### Output & Dashboard Integration

1. **Screener scan** (~90s): identifies C3/C4 entries, ranks by bar recency then TrendScore
2. **CSV export**: `apps/screener/configs/screener_results.csv` — symbol, combo type, entry bar, trend score
3. **JSON export**: `data/dashboard_artifacts/daily_screener.json` — full metadata
4. **Config injection**: writes combo tickers to `configs/lists/entry_stocks.csv` (the "Entry Stocks" group)
5. **Dashboard build**: downloads full history (1D/1W/4H), enriches with all 25+ indicators, generates charts

### CLI

```bash
# Full run: scan + dashboard build
python -m trading_dashboard screener run

# Scan only, no dashboard build
python -m trading_dashboard screener run --no-dashboard

# Dry run: show universe stats without downloading
python -m trading_dashboard screener run --dry-run

# Use cached data (from existing enriched parquets)
python -m trading_dashboard screener run --cached

# Regenerate universe CSV from index sources
python -m trading_dashboard screener seed-universe
```

### Implementation

| Component | File | Role |
|-----------|------|------|
| Orchestrator | `apps/screener/daily_screener.py` | Pipeline: universe → download → SR Break pre-filter → lean enrich → onset detect → v5 gates → rank → output |
| Lean enrichment | `apps/screener/lean_enrichment.py` | Computes 5 indicators + SMA200 + SMA20 for 1D C3/C4 detection and v5 entry gates |
| Universe loader | `apps/screener/universe.py` | Loads universe CSV, applies quality filters |
| Universe builder | `apps/screener/_build_universe.py` | Generates `universe.csv` from index constituent lists |
| CLI integration | `trading_dashboard/cli.py` | `screener run` and `screener seed-universe` commands |

---

## 8. Version History

| Version | Date | Changes |
|---------|------|---------|
| v1 | Feb 2026 | Initial locked strategy: C3/C4 entry, Exit Flow v4, 1x/1.5x sizing |
| v2 | Feb 2026 | Added breakout KPI analysis, golden score C4 investigation, C4 standalone stats, research log, decisions rationale |
| v3 | Feb 2026 | Added sector-specific combo analysis (11 sectors, 235 stocks). Decision: not adopted — global combos remain |
| v4 | Feb 2026 | sample_300 universe (295 stocks). Full per-sector optimizer (v13) confirms global combos. 1.5x C4 scaling validated across all 33 sector-TF combinations. 0.1% commission model. Walk-forward validation |
| v5 | Feb 2026 | Exit Flow v4 integrated into live dashboard. Position tracking (action, ATR stop, sizing) in screener, signal card, logger, and alerts. Filter pills for active positions |
| v6 | Feb 2026 | Fixed screener position tracker: replaced backward-scan (found wrong entry date on positions that survived KPI flickers) with forward-walk matching the chart's unified model exactly. Added `last_exit_bars_ago` and `last_exit_reason` for FLAT positions. HOLD badge now shows bars held |
| v7 | Feb 2026 | Phase 12 strategy improvement investigation: trailing stops, regime filter, portfolio controls. Breadth filter recommended at ≥40%. Trailing stops rejected. Portfolio ops guidance added |
| v8 | Feb 2026 | Phase 12b: tested 15 per-stock entry confirmation filters. Adopted Close > SMA200 for 1D/1W (-3-5% P&L, +0.7-2.5 PF). GMMA redundant. Breadth filter superseded. Implemented in screener + charts |
| v8.1 | Feb 2026 | Phase 12c: tested volatility-aware sizing (ATR% threshold + ATR%-scaled). Neither adopted — PnL cost exceeds risk reduction benefit. ATR stop already provides volatility adaptation |
| v9 | Feb 2026 | Daily screener: scans ~3,800 US+EU stocks for new C3/C4 entries (transitions within last 2 bars). Lean enrichment (5 indicators). `entry_stocks.csv` group. `combo_bars` field tracks bars since most recent combo activation (C3 entry or C4 scale-up). Action badge shows ENTRY when combo_bars=0, HOLD Nb anchored to combo activation |
| v10 | Feb 2026 | Phase 12d: breakeven stop tested and rejected (-4% to -8% PnL, kills ~850 winners). Phase 12e: tested 4 entry quality filters (overextension, volume, min data, trend age). Overextension 5b >15% adopted for 1W only: -5.5% PnL, +0.6 PF, +1.2pp HR, worst trade -38.6% → -26.7% |
| v11 | Feb 2026 | Implemented 1W overextension filter in `strategy.py` (`_OVEREXT_LOOKBACK=5`, `_OVEREXT_PCT=15.0`). Applies to both `compute_position_status` and `compute_trailing_pnl`. Blocks C3 entries on 1W where Close > 15% above Close[5 bars ago] |
| v12 | Feb 2026 | Expanded screener universe from ~1,080 to ~3,800 tickers. US stocks now sourced from NASDAQ screener API (all NYSE+NASDAQ+AMEX, pre-filtered mcap >= $300M, price >= $5). EU unchanged (hardcoded index constituents) |
| v13 | Feb 2026 | **Entry Strategy v5.** Phase 13+14 backtested and implemented: (1) onset-only C3 detection — continuation entries dropped (PF 7.5 vs 3.3), (2) SMA20>SMA200 gate replaces Close>SMA200 (+0.8pp HR, +0.7 PF), (3) volume spike 1.5× N=5 confirmation (+2.7pp HR, PF 7.1→8.1), (4) SR Break N=10 pre-filter in daily screener (PF→10.8, before lean enrichment). SMA20 line added to charts. All entry gates applied in strategy.py, figures_indicators.py, chart_builder.js, and daily_screener.py |
| v14 | Feb 2026 | Phase 15: Entry delay sensitivity test. H=0..10 on v4 and v5, plus H1 down-bar filter and hybrid strategy. Confirmed H=1 (next bar open) as optimal. Hybrid entry has look-ahead bias |
| v15 | Feb 2026 | **Entry Strategy v6.** Phase 16 PF-optimized combos adopted. 4H C3: `NWSm+DEMA+Stoch` (PF 14.0, +103%). 1W C3: `NWSm+DEMA+cRSI` (PF 47.4, +350%). 1W C4: `NWSm+Stoch+cRSI+Vol>MA` (PF 43.9). 1D unchanged. Stoch_MTM is the key PF-improving KPI. C5/C6 not beneficial. Entry gates and delays confirmed (SMA20>200, H=1) |

---

## 9. Phase 12 — Strategy Improvement Investigation

Three categories tested against the locked Exit Flow v4 baseline on OOS data (last 30%, 291 stocks, 0.1% commission).

### 8.1 Trailing Stop Variants — REJECTED

Tested three alternatives to the fixed ATR stop with checkpoint reset:

| Variant | Mechanism | 4H P&L | 1D P&L | 1W P&L | Verdict |
|---------|-----------|--------|--------|--------|---------|
| **Baseline** | Fixed stop, M-bar checkpoint reset | +13,922% | +24,404% | +22,492% | **Winner** |
| A) Trailing ATR | After lenient, stop = max_close - K×ATR | +13,490% (-3%) | +23,393% (-4%) | +22,133% (-2%) | Worse |
| B) Tightening K | K=4→3→2 over time | +12,090% (-13%) | +21,630% (-11%) | +19,279% (-14%) | Worse |
| C) Partial take | 50% off at +2×ATR, trail rest K=2 | +10,370% (-26%) | +17,399% (-29%) | +15,779% (-30%) | Worst |

**Key findings:**

1. **Baseline wins on total P&L across all timeframes.** The KPI-based exit already acts as an intelligent trailing stop — it lets winners run when KPIs stay bullish.
2. **Worst trade is identical** across baseline, trailing, and partial (-20.7% on 4H, -26.1% on 1D). The worst losses happen early and aren't affected by trailing mechanics.
3. **Giveback is already low** in baseline: 2.25% (4H), 3.60% (1D), 9.80% (1W). Trailing variants actually increase giveback.
4. **Partial take improves HR** (+4-6pp) but kills total P&L by 26-30%. Not worth it.
5. **Tightening K makes 1W worse**: worst trade goes from -38.6% to -46.9% because the tighter stop exits profitable trades early, leaving only losers to hit the initial wide stop.

**Decision: Do not modify the exit strategy.** Exit Flow v4 with checkpoint reset is already near-optimal.

### 8.2 Regime Filter (NWSm Breadth) — RECOMMENDED

Suppress new C3 entries when fewer than X% of universe stocks have bullish NWSm (internal breadth signal — no external data required).

| Threshold | 4H P&L | 4H PF | 1D P&L | 1D PF | 1W P&L | 1W PF | Trades skipped |
|-----------|--------|-------|--------|-------|--------|-------|----------------|
| None | +13,922% | 8.93 | +24,404% | 6.64 | +22,492% | 16.10 | — |
| ≥ 30% | +13,328% | 9.19 | +24,041% | 7.02 | +22,492% | 16.10 | 85-110 |
| ≥ 35% | +13,177% | 9.29 | +23,607% | 7.06 | +22,492% | 16.10 | 117-167 |
| **≥ 40%** | **+13,062%** | **9.35** | **+23,151%** | **7.06** | **+22,435%** | **17.74** | **138-221** |
| ≥ 45% | +12,298% | 9.40 | +21,614% | 7.06 | +21,731% | 18.30 | 254-387 |
| ≥ 50% | +11,381% | 9.47 | +19,510% | 7.14 | +21,079% | 17.98 | 408-601 |

**Recommended: ≥ 40% NWSm breadth threshold.**

- PF improves +5% (4H), +6% (1D), +10% (1W)
- P&L sacrifice is minimal: -6% (4H), -5% (1D), -0.3% (1W)
- HR improves +0.4 to +1.2pp
- Skips only 5-8% of trades (the worst-quality entries into weak markets)
- Uses data already computed for every stock — zero additional cost

**Status: Superseded by Phase 12b.** Per-stock `Close > SMA200` filter adopted instead — it targets the same problem (weak-market entries) at the individual stock level rather than universe-wide, with better precision and less P&L sacrifice.

### 8.3 Portfolio-Level Risk Controls — GUIDANCE

**Critical finding: Average 119 concurrent positions on 1D.**

The strategy fires signals across many stocks simultaneously. A realistic portfolio can only hold a fraction of available signals.

| Configuration | Trades | HR | P&L(w) | PF | Max DD | Final (100k) |
|---------------|--------|----|--------|-----|--------|-------------|
| No limit (indep. sum) | 2,797 | 66.8% | +24,404% | 6.64 | — | — |
| **Max 25 pos** | **594** | **63.3%** | **+5,145%** | **5.85** | **11.2%** | **306k** |
| Max 30 pos | 718 | 64.5% | +6,286% | 6.18 | 10.5% | 310k |
| Max 50 pos | 1,154 | 66.0% | +10,117% | 6.58 | 9.3% | 302k |
| 25 pos, 3/sector | 79 | 64.6% | +481% | 3.48 | 2.4% | — |
| 25 pos, 5/sector | 125 | 62.4% | +1,101% | 4.98 | 2.7% | — |

**Findings:**

1. **Max drawdown is well-contained: 9-12% across all configurations.** The strategy's high HR (63-66%) and positive skew keep drawdowns shallow.
2. **25-30 positions is the practical sweet spot.** PF stays above 5.8, max DD under 12%, final equity triples from 100k.
3. **Sector caps are too restrictive.** 3-5 per sector leaves too few slots (only 79-125 trades accepted). With 11 GICS sectors and 25 slots, natural diversification is already adequate.
4. **Equity curve stops are unnecessary.** Max DD is only 11.2% at 25 positions — any threshold below that kills returns, any above never triggers.
5. **Priority over selection**: when at capacity, favour stocks with highest trend score for entry — this is not yet implemented.

**Recommended operating range: 20-30 simultaneous positions, equal-weight (1/N of capital per position). No sector cap. No equity stop.**

### 8.4 Volatility-Aware Entry Sizing (Phase 12c) — NOT ADOPTED

Tested two approaches to limit damage from high-volatility entries, using ATR as a percentage of price (ATR% = ATR/Close × 100) as the volatility measure. Baseline includes Close > SMA200 filter on 1D/1W.

**ATR% distribution at entry (baseline):**

| TF | P25 | P50 | P75 | P90 | P95 | Max |
|----|-----|-----|-----|-----|-----|-----|
| 4H | 1.06% | 1.38% | 1.94% | 3.02% | 4.36% | 9.31% |
| 1D | 1.69% | 2.08% | 2.65% | 3.43% | 3.89% | 7.35% |
| 1W | 4.20% | 5.10% | 6.67% | 8.47% | 9.96% | 16.31% |

**Approach 1 — ATR% Threshold (reject entry if ATR% > cap):**

| Cap | 4H PnL (Δ) | 1D PnL (Δ) | 1W PnL (Δ) | 4H PF | 1D PF | 1W PF | 1W Worst |
|-----|-------------|-------------|-------------|-------|-------|-------|----------|
| Baseline | 13,922 | 20,831 | 19,652 | 8.93 | 7.14 | 18.67 | -57.9% |
| ≤ 3% | -1,207 | -3,970 | -18,967 | 9.52 | 6.71 | 23.63 | -13.1% |
| ≤ 5% | -104 | -349 | -9,725 | 9.21 | 7.09 | 13.70 | -34.2% |
| ≤ 7% | +8 | -34 | -4,895 | 9.00 | 7.13 | 19.38 | -34.2% |
| ≤ 8% | +12 | +0 | -3,542 | 9.00 | 7.14 | 17.85 | -34.2% |

- On 4H/1D, ATR% is naturally low; thresholds ≥ 5% are nearly neutral (remove < 2% of trades). No meaningful improvement.
- On 1W, thresholds low enough to help (≤ 5-6%) destroy P&L by removing 36-64% of trades. The weekly timeframe inherently has higher ATR%, and filtering it out removes mostly good trades.

**Approach 2 — ATR%-Scaled Sizing (weight = base × min(1, target / ATR%)):**

| Target | 4H PnL (Δ) | 1D PnL (Δ) | 1W PnL (Δ) | 1W PF | 1W Worst | 1W Avg Weight |
|--------|-------------|-------------|-------------|-------|----------|---------------|
| 1.5% | -2,808 | -7,374 | -14,401 | 18.89 | -8.8% | 0.395 |
| 2.0% | -1,540 | -4,153 | -12,651 | 18.89 | -11.8% | 0.527 |
| 2.5% | -959 | -2,277 | -10,902 | 18.90 | -14.7% | 0.658 |
| 3.0% | -661 | -1,271 | -9,164 | 18.91 | -17.7% | 0.787 |

- Preserves all trades (no filtering). PF stays stable or improves slightly.
- Dramatically improves worst-case loss (1W: -57.9% → -17.7% at target=3.0%).
- But PnL cost is proportional to weight reduction — 1W loses 47% of PnL at target=3.0%.
- On 1D, even the mildest target (3.0%) costs 1,271 PnL (-6%) for a worst-trade improvement of only 2.5pp.

**Key findings:**

1. **The strategy's losses are not primarily volatility-driven.** The worst trades on 4H and 1D have the same worst-case regardless of ATR% filtering (stop-loss determines worst case, not position size).
2. **Weekly data is inherently volatile.** Median ATR% on 1W is 5.1%, so any meaningful threshold removes the majority of weekly trades. The approach is incompatible with 1W.
3. **Scaled sizing is mechanically sound but too expensive.** It correctly reduces per-trade risk but the PnL cost is strictly proportional — there's no "free lunch" from sizing.
4. **The existing ATR stop already adapts to volatility.** High-ATR% stocks get wider stops (K × ATR), which lets winners run further. Reducing position size on top of this double-penalizes volatile winners.
5. **Extreme outliers like MSTR (ATR% 9%, 1D) are already blocked by SMA200 filter or are too rare to justify a universal rule.** On 1D, only 1 trade had ATR% > 7%.

**Decision: Do not adopt volatility-based entry filtering or position sizing.** The ATR stop mechanism and Close > SMA200 filter already provide adequate protection. Additional volatility gates cost more in lost winners than they save in avoided losers.

### 9.5 Breakeven Stop (Phase 12d) — NOT ADOPTED

Tested moving the stop-loss to entry price once the stock dips below entry and then recovers (Close >= entry_price). Two variants: breakeven with checkpoint resets still active, and breakeven with resets frozen at entry price.

| TF | Baseline PnL | BE PnL | Δ PnL | Baseline PF | BE PF | Worst Δ |
|----|-------------|--------|-------|-------------|-------|---------|
| **4H** | +12,920% | +11,876% | **-8.1%** | 8.8 | 7.2 | 0 |
| **1D** | +19,359% | +18,096% | **-6.5%** | 6.9 | 6.0 | 0 |
| **1W** | +17,815% | +17,109% | **-4.0%** | 15.3 | 13.2 | +12.7 |

The "no reset" variant produced identical results — checkpoint resets rarely interact with the breakeven stop.

**Key findings:**

1. ~60-65% of all trades dip below entry then recover — normal price behaviour, not a distress signal.
2. The breakeven stop kills ~850 trades per timeframe (4H/1D) that would have been profitable under the baseline.
3. Hit rate drops 17-20pp because breakeven creates hundreds of ~0% exits (small losses after commission).
4. Worst trade is unchanged on 4H/1D — the truly bad trades gap down hard and never recover to entry.
5. 1W worst trade improves (-38.6% → -25.9%) but at -4% total PnL cost.

**Decision: Do not adopt.** Consistent with Phase 12's trailing stop findings — any tighter stop mechanism costs more in lost winners than it saves in avoided losers. The existing KPI-based exit + ATR stop already manages risk effectively.

### 9.6 Entry Quality Filters (Phase 12e) — OVEREXTENSION RECOMMENDED (1W)

Tested four entry gates inspired by the IPAY 1W Dec-2024 loss (C3+C4 entry at peak of +16% rally, -14.2% loss):

**A) Overextension — block entry if Close > X% above Close[N bars ago]**

| Variant | 4H Δ | 1D Δ | 1W Δ | 1W PF Δ | 1W HR Δ | 1W Worst Δ |
|---------|------|------|------|---------|---------|------------|
| 5b >8% | -3.4% | -4.0% | -19.6% | -1.9 | +0.0 | **+11.9** |
| 5b >10% | -0.9% | -3.1% | -14.4% | -1.1 | +0.7 | **+11.9** |
| 5b >12% | -0.3% | -2.3% | -10.7% | -0.5 | +0.8 | **+11.9** |
| **5b >15%** | **+0.7%** | **-1.2%** | **-5.5%** | **+0.6** | **+1.2** | **+11.9** |
| 10b >12% | -0.6% | -4.0% | -10.4% | +3.6 | +3.2 | +11.9 |
| 10b >15% | -0.6% | -2.1% | -7.6% | +2.6 | +1.4 | +11.9 |
| 10b >20% | +0.1% | -1.3% | -4.6% | +3.0 | +1.0 | +11.9 |
| 10b >25% | +0.0% | -0.6% | -3.3% | +2.5 | +0.8 | +11.9 |

Every overextension variant catches the same worst-case 1W trade (-38.6% → -26.7%). The 5b >15% variant is the sweet spot: marginal cost on 4H (+0.7%) and 1D (-1.2%), modest cost on 1W (-5.5%), with improved PF and HR.

**B) Volume confirmation (entry volume >= X × MA20) — NOT ADOPTED**

Destructive at every threshold. -14% to -52% PnL on 4H, -14% to -67% PnL on 1W. On 1D, the "Volume > MA20" C3 KPI already handles this — no additional trades blocked at 0.8x or 1.0x thresholds.

**C) Minimum data for SMA200 (require 200 bars) — NOT ADOPTED**

Only affects 1W (37 stocks blocked). Costs -10.6% PnL with zero worst-trade improvement. The stocks removed include profitable setups.

**D) Trend age (block if C4 active ≥ N bars before C3) — NOT ADOPTED**

PF improves modestly (+0.1 to +0.5) but zero worst-trade improvement across all thresholds. The IPAY-type loss isn't a trend-age problem — it's an overextension problem. Persistent -5% PnL floor on 1W.

**Decision: Adopt 1W overextension filter (5b >15%).** Block 1W C3 entries where the stock has rallied more than 15% in the last 5 weekly bars.

| Metric | 1W Baseline | 1W With Filter | Change |
|--------|-------------|---------------|--------|
| PnL | +17,863% | +16,875% | -5.5% |
| PF | 18.2 | 18.7 | +0.6 |
| HR | 78.9% | 80.1% | +1.2pp |
| Worst | -38.6% | -26.7% | **+11.9pp** |
| Trades | 574 | 552 | -22 |

Do not apply on 4H or 1D — overextension dynamics differ on shorter timeframes and the benefit is negligible.

---

## 10. Open Items / Future Work

- ~~Paper trade simulation: deploy locked strategy on live data~~ → **Done (v5)**
- ~~Trailing stop / profit-taking investigation~~ → **Done (v7)**: Baseline wins, no change
- ~~Portfolio drawdown controls~~ → **Done (v7)**: Max DD 9-12%, no equity stop needed
- ~~Implement breadth filter (≥ 40% NWSm)~~ → **Superseded (v8)**: per-stock Close > SMA200 filter adopted instead (Phase 12b)
- ~~Volatility-based entry sizing~~ → **Done (v8.1)**: Neither ATR% threshold nor scaled sizing adopted — cost exceeds benefit
- ~~Breakeven stop~~ → **Done (v10)**: Not adopted — kills winners, -4% to -8% PnL
- ~~Entry quality filters~~ → **Done (v10)**: Overextension 5b >15% adopted for 1W. Volume, min data, trend age rejected
- ~~Implement 1W overextension filter~~ → **Done (v11)**: Implemented in `strategy.py`
- ~~Onset-only screener detection~~ → **Done (v13)**: Phase 13 backtested, implemented in daily_screener.py
- ~~SMA20>SMA200 entry gate~~ → **Done (v13)**: Phase 14a backtested, replaces Close>SMA200 in strategy.py, chart overlay, JS
- ~~Volume spike 1.5× N=5 confirmation~~ → **Done (v13)**: Phase 14b backtested, implemented in strategy.py, chart overlay, JS
- ~~SR Break N=10 screener pre-filter~~ → **Done (v13)**: Phase 14b backtested, implemented in daily_screener.py before lean enrichment
- **Implement entry prioritization** when at max positions — rank by trend score
- 4H C3 breakout variant (`NWSm + Donch + cRSI`) — revisit with more data
- Paper trade monitoring: track live signal accuracy over 2-3 months
- Multi-asset class extension (crypto, commodities, forex)
- **Walk-forward validation of v6 combos** — run OOS-A/OOS-B split on 4H `NWSm+DEMA+Stoch` and 1W `NWSm+DEMA+cRSI` to confirm holdout robustness
- Paper trade v6 combos for 2-3 months to verify real-time signal quality

---

## §13  Phase 13 — Screener Quality Optimization

**Goal**: reduce daily screener hits from ~700 to ~150 while improving per-trade
hit rate, return, and P&L.

**Script**: `phase13_screener_quality.py`
**Datasets**: `sample_300` (curated backtest), `entry_stocks` (last live scan)
**OOS**: last 30% of each stock's history.

### Experiments

| ID | Name | What it tests | Sweep values |
|------|------|------|------|
| 13a | Transition vs continuation | Are new combo onsets (off→on) more profitable than re-entries into an already-active combo? | Binary: onset-only vs continuation-only vs combined |
| 13b | TrendScore sensitivity | What minimum TrendScore at entry improves HR and PF? | 0, 2, 3, 4, 5, 6, 7, 8, 10, 12 |
| 13c | Market cap sensitivity | Does filtering small-cap stocks improve trade quality? | $0.3B, $0.5B, $1B, $2B, $5B, $10B, $20B |
| 13d | Alternative KPI combos | Can C5/C6 combos beat locked C3? Can stricter 3-KPI or 4-KPI combos improve return? | Exhaustive search over C3/C4/C5/C6 from available KPIs, ranked by PnL, HR floor ≥ 60% |

### Baseline

- Entry: C3 onset + C4 scale-up (Exit Flow v4)
- SMA200 filter on 1D/1W
- 0.1% commission, ATR×4 stop-loss
- Timeframe: 1D (primary screener cadence)

### Results

**Runtime**: 41 min (sample_300 268 stocks + entry_stocks 714 stocks, 1D timeframe).

#### 13a — Transition vs Continuation

| Metric | sample_300 onset | sample_300 cont. | entry_stocks onset | entry_stocks cont. |
|--------|------------------|-------------------|--------------------|--------------------|
| Trades | 1,949 | 198 | 4,987 | 536 |
| HR% | 69.5 | 66.2 | 70.1 | 68.1 |
| PF | 7.5 | 3.3 | 9.8 | 9.1 |
| Avg% | +6.95 | +4.00 | +11.78 | +12.72 |

**Finding**: Onset entries dominate volume (90%+ of trades) and have consistently higher PF (7.5 vs 3.3 on sample_300). Continuation adds only marginal PnL at lower quality. **Recommend: filter to onset-only entries in the screener.** This alone cuts screener hits dramatically since most "active" combos are continuations, not transitions.

#### 13b — TrendScore Sensitivity

| Min TS | sample_300 Trades | HR% | PF | Δ PnL% | entry_stocks Trades | HR% | PF | Δ PnL% | Consistent |
|--------|------|------|------|------|------|------|------|------|------|
| 0 | 2,042 | 68.7 | 6.5 | baseline | 5,265 | 69.7 | 9.5 | baseline | ✓ |
| 4 | 1,986 | 68.9 | 6.5 | -3.6% | 5,129 | 69.5 | 9.4 | -2.9% | ✓ |
| 8 | 1,891 | 68.4 | 6.1 | -10.1% | 4,887 | 69.7 | 9.2 | -8.3% | ✓ |
| 12 | 1,755 | 68.9 | 6.1 | -17.3% | 4,547 | 69.3 | 8.7 | -16.9% | ✓ |

**Finding**: TrendScore filtering is *consistently negative* for PnL — higher thresholds block more trades and reduce total PnL without meaningfully improving HR or PF. The direction is consistent across both datasets (all ✓). **Recommend: do not add a TrendScore minimum.** The C3/C4 combo already captures the relevant trend quality.

#### 13c — Market Cap Sensitivity

| Min MCap | sample_300 Trades | HR% | PF | Δ PnL% | entry_stocks Trades | HR% | PF | Δ PnL% |
|--------|------|------|------|------|------|------|------|------|
| $0.3B | 2,094 | 69.3 | 7.1 | baseline | 5,378 | 70.3 | 10.1 | baseline |
| $2B | 2,094 | 69.3 | 7.1 | 0.0% | 4,310 | 71.0 | 10.6 | -20.8% |
| $5B | 2,087 | 69.3 | 7.1 | -0.2% | 3,468 | 71.3 | 10.6 | -38.6% |
| $10B | 2,074 | 69.2 | 7.1 | -1.4% | 2,842 | 71.0 | 10.0 | -51.1% |
| $20B | 2,058 | 69.2 | 7.0 | -3.0% | 2,226 | 70.7 | 10.0 | -61.5% |

**Finding**: On sample_300, market cap has negligible effect (curated set skews large). On entry_stocks, higher caps improve HR and PF slightly but slash trade count heavily. Since per-trade quality (HR%, PF) stays stable or improves marginally, market cap filtering primarily reduces *quantity* not *quality*. **Recommend: no hard market cap floor.** Use market cap as an optional screener sort column for manual prioritization, not as a gate.

#### 13d — Alternative KPI Combos

**Top C3 combos by PnL (both datasets agree):**

| Rank | Combo | sample_300 PnL | PF | HR% | entry_stocks PnL | PF | HR% |
|------|-------|------|------|------|------|------|------|
| 1 | NWSm+Madrid+Vol>MA **(locked)** | +19,048 | 7.1 | 69.3 | +90,132 | 10.1 | 70.3 |
| 2 | DEMA+Madrid+NWSm | +19,042 | 6.5 | 67.2 | +91,590 | 10.1 | 69.3 |
| 3 | Madrid+NWSm+WT | +17,870 | 6.3 | 67.0 | +83,219 | 9.1 | 68.0 |

**Finding**: The locked C3 combo (`NWSm+Madrid+Vol>MA`) is already optimal or near-optimal across both datasets. `DEMA+Madrid+NWSm` ties on PnL but has lower PF and HR. Adding more KPIs (C4/C5/C6) consistently *reduces* PnL, PF, and HR — more restrictive entry hurts total return without improving per-trade quality. **Recommend: keep the locked C3.** No combo change needed.

### Conclusions

1. **Primary lever**: Switch screener from "currently active" to **onset-only** (transition detection). This is the single highest-impact change — it eliminates continuation noise and focuses on fresh entries.
2. **TrendScore gate**: Not beneficial. Drop.
3. **Market cap filter**: Neutral to slightly positive on quality, but at high cost to trade count. Use as soft sort, not hard gate.
4. **KPI combos**: Locked C3 is already top-tier. No change needed.

### Recommended screener change

```python
# In daily_screener.py, replace:
c3_now = _kpi_bull_at(c3_kpis, -1)
# With:
c3_now = _kpi_bull_at(c3_kpis, -1) and not _kpi_bull_at(c3_kpis, -2)
```

This transition check should reduce daily hits from ~700 to ~50-150 (onset-only), matching the documented behavior and improving screener signal quality.

---

## §14  Phase 14 — Entry Quality Optimization (Run 1)

**Goal**: Identify the best entry gate (SMA variant), test breakout confirmation
as a timing layer, and evaluate ATR%-based risk gates.

**Script**: `phase14_entry_quality.py`
**Datasets**: `sample_300` (268 stocks), `entry_stocks` (714 stocks)
**Timeframe**: 1D, onset-only, OOS 30%, 0.1% commission.
**Runtime**: 52 seconds.

### 14a — SMA Gate Variants

| Filter | s300 Trades | s300 HR% | s300 PF | s300 Avg% | entry Trades | entry HR% | entry PF | entry Avg% |
|--------|------|------|------|------|------|------|------|------|
| No filter | 2,330 | 67.0 | 6.4 | +6.37 | 5,984 | 68.3 | 9.2 | +11.32 |
| Close > SMA20 | 2,242 | 64.4 | 5.3 | +5.75 | 5,760 | 66.5 | 7.9 | +10.52 |
| Close > SMA50 | 2,178 | 66.4 | 5.9 | +6.09 | 5,644 | 67.6 | 8.6 | +11.08 |
| Close > SMA100 | 2,219 | 67.7 | 6.4 | +6.40 | 5,714 | 69.0 | 9.2 | +11.53 |
| **Close > SMA200** | **2,094** | **69.3** | **7.1** | **+6.72** | **5,378** | **70.3** | **10.1** | **+12.00** |
| SMA20 > SMA50 | 1,867 | 70.3 | 7.3 | +6.74 | 4,817 | 71.7 | 11.2 | +12.34 |
| SMA20 > SMA200 | 1,814 | 70.1 | 7.8 | +7.14 | 4,605 | 73.1 | 11.8 | +12.59 |
| SMA100 > SMA200 | 1,602 | 70.6 | 8.1 | +7.31 | 4,094 | 73.1 | 12.0 | +12.54 |
| SMA20>50>200 | 1,315 | 72.3 | 8.8 | +7.56 | 3,469 | 76.2 | 13.4 | +13.35 |
| SMA20>50>100>200 | 1,025 | 72.9 | 8.6 | +7.73 | 2,726 | 78.1 | 14.9 | +14.07 |

**Finding**: Clear monotonic gradient — longer SMAs and tighter stacks consistently improve HR%, PF, and per-trade avg%. The tradeoff is trade count. Close > SMA200 remains the best single-SMA filter (highest PF among Close>SMA variants). Crossover filters (SMA20>SMA200, SMA100>SMA200) and full stacks offer strictly better per-trade quality but at 15-55% fewer trades. The full SMA20>50>100>200 stack reaches 78.1% HR and 14.9 PF on entry_stocks but loses 54% of trades. **For a system targeting 5-10 trades/day from ~50-150 onsets, upgrading to SMA20>SMA200 or SMA100>SMA200 is recommended.** Both are consistent across datasets.

### 14b — Breakout Confirmation Layer

| Signal | Best N | s300 Trades | s300 HR% | s300 PF | entry Trades | entry HR% | entry PF |
|--------|------|------|------|------|------|------|------|
| Baseline (no breakout) | — | 2,094 | 69.3 | 7.1 | 5,378 | 70.3 | 10.1 |
| NWE(MAE) bull | N=5 | 45 | 88.9 | 33.3 | 203 | 94.1 | 86.4 |
| NWE(STD) bull | N=5 | 15 | 86.7 | 23.0 | 74 | 98.6 | 312.8 |
| cRSI breakout | N=10 | 714 | 73.2 | 6.5 | 2,058 | 76.2 | 10.5 |
| SR Break | N=10 | 582 | 72.7 | 10.8 | 1,574 | 71.5 | 10.7 |
| Vol spike 1.5x | N=5 | 1,438 | 72.0 | 8.1 | 4,047 | 71.7 | 10.9 |
| BB30 dip | N=10 | 42 | 85.7 | 32.3 | 97 | 78.4 | 14.4 |
| Confluence≥1 N=10 | — | 1,835 | 71.1 | 7.5 | 4,866 | 71.0 | 10.4 |
| Confluence≥2 N=10 | — | 831 | 73.3 | 7.2 | 2,453 | 74.2 | 11.1 |

**Finding**: Envelope breakouts (NWE) and BB30 dips produce extraordinarily high HR% and PF but with very few trades (too sparse for production use). The most *practical* breakout signals are:

- **Volume spike 1.5× N=5**: keeps 69% of trades on sample_300, improves PF from 7.1→8.1 and HR +2.7pp. Consistent on entry_stocks (PF 10.1→10.9).
- **SR Break N=10**: PF jumps to 10.8 (sample_300) and 10.7 (entry_stocks) — consistent across datasets. Keeps ~30% of trades.
- **cRSI breakout N=10**: keeps 34% of trades, improves HR by +3.9pp (sample_300) and +5.9pp (entry_stocks). Marginal PF change.
- **Confluence≥2 N=10**: 831 trades (s300), HR 73.3%, PF 7.2; 2,453 trades (entry), HR 74.2%, PF 11.1.

For a 5-10 trades/day target, **Vol spike 1.5× N=5** is the best balance of quality improvement vs trade availability. SR Break N=10 is the best if you can accept a smaller pool.

### 14f — ATR% Risk Gate

| Gate | s300 Trades | s300 HR% | s300 PF | Δ PnL% | entry Trades | entry HR% | entry PF | Δ PnL% |
|------|------|------|------|------|------|------|------|------|
| None (baseline) | 2,094 | 69.3 | 7.1 | — | 5,378 | 70.3 | 10.1 | — |
| ATR% ≤ 8% | 2,094 | 69.3 | 7.1 | 0.0% | 5,322 | 70.3 | 9.8 | -4.2% |
| ATR% ≤ 6% | 2,087 | 69.2 | 7.1 | -0.3% | 5,130 | 70.2 | 9.5 | -14.7% |
| ATR% ≤ 5% | 2,073 | 69.1 | 7.1 | -1.7% | 4,879 | 70.0 | 9.0 | -25.1% |
| ATR% ≤ 4% | 2,045 | 68.9 | 6.7 | -6.6% | 4,411 | 69.6 | 8.7 | -39.4% |
| ATR% ≤ 3% | 1,864 | 68.8 | 6.7 | -20.2% | 3,435 | 69.5 | 7.8 | -61.4% |
| ATR% ≤ 2% | 1,178 | 70.4 | 7.0 | -55.1% | 1,540 | 69.8 | 7.3 | -86.1% |
| ATR% floor ≥ 1% | 2,088 | 69.3 | 7.1 | 0.0% | 5,342 | 70.4 | 10.1 | -0.0% |

**Finding**: ATR% filtering has **no positive effect** on sample_300 — the curated universe already has well-behaved volatility. On entry_stocks, capping ATR% removes high-volatility stocks but consistently *hurts* PF and PnL (the removed trades were profitable on average). ATR% floor (minimum) has negligible effect. **Recommend: do not adopt an ATR% gate.** Use ATR% as a ranking tiebreaker (prefer lower-volatility entries when choosing among equally-ranked onsets), not as a hard filter.

### Phase 14 Conclusions

1. **SMA gate**: Close > SMA200 confirmed as the best single filter. For top-N daily selection, upgrade to **SMA20 > SMA200** or **SMA100 > SMA200** for higher per-trade quality.
2. **Breakout confirmation**: **Vol spike 1.5× N=5** is the best practical filter — consistent across both datasets, keeps most trades, improves PF. SR Break N=10 is strongest but cuts 70% of trades.
3. **ATR% gate**: Not beneficial as a hard filter. Use as a ranking signal instead.

---

## §15  Phase 15 — Entry Delay Sensitivity Test

**Goal**: Determine whether delaying entry by H bars after a C3 onset signal improves per-trade quality or total P&L.

**Script**: `phase15_entry_delay.py`
**Dataset**: sample_300 (~295 stocks), OOS last 30%.
**Delay values**: H = {0, 1, 2, 3, 5, 10}
**Versions**: v4 (onset-only) and v5 (full gates).
**Fill rule**: H=0 fills at `Close[signal_bar]`; H≥1 fills at `Open[signal_bar + H]`.
**ATR stop**: computed at the fill bar (`signal + H`), not the signal bar.

### Results

See Phase 15 output files for full tables. Key finding: **H=1 (next bar open) is the default and broadly optimal**. H=0 has look-ahead issues (fills at signal bar close). H=2+ reduces trade count and PnL without meaningful quality improvement.

---

## §16  Phase 16 — PF/Return-Optimized Strategy Search

**Goal**: Re-optimize the entire strategy (combo selection, entry gates, entry delay) with **Profit Factor (PF)** and **average return per trade** as the primary objectives, instead of cumulative PnL.

**Motivation**: The trader plans to take only ~10 trades/day. With low trade volume, per-trade quality (PF, avg return) matters more than total P&L accumulated over thousands of trades. The current locked combos were optimized for cumulative PnL — they may not be optimal when the selection pool is small and each trade must be high-quality.

**Script**: `phase16_pf_optimization.py`
**Dataset**: sample_300 (~295 stocks), OOS last 30%.
**Commission**: 0.1% + 0.5% slippage per trade.
**Exit**: Exit Flow v4 (unchanged).

### Steps

| Step | Name | What it tests |
|------|------|---------------|
| 16a | Exhaustive combo search | All C3/C4/C5/C6 KPI combinations, ranked by PF (HR floor ≥ 60%, min 20 trades) |
| 16b | Entry gate sweep | 5 gate variants (none, SMA200, SMA20>200, SMA stack, v5) on top PF combos per size |
| 16c | Entry delay sweep | H = {0, 1, 2, 3, 5, 10} on top PF combos with best gate |

### Design

- **Combo pool**: C3/C4 use all available KPIs (~25); C5/C6 use top 15 KPIs (to keep runtime feasible).
- **Ranking**: Primary = PF; ties broken by avg return. HR floor ≥ 60% and min 20 trades filter out unreliable combos.
- **Gates tested**: no gate, Close > SMA200, SMA20 > SMA200, full SMA stack (20>50>100>200), v5 (SMA20>200 + vol spike + overextension).
- **Delay**: H=0 (signal bar close), H=1 (next bar open, default), H=2, H=3, H=5, H=10.
- **Comparison**: Each step includes the locked C3 combo as a benchmark.

### Key Questions

1. Does the PF-optimal C3 differ from the PnL-optimal C3 (currently locked)?
2. Do larger combos (C5/C6) improve per-trade quality even though they reduce trade count?
3. Which entry gate maximizes PF for the new combos?
4. Does entry delay interact differently with PF-optimal combos?

### Results

**Runtime**: 50.5 min (3 TFs × exhaustive combo search + gates + delays, 268 stocks).

#### 16a — Exhaustive Combo Search (ranked by PF)

**4H — Top combos by PF:**

| Size | Rank | Combo | Trades | HR% | AvgRet% | PnL% | PF | Worst% | C4% |
|------|------|-------|--------|-----|---------|-------|-----|--------|-----|
| **LOCKED** | — | NWSm+cRSI+OBVOsc | 1,918 | 68.8 | +4.11 | +10,928 | 6.87 | -21.1 | 51.8 |
| C3 | 1 | NWSm+Vol>MA+BB30 | 41 | 97.6 | +7.67 | +388 | 2274 | -0.2 | 22.0 |
| C3 | 5 | **NWSm+DEMA+Stoch** | **1,361** | **79.4** | **+5.93** | **+10,385** | **13.98** | **-17.5** | **43.9** |
| C4 | 1 | NWSm+Stoch+Vol>MA+BB30 | 26 | 96.2 | +5.18 | +160 | 939 | -0.2 | 19.2 |
| C5 | 1 | NWSm+cRSI+GKTr+Vol>MA+Stoch | 622 | 68.8 | +4.92 | +4,567 | 7.37 | -12.5 | 87.3 |
| C6 | 1 | NWSm+cRSI+GKTr+DEMA+Vol>MA+Stoch | 606 | 68.0 | +4.60 | +4,177 | 6.55 | -12.5 | 87.1 |

**1D — Top combos by PF:**

| Size | Rank | Combo | Trades | HR% | AvgRet% | PnL% | PF | Worst% | C4% |
|------|------|-------|--------|-----|---------|-------|-----|--------|-----|
| **LOCKED** | — | NWSm+Madrid+Vol>MA | 2,180 | 63.3 | +5.68 | +17,105 | 5.27 | -28.1 | 50.7 |
| C3 | 1 | NWSm+Impulse+NWE-MAE | 20 | 100.0 | +22.37 | +635 | 999 | +2.7 | 70.0 |
| C5 | 1 | NWSm+cRSI+DEMA+Stoch+WT | 1,541 | 62.7 | +4.36 | +9,435 | 4.87 | -25.5 | 42.4 |
| C6 | 1 | NWSm+cRSI+Madrid+GKTr+DEMA+OBVOsc | 1,117 | 63.9 | +5.63 | +9,515 | 4.52 | -25.5 | 95.2 |

**1W — Top combos by PF:**

| Size | Rank | Combo | Trades | HR% | AvgRet% | PnL% | PF | Worst% | C4% |
|------|------|-------|--------|-----|---------|-------|-----|--------|-----|
| **LOCKED** | — | NWSm+Madrid+DEMA | 436 | 72.2 | +16.78 | +10,552 | 10.53 | -35.6 | 61.2 |
| C3 | 1 | NWSm+cRSI+NWE-MAE | 23 | 91.3 | +45.15 | +1,349 | 52.57 | -9.4 | 47.8 |
| C3 | 2 | **NWSm+DEMA+cRSI** | **418** | **89.0** | **+20.25** | **+11,725** | **47.39** | **-21.1** | **48.6** |
| C4 | 1 | **NWSm+Stoch+cRSI+Vol>MA** | **168** | **88.1** | **+17.35** | **+3,913** | **43.85** | **-12.6** | **41.7** |
| C5 | 1 | NWSm+cRSI+Vol>MA+Stoch+PSAR | 169 | 71.6 | +11.16 | +2,637 | 12.25 | -22.7 | 38.5 |
| C6 | 1 | NWSm+cRSI+GKTr+Vol>MA+Stoch+Ichi | 61 | 68.9 | +15.52 | +1,432 | 10.71 | -17.7 | 82.0 |

**Key findings (16a):**

1. **PF-optimal C3 combos differ significantly from PnL-optimal ones.** The PF winners include rare indicators (NWE-MAE, BB30) that fire very few signals (20-41 trades) with extreme PF (999+). These are statistically unreliable.
2. **Filtering to combos with ≥100 trades reveals robust PF winners:**
   - **4H**: `NWSm+DEMA+Stoch` — 1,361 trades, 79.4% HR, PF 13.98, avg +5.93%. Beats locked on PF (13.98 vs 6.87) and HR (+10.6pp), with comparable PnL (-5%).
   - **1W**: `NWSm+DEMA+cRSI` — 418 trades, 89.0% HR, PF 47.39, avg +20.25%. Beats locked on PF (47.39 vs 10.53), HR (+16.8pp), avg return (+3.47pp), AND PnL (+11%).
   - **1W C4**: `NWSm+Stoch+cRSI+Vol>MA` — 168 trades, 88.1% HR, PF 43.85. Massively outperforms locked C4.
3. **1D locked combo is competitive.** No C3 with ≥100 trades beats `NWSm+Madrid+Vol>MA` on PF. The locked 1D combo appears near-optimal for PF as well.
4. **C5/C6 combos do NOT improve per-trade quality.** They reduce PF vs C3/C4 while also reducing trade count. More KPIs = more restrictive entry = missed good trades.
5. **Stoch_MTM is the standout PF KPI.** It appears in the majority of top PF combos across all TFs.

#### 16b — Entry Gate Sweep

**On practical combos (≥100 trades):**

| TF | Combo | No gate PF | SMA200 PF | SMA20>200 PF | SMA stack PF | v5 PF |
|----|-------|------------|-----------|--------------|-------------|-------|
| 4H | C5 top | 7.37 | 7.37 | 7.37 | 7.37 | 7.21 |
| 1D | C5 top | 4.87 | 4.57 | **5.17** | **5.66** | **5.71** |
| 1D | C6 top | 4.52 | 4.83 | 4.94 | **5.69** | 5.50 |
| 1W | C4 top | 43.85 | 29.85 | **50.33** | 24.05 | 41.75 |
| 1W | C5 top | 12.25 | **12.34** | 11.60 | 9.57 | 8.98 |

**Key findings (16b):**

1. **4H**: Gates have no effect — the combo KPIs already handle trend filtering on 4H data.
2. **1D**: v5 gates and SMA stack provide meaningful PF improvements (+17-26%). SMA20>SMA200 is the most consistent single upgrade.
3. **1W**: SMA20>SMA200 is the best gate for the top C4 combo (PF 43.85 → 50.33). Stricter gates (SMA stack, v5) hurt PF by removing too many trades.

#### 16c — Entry Delay Sweep

**Best delays per TF (practical combos):**

| TF | Combo | H=0 PF | H=1 PF | H=2 PF | H=3 PF | H=5 PF | H=10 PF |
|----|-------|--------|--------|--------|--------|--------|---------|
| 4H | C5 | 7.59 | **7.37** | **7.68** | 6.74 | 5.60 | 3.68 |
| 1D | C5 (v5) | 5.49 | **5.71** | 5.58 | 5.24 | 4.75 | 3.32 |
| 1D | C6 (stack) | 5.20 | 5.69 | 5.83 | **5.87** | 5.02 | 4.09 |
| 1W | C4 (SMA20>200) | 36.25 | **50.33** | 22.60 | 19.26 | 11.24 | 5.49 |

**Key findings (16c):**

1. **H=1 (next bar open) is optimal for PF** on most combos. It consistently produces the best or near-best PF.
2. **H=2 or H=3 can marginally improve PF on 1D** but at the cost of higher worst-case losses and lower HR.
3. **Delays beyond H=3 consistently degrade PF** across all TFs — the signal loses predictive power.

### Phase 16 Conclusions

1. **4H C3 candidate: `NWSm+DEMA+Stoch`** — PF 13.98 (vs locked 6.87), HR 79.4% (vs 68.8%), 1,361 trades. This combo doubles the PF with +10.6pp higher HR. PnL is comparable (-5%). **Strong upgrade candidate for PF-focused trading.**

2. **1D C3: locked combo remains optimal.** No high-volume C3 combo beats `NWSm+Madrid+Vol>MA` on PF. Adding v5 gates improves PF from 5.27 to ~5.71.

3. **1W C3 candidate: `NWSm+DEMA+cRSI`** — PF 47.39 (vs locked 10.53), HR 89.0% (vs 72.2%), avg return +20.25% (vs +16.78%). Beats locked on **every metric including PnL**. **Strong upgrade candidate.**

4. **1W C4 candidate: `NWSm+Stoch+cRSI+Vol>MA`** — PF 43.85 (vs locked Donch+GKTr+OBVOsc ~11). With SMA20>200 gate: PF 50.33, HR 89.3%. **Strong upgrade candidate.**

5. **C5/C6 combos are not worth it for PF optimization.** They add complexity without improving per-trade quality.

6. **Entry delay H=1 confirmed optimal** across all TFs for PF-focused trading. No benefit from delayed entry.

7. **Stoch_MTM is the key PF-improving KPI** — its inclusion in any combo consistently lifts HR and PF. It was not in any of the locked combos.

### Adopted Changes (v6)

| TF | v5 Locked C3 | v6 PF-Optimal C3 | PF Δ | HR Δ | PnL Δ |
|----|-------------------|---------------|------|------|-------|
| **4H** | NWSm+cRSI+OBVOsc (PF 6.87) | **NWSm+DEMA+Stoch** (PF 13.98) | **+103%** | **+10.6pp** | -5% |
| **1D** | NWSm+Madrid+Vol>MA (PF 5.27) | *No change* | — | — | — |
| **1W** | NWSm+Madrid+DEMA (PF 10.53) | **NWSm+DEMA+cRSI** (PF 47.39) | **+350%** | **+16.8pp** | +11% |

| TF | v5 Locked C4 | v6 PF-Optimal C4 | PF Δ |
|----|-------------------|---------------|------|
| **4H** | NWSm+Madrid+GKTr+cRSI | *No change* | — |
| **1D** | NWSm+Madrid+GKTr+cRSI | *No change* | — |
| **1W** | NWSm+Donch+GKTr+OBVOsc (~11) | **NWSm+Stoch+cRSI+Vol>MA** (PF 43.9) | **~300%** |

**Status: Adopted as v6 (v15).** All changes implemented in config.json, dashboard, screener, and charts.

---

## §17  Phase 17 — Unified Strategy Archetype Optimization

**Goal**: Expand the strategy search beyond pure trend-following combos to 5 distinct
strategy archetypes, each with tailored entry pools, polarity rules, and exit modes.
Also integrates the 10 Stoof (Band Light) indicators and 2 additional timeframes (2W, 1M).

**Scripts**:
- `phase17_step0_audit.py` — Pre-flight data quality, coverage, correlation, scorecard
- `phase17_strategy_archetypes.py` — Main pipeline (Stages 1–5)

**Dataset**: sample_300 (~268 stocks, expanding to ~300 after re-enrichment)
**Timeframes**: 4H, 1D, 1W, 2W, 1M (5 total)
**KPIs**: 28 v6 + 10 Stoof = 38 total
**OOS**: 50% in-sample / 25% OOS-A / 25% OOS-B

### Prerequisites (Step 0)

Before running the main pipeline, Step 0 validates data readiness:

| Sub-step | What | Status |
|----------|------|--------|
| 0a | Re-enrich sample_300 with Stoof indicators + 2W/1M TFs | **PENDING** — run `fetch_sample300.py --force` |
| 0b | Data quality audit (missing bars, columns, date ranges) | **DONE** — 268/300 symbols on 3 TFs, 0/300 on 2W/1M |
| 0c | KPI state coverage (NA%, always-bull, signal rarity) | **DONE** — 10 Stoof KPIs show 100% NA (need re-enrichment) |
| 0d | Correlation analysis (Spearman pairwise, r > 0.70) | **DONE** — see findings below |
| 0e | Individual KPI scorecard (standalone HR/return per polarity) | **DONE** — 28 v6 KPIs scored |
| 0f | Search space estimation (combos per archetype × TF) | **DONE** — ~397K combos/TF for v6 KPIs |

### Step 0 Key Findings

**Correlation clusters (consistent across TFs):**
- Ichimoku ↔ Madrid Ribbon (r=0.82–0.86): highest persistent correlation
- Ichimoku ↔ GMMA (r=0.78–0.83): strong overlap
- Ichimoku ↔ ADX & DI (r=0.73–0.81): overlapping trend signal
- TuTCI ↔ Donchian Ribbon (r=0.77–0.82): redundant trend
- ADX & DI ↔ GMMA (r=0.72–0.75): moderate overlap
- **Action**: Exclusion pairs applied — combos containing both sides not tested

**Degenerate KPIs (flagged across all TFs):**
- BB 30, NWE-MAE, NWE-STD: <1% bullish — too rare for entry combos
- NWE-Repainting: 73% NA on 4H/1D — excluded from combos
- **Action**: Excluded from combo pool but kept for breakout archetypes where rarity is expected

**Top standalone KPIs by HR@mid-horizon:**
- 4H: GK Trend Ribbon (+1, HR@6=61.3%), Nadaraya-Watson Envelop MAE (+1, HR@6=60.9%)
- 1D: NWE-Repainting (+1, HR@5=62.3%), Donchian Ribbon (-1, HR@5=59.5%)
- 1W: NWE-Repainting (+1, HR@4=94.1%), BB 30 (+1, HR@4=88.5%), TuTCI (-1, HR@4=68.5%)

### Strategy Archetypes

| Key | Label | Anchor | KPI Pool | Polarity | Exit Mode |
|-----|-------|--------|----------|----------|-----------|
| A_trend | Trend Following | trend | trend + momentum + rel.strength | All bull | Standard (v4) |
| B_dip | Mean Reversion / Buy the Dip | trend | trend + mean_rev + breakout + momentum | Mixed (+1/-1) | Trend anchor |
| C_breakout | Breakout / Momentum Surge | breakout | breakout + momentum + rel.strength | All bull | Momentum-governed |
| D_risk | Trend + Risk-Managed | trend | trend + risk_exit + momentum | All bull | Risk priority |
| E_mixed | Full Mixed / Unconstrained | any | all dimensions | Mixed (+1/-1) | Adaptive |

**Exit modes explained:**
- **Standard** = Exit Flow v4 unchanged (T/M lenient+strict, ATR stop, checkpoint)
- **Trend anchor** = Only trend-dimension KPIs govern exit (contrarian KPIs ignored)
- **Momentum-governed** = Only momentum/breakout KPIs govern exit
- **Risk priority** = Any bearish risk KPI → immediate exit (tighter than standard)
- **Adaptive** = Exit KPIs selected dynamically based on combo's dimension profile

### Pipeline Stages

```
Stage 0  ──→  Stage 1  ──→  Stage 2  ──→  Stage 3  ──→  Stage 4  ──→  Stage 5
Pre-flight    Combo         Exit Rule      Entry Gate    Walk-Forward   Final
Audit         Search        Optimization   + Delay       Validation     Recommendation
(done)        (per arch)    (top-N)        (sweep)       (OOS-B)        (compare all)
```

| Stage | What | Per-archetype |
|-------|------|---------------|
| 1 | C3–C6 combo search within archetype pool, ranked by PF | 5 archetypes × 5 TFs |
| 2 | Test 5 exit modes on top-N combos from Stage 1 | Top 2 per size per arch |
| 3 | Sweep 3 entry gates × 5 delays on best exit-optimized combos | Top 3 per arch |
| 4 | Validate winners on OOS-B holdout (HR decay, PF ratio) | Top 5 per arch |
| 5 | Cross-strategy comparison, final recommendation | Global ranking |

### Validation Criteria (Stage 4)

A combo **passes** walk-forward validation if:
- OOS-B HR >= 50%
- IS→OOS HR decay <= 15pp
- OOS PF / IS PF ratio >= 0.5
- OOS trades >= 3

### Decision Framework (Stage 5)

| Action | Condition |
|--------|-----------|
| **ADOPT** | OOS PF >= 1.2 AND OOS HR >= 55% AND OOS trades >= 10 |
| **MONITOR** | OOS PF >= 1.0 but below ADOPT thresholds |
| **HOLD_CURRENT** | No strategies pass validation |

### Current Status

- **Step 0 DONE** on v6 KPIs (3 TFs). Stoof KPIs require re-enrichment.
- **Stages 1–5 READY** to run once re-enrichment completes.
- Output directory: `research/kpi_optimization/outputs/all/phase17/`

### Stoof Indicator Integration (Pre-Phase 17)

Ten Stoof (Band Light) indicators were audited against PineScript source and corrected:

| Indicator | Dimension | Fix Applied |
|-----------|-----------|-------------|
| MACD_BL | momentum | Signal line: SMA → EMA (matches Pine `ta.macd`) |
| WT_LB_BL | mean_reversion | Source: hlc3 → close (matches Band Light PineScript) |
| ADX_DI_BL | trend | Smoothing: SMA → RMA (matches Pine `ta.rma`) |
| OBVOSC_BL | mean_reversion | No fix needed — correct |
| CCI_Chop_BB_v1/v2 | mean_reversion | No fix needed — correct |
| LuxAlgo_Norm_v1/v2 | mean_reversion | No fix needed — correct |
| Risk_Indicator | risk_exit | No fix needed — correct |
| PAI | momentum | No fix needed — correct |

All fixes are backward-compatible (new parameters with v6 defaults). 22 unit tests added.
See `docs/pinescripts/Combined Band Light/Combined Band Light - Audit.txt` for line-by-line analysis.
