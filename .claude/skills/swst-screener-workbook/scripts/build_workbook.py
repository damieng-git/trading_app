"""Merge parsed SWS screener sources, dedupe, region-filter, and write the xlsx.

All derived cells in the output are LIVE Excel formulas (INDEX/MATCH, IF, AND,
ABS only) so the user can edit thresholds and FX on the Assumptions sheet and
have the sheet re-flag itself. Python still computes the same values internally
for sorting and for verification, but never writes them into the derived cells.

Public API:
  load_config(path)                    -> dict
  merge_sources(sources, cfg)          -> (DataFrame, merge_report)
  filter_region(df, codes, cfg)        -> (DataFrame, filter_report)
  sort_by_eur(df, cfg)                 -> DataFrame  (adds _eur_cap, _flag_* cols)
  write_workbook(df, cfg, path, meta)  -> path
  run(sources, cfg, out_path, region)  -> BuildResult
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from parse_swst import CANONICAL_FIELDS, ParsedSource
import enrich as _enrich
import identity as _identity

# Display column -> canonical field, or "FORMULA" / special renderers.
COLUMN_SOURCES = {
    "Investing Idea": "investing_idea",
    "Company": "_company_display",
    "Ticker": "ticker",
    "Market Cap (EUR m, approx)": "FORMULA",
    "Country": "country",
    "Exchange": "exchange",
    "Currency": "currency",
    "Market Cap (local, m)": "market_cap_local_m",
    "Price (local)": "price_local",
    "7D Return": "ret_7d",
    "1Y Return": "ret_1y",
    "Analyst Target": "analyst_target",
    "Upside to Target": "FORMULA",
    "Valuation (SWS)": "valuation_sws",
    "Growth (SWS)": "growth_sws",
    "Div Yield": "div_yield",
    "Industry": "industry",
    "Business": "business",
    "Source": "source",
    "New on screener": "_new_display",
    "Liquidity flag": "FORMULA",
    "Downtrend flag": "FORMULA",
    "Big 7D move flag": "FORMULA",
    # --- Phase-1 yfinance enrichment (blue INPUT columns) ---
    "P/E (trailing)": "pe_trailing",
    "P/E (forward)": "pe_forward",
    "P/S": "ps_ratio",
    "EV/EBITDA": "ev_ebitda",
    "P/FCF": "p_fcf",
    "EV/Sales": "ev_sales",
    "EV/FCF": "ev_fcf",
    "Px vs 50d MA": "dist_ma50",
    "Px vs 200d MA": "dist_ma200",
    "% from 52wk high": "pct_from_high",
    "% from 52wk low": "pct_from_low",
    "6M momentum": "mom_6m",
    "12M momentum": "mom_12m",
    "RSI (14)": "rsi_14",
    "Beta": "beta",
    "Max drawdown (3y)": "max_drawdown_3y",
    "Avg $ vol (EUR m)": "avg_dollar_vol_eur_m",
    "Net cash (EUR m)": "net_cash_eur_m",
    "Cash runway (qtrs)": "runway_quarters",
    "Next earnings": "next_earnings",
    "Ex-dividend": "ex_div",
    "Cap tier": "cap_tier",
    # --- Phase-1 timing FLAGS (live Excel formulas over the inputs) ---
    "Pullback flag": "FORMULA",
    "Oversold flag": "FORMULA",
    "Breakout flag": "FORMULA",
}

PCT_COLUMNS = {"7D Return", "1Y Return", "Upside to Target", "Growth (SWS)", "Div Yield",
               "Px vs 50d MA", "Px vs 200d MA", "% from 52wk high", "% from 52wk low",
               "6M momentum", "12M momentum", "Max drawdown (3y)"}
CAP_COLUMNS = {"Market Cap (EUR m, approx)", "Market Cap (local, m)",
               "Avg $ vol (EUR m)", "Net cash (EUR m)"}
PRICE_COLUMNS = {"Price (local)", "Analyst Target"}
VAL_COLUMNS = {"Valuation (SWS)"}
RATIO_COLUMNS = {"P/E (trailing)", "P/E (forward)", "P/S", "EV/EBITDA", "P/FCF",
                 "Beta", "RSI (14)", "Cash runway (qtrs)"}
DATE_COLUMNS = {"Next earnings", "Ex-dividend"}
WIDE_COLUMNS = {"Company": 34, "Business": 50, "Industry": 22, "Investing Idea": 26,
                "Source": 20}

BLUE = "0000FF"
BLACK = "000000"
RED = "C00000"
YELLOW = "FFFF00"

# First data row of the FX table on the Assumptions sheet. Threshold inputs
# occupy B3..B8, then the FX table header (rows 10-11), then the FX rows.
FX_START_ROW = 12


@dataclass
class BuildResult:
    unfiltered_path: str
    filtered_path: Optional[str]
    report: dict
    frame: pd.DataFrame = field(repr=False, default=None)


# ---------------------------------------------------------------------------
def load_config(path: Optional[str] = None) -> dict:
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "config", "defaults.yaml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Merge + dedupe
# ---------------------------------------------------------------------------
def _first_non_null(series):
    for v in series:
        if v is not None and not (isinstance(v, float) and np.isnan(v)) and v != "":
            return v
    return None


def merge_sources(sources: list[ParsedSource], cfg: dict) -> tuple[pd.DataFrame, dict]:
    frames = []
    per_source = {}
    gaps = []
    for src in sources:
        df = src.to_frame()
        # Guarantee an Investing Idea on every row.
        idea = src.investing_idea or src.source or "Unlabeled screen"
        df["investing_idea"] = df["investing_idea"].fillna(idea).replace("", idea)
        df["source"] = df["source"].fillna(src.source)
        frames.append(df)
        per_source[src.source] = {
            "parsed": src.parsed_count,
            "header": src.header_count,
        }
        if src.header_count is not None and src.header_count > src.parsed_count:
            gaps.append({
                "source": src.source,
                "header": src.header_count,
                "parsed": src.parsed_count,
                "missing": src.header_count - src.parsed_count,
            })

    frames = [f for f in frames if not f.empty]
    allrows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=CANONICAL_FIELDS)
    before = len(allrows)

    # Identity (identity.py): a resolved _yf_symbol wins; otherwise ticker|currency, and
    # two such rows are one company only if price AND market-cap ratios are <= 1.6.
    # A ticker reused by a different company gets a "#2" key and a note on every row.
    keys, collided = _identity.assign_keys(allrows.to_dict(orient="records"))
    allrows["_key"] = keys
    allrows["_collided"] = collided

    one_per_idea = bool(cfg.get("one_row_per_idea", False))
    merged_rows = []
    overlap = 0
    separate = []
    for _, grp in allrows.groupby("_key", sort=False):
        srcs = sorted({s for s in grp["source"] if s})
        if len(srcs) > 1:
            overlap += 1
        is_sep = bool(grp["_collided"].any())
        if is_sep:
            separate.append(str(grp["_key"].iloc[0]))
        if one_per_idea:
            # keep each idea separate but still merge duplicate (idea,key) rows
            for _, sub in grp.groupby("investing_idea", sort=False):
                merged_rows.append(_collapse(sub, srcs, is_sep))
        else:
            merged_rows.append(_collapse(grp, srcs, is_sep))

    merged = pd.DataFrame(merged_rows)
    report = {
        "per_source": per_source,
        "header_gaps": gaps,
        "rows_before_dedup": before,
        "rows_after_dedup": len(merged),
        "duplicates_removed": before - len(merged),
        "overlap_tickers": overlap,
        "same_ticker_kept_separate": separate,
    }
    return merged, report


def _collapse(grp: pd.DataFrame, srcs: list[str], separate: bool = False) -> dict:
    row = {}
    for f in CANONICAL_FIELDS:
        if f == "new_on_screener":
            row[f] = bool(grp[f].any())
        elif f == "investing_idea":
            ideas = sorted({i for i in grp[f] if i})
            row[f] = "; ".join(ideas)
        elif f == "source":
            row[f] = "; ".join(srcs)
        else:
            row[f] = _first_non_null(grp[f].tolist())
    if "_yf_symbol" in grp.columns:
        row["_yf_symbol"] = _first_non_null(grp["_yf_symbol"].tolist())
    row["_merge_note"] = _identity.COLLISION_NOTE if separate else None
    return row


# ---------------------------------------------------------------------------
# Region filter
# ---------------------------------------------------------------------------
def resolve_region(region, cfg: dict) -> Optional[list[str]]:
    """region may be None, a list of ISO codes, or a named set in defaults.yaml."""
    if region is None:
        return None
    if isinstance(region, str):
        codes = cfg.get("region_sets", {}).get(region)
        if codes is None:
            raise KeyError(f"Unknown region set '{region}'. "
                           f"Known: {list(cfg.get('region_sets', {}))}")
        return [str(c).lower() for c in codes]
    # already a list -> may contain named sets and/or codes
    out = []
    for item in region:
        named = cfg.get("region_sets", {}).get(item)
        out.extend(named if named else [item])
    return [str(c).lower() for c in out]


def filter_region(df: pd.DataFrame, codes: Optional[list[str]],
                  cfg: dict) -> tuple[pd.DataFrame, dict]:
    if codes is None:
        return df.copy(), {"applied": False}

    codes = set(codes)
    trans = set(cfg.get("edge_case_countries", {}).get("transcontinental", []))
    country = df["country"].fillna("").str.lower()

    keep_mask = country.isin(codes)
    unclassified = df[country == ""]
    kept = df[keep_mask].copy()
    dropped = df[~keep_mask & (country != "")]

    judgment = []
    # Transcontinental countries present but excluded -> state the call.
    for tc in sorted(trans):
        n = int((dropped["country"].fillna("").str.lower() == tc).sum())
        if n and tc not in codes:
            judgment.append(
                f"{tc.upper()} classified as transcontinental and EXCLUDED "
                f"({n} row(s)); include it explicitly if you want it in-region.")
        n_in = int((kept["country"].fillna("").str.lower() == tc).sum())
        if n_in:
            judgment.append(
                f"{tc.upper()} is transcontinental but was requested in-region "
                f"({n_in} row(s) KEPT) — borderline, verify it belongs.")
    n_eu = int((kept["country"].fillna("").str.lower() == "eu").sum())
    if n_eu:
        judgment.append(
            f"{n_eu} EUR-quoted row(s) kept with unknown exchange (table view); "
            f"resolved by exchange probing where price/1Y match, else not enriched.")
    if len(unclassified):
        judgment.append(
            f"{len(unclassified)} row(s) had no listing country in the paste "
            f"(usually global-list-only rows) and were EXCLUDED from the region "
            f"filter. Re-check them if you expected them kept.")

    report = {
        "applied": True,
        "codes": sorted(codes),
        "kept": len(kept),
        "kept_by_country": kept["country"].fillna("(none)").value_counts().to_dict(),
        "dropped": len(dropped),
        "dropped_by_country": dropped["country"].fillna("(none)").value_counts().to_dict(),
        "unclassified_excluded": len(unclassified),
        "judgment_calls": judgment,
        "policy": ("Rows are classified by LISTING VENUE (the exchange country "
                   "in the URL), not domicile. ADRs and foreign companies on a "
                   "local exchange therefore follow the venue; borderline cases "
                   "are listed above."),
    }
    return kept, report


# ---------------------------------------------------------------------------
# EUR cap, flags, sort (Python side — for ordering and verification only)
# ---------------------------------------------------------------------------
def _fx_map(cfg: dict) -> dict:
    return {k.upper(): float(v) for k, v in cfg.get("fx_per_eur", {}).items()}


def sort_by_eur(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    fx = _fx_map(cfg)
    th = cfg.get("thresholds", {})
    liq = float(th.get("liquidity_eur_m", 250))
    down = float(th.get("downtrend_1y", -0.30))
    big = float(th.get("big_move_7d_abs", 0.08))

    def eur(row):
        cap, cur = row["market_cap_local_m"], row["currency"]
        if cap is None or cur is None or cur.upper() not in fx:
            return np.nan
        return cap / fx[cur.upper()]

    df["_eur_cap"] = df.apply(eur, axis=1)
    df["_flag_liq"] = df["_eur_cap"].apply(lambda v: v == v and v < liq)  # NaN safe
    df["_flag_down"] = df["ret_1y"].apply(
        lambda v: v is not None and not (isinstance(v, float) and np.isnan(v)) and v <= down)
    df["_flag_big"] = df["ret_7d"].apply(
        lambda v: v is not None and not (isinstance(v, float) and np.isnan(v)) and abs(v) >= big)
    df = df.sort_values("_eur_cap", ascending=False, na_position="last").reset_index(drop=True)
    return df


def flag_counts(df: pd.DataFrame) -> dict:
    return {
        "illiquid": int(df["_flag_liq"].sum()),
        "downtrend": int(df["_flag_down"].sum()),
        "big_move": int(df["_flag_big"].sum()),
    }


# ---------------------------------------------------------------------------
# Workbook writer
# ---------------------------------------------------------------------------
def _company_display(row) -> str:
    """Pasted name first; else the name Yahoo returned for the cross-checked
    listing; else an explicit placeholder. Never guessed from memory."""
    c = row.get("company")
    if c and str(c).strip():
        return str(c)
    y = row.get("yf_name")
    if isinstance(y, str) and y.strip():
        return y.strip()
    return f"{row.get('ticker')} (name not in paste)"


def _new_display(row) -> str:
    return "New" if row.get("new_on_screener") else ""


def write_workbook(df: pd.DataFrame, cfg: dict, path: str, meta: dict) -> str:
    columns = cfg["screen_columns"]
    # Every currency in the data gets an FX row. Ones without a configured rate get
    # an EMPTY yellow input cell (the EUR cap shows blank until the user fills it),
    # so MATCH never fails and the sheet never shows #N/A.
    fx = dict(cfg.get("fx_per_eur", {}))
    if "currency" in df.columns:
        for c in sorted({str(c) for c in df["currency"].dropna() if str(c).strip()}):
            fx.setdefault(c, None)
    cfg = {**cfg, "fx_per_eur": fx}
    th = cfg.get("thresholds", {})

    wb = Workbook()
    ws = wb.active
    ws.title = "Screen"
    asm = wb.create_sheet("Assumptions")
    notes = wb.create_sheet("Notes")

    # ---- Assumptions sheet (build first so we know cell refs) ----
    _write_assumptions(asm, cfg, meta)
    liq_ref, down_ref, big_ref = "Assumptions!$B$3", "Assumptions!$B$4", "Assumptions!$B$5"
    osold_ref, brk_ref, pb_ref = "Assumptions!$B$6", "Assumptions!$B$7", "Assumptions!$B$8"
    tflag_refs = {"oversold": osold_ref, "breakout": brk_ref, "pullback": pb_ref}
    fx_keys = list(fx.keys())
    fx_start, fx_end = FX_START_ROW, FX_START_ROW + len(fx_keys) - 1
    fx_key_range = f"Assumptions!$A${fx_start}:$A${fx_end}"
    fx_val_range = f"Assumptions!$B${fx_start}:$B${fx_end}"

    # column name -> letter
    col_letter = {name: get_column_letter(i + 1) for i, name in enumerate(columns)}

    # ---- Screen header ----
    header_font = Font(name="Arial", size=10, bold=True)
    for i, name in enumerate(columns):
        c = ws.cell(row=1, column=i + 1, value=name)
        c.font = header_font
        c.alignment = Alignment(vertical="center", wrap_text=False)

    # ---- Screen data rows ----
    for r, (_, row) in enumerate(df.iterrows(), start=2):
        for i, name in enumerate(columns):
            src = COLUMN_SOURCES.get(name)
            cell = ws.cell(row=r, column=i + 1)
            if src == "FORMULA":
                cell.value = _formula_for(name, r, col_letter, liq_ref, down_ref,
                                          big_ref, fx_key_range, fx_val_range,
                                          tflag_refs)
                cell.font = Font(name="Arial", size=10, color=BLACK)
            elif src == "_company_display":
                cell.value = _company_display(row)
                cell.font = Font(name="Arial", size=10, color=BLUE)
            elif src == "_new_display":
                cell.value = _new_display(row)
                cell.font = Font(name="Arial", size=10, color=BLUE)
            else:
                val = row.get(src)
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    cell.value = val
                cell.font = Font(name="Arial", size=10, color=BLUE)
            _apply_number_format(cell, name)

    _finish_screen(ws, columns, len(df), col_letter)

    # ---- Notes sheet ----
    _write_notes(notes, meta)

    wb.calculation.fullCalcOnLoad = True  # Excel recalculates every formula on open
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    wb.save(path)
    return path


def _formula_for(name, r, cl, liq, down, big, fx_key, fx_val, tflag=None) -> str:
    tflag = tflag or {}
    if name == "Oversold flag":
        # RSI(14) below the oversold threshold.
        R = cl["RSI (14)"]
        return (f'=IF(AND({R}{r}<>"",{R}{r}<{tflag["oversold"]}),'
                f'"oversold","")')
    if name == "Breakout flag":
        # Price within breakout_within_pct of the 52wk high.
        H = cl["% from 52wk high"]
        return (f'=IF(AND({H}{r}<>"",ABS({H}{r})<={tflag["breakout"]}),'
                f'"breakout","")')
    if name == "Pullback flag":
        # Uptrend (MA50>MA200 <=> distance-to-MA50 < distance-to-MA200) AND price
        # within pullback_within_pct of the 50d MA.
        D50, D200 = cl["Px vs 50d MA"], cl["Px vs 200d MA"]
        return (f'=IF(AND({D50}{r}<>"",{D200}{r}<>"",{D50}{r}<{D200}{r},'
                f'ABS({D50}{r})<={tflag["pullback"]}),"pullback","")')
    if name == "Market Cap (EUR m, approx)":
        H, G = cl["Market Cap (local, m)"], cl["Currency"]
        rate = f"INDEX({fx_val},MATCH({G}{r},{fx_key},0))"
        return (f'=IF({H}{r}="","",IF({G}{r}="","",IF({rate}="","",{H}{r}/{rate})))')
    if name == "Upside to Target":
        L, I = cl["Analyst Target"], cl["Price (local)"]
        return (f'=IF(AND({L}{r}<>"",{I}{r}<>"",{I}{r}<>0),'
                f'{L}{r}/{I}{r}-1,"")')
    if name == "Liquidity flag":
        D = cl["Market Cap (EUR m, approx)"]
        return f'=IF(AND({D}{r}<>"",{D}{r}<{liq}),"illiquid","")'
    if name == "Downtrend flag":
        K = cl["1Y Return"]
        return f'=IF(AND({K}{r}<>"",{K}{r}<={down}),"downtrend","")'
    if name == "Big 7D move flag":
        J = cl["7D Return"]
        return f'=IF(AND({J}{r}<>"",ABS({J}{r})>={big}),"big move","")'
    return ""


def _apply_number_format(cell, name):
    if name in PCT_COLUMNS:
        cell.number_format = "0.0%"
    elif name in CAP_COLUMNS:
        cell.number_format = "#,##0;-#,##0"
    elif name in PRICE_COLUMNS:
        cell.number_format = "#,##0.00;-#,##0.00"
    elif name in VAL_COLUMNS:
        cell.number_format = "0.0;-0.0"
    elif name in RATIO_COLUMNS:
        cell.number_format = "0.00;-0.00"
    elif name in DATE_COLUMNS:
        cell.number_format = "yyyy-mm-dd"


def _finish_screen(ws, columns, nrows, col_letter):
    last_col = get_column_letter(len(columns))
    last_row = nrows + 1
    ws.auto_filter.ref = f"A1:{last_col}{last_row}"
    # Freeze header row + the first three columns (through Ticker).
    ticker_idx = columns.index("Ticker") + 1 if "Ticker" in columns else 3
    ws.freeze_panes = f"{get_column_letter(ticker_idx + 1)}2"
    for i, name in enumerate(columns):
        width = WIDE_COLUMNS.get(name, 14)
        ws.column_dimensions[get_column_letter(i + 1)].width = width


def _write_assumptions(asm, cfg, meta):
    th = cfg.get("thresholds", {})
    fx = cfg.get("fx_per_eur", {})
    input_font = Font(name="Arial", size=10, color=BLUE)
    label_font = Font(name="Arial", size=10)
    bold = Font(name="Arial", size=10, bold=True)
    yellow = PatternFill(fill_type="solid", fgColor=YELLOW)

    asm["A1"] = "Assumptions (edit the yellow cells; the Screen sheet re-flags)"
    asm["A1"].font = bold

    rows = [
        ("A3", "Liquidity cutoff (EUR m)", "B3", th.get("liquidity_eur_m", 250)),
        ("A4", "Downtrend cutoff (1Y return)", "B4", th.get("downtrend_1y", -0.30)),
        ("A5", "Big 7D move cutoff (abs)", "B5", th.get("big_move_7d_abs", 0.08)),
        ("A6", "Oversold RSI(14) below", "B6", th.get("oversold_rsi", 30)),
        ("A7", "Breakout: within % of 52wk high", "B7", th.get("breakout_within_pct", 0.03)),
        ("A8", "Pullback: within % of 50d MA", "B8", th.get("pullback_within_pct", 0.03)),
    ]
    for a, label, b, val in rows:
        asm[a] = label
        asm[a].font = label_font
        asm[b] = val
        asm[b].font = input_font
        asm[b].fill = yellow
    asm["B4"].number_format = "0.0%"
    asm["B5"].number_format = "0.0%"
    asm["B7"].number_format = "0.0%"
    asm["B8"].number_format = "0.0%"

    asm["A10"] = "FX table — local currency units per 1 EUR"
    asm["A10"].font = bold
    asm["A11"], asm["B11"] = "Currency", "Units per 1 EUR"
    asm["A11"].font = bold
    asm["B11"].font = bold
    r = FX_START_ROW
    no_rate = []
    for cur, rate in fx.items():
        asm.cell(row=r, column=1, value=cur).font = label_font
        vc = asm.cell(row=r, column=2, value=None if rate is None else float(rate))
        vc.font = input_font
        vc.fill = yellow
        if rate is None:
            no_rate.append(cur)
        r += 1
    if no_rate:
        r += 1
        asm.cell(row=r, column=1,
                 value=("No FX rate for: " + ", ".join(no_rate) + ". Their EUR market cap "
                        "and liquidity flag stay blank until you type a rate (local units "
                        "per 1 EUR) in the yellow cell.")
                 ).font = Font(name="Arial", size=10, color=RED, bold=True)

    if not cfg.get("fx_is_live", False):
        note = asm.cell(row=r + 1, column=1,
                        value=("FX RATES ARE PLACEHOLDERS — not live, not sourced. "
                               "The EUR market cap and the liquidity flag are only "
                               "indicative, especially near the cutoff."))
        note.font = Font(name="Arial", size=10, color=RED, bold=True)
    else:
        asm.cell(row=r + 1, column=1,
                 value=f"FX source: {cfg.get('fx_source')} (as of {cfg.get('fx_asof')})"
                 ).font = label_font
    asm.column_dimensions["A"].width = 46
    asm.column_dimensions["B"].width = 18


def _write_notes(notes, meta):
    font = Font(name="Arial", size=10)
    bold = Font(name="Arial", size=10, bold=True)
    lines = meta.get("notes_lines", [])
    notes["A1"] = "Notes"
    notes["A1"].font = bold
    for i, line in enumerate(lines, start=2):
        c = notes.cell(row=i, column=1, value=line)
        c.font = bold if line.endswith(":") else font
        c.alignment = Alignment(wrap_text=False)
    notes.column_dimensions["A"].width = 110


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run(sources: list[ParsedSource], cfg: dict, out_path: str,
        region=None, snapshot_date=None, enrich=True, fetch=None,
        enrich_scope="all") -> BuildResult:
    snapshot_date = snapshot_date or _dt.date.today().isoformat()
    merged, merge_rep = merge_sources(sources, cfg)

    # Enrich the MERGED frame AFTER merge_sources, BEFORE region-filter/sort/write
    # (decision #8). enrich_rows does not mutate ret_1y/ret_7d/currency/
    # market_cap_local_m and does not sort.
    # enrich_scope="filtered" (needs a region) filters FIRST and only fetches the
    # surviving rows; rows outside the filter stay un-enriched.
    codes = resolve_region(region, cfg)
    enrich_rep = {"enriched": 0, "unresolved": 0, "cross_check_failed": 0,
                  "fetch_failed": 0, "probed": 0, "resolved_by_probe": 0}
    if enrich:
        fetch = fetch or _enrich._fetch_snapshot
        if enrich_scope == "filtered" and codes is not None:
            pre, _ = filter_region(merged, codes, cfg)
            enriched = _enrich.enrich_rows(pre, cfg, fetch=fetch)
            merged = merged.copy()
            for f in _enrich.ENRICH_FIELDS:
                merged[f] = pd.Series([None] * len(merged), index=merged.index,
                                      dtype=object)
            for f in _enrich.ENRICH_FIELDS:
                merged.loc[pre.index, f] = pd.Series(
                    enriched[f].tolist(), index=pre.index, dtype=object)
            outside = merged.index.difference(pre.index)
            merged.loc[outside, "_enrich_note"] = "not enriched (outside region filter)"
            cov = enriched.attrs.get("enrich_coverage", {})
        else:
            merged = _enrich.enrich_rows(merged, cfg, fetch=fetch)
            cov = merged.attrs.get("enrich_coverage", {})
        ok = merged["_yf_symbol"].notna() & merged["_enrich_note"].isna()
        enrich_rep = {
            "enriched": int(ok.sum()),
            "unresolved": len(cov.get("unresolved", [])),
            "cross_check_failed": len(cov.get("cross_check_failed", [])),
            "fetch_failed": len(cov.get("fetch_failed", [])),
            "probed": int(cov.get("probed", 0)),
            "resolved_by_probe": int(cov.get("resolved_by_probe", 0)),
        }

    filtered, filt_rep = filter_region(merged, codes, cfg)

    unfiltered_sorted = sort_by_eur(merged, cfg)
    filtered_sorted = sort_by_eur(filtered, cfg)

    report = {
        "snapshot_date": snapshot_date,
        "merge": merge_rep,
        "filter": filt_rep,
        "flag_counts_unfiltered": flag_counts(unfiltered_sorted),
        "flag_counts_filtered": flag_counts(filtered_sorted),
        "fx_is_live": cfg.get("fx_is_live", False),
        "fx_source": cfg.get("fx_source"),
        "enrich": enrich_rep,
    }

    # Always write the UNFILTERED workbook; never overwrite it with a filtered one.
    meta_unf = _meta(cfg, report, merge_rep, filt_rep=None, snapshot_date=snapshot_date,
                     scope="all parsed rows (unfiltered)")
    write_workbook(unfiltered_sorted, cfg, out_path, meta_unf)

    filtered_path = None
    if codes is not None:
        base, ext = os.path.splitext(out_path)
        tag = "-".join(sorted(set(codes)))[:40]
        filtered_path = f"{base}__filtered_{tag}{ext}"
        meta_filt = _meta(cfg, report, merge_rep, filt_rep, snapshot_date,
                          scope=f"region filter: {sorted(set(codes))}")
        write_workbook(filtered_sorted, cfg, filtered_path, meta_filt)

    return BuildResult(unfiltered_path=out_path, filtered_path=filtered_path,
                       report=report,
                       frame=filtered_sorted if codes is not None else unfiltered_sorted)


def _meta(cfg, report, merge_rep, filt_rep, snapshot_date, scope) -> dict:
    lines = [
        "Source & scope:",
        f"  Scope: {scope}",
        f"  Snapshot date: {snapshot_date}",
        f"  Sources: {', '.join(merge_rep['per_source'].keys())}",
        "",
        "Row counts:",
    ]
    for s, d in merge_rep["per_source"].items():
        hdr = d["header"] if d["header"] is not None else "n/a"
        lines.append(f"  {s}: parsed {d['parsed']} vs page header {hdr}")
    for g in merge_rep["header_gaps"]:
        lines.append(f"  GAP: '{g['source']}' page claims {g['header']} but only "
                     f"{g['parsed']} rows were in the paste ({g['missing']} missing). "
                     f"Scroll to the bottom of the page before copying to capture all rows.")
    lines += [
        f"  Rows before dedup: {merge_rep['rows_before_dedup']}",
        f"  Duplicates removed: {merge_rep['duplicates_removed']}",
        f"  Tickers appearing in more than one source (overlap): {merge_rep['overlap_tickers']}",
        f"  Unique rows: {merge_rep['rows_after_dedup']}",
        "",
    ]
    if filt_rep and filt_rep.get("applied"):
        lines += [
            "Region filter:",
            f"  Kept: {filt_rep['kept']}  by country: {filt_rep['kept_by_country']}",
            f"  Dropped: {filt_rep['dropped']}  by country: {filt_rep['dropped_by_country']}",
            f"  Excluded (no country in paste): {filt_rep['unclassified_excluded']}",
            f"  Policy: {filt_rep['policy']}",
        ]
        for j in filt_rep["judgment_calls"]:
            lines.append(f"  Judgment call: {j}")
        lines.append("")
    lines += [
        "Caveats:",
        "  Sort is STATIC: rows are ordered by EUR market cap at build time. "
        "If you edit FX rates, re-sort the Screen sheet by the EUR cap column.",
    ]
    if not cfg.get("fx_is_live", False):
        lines.append("  FX rates are PLACEHOLDERS (not live, not sourced). The EUR cap "
                     "and liquidity flag are only indicative, especially near the cutoff.")
    else:
        lines.append(f"  FX source: {cfg.get('fx_source')} as of {cfg.get('fx_asof')}.")
    lines += [
        "  'Valuation (SWS)' is the Simply Wall St valuation column. It is NOT a "
        "uniform P/E — banks read ~1, software ~29 — so do not compare it across sectors.",
        "  Analyst target, upside, valuation, growth, dividend yield and industry exist "
        "only where the US table view was pasted. Blank elsewhere means 'not in paste', not zero.",
        "  Company names are never invented. Where the paste has no name (the US table view "
        "has none), the name is the one Yahoo Finance returned for the matched listing, used "
        "only after its price, currency and 1Y return cross-checked against the paste. "
        "'<TICKER> (name not in paste)' means neither source had one; Business is left blank.",
        "",
        "This workbook is a screening aid. It is NOT investment advice and the flags are "
        "not trade signals.",
    ]
    return {"notes_lines": lines}


__all__ = [
    "load_config", "merge_sources", "filter_region", "resolve_region",
    "sort_by_eur", "flag_counts", "write_workbook", "run", "BuildResult",
    "COLUMN_SOURCES",
]
