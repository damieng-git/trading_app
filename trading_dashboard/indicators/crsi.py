from __future__ import annotations

import numpy as np
import pandas as pd

from ._base import rma


def crsi(series: pd.Series, domcycle: int = 20, vibration: int = 10, leveling: float = 10.0) -> pd.DataFrame:
    domcycle = int(domcycle)
    cyclelen = max(1, int(round(domcycle / 2.0)))
    vibration = int(vibration)
    leveling = float(leveling)
    cyclicmemory = int(domcycle * 2)

    torque = 2.0 / (vibration + 1.0)
    phasing_lag = int(round((vibration - 1) / 2.0))
    aperc = leveling / 100.0

    delta = series.diff()
    up = rma(delta.clip(lower=0.0), cyclelen)
    down = rma((-delta.clip(upper=0.0)), cyclelen)
    rsi = pd.Series(np.nan, index=series.index, dtype=float)
    rsi = np.where(down == 0, 100.0, np.where(up == 0, 0.0, 100.0 - 100.0 / (1.0 + up / down.replace(0.0, np.nan))))
    rsi = pd.Series(rsi, index=series.index, dtype=float)

    crsi_vals = np.full(len(series), np.nan, dtype=float)
    lb_vals = np.full(len(series), np.nan, dtype=float)
    ub_vals = np.full(len(series), np.nan, dtype=float)

    for i in range(len(series)):
        rsi_i = rsi.iat[i]
        rsi_lag = rsi.iat[i - phasing_lag] if (i - phasing_lag) >= 0 else np.nan
        prev_crsi = crsi_vals[i - 1] if i > 0 else np.nan
        if np.isnan(prev_crsi):
            prev_crsi = rsi_i
        if np.isnan(rsi_lag):
            rsi_lag = rsi_i
        crsi_vals[i] = torque * (2.0 * rsi_i - rsi_lag) + (1.0 - torque) * prev_crsi

        start = max(0, i - cyclicmemory + 1)
        window = crsi_vals[start : i + 1]
        window = window[~np.isnan(window)]
        if window.size == 0:
            continue
        lmax = float(np.max(window))
        lmin = float(np.min(window))
        mstep = (lmax - lmin) / 100.0 if (lmax - lmin) != 0 else 0.0
        L = float(len(window))

        db = lmin
        for steps in range(0, 101):
            testvalue = lmin + mstep * steps
            below = float(np.sum(window < testvalue))
            ratio = below / L if L else 0.0
            if ratio >= aperc:
                db = testvalue
                break
        ub = lmax
        for steps in range(0, 101):
            testvalue = lmax - mstep * steps
            above = float(np.sum(window >= testvalue))
            ratio = above / L if L else 0.0
            if ratio >= aperc:
                ub = testvalue
                break

        lb_vals[i] = db
        ub_vals[i] = ub

    out = pd.DataFrame(
        {
            "cRSI": pd.Series(crsi_vals, index=series.index),
            "cRSI_lb": pd.Series(lb_vals, index=series.index),
            "cRSI_ub": pd.Series(ub_vals, index=series.index),
        },
        index=series.index,
    )
    return out
