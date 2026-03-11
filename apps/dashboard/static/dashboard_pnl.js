/* dashboard_pnl.js — P&L tab, trade entry/close modals, trade table */
(function(D) {
  "use strict";
  D = window.Dashboard = window.Dashboard || {};
/* ================================================================== */
/*  P&L Tab — aggregate portfolio P&L                                */
/* ================================================================== */
let _pnlBuilding = false;
let _pnlApiData = null;
let _pnlCacheGroup = null;
let _pnlCacheTF = null;
let _pnlCacheStrategy = null;
let _pnlTableSortCol = "return";
let _pnlTableSortAsc = false;

async function buildPnlTab() {
  if (_pnlBuilding) return;
  // BUG-PL3 fix: include strategy in cache key
  const _pnlStrat = (typeof window.currentStrategy === "string") ? window.currentStrategy : "legacy";
  if (_pnlCacheGroup === currentGroup && _pnlCacheTF === currentTF && _pnlCacheStrategy === _pnlStrat && _pnlApiData) {
    _renderPnlFromApi(_pnlApiData);
    return;
  }
  _pnlBuilding = true;
  _pnlCacheGroup = currentGroup;
  _pnlCacheTF = currentTF;
  _pnlCacheStrategy = _pnlStrat;
  const prog = document.getElementById("pnlProgress");
  if (prog) prog.textContent = "Loading…";

  try {
    var url = "/api/pnl-summary?group=" + encodeURIComponent(currentGroup) +
              "&tf=" + encodeURIComponent(currentTF) +
              "&strategy=" + encodeURIComponent(_pnlStrat);
    var r = await fetch(url, { cache: "no-store" });
    var envelope = await r.json();
    var data = (envelope && envelope.data !== undefined) ? envelope.data : envelope;
    _pnlApiData = data;
    if (prog) prog.textContent = (data.portfolio ? data.portfolio.total_trades : 0) + " trades loaded";
    _renderPnlFromApi(data);
  } catch (e) {
    if (prog) prog.textContent = "Failed to load P&L data";
    console.warn("P&L fetch failed:", e);
  } finally {
    _pnlBuilding = false;
  }
}

function _renderPnlFromApi(data) {
  if (!data || !data.portfolio) return;
  var p = data.portfolio;
  var allTrades = data.all_trades || [];
  var eqDates = (p.equity_curve && p.equity_curve.dates) || [];
  var eqValues = (p.equity_curve && p.equity_curve.values) || [];

  var ddValues = [];
  var ddPeak = -Infinity;
  for (var i = 0; i < eqValues.length; i++) {
    if (eqValues[i] > ddPeak) ddPeak = eqValues[i];
    ddValues.push(eqValues[i] - ddPeak);
  }

  var totalReturn = p.total_return || 0;
  var hr = p.win_rate || 0;
  var avgPnl = p.total_trades ? totalReturn / p.total_trades : 0;
  var maxDD = p.max_dd || 0;
  var pf = p.profit_factor;
  var sharpe = p.sharpe || 0;
  var best = p.best || 0;
  var worst = p.worst || 0;
  var avgWin = p.avg_gain || 0;
  var avgLoss = Math.abs(p.avg_loss || 0);
  var wins = allTrades.filter(function(t) { return t.ret >= 0; }).length;
  var losses = allTrades.length - wins;
  var calmar = maxDD < 0 ? totalReturn / Math.abs(maxDD) : (totalReturn > 0 ? Infinity : 0);
  var expectancy = allTrades.length ? (hr / 100) * avgWin - (1 - hr / 100) * avgLoss : 0;
  var rMultiple = avgLoss > 0 ? avgWin / avgLoss : (avgWin > 0 ? Infinity : 0);

  var statsBar = document.getElementById("pnlStatsBar");
  if (statsBar) {
    var retClass = totalReturn >= 0 ? "pnl-stat-green" : "pnl-stat-red";
    statsBar.innerHTML =
      '<span><span class="pnl-stat-label">Return</span><span class="' + retClass + '">' + (totalReturn >= 0 ? "+" : "") + totalReturn.toFixed(1) + '%</span></span>' +
      '<span><span class="pnl-stat-label">HR</span><span class="pnl-stat-neu">' + hr.toFixed(0) + '%</span></span>' +
      '<span><span class="pnl-stat-label">Avg P&L</span><span class="' + (avgPnl >= 0 ? "pnl-stat-green" : "pnl-stat-red") + '">' + (avgPnl >= 0 ? "+" : "") + avgPnl.toFixed(2) + '%</span></span>' +
      '<span><span class="pnl-stat-label">Max DD</span><span class="pnl-stat-red">' + maxDD.toFixed(1) + '%</span></span>' +
      '<span><span class="pnl-stat-label">PF</span><span class="pnl-stat-neu">' + (pf == null ? "\u221e" : pf.toFixed(1)) + '</span></span>' +
      '<span><span class="pnl-stat-label">Best</span><span class="pnl-stat-green">+' + best.toFixed(1) + '%</span></span>' +
      '<span><span class="pnl-stat-label">Worst</span><span class="pnl-stat-red">' + worst.toFixed(1) + '%</span></span>' +
      '<span><span class="pnl-stat-label">Trades</span><span class="pnl-stat-neu">' + (p.total_trades || 0) + '</span></span>' +
      '<span><span class="pnl-stat-label">Sharpe</span><span class="pnl-stat-neu">' + sharpe.toFixed(2) + '</span></span>' +
      '<span><span class="pnl-stat-label">Calmar</span><span class="pnl-stat-neu">' + (calmar === Infinity ? "\u221e" : calmar.toFixed(2)) + '</span></span>' +
      '<span><span class="pnl-stat-label">Expect.</span><span class="' + (expectancy >= 0 ? "pnl-stat-green" : "pnl-stat-red") + '">' + (expectancy >= 0 ? "+" : "") + expectancy.toFixed(2) + '%</span></span>' +
      '<span><span class="pnl-stat-label">W/L</span><span class="pnl-stat-neu">' + (rMultiple === Infinity ? "\u221e" : rMultiple.toFixed(2)) + '</span></span>';
  }

  // Equity curve chart
  var thm = getPlotlyThemeOverrides();
  var eqTraces = [];
  var eqPos = eqValues.map(function(v) { return v >= 0 ? v : 0; });
  var eqNeg = eqValues.map(function(v) { return v < 0 ? v : 0; });
  eqTraces.push({ type: "scatter", x: eqDates, y: eqPos, fill: "tozeroy", fillcolor: "rgba(38,166,91,0.12)", line: { width: 0 }, hoverinfo: "skip", showlegend: false });
  eqTraces.push({ type: "scatter", x: eqDates, y: eqNeg, fill: "tozeroy", fillcolor: "rgba(234,57,67,0.12)", line: { width: 0 }, hoverinfo: "skip", showlegend: false });
  if (eqDates.length >= 2) {
    eqTraces.push({ type: "scatter", x: [eqDates[0], eqDates[eqDates.length - 1]], y: [0, 0], mode: "lines", line: { color: "rgba(148,163,184,0.3)", width: 1, dash: "dot" }, hoverinfo: "skip", showlegend: false });
  }
  eqTraces.push({ type: "scatter", x: eqDates, y: eqValues, mode: "lines", line: { color: totalReturn >= 0 ? "var(--candle-up)" : "var(--candle-down)", width: 2 }, hovertemplate: "<b>Cum. Return</b>: %{y:.1f}%<br>%{x}<extra></extra>", showlegend: false });
  if (allTrades.length) {
    eqTraces.push({
      type: "scatter", x: allTrades.map(function(t) { return t.exit; }),
      y: (function() { var c2 = 0; return allTrades.map(function(t) { c2 += t.ret; return c2; }); })(),
      mode: "markers",
      marker: { size: 5, color: allTrades.map(function(t) { return t.ret >= 0 ? "rgba(34,197,94,0.6)" : "rgba(239,68,68,0.6)"; }), line: { width: 0 } },
      customdata: allTrades.map(function(t) { return t.symbol + " " + t.label + " " + (t.ret >= 0 ? "+" : "") + t.ret.toFixed(1) + "%"; }),
      hovertemplate: "%{customdata}<extra></extra>", showlegend: false,
    });
  }

  var eqLayout = {
    paper_bgcolor: thm.paper_bgcolor, plot_bgcolor: thm.plot_bgcolor, font: thm.font,
    margin: { t: 30, b: 30, l: 60, r: 20 }, height: 340, autosize: true,
    xaxis: { gridcolor: thm.xaxis.gridcolor, zerolinecolor: thm.xaxis.zerolinecolor },
    yaxis: { gridcolor: thm.xaxis.gridcolor, zerolinecolor: thm.xaxis.zerolinecolor, ticksuffix: "%", title: "" },
    annotations: [{ x: 0.01, y: 1.0, xref: "paper", yref: "paper", xanchor: "left", yanchor: "top", showarrow: false,
      text: "<b>Aggregate Equity Curve</b> \u2014 " + (currentGroup || "all").toUpperCase() + " / " + currentTF, font: { size: 12 } }],
  };
  Plotly.react("pnlEquityChart", eqTraces, eqLayout, { displayModeBar: false, responsive: true });

  var ddTraces = [{ type: "scatter", x: eqDates, y: ddValues, fill: "tozeroy", fillcolor: "rgba(239,68,68,0.15)", line: { color: "var(--candle-down)", width: 1.5 }, hovertemplate: "<b>Drawdown</b>: %{y:.1f}%<br>%{x}<extra></extra>", showlegend: false }];
  var ddLayout = { paper_bgcolor: thm.paper_bgcolor, plot_bgcolor: thm.plot_bgcolor, font: thm.font, margin: { t: 10, b: 30, l: 60, r: 20 }, height: 140, autosize: true, xaxis: { gridcolor: thm.xaxis.gridcolor, zerolinecolor: thm.xaxis.zerolinecolor }, yaxis: { gridcolor: thm.xaxis.gridcolor, zerolinecolor: thm.xaxis.zerolinecolor, ticksuffix: "%", title: "" } };
  Plotly.react("pnlDrawdownChart", ddTraces, ddLayout, { displayModeBar: false, responsive: true });

  var riskPanel = document.getElementById("pnlRiskSummary");
  if (riskPanel) {
    var c4Count = allTrades.filter(function(t) { return t.label === "C4"; }).length;
    var c3Count = allTrades.length - c4Count;
    var avgHoldVal = allTrades.length ? (allTrades.reduce(function(s, t) { return s + (t.hold || 0); }, 0) / allTrades.length).toFixed(0) : "\u2014";
    var c4Ret = c4Count ? allTrades.filter(function(t) { return t.label === "C4"; }).reduce(function(s, t) { return s + t.ret; }, 0) : 0;
    var c3Ret = c3Count ? allTrades.filter(function(t) { return t.label !== "C4"; }).reduce(function(s, t) { return s + t.ret; }, 0) : 0;
    var uniqueSyms = new Set(allTrades.map(function(t) { return t.symbol; })).size;
    riskPanel.innerHTML =
      '<div class="risk-grid">' +
      '<div class="risk-card"><div class="risk-label">Positions</div><div class="risk-val">' + uniqueSyms + ' syms</div></div>' +
      '<div class="risk-card"><div class="risk-label">C3 trades</div><div class="risk-val">' + c3Count + ' (1x)</div><div class="risk-sub ' + (c3Ret >= 0 ? "pnl-stat-green" : "pnl-stat-red") + '">' + (c3Ret >= 0 ? "+" : "") + c3Ret.toFixed(1) + '%</div></div>' +
      '<div class="risk-card"><div class="risk-label">C4 trades</div><div class="risk-val">' + c4Count + ' (1.5x)</div><div class="risk-sub ' + (c4Ret >= 0 ? "pnl-stat-green" : "pnl-stat-red") + '">' + (c4Ret >= 0 ? "+" : "") + c4Ret.toFixed(1) + '%</div></div>' +
      '<div class="risk-card"><div class="risk-label">Avg Hold</div><div class="risk-val">' + avgHoldVal + ' bars</div></div>' +
      '<div class="risk-card"><div class="risk-label">Expectancy</div><div class="risk-val ' + (expectancy >= 0 ? "pnl-stat-green" : "pnl-stat-red") + '">' + (expectancy >= 0 ? "+" : "") + expectancy.toFixed(2) + '%</div></div>' +
      '<div class="risk-card"><div class="risk-label">Calmar</div><div class="risk-val">' + (calmar === Infinity ? "\u221e" : calmar.toFixed(2)) + '</div></div>' +
      '</div>';
  }

  _buildPnlTableFromApi(data.per_symbol || []);
}

function _buildPnlTableFromApi(perSymbol) {
  var wrap = document.getElementById("pnlTable");
  if (!wrap) return;

  var rows = perSymbol.map(function(s) {
    return {
      symbol: s.symbol, displayName: s.name || s.symbol,
      trades: s.trades, hr: s.hit_rate || 0, return: s.return || 0,
      avg: s.trades ? (s.return / s.trades) : 0,
      best: s.avg_gain || 0, worst: s.avg_loss || 0,
      has_open: s.has_open,
    };
  });

  var col = _pnlTableSortCol;
  var asc2 = _pnlTableSortAsc;
  rows.sort(function(a, b) {
    var va = a[col], vb = b[col];
    if (typeof va === "string") return asc2 ? va.localeCompare(vb) : vb.localeCompare(va);
    return asc2 ? va - vb : vb - va;
  });

  var cols = [
    { key: "symbol", label: "Symbol" },
    { key: "displayName", label: "Name" },
    { key: "trades", label: "Trades" },
    { key: "hr", label: "HR" },
    { key: "return", label: "Return" },
    { key: "avg", label: "Avg P&L" },
    { key: "best", label: "Avg Gain" },
    { key: "worst", label: "Avg Loss" },
  ];

  var html = '<table><thead><tr>';
  for (var ci = 0; ci < cols.length; ci++) {
    var c2 = cols[ci];
    var arrow = _pnlTableSortCol === c2.key ? (_pnlTableSortAsc ? " \u25b2" : " \u25bc") : "";
    html += '<th data-pnlsort="' + c2.key + '">' + c2.label + '<span class="sort-arrow">' + arrow + '</span></th>';
  }
  html += '</tr></thead><tbody>';

  for (var ri = 0; ri < rows.length; ri++) {
    var r2 = rows[ri];
    var retClass2 = r2.return >= 0 ? "pnl-stat-green" : "pnl-stat-red";
    var avgClass2 = r2.avg >= 0 ? "pnl-stat-green" : "pnl-stat-red";
    html += '<tr data-pnlsym="' + r2.symbol + '">';
    html += '<td style="text-align:left;font-weight:700;">' + r2.symbol + '</td>';
    html += '<td style="text-align:left;" title="' + (r2.displayName || "").replace(/"/g, "&quot;") + '">' + _truncate(r2.displayName, 22) + '</td>';
    html += '<td>' + r2.trades + '</td>';
    html += '<td>' + r2.hr.toFixed(0) + '%</td>';
    html += '<td class="' + retClass2 + '">' + (r2.return >= 0 ? "+" : "") + r2.return.toFixed(1) + '%</td>';
    html += '<td class="' + avgClass2 + '">' + (r2.avg >= 0 ? "+" : "") + r2.avg.toFixed(2) + '%</td>';
    html += '<td class="pnl-stat-green">+' + r2.best.toFixed(1) + '%</td>';
    html += '<td class="pnl-stat-red">' + r2.worst.toFixed(1) + '%</td>';
    html += '</tr>';
  }

  html += '</tbody></table>';
  wrap.innerHTML = html;

  wrap.querySelectorAll("th[data-pnlsort]").forEach(function(th) {
    th.addEventListener("click", function() {
      var newCol = th.dataset.pnlsort;
      if (_pnlTableSortCol === newCol) _pnlTableSortAsc = !_pnlTableSortAsc;
      else { _pnlTableSortCol = newCol; _pnlTableSortAsc = false; }
      _buildPnlTableFromApi(perSymbol);
    });
  });

  wrap.querySelectorAll("tr[data-pnlsym]").forEach(function(tr) {
    tr.addEventListener("click", function() {
      _showPnlDrillDown(tr.dataset.pnlsym);
    });
  });
}

function _showPnlDrillDown(sym) {
  const drillWrap = document.getElementById("pnlDrillDown");
  if (!drillWrap) return;
  drillWrap.style.display = "block";
  drillWrap.innerHTML = "";

  const header = document.createElement("div");
  header.style.cssText = "display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--border);";
  const title = document.createElement("span");
  title.style.cssText = "font-weight:700;font-size:14px;";
  const dispName = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[sym]) || sym;
  title.textContent = sym + " — " + dispName + " (Multi-TF Drill Down)";
  header.appendChild(title);
  const closeBtn = document.createElement("button");
  closeBtn.textContent = "✕ Close";
  closeBtn.className = "btn-subtle";
  closeBtn.style.cssText = "font-size:11px;padding:3px 8px;cursor:pointer;border:1px solid var(--border);border-radius:4px;background:var(--card-bg);color:var(--fg);";
  closeBtn.addEventListener("click", () => { drillWrap.style.display = "none"; drillWrap.innerHTML = ""; });
  header.appendChild(closeBtn);
  drillWrap.appendChild(header);

  const allTFs = TIMEFRAMES || ["4H", "1D", "1W", "2W", "1M"];
  const grid = document.createElement("div");
  grid.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;padding:10px;";

  allTFs.forEach(tf => {
    const card = document.createElement("div");
    card.style.cssText = "background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:8px;";
    const tfTitle = document.createElement("div");
    tfTitle.style.cssText = "font-weight:700;font-size:12px;margin-bottom:6px;color:var(--fg);";
    tfTitle.textContent = tf;

    const chartId = "pnlDrill_" + sym.replace(/[^a-zA-Z0-9]/g, "_") + "_" + tf;
    const chartDiv = document.createElement("div");
    chartDiv.id = chartId;
    chartDiv.style.cssText = "width:100%;height:200px;";

    card.appendChild(tfTitle);
    card.appendChild(chartDiv);
    grid.appendChild(card);

    // Load data and simulate
    const cacheKey = sym + "|" + tf;
    const fig = figCache[cacheKey];
    const raw = fig && fig._rawPayload ? fig._rawPayload : (fig && fig.c && fig.x ? fig : null);
    if (raw) {
      _renderDrillChart(chartId, raw, tf, sym);
    } else {
      chartDiv.innerHTML = '<div style="color:var(--muted);font-size:11px;padding:20px;text-align:center;">Loading ' + tf + '…</div>';
      loadFig(sym, tf).then(data => {
        const pl = data && data._rawPayload ? data._rawPayload : (data && data.c && data.x ? data : null);
        if (pl) {
          _renderDrillChart(chartId, pl, tf, sym);
        } else {
          chartDiv.innerHTML = '<div style="color:var(--muted);font-size:11px;padding:20px;text-align:center;">No data for ' + tf + '</div>';
        }
      }).catch(() => {
        chartDiv.innerHTML = '<div style="color:var(--muted);font-size:11px;padding:20px;text-align:center;">No data for ' + tf + '</div>';
      });
    }
  });

  drillWrap.appendChild(grid);
}

function _renderDrillChart(chartId, payload, tf, sym) {
  const result = simulateTrades(payload, tf);
  if (!result || !result.trades || !result.trades.length) {
    const el = document.getElementById(chartId);
    if (el) el.innerHTML = '<div style="color:var(--muted);font-size:11px;padding:20px;text-align:center;">No trades</div>';
    return;
  }
  const trades = result.trades;
  const eqDates = [];
  const eqValues = [];
  let cum = 0;
  trades.forEach(t => {
    const w = t.ret * (t.scaled ? 1.5 : 1.0);
    cum += w;
    eqDates.push(t.exitDate);
    eqValues.push(cum);
  });
  const wins = trades.filter(t => t.ret >= 0).length;
  const hr = trades.length ? (wins / trades.length * 100).toFixed(0) : "—";
  const thm = getPlotlyThemeOverrides();
  var traces = [{
    type: "scatter", x: eqDates, y: eqValues, mode: "lines",
    line: { color: cum >= 0 ? "var(--candle-up)" : "var(--candle-down)", width: 1.5 },
    fill: "tozeroy", fillcolor: cum >= 0 ? "rgba(38,166,91,0.08)" : "rgba(234,57,67,0.08)",
    hovertemplate: "%{y:.1f}%<extra>" + sym + " " + tf + "</extra>",
    showlegend: false,
  }];
  var layout = {
    paper_bgcolor: thm.paper_bgcolor, plot_bgcolor: thm.plot_bgcolor,
    font: thm.font, margin: { t: 20, b: 25, l: 45, r: 10 },
    height: 200, autosize: true,
    xaxis: { gridcolor: thm.xaxis.gridcolor, zerolinecolor: thm.xaxis.zerolinecolor, tickfont: { size: 9 } },
    yaxis: { gridcolor: thm.xaxis.gridcolor, ticksuffix: "%", tickfont: { size: 9 } },
    annotations: [{
      x: 0.02, y: 0.98, xref: "paper", yref: "paper",
      xanchor: "left", yanchor: "top", showarrow: false,
      text: "<b>" + (cum >= 0 ? "+" : "") + cum.toFixed(1) + "%</b> | " + trades.length + " trades | HR " + hr + "%",
      font: { size: 10 },
    }],
  };
  Plotly.react(chartId, traces, layout, { displayModeBar: false, responsive: true });
}

  function loadTrades() {
  Promise.all([
    fetch(_BASE + "/api/trades").then(function(r) { return r.json(); }).then(function(raw) { return (raw && raw.data !== undefined) ? raw.data : raw; }),
    fetch(_BASE + "/api/trades/stats").then(function(r) { return r.json(); }).then(function(raw) { return (raw && raw.data !== undefined) ? raw.data : raw; }),
  ]).then(function(arr) {
    var trades = arr[0]; var stats = arr[1];
    renderTradesStats(stats);
    renderOpenTrades(trades.filter(function(t) { return t.status === "open"; }));
    renderClosedTrades(trades.filter(function(t) { return t.status === "closed"; }));
    renderTradesEquity(trades.filter(function(t) { return t.status === "closed"; }));
  }).catch(function() {});
}

  function renderTradesStats(s) {
  var el = document.getElementById("tradesStats");
  if (!el) return;
  el.innerHTML =
    '<div class="trades-stat"><span class="trades-stat-val">' + s.total + '</span><span class="trades-stat-lbl">Trades</span></div>' +
    '<div class="trades-stat"><span class="trades-stat-val">' + s.win_rate + '%</span><span class="trades-stat-lbl">Win Rate</span></div>' +
    '<div class="trades-stat"><span class="trades-stat-val ' + (s.total_pnl >= 0 ? 'trade-pnl-pos' : 'trade-pnl-neg') + '">' + (s.total_pnl >= 0 ? '+' : '') + s.total_pnl + '%</span><span class="trades-stat-lbl">Total P&L</span></div>' +
    '<div class="trades-stat"><span class="trades-stat-val">' + (s.expectancy >= 0 ? '+' : '') + s.expectancy + '%</span><span class="trades-stat-lbl">Expectancy</span></div>' +
    '<div class="trades-stat"><span class="trades-stat-val trade-pnl-pos">+' + s.avg_gain + '%</span><span class="trades-stat-lbl">Avg Win</span></div>' +
    '<div class="trades-stat"><span class="trades-stat-val trade-pnl-neg">' + s.avg_loss + '%</span><span class="trades-stat-lbl">Avg Loss</span></div>';
}

  function renderOpenTrades(trades) {
  var el = document.getElementById("tradesOpenTable");
  if (!el) return;
  if (!trades.length) { el.innerHTML = '<p style="color:var(--muted);font-size:12px;">No open positions</p>'; return; }
  var html = '<table class="trades-table"><thead><tr><th>Symbol</th><th>TF</th><th>Dir</th><th>Entry</th><th>Date</th><th>Size</th><th>Stop</th><th>Unrealized</th><th>Notes</th><th></th></tr></thead><tbody>';
  trades.forEach(function(t) {
    var sm = (typeof SCREENER !== "undefined" && SCREENER.by_symbol && SCREENER.by_symbol[t.symbol] && SCREENER.by_symbol[t.symbol][t.timeframe]);
    var curPrice = sm ? sm.last_close : null;
    var pnl = null;
    if (curPrice && t.entry_price) {
      pnl = t.direction === "long"
        ? ((curPrice - t.entry_price) / t.entry_price * 100)
        : ((t.entry_price - curPrice) / t.entry_price * 100);
    }
    var pnlStr = pnl !== null ? ((pnl >= 0 ? "+" : "") + pnl.toFixed(1) + "%") : "\u2014";
    var pnlCls = pnl !== null ? (pnl >= 0 ? "trade-pnl-pos" : "trade-pnl-neg") : "";
    html += '<tr><td><b>' + t.symbol + '</b></td><td>' + t.timeframe + '</td><td>' + t.direction + '</td>';
    html += '<td>' + t.entry_price.toFixed(2) + '</td><td>' + t.entry_date + '</td>';
    html += '<td>' + t.size + '</td><td>' + (t.stop_price ? t.stop_price.toFixed(2) : "\u2014") + '</td>';
    html += '<td class="' + pnlCls + '">' + pnlStr + '</td>';
    html += '<td style="font-size:10px;color:var(--muted)">' + (t.notes || "") + '</td>';
    html += '<td><button class="trade-btn close-btn" onclick="openCloseTrade(\'' + t.id + '\',\'' + t.symbol + '\',' + t.entry_price + ')">Close</button> ';
    html += '<button class="trade-btn delete-btn" onclick="deleteTrade(\'' + t.id + '\')">&#10005;</button></td></tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

  function renderClosedTrades(trades) {
  var el = document.getElementById("tradesClosedTable");
  if (!el) return;
  if (!trades.length) { el.innerHTML = '<p style="color:var(--muted);font-size:12px;">No closed trades</p>'; return; }
  var html = '<table class="trades-table"><thead><tr><th>Symbol</th><th>TF</th><th>Dir</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Dates</th><th>Notes</th><th></th></tr></thead><tbody>';
  trades.forEach(function(t) {
    var pnl = t.direction === "long"
      ? ((t.exit_price - t.entry_price) / t.entry_price * 100)
      : ((t.entry_price - t.exit_price) / t.entry_price * 100);
    var pnlCls = pnl >= 0 ? "trade-pnl-pos" : "trade-pnl-neg";
    html += '<tr><td><b>' + t.symbol + '</b></td><td>' + t.timeframe + '</td><td>' + t.direction + '</td>';
    html += '<td>' + t.entry_price.toFixed(2) + '</td><td>' + t.exit_price.toFixed(2) + '</td>';
    html += '<td class="' + pnlCls + '">' + (pnl >= 0 ? "+" : "") + pnl.toFixed(1) + '%</td>';
    html += '<td style="font-size:10px">' + t.entry_date + ' \u2192 ' + t.exit_date + '</td>';
    html += '<td style="font-size:10px;color:var(--muted)">' + (t.notes || "") + '</td>';
    html += '<td><button class="trade-btn delete-btn" onclick="deleteTrade(\'' + t.id + '\')">&#10005;</button></td></tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

  function renderTradesEquity(closedTrades) {
  var el = document.getElementById("tradesEquityChart");
  if (!el || typeof Plotly === "undefined") return;
  if (!closedTrades.length) { el.innerHTML = '<p style="color:var(--muted);font-size:12px;">No data for equity curve</p>'; return; }
  var sorted = closedTrades.slice().sort(function(a, b) { return a.exit_date < b.exit_date ? -1 : 1; });
  var cumPnl = 0;
  var x = []; var y = [];
  sorted.forEach(function(t) {
    var pnl = t.direction === "long"
      ? ((t.exit_price - t.entry_price) / t.entry_price * 100)
      : ((t.entry_price - t.exit_price) / t.entry_price * 100);
    cumPnl += pnl;
    x.push(t.exit_date);
    y.push(Math.round(cumPnl * 100) / 100);
  });
  var trace = { x: x, y: y, type: "scatter", mode: "lines+markers", line: { color: "var(--candle-up)", width: 2 }, marker: { size: 4 }, name: "Cum. P&L %" };
  var layout = {
    height: 260, margin: { t: 20, b: 40, l: 50, r: 20 },
    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    xaxis: { color: "var(--muted)", gridcolor: "var(--border)" },
    yaxis: { color: "var(--muted)", gridcolor: "var(--border)", title: { text: "Cum. P&L %", font: { size: 10 } } },
    font: { color: "var(--fg)", size: 10 },
  };
  Plotly.react(el, [trace], layout, { displayModeBar: false, responsive: true });
}

  D.buildPnlTab = buildPnlTab;
  D.loadTrades = loadTrades;

  (function() {
  var subTabs = document.querySelectorAll(".pnl-sub-tab");
  var backtestContent = document.getElementById("pnlBacktestContent");
  var tradesContent = document.getElementById("pnlTradesContent");
  if (!subTabs.length || !backtestContent || !tradesContent) return;

  var currentPnlSub = (typeof _st0 !== "undefined" && _st0 && _st0.pnlSub) ? _st0.pnlSub : "backtest";

  function switchPnlSub(sub) {
    currentPnlSub = sub;
    subTabs.forEach(function(t) { t.classList.toggle("active", t.dataset.pnlSub === sub); });
    backtestContent.style.display = sub === "backtest" ? "" : "none";
    tradesContent.style.display = sub === "trades" ? "" : "none";
    if (typeof saveState === "function") saveState({ pnlSub: sub });
    if (sub === "trades") D.loadTrades();
  }

  subTabs.forEach(function(t) {
    t.addEventListener("click", function() { switchPnlSub(t.dataset.pnlSub); });
  });
  switchPnlSub(currentPnlSub);
  })();

  (function() {
  var enterModal = document.getElementById("enterTradeModal");
  var closeModal = document.getElementById("closeTradeModal");
  var enterBtn = document.getElementById("btnEnterTrade");
  if (!enterModal || !enterBtn) return;

  var _closingTradeId = null;

  // Enter trade modal
  document.getElementById("enterTradeClose").addEventListener("click", function() { enterModal.style.display = "none"; });
  enterModal.addEventListener("click", function(e) { if (e.target === enterModal) enterModal.style.display = "none"; });
  document.getElementById("closeTradeClose").addEventListener("click", function() { closeModal.style.display = "none"; });
  closeModal.addEventListener("click", function(e) { if (e.target === closeModal) closeModal.style.display = "none"; });

  enterBtn.addEventListener("click", function() {
    enterModal.style.display = "flex";
    document.getElementById("tradeEntryDate").value = new Date().toISOString().slice(0, 10);
    document.getElementById("tradeSymbol").value = currentSymbol || "";
    var sm = (typeof SCREENER !== "undefined" && SCREENER.by_symbol && SCREENER.by_symbol[currentSymbol] && SCREENER.by_symbol[currentSymbol][currentTF]);
    if (sm && sm.last_close) document.getElementById("tradeEntryPrice").value = sm.last_close.toFixed(2);
    document.getElementById("tradeTF").value = currentTF || "1D";
    document.getElementById("tradeStatus").textContent = "";
    document.getElementById("tradeSymbol").focus();
  });

  document.getElementById("tradeSubmit").addEventListener("click", function() {
    var sym = (document.getElementById("tradeSymbol").value || "").trim().toUpperCase();
    var price = parseFloat(document.getElementById("tradeEntryPrice").value);
    var date = document.getElementById("tradeEntryDate").value;
    var size = parseFloat(document.getElementById("tradeSize").value) || 1;
    var stop = parseFloat(document.getElementById("tradeStopPrice").value) || null;
    var dir = document.getElementById("tradeDirection").value;
    var tf = document.getElementById("tradeTF").value;
    var notes = document.getElementById("tradeNotes").value || "";
    var status = document.getElementById("tradeStatus");

    if (!sym || !price || !date) {
      status.textContent = "Symbol, price, and date are required";
      status.className = "modal-status err";
      return;
    }
    var ccy = (typeof SYMBOL_CURRENCIES !== "undefined" && SYMBOL_CURRENCIES[sym]) || "USD";
    status.textContent = "Submitting\u2026";
    status.className = "modal-status loading";
    fetch(_BASE + "/api/trades", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: sym, entry_price: price, entry_date: date, timeframe: tf, direction: dir, size: size, stop_price: stop, notes: notes, currency: ccy }),
    })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.ok) {
          status.textContent = "Trade recorded!";
          status.className = "modal-status ok";
          setTimeout(function() { enterModal.style.display = "none"; D.loadTrades(); }, 800);
        } else {
          status.textContent = d.error || "Failed";
          status.className = "modal-status err";
        }
      })
      .catch(function(err) {
        status.textContent = "Error: " + err;
        status.className = "modal-status err";
      });
  });

  // Close trade modal
  document.getElementById("closeTradeSubmit").addEventListener("click", function() {
    var exitPrice = parseFloat(document.getElementById("closeTradeExitPrice").value);
    var exitDate = document.getElementById("closeTradeExitDate").value;
    var status = document.getElementById("closeTradeStatus");
    if (!exitPrice || !exitDate || !_closingTradeId) {
      status.textContent = "Price and date required";
      status.className = "modal-status err";
      return;
    }
    status.textContent = "Closing\u2026";
    status.className = "modal-status loading";
    fetch(_BASE + "/api/trades/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: _closingTradeId, exit_price: exitPrice, exit_date: exitDate }),
    })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (d.ok) {
          status.textContent = "Trade closed!";
          status.className = "modal-status ok";
          setTimeout(function() { closeModal.style.display = "none"; D.loadTrades(); }, 600);
        } else {
          status.textContent = d.error || "Failed";
          status.className = "modal-status err";
        }
      })
      .catch(function(err) {
        status.textContent = "Error: " + err;
        status.className = "modal-status err";
      });
  });

  window.openCloseTrade = function(tradeId, sym, entryPrice) {
    _closingTradeId = tradeId;
    closeModal.style.display = "flex";
    document.getElementById("closeTradeExitDate").value = new Date().toISOString().slice(0, 10);
    document.getElementById("closeTradeInfo").innerHTML = "<p style='font-size:12px;'>Closing <b>" + sym + "</b> \u2014 Entry: " + entryPrice.toFixed(2) + "</p>";
    var sm = (typeof SCREENER !== "undefined" && SCREENER.by_symbol && SCREENER.by_symbol[sym] && SCREENER.by_symbol[sym][currentTF]);
    document.getElementById("closeTradeExitPrice").value = (sm && sm.last_close) ? sm.last_close.toFixed(2) : "";
    document.getElementById("closeTradeStatus").textContent = "";
  };

  window.deleteTrade = function(tradeId) {
    if (!confirm("Delete this trade?")) return;
    fetch(_BASE + "/api/trades/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: tradeId }),
    })
      .then(function(r) { return r.json(); })
      .then(function() { D.loadTrades(); })
      .catch(function() {});
  };
  })();
})(window.Dashboard = window.Dashboard || {});
