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
| 0JDK.L | 0JDK.L | 0JDK.L |
| 1810.HK | 1810.HK | 1810.HK |
| 3BRL.L | 3BRL.L | 3BRL.L |
| AAPL | AAPL | AAPL |
| ACA.PA | ACA.PA | ACA.PA |
| ACHC | ACHC | ACHC |
| ADAMN | ADAMN | ADAMN |
| ADBE | ADBE | ADBE |
| AGX | AGX | AGX |
| AIR.PA | AIR.PA | AIR.PA |
| ALGN | ALGN | ALGN |
| ALREW.PA | ALREW.PA | ALREW.PA |
| ALV.DE | ALV.DE | ALV.DE |
| AMN | AMN | AMN |
| ARVN | ARVN | ARVN |
| ATE.PA | ATE.PA | ATE.PA |
| ATO.PA | ATO.PA | ATO.PA |
| AVNT | AVNT | AVNT |
| BAS.DE | BAS.DE | BAS.DE |
| BETZ | BETZ | BETZ |
| BMPS.MI | BMPS.MI | BMPS.MI |
| BNK.PA | BNK.PA | BNK.PA |
| BNP.PA | BNP.PA | BNP.PA |
| BPE.MI | BPE.MI | BPE.MI |
| BRZE | BRZE | BRZE |
| BTC-USD | BTC-USD | BTC-USD |
| BWIN | BWIN | BWIN |
| CAC.PA | CAC.PA | CAC.PA |
| CBBHF | CBBHF | CBBHF |
| CCZ | CCZ | CCZ |
| CE | CE | CE |
| CLDD.DE | CLDD.DE | CLDD.DE |
| CMCSA | CMCSA | CMCSA |
| CNI | CNI | CNI |
| CNMD | CNMD | CNMD |
| COCO.L | COCO.L | COCO.L |
| COLM | COLM | COLM |
| COTN.SW | COTN.SW | COTN.SW |
| CRM | CRM | CRM |
| CRVL | CRVL | CRVL |
| CS.PA | CS.PA | CS.PA |
| CSAN | CSAN | CSAN |
| CSPX.AS | CSPX.AS | CSPX.AS |
| CSQ | CSQ | CSQ |
| CVGI | CVGI | CVGI |
| CW8.PA | CW8.PA | CW8.PA |
| CWT | CWT | CWT |
| CYBR | CYBR | CYBR |
| DANSKE.CO | DANSKE.CO | DANSKE.CO |
| DASH | DASH | DASH |
| DEO | DEO | DEO |
| DPGA.DE | DPGA.DE | DPGA.DE |
| DSGX | DSGX | DSGX |
| DSY.PA | DSY.PA | DSY.PA |
| DV | DV | DV |
| DX-Y.NYB | DX-Y.NYB | DX-Y.NYB |
| EGHT | EGHT | EGHT |
| EGLN.L | EGLN.L | EGLN.L |
| EIDO | EIDO | EIDO |
| ENGI.PA | ENGI.PA | ENGI.PA |
| ERA.PA | ERA.PA | ERA.PA |
| ETH-USD | ETH-USD | ETH-USD |
| EXH3D.XD | EXH3D.XD | EXH3D.XD |
| EXSD.DE | EXSD.DE | EXSD.DE |
| EXV5.DE | EXV5.DE | EXV5.DE |
| EXV7.DE | EXV7.DE | EXV7.DE |
| FCIT.L | FCIT.L | FCIT.L |
| FDJ.PA | FDJ.PA | FDJ.PA |
| FDM.L | FDM.L | FDM.L |
| FINX | FINX | FINX |
| FISV | FISV | FISV |
| FORR | FORR | FORR |
| FRME | FRME | FRME |
| FTSEMIB.MI | FTSEMIB.MI | FTSEMIB.MI |
| GDX | GDX | GDX |
| GFS | GFS | GFS |
| GFT.F | GFT.F | GFT.F |
| GOOG | GOOG | GOOG |
| GPRE | GPRE | GPRE |
| GTM | GTM | GTM |
| HAIN | HAIN | HAIN |
| HELP | HELP | HELP |
| HIG | HIG | HIG |
| HLT.PA | HLT.PA | HLT.PA |
| HRB | HRB | HRB |
| HUM | HUM | HUM |
| IBB | IBB | IBB |
| IDIA.SW | IDIA.SW | IDIA.SW |
| IFF | IFF | IFF |
| IGV | IGV | IGV |
| IHI | IHI | IHI |
| INCH.L | INCH.L | INCH.L |
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
| IUSB.MU | IUSB.MU | IUSB.MU |
| IUSS.DE | IUSS.DE | IUSS.DE |
| IUUS.L | IUUS.L | IUUS.L |
| JACK | JACK | JACK |
| KBE | KBE | KBE |
| KFTK.DE | KFTK.DE | KFTK.DE |
| KGRN | KGRN | KGRN |
| KLAR | KLAR | KLAR |
| KNIN.SW | KNIN.SW | KNIN.SW |
| KROS | KROS | KROS |
| KSPI | KSPI | KSPI |
| LI | LI | LI |
| LRMR | LRMR | LRMR |
| LSPD | LSPD | LSPD |
| LULU | LULU | LULU |
| LYP6.DE | LYP6.DE | LYP6.DE |
| MAAS | MAAS | MAAS |
| MBUU | MBUU | MBUU |
| MEI | MEI | MEI |
| MGPI | MGPI | MGPI |
| MOH | MOH | MOH |
| MRNA | MRNA | MRNA |
| MRTN | MRTN | MRTN |
| MSEX | MSEX | MSEX |
| MSFT | MSFT | MSFT |
| MTU.PA | MTU.PA | MTU.PA |
| NATO.L | NATO.L | NATO.L |
| NBTB | NBTB | NBTB |
| NBTK.MU | NBTK.MU | NBTK.MU |
| NEOG | NEOG | NEOG |
| NKE | NKE | NKE |
| NL | NL | NL |
| NVO | NVO | NVO |
| OFLX | OFLX | OFLX |
| ORA.PA | ORA.PA | ORA.PA |
| ORSTED.CO | ORSTED.CO | ORSTED.CO |
| PACB | PACB | PACB |
| PLUG | PLUG | PLUG |
| PRCT | PRCT | PRCT |
| PRTA | PRTA | PRTA |
| PSNY | PSNY | PSNY |
| PUBM | PUBM | PUBM |
| PUM.DE | PUM.DE | PUM.DE |
| QDV5.DE | QDV5.DE | QDV5.DE |
| QFIN | QFIN | QFIN |
| QSV.F | QSV.F | QSV.F |
| RBREW.CO | RBREW.CO | RBREW.CO |
| RCO.PA | RCO.PA | RCO.PA |
| REPL | REPL | REPL |
| RHI | RHI | RHI |
| RI.PA | RI.PA | RI.PA |
| RIZF.DE | RIZF.DE | RIZF.DE |
| RMAX | RMAX | RMAX |
| ROG | ROG | ROG |
| SB=F | SB=F | SB=F |
| SBET | SBET | SBET |
| SC06.DE | SC06.DE | SC06.DE |
| SENS | SENS | SENS |
| SMH | SMH | SMH |
| SMIN | SMIN | SMIN |
| SNBR | SNBR | SNBR |
| SNDR | SNDR | SNDR |
| STLAP.PA | STLAP.PA | STLAP.PA |
| STZ | STZ | STZ |
| SUGA.L | SUGA.L | SUGA.L |
| SWBI | SWBI | SWBI |
| TGT | TGT | TGT |
| THRM | THRM | THRM |
| TNDM | TNDM | TNDM |
| TNO.PA | TNO.PA | TNO.PA |
| TPH | TPH | TPH |
| TRGP | TRGP | TRGP |
| TSLA | TSLA | TSLA |
| UNH | UNH | UNH |
| USNA | USNA | USNA |
| USPH | USPH | USPH |
| VEGI | VEGI | VEGI |
| VIV.PA | VIV.PA | VIV.PA |
| VRT | VRT | VRT |
| W1TB.MU | W1TB.MU | W1TB.MU |
| W1TBD.XD | W1TBD.XD | W1TBD.XD |
| W3B3.DE | W3B3.DE | W3B3.DE |
| WIG20.WA | WIG20.WA | WIG20.WA |
| WIRUS.FGI | WIRUS.FGI | WIRUS.FGI |
| WST | WST | WST |
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
| ZAL.DE | ZAL.DE | ZAL.DE |
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
| ^IUSB | ^IUSB | ^IUSB |
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
- **Price Action Index [BL]** (`PAI`, Momentum) — KPI: PAI (trend)
  - Columns: `PAI`
- **WT MTF Signal [PlungerMen]** (`WT_MTF`, Momentum) — KPI: WT_MTF (trend)
  - Columns: `WT_MTF_wt1`, `WT_MTF_wt2`, `WT_MTF_signal`, `WT_MTF_rsi`
