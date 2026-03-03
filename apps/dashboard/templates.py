"""
HTML/CSS/JS template functions for dashboard output.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from trading_dashboard.indicators.registry import (
    DIMENSIONS,
    DIMENSION_ORDER,
    get_dimension_map,
    get_dimension_label,
    get_all as _get_all_indicators,
    get_strategies as _get_strategies,
    get_kpi_trend_order as _get_kpi_trend_order,
)

_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"

def _load_config_field(field: str, default: Any = None) -> Any:
    """Load a single field from config.json, returning default on error."""
    try:
        cfg = json.loads((_CONFIGS_DIR / "config.json").read_text(encoding="utf-8"))
        return cfg.get(field, default)
    except Exception as exc:
        logger.warning("Failed to load config field %r: %s", field, exc)
        return default



def write_lazy_dashboard_shell_html(
    *,
    output_path: Path,
    fig_source: str,
    assets_rel_dir: str | None,
    symbols: List[str],
    symbol_groups: dict[str, list[str]] | None,
    timeframes: List[str],
    symbol_display: Dict[str, str],
    symbol_to_asset: Dict[str, str] | None,
    run_metadata: dict,
    data_health: dict,
    symbol_meta: dict,
    screener_summary: dict,
    fx_rates: Dict[str, float] | None = None,
    symbol_currencies: Dict[str, str] | None = None,
) -> None:
    """
    Write a small HTML "shell" that lazy-loads per-symbol-per-TF Plotly JSON files.

    Important: browsers may block `fetch()` when opening HTML from `file://`.
    In that case, serve `output_data/` via a local HTTP server, e.g.:
      python -m http.server 8000
    """

    def _esc(s: str) -> str:
        return (
            (s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
        )

    def _display(s: str) -> str:
        name = (symbol_display.get(s) or "").strip()
        return f"{s} - {name}" if name else s

    fig_source = (fig_source or "").strip().lower() or "static_js"
    if fig_source not in {"static", "static_js", "server"}:
        fig_source = "static_js"

    # Prefer offline Plotly JS to avoid any internet dependency (works with file://).
    try:
        from plotly.offline import get_plotlyjs  # type: ignore

        plotly_js = get_plotlyjs()
        # Defensive: avoid any accidental script close sequences.
        plotly_js = plotly_js.replace("</script>", "<\\/script>")
    except Exception:
        plotly_js = ""

    symbols = sorted([str(x).strip().upper() for x in symbols if str(x).strip()])
    timeframes = [str(x).strip().upper() for x in timeframes if str(x).strip()]
    default_symbol = symbols[0] if symbols else ""
    default_tf = "1W" if "1W" in timeframes else (timeframes[0] if timeframes else "")

    # Embed only small metadata; fetch big Plotly JSON on demand.
    meta_payload = json.dumps(run_metadata or {}, allow_nan=False, separators=(",", ":"))
    health_payload = json.dumps(data_health or {}, allow_nan=False, separators=(",", ":"))
    sym_meta_payload = json.dumps(symbol_meta or {}, allow_nan=False, separators=(",", ":"))
    sym_disp_payload = json.dumps(symbol_display or {}, allow_nan=False, separators=(",", ":"))
    sym_to_asset_payload = json.dumps(symbol_to_asset or {}, allow_nan=False, separators=(",", ":"))
    screener_payload = json.dumps(screener_summary or {}, allow_nan=False, separators=(",", ":"))
    groups_payload = json.dumps(symbol_groups or {}, allow_nan=False, separators=(",", ":"))
    exit_params_payload = json.dumps(
        _load_config_field("exit_params", {"4H":{"T":4,"M":48,"K":4.0},"1D":{"T":4,"M":40,"K":4.0},"1W":{"T":2,"M":20,"K":4.0},"2W":{"T":2,"M":10,"K":4.0},"1M":{"T":1,"M":6,"K":4.0}}),
        separators=(",", ":")
    )
    _kpi_w = _load_config_field("kpi_weights", {})
    max_trend_score = sum(float(v) for v in _kpi_w.values()) if _kpi_w else 28.2
    try:
        from trading_dashboard.kpis.catalog import KPI_ORDER  # local import (keeps UI aligned)

        kpi_keys_payload = json.dumps(list(KPI_ORDER), allow_nan=False, separators=(",", ":"))
    except Exception:
        kpi_keys_payload = "[]"

    # Strategy setups: {strategy_name: {label, kpis: [kpi_names]}}
    _strategy_setups_raw = _load_config_field("strategy_setups", {})
    _strategy_kpis_map: dict = {}
    try:
        for strat in _get_strategies():
            _strategy_kpis_map[strat] = _get_kpi_trend_order(strat)
    except Exception as exc:
        logger.warning("Failed to build strategy KPI map: %s", exc)
    strategy_setups_payload = json.dumps({
        "setups": _strategy_setups_raw,
        "kpis_by_strategy": _strategy_kpis_map,
    }, allow_nan=False, separators=(",", ":"))

    def _build_dimension_map_payload() -> str:
        """Build JSON mapping {indicator_label: "Dimension Label"} for the JS UI.

        Covers both KPI names and non-KPI trace labels so every indicator
        gets routed to its registered dimension in the indicator strip.
        """
        dim_map = get_dimension_map()  # {kpi_name: dimension_key}
        result = {kpi: get_dimension_label(dk) for kpi, dk in dim_map.items()}
        _key_to_dim = {ind.key: ind.dimension for ind in _get_all_indicators()}
        _trace_labels = {
            "ATR": "ATR",
            "VOL_MA": "Volume + MA20",
        }
        for reg_key, trace_label in _trace_labels.items():
            dk = _key_to_dim.get(reg_key)
            if dk and trace_label not in result:
                result[trace_label] = get_dimension_label(dk)
        for ind in _get_all_indicators():
            for lbl in (ind.kpi_name, ind.title):
                if lbl and lbl not in result:
                    result[lbl] = get_dimension_label(ind.dimension)
        return json.dumps(result, allow_nan=False, separators=(",", ":"))

    fx_rates_payload = json.dumps(fx_rates or {}, allow_nan=False, separators=(",", ":"))
    sym_currencies_payload = json.dumps(symbol_currencies or {}, allow_nan=False, separators=(",", ":"))

    _static_dir = Path(__file__).resolve().parent / "static"
    _css_path = _static_dir / "dashboard.css"
    _chart_builder_path = _static_dir / "chart_builder.js"
    _js_module_paths = [
        _static_dir / "dashboard_screener.js",
        _static_dir / "dashboard_pnl.js",
        _static_dir / "dashboard_modals.js",
        _static_dir / "dashboard.js",
    ]
    if not _css_path.exists():
        raise FileNotFoundError(f"Missing dashboard CSS: {_css_path}. Ensure apps/dashboard/static/dashboard.css exists.")
    for p in _js_module_paths:
        if not p.exists():
            raise FileNotFoundError(f"Missing dashboard JS: {p}. Ensure apps/dashboard/static/ has all module files.")
    _css_text = _css_path.read_text(encoding="utf-8")
    _chart_builder_text = _chart_builder_path.read_text(encoding="utf-8") if _chart_builder_path.exists() else ""
    _js_text = "\n".join(_p.read_text(encoding="utf-8") for _p in _js_module_paths)

    def _tf_btn(tf: str) -> str:
        lbl = "D" if tf == "1D" else "W" if tf == "1W" else tf
        return f'<div class="tab-tf-btn" data-tf="{tf}">{lbl}</div>'
    tf_buttons = "".join(_tf_btn(tf) for tf in timeframes)
    tf_options = "".join(f'<option value="{tf}">{tf}</option>' for tf in timeframes)

    def _build_head_section() -> str:
        """Build the <head> section with Plotly JS and CSS."""
        return f"""  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Trading Dashboard (Lazy)</title>
  <script>{plotly_js}</script>
  <style>
{_css_text}
  </style>
