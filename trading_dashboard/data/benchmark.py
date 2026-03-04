"""
Benchmark and sector map utilities for the data layer.

Provides benchmark resolution without depending on the apps layer.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_BENCHMARK = "SPY"
_NATIONAL_INDEX = {
    "US": "SPY",
    "DE": "EWG",
    "FR": "EWQ",
    "NL": "EWN",
    "GB": "EWU",
    "IT": "EWI",
    "ES": "EWP",
    "EU": "VGK",
}

# Ticker suffix → country code (for symbol-only lookup when sector_map is None)
_SUFFIX_TO_COUNTRY = {
    ".PA": "FR",
    ".DE": "DE",
    ".AS": "NL",
    ".MI": "IT",
    ".MC": "ES",
    ".SW": "CH",
    ".VI": "AT",
    ".L": "GB",
    ".CO": "DK",
    ".OL": "NO",
    ".HE": "FI",
    ".WA": "PL",
    ".BR": "BE",
    ".LS": "EU",
    ".IR": "EU",
    ".SS": "CN",
    ".T": "JP",
    ".BO": "IN",
    ".SA": "BR",
    ".TO": "CA",
}


def get_national_index(symbol: str, sector_map: Optional[dict] = None) -> str:
    """Return the national benchmark ETF for a symbol based on its country."""
    if sector_map and symbol in sector_map:
        country = (sector_map[symbol].get("country") or "US").upper()
    else:
        sym = symbol.upper()
        country = "US"
        for suf, c in _SUFFIX_TO_COUNTRY.items():
            if sym.endswith(suf):
                country = c
                break
    return _NATIONAL_INDEX.get(country, _DEFAULT_BENCHMARK)


def get_benchmark_etf(symbol: str, sector_map: Optional[dict] = None) -> str:
    """Return the benchmark ETF for a symbol (sector or national)."""
    if sector_map and symbol in sector_map:
        etf = sector_map[symbol].get("benchmark_etf") or sector_map[symbol].get("sector_etf")
        if etf:
            return etf
    return get_national_index(symbol, sector_map)


# Sector/industry → ETF mapping for get_benchmark_etf_from_sector (used when
# enrichment has sector_info dict with sector, industry, geo but no full sector_map)
_SECTOR_ETF_US = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

_SECTOR_ETF_EU = {
    "Technology": "TNO.PA",
    "Healthcare": "HLT.PA",
    "Financial Services": "IUFS.L",
    "Financials": "IUFS.L",
    "Industrials": "IUIS.L",
    "Consumer Cyclical": "IUCD.L",
    "Consumer Defensive": "IUCS.L",
    "Energy": "IUES.L",
    "Utilities": "IUUS.L",
    "Basic Materials": "IUES.L",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

_INDUSTRY_ETF_US = {
    "Semiconductors": "SMH",
    "Semiconductor Equipment & Materials": "SMH",
    "Software - Application": "IGV",
    "Software - Infrastructure": "IGV",
    "Biotechnology": "IBB",
    "Drug Manufacturers - General": "XPH",
    "Drug Manufacturers - Specialty & Generic": "XPH",
    "Medical Devices": "IHI",
    "Aerospace & Defense": "ITA",
    "Banks - Regional": "KBE",
    "Banks - Diversified": "KBE",
    "Oil & Gas E&P": "XOP",
    "Oil & Gas Integrated": "XOP",
    "Gold": "GDX",
}

_INDUSTRY_ETF_EU = {
    "Banks - Regional": "BNK.PA",
    "Banks - Diversified": "BNK.PA",
    "Insurance - Diversified": "INS.PA",
    "Insurance - Life": "INS.PA",
    "Insurance - Property & Casualty": "INS.PA",
    "Drug Manufacturers - General": "HLT.PA",
    "Biotechnology": "HLT.PA",
}


def get_benchmark_etf_from_sector(sector: str, industry: str, geo: str) -> Optional[str]:
    """Return the benchmark ETF for a stock given its sector, industry, and geo."""
    if geo == "EU":
        return _SECTOR_ETF_EU.get(sector) or _INDUSTRY_ETF_EU.get(industry)
    return _SECTOR_ETF_US.get(sector) or _INDUSTRY_ETF_US.get(industry)
