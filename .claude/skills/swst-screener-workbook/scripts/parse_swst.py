"""Parse Simply Wall St "Investing Ideas" screener pastes into a normalized frame.

Three paste shapes are supported (see references/formats.md):

  1. GLOBAL_TEXT   - plain-text page, repeating blocks of company/market cap/
                     description/ticker/price/7D/1Y.
  2. COUNTRY_MD    - markdown with links; the stock-detail URLs encode
                     country / sector / exchange / ticker.
  3. US_TABLE      - a column table (Company/Last Price/7D/1Y/Market Cap/
                     Analysts Target/Valuation/Growth/Div Yield/Industry) with
                     tickers only, no company names.

Design rules baked in here (each came from a real mistake):
  * Never invent a company name. Missing name -> None (the builder renders
    "<TICKER> (name not in paste)").
  * "n/a" is None, never 0.
  * Percentages are stored as fractions (+1.2% -> 0.012).
  * Market caps are stored in MILLIONS of local currency
    (t=1e6 m, b=1e3 m, m=1 m).
  * The page's own header count is captured separately so callers can compare
    it against the number of rows actually parsed and warn on a gap.

The public entry points are:
  parse_paste(text, investing_idea=None, source=None)  -> ParsedSource
  parse_money(token)                                    -> (currency, millions)
  parse_pct(token)                                      -> float | None
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Currency symbols. ORDER MATTERS: longest / most specific first so that
# "US$" wins over "$", "R$" over "R", "CN¥" over "¥", "NT$"/"HK$"/"S$" over "$".
# Values are ISO-4217 codes used as the FX-table key.
# ---------------------------------------------------------------------------
CURRENCY_SYMBOLS: list[tuple[str, str]] = [
    ("د.إ", "AED"),
    ("د.أ", "JOD"),
    ("د.م", "MAD"),
    ("ر.ع", "OMR"),
    ("ر.س", "SAR"),
    ("ج.م", "EGP"),
    ("LK₨", "LKR"),
    ("PK₨", "PKR"),
    ("Mex$", "MXN"),
    ("KSh", "KES"),     # before the bare "K" (ZMW) below
    ("TSh", "TZS"),
    ("XOF", "XOF"),
    ("AR$", "ARS"),
    ("US$", "USD"),
    ("CA$", "CAD"),
    ("AU$", "AUD"),
    ("NZ$", "NZD"),
    ("NT$", "TWD"),
    ("HK$", "HKD"),
    ("CN¥", "CNY"),
    ("JP¥", "JPY"),
    ("UK£", "GBP"),
    ("R$", "BRL"),
    ("S$", "SGD"),
    ("RM", "MYR"),
    ("Rp", "IDR"),
    ("zł", "PLN"),
    ("SEK", "SEK"),
    ("NOK", "NOK"),
    ("DKK", "DKK"),
    ("CHF", "CHF"),
    ("₽", "RUB"),
    ("৳", "BDT"),
    ("₦", "NGN"),
    ("₩", "KRW"),
    ("₹", "INR"),
    ("₫", "VND"),
    ("€", "EUR"),
    ("₱", "PHP"),
    ("฿", "THB"),
    ("₺", "TRY"),
    ("₪", "ILS"),
    ("Ft", "HUF"),
    ("K", "ZMW"),       # AFTER "KSh"
    ("R", "ZAR"),
    # Bare fallbacks, matched only after every multi-char symbol above.
    ("£", "GBP"),
    ("$", "USD"),
    ("¥", "JPY"),
]

# Suffix -> multiplier expressed in MILLIONS of local currency.
SUFFIX_TO_MILLIONS = {"t": 1_000_000.0, "b": 1_000.0, "m": 1.0, "k": 0.001, "": 1.0}

_NUM = r"[-+]?\d[\d,]*(?:\.\d+)?"

# ---------------------------------------------------------------------------
# Normalized schema
# ---------------------------------------------------------------------------
CANONICAL_FIELDS = [
    "company",          # str | None  (None == not in paste; never invented)
    "ticker",           # str         (bare, upper-cased symbol, no exchange)
    "exchange",         # str | None  (e.g. LSE, AIM, ETR, EPA, CPH, NasdaqGS)
    "country",          # str | None  (ISO alpha-2, lower, from URL when known)
    "sector",           # str | None  (from URL slug when known)
    "currency",         # str | None  (ISO-4217)
    "market_cap_local_m",  # float | None  (millions of local currency)
    "price_local",      # float | None
    "ret_7d",           # float | None  (fraction)
    "ret_1y",           # float | None  (fraction)
    "analyst_target",   # float | None  (US table only)
    "upside_to_target", # float | None  (left None here; builder uses a formula)
    "valuation_sws",    # float | None  (US table only; NOT a uniform P/E)
    "growth_sws",       # float | None  (US table only; fraction)
    "div_yield",        # float | None  (US table only; fraction)
    "industry",         # str | None    (US table only)
    "business",         # str | None    (description text)
    "new_on_screener",  # bool
    "investing_idea",   # str | None
    "source",           # str
    "paste_format",     # str
]


@dataclass
class ParsedSource:
    """Rows from one paste plus the metadata needed to warn about gaps."""
    paste_format: str
    investing_idea: Optional[str]
    source: str
    header_count: Optional[int]          # what the page claimed ("N companies")
    rows: list[dict] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    title: Optional[str] = None          # investing-idea name grabbed from the paste

    @property
    def parsed_count(self) -> int:
        return len(self.rows)

    def to_frame(self) -> pd.DataFrame:
        # Extra keys on a row (e.g. a pre-resolved "_yf_symbol") are kept after the
        # canonical columns instead of being silently dropped.
        extras = sorted({k for r in self.rows for k in r} - set(CANONICAL_FIELDS))
        return pd.DataFrame(self.rows, columns=CANONICAL_FIELDS + extras)


# ---------------------------------------------------------------------------
# Scalar parsers
# ---------------------------------------------------------------------------
def parse_money(token: str) -> tuple[Optional[str], Optional[float]]:
    """'US$1.2b' -> ('USD', 1200.0)  (millions).  'n/a' -> (None, None)."""
    if token is None:
        return None, None
    s = token.strip()
    if s == "" or s.lower() in {"n/a", "na", "-", "—"}:
        return None, None
    # Strip a leading label like "Market Cap:".
    s = re.sub(r"(?i)^\s*market\s*cap\s*:?", "", s).strip()
    currency = None
    for sym, code in CURRENCY_SYMBOLS:
        if s.startswith(sym):
            currency = code
            s = s[len(sym):].strip()
            break
    m = re.match(rf"({_NUM})\s*([tbmk])?\b", s, flags=re.IGNORECASE)
    if not m:
        if currency is None:
            u = _unknown_symbol(s)
            if u:
                return UNKNOWN, u[1] * SUFFIX_TO_MILLIONS.get(u[2], 1.0)
        return currency, None
    value = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    millions = value * SUFFIX_TO_MILLIONS.get(suffix, 1.0)
    return currency, millions


# A whole token that is a short unfamiliar prefix + a number (+ optional scale),
# e.g. a currency symbol we have not met yet. Strict full match, so free text
# like "Level 5, 320 Pitt Street" never qualifies.
_UNKNOWN_CCY = re.compile(rf"^([^\d\s+\-.,%][^\d\s]{{0,4}})\s?({_NUM})\s*([tbmk])?$", re.I)
UNKNOWN = "UNKNOWN"


def _unknown_symbol(s: str):
    m = _UNKNOWN_CCY.match(s)
    if not m:
        return None
    return m.group(1), float(m.group(2).replace(",", "")), (m.group(3) or "").lower()


def parse_price(token: str) -> tuple[Optional[str], Optional[float]]:
    """Like parse_money but no scale suffix; keeps the raw number."""
    if token is None:
        return None, None
    s = token.strip()
    if s == "" or s.lower() in {"n/a", "na", "-", "—"}:
        return None, None
    currency = None
    for sym, code in CURRENCY_SYMBOLS:
        if s.startswith(sym):
            currency = code
            s = s[len(sym):].strip()
            break
    m = re.match(rf"({_NUM})", s)
    if not m:
        if currency is None:
            u = _unknown_symbol(s)
            if u and not u[2]:
                return UNKNOWN, u[1]
        return currency, None
    return currency, float(m.group(1).replace(",", ""))


def parse_pct(token: str) -> Optional[float]:
    """'+1.2%' -> 0.012 ; '-15.3%' -> -0.153 ; 'n/a' -> None."""
    if token is None:
        return None
    s = token.strip()
    if s == "" or s.lower() in {"n/a", "na", "-", "—"}:
        return None
    m = re.match(rf"({_NUM})\s*%?", s)
    if not m:
        return None
    return float(m.group(1).replace(",", "")) / 100.0


def _clean_ticker(raw: str) -> tuple[Optional[str], str]:
    """'NasdaqGS:ABCD' -> ('NasdaqGS', 'ABCD'); 'ABCD' -> (None, 'ABCD')."""
    raw = raw.strip()
    if ":" in raw:
        exch, tick = raw.split(":", 1)
        return exch.strip() or None, tick.strip().upper()
    return None, raw.strip().upper()


def _blank_row(**over) -> dict:
    row = {k: None for k in CANONICAL_FIELDS}
    row["new_on_screener"] = False
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# Header count
# ---------------------------------------------------------------------------
_HEADER_PATTERNS = [
    re.compile(r"(\d[\d,]*)\s+companies", re.IGNORECASE),
    re.compile(r"Results\s+(\d[\d,]*)", re.IGNORECASE),
]


def extract_header_count(text: str) -> Optional[int]:
    for pat in _HEADER_PATTERNS:
        m = pat.search(text)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


# Lines that are page chrome, not the idea title.
_TITLE_SKIP = re.compile(
    r"(?i)^\s*(results\b|\d[\d,]*\s+companies\b|market\s*cap|7d|1y|new|n/a|"
    r"sort|filter|showing|page\b)")


# Breadcrumb / navigation chrome that sits around the real idea title.
_BREADCRUMB = {
    "investing ideas", "home", "stocks", "screener", "stock screener",
    "stock screener & alerts", "more ideas",
}
_ANCHOR = re.compile(
    r"(?i)^\s*(results\b|\d[\d,]*\s+companies\b)|last\s*price.*market\s*cap")


def extract_title(text: str) -> Optional[str]:
    """Grab the investing-idea name (page title) from a paste.

    SWS pastes wrap the title in breadcrumb chrome, e.g.

        Investing Ideas
        ›
        Undervalued Healthcare Stocks
        Undervalued Healthcare Stocks
        Results
        27

    The reliable anchor is the "Results" / "N companies" / table-header line: the
    idea name is the last meaningful, non-breadcrumb line just ABOVE it (often
    duplicated). If nothing usable precedes the anchor, there is no title.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    anchor = next((i for i, ln in enumerate(lines) if ln and _ANCHOR.match(ln)), None)
    scan = range(anchor - 1, -1, -1) if anchor is not None else range(len(lines))
    for i in scan:
        line = lines[i]
        if not line:
            continue
        m = _MD_LINK.fullmatch(line)
        if m:
            line = m.group(1).strip()
        if _is_breadcrumb(line):
            continue
        if _title_ok(line):
            return line
    return None