"""

    def _build_sidebar() -> str:
        """Build the sidebar HTML fragment."""
        return """        <div id="symbolListTools">
          <label for="symbolListSearch" class="visually-hidden">Filter symbols by ticker or exchange suffix</label>
          <input id="symbolListSearch" type="search" placeholder="Filter symbols (e.g. .DE, AAPL)" aria-label="Filter symbols by ticker or exchange suffix" />
        </div>
        <div id="sidebarSort">
          <div class="btn active" data-sort="name">A-Z</div>
          <div class="btn" data-sort="delta">% Chg</div>
          <div class="btn" data-sort="trend">Score</div>
          <div class="btn" data-sort="combo" title="Show only stocks with active combo">Combo</div>
        </div>
        <div id="symbolList" aria-label="Symbols"></div>
"""

    def _build_main_content() -> str:
        """Build the main body content (topbar, app, screener, info, pnl, modals)."""
        # Inline the body HTML - uses tf_buttons, tf_options, _build_sidebar from closure
        return _get_body_content()

    def _build_scripts() -> str:
        """Build the script section with config and JS."""
        return f"""  <script>
    const FIG_SOURCE = {json.dumps(fig_source)};
    const ASSETS_DIR = {json.dumps(assets_rel_dir or "")};
    const SYMBOLS = {json.dumps(symbols)};
    const SYMBOL_GROUPS = {groups_payload};
    const TIMEFRAMES = {json.dumps(timeframes)};
    const KPI_KEYS = {kpi_keys_payload};
    const RUN_META = {meta_payload};
    const DATA_HEALTH = {health_payload};
    const SYMBOL_META = {sym_meta_payload};
    const SYMBOL_DISPLAY = {sym_disp_payload};
    const SYMBOL_TO_ASSET = {sym_to_asset_payload};
    const SCREENER = {screener_payload};
    const EXIT_PARAMS_CFG = {exit_params_payload};
    const MAX_TREND_SCORE = {max_trend_score};
    const DIMENSION_MAP = {_build_dimension_map_payload()};
    const DIMENSION_ORDER = {json.dumps([DIMENSIONS[k] for k in DIMENSION_ORDER])};
    const DEFAULT_SYMBOL = {json.dumps(default_symbol)};
    const DEFAULT_TF = {json.dumps(default_tf)};
    const FX_TO_EUR = {fx_rates_payload};
    const SYMBOL_CURRENCIES = {sym_currencies_payload};
    const STRATEGY_SETUPS = {strategy_setups_payload};

  </script>
  <script>
{_chart_builder_text}
  </script>
  <script>
{_js_text}
  </script>
