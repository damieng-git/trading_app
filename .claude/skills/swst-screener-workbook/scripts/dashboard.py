"""Self-contained static HTML dashboard for one scored SWS list, or a hub of many.

render_html(df, report, cfg, meta) -> str              one list, no tab bar
render_hub_html(views, cfg, meta) -> str               tab per view (see build_hub.py)

The page is one file: inline CSS, plain JS, two inline SVG scatters (Value vs Quality,
Value vs Timing) sharing one selection with the table, filter drop-downs whose counts
and the tab counts follow the active filters, and the data as JSON in
<script type="application/json" id="data">. No external resources, no server.
All data reaches the DOM through textContent only.

Screening aid only. Scores are closeness to fixed hand-set anchors, not forecasts.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import math
from typing import Optional

import numpy as np
import pandas as pd

import notes as _notes
import peers as _peers
import takes as _takes
from flags import _num

US_CODES = {"us"}
FLAG_NAMES = ("oversold", "pullback", "breakout", "downtrend")


# ---------------------------------------------------------------------------
# Data shaping
# ---------------------------------------------------------------------------
def sanitize(obj):
    """NaN/NaT/inf -> None, numpy scalars -> python, dates -> ISO strings."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if obj is pd.NaT:
        return None
    if isinstance(obj, pd.Timestamp):
        return obj.date().isoformat()
    if isinstance(obj, _dt.datetime):
        return obj.date().isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if isinstance(obj, (int, str)):
        return obj
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return str(obj)


def _pct(v, mult=100.0, nd=1):
    f = _num(v)
    return None if f is None else round(f * mult, nd)


def _region(country) -> str:
    c = str(country or "").lower()
    return "US" if c in US_CODES else ("EU" if c else "Other")


def _clean_str(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v)
    return s if s.strip() else None


def build_records(df: pd.DataFrame, stats: dict) -> list[dict]:
    recs = []
    for i, (_, row) in enumerate(df.iterrows()):
        r = row.to_dict()
        row_stats = stats
        if r.get("_snapshot_date") is not None:     # hub: the row's own run date
            row_stats = dict(stats, snapshot_date=_notes._as_date(r.get("_snapshot_date")))
        pf, pt = _num(r.get("pe_forward")), _num(r.get("pe_trailing"))
        pe_m = "pe_forward" if pf is not None else "pe_trailing"
        flags = [n for n in ("oversold", "pullback", "breakout")
                 if bool(r.get("flag_" + n))]
        if bool(r.get("_flag_down")):
            flags.append("downtrend")
        comp = _num(r.get("score_0_100"))
        company = _clean_str(r.get("company")) or _clean_str(r.get("yf_name"))
        heads = r.get("headlines")
        heads = heads if isinstance(heads, list) else []
        recs.append({
            "id": i,
            "ticker": _clean_str(r.get("ticker")),
            "company": company,
            "country": str(r.get("country") or "").lower() or None,
            # price at the data snapshot (yfinance, pence already in pounds); pasted SWS price as fallback
            "price": _num(r.get("yf_price")) if _num(r.get("yf_price")) is not None else _num(r.get("price_local")),
            "ccy": _clean_str(r.get("currency")),
            "region": _region(r.get("country")),
            "industry": _clean_str(r.get("industry")),
            "tier": _clean_str(r.get("tier")),
            "score": None if comp is None else int(comp),
            "rank_tier": _num(r.get("rank_in_tier")),
            "rank_list": _num(r.get("rank_in_list")),
            "v": _pct(r.get("score_value"), 100, 0),
            "q": _pct(r.get("score_quality"), 100, 0),
            "t": _pct(r.get("score_timing"), 100, 0),
            "r": _pct(r.get("score_risk"), 100, 0),
            "gate": _clean_str(r.get("gate")),
            "down": _clean_str(r.get("downtrend_label")),
            "pe": pf if pf is not None else pt,
            "pe_kind": "forward" if pf is not None else ("trailing" if pt is not None else None),
            "pe_vs": _pct(r.get("vs_peer_" + pe_m), 100, 0),
            "pe_med": _num(r.get("peer_med_" + pe_m)),
            "ps": _num(r.get("ps_ratio")), "ps_vs": _pct(r.get("vs_peer_ps_ratio"), 100, 0),
            "ps_med": _num(r.get("peer_med_ps_ratio")),
            "evs": _num(r.get("ev_sales")), "evs_vs": _pct(r.get("vs_peer_ev_sales"), 100, 0),
            "evs_med": _num(r.get("peer_med_ev_sales")),
            "evf": _num(r.get("ev_fcf")), "evf_vs": _pct(r.get("vs_peer_ev_fcf"), 100, 0),
            "evf_med": _num(r.get("peer_med_ev_fcf")),
            "peer_group": _clean_str(r.get("peer_group")), "peer_n": _num(r.get("peer_n")),
            "rsi": _num(r.get("rsi_14")),
            "ma200": _pct(r.get("dist_ma200"), 100, 1),
            "adv": _num(r.get("avg_dollar_vol_eur_m")),
            "earn": r.get("next_earnings"),
            "flags": flags,
            "cov": _pct(r.get("coverage_pct"), 100, 0),
            "cap_m": _num(r.get("_eur_cap")),
            "ret1y": _pct(r.get("ret_1y"), 100, 1),
            "note": _notes.row_note(r, row_stats),
            "reason": _clean_str(r.get("not_scored_reason")),
            "data_note": "; ".join(n for n in (_clean_str(r.get("_enrich_note")),
                                                _clean_str(r.get("_probe_note")),
                                                _clean_str(r.get("_merge_note"))) if n) or None,
            "take": _clean_str(r.get("take")),
            "take_status": _clean_str(r.get("take_status")),
            "consensus": _clean_str(r.get("consensus_line")) or _clean_str(_takes.consensus_line(r)),
            "heads": [{"t": _clean_str(h.get("title")), "d": _clean_str(h.get("date")),
                       "u": h.get("link") if str(h.get("link") or "").startswith("https://")
                       else None} for h in heads[:3] if isinstance(h, dict)],
        })
    return sanitize(recs)