def _is_breadcrumb(line: str) -> bool:
    s = line.strip().lower()
    if s in _BREADCRUMB:
        return True
    # a bare separator glyph (›, >, /, |, •)
    return bool(re.fullmatch(r"[›>/|•\-\s]+", line.strip()))


def _title_ok(line: str) -> bool:
    # A title is prose, not a price/percent/ticker/count token.
    if _TITLE_SKIP.match(line):
        return False
    if _TICKER_LINE.match(line):
        return False
    if re.fullmatch(rf"[^\w]*{_NUM}\s*[tbmk%]?", line):
        return False
    return len(line) >= 3


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------
_TICKER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9 .]{0,14}:[A-Z0-9.]{1,12}$")
_STOCK_URL = re.compile(
    r"/stocks/([a-z]{2})/([a-z0-9-]+)/([a-z0-9]+)-([a-z0-9.]+)", re.IGNORECASE
)
_US_TABLE_HEADER = re.compile(
    r"company.*last\s*price.*7d.*1y.*market\s*cap", re.IGNORECASE | re.DOTALL
)


def detect_format(text: str) -> str:
    if _STOCK_URL.search(text):
        return "COUNTRY_MD"
    # A US table: a header row naming the columns, laid out as a pipe or
    # tab/multi-space table.
    first_chunk = "\n".join(text.splitlines()[:40])
    if _US_TABLE_HEADER.search(first_chunk.replace("|", " ")):
        return "US_TABLE"
    return "GLOBAL_TEXT"


