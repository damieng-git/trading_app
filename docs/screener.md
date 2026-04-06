# Daily Screener

> Scans ~3,800 US + EU stocks for new C3/C4 combo entries within the last 2 bars.
> Outputs to the "Entry Stocks" group (full overwrite each run) with full dashboard treatment.

---

## Quick Start

```bash
# Full run: scan → detect → inject → build dashboard
python -m trading_dashboard screener run

# Scan only, skip dashboard build
python -m trading_dashboard screener run --no-dashboard

# Dry run: show universe stats
python -m trading_dashboard screener run --dry-run

# Regenerate universe.csv from index sources
python -m trading_dashboard screener seed-universe
```

### Interactive Mode (Dashboard UI)

In server mode (`python3 -m apps.dashboard.serve_dashboard`), click the **⚙ Scan** button
in the topbar. A progress bar slides down showing real-time status:

| Phase | # | Pct range | What happens |
|-------|---|-----------|-------------|
| init | 0 | 0% | Bootstrap |
| download | 1 | 0-36% | Batch OHLCV download (1D only, 50-ticker chunks) |
| filter | 2 | 36-37% | Quality filters (price, volume, market cap) |
| enrich | 3 | 37-51% | Lean enrichment + C3/C4 detection |
| detect | 4 | 51-60% | Ranking + saving results |
| inject | 5 | 62% | Write combo tickers to `entry_stocks.csv` |
| build | 6 | 70% | Full dashboard build (`--mode all`: download 3 TFs + enrich + HTML) |
| validate | 7 | 92% | Verify data files exist for all new tickers |

On completion, the bar turns green and "Entry Stocks" group is auto-selected.
Close the bar with the ✕ button.

**Architecture**: The scan runs in a background thread via the `_ScanState` singleton in `serve_dashboard.py`. This means:

- **Page refresh is safe** — the scan continues running. On reload, the frontend auto-detects the running scan via `GET /api/scan/status` and reconnects.
- **No concurrent scans** — clicking "Scan" while one is running just subscribes to the existing scan.
- **Progress replay** — all events are stored in an in-memory log. New SSE clients receive the full history, then stream live.

**SSE events** (`GET /api/scan`):

| Event | When | UI behavior |
|-------|------|------------|
| `progress` | During pipeline | Updates progress bar, label, detail, ETA |
| `complete` | All phases succeeded, all data files found | Green bar, refreshes groups |
| `failed` (severity: `partial`) | Build succeeded but some tickers missing data files | Amber bar, shows `X/Y tickers ready`, still refreshes groups |
| `failed` (severity: `critical`) | Screener or build crashed | Red bar, shows error message |

**Status endpoint**: `GET /api/scan/status` returns `{"running": true/false}` — used by the frontend on page load to auto-reconnect.

---

## How It Works

### 1. Universe Loading

Source: `configs/universe.csv` (~3,800 tickers)

Composed from:
- **US**: All NYSE + NASDAQ + AMEX listed equities via NASDAQ screener API, pre-filtered to market cap >= $300M and price >= $5 (~3,200 tickers)
- **EU**: Hardcoded index constituents (~600 tickers): FTSE 100/250, DAX, CAC 40, SMI, AEX, IBEX 35, FTSE MIB, Nordic (OMX Stockholm/Copenhagen/Helsinki), BEL 20, OBX, WIG 20, ATX

Regenerate with `python3 apps/screener/_build_universe.py` (fetches live data from NASDAQ API).

### 2. Quality Filters

| Filter | Threshold |
|--------|-----------|
| Geography | US + EU only |
| Min price | $5 |
| Min daily dollar volume (20-day avg) | $2M |
| Min market cap | $300M |
| Min data history | 250 bars |
| SR Break pre-filter | SR_state transition to 1 within last 10 bars (raw OHLCV) |
| SMA gate | SMA20 > SMA200 |
| Volume spike | Vol ≥ 1.5× Vol_MA20 within last 5 bars |
| Onset detection | C3/C4 must transition FALSE→TRUE (not continuation) |
| Excluded | Leveraged/inverse products, index tickers |

### 3. Lean Enrichment

Only the 5 indicators required for 1D C3/C4 detection are computed, plus SMA200 and SMA20:

| Indicator | C3 (1D) | C4 (1D) | Entry gate |
|-----------|---------|---------|------------|
| Nadaraya-Watson Smoother | x | x | |
| Madrid Ribbon | x | x | |
| Volume > MA20 | x | | Vol spike |
| GK Trend Ribbon | | x | |
| cRSI | | x | |
| SMA200 | | | SMA20>SMA200 |
| SMA20 | | | SMA20>SMA200 |

