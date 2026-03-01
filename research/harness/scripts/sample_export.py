"""
sample_export.py

Single canonical entrypoint for the sample-100 dataset lifecycle:
- create: build a reproducible random sample from a fixed index roster and export enriched data
- reexport: recompute enriched data for the existing sample without re-sampling

All outputs live under (default):
  data/feature_store/enriched/sample_100/
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import yfinance as yf


SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_DIR = SCRIPT_DIR.parent
RESEARCH_DIR = HARNESS_DIR.parent
REPO_DIR = RESEARCH_DIR.parent  # .../PRIVATE/trading_dashboard

if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from apps.dashboard.build_dashboard import END_DATE, START_DATE, _download_daily_ohlcv  # noqa: E402


DEFAULT_OUT_DIR = REPO_DIR / "data" / "feature_store" / "enriched" / "sample_100"
DEFAULT_CONFIG_JSON = REPO_DIR / "apps" / "dashboard" / "configs" / "config.json"


@dataclass(frozen=True)
class IndexSpec:
    name: str
    code: str
    wiki_url: str
    ticker_columns: Tuple[str, ...]
    suffix: str
    kind: str = "symbol"  # "symbol" or "numeric_code"


def _read_indices_from_pdf_text() -> List[Tuple[str, str]]:
    # Hardcoded roster; avoids PDF parsing dependencies.
    return [
        ("S&P 500", "SPX"),
        ("NASDAQ Composite", "IXIC"),
        ("Dow Jones Industrial Average", "DJI"),
        ("Nikkei 225", "N225"),
        ("TOPIX", "TPX"),
        ("FTSE 100", "FTSE"),
        ("DAX 40", "DAX"),
        ("CAC 40", "PX1"),
        ("Shanghai Composite", "SSEC"),
        ("CSI 300", "CSI300"),
        ("Hang Seng Index", "HSI"),
        ("BSE Sensex", "SENSEX"),
        ("Nifty 50", "NIFTY"),
        ("S&P/TSX Composite", "TSX"),
        ("KOSPI", "KOSPI"),
        ("ASX 200", "AS51"),
        ("IBEX 35", "IBEX"),
        ("FTSE MIB", "FTSEMIB"),
        ("AEX", "AEX"),
        ("SMI", "SMI"),
        ("OMXS30", "OMXS30"),
        ("BEL 20", "BEL20"),
        ("ATX", "ATX"),
        ("PSI 20", "PSI20"),
        ("OBX", "OBX"),
        ("OMX Helsinki 25", "OMXH25"),
        ("WIG20", "WIG20"),
        ("Bovespa", "IBOV"),
        ("IPC", "MXX"),
        ("TA-35", "TA35"),
    ]


def _default_index_sources() -> Dict[str, IndexSpec]:
    return {
        "SPX": IndexSpec("S&P 500", "SPX", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol", "Ticker"), "", "symbol"),
        "DJI": IndexSpec("Dow Jones Industrial Average", "DJI", "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", ("Symbol", "Company", "Ticker"), "", "symbol"),
        "IXIC": IndexSpec("NASDAQ Composite (proxy: NASDAQ-100)", "IXIC", "https://en.wikipedia.org/wiki/Nasdaq-100", ("Ticker", "Company", "Symbol"), "", "symbol"),
        "N225": IndexSpec("Nikkei 225", "N225", "https://en.wikipedia.org/wiki/Nikkei_225", ("Code", "Ticker", "Symbol"), ".T", "symbol"),
        "TPX": IndexSpec("TOPIX (proxy: TOPIX Core30)", "TPX", "https://en.wikipedia.org/wiki/TOPIX_Core30", ("Code", "Ticker", "Symbol"), ".T", "symbol"),
        "FTSE": IndexSpec("FTSE 100", "FTSE", "https://en.wikipedia.org/wiki/FTSE_100_Index", ("EPIC", "Ticker", "Symbol"), ".L", "symbol"),
        "DAX": IndexSpec("DAX 40", "DAX", "https://en.wikipedia.org/wiki/DAX", ("Ticker", "Symbol", "Code"), ".DE", "symbol"),
        "PX1": IndexSpec("CAC 40", "PX1", "https://en.wikipedia.org/wiki/CAC_40", ("Ticker", "Symbol", "Mnemonic"), ".PA", "symbol"),
        "SSEC": IndexSpec("Shanghai Composite (proxy: SSE 50)", "SSEC", "https://en.wikipedia.org/wiki/SSE_50_Index", ("Symbol", "Ticker", "Code"), ".SS", "symbol"),
        "CSI300": IndexSpec("CSI 300", "CSI300", "https://en.wikipedia.org/wiki/CSI_300_Index", ("Ticker", "Symbol", "Code"), "", "symbol"),
        "HSI": IndexSpec("Hang Seng Index", "HSI", "https://en.wikipedia.org/wiki/Hang_Seng_Index", ("Code", "Stock code", "Ticker"), ".HK", "numeric_code"),
        "SENSEX": IndexSpec("BSE Sensex", "SENSEX", "https://en.wikipedia.org/wiki/BSE_SENSEX", ("Symbol", "Ticker", "Scrip"), ".BO", "symbol"),
        "NIFTY": IndexSpec("Nifty 50", "NIFTY", "https://en.wikipedia.org/wiki/NIFTY_50", ("Symbol", "Ticker"), ".NS", "symbol"),
        "TSX": IndexSpec("S&P/TSX Composite", "TSX", "https://en.wikipedia.org/wiki/S%26P/TSX_Composite_Index", ("Ticker", "Symbol"), ".TO", "symbol"),
        "KOSPI": IndexSpec("KOSPI", "KOSPI", "https://en.wikipedia.org/wiki/KOSPI", ("Symbol", "Ticker", "Code"), ".KS", "symbol"),
        "AS51": IndexSpec("ASX 200", "AS51", "https://en.wikipedia.org/wiki/S%26P/ASX_200", ("Ticker", "Symbol"), ".AX", "symbol"),
        "IBEX": IndexSpec("IBEX 35", "IBEX", "https://en.wikipedia.org/wiki/IBEX_35", ("Ticker", "Symbol"), ".MC", "symbol"),
        "FTSEMIB": IndexSpec("FTSE MIB", "FTSEMIB", "https://en.wikipedia.org/wiki/FTSE_MIB", ("Ticker", "Symbol"), ".MI", "symbol"),
        "AEX": IndexSpec("AEX", "AEX", "https://en.wikipedia.org/wiki/AEX_index", ("Ticker", "Symbol"), ".AS", "symbol"),
        "SMI": IndexSpec("SMI", "SMI", "https://en.wikipedia.org/wiki/Swiss_Market_Index", ("Symbol", "Ticker"), ".SW", "symbol"),
        "OMXS30": IndexSpec("OMXS30", "OMXS30", "https://en.wikipedia.org/wiki/OMXS30", ("Ticker", "Symbol"), ".ST", "symbol"),
        "BEL20": IndexSpec("BEL 20", "BEL20", "https://en.wikipedia.org/wiki/BEL_20", ("Ticker", "Symbol"), ".BR", "symbol"),
        "ATX": IndexSpec("ATX", "ATX", "https://en.wikipedia.org/wiki/Austrian_Traded_Index", ("Ticker", "Symbol"), ".VI", "symbol"),
        "PSI20": IndexSpec("PSI 20", "PSI20", "https://en.wikipedia.org/wiki/PSI-20", ("Ticker", "Symbol"), ".LS", "symbol"),
        "OBX": IndexSpec("OBX", "OBX", "https://en.wikipedia.org/wiki/OBX_Index", ("Ticker", "Symbol"), ".OL", "symbol"),
        "OMXH25": IndexSpec("OMX Helsinki 25", "OMXH25", "https://en.wikipedia.org/wiki/OMX_Helsinki_25", ("Ticker", "Symbol"), ".HE", "symbol"),
        "WIG20": IndexSpec("WIG20", "WIG20", "https://en.wikipedia.org/wiki/WIG20", ("Ticker", "Symbol"), ".WA", "symbol"),
        "IBOV": IndexSpec("Bovespa", "IBOV", "https://en.wikipedia.org/wiki/Índice_Bovespa", ("Ticker", "Symbol"), ".SA", "symbol"),
        "MXX": IndexSpec("IPC (Mexico)", "MXX", "https://en.wikipedia.org/wiki/Índice_de_Precios_y_Cotizaciones", ("Ticker", "Symbol"), ".MX", "symbol"),
        "TA35": IndexSpec("TA-35", "TA35", "https://en.wikipedia.org/wiki/TA-35_Index", ("Ticker", "Symbol"), ".TA", "symbol"),
    }


def _strip_md(s: str) -> str:
    s0 = str(s or "")
    out = ""
    i = 0
    while i < len(s0):
        if s0[i] == "[":
            j = s0.find("]", i + 1)
            k = s0.find("(", j + 1) if j != -1 else -1
            m = s0.find(")", k + 1) if k != -1 else -1
            if j != -1 and k != -1 and m != -1:
                out += s0[i + 1 : j]
                i = m + 1
                continue
        out += s0[i]
        i += 1
    while "[[" in out and "]]" in out:
        a = out.find("[[")
        b = out.find("]]", a + 2)
        if a != -1 and b != -1:
            out = out[:a] + out[b + 2 :]
        else:
            break
    return " ".join(out.replace("\u00a0", " ").split()).strip()


def _fetch_wiki_markdown(url: str) -> str:
    url0 = requests.utils.requote_uri(str(url))
    proxy = f"https://r.jina.ai/{url0}"
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            r = requests.get(proxy, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code >= 400:
                r.raise_for_status()
            txt = r.text
            break
        except Exception as e:
            last_err = e
            time.sleep(1.0 + 1.5 * attempt)
    else:
        raise last_err or RuntimeError("failed to fetch wiki markdown")

    marker = "Markdown Content:"
    if marker in txt:
        txt = txt.split(marker, 1)[1]
    return txt


def _parse_markdown_tables(md: str) -> List[pd.DataFrame]:
    lines = [ln.rstrip("\n") for ln in (md or "").splitlines()]
    tables: List[pd.DataFrame] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if i + 1 >= len(lines):
            break
        sep = lines[i + 1].strip()
        if not (sep.startswith("|") and "---" in sep):
            i += 1
            continue
        i += 2
        rows: List[List[str]] = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            if len(cells) < len(header):
                cells = cells + [""] * (len(header) - len(cells))
            if len(cells) > len(header):
                cells = cells[: len(header)]
            rows.append([_strip_md(x) for x in cells])
            i += 1
        if rows and header:
            try:
                tables.append(pd.DataFrame(rows, columns=[_strip_md(h) for h in header]))
            except Exception:
                pass
        continue
    return tables


def _fetch_tables(url: str) -> List[pd.DataFrame]:
    return _parse_markdown_tables(_fetch_wiki_markdown(url))


def _extract_tickers_from_tables(tables: List[pd.DataFrame], columns: Tuple[str, ...]) -> List[str]:
    cols_norm = {c.lower().strip(): c for c in columns}
    out: List[str] = []
    for t in tables:
        if t is None or t.empty:
            continue
        col_map = {str(c).strip().lower(): c for c in t.columns}
        pick = None
        for want_lower in cols_norm.keys():
            if want_lower in col_map:
                pick = col_map[want_lower]
                break
        if pick is None:
            for c in t.columns:
                cl = str(c).lower()
                if "ticker" in cl or "symbol" in cl or "epic" in cl or "code" == cl or "stock code" in cl:
                    pick = c
                    break
        if pick is None:
            continue
        vals = [str(x).strip() for x in t[pick].tolist()]
        vals = [v for v in vals if v and v.lower() not in {"nan", "none"}]
        out.extend(vals)
    dedup: List[str] = []
    seen = set()
    for v in out:
        if v in seen:
            continue
        seen.add(v)
        dedup.append(v)
    return dedup


def _normalize_yf_ticker(raw: str, spec: IndexSpec) -> Optional[str]:
    s = str(raw or "").strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    if "." in s and spec.suffix == "":
        s = s.replace(".", "-")
    if "." in s and spec.kind != "numeric_code" and len(s.split(".")[-1]) <= 3:
        return s
    if spec.kind == "numeric_code":
        digits = "".join([ch for ch in s if ch.isdigit()])
        if not digits:
            return None
        return f"{digits.zfill(4)}{spec.suffix}"
    s = s.split("[")[0].strip()
    s = s.split(" ")[0].strip()
    if spec.suffix and not s.endswith(spec.suffix):
        return f"{s}{spec.suffix}"
    return s


_VALID_YF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,24}$")


def _is_valid_yf_ticker(sym: str) -> bool:
    s = str(sym or "").strip()
    if not s:
        return False
    if ":" in s or "/" in s or " " in s:
        return False
    return bool(_VALID_YF_RE.match(s))


def _pick_even_sample(
    *,
    index_to_tickers: Dict[str, List[str]],
    target_n: int,
    seed: int,
    validate_with_yfinance: bool,
    requires_intraday: bool,
) -> List[Tuple[str, str]]:
    rng = random.Random(int(seed))
    idxs = sorted([c for c, v in index_to_tickers.items() if v])
    if not idxs:
        return []

    base = target_n // len(idxs)
    rem = target_n - base * len(idxs)
    allocations = {c: base for c in idxs}
    for c in rng.sample(idxs, k=min(rem, len(idxs))):
        allocations[c] += 1

    avail_cache: Dict[str, bool] = {}

    def _quick_has_data(sym: str) -> bool:
        if not bool(validate_with_yfinance):
            return True
        s = str(sym or "").strip()
        if s in avail_cache:
            return avail_cache[s]
        ok = False
        try:
            # Keep this validation intentionally light: we only need to know the symbol exists and has
            # some recent tradable history. The full export step will fetch the full date ranges.
            d1 = yf.download(
                tickers=s,
                period="6mo",
                interval="1d",
                auto_adjust=False,
                progress=False,
                group_by="column",
                threads=False,
            )
            if d1 is not None and (not d1.empty):
                if isinstance(d1.columns, pd.MultiIndex):
                    lvl_names = [n or "" for n in d1.columns.names]
                    if "Ticker" in lvl_names:
                        d1 = d1.xs(s, axis=1, level="Ticker", drop_level=True)
                    elif s in d1.columns.get_level_values(-1):
                        d1 = d1.xs(s, axis=1, level=-1, drop_level=True)
                if "Close" in d1.columns and int(d1["Close"].dropna().shape[0]) >= 20:
                    ok = True

            if ok and bool(requires_intraday):
                h1 = yf.download(
                    tickers=s,
                    period="30d",
                    interval="60m",
                    auto_adjust=False,
                    progress=False,
                    group_by="column",
                    threads=False,
                )
                if h1 is None or h1.empty:
                    ok = False
                else:
                    if isinstance(h1.columns, pd.MultiIndex):
                        lvl_names = [n or "" for n in h1.columns.names]
                        if "Ticker" in lvl_names:
                            h1 = h1.xs(s, axis=1, level="Ticker", drop_level=True)
                        elif s in h1.columns.get_level_values(-1):
                            h1 = h1.xs(s, axis=1, level=-1, drop_level=True)
                    if ("Close" not in h1.columns) or (int(h1["Close"].dropna().shape[0]) < 40):
                        ok = False
        except Exception:
            ok = False
        avail_cache[s] = ok
        return ok

    picked: List[Tuple[str, str]] = []
    used = set()

    for code in idxs:
        want = int(allocations.get(code, 0) or 0)
        if want <= 0:
            continue
        pool = [t for t in index_to_tickers.get(code, []) if t not in used]
        rng.shuffle(pool)
        got = 0
        for t in pool:
            if got >= want:
                break
            if t in used:
                continue
            if not _quick_has_data(t):
                continue
            used.add(t)
            picked.append((code, t))
            got += 1

    if len(picked) < target_n:
        global_pool: List[Tuple[str, str]] = []
        for code in idxs:
            global_pool.extend([(code, t) for t in index_to_tickers.get(code, []) if t not in used])
        rng.shuffle(global_pool)
        for code, t in global_pool:
            if len(picked) >= target_n:
                break
            if t in used:
                continue
            if not _quick_has_data(t):
                continue
            used.add(t)
            picked.append((code, t))

    return picked[:target_n]


def _read_json(path: Path) -> dict:
    try:
        if path.exists() and path.stat().st_size > 0:
            v = json.loads(path.read_text(encoding="utf-8"))
            return v if isinstance(v, dict) else {}
    except Exception:
        return {}
    return {}


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _rel_out_dir(out_dir: Path) -> str:
    try:
        return str(out_dir.resolve().relative_to(REPO_DIR.resolve())).replace("\\", "/")
    except Exception:
        return str(out_dir).replace("\\", "/")


def _load_symbols_from_sample(out_dir: Path) -> List[str]:
    run_meta = _read_json(out_dir / "run_metadata.json")
    syms = [str(x).strip() for x in (run_meta.get("symbols") or []) if str(x).strip()]
    if syms:
        return syms
    sample_csv = out_dir / "sample_100.csv"
    if sample_csv.exists():
        try:
            df = pd.read_csv(sample_csv)
            if "yfinance_ticker" in df.columns:
                return [str(x).strip() for x in df["yfinance_ticker"].tolist() if str(x).strip()]
        except Exception:
            return []
    return []


def _run_stock_export(
    *,
    indicator_config: str | None,
    export_phase: str,
    force_recompute_indicators: bool,
) -> None:
    cmd = [sys.executable, "-m", "apps.dashboard.stock_export", "--export_phase", export_phase]
    if force_recompute_indicators:
        cmd.append("--force_recompute_indicators")
    if indicator_config:
        cmd.extend(["--indicator_config", str(indicator_config)])
    subprocess.run(cmd, check=True, cwd=str(REPO_DIR))


def _with_temp_config(config_json: Path, cfg_overrides: dict, fn) -> None:
    prev_txt = config_json.read_text(encoding="utf-8") if config_json.exists() else ""
    try:
        prev = _read_json(config_json)
        cfg = dict(prev)
        cfg.update(cfg_overrides)
        config_json.parent.mkdir(parents=True, exist_ok=True)
        config_json.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        fn()
    finally:
        try:
            if prev_txt.strip():
                config_json.write_text(prev_txt, encoding="utf-8")
        except Exception:
            pass


def _write_coverage_summary(out_dir: Path, symbols: List[str], timeframes: List[str]) -> None:
    stock_data_dir = out_dir / "stock_data"
    rows: list[dict] = []
    for tf in timeframes:
        ok = 0
        for sym in symbols:
            p = stock_data_dir / f"{sym}_{tf}.csv"
            status = "missing"
            bars = 0
            if p.exists() and p.stat().st_size > 0:
                try:
                    df = pd.read_csv(p, parse_dates=[0], index_col=0)
                    bars = int(len(df.index))
                    status = "ok" if bars > 0 else "empty"
                except Exception:
                    status = "read_failed"
            rows.append({"symbol": sym, "timeframe": tf, "status": status, "bars": bars, "path": str(p)})
            if status == "ok":
                ok += 1
        pd.DataFrame([{"timeframe": tf, "ok": int(ok), "total": int(len(symbols)), "missing_or_bad": int(len(symbols) - ok)}]).to_csv(
            out_dir / f"coverage_summary_{tf}.csv",
            index=False,
        )
    pd.DataFrame(rows).to_csv(out_dir / "export_status.csv", index=False)


def _cmd_create(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stock_data").mkdir(parents=True, exist_ok=True)

    tfs = [t.strip().upper() for t in str(args.timeframes or "").split(",") if t.strip()]
    if not tfs:
        tfs = ["1W"]
    requires_intraday = "4H" in set(tfs)

    indices = _read_indices_from_pdf_text()
    sources = _default_index_sources()

    index_to_tickers: dict[str, list[str]] = {}
    provenance_rows: list[dict] = []

    for name, code in indices:
        spec = sources.get(code)
        if spec is None:
            provenance_rows.append({"index_code": code, "index_name": name, "status": "no_source_mapping", "wiki_url": ""})
            continue
        try:
            tables = _fetch_tables(spec.wiki_url)
            raw_tickers = _extract_tickers_from_tables(tables, spec.ticker_columns)
            yf: list[str] = []
            for rt in raw_tickers:
                t = _normalize_yf_ticker(rt, spec)
                if t and _is_valid_yf_ticker(t):
                    yf.append(t)
            yf = list(dict.fromkeys(yf))
            if len(yf) >= 5:
                index_to_tickers[code] = yf
            provenance_rows.append(
                {
                    "index_code": code,
                    "index_name": spec.name,
                    "status": "ok",
                    "wiki_url": spec.wiki_url,
                    "n_candidates": int(len(yf)),
                    "suffix": spec.suffix,
                    "kind": spec.kind,
                }
            )
        except Exception as e:
            provenance_rows.append(
                {
                    "index_code": code,
                    "index_name": spec.name,
                    "status": f"fetch_failed:{type(e).__name__}",
                    "wiki_url": spec.wiki_url,
                }
            )

    picked = _pick_even_sample(
        index_to_tickers=index_to_tickers,
        target_n=int(args.n),
        seed=int(args.seed),
        validate_with_yfinance=bool(getattr(args, "validate_with_yfinance", False)),
        requires_intraday=requires_intraday,
    )
    sample_rows: list[dict] = []
    for idx_code, sym in picked:
        spec = sources.get(idx_code)
        sample_rows.append(
            {
                "index_code": idx_code,
                "index_name": spec.name if spec else idx_code,
                "wiki_url": (spec.wiki_url if spec else ""),
                "yfinance_ticker": sym,
            }
        )
    symbols = [r["yfinance_ticker"] for r in sample_rows]

    pd.DataFrame(provenance_rows).to_csv(out_dir / "indices_provenance.csv", index=False)
    pd.DataFrame(sample_rows).to_csv(out_dir / "sample_100.csv", index=False)

    meta = {
        "seed": int(args.seed),
        "n": int(args.n),
        "output_dir": str(out_dir),
        "timeframes": tfs,
        "start_date": "2018-01-01",
        "notes": {"proxies": {"IXIC": "NASDAQ-100", "SSEC": "SSE 50", "TPX": "TOPIX Core30"}},
    }
    _write_json(out_dir / "sample_meta.json", meta)

    if bool(args.dry_run):
        print(f"[dry_run] wrote manifests to: {out_dir}")
        return 0

    def _run() -> None:
        t0 = time.perf_counter()
        phase = str(args.export_phase or "all").strip().lower()
        if phase not in {"all", "download", "compute"}:
            raise SystemExit(f"Bad --export_phase: {phase}")

        if phase == "all":
            _run_stock_export(indicator_config=None, export_phase="download", force_recompute_indicators=False)
            _run_stock_export(
                indicator_config=str(args.indicator_config or "").strip() or None,
                export_phase="compute",
                force_recompute_indicators=True,
            )
        elif phase == "download":
            _run_stock_export(indicator_config=None, export_phase="download", force_recompute_indicators=False)
        else:
            _run_stock_export(
                indicator_config=str(args.indicator_config or "").strip() or None,
                export_phase="compute",
                force_recompute_indicators=True,
            )
        elapsed_s = time.perf_counter() - t0
        meta["export_elapsed_seconds"] = float(elapsed_s)
        _write_json(out_dir / "sample_meta.json", meta)
        print(f"[OK] create+export completed in {elapsed_s:.2f}s ({len(symbols)} symbols × {len(tfs)} timeframes)")

    _with_temp_config(
        Path(args.config_json).expanduser() if str(args.config_json or "").strip() else DEFAULT_CONFIG_JSON,
        {
            "symbols": symbols,
            "timeframes": tfs,
            "dataset_name": "sample_100",
            "cache_ttl_hours": 0,
        },
        _run,
    )

    _write_coverage_summary(out_dir, symbols, tfs)
    return 0


def _cmd_reexport(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stock_data").mkdir(parents=True, exist_ok=True)

    indicator_config = str(args.indicator_config or "").strip()
    if not indicator_config:
        raise SystemExit("--indicator_config is required for reexport")

    tfs = [t.strip().upper() for t in str(args.timeframes or "").split(",") if t.strip()]
    if not tfs:
        tfs = ["4H", "1D", "1W"]

    symbols = _load_symbols_from_sample(out_dir)
    if not symbols:
        raise SystemExit(f"No symbols found in {out_dir}/run_metadata.json or {out_dir}/sample_100.csv")

    phase = str(args.export_phase or "all").strip().lower()
    if phase not in {"all", "download", "compute"}:
        raise SystemExit(f"Bad --export_phase: {phase}")

    def _run() -> None:
        t0 = time.perf_counter()
        if phase == "all":
            _run_stock_export(indicator_config=None, export_phase="download", force_recompute_indicators=False)
            _run_stock_export(indicator_config=indicator_config, export_phase="compute", force_recompute_indicators=True)
        elif phase == "download":
            _run_stock_export(indicator_config=None, export_phase="download", force_recompute_indicators=False)
        else:
            _run_stock_export(indicator_config=indicator_config, export_phase="compute", force_recompute_indicators=True)
        elapsed = time.perf_counter() - t0
        print(f"[OK] reexport completed in {elapsed:.2f}s ({len(symbols)} symbols × {len(tfs)} timeframes; phase={phase})")

    _with_temp_config(
        Path(args.config_json).expanduser() if str(args.config_json or "").strip() else DEFAULT_CONFIG_JSON,
        {
            "symbols": symbols,
            "timeframes": tfs,
            "dataset_name": "sample_100",
            "cache_ttl_hours": 24 if phase in {"all", "download"} else 0,
        },
        _run,
    )

    _write_coverage_summary(out_dir, symbols, tfs)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="sample_export.py", add_help=True)
    ap.add_argument("--out_dir", type=str, default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--config_json", type=str, default=str(DEFAULT_CONFIG_JSON))

    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_create = sub.add_parser("create", help="Create a random sample and export enriched data.")
    ap_create.add_argument("--seed", type=int, default=20260218)
    ap_create.add_argument("--n", type=int, default=100)
    ap_create.add_argument("--dry_run", action="store_true")
    ap_create.add_argument("--timeframes", type=str, default="1W", help="Comma-separated timeframes (default: 1W).")
    ap_create.add_argument("--export_phase", type=str, default="all", choices=["all", "download", "compute"])
    ap_create.add_argument("--indicator_config", type=str, default="", help="Optional indicator_config JSON path.")
    ap_create.add_argument(
        "--validate_with_yfinance",
        action="store_true",
        help="If set, validate candidate symbols by fetching daily OHLCV (slow; helps ensure sample has data).",
    )

    ap_re = sub.add_parser("reexport", help="Re-export existing sample without re-sampling.")
    ap_re.add_argument("--indicator_config", required=True, help="Path to indicator_config JSON.")
    ap_re.add_argument("--timeframes", type=str, default="4H,1D,1W")
    ap_re.add_argument("--export_phase", type=str, default="all", choices=["all", "download", "compute"])

    args = ap.parse_args(argv)
    if args.cmd == "create":
        return _cmd_create(args)
    if args.cmd == "reexport":
        return _cmd_reexport(args)
    raise SystemExit(f"Unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())