# ---------------------------------------------------------------------------
# Format 1: GLOBAL_TEXT
# ---------------------------------------------------------------------------
def parse_global_text(
    text: str, investing_idea: Optional[str], source: str
) -> ParsedSource:
    header = extract_header_count(text)
    lines = [ln.strip() for ln in text.splitlines()]
    # Index of every "Market Cap:" line -> anchors a company block.
    cap_idx = [i for i, ln in enumerate(lines) if re.match(r"(?i)^market\s*cap\s*:", ln)]
    rows: list[dict] = []
    for k, ci in enumerate(cap_idx):
        # Company name: nearest non-empty line above the market-cap line.
        company = None
        j = ci - 1
        while j >= 0:
            if lines[j]:
                company = lines[j]
                break
            j -= 1
        currency, cap_m = parse_money(lines[ci])

        # Block body runs from just after the cap line to just before the next
        # block's company line (or EOF).
        end = cap_idx[k + 1] - 1 if k + 1 < len(cap_idx) else len(lines)
        # back off one for the next company's name line
        if k + 1 < len(cap_idx):
            nb = cap_idx[k + 1] - 1
            while nb > ci and not lines[nb]:
                nb -= 1
            end = nb  # stop before the next company name
        body = lines[ci + 1:end]
        row = _parse_global_block(body)
        row["company"] = company
        row["currency"] = row["currency"] or currency
        row["market_cap_local_m"] = cap_m
        row["investing_idea"] = investing_idea
        row["source"] = source
        row["paste_format"] = "GLOBAL_TEXT"
        if row["ticker"]:
            rows.append(row)

    countries = sorted({r["country"] for r in rows if r["country"]})
    return ParsedSource(
        paste_format="GLOBAL_TEXT",
        investing_idea=investing_idea,
        source=source,
        header_count=header,
        rows=rows,
        countries=countries,
    )