"""

    def _get_body_content() -> str:
        return f"""  <div class="topbar">
    <div class="topbarRow">
      <div class="nav-tabs" role="tablist" aria-label="Dashboard tabs">
        <div id="tabScreener" class="nav-tab" role="tab" tabindex="0" aria-selected="false">Screener</div>
        <div id="tabStrategy" class="nav-tab" role="tab" tabindex="-1" aria-selected="false">Strategy</div>
        <div id="tabChart" class="nav-tab active" role="tab" tabindex="-1" aria-selected="true">Charts</div>
        <div id="tabPnl" class="nav-tab" role="tab" tabindex="-1" aria-selected="false">P&amp;L</div>
        <div id="tabInfo" class="nav-tab" role="tab" tabindex="-1" aria-selected="false">Info</div>
      </div>
      <div class="topbar-sep"></div>
      <div id="themeToggle" title="Toggle dark/light mode">&#9790;</div>
      <div class="topbar-sep"></div>
      <button id="eurToggle" class="eur-toggle" title="Toggle prices to EUR">Local</button>
      <div class="topbar-sep"></div>
      <button id="scanBtn" class="scan-btn" title="Run stock screener">&#9881; Scan</button>
      <button id="refreshBtn" class="scan-btn" title="Re-download &amp; re-enrich all data">&#8635; Refresh</button>
    </div>
  </div>
  <div id="scanBar" class="scan-bar hidden">
    <div class="scan-bar-inner">
      <div id="scanFill" class="scan-fill"></div>
    </div>
    <span id="scanLabel" class="scan-label">Initialising…</span>
    <span id="scanDetail" class="scan-detail"></span>
    <span id="scanEta" class="scan-eta"></span>
    <button id="scanClose" class="scan-close hidden" title="Close">&#10005;</button>
  </div>
  <div id="app">
    <div id="appHeader">
      <div class="tab-filter-bar" data-scope="chart">
        <div class="filter-group">
          <div class="filter-label">Strategy</div>
          <div id="strategyDropdown" class="tab-group-dropdown" data-scope="chart">
            <div id="strategyTrigger" class="tab-group-trigger">Strategy v6 &#9662;</div>
            <div id="strategyMenu" class="tab-group-menu group-menu"></div>
          </div>
        </div>
        <div class="filter-sep-v"></div>
        <div class="filter-group">
          <div class="filter-label">Stock List</div>
          <div class="tab-group-dropdown" data-scope="chart">
            <div class="tab-group-trigger">All &#9662;</div>
            <div class="tab-group-menu group-menu"></div>
          </div>
        </div>
        <div class="filter-sep-v"></div>
        <div class="filter-group">
          <div class="filter-label">Timeframe</div>
          <div class="tab-tf-selector" data-scope="chart">
            {tf_buttons}
          </div>
        </div>
      </div>
      <div id="stockTitle"></div>
      <div id="signalCard"></div>
      <div id="status"></div>
      <div id="dataWarn" class="warn"></div>
      <div id="fileWarn" class="warn"></div>
      <div id="indicatorWrap">
        <div id="indicatorToggle" class="btn" title="Show/hide indicator panel">Indicators &#9660;</div>
        <div id="indicatorStrip" aria-label="Indicators"></div>
      </div>
    </div>
    <div id="appBody">
      <main id="main">
        <div style="position:relative;">
          <div id="loadingOverlay">
            <div class="spinner"></div>
            <div id="loadingText">Loading…</div>
          </div>
          <div id="chartUpper"></div>
        </div>
        <!-- Strategy tab panels -->
        <div id="chartPnl"></div>
        <div id="strategySpacing" style="height:24px;"></div>
        <div id="chartTs"></div>
        <!-- Charts tab panels -->
        <div id="oscWrap" style="display:none;">
          <div id="oscToggle" class="panel-toggle">Oscillators &#9654;</div>
          <div id="chartOsc" class="osc-collapsed"></div>
        </div>
        <div id="chartLower" style="display:none;"></div>
      </main>
      <div id="sidebarResizer"></div>
      <aside id="sidebar">
{_build_sidebar()}
      </aside>
    </div>
  </div>
  <div id="comboTooltip"></div>
  <div id="screenerWrap" style="display:none;">
    <div id="screenerTools">
      <label for="screenerSearch" class="visually-hidden">Filter screener symbols by ticker or exchange</label>
      <input id="screenerSearch" type="search" placeholder="Filter symbols (e.g. DE, PA, DASH)" aria-label="Filter screener symbols by ticker or exchange" />
      <div id="screenerFilters">
        <div class="tab-group-dropdown" data-scope="screener">
          <div class="tab-group-trigger">All &#9662;</div>
          <div class="tab-group-menu group-menu"></div>
        </div>
        <div class="tab-tf-selector" data-scope="screener">
          {tf_buttons}
        </div>
        <span class="filter-sep"></span>
        <div class="btn active" data-filter="all" title="Show all symbols">All</div>
        <span class="filter-sep"></span>
        <span class="filter-label">Trend:</span>
        <div class="btn" data-filter="bull" title="TrendScore &gt; 0">Bullish</div>
        <div class="btn" data-filter="bear" title="TrendScore &lt; 0">Bearish</div>
        <div class="btn" data-filter="strong" title="Absolute TrendScore &ge; 5">Strong (&ge;5)</div>
        <div class="btn" data-filter="improving" title="Trend delta &gt; 0 over last 3 bars — momentum turning">Improving</div>
        <span class="filter-sep"></span>
        <span class="filter-label">Signal:</span>
        <div class="btn" data-filter="combo" title="At least one combo (C3/C4) active on latest bar">Combo</div>
        <div class="btn" data-filter="new_combo" title="Combo just appeared — was not active on previous bar">New Combo</div>
        <div class="btn" data-filter="recent_combo" title="Combo signal within the last 3 bars">Recent (&le;3)</div>
        <span class="filter-sep"></span>
        <span class="filter-label">Position:</span>
        <div class="btn" data-filter="active_position" title="Stocks with active ENTRY, SCALE, or HOLD signals">Active</div>
        <div class="btn" data-filter="entry_signal" title="Stocks with new ENTRY or SCALE signals only">Entry/Scale</div>
        <span class="filter-sep"></span>
        <span class="filter-label">Analyst:</span>
        <div class="btn" data-filter="buy" title="Analyst consensus is Buy or Strong Buy">Buy Rating</div>
        <span class="filter-sep"></span>
        <span class="filter-label">Strategy:</span>
        <div class="btn" data-filter="strat_active" title="Any strategy active (entry or hold)">Any Active</div>
        <div class="btn" data-filter="strat_dip" title="Dip Buy entry signal">Dip Buy</div>
        <div class="btn" data-filter="strat_swing" title="Swing entry or hold">Swing</div>
        <div class="btn" data-filter="strat_trend" title="Trend Position entry or hold">Trend</div>
      </div>
      <button id="btnAddTicker" class="btn btn-add" type="button" title="Add ticker to watchlist">+ Add</button>
      <button id="btnExport" class="btn" type="button">Export CSV</button>
    </div>
    <div id="screenerBox">
      <div id="screener"></div>
    </div>
  </div>

  <div id="infoWrap" style="display:none;">
    <div class="info-panel">

      <h2 class="info-h2">Trading Strategy — Entry v6 + Exit Flow v4</h2>
      <p class="info-sub">Status: Locked (v15) — Feb 2026 &nbsp;|&nbsp; PF-optimized combos (Phase 16) &nbsp;|&nbsp; Backtest: ~295 stocks, out-of-sample (last 30%)</p>

      <!-- ═══════════════════════════════════════════════ -->
      <!--  SECTION 1 — Full Strategy Workflow Chart      -->
      <!-- ═══════════════════════════════════════════════ -->
      <section class="info-section">
        <h3>1. Strategy Workflow</h3>

        <div class="wf-chart">
          <!-- Row 1: Entry decision -->
          <div class="wf-row">
            <div class="wf-node wf-start">New bar arrives</div>
          </div>
          <div class="wf-arrow"></div>

          <div class="wf-row">
            <div class="wf-node wf-decision">Position open?</div>
          </div>

          <div class="wf-branch">
            <!-- LEFT: No position -->
            <div class="wf-leg">
              <div class="wf-leg-label">NO</div>
              <div class="wf-arrow"></div>
              <div class="wf-node wf-decision">All C3 KPIs bullish?</div>
              <div class="wf-branch-inner">
                <div class="wf-leg-sm">
                  <div class="wf-leg-label">YES</div>
                  <div class="wf-arrow-sm"></div>
                  <div class="wf-node wf-action wf-c3">ENTER at 1.0x<br><small>C3 combo fires</small></div>
                  <div class="wf-arrow-sm"></div>
                  <div class="wf-node wf-decision wf-small">C4 also bullish?</div>
                  <div class="wf-branch-inner">
                    <div class="wf-leg-sm">
                      <div class="wf-leg-label">YES</div>
                      <div class="wf-arrow-sm"></div>
                      <div class="wf-node wf-action wf-c4">Scale to 1.5x<br><small>from bar 1</small></div>
                    </div>
                    <div class="wf-leg-sm">
                      <div class="wf-leg-label">NO</div>
                      <div class="wf-arrow-sm"></div>
                      <div class="wf-node wf-wait">Hold at 1.0x</div>
                    </div>
                  </div>
                </div>
                <div class="wf-leg-sm">
                  <div class="wf-leg-label">NO</div>
                  <div class="wf-arrow-sm"></div>
                  <div class="wf-node wf-wait">Stay flat — wait</div>
                </div>
              </div>
            </div>

            <!-- RIGHT: In position -->
            <div class="wf-leg">
              <div class="wf-leg-label">YES</div>
              <div class="wf-arrow"></div>
              <div class="wf-node wf-decision">C4 fires &amp; not scaled yet?</div>
              <div class="wf-branch-inner">
                <div class="wf-leg-sm">
                  <div class="wf-leg-label">YES</div>
                  <div class="wf-arrow-sm"></div>
                  <div class="wf-node wf-action wf-c4">Scale to 1.5x</div>
                </div>
                <div class="wf-leg-sm">
                  <div class="wf-leg-label">NO</div>
                  <div class="wf-arrow-sm"></div>
                  <div class="wf-node wf-wait">Keep current size</div>
                </div>
              </div>
              <div class="wf-arrow"></div>
              <div class="wf-node wf-exit-header">Run Exit Checks &#x2193;</div>
            </div>
          </div>
        </div>
      </section>

      <!-- ═══════════════════════════════════════════════ -->
      <!--  SECTION 1b — Entry Gate Filters               -->
      <!-- ═══════════════════════════════════════════════ -->
      <section class="info-section">
        <h3>1b. Entry Gate Filters (v5)</h3>
        <p>Even when a C3 <b>onset</b> fires (transition from off→on), the entry is <b>blocked</b> if any of these filters fails:</p>

        <table class="info-tbl">
          <thead><tr><th>Filter</th><th>Applies to</th><th>Rule</th><th>Rationale</th></tr></thead>
          <tbody>
            <tr>
              <td><b>Onset-only</b></td>
              <td>All TFs</td>
              <td>C3 must transition from FALSE→TRUE (not continuation)</td>
              <td>Eliminates continuation noise, focuses on fresh entries. Phase 13: onset entries have PF 7.5 vs 3.3 for continuations.</td>
            </tr>
            <tr>
              <td><b>SMA20 &gt; SMA200</b></td>
              <td>1D, 1W</td>
              <td>SMA(20) &ge; SMA(200)</td>
              <td>Structural uptrend gate. Phase 14: HR 70.1%, PF 7.8 (vs 69.3%/7.1 with Close&gt;SMA200). Keeps 87% of trades.</td>
            </tr>
            <tr>
              <td><b>Volume spike</b></td>
              <td>All TFs</td>
              <td>Volume &ge; 1.5&times; Vol_MA20 within last 5 bars</td>
              <td>Momentum confirmation. Phase 14: HR +2.7pp, PF 7.1→8.1, keeps 69% of trades.</td>
            </tr>
            <tr>
              <td><b>Overextension</b></td>
              <td>1W only</td>
              <td>Close &le; 115% of Close[5 bars ago]</td>
              <td>Block entry at peak of sharp rallies. Worst trade -38.6% &rarr; -26.7%. PF +0.6, HR +1.2pp.</td>
            </tr>
          </tbody>
        </table>
        <p class="info-note">Daily screener adds an additional pre-filter: <b>SR Break N=10</b> — stock must have had an SR support/resistance breakout within the last 10 bars (computed on raw OHLCV before lean enrichment).</p>
        <p class="info-note">Implemented in <code>strategy.py</code>. Applied in <code>compute_position_status</code>, <code>compute_trailing_pnl</code>, chart overlay, and JS position model.</p>
      </section>

      <!-- ═══════════════════════════════════════════════ -->
      <!--  SECTION 2 — Exit Flow Workflow                -->
      <!-- ═══════════════════════════════════════════════ -->
      <section class="info-section">
        <h3>2. Exit Flow v4 — Decision Tree (unchanged)</h3>
        <p class="info-note" style="font-style:normal;color:var(--fg);margin-bottom:10px;">
          Evaluated every bar while in position. The <b>highest active combo</b> governs exit rules
          (C4 if scaled, else C3). Exit logic is unchanged from v4.
        </p>
        <div class="wf-chart">
          <div class="wf-row">
            <div class="wf-node wf-start">Each bar while in position</div>
          </div>
          <div class="wf-arrow"></div>

          <!-- ATR stop — always checked first -->
          <div class="wf-row">
            <div class="wf-node wf-decision">Price &lt; ATR stop?<br><small><code>stop = entry − K × ATR(14)</code></small></div>
          </div>
          <div class="wf-branch">
            <div class="wf-leg">
              <div class="wf-leg-label">YES</div>
              <div class="wf-arrow"></div>
              <div class="wf-node wf-exit">EXIT — ATR stop hit</div>
            </div>
            <div class="wf-leg">
              <div class="wf-leg-label">NO</div>
              <div class="wf-arrow"></div>
              <div class="wf-node wf-decision">Bars since entry ≤ T?<br><small>(lenient period)</small></div>
              <div class="wf-branch-inner">
                <div class="wf-leg-sm">
                  <div class="wf-leg-label">YES — Stage 1</div>
                  <div class="wf-arrow-sm"></div>
                  <div class="wf-node wf-decision wf-small">ALL KPIs bearish?</div>
                  <div class="wf-branch-inner">
                    <div class="wf-leg-sm">
                      <div class="wf-leg-label">YES</div>
                      <div class="wf-arrow-sm"></div>
                      <div class="wf-node wf-exit">EXIT — full invalidation</div>
                    </div>
                    <div class="wf-leg-sm">
                      <div class="wf-leg-label">NO</div>
                      <div class="wf-arrow-sm"></div>
                      <div class="wf-node wf-wait">HOLD</div>
                    </div>
                  </div>
                </div>
                <div class="wf-leg-sm">
                  <div class="wf-leg-label">NO — Stage 2</div>
                  <div class="wf-arrow-sm"></div>
                  <div class="wf-node wf-decision wf-small">≥ 2 KPIs bearish?</div>
                  <div class="wf-branch-inner">
                    <div class="wf-leg-sm">
                      <div class="wf-leg-label">YES</div>
                      <div class="wf-arrow-sm"></div>
                      <div class="wf-node wf-exit">EXIT — KPI invalidation</div>
                    </div>
                    <div class="wf-leg-sm">
                      <div class="wf-leg-label">NO</div>
                      <div class="wf-arrow-sm"></div>
                      <div class="wf-node wf-decision wf-small">Checkpoint? (every M bars)</div>
                      <div class="wf-branch-inner">
                        <div class="wf-leg-sm">
                          <div class="wf-leg-label">All KPIs bull</div>
                          <div class="wf-arrow-sm"></div>
                          <div class="wf-node wf-action wf-c3">RESET ATR stop &#x2191;<br><small>stop = price − K × ATR</small></div>
                        </div>
                        <div class="wf-leg-sm">
                          <div class="wf-leg-label">Any KPI bear</div>
                          <div class="wf-arrow-sm"></div>
                          <div class="wf-node wf-exit">EXIT — checkpoint fail</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <h4>Exit Parameters by Timeframe</h4>
        <table class="info-tbl">
          <thead><tr><th>TF</th><th>T (lenient bars)</th><th>M (checkpoint interval)</th><th>K (ATR multiplier)</th><th>ATR period</th><th>Hard cap</th></tr></thead>
          <tbody>
            <tr><td><b>4H</b></td><td>4 bars</td><td>48 bars</td><td>4.0</td><td>14</td><td>500 bars</td></tr>
            <tr><td><b>1D</b></td><td>4 bars</td><td>40 bars</td><td>4.0</td><td>14</td><td>500 bars</td></tr>
            <tr><td><b>1W</b></td><td>2 bars</td><td>20 bars</td><td>4.0</td><td>14</td><td>500 bars</td></tr>
          </tbody>
        </table>
      </section>

      <!-- ═══════════════════════════════════════════════ -->
      <!--  SECTION 3 — Entry Details + Position Sizing   -->
      <!-- ═══════════════════════════════════════════════ -->
      <section class="info-section">
        <h3>3. Entry Combos — Detailed</h3>
        <p><b>C3 (Combo)</b> — Base entry. 3 KPIs must all be bullish. Optimized for <em>total P&amp;L</em>. Opens at <b>1.0x</b>.</p>
        <p><b>C4 (Golden Combo)</b> — Scale-up only. 4 KPIs must all be bullish. Optimized for <em>P&amp;L with HR ≥ 65%</em>. Never opens a position independently — only adds +50% when C3 is already open.</p>

        <p class="info-note" style="font-style:normal;color:var(--fg);margin:10px 0 8px;">
          <b>Combo detection is all-or-nothing:</b> every KPI in the combo must be bullish (= 1) simultaneously.
          There is no weighting between KPIs for entry — a combo either fires or it doesn't.
        </p>

        <table class="info-tbl combo-detail">
          <thead>
            <tr><th>TF</th><th>Level</th><th>KPI</th><th>Category</th><th>Role in Combo</th></tr>
          </thead>
          <tbody>
            <tr class="tf-group"><td rowspan="7"><b>4H</b></td><td rowspan="3" class="combo-level c3-level">C3</td>
              <td>Nadaraya-Watson Smoother</td><td>Trend</td><td>Must be bullish</td></tr>
            <tr><td>DEMA</td><td>Trend (Double EMA)</td><td>Must be bullish</td></tr>
            <tr><td>Stoch_MTM</td><td>Momentum</td><td>Must be bullish</td></tr>
            <tr class="c4-sep"><td rowspan="4" class="combo-level c4-level">C4</td>
              <td>Nadaraya-Watson Smoother</td><td>Trend</td><td>Must be bullish</td></tr>
            <tr><td>Madrid Ribbon</td><td>Multi-Trend</td><td>Must be bullish</td></tr>
            <tr><td>GK Trend Ribbon</td><td>Multi-Trend</td><td>Must be bullish</td></tr>
            <tr><td>cRSI</td><td>Momentum</td><td>Must be bullish</td></tr>

            <tr class="tf-divider"><td rowspan="7"><b>1D</b></td><td rowspan="3" class="combo-level c3-level">C3</td>
              <td>Nadaraya-Watson Smoother</td><td>Trend</td><td>Must be bullish</td></tr>
            <tr><td>Madrid Ribbon</td><td>Multi-Trend</td><td>Must be bullish</td></tr>
            <tr><td>Volume + MA20</td><td>Volume Confirm.</td><td>Must be bullish</td></tr>
            <tr class="c4-sep"><td rowspan="4" class="combo-level c4-level">C4</td>
              <td>Nadaraya-Watson Smoother</td><td>Trend</td><td>Must be bullish</td></tr>
            <tr><td>Madrid Ribbon</td><td>Multi-Trend</td><td>Must be bullish</td></tr>
            <tr><td>GK Trend Ribbon</td><td>Multi-Trend</td><td>Must be bullish</td></tr>
            <tr><td>cRSI</td><td>Momentum</td><td>Must be bullish</td></tr>

            <tr class="tf-divider"><td rowspan="7"><b>1W</b></td><td rowspan="3" class="combo-level c3-level">C3</td>
              <td>Nadaraya-Watson Smoother</td><td>Trend</td><td>Must be bullish</td></tr>
            <tr><td>DEMA</td><td>Trend (Double EMA)</td><td>Must be bullish</td></tr>
            <tr><td>cRSI</td><td>Momentum</td><td>Must be bullish</td></tr>
            <tr class="c4-sep"><td rowspan="4" class="combo-level c4-level">C4</td>
              <td>Nadaraya-Watson Smoother</td><td>Trend</td><td>Must be bullish</td></tr>
            <tr><td>Stoch_MTM</td><td>Momentum</td><td>Must be bullish</td></tr>
            <tr><td>cRSI</td><td>Momentum</td><td>Must be bullish</td></tr>
            <tr><td>Volume + MA20</td><td>Volume Confirm.</td><td>Must be bullish</td></tr>
          </tbody>
        </table>

        <h4>Position Sizing</h4>
        <table class="info-tbl">
          <thead><tr><th>Event</th><th>Position Size</th></tr></thead>
          <tbody>
            <tr><td>C3 fires (no position open)</td><td><b>1.0x</b> — open base position</td></tr>
            <tr><td>C4 fires while in C3 position</td><td><b>1.5x</b> — scale up +50%</td></tr>
            <tr><td>C4 fires simultaneously with C3</td><td><b>1.5x</b> from bar 1</td></tr>
          </tbody>
        </table>
      </section>

      <!-- ═══════════════════════════════════════════════ -->
      <!--  SECTION 4 — Sector-Specific Combos            -->
      <!-- ═══════════════════════════════════════════════ -->
      <section class="info-section">
        <h3>4. Sector-Specific Combos</h3>
        <div class="info-status-box info-status-off">
          <span class="info-status-badge">NOT ACTIVE</span>
          <span>Global combos are used for all stocks. Sector-specific combos were researched but not adopted.</span>
        </div>

        <h4>Research Summary (Phase 11 v12 — 235 stocks, 11 GICS sectors)</h4>
        <p>Per-sector analysis tested whether each sector benefits from its own C3/C4 combos instead of the global ones.</p>

        <table class="info-tbl">
          <thead><tr><th>TF</th><th>C3: Sector wins</th><th>C3: Global better</th><th>C4: Reliable data?</th><th>Observation</th></tr></thead>
          <tbody>
            <tr><td><b>4H</b></td><td>10 / 11</td><td>1 / 11</td><td>1 / 11</td><td>Most sectors find a better C3, but C4 lacks data</td></tr>
            <tr><td><b>1D</b></td><td>2 / 11</td><td>9 / 11</td><td>2 / 11</td><td>Global combo dominates — already near-optimal</td></tr>
            <tr><td><b>1W</b></td><td>7 / 11</td><td>4 / 11</td><td>9 / 11</td><td>Weekly has enough data for sector C4s</td></tr>
          </tbody>
        </table>

        <h4>Why not adopted</h4>
        <ul class="info-list">
          <li><b>Complexity vs gain</b> — 33 combos (11 sectors × 3 TFs) to manage for modest aggregate improvement</li>
          <li><b>1D doesn't benefit</b> — the global combo already wins in 9/11 sectors on the highest-trade-count timeframe</li>
          <li><b>C4 too sparse</b> — 4 KPIs + HR ≥ 65% + small sector = too few trades for reliable selection</li>
          <li><b>Overfitting risk</b> — sectors with &lt;15 stocks (Energy, Utilities, Real Estate) produce unreliable results</li>
          <li><b>No universal pattern</b> — each sector favours different KPIs, suggesting curve-fitting rather than true edge</li>
        </ul>

        <h4>Notable sector-specific C3 combos (4H, for reference only)</h4>
        <table class="info-tbl">
          <thead><tr><th>Sector</th><th>Global C3 PnL</th><th>Best Sector C3</th><th>Sector PnL</th></tr></thead>
          <tbody>
            <tr><td>Consumer Cyclical</td><td>+877%</td><td>NWSm + DEMA + WT</td><td>+1,300%</td></tr>
            <tr><td>Industrials</td><td>+1,417%</td><td>NWSm + SQZ + PSAR</td><td>+1,645%</td></tr>
            <tr><td>Financial Services</td><td>+1,102%</td><td>NWSm + WT + PSAR</td><td>+1,281%</td></tr>
            <tr><td>Real Estate*</td><td>+1,726%</td><td>NWSm + OBVOsc + SupTr</td><td>+6,607%</td></tr>
          </tbody>
        </table>
        <p class="info-note">* Real Estate has only 6 stocks — results not statistically reliable. Sector optimization may be revisited with a larger universe (500+ stocks per sector).</p>
      </section>

      <!-- ═══════════════════════════════════════════════ -->
      <!--  SECTION 5 — Backtest Results                  -->
      <!-- ═══════════════════════════════════════════════ -->
      <section class="info-section">
        <h3>5. Backtest Results (OOS — C3 at 1x, C4 scale to 1.5x)</h3>
        <p class="info-note" style="font-style:normal;color:var(--fg);margin-bottom:8px;">
          v6 entry gates: onset-only + SMA20&gt;SMA200 (1D/1W) + vol spike 1.5&times; N=5 + overextension (1W).
          Optimized for <b>Profit Factor</b> (per-trade quality) — Phase 16.
        </p>
        <table class="info-tbl perf-tbl">
          <thead><tr><th>TF</th><th>Trades</th><th>HR</th><th>Avg Ret</th><th>PnL (1x)</th><th>PF</th><th>Avg Hold</th><th>C4 Scale %</th></tr></thead>
          <tbody>
            <tr><td><b>4H</b></td><td>1,361</td><td>79.4%</td><td>+5.93%</td><td>+10,385%</td><td>14.0</td><td>31 bars</td><td>44%</td></tr>
            <tr><td><b>1D</b></td><td>2,180</td><td>63.3%</td><td>+5.68%</td><td>+17,105%</td><td>5.3</td><td>25 bars</td><td>51%</td></tr>
            <tr><td><b>1W</b></td><td>418</td><td>89.0%</td><td>+20.25%</td><td>+11,725%</td><td>47.4</td><td>22 bars</td><td>49%</td></tr>
          </tbody>
        </table>
        <p class="info-note">4H: PF doubled vs v5 (+103%), HR +10.6pp. 1D: unchanged (near-optimal for PF). 1W: PF 4.5&times; vs v5, HR +16.8pp, worst trade &minus;35.6% &rarr; &minus;21.1%.</p>

        <h4>C4 Standalone Performance</h4>
        <table class="info-tbl">
          <thead><tr><th>TF</th><th>Combo</th><th>Trades</th><th>HR</th><th>Avg Ret</th><th>PnL</th><th>PF</th><th>Worst</th></tr></thead>
          <tbody>
            <tr><td><b>4H</b></td><td>NWSm + Madrid + GKTr + cRSI</td><td>1,483</td><td>69%</td><td>+4.4%</td><td>+6,580%</td><td>6.1</td><td>−16.8%</td></tr>
            <tr><td><b>1D</b></td><td>NWSm + Madrid + GKTr + cRSI</td><td>1,448</td><td>71%</td><td>+7.1%</td><td>+10,299%</td><td>5.6</td><td>−39.8%</td></tr>
            <tr><td><b>1W</b></td><td>NWSm + Stoch + cRSI + Vol>MA</td><td>168</td><td>88.1%</td><td>+17.35%</td><td>+3,913%</td><td>43.9</td><td>−12.6%</td></tr>
          </tbody>
        </table>
      </section>

      <!-- ═══════════════════════════════════════════════ -->
      <!--  SECTION 6 — TrendScore (separate concept)     -->
      <!-- ═══════════════════════════════════════════════ -->
      <section class="info-section">
        <h3>6. TrendScore Weights <span style="font-size:12px;font-weight:400;color:var(--muted);">(separate from combo detection)</span></h3>
        <p>The <b>TrendScore</b> in the screener is a weighted sum of all trend KPI states (not just combo KPIs).
        Each KPI contributes <code>weight × state</code> where state is +1 (bullish) or −1 (bearish).
        This is purely informational — it does <em>not</em> affect combo entry/exit decisions.</p>

        <h4>Combo KPIs and their TrendScore weights</h4>
        <table class="info-tbl">
          <thead><tr><th>TF</th><th>Level</th><th>KPIs (TrendScore weight)</th><th>Combined TS Weight</th></tr></thead>
          <tbody>
            <tr><td>4H</td><td class="c3-level">C3</td><td>NWSm (3.0) + DEMA (1.0) + Stoch (1.2)</td><td><b>5.2</b></td></tr>
            <tr><td>4H</td><td class="c4-level">C4</td><td>NWSm (3.0) + Madrid (0.8) + GKTr (0.8) + cRSI (1.5)</td><td><b>6.1</b></td></tr>
            <tr class="tf-divider"><td>1D</td><td class="c3-level">C3</td><td>NWSm (3.0) + Madrid (0.8) + Vol>MA (n/a)</td><td><b>3.8</b></td></tr>
            <tr><td>1D</td><td class="c4-level">C4</td><td>NWSm (3.0) + Madrid (0.8) + GKTr (0.8) + cRSI (1.5)</td><td><b>6.1</b></td></tr>
            <tr class="tf-divider"><td>1W</td><td class="c3-level">C3</td><td>NWSm (3.0) + DEMA (1.0) + cRSI (1.5)</td><td><b>5.5</b></td></tr>
            <tr><td>1W</td><td class="c4-level">C4</td><td>NWSm (3.0) + Stoch (1.2) + cRSI (1.5) + Vol>MA (n/a)</td><td><b>5.7</b></td></tr>
          </tbody>
        </table>
        <p class="info-note">"n/a" = volume-based KPIs are not in the TrendScore weight table. They participate in combo detection but don't contribute to the TrendScore number in the screener.</p>
      </section>

      <!-- ═══════════════════════════════════════════════ -->
      <!--  SECTION 7 — KPI Reference                    -->
      <!-- ═══════════════════════════════════════════════ -->
      <section class="info-section">
        <h3>7. KPI Reference</h3>
        <table class="info-tbl">
          <thead><tr><th>Short</th><th>Full Name</th><th>Category</th><th>TrendScore Weight</th><th>Used in</th></tr></thead>
          <tbody>
            <tr><td>NWSm</td><td>Nadaraya-Watson Smoother</td><td>Trend</td><td>3.0</td><td>C3 (all), C4 (4H/1D)</td></tr>
            <tr><td>DEMA</td><td>DEMA</td><td>Trend (Double EMA)</td><td>1.0</td><td>C3 (4H, 1W)</td></tr>
            <tr><td>Madrid</td><td>Madrid Ribbon</td><td>Multi-Trend</td><td>0.8</td><td>C3 (1D), C4 (4H/1D)</td></tr>
            <tr><td>GKTr</td><td>GK Trend Ribbon</td><td>Multi-Trend</td><td>0.8</td><td>C4 (4H/1D)</td></tr>
            <tr><td>Stoch</td><td>Stoch_MTM</td><td>Momentum</td><td>1.2</td><td>C3 (4H), C4 (1W)</td></tr>
            <tr><td>cRSI</td><td>cRSI</td><td>Momentum</td><td>1.5</td><td>C3 (1W), C4 (4H/1D/1W)</td></tr>
            <tr><td>Vol>MA</td><td>Volume + MA20</td><td>Volume Confirm.</td><td>—</td><td>C3 (1D), C4 (1W)</td></tr>
          </tbody>
        </table>
      </section>

    </div>
  </div>

  <div id="pnlWrap" style="display:none;">
    <div class="pnl-panel">
      <div class="tab-filter-bar" data-scope="pnl">
        <div class="filter-group">
          <div class="filter-label">Strategy</div>
          <div class="tab-group-dropdown strategy-placeholder" data-scope="pnl">
            <div class="tab-group-trigger">Strategy v6 &#9662;</div>
            <div class="tab-group-menu group-menu"></div>
          </div>
        </div>
        <div class="filter-sep-v"></div>
        <div class="filter-group">
          <div class="filter-label">Stock List</div>
          <div class="tab-group-dropdown" data-scope="pnl">
            <div class="tab-group-trigger">All &#9662;</div>
            <div class="tab-group-menu group-menu"></div>
          </div>
        </div>
        <div class="filter-sep-v"></div>
        <div class="filter-group">
          <div class="filter-label">Timeframe</div>
          <div class="tab-tf-selector" data-scope="pnl">
            {tf_buttons}
          </div>
        </div>
        <div class="pnl-sub-tabs">
          <div class="pnl-sub-tab active" data-pnl-sub="backtest">Backtest</div>
          <div class="pnl-sub-tab" data-pnl-sub="trades">My Trades</div>
        </div>
      </div>
      <div id="pnlBacktestContent">
        <div id="pnlControls">
          <div id="pnlProgress" class="pnl-progress"></div>
        </div>
        <div id="pnlStatsBar" class="pnl-stats-bar"></div>
        <div id="pnlEquityChart" style="width:100%;min-height:340px;"></div>
        <div id="pnlDrawdownChart" style="width:100%;min-height:140px;"></div>
        <div id="pnlRiskSummary" class="pnl-risk-summary"></div>
        <div id="pnlDrillDown" class="pnl-drilldown" style="display:none;"></div>
        <h3 class="pnl-section-title">Per-Symbol Breakdown</h3>
        <div id="pnlTableWrap" class="pnl-table-wrap">
          <div id="pnlTable"></div>
        </div>
      </div>
      <div id="pnlTradesContent" style="display:none;">
        <div class="trades-toolbar">
          <button id="btnEnterTrade" class="btn btn-add" type="button">+ Enter Trade</button>
          <div id="tradesStats" class="trades-stats"></div>
        </div>
        <h3 class="pnl-section-title">Open Positions</h3>
        <div id="tradesOpenTable"></div>
        <h3 class="pnl-section-title">Closed Trades</h3>
        <div id="tradesClosedTable"></div>
        <h3 class="pnl-section-title">Equity Curve</h3>
        <div id="tradesEquityChart" style="width:100%;min-height:260px;"></div>
      </div>
    </div>
  </div>

  <!-- Add Ticker Modal -->
  <div id="addTickerModal" class="modal-overlay" style="display:none;">
    <div class="modal-box">
      <div class="modal-header">
        <h3>Add Tickers to Watchlist</h3>
        <button class="modal-close" id="addTickerClose">&#10005;</button>
      </div>
      <div class="modal-body">
        <div class="modal-field">
          <label for="addTickerInput">Search ticker or company name</label>
          <div style="display:flex;gap:8px;">
            <input id="addTickerInput" type="text" placeholder="e.g. AAPL, Microsoft, IWDA, BNP.PA" aria-label="Search ticker or company name" style="flex:1;" />
            <button id="addTickerSearch" class="btn btn-search">Search</button>
          </div>
        </div>
        <div id="addTickerResults" class="modal-results"></div>
        <div id="addTickerStaging" class="add-staging" style="display:none;">
          <div class="add-staging-header">
            <span class="add-staging-label">Queued (<span id="addStagingCount">0</span>)</span>
            <button id="addTickerConfirm" class="btn btn-confirm" type="button">Confirm &amp; Enrich</button>
          </div>
          <div id="addStagingList" class="add-staging-list"></div>
        </div>
        <div id="addTickerStatus" class="modal-status"></div>
      </div>
    </div>
  </div>

  <!-- Enter Trade Modal -->
  <div id="enterTradeModal" class="modal-overlay" style="display:none;">
    <div class="modal-box">
      <div class="modal-header">
        <h3>Enter Trade</h3>
        <button class="modal-close" id="enterTradeClose">&#10005;</button>
      </div>
      <div class="modal-body">
        <div class="modal-field">
          <label>Symbol</label>
          <input id="tradeSymbol" type="text" placeholder="e.g. AAPL" />
        </div>
        <div class="modal-row">
          <div class="modal-field">
            <label>Entry Price</label>
            <input id="tradeEntryPrice" type="number" step="0.01" />
          </div>
          <div class="modal-field">
            <label>Entry Date</label>
            <input id="tradeEntryDate" type="date" />
          </div>
        </div>
        <div class="modal-row">
          <div class="modal-field">
            <label>Size</label>
            <input id="tradeSize" type="number" step="0.1" value="1.0" />
          </div>
          <div class="modal-field">
            <label>Stop Price</label>
            <input id="tradeStopPrice" type="number" step="0.01" />
          </div>
        </div>
        <div class="modal-row">
          <div class="modal-field">
            <label>Direction</label>
            <select id="tradeDirection">
              <option value="long">Long</option>
              <option value="short">Short</option>
            </select>
          </div>
          <div class="modal-field">
            <label>Timeframe</label>
            <select id="tradeTF">
              {tf_options}
            </select>
          </div>
        </div>
        <div class="modal-field">
          <label>Notes</label>
          <input id="tradeNotes" type="text" placeholder="Optional notes" />
        </div>
        <button id="tradeSubmit" class="btn btn-add" style="width:100%;margin-top:10px;">Submit Trade</button>
        <div id="tradeStatus" class="modal-status"></div>
      </div>
    </div>
  </div>

  <!-- Close Trade Modal -->
  <div id="closeTradeModal" class="modal-overlay" style="display:none;">
    <div class="modal-box">
      <div class="modal-header">
        <h3>Close Trade</h3>
        <button class="modal-close" id="closeTradeClose">&#10005;</button>
      </div>
      <div class="modal-body">
        <div id="closeTradeInfo"></div>
        <div class="modal-row">
          <div class="modal-field">
            <label>Exit Price</label>
            <input id="closeTradeExitPrice" type="number" step="0.01" />
          </div>
          <div class="modal-field">
            <label>Exit Date</label>
            <input id="closeTradeExitDate" type="date" />
          </div>
        </div>
        <button id="closeTradeSubmit" class="btn" style="width:100%;margin-top:10px;background:var(--danger);color:#fff;">Close Trade</button>
        <div id="closeTradeStatus" class="modal-status"></div>
      </div>
    </div>
  </div>
