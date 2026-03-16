"""
HTML/CSS/JS template functions for dashboard output.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from trading_dashboard.indicators.registry import (
    DIMENSION_ORDER,
    DIMENSIONS,
    get_dimension_label,
    get_dimension_map,
)
from trading_dashboard.indicators.registry import (
    get_all as _get_all_indicators,
)
from trading_dashboard.indicators.registry import (
    get_kpi_trend_order as _get_kpi_trend_order,
)
from trading_dashboard.indicators.registry import (
    get_strategies as _get_strategies,
)

_CONFIGS_DIR = Path(__file__).resolve().parent / "configs"

# Path to KPI optimization research results (20260315 run)
_KPI_RESEARCH_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "research" / "kpi_optimization" / "20260315" / "data" / "results"
)


def _build_kpi_test_results() -> str:
    """
    Build the HTML content for the 'Test Results' info subtab.
    Reads from the KPI optimization research results directory.
    Returns a placeholder message if results are not available.
    """
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return "<p class='info-note'>pandas not available — cannot render test results.</p>"

    def _pct(v, dec=1):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        return f"{v * 100:.{dec}f}%"

    def _f(v, dec=2):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        return f"{v:.{dec}f}"

    def _grade(cagr, sharpe, max_dd):
        if cagr <= 0 or max_dd > 0.35:
            return "FAIL", "#ffc7ce", "✗"
        if sharpe >= 1.2 and cagr >= 0.15:
            return "STRONG", "#c6efce", "✓✓"
        if sharpe >= 0.8 and cagr >= 0.09:
            return "PASS", "#e2efda", "✓"
        return "WEAK", "#ffeb9c", "~"

    _STRAT_LABEL = {
        "breakout":          "Breakout",
        "pullback_trailing": "Pullback (Trailing ATR)",
        "trend_following":   "Trend Following",
        "momentum":          "Momentum Rotation",
        "sr":                "Support/Resistance",
    }
    _SLOT_LABEL = {
        "regime": "Regime Filter",
        "entry":  "Entry Trigger",
        "position": "Position Filter",
        "exit":   "Exit Signal",
        "multi_tf": "Multi-Timeframe",
    }

    # ── Load data ──────────────────────────────────────────────────────────
    r = _KPI_RESEARCH_DIR
    try:
        baseline_df = pd.read_csv(r / "summary_baseline.csv").set_index("strategy")
        baseline_df = baseline_df[baseline_df.index != "pullback_fixed"]
    except Exception:
        baseline_df = pd.DataFrame()

    try:
        dm = pd.read_csv(r / "summary_decision_matrix.csv")
    except Exception:
        dm = pd.DataFrame()

    try:
        frozen = json.loads((r / "frozen_config.json").read_text())
    except Exception:
        frozen = {}

    try:
        phase5 = json.loads((r / "phase5_winners.json").read_text())
    except Exception:
        phase5 = {}

    group_b: dict = {}
    for strat in ["breakout", "pullback_trailing", "trend_following"]:
        try:
            row = pd.read_parquet(r / f"group_b_{strat}.parquet").iloc[0].to_dict()
            group_b[strat] = row
        except Exception:
            pass

    if not group_b and baseline_df.empty and dm.empty:
        return "<p class='info-note'>Test results not yet available — run the KPI optimization pipeline first.</p>"

    # ── Grade colours (CSS-safe light/dark aware via opacity) ──────────────
    # We embed inline styles only for coloured badges, rest uses CSS vars.

    parts: list[str] = []

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 0 — Executive Summary
    # ══════════════════════════════════════════════════════════════════════
    parts.append('<section class="info-section">')
    parts.append('<h3>Executive Summary</h3>')

    # Summary cards
    strats_tested = ["breakout", "pullback_trailing", "trend_following"]
    card_html = '<div class="tr-cards">'
    for strat in strats_tested:
        gb = group_b.get(strat, {})
        ba = baseline_df.loc[strat] if (not baseline_df.empty and strat in baseline_df.index) else None
        gb_cagr  = gb.get("cagr", 0)
        gb_sharpe = gb.get("sharpe", 0)
        gb_dd    = gb.get("max_dd", 1)
        grade_label, grade_bg, grade_sym = _grade(gb_cagr, gb_sharpe, gb_dd)

        rec_map = {
            "STRONG": "Ready for live trading",
            "PASS":   "Passes all gates — monitor closely",
            "WEAK":   "Marginal — needs further tuning",
            "FAIL":   "Do not deploy — failed risk gate",
        }
        rec_color = {
            "STRONG": "var(--success)",
            "PASS":   "var(--success)",
            "WEAK":   "var(--warning)",
            "FAIL":   "var(--danger)",
        }
        card_html += f'''<div class="tr-card">
  <div class="tr-card-label">{_STRAT_LABEL.get(strat, strat)}</div>
  <div class="tr-card-grade" style="color:{rec_color[grade_label]}">{grade_sym} {grade_label}</div>
  <div class="tr-card-stat">Group B CAGR: <strong>{_pct(gb_cagr)}</strong></div>
  <div class="tr-card-stat">Sharpe: <strong>{_f(gb_sharpe)}</strong> &nbsp; MaxDD: <strong>{_pct(gb_dd)}</strong></div>
  <div class="tr-card-stat">Win Rate: <strong>{_pct(gb.get("win_rate"))}</strong> &nbsp; Trades: <strong>{int(gb.get("trade_count", 0))}</strong></div>
  <div class="tr-card-rec" style="color:{rec_color[grade_label]}">{rec_map[grade_label]}</div>
</div>'''
    card_html += '</div>'
    parts.append(card_html)

    parts.append('''<div class="tr-exec-body">
<p><strong>What we did:</strong> We ran a systematic 8-phase experiment to find the best technical indicators ("KPIs")
for each trading strategy — replacing hand-picked defaults with statistically validated choices.
We tested <strong>52 indicators</strong> across <strong>5 strategies</strong> on <strong>S&amp;P 500 stocks (2008–2021)</strong>,
using 10 rolling walk-forward windows to avoid overfitting, and a held-out Group B stock universe for validation.</p>
<p><strong>What we found:</strong>
Breakout and Pullback pass the risk gates on out-of-sample data with modest but real improvements from the optimized KPIs.
Trend Following showed the strongest Group A performance (19% CAGR, Sharpe 1.42) but failed the Group B MaxDD gate (46% drawdown),
suggesting the strategy is currently overfit to the training universe — further tuning required before deploying.</p>
<p><strong>Key caveat:</strong> Group B is a <em>cross-sectional</em> holdout (different stocks, same period) — not a temporal holdout.
The 2022–2025 temporal holdout remains locked and will be the final confirmation step.</p>
</div>''')
    parts.append('</section>')

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 1 — Research Pipeline Logigramme
    # ══════════════════════════════════════════════════════════════════════
    parts.append('<section class="info-section">')
    parts.append('<h3>Research Pipeline — How the Test Worked</h3>')
    parts.append('<p class="info-note">Plain-English explanation of each phase. No trading or coding knowledge required.</p>')

    phases = [
        ("Phase 0", "Bug Fixes", "done",
         "Before running any tests, we audited every indicator formula and fixed 6 confirmed bugs "
         "(wrong ATR calculation, incorrect PSAR initialisation, etc.). This ensures the test results are trustworthy."),
        ("Phase 1", "Build the Universe", "done",
         "We pulled the historical S&P 500 membership list — the 500 stocks that were actually <em>in</em> the index at each point in time "
         "(not today's list, which would introduce survivorship bias). We then split them randomly into two groups: "
         "<strong>Group A</strong> (used for all training and testing) and <strong>Group B</strong> (locked away for final validation)."),
        ("Phase 2", "Download Price Data", "done",
         "Daily price bars from 2008 to 2025 downloaded for every stock in our universe, plus macro data "
         "(S&amp;P 500, VIX, 10yr Treasury yield). 2022–2025 is kept locked — the models never see it until the very last step."),
        ("Phase 3", "Baseline Strategies", "done",
         "We ran each of the 5 strategies with their <em>default</em> settings (no optimisation) to establish a baseline. "
         "This tells us how the strategy performs before any indicator swaps — the benchmark to beat."),
        ("Phase 4", "Walk-Forward Test (52 KPIs × 5 Strategies)", "done",
         "The main experiment. For each of 52 indicators and 5 strategies, we ran 10 rolling tests: "
         "train on 4 years, test on the following 1 year, slide forward, repeat. "
         "This gives us 10 independent data points per indicator, reducing the risk of lucky flukes. "
         "We collected 23 performance metrics per window (return, Sharpe, drawdown, win rate, etc.). "
         "A statistical significance test (BH FDR) filters out indicators that only look good by chance."),
        ("Phase 5", "Parameter Sensitivity", "done",
         "For the indicators that survived Phase 4, we tested 3 parameter variants each "
         "(e.g. tight / standard / loose thresholds) to find the most robust setting. "
         "An indicator that only works with one very specific setting is fragile; "
         "one that works across multiple settings is more trustworthy."),
        ("Phase 6", "Group B Validation", "done",
         "The winning indicator config was run — just once — on Group B (the stocks set aside at the very start). "
         "This is the honest out-of-sample test: the model has never seen these stocks. "
         "Pass criteria: CAGR &gt; 0%, MaxDD ≤ 35%. Strategies that fail here are flagged for further work."),
        ("Phase 7", "Results Dashboard", "done",
         "Automated HTML dashboard built showing equity curves, drawdowns, walk-forward heatmaps, "
         "and KPI hit-rate / expectancy charts for all strategies."),
        ("Phase 8", "Config Freeze", "done",
         "The winning indicators per strategy are written to <code>frozen_config.json</code> — "
         "a production-ready config that can be dropped into the live dashboard."),
        ("Phase 7b", "Temporal Holdout (2022–2025)", "locked",
         "🔒 LOCKED — only unlocked after Phase 6 passes for all strategies. "
         "This is the ultimate out-of-sample test: running the frozen config on 3 years of data the model has never seen."),
        ("Phase 5b/5c", "Param Tuning + Interactions", "todo",
         "Further refinement: sweep each winning indicator's parameters more finely, "
         "and test combinations of entry + exit indicators together. Not yet run."),
        ("Phase 6b", "Regime Stratification", "todo",
         "Break down results by market regime (Bull / Correction / Bear / Sideways) "
         "to understand when each strategy works best. Not yet run."),
    ]
    STATUS_STYLE = {
        "done":   ("var(--success)", "✓ Done"),
        "locked": ("var(--warning)", "🔒 Locked"),
        "todo":   ("var(--muted)",   "○ Pending"),
    }
    parts.append('<div class="tr-pipeline">')
    for phase_id, phase_name, status, desc in phases:
        color, status_label = STATUS_STYLE[status]
        parts.append(f'''<div class="tr-phase-row">
  <div class="tr-phase-id" style="color:{color}">{phase_id}</div>
  <div class="tr-phase-body">
    <div class="tr-phase-title">{phase_name} <span class="tr-phase-status" style="color:{color}">{status_label}</span></div>
    <div class="tr-phase-desc">{desc}</div>
  </div>
</div>''')
    parts.append('</div>')  # tr-pipeline
    parts.append('</section>')

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 2 — Per-Strategy Results
    # ══════════════════════════════════════════════════════════════════════
    parts.append('<section class="info-section">')
    parts.append('<h3>Strategy Results — Baseline vs. Optimised vs. Group B</h3>')
    parts.append('<p class="info-note">Group A = training universe (10 walk-forward windows, mean shown). Group B = held-out validation stocks (one shot).</p>')

    all_strats_for_table = ["breakout", "pullback_trailing", "trend_following", "momentum", "sr"]

    parts.append('<table class="info-tbl tr-results-tbl">')
    parts.append('<thead><tr>'
                 '<th>Strategy</th>'
                 '<th>Group A CAGR</th><th>Group A Sharpe</th><th>Group A MaxDD</th>'
                 '<th>Group A Win%</th><th>Trades/wf</th><th>Avg Hold</th>'
                 '<th>Group B CAGR</th><th>Group B Sharpe</th><th>Group B MaxDD</th>'
                 '<th>Group B Win%</th><th>Grade</th>'
                 '</tr></thead><tbody>')

    for strat in all_strats_for_table:
        ba = baseline_df.loc[strat] if (not baseline_df.empty and strat in baseline_df.index) else None
        gb = group_b.get(strat, {})

        ga_cagr   = ba["cagr_mean"]   if ba is not None else None
        ga_sharpe = ba["sharpe_mean"] if ba is not None else None
        ga_dd     = ba["max_dd_mean"] if ba is not None else None
        ga_wr     = ba["win_rate_mean"] if ba is not None else None
        ga_tc     = ba["trade_count_mean"] if ba is not None else None
        ga_hold   = ba["avg_holding_days_mean"] if ba is not None else None

        gb_cagr   = gb.get("cagr")
        gb_sharpe = gb.get("sharpe")
        gb_dd     = gb.get("max_dd")
        gb_wr     = gb.get("win_rate")
        gb_tc     = gb.get("trade_count")

        if gb_cagr is not None:
            grade_label, _, grade_sym = _grade(gb_cagr, gb_sharpe or 0, gb_dd or 1)
        elif ga_cagr is not None:
            grade_label, _, grade_sym = _grade(ga_cagr, ga_sharpe or 0, ga_dd or 1)
            grade_label = grade_label + "*"
            grade_sym = "?"
        else:
            grade_label, grade_sym = "N/A", "—"

        grade_colors = {"STRONG": "var(--success)", "PASS": "var(--success)",
                        "WEAK": "var(--warning)", "FAIL": "var(--danger)", "N/A": "var(--muted)"}
        grade_c = grade_colors.get(grade_label.rstrip("*"), "var(--muted)")

        parts.append(f'<tr>'
                     f'<td><strong>{_STRAT_LABEL.get(strat, strat)}</strong></td>'
                     f'<td>{_pct(ga_cagr)}</td><td>{_f(ga_sharpe)}</td><td>{_pct(ga_dd)}</td>'
                     f'<td>{_pct(ga_wr)}</td>'
                     f'<td>{_f(ga_tc, 0) if ga_tc else "—"}</td>'
                     f'<td>{_f(ga_hold, 1) + "d" if ga_hold else "—"}</td>'
                     f'<td>{_pct(gb_cagr)}</td><td>{_f(gb_sharpe)}</td><td>{_pct(gb_dd)}</td>'
                     f'<td>{_pct(gb_wr)}</td>'
                     f'<td style="color:{grade_c};font-weight:700">{grade_sym} {grade_label}</td>'
                     f'</tr>')

    parts.append('</tbody></table>')
    parts.append('</section>')

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 3 — Winning KPI Config per Strategy
    # ══════════════════════════════════════════════════════════════════════
    parts.append('<section class="info-section">')
    parts.append('<h3>Winning KPI Configuration (Frozen Config)</h3>')
    parts.append('<p class="info-note">'
                 'The final indicator selected for each role in each strategy, after all phases. '
                 '"dCAGR" = improvement over baseline. "Robust" = fraction of parameter variants that also passed (1.0 = all 3 variants passed).'
                 '</p>')

    for strat in ["breakout", "pullback_trailing", "trend_following"]:
        gb = group_b.get(strat, {})
        gb_cagr = gb.get("cagr", 0)
        gb_sharpe = gb.get("sharpe", 0)
        gb_dd = gb.get("max_dd", 1)
        grade_label, _, grade_sym = _grade(gb_cagr, gb_sharpe, gb_dd)
        grade_colors = {"STRONG": "var(--success)", "PASS": "var(--success)",
                        "WEAK": "var(--warning)", "FAIL": "var(--danger)"}
        grade_c = grade_colors.get(grade_label, "var(--muted)")

        slots = frozen.get("strategies", {}).get(strat, {})
        parts.append(f'<h4>{_STRAT_LABEL.get(strat, strat)} '
                     f'<span style="color:{grade_c}">— Group B: {grade_sym} {grade_label} '
                     f'(CAGR {_pct(gb_cagr)}, Sharpe {_f(gb_sharpe)}, MaxDD {_pct(gb_dd)})</span></h4>')

        parts.append('<table class="info-tbl">')
        parts.append('<thead><tr><th>Role</th><th>Indicator</th><th>Variant / Setting</th>'
                     '<th>CAGR (Group A)</th><th>Improvement</th><th>Win Rate</th>'
                     '<th>Trades</th><th>Robustness</th><th>Source</th></tr></thead><tbody>')

        for slot in ["regime", "entry", "position", "exit", "multi_tf"]:
            if slot not in slots:
                continue
            entries = slots[slot]
            if not isinstance(entries, list):
                entries = [entries]
            for e in entries:
                kpi = e.get("kpi", "?")
                p5  = phase5.get(kpi, {}).get(strat, {})
                wr  = p5.get("win_rate")
                tc  = p5.get("trade_count")
                dcagr = e.get("delta_cagr", 0)
                dcagr_str = ("+" if dcagr >= 0 else "") + _pct(dcagr)
                dcagr_c = "var(--success)" if dcagr > 0.005 else ("var(--muted)" if abs(dcagr) < 0.005 else "var(--danger)")
                rob = e.get("robustness_score")
                sel = e.get("selection", "?")
                sel_badge = ("improved" if sel == "improved"
                             else "current" if sel == "fallback_current"
                             else "phase4")
                parts.append(f'<tr>'
                              f'<td>{_SLOT_LABEL.get(slot, slot)}</td>'
                              f'<td><strong>{kpi}</strong></td>'
                              f'<td><code>{e.get("variant", "—")}</code></td>'
                              f'<td>{_pct(e.get("cagr_group_a"))}</td>'
                              f'<td style="color:{dcagr_c}">{dcagr_str}</td>'
                              f'<td>{_pct(wr) if wr else "—"}</td>'
                              f'<td>{int(tc) if tc else "—"}</td>'
                              f'<td>{_f(rob) if rob is not None else "—"}</td>'
                              f'<td><span class="tr-badge tr-badge-{sel_badge}">{sel_badge}</span></td>'
                              f'</tr>')

        parts.append('</tbody></table>')

    parts.append('</section>')

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 4 — KPI Family Leaderboard
    # ══════════════════════════════════════════════════════════════════════
    parts.append('<section class="info-section">')
    parts.append('<h3>KPI Family Test Results — Top Performers per Family</h3>')
    parts.append('<p class="info-note">'
                 'Top 3 indicators per family per strategy. "Sig?" = statistically significant (BH FDR q &lt; 0.10). '
                 '"dCAGR" = CAGR improvement vs. baseline. PF = Profit Factor.'
                 '</p>')

    if not dm.empty:
        for strat in ["breakout", "pullback_trailing", "trend_following"]:
            sub = dm[dm["strategy"] == strat].copy()
            if sub.empty:
                continue
            parts.append(f'<h4>{_STRAT_LABEL.get(strat, strat)}</h4>')
            parts.append('<table class="info-tbl">')
            parts.append('<thead><tr><th>Family</th><th>Rank</th><th>Indicator</th>'
                         '<th>CAGR</th><th>dCAGR</th><th>Sharpe</th>'
                         '<th>Win Rate</th><th>PF</th><th>Trades</th><th>Significant?</th>'
                         '</tr></thead><tbody>')

            families = sorted(sub["family"].unique())
            for fam in families:
                rows = sub[(sub["family"] == fam) & (sub["rank"] <= 3)].sort_values("rank")
                for i, (_, row) in enumerate(rows.iterrows()):
                    sig = row.get("passes_significance", False)
                    sig_html = ('<span style="color:var(--success)">✓ Yes</span>' if sig
                                else '<span style="color:var(--muted)">No</span>')
                    dcagr = row["delta_cagr"]
                    dcagr_str = ("+" if dcagr >= 0 else "") + _pct(dcagr)
                    dcagr_c = "var(--success)" if dcagr > 0.005 else ("var(--muted)" if abs(dcagr) < 0.005 else "var(--danger)")
                    gate = row.get("passes_hard_gates", True)
                    kpi_style = "" if gate else ' style="color:var(--muted)"'
                    fam_cell = fam if i == 0 else ""
                    parts.append(f'<tr>'
                                  f'<td>{fam_cell}</td>'
                                  f'<td style="color:var(--muted)">#{int(row["rank"])}</td>'
                                  f'<td{kpi_style}><strong>{row["kpi"]}</strong>{"" if gate else " <em>(gate fail)</em>"}</td>'
                                  f'<td>{_pct(row["cagr"])}</td>'
                                  f'<td style="color:{dcagr_c}">{dcagr_str}</td>'
                                  f'<td>{_f(row["sharpe"])}</td>'
                                  f'<td>{_pct(row["win_rate"])}</td>'
                                  f'<td>{_f(row["profit_factor"])}</td>'
                                  f'<td>{int(row["trade_count"])}</td>'
                                  f'<td>{sig_html}</td>'
                                  f'</tr>')
            parts.append('</tbody></table>')
    else:
        parts.append('<p class="info-note">Decision matrix not available.</p>')

    parts.append('</section>')

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 5 — Recommendations & Next Steps
    # ══════════════════════════════════════════════════════════════════════
    parts.append('<section class="info-section">')
    parts.append('<h3>Recommendations &amp; Next Steps</h3>')

    recs = [
        ("Breakout", "breakout", "WEAK",
         "Passes the risk gates on Group B (CAGR 6.8%, MaxDD 23.9%). "
         "The optimised KPIs (GMMA Weekly, VIX regime filter) show meaningful improvement over baseline (+3–6% CAGR). "
         "Sharpe is low (0.59) — investigate regime stratification before deploying to production. "
         "The strategy is most reliable in low-VIX, broad-market-healthy conditions.",
         "Review — safe to paper-trade, not yet live"),
        ("Pullback (Trailing ATR)", "pullback_trailing", "WEAK",
         "Best Group B result of the three (CAGR 10.6%, Sharpe 0.80, MaxDD 23.1%). "
         "CCI_Chop_BB entry and ATR_Expansion exit provided real improvement. "
         "Still below Sharpe 1.0 threshold. The D4 bug (Exit KPI re-run for pullback) is pending — "
         "results may improve after that fix. Recommended for near-term deployment after D4 re-run.",
         "Near-deploy — fix D4, then promote to staging"),
        ("Trend Following", "trend_following", "FAIL",
         "Exceptional Group A performance (CAGR 18.7%, Sharpe 1.42) but failed Group B on MaxDD (46.2% > 35% gate). "
         "The CAGR gap between Group A (18.7%) and Group B (9.5%) is a strong overfit signal — "
         "the current config is tuned too tightly to the Group A universe. "
         "Recommended fix: tighten the ATR stop multiplier, add a volatility-adjusted position size cap, "
         "or restrict to bull-regime only (VIX &lt; 20, breadth &gt; 60%). Do not deploy until Phase 6 passes.",
         "Do not deploy — fix MaxDD first"),
    ]

    for strat_name, strat_key, grade_label, analysis, action in recs:
        grade_colors = {"STRONG": "var(--success)", "PASS": "var(--success)",
                        "WEAK": "var(--warning)", "FAIL": "var(--danger)"}
        grade_c = grade_colors.get(grade_label, "var(--muted)")
        parts.append(f'''<div class="tr-rec-block">
  <div class="tr-rec-header">
    <span class="tr-rec-strat">{strat_name}</span>
    <span class="tr-rec-grade" style="color:{grade_c}">● {grade_label}</span>
    <span class="tr-rec-action" style="color:{grade_c}">→ {action}</span>
  </div>
  <p class="tr-rec-body">{analysis}</p>
</div>''')

    parts.append('<h4>Remaining Pipeline Steps</h4>')
    next_steps = [
        ("NOW",    "var(--danger)",   "Re-run Phase 4+9 for Pullback Exit KPIs (D4 bug fix) — see docs/AUDIT_FIXES.md"),
        ("NOW",    "var(--danger)",   "Investigate Trend Following MaxDD on Group B — tighten stop or add position sizing"),
        ("NEXT",   "var(--warning)",  "Run Phase 5b: parameter tuning for each winning KPI (currently on Phase 4 defaults)"),
        ("NEXT",   "var(--warning)",  "Run Phase 6b: regime stratification (Bull/Correction/Bear/Sideways breakdown)"),
        ("LATER",  "var(--muted)",    "Run Phase 5c: pairwise interaction effects (entry × exit combos)"),
        ("LOCKED", "var(--muted)",    "Phase 7b: Temporal holdout 2022–2025 — unlock only after all Group B gates pass"),
        ("DEPLOY", "var(--success)",  "Pullback: promote frozen_config.json to apps/dashboard/configs/config.json after D4 fix"),
    ]
    parts.append('<table class="info-tbl">')
    parts.append('<thead><tr><th>Priority</th><th>Action</th></tr></thead><tbody>')
    for priority, color, desc in next_steps:
        parts.append(f'<tr><td style="color:{color};font-weight:700;white-space:nowrap">{priority}</td>'
                     f'<td>{desc}</td></tr>')
    parts.append('</tbody></table>')
    parts.append('</section>')

    return "\n".join(parts)


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
    exit_params: dict | None = None,
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
    groups_payload = json.dumps(symbol_groups or {}, allow_nan=False, separators=(",", ":"))
    # exit_params: caller passes pre-loaded value (single source of truth: config.json via config_loader)
    exit_params_payload = json.dumps(exit_params or {}, separators=(",", ":"))
    _kpi_w = _load_config_field("kpi_weights", {})
    max_trend_score = sum(float(v) for v in _kpi_w.values()) if _kpi_w else None
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
        _static_dir / "dashboard_scan.js",
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
    kpi_test_results = _build_kpi_test_results()

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
    const SCREENER = {{}};  // populated on startup via /api/screener-data
    const EXIT_PARAMS_CFG = {exit_params_payload};
    const MAX_TREND_SCORE = {json.dumps(max_trend_score)};
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
    // API base-path prefix — auto-detected from URL so the same build works
    // both at the root (production) and under /test/ (staging via nginx proxy).
    const _BASE = window.location.pathname.startsWith("/test/") ? "/test" : "";
{_js_text}
  </script>
"""

    def _get_body_content() -> str:
        return f"""  <div class="topbar">
    <div class="topbarRow">
      <div class="nav-tabs" role="tablist" aria-label="Dashboard tabs">
        <div id="tabScreener" class="nav-tab" role="tab" tabindex="0" aria-selected="false">Screener</div>
        <div id="tabScan" class="nav-tab" role="tab" tabindex="-1" aria-selected="false">&#128269; Scan</div>
        <div id="tabStrategy" class="nav-tab" role="tab" tabindex="-1" aria-selected="false">Strategy</div>
        <div id="tabChart" class="nav-tab active" role="tab" tabindex="-1" aria-selected="true">Charts</div>
        <div id="tabPnl" class="nav-tab" role="tab" tabindex="-1" aria-selected="false">P&amp;L</div>
        <div id="tabInfo" class="nav-tab" role="tab" tabindex="-1" aria-selected="false">Info</div>
      </div>
      <div class="topbar-sep"></div>
      <div class="refresh-split" id="refreshSplit">
        <button id="refreshToggle" class="topbar-refresh-toggle" title="Refresh options">&#8635; Refresh &#9662;</button>
        <div class="refresh-menu" id="refreshMenu">
          <button id="rebuildUiBtn" class="refresh-menu-item">&#9889; UI Refresh <span class="refresh-menu-hint">templates &amp; JS only</span></button>
          <button id="refreshBtn" class="refresh-menu-item">&#8635; Full Refresh <span class="refresh-menu-hint">re-download &amp; re-enrich</span></button>
        </div>
      </div>
      <div class="topbar-sep"></div>
      <div id="themeToggle" title="Toggle dark/light mode">&#9790;</div>
      <div class="topbar-sep"></div>
      <button id="eurToggle" class="eur-toggle" title="Toggle prices to EUR">Local</button>
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
            <div id="strategyTrigger" class="tab-group-trigger">Trend Position &#9662;</div>
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
        <div class="btn" data-filter="active_position" title="Stocks with an active ENTRY, SCALE, or HOLD signal (any strategy)">In Position</div>
        <div class="btn" data-filter="new_combo" title="Combo just appeared — was not active on the previous bar">New Signals</div>
        <div class="btn" data-filter="improving" title="Trend delta &gt; 0 over last 3 bars — momentum turning up">Improving</div>
        <div class="btn" data-filter="combo" title="At least one combo (C3/C4) active on the latest bar">Combo</div>
        <span class="filter-sep"></span>
        <span class="filter-label">Strategy:</span>
        <div class="btn" data-filter="strat_dip" title="Dip Buy entry signal active (D badge)">Dip Buy</div>
        <div class="btn" data-filter="strat_swing" title="Swing Trading entry or hold (S badge)">Swing</div>
        <div class="btn" data-filter="strat_trend" title="Trend Position entry or hold (T badge)">Trend</div>
        <div class="btn" data-filter="strat_stoof" title="Stoof entry or hold active on 2W or 1M">Stoof</div>
      </div>
      <button id="btnAddTicker" class="btn btn-add" type="button" title="Add ticker to watchlist">+ Add</button>
      <button id="btnExport" class="btn" type="button">Export CSV</button>
    </div>
    <div id="screenerBox">
      <div id="screener"></div>
    </div>
  </div>

  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <!--  SCAN TAB                                                         -->
  <!-- ═══════════════════════════════════════════════════════════════════ -->
  <div id="scanWrap" style="display:none;">
    <div class="scan-page">

      <!-- ── Controls ──────────────────────────────────────────────────── -->
      <div class="scan-controls-bar">
        <div class="scan-controls-left">
          <span class="scan-controls-label">Timeframe</span>
          <div id="scanTfSelector" class="scan-tf-pills">
            <button class="scan-tf-pill scan-tf-confirm" data-tf="4H" title="4H is not scanned — used only to confirm 1D entry timing">4H &#10003;</button>
            <button class="scan-tf-pill active" data-tf="1D">1D</button>
            <button class="scan-tf-pill" data-tf="1W">1W</button>
            <button class="scan-tf-pill" data-tf="2W">2W</button>
            <button class="scan-tf-pill" data-tf="1M">1M</button>
            <button class="scan-tf-pill scan-tf-all" data-tf="all">&#9889; All TFs</button>
          </div>
        </div>
        <div class="scan-controls-right">
          <button id="scanBtn" class="scan-action-btn scan-action-primary" title="Scan universe for all strategies on selected timeframe">&#9881; Scan</button>
          <button id="scanExportAllBtn" class="scan-action-btn" title="Export all-TF scan results to CSV">&#8595; Export All</button>
        </div>
      </div>

      <!-- ── New Signals ───────────────────────────────────────────────── -->
      <section class="scan-section" id="scanSectionNew">
        <div class="scan-section-header">
          <h2 class="scan-section-title">&#128994; New Signals <span id="scanNewCount" class="scan-badge"></span></h2>
          <span class="scan-section-sub" id="scanNewMeta"></span>
        </div>
        <div id="scanNewSignals"></div>
      </section>

      <!-- ── Positions at Risk ─────────────────────────────────────────── -->
      <section class="scan-section" id="scanSectionRisk">
        <div class="scan-section-header">
          <h2 class="scan-section-title">&#9888; Positions at Risk <span id="scanRiskCount" class="scan-badge scan-badge-warn"></span></h2>
          <span class="scan-section-sub">Open positions where stop is &lt;10% away</span>
        </div>
        <div id="scanPositionsAtRisk"></div>
      </section>

      <!-- ── Pre-Signals ───────────────────────────────────────────────── -->
      <section class="scan-section" id="scanSectionPre">
        <div class="scan-section-header">
          <h2 class="scan-section-title">&#128064; Almost There <span id="scanPreCount" class="scan-badge scan-badge-info"></span></h2>
          <span class="scan-section-sub">2 of 3 C3 KPIs bullish — signal may fire next bar</span>
        </div>
        <div id="scanPreSignals"></div>
      </section>

      <!-- ── Strategy Cards ────────────────────────────────────────────── -->
      <section class="scan-section">
        <div class="scan-section-header">
          <h2 class="scan-section-title">&#128196; Strategy Reference</h2>
          <span class="scan-section-sub">Entry conditions, KPIs, and gates for each active strategy</span>
        </div>
        <div id="scanStrategyCards" class="scan-strategy-cards"></div>
      </section>

      <!-- ── Decision Logigram ─────────────────────────────────────────── -->
      <section class="scan-section">
        <div class="scan-section-header">
          <h2 class="scan-section-title">&#128336; How to Act on a Signal</h2>
          <span class="scan-section-sub">Decision checklist — run through this before placing any order</span>
        </div>
        <div class="scan-logigram">
          <div class="logi-step logi-step-trigger">
            <div class="logi-icon">&#128269;</div>
            <div class="logi-body">
              <div class="logi-title">New Signal appears in scan</div>
              <div class="logi-desc">A stock just triggered a C3 combo onset for one or more strategies</div>
            </div>
          </div>
          <div class="logi-arrow">&#8595;</div>
          <div class="logi-decision-row">
            <div class="logi-check">
              <div class="logi-check-num">1</div>
              <div class="logi-check-body">
                <div class="logi-check-title">Signal Fresh? <span class="logi-tag logi-tag-green">&#128994; &#8804;1 bar = Act</span> <span class="logi-tag logi-tag-yellow">&#128993; &#8804;3 = Watch</span> <span class="logi-tag logi-tag-red">&#128308; &gt;3 = Skip</span></div>
                <div class="logi-check-desc">Check <b>Freshness</b> column. Stale signals (&gt;3 bars) likely already priced in.</div>
              </div>
            </div>
            <div class="logi-check">
              <div class="logi-check-num">2</div>
              <div class="logi-check-body">
                <div class="logi-check-title">TF Alignment &#8805;2 timeframes?</div>
                <div class="logi-check-desc">Check <b>TF Align</b> column. Signal on 1D confirmed by 1W trend = high conviction. Single TF = lower conviction.</div>
              </div>
            </div>
            <div class="logi-check">
              <div class="logi-check-num">3</div>
              <div class="logi-check-body">
                <div class="logi-check-title">Risk acceptable? <span class="logi-tag logi-tag-green">&lt;5% = Full size</span> <span class="logi-tag logi-tag-yellow">5-8% = Half</span> <span class="logi-tag logi-tag-red">&gt;8% = Skip</span></div>
                <div class="logi-check-desc">Check <b>Risk%</b> column = (price − ATR stop) / price. Size position so 1 risk unit = 1% of portfolio.</div>
              </div>
            </div>
            <div class="logi-check">
              <div class="logi-check-num">4</div>
              <div class="logi-check-body">
                <div class="logi-check-title">Sector concentration OK?</div>
                <div class="logi-check-desc">Check <b>Sector cluster</b> header. If ≥4 signals in same sector, reduce size — sector bias, not alpha.</div>
              </div>
            </div>
            <div class="logi-check">
              <div class="logi-check-num">5</div>
              <div class="logi-check-body">
                <div class="logi-check-title">Multi-strategy agreement?</div>
                <div class="logi-check-desc">Check <b>Strategies</b> column. ≥2 strategies agree = strong conviction → full allocation. 1 strategy only → half allocation.</div>
              </div>
            </div>
          </div>
          <div class="logi-arrow">&#8595;</div>
          <div class="logi-outcome-row">
            <div class="logi-outcome logi-outcome-strong">
              <div class="logi-outcome-icon">&#128994;</div>
              <div class="logi-outcome-title">STRONG — Full size</div>
              <div class="logi-outcome-desc">Fresh (&le;1 bar) · &#8805;2 TFs aligned · Risk &lt;5% · &#8805;2 strategies · Sector OK</div>
            </div>
            <div class="logi-outcome logi-outcome-moderate">
              <div class="logi-outcome-icon">&#128993;</div>
              <div class="logi-outcome-title">MODERATE — Half size</div>
              <div class="logi-outcome-desc">2-3 bars old · 1 TF only · Risk 5-8% · or single strategy</div>
            </div>
            <div class="logi-outcome logi-outcome-skip">
              <div class="logi-outcome-icon">&#128308;</div>
              <div class="logi-outcome-title">SKIP — Wait for reconfirmation</div>
              <div class="logi-outcome-desc">Stale &gt;3 bars · Risk &gt;8% · Sector over-concentrated</div>
            </div>
          </div>
        </div>
      </section>

      <!-- ── Scan Dataflow ─────────────────────────────────────────────── -->
      <section class="scan-section">
        <div class="scan-section-header">
          <h2 class="scan-section-title">&#128260; Scan Dataflow</h2>
          <span class="scan-section-sub">How signals are generated — from universe to entry</span>
        </div>
        <div class="scan-dataflow">
          <div class="df-step">
            <div class="df-step-num">1</div>
            <div class="df-step-body">
              <div class="df-step-title">Universe Download <span class="df-tf-badge">1D · 1W · 2W · 1M</span></div>
              <div class="df-step-desc">All symbols from <code>universe.csv</code> are downloaded via yfinance in batches of 50. Each of the four scan timeframes (1D, 1W, 2W, 1M) is downloaded once. <b>4H is excluded from the scan</b> — it's reserved for entry timing confirmation only.</div>
            </div>
          </div>
          <div class="df-arrow">&#8595;</div>
          <div class="df-step">
            <div class="df-step-num">2</div>
            <div class="df-step-body">
              <div class="df-step-title">Lean Enrichment + Quality Gate</div>
              <div class="df-step-desc">For each symbol, only the KPIs required by the active strategy combos are computed (no full indicator suite). Quality gates are applied per strategy: <b>SMA20&gt;SMA200</b> (trend filter), <b>Volume spike ≥1.5× MA20</b> (momentum filter), <b>SR Break</b> (support/resistance breakout within 10 bars).</div>
            </div>
          </div>
          <div class="df-arrow">&#8595;</div>
          <div class="df-step">
            <div class="df-step-num">3</div>
            <div class="df-step-body">
              <div class="df-step-title">C3 Onset Detection</div>
              <div class="df-step-desc">A C3 signal fires when <b>all C3 KPIs flip bullish simultaneously</b> within the last 3 bars (onset detection). Stocks that pass are "lean candidates" — quickly enriched without full pipeline overhead.</div>
            </div>
          </div>
          <div class="df-arrow">&#8595;</div>
          <div class="df-step">
            <div class="df-step-num">4</div>
            <div class="df-step-body">
              <div class="df-step-title">Full Enrichment + Re-validation</div>
              <div class="df-step-desc">Lean candidates are fully enriched (all indicators, all timeframes including 4H) and written to the feature store. C3 onset is re-validated on real enriched data. Only confirmed signals survive.</div>
            </div>
          </div>
          <div class="df-arrow">&#8595;</div>
          <div class="df-step df-step-4h">
            <div class="df-step-num">5</div>
            <div class="df-step-body">
              <div class="df-step-title">4H Entry Confirmation <span class="df-confirm-badge">&#10003; Confirmation only</span></div>
              <div class="df-step-desc">After a stock appears in the New Signals list (1D+), check the <b>4H timeframe</b> to time the entry. Look for 4H KPIs aligning bullish and volume confirmation before placing the order. The 4H tab in the scan view shows this confirmation layer — it is <b>not a source of scan signals</b>.</div>
            </div>
          </div>
          <div class="df-arrow">&#8595;</div>
          <div class="df-step df-step-output">
            <div class="df-step-num">6</div>
            <div class="df-step-body">
              <div class="df-step-title">Dashboard Refresh &amp; Strategy CSVs</div>
              <div class="df-step-desc">Confirmed symbols are written to <code>configs/lists/&#123;strategy&#125;.csv</code>. A background dashboard refresh rebuilds all Plotly assets and the screener. The New Signals table is populated from the latest screener data.</div>
            </div>
          </div>
        </div>
      </section>

      <!-- ── Scan History ───────────────────────────────────────────────── -->
      <section class="scan-section">
        <div class="scan-section-header">
          <h2 class="scan-section-title">&#128203; Scan History</h2>
          <span class="scan-section-sub">Every scan run — what was added and removed per strategy</span>
        </div>
        <div id="scanHistory"></div>
      </section>

    </div>
  </div>

  <div id="infoWrap" style="display:none;">
    <div class="info-panel">

      <!-- Info subtab nav -->
      <div class="info-sub-tabs">
        <div class="info-sub-tab active" data-info-sub="docs">Strategy Docs</div>
        <div class="info-sub-tab" data-info-sub="testresults">Test Results</div>
      </div>

      <!-- ── Strategy Docs (existing content) ── -->
      <div id="infoDocsContent">

      <h2 class="info-h2">Trading Strategy — Trend Position + Exit Flow v4</h2>
      <p class="info-sub">Status: Locked (v15) — Feb 2026 &nbsp;|&nbsp; PF-optimized combos (Phase 20) &nbsp;|&nbsp; Backtest: ~295 stocks, out-of-sample (last 30%)</p>

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

      </div><!-- /infoDocsContent -->

      <!-- ── Test Results (KPI Optimization) ── -->
      <div id="infoTestResultsContent" style="display:none;">
        {kpi_test_results}
      </div>

    </div>
  </div>

  <div id="pnlWrap" style="display:none;">
    <div class="pnl-panel">
      <div class="tab-filter-bar" data-scope="pnl">
        <div class="filter-group">
          <div class="filter-label">Strategy</div>
          <div class="tab-group-dropdown strategy-placeholder" data-scope="pnl">
            <div class="tab-group-trigger">Trend Position &#9662;</div>
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
