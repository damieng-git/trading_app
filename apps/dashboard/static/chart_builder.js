/**
 * chart_builder.js — Builds Plotly figures client-side from data JSON.
 *
 * Replaces server-side figure generation in figures.py.
 * Output is a standard Plotly figure {data, layout} compatible with splitFigure().
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ */
  /*  Helpers                                                            */
  /* ------------------------------------------------------------------ */

  function has(c, cols) {
    for (let i = 0; i < cols.length; i++) if (!(cols[i] in c)) return false;
    return true;
  }

  function col(c, name) { return c[name] || []; }

  /** Where mask is falsy, replace with null (Plotly gap). */
  function where(arr, mask) {
    const out = new Array(arr.length);
    for (let i = 0; i < arr.length; i++) out[i] = mask[i] ? arr[i] : null;
    return out;
  }

  /** Where condition == val, keep arr[i], else null. */
  function whereEq(arr, cond, val) {
    const out = new Array(arr.length);
    for (let i = 0; i < arr.length; i++) out[i] = cond[i] === val ? arr[i] : null;
    return out;
  }

  /** Boolean mask from column (truthy + not-null). */
  function boolMask(c, name) {
    const v = c[name];
    if (!v) return null;
    return v.map(x => x != null && x !== false && x !== 0);
  }

  /** Shift array left by 1 (first element = first original value). */
  function shift1(arr) {
    const out = new Array(arr.length);
    out[0] = arr[0];
    for (let i = 1; i < arr.length; i++) out[i] = arr[i - 1];
    return out;
  }

  /** Constant array */
  function constant(n, val) {
    const out = new Array(n);
    for (let i = 0; i < n; i++) out[i] = val;
    return out;
  }

  /** Create zeros array */
  function zeros(n) { return constant(n, 0); }

  /** Column or zeros */
  function colOr0(c, name) { return c[name] || zeros((c.Open || []).length); }

  /** Filter x array by boolean mask -> [x values where mask is true] */
  function filterByMask(x, mask) {
    const out = [];
    for (let i = 0; i < x.length; i++) if (mask[i]) out.push(x[i]);
    return out;
  }

  /** Filter arr by boolean mask */
  function filterArr(arr, mask) {
    const out = [];
    for (let i = 0; i < arr.length; i++) if (mask[i]) out.push(arr[i]);
    return out;
  }

  /** Element-wise NOT of boolean array */
  function bNot(a) { return a.map(v => !v); }

  /** rolling mean with min_periods=1 */
  function rollingMean(arr, win) {
    const out = new Array(arr.length);
    let sum = 0, cnt = 0;
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i];
      if (v != null && !isNaN(v)) { sum += v; cnt++; }
      if (i >= win) {
        const old = arr[i - win];
        if (old != null && !isNaN(old)) { sum -= old; cnt--; }
      }
      out[i] = cnt > 0 ? sum / cnt : 0;
    }
    return out;
  }

  /** Axis assignment for subplot row (1-based). */
  function ax(row) {
    if (row === 1) return { xaxis: "x", yaxis: "y" };
    return { xaxis: "x" + row, yaxis: "y" + row };
  }

  const SHORT_LABELS = {
    "Nadaraya-Watson Smoother": "NW Smooth",
    "Nadaraya-Watson Envelop (MAE)": "NWE MAE",
    "Nadaraya-Watson Envelop (STD)": "NWE STD",
    "Nadaraya-Watson Envelop (Repainting)": "NWE RP",
    "RSI Strength & Consolidation Zones (Zeiierman)": "RSI Zones",
    "RSI Strength & Consolidation Zones (Zeiierman) (breakout)": "RSI Zones (BO)",
    "Stoch_MTM": "SMI",
    "CM_Ult_MacD_MFT": "MACD",
    "CM_P-SAR": "P-SAR",
    "SQZMOM_LB": "Squeeze Mom",
    "OBVOSC_LB": "OBV Osc",
    "WT_LB": "WaveTrend",
    "Volume + MA20": "Volume/MA20",
    "Donchian Ribbon": "Donchian",
    "Madrid Ribbon": "Madrid",
    "UT Bot Alert": "UT Bot",
    "ADX & DI": "ADX/DI",
  };

  function shortLabel(s, maxLen) {
    maxLen = maxLen || 20;
    if (!s) return "";
    let s0 = s.trim();
    if (s0.startsWith("BO_")) s0 = s0.slice(3);
    s0 = SHORT_LABELS[s0] || s0;
    if (s0.length <= maxLen) return s0;
    return s0.slice(0, maxLen - 1).trimEnd() + "\u2026";
  }

  const LABEL = {
    Price: "Price",
    NW_Smoother: "Nadaraya-Watson Smoother",
    NW_Envelope_MAE: "Nadaraya-Watson Envelop (MAE)",
    NW_Envelope_STD: "Nadaraya-Watson Envelop (STD)",
    NW_Envelope_RP: "Nadaraya-Watson Envelop (Repainting)",
    BB: "BB 30",
    ATR: "ATR",
    SuperTrend: "SuperTrend",
    UT_Bot: "UT Bot Alert",
    TuTCI: "TuTCI",
    GMMA: "GMMA",
    MA_Ribbon: "MA Ribbon",
    MadridRibbon: "Madrid Ribbon",
    DonchianRibbon: "Donchian Ribbon",
    PSAR: "CM_P-SAR",
    DEMA: "DEMA",
    WT_LB: "WT_LB",
    ADX_DI: "ADX & DI",
    OBVOSC: "OBVOSC_LB",
    SQZMOM: "SQZMOM_LB",
    SMI: "Stoch_MTM",
    MACD: "CM_Ult_MacD_MFT",
    VOL_MA: "Volume + MA20",
    cRSI: "cRSI",
    RSI_Zei: "RSI Strength & Consolidation Zones (Zeiierman)",
    Ichimoku: "Ichimoku",
    Mansfield_RS: "Mansfield RS",
    SR_Breaks: "SR Breaks",
    GK_Trend: "GK Trend Ribbon",
    Impulse_Trend: "Impulse Trend",
    MACD_BL: "MACD_BL",
    WT_LB_BL: "WT_LB_BL",
    OBVOSC_BL: "OBVOSC_BL",
    CCI_Chop_BB_v1: "CCI_Chop_BB_v1",
    CCI_Chop_BB_v2: "CCI_Chop_BB_v2",
    ADX_DI_BL: "ADX_DI_BL",
    LuxAlgo_Norm_v1: "LuxAlgo_Norm_v1",
    LuxAlgo_Norm_v2: "LuxAlgo_Norm_v2",
    Risk_Indicator: "Risk_Indicator",
    PAI: "PAI",
    WT_MTF: "WT_MTF",
  };

  /* ------------------------------------------------------------------ */
  /*  Trace factory                                                      */
  /* ------------------------------------------------------------------ */

  function mkTrace(base, row, indicatorLabel, vis) {
    const t = Object.assign({}, base, ax(row));
    t.showlegend = false;
    t.meta = { indicator: indicatorLabel };
    t.visible = vis !== undefined ? vis : (indicatorLabel === LABEL.Price);
    return t;
  }

  /* ------------------------------------------------------------------ */
  /*  Main builder                                                       */
  /* ------------------------------------------------------------------ */

  function buildFigureFromData(data) {
    const x = data.x;
    const c = data.c;
    const tf = data.timeframe || "1D";
    const symbol = data.symbol || "";
    const displayName = data.display_name || "";
    const n = x.length;

    const _gcss = (v) => { try { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); } catch(e) { return ""; } };

    const traces = [];
    const shapes = [];

    /* ============================================================ */
    /*  ROW 1 — Price + overlays                                    */
    /* ============================================================ */

    // Candlestick
    traces.push(mkTrace({
      type: "candlestick", x: x,
      open: col(c, "Open"), high: col(c, "High"),
      low: col(c, "Low"), close: col(c, "Close"),
      name: "Price", hoverinfo: "skip",
    }, 1, LABEL.Price, true));

    // Hover-catcher with OHLCV tooltip
    const hoverCd = [];
    const O = col(c, "Open"), H = col(c, "High"), L = col(c, "Low"), C = col(c, "Close"), V = colOr0(c, "Volume");
    for (let i = 0; i < n; i++) hoverCd.push([O[i], H[i], L[i], C[i], V[i]]);
    traces.push(mkTrace({
      type: "scatter", x: x, y: C, mode: "markers",
      marker: { size: 18, opacity: 0 }, name: "",
      customdata: hoverCd,
      hovertemplate: "<b>O</b> %{customdata[0]:.2f}  <b>H</b> %{customdata[1]:.2f}<br>" +
                     "<b>L</b> %{customdata[2]:.2f}  <b>C</b> %{customdata[3]:.2f}<br>" +
                     "<b>Vol</b> %{customdata[4]:,.0f}<extra></extra>",
    }, 1, LABEL.Price, true));

    // --- NW Smoother ---
    if ("NW_LuxAlgo_value" in c || "NW_LuxAlgo_endpoint" in c) {
      const nwe = c.NW_LuxAlgo_value || c.NW_LuxAlgo_endpoint;
      const slopeUp = c.NW_LuxAlgo_color
        ? c.NW_LuxAlgo_color.map(v => String(v).toLowerCase() === "green")
        : nwe.map((v, i) => i > 0 ? v > nwe[i - 1] : true);
      traces.push(mkTrace({ type: "scatter", x: x, y: where(nwe, slopeUp), mode: "lines", line: { width: 2, color: "#22c55e" }, connectgaps: false }, 1, LABEL.NW_Smoother));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(nwe, bNot(slopeUp)), mode: "lines", line: { width: 2, color: "#ef4444" }, connectgaps: false }, 1, LABEL.NW_Smoother));
      if (c.NW_LuxAlgo_arrow_up && c.NW_LuxAlgo_arrow_down) {
        const up = boolMask(c, "NW_LuxAlgo_arrow_up"), dn = boolMask(c, "NW_LuxAlgo_arrow_down");
        if (up) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, up), y: filterArr(C, up), mode: "markers", marker: { symbol: "triangle-up", size: 10, color: "#22c55e" } }, 1, LABEL.NW_Smoother));
        if (dn) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, dn), y: filterArr(C, dn), mode: "markers", marker: { symbol: "triangle-down", size: 10, color: "#ef4444" } }, 1, LABEL.NW_Smoother));
      }
    }

    // --- NWE Envelopes (MAE, STD, RP) ---
    const envelopes = [
      { u: "NWE_MAE_env_upper", l: "NWE_MAE_env_lower", co: "NWE_MAE_env_crossover", cu: "NWE_MAE_env_crossunder", lbl: LABEL.NW_Envelope_MAE, cu1: "#22c55e", cl1: "#ef4444", cu2: "#ef4444", cl2: "#22c55e", w: 1.6 },
      { u: "NWE_STD_env_upper", l: "NWE_STD_env_lower", co: "NWE_STD_env_crossover", cu: "NWE_STD_env_crossunder", lbl: LABEL.NW_Envelope_STD, cu1: "#60a5fa", cl1: "#f59e0b", cu2: "#f59e0b", cl2: "#60a5fa", w: 1.6 },
      { u: "NWE_RP_env_upper", l: "NWE_RP_env_lower", co: "NWE_RP_env_crossover", cu: "NWE_RP_env_crossunder", lbl: LABEL.NW_Envelope_RP, cu1: "rgba(34,197,94,0.70)", cl1: "rgba(239,68,68,0.70)", cu2: "rgba(239,68,68,0.75)", cl2: "rgba(34,197,94,0.75)", w: 1.2, dash: "dot", ms: 9 },
    ];
    envelopes.forEach(e => {
      if (!has(c, [e.u, e.l])) return;
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, e.u), mode: "lines", line: { width: e.w, color: e.cu1, dash: e.dash } }, 1, e.lbl));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, e.l), mode: "lines", line: { width: e.w, color: e.cl1, dash: e.dash } }, 1, e.lbl));
      if (c[e.co]) {
        const m = boolMask(c, e.co);
        if (m) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, m), y: filterArr(H, m), mode: "markers", marker: { symbol: "triangle-down", size: e.ms || 10, color: e.cu2 } }, 1, e.lbl));
      }
      if (c[e.cu]) {
        const m = boolMask(c, e.cu);
        if (m) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, m), y: filterArr(L, m), mode: "markers", marker: { symbol: "triangle-up", size: e.ms || 10, color: e.cl2 } }, 1, e.lbl));
      }
    });

    // --- BB ---
    if (has(c, ["BB_basis", "BB_upper", "BB_lower"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "BB_basis"), mode: "lines", line: { width: 1.6, color: "#2962FF" } }, 1, LABEL.BB));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "BB_upper"), mode: "lines", line: { width: 1.2, color: "#F23645" } }, 1, LABEL.BB));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "BB_lower"), mode: "lines", line: { width: 1.2, color: "#089981" } }, 1, LABEL.BB));
    }

    // --- ATR ---
    if (has(c, ["ATR_short_stop", "ATR_long_stop"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "ATR_short_stop"), mode: "lines", line: { width: 1.2, color: "#ef4444", dash: "dot" } }, 1, LABEL.ATR));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "ATR_long_stop"), mode: "lines", line: { width: 1.2, color: "#22c55e", dash: "dot" } }, 1, LABEL.ATR));
    }

    // --- SuperTrend ---
    if (has(c, ["SuperTrend_line", "SuperTrend_trend"])) {
      const stLine = col(c, "SuperTrend_line"), stTrend = col(c, "SuperTrend_trend");
      const ohlc4 = O.map((v, i) => (v + H[i] + L[i] + C[i]) / 4);
      // Uptrend fill
      traces.push(mkTrace({ type: "scatter", x: x, y: ohlc4, mode: "lines", line: { width: 0, color: "rgba(0,0,0,0)" }, hoverinfo: "skip" }, 1, LABEL.SuperTrend));
      traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(stLine, stTrend, 1), mode: "lines", line: { width: 2, color: "#22c55e" }, fill: "tonexty", fillcolor: "rgba(34,197,94,0.12)", connectgaps: false }, 1, LABEL.SuperTrend));
      // Downtrend fill
      traces.push(mkTrace({ type: "scatter", x: x, y: ohlc4, mode: "lines", line: { width: 0, color: "rgba(0,0,0,0)" }, hoverinfo: "skip" }, 1, LABEL.SuperTrend));
      traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(stLine, stTrend, -1), mode: "lines", line: { width: 2, color: "#ff0000" }, fill: "tonexty", fillcolor: "rgba(255,0,0,0.10)", connectgaps: false }, 1, LABEL.SuperTrend));
      // Buy/Sell markers
      const stBuy = boolMask(c, "SuperTrend_buy"), stSell = boolMask(c, "SuperTrend_sell");
      if (stBuy) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, stBuy), y: filterArr(stLine, stBuy), mode: "markers+text", marker: { symbol: "circle", size: 8, color: "#22c55e" }, text: filterArr(stBuy, stBuy).map(() => "Buy"), textposition: "top center", textfont: { color: "#ffffff", size: 9 } }, 1, LABEL.SuperTrend));
      if (stSell) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, stSell), y: filterArr(stLine, stSell), mode: "markers+text", marker: { symbol: "circle", size: 8, color: "#ff0000" }, text: filterArr(stSell, stSell).map(() => "Sell"), textposition: "bottom center", textfont: { color: "#ffffff", size: 9 } }, 1, LABEL.SuperTrend));
    }

    // --- UT Bot ---
    if ("UT_trailing_stop" in c) {
      const ts = col(c, "UT_trailing_stop"), pos = c.UT_pos;
      if (pos) {
        traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(ts, pos, 1), mode: "lines", line: { width: 2, color: "#22c55e" }, connectgaps: false }, 1, LABEL.UT_Bot));
        traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(ts, pos, -1), mode: "lines", line: { width: 2, color: "#ff0000" }, connectgaps: false }, 1, LABEL.UT_Bot));
        traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(ts, pos, 0), mode: "lines", line: { width: 2, color: "#2962FF" }, connectgaps: false }, 1, LABEL.UT_Bot));
      } else {
        traces.push(mkTrace({ type: "scatter", x: x, y: ts, mode: "lines", line: { width: 2, color: _gcss("--border-strong") || "#3a3f3c" } }, 1, LABEL.UT_Bot));
      }
      const utBuy = boolMask(c, "UT_buy"), utSell = boolMask(c, "UT_sell");
      if (utBuy) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, utBuy), y: filterArr(L, utBuy), mode: "markers+text", marker: { symbol: "square", size: 10, color: "#22c55e" }, text: filterArr(utBuy, utBuy).map(() => "Buy"), textposition: "bottom center", textfont: { color: "#ffffff", size: 9 } }, 1, LABEL.UT_Bot));
      if (utSell) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, utSell), y: filterArr(H, utSell), mode: "markers+text", marker: { symbol: "square", size: 10, color: "#ff0000" }, text: filterArr(utSell, utSell).map(() => "Sell"), textposition: "top center", textfont: { color: "#ffffff", size: 9 } }, 1, LABEL.UT_Bot));
    }

    // --- TuTCI ---
    if (has(c, ["TuTCI_upper", "TuTCI_lower", "TuTCI_trend", "TuTCI_exit"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "TuTCI_upper"), mode: "lines", line: { width: 1.2, color: "#0094FF" } }, 1, LABEL.TuTCI));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "TuTCI_lower"), mode: "lines", line: { width: 1.2, color: "#0094FF" } }, 1, LABEL.TuTCI));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "TuTCI_trend"), mode: "lines", line: { width: 1.8, color: "#ef4444" } }, 1, LABEL.TuTCI));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "TuTCI_exit"), mode: "lines", line: { width: 1.2, color: "#3b82f6" } }, 1, LABEL.TuTCI));
    }

    // --- GMMA ---
    const gmmaCols = Object.keys(c).filter(k => k.startsWith("GMMA_ema_")).sort((a, b) => {
      const la = parseInt(a.split("_").pop()) || 1e9, lb = parseInt(b.split("_").pop()) || 1e9;
      return la - lb;
    });
    if (gmmaCols.length) {
      const shortLens = new Set([3, 5, 8, 10, 12, 15]);
      const longLens = new Set([30, 35, 40, 45, 50, 60]);
      const sp = ["rgba(34,197,94,0.90)", "rgba(34,197,94,0.78)", "rgba(34,197,94,0.66)", "rgba(34,197,94,0.54)", "rgba(34,197,94,0.42)", "rgba(34,197,94,0.30)"];
      const lp = ["rgba(239,68,68,0.90)", "rgba(239,68,68,0.78)", "rgba(239,68,68,0.66)", "rgba(239,68,68,0.54)", "rgba(239,68,68,0.42)", "rgba(239,68,68,0.30)"];
      let si = 0, li = 0;
      gmmaCols.forEach(gc => {
        const gLen = parseInt(gc.split("_").pop()) || 1e9;
        let color;
        if (shortLens.has(gLen)) color = sp[Math.min(si++, sp.length - 1)];
        else if (longLens.has(gLen)) color = lp[Math.min(li++, lp.length - 1)];
        else color = "rgba(148,163,184,0.55)";
        traces.push(mkTrace({ type: "scatter", x: x, y: col(c, gc), mode: "lines", line: { width: 1.2, color: color } }, 1, LABEL.GMMA));
      });
    }

    // --- MA Ribbon ---
    if (has(c, ["MA_Ribbon_ma1", "MA_Ribbon_ma2", "MA_Ribbon_ma3", "MA_Ribbon_ma4"])) {
      ["#f6c309", "#fb9800", "#fb6500", "#f60c0c"].forEach((cl, i) => {
        traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "MA_Ribbon_ma" + (i + 1)), mode: "lines", line: { width: 1.2, color: cl } }, 1, LABEL.MA_Ribbon));
      });
    }

    // --- PSAR ---
    if ("PSAR" in c) traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "PSAR"), mode: "markers", marker: { size: 6, color: "rgba(59,130,246,0.75)" } }, 1, LABEL.PSAR));

    // --- DEMA ---
    if ("DEMA_9" in c) traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "DEMA_9"), mode: "lines", line: { width: 1.6, color: "#43A047" } }, 1, LABEL.DEMA));

    // --- Ichimoku ---
    if (has(c, ["Ichi_tenkan", "Ichi_kijun"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "Ichi_tenkan"), mode: "lines", line: { width: 1, color: "#ef4444" }, name: "Tenkan" }, 1, LABEL.Ichimoku));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "Ichi_kijun"), mode: "lines", line: { width: 1, color: "#2962FF" }, name: "Kijun" }, 1, LABEL.Ichimoku));
    }
    if (has(c, ["Ichi_senkou_a", "Ichi_senkou_b"])) {
      const sa = col(c, "Ichi_senkou_a"), sb = col(c, "Ichi_senkou_b");
      const bullCloud = sa.map((v, i) => v != null && sb[i] != null && v >= sb[i]);
      traces.push(mkTrace({ type: "scatter", x: x, y: sa, mode: "lines", line: { width: 0 }, hoverinfo: "skip" }, 1, LABEL.Ichimoku));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(sb, bullCloud), mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: "rgba(34,197,94,0.12)", hoverinfo: "skip", connectgaps: false }, 1, LABEL.Ichimoku));
      traces.push(mkTrace({ type: "scatter", x: x, y: sa, mode: "lines", line: { width: 0 }, hoverinfo: "skip" }, 1, LABEL.Ichimoku));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(sb, bNot(bullCloud)), mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: "rgba(239,68,68,0.12)", hoverinfo: "skip", connectgaps: false }, 1, LABEL.Ichimoku));
    }
    if ("Ichi_chikou" in c) traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "Ichi_chikou"), mode: "lines", line: { width: 1, color: "rgba(120,123,134,0.5)" }, name: "Chikou" }, 1, LABEL.Ichimoku));

    // --- GK Trend Ribbon ---
    if (has(c, ["GK_zl", "GK_upper", "GK_lower", "GK_trend"])) {
      const gkt = col(c, "GK_trend"), gkzl = col(c, "GK_zl");
      traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(gkzl, gkt, 1), mode: "lines", line: { width: 2, color: "#22c55e" }, connectgaps: false, name: "GK Zero-Lag" }, 1, LABEL.GK_Trend));
      traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(gkzl, gkt, -1), mode: "lines", line: { width: 2, color: "#ef4444" }, connectgaps: false }, 1, LABEL.GK_Trend));
      traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(gkzl, gkt, 0), mode: "lines", line: { width: 2, color: "#9ca3af" }, connectgaps: false }, 1, LABEL.GK_Trend));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "GK_upper"), mode: "lines", line: { width: 1, color: "rgba(148,163,184,0.4)", dash: "dot" } }, 1, LABEL.GK_Trend));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "GK_lower"), mode: "lines", line: { width: 1, color: "rgba(148,163,184,0.4)", dash: "dot" } }, 1, LABEL.GK_Trend));
    }

    // --- Impulse Trend ---
    if (has(c, ["ITL_basis", "ITL_upper", "ITL_lower", "ITL_trend"])) {
      const itlT = col(c, "ITL_trend"), itlB = col(c, "ITL_basis");
      traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(itlB, itlT, 1), mode: "lines", line: { width: 2, color: "#22c55e" }, connectgaps: false, name: "Impulse Basis" }, 1, LABEL.Impulse_Trend));
      traces.push(mkTrace({ type: "scatter", x: x, y: whereEq(itlB, itlT, -1), mode: "lines", line: { width: 2, color: "#ef4444" }, connectgaps: false }, 1, LABEL.Impulse_Trend));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "ITL_upper"), mode: "lines", line: { width: 1, color: "rgba(34,197,94,0.35)", dash: "dot" } }, 1, LABEL.Impulse_Trend));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "ITL_lower"), mode: "lines", line: { width: 1, color: "rgba(239,68,68,0.35)", dash: "dot" } }, 1, LABEL.Impulse_Trend));
    }

    // --- SR Breaks ---
    if (has(c, ["SR_support", "SR_resistance"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "SR_support"), mode: "lines", line: { width: 1, color: "#22c55e", dash: "dot" }, name: "Support" }, 1, LABEL.SR_Breaks));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "SR_resistance"), mode: "lines", line: { width: 1, color: "#ef4444", dash: "dot" }, name: "Resistance" }, 1, LABEL.SR_Breaks));

      // Zone rects
      const tfU = tf.toUpperCase();
      const hbMs = tfU === "1D" ? 43200000 : (tfU === "1W" ? 302400000 : 7344000);
      function addSRZoneRects(level, edge, fillcolor) {
        if (!level || !edge) return;
        let i = 0;
        while (i < n) {
          if (level[i] == null || edge[i] == null || isNaN(level[i]) || isNaN(edge[i])) { i++; continue; }
          const curLv = level[i], curEv = edge[i];
          let j = i + 1;
          while (j < n && level[j] != null && level[j] === curLv && edge[j] != null && edge[j] === curEv) j++;
          const y0 = Math.min(curLv, curEv), y1 = Math.max(curLv, curEv);
          const x0 = new Date(new Date(x[i]).getTime() - hbMs).toISOString();
          const x1 = new Date(new Date(x[j - 1]).getTime() + hbMs).toISOString();
          shapes.push({ type: "rect", x0: x0, x1: x1, y0: y0, y1: y1, fillcolor: fillcolor, line: { width: 0 }, layer: "below", xref: "x", yref: "y" });
          i = j;
        }
      }
      addSRZoneRects(c.SR_support, c.SR_support_lo, "rgba(34,197,94,0.15)");
      addSRZoneRects(c.SR_resistance, c.SR_resistance_hi, "rgba(239,68,68,0.15)");
    }
    // SR Break/Hold markers
    const srMarkers = [
      { col: "SR_break_res", yCol: "SR_resistance", sym: "triangle-up", color: "#22c55e", name: "Break Res", sz: 12 },
      { col: "SR_break_sup", yCol: "SR_support", sym: "triangle-down", color: "#ef4444", name: "Break Sup", sz: 12 },
      { col: "SR_sup_holds", yCol: "SR_support", sym: "diamond", color: "#22c55e", name: "Sup Holds", sz: 8 },
      { col: "SR_res_holds", yCol: "SR_resistance", sym: "diamond", color: "#ef4444", name: "Res Holds", sz: 8 },
    ];
    srMarkers.forEach(m => {
      if (!(m.col in c) || !(m.yCol in c)) return;
      const mask = boolMask(c, m.col);
      if (!mask || !mask.some(Boolean)) return;
      traces.push(mkTrace({ type: "scatter", x: filterByMask(x, mask), y: filterArr(col(c, m.yCol), mask), mode: "markers", marker: { symbol: m.sym, size: m.sz, color: m.color }, name: m.name }, 1, LABEL.SR_Breaks));
    });

    // --- Benchmark overlay ---
    const benchCols = Object.keys(c).filter(k => k.startsWith("_bench_"));
    if (benchCols.length) {
      const bc = benchCols[0];
      const benchName = bc.replace("_bench_", "");
      const benchVals = col(c, bc);
      const benchValid = benchVals.filter(v => v != null);
      if (benchValid.length >= 2) {
        const closeValid = C.filter(v => v != null);
        const closeFirst = closeValid.length ? closeValid[0] : 1;
        const benchFirst = benchValid[0] || 1;
        const benchNorm = benchVals.map(v => v != null ? (v / benchFirst) * closeFirst : null);
        traces.push(mkTrace({ type: "scatter", x: x, y: benchNorm, mode: "lines", line: { width: 1.5, color: "rgba(168,85,247,0.55)", dash: "dash" }, name: benchName + " (overlay)" }, 1, "Benchmark (" + benchName + ")", false));
      }
    }

    /* ============================================================ */
    /*  ROW 3 — Oscillators                                         */
    /* ============================================================ */

    // --- Madrid Ribbon (heatmap) ---
    const mmarbCols = Object.keys(c).filter(k => k.startsWith("MMARB_state_")).sort();
    if (mmarbCols.length) {
      const mmarbLens = mmarbCols.map(k => parseInt(k.split("_").pop()));
      const z = mmarbCols.map(k => col(c, k).map(v => v != null ? v : 0));
      traces.push(mkTrace({
        type: "heatmap", x: x, y: mmarbLens, z: z,
        zmin: -2, zmax: 2,
        colorscale: [[0, "#7f1d1d"], [0.25, "#ef4444"], [0.5, "#9ca3af"], [0.75, "#22c55e"], [1, "#00e676"]],
        showscale: false, hovertemplate: "MMARB %{y}<br>%{x}<extra></extra>",
      }, 3, LABEL.MadridRibbon, false));
    }

    // --- Donchian Ribbon (heatmap) ---
    const donCols = Object.keys(c).filter(k => k.startsWith("Donchian_trend_")).sort((a, b) => {
      const la = parseInt(a.split("_").pop()) || 1e9, lb = parseInt(b.split("_").pop()) || 1e9;
      return lb - la;
    });
    if ("Donchian_maintrend" in c && donCols.length) {
      const main = col(c, "Donchian_maintrend").map(v => v != null ? v : 0);
      const zRows = donCols.map(dc => {
        const tr = col(c, dc).map(v => v != null ? v : 0);
        return tr.map((v, i) => main[i] === 1 ? (v === 1 ? 2 : 1) : (main[i] === -1 ? (v === -1 ? -2 : -1) : 0));
      });
      const yLevels = [];
      for (let i = 0; i < donCols.length; i++) yLevels.push(5 * (i + 1));
      traces.push(mkTrace({
        type: "heatmap", x: x, y: yLevels, z: zRows,
        zmin: -2, zmax: 2,
        colorscale: [[0, "#ff0000"], [0.25, "rgba(255,0,0,0.62)"], [0.5, "rgba(0,0,0,0.0)"], [0.75, "rgba(0,255,0,0.62)"], [1, "#00ff00"]],
        showscale: false, hovertemplate: "Donchian ribbon<br>%{x}<extra></extra>",
      }, 3, LABEL.DonchianRibbon, false));
    }

    // --- WaveTrend ---
    if (has(c, ["WT_LB_wt1", "WT_LB_wt2", "WT_LB_hist"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "WT_LB_wt1"), mode: "lines", line: { color: "#22c55e", width: 1.8 } }, 3, LABEL.WT_LB, true));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "WT_LB_wt2"), mode: "lines", line: { color: "#ef4444", width: 1.8, dash: "dot" } }, 3, LABEL.WT_LB, true));
      traces.push(mkTrace({ type: "bar", x: x, y: col(c, "WT_LB_hist"), marker: { color: "rgba(59,130,246,0.35)" } }, 3, LABEL.WT_LB, true));
    }

    // --- ADX / DI ---
    if (has(c, ["ADX", "DI_plus", "DI_minus"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "DI_plus"), mode: "lines", line: { color: "#22c55e", width: 1.2 } }, 3, LABEL.ADX_DI));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "DI_minus"), mode: "lines", line: { color: "#ef4444", width: 1.2 } }, 3, LABEL.ADX_DI));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "ADX"), mode: "lines", line: { color: "#1e3a8a", width: 1.6 } }, 3, LABEL.ADX_DI));
    }

    // --- OBV Oscillator ---
    if ("OBV_osc" in c) {
      const obv = col(c, "OBV_osc");
      const obvPos = obv.map(v => v != null && v >= 0 ? v : null);
      const obvNeg = obv.map(v => v != null && v < 0 ? v : null);
      traces.push(mkTrace({ type: "scatter", x: x, y: obvPos, mode: "lines", line: { color: "#22c55e", width: 2 }, fill: "tozeroy", fillcolor: "rgba(148,163,184,0.28)", connectgaps: false }, 3, LABEL.OBVOSC));
      traces.push(mkTrace({ type: "scatter", x: x, y: obvNeg, mode: "lines", line: { color: "#ef4444", width: 2 }, fill: "tozeroy", fillcolor: "rgba(148,163,184,0.28)", connectgaps: false }, 3, LABEL.OBVOSC));
      traces.push(mkTrace({ type: "scatter", x: x, y: zeros(n), mode: "lines", line: { color: "rgba(148,163,184,0.45)", dash: "dot", width: 1 } }, 3, LABEL.OBVOSC));
    }

    // --- Squeeze Momentum ---
    if ("SQZ_val" in c) {
      const sqzVal = col(c, "SQZ_val");
      let sqzColors;
      if (c.SQZ_bcolor) {
        sqzColors = col(c, "SQZ_bcolor");
      } else {
        const prev = shift1(sqzVal);
        sqzColors = sqzVal.map((v, i) => {
          if (v == null) return "#9ca3af";
          const p = prev[i] || 0;
          return (v > 0 && v > p) ? "#00e676" : (v > 0 ? "#22c55e" : (v < p ? "#ef4444" : "#7f1d1d"));
        });
      }
      traces.push(mkTrace({ type: "bar", x: x, y: sqzVal, marker: { color: sqzColors }, opacity: 0.95 }, 3, LABEL.SQZMOM));
      if (c.SQZ_scolor) {
        traces.push(mkTrace({ type: "scatter", x: x, y: zeros(n), mode: "markers", marker: { symbol: "x", size: 7, color: col(c, "SQZ_scolor") } }, 3, LABEL.SQZMOM));
      }
      traces.push(mkTrace({ type: "scatter", x: x, y: zeros(n), mode: "lines", line: { color: "rgba(148,163,184,0.45)", dash: "dot", width: 1 } }, 3, LABEL.SQZMOM));
    }

    // --- SMI (Stoch_MTM) ---
    if (has(c, ["SMI", "SMI_ema"])) {
      const smi = col(c, "SMI").map(v => v != null ? v : null);
      const smiEma = col(c, "SMI_ema");
      const ob = 40, os = -40;
      const obLine = constant(n, ob), osLine = constant(n, os);
      const smiOb = smi.map(v => v != null && v > ob ? v : null);
      const smiOs = smi.map(v => v != null && v < os ? v : null);
      // OB fill
      traces.push(mkTrace({ type: "scatter", x: x, y: obLine, mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, hoverinfo: "skip" }, 3, LABEL.SMI));
      traces.push(mkTrace({ type: "scatter", x: x, y: smiOb, mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, fill: "tonexty", fillcolor: "rgba(239,68,68,0.40)", connectgaps: false, hoverinfo: "skip" }, 3, LABEL.SMI));
      // OS fill
      traces.push(mkTrace({ type: "scatter", x: x, y: osLine, mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, hoverinfo: "skip" }, 3, LABEL.SMI));
      traces.push(mkTrace({ type: "scatter", x: x, y: smiOs, mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, fill: "tonexty", fillcolor: "rgba(34,197,94,0.40)", connectgaps: false, hoverinfo: "skip" }, 3, LABEL.SMI));
      // Threshold + main lines
      traces.push(mkTrace({ type: "scatter", x: x, y: obLine, mode: "lines", line: { color: "rgba(148,163,184,0.55)", width: 1, dash: "dot" } }, 3, LABEL.SMI));
      traces.push(mkTrace({ type: "scatter", x: x, y: osLine, mode: "lines", line: { color: "rgba(148,163,184,0.55)", width: 1, dash: "dot" } }, 3, LABEL.SMI));
      traces.push(mkTrace({ type: "scatter", x: x, y: smi, mode: "lines", line: { color: "rgba(226,232,240,0.95)", width: 1.4 } }, 3, LABEL.SMI));
      traces.push(mkTrace({ type: "scatter", x: x, y: smiEma, mode: "lines", line: { color: "#ef4444", width: 1.2 } }, 3, LABEL.SMI));
    }

    // --- MACD ---
    if (has(c, ["MACD", "MACD_signal", "MACD_hist"])) {
      const macdLine = col(c, "MACD"), sigLine = col(c, "MACD_signal"), hist = col(c, "MACD_hist");
      const macdAbove = macdLine.map((v, i) => v != null && sigLine[i] != null && v >= sigLine[i]);
      const cross = macdLine.map((v, i) => {
        if (v == null || sigLine[i] == null || i === 0) return false;
        const d = v - sigLine[i], pd = (macdLine[i - 1] || 0) - (sigLine[i - 1] || 0);
        return d * pd < 0;
      });
      const histPrev = shift1(hist);
      const histColors = hist.map((v, i) => {
        if (v == null) return "#facc15";
        const p = histPrev[i] || 0;
        if (v > 0 && v > p) return "#22d3ee";
        if (v > 0) return "#2563eb";
        if (v <= 0 && v < p) return "#ef4444";
        if (v <= 0 && v > p) return "#7f1d1d";
        return "#facc15";
      });
      traces.push(mkTrace({ type: "bar", x: x, y: hist, marker: { color: histColors } }, 3, LABEL.MACD));
      traces.push(mkTrace({ type: "scatter", x: x, y: zeros(n), mode: "lines", line: { color: "rgba(148,163,184,0.55)", width: 2 } }, 3, LABEL.MACD));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(macdLine, macdAbove), mode: "lines", line: { color: "#84cc16", width: 4 }, connectgaps: false }, 3, LABEL.MACD));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(macdLine, bNot(macdAbove)), mode: "lines", line: { color: "#ef4444", width: 4 }, connectgaps: false }, 3, LABEL.MACD));
      traces.push(mkTrace({ type: "scatter", x: x, y: sigLine, mode: "lines", line: { color: "#facc15", width: 2 } }, 3, LABEL.MACD));
      if (cross.some(Boolean)) {
        const crossColors = cross.map((v, i) => macdAbove[i] ? "#84cc16" : "#ef4444");
        traces.push(mkTrace({ type: "scatter", x: filterByMask(x, cross), y: filterArr(sigLine, cross), mode: "markers", marker: { symbol: "circle", size: 10, color: filterArr(crossColors, cross) } }, 3, LABEL.MACD));
      }
    }

    // --- Volume + MA20 ---
    if (has(c, ["Volume", "Vol_MA20"])) {
      const vol = col(c, "Volume"), volMA = col(c, "Vol_MA20");
      const gt = c.Vol_gt_MA20
        ? col(c, "Vol_gt_MA20").map(v => !!v)
        : vol.map((v, i) => v != null && volMA[i] != null && v > volMA[i]);
      const volColors = gt.map(v => v ? "#22c55e" : "rgba(148,163,184,0.55)");
      traces.push(mkTrace({ type: "bar", x: x, y: vol, marker: { color: volColors }, opacity: 0.9 }, 3, LABEL.VOL_MA));
      traces.push(mkTrace({ type: "scatter", x: x, y: volMA, mode: "lines", line: { color: "#facc15", width: 2 } }, 3, LABEL.VOL_MA));
    }

    // --- cRSI ---
    if (has(c, ["cRSI", "cRSI_lb", "cRSI_ub"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "cRSI"), mode: "lines", line: { color: "#a21caf", width: 1.4 } }, 3, LABEL.cRSI));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "cRSI_lb"), mode: "lines", line: { color: "#06b6d4", width: 1, dash: "dash" } }, 3, LABEL.cRSI));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "cRSI_ub"), mode: "lines", line: { color: "#06b6d4", width: 1, dash: "dash" } }, 3, LABEL.cRSI));
    }

    // --- RSI Zeiierman ---
    if (has(c, ["Zei_rsi", "Zei_rsi_strength", "Zei_bullish"])) {
      const bullish = col(c, "Zei_bullish").map(v => !!v);
      const rsi = col(c, "Zei_rsi"), rsiS = col(c, "Zei_rsi_strength");
      traces.push(mkTrace({ type: "scatter", x: x, y: constant(n, 70), mode: "lines", line: { color: "rgba(50,211,255,0.0)", width: 1 } }, 3, LABEL.RSI_Zei));
      traces.push(mkTrace({ type: "scatter", x: x, y: constant(n, 30), mode: "lines", line: { color: "rgba(50,211,255,0.0)", width: 1 }, fill: "tonexty", fillcolor: "rgba(50,211,255,0.10)" }, 3, LABEL.RSI_Zei));
      traces.push(mkTrace({ type: "scatter", x: x, y: constant(n, 50), mode: "lines", line: { color: "rgba(50,211,255,0.35)", width: 1, dash: "dash" } }, 3, LABEL.RSI_Zei));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(rsi, bullish), mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, connectgaps: false }, 3, LABEL.RSI_Zei));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(rsiS, bullish), mode: "lines", line: { color: "#00e676", width: 8 }, fill: "tonexty", fillcolor: "rgba(0,230,118,0.35)", connectgaps: false }, 3, LABEL.RSI_Zei));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(rsi, bNot(bullish)), mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, connectgaps: false }, 3, LABEL.RSI_Zei));
      traces.push(mkTrace({ type: "scatter", x: x, y: where(rsiS, bNot(bullish)), mode: "lines", line: { color: "#ef4444", width: 8 }, fill: "tonexty", fillcolor: "rgba(239,68,68,0.30)", connectgaps: false }, 3, LABEL.RSI_Zei));
      traces.push(mkTrace({ type: "scatter", x: x, y: rsi, mode: "lines", line: { color: "#32d3ff", width: 2.5 } }, 3, LABEL.RSI_Zei));
      // Arrows on price chart
      const prevBull = shift1(bullish);
      const bullFlip = bullish.map((v, i) => v && !prevBull[i]);
      const bearFlip = bullish.map((v, i) => !v && prevBull[i]);
      if (bullFlip.some(Boolean)) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, bullFlip), y: filterArr(L, bullFlip), mode: "markers", marker: { symbol: "triangle-up", size: 10, color: "#22c55e" } }, 1, LABEL.RSI_Zei));
      if (bearFlip.some(Boolean)) traces.push(mkTrace({ type: "scatter", x: filterByMask(x, bearFlip), y: filterArr(H, bearFlip), mode: "markers", marker: { symbol: "triangle-down", size: 10, color: "#ff0000" } }, 1, LABEL.RSI_Zei));
    }

    // --- Mansfield RS ---
    if ("MRS" in c) {
      const mrsBenchCols = Object.keys(c).filter(k => k.startsWith("_bench_"));
      const mrsBenchSym = mrsBenchCols.length ? mrsBenchCols[0].replace("_bench_", "") : "";
      const mrsLabel = mrsBenchSym ? "Mansfield RS (vs " + mrsBenchSym + ")" : LABEL.Mansfield_RS;
      const mrs = col(c, "MRS");
      const mrsPos = mrs.map(v => v != null && v >= 0 ? v : null);
      const mrsNeg = mrs.map(v => v != null && v < 0 ? v : null);
      traces.push(mkTrace({ type: "bar", x: x, y: mrsPos, marker: { color: "rgba(34,197,94,0.6)" }, name: "MRS+" }, 3, mrsLabel));
      traces.push(mkTrace({ type: "bar", x: x, y: mrsNeg, marker: { color: "rgba(239,68,68,0.6)" }, name: "MRS-" }, 3, mrsLabel));
    }

    // --- Stoof (Band Light) oscillator traces ---
    if (has(c, ["MACD_BL", "MACD_BL_hist"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "MACD_BL"), mode: "lines", line: { color: "#3b82f6", width: 1.5 } }, 3, LABEL.MACD_BL));
      if ("MACD_BL_signal" in c) traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "MACD_BL_signal"), mode: "lines", line: { color: "#f59e0b", width: 1.2, dash: "dot" } }, 3, LABEL.MACD_BL));
      traces.push(mkTrace({ type: "bar", x: x, y: col(c, "MACD_BL_hist"), marker: { color: col(c, "MACD_BL_hist").map(v => v >= 0 ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.5)") } }, 3, LABEL.MACD_BL));
    }
    if (has(c, ["WT_LB_BL_wt1", "WT_LB_BL_wt2"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "WT_LB_BL_wt1"), mode: "lines", line: { color: "#22c55e", width: 1.5 } }, 3, LABEL.WT_LB_BL));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "WT_LB_BL_wt2"), mode: "lines", line: { color: "#ef4444", width: 1.2, dash: "dot" } }, 3, LABEL.WT_LB_BL));
    }
    if ("OBVOSC_BL_osc" in c) {
      const obvBl = col(c, "OBVOSC_BL_osc");
      traces.push(mkTrace({ type: "bar", x: x, y: obvBl, marker: { color: obvBl.map(v => v >= 0 ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.5)") } }, 3, LABEL.OBVOSC_BL));
    }
    if ("CCI_Chop_BB_v1_smooth" in c) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "CCI_Chop_BB_v1_smooth"), mode: "lines", line: { color: "#8b5cf6", width: 1.5 } }, 3, LABEL.CCI_Chop_BB_v1));
    }
    if ("CCI_Chop_BB_v2_smooth" in c) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "CCI_Chop_BB_v2_smooth"), mode: "lines", line: { color: "#a855f7", width: 1.5 } }, 3, LABEL.CCI_Chop_BB_v2));
    }
    if (has(c, ["ADX_BL", "DI_plus_BL", "DI_minus_BL"])) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "DI_plus_BL"), mode: "lines", line: { color: "#22c55e", width: 1.2 } }, 3, LABEL.ADX_DI_BL));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "DI_minus_BL"), mode: "lines", line: { color: "#ef4444", width: 1.2 } }, 3, LABEL.ADX_DI_BL));
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "ADX_BL"), mode: "lines", line: { color: "#1e3a8a", width: 1.6 } }, 3, LABEL.ADX_DI_BL));
    }
    if ("LuxAlgo_Norm_v1" in c) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "LuxAlgo_Norm_v1"), mode: "lines", line: { color: "#06b6d4", width: 1.5 } }, 3, LABEL.LuxAlgo_Norm_v1));
    }
    if ("LuxAlgo_Norm_v2" in c) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "LuxAlgo_Norm_v2"), mode: "lines", line: { color: "#0891b2", width: 1.5, dash: "dot" } }, 3, LABEL.LuxAlgo_Norm_v2));
    }
    if ("Risk_Indicator" in c) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "Risk_Indicator"), mode: "lines", line: { color: "#f97316", width: 1.5 } }, 3, LABEL.Risk_Indicator));
    }
    if ("PAI" in c) {
      traces.push(mkTrace({ type: "scatter", x: x, y: col(c, "PAI"), mode: "lines", line: { color: "#14b8a6", width: 1.5 } }, 3, LABEL.PAI));
    }
    if (has(c, ["WT_MTF_wt1", "WT_MTF_wt2"])) {
      const wtOb = constant(n, 60), wtOs = constant(n, -60);
      const wt1 = col(c, "WT_MTF_wt1"), wt2 = col(c, "WT_MTF_wt2");
      const wtOb2 = wt1.map(v => v != null && v > 60 ? v : null);
      const wtOs2 = wt1.map(v => v != null && v < -60 ? v : null);
      traces.push(mkTrace({ type: "scatter", x: x, y: wtOb, mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, hoverinfo: "skip" }, 3, LABEL.WT_MTF));
      traces.push(mkTrace({ type: "scatter", x: x, y: wtOb2, mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, fill: "tonexty", fillcolor: "rgba(239,68,68,0.25)", connectgaps: false, hoverinfo: "skip" }, 3, LABEL.WT_MTF));
      traces.push(mkTrace({ type: "scatter", x: x, y: wtOs, mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, hoverinfo: "skip" }, 3, LABEL.WT_MTF));
      traces.push(mkTrace({ type: "scatter", x: x, y: wtOs2, mode: "lines", line: { color: "rgba(0,0,0,0)", width: 0 }, fill: "tonexty", fillcolor: "rgba(34,197,94,0.25)", connectgaps: false, hoverinfo: "skip" }, 3, LABEL.WT_MTF));
      traces.push(mkTrace({ type: "scatter", x: x, y: wtOb, mode: "lines", line: { color: "rgba(239,68,68,0.55)", width: 1, dash: "dot" } }, 3, LABEL.WT_MTF));
      traces.push(mkTrace({ type: "scatter", x: x, y: wtOs, mode: "lines", line: { color: "rgba(34,197,94,0.55)", width: 1, dash: "dot" } }, 3, LABEL.WT_MTF));
      traces.push(mkTrace({ type: "scatter", x: x, y: wt1, mode: "lines", line: { color: "#6366f1", width: 1.5 } }, 3, LABEL.WT_MTF));
      traces.push(mkTrace({ type: "scatter", x: x, y: wt2, mode: "lines", line: { color: "#a78bfa", width: 1.2, dash: "dot" } }, 3, LABEL.WT_MTF));
    }

    /* ============================================================ */
    /*  ROW 4–7 — KPI panels                                        */
    /* ============================================================ */

    const kpi = data.kpi || {};
    const kpiZ = kpi.z || [];
    const kpiNames = kpi.kpis || [];
    const kpiCustom = kpi.custom || [];
    const kpisTrend = kpi.kpis_trend || [];
    const kpisBreakout = kpi.kpis_breakout || [];
    const kpiWeights = data.kpi_weights || {};

    const idxByName = {};
    kpiNames.forEach((nm, i) => { idxByName[nm] = i; });

    function kpiSlice(names) {
      const kk = [], zz = [], cc = [];
      names.forEach(nm => {
        const idx = idxByName[nm];
        if (idx === undefined) return;
        const row = kpiZ[idx];
        if (row) { kk.push(nm); zz.push(row); cc.push(kpiCustom[idx]); }
      });
      return { kk, zz, cc };
    }

    const trend = kpiSlice(kpisTrend);
    const brk = kpiSlice(kpisBreakout);

    // Strategy-aware: build Stoof KPI slice
    const strategyKpis = data.strategy_kpis || {};
    const _activeStrat = (typeof window.currentStrategy === "string") ? window.currentStrategy : "trend";
    const stoofKpiNames = strategyKpis["stoof"] || [];
    const stoofSlice = kpiSlice(stoofKpiNames);

    let combo3kpis = data.combo_3_kpis || ["Nadaraya-Watson Smoother", "Madrid Ribbon", "Volume + MA20"];
    let combo4kpis = data.combo_4_kpis || ["Nadaraya-Watson Smoother", "Madrid Ribbon", "GK Trend Ribbon", "cRSI"];
    let combo3pols = null, combo4pols = null;

    // Override combos if a polarity_combo strategy is selected; prefer TF-specific combos
    const _stratSetups = (typeof STRATEGY_SETUPS !== "undefined") ? (STRATEGY_SETUPS.setups || {}) : {};
    const _activeDef = _stratSetups[_activeStrat];
    let _stratColor = null;
    if (_activeDef && _activeDef.entry_type === "polarity_combo") {
      const _tfUpper = tf.toUpperCase();
      const _combosByTf = _activeDef.combos_by_tf || {};
      const _tfCombos = _combosByTf[_tfUpper] || {};
      const combos = Object.keys(_tfCombos).length ? _tfCombos : (_activeDef.combos || {});
      const c3d = combos.c3 || {};
      const c4d = combos.c4;
      if (c3d.kpis) { combo3kpis = c3d.kpis; combo3pols = c3d.pols || null; }
      if (c4d && c4d.kpis) { combo4kpis = c4d.kpis; combo4pols = c4d.pols || null; }
      else { combo4kpis = []; }
      _stratColor = _activeDef.color || null;
    }

    let c3Active = null, c4Active = null;
    const allKpiZ = {};
    kpiNames.forEach((nm, i) => { allKpiZ[nm] = kpiZ[i]; });

    function comboBool(kpiList, polList) {
      const rows = [];
      const pols = polList || kpiList.map(() => 1);
      for (let ki = 0; ki < kpiList.length; ki++) {
        const row = allKpiZ[kpiList[ki]];
        if (!row) return null;
        rows.push({z: row, p: pols[ki]});
      }
      if (!rows.length) return null;
      return rows[0].z.map((_, ci) => rows.every(r => r.z[ci] === r.p));
    }

    function _hexToRgba(hex, a) {
      if (!hex || hex[0] !== "#") return "rgba(128,128,128," + a + ")";
      const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
      return "rgba(" + r + "," + g + "," + b + "," + a + ")";
    }

    // KPI colorscale
    const eps = 1e-6;
    const colorscale = [
      [0.0, "#ffffff"], [0.25 - eps, "#ffffff"],
      [0.25, "#e5e7eb"], [0.50 - eps, "#e5e7eb"],
      [0.50, "#ff0000"], [0.75 - eps, "#ff0000"],
      [0.75, "#9ca3af"], [1.0 - eps, "#9ca3af"],
      [1.0, "#22c55e"],
    ];

    // Breakout dots (row 5) — filter by strategy, sorted to match trend heatmap order
    const _polStratKpiNames = (_activeDef && _activeDef.entry_type === "polarity_combo")
      ? (strategyKpis[_activeStrat] || []) : null;
    const activeStratKpiNames = _polStratKpiNames
      ? _polStratKpiNames
      : (_activeStrat === "stoof" ? stoofKpiNames : null);
    const _brkFilteredRaw = activeStratKpiNames
      ? kpisBreakout.filter(bk => {
          const base = bk.startsWith("BO_") ? bk.slice(3) : bk;
          return activeStratKpiNames.includes(base);
        })
      : kpisBreakout.slice();
    const _heatmapOrder = _polStratKpiNames
      ? _polStratKpiNames
      : (_activeStrat === "stoof" ? stoofKpiNames : kpisTrend);
    const _heatmapIdx = {};
    _heatmapOrder.forEach((k, i) => { _heatmapIdx[k] = i; });
    _brkFilteredRaw.sort((a, b) => {
      const ba = a.startsWith("BO_") ? a.slice(3) : a;
      const bb = b.startsWith("BO_") ? b.slice(3) : b;
      const ia = _heatmapIdx[ba] != null ? _heatmapIdx[ba] : 9999;
      const ib = _heatmapIdx[bb] != null ? _heatmapIdx[bb] : 9999;
      return ia - ib;
    });
    const brkFiltered = kpiSlice(_brkFilteredRaw);
    const brLabels = [];
    if (brkFiltered.kk.length) {
      brkFiltered.kk.forEach((k, i) => {
        const label = shortLabel(k);
        brLabels.push(label);
        const rowZ = brkFiltered.zz[i], rowC = brkFiltered.cc[i];
        const bullX = [], bullText = [], bearX = [], bearText = [];
        for (let ci = 0; ci < n; ci++) {
          if (rowZ[ci] === 1) { bullX.push(x[ci]); bullText.push(label + ": " + (rowC[ci] || "")); }
          else if (rowZ[ci] === -1) { bearX.push(x[ci]); bearText.push(label + ": " + (rowC[ci] || "")); }
        }
        if (bullX.length) traces.push(mkTrace({ type: "scatter", x: bullX, y: constant(bullX.length, label), mode: "markers", marker: { symbol: "diamond", size: 6, color: "#22c55e" }, text: constant(bullX.length, label), hoverinfo: "text+x" }, 5, "KPI Breakout", true));
        if (bearX.length) traces.push(mkTrace({ type: "scatter", x: bearX, y: constant(bearX.length, label), mode: "markers", marker: { symbol: "diamond", size: 6, color: "#ef4444" }, text: constant(bearX.length, label), hoverinfo: "text+x" }, 5, "KPI Breakout", true));
      });
    }

    // TrendScore / StoofScore (row 4)
    let tsPad = 1;
    const isStoof = _activeStrat === "stoof";
    const isPolStrat = !!_polStratKpiNames;
    const polStratSlice = isPolStrat ? kpiSlice(_polStratKpiNames) : null;
    const scoreSlice = isPolStrat ? polStratSlice
      : (isStoof ? stoofSlice : trend);
    const scoreLabel = isPolStrat ? (_activeDef.label || _activeStrat) + " Score"
      : (isStoof ? "StoofScore" : "TrendScore");

    // Build polarity map for polarity strategies: KPI name → expected polarity
    const _polMap = {};
    if (isPolStrat && _activeDef && _activeDef.combos) {
      const _cc = _activeDef.combos;
      ["c3", "c4"].forEach(ck => {
        const cd = _cc[ck];
        if (cd && cd.kpis && cd.pols) {
          cd.kpis.forEach((kn, ki) => { _polMap[kn] = cd.pols[ki]; });
        }
      });
    }

    if (scoreSlice.kk.length && scoreSlice.zz.length) {
      const tsValues = new Array(n).fill(0);
      scoreSlice.kk.forEach((k, i) => {
        const w = (isStoof || isPolStrat) ? 1 : (kpiWeights[k] != null ? kpiWeights[k] : 1);
        const expectedPol = (isPolStrat && _polMap[k] != null) ? _polMap[k] : 1;
        const row = scoreSlice.zz[i];
        for (let ci = 0; ci < n; ci++) {
          const v = row[ci];
          if (v === expectedPol) tsValues[ci] += w;
          else if (v === -expectedPol) tsValues[ci] -= w;
        }
      });
      const _tsUpColor = (isPolStrat && _stratColor) ? _hexToRgba(_stratColor, 0.8) : "rgba(34,197,94,0.8)";
      const tsColors = tsValues.map(v => v > 0 ? _tsUpColor : (v < 0 ? "rgba(239,68,68,0.8)" : "rgba(148,163,184,0.5)"));
      traces.push(mkTrace({
        type: "bar", x: x, y: tsValues, marker: { color: tsColors },
        hovertemplate: "<b>" + scoreLabel + "</b>: %{y:.1f}<br>%{x}<extra></extra>",
      }, 4, "TrendScore", true));

      if (isStoof) {
        // Threshold line at 7 (the "7/10 rule")
        traces.push(mkTrace({
          type: "scatter", x: [x[0], x[n-1]], y: [7, 7],
          mode: "lines", line: { color: "rgba(59,130,246,0.6)", width: 1.5, dash: "dash" },
          hoverinfo: "skip",
        }, 4, "TrendScore", true));
      }

      const tsMax = Math.max(...tsValues.map(Math.abs), 1);
      tsPad = (isStoof || isPolStrat) ? Math.max(scoreSlice.kk.length + 1, 5) : Math.max(tsMax * 1.1, 1);

      if (!isStoof) {
        c3Active = comboBool(combo3kpis, combo3pols);
        c4Active = combo4kpis.length ? comboBool(combo4kpis, combo4pols) : null;
      }
    }

    // KPI Trend Heatmap (row 6) — uses scoreSlice when strategy is active
    const heatmapSlice = isPolStrat ? polStratSlice
      : (isStoof ? stoofSlice : trend);
    const trLabels = [];
    if (heatmapSlice.kk.length) {
      heatmapSlice.kk.forEach(k => trLabels.push(shortLabel(k)));
      const grouped_kpi_labels = heatmapSlice.kk.map(k => constant(n, shortLabel(k)));
      traces.push(mkTrace({
        type: "heatmap", x: x, y: trLabels, z: heatmapSlice.zz,
        zmin: -3, zmax: 1, colorscale: colorscale, zsmooth: false,
        showscale: false, xgap: 0, ygap: 3,
        customdata: grouped_kpi_labels, text: heatmapSlice.cc,
        hovertemplate: "<b>%{customdata}</b><br>%{x}<br>%{text}<extra></extra>",
      }, 6, "KPI Trend", true));
    }

    /* ============================================================ */
    /*  Combo zones metadata + Exit Flow v4 unified position model  */
    /* ============================================================ */
    const comboZones = [];

    function zoneRanges(mask) {
      const ranges = [];
      const ext = [false, ...mask, false];
      for (let i = 1; i < ext.length; i++) {
        if (ext[i] && !ext[i - 1]) { const s = i - 1; let e = s; while (e + 1 < mask.length && mask[e + 1]) e++; ranges.push([s, e]); }
      }
      return ranges;
    }

    if (c3Active) {
      if (!c4Active) c4Active = new Array(n).fill(false);
      [c3Active, c4Active].forEach((mask, mi) => {
        const name = ["C3", "C4"][mi];
        zoneRanges(mask).forEach(([s, e]) => {
          comboZones.push({ name: name, start: x[s].slice(0, 10), end: x[e].slice(0, 10), bars: e - s + 1, x0: x[s], x1: x[e] });
        });
      });
    }

    /* ============================================================ */
    /*  Exit Flow v4 — Position events (single source of truth)     */
    /* ============================================================ */
    const allTrades = [];

    {
      const _EP = (typeof EXIT_PARAMS_CFG !== "undefined") ? EXIT_PARAMS_CFG : {"4H":{T:4,M:48,K:4.0},"1D":{T:4,M:40,K:4.0},"1W":{T:2,M:20,K:4.0},"2W":{T:2,M:10,K:4.0},"1M":{T:1,M:6,K:4.0}};
      const params = _EP[tf.toUpperCase()] || _EP["1D"];
      const T = params.T, M = params.M, K = params.K;

      const close = C.map(v => v != null ? v : NaN);
      const high = H.map(v => v != null ? v : NaN);
      const low = L.map(v => v != null ? v : NaN);
      const tfU = tf.toUpperCase();
      const hbMs = tfU === "1D" ? 43200000 : (tfU === "1W" ? 302400000 : 7344000);
      const pLo = Math.min(...low.filter(v => !isNaN(v)));
      const pHi = Math.max(...high.filter(v => !isNaN(v)));
      const pRange = Math.max(pHi - pLo, 1);

      // --- Build trade list from pre-computed events or fallback ---
      const _peByStrat = data.position_events_by_strategy || {};
      const _isAllStrats = _activeStrat === "all";
      const _useStratEvents = isPolStrat && _peByStrat[_activeStrat] && _peByStrat[_activeStrat].length;
      const _useAllOverlay = _isAllStrats && Object.keys(_peByStrat).length > 0;

      function _pushEvents(evList, stratKey) {
        const _sSetup = _stratSetups[stratKey];
        const _sColor = _sSetup ? (_sSetup.color || null) : null;
        const _sLabel = _sSetup ? (_sSetup.label || stratKey) : stratKey;
        for (const ev of evList) {
          const ei = Math.max(0, Math.min(ev.entry_idx, n - 1));
          const xi = Math.max(0, Math.min(ev.exit_idx, n - 1));
          const si = ev.scale_idx != null ? Math.max(0, Math.min(ev.scale_idx, n - 1)) : null;
          const ep = ev.entry_price;
          const xp = ev.exit_price != null ? ev.exit_price : close[xi];
          const ret = ev.ret_pct != null ? ev.ret_pct : (ep > 0 ? ((xp - ep) / ep) * 100 : 0);
          const hold = xi - ei;
          const trail = ev.stop_trail || [ep * 0.95];
          allTrades.push({
            entryIdx: ei, exitIdx: xi, ret: ret, hold: hold,
            label: ev.scaled ? "C4" : "C3", reason: ev.exit_reason,
            scaled: ev.scaled, scaleIdx: si, stopTrail: trail, ep: ep,
            entryDate: x[ei], exitDate: x[xi],
            _stratKey: stratKey, _stratColor: _sColor, _stratLabel: _sLabel,
          });
        }
      }

      if (_useStratEvents) {
        _pushEvents(_peByStrat[_activeStrat], _activeStrat);
      } else if (_useAllOverlay) {
        if (data.position_events && data.position_events.length) {
          _pushEvents(data.position_events, "v6");
        }
        for (const sk in _peByStrat) {
          _pushEvents(_peByStrat[sk], sk);
        }
      } else if (data.position_events && data.position_events.length) {
        _pushEvents(data.position_events, "v6");
      } else if (c3Active && Object.keys(allKpiZ).length) {
        // Fallback: compute from payload data (backward compat)
        if (!c4Active) c4Active = new Array(n).fill(false);
        const O_fig = c.Open || C;
        const open_fig = O_fig.map(v => v != null ? v : NaN);
        const SLIP_FIG = 0.005;
        const prevC = [close[0], ...close.slice(0, -1)];
        const tr = close.map((_, i) => Math.max(high[i] - low[i], Math.abs(high[i] - prevC[i]), Math.abs(low[i] - prevC[i])));
        const atr = rollingMean(tr, 14);
        const c3Onset = c3Active.map((v, i) => v && (i === 0 || !c3Active[i - 1]));

        function bearishCount(kpis, j, pols) {
          let nb = 0;
          const pp = pols || kpis.map(() => 1);
          for (let ki = 0; ki < kpis.length; ki++) { const k = kpis[ki]; if (k in allKpiZ && j < allKpiZ[k].length && allKpiZ[k][j] !== pp[ki]) nb++; }
          return nb;
        }

        let smaGate = null;
        if (tfU === "1D" || tfU === "1W") {
          if (data.sma20_ok && data.sma20_ok.length === n) { smaGate = data.sma20_ok; }
          else if (n >= 200) {
            smaGate = new Array(n).fill(true);
            const sma200 = new Array(n).fill(NaN), sma20 = new Array(n).fill(NaN);
            let sum200 = 0, sum20 = 0;
            for (let si = 0; si < n; si++) { const cv = close[si] || 0; sum200 += cv; sum20 += cv; if (si >= 200) sum200 -= close[si - 200] || 0; if (si >= 20) sum20 -= close[si - 20] || 0; if (si >= 199) sma200[si] = sum200 / 200; if (si >= 19) sma20[si] = sum20 / 20; }
            for (let si = 0; si < n; si++) { smaGate[si] = isNaN(sma200[si]) || isNaN(sma20[si]) || sma20[si] >= sma200[si]; }
          }
        }
        const _VS_MULT = 1.5, _VS_LB = 5;
        let volSpikeOk = null;
        if (c.Volume && c.Volume.length === n) {
          const vol = c.Volume.map(v => v != null ? v : 0);
          const volMa20 = rollingMean(vol, 20);
          const spikeRaw = vol.map((v, j) => v >= _VS_MULT * (volMa20[j] || 1) ? 1 : 0);
          volSpikeOk = new Array(n).fill(false);
          for (let si = 0; si < n; si++) { for (let lb = 0; lb < _VS_LB && si - lb >= 0; lb++) { if (spikeRaw[si - lb]) { volSpikeOk[si] = true; break; } } }
        }
        let overextOk_fig = null;
        if (tfU === "1W") { overextOk_fig = new Array(n).fill(true); for (let oi = 5; oi < n; oi++) { if (close[oi - 5] > 0 && close[oi] > close[oi - 5] * 1.15) overextOk_fig[oi] = false; } }

        let i = 0;
        while (i < n) {
          if (!c3Onset[i]) { i++; continue; }
          if (smaGate && !smaGate[i]) { i++; continue; }
          if (volSpikeOk && !volSpikeOk[i]) { i++; continue; }
          if (overextOk_fig && !overextOk_fig[i]) { i++; continue; }
          const fillBar = i + 1; if (fillBar >= n) break;
          const ep = open_fig[fillBar]; if (ep <= 0 || isNaN(ep)) { i++; continue; }
          const entryIdx = fillBar;
          let scaled = c4Active[i], scaleIdx = scaled ? i : null;
          let activeKpis = scaled ? combo4kpis : combo3kpis, nk = activeKpis.length;
          let activePols = scaled ? combo4pols : combo3pols;
          let stopPrice = ep, stop = atr[i] > 0 ? stopPrice - K * atr[i] : stopPrice * 0.95;
          let barsSinceReset = 0; const stopTrail = [stop];
          let exitIdx = null, exitReason = null;
          for (let j = entryIdx + 1; j < n; j++) {
            barsSinceReset++; const cj = close[j];
            if (isNaN(cj)) { stopTrail.push(stopTrail[stopTrail.length - 1]); continue; }
            if (cj < stop) { exitIdx = j; exitReason = "ATR stop"; break; }
            if (!scaled && c4Active[j]) { scaled = true; scaleIdx = j; activeKpis = combo4kpis; activePols = combo4pols; nk = activeKpis.length; }
            const nb = bearishCount(activeKpis, j, activePols), barsHeld = j - entryIdx;
            if (barsHeld <= T) { if (nb >= nk) { exitIdx = j; exitReason = "Full invalidation"; break; } }
            else { if (nb >= 2) { exitIdx = j; exitReason = nb + "/" + nk + " KPIs bearish"; break; } }
            if (barsSinceReset >= M) { if (nb === 0) { stopPrice = cj; stop = atr[j] > 0 ? stopPrice - K * atr[j] : stopPrice * 0.95; barsSinceReset = 0; } else { exitIdx = j; exitReason = "Checkpoint exit"; break; } }
            stopTrail.push(stop);
          }
          if (exitIdx == null) { exitIdx = n - 1; exitReason = "Open"; }
          while (stopTrail.length < (exitIdx - entryIdx + 1)) stopTrail.push(stopTrail[stopTrail.length - 1] || stop);
          const exitFill = (exitIdx < n - 1 && exitReason !== "Open") ? exitIdx + 1 : exitIdx;
          const xp = exitFill !== exitIdx ? open_fig[exitFill] : close[exitIdx];
          const cost = 0.001 + SLIP_FIG; const ret = ep > 0 ? ((xp - ep) / ep - cost) * 100 : 0;
          allTrades.push({ entryIdx: entryIdx, exitIdx: exitIdx, ret: ret, hold: exitIdx - entryIdx, label: scaled ? "C4" : "C3", reason: exitReason, scaled: scaled, scaleIdx: scaleIdx, stopTrail: stopTrail, ep: ep, entryDate: x[entryIdx], exitDate: x[exitIdx] });
          i = exitReason !== "Open" ? exitIdx + 1 : n;
        }
      }

      // --- "All strategies" mode: compute trades for every polarity_combo strategy ---
      if (_activeStrat === "all" && Object.keys(allKpiZ).length) {
        allTrades.length = 0;
        const _aSlip = 0.005;
        const _aEP2 = (typeof EXIT_PARAMS_CFG !== "undefined") ? EXIT_PARAMS_CFG : {"4H":{T:4,M:48,K:4.0},"1D":{T:4,M:40,K:4.0},"1W":{T:2,M:20,K:4.0},"2W":{T:2,M:10,K:4.0},"1M":{T:1,M:6,K:4.0}};
        const _aPrms = _aEP2[tf.toUpperCase()] || _aEP2["1D"];
        const _aT = _aPrms.T, _aM = _aPrms.M, _aK = _aPrms.K;
        const _aClose = C.map(v => v != null ? v : NaN);
        const _aOpen2 = (c.Open || C).map(v => v != null ? v : NaN);
        const _aHigh = H.map(v => v != null ? v : NaN);
        const _aLow = L.map(v => v != null ? v : NaN);
        const _aTfU = tf.toUpperCase();
        let _aSmaGate = null;
        if (_aTfU === "1D" || _aTfU === "1W") {
          if (data.sma20_ok && data.sma20_ok.length === n) { _aSmaGate = data.sma20_ok; }
          else if (n >= 200) {
            _aSmaGate = new Array(n).fill(true);
            let _as200 = 0, _as20 = 0;
            for (let _si2 = 0; _si2 < n; _si2++) {
              const _cv2 = _aClose[_si2] || 0; _as200 += _cv2; _as20 += _cv2;
              if (_si2 >= 200) _as200 -= _aClose[_si2 - 200] || 0;
              if (_si2 >= 20) _as20 -= _aClose[_si2 - 20] || 0;
              const _v200 = _si2 >= 199 ? _as200 / 200 : NaN;
              const _v20 = _si2 >= 19 ? _as20 / 20 : NaN;
              _aSmaGate[_si2] = isNaN(_v200) || isNaN(_v20) || _v20 >= _v200;
            }
          }
        }
        let _aVolOk = null;
        if (c.Volume && c.Volume.length === n) {
          const _avol = c.Volume.map(v => v != null ? v : 0);
          const _avolMa = rollingMean(_avol, 20);
          const _aSpike = _avol.map((v, j) => v >= 1.5 * (_avolMa[j] || 1) ? 1 : 0);
          _aVolOk = new Array(n).fill(false);
          for (let _si2 = 0; _si2 < n; _si2++) { for (let _lb = 0; _lb < 5 && _si2 - _lb >= 0; _lb++) { if (_aSpike[_si2 - _lb]) { _aVolOk[_si2] = true; break; } } }
        }
        let _aOverext = null;
        if (_aTfU === "1W") { _aOverext = new Array(n).fill(true); for (let _oi2 = 5; _oi2 < n; _oi2++) { if (_aClose[_oi2 - 5] > 0 && _aClose[_oi2] > _aClose[_oi2 - 5] * 1.15) _aOverext[_oi2] = false; } }
        const _aPrevC = [_aClose[0], ..._aClose.slice(0, -1)];
        const _aTrR = _aClose.map((_, i) => Math.max(_aHigh[i] - _aLow[i], Math.abs(_aHigh[i] - _aPrevC[i]), Math.abs(_aLow[i] - _aPrevC[i])));
        const _aAtr = rollingMean(_aTrR, 14);
        function _aBearCount(kpis, j, pols) {
          let nb = 0; const pp = pols || kpis.map(() => 1);
          for (let ki = 0; ki < kpis.length; ki++) { const k = kpis[ki]; if (k in allKpiZ && j < allKpiZ[k].length && allKpiZ[k][j] !== pp[ki]) nb++; }
          return nb;
        }
        for (const [, sdef] of Object.entries(_stratSetups)) {
          if (sdef.entry_type !== "polarity_combo") continue;
          const _sColor = sdef.color || "#888888";
          const _cByTf = sdef.combos_by_tf || {};
          const _tfC2 = _cByTf[_aTfU] || {};
          const _sCombos = Object.keys(_tfC2).length ? _tfC2 : (sdef.combos || {});
          const _sc3d = _sCombos.c3 || {};
          const _sc4d = _sCombos.c4;
          const _sc3kpis = _sc3d.kpis || [];
          const _sc3pols = _sc3d.pols || _sc3kpis.map(() => 1);
          const _sc4kpis = _sc4d ? (_sc4d.kpis || []) : [];
          const _sc4pols = _sc4d ? (_sc4d.pols || _sc4kpis.map(() => 1)) : [];
          if (!_sc3kpis.length || _sc3kpis.some(k => !allKpiZ[k])) continue;
          const _sc3Act = comboBool(_sc3kpis, _sc3pols);
          const _sc4Act = _sc4kpis.length ? comboBool(_sc4kpis, _sc4pols) : null;
          if (!_sc3Act) continue;
          const _sc3Onset = _sc3Act.map((v, i) => v && (i === 0 || !_sc3Act[i - 1]));
          let _sIdx = 0;
          while (_sIdx < n) {
            if (!_sc3Onset[_sIdx]) { _sIdx++; continue; }
            if (_aSmaGate && !_aSmaGate[_sIdx]) { _sIdx++; continue; }
            if (_aVolOk && !_aVolOk[_sIdx]) { _sIdx++; continue; }
            if (_aOverext && !_aOverext[_sIdx]) { _sIdx++; continue; }
            const _sfb = _sIdx + 1; if (_sfb >= n) break;
            const _sep = _aOpen2[_sfb]; if (_sep <= 0 || isNaN(_sep)) { _sIdx++; continue; }
            const _sEnt = _sfb;
            let _sSc = _sc4Act ? _sc4Act[_sIdx] : false, _sScIdx = _sSc ? _sIdx : null;
            let _sAk = _sSc ? _sc4kpis : _sc3kpis, _sAp = _sSc ? _sc4pols : _sc3pols;
            let _sSp = _sep, _sStop = _aAtr[_sIdx] > 0 ? _sSp - _aK * _aAtr[_sIdx] : _sSp * 0.95;
            let _sBsr = 0; const _sSt = [_sStop]; let _sXi = null, _sXr = null;
            for (let _sj = _sEnt + 1; _sj < n; _sj++) {
              _sBsr++; const _scj = _aClose[_sj]; if (isNaN(_scj)) { _sSt.push(_sSt[_sSt.length - 1]); continue; }
              if (_scj < _sStop) { _sXi = _sj; _sXr = "ATR stop"; break; }
              if (!_sSc && _sc4Act && _sc4Act[_sj]) { _sSc = true; _sScIdx = _sj; _sAk = _sc4kpis; _sAp = _sc4pols; }
              const _snb = _aBearCount(_sAk, _sj, _sAp), _sbh = _sj - _sEnt;
              if (_sbh <= _aT) { if (_snb >= _sAk.length) { _sXi = _sj; _sXr = "Full invalidation"; break; } }
              else { if (_snb >= 2) { _sXi = _sj; _sXr = _snb + "/" + _sAk.length + " KPIs bearish"; break; } }
              if (_sBsr >= _aM) { if (_snb === 0) { _sSp = _scj; _sStop = _aAtr[_sj] > 0 ? _sSp - _aK * _aAtr[_sj] : _sStop; _sBsr = 0; } else { _sXi = _sj; _sXr = "Checkpoint exit"; break; } }
              _sSt.push(_sStop);
            }
            if (_sXi == null) { _sXi = n - 1; _sXr = "Open"; }
            while (_sSt.length < (_sXi - _sEnt + 1)) _sSt.push(_sSt[_sSt.length - 1] || _sStop);
            const _sXf = (_sXi < n - 1 && _sXr !== "Open") ? _sXi + 1 : _sXi;
            const _sXp = _sXf !== _sXi ? _aOpen2[_sXf] : _aClose[_sXi];
            const _sRet = _sep > 0 ? ((_sXp - _sep) / _sep - (0.001 + _aSlip)) * 100 : 0;
            allTrades.push({ entryIdx: _sEnt, exitIdx: _sXi, ret: _sRet, hold: _sXi - _sEnt, label: _sSc ? "C4" : "C3", reason: _sXr, scaled: _sSc, scaleIdx: _sScIdx, stopTrail: _sSt, ep: _sep, entryDate: x[_sEnt], exitDate: x[_sXi], _stratColor: _sColor });
            _sIdx = _sXr !== "Open" ? _sXi + 1 : n;
          }
        }
      }

      // --- Position shading ---
      const _loseColor = "rgba(244,63,94,0.20)";

      for (const t of allTrades) {
        const ei = t.entryIdx, xi = t.exitIdx;
        const _tc = t._stratColor || null;
        const _tWinC3 = _tc ? _hexToRgba(_tc, 0.13) : "rgba(250,204,21,0.13)";
        const _tWinC4 = _tc ? _hexToRgba(_tc, 0.20) : "rgba(74,222,128,0.14)";
        if (t.scaled && t.scaleIdx != null && t.scaleIdx > ei) {
          shapes.push({
            type: "rect",
            x0: new Date(new Date(x[ei]).getTime() - hbMs).toISOString(),
            x1: new Date(new Date(x[Math.min(t.scaleIdx, xi)]).getTime() + hbMs).toISOString(),
            y0: 0, y1: 1, yref: "y domain", xref: "x",
            fillcolor: t.ret >= 0 ? _tWinC3 : _loseColor,
            line: { width: 0 }, layer: "below", _strategy: true,
          });
          shapes.push({
            type: "rect",
            x0: new Date(new Date(x[t.scaleIdx]).getTime() - hbMs).toISOString(),
            x1: new Date(new Date(x[xi]).getTime() + hbMs).toISOString(),
            y0: 0, y1: 1, yref: "y domain", xref: "x",
            fillcolor: t.ret >= 0 ? _tWinC4 : _loseColor,
            line: { width: 0 }, layer: "below", _strategy: true,
          });
        } else {
          const fc = t.ret >= 0
            ? (t.scaled ? _tWinC4 : _tWinC3)
            : _loseColor;
          shapes.push({
            type: "rect",
            x0: new Date(new Date(x[ei]).getTime() - hbMs).toISOString(),
            x1: new Date(new Date(x[xi]).getTime() + hbMs).toISOString(),
            y0: 0, y1: 1, yref: "y domain", xref: "x",
            fillcolor: fc, line: { width: 0 }, layer: "below", _strategy: true,
          });
        }
      }

      // --- ATR stop-loss line ---
      const stopX = [], stopY = [];
      for (const t of allTrades) {
        const ei = t.entryIdx, xi = t.exitIdx;
        const trail = t.stopTrail;
        for (let j = ei; j <= Math.min(xi, n - 1); j++) {
          stopX.push(x[j]);
          stopY.push(trail[j - ei] != null ? trail[j - ei] : trail[trail.length - 1]);
        }
        stopX.push(null); stopY.push(null);
      }
      if (stopX.length) {
        traces.push(mkTrace({
          type: "scatter", x: stopX, y: stopY, mode: "lines",
          line: { color: "rgba(239,68,68,0.55)", width: 1.5, dash: "dot" },
          hoverinfo: "skip",
        }, 1, LABEL.Price, true));
      }

      // --- SMA200 + SMA20 lines (v5 entry gate: SMA20 > SMA200) ---
      if (data.sma200_vals && data.sma200_vals.length === n) {
        traces.push(mkTrace({
          type: "scatter", x: x, y: data.sma200_vals, mode: "lines",
          line: { color: "rgba(59,130,246,0.6)", width: 1.3, dash: "dot" },
          hovertemplate: "<b>SMA200</b>: %{y:.2f}<extra></extra>",
          name: "SMA200",
        }, 1, LABEL.Price, true));
      }
      if (data.sma20_vals && data.sma20_vals.length === n) {
        traces.push(mkTrace({
          type: "scatter", x: x, y: data.sma20_vals, mode: "lines",
          line: { color: "rgba(251,191,36,0.6)", width: 1.1, dash: "dot" },
          hovertemplate: "<b>SMA20</b>: %{y:.2f}<extra></extra>",
          name: "SMA20",
        }, 1, LABEL.Price, true));
      }

      // --- Entry markers ---
      const entryX = [], entryY = [], entryText = [], entryCustom = [], entryColors = [];
      for (const t of allTrades) {
        const ei = t.entryIdx;
        const _eColor = t._stratColor || (t.scaled ? "#4ade80" : "#facc15");
        const _eLbl = t._stratLabel ? t._stratLabel : (t.scaled ? "1.5x" : "1x");
        entryX.push(x[ei]);
        entryY.push(low[ei] - pRange * 0.03);
        entryText.push(_eLbl);
        entryColors.push(_eColor);
        entryCustom.push(
          "<b>" + (t._stratLabel || "ENTRY") + " " + (t.scaled ? "C4" : "C3") + "</b><br>" +
          "Price: " + t.ep.toFixed(2) + "<br>" +
          "ATR stop: " + (t.stopTrail[0] != null ? t.stopTrail[0].toFixed(2) : "N/A") + "<br>" +
          "Date: " + x[ei].slice(0, 10)
        );
      }
      if (entryX.length) {
        traces.push(mkTrace({
          type: "scatter", x: entryX, y: entryY,
          mode: "markers+text",
          marker: { symbol: "triangle-up", size: 11, color: entryColors, line: { width: 1.2, color: "#ffffff" } },
          text: entryText, textposition: "bottom center",
          textfont: { color: entryColors, size: 8, family: "Arial Black" },
          customdata: entryCustom, hovertemplate: "%{customdata}<extra></extra>",
        }, 1, LABEL.Price, true));
      }

      // --- C4 scale-up markers (mid-position) ---
      const scaleX = [], scaleY = [], scaleCustom = [];
      for (const t of allTrades) {
        if (t.scaleIdx != null && t.scaleIdx > t.entryIdx) {
          scaleX.push(x[t.scaleIdx]);
          scaleY.push(low[t.scaleIdx] - pRange * 0.035);
          scaleCustom.push(
            "<b>\u25B2 SCALE to 1.5x</b><br>" +
            "C4 fired at " + close[t.scaleIdx].toFixed(2) + "<br>" +
            "Entry was " + t.ep.toFixed(2) + "<br>" +
            "Date: " + x[t.scaleIdx].slice(0, 10)
          );
        }
      }
      if (scaleX.length) {
        traces.push(mkTrace({
          type: "scatter", x: scaleX, y: scaleY,
          mode: "markers+text",
          marker: { symbol: "triangle-up", size: 10, color: "#ff9800", line: { width: 1.2, color: "#ffffff" } },
          text: scaleX.map(() => "\u25B2 1.5x"), textposition: "bottom center",
          textfont: { color: "#ff9800", size: 7, family: "Arial Black" },
          customdata: scaleCustom, hovertemplate: "%{customdata}<extra></extra>",
        }, 1, LABEL.Price, true));
      }

      // --- Exit markers ---
      const exitX = [], exitY = [], exitCustom = [], exitColors = [];
      for (const t of allTrades) {
        if (t.reason === "Open") continue;
        const ret = t.ret;
        const _xColor = t._stratColor || (t.scaled ? "#4ade80" : "#facc15");
        exitColors.push(ret >= 0 ? _xColor : "#f43f5e");
        exitX.push(x[t.exitIdx]);
        exitY.push(high[t.exitIdx] + pRange * 0.025);
        const sizing = t.scaled ? "1.5x" : "1x";
        const stage = t.hold <= T ? "Lenient" : "Strict";
        exitCustom.push(
          "<b>" + (t._stratLabel || "EXIT") + " (" + sizing + "): " + (ret >= 0 ? "+" : "") + ret.toFixed(1) + "%</b><br>" +
          "Reason: " + t.reason + "<br>" +
          "Stage: " + stage + " (T=" + T + ", M=" + M + ")<br>" +
          "Hold: " + t.hold + " bars<br>" +
          "Entry: " + t.ep.toFixed(2) + " (" + x[t.entryIdx].slice(0, 10) + ")<br>" +
          "Exit: " + close[t.exitIdx].toFixed(2) + " (" + x[t.exitIdx].slice(0, 10) + ")"
        );
      }
      if (exitX.length) {
        traces.push(mkTrace({
          type: "scatter", x: exitX, y: exitY, mode: "markers",
          marker: { symbol: "triangle-down", size: 10, color: exitColors, line: { width: 1.2, color: "#ffffff" } },
          customdata: exitCustom, hovertemplate: "%{customdata}<extra></extra>",
        }, 1, LABEL.Price, true));
      }
    }

    /* ============================================================ */
    /*  ROW 2 — P&L / Hit Rate / Return panel                       */
    /* ============================================================ */
    {
      // Sort trades by exitIdx to build the equity curve chronologically
      const trades = allTrades.slice().sort((a, b) => a.exitIdx - b.exitIdx);

      const eqCurve = new Array(n).fill(0);
      let cumRet = 0;
      for (const t of trades) {
        const entryPrice = C[t.entryIdx];
        const weight = t.scaled ? 1.5 : 1.0;
        for (let i = t.entryIdx; i < t.exitIdx && i < n; i++) {
          const unrealised = entryPrice > 0 && C[i] != null ? ((C[i] - entryPrice) / entryPrice) * 100 * weight : 0;
          eqCurve[i] = cumRet + unrealised;
        }
        cumRet += t.ret * weight;
        for (let i = t.exitIdx; i < n; i++) eqCurve[i] = cumRet;
      }

      // Positive/negative fill areas
      const eqPos = eqCurve.map(v => v >= 0 ? v : 0);
      const eqNeg = eqCurve.map(v => v < 0 ? v : 0);

      // Equity curve fill — positive
      traces.push(mkTrace({
        type: "scatter", x: x, y: eqPos, fill: "tozeroy",
        fillcolor: "rgba(38,166,91,0.12)", line: { width: 0 },
        hoverinfo: "skip",
      }, 2, "P&L", true));
      // Equity curve fill — negative
      traces.push(mkTrace({
        type: "scatter", x: x, y: eqNeg, fill: "tozeroy",
        fillcolor: "rgba(234,57,67,0.12)", line: { width: 0 },
        hoverinfo: "skip",
      }, 2, "P&L", true));
      // Zero line
      traces.push(mkTrace({
        type: "scatter", x: [x[0], x[n - 1]], y: [0, 0], mode: "lines",
        line: { color: "rgba(148,163,184,0.3)", width: 1, dash: "dot" },
        hoverinfo: "skip",
      }, 2, "P&L", true));
      // Equity curve line
      traces.push(mkTrace({
        type: "scatter", x: x, y: eqCurve, mode: "lines",
        line: { color: eqCurve[n - 1] >= 0 ? "#26a65b" : "#ea3943", width: 1.8 },
        hovertemplate: "<b>Cum. Return</b>: %{y:.1f}%<extra></extra>",
      }, 2, "P&L", true));

      // Per-trade P&L bars centered on trade midpoint, width = trade duration
      if (trades.length) {
        const barX = trades.map(t => {
          const t0 = new Date(x[t.entryIdx]).getTime(), t1 = new Date(x[t.exitIdx]).getTime();
          return new Date((t0 + t1) / 2).toISOString();
        });
        const barW = trades.map(t => {
          const t0 = new Date(x[t.entryIdx]).getTime(), t1 = new Date(x[t.exitIdx]).getTime();
          return Math.max(t1 - t0, 3600000);
        });
        const barY = trades.map(t => t.ret * (t.scaled ? 1.5 : 1.0));
        const barColors = trades.map(t => {
          if (t._stratColor && t.ret >= 0) return _hexToRgba(t._stratColor, 0.75);
          return t.ret >= 0 ? "rgba(34,197,94,0.75)" : "rgba(239,68,68,0.75)";
        });
        traces.push(mkTrace({
          type: "bar", x: barX, y: barY, width: barW,
          marker: { color: barColors, line: { width: 0 } },
          hovertemplate: "<b>Trade P&L</b>: %{y:.2f}%<extra></extra>",
        }, 2, "P&L", true));
      }

      // Stat banner annotation
      const totalRet = cumRet;
      const wins = trades.filter(t => t.ret >= 0).length;
      let maxDD = 0, peak = -Infinity;
      eqCurve.forEach(v => { if (v > peak) peak = v; const dd = peak - v; if (dd > maxDD) maxDD = dd; });

      // Store trades with exit dates for dynamic stat recalculation on zoom
      data._pnlTrades = trades.map(t => ({
        entryDate: x[t.entryIdx], exitDate: x[t.exitIdx], ret: t.ret, scaled: t.scaled,
      }));
      data._pnlStatsText = function(filteredTrades) {
        const ft = filteredTrades || trades;
        const fTotal = ft.reduce((s, t) => s + t.ret * (t.scaled ? 1.5 : 1.0), 0);
        const fWins = ft.filter(t => t.ret >= 0).length;
        const fHR = ft.length ? ((fWins / ft.length) * 100).toFixed(0) : "\u2013";
        const fAvg = ft.length ? (fTotal / ft.length).toFixed(2) : "\u2013";
        let fMaxDD = 0, fPeak = 0, fCum = 0;
        ft.forEach(t => { fCum += t.ret * (t.scaled ? 1.5 : 1.0); if (fCum > fPeak) fPeak = fCum; const dd = fPeak - fCum; if (dd > fMaxDD) fMaxDD = dd; });
        const fSign = fTotal >= 0 ? "+" : "";
        const fColor = fTotal >= 0 ? "#22c55e" : "#ef4444";
        return "<b>Return</b> <span style='color:" + fColor + "'>" + fSign + fTotal.toFixed(1) + "%</span>" +
               "  |  <b>HR</b> " + fHR + "%" +
               "  |  <b>Avg</b> " + fAvg + "%" +
               "  |  <b>Max DD</b> <span style='color:#ef4444'>-" + fMaxDD.toFixed(1) + "%</span>" +
               "  |  <b>Trades</b> " + ft.length;
      };
      data._pnlStats = { text: data._pnlStatsText(trades) };
    }

    /* ============================================================ */
    /*  Layout                                                       */
    /* ============================================================ */
    const titleStr = displayName ? displayName + " (" + symbol + ") \u2014 " + tf : symbol + " \u2014 " + tf;
    const nBr = brLabels.length, nTr = trLabels.length;
    const nKpi = Math.max(nBr + nTr, 1);
    const kpiShare = 0.32;
    const brShare = nKpi ? (kpiShare * nBr / nKpi) : 0.16;
    const trShare = nKpi ? (kpiShare * nTr / nKpi) : 0.16;
    const tsShare = 0.09;
    const pnlShare = 0.08;
    const priceShare = 0.27;
    const oscShare = 1 - priceShare - pnlShare - brShare - trShare - tsShare;
    const gap = 0.015;

    const r6Bot = 0, r6Top = r6Bot + trShare;
    const r5Bot = r6Top + gap, r5Top = r5Bot + brShare;
    const r4Bot = r5Top + gap, r4Top = r4Bot + tsShare;
    const r3Bot = r4Top + gap, r3Top = r3Bot + oscShare;
    const r2Bot = r3Top + gap, r2Top = r2Bot + pnlShare;
    const r1Bot = r2Top + gap, r1Top = 1.0;

    // X range with padding
    let xRange;
    if (n >= 2) {
      const tfU = tf.toUpperCase();
      const padMs = tfU === "1D" ? 86400000 : (tfU === "1W" ? 345600000 : 900000);
      const xMin = new Date(new Date(x[0]).getTime() - padMs).toISOString();
      const xMax = new Date(new Date(x[n - 1]).getTime() + padMs).toISOString();
      xRange = [xMin, xMax];
    }

    const _gridAlpha = _gcss("--plotly-grid-alpha") || "rgba(110,115,108,0.25)";
    const _zeroLine = _gcss("--plotly-zeroline") || "rgba(110,115,108,0.40)";
    const _spikeClr = _gcss("--plotly-spike") || "rgba(224,221,213,0.70)";
    const makeXAxis = (domain) => {
      const a = {
        showgrid: true, gridcolor: _gridAlpha,
        showticklabels: true, showspikes: true,
        spikemode: "across+toaxis", spikesnap: "cursor",
        spikecolor: _spikeClr, spikethickness: 1.5,
      };
      if (xRange) a.range = xRange;
      return a;
    };
    const makeYAxis = (domain, extra) => Object.assign({
      domain: domain, showgrid: true, gridcolor: _gridAlpha,
      automargin: true, tickfont: { size: 10 },
      ticklabeloverflow: "hide past domain",
    }, extra || {});

    const xax1 = makeXAxis();
    xax1.rangeslider = { visible: false };

    const layout = {
      title: titleStr, template: "plotly_white", autosize: false,
      showlegend: false,
      margin: { l: Math.round(160 * 1.25), r: 28, t: 70, b: 40 }, height: 2800,
      hovermode: "x unified", spikedistance: -1, hoverdistance: -1,
      shapes: shapes,
      meta: { combo_zones: comboZones, pnl_trades: data._pnlTrades || [], pnl_stats_fn: data._pnlStatsText || null, combo_3_kpis: combo3kpis, combo_4_kpis: combo4kpis },
      xaxis: xax1, xaxis2: makeXAxis(), xaxis3: makeXAxis(),
      xaxis4: makeXAxis(), xaxis5: makeXAxis(), xaxis6: makeXAxis(),
      yaxis: makeYAxis([r1Bot, r1Top]),
      yaxis2: makeYAxis([r2Bot, r2Top], {
        ticksuffix: "%", zeroline: true, zerolinecolor: _zeroLine,
        tickfont: { size: 9 },
      }),
      yaxis3: makeYAxis([r3Bot, r3Top]),
      yaxis4: makeYAxis([r4Bot, r4Top], {
        tickfont: { size: 8 }, showgrid: false,
        zeroline: true, zerolinecolor: _zeroLine,
        range: [-tsPad, tsPad], autorange: false, fixedrange: true,
      }),
      yaxis5: (brLabels.length ? {
        domain: [r5Bot, r5Top], tickmode: "array",
        tickvals: brLabels, ticktext: brLabels, tickfont: { size: 8 },
        ticklabelstandoff: 8, automargin: true,
        categoryorder: "array", categoryarray: brLabels.slice().reverse(),
        showgrid: true, gridcolor: _gridAlpha,
      } : makeYAxis([r5Bot, r5Top])),
      yaxis6: (trLabels.length ? {
        domain: [r6Bot, r6Top], tickmode: "array",
        tickvals: trLabels, ticktext: trLabels, tickfont: { size: 8 },
        ticklabelstandoff: 8, automargin: true,
        categoryorder: "array", categoryarray: trLabels.slice().reverse(),
        showgrid: true, gridcolor: _gridAlpha,
      } : makeYAxis([r6Bot, r6Top])),
    };

    // Subplot titles as annotations
    const subplotTitles = [
      { text: "Price (" + tf + ")", xref: "paper", yref: "paper", x: 0.5, y: r1Top, showarrow: false, font: { size: 14 }, xanchor: "center", yanchor: "bottom" },
      { text: "Oscillators", xref: "paper", yref: "paper", x: 0.5, y: r3Top, showarrow: false, font: { size: 12 }, xanchor: "center", yanchor: "bottom" },
      { text: "TrendScore", xref: "paper", yref: "paper", x: 0.5, y: r4Top, showarrow: false, font: { size: 12 }, xanchor: "center", yanchor: "bottom" },
      { text: "KPI \u2014 Breakout (signals)", xref: "paper", yref: "paper", x: 0.5, y: r5Top, showarrow: false, font: { size: 12 }, xanchor: "center", yanchor: "bottom" },
      { text: "KPI \u2014 Trend (regime)", xref: "paper", yref: "paper", x: 0.5, y: r6Top, showarrow: false, font: { size: 12 }, xanchor: "center", yanchor: "bottom" },
    ];
    // Stash KPI counts in layout.meta for dynamic height in splitFigure
    layout.meta = layout.meta || {};
    layout.meta._nBr = nBr;
    layout.meta._nTr = nTr;
    // P&L dynamic stat banner (updates on zoom)
    if (data._pnlStats && data._pnlStats.text) {
      subplotTitles.push({
        x: 0.01, y: r2Top, xref: "paper", yref: "paper",
        xanchor: "left", yanchor: "top", showarrow: false,
        text: data._pnlStats.text,
        font: { size: 10 },
        bgcolor: "rgba(14,17,23,0.75)", bordercolor: "rgba(148,163,184,0.2)",
        borderwidth: 1, borderpad: 4,
      });
    }
    layout.annotations = subplotTitles;

    return { data: traces, layout: layout };
  }

  /**
   * Standalone trade simulation — reusable outside of figure building.
   * Prefers pre-computed position_events from the payload (single source of truth).
   * Falls back to client-side computation when events are unavailable.
   */
  function simulateTrades(payload, tf) {
    if (!payload || !payload.c || !payload.x) return { trades: [], eqCurve: [], dates: [] };
    const x = payload.x;
    const c = payload.c;
    const n = x.length;
    if (n < 2) return { trades: [], eqCurve: [], dates: x };

    const C = c.Close || [];
    const close = C.map(v => v != null ? v : NaN);
    const trades = [];

    // --- Prefer pre-computed events (strategy-aware) ---
    const _peByStrat2 = payload.position_events_by_strategy || {};
    const _cStrat2a = (typeof window !== "undefined" && window.currentStrategy) ? window.currentStrategy : "v6";
    const _stSetups2a = (typeof STRATEGY_SETUPS !== "undefined") ? (STRATEGY_SETUPS.setups || {}) : {};
    const _sDef2a = _stSetups2a[_cStrat2a];
    const _isPolStrat2 = _sDef2a && _sDef2a.entry_type === "polarity_combo";
    const _stratEvents2 = _isPolStrat2 ? _peByStrat2[_cStrat2a] : null;
    const _useEvents2 = (_stratEvents2 && _stratEvents2.length) ? _stratEvents2
      : (payload.position_events && payload.position_events.length ? payload.position_events : null);

    if (_useEvents2) {
      for (const ev of _useEvents2) {
        const ei = Math.max(0, Math.min(ev.entry_idx, n - 1));
        const xi = Math.max(0, Math.min(ev.exit_idx, n - 1));
        const si = ev.scale_idx != null ? Math.max(0, Math.min(ev.scale_idx, n - 1)) : null;
        const ep = ev.entry_price;
        const xp = ev.exit_price != null ? ev.exit_price : close[xi];
        const ret = ev.ret_pct != null ? ev.ret_pct : (ep > 0 ? ((xp - ep) / ep) * 100 : 0);
        trades.push({
          entryIdx: ei, exitIdx: xi, ret: ret, hold: xi - ei,
          label: ev.scaled ? "C4" : "C3", reason: ev.exit_reason,
          scaled: ev.scaled, scaleIdx: si,
          entryDate: x[ei], exitDate: x[xi],
        });
      }
    } else {
      // Fallback: full client-side computation (backward compat)
      if (!payload.kpi) return { trades: [], eqCurve: [], dates: x };
      let combo3kpis = payload.combo_3_kpis || [];
      let combo4kpis = payload.combo_4_kpis || [];
      let combo3pols = null, combo4pols = null;
      // Support polarity_combo strategies in fallback simulation
      const _stSetups2 = (typeof STRATEGY_SETUPS !== "undefined") ? (STRATEGY_SETUPS.setups || {}) : {};
      const _cStrat2 = (typeof window !== "undefined" && window.currentStrategy) ? window.currentStrategy : "v6";
      const _sDef2 = _stSetups2[_cStrat2];
      if (_sDef2 && _sDef2.entry_type === "polarity_combo") {
        const cc = _sDef2.combos || {};
        if (cc.c3 && cc.c3.kpis) { combo3kpis = cc.c3.kpis; combo3pols = cc.c3.pols; }
        if (cc.c4 && cc.c4.kpis) { combo4kpis = cc.c4.kpis; combo4pols = cc.c4.pols; }
        else { combo4kpis = []; }
      }
      const kpiNames = payload.kpi.kpis || [];
      const kpiZRows = payload.kpi.z || [];
      const allKpiZ = {};
      kpiNames.forEach((nm, i) => { allKpiZ[nm] = kpiZRows[i]; });
      if (!combo3kpis.length || !Object.keys(allKpiZ).length) return { trades: [], eqCurve: [], dates: x };

      const H = c.High || [], L = c.Low || [];
      let c3Active = null, c4Active = null;
      const _pols3 = combo3pols || combo3kpis.map(() => 1);
      const _pols4 = combo4pols || combo4kpis.map(() => 1);
      if (combo3kpis.length) { c3Active = new Array(n).fill(true); for (let ki = 0; ki < combo3kpis.length; ki++) { const z = allKpiZ[combo3kpis[ki]]; if (!z) { c3Active = null; break; } for (let i = 0; i < n; i++) if (z[i] !== _pols3[ki]) c3Active[i] = false; } }
      if (combo4kpis.length) { c4Active = new Array(n).fill(true); for (let ki = 0; ki < combo4kpis.length; ki++) { const z = allKpiZ[combo4kpis[ki]]; if (!z) { c4Active = null; break; } for (let i = 0; i < n; i++) if (z[i] !== _pols4[ki]) c4Active[i] = false; } }
      if (!c3Active) return { trades: [], eqCurve: [], dates: x };
      if (!c4Active) c4Active = new Array(n).fill(false);

      const _EP = (typeof EXIT_PARAMS_CFG !== "undefined") ? EXIT_PARAMS_CFG : {"4H":{T:4,M:48,K:4.0},"1D":{T:4,M:40,K:4.0},"1W":{T:2,M:20,K:4.0},"2W":{T:2,M:10,K:4.0},"1M":{T:1,M:6,K:4.0}};
      const params = _EP[(tf || "1D").toUpperCase()] || _EP["1D"];
      const T = params.T, M = params.M, K = params.K;
      const O = c.Open || C; const open = O.map(v => v != null ? v : NaN); const SLIPPAGE = 0.005;
      const prevC = [close[0], ...close.slice(0, -1)];
      const tr = close.map((_, i) => { const h = H[i] != null ? H[i] : NaN, l = L[i] != null ? L[i] : NaN; return Math.max(h - l, Math.abs(h - prevC[i]), Math.abs(l - prevC[i])); });
      const atr = rollingMean(tr, 14);
      const c3Onset = c3Active.map((v, i) => v && (i === 0 || !c3Active[i - 1]));
      const tfUp = (tf || "1D").toUpperCase();
      let sma200ok = null;
      if (tfUp === "1D" || tfUp === "1W") { if (payload.sma20_ok && payload.sma20_ok.length === n) { sma200ok = payload.sma20_ok; } else if (payload.sma200_ok && payload.sma200_ok.length === n) { sma200ok = payload.sma200_ok; } else if (n >= 200) { const sma200 = new Array(n).fill(NaN), sma20 = new Array(n).fill(NaN); let s200 = 0, s20 = 0; for (let i = 0; i < n; i++) { const cv = close[i] || 0; s200 += cv; s20 += cv; if (i >= 200) s200 -= close[i - 200] || 0; if (i >= 20) s20 -= close[i - 20] || 0; if (i >= 199) sma200[i] = s200 / 200; if (i >= 19) sma20[i] = s20 / 20; } sma200ok = new Array(n); for (let i = 0; i < n; i++) { sma200ok[i] = isNaN(sma200[i]) || isNaN(sma20[i]) || sma20[i] >= sma200[i]; } } }
      let vsOk = null; if (c.Volume && c.Volume.length === n) { const vol = c.Volume.map(v => v != null ? v : 0); const vma = rollingMean(vol, 20); const sr = vol.map((v, j) => v >= 1.5 * (vma[j] || 1) ? 1 : 0); vsOk = new Array(n).fill(false); for (let i = 0; i < n; i++) { for (let lb = 0; lb < 5 && i - lb >= 0; lb++) { if (sr[i - lb]) { vsOk[i] = true; break; } } } }
      let overextOk = null; if (tfUp === "1W") { overextOk = new Array(n).fill(true); for (let i = 5; i < n; i++) { if (close[i - 5] > 0 && close[i] > close[i - 5] * 1.15) overextOk[i] = false; } }
      function bearishCount(kpis, j, pols) { let nb = 0; const pp = pols || kpis.map(() => 1); for (let ki = 0; ki < kpis.length; ki++) { const k = kpis[ki]; if (k in allKpiZ && j < allKpiZ[k].length && allKpiZ[k][j] !== pp[ki]) nb++; } return nb; }
      let idx = 0;
      while (idx < n) {
        if (!c3Onset[idx]) { idx++; continue; } if (sma200ok && !sma200ok[idx]) { idx++; continue; } if (vsOk && !vsOk[idx]) { idx++; continue; } if (overextOk && !overextOk[idx]) { idx++; continue; }
        const fillBar = idx + 1; if (fillBar >= n) break; const ep = open[fillBar]; if (ep <= 0 || isNaN(ep)) { idx++; continue; }
        const entryIdx = fillBar; let scaled = c4Active[idx], scaleIdx = scaled ? idx : null; let activeKpis = scaled ? combo4kpis : combo3kpis; let activePols = scaled ? combo4pols : combo3pols; let nk = activeKpis.length;
        let stopPrice = ep, stop = atr[idx] > 0 ? stopPrice - K * atr[idx] : stopPrice * 0.95; let barsSinceReset = 0; let exitIdx = null, exitReason = null;
        for (let j = entryIdx + 1; j < n; j++) { barsSinceReset++; const cj = close[j]; if (isNaN(cj)) continue; if (cj < stop) { exitIdx = j; exitReason = "ATR stop"; break; } if (!scaled && c4Active[j]) { scaled = true; scaleIdx = j; activeKpis = combo4kpis; activePols = combo4pols; nk = activeKpis.length; } const nb = bearishCount(activeKpis, j, activePols); const barsHeld = j - entryIdx; if (barsHeld <= T) { if (nb >= nk) { exitIdx = j; exitReason = "Full invalidation"; break; } } else { if (nb >= 2) { exitIdx = j; exitReason = nb + "/" + nk + " KPIs bearish"; break; } } if (barsSinceReset >= M) { if (nb === 0) { stopPrice = cj; stop = atr[j] > 0 ? stopPrice - K * atr[j] : stopPrice * 0.95; barsSinceReset = 0; } else { exitIdx = j; exitReason = "Checkpoint exit"; break; } } }
        if (exitIdx == null) { exitIdx = n - 1; exitReason = "Open"; }
        const exitFill = (exitIdx < n - 1 && exitReason !== "Open") ? exitIdx + 1 : exitIdx; const xp = exitFill !== exitIdx ? open[exitFill] : close[exitIdx]; const cost = 0.001 + SLIPPAGE; const weight = scaled ? 1.5 : 1.0; const ret = ep > 0 ? ((xp - ep) / ep - cost) * 100 * weight : 0;
        trades.push({ entryIdx: entryIdx, exitIdx: exitIdx, ret: ret, hold: exitIdx - entryIdx, label: scaled ? "C4" : "C3", reason: exitReason, scaled: scaled, scaleIdx: scaleIdx, entryDate: x[entryIdx], exitDate: x[exitIdx] });
        idx = exitReason !== "Open" ? exitIdx + 1 : n;
      }
    }

    trades.sort((a, b) => a.exitIdx - b.exitIdx);

    const eqCurve = new Array(n).fill(0);
    let cumRet = 0;
    for (const t of trades) {
      const entryPrice = close[t.entryIdx];
      const weight = t.scaled ? 1.5 : 1.0;
      for (let i = t.entryIdx; i < t.exitIdx && i < n; i++) {
        const unrealised = entryPrice > 0 && close[i] != null ? ((close[i] - entryPrice) / entryPrice) * 100 * weight : 0;
        eqCurve[i] = cumRet + unrealised;
      }
      cumRet += t.ret * weight;
      for (let i = t.exitIdx; i < n; i++) eqCurve[i] = cumRet;
    }

    return { trades: trades, eqCurve: eqCurve, dates: x };
  }

  // Expose globally
  window.buildFigureFromData = buildFigureFromData;
  window.simulateTrades = simulateTrades;

})();