"""

    head = _build_head_section()
    content = _build_main_content()
    scripts = _build_scripts()
    html = f"<!doctype html>\n<html lang=\"en\">\n<head>\n{head}</head>\n<body>\n{content}\n{scripts}\n</body>\n</html>"
    output_path.write_text(html, encoding="utf-8")


# =============================================================================
# Documentation outputs
# =============================================================================




def write_mapping_doc(
    pine_sources: Dict[str, str],
    symbol_resolution: Dict[str, Dict[str, object]],
    output_path: Path,
) -> None:
    """Write Pine Script → Python mapping and symbol resolution to a markdown file."""
    def _sanitize_utf8(s: str) -> str:
        # Some extracted Pine sources may contain invalid surrogate code points.
        # Make the output file always writable as UTF-8.
        return s.encode("utf-8", "backslashreplace").decode("utf-8")

    lines: List[str] = []
    lines.append("# Pine Script → Python mapping")
    lines.append("")
    lines.append("This file documents how each PineScript indicator was translated to Python.")
    lines.append("")
    lines.append("## Symbols and data source")
    lines.append("")
    lines.append("- Data source: `yfinance`")
    lines.append("- Timeframes:")
    lines.append("  - `4H`: built from `60m` candles resampled to 4-hour OHLCV")
    lines.append("  - `1D`: `1d` candles from Yahoo")
    lines.append("  - `1W`: `1d` candles resampled to weekly (`W-FRI`)")
    lines.append("- OHLCV aggregation rules: open=first, high=max, low=min, close=last, volume=sum")
    lines.append("")
    lines.append("| Display symbol | yfinance ticker used | Attempts |")
    lines.append("|---|---|---|")
    for sym, info in symbol_resolution.items():
        used = info.get("used") or "[NOT FOUND]"
        attempts = ", ".join(info.get("attempts", []))
        lines.append(f"| {sym} | {used} | {attempts} |")
    lines.append("")

    lines.append("## Input PineScripts (from RTF)")
    lines.append("")
    for fname, src in pine_sources.items():
        lines.append(f"### `{fname}`")
        lines.append("")
        lines.append("Extracted Pine source (sanitized):")
        lines.append("")
        lines.append("```")
        lines.append(_sanitize_utf8(src.strip()))
        lines.append("```")
        lines.append("")

    lines.append("## Translations implemented (auto-generated from registry)")
    lines.append("")
    lines.append("Implemented indicators (computed on each selected timeframe):")
    lines.append("")
    try:
        all_indicators = _get_all_indicators()
        for ind in all_indicators:
            cols = ", ".join(f"`{c}`" for c in (ind.columns or []))
            kpi_tag = f" — KPI: {ind.kpi_name} ({ind.kpi_type})" if ind.kpi_name else ""
            dim_label = DIMENSIONS.get(ind.dimension, ind.dimension)
            lines.append(f"- **{ind.title}** (`{ind.key}`, {dim_label}){kpi_tag}")
            if cols:
                lines.append(f"  - Columns: {cols}")
    except Exception:
        lines.append("- *(Could not auto-generate indicator list from registry)*")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")



def write_readme(output_path: Path) -> None:
    """Write project README with run instructions and symbol management."""
    content = """# Trading indicators dashboard (4H / 1D / 1W / 2W / 1M)

