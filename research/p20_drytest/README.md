# Phase 20 — Dry Test: Strategy Performance Validation

## Objective

Run all four strategies (v6, dip_buy, swing, trend) on the **full enriched data**
for every dashboard symbol (~187 stocks) across all timeframes (4H, 1D, 1W, 2W, 1M).
Use 2-fold walk-forward validation to measure in-sample (IS) and out-of-sample (OOS)
performance.

This answers: **Which strategy works best, on which timeframe, and does it hold
out-of-sample?**

## Server Constraints

| Resource | Value |
|----------|-------|
| Total RAM | 7.6 GB |
| Available (typical) | ~2 GB |
| 70 % cap | 5.3 GB |
| Dashboard server RSS | ~2.4 GB |
| Budget for test | ~1.5 GB peak |

Design: process one timeframe at a time, one symbol at a time, explicit `del` + `gc.collect()`.

## Data Inventory

| TF | Symbols | Avg Bars | History |
|----|---------|----------|---------|
| 4H | 187 | 1 883 | 2023-02 → 2026-02 |
| 1D | 180 | 804 | 2023-01 → 2026-02 |
| 1W | 180 | 164 | 2022-12 → 2026-02 |
| 2W | 177 | 82 | 2023-01 → 2026-03 |
| 1M | 177 | 39 | 2022-12 → 2026-02 |

## Strategies Tested

| Key | Entry KPIs (c3) | Polarity | c4 KPIs | Exit |
|-----|----------------|----------|---------|------|
| v6 | NW, Madrid Ribbon, Vol+MA20 | all +1 | NW, Madrid, GK Trend, cRSI | same as entry |
| dip_buy | NW, ADX&DI, WT_LB | +1, -1, -1 | + SQZMOM_LB | NW(+1), ADX(-1), Stoch_MTM(+1) |
| swing | NW, Stoch_MTM, cRSI | all +1 | + Vol+MA20 | same as entry |
| trend | NW, DEMA, cRSI | all +1 | + Stoch_MTM | same as entry |

## Walk-Forward Design

**2-fold split** on each symbol's data:

- **Fold 1**: IS = first half, OOS = second half
- **Fold 2**: IS = second half, OOS = first half

Per fold, run each strategy's position engine on the IS slice and the OOS slice
independently. Aggregate metrics across all symbols.

## Metrics Collected (per strategy × TF × fold × period)

- Trades, Winners, Losers
- Hit Rate (HR%)
- Average Return %, Median Return %
- Total Return %
- Profit Factor (PF)
- Average Hold (bars)
- Win Avg %, Loss Avg %
- Max Drawdown %

## Output

- `research/p20_drytest/p20_drytest_results.json` — full results
- `research/p20_drytest/p20_drytest.log` — progress log
- Console summary table

## How to Run

```bash
cd /root/damiverse_apps/trading_app
nohup .venv/bin/python3 -m research.p20_drytest.p20_drytest > research/p20_drytest/p20_drytest.log 2>&1 &
```