def banner_counts(df: pd.DataFrame, report: dict) -> dict:
    per = report.get("merge", {}).get("per_source", {})
    headers = [d["header"] for d in per.values() if d.get("header") is not None]
    parsed = sum(d.get("parsed", 0) for d in per.values())
    filt = report.get("filter", {})
    comp = pd.to_numeric(df["composite"], errors="coerce") if "composite" in df.columns \
        else pd.Series([], dtype=float)
    scored = int(comp.notna().sum())
    return {
        "header": sum(headers) if headers else None,
        "parsed": parsed,
        "kept": filt.get("kept", len(df)) if filt.get("applied") else len(df),
        "enriched": report.get("enrich", {}).get("enriched", 0),
        "scored": scored,
        "unscored": len(df) - scored,
    }


def banner_line(df: pd.DataFrame, report: dict, meta: dict) -> str:
    c = banner_counts(df, report)
    hdr = "n/a" if c["header"] is None else c["header"]
    return (f"{meta.get('title') or 'SWS list'} - snapshot {meta.get('snapshot_date')}. "
            f"Screen page claims {hdr}; {c['parsed']} parsed; {c['kept']} kept by "
            f"{meta.get('region_label', 'US+EU')} filter; {c['enriched']} enriched; "
            f"{c['scored']} scored; {c['unscored']} not scored.")


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
def _fmt_anchor(d: dict) -> str:
    return "; ".join(f"{k} {v[0]} to {v[1]}" for k, v in d.items())


def footer_paragraphs(df: pd.DataFrame, report: dict, cfg: dict, meta: dict) -> list[tuple[str, str]]:
    sc, th = cfg["scoring"], cfg.get("thresholds", {})
    w = sc["weights"]
    paras: list[tuple[str, str]] = []
    paras.append(("Method",
        "Each metric is scored 0 to 1 against fixed anchors, not against this list's "
        "distribution. Value, quality and risk are the mean of their available metric "
        "scores with a neutral 0.5 prior counted as one extra metric; a part needs at "
        f"least {sc['min_metrics']['value']} metrics or it is blank. Timing is the best "
        "of the oversold, breakout and pullback setup scores (0 means no setup). The "
        "composite is a cap-tier weighted sum of the four parts and exists only when all "
        "four parts exist; a missing input is never counted as 0."))
    paras.append(("Weights (value / quality / timing / risk)",
        "; ".join(f"{t}: " + " / ".join(f"{x:.2f}" for x in w[t])
                  for t in ("large", "mid", "small"))
        + f". Cap tiers: large at or above EUR {th.get('cap_large_eur_m')}m, mid at or "
          f"above EUR {th.get('cap_mid_eur_m')}m, else small."))
    paras.append(("Anchors (hand-set, not fitted or backtested)",
        f"Value, lower is better: {_fmt_anchor(sc['value_anchors'])}. "
        f"Quality, higher is better: {_fmt_anchor(sc['quality_anchors'])}; "
        f"runway of {sc['runway_full_qtrs']} quarters scores 1. "
        f"Risk: {_fmt_anchor(sc['risk_anchors'])} (beta lower is better, drawdown higher "
        f"is better, volume on a log scale). Timing: RSI zero point {sc['timing']['rsi_zero']}, "
        f"breakout zero at {sc['timing']['breakout_zero_pct']:.0%} from the high, pullback "
        f"zero at {sc['timing']['pullback_zero_pct']:.0%} from the 50d MA. Financials use "
        "P/E and P/B for value and growth and ROE for quality."))
    paras.append(("Reading the scores",
        "Scores measure closeness to fixed anchors, not future returns."))
    n_unenr = int(sum(1 for v in df.get("_yf_symbol", []) if v is None
                      or (isinstance(v, float) and math.isnan(v)))) if "_yf_symbol" in df.columns else len(df)
    paras.append(("Blanks",
        f"{n_unenr} of {len(df)} rows have no market data. Blank fields show n/a, never 0."))
    fx_live = cfg.get("fx_is_live", False)
    paras.append(("FX",
        f"{cfg.get('fx_source')}, as of {cfg.get('fx_asof')}."
        + ("" if fx_live else " Rates are placeholders; EUR caps and tiers are indicative.")))
    paras.append(("Thresholds at build time",
        f"Oversold: RSI below {th.get('oversold_rsi')}. Breakout: within "
        f"{th.get('breakout_within_pct'):.1%} of the 52wk high. Pullback: uptrend and within "
        f"{th.get('pullback_within_pct'):.1%} of the 50d MA. Downtrend: 1Y return at or below "
        f"{th.get('downtrend_1y'):.0%}."))
    gaps = report.get("merge", {}).get("header_gaps", [])
    if gaps:
        gtxt = " ".join(f"'{g['source']}': page claims {g['header']}, {g['parsed']} rows in "
                        f"the paste ({g['missing']} missing)." for g in gaps)
        gtxt += " Scroll to the bottom of the page before copying to capture all rows."
    else:
        gtxt = "No gap between the page header count and the rows pasted."
    paras.append(("Header gap", gtxt))
    jc = report.get("filter", {}).get("judgment_calls", [])
    if jc:
        paras.append(("Judgment calls", " ".join(jc)))
    n_take = int(sum(1 for t in df.get("take", []) if isinstance(t, str) and t.strip())) \
        if "take" in df.columns else 0
    if "take" in df.columns:
        paras.append(("Claude's takes",
            f"{n_take} row(s) carry a take written by Claude (model alias "
            f"'{meta.get('take_model', 'sonnet')}') on the build date. It may only use the facts "
            "shown for that row plus the listed Yahoo Finance headlines, and every take is "
            "checked for length and verdict words before it is shown. It is generated text: "
            "it can misread a figure or a headline, and it goes stale with the data. The "
            "analyst-consensus line is quoted from Yahoo, not our view. Company names for "
            "table-view rows come from Yahoo for the cross-checked listing."))
    paras.append(("Not advice",
        "Screening aid, not investment advice. A high score is not a buy signal; review "
        "each name."))
    return paras


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
def _embed_json(obj) -> str:
    s = json.dumps(sanitize(obj), allow_nan=False, ensure_ascii=False)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _foot_html(paras) -> str:
    return "".join(f"<p><b>{html.escape(h)}.</b> {html.escape(t)}</p>" for h, t in paras)