This project converts Pine Script indicators into Python, computes them on multi-timeframe OHLCV data (4H, 1D, 1W, 2W, 1M), and generates a standalone Plotly dashboard.

## What it produces

- `data/dashboard_artifacts/dashboard_shell.html`: interactive dashboard (lazy-load shell)
- `data/dashboard_artifacts/dashboard_assets/`: per-symbol Plotly JSON assets
- `data/feature_store/enriched/<dataset>/stock_data/<SYMBOL>_<TF>.parquet`: enriched OHLCV + computed indicator columns
- `docs/pine_to_python_mapping.md`: Pine → Python mapping and limitations

## Run

```bash
# Full build (download + compute + dashboard)
python -m trading_dashboard dashboard build

# Refresh dashboard from cached data (no yfinance)
python -m trading_dashboard dashboard refresh

# UI-only rebuild (fastest — skip indicator recomputation)
python -m trading_dashboard dashboard rebuild-ui

# Serve dashboard via local HTTP server
python -m apps.dashboard.serve_dashboard
```

## Symbol management

```bash
python -m trading_dashboard symbols list
python -m trading_dashboard symbols add AAPL --group watchlist
python -m trading_dashboard symbols sync
```

## Notes

- Data is downloaded via `yfinance`:
  - hourly (`60m`) then resampled to 4H
  - daily (`1d`) then optionally resampled to 1W (`W-FRI`)
- If a symbol is not found, the script tries common exchange suffixes (e.g. `.PA`).
"""
    output_path.write_text(content, encoding="utf-8")