def _parse_global_block(body: list[str]) -> dict:
    """Given the lines of one company block (after the Market Cap line),
    extract description / New / ticker / price / 7D / 1Y."""
    row = _blank_row()
    # Locate the ticker line first: it splits description from the numbers.
    tick_i = None
    for i, ln in enumerate(body):
        if _TICKER_LINE.match(ln):
            tick_i = i
            break
    desc_lines = []
    if tick_i is not None:
        for ln in body[:tick_i]:
            if ln.lower() == "new":
                row["new_on_screener"] = True
            elif ln:
                desc_lines.append(ln)
        exch, tick = _clean_ticker(body[tick_i])
        row["exchange"] = exch
        row["ticker"] = tick
        tail = body[tick_i + 1:]
    else:
        # No exchange:ticker line. Fall back: treat a lone all-caps token as
        # the ticker; everything before it is description.
        tail = body
    row["business"] = " ".join(desc_lines).strip() or None

    # Walk the tail for price, then the 7D / 1Y anchored values.
    _fill_price_and_returns(row, tail)
    return row


def _fill_price_and_returns(row: dict, tail: list[str]) -> None:
    # Price: first currency-prefixed number in the tail.
    for i, ln in enumerate(tail):
        cur, px = parse_price(ln)
        if px is not None and (cur is not None or re.match(rf"^{_NUM}$", ln)):
            row["price_local"] = px
            if cur:
                row["currency"] = row["currency"] or cur
            break
    # Returns: value on the line AFTER the "7D" / "1Y" anchors.
    for i, ln in enumerate(tail):
        low = ln.lower()
        if low == "7d" and i + 1 < len(tail):
            row["ret_7d"] = parse_pct(tail[i + 1])
        elif low == "1y" and i + 1 < len(tail):
            row["ret_1y"] = parse_pct(tail[i + 1])


