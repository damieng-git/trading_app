# Best Combo by Sector — Discovery Report

**Timeframe:** 1W | **Horizon:** 4 bars (4w) | **Min trades:** 15

## Summary Table

| Sector | Level | Global Combo HR | Sector Combo HR | Improvement | Sector KPIs |
|--------|-------|-----------------|-----------------|-------------|-------------|
| ALL | C3 | 70% | 100% | +30% | Nadaraya-Watson Smoother, Madrid Ribbon, Nadaraya-Watson Envelop (MAE) |
| ALL | C4 | 70% | 100% | +30% | Nadaraya-Watson Smoother, Madrid Ribbon, Donchian Ribbon, Nadaraya-Watson Envelop (MAE) |
| ALL | C5 | 67% | 94% | +27% | Nadaraya-Watson Smoother, cRSI, MA Ribbon, Madrid Ribbon, BB 30 |
| Communication Services | C3 | — | 82% |  | cRSI, Stoch_MTM, Ichimoku |
| Communication Services | C4 | — | 100% |  | cRSI, Stoch_MTM, CM_Ult_MacD_MFT, Ichimoku |
| Communication Services | C5 | — | 100% |  | Nadaraya-Watson Smoother, cRSI, Stoch_MTM, CM_Ult_MacD_MFT, Ichimoku |
| Consumer Cyclical | C3 | 79% | 79% | +0% | Nadaraya-Watson Smoother, cRSI, SR Breaks |
| Consumer Cyclical | C4 | — | 79% |  | Nadaraya-Watson Smoother, cRSI, SR Breaks, DEMA |
| Consumer Cyclical | C5 | — | 79% |  | Nadaraya-Watson Smoother, cRSI, SR Breaks, CM_Ult_MacD_MFT, DEMA |
| Consumer Defensive | C3 | 75% | 88% | +12% | cRSI, SR Breaks, Madrid Ribbon |
| Consumer Defensive | C4 | 79% | 88% | +9% | Nadaraya-Watson Smoother, cRSI, SR Breaks, Madrid Ribbon |
| Consumer Defensive | C5 | 76% | 88% | +12% | Nadaraya-Watson Smoother, cRSI, SR Breaks, Stoch_MTM, Madrid Ribbon |
| Financial Services | C3 | 84% | 85% | +1% | Nadaraya-Watson Smoother, SR Breaks, SQZMOM_LB |
| Financial Services | C4 | — | 85% |  | Nadaraya-Watson Smoother, SR Breaks, SQZMOM_LB, Ichimoku |
| Financial Services | C5 | — | 85% |  | Nadaraya-Watson Smoother, SR Breaks, SQZMOM_LB, Ichimoku, Madrid Ribbon |
| Healthcare | C3 | 50% | 88% | +38% | Stoch_MTM, Madrid Ribbon, Donchian Ribbon |
| Healthcare | C4 | — | 88% |  | Nadaraya-Watson Smoother, Stoch_MTM, Madrid Ribbon, Donchian Ribbon |
| Healthcare | C5 | — | 88% |  | Nadaraya-Watson Smoother, Stoch_MTM, ADX & DI, Madrid Ribbon, Donchian Ribbon |
| Industrials | C3 | 72% | 72% | +0% | Nadaraya-Watson Smoother, cRSI, SR Breaks |
| Industrials | C4 | 69% | 74% | +4% | Nadaraya-Watson Smoother, cRSI, SR Breaks, CM_P-SAR |
| Industrials | C5 | 69% | 74% | +4% | Nadaraya-Watson Smoother, cRSI, SR Breaks, CM_P-SAR, CM_Ult_MacD_MFT |
| Technology | C3 | 73% | 79% | +6% | Nadaraya-Watson Smoother, SR Breaks, CM_P-SAR |
| Technology | C4 | 75% | 82% | +7% | Nadaraya-Watson Smoother, SR Breaks, CM_P-SAR, DEMA |
| Technology | C5 | 75% | 82% | +7% | Nadaraya-Watson Smoother, SR Breaks, CM_P-SAR, WT_LB, DEMA |

## Sector-Specific Combo Definitions

These definitions can be loaded by the dashboard to replace the global combo for each stock:

```json
{
  "Communication Services": {
    "combo_3": [
      "cRSI",
      "Stoch_MTM",
      "Ichimoku"
    ],
    "combo_4": [
      "cRSI",
      "Stoch_MTM",
      "CM_Ult_MacD_MFT",
      "Ichimoku"
    ],
    "combo_5": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "Stoch_MTM",
      "CM_Ult_MacD_MFT",
      "Ichimoku"
    ]
  },
  "Consumer Cyclical": {
    "combo_3": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "SR Breaks"
    ],
    "combo_4": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "SR Breaks",
      "DEMA"
    ],
    "combo_5": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "SR Breaks",
      "CM_Ult_MacD_MFT",
      "DEMA"
    ]
  },
  "Consumer Defensive": {
    "combo_3": [
      "cRSI",
      "SR Breaks",
      "Madrid Ribbon"
    ],
    "combo_4": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "SR Breaks",
      "Madrid Ribbon"
    ],
    "combo_5": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "SR Breaks",
      "Stoch_MTM",
      "Madrid Ribbon"
    ]
  },
  "Financial Services": {
    "combo_3": [
      "Nadaraya-Watson Smoother",
      "SR Breaks",
      "SQZMOM_LB"
    ],
    "combo_4": [
      "Nadaraya-Watson Smoother",
      "SR Breaks",
      "SQZMOM_LB",
      "Ichimoku"
    ],
    "combo_5": [
      "Nadaraya-Watson Smoother",
      "SR Breaks",
      "SQZMOM_LB",
      "Ichimoku",
      "Madrid Ribbon"
    ]
  },
  "Healthcare": {
    "combo_3": [
      "Stoch_MTM",
      "Madrid Ribbon",
      "Donchian Ribbon"
    ],
    "combo_4": [
      "Nadaraya-Watson Smoother",
      "Stoch_MTM",
      "Madrid Ribbon",
      "Donchian Ribbon"
    ],
    "combo_5": [
      "Nadaraya-Watson Smoother",
      "Stoch_MTM",
      "ADX & DI",
      "Madrid Ribbon",
      "Donchian Ribbon"
    ]
  },
  "Industrials": {
    "combo_3": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "SR Breaks"
    ],
    "combo_4": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "SR Breaks",
      "CM_P-SAR"
    ],
    "combo_5": [
      "Nadaraya-Watson Smoother",
      "cRSI",
      "SR Breaks",
      "CM_P-SAR",
      "CM_Ult_MacD_MFT"
    ]
  },
  "Technology": {
    "combo_3": [
      "Nadaraya-Watson Smoother",
      "SR Breaks",
      "CM_P-SAR"
    ],
    "combo_4": [
      "Nadaraya-Watson Smoother",
      "SR Breaks",
      "CM_P-SAR",
      "DEMA"
    ],
    "combo_5": [
      "Nadaraya-Watson Smoother",
      "SR Breaks",
      "CM_P-SAR",
      "WT_LB",
      "DEMA"
    ]
  }
}
```

## Interpretation

- **Sector-optimized combos** are found via exhaustive search over all C(22,k) combinations.
- **Improvement** shows the hit-rate gain vs. the global combo applied to that sector's stocks.
- Sectors with large improvement benefit most from sector-specific tuning.
- Sectors where global and sector combos are similar confirm the global combo is robust.