def _warn_html(text) -> str:
    return f'<div class="warn">{html.escape(text)}</div>' if text else ""


def _assemble(title: str, banner: str, warn: str, foot: str, payload: dict,
              tabs: bool) -> str:
    return (_TEMPLATE
            .replace("__TITLE__", html.escape(str(title)))
            .replace("__TABS__", '<div class="tabs" id="tabs"></div>' if tabs else "")
            .replace("__BANNER__", html.escape(banner))
            .replace("__STALE__", warn)
            .replace("__FOOTER__", foot)
            .replace("__DATA__", _embed_json(payload))
            .replace("__JS__", _JS))


def render_html(df: pd.DataFrame, report: dict, cfg: dict, meta: dict) -> str:
    """One list, no tab bar. (The hub page uses the same template and JS.)"""
    meta = dict(meta or {})
    snap = meta.get("snapshot_date") or _dt.date.today().isoformat()
    meta["snapshot_date"] = snap
    stats = _notes.list_stats(df, snap)
    if "peer_group" not in df.columns:      # per-list page: peers are the other stocks in this list
        df = pd.DataFrame(_peers.attach(df.to_dict(orient="records"))).astype(object)
        df = df.where(df.notna(), None)
    recs = build_records(df, stats)

    stale_days = int(cfg["scoring"].get("stale_days", 3))
    today = _notes._as_date(meta.get("today")) or _dt.date.today()
    snap_d = _notes._as_date(snap)
    warn = ""
    if snap_d is not None and (today - snap_d).days > stale_days:
        warn = _warn_html(f"Data snapshot is {(today - snap_d).days} days old "
                          f"(limit {stale_days}). Prices and indicators may have moved; "
                          f"re-run before relying on this page.")
    return _assemble(meta.get("title") or "SWS list", banner_line(df, report, meta), warn,
                     _foot_html(footer_paragraphs(df, report, cfg, meta)),
                     {"rows": recs, "snapshot": snap}, tabs=False)


