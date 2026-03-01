# Research Directory

> Strategy research pipeline for KPI-based entry/exit optimization.
> Current strategy: **STRATEGY.md** (v12) in `kpi_optimization/`.

---

## Directory Structure

```
research/
├── kpi_optimization/       # Active research scripts + strategy doc
│   ├── STRATEGY.md          # Locked strategy (v12) — single source of truth
│   ├── phase11v7-v14        # Active phase scripts (see index below)
│   ├── legacy/              # Superseded phases 1-10 + early v11 variants
│   ├── outputs/             # Charts, JSONs, CSVs from all phases
│   ├── tf_config.py         # Timeframe definitions shared by all phases
│   ├── visualize_results.py # Shared visualization helpers
│   └── fetch_sample300.py   # Script to build the sample_300 universe
│
├── optimization/            # Generic search/sampling framework used by phases
├── sample_universe/         # sample_300.csv + sample_meta.json
├── industry_analysis/       # Standalone sector/industry analysis scripts
├── harness/                 # Research harness runner + PDF export
└── _archive/                # Dead/unused code (indicator_config_optimiser, kpi_optimiser)
```

---

## Active Phase Index

| Script | Phase | Purpose | Status | Feeds STRATEGY.md |
|--------|-------|---------|--------|-------------------|
| `phase11v7_exitv4_hr65.py` | 11 v7 | Exit Flow v4 + HR ≥ 65% entry screening → locked C3/C4 combos | **Done** | §1 Entry, §2 Exit |
| `phase11v8_param_sweep.py` | 11 v8 | T/M/K parameter grid search → locked exit params | **Done** | §2 Exit params |
| `phase11v9_unified_position.py` | 11 v9 | Unified position sim (1x vs 1.5x vs 2x C4 scaling) | **Done** | §1 Position sizing |
| `phase11v10_breakout_combos.py` | 11 v10 | Breakout KPI combo screening | **Done** | §1 Breakout KPIs |
| `phase11v11_golden_score.py` | 11 v11 | Golden score (`HR×PF/|worst|`) C4 optimization | **Done** | §1 Why P&L not golden |
| `phase11v12_sector_combos.py` | 11 v12 | Per-sector C3/C4 screening (old universe) | **Done** | §1 Sector combos |
| `phase11v13_sector_optimizer.py` | 11 v13 | Full per-sector optimizer (sample_300) | **Done** | §1 Sector combos |
| `phase11v14_walkforward.py` | 11 v14 | Walk-forward validation + 0.1% commission | **Done** | §4 Walk-forward |
| `phase12_strategy_improvements.py` | 12 | Trailing stops, regime filter, portfolio controls | **Done** | §8 Phase 12 |
| `phase12b_entry_confirmation.py` | 12b | Per-stock entry confirmation filters (SMA/EMA/GMMA/ADX/MACD) → Close > SMA200 adopted for 1D/1W | **Done** | §1 Entry filter |
| `phase12c_volatility_sizing.py` | 12c | Volatility-aware entry sizing (ATR% threshold + scaled sizing) → neither adopted | **Done** | §8.4 Volatility |
| `phase12d_breakeven_stop.py` | 12d | Breakeven stop test → not adopted (-4% to -8% PnL, kills winners) | **Done** | §9.5 Breakeven |
| `phase12e_entry_filters.py` | 12e | Entry quality filters (overextension, volume, min data, trend age) → 1W overextension adopted | **Done** | §9.6 Entry quality |
| `phase13_screener_quality.py` | 13 | Screener quality optimization: transition vs continuation, TrendScore sensitivity, market cap sensitivity, alternative KPI combos (C3-C6). Dual-dataset: sample_300 + entry_stocks → onset-only adopted, rest rejected | **Done** | §13 Screener quality |
| `phase14_entry_quality.py` | 14 | Entry quality optimization: SMA gate variants (SMA20-200, crossovers, stacks), breakout confirmation layer (BB30, NWE, cRSI, SR Breaks, Vol spike, Stoch_MTM, confluence), ATR% risk gate. Dual-dataset → Vol spike 1.5× recommended, SMA upgrade for top-N selection, ATR% gate rejected | **Done** | §14 Entry quality |

## Shared Utilities

| File | Purpose |
|------|---------|
| `tf_config.py` | Timeframe definitions (4H/1D/1W), combo KPI lists, exit params |
| `visualize_results.py` | Charting helpers for backtest results |
| `fetch_sample300.py` | Builds `sample_universe/sample_300.csv` from yfinance |
| `optimization/` | Generic grid search, objective functions, sampling |

## Legacy / Archive

| Directory | Why archived |
|-----------|-------------|
| `kpi_optimization/legacy/` | Phases 1-10 and early v11 variants. All superseded by v7+ |
| `_archive/indicator_config_optimiser/` | Broken path references, never wired to main pipeline |
| `_archive/kpi_optimiser/` | UK-spelling duplicate of kpi_optimization, unused |

## Outputs

Research outputs live in `kpi_optimization/outputs/` organized by timeframe:

```
outputs/
├── 4H/    # Phase results for 4-hour timeframe
├── 1D/    # Phase results for daily timeframe
├── 1W/    # Phase results for weekly timeframe
└── all/   # Cross-timeframe aggregate results
```

280 PNGs, 38 JSONs, 6 CSVs, 31 MDs. These are generated artifacts — do not edit manually.
