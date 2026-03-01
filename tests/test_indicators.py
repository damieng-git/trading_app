"""Tests for indicator functions."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_dashboard.indicators import (
    sma, ema, rma, dema, wma, atr, true_range,
    highest, lowest, stdev, hlc3, rsi_wilder,
    supertrend,
    bollinger_bands,
    macd,
    adx_di,
    wavetrend_lazybear,
    obv_oscillator,
    squeeze_momentum_lazybear,
    stoch_momentum_index,
    ut_bot_alert,
    turtle_trade_channels,
    atr_stop_loss_finder,
    parabolic_sar,
    gmma,
    ma_ribbon,
    crsi,
    donchian_trend_ribbon,
    madrid_ma_ribbon_state,
    nadaraya_watson_endpoint,
    nadaraya_watson_repainting,
    nwe_color_and_arrows,
    ichimoku,
    mansfield_relative_strength,
    sr_breaks_retests,
)


class TestBaseIndicators:
    def test_sma(self, sample_ohlcv):
        result = sma(sample_ohlcv["Close"], 20)
        assert len(result) == len(sample_ohlcv)
        assert result.iloc[:19].isna().all()
        assert result.iloc[19:].notna().all()

    def test_ema(self, sample_ohlcv):
        result = ema(sample_ohlcv["Close"], 10)
        assert result.notna().sum() > 100

    def test_rma(self, sample_ohlcv):
        result = rma(sample_ohlcv["Close"], 14)
        assert result.notna().sum() > 100

    def test_dema(self, sample_ohlcv):
        result = dema(sample_ohlcv["Close"], 9)
        assert result.notna().sum() > 100

    def test_true_range(self, sample_ohlcv):
        tr = true_range(sample_ohlcv)
        assert (tr.dropna() >= 0).all()

    def test_atr(self, sample_ohlcv):
        result = atr(sample_ohlcv, 14)
        assert (result.dropna() > 0).all()

    def test_rsi_wilder(self, sample_ohlcv):
        result = rsi_wilder(sample_ohlcv["Close"], 14)
        valid = result.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()


class TestOverlayIndicators:
    def test_supertrend(self, sample_ohlcv):
        line, trend, st_atr = supertrend(sample_ohlcv, periods=10, multiplier=3.0)
        assert "SuperTrend" not in sample_ohlcv.columns  # non-destructive
        assert line.notna().sum() > 50
        assert set(trend.dropna().unique()).issubset({1, -1})

    def test_bollinger_bands(self, sample_ohlcv):
        basis, upper, lower = bollinger_bands(sample_ohlcv["Close"], 20, 2.0)
        valid_idx = basis.dropna().index
        assert (upper.loc[valid_idx] >= basis.loc[valid_idx]).all()
        assert (lower.loc[valid_idx] <= basis.loc[valid_idx]).all()

    def test_parabolic_sar(self, sample_ohlcv):
        result = parabolic_sar(sample_ohlcv)
        assert result.notna().sum() > 100

    def test_ut_bot(self, sample_ohlcv):
        result = ut_bot_alert(sample_ohlcv)
        assert "UT_trailing_stop" in result.columns
        assert "UT_buy" in result.columns

    def test_turtle_channels(self, sample_ohlcv):
        result = turtle_trade_channels(sample_ohlcv, 20, 10)
        assert "TuTCI_upper" in result.columns
        assert "TuTCI_trend" in result.columns

    def test_atr_stop(self, sample_ohlcv):
        result = atr_stop_loss_finder(sample_ohlcv)
        assert "ATR_long_stop" in result.columns
        assert "ATR_short_stop" in result.columns

    def test_gmma(self, sample_ohlcv):
        result = gmma(sample_ohlcv)
        ema_cols = [c for c in result.columns if c.startswith("GMMA_ema_")]
        assert len(ema_cols) >= 10

    def test_ma_ribbon(self, sample_ohlcv):
        result = ma_ribbon(sample_ohlcv)
        assert "MA_Ribbon_ma1" in result.columns

    def test_ichimoku(self, sample_ohlcv):
        result = ichimoku(sample_ohlcv)
        assert "Ichi_tenkan" in result.columns
        assert "Ichi_kumo_bull" in result.columns


class TestOscillatorIndicators:
    def test_macd(self, sample_ohlcv):
        line, sig, hist = macd(sample_ohlcv["Close"])
        assert line.notna().sum() > 50
        assert hist.notna().sum() > 50

    def test_adx_di(self, sample_ohlcv):
        adx_val, di_p, di_m = adx_di(sample_ohlcv, 14)
        valid = adx_val.dropna()
        assert (valid >= 0).all()

    def test_wavetrend(self, sample_ohlcv):
        wt1, wt2, hist = wavetrend_lazybear(sample_ohlcv)
        assert wt1.notna().sum() > 50

    def test_obv_oscillator(self, sample_ohlcv):
        obv, osc = obv_oscillator(sample_ohlcv, 20)
        assert obv.notna().sum() > 100

    def test_squeeze(self, sample_ohlcv):
        result = squeeze_momentum_lazybear(sample_ohlcv)
        assert "SQZ_val" in result.columns

    def test_smi(self, sample_ohlcv):
        smi_val, smi_ema_val = stoch_momentum_index(sample_ohlcv)
        assert smi_val.notna().sum() > 50

    def test_crsi(self, sample_ohlcv):
        result = crsi(sample_ohlcv["Close"])
        assert "cRSI" in result.columns

    def test_donchian_ribbon(self, sample_ohlcv):
        result = donchian_trend_ribbon(sample_ohlcv)
        trend_cols = [c for c in result.columns if "trend" in c.lower()]
        assert len(trend_cols) > 0

    def test_madrid_ribbon(self, sample_ohlcv):
        result = madrid_ma_ribbon_state(sample_ohlcv)
        assert "MMARB_ma05" in result.columns


class TestNadarayaWatson:
    def test_endpoint(self, sample_ohlcv):
        result = nadaraya_watson_endpoint(sample_ohlcv["Close"], 8.0, 50)
        assert result.notna().sum() > 50

    def test_repainting(self, sample_ohlcv):
        result = nadaraya_watson_repainting(sample_ohlcv["Close"], 8.0, 50)
        assert result.notna().sum() > 0

    def test_color_and_arrows(self, sample_ohlcv):
        nw = nadaraya_watson_endpoint(sample_ohlcv["Close"], 8.0, 50)
        result = nwe_color_and_arrows(nw)
        assert "NW_color" in result.columns
        assert set(result["NW_color"].dropna().unique()).issubset({"green", "red"})


class TestKPIStates:
    def test_compute_kpi_state_map(self, sample_ohlcv):
        from trading_dashboard.kpis.catalog import compute_kpi_state_map
        from trading_dashboard.data.enrichment import translate_and_compute_indicators

        enriched, _ = translate_and_compute_indicators(sample_ohlcv)
        states = compute_kpi_state_map(enriched)
        assert isinstance(states, dict)
        assert len(states) > 0
        for name, series in states.items():
            valid = series.dropna()
            assert set(valid.unique()).issubset({-2, -1, 0, 1}), f"KPI {name} has invalid states"


class TestEdgeCases:
    def test_empty_dataframe(self):
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        result = sma(pd.Series(dtype=float), 10)
        assert len(result) == 0

    def test_single_bar(self):
        df = pd.DataFrame({
            "Open": [100.0], "High": [105.0], "Low": [95.0],
            "Close": [102.0], "Volume": [1000],
        }, index=pd.DatetimeIndex(["2025-01-01"]))
        tr = true_range(df)
        assert len(tr) == 1

    def test_sr_breaks(self, sample_ohlcv):
        result = sr_breaks_retests(sample_ohlcv)
        assert isinstance(result, pd.DataFrame)

    def test_mansfield_rs(self, sample_ohlcv):
        bench = sample_ohlcv["Close"] * 1.1
        result = mansfield_relative_strength(sample_ohlcv, bench)
        assert "MRS_raw" in result.columns
