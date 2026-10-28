"""Unit tests for the SWS screener parser.

Run:  python -m pytest tests/test_parse.py -q
from the skill directory (scripts/ must be importable).
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import parse_swst as P  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def _read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Scalar parsers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "token,cur,millions",
    [
        ("US$1.2b", "USD", 1200.0),
        ("Market Cap:US$1.2b", "USD", 1200.0),
        ("€2.5b", "EUR", 2500.0),
        ("€500.0m", "EUR", 500.0),
        ("JP¥300.0b", "JPY", 300000.0),
        ("US$1.2t", "USD", 1_200_000.0),
        ("R$800.0m", "BRL", 800.0),      # R$ must beat bare R
        ("UK£450.0m", "GBP", 450.0),
        ("NT$50.0b", "TWD", 50000.0),
        ("HK$3.0b", "HKD", 3000.0),
        ("CN¥900.0m", "CNY", 900.0),
        ("zł120.0m", "PLN", 120.0),
        ("DKK6.5b", "DKK", 6500.0),
        ("n/a", None, None),
    ],
)
def test_parse_money(token, cur, millions):
    c, m = P.parse_money(token)
    assert c == cur
    assert m == pytest.approx(millions) if millions is not None else m is None


@pytest.mark.parametrize(
    "token,frac",
    [("+1.2%", 0.012), ("-15.3%", -0.153), ("0.0%", 0.0), ("n/a", None),
     ("", None), ("+8.2%", 0.082)],
)
def test_parse_pct(token, frac):
    v = P.parse_pct(token)
    if frac is None:
        assert v is None
    else:
        assert v == pytest.approx(frac)


def test_na_is_none_never_zero():
    assert P.parse_pct("n/a") is None
    assert P.parse_money("n/a") == (None, None)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------
def test_detect_formats():
    assert P.detect_format(_read("global_plaintext.txt")) == "GLOBAL_TEXT"
    assert P.detect_format(_read("country_markdown.md")) == "COUNTRY_MD"
    assert P.detect_format(_read("us_table.md")) == "US_TABLE"


# ---------------------------------------------------------------------------
# Title / investing-idea grabbed from the paste itself
# ---------------------------------------------------------------------------
def test_title_is_grabbed_from_paste():
    g = P.parse_paste(_read("global_plaintext.txt"))
    assert g.title == "Undervalued based on future growth"
    # with no explicit label, every row inherits the grabbed title
    assert all(r["investing_idea"] == g.title for r in g.rows)

    c = P.parse_paste(_read("country_markdown.md"))
    assert c.title == "Undervalued based on future growth (Europe)"

    u = P.parse_paste(_read("us_table.md"))
    assert u.title == "Undervalued US stocks based on future growth"


def test_explicit_idea_overrides_grabbed_title():
    g = P.parse_paste(_read("global_plaintext.txt"), investing_idea="My label")
    assert g.title == "Undervalued based on future growth"   # still grabbed
    assert all(r["investing_idea"] == "My label" for r in g.rows)  # override wins


def test_title_skips_description_line_in_2026_10_layout():
    txt = ("Investing Ideas\nBrowse All Stocks\nMarkets\n\n"
           "Undiscovered Gems With Strong Fundamentals\n"
           "High-performing small cap companies that haven't yet garnered significant "
           "analyst attention, and could lead to superior returns.\n260 companies\n")
    assert P.extract_title(txt) == "Undiscovered Gems With Strong Fundamentals"


def test_title_none_when_page_starts_with_results():
    txt = "Results 2\n2 companies\nFoo Ltd\nMarket Cap:US$1.0b\nDesc\nNYSE:FOO\nUS$1.00\n7D\n+0.1%\n1Y\n+0.2%\n"
    src = P.parse_paste(txt)
    assert src.title is None


# ---------------------------------------------------------------------------
# Format 1: global plain text
# ---------------------------------------------------------------------------
def test_global_parse_counts_and_header_gap():
    src = P.parse_paste(_read("global_plaintext.txt"), investing_idea="Growth")
    assert src.paste_format == "GLOBAL_TEXT"
    assert src.parsed_count == 6           # only 6 blocks are actually present
    assert src.header_count == 8           # page CLAIMS 8 -> gap of 2 to warn on
    tickers = {r["ticker"] for r in src.rows}
    assert tickers == {"ACME", "BETA", "GAMA", "DLTA", "EPSL", "ZETA"}


def test_global_fields_and_flags_inputs():
    src = P.parse_paste(_read("global_plaintext.txt"), investing_idea="Growth")
    by = {r["ticker"]: r for r in src.rows}
    acme = by["ACME"]
    assert acme["company"] == "Acme Global Holdings"
    assert acme["exchange"] == "NasdaqGS"
    assert acme["currency"] == "USD"
    assert acme["market_cap_local_m"] == pytest.approx(1200.0)
    assert acme["price_local"] == pytest.approx(12.34)
    assert acme["ret_7d"] == pytest.approx(0.012)
    assert acme["ret_1y"] == pytest.approx(-0.153)
    assert acme["new_on_screener"] is True
    assert acme["business"].startswith("Designs and manufactures")
    # n/a -> None, never 0
    assert by["BETA"]["ret_1y"] is None
    # currency + suffix variety
    assert by["DLTA"]["currency"] == "JPY"
    assert by["DLTA"]["market_cap_local_m"] == pytest.approx(300000.0)
    assert by["EPSL"]["currency"] == "BRL"


# ---------------------------------------------------------------------------
# Format 2: country markdown (geography from URLs)
# ---------------------------------------------------------------------------
def test_country_md_extracts_geo_from_urls():
    src = P.parse_paste(_read("country_markdown.md"), investing_idea="Growth")
    assert src.paste_format == "COUNTRY_MD"
    by = {r["ticker"]: r for r in src.rows}
    assert by["POLR"]["country"] == "gb"
    assert by["POLR"]["exchange"] == "AIM"
    assert by["POLR"]["sector"] == "diversified-financials"
    assert by["1INN"]["country"] == "de"
    assert by["1INN"]["exchange"] == "ETR"
    assert by["VK"]["country"] == "fr"
    assert by["MTHH"]["country"] == "dk"
    assert by["MTHH"]["currency"] == "DKK"
    assert set(src.countries) == {"gb", "de", "fr", "dk"}


# ---------------------------------------------------------------------------
# Format 3: US table (no names)
# ---------------------------------------------------------------------------
def test_us_table_no_names_and_extra_columns():
    src = P.parse_paste(_read("us_table.md"), investing_idea="US Growth")
    assert src.paste_format == "US_TABLE"
    by = {r["ticker"]: r for r in src.rows}
    assert set(by) == {"ACME", "HELM", "ZETA"}
    # names are never invented in the US table
    assert all(r["company"] is None for r in src.rows)
    helm = by["HELM"]
    assert helm["country"] == "us"
    assert helm["analyst_target"] == pytest.approx(52.0)
    assert helm["valuation_sws"] == pytest.approx(18.9)
    assert helm["growth_sws"] == pytest.approx(0.09)
    assert helm["div_yield"] == pytest.approx(0.0)   # 0.0% stays 0, it is real
    assert helm["industry"] == "Software"
    # n/a cells stay None
    assert by["ZETA"]["valuation_sws"] is None
    assert by["ZETA"]["div_yield"] is None


# ---------------------------------------------------------------------------
# Overlap across formats (feeds the dedup logic in the builder)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Hard multi-market table view (dividend list): numeric/space tickers, many
# currencies, EUR pseudo-country, big header gap, footer chrome.
# ---------------------------------------------------------------------------
def test_dividend_table_hard_cases():
    src = P.parse_paste(_read("dividend_table.txt"))
    assert src.paste_format == "US_TABLE"
    assert src.title == "Growing Dividend Payers with 2-5% yield"
    assert src.header_count == 190          # page claims 190 ...
    assert src.parsed_count == 82           # ... but only 82 rows were pasted (gap)
    by = {r["ticker"]: r for r in src.rows}

    # numeric tickers (HK / China / Japan)
    assert by["1288"]["currency"] == "HKD" and by["1288"]["country"] == "hk"
    assert by["600519"]["currency"] == "CNY" and by["600519"]["market_cap_local_m"] == 1_600_000
    assert by["8766"]["currency"] == "JPY"
    # space / share-class tickers
    assert by["SEB A"]["currency"] == "SEK" and by["SEB A"]["country"] == "se"
    assert by["ZZ B"]["currency"] == "SEK"
    # new currencies
    assert by["GULFOILLUB"]["currency"] == "INR" and by["GULFOILLUB"]["country"] == "in"
    assert by["HLG"]["currency"] == "NZD" and by["HLG"]["country"] == "nz"
    assert by["A119610"]["currency"] == "KRW"
    # EUR -> "eu" pseudo-country (exact country unknown from the table view)
    assert by["ALV"]["currency"] == "EUR" and by["ALV"]["country"] == "eu"
    # ticker "NA" must not be mistaken for an n/a token
    assert by["NA"]["currency"] == "CAD"
    # n/a analyst target -> None, never 0
    assert by["1945"]["analyst_target"] is None
    # New markers survive
    assert {"UNP", "535602", "CER"} <= {r["ticker"] for r in src.rows if r["new_on_screener"]}
    # comma-thousands price parsed
    assert by["600519"]["price_local"] == 1257.12


def test_overlap_between_sources():
    g = {r["ticker"] for r in P.parse_paste(_read("global_plaintext.txt")).rows}
    c = {r["ticker"] for r in P.parse_paste(_read("country_markdown.md")).rows}
    u = {r["ticker"] for r in P.parse_paste(_read("us_table.md")).rows}
    assert "BETA" in (g & c)             # global vs regional overlap
    assert {"ACME", "ZETA"} <= (g & u)   # global vs US overlap
    assert (g | c | u) == {
        "ACME", "BETA", "GAMA", "DLTA", "EPSL", "ZETA",  # global
        "POLR", "1INN", "VK", "MTHH",                    # regional-only
        "HELM",                                          # US-only
    }


# ---------------------------------------------------------------------------
# Currencies first seen in "Undiscovered Gems" (Oct 2026) and the unknown-symbol
# fallback: an unfamiliar symbol must never end the table early.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("token,cur,millions", [
    ("ر.س2.0b", "SAR", 2000.0), ("ج.م127.7b", "EGP", 127700.0),
    ("AR$1.4t", "ARS", 1_400_000.0), ("CHF 426.3m", "CHF", 426.3),
    ("₽3.0t", "RUB", 3_000_000.0), ("৳65.7b", "BDT", 65700.0),
    ("₦4.8t", "NGN", 4_800_000.0), ("XYZ1.2b", "UNKNOWN", 1200.0),
])
def test_new_and_unknown_currencies(token, cur, millions):
    c, m = P.parse_money(token)
    assert c == cur and m == pytest.approx(millions)


def test_unknown_symbol_does_not_truncate_vertical_table():
    rows = ["Results", "3", "3 companies",
            "Company\tLast Price\t7D Return\t1Y Return\tMarket Cap\tAnalysts Target\t"
            "Valuation\tGrowth\tDiv Yield\tIndustry"]
    for tk, px in (("AAA", "US$10.00"), ("BBB", "XYZ5.50"), ("CCC", "US$7.00")):
        cap = "US$1.0b" if px.startswith("US$") else "XYZ2.0b"
        rows += [tk, px, "1.0%", "2.0%", cap, "n/a", "10.0", "5.0%", "1.0%", "Tech"]
    rows += ["Level 5, 320 Pitt Street, Sydney", "S&P Global Market Intelligence"]
    src = P.parse_paste("\n".join(rows))
    assert [r["ticker"] for r in src.rows] == ["AAA", "BBB", "CCC"]
    bbb = src.rows[1]
    assert bbb["currency"] == "UNKNOWN" and bbb["price_local"] == 5.5
    assert bbb["country"] is None


def test_free_text_is_not_a_price():
    assert P.parse_price("Level 5, 320 Pitt Street, Sydney") == (None, None)
    assert P.parse_price("S&P Global") == (None, None)
