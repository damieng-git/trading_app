# Pine Script → Python mapping

This file documents how each PineScript indicator was translated to Python.

## Data source and timeframes

- Data source: `yfinance`
- Timeframes (4 active):
  - `1D`: `1d` candles from Yahoo
  - `1W`: `1d` candles resampled to weekly (`W-FRI`)
  - `2W`: `1d` candles resampled to bi-weekly
  - `1M`: `1d` candles resampled to monthly
  - ~~`4H`~~: removed
- OHLCV aggregation rules: open=first, high=max, low=min, close=last, volume=sum

Symbol lists are maintained in `apps/dashboard/configs/lists/*.csv`.

## Input PineScripts

Original Pine Script source files are in `docs/pinescripts/`.

## Translations implemented

Implemented indicators (computed on each selected timeframe):

- **Nadaraya-Watson Smoothers [LuxAlgo]** (`NW_LuxAlgo`, Trend) — KPI: Nadaraya-Watson Smoother (trend)
- **Turtle Trade Channels** (`TuTCI`, Trend) — KPI: TuTCI (trend)
- **MA Ribbon (4 MAs)** (`MA_Ribbon`, Trend) — KPI: MA Ribbon (trend)
- **Madrid MA Ribbon Bar v2** (`MadridRibbon`, Trend) — KPI: Madrid Ribbon (trend)
- **Donchian Trend Ribbon** (`DonchianRibbon`, Trend) — KPI: Donchian Ribbon (trend)
- **Double EMA (DEMA, 9)** (`DEMA`, Trend) — KPI: DEMA (trend)
- **Ichimoku Kinkō Hyō** (`Ichimoku`, Trend) — KPI: Ichimoku (trend)
- **GK Trend Ribbon** (`GK_Trend`, Trend) — KPI: GK Trend Ribbon (trend)
- **Impulse Trend Levels** (`Impulse_Trend`, Trend) — KPI: Impulse Trend (trend)
- **WaveTrend [LazyBear]** (`WT_LB`, Momentum) — KPI: WT_LB (trend)
- **Squeeze Momentum [LazyBear]** (`SQZMOM_LB`, Momentum) — KPI: SQZMOM_LB (trend)
- **Stochastic Momentum Index** (`SMI`, Momentum) — KPI: Stoch_MTM (trend)
- **MACD (12, 26, 9)** (`MACD`, Momentum) — KPI: CM_Ult_MacD_MFT (trend)
- **cRSI** (`cRSI`, Momentum) — KPI: cRSI (trend)
- **ADX & DI (14)** (`ADX_DI`, Momentum) — KPI: ADX & DI (trend)
- **GMMA (EMAs)** (`GMMA`, Momentum) — KPI: GMMA (trend)
- **RSI Strength & Consolidation Zones (Zeiierman)** (`RSI_Zeiierman`, Momentum) — KPI: RSI Strength & Consolidation Zones (Zeiierman) (trend)
- **OBV Oscillator (20)** (`OBVOSC`, Momentum) — KPI: OBVOSC_LB (trend)
- **Mansfield Relative Strength** (`Mansfield_RS`, Relative Strength) — KPI: Mansfield RS (trend)
- **SR Breaks & Retests** (`SR_Breaks`, Relative Strength) — KPI: SR Breaks (trend)
- **Bollinger Bands (20, 2.0)** (`BB`, Breakout) — KPI: BB 30 (breakout)
- **Nadaraya-Watson Envelope (MAE bands)** (`NWE_Envelope_MAE`, Breakout) — KPI: Nadaraya-Watson Envelop (MAE) (breakout)
- **Nadaraya-Watson Envelope (STD bands)** (`NWE_Envelope_STD`, Breakout) — KPI: Nadaraya-Watson Envelop (STD) (breakout)
- **Nadaraya-Watson Envelope (repainting)** (`NWE_Envelope_RP`, Breakout) — KPI: Nadaraya-Watson Envelop (Repainting) (breakout)
- **SuperTrend (12, 3.0)** (`SuperTrend`, Risk / Exit) — KPI: SuperTrend (trend)
- **UT Bot Alerts** (`UT_Bot`, Risk / Exit) — KPI: UT Bot Alert (trend)
- **Parabolic SAR** (`PSAR`, Risk / Exit) — KPI: CM_P-SAR (trend)
- **Volume + MA20** (`VOL_MA`, Momentum) — KPI: Volume + MA20 (trend)
- **ATR Stop Loss Finder** (`ATR`, Other)
- **MACD (15, 23, 5) [BL]** (`MACD_BL`, Momentum) — KPI: MACD_BL (trend)
  - Columns: `MACD_BL`, `MACD_BL_hist`, `MACD_BL_signal`
- **WaveTrend (27, 21) [BL]** (`WT_LB_BL`, Momentum) — KPI: WT_LB_BL (trend)
  - Columns: `WT_LB_BL_wt1`, `WT_LB_BL_wt2`
- **OBV Oscillator Dual-EMA [BL]** (`OBVOSC_BL`, Momentum) — KPI: OBVOSC_BL (trend)
  - Columns: `OBVOSC_BL_osc`
- **CCI+Chop+BB v1 [BL]** (`CCI_Chop_BB_v1`, Momentum) — KPI: CCI_Chop_BB_v1 (trend)
  - Columns: `CCI_Chop_BB_v1_smooth`
- **ADX & DI (14) [BL]** (`ADX_DI_BL`, Trend) — KPI: ADX_DI_BL (trend)
  - Columns: `ADX_BL`, `DI_plus_BL`, `DI_minus_BL`
- **LuxAlgo Normalized v1 [BL]** (`LuxAlgo_Norm_v1`, Momentum) — KPI: LuxAlgo_Norm_v1 (trend)
  - Columns: `LuxAlgo_Norm_v1`
- **Risk Indicator [BL]** (`Risk_Indicator`, Risk / Exit) — KPI: Risk_Indicator (trend)
  - Columns: `Risk_Indicator`
- **LuxAlgo Normalized v2 [BL]** (`LuxAlgo_Norm_v2`, Momentum) — KPI: LuxAlgo_Norm_v2 (trend)
  - Columns: `LuxAlgo_Norm_v2`
- **CCI+Chop+BB v2 [BL]** (`CCI_Chop_BB_v2`, Momentum) — KPI: CCI_Chop_BB_v2 (trend)
  - Columns: `CCI_Chop_BB_v2_smooth`
- **SMA Context [A]** (`ARCHA_G1`, Trend) — KPI: SuperTrend (trend)
- **RSI Dip [A]** (`ARCHA_G2`, Momentum) — KPI: cRSI (trend)
- **MACD Rev. [A]** (`ARCHA_G4`, Momentum) — KPI: CM_Ult_MacD_MFT (trend)
- **Price Action Index [BL]** (`PAI`, Momentum) — KPI: PAI (trend)
  - Columns: `PAI`
- **WT MTF Signal [PlungerMen]** (`WT_MTF`, Momentum) — KPI: WT_MTF (trend)
  - Columns: `WT_MTF_wt1`, `WT_MTF_wt2`, `WT_MTF_signal`, `WT_MTF_rsi`