# ---------------------------------------------------------------------------
# Format 2: COUNTRY_MD (markdown with links whose URLs encode geography)
# ---------------------------------------------------------------------------
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def parse_country_md(
    text: str, investing_idea: Optional[str], source: str
) -> ParsedSource:
    header = extract_header_count(text)

    # Map ticker -> (country, sector, exchange, name) from stock-detail URLs.
    url_meta: dict[str, dict] = {}
    for label, url in _MD_LINK.findall(text):
        m = _STOCK_URL.search(url)
        if m:
            country, sector, exch, tick = m.groups()
            tick = tick.upper()
            meta = url_meta.setdefault(
                tick,
                {"country": country.lower(), "sector": sector.lower(),
                 "exchange": exch.upper(), "name": None},
            )
            # The company-name link tends to carry the human name as its label
            # and is usually the first/most descriptive; keep the longest label
            # that is not a bare price/percent/marketcap token.
            if label and not _looks_numeric(label):
                if meta["name"] is None or len(label) > len(meta["name"]):
                    meta["name"] = label

    # Strip markdown links to their visible text, then reuse the global parser.
    plain = _MD_LINK.sub(lambda m: m.group(1), text)
    base = parse_global_text(plain, investing_idea, source)

    for row in base.rows:
        meta = url_meta.get(row["ticker"])
        if meta:
            row["country"] = meta["country"]
            row["sector"] = meta["sector"]
            row["exchange"] = row["exchange"] or meta["exchange"]
            if not row["company"] and meta["name"]:
                row["company"] = meta["name"]
        row["paste_format"] = "COUNTRY_MD"

    countries = sorted({r["country"] for r in base.rows if r["country"]})
    return ParsedSource(
        paste_format="COUNTRY_MD",
        investing_idea=investing_idea,
        source=source,
        header_count=header,
        rows=base.rows,
        countries=countries,
    )


def _looks_numeric(label: str) -> bool:
    s = label.strip()
    return bool(
        re.match(r"(?i)^(market\s*cap|7d|1y|n/a)$", s)
        or re.match(rf"^[^\w]*{_NUM}\s*[tbmk%]?\s*$", s)
    )


# ---------------------------------------------------------------------------
# Format 3: US_TABLE  (a.k.a. the "table view" — it is NOT always US-only)
# ---------------------------------------------------------------------------
US_COLUMNS = [
    "company", "last_price", "7d", "1y", "market_cap",
    "analyst_target", "valuation", "growth", "div_yield", "industry",
]

# The table view carries no exchange/URL, only a currency symbol, so the listing
# country is inferred from the currency. Only unambiguous single-country
# currencies are mapped; ambiguous ones (EUR) stay None and are reported.
CURRENCY_COUNTRY = {
    "USD": "us", "GBP": "gb", "CAD": "ca", "AUD": "au", "NZD": "nz", "JPY": "jp",
    "KRW": "kr", "TWD": "tw", "HKD": "hk", "CNY": "cn", "INR": "in",
    "IDR": "id", "PHP": "ph", "THB": "th", "VND": "vn", "MYR": "my",
    "ZAR": "za", "BRL": "br", "SGD": "sg", "PLN": "pl", "SEK": "se",
    "NOK": "no", "DKK": "dk", "CHF": "ch", "TRY": "tr", "AED": "ae",
    "SAR": "sa", "EGP": "eg", "ARS": "ar", "RUB": "ru", "BDT": "bd", "NGN": "ng",
    "ILS": "il", "HUF": "hu", "JOD": "jo", "ZMW": "zm", "LKR": "lk", "PKR": "pk",
    "TZS": "tz", "OMR": "om", "MXN": "mx", "KES": "ke", "MAD": "ma",
    "XOF": None,   # CFA franc: shared by several countries, so no single country
    # EUR is a shared currency: the table view carries no exchange, so the exact
    # country is unknown. Map to a "eu" pseudo-code (Eurozone, country unspecified)
    # so Europe region filters can keep these rows while the report flags them.
    "EUR": "eu",
}

