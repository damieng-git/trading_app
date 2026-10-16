"""Phase-1 yfinance enrichment for the SWS screener workbook.

Turns each parsed/merged screener row into a wider decision substrate by adding
the HIGH-CONFIDENCE tier of yfinance-derived metrics (see references/design.md).
Everything here obeys the skill's non-negotiables:

  * A missing / failed / undefined field is ``None`` (rendered blank downstream),
    NEVER 0.
  * Negative-denominator multiples are blank (decision #5).
  * yfinance is unofficial and rate-limited; every ticker and every field is
    wrapped so nothing aborts the run (decision #7).
  * Pure metric functions consume normalized shapes (a price/volume DataFrame and
    an ``info`` dict) so they unit-test without the network (decision #11).

The network boundary is a single injectable function ``_fetch_snapshot(symbol)``.
``enrich_rows`` takes ``fetch=`` so tests pass an offline stub.

Public API:
  Snapshot                      - normalized, serializable fetch result
  _fetch_snapshot(symbol)       - the live yfinance boundary (DI'd)
  enrich_rows(df, cfg, fetch=)  - add Phase-1 columns to the merged frame
  map_symbol(ticker, country)   - SWS bare ticker -> yfinance symbol
  to_major_units(series, ccy)   - GBX/pence -> major-unit normalization
  cross_check(...)              - currency / 1y-return / price guard
  plus the pure metric functions (wilder_rsi, price_return, max_drawdown, ...).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# New columns this module adds. Internal field name -> display column name is
# defined in build_workbook.COLUMN_SOURCES; the field names here are the keys
# written onto the DataFrame.
# ---------------------------------------------------------------------------
NUMERIC_INPUT_FIELDS = [
    "pe_trailing", "pe_forward", "ps_ratio", "ev_ebitda", "p_fcf", "ev_sales", "ev_fcf",
    "dist_ma50", "dist_ma200", "pct_from_high", "pct_from_low",
    "mom_6m", "mom_12m", "rsi_14", "beta",
    "max_drawdown_3y", "avg_dollar_vol_eur_m",
    "net_cash_eur_m", "runway_quarters",
    "pb_ratio", "roe",
]
DATE_FIELDS = ["next_earnings", "ex_div"]
OTHER_FIELDS = ["cap_tier", "_yf_symbol", "_enrich_note", "_probe_note",
                "fcf_positive", "yf_sector", "yf_industry",
                # Name + context for the dashboard's Claude take. Not xlsx columns.
                "yf_name", "yf_price", "rec_key", "rec_mean", "n_analysts",
                "tgt_mean", "tgt_low", "tgt_high",
                "profit_margin", "revenue_growth", "debt_to_equity"]
ENRICH_FIELDS = NUMERIC_INPUT_FIELDS + DATE_FIELDS + OTHER_FIELDS

# yfinance exchange suffix by SWS listing-country (decision #3). Bare-US = "".
SUFFIX_BY_COUNTRY = {
    "us": "", "gb": ".L", "de": ".DE", "fr": ".PA", "dk": ".CO", "se": ".ST",
    "no": ".OL", "fi": ".HE", "it": ".MI", "nl": ".AS", "es": ".MC", "ch": ".SW",
    "be": ".BR", "at": ".VI", "ie": ".IR", "pt": ".LS", "pl": ".WA", "ca": ".TO", "au": ".AX",
    "jp": ".T", "hk": ".HK",
}

# Cross-check tolerances (decision #4). Screener as-of date is ~today, so prices
# should agree closely; a gross divergence means the symbol hit the wrong listing.
_CCY_STRICT = True
_RET_TOL = 0.50          # abs difference in 1y return (fraction)
_PX_REL_TOL = 0.20       # relative price difference


# ---------------------------------------------------------------------------
# Normalized, serializable snapshot (decision #11: no pickled yf objects)
# ---------------------------------------------------------------------------
@dataclass
class Snapshot:
    symbol: str
    history: pd.DataFrame                    # index=Date; columns: close, volume (RAW units)
    info: dict = field(default_factory=dict)
    calendar: dict = field(default_factory=dict)   # {'earnings': date|None, 'ex_div': date|None}
    cashflow: Optional[pd.DataFrame] = None
    ok: bool = True                          # False when the fetch itself failed

    def dump(self, dirpath: str) -> None:
        """Persist to disk in a fixture-friendly shape (CSV price + JSON info)."""
        os.makedirs(dirpath, exist_ok=True)
        base = os.path.join(dirpath, self.symbol.replace("/", "_"))
        if self.history is not None and len(self.history):
            self.history.to_csv(base + ".history.csv")
        with open(base + ".info.json", "w", encoding="utf-8") as fh:
            json.dump(_json_safe(self.info), fh, indent=1, default=str)
        with open(base + ".calendar.json", "w", encoding="utf-8") as fh:
            json.dump(_json_safe(self.calendar), fh, indent=1, default=str)
        if self.cashflow is not None:
            try:
                self.cashflow.to_csv(base + ".cashflow.csv")
            except Exception:
                pass


def load_snapshot(dirpath: str, symbol: str) -> Snapshot:
    """Rebuild a Snapshot from a dumped fixture directory (offline)."""
    base = os.path.join(dirpath, symbol.replace("/", "_"))
    hist = pd.read_csv(base + ".history.csv", index_col=0, parse_dates=[0])
    with open(base + ".info.json", encoding="utf-8") as fh:
        info = json.load(fh)
    cal = {"earnings": None, "ex_div": None}
    if os.path.exists(base + ".calendar.json"):
        with open(base + ".calendar.json", encoding="utf-8") as fh:
            raw = json.load(fh)
        cal = {"earnings": _parse_date(raw.get("earnings")),
               "ex_div": _parse_date(raw.get("ex_div"))}
    return Snapshot(symbol=symbol, history=hist, info=info, calendar=cal)


def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


def _parse_date(v):
    if v is None:
        return None
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ticker mapping (decision #3)
# ---------------------------------------------------------------------------
def map_symbol(ticker: Optional[str], country: Optional[str],
               currency: Optional[str] = None,
               overrides: Optional[dict] = None) -> Optional[str]:
    """SWS bare ticker + listing country -> yfinance symbol.

    An override keyed ``"TICKER|CCY"`` wins first. Otherwise trailing dots are
    stripped, then spaces and inner dots become "-" before the exchange suffix is
    added ('SN.'->'SN.L', 'SEB A'->'SEB-A.ST', 'BT.A'->'BT-A.L'). Country 'eu'
    (EUR row, no exchange in the table view) and unknown countries return None
    unless overridden, so we never fetch a bare ambiguous symbol.
    """
    if not ticker:
        return None
    if overrides:
        ov = overrides.get(f"{str(ticker).strip()}|{currency}")
        if ov:
            return str(ov)
    c = (country or "").strip().lower()
    raw = str(ticker).strip().upper()
    # US OTC ADR / foreign listing: LKNC.Y -> LKNCY, XYZF.F -> XYZFF (Yahoo has no dot).
    # Other inner dots keep the dash rule below (BRK.B -> BRK-B).
    if c == "us" and _OTC_DOT.match(raw) and raw[-1] in ("Y", "F"):
        return raw.replace(".", "")
    base = _norm_base(ticker)
    if not base:
        return None
    if c in SUFFIX_BY_COUNTRY:
        return base + SUFFIX_BY_COUNTRY[c]
    return None


_OTC_DOT = re.compile(r"^[A-Z]{4,5}\.[A-Z]$")


def _norm_base(ticker) -> str:
    """Existing normalisation: trailing dots stripped, spaces and inner dots -> '-'."""
    base = str(ticker or "").strip().rstrip(".").upper()
    return base.replace(" ", "-").replace(".", "-")


# ---------------------------------------------------------------------------
# GBX / pence normalization (decision #2)
# ---------------------------------------------------------------------------
def to_major_units(series: pd.Series, currency: Optional[str]) -> pd.Series:
    """Divide price-derived figures by 100 when the quote currency is GBp/GBX."""
    if currency in ("GBp", "GBX"):
        return series / 100.0
    return series


def iso_currency(currency: Optional[str]) -> Optional[str]:
    """Map a yfinance price currency to an ISO-4217 code (GBp/GBX -> GBP)."""
    if currency in ("GBp", "GBX"):
        return "GBP"
    return currency


# ---------------------------------------------------------------------------
# Cross-check guard (decision #4)
# ---------------------------------------------------------------------------
def cross_check(yf_currency: Optional[str], yf_ret_1y: Optional[float],
                yf_price: Optional[float], paste_currency: Optional[str],
                paste_ret_1y: Optional[float], paste_price: Optional[float],
                ) -> tuple[bool, Optional[str]]:
    """Return (ok, reason). ok=False means blank the whole row and log it."""
    yc, pc = iso_currency(yf_currency), (paste_currency or None)
    if _CCY_STRICT and yc and pc and yc.upper() != pc.upper():
        return False, f"currency mismatch yf={yc} paste={pc}"
    if yf_ret_1y is not None and paste_ret_1y is not None:
        if abs(yf_ret_1y - paste_ret_1y) > _RET_TOL:
            return False, (f"1y-return mismatch yf={yf_ret_1y:+.1%} "
                           f"paste={paste_ret_1y:+.1%}")
    if yf_price and paste_price:
        rel = abs(yf_price / paste_price - 1.0)
        if rel > _PX_REL_TOL:
            return False, (f"price mismatch yf={yf_price:.4g} "
                           f"paste={paste_price:.4g} ({rel:.0%})")
    return True, None


# ---------------------------------------------------------------------------
# Pure metric functions (unit-testable; blank == None, never 0)
# ---------------------------------------------------------------------------
def _clean_close(close) -> Optional[pd.Series]:
    s = pd.Series(close).astype(float).dropna()
    return s if len(s) else None


def price_return(close, lookback: int) -> Optional[float]:
    """Price return over ``lookback`` trading rows: close[-1]/close[-1-lookback]-1."""
    s = _clean_close(close)
    if s is None or len(s) <= lookback:
        return None
    now, then = s.iloc[-1], s.iloc[-1 - lookback]
    if then == 0 or pd.isna(then) or pd.isna(now):
        return None
    return float(now / then - 1.0)


def max_drawdown(close) -> Optional[float]:
    """Peak-to-trough on the given (already 3y-sliced) close series. Negative."""
    s = _clean_close(close)
    if s is None or len(s) < 2:
        return None
    dd = (s / s.cummax() - 1.0).min()
    return float(dd) if pd.notna(dd) else None


def ma_distance(close, window: int) -> Optional[float]:
    """(price / SMA(window)) - 1 using the latest bar. None if too little data."""
    s = _clean_close(close)
    if s is None or len(s) < window:
        return None
    ma = s.rolling(window).mean().iloc[-1]
    if pd.isna(ma) or ma == 0:
        return None
    return float(s.iloc[-1] / ma - 1.0)


def dist_from_high(close, window: int = 252) -> Optional[float]:
    s = _clean_close(close)
    if s is None:
        return None
    hi = s.tail(window).max()
    if pd.isna(hi) or hi == 0:
        return None
    return float(s.iloc[-1] / hi - 1.0)


def dist_from_low(close, window: int = 252) -> Optional[float]:
    s = _clean_close(close)
    if s is None:
        return None
    lo = s.tail(window).min()
    if pd.isna(lo) or lo == 0:
        return None
    return float(s.iloc[-1] / lo - 1.0)


def wilder_rsi(close, period: int = 14) -> Optional[float]:
    """Classic Wilder RSI of the last bar (SMA seed + Wilder smoothing).

    Implemented locally so the skill stays portable (decision #9); do NOT import
    from trading_dashboard.
    """
    s = _clean_close(close)
    if s is None or len(s) < period + 1:
        return None
    delta = s.diff().dropna()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = float(gain.iloc[:period].mean())
    avg_loss = float(loss.iloc[:period].mean())
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + float(gain.iloc[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(loss.iloc[i])) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def median_dollar_volume(close, volume, fx_rate: Optional[float],
                         window: int = 30) -> Optional[float]:
    """MEDIAN of close*volume over the last ``window`` bars, converted to EUR.

    ``close`` must already be in major units; ``fx_rate`` is LOCAL units per EUR.
    Returns absolute EUR (caller scales to millions).
    """
    if fx_rate is None or fx_rate == 0:
        return None
    c = _clean_close(close)
    v = pd.Series(volume).astype(float)
    if c is None or not len(v):
        return None
    df = pd.concat([c, v], axis=1).dropna()
    if not len(df):
        return None
    turn = (df.iloc[:, 0] * df.iloc[:, 1]).tail(window)
    med = turn.median()
    if pd.isna(med):
        return None
    return float(med / fx_rate)


def pe_trailing(info: dict) -> Optional[float]:
    eps, pe = info.get("trailingEps"), info.get("trailingPE")
    if eps is not None and eps > 0 and pe is not None and pe > 0:
        return float(pe)
    return None


def pe_forward(info: dict) -> Optional[float]:
    eps, pe = info.get("forwardEps"), info.get("forwardPE")
    if eps is not None and eps > 0 and pe is not None and pe > 0:
        return float(pe)
    return None


def ps_ratio(info: dict) -> Optional[float]:
    v = info.get("priceToSalesTrailing12Months")
    return float(v) if v is not None and v > 0 else None


def ev_ebitda(info: dict) -> Optional[float]:
    v = info.get("enterpriseToEbitda")
    return float(v) if v is not None and v > 0 else None


def p_fcf(info: dict) -> Optional[float]:
    mc, fcf = info.get("marketCap"), info.get("freeCashflow")
    if mc is not None and fcf is not None and fcf > 0:
        return float(mc / fcf)
    return None


def ev_sales(info: dict) -> Optional[float]:
    v = info.get("enterpriseToRevenue")
    return float(v) if v is not None and v > 0 else None


def ev_fcf(info: dict) -> Optional[float]:
    ev, fcf = info.get("enterpriseValue"), info.get("freeCashflow")
    if ev is not None and ev > 0 and fcf is not None and fcf > 0:
        return float(ev / fcf)
    return None


def pb_ratio(info: dict) -> Optional[float]:
    v = info.get("priceToBook")
    try:
        return float(v) if v is not None and float(v) > 0 else None
    except (TypeError, ValueError):
        return None


def roe(info: dict) -> Optional[float]:
    v = info.get("returnOnEquity")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pos_or_any(v) -> Optional[float]:
    """Finite float or None (sign kept)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def consensus_fields(info: dict, raw_ccy: Optional[str]) -> dict:
    """Analyst consensus from Yahoo. Targets converted to major units (GBp -> GBP)
    so they compare with yf_price. Missing values stay None."""
    div = 100.0 if raw_ccy in ("GBp", "GBX") else 1.0

    def tgt(k):
        v = _pos_or_any(info.get(k))
        return v / div if v is not None and v > 0 else None

    n = info.get("numberOfAnalystOpinions")
    key = info.get("recommendationKey")
    return {
        "rec_key": key if key and key != "none" else None,
        "rec_mean": _pos_or_any(info.get("recommendationMean")),
        "n_analysts": int(n) if isinstance(n, (int, float)) and n == n and n > 0 else None,
        "tgt_mean": tgt("targetMeanPrice"),
        "tgt_low": tgt("targetLowPrice"),
        "tgt_high": tgt("targetHighPrice"),
    }


def fcf_positive(info: dict) -> Optional[bool]:
    v = info.get("freeCashflow")
    if v is None:
        return None
    try:
        return bool(float(v) > 0)
    except (TypeError, ValueError):
        return None


def beta(info: dict) -> Optional[float]:
    v = info.get("beta")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def net_cash(info: dict) -> Optional[float]:
    """Total cash (incl. ST investments per Yahoo 'totalCash') minus total debt,
    in the reporting currency's absolute units. EXPERIMENTAL (decision #10)."""
    tc = info.get("totalCash")
    if tc is None:
        return None
    td = info.get("totalDebt") or 0.0
    return float(tc - td)


def runway_quarters(info: dict) -> Optional[float]:
    """(cash + ST investments) / TTM operating-cash burn, only when burn < 0.

    EXPERIMENTAL (decision #10); blank when the statement figure is missing.
    """
    tc = info.get("totalCash")
    ocf = info.get("operatingCashflow")
    if tc is None or ocf is None or ocf >= 0:
        return None
    return float(tc / abs(ocf) * 4.0)


def cap_tier(eur_cap_m: Optional[float], cfg: dict) -> Optional[str]:
    if eur_cap_m is None or (isinstance(eur_cap_m, float) and np.isnan(eur_cap_m)):
        return None
    th = cfg.get("thresholds", {})
    large = float(th.get("cap_large_eur_m", 10000))
    mid = float(th.get("cap_mid_eur_m", 2000))
    if eur_cap_m >= large:
        return "large"
    if eur_cap_m >= mid:
        return "mid"
    return "small"


# ---------------------------------------------------------------------------
# The live yfinance boundary (decision #7). Injectable for tests.
# ---------------------------------------------------------------------------
def _fetch_snapshot(symbol: str, *, period: str = "3y", throttle: float = 0.4,
                    timeout: int = 20, retries: int = 1,
                    snapshot_dir: Optional[str] = None) -> Snapshot:
    """Fetch one symbol's price history + info + cashflow, tolerant of failure.

    Throttle + one retry/backoff + per-call try/except so nothing aborts the run.
    yfinance is imported lazily so importing this module never pulls it in.
    """
    import yfinance as yf  # lazy

    snapshot_dir = snapshot_dir or os.environ.get("SWST_SNAPSHOT_DIR")
    last_err = None
    for attempt in range(retries + 1):
        try:
            time.sleep(throttle * (attempt + 1))
            tk = yf.Ticker(symbol)

            hist = pd.DataFrame(columns=["close", "volume"])
            try:
                h = tk.history(period=period, auto_adjust=True, timeout=timeout)
                if h is not None and len(h):
                    hist = pd.DataFrame(
                        {"close": h["Close"].astype(float),
                         "volume": h["Volume"].astype(float)})
                    hist.index.name = "Date"
            except Exception:
                pass

            info = {}
            try:
                info = dict(tk.info)
            except Exception:
                pass

            cal = {"earnings": None, "ex_div": None}
            try:
                c = tk.calendar or {}
                ed = c.get("Earnings Date")
                if isinstance(ed, (list, tuple)) and ed:
                    cal["earnings"] = ed[0]
                elif ed:
                    cal["earnings"] = ed
                cal["ex_div"] = c.get("Ex-Dividend Date")
            except Exception:
                pass

            cf = None
            try:
                cf = tk.cashflow
            except Exception:
                pass

            snap = Snapshot(symbol=symbol, history=hist, info=info,
                            calendar=cal, cashflow=cf,
                            ok=bool(len(hist) or info))
            if snapshot_dir:
                try:
                    snap.dump(snapshot_dir)
                except Exception:
                    pass
            return snap
        except Exception as e:  # pragma: no cover - network variability
            last_err = e
    return Snapshot(symbol=symbol, history=pd.DataFrame(columns=["close", "volume"]),
                    info={}, ok=False, calendar={"earnings": None, "ex_div": None})


# ---------------------------------------------------------------------------
# Config helpers (kept local to avoid a circular import with build_workbook)
# ---------------------------------------------------------------------------
def _load_defaults() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "config", "defaults.yaml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _fx_map(cfg: dict) -> dict:
    return {k.upper(): float(v) for k, v in cfg.get("fx_per_eur", {}).items()}


def _to_dt(d) -> Optional[_dt.datetime]:
    d = _parse_date(d)
    if d is None:
        return None
    if isinstance(d, _dt.datetime):
        return d
    return _dt.datetime(d.year, d.month, d.day)


# ---------------------------------------------------------------------------
# Per-row enrichment
# ---------------------------------------------------------------------------
def enrich_one(row: dict, snap: Snapshot, cfg: dict, fx: dict) -> dict:
    """Compute the Phase-1 fields for a single row given its snapshot.

    Pure aside from the snapshot it is handed; never raises (per-field try/except).
    Returns a dict of ENRICH_FIELDS; a failed field is None, never 0.
    """
    out = {f: None for f in ENRICH_FIELDS}
    out["_yf_symbol"] = snap.symbol

    info = snap.info or {}
    raw_ccy = info.get("currency")
    close_major = None
    volume = None
    if snap.history is not None and len(snap.history):
        try:
            close_major = to_major_units(snap.history["close"].astype(float), raw_ccy)
            volume = snap.history["volume"].astype(float)
        except Exception:
            close_major = None

    # ---- cross-check guard (blank whole row on mismatch) ----
    yf_ret_1y = price_return(close_major, 252) if close_major is not None else None
    yf_price = float(close_major.iloc[-1]) if close_major is not None and len(close_major) else None
    ok, reason = cross_check(raw_ccy, yf_ret_1y, yf_price,
                             row.get("currency"), row.get("ret_1y"),
                             row.get("price_local"))
    if not ok:
        out["_enrich_note"] = f"cross-check failed: {reason}"
        return out

    def _try(fn):
        try:
            return fn()
        except Exception:
            return None

    # ---- valuation multiples (blank on non-positive denominator) ----
    out["pe_trailing"] = _try(lambda: pe_trailing(info))
    out["pe_forward"] = _try(lambda: pe_forward(info))
    out["ps_ratio"] = _try(lambda: ps_ratio(info))
    out["ev_ebitda"] = _try(lambda: ev_ebitda(info))
    out["p_fcf"] = _try(lambda: p_fcf(info))
    out["ev_sales"] = _try(lambda: ev_sales(info))
    out["ev_fcf"] = _try(lambda: ev_fcf(info))

    # ---- trend / momentum (ratios: unit-invariant) ----
    if close_major is not None:
        out["dist_ma50"] = _try(lambda: ma_distance(close_major, 50))
        out["dist_ma200"] = _try(lambda: ma_distance(close_major, 200))
        out["pct_from_high"] = _try(lambda: dist_from_high(close_major, 252))
        out["pct_from_low"] = _try(lambda: dist_from_low(close_major, 252))
        out["mom_6m"] = _try(lambda: price_return(close_major, 126))
        out["mom_12m"] = _try(lambda: price_return(close_major, 252))
        out["max_drawdown_3y"] = _try(lambda: max_drawdown(close_major))
        out["rsi_14"] = _try(lambda: wilder_rsi(close_major, 14))

    out["beta"] = _try(lambda: beta(info))
    out["pb_ratio"] = _try(lambda: pb_ratio(info))
    out["roe"] = _try(lambda: roe(info))
    out["fcf_positive"] = _try(lambda: fcf_positive(info))
    out["yf_sector"] = info.get("sector") or None
    out["yf_industry"] = info.get("industry") or None
    # Only reached after the cross-check passed, so the name belongs to the
    # listing we actually matched. Never guessed.
    out["yf_name"] = (info.get("longName") or info.get("shortName") or None)
    out["yf_price"] = yf_price
    out.update(consensus_fields(info, raw_ccy))
    out["profit_margin"] = _try(lambda: _pos_or_any(info.get("profitMargins")))
    out["revenue_growth"] = _try(lambda: _pos_or_any(info.get("revenueGrowth")))
    out["debt_to_equity"] = _try(lambda: _pos_or_any(info.get("debtToEquity")))

    # ---- liquidity (converts local turnover to EUR) ----
    price_ccy = iso_currency(raw_ccy)
    fx_price = fx.get((price_ccy or "").upper()) if price_ccy else None
    if close_major is not None and volume is not None and fx_price:
        eur = _try(lambda: median_dollar_volume(close_major, volume, fx_price, 30))
        out["avg_dollar_vol_eur_m"] = (eur / 1e6) if eur is not None else None

    # ---- balance sheet (EXPERIMENTAL; financials are in major units already) ----
    fin_ccy = iso_currency(info.get("financialCurrency") or raw_ccy)
    fx_fin = fx.get((fin_ccy or "").upper()) if fin_ccy else None
    nc = _try(lambda: net_cash(info))
    if nc is not None and fx_fin:
        out["net_cash_eur_m"] = nc / fx_fin / 1e6
    out["runway_quarters"] = _try(lambda: runway_quarters(info))

    # ---- catalysts ----
    out["next_earnings"] = _to_dt(snap.calendar.get("earnings"))
    out["ex_div"] = _to_dt(snap.calendar.get("ex_div"))

    # ---- cap tier ----
    eur_cap = None
    cap_local, ccy = row.get("market_cap_local_m"), row.get("currency")
    if cap_local is not None and ccy and ccy.upper() in fx:
        try:
            eur_cap = float(cap_local) / fx[ccy.upper()]
        except Exception:
            eur_cap = None
    out["cap_tier"] = cap_tier(eur_cap, cfg)

    return out


# ---------------------------------------------------------------------------
# Exchange probing (EUR rows with no exchange, and "no data" rows)
# ---------------------------------------------------------------------------
EUR_VENUE_SUFFIXES = [".DE", ".F", ".PA", ".AS", ".MI", ".MC", ".BR", ".HE", ".VI",
                      ".LS", ".IR"]
MAX_PROBE_FETCHES = 6            # per row, across Search candidates and blind probes
DEFAULT_SEARCH_CACHE = os.path.join(os.path.expanduser("~"), "swst_reports", "data",
                                    "symbol_search_cache.json")
_UNSET = object()
_QUOTE_KEEP = ("symbol", "exchange", "quoteType", "currency", "shortname")


class SearchCache:
    """Cache of yfinance Search quotes keyed by ticker. It caches SEARCH RESULTS only,
    never an accept decision: every candidate is re-validated with cross_check, because
    one ticker can be two companies (SAN = Sanofi and Banco Santander)."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is None:
            self._data = {}
            if self.path and os.path.exists(self.path):
                try:
                    with open(self.path, encoding="utf-8") as fh:
                        raw = json.load(fh)
                    if isinstance(raw, dict):
                        self._data = raw
                except (OSError, ValueError):
                    self._data = {}
        return self._data

    def get(self, ticker: str) -> Optional[list]:
        ent = self._load().get(ticker)
        return ent.get("quotes") if isinstance(ent, dict) else None

    def put(self, ticker: str, quotes: list) -> None:
        data = self._load()
        data[ticker] = {"asof": _dt.date.today().isoformat(),
                        "quotes": [{k: q.get(k) for k in _QUOTE_KEEP if q.get(k) is not None}
                                   for q in quotes if isinstance(q, dict)]}
        if self.path:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
                os.replace(tmp, self.path)
            except OSError:
                pass


def _live_search(query: str, *, throttle: float = 0.4) -> list:
    """yfinance.Search boundary. Raises when Search is unavailable or fails, which the
    resolver treats as 'Search unavailable' (blind suffix probing instead)."""
    import yfinance as yf  # lazy
    time.sleep(throttle)
    return list(yf.Search(query, max_results=20).quotes)


def _split_symbol(symbol: str) -> tuple[str, str]:
    """'SAP.DE' -> ('SAP', '.DE'); 'BT-A.L' -> ('BT-A', '.L'); 'AAPL' -> ('AAPL', '')."""
    if "." not in symbol:
        return symbol.upper(), ""
    b, sfx = symbol.rsplit(".", 1)
    return b.upper(), "." + sfx.upper()


def _search_quotes(base: str, search, cache: SearchCache):
    """Cached Search results for ``base``; None when Search is unavailable/failed."""
    hit = cache.get(base)
    if hit is not None:
        return hit
    if search is None:
        return None
    try:
        quotes = [q for q in (search(base) or []) if isinstance(q, dict)]
    except Exception:
        return None
    cache.put(base, quotes)
    return cache.get(base)


def _quote_ok_type(q: dict) -> bool:
    t = q.get("quoteType")
    return t is None or str(t).upper() == "EQUITY"


def _candidates_from_quotes(quotes, base, mode, currency, exclude):
    out = []
    ccy = (iso_currency(currency) or "").upper()
    for q in quotes or []:
        sym = str(q.get("symbol") or "").upper()
        b, sfx = _split_symbol(sym)
        if not sfx or b != base or sym in exclude or not _quote_ok_type(q):
            continue
        if mode == "eu":
            if sfx not in EUR_VENUE_SUFFIXES:
                continue
        else:  # "nodata": same currency as the row, GBp counts as GBP
            qc = q.get("currency")
            if qc and ccy and (iso_currency(qc) or "").upper() != ccy:
                continue
        if sym not in out:
            out.append(sym)
    if mode == "eu":
        out.sort(key=lambda x: EUR_VENUE_SUFFIXES.index(_split_symbol(x)[1]))
    return out


def resolve_by_probe(r: dict, mode: str, fetch, cfg: dict, fx: dict, search,
                     cache: SearchCache, exclude=()):
    """Find the listing a row really belongs to.

    mode "eu":     EUR row with no exchange. Candidates: Search quotes whose symbol base
                   equals the normalised ticker and whose suffix is a EUR venue; if none of
                   them passes (or Search is unavailable) the EUR_VENUE_SUFFIXES are probed
                   blind in order. At most MAX_PROBE_FETCHES fetches.
    mode "nodata": direct symbol returned nothing. Candidates: Search quotes of the same
                   base (any venue) whose quote currency matches the row's (GBp = GBP).

    A candidate is accepted only if enrich_one's cross_check passes against the row's
    currency, ret_1y and price_local. Several pass -> smallest relative price difference,
    runner-up named in the note. Returns (symbol, vals, note) or (None, None, reason).
    """
    base = _norm_base(r.get("ticker"))
    px, ret = r.get("price_local"), r.get("ret_1y")
    has_px = px is not None and not (isinstance(px, float) and np.isnan(px))
    has_ret = ret is not None and not (isinstance(ret, float) and np.isnan(ret))
    fail = ("no EUR listing matched price/1Y" if mode == "eu"
            else "no alternative listing matched price/1Y")
    if not base or not (has_px or has_ret):
        # Currency alone is no evidence of the right company; never accept on it.
        return None, None, fail
    exclude = {str(x).upper() for x in exclude}
    quotes = _search_quotes(base, search, cache)
    tried: set = set(exclude)
    passed: list = []     # (rel_diff, order, symbol, vals)
    budget = MAX_PROBE_FETCHES

    def run(cands):
        nonlocal budget
        for sym in cands:
            if budget <= 0:
                return
            if sym in tried:
                continue
            tried.add(sym)
            budget -= 1
            try:
                snap = fetch(sym)
            except Exception:
                continue
            if not getattr(snap, "ok", True):
                continue
            if (snap.history is None or len(snap.history) == 0) \
                    and not (snap.info or {}).get("currency"):
                continue
            vals = enrich_one(r, snap, cfg, fx)
            if (vals.get("_enrich_note") or "").startswith("cross-check failed"):
                continue
            yp = vals.get("yf_price")
            rel = abs(yp / px - 1.0) if (yp and has_px) else 1e9
            passed.append((rel, len(passed), sym, vals))

    run(_candidates_from_quotes(quotes, base, mode, r.get("currency"), exclude))
    if mode == "eu" and not passed:
        run([base + sfx for sfx in EUR_VENUE_SUFFIXES])
    if not passed:
        return None, None, fail
    passed.sort(key=lambda t: (t[0], t[1]))
    _, _, sym, vals = passed[0]
    note = f"resolved by exchange probe to {sym}"
    if len(passed) > 1:
        note += f" (runner-up {passed[1][2]})"
    return sym, vals, note


def enrich_rows(df: pd.DataFrame, cfg: Optional[dict] = None,
                fetch: Callable[[str], Snapshot] = _fetch_snapshot,
                logger=None, search=_UNSET,
                search_cache: Optional[SearchCache] = None) -> pd.DataFrame:
    """Add the Phase-1 enrichment columns to the MERGED frame.

    Contract (decision #8): does NOT mutate ret_1y/ret_7d/currency/
    market_cap_local_m and does NOT sort. Returns a new frame with ENRICH_FIELDS
    appended. A failed ticker or field is blank (None), never 0, never aborts.

    EUR rows with no exchange ("eu") and rows whose direct symbol had "no data" go
    through resolve_by_probe. ``search`` is the injectable yfinance.Search boundary
    (query -> list of quote dicts): by default it is the live one only when ``fetch`` is
    the live fetch too; with an injected fetch and no search, blind suffix probing runs
    through that fetch (so offline tests never touch the network).
    """
    cfg = cfg or _load_defaults()
    fx = _fx_map(cfg)
    out = df.copy()
    for f in ENRICH_FIELDS:
        out[f] = None
    out[list(NUMERIC_INPUT_FIELDS)] = out[list(NUMERIC_INPUT_FIELDS)].astype(object)

    live = fetch is _fetch_snapshot
    if search is _UNSET:
        search = _live_search if live else None
    if search_cache is None:
        search_cache = SearchCache(DEFAULT_SEARCH_CACHE if live else None)

    log = logger if logger is not None else (lambda m: None)
    coverage: dict = {"unresolved": [], "cross_check_failed": [], "fetch_failed": [],
                      "probed": 0, "resolved_by_probe": 0}

    def probe(idx, r, mode, exclude=()) -> bool:
        coverage["probed"] += 1
        psym, vals, note = resolve_by_probe(r, mode, fetch, cfg, fx, search,
                                            search_cache, exclude)
        if psym is None:
            log(f"[probe-failed] {r.get('ticker')} ({mode}): {note}")
            return False
        coverage["resolved_by_probe"] += 1
        for f in ENRICH_FIELDS:
            out.at[idx, f] = vals[f]
        out.at[idx, "_probe_note"] = note
        log(f"[probe] {r.get('ticker')} -> {psym}")
        return True

    for idx, row in df.iterrows():
        r = row.to_dict()
        sym = map_symbol(r.get("ticker"), r.get("country"), r.get("currency"),
                         cfg.get("symbol_overrides"))
        if sym is None:
            if (str(r.get("country") or "").lower() == "eu" and _norm_base(r.get("ticker"))):
                if probe(idx, r, "eu"):
                    continue
                coverage["unresolved"].append(r.get("ticker"))
                out.at[idx, "_enrich_note"] = "no EUR listing matched price/1Y"
                continue
            coverage["unresolved"].append(r.get("ticker"))
            out.at[idx, "_enrich_note"] = "unresolved ticker (no yfinance symbol)"
            log(f"[unresolved] {r.get('ticker')} country={r.get('country')}")
            continue
        try:
            snap = fetch(sym)
        except Exception as e:  # pragma: no cover
            coverage["fetch_failed"].append(sym)
            out.at[idx, "_enrich_note"] = f"fetch failed: {e}"
            log(f"[fetch-failed] {sym}: {e}")
            continue
        if not getattr(snap, "ok", True):
            coverage["fetch_failed"].append(sym)
            out.at[idx, "_enrich_note"] = "fetch returned nothing"
            log(f"[fetch-empty] {sym}")
            continue
        no_hist = snap.history is None or len(snap.history) == 0
        if no_hist and not (snap.info or {}).get("currency"):
            # Yahoo answered but knows nothing: wrong symbol, delisted, or listed on
            # another exchange than the currency suggests. Try the other listings of this
            # ticker; if none matches, say so (never a silent blank).
            if probe(idx, r, "nodata", exclude=(sym,)):
                continue
            coverage["fetch_failed"].append(sym)
            out.at[idx, "_enrich_note"] = (f"no data from Yahoo for {sym} (not found; it may "
                                           "trade on another exchange)")
            log(f"[no-data] {sym}")
            continue

        vals = enrich_one(r, snap, cfg, fx)
        if (vals.get("_enrich_note") or "").startswith("cross-check failed"):
            coverage["cross_check_failed"].append((sym, vals["_enrich_note"]))
            log(f"[cross-check] {sym}: {vals['_enrich_note']}")
        for f in ENRICH_FIELDS:
            out.at[idx, f] = vals[f]

    out.attrs["enrich_coverage"] = coverage
    return out


def coverage_counts(df: pd.DataFrame) -> dict:
    """Populated-vs-blank count per enrichment field across all rows."""
    n = len(df)
    counts = {}
    for f in NUMERIC_INPUT_FIELDS + DATE_FIELDS + ["cap_tier"]:
        if f not in df.columns:
            counts[f] = (0, n)
            continue
        pop = int(df[f].apply(
            lambda v: v is not None and not (isinstance(v, float) and pd.isna(v))).sum())
        counts[f] = (pop, n - pop)
    return counts


__all__ = [
    "Snapshot", "load_snapshot", "_fetch_snapshot", "enrich_rows", "enrich_one",
    "map_symbol", "resolve_by_probe", "SearchCache", "EUR_VENUE_SUFFIXES",
    "to_major_units", "iso_currency", "cross_check",
    "price_return", "max_drawdown", "ma_distance", "dist_from_high",
    "dist_from_low", "wilder_rsi", "median_dollar_volume",
    "pe_trailing", "pe_forward", "ps_ratio", "ev_ebitda", "p_fcf", "ev_sales", "ev_fcf", "beta",
    "pb_ratio", "roe", "fcf_positive",
    "net_cash", "runway_quarters", "cap_tier", "coverage_counts",
    "NUMERIC_INPUT_FIELDS", "DATE_FIELDS", "ENRICH_FIELDS",
]
