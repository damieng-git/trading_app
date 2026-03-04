"""Tests for indicator functions."""

from __future__ import annotations

import pandas as pd

from trading_dashboard.indicators import (
    adx_di,
    atr,
    atr_stop_loss_finder,
    bollinger_bands,
    cci_chop_bb,
    crsi,
    dema,
    donchian_trend_ribbon,
    ema,
    gmma,
    ichimoku,
    luxalgo_normalized,
    ma_ribbon,
    macd,
    madrid_ma_ribbon_state,
    mansfield_relative_strength,
    nadaraya_watson_endpoint,
    nadaraya_watson_repainting,
    nwe_color_and_arrows,
    obv_oscillator,
    obv_oscillator_dual_ema,
    parabolic_sar,
    price_action_index,
    risk_indicator,
    rma,
    rsi_wilder,
    sma,
    squeeze_momentum_lazybear,
    sr_breaks_retests,
    stoch_momentum_index,
    supertrend,
    true_range,
    turtle_trade_channels,
    ut_bot_alert,
    wavetrend_lazybear,
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
        from trading_dashboard.data.enrichment import translate_and_compute_indicators
        from trading_dashboard.kpis.catalog import compute_kpi_state_map

        enriched, _ = translate_and_compute_indicators(sample_ohlcv)
        states = compute_kpi_state_map(enriched)
        assert isinstance(states, dict)
        assert len(states) > 0
        for name, series in states.items():
            valid = series.dropna()
            assert set(valid.unique()).issubset({-2, -1, 0, 1}), f"KPI {name} has invalid states"


class TestEdgeCases:
    def test_empty_dataframe(self):
        pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
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


# ══════════════════════════════════════════════════════════════════════════════
# Stoof (Band Light) indicator tests
# ══════════════════════════════════════════════════════════════════════════════

class TestStoofMACDBL:
    """BL1: MACD with EMA signal line (Pine ta.macd semantics)."""

    def test_ema_signal_differs_from_sma(self, sample_ohlcv):
        line_ema, sig_ema, hist_ema = macd(sample_ohlcv["Close"], 15, 23, 5, signal_ma="EMA")
        line_sma, sig_sma, hist_sma = macd(sample_ohlcv["Close"], 15, 23, 5, signal_ma="SMA")
        assert line_ema.equals(line_sma), "MACD line should be identical regardless of signal_ma"
        assert not sig_ema.equals(sig_sma), "EMA and SMA signal lines must differ"
        assert not hist_ema.equals(hist_sma), "Histograms must differ when signal smoothing differs"

    def test_backward_compatible_default(self, sample_ohlcv):
        _, sig_default, _ = macd(sample_ohlcv["Close"], 12, 26, 9)
        _, sig_sma, _ = macd(sample_ohlcv["Close"], 12, 26, 9, signal_ma="SMA")
        assert sig_default.equals(sig_sma), "Default signal_ma must be SMA for backward compat"

    def test_output_shape(self, sample_ohlcv):
        line, sig, hist = macd(sample_ohlcv["Close"], 15, 23, 5, signal_ma="EMA")
        assert len(line) == len(sample_ohlcv)
        assert line.notna().sum() > 50
        assert hist.notna().sum() > 50


class TestStoofWaveTrendBL:
    """BL2: WaveTrend using close instead of hlc3."""

    def test_close_source_differs_from_hlc3(self, sample_ohlcv):
        wt1_hlc3, _, _ = wavetrend_lazybear(sample_ohlcv, n1=27, n2=21, source="hlc3")
        wt1_close, _, _ = wavetrend_lazybear(sample_ohlcv, n1=27, n2=21, source="close")
        assert not wt1_hlc3.equals(wt1_close), "close vs hlc3 source must produce different values"

    def test_backward_compatible_default(self, sample_ohlcv):
        wt1_default, _, _ = wavetrend_lazybear(sample_ohlcv, n1=10, n2=21)
        wt1_hlc3, _, _ = wavetrend_lazybear(sample_ohlcv, n1=10, n2=21, source="hlc3")
        assert wt1_default.equals(wt1_hlc3), "Default source must be hlc3 for backward compat"

    def test_output_shape(self, sample_ohlcv):
        wt1, wt2, hist = wavetrend_lazybear(sample_ohlcv, n1=27, n2=21, source="close")
        assert len(wt1) == len(sample_ohlcv)
        assert wt1.notna().sum() > 50


class TestStoofOBVDualEMA:
    """BL3: OBV Oscillator with dual-EMA crossover."""

    def test_basic_output(self, sample_ohlcv):
        obv, osc = obv_oscillator_dual_ema(sample_ohlcv, short_length=1, long_length=20)
        assert len(obv) == len(sample_ohlcv)
        assert obv.notna().sum() > 100
        assert osc.notna().sum() > 100

    def test_short1_equals_obv_minus_ema(self, sample_ohlcv):
        obv, osc = obv_oscillator_dual_ema(sample_ohlcv, short_length=1, long_length=20)
        obv_ema20 = ema(obv, 20)
        ema1 = ema(obv, 1)
        expected = ema1 - obv_ema20
        diff = (osc - expected).dropna().abs()
        assert (diff < 1e-6).all(), "short=1 EMA should approximate raw OBV"


class TestStoofCCIChopBB:
    """BL4/BL9: CCI+Chop+BB composite oscillator (v1 and v2 params)."""

    def test_v1_output_range(self, sample_ohlcv):
        raw, smooth = cci_chop_bb(sample_ohlcv, cci_length=18, chop_length=14, bb_length=20, bb_mult=2.0, smooth=10)
        assert len(raw) == len(sample_ohlcv)
        assert smooth.notna().sum() > 50

    def test_v2_different_from_v1(self, sample_ohlcv):
        _, s1 = cci_chop_bb(sample_ohlcv, cci_length=18, chop_length=14, bb_length=20)
        _, s2 = cci_chop_bb(sample_ohlcv, cci_length=90, chop_length=24, bb_length=10)
        assert not s1.equals(s2), "Different params must produce different results"

    def test_smooth_is_ema_of_raw(self, sample_ohlcv):
        raw, smooth = cci_chop_bb(sample_ohlcv, smooth=10)
        expected = ema(raw, 10)
        diff = (smooth - expected).dropna().abs()
        assert (diff < 1e-6).all()


class TestStoofADXBL:
    """BL5: ADX & DI with RMA smoothing for ADX (Pine ta.rma semantics)."""

    def test_rma_differs_from_sma(self, sample_ohlcv):
        adx_sma, dip_s, dim_s = adx_di(sample_ohlcv, 14, adx_smoothing="SMA")
        adx_rma, dip_r, dim_r = adx_di(sample_ohlcv, 14, adx_smoothing="RMA")
        assert dip_s.equals(dip_r), "DI+ must be identical regardless of ADX smoothing"
        assert dim_s.equals(dim_r), "DI- must be identical regardless of ADX smoothing"
        assert not adx_sma.equals(adx_rma), "RMA and SMA ADX must differ"

    def test_backward_compatible_default(self, sample_ohlcv):
        adx_default, _, _ = adx_di(sample_ohlcv, 14)
        adx_sma, _, _ = adx_di(sample_ohlcv, 14, adx_smoothing="SMA")
        assert adx_default.equals(adx_sma), "Default adx_smoothing must be SMA for backward compat"

    def test_adx_positive(self, sample_ohlcv):
        adx_val, _, _ = adx_di(sample_ohlcv, 14, adx_smoothing="RMA")
        valid = adx_val.dropna()
        assert (valid >= 0).all(), "ADX must be non-negative"


class TestStoofLuxAlgoNormalized:
    """BL6/BL8: LuxAlgo multi-scale normalized oscillator."""

    def test_output_range(self, sample_ohlcv):
        result = luxalgo_normalized(sample_ohlcv["Close"], length=14, presmooth=10, postsmooth=10)
        valid = result.dropna()
        assert len(valid) > 50
        assert valid.min() >= -5.0, "Should be roughly 0-100 (some overshoot possible)"
        assert valid.max() <= 105.0

    def test_v1_equals_v2_with_same_params(self, sample_ohlcv):
        v1 = luxalgo_normalized(sample_ohlcv["Close"], length=14, presmooth=10, postsmooth=10)
        v2 = luxalgo_normalized(sample_ohlcv["Close"], length=14, presmooth=10, postsmooth=10)
        assert v1.equals(v2), "Same params should produce identical output (Pine v1==v2 by design)"

    def test_different_params_differ(self, sample_ohlcv):
        v1 = luxalgo_normalized(sample_ohlcv["Close"], length=14, presmooth=10, postsmooth=10)
        v2 = luxalgo_normalized(sample_ohlcv["Close"], length=20, presmooth=5, postsmooth=15)
        assert not v1.equals(v2)


class TestStoofRiskIndicator:
    """BL7: Risk Indicator — normalized log deviation."""

    def test_output_range(self, sample_ohlcv):
        result = risk_indicator(sample_ohlcv["Close"], sma_period=50, power_factor=0.395, initial_atl=2.5)
        valid = result.dropna()
        assert len(valid) > 50
        assert valid.min() >= 0.0 - 1e-9
        assert valid.max() <= 1.0 + 1e-9

    def test_sensitive_to_power_factor(self, sample_ohlcv):
        r1 = risk_indicator(sample_ohlcv["Close"], power_factor=0.395)
        r2 = risk_indicator(sample_ohlcv["Close"], power_factor=0.5)
        assert not r1.equals(r2)


class TestStoofPAI:
    """BL10: Price Action Index — stoch * dispersion."""

    def test_output_shape(self, sample_ohlcv):
        result = price_action_index(sample_ohlcv, stoch_length=20, smooth=3, dispersion_length=20)
        assert len(result) == len(sample_ohlcv)
        valid = result.dropna()
        assert len(valid) > 50

    def test_sign_has_both_positive_and_negative(self, sample_ohlcv):
        result = price_action_index(sample_ohlcv)
        valid = result.dropna()
        assert (valid > 0).any(), "Should have some positive (bullish) values"
        assert (valid < 0).any(), "Should have some negative (bearish) values"


class TestStoofKPIStates:
    """Integration: Stoof KPI states from enriched data."""

    def test_stoof_states_valid(self, sample_ohlcv):
        from trading_dashboard.data.enrichment import translate_and_compute_indicators
        from trading_dashboard.kpis.catalog import compute_kpi_state_map

        enriched, _ = translate_and_compute_indicators(sample_ohlcv)
        states = compute_kpi_state_map(enriched)

        stoof_kpis = [
            "MACD_BL", "WT_LB_BL", "OBVOSC_BL", "CCI_Chop_BB_v1",
            "ADX_DI_BL", "LuxAlgo_Norm_v1", "Risk_Indicator",
            "LuxAlgo_Norm_v2", "CCI_Chop_BB_v2", "PAI",
        ]
        for name in stoof_kpis:
            assert name in states, f"Missing Stoof KPI state: {name}"
            valid = states[name].dropna()
            assert set(valid.unique()).issubset({-2, -1, 0, 1}), f"KPI {name} has invalid states"
            assert len(valid) > 0, f"KPI {name} has no valid states"