SR Breaks are computed **before** lean enrichment on raw OHLCV data. This takes ~90 seconds vs ~15+ minutes for the full 25-indicator enrichment.

### 4. Combo Detection

A stock is flagged when **all KPIs in C3 or C4 are bullish on the latest 2 bars** (current bar or previous bar):

```
for each of the last 2 bars (current, -1):
    if combo is TRUE on this bar AND FALSE on the bar before → new entry found
```

This captures stocks with **new** C3 or C4 entries — combo transitions from false to true within the last 2 trading days. Stocks already in long-running combos are excluded. Stocks can appear in both C3 and C4 lists simultaneously.

> **Note**: The screener scans on 1D only. Entry gates (SMA20>SMA200, volume spike, SR break, overextension) are configured per-strategy in `config.json → entry_gates`. See `docs/strategy_pipeline_design.md` for details.

### 5. Ranking

- C3 hits: sorted by trend score (descending)
- C4 hits: sorted by trend score (descending)
- Merged into a single deduplicated list (C4 hits take priority for duplicates)

### 6. Output

| Output | Path | Content |
|--------|------|---------|
| CSV (results) | `apps/screener/configs/screener_results.csv` | Symbol, combo type, entry bar, trend score |
| JSON | `data/dashboard_artifacts/daily_screener.json` | Full metadata: universe size, filters, all hits |
| CSV (group) | `apps/dashboard/configs/lists/entry_stocks.csv` | Combo tickers written to the "Entry Stocks" group (full overwrite each run) |

### 7. Dashboard Integration

After writing to `entry_stocks.csv`, the pipeline triggers a full dashboard build:
- Downloads history (1D, 1W, 2W, 1M — 24 months)
- Enriches with all 25+ indicators
- Generates charts, screener table data, strategy signals

Entry Stocks get the exact same treatment as portfolio/watchlist stocks.
Portfolio tickers are excluded from entry_stocks to avoid duplicates.

---

## File Map

| File | Role |
|------|------|
| `daily_screener.py` | Pipeline orchestrator |
| `lean_enrichment.py` | Minimal indicator computation for C3/C4 detection |
| `universe.py` | Universe CSV loader + quality filters |
| `_build_universe.py` | Generates universe.csv from NASDAQ API (US) + hardcoded EU index lists |
| `seed_universe.py` | CLI helper for `screener seed-universe` |
| `configs/universe.csv` | Ticker universe (~3,800 US+EU stocks) |
| `configs/screener_results.csv` | Latest screener output |
| `serve_dashboard.py` (endpoint) | `GET /api/scan` — SSE stream via `_ScanState` singleton (background thread + reconnection) |

---

## Dashboard Column Behavior

### Action Column

Uses `combo_bars` — bars since the **most recent combo activation** (C3 entry or C4 scale-up):

| Event | combo_bars | bars_held | Action Badge |
|-------|-----------|-----------|-------------|
| C3 entered today | 0 | 0 | ENTRY 1x |
| C4 scaled up today on 45-bar position | 0 | 45 | ENTRY 1.5x |
| C4 scaled 10 bars ago on 45-bar position | 10 | 45 | HOLD 10b |
| C3 position, no C4, 8 bars old | 8 | 8 | HOLD 8b |
| Exit triggered on current bar | — | — | EXIT (red) |
| Exit triggered 1 bar ago | — | — | EXIT 1b (red) |
| Exit triggered 2 bars ago | — | — | EXIT 2b (red) |
| Exit > 2 bars ago | — | — | exit Nb ago (muted) |

`bars_held` is still used for exit logic (T-bar grace, M-bar checkpoint). The tooltip shows both when they differ.

### Combo Column

Only shows combo timing when the stock has an **active position**. When the position is flat (exited or never entered), it shows "FLAT" regardless of when the last combo occurred historically.

| Position State | Combo Data | Combo Column |
|----------------|-----------|-------------|
| ENTRY / SCALE | combo on current bar | **C3** or **C4** badge |
| HOLD | combo N bars ago | **N bars** (amber ≤ 3, muted otherwise) |
| EXIT / FLAT | any | **FLAT** (muted) |

Sorting: active positions with combos sort first (most recent combo highest), FLAT stocks sort to the bottom.
