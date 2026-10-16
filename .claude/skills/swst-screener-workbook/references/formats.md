# The three Simply Wall St paste formats

All examples below are anonymized. The parser (`scripts/parse_swst.py`)
auto-detects the format with `detect_format(text)`.

## Detection order

1. Any `/stocks/<cc>/<sector>/<exch>-<ticker>` URL present → **COUNTRY_MD**.
2. Else a header row naming `Company … Last Price … 7D … 1Y … Market Cap` → **US_TABLE**.
3. Else → **GLOBAL_TEXT**.

---

## 1. GLOBAL_TEXT — global plain-text page

Repeating blocks. The `Market Cap:` line anchors each block; the company name is
the non-empty line just above it; `7D` and `1Y` are literal anchors whose value
is on the next line. An optional `New` line marks new-on-screener.

```
Undervalued based on future growth
Results 150
150 companies
Acme Global Holdings
Market Cap:US$1.2b
Designs and manufactures industrial widgets.
New
NasdaqGS:ACME
US$12.34
7D
+1.2%
1Y
-15.3%
Beta Industries Ltd
Market Cap:UK£450.0m
...
```

Gotchas:
- The header count (`150 companies`) can exceed the rows actually pasted. Compare
  `parsed_count` to `header_count` and warn on the gap.
- No country/sector in this format — those stay blank unless the same ticker is
  also present in a COUNTRY_MD or US paste (then the merge fills them in).

---

## 2. COUNTRY_MD — country page as markdown with links

Same fields as GLOBAL_TEXT, but each is a markdown link. The stock-detail URL
encodes geography, so **read country / sector / exchange from the URL, never
guess them**.

```
[Polar Capital Holdings](https://simplywall.st/stocks/gb/diversified-financials/aim-polr/polar-capital-holdings)
Market Cap:UK£520.0m
Asset management firm focused on specialist equity strategies.
[AIM:POLR](https://simplywall.st/stocks/gb/diversified-financials/aim-polr/polar-capital-holdings)
UK£4.80
7D
+0.5%
1Y
-12.0%
```

URL shape: `/stocks/<country>/<sector>/<exchange>-<ticker>/...`. Real examples of
the `<exchange>-<ticker>` tail: `aim-polr`, `lse-fsg`, `etr-1inn`, `epa-vk`,
`cph-mthh`. The parser strips the markdown to plain text, reuses the GLOBAL_TEXT
block logic, then overlays country/sector/exchange (and a fallback company name)
keyed by ticker.

---

## 3. US_TABLE — US table view

Columns: `Company | Last Price | 7D | 1Y | Market Cap | Analysts Target |
Valuation | Growth | Div Yield | Industry`. Pipe or aligned tables both parse.
**Tickers only — there are no company names**, so the workbook renders
`<TICKER> (name not in paste)` and leaves Business blank.

```
| Company | Last Price | 7D | 1Y | Market Cap | Analysts Target | Valuation | Growth | Div Yield | Industry |
|---|---|---|---|---|---|---|---|---|---|
| NasdaqGS:ACME | US$12.34 | +1.2% | -15.3% | US$1.2b | US$18.00 | 12.4x | 22.0% | 1.5% | Machinery |
| NYSE:HELM | US$45.60 | -3.0% | +5.0% | US$8.5b | US$52.00 | 18.9x | 9.0% | 0.0% | Software |
```

Gotchas:
- `Valuation` is **not** a uniform P/E (banks ~1, software ~29). Labelled
  `Valuation (SWS)`; warn against cross-sector comparison.
- Analyst target / upside / valuation / growth / yield / industry exist ONLY
  here. They are blank (not zero) for rows that came from the other formats.
- `0.0%` div yield is a real zero and is kept; `n/a` becomes empty.

---

## Currency symbols

Matched longest-first so multi-character symbols win (`US$` over `$`, `R$` over
`R`, `CN¥` over `¥`, `NT$`/`HK$`/`S$` over `$`).

| Symbol | Code | | Symbol | Code | | Symbol | Code |
|---|---|---|---|---|---|---|---|
| ₩ | KRW | | € | EUR | | ฿ | THB |
| NT$ | TWD | | US$ | USD | | ₺ | TRY |
| HK$ | HKD | | CA$ | CAD | | R | ZAR |
| CN¥ | CNY | | AU$ | AUD | | R$ | BRL |
| JP¥ | JPY | | UK£ | GBP | | zł | PLN |
| ₫ | VND | | S$ | SGD | | SEK | SEK |
| RM | MYR | | Rp | IDR | | NOK | NOK |
| ₱ | PHP | | د.إ | AED | | DKK | DKK |

Market-cap suffixes → millions: `t` = 1,000,000 · `b` = 1,000 · `m` = 1.
Percentages are stored as fractions (`+1.2%` → `0.012`). `n/a` → empty, never 0.

## Region classification

Default policy: **by listing venue** (the exchange country), not domicile.
Stated edge cases the report must call out:
- **Transcontinental** (Turkey): excluded from "Europe" unless explicitly asked.
- **ADRs** (e.g. a China-based company listed in the US): follow the venue (US).
- **Foreign company on a local exchange** (e.g. a MENA company on the LSE): venue.
- **Domicile vs venue** (e.g. a Malta-based company listed in Sweden): venue.
- Rows with **no country in the paste** (global-list-only) are excluded from a
  region filter and reported, since the venue is unknown.

If you later want global-only rows classified by their exchange code, that is a
deliberate extension (exchange→country map) — validate it against real pastes
first, because it changes the filtered counts.
