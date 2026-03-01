#!/usr/bin/env python3
"""Build universe.csv from NASDAQ screener API (US) + hardcoded EU index constituents.

US stocks are fetched live from the NASDAQ screener API (NYSE + NASDAQ + AMEX),
which provides ticker, name, market cap, price, sector, and country.  A coarse
pre-filter (market cap >= $300M, price >= $5) is applied to drop obvious junk
before writing the CSV.

EU stocks come from hardcoded index constituent lists that are updated quarterly.

Usage: python3 apps/screener/_build_universe.py
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_OUT = Path(__file__).resolve().parent / "configs" / "universe.csv"

_NASDAQ_API = "https://api.nasdaq.com/api/screener/stocks"
_EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TradingDashboard/1.0)"}
_MIN_MCAP = 300_000_000
_MIN_PRICE = 5.0

# Patterns to exclude: SPACs, warrants, units, rights, preferred, notes, ETFs
_EXCLUDE_SUFFIXES = re.compile(
    r"\b(warrant|warrants|right|rights|units?|notes?|debenture)\b", re.IGNORECASE
)
_EXCLUDE_TICKER = re.compile(r"[+^]|W$|WS$|\.U$|\.R$|\.WS$")


def _fetch_us_tickers() -> list[dict]:
    """Fetch all US-listed equities from the NASDAQ screener API."""
    import requests

    all_rows: list[dict] = []
    for exchange in _EXCHANGES:
        url = f"{_NASDAQ_API}?tableonly=true&exchange={exchange}&limit=25000&download=true"
        try:
            r = requests.get(url, headers=_HEADERS, timeout=20)
            r.raise_for_status()
            rows = r.json().get("data", {}).get("rows", [])
            for row in rows:
                row["_exchange"] = exchange
            all_rows.extend(rows)
            logger.info("%s: %d raw tickers", exchange, len(rows))
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", exchange, exc)
        time.sleep(0.3)

    records: list[dict] = []
    seen: set[str] = set()

    for row in all_rows:
        symbol = (row.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        if _EXCLUDE_TICKER.search(symbol):
            continue

        name = row.get("name") or ""
        if _EXCLUDE_SUFFIXES.search(name):
            continue

        price_str = (row.get("lastsale") or "").replace("$", "").replace(",", "")
        mcap_str = row.get("marketCap") or "0"
        try:
            price = float(price_str)
        except (ValueError, TypeError):
            price = 0.0
        try:
            mcap = float(mcap_str)
        except (ValueError, TypeError):
            mcap = 0.0

        if price < _MIN_PRICE or mcap < _MIN_MCAP:
            continue

        seen.add(symbol)
        records.append({
            "ticker": symbol,
            "source_index": row.get("_exchange", "").lower(),
            "geo": "US",
        })

    logger.info("US tickers after pre-filter: %d", len(records))
    return records


# ── Hardcoded EU index constituents (Feb 2026, quarterly-updated) ──────
# Tickers in yfinance format.

_FTSE_100 = [
    "III.L", "ABF.L", "ADM.L", "AHT.L", "ANTO.L", "AUTO.L", "AV.L", "AZN.L",
    "BA.L", "BARC.L", "BDEV.L", "BEZ.L", "BKG.L", "BME.L", "BNZL.L", "BP.L",
    "BRBY.L", "BT-A.L", "CBG.L", "CCH.L", "CNA.L", "CPG.L", "CRDA.L", "CRH",
    "CTEC.L", "DARK.L", "DCC.L", "DGE.L", "DPLM.L", "EDV.L", "ENT.L", "EXPN.L",
    "EZJ.L", "FLTR.L", "FRAS.L", "FRES.L", "GLEN.L", "GSK.L", "HIK.L", "HL.L",
    "HLMA.L", "HLN.L", "HSBA.L", "HWDN.L", "IAG.L", "ICG.L", "IHG.L", "IMB.L",
    "INF.L", "ITRK.L", "JD.L", "KGF.L", "LAND.L", "LGEN.L", "LLOY.L", "LSEG.L",
    "MKS.L", "MNDI.L", "MNG.L", "MRO.L", "NG.L", "NWG.L", "NXT.L", "PHNX.L",
    "PRU.L", "PSH.L", "PSN.L", "PSON.L", "REL.L", "RIO.L", "RKT.L", "RMV.L",
    "RR.L", "RTO.L", "SAE.L", "SBRY.L", "SDR.L", "SGE.L", "SGRO.L", "SHEL.L",
    "SKG.L", "SMDS.L", "SMIN.L", "SMT.L", "SN.L", "SPX.L", "SSE.L", "STAN.L",
    "SVT.L", "TSCO.L", "TW.L", "ULVR.L", "UTG.L", "UU.L", "VOD.L", "VTY.L",
    "WEIR.L", "WPP.L", "WTB.L",
]

_DAX = [
    "1COV.DE", "ADS.DE", "AIR.PA", "ALV.DE", "BAS.DE", "BAYN.DE", "BEI.DE",
    "BMW.DE", "BNR.DE", "CBK.DE", "CON.DE", "DB1.DE", "DBK.DE", "DHL.DE",
    "DTE.DE", "DTG.DE", "ENR.DE", "EON.DE", "FME.DE", "FRE.DE", "HEI.DE",
    "HEN3.DE", "HNR1.DE", "IFX.DE", "MBG.DE", "MRK.DE", "MTX.DE", "MUV2.DE",
    "PAH3.DE", "RHM.DE", "RWE.DE", "SAP.DE", "SHL.DE", "SIE.DE", "SRT3.DE",
    "SY1.DE", "TKA.DE", "VNA.DE", "VOW3.DE", "ZAL.DE",
]

_CAC_40 = [
    "AI.PA", "AIR.PA", "ALO.PA", "ATO.PA", "BN.PA", "BNP.PA", "CA.PA",
    "CAP.PA", "CS.PA", "DG.PA", "DSY.PA", "EL.PA", "EN.PA", "ENGI.PA",
    "ERF.PA", "GLE.PA", "HO.PA", "KER.PA", "LR.PA", "MC.PA", "ML.PA",
    "MT.AS", "OR.PA", "ORA.PA", "PUB.PA", "RI.PA", "RMS.PA", "RNO.PA",
    "SAF.PA", "SAN.PA", "SGO.PA", "SK.PA", "SOP.PA", "STLAP.PA", "STM.PA",
    "SU.PA", "TEP.PA", "TTE.PA", "URW.PA", "VIE.PA", "VIV.PA",
]

_SMI = [
    "ABBN.SW", "ADEN.SW", "CFR.SW", "CSGN.SW", "GEBN.SW", "GIVN.SW",
    "HOLN.SW", "KNIN.SW", "LONN.SW", "NESN.SW", "NOVN.SW", "PGHN.SW",
    "ROG.SW", "SCMN.SW", "SGSN.SW", "SIKA.SW", "SLHN.SW", "SREN.SW",
    "UBSG.SW", "ZURN.SW",
]

_AEX = [
    "ABN.AS", "AD.AS", "ADYEN.AS", "AGN.AS", "AH.AS", "AKZA.AS", "ASM.AS",
    "ASML.AS", "BESI.AS", "DSM.AS", "HEIA.AS", "INGA.AS", "KPN.AS",
    "MT.AS", "NN.AS", "PHIA.AS", "PRX.AS", "RAND.AS", "REN.AS", "SHELL.AS",
    "UMG.AS", "UNA.AS", "URW.AS", "WKL.AS",
]

_IBEX_35 = [
    "ACS.MC", "ACX.MC", "AMS.MC", "ANA.MC", "BBVA.MC", "BKT.MC", "CABK.MC",
    "CLNX.MC", "ELE.MC", "ENG.MC", "FDR.MC", "FER.MC", "GRF.MC", "IAG.MC",
    "IBE.MC", "IDR.MC", "ITX.MC", "LOG.MC", "MAP.MC", "MEL.MC", "MRL.MC",
    "MTS.MC", "NTGY.MC", "RED.MC", "REP.MC", "ROVI.MC", "SAB.MC", "SAN.MC",
    "SCYR.MC", "SLR.MC", "SOL.MC", "TEF.MC", "UNI.MC",
]

_FTSE_MIB = [
    "A2A.MI", "AMP.MI", "AZM.MI", "BAMI.MI", "BPE.MI", "BPER.MI", "BZU.MI",
    "CPR.MI", "DIA.MI", "ENEL.MI", "ENI.MI", "ERG.MI", "FBK.MI", "G.MI",
    "HER.MI", "IG.MI", "INW.MI", "IP.MI", "ISP.MI", "IVG.MI", "LDO.MI",
    "MB.MI", "MONC.MI", "NEXI.MI", "PIRC.MI", "PST.MI", "PRY.MI", "REC.MI",
    "RACE.MI", "SPM.MI", "SRG.MI", "STM.MI", "TEN.MI", "TIT.MI", "TRN.MI",
    "UCG.MI", "UNI.MI",
]

_OMXS_30 = [
    "ABB.ST", "ALFA.ST", "ASSA-B.ST", "ATCO-A.ST", "AZN.ST", "BOL.ST",
    "ELUX-B.ST", "ERIC-B.ST", "ESSITY-B.ST", "EVO.ST", "GETI-B.ST",
    "HEXA-B.ST", "HM-B.ST", "INVE-B.ST", "KINV-B.ST", "NDA-SE.ST",
    "NIBE-B.ST", "SAND.ST", "SBB-B.ST", "SCA-B.ST", "SEB-A.ST", "SHB-A.ST",
    "SKA-B.ST", "SKF-B.ST", "SSAB-A.ST", "SWED-A.ST", "TELIA.ST",
    "VOLV-B.ST",
]

_OMXC_25 = [
    "AMBU-B.CO", "BAVA.CO", "CARL-B.CO", "CHR.CO", "COLO-B.CO", "DANSKE.CO",
    "DEMANT.CO", "DSV.CO", "FLS.CO", "GN.CO", "ISS.CO", "JYSK.CO",
    "MAERSK-B.CO", "NETC.CO", "NOVO-B.CO", "NZYM-B.CO", "ORSTED.CO",
    "PNDORA.CO", "RBREW.CO", "ROCK-B.CO", "SYDB.CO", "TOP.CO",
    "TRYG.CO", "VWS.CO", "WDH.CO",
]

_OMXH_25 = [
    "ELISA.HE", "FORTUM.HE", "KNEBV.HE", "KOJAMO.HE", "METSB.HE",
    "NESTE.HE", "NOKIA.HE", "NORDEA.HE", "ORNBV.HE", "OUT1V.HE",
    "SAMPO.HE", "STERV.HE", "TIETO.HE", "TLS1V.HE", "UPM.HE",
    "VALMT.HE", "WRT1V.HE",
]

_BEL_20 = [
    "ABI.BR", "ACKB.BR", "AGS.BR", "APAM.BR", "COFB.BR", "COLR.BR",
    "ELI.BR", "GBLB.BR", "KBC.BR", "MELE.BR", "PROX.BR", "SOF.BR",
    "SOLB.BR", "UCB.BR", "UMI.BR", "WDP.BR",
]

_OBX = [
    "AKRBP.OL", "AKER.OL", "BAKKA.OL", "DNB.OL", "EQNR.OL", "FLNG.OL",
    "FRO.OL", "GJF.OL", "KOG.OL", "MOWI.OL", "NHY.OL", "ORK.OL",
    "SALM.OL", "SCATC.OL", "STB.OL", "SUBC.OL", "TEL.OL", "TOM.OL",
    "VAR.OL", "YAR.OL",
]

_WIG_20 = [
    "ALE.WA", "ALR.WA", "CCC.WA", "CDR.WA", "CPS.WA", "DNP.WA", "JSW.WA",
    "KGH.WA", "KRU.WA", "KTY.WA", "LPP.WA", "MBK.WA", "OPL.WA", "PEO.WA",
    "PGE.WA", "PKN.WA", "PKO.WA", "PZU.WA", "SPL.WA",
]

_ATX = [
    "AMS.VI", "ANDR.VI", "BG.VI", "CAI.VI", "DO.VI", "EBS.VI", "EVN.VI",
    "FLU.VI", "IIA.VI", "LNZ.VI", "OMV.VI", "POST.VI", "RBI.VI", "SBO.VI",
    "TKA.VI", "UQA.VI", "VER.VI", "VIG.VI", "VOE.VI", "WIE.VI",
]

_FTSE_250_SAMPLE = [
    "3IN.L", "AAF.L", "AAS.L", "ABDN.L", "AGR.L", "AGT.L", "AJB.L",
    "ANTO.L", "ASHM.L", "ATK.L", "AVON.L", "AZN.L", "BAKK.L", "BBOX.L",
    "BCPT.L", "BDEV.L", "BGEO.L", "BHP.L", "BIRK.L", "BLND.L", "BMY.L",
    "BNKR.L", "BOY.L", "BREE.L", "BRSC.L", "BVIC.L", "BWY.L", "CAL.L",
    "CBG.L", "CCC.L", "CCL.L", "CHRT.L", "CINE.L", "CLG.L", "CMCX.L",
    "CNA.L", "COA.L", "CRD-A.L", "CREI.L", "CRST.L", "CTY.L", "CWK.L",
    "DJAN.L", "DLN.L", "DOCS.L", "DOM.L", "DPLM.L", "DRX.L", "DWHT.L",
    "ECM.L", "ELM.L", "EMG.L", "ENOG.L", "ERIS.L", "ESNT.L", "EVR.L",
    "FCIT.L", "FDM.L", "FGT.L", "FOUR.L", "FSV.L", "GAW.L", "GCP.L",
    "GFTU.L", "GNS.L", "GPOR.L", "GRI.L", "GRG.L", "GROW.L", "HICL.L",
    "HBR.L", "HLCL.L", "HMSO.L", "HSV.L", "HSX.L", "HTWS.L", "HWDN.L",
    "IGG.L", "III.L", "IMI.L", "INCH.L", "INF.L", "IPO.L", "IPX.L",
    "ITV.L", "JLEN.L", "JLG.L", "JET2.L", "KGF.L", "KIE.L", "KWS.L",
    "LGEN.L", "LIO.L", "LSEG.L", "LXI.L", "MONY.L", "MGNS.L", "MNKS.L",
    "MNZS.L", "MRO.L", "MTO.L", "MUT.L", "NCC.L", "NRR.L", "NWG.L",
    "OSB.L", "OXIG.L", "PAGE.L", "PAX.L", "PFG.L", "PHNX.L", "PIN.L",
    "POLY.L", "PNN.L", "PNL.L", "PPH.L", "PRU.L", "PSH.L", "PSN.L",
    "PSON.L", "QQ.L", "RAT.L", "RDW.L", "REL.L", "RHIM.L", "RKT.L",
    "RMV.L", "RNK.L", "ROR.L", "RR.L", "RTO.L", "RWS.L", "SAE.L",
    "SAFE.L", "SBRY.L", "SCHR.L", "SDRY.L", "SDR.L", "SGE.L", "SGRO.L",
    "SHED.L", "SKG.L", "SMDS.L", "SMIN.L", "SMT.L", "SN.L", "SNDR.L",
    "SPT.L", "SPX.L", "SSE.L", "SSON.L", "STAN.L", "STS.L", "SVS.L",
    "SVT.L", "SXS.L", "SYNT.L", "TATE.L", "TCAP.L", "TIFS.L", "TPFG.L",
    "TRB.L", "TRST.L", "TSCO.L", "TUI1.L", "TW.L", "UDG.L", "ULVR.L",
    "UKCM.L", "UTG.L", "UU.L", "VCT.L", "VEIL.L", "VMUK.L", "VOD.L",
    "VTY.L", "WAG.L", "WG.L", "WEIR.L", "WHR.L", "WINK.L", "WIX.L",
    "WPP.L", "WTB.L", "XPS.L",
]


def _build_eu_records() -> list[dict]:
    """Build records from all hardcoded EU index constituent lists."""
    eu_lists = [
        (_FTSE_100, "ftse100"),
        (_FTSE_250_SAMPLE, "ftse250"),
        (_DAX, "dax"),
        (_CAC_40, "cac40"),
        (_SMI, "smi"),
        (_AEX, "aex"),
        (_IBEX_35, "ibex35"),
        (_FTSE_MIB, "ftsemib"),
        (_OMXS_30, "omxs30"),
        (_OMXC_25, "omxc25"),
        (_OMXH_25, "omxh25"),
        (_BEL_20, "bel20"),
        (_OBX, "obx"),
        (_WIG_20, "wig20"),
        (_ATX, "atx"),
    ]
    records: list[dict] = []
    for tickers, source in eu_lists:
        for t in tickers:
            records.append({"ticker": t, "source_index": source, "geo": "EU"})
        logger.info("%s: %d tickers", source.upper(), len(tickers))
    return records


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import pandas as pd

    # ── US stocks from NASDAQ API ─────────────────────────────────────────
    print("Fetching US stocks from NASDAQ screener API...")
    us_records = _fetch_us_tickers()
    print(f"  US stocks (mcap >= ${_MIN_MCAP/1e6:.0f}M, price >= ${_MIN_PRICE}): {len(us_records)}")

    # ── EU stocks from hardcoded lists ────────────────────────────────────
    print("\nLoading EU index constituents...")
    eu_records = _build_eu_records()
    print(f"  EU stocks: {len(eu_records)}")

    # ── Merge, deduplicate, write ─────────────────────────────────────────
    all_records = us_records + eu_records
    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset=["ticker"], keep="first")
    df = df.sort_values("ticker").reset_index(drop=True)

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(_OUT, index=False)

    n_us = len(df[df["geo"] == "US"])
    n_eu = len(df[df["geo"] == "EU"])
    print(f"\nUniverse CSV written: {_OUT}")
    print(f"  Total unique tickers: {len(df)}")
    print(f"  US: {n_us}  |  EU: {n_eu}")


if __name__ == "__main__":
    main()
