"""
Indicator implementations (pandas/numpy).

This package is the shared indicator library used by:
- apps/dashboard (runtime dashboards)
- research (optimisation/harness)
- trading_dashboard/screener (market scanning)
"""

from trading_dashboard.indicators._base import (
    AtrSmoothing,
    atr,
    dema,
    ema,
    gaussian_weights,
    hlc3,
    highest,
    linreg,
    lowest,
    rma,
    rsi_wilder,
    sma,
    stdev,
    true_range,
    vwma,
    wma,
)
from trading_dashboard.indicators.wavetrend import wavetrend_lazybear
from trading_dashboard.indicators.macd import macd
from trading_dashboard.indicators.bollinger import bollinger_bands
from trading_dashboard.indicators.adx import adx_di
from trading_dashboard.indicators.supertrend import supertrend
from trading_dashboard.indicators.obv import obv_oscillator, obv_oscillator_dual_ema
from trading_dashboard.indicators.cci_chop_bb import cci_chop_bb
from trading_dashboard.indicators.luxalgo_normalized import luxalgo_normalized
from trading_dashboard.indicators.risk_indicator import risk_indicator
from trading_dashboard.indicators.price_action_index import price_action_index
from trading_dashboard.indicators.squeeze import squeeze_momentum_lazybear
from trading_dashboard.indicators.smi import stoch_momentum_index
from trading_dashboard.indicators.ut_bot import ut_bot_alert
from trading_dashboard.indicators.turtle import turtle_trade_channels
from trading_dashboard.indicators.atr_stop import atr_stop_loss_finder
from trading_dashboard.indicators.psar import parabolic_sar
from trading_dashboard.indicators.gmma import gmma
from trading_dashboard.indicators.ma_ribbon import ma_ribbon
from trading_dashboard.indicators.nadaraya_watson import (
    nadaraya_watson_endpoint,
    nadaraya_watson_envelope_endpoint,
    nadaraya_watson_envelope_luxalgo,
    nadaraya_watson_envelope_luxalgo_std,
    nadaraya_watson_repainting,
    nwe_color_and_arrows,
)
from trading_dashboard.indicators.crsi import crsi
from trading_dashboard.indicators.donchian import donchian_trend_ribbon
from trading_dashboard.indicators.madrid import madrid_ma_ribbon_state
from trading_dashboard.indicators.rsi_zeiierman import rsi_strength_consolidation_zeiierman
from trading_dashboard.indicators.ichimoku import ichimoku
from trading_dashboard.indicators.mansfield_rs import mansfield_relative_strength
from trading_dashboard.indicators.sr_breaks_retests import sr_breaks_retests
from trading_dashboard.indicators.gk_trend_ribbon import gk_trend_ribbon
from trading_dashboard.indicators.impulse_trend import impulse_trend_levels
from trading_dashboard.indicators.breakout_targets import breakout_targets
from trading_dashboard.indicators.wt_mtf_signal import wt_mtf_signal
