from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import gaussian_weights, sma

# ---------------------------------------------------------------------------
#  Numba-accelerated non-repainting NW kernel (10-50x faster than pure Python)
#  Falls back to plain numpy loop if numba is not installed.
# ---------------------------------------------------------------------------
try:
    from numba import njit as _njit

    @_njit(cache=True)
    def _nw_nr_loop(data, weights, n, window):
        """Non-repainting rolling NW kernel — numba JIT compiled."""
        out = np.full(n, np.nan)
        for i in range(n):
            length = min(i + 1, window)
            denom = 0.0
            numer = 0.0
            for j in range(length):
                w = weights[j]
                numer += data[i - j] * w
                denom += w
            if denom != 0.0:
                out[i] = numer / denom
        return out

except ImportError:
    def _nw_nr_loop(data, weights, n, window):
        """Non-repainting rolling NW kernel — pure numpy fallback."""
        out = np.full(n, np.nan)
        for i in range(n):
            length = min(i + 1, window)
            x_rev = data[i - length + 1: i + 1][::-1]
            ww = weights[:length]
            denom = ww.sum()
            if denom == 0:
                continue
            out[i] = float(np.dot(x_rev, ww) / denom)
        return out


def _nw_kernel(data: np.ndarray, bandwidth: float, window: int = 500, repaint: bool = False) -> np.ndarray:
    """Compute NW estimator values as a raw numpy array (shared by all envelope variants)."""
    n = len(data)
    if n == 0:
        return np.array([], dtype=float)
    if repaint:
        h = float(bandwidth)
        m = min(window, n)
        out = np.full(n, np.nan, dtype=float)
        if m <= 0:
            return out
        x_oldest = data[-m:]
        x_rev = x_oldest[::-1]
        if h <= 0:
            y_rev = x_rev.copy()
        else:
            idx = np.arange(m, dtype=float)
            diff = idx[:, None] - idx[None, :]
            w = np.exp(-(diff ** 2) / (h * h * 2.0))
            denom = w.sum(axis=1)
            denom = np.where(denom == 0, np.nan, denom)
            y_rev = (w @ x_rev) / denom
        out[-m:] = y_rev[::-1]
        return out
    # Non-repainting: rolling endpoint kernel (JIT-accelerated when numba available)
    w = gaussian_weights(window, bandwidth)
    return _nw_nr_loop(np.ascontiguousarray(data, dtype=np.float64), w, n, window)


def nadaraya_watson_endpoint(src: pd.Series, bandwidth: float, window: int = 500) -> pd.Series:
    w = gaussian_weights(window, bandwidth)

    def _apply(x: np.ndarray) -> float:
        x_rev = x[::-1]
        ww = w[: len(x_rev)]
        denom = ww.sum()
        if denom == 0:
            return np.nan
        return float(np.dot(x_rev, ww) / denom)

    return src.rolling(window=window, min_periods=1).apply(lambda x: _apply(x.to_numpy()), raw=False)


def nadaraya_watson_repainting(src: pd.Series, bandwidth: float, window: int = 500) -> pd.Series:
    h = float(bandwidth)
    if src is None or src.empty:
        return pd.Series(dtype=float, index=src.index if src is not None else None)

    n = int(len(src))
    m = int(min(int(window), n))
    out = pd.Series(np.nan, index=src.index, dtype=float)
    if m <= 0:
        return out

    x_oldest_to_newest = src.iloc[-m:].to_numpy(dtype=float)
    x_bars_back = x_oldest_to_newest[::-1]

    if h <= 0:
        y_bars_back = x_bars_back.copy()
    else:
        idx = np.arange(m, dtype=float)
        diff = idx[:, None] - idx[None, :]
        w = np.exp(-(diff**2) / (h * h * 2.0))
        denom = w.sum(axis=1)
        denom = np.where(denom == 0, np.nan, denom)
        y_bars_back = (w @ x_bars_back) / denom

    y_oldest_to_newest = y_bars_back[::-1]
    out.iloc[-m:] = y_oldest_to_newest
    return out


