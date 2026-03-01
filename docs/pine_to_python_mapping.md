# Pine Script → Python mapping

This file documents how each PineScript indicator was translated to Python.

## Symbols and data source

- Data source: `yfinance`
- Timeframes:
  - `4H`: built from `60m` candles resampled to 4-hour OHLCV
  - `1D`: `1d` candles from Yahoo
  - `1W`: `1d` candles resampled to weekly (`W-FRI`)
- OHLCV aggregation rules: open=first, high=max, low=min, close=last, volume=sum

| Display symbol | yfinance ticker used | Attempts |
|---|---|---|
| 000001.SS | 000001.SS | 000001.SS |
| 3BRL.L | 3BRL.L | 3BRL.L |
| ACA.PA | ACA.PA | ACA.PA |
| AIR.PA | AIR.PA | AIR.PA |
| ALREW.PA | ALREW.PA | ALREW.PA |
| ALV.DE | ALV.DE | ALV.DE |
| AMN | AMN | AMN |
| ATE.PA | ATE.PA | ATE.PA |
| BA.L | BA.L | BA.L |
| BETZ | BETZ | BETZ |
| BMPS.MI | BMPS.MI | BMPS.MI |
| BNK.PA | BNK.PA | BNK.PA |
| BNP.PA | BNP.PA | BNP.PA |
| BPE.MI | BPE.MI | BPE.MI |
| CAC.PA | CAC.PA | CAC.PA |
| CLDD.DE | CLDD.DE | CLDD.DE |
| CLDX | CLDX | CLDX |
| CNI | CNI | CNI |
| COCO.L | COCO.L | COCO.L |
| COTN.SW | COTN.SW | COTN.SW |
| CSPX.AS | CSPX.AS | CSPX.AS |
| CVGI | CVGI | CVGI |
| CW8.PA | CW8.PA | CW8.PA |
| CYBR | CYBR | CYBR |
| DANSKE.CO | DANSKE.CO | DANSKE.CO |
| DASH | DASH | DASH |
| DPGA.DE | DPGA.DE | DPGA.DE |
| DSY.PA | DSY.PA | DSY.PA |
| DX-Y.NYB | DX-Y.NYB | DX-Y.NYB |
| EGLN.L | EGLN.L | EGLN.L |
| EIDO | EIDO | EIDO |
| ENGI.PA | ENGI.PA | ENGI.PA |
| EPR | EPR | EPR |
| ERA.PA | ERA.PA | ERA.PA |
| EXSD.DE | EXSD.DE | EXSD.DE |
| EXV5.DE | EXV5.DE | EXV5.DE |
| EXV7.DE | EXV7.DE | EXV7.DE |
| FDJ.PA | FDJ.PA | FDJ.PA |
| FINX | FINX | FINX |
| FTSEMIB.MI | FTSEMIB.MI | FTSEMIB.MI |
| GDX | GDX | GDX |
| HLT.PA | HLT.PA | HLT.PA |
| HWDN.L | HWDN.L | HWDN.L |
| IBB | IBB | IBB |
| IGV | IGV | IGV |
| IHI | IHI | IHI |
| INS.PA | INS.PA | INS.PA |
| IPAY | IPAY | IPAY |
| IPS.PA | IPS.PA | IPS.PA |
| IRDM | IRDM | IRDM |
| ITA | ITA | ITA |
| IUCD.L | IUCD.L | IUCD.L |
| IUCS.L | IUCS.L | IUCS.L |
| IUES.L | IUES.L | IUES.L |
| IUFS.L | IUFS.L | IUFS.L |
| IUIS.L | IUIS.L | IUIS.L |
| IUSB | IUSB | IUSB |
| IUSS.DE | IUSS.DE | IUSS.DE |
| IUUS.L | IUUS.L | IUUS.L |
| JACK | JACK | JACK |
| KBE | KBE | KBE |
| KFTK.DE | KFTK.DE | KFTK.DE |
| KGRN | KGRN | KGRN |
| KNIN.SW | KNIN.SW | KNIN.SW |
| LPL | LPL | LPL |
| LULU | LULU | LULU |
| LYP6.DE | LYP6.DE | LYP6.DE |
| MBUU | MBUU | MBUU |
| MEI | MEI | MEI |
| MGPI | MGPI | MGPI |
| MOH | MOH | MOH |
| MRNA | MRNA | MRNA |
| MSFT | MSFT | MSFT |
| MTU.PA | MTU.PA | MTU.PA |
| NATO.L | NATO.L | NATO.L |
| NEOG | NEOG | NEOG |
| NKE | NKE | NKE |
| OFLX | OFLX | OFLX |
| ORA.PA | ORA.PA | ORA.PA |
| PACB | PACB | PACB |
| PLUG | PLUG | PLUG |
| PRTA | PRTA | PRTA |
| PUM.DE | PUM.DE | PUM.DE |
| QDV5.DE | QDV5.DE | QDV5.DE |
| QFIN | QFIN | QFIN |
| RCO.PA | RCO.PA | RCO.PA |
| RI.PA | RI.PA | RI.PA |
| RIZF.DE | RIZF.DE | RIZF.DE |
| ROG | ROG | ROG |
| SC06.DE | SC06.DE | SC06.DE |
| SMH | SMH | SMH |
| SMIN | SMIN | SMIN |
| STZ | STZ | STZ |
| SUGA.L | SUGA.L | SUGA.L |
| SWBI | SWBI | SWBI |
| TGT | TGT | TGT |
| THRM | THRM | THRM |
| TNO.PA | TNO.PA | TNO.PA |
| TRGP | TRGP | TRGP |
| VEGI | VEGI | VEGI |
| VRT | VRT | VRT |
| W3B3.DE | W3B3.DE | W3B3.DE |
| WIG20.WA | WIG20.WA | WIG20.WA |
| XLB | XLB | XLB |
| XLC | XLC | XLC |
| XLE | XLE | XLE |
| XLF | XLF | XLF |
| XLI | XLI | XLI |
| XLK | XLK | XLK |
| XLP | XLP | XLP |
| XLRE | XLRE | XLRE |
| XLU | XLU | XLU |
| XLV | XLV | XLV |
| XLY | XLY | XLY |
| XOP | XOP | XOP |
| XPEV | XPEV | XPEV |
| XPH | XPH | XPH |
| XS8R.DE | XS8R.DE | XS8R.DE |
| YOU | YOU | YOU |
| ^AEX | ^AEX | ^AEX |
| ^ATX | ^ATX | ^ATX |
| ^AXJO | ^AXJO | ^AXJO |
| ^BFX | ^BFX | ^BFX |
| ^BSESN | ^BSESN | ^BSESN |
| ^BVSP | ^BVSP | ^BVSP |
| ^DJI | ^DJI | ^DJI |
| ^FCHI | ^FCHI | ^FCHI |
| ^FTSE | ^FTSE | ^FTSE |
| ^GDAXI | ^GDAXI | ^GDAXI |
| ^GSPC | ^GSPC | ^GSPC |
| ^GSPTSE | ^GSPTSE | ^GSPTSE |
| ^HSI | ^HSI | ^HSI |
| ^IBEX | ^IBEX | ^IBEX |
| ^IXIC | ^IXIC | ^IXIC |
| ^KS11 | ^KS11 | ^KS11 |
| ^MXX | ^MXX | ^MXX |
| ^N225 | ^N225 | ^N225 |
| ^NZ50 | ^NZ50 | ^NZ50 |
| ^OBX | ^OBX | ^OBX |
| ^OMXC25 | ^OMXC25 | ^OMXC25 |
| ^OMXH25 | ^OMXH25 | ^OMXH25 |
| ^RUT | ^RUT | ^RUT |
| ^SSMI | ^SSMI | ^SSMI |
| ^STI | ^STI | ^STI |
| ^STOXX50E | ^STOXX50E | ^STOXX50E |
| ^TWII | ^TWII | ^TWII |

## Input PineScripts (from RTF)

## Translations implemented (auto-generated from registry)

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