def render_hub_html(views: list[dict], cfg: dict, meta: dict) -> str:
    """views = [{"id","label","records","banner","footer_paras","stale": bool,
    "warn_text": optional}], first view is active by default. Tab ids become
    location.hash values."""
    meta = dict(meta or {})
    if not views:
        raise ValueError("render_hub_html needs at least one view")
    out_views = []
    for v in views:
        warn = (v.get("warn_text") or "Data in this tab is out of date.") if v.get("stale") else None
        out_views.append({"id": str(v["id"]), "label": str(v["label"]),
                          "records": v["records"], "banner": v["banner"],
                          "footer": [[h, t] for h, t in v["footer_paras"]],
                          "warn": warn})
    first = out_views[0]
    return _assemble(meta.get("title") or "SWS screener lists", first["banner"],
                     _warn_html(first["warn"]),
                     _foot_html(views[0]["footer_paras"]),
                     {"views": out_views, "snapshot": meta.get("snapshot_date")},
                     tabs=len(out_views) > 1)


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ dashboard</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b2430;--mut:#5b6675;--line:#d9dde3;--us:#c0392b;--eu:#1f5fa8;--oth:#7a8594;--sel:#b8860b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:0 16px 40px}
.wrap{max-width:1280px;margin:0 auto}
h1{font-size:20px;margin:18px 0 6px}
h2{font-size:15px;margin:22px 0 8px}
.banner{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.warn{background:#fff6e0;border:1px solid #e3c36b;border-radius:6px;padding:8px 12px;margin-top:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px;margin-top:12px}
.filters{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.fg{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center}
label{white-space:nowrap}
.dd{position:relative}
.ddbtn{font:inherit;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);cursor:pointer;white-space:nowrap}
.ddbtn.act{border-color:#1b2430;font-weight:600}
.ddpanel{position:absolute;top:100%;left:0;z-index:6;margin-top:4px;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px 10px;min-width:190px;max-height:320px;overflow-y:auto;box-shadow:0 4px 14px rgba(0,0,0,.12)}
.ddpanel label{display:block;padding:2px 0}
.ddall{display:flex;gap:10px;margin-bottom:6px;font-size:12px}
.ddall a{color:#1f5fa8;cursor:pointer}
.chart-top{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center;margin-bottom:6px}
.charts{display:flex;flex-wrap:wrap;gap:12px}
.chartbox{position:relative;flex:1 1 380px;min-width:0}
.chartbox h3{font-size:13px;margin:0 0 4px}
svg.chart{width:100%;height:auto;display:block;background:#fff}
#goto{color:#1f5fa8;cursor:pointer;margin-left:6px}
.axis{stroke:#9aa3af;stroke-width:1}
.grid{stroke:#e5e8ec;stroke-width:1}
.quad{stroke:#9aa3af;stroke-width:1;stroke-dasharray:5 4}
.tick{font-size:10px;fill:#5b6675}
.alab{font-size:12px;fill:#1b2430}
circle.dot{fill-opacity:.62;stroke-width:1;cursor:pointer}
circle.dot:hover,circle.dot:focus{fill-opacity:.95;outline:none;stroke-width:2}
circle.dot.sel{fill-opacity:1;stroke:var(--sel);stroke-width:3}
circle.ring{fill:none;stroke:var(--sel);stroke-width:3;pointer-events:none}
.sellab{font-size:12px;font-weight:bold;fill:#111;paint-order:stroke;stroke:#fff;stroke-width:4px;stroke-linejoin:round;pointer-events:none}
tbody tr{cursor:pointer}
circle.US{fill:var(--us);stroke:#8e2a20}
circle.EU{fill:var(--eu);stroke:#164677}
circle.Other{fill:var(--oth);stroke:#566070}
.sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:middle}
.sw.US{background:var(--us)}.sw.EU{background:var(--eu)}.sw.Other{background:var(--oth)}
.tip{position:absolute;pointer-events:none;background:#1b2430;color:#fff;padding:6px 8px;border-radius:4px;font-size:12px;max-width:260px;z-index:5}
.tip div+div{margin-top:2px}
.muted{color:var(--mut);font-size:12px}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--card)}
th,td{border-bottom:1px solid var(--line);padding:4px 7px;text-align:left;vertical-align:top}
th{cursor:pointer;background:#eef0f3;position:sticky;top:0;white-space:nowrap;font-size:12px}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.note{min-width:300px;max-width:480px}
td.note p{margin:0 0 6px}
td.note p.take{border-left:3px solid #6b7a90;padding-left:6px}
td.note p.cons,td.note ul{font-size:12px;color:var(--mut)}
td.note ul{margin:0;padding-left:16px}
td.note a{color:#1f5fa8}
tr.hl td{background:#fff8dc;box-shadow:inset 0 2px 0 var(--sel),inset 0 -2px 0 var(--sel)}
tr.hl td:first-child{box-shadow:inset 2px 0 0 var(--sel),inset 0 2px 0 var(--sel),inset 0 -2px 0 var(--sel)}
tr.hl td:last-child{box-shadow:inset -2px 0 0 var(--sel),inset 0 2px 0 var(--sel),inset 0 -2px 0 var(--sel)}
tr.hl td:first-child:last-child{box-shadow:inset 0 0 0 2px var(--sel)}
footer{margin-top:22px;color:var(--mut);font-size:12px}
footer p{margin:6px 0}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.tabs button{font:inherit;padding:5px 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);cursor:pointer}
.tabs button.on{background:#1b2430;color:#fff;border-color:#1b2430}
tr.multi td:first-child{box-shadow:inset 4px 0 0 #e0a800}
tr.multi.hl td:first-child{box-shadow:inset 2px 0 0 var(--sel),inset 0 2px 0 var(--sel),inset 0 -2px 0 var(--sel)}
</style></head>
<body><div class="wrap">
<h1>__TITLE__</h1>
__TABS__
<div class="banner" id="banner">__BANNER__</div>
<div id="warnbox">__STALE__</div>
<div class="card filters" id="filters"></div>
<div class="card">
  <div class="chart-top">
    <span><span class="sw US"></span>US</span><span><span class="sw EU"></span>EU</span>
    <span class="muted">Dot size: market cap (EUR). Click a dot to mark it on both charts and in the table; click it again to clear.</span>
  </div>
  <div class="charts" id="charts">
    <div class="chartbox" id="box-q">
      <h3>Value vs Quality</h3>
      <svg class="chart" id="chart-q" viewBox="0 0 720 480" role="img" aria-label="Value against Quality"></svg>
      <div class="tip" id="tip-q" hidden></div>
    </div>
    <div class="chartbox" id="box-t">
      <h3>Value vs Timing</h3>
      <svg class="chart" id="chart-t" viewBox="0 0 720 480" role="img" aria-label="Value against Timing"></svg>
      <div class="tip" id="tip-t" hidden></div>
    </div>
  </div>
  <div class="muted"><span id="notplotted"></span><a id="goto" hidden>Go to row</a></div>
</div>
<h2 id="h-scored">Scored</h2>
<label><input type="checkbox" id="allcols"> Show all columns</label>
<div class="tw"><table id="tbl"></table></div>
<div id="nswrap"><h2 id="h-ns"></h2><div class="tw"><table id="nstbl"></table></div></div>
<footer id="foot">__FOOTER__</footer>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>__JS__</script>
</body></html>
"""

_JS = r"""
(function(){
"use strict";
var DATA = JSON.parse(document.getElementById("data").textContent);
var HUB = !!DATA.views;
var VIEWS = HUB ? DATA.views : [{id:"list", records:DATA.rows}];
var ROWS = VIEWS[0].records;
var NS = document.getElementById("chart-q").namespaceURI;  // SVG namespace, read from the page
function el(tag, text, cls){var e=document.createElement(tag); if(text!=null) e.textContent=text; if(cls) e.className=cls; return e;}
function sv(tag, attrs, text){var e=document.createElementNS(NS, tag); for(var k in attrs){e.setAttribute(k, String(attrs[k]));} if(text!=null) e.textContent=text; return e;}
function clear(n){while(n.firstChild){n.removeChild(n.firstChild);}}
function isNum(v){return typeof v==="number" && isFinite(v);}

var METRICS=[{k:"score",l:"Composite score"},{k:"v",l:"Value"},{k:"q",l:"Quality"},{k:"t",l:"Timing"},{k:"r",l:"Risk"},{k:"ret1y",l:"1Y return (%)"}];
var TIERS=["large","mid","small"];
var FLAGS=["oversold","pullback","breakout","downtrend"];
// Two fixed charts, Value on X for both. Filters keep what is switched OFF (tier, country) or ON
// (flags), so they survive a tab switch and the tab counts compare like with like.
var CHARTS=[{svg:"chart-q",box:"box-q",tip:"tip-q",x:"v",y:"q",l:"Value vs Quality"},
            {svg:"chart-t",box:"box-t",tip:"tip-t",x:"v",y:"t",l:"Value vs Timing"}];
var state={sel:null,view:VIEWS[0].id,off:{tier:{},country:{}},flags:{},hideUnscored:false,allcols:false,
  sort:{tbl:{k:"score",dir:-1},nstbl:{k:"cap_m",dir:-1}}};

function tierOf(r){return r.tier||"n/a";}
function fmtNum(v,nd){return isNum(v)? v.toFixed(nd): "n/a";}
function fmtPct(v,nd){return isNum(v)? (v>0?"+":"")+v.toFixed(nd)+"%": "n/a";}
function vsPeer(v){return isNum(v)? " ("+(v>0?"+":"")+v.toFixed(0)+"% vs peers)": "";}
function peerTip(r,med){return r.peer_group? "Peer median "+(isNum(med)? med.toFixed(1)+"x": "n/a (fewer than 3 values)")+", "+r.peer_group+", n="+r.peer_n+". Negative = cheaper than peers.": "No peer group (fewer than 3 stocks in its industry or sector here).";}
function txt(v){return (v===null||v===undefined||v==="")? "n/a": String(v);}

function renderNote(r,td){
  td.appendChild(el("p", txt(r.note)));
  if(r.take){
    var p=el("p",null,"take"); p.appendChild(el("b","Claude's take: ")); p.appendChild(document.createTextNode(r.take));
    td.appendChild(p);
  } else if(r.take_status && r.take_status.indexOf("not scored")<0){
    td.appendChild(el("p","Take unavailable: "+r.take_status,"cons"));
  }
  if(r.consensus){td.appendChild(el("p",r.consensus,"cons"));}
  if(r.heads && r.heads.length){
    var ul=el("ul");
    r.heads.forEach(function(h){
      var li=el("li"); var label=(h.d? h.d+": ": "")+txt(h.t);
      if(h.u){  // links are scheme-checked in build_records
        var a=el("a",label); a.setAttribute("href",h.u); a.setAttribute("target","_blank"); a.setAttribute("rel","noopener noreferrer");
        li.appendChild(a);
      } else { li.appendChild(document.createTextNode(label)); }
      ul.appendChild(li);
    });
    td.appendChild(ul);
  }
}

var SCORED_COLS=[
 {k:"ticker",l:"Ticker",core:1,f:function(r){return txt(r.ticker);}},
 {k:"company",l:"Name",core:1,f:function(r){return txt(r.company);}},
 {k:"country",l:"Country",core:1,f:function(r){return txt(r.country&&r.country.toUpperCase());}},
 {k:"price",l:"Price",core:1,num:1,f:function(r){return isNum(r.price)? r.price.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})+(r.ccy?" "+r.ccy:""): "n/a";}},
 {k:"n_lists",l:"Lists",core:1,hub:1,desc:1,f:function(r){return listsText(r);}},
 {k:"tier",l:"Tier",core:1,f:function(r){return tierOf(r);}},
 {k:"score",l:"Score",core:1,num:1,heat:1,f:function(r){return fmtNum(r.score,0);}},
 {k:"rank_tier",l:"Rank (tier)",num:1,f:function(r){return fmtNum(r.rank_tier,0);}},
 {k:"v",l:"V",core:1,num:1,heat:1,f:function(r){return fmtNum(r.v,0);}},
 {k:"q",l:"Q",core:1,num:1,heat:1,f:function(r){return fmtNum(r.q,0);}},
 {k:"t",l:"T",core:1,num:1,heat:1,f:function(r){return fmtNum(r.t,0);}},
 {k:"r",l:"R",core:1,num:1,heat:1,f:function(r){return fmtNum(r.r,0);}},
 {k:"gate",l:"Gate",core:1,f:function(r){var a=[]; if(r.gate){a.push(r.gate);} if(r.down){a.push(r.down);} return a.length? a.join("; "): "none";}},
 {k:"pe",l:"Fwd P/E",core:1,num:1,f:function(r){return isNum(r.pe)? r.pe.toFixed(1)+(r.pe_kind==="trailing"?" (trailing)":"")+vsPeer(r.pe_vs): "n/a";},tip:function(r){return peerTip(r,r.pe_med);}},
 {k:"ps",l:"P/S",core:1,num:1,f:function(r){return isNum(r.ps)? r.ps.toFixed(1)+vsPeer(r.ps_vs): "n/a";},tip:function(r){return peerTip(r,r.ps_med);}},
 {k:"evs",l:"EV/Sales",num:1,f:function(r){return isNum(r.evs)? r.evs.toFixed(1)+vsPeer(r.evs_vs): "n/a";},tip:function(r){return peerTip(r,r.evs_med);}},
 {k:"evf",l:"EV/FCF",core:1,num:1,f:function(r){return isNum(r.evf)? r.evf.toFixed(1)+vsPeer(r.evf_vs): "n/a";},tip:function(r){return peerTip(r,r.evf_med);}},
 {k:"peer_group",l:"Peers",f:function(r){return r.peer_group? r.peer_group+" (n="+r.peer_n+")": "none";}},
 {k:"rsi",l:"RSI",core:1,num:1,f:function(r){return fmtNum(r.rsi,0);}},
 {k:"ma200",l:"vs 200d",core:1,num:1,f:function(r){return fmtPct(r.ma200,1);}},
 {k:"adv",l:"ADV EUR m",num:1,f:function(r){return fmtNum(r.adv,1);}},
 {k:"earn",l:"Next earnings",f:function(r){return txt(r.earn);}},
 {k:"flags",l:"Flags",core:1,f:function(r){return r.flags.length? r.flags.join(", "): "none";}},
 {k:"cov",l:"Coverage",num:1,f:function(r){return isNum(r.cov)? r.cov.toFixed(0)+"%": "n/a";}},
 {k:"data_date",l:"Data date",hub:1,f:function(r){return txt(r.data_date)+(r.data_from_other_list?" (other list)":"");}},
 {k:"note",l:"Note",core:1,cls:"note",f:function(r){return txt(r.note);},render:renderNote}
];
var NS_COLS=[
 {k:"ticker",l:"Ticker",f:function(r){return txt(r.ticker);}},
 {k:"company",l:"Name",f:function(r){return txt(r.company);}},
 {k:"country",l:"Country",f:function(r){return txt(r.country&&r.country.toUpperCase());}},
 {k:"n_lists",l:"Lists",hub:1,desc:1,f:function(r){return listsText(r);}},
 {k:"tier",l:"Tier",f:function(r){return tierOf(r);}},
 {k:"cap_m",l:"Cap EUR m",num:1,f:function(r){return isNum(r.cap_m)? r.cap_m.toFixed(0): "n/a";}},
 {k:"v",l:"V",num:1,f:function(r){return fmtNum(r.v,0);}},
 {k:"q",l:"Q",num:1,f:function(r){return fmtNum(r.q,0);}},
 {k:"t",l:"T",num:1,f:function(r){return fmtNum(r.t,0);}},
 {k:"r",l:"R",num:1,f:function(r){return fmtNum(r.r,0);}},
 {k:"reason",l:"Reason",f:function(r){return txt(r.reason);}},
 {k:"data_note",l:"Data note",f:function(r){return txt(r.data_note);}},
 {k:"note",l:"Note",cls:"note",f:function(r){return txt(r.note);}}
];

function listsText(r){
  var L=r.lists||[];
  if(!L.length){return "";}
  return state.view==="all"? L.length+": "+L.join("; "): "also: "+L.join("; ");
}
function sortVal(col,r){
  if(col.k==="gate"){return (r.gate||"")+(r.down||"");}
  if(col.k==="flags"){return r.flags.join(",");}
  return r[col.k];
}
function sortRows(rows,col,dir){
  var out=rows.slice();
  out.sort(function(a,b){
    var x=sortVal(col,a), y=sortVal(col,b);
    var xn=(x===null||x===undefined||x===""), yn=(y===null||y===undefined||y==="");
    if(xn&&yn){return a.id-b.id;}
    if(xn){return 1;}
    if(yn){return -1;}
    var c=(typeof x==="number"&&typeof y==="number")? x-y: String(x).localeCompare(String(y));
    return c? c*dir: a.id-b.id;
  });
  return out;
}

function renderTable(tblId, cols, rows){
  var tbl=document.getElementById(tblId);
  clear(tbl);
  var vis=cols.filter(function(c){return (tblId!=="tbl"||state.allcols||c.core)&&(!c.hub||HUB);});
  var st=state.sort[tblId];
  var thead=el("thead"), hr=el("tr");
  vis.forEach(function(c){
    var th=el("th", c.l+(st.k===c.k?(st.dir>0?" \u25B2":" \u25BC"):""));
    th.addEventListener("click", function(){
      if(st.k===c.k){st.dir=-st.dir;}else{st.k=c.k; st.dir=(c.num||c.desc)?-1:1;}
      draw();
    });
    hr.appendChild(th);
  });
  thead.appendChild(hr); tbl.appendChild(thead);
  var scol=cols.filter(function(c){return c.k===st.k;})[0]||cols[0];
  var tb=el("tbody");
  sortRows(rows,scol,st.dir).forEach(function(r){
    var tr=el("tr"); tr.id="row-"+r.id;
    var cn=[]; if(r.n_lists>=2){cn.push("multi");} if(state.sel===r.id){cn.push("hl");}
    tr.className=cn.join(" ");
    tr.addEventListener("click",function(e){if(e&&e.target&&e.target.tagName==="A"){return;} selectRow(r.id);});
    vis.forEach(function(c){
      var td=c.render? el("td", null, c.cls||""): el("td", c.f(r), (c.num?"num ":"")+(c.cls||""));
      if(c.render){c.render(r,td);}
      if(c.tip){var tt=c.tip(r); if(tt){td.title=tt;}}
      if(c.heat && isNum(r[c.k])){td.style.backgroundColor="rgba(55,71,99,"+(0.06+0.42*r[c.k]/100).toFixed(3)+")";}
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  tbl.appendChild(tb);
}

// skip = the filter group whose own options are being counted (facet counts ignore their own group)
function passes(r,skip){
  if(skip!=="country" && state.off.country[r.country||"n/a"]){return false;}
  if(skip!=="tier" && state.off.tier[tierOf(r)]){return false;}
  if(skip!=="flag"){
    var anyFlag=FLAGS.some(function(f){return state.flags[f];});
    if(anyFlag && !r.flags.some(function(f){return state.flags[f];})){return false;}
  }
  if(state.hideUnscored && r.score===null){return false;}
  return true;
}
function visibleRows(){return ROWS.filter(function(r){return passes(r,null);});}

function showTip(r,evt,cfg){
  var tip=document.getElementById(cfg.tip), box=document.getElementById(cfg.box);
  clear(tip);
  tip.appendChild(el("div", r.ticker+(r.company?" - "+r.company:"")));
  tip.appendChild(el("div", "Tier "+tierOf(r)+" | Score "+fmtNum(r.score,0)+" | "+(r.region)));
  tip.appendChild(el("div", "V "+fmtNum(r.v,0)+"  Q "+fmtNum(r.q,0)+"  T "+fmtNum(r.t,0)+"  R "+fmtNum(r.r,0)));
  tip.appendChild(el("div", "Flags: "+(r.flags.length? r.flags.join(", "): "none")));
  tip.hidden=false;
  var b=box.getBoundingClientRect();
  var x=evt.clientX-b.left+12, y=evt.clientY-b.top+12;
  if(x>b.width-250){x=evt.clientX-b.left-250;}
  tip.style.left=Math.max(0,x)+"px"; tip.style.top=y+"px";
}
function hideTip(cfg){document.getElementById(cfg.tip).hidden=true;}
function toggleSel(id){state.sel=(state.sel===id)? null: id; draw();}
function selectRow(id){
  // Table click: mark (or clear) it, and scroll up if both charts are out of view.
  toggleSel(id);
  if(state.sel!==null){
    var c=document.getElementById("charts"), b=c.getBoundingClientRect();
    if(b.top<0||b.bottom>window.innerHeight){c.scrollIntoView({block:"center",behavior:"smooth"});}
  }
}
function lab(k){return METRICS.filter(function(m){return m.k===k;})[0].l;}

function drawChart(cfg, rows){
  var svg=document.getElementById(cfg.svg);
  clear(svg);
  var W=720,H=480,ML=54,MR=16,MT=14,MB=46,PW=W-ML-MR,PH=H-MT-MB;
  var xk=cfg.x, yk=cfg.y;
  var plot=rows.filter(function(r){return isNum(r[xk])&&isNum(r[yk]);});
  function sx(v){return ML+v/100*PW;}
  function sy(v){return MT+PH-v/100*PH;}
  [0,25,50,75,100].forEach(function(t){
    svg.appendChild(sv("line",{x1:sx(t),y1:MT,x2:sx(t),y2:MT+PH,"class":"grid"}));
    svg.appendChild(sv("text",{x:sx(t),y:MT+PH+14,"text-anchor":"middle","class":"tick"},String(t)));
    svg.appendChild(sv("line",{x1:ML,y1:sy(t),x2:ML+PW,y2:sy(t),"class":"grid"}));
    svg.appendChild(sv("text",{x:ML-6,y:sy(t)+3,"text-anchor":"end","class":"tick"},String(t)));
  });
  svg.appendChild(sv("line",{x1:ML,y1:MT+PH,x2:ML+PW,y2:MT+PH,"class":"axis"}));
  svg.appendChild(sv("line",{x1:ML,y1:MT,x2:ML,y2:MT+PH,"class":"axis"}));
  svg.appendChild(sv("line",{x1:sx(50),y1:MT,x2:sx(50),y2:MT+PH,"class":"quad"}));
  svg.appendChild(sv("line",{x1:ML,y1:sy(50),x2:ML+PW,y2:sy(50),"class":"quad"}));
  svg.appendChild(sv("text",{x:ML+PW/2,y:H-8,"text-anchor":"middle","class":"alab"},lab(xk)));
  svg.appendChild(sv("text",{x:14,y:MT+PH/2,"text-anchor":"middle","class":"alab",transform:"rotate(-90 14 "+(MT+PH/2)+")"},lab(yk)));
  var selDot=null;
  plot.slice().sort(function(a,b){return (b.cap_m||0)-(a.cap_m||0);}).forEach(function(r){
    var rad=4;
    if(isNum(r.cap_m)){rad=Math.min(14,Math.max(4,4+2.5*Math.log10(Math.max(r.cap_m,100)/100)));}
    var isSel=(state.sel===r.id);
    var c=sv("circle",{cx:sx(r[xk]),cy:sy(r[yk]),r:rad,"class":"dot "+r.region+(isSel?" sel":""),tabindex:0,"data-id":r.id});
    c.addEventListener("mousemove",function(e){showTip(r,e,cfg);});
    c.addEventListener("mouseleave",function(){hideTip(cfg);});
    c.addEventListener("click",function(){hideTip(cfg); toggleSel(r.id);});
    c.addEventListener("keydown",function(e){if(e.key==="Enter"){toggleSel(r.id);}});
    if(isSel){selDot={x:sx(r[xk]),y:sy(r[yk]),r:rad,t:r.ticker,c:c}; return;}  // appended last, on top
    svg.appendChild(c);
  });
  if(selDot){
    svg.appendChild(selDot.c);
    svg.appendChild(sv("circle",{cx:selDot.x,cy:selDot.y,r:selDot.r+6,"class":"ring"}));
    svg.appendChild(sv("text",{x:selDot.x+selDot.r+9,y:selDot.y-selDot.r-4,"class":"sellab"},selDot.t));
  }
  return {missing:rows.length-plot.length, shown:!!selDot};
}

// ---- filters: one drop-down per family, counts in brackets follow the other filters
var DD={};
var GROUPS=[{k:"tier",t:"Tier",mode:"off"},{k:"country",t:"Country",mode:"off"},{k:"flag",t:"Flags (any)",mode:"on"}];
function isChecked(g,it){return g.mode==="off"? !state.off[g.k][it]: state.flags[it]===true;}
function setChecked(g,it,on){
  if(g.mode==="off"){ if(on){delete state.off[g.k][it];} else {state.off[g.k][it]=true;} }
  else { state.flags[it]=on; }
}
function closePanels(except){for(var k in DD){if(DD[k].panel!==except){DD[k].panel.hidden=true;}}}
function stop(e){if(e&&e.stopPropagation){e.stopPropagation();}}
function dropdown(parent,g,items,labelFn){
  var wrap=el("div",null,"dd"); wrap.setAttribute("data-k",g.k);
  var btn=el("button",g.t,"ddbtn"); btn.setAttribute("type","button");
  var panel=el("div",null,"ddpanel"); panel.hidden=true;
  var allbar=el("div",null,"ddall"), aAll=el("a","All"), aNone=el("a","None");
  aAll.addEventListener("click",function(e){stop(e); items.forEach(function(it){setChecked(g,it,true);}); draw();});
  aNone.addEventListener("click",function(e){stop(e); items.forEach(function(it){setChecked(g,it,false);}); draw();});
  allbar.appendChild(aAll); allbar.appendChild(aNone); panel.appendChild(allbar);
  var boxes={};
  items.forEach(function(it){
    var l=el("label"), cb=document.createElement("input"), n=el("span",null,"muted");
    cb.type="checkbox"; cb.checked=isChecked(g,it);
    cb.addEventListener("change",function(){setChecked(g,it,cb.checked); draw();});
    l.appendChild(cb); l.appendChild(document.createTextNode(" "+(labelFn?labelFn(it):it)+" ")); l.appendChild(n);
    panel.appendChild(l); boxes[it]={cb:cb,n:n};
  });
  btn.addEventListener("click",function(e){stop(e); var open=panel.hidden; closePanels(panel); panel.hidden=!open;});
  panel.addEventListener("click",stop);
  wrap.appendChild(btn); wrap.appendChild(panel); parent.appendChild(wrap);
  DD[g.k]={g:g,btn:btn,panel:panel,boxes:boxes,items:items};
}
function updateFilters(){
  GROUPS.forEach(function(g){
    var d=DD[g.k]; if(!d){return;}
    var base=ROWS.filter(function(r){return passes(r,g.k);});
    var on=0;
    d.items.forEach(function(it){
      var n=base.filter(function(r){
        if(g.k==="tier"){return tierOf(r)===it;}
        if(g.k==="country"){return (r.country||"n/a")===it;}
        return r.flags.indexOf(it)>=0;
      }).length;
      d.boxes[it].n.textContent="("+n+")";
      d.boxes[it].cb.checked=isChecked(g,it);
      if(isChecked(g,it)){on++;}
    });
    var all=d.items.length, summary, active;
    if(g.mode==="off"){summary=(on===all)? "all": on+" of "+all; active=on!==all;}
    else {summary=on? on+" selected": "no filter"; active=on>0;}
    d.btn.textContent=g.t+": "+summary+" ▾";
    d.btn.className="ddbtn"+(active?" act":"");
  });
}
function buildFilters(){
  var f=document.getElementById("filters");
  clear(f); DD={};
  var countries=[], seen={};
  ROWS.forEach(function(r){var c=r.country||"n/a"; if(!seen[c]){seen[c]=1; countries.push(c);}});
  countries.sort();
  var tiers=TIERS.slice(); if(ROWS.some(function(r){return !r.tier;})){tiers.push("n/a");}
  var tierLbl={large:"Large (€10bn+)",mid:"Mid (€2-10bn)",small:"Small (<€2bn)"};
  dropdown(f,GROUPS[0],tiers,function(t){return tierLbl[t]||t;});
  dropdown(f,GROUPS[1],countries,function(c){return c.toUpperCase();});
  dropdown(f,GROUPS[2],FLAGS);
  var g=el("div",null,"fg"), l=el("label"), cb=document.createElement("input");
  cb.type="checkbox"; cb.checked=state.hideUnscored===true;
  cb.addEventListener("change",function(){state.hideUnscored=cb.checked; draw();});
  l.appendChild(cb); l.appendChild(document.createTextNode(" Hide not scored")); g.appendChild(l); f.appendChild(g);
}

var TABBTNS=[];
function draw(){
  var rows=visibleRows();
  var res=CHARTS.map(function(c){return drawChart(c,rows);});
  var msg="Showing "+rows.length+" of "+ROWS.length+" rows. Not plotted (missing scores): "+
    CHARTS.map(function(c,i){return c.l+" "+res[i].missing;}).join(", ")+".";
  var gotoEl=document.getElementById("goto"); gotoEl.hidden=true;
  if(state.sel!==null){
    var sr=ROWS.filter(function(r){return r.id===state.sel;})[0];
    var onChart=res.some(function(x){return x.shown;});
    msg+=" Selected "+(sr?sr.ticker:"")+(onChart? ".": " is hidden by a filter or has no scores.");
    gotoEl.hidden=!rows.some(function(r){return r.id===state.sel;});
  }
  document.getElementById("notplotted").textContent=msg;
  var sc=rows.filter(function(r){return r.score!==null;});
  var ns=rows.filter(function(r){return r.score===null;});
  document.getElementById("h-scored").textContent="Scored ("+sc.length+")";
  renderTable("tbl",SCORED_COLS,sc);
  document.getElementById("nswrap").hidden=ns.length===0;
  document.getElementById("h-ns").textContent="Not scored ("+ns.length+") - reasons";
  renderTable("nstbl",NS_COLS,ns);
  updateFilters();
  TABBTNS.forEach(function(b){
    var n=b.view.records.filter(function(r){return passes(r,null);}).length;
    b.btn.textContent=b.view.label+" ("+n+")";
  });
}

function viewById(id){return VIEWS.filter(function(v){return v.id===id;})[0]||null;}
function setView(id){
  var v=viewById(id)||VIEWS[0];
  state.view=v.id; ROWS=v.records; state.sel=null;
  buildFilters();
  if(HUB){
    document.getElementById("banner").textContent=v.banner;
    var wb=document.getElementById("warnbox"); clear(wb);
    if(v.warn){wb.appendChild(el("div",v.warn,"warn"));}
    var ft=document.getElementById("foot"); clear(ft);
    v.footer.forEach(function(p){var e=el("p"); e.appendChild(el("b",p[0]+". ")); e.appendChild(document.createTextNode(p[1])); ft.appendChild(e);});
    TABBTNS.forEach(function(b){var on=(b.vid===v.id); b.btn.className=on?"on":""; b.btn.setAttribute("aria-selected",on?"true":"false");});
    try{ if(typeof location!=="undefined" && location.hash!=="#"+v.id){location.hash="#"+v.id;} }catch(e){}
  }
  draw();
}
function init(){
  document.getElementById("allcols").addEventListener("change",function(e){state.allcols=e.target.checked; draw();});
  document.getElementById("goto").addEventListener("click",function(){
    var tr=document.getElementById("row-"+state.sel);
    if(tr){tr.scrollIntoView({block:"center",behavior:"smooth"});}
  });
  try{ if(typeof document.addEventListener==="function"){document.addEventListener("click",function(){closePanels(null);});} }catch(e){}
  if(HUB && VIEWS.length>1){
    var bar=document.getElementById("tabs");
    VIEWS.forEach(function(v){
      var b=el("button", v.label+" ("+v.records.length+")"); b.setAttribute("role","tab"); b.setAttribute("type","button");
      b.addEventListener("click",function(){setView(v.id);});
      bar.appendChild(b); TABBTNS.push({vid:v.id,btn:b,view:v});
    });
  }
  var start=VIEWS[0].id;
  try{ if(HUB && typeof location!=="undefined" && location.hash && viewById(location.hash.slice(1))){start=location.hash.slice(1);} }catch(e){}
  try{ if(HUB && typeof window.addEventListener==="function"){
    window.addEventListener("hashchange",function(){
      var h=(typeof location!=="undefined")? location.hash.slice(1): "";
      if(h && h!==state.view && viewById(h)){setView(h);}
    });
  } }catch(e){}
  setView(start);
}
init();
})();
"""
