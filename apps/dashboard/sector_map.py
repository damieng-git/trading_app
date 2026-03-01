"""
Sector / Industry metadata, fundamentals, and ETF benchmark mapping.

Provides:
  - fetch_sector_map()  : build/refresh cached sector_map.json from yfinance
  - load_sector_map()   : read cached mapping
  - get_benchmark_etf() : resolve the geo-appropriate ETF for a stock's sector
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SECTOR_MAP_PATH = Path(__file__).parent / "configs" / "sector_map.json"

# ── Geography detection from ticker suffix ──────────────────────────────────

EU_SUFFIXES = {
    ".PA", ".DE", ".AS", ".MI", ".MC", ".SW", ".VI", ".L", ".CO",
    ".OL", ".HE", ".WA", ".BR", ".LS", ".IR",
}

def _clean_display_name(raw: str, max_len: int = 45) -> str:
    """Normalise a yfinance display name: collapse whitespace, strip share-class
    suffix (single trailing letter separated by whitespace), and truncate."""
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    # Remove single trailing char that is a share-class indicator (e.g. "SAP SE I")
    name = re.sub(r"\s+[A-Za-z]$", "", name).strip()
    # Remove trailing punctuation artefacts (e.g. "Index -")
    name = name.rstrip(" -")
    if name and len(name) > max_len:
        name = name[: max_len - 1].rstrip() + "…"
    return name


def _ticker_geo(symbol: str) -> str:
    """Return 'EU', 'US', or 'OTHER' based on ticker suffix."""
    for suf in EU_SUFFIXES:
        if symbol.upper().endswith(suf):
            return "EU"
    if "." not in symbol or symbol.endswith(".TO"):
        return "US"
    special = {".SS", ".BO", ".T", ".SA"}
    for suf in special:
        if symbol.upper().endswith(suf):
            return "OTHER"
    return "OTHER"

# ── Sector → ETF mapping (US and EU) ───────────────────────────────────────

SECTOR_ETF_US = {
    "Technology":               "XLK",
    "Healthcare":               "XLV",
    "Financial Services":       "XLF",
    "Financials":               "XLF",
    "Industrials":              "XLI",
    "Consumer Cyclical":        "XLY",
    "Consumer Defensive":       "XLP",
    "Energy":                   "XLE",
    "Utilities":                "XLU",
    "Basic Materials":          "XLB",
    "Real Estate":              "XLRE",
    "Communication Services":   "XLC",
}

SECTOR_ETF_EU = {
    "Technology":               "TNO.PA",
    "Healthcare":               "HLT.PA",
    "Financial Services":       "IUFS.L",
    "Financials":               "IUFS.L",
    "Industrials":              "IUIS.L",
    "Consumer Cyclical":        "IUCD.L",
    "Consumer Defensive":       "IUCS.L",
    "Energy":                   "IUES.L",
    "Utilities":                "IUUS.L",
    "Basic Materials":          "IUES.L",
    "Real Estate":              "XLRE",
    "Communication Services":   "XLC",
}

INDUSTRY_ETF_US = {
    "Semiconductors":                   "SMH",
    "Semiconductor Equipment & Materials": "SMH",
    "Software - Application":           "IGV",
    "Software - Infrastructure":        "IGV",
    "Biotechnology":                    "IBB",
    "Drug Manufacturers - General":     "XPH",
    "Drug Manufacturers - Specialty & Generic": "XPH",
    "Medical Devices":                  "IHI",
    "Aerospace & Defense":              "ITA",
    "Banks - Regional":                 "KBE",
    "Banks - Diversified":              "KBE",
    "Oil & Gas E&P":                    "XOP",
    "Oil & Gas Integrated":             "XOP",
    "Gold":                             "GDX",
}

INDUSTRY_ETF_EU = {
    "Banks - Regional":                 "BNK.PA",
    "Banks - Diversified":              "BNK.PA",
    "Insurance - Diversified":          "INS.PA",
    "Insurance - Life":                 "INS.PA",
    "Insurance - Property & Casualty":  "INS.PA",
    "Drug Manufacturers - General":     "HLT.PA",
    "Biotechnology":                    "HLT.PA",
}


NATIONAL_INDEX_BY_SUFFIX: Dict[str, str] = {
    ".PA":  "^FCHI",       # CAC 40
    ".DE":  "^GDAXI",      # DAX
    ".AS":  "^AEX",        # AEX (Amsterdam)
    ".MI":  "FTSEMIB.MI",  # FTSE MIB (Milan)
    ".MC":  "^IBEX",       # IBEX 35 (Madrid)
    ".SW":  "^SSMI",       # SMI (Zurich)
    ".VI":  "^ATX",        # ATX (Vienna)
    ".L":   "^FTSE",       # FTSE 100 (London)
    ".CO":  "^OMXC25",     # OMX Copenhagen 25
    ".OL":  "^OBX",        # OBX (Oslo)
    ".HE":  "^OMXH25",     # OMX Helsinki 25
    ".WA":  "WIG20.WA",    # WIG 20 (Warsaw)
    ".BR":  "^BFX",        # BEL 20 (Brussels)
    ".LS":  "^STOXX50E",   # Euro Stoxx 50 (fallback for Lisbon)
    ".IR":  "^STOXX50E",   # Euro Stoxx 50 (fallback for Dublin)
    ".SS":  "000001.SS",   # SSE Composite (Shanghai)
    ".T":   "^N225",       # Nikkei 225 (Tokyo)
    ".BO":  "^BSESN",      # BSE Sensex (Mumbai)
    ".SA":  "^BVSP",       # Bovespa (Sao Paulo)
    ".TO":  "^GSPTSE",     # S&P/TSX (Toronto)
}
NATIONAL_INDEX_US = "^GSPC"  # S&P 500


def get_national_index(symbol: str) -> str:
    """Resolve the national index for a stock based on its ticker suffix."""
    sym = symbol.upper()
    for suf, idx in NATIONAL_INDEX_BY_SUFFIX.items():
        if sym.endswith(suf):
            return idx
    return NATIONAL_INDEX_US


def get_sector_etf(sector: str, geo: str) -> Optional[str]:
    """Resolve the sector-level ETF for a stock (always available when sector is known)."""
    if geo == "EU":
        return SECTOR_ETF_EU.get(sector)
    return SECTOR_ETF_US.get(sector)


def get_industry_etf(industry: str, geo: str) -> Optional[str]:
    """Resolve the industry-level ETF (more granular, but only available for some industries)."""
    if geo == "EU":
        return INDUSTRY_ETF_EU.get(industry)
    return INDUSTRY_ETF_US.get(industry)


def get_benchmark_etf(sector: str, industry: str, geo: str) -> Optional[str]:
    """Resolve the best ETF benchmark: sector-level first (broad coverage), industry as extra detail."""
    return get_sector_etf(sector, geo) or get_industry_etf(industry, geo)


# ── Fundamental fields extracted from yf.Ticker().info ─────────────────────
# Maps our storage key → yfinance info key.  All come from the single .info
# call we already make, so there is zero additional API cost.

_FUNDAMENTAL_FIELDS: Dict[str, str] = {
    # Valuation
    "market_cap":        "marketCap",
    "trailing_pe":       "trailingPE",
    "forward_pe":        "forwardPE",
    "peg_ratio":         "pegRatio",
    "price_to_book":     "priceToBook",
    # Profitability
    "profit_margins":    "profitMargins",
    "return_on_equity":  "returnOnEquity",
    "gross_margins":     "grossMargins",
    # Growth
    "earnings_growth":   "earningsGrowth",
    "revenue_growth":    "revenueGrowth",
    # Dividends
    "dividend_yield":    "dividendYield",
    # Market / risk
    "beta":              "beta",
    "52w_high":          "fiftyTwoWeekHigh",
    "52w_low":           "fiftyTwoWeekLow",
    "short_pct_float":   "shortPercentOfFloat",
    # Analyst
    "recommendation":    "recommendationKey",
    "target_price":      "targetMeanPrice",
    "num_analysts":      "numberOfAnalystOpinions",
    # Identity
    "country":           "country",
    "currency":          "currency",
    # Financial health
    "debt_to_equity":    "debtToEquity",
    "free_cashflow":     "freeCashflow",
    "total_revenue":     "totalRevenue",
}


def _extract_fundamentals(info: dict) -> Dict[str, Any]:
    """Pull fundamental fields from a yfinance info dict."""
    out: Dict[str, Any] = {}
    for our_key, yf_key in _FUNDAMENTAL_FIELDS.items():
        val = info.get(yf_key)
        if val is not None:
            out[our_key] = val
    return out


# ── Fetch & cache ──────────────────────────────────────────────────────────

def _fetch_one_symbol(
    sym: str,
    entry: Dict[str, Any],
    refresh_fundamentals: bool,
) -> tuple[str, Dict[str, Any] | None]:
    """Fetch metadata for a single symbol via yfinance. Thread-safe."""
    import yfinance as yf

    has_name = bool(entry.get("name"))
    has_index = bool(entry.get("national_index"))
    has_fundamentals = bool(entry.get("fundamentals"))

    needs_identity = not (has_name and has_index)
    needs_fundamentals = refresh_fundamentals or not has_fundamentals

    if not needs_identity and not needs_fundamentals:
        return sym, None

    try:
        geo = entry.get("geo") or _ticker_geo(sym)
        nat_idx = entry.get("national_index") or get_national_index(sym)

        if has_name and not has_index and not needs_fundamentals:
            entry.setdefault("geo", geo)
            entry["national_index"] = nat_idx
            return sym, entry

        info = yf.Ticker(sym).info or {}

        if needs_identity:
            sector = info.get("sector", "")
            industry = info.get("industry", "")
            raw_name = info.get("longName") or info.get("shortName") or ""
            name = _clean_display_name(raw_name)
            prev_name = entry.get("name") or ""
            prev_sector = entry.get("sector") or ""
            prev_industry = entry.get("industry") or ""
            final_name = prev_name or name
            final_sector = prev_sector or sector or ""
            final_industry = prev_industry or industry or ""
            sec_etf = get_sector_etf(final_sector, geo) if final_sector else (entry.get("sector_etf") or "")
            ind_etf = get_industry_etf(final_industry, geo) if final_industry else (entry.get("industry_etf") or "")
            entry.update({
                "name": final_name,
                "sector": final_sector,
                "industry": final_industry,
                "geo": geo,
                "national_index": nat_idx,
                "sector_etf": sec_etf or "",
                "industry_etf": ind_etf or "",
                "benchmark_etf": sec_etf or ind_etf or "",
            })

        fundamentals = _extract_fundamentals(info)
        if fundamentals:
            entry["fundamentals"] = fundamentals

        return sym, entry
    except Exception:
        if not has_name:
            return sym, {
                "name": "", "sector": "", "industry": "",
                "geo": _ticker_geo(sym),
                "national_index": get_national_index(sym),
                "sector_etf": "", "industry_etf": "",
                "benchmark_etf": "", "fundamentals": {},
            }
        return sym, None


def fetch_sector_map(
    symbols: list[str],
    cache_path: Path | None = None,
    refresh_fundamentals: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """
    Single yf.Ticker().info call per symbol — fetches name, sector, industry,
    fundamentals, and resolves geo, national index, and ETF benchmarks.
    Results are cached to sector_map.json so offline/rebuild runs never need
    the network.

    Uses ThreadPoolExecutor to parallelize yfinance API calls (I/O-bound).

    Set *refresh_fundamentals=True* to re-fetch fundamentals for symbols that
    already have identity data (useful for keeping P/E, analyst targets, etc.
    up to date).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    path = cache_path or SECTOR_MAP_PATH
    existing: Dict[str, Dict[str, Any]] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Failed to load existing sector_map from cache: %s", exc)
            pass

    tasks = []
    for sym in symbols:
        entry = dict(existing.get(sym, {}))
        tasks.append((sym, entry, refresh_fundamentals))

    updated = 0
    _MAX_WORKERS = min(12, max(1, len(tasks)))
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one_symbol, *t): t[0] for t in tasks}
        for future in as_completed(futures):
            sym, result = future.result()
            if result is not None:
                existing[sym] = result
                updated += 1

    if updated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Sector map: updated {updated} symbols, total {len(existing)}")
    return existing


def load_sector_map(cache_path: Path | None = None) -> Dict[str, Dict[str, Any]]:
    """Load cached sector_map.json (no network calls)."""
    path = cache_path or SECTOR_MAP_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_display_names(
    sector_map: Dict[str, Dict[str, Any]],
    overrides: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """Derive a flat {symbol: display_name} dict from the sector map + overrides."""
    result: Dict[str, str] = {}
    for sym, meta in sector_map.items():
        name = str(meta.get("name") or "").strip()
        if name:
            result[sym] = name
    if overrides:
        result.update(overrides)
    return result
