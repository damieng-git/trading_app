# Design notes

The rules behind the pipeline, kept short. The code and tests are the source of truth;
this file says why.

## Pipeline order
parse -> merge -> **filter to US + EU** (`us_eu` region set, includes the `eu` pseudo-country)
-> enrich (yfinance, survivors only) -> score -> write xlsx + per-list HTML + `rows.json`
-> rebuild the hub. Filtering comes after merge (a row's country may only be known from
another paste) and before enrichment (no wasted Yahoo calls).

## Parsing rules
- Never invent a company name. Order: pasted name, Yahoo `longName` for the cross-checked
  listing, else `<TICKER> (name not in paste)`.
- Blank means "not available", never 0. Percentages are fractions; caps are millions of
  local currency (t/b/m/k suffixes).
- The record boundary in the vertical table view is price-anchored, so numeric (1288),
  spaced (SEB A) and symbol tickers (PE&OLES *) all parse. An unknown currency symbol is
  kept as `UNKNOWN`, never ends the table. A record that cannot become a row is listed in
  `ParsedSource.notes`, never dropped silently.
- Always compare the page's header count with rows parsed and report the gap.

## Symbol resolution (enrich.py)
- Country to Yahoo suffix via `SUFFIX_BY_COUNTRY` (gb .L, se .ST, pl .WA, ...). Share class
  spaces/dots become `-` (SEB-A.ST, BT-A.L); US OTC `XXXX.Y` loses the dot (LKNCY).
- `eu` rows (EUR quote, no exchange) and "no data" rows are probed: yfinance Search
  candidates, then blind EUR venue suffixes (max 6 fetches). A candidate is accepted only if
  currency, 1Y return and price cross-check against the paste; the closest price wins.
  Search results are cached in `~/swst_reports/data/symbol_search_cache.json`, decisions never.
- `symbol_overrides` in config (`"TICKER|CCY": SYMBOL`) wins over everything.
- LSE quotes are in pence (GBp): divide price-derived values by 100.
- Luxembourg has no trusted suffix and stays unresolved on purpose.

## Identity across pastes and lists (identity.py)
Key = resolved Yahoo symbol when present, else `ticker|currency`. Two rows sharing
`ticker|currency` are the same company only if both price and market-cap ratios are <= 1.6;
otherwise they are kept apart (`...#2`). Example: SAN|EUR is Sanofi (71.60) and Banco
Santander (12.26).

## Scoring (score.py, `scoring:` in config)
- Each metric is scored 0..1 against fixed, hand-set anchors (not percentiles, not fitted,
  not backtested). Part = (sum + 0.5) / (n + 1), shrinking thin data toward the middle.
- Value: P/E (fwd, else trailing), EV/EBITDA, P/FCF (P/S only if no P/E and no EV/EBITDA).
  Financials: P/E + P/B. Needs 2 metrics.
- Quality: net cash/cap, SWS forecast growth, ROE (only if P/B > 0), FCF positive, cash runway
  (loss-makers only). Financials: growth + ROE. Needs 2 metrics.
- Timing: best of oversold / breakout / pullback closeness. Risk: beta, 3y max drawdown,
  liquidity (log scale). Needs 2 metrics.
- Composite only when all four parts exist, weighted by cap tier
  (large 35/35/10/20, mid 30/30/25/15, small 25/30/30/15 for V/Q/T/R).
- Tiers by EUR cap: large >= EUR 10bn, mid >= EUR 2bn, small below.
- Gates (prompts, never buy/sell): large "DCA candidate (review)" if V >= 0.5 and Q >= 0.6;
  mid/small "Entry setup (review)" if V >= 0.5, Q >= 0.5 and a timing flag is on.
  A downtrend is a label, not a score input.
- Known limitation: Timing saturates (many names at 100), lifting small caps.

## Hub (build_hub.py)
Latest run per list slug, read from `data/*.rows.json` (no network). Every tab shows the row
from the newest snapshot for a stock, so one stock has one score everywhere; ranks and the
"median" in notes are recomputed per tab. `index.html` is the only file overwritten on purpose.

Page layout (dashboard.py, palette Ash Grey / Muted Teal / Deep Teal / Dark Slate / Charcoal Blue,
selection dark yellow #b8860b): a dark left rail holds the lists (count in brackets, following the
active filters) and the filter drop-downs (Tier, Country, Flags, Hide not scored; filters persist
across lists). The middle column has the list title with summary chips (in view, scored, not
scored, on 2+ lists), two scatters side by side (Value vs Quality, Value vs Timing, Value on X),
then the scored and not-scored tables. The right column is the selected-stock panel (price,
V/Q/T/R bars, key figures, lists, note, take, consensus, headlines), sticky on wide screens.
Clicking a bubble or a row selects it on both charts, frames the row (one outer frame) and fills
the panel; clicking again or the panel's close button clears it. US dots are muted teal, EU
charcoal (they differ in lightness, not only hue). No external fonts: the page stays self-contained.

## Peers (peers.py)
Each stock's P/E (trailing and forward), P/S, EV/Sales and EV/FCF against the median of its
peers: the other stocks in the same yfinance industry (needs 3 or more), else the same
sector, else none. In the hub the peer set is the whole "All lists" universe, one row per
company, and the result is written onto the winning row so every tab shows the same
comparison; a per-list page uses that list only. A median needs 3 values; the stock is
never in its own median. Shown as "8.4 (-47% vs peers)" with the median in the cell
tooltip. Cheaper than peers is a prompt to look, not a value call. EV/Sales is yfinance
`enterpriseToRevenue`; EV/FCF is `enterpriseValue / freeCashflow` (blank when FCF <= 0).

## Takes (takes.py)
Claude writes 3-4 neutral sentences from a fixed fact pack plus relevance-filtered headlines
(Yahoo RSS + Google News). Checked for length and verdict words; one retry, then dropped with
the reason. Needs `~/.config/swst-screener/claude_oauth_token` (from `claude setup-token`).
The Yahoo analyst consensus line is quoted and attributed, kept apart from our own wording.

## Hard rules
No buy/sell language in our own text. No em dashes. Tests never touch the network.
Workbooks and per-list pages are never overwritten (timestamped, opened with "x").
