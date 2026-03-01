# KPI & Combo Performance by Sector

**Timeframe:** 1W  
**Horizon:** 4 bars (4 weeks)  
**Minimum stocks per sector:** 5  

## Key Findings

**Sectors where combos work best:**
- **Financial Services**: C3 = 84.2% (vs 70.4% ALL)
- **Consumer Cyclical**: C3 = 78.8% (vs 70.4% ALL)
- **Consumer Defensive**: C5 = 75.8% (vs 70.4% ALL)
- **Technology**: C5 = 75.0% (vs 70.4% ALL)

**Sectors where combos underperform:**
- **Healthcare**: C3 = 50.0% (vs 70.4% ALL)


## Best KPIs per Sector

| Sector | #Stocks | Best KPI | HR | 2nd Best | HR | C3 HR | C5 HR |
|--------|---------|----------|-----|----------|-----|-------|-------|
| Communication Services | 12 | Stoch_MTM | 72% | Nadaraya-Watson Smoother | 70% | — | — |
| Consumer Cyclical | 26 | Nadaraya-Watson Envelop (MAE) | 69% | Nadaraya-Watson Smoother | 68% | 79% | — |
| Consumer Defensive | 9 | Nadaraya-Watson Smoother | 68% | Stoch_MTM | 58% | 75% | 76% |
| Financial Services | 25 | Nadaraya-Watson Envelop (MAE) | 77% | SR Breaks | 75% | 84% | — |
| Healthcare | 16 | Nadaraya-Watson Smoother | 66% | Stoch_MTM | 64% | 50% | — |
| Industrials | 25 | Nadaraya-Watson Smoother | 69% | Nadaraya-Watson Envelop (MAE) | 59% | 72% | 69% |
| Technology | 27 | Nadaraya-Watson Envelop (MAE) | 70% | Nadaraya-Watson Smoother | 68% | 73% | 75% |

## Recommendations

### General

- The current C3/C5 combo definitions use the **same KPIs for all sectors**.
- This analysis reveals whether sector-specific combo tuning could improve results.

### Sector-Specific Guidance

**Communication Services** (12 stocks):
- Top KPIs: Stoch_MTM (72%), Nadaraya-Watson Smoother (70%), Mansfield RS (60%)
- Consider sector-specific combo: Stoch_MTM + Nadaraya-Watson Smoother + Mansfield RS
- Combos underperform; consider sector-specific filtering

**Consumer Cyclical** (26 stocks):
- Top KPIs: Nadaraya-Watson Envelop (MAE) (69%), Nadaraya-Watson Smoother (68%), Mansfield RS (54%)
- Consider sector-specific combo: Nadaraya-Watson Envelop (MAE) + Nadaraya-Watson Smoother + Mansfield RS
- C3 combo is reliable (79%); C5 adds marginal value

**Consumer Defensive** (9 stocks):
- Top KPIs: Nadaraya-Watson Smoother (68%), Stoch_MTM (58%), SuperTrend (51%)
- Consider sector-specific combo: Nadaraya-Watson Smoother + Stoch_MTM + SuperTrend
- C5 combo is strong (76%); use with confidence

**Financial Services** (25 stocks):
- Top KPIs: Nadaraya-Watson Envelop (MAE) (77%), SR Breaks (75%), Nadaraya-Watson Smoother (68%)
- Current C3 combo aligns well with sector (2/3 overlap)
- C3 combo is reliable (84%); C5 adds marginal value

**Healthcare** (16 stocks):
- Top KPIs: Nadaraya-Watson Smoother (66%), Stoch_MTM (64%), Madrid Ribbon (61%)
- Consider sector-specific combo: Nadaraya-Watson Smoother + Stoch_MTM + Madrid Ribbon
- Combos underperform; consider sector-specific filtering

**Industrials** (25 stocks):
- Top KPIs: Nadaraya-Watson Smoother (69%), Nadaraya-Watson Envelop (MAE) (59%), Mansfield RS (58%)
- Consider sector-specific combo: Nadaraya-Watson Smoother + Nadaraya-Watson Envelop (MAE) + Mansfield RS
- C3 combo is reliable (72%); C5 adds marginal value

**Technology** (27 stocks):
- Top KPIs: Nadaraya-Watson Envelop (MAE) (70%), Nadaraya-Watson Smoother (68%), Mansfield RS (58%)
- Consider sector-specific combo: Nadaraya-Watson Envelop (MAE) + Nadaraya-Watson Smoother + Mansfield RS
- C5 combo is strong (75%); use with confidence

### Monitoring Approach

- **High-conviction sectors** (combo HR > 70%): Trade combos directly
- **Average sectors** (combo HR 55-70%): Use combos as filters, add confirmation
- **Low-conviction sectors** (combo HR < 55%): Consider sector-specific KPI combos
  or use sector ETFs as a regime filter before applying stock-level combos