_TICKER_ONLY = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
_US_TICKER = re.compile(r"^[A-Z0-9.&* \-]{1,16}$")


def parse_us_table(
    text: str, investing_idea: Optional[str], source: str
) -> ParsedSource:
    header = extract_header_count(text)
    # Pipe / aligned tables (one row per line) vs the vertical stream SWS pastes.
    if any("|" in ln for ln in text.splitlines()):
        records = _split_table_rows(text)
    else:
        records = _vertical_records(text, header)

    rows: list[dict] = []
    inferred = False
    dropped: list[str] = []
    for cells, is_new in records:
        if not cells:
            dropped.append("dropped <empty>: record had no cells")
            continue
        exch, tick = _clean_ticker(cells[0])
        # Allow spaces for share-class tickers (e.g. "SEB A", "ZZ B"), pure digits for
        # Asian listings (e.g. "1288", "600519"), and & - * (NAM-INDIA, PE&OLES *, TL-R,
        # J&KBANK). A record that still fails is NEVER dropped silently: it is noted.
        if not tick or not _US_TICKER.match(tick):
            dropped.append(f"dropped {cells[0]}: ticker not recognised "
                           f"(allowed: A-Z 0-9 . space & - *, 1 to 16 chars)")
            continue
        cur_px, price = parse_price(_get(cells, 1))
        cur_cap, cap_m = parse_money(_get(cells, 4))
        cur_tgt, target = parse_price(_get(cells, 5))
        currency = cur_px or cur_cap or cur_tgt or "USD"
        country = CURRENCY_COUNTRY.get(currency)
        if country is None:
            inferred = True  # currency did not resolve to a single country
        row = _blank_row(
            company=None,                       # table view has no names
            ticker=tick,
            exchange=exch,
            country=country,
            currency=currency,
            price_local=price,
            ret_7d=parse_pct(_get(cells, 2)),
            ret_1y=parse_pct(_get(cells, 3)),
            market_cap_local_m=cap_m,
            analyst_target=target,
            valuation_sws=_num_or_none(_get(cells, 6)),
            growth_sws=parse_pct(_get(cells, 7)),
            div_yield=parse_pct(_get(cells, 8)),
            industry=_text_or_none(_get(cells, 9)),
            new_on_screener=bool(is_new),
            investing_idea=investing_idea,
            source=source,
            paste_format="US_TABLE",
        )
        rows.append(row)

    notes = list(dropped)
    if any(r["country"] is None for r in rows) or inferred:
        notes.append("Some table-view rows had an ambiguous currency; their "
                     "country is blank (the table view carries no exchange/URL).")
    notes.append("Country in the table view is inferred from the currency "
                 "(USD->us, UK£->gb, ...), used as a listing-venue proxy.")
    return ParsedSource(
        paste_format="US_TABLE",
        investing_idea=investing_idea,
        source=source,
        header_count=header,
        rows=rows,
        countries=sorted({r["country"] for r in rows if r["country"]}),
        notes=notes,
    )