def nadaraya_watson_envelope_endpoint(
    src: pd.Series, bandwidth: float = 8.0, window: int = 500, mult: float = 3.0
) -> pd.DataFrame:
    out = nadaraya_watson_endpoint(src, bandwidth=bandwidth, window=window)
    mae = sma((src - out).abs(), 499) * float(mult)
    upper = out + mae
    lower = out - mae
    return pd.DataFrame(
        {"NWE_env_mid": out, "NWE_env_mae": mae, "NWE_env_upper": upper, "NWE_env_lower": lower},
        index=src.index,
    )


def nadaraya_watson_envelope_luxalgo(
    src: pd.Series,
    *,
    bandwidth: float = 8.0,
    window: int = 500,
    mult: float = 3.0,
    repaint: bool = True,
) -> pd.DataFrame:
    window = int(window)
    mult = float(mult)

    if src is None or src.empty:
        return pd.DataFrame(index=src.index if src is not None else None)

    if repaint:
        mid = nadaraya_watson_repainting(src, bandwidth=float(bandwidth), window=window)
        mask = mid.notna() & src.notna()
        if mask.any():
            sae = float((src.loc[mask] - mid.loc[mask]).abs().mean()) * mult
        else:
            sae = np.nan
        band = pd.Series(np.nan, index=src.index, dtype=float)
        band.loc[mask] = sae
        upper = mid + band
        lower = mid - band
        return pd.DataFrame(
            {"NWE_env_mid": mid, "NWE_env_mae": band, "NWE_env_upper": upper, "NWE_env_lower": lower},
            index=src.index,
        )

    mid = nadaraya_watson_endpoint(src, bandwidth=float(bandwidth), window=window)
    mae_len = max(1, window - 1)
    # Use min_periods=1 so short histories still get bands.
    mae = (src - mid).abs().rolling(window=mae_len, min_periods=1).mean() * mult
    upper = mid + mae
    lower = mid - mae
    return pd.DataFrame(
        {"NWE_env_mid": mid, "NWE_env_mae": mae, "NWE_env_upper": upper, "NWE_env_lower": lower},
        index=src.index,
    )


def nadaraya_watson_envelope_luxalgo_std(
    src: pd.Series,
    *,
    bandwidth: float = 8.0,
    window: int = 500,
    mult: float = 3.0,
    repaint: bool = True,
) -> pd.DataFrame:
    window = int(window)
    mult = float(mult)

    if src is None or src.empty:
        return pd.DataFrame(index=src.index if src is not None else None)

    if repaint:
        mid = nadaraya_watson_repainting(src, bandwidth=float(bandwidth), window=window)
        mask = mid.notna() & src.notna()
        if mask.any():
            resid = (src.loc[mask] - mid.loc[mask]).astype(float)
            sse = float(resid.std(ddof=0)) * mult
        else:
            sse = np.nan
        band = pd.Series(np.nan, index=src.index, dtype=float)
        band.loc[mask] = sse
        upper = mid + band
        lower = mid - band
        return pd.DataFrame(
            {"NWE_env_mid": mid, "NWE_env_std": band, "NWE_env_upper": upper, "NWE_env_lower": lower},
            index=src.index,
        )

    mid = nadaraya_watson_endpoint(src, bandwidth=float(bandwidth), window=window)
    std_len = max(1, window - 1)
    resid = (src - mid).astype(float)
    # Use min_periods=1 so short histories still get bands.
    sse = resid.rolling(window=std_len, min_periods=1).std(ddof=0) * mult
    upper = mid + sse
    lower = mid - sse
    return pd.DataFrame(
        {"NWE_env_mid": mid, "NWE_env_std": sse, "NWE_env_upper": upper, "NWE_env_lower": lower},
        index=src.index,
    )


def nwe_color_and_arrows(nwe: pd.Series, *, forward_diff: bool = False) -> pd.DataFrame:
    if forward_diff:
        d = nwe.shift(-1) - nwe
        prev_d = d.shift(1)
        slope_up = nwe.shift(-1) > nwe
    else:
        d = nwe.diff()
        prev_d = d.shift(1)
        slope_up = nwe > nwe.shift(1)
    flip = (d * prev_d) < 0
    arrow_up = flip & (prev_d < 0)
    arrow_down = flip & (prev_d > 0)

    return pd.DataFrame(
        {
            "NW_slope_up": slope_up.fillna(False),
            "NW_color": np.where(slope_up, "green", "red"),
            "NW_arrow_up": arrow_up.fillna(False),
            "NW_arrow_down": arrow_down.fillna(False),
        },
        index=nwe.index,
    )
