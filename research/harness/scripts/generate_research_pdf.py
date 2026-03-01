"""
generate_research_pdf.py (research repository)

Create a structured, non-overlapping PDF report for a research run produced by
`research/harness/scripts/run_research_harness.py`.

Inputs (per run_id):
  PRIVATE/TRADING/research/harness/runs/<run_id>/
    meta.json
    <TF>/
      summary_<TF>.csv
      kpi_rankings_<TF>.csv
      combo_rankings_<TF>.csv
      REPORT.md

Output:
  PRIVATE/TRADING/research/harness/runs/<run_id>/RESEARCH_REPORT_<run_id>.pdf
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_DIR = SCRIPT_DIR.parent
REPO_DIR = HARNESS_DIR.parent.parent
RUNS_DIR = REPO_DIR / "data" / "research_runs" / "harness"


def _read_json(path: Path) -> dict:
    try:
        if path.exists() and path.stat().st_size > 0:
            obj = json.loads(path.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}
    return {}


def _latest_run_dir() -> Path | None:
    if not RUNS_DIR.exists():
        return None
    dirs = [p for p in RUNS_DIR.iterdir() if p.is_dir() and (p / "meta.json").exists()]
    if not dirs:
        return None
    try:
        return sorted(dirs, key=lambda p: p.name)[-1]
    except Exception:
        return sorted(dirs, key=lambda p: p.stat().st_mtime)[-1]


def _num(s) -> float:
    try:
        if s is None:
            return float("nan")
        return float(s)
    except Exception:
        return float("nan")


def _pct(x: float) -> str:
    if x is None or not math.isfinite(float(x)):
        return ""
    return f"{float(x)*100.0:.1f}%"


def _fmt(x: float, nd: int = 3) -> str:
    if x is None or not math.isfinite(float(x)):
        return ""
    return f"{float(x):.{nd}f}"


def _ensure_cols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    return df


def _short_label(s: str, *, max_len: int = 28) -> str:
    s0 = str(s or "").strip()
    if not s0:
        return ""
    rep = {
        "RSI Strength & Consolidation Zones (Zeiierman) (breakout)": "RSI Zones (breakout)",
        "RSI Strength & Consolidation Zones (Zeiierman)": "RSI Zones",
        "Nadaraya-Watson Smoother": "NW Smoother",
        "Nadaraya-Watson Envelop": "NW Envelope",  # legacy
        "Nadaraya-Watson Envelop (MAE)": "NWE MAE",
        "Nadaraya-Watson Envelop (STD)": "NWE STD",
        "Volume + MA20": "Vol + MA20",
        "Donchian Ribbon": "Donchian",
        "Madrid Ribbon": "Madrid",
        "CM_Ult_MacD_MFT": "Ult MACD",
        "CM_P-SAR": "P-SAR",
        "ADX & DI": "ADX/DI",
    }
    if s0 in rep:
        s0 = rep[s0]
    s0 = s0.replace("&", "+").replace("  ", " ").strip()
    if len(s0) > max_len:
        return s0[: max(0, max_len - 1)].rstrip() + "…"
    return s0


def _short_combo_label(s: str, *, max_len: int = 44) -> str:
    raw = str(s or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in raw.replace("&", "+").split("+") if p.strip()]
    if len(parts) <= 1:
        return _short_label(raw, max_len=max_len)
    short_parts = [_short_label(p, max_len=18) for p in parts]
    out = " + ".join(short_parts)
    if len(out) > max_len:
        return out[: max(0, max_len - 1)].rstrip() + "…"
    return out


def _load_tf(run_dir: Path, tf: str) -> dict[str, pd.DataFrame | str]:
    tf_dir = run_dir / tf
    out: dict[str, pd.DataFrame | str] = {}
    out["summary"] = pd.read_csv(tf_dir / f"summary_{tf}.csv") if (tf_dir / f"summary_{tf}.csv").exists() else pd.DataFrame()
    out["kpis"] = pd.read_csv(tf_dir / f"kpi_rankings_{tf}.csv") if (tf_dir / f"kpi_rankings_{tf}.csv").exists() else pd.DataFrame()
    out["combos"] = pd.read_csv(tf_dir / f"combo_rankings_{tf}.csv") if (tf_dir / f"combo_rankings_{tf}.csv").exists() else pd.DataFrame()
    out["report_md"] = (tf_dir / "REPORT.md").read_text(encoding="utf-8") if (tf_dir / "REPORT.md").exists() else ""
    return out


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--run_id", type=str, default="", help="Run id under research/harness/runs/. If omitted uses latest.")
    args = ap.parse_args()

    if str(args.run_id or "").strip():
        run_dir = RUNS_DIR / str(args.run_id).strip()
    else:
        run_dir = _latest_run_dir()
        if run_dir is None:
            raise SystemExit("No research runs found under research/harness/runs/")

    meta = _read_json(run_dir / "meta.json")
    timeframes = meta.get("timeframes") if isinstance(meta.get("timeframes"), list) else []
    timeframes = [str(x).strip().upper() for x in timeframes if str(x).strip()]
    if not timeframes:
        # Fallback: infer from subfolders
        timeframes = [p.name for p in run_dir.iterdir() if p.is_dir() and (p / "REPORT.md").exists()]

    # Lazy import reportlab (keeps base dependencies light).
    from reportlab.lib import colors  # type: ignore
    from reportlab.lib.pagesizes import letter  # type: ignore
    from reportlab.lib.styles import getSampleStyleSheet  # type: ignore
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # type: ignore

    styles = getSampleStyleSheet()

    out_pdf = run_dir / f"RESEARCH_REPORT_{run_dir.name}.pdf"
    doc = SimpleDocTemplate(str(out_pdf), pagesize=letter)
    story = []

    story.append(Paragraph(f"Research report — {run_dir.name}", styles["Title"]))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Meta", styles["Heading2"]))
    story.append(Paragraph(f"Stock data dir: {meta.get('stock_data_dir','')}", styles["BodyText"]))
    story.append(Paragraph(f"Config: {meta.get('config_path','')}", styles["BodyText"]))
    story.append(Spacer(1, 12))

    for tf in timeframes:
        tf_data = _load_tf(run_dir, tf)
        kpis = tf_data["kpis"] if isinstance(tf_data["kpis"], pd.DataFrame) else pd.DataFrame()

        story.append(Paragraph(f"Timeframe: {tf}", styles["Heading1"]))
        story.append(Spacer(1, 8))

        if kpis is None or kpis.empty:
            story.append(Paragraph("No KPI rankings available.", styles["BodyText"]))
            story.append(Spacer(1, 12))
            continue

        # Simple top table by lift at h=4 (if present)
        d = kpis.copy()
        d = _ensure_cols(d, ["direction", "horizon_bars", "lift", "n", "win_rate", "mae_p95"])
        d["horizon_bars"] = pd.to_numeric(d["horizon_bars"], errors="coerce")
        d["lift"] = pd.to_numeric(d["lift"], errors="coerce")
        d["n"] = pd.to_numeric(d["n"], errors="coerce")
        d["win_rate"] = pd.to_numeric(d["win_rate"], errors="coerce")
        d["mae_p95"] = pd.to_numeric(d["mae_p95"], errors="coerce")

        h0 = 4
        tb = d.loc[(d["direction"] == "bullish") & (d["horizon_bars"] == float(h0))].sort_values(["lift", "n"], ascending=[False, False]).head(12)
        ts = d.loc[(d["direction"] == "bearish") & (d["horizon_bars"] == float(h0))].sort_values(["lift", "n"], ascending=[False, False]).head(12)

        def _table_for(df0: pd.DataFrame, title: str) -> None:
            story.append(Paragraph(title, styles["Heading2"]))
            if df0.empty:
                story.append(Paragraph("No rows.", styles["BodyText"]))
                story.append(Spacer(1, 8))
                return
            rows = [["Name", "Lift", "Win rate", "n", "MAE p95"]]
            for _, r in df0.iterrows():
                rows.append(
                    [
                        _short_label(r.get("name", ""), max_len=34),
                        _fmt(_num(r.get("lift")), 3),
                        _pct(_num(r.get("win_rate"))),
                        str(int(float(r.get("n", 0) or 0))),
                        _fmt(_num(r.get("mae_p95")), 3),
                    ]
                )
            t = Table(rows, hAlign="LEFT")
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]
                )
            )
            story.append(t)
            story.append(Spacer(1, 10))

        _table_for(tb, f"Top KPIs (bullish, h={h0})")
        _table_for(ts, f"Top KPIs (bearish, h={h0})")

        # Embed any PNGs present under plots/
        plots_dir = run_dir / tf / "plots"
        if plots_dir.exists():
            for p in sorted(plots_dir.glob("*.png")):
                story.append(Paragraph(p.name, styles["Heading3"]))
                story.append(Image(str(p), width=520, height=260))
                story.append(Spacer(1, 10))

    doc.build(story)
    print(f"[OK] Wrote: {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

