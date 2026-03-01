# Pine Script → Python Cheat Sheet

Quick reference for translating Pine Script built-ins to pandas/numpy equivalents.

## Series Operations

| Pine Script | Python (pandas/numpy) |
|---|---|
| `ta.sma(src, len)` | `from ._base import sma; sma(series, length)` |
| `ta.ema(src, len)` | `from ._base import ema; ema(series, length)` |
| `ta.wma(src, len)` | `from ._base import wma; wma(series, length)` |
| `ta.vwma(src, vol, len)` | `from ._base import vwma; vwma(price, volume, length)` |
| `ta.rma(src, len)` | `from ._base import rma; rma(series, length)` |
| `ta.stdev(src, len)` | `from ._base import stdev; stdev(series, length)` |
| `ta.highest(src, len)` | `from ._base import highest; highest(series, length)` |
| `ta.lowest(src, len)` | `from ._base import lowest; lowest(series, length)` |
| `ta.atr(len)` | `from ._base import atr; atr(df, length)` |
| `ta.tr(true)` | `from ._base import true_range; true_range(df)` |
| `ta.rsi(src, len)` | `from ._base import rsi_wilder; rsi_wilder(close, length)` |
| `ta.linreg(src, len, 0)` | `from ._base import linreg; linreg(series, length)` |
| `ta.macd(src, fast, slow, sig)` | `from .macd_indicator import macd; macd(series, fast, slow, signal)` |
| `ta.supertrend(mult, len)` | `from .supertrend_indicator import supertrend; supertrend(df, periods, multiplier)` |

## Price Sources

| Pine Script | Python |
|---|---|
| `close` | `df["Close"]` |
| `open` | `df["Open"]` |
| `high` | `df["High"]` |
| `low` | `df["Low"]` |
| `volume` | `df["Volume"]` |
| `hlc3` | `from ._base import hlc3; hlc3(df)` |
| `hl2` | `(df["High"] + df["Low"]) / 2` |
| `ohlc4` | `(df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4` |

## Shift / Lookback

| Pine Script | Python |
|---|---|
| `src[1]` (1 bar ago) | `series.shift(1)` |
| `src[n]` (n bars ago) | `series.shift(n)` |

## Crossover / Crossunder

| Pine Script | Python |
|---|---|
| `ta.crossover(a, b)` | `(a > b) & (a.shift(1) <= b.shift(1))` |
| `ta.crossunder(a, b)` | `(a < b) & (a.shift(1) >= b.shift(1))` |
| `ta.cross(a, b)` | crossover OR crossunder |

## Conditional Assignment

| Pine Script | Python |
|---|---|
| `x = cond ? a : b` | `x = np.where(cond, a, b)` |
| `x := na(x) ? default : x` | `x = x.fillna(default)` |

## Stateful Variables (var)

Pine `var` persists across bars. In pandas, use `.shift()` + forward-fill:

```python
# Pine: var float level = na
# Pine: if condition
# Pine:     level := close
level = pd.Series(np.nan, index=df.index)
level[condition] = df["Close"][condition]
level = level.ffill()
```

## Common Patterns

### Bollinger Bands
```python
basis = sma(close, 20)
dev = stdev(close, 20) * 2.0
upper = basis + dev
lower = basis - dev
```

### RSI Divergence Detection
```python
rsi = rsi_wilder(close, 14)
# Use pivot detection on rsi to find divergences
```

### Color Encoding (Pine → Python)
```python
# Pine: color = close > open ? color.green : color.red
color = np.where(df["Close"] > df["Open"], "green", "red")
```
