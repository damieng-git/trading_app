# Phase 20C — 1D Entry + 1W Exit Swing Optimization

## Objective

Find the best **1D entry combos** (including bi-polarity) for swing trades
managed by the **locked 1W strategies** on the exit side.

```
1D combo onset → ENTER (next day open)
1W locked strategy → MONITOR + EXIT (weekly KPI invalidation / ATR stop)
```

This produces swing trades with daily-precision entries and weekly-governed
holds (typically 1–4 weeks).

---

## Locked 1W Exit Strategies

| Type | 1W Combo | Status | Avg Hold |
|---|---|---|---|
| Dip Buy | NWSm(+)+ADX(-)+Stoch(+) | READY 4/4 | 7.1 wk |
| Swing | NWSm(+)+Stoch(+)+cRSI(+) | READY 4/4 | 4.5 wk |
| Trend | NWSm(+)+DEMA(+)+cRSI(+) | READY 4/4 | 7.5 wk |

Exit logic per 1W strategy:
- Check weekly bars for KPI invalidation (exit KPIs no longer aligned)
- ATR trailing stop on weekly close
- Max hold cap (M parameter from EXIT_PARAMS["1W"])

---

## 1D Entry Candidates

### Pool 1 — P18 top 1D combos (15 combos)
Already tested in P18, re-simulate with 1W exit instead of 1D exit.

### Pool 2 — Single-KPI bi-polarity sweep (76 tests)
Each of the 38 KPIs tested individually at +1 and −1 polarity.
Answers: which single 1D KPI onset best times a swing entry?

### Pool 3 — 2-KPI bi-polarity pairs (top ~50 pairs)
Top KPI pairs from P18's mixed-polarity results, plus hand-picked
dip-buy pairs: e.g. ADX(−)+Stoch(+), WT(−)+cRSI(+), SQZ(−)+MACD(+).

### Total: ~140 entry candidates × 3 exit strategies = ~420 simulations

---

## Simulation Design

For each (1D entry combo × 1W exit strategy):

1. **Entry**: scan 1D bars for combo onset (transition from inactive to active)
   - Apply v5 gates: SMA20>SMA200 on 1D, volume spike, overextension
   - Enter at next day's open

2. **Exit**: align entry date to 1W bars, then on each subsequent weekly bar:
   - Check if 1W exit KPIs have invalidated
   - Check ATR trailing stop on weekly close
   - If either triggers → close at next daily open after the weekly bar
   - Max hold: M weeks (from 1W EXIT_PARAMS)

3. **Metrics**: trades, HR, PF, avg_ret, avg_hold (in days), worst trade

---

## Pass Criteria

| Metric | Threshold |
|---|---|
| Min trades | ≥ 50 (across 268 stocks, full history) |
| Min HR | ≥ 65% |
| Min avg_ret | > 0% |
| Min PF | ≥ 2.0 |
| Worst trade | > −25% |

---

## Output

- `phase20c_results.json` — all 420 simulation results
- `phase20c_best.json` — top 5 per (1W exit strategy)
- Console report with ranked results

---

## Memory / Runtime

- Load 1D + 1W data simultaneously (~4 GB)
- ~420 sims × 268 stocks × ~2000 bars = ~10 min
- Single script, runs inline