def _vertical_records(text: str, header_count: Optional[int]):
    """Parse the vertical table stream: after the header line, each company is
    ticker, optional 'New', then 9 field lines. Stops when a record's price line
    is not a currency/number (i.e. we have run into the page footer)."""
    lines = [ln.strip() for ln in text.splitlines()]
    # Start right after the header row that names the columns.
    start = 0
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "last price" in low and "market cap" in low:
            start = i + 1
            break
    i = start
    out = []
    n = len(lines)
    while i < n:
        # skip blanks
        if not lines[i]:
            i += 1
            continue
        tick = lines[i]
        # A record boundary is PRICE-ANCHORED, not ticker-shape-anchored: the
        # line after the ticker (skipping an optional "New") must parse as a
        # price. This is what lets tickers be numeric (1288, 600519), spaced
        # (SEB A, ZZ B) or alphanumeric (A119610) without a shape whitelist.
        j = i + 1
        while j < n and not lines[j]:
            j += 1
        is_new = False
        if j < n and lines[j].lower() == "new":
            is_new = True
            j += 1
            while j < n and not lines[j]:
                j += 1
        cur, px = parse_price(lines[j]) if j < n else (None, None)
        price_ok = px is not None or cur is not None
        # The ticker line itself must not be a field token (a percent, an "n/a",
        # or a currency-prefixed number) — that would mean the stream drifted.
        if not price_ok or _looks_like_field(tick):
            if out:
                break  # past the last row -> footer
            i += 1     # still in the preamble -> keep scanning
            continue
        # collect the 9 field lines after the (optional New +) ticker
        fields = []
        k = j
        while k < n and len(fields) < 9:
            if lines[k] == "":
                k += 1
                continue
            fields.append(lines[k])
            k += 1
        out.append(([tick] + fields, is_new))
        i = k
        if header_count and len(out) >= header_count:
            break
    return out


def _looks_like_field(s: str) -> bool:
    """True only when a line is UNMISTAKABLY a data field, never a ticker.

    The record boundary is already price-anchored (the line after the ticker must
    be a price), so this is just a drift guard. It must not reject legitimate
    tickers: bare integers (1288, 600519) and letters that happen to start with a
    currency glyph (RY, REL -> "R" is the ZAR symbol) ARE valid tickers. So only
    percents and n/a-style tokens count as fields here."""
    s = s.strip()
    if not s:
        return True
    if s.endswith("%"):
        return True
    # Use the slash form only: bare "NA" is a real ticker (National Bank of
    # Canada), and "New" is handled separately as the new-on-screener marker.
    if s.lower() in {"n/a", "-", "—"}:
        return True
    return False
    return False


def _split_table_rows(text: str) -> list[tuple[list[str], bool]]:
    """Return (cells, is_new) for a pipe or aligned table. Skips the header row
    and any separator (---) row. is_new is True if a cell equals 'New'."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out: list[tuple[list[str], bool]] = []
    header_seen = False
    for ln in lines:
        if "|" in ln:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        else:
            # aligned columns: split on runs of 2+ spaces or tabs
            cells = [c.strip() for c in re.split(r"\t|\s{2,}", ln.strip())]
        low = " ".join(cells).lower()
        if set("".join(cells)) <= set("-: "):
            continue  # markdown separator row
        if not header_seen:
            if "last price" in low and "market cap" in low:
                header_seen = True
            continue
        is_new = any(c.lower() == "new" for c in cells)
        cells = [c for c in cells if c.lower() != "new"]
        out.append((cells, is_new))
    return out


def _get(cells: list[str], i: int) -> Optional[str]:
    return cells[i] if i < len(cells) else None


def _num_or_none(token: Optional[str]) -> Optional[float]:
    if not token:
        return None
    s = token.strip().rstrip("x")  # SWS sometimes shows "0.9x"
    if s.lower() in {"n/a", "na", "-", "—", ""}:
        return None
    m = re.match(rf"({_NUM})", s)
    return float(m.group(1).replace(",", "")) if m else None


def _text_or_none(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    s = token.strip()
    return s or None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def parse_paste(
    text: str, investing_idea: Optional[str] = None, source: Optional[str] = None
) -> ParsedSource:
    fmt = detect_format(text)
    # Grab the idea name from the paste itself. An explicit investing_idea wins
    # (caller override); otherwise the page title is used as the label.
    title = extract_title(text)
    idea = investing_idea or title
    source = source or (idea or fmt)
    if fmt == "COUNTRY_MD":
        src = parse_country_md(text, idea, source)
    elif fmt == "US_TABLE":
        src = parse_us_table(text, idea, source)
    else:
        src = parse_global_text(text, idea, source)
    src.title = title
    return src


__all__ = [
    "parse_paste", "parse_global_text", "parse_country_md", "parse_us_table",
    "parse_money", "parse_price", "parse_pct", "detect_format",
    "extract_header_count", "extract_title", "ParsedSource", "CANONICAL_FIELDS",
]
