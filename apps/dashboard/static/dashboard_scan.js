/* dashboard_scan.js — Scan tab: new signals, positions at risk, pre-signals,
   strategy cards, decision logigram, scan history.
   All items 1-9 from the trading decision checklist. */
(function(D) {
  "use strict";
  if (!D) return;

  // ── State ────────────────────────────────────────────────────────────────
  var _scanTf = "1D";      // currently selected TF pill
  var _scanLogData = null; // cached /api/scan-log response
  var _scanLogLoaded = false;

  // ── Helpers ──────────────────────────────────────────────────────────────
  function _screenerRows(tf) {
    return (typeof SCREENER !== "undefined" && SCREENER && SCREENER.rows_by_tf && SCREENER.rows_by_tf[tf])
      ? SCREENER.rows_by_tf[tf] : [];
  }
  function _bySymbol(sym, tf) {
    return (typeof SCREENER !== "undefined" && SCREENER && SCREENER.by_symbol
      && SCREENER.by_symbol[sym] && SCREENER.by_symbol[sym][tf])
      ? SCREENER.by_symbol[sym][tf] : null;
  }
  function _allTfs() { return typeof TIMEFRAMES !== "undefined" ? TIMEFRAMES : ["1D","1W","2W","1M"]; }
  function _isToday(dateStr) {
    if (!dateStr) return false;
    var today = new Date();
    var ymd = today.getFullYear() + "-" +
      String(today.getMonth() + 1).padStart(2, "0") + "-" +
      String(today.getDate()).padStart(2, "0");
    return String(dateStr).slice(0, 10) === ymd;
  }
  function _strategies() {
    if (typeof STRATEGY_SETUPS === "undefined") return [];
    var setups = STRATEGY_SETUPS.setups || {};
    return Object.keys(setups).filter(function(k) { return setups[k].entry_type !== "threshold"; });
  }
  function _stratLabel(key) {
    var setups = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS.setups) || {};
    return (setups[key] && setups[key].label) ? setups[key].label : key;
  }
  function _stratColor(key) {
    var setups = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS.setups) || {};
    return (setups[key] && setups[key].color) ? setups[key].color : "#888";
  }
  function _fmt(n, dec) { return (typeof n === "number") ? n.toFixed(dec || 0) : "—"; }
  function _pct(n) { return (typeof n === "number") ? (n > 0 ? "+" : "") + n.toFixed(1) + "%" : "—"; }

  // Risk% = (price - atr_stop) / price * 100
  function _riskPct(row) {
    if (!row || !row.last_close || !row.atr_stop) return null;
    return (row.last_close - row.atr_stop) / row.last_close * 100;
  }

  // Implied position size for 1% portfolio risk
  function _posSize(row, portfolioEur) {
    var rp = _riskPct(row);
    if (!rp || rp <= 0) return null;
    var budget = (portfolioEur || 100000);
    var riskAmt = budget * 0.01;
    var stopDist = row.last_close - row.atr_stop;
    if (!stopDist || stopDist <= 0) return null;
    var shares = Math.floor(riskAmt / stopDist);
    return { shares: shares, amount: Math.round(shares * row.last_close) };
  }

  // Freshness tier from last_combo_bars
  function _freshness(bars) {
    if (bars == null) return { label: "—", cls: "" };
    if (bars <= 1) return { label: "&#128994; Fresh", cls: "fresh-green" };
    if (bars <= 3) return { label: "&#128993; Recent", cls: "fresh-yellow" };
    return { label: "&#128308; Stale", cls: "fresh-red" };
  }

  // Count TFs where a symbol has ENTRY or HOLD for ANY strategy
  function _tfAlignment(sym) {
    var tfs = _allTfs();
    var count = 0;
    tfs.forEach(function(tf) {
      var rec = _bySymbol(sym, tf);
      if (!rec) return;
      var strats = rec.strat_statuses || {};
      var hasSignal = Object.keys(strats).some(function(k) {
        var a = strats[k].signal_action || "";
        return a.indexOf("ENTRY") === 0 || a === "HOLD";
      });
      if (!hasSignal) {
        var a = rec.signal_action || "";
        hasSignal = a.indexOf("ENTRY") === 0 || a === "HOLD";
      }
      if (hasSignal) count++;
    });
    return count;
  }

  // Which strategies have an active signal for a symbol on a given TF
  function _activeStrategies(sym, tf) {
    var rec = _bySymbol(sym, tf);
    if (!rec) return [];
    var active = [];
    var strats = rec.strat_statuses || {};
    Object.keys(strats).forEach(function(k) {
      var a = strats[k].signal_action || "";
      if (a.indexOf("ENTRY") === 0 || a === "HOLD") active.push(k);
    });
    // legacy signal_action
    if (!active.length) {
      var a = rec.signal_action || "";
      if (a.indexOf("ENTRY") === 0 || a === "HOLD") active.push("trend");
    }
    return active;
  }

  // Aggregate l12m stats from screener rows for a strategy
  function _stratStats(stratKey) {
    var tfs = _allTfs();
    var rows = _screenerRows(tfs[1] || "1D"); // use 1D as base
    var hits = [], pnls = [];
    rows.forEach(function(r) {
      var ss = (r.strat_statuses || {})[stratKey];
      if (!ss) return;
      if (typeof ss.l12m_hit_rate === "number") hits.push(ss.l12m_hit_rate);
      if (typeof ss.l12m_pnl === "number") pnls.push(ss.l12m_pnl);
    });
    if (!hits.length) return null;
    var avgHit = hits.reduce(function(a, b) { return a + b; }, 0) / hits.length;
    var avgPnl = pnls.length ? pnls.reduce(function(a, b) { return a + b; }, 0) / pnls.length : null;
    return { hit_rate: avgHit, avg_pnl: avgPnl, n: hits.length };
  }

  // ── Live Scan Stats Bar (shown during/after SSE stream) ──────────────────
  function _renderLiveScanStats(d) {
    var bar = document.getElementById("scanStatsBar");
    if (!bar) return;

    var chips = [];

    function chip(label, value, cls) {
      return '<span class="scan-stat-chip' + (cls ? " " + cls : "") + '">' +
        '<span class="scan-stat-label">' + label + '</span>' +
        '<span class="scan-stat-value">' + value + '</span>' +
        '</span>';
    }

    var scanned = d.universe_total || 0;
    var dlOk    = d.downloaded_ok  || 0;
    var dlFail  = d.downloaded_fail || 0;
    var raw     = d.raw_signals    || 0;
    var conf    = d.total          || 0;
    var filt    = d.filtered_open  || 0;
    var elapsed = d.elapsed_s != null ? d.elapsed_s : null;

    if (scanned) chips.push(chip("Scanned", scanned));
    if (dlOk || dlFail) {
      var dlVal = dlOk + (dlFail > 0 ? ' <span class="scan-stat-warn">(' + dlFail + " failed)</span>" : "");
      chips.push(chip("Downloaded", dlVal, dlFail > 0 ? "chip-warn" : ""));
    }
    if (raw)  chips.push(chip("Raw candidates", raw));
    if (conf != null) chips.push(chip("Confirmed", conf, conf > 0 ? "chip-good" : ""));
    if (filt) chips.push(chip("Filtered (open pos)", filt, "chip-muted"));

    var byTf = d.by_tf || {};
    var tfKeys = Object.keys(byTf).filter(function(k) { return byTf[k] > 0; });
    if (tfKeys.length > 1) {
      var tfStr = tfKeys.map(function(k) { return k + ": " + byTf[k]; }).join(" · ");
      chips.push(chip("By TF", tfStr));
    }

    if (elapsed != null) chips.push(chip("Time", elapsed + "s", "chip-muted"));

    bar.innerHTML = chips.join("");
    bar.style.display = chips.length ? "flex" : "none";
  }

  // expose so dashboard.js SSE complete handler can call it
  window._renderScanStats = _renderLiveScanStats;

  // ── TF Pill wiring ────────────────────────────────────────────────────────
  function _initTfPills() {
    document.querySelectorAll(".scan-tf-pill").forEach(function(btn) {
      btn.addEventListener("click", function() {
        document.querySelectorAll(".scan-tf-pill").forEach(function(b) { b.classList.remove("active"); });
        btn.classList.add("active");
        _scanTf = btn.dataset.tf;
        _renderNewSignals();
        _renderPreSignals();
        _renderPositionsAtRisk();
      });
    });
  }

  // ── Export All ────────────────────────────────────────────────────────────
  function _initExportAll() {
    var btn = document.getElementById("scanExportAllBtn");
    if (!btn) return;
    btn.addEventListener("click", function() {
      var tfs = _allTfs();
      var allRows = [];
      tfs.forEach(function(tf) {
        var rows = _screenerRows(tf);
        rows.forEach(function(r) {
          if (!r.combo_3_new && !r.combo_4_new) return;
          var activeStrats = _activeStrategies(r.symbol, tf);
          allRows.push({
            tf: tf,
            symbol: r.symbol || "",
            name: r.name || "",
            sector: r.sector || "",
            combo: r.combo_4_new ? "C4" : "C3",
            strategies: activeStrats.join("|"),
            trend_score: r.trend_score || "",
            price: r.last_close || "",
            risk_pct: _riskPct(r) != null ? _riskPct(r).toFixed(1) : "",
            freshness: r.last_combo_bars != null ? r.last_combo_bars : "",
            tf_align: _tfAlignment(r.symbol),
            scan_date: (SCREENER && SCREENER.generated_utc) || "",
          });
        });
      });
      var cols = ["tf","symbol","name","sector","combo","strategies","trend_score","price","risk_pct","freshness","tf_align","scan_date"];
      var lines = [cols.join(",")].concat(allRows.map(function(r) {
        return cols.map(function(c) { return JSON.stringify(r[c] != null ? r[c] : ""); }).join(",");
      }));
      var blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url; a.download = "scan_all_tfs.csv";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function() { URL.revokeObjectURL(url); }, 2000);
    });
  }

  // ── Section 1: New Signals ────────────────────────────────────────────────
  function _renderNewSignals() {
    var wrap = document.getElementById("scanNewSignals");
    var countEl = document.getElementById("scanNewCount");
    var metaEl = document.getElementById("scanNewMeta");
    if (!wrap) return;

    var tf = (_scanTf === "all") ? "1D" : _scanTf;
    var rows = _screenerRows(tf).filter(function(r) {
      return r.combo_3_new || r.combo_4_new || _isToday(r.scan_last_confirmed);
    });

    if (countEl) countEl.textContent = rows.length;
    if (metaEl) {
      var ts = (typeof SCREENER !== "undefined" && SCREENER.generated_utc) ? SCREENER.generated_utc : "";
      metaEl.textContent = ts ? "Last scan: " + ts.replace("T", " ").replace("+00:00","").replace("Z","") + " UTC" : "";
    }

    wrap.innerHTML = "";
    if (!rows.length) {
      wrap.innerHTML = '<div class="scan-empty">No new signals on ' + tf + ' — run a scan to update.</div>';
      return;
    }

    // Group by sector for item 4 (sector clustering)
    var bySector = {};
    rows.forEach(function(r) {
      var s = r.sector || "Unknown";
      if (!bySector[s]) bySector[s] = [];
      bySector[s].push(r);
    });

    // Sector cluster warning header
    var hotSectors = Object.keys(bySector).filter(function(s) { return bySector[s].length >= 3; });
    if (hotSectors.length) {
      var warn = document.createElement("div");
      warn.className = "scan-sector-warn";
      warn.innerHTML = "&#9888; Sector cluster detected: " +
        hotSectors.map(function(s) {
          return "<b>" + s + "</b> (" + bySector[s].length + " signals)";
        }).join(", ") + " — consider reducing size per position.";
      wrap.appendChild(warn);
    }

    var table = document.createElement("table");
    table.className = "scan-table";
    var thead = document.createElement("thead");
    var hdr = [
      ["symbol","Stock","13%"],["combo","Combo","5%"],["strategies","Strategies","9%"],
      ["action","Action","6%"],["sector","Sector","8%"],["price","Price","6%"],["risk","Risk%","6%"],
      ["size","Size@1%","6%"],["fresh","Freshness","7%"],["align","TF Align","6%"],
      ["ts","TrendScore","8%"],["date_added","Added","8%"],
    ];
    var colgroup = document.createElement("colgroup");
    hdr.forEach(function(h) { var c = document.createElement("col"); c.style.width = h[2]; colgroup.appendChild(c); });
    table.appendChild(colgroup);
    var trh = document.createElement("tr");
    hdr.forEach(function(h) {
      var th = document.createElement("th"); th.textContent = h[1];
      if (["risk","size","fresh","align","ts","combo"].indexOf(h[0]) >= 0) th.style.textAlign = "center";
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    // Sort: C4 first, then C3, then scan-only, then by trend_score desc
    rows.sort(function(a, b) {
      var rankA = a.combo_4_new ? 0 : a.combo_3_new ? 1 : 2;
      var rankB = b.combo_4_new ? 0 : b.combo_3_new ? 1 : 2;
      if (rankA !== rankB) return rankA - rankB;
      return (b.trend_score || 0) - (a.trend_score || 0);
    });

    rows.forEach(function(r) {
      var tr = document.createElement("tr");
      var sym = String(r.symbol || "").toUpperCase();
      var risk = _riskPct(r);
      var fresh = _freshness(r.last_combo_bars);
      var align = _tfAlignment(sym);
      var activeStrats = _activeStrategies(sym, tf);
      var size = _posSize(r);

      hdr.forEach(function(h) {
        var td = document.createElement("td");
        if (["risk","size","fresh","align","ts","combo"].indexOf(h[0]) >= 0) td.style.textAlign = "center";

        if (h[0] === "symbol") {
          var a = document.createElement("span");
          a.className = "link";
          a.innerHTML = "<b>" + sym + "</b>" + (r.name ? '<br><span style="font-size:10px;color:var(--muted)">' + r.name + "</span>" : "");
          a.title = (r.sector || "") + (r.industry ? " — " + r.industry : "");
          a.addEventListener("click", function() {
            if (typeof currentSymbol !== "undefined") {
              currentSymbol = sym;
              if (typeof saveState === "function") saveState({ symbol: sym });
              if (typeof buildSymbolList === "function") buildSymbolList();
              if (typeof switchTab === "function") switchTab("strategy");
              if (typeof renderChart === "function") renderChart();
            }
          });
          td.appendChild(a);

        } else if (h[0] === "combo") {
          var badge = document.createElement("span");
          if (r.combo_4_new) {
            badge.className = "scan-combo-badge scan-combo-c4";
            badge.textContent = "C4";
          } else if (r.combo_3_new) {
            badge.className = "scan-combo-badge scan-combo-c3";
            badge.textContent = "C3";
          } else {
            // scan-confirmed today but screener hasn't caught up yet
            badge.className = "scan-combo-badge scan-combo-scan";
            badge.textContent = "SCAN";
            badge.title = "Confirmed by today\u2019s scan — screener will update on next rebuild";
          }
          td.appendChild(badge);

        } else if (h[0] === "strategies") {
          activeStrats.forEach(function(k) {
            var sb = document.createElement("span");
            sb.className = "scan-strat-badge";
            sb.style.borderColor = _stratColor(k);
            sb.style.color = _stratColor(k);
            sb.textContent = _stratLabel(k);
            sb.title = k;
            td.appendChild(sb);
          });
          if (!activeStrats.length) { td.style.color = "var(--muted)"; td.textContent = "—"; }

        } else if (h[0] === "sector") {
          td.textContent = r.sector || "—";
          td.style.fontSize = "11px";
          if (hotSectors.indexOf(r.sector) >= 0) {
            td.style.color = "var(--warning)";
            td.style.fontWeight = "600";
          }

        } else if (h[0] === "price") {
          td.innerHTML = (r.last_close ? "<b>" + r.last_close.toFixed(2) + "</b>" : "—") +
            (typeof r.delta_pct === "number" ? '<br><span style="font-size:9px;color:' + (r.delta_pct >= 0 ? "var(--candle-up)" : "var(--candle-down)") + '">' + _pct(r.delta_pct) + "</span>" : "");

        } else if (h[0] === "risk") {
          if (risk != null) {
            var rEl = document.createElement("span");
            rEl.className = "scan-risk-badge" + (risk < 5 ? " risk-low" : risk < 8 ? " risk-mid" : " risk-high");
            rEl.textContent = risk.toFixed(1) + "%";
            rEl.title = "ATR stop: " + (r.atr_stop ? r.atr_stop.toFixed(2) : "—");
            td.appendChild(rEl);
          } else { td.textContent = "—"; td.style.color = "var(--muted)"; }

        } else if (h[0] === "size") {
          if (size) {
            td.innerHTML = "<span title='Shares for 1% portfolio risk at €100k'>" + size.shares + " sh<br><span style='font-size:9px;color:var(--muted)'>€" + size.amount.toLocaleString() + "</span></span>";
          } else { td.textContent = "—"; td.style.color = "var(--muted)"; }

        } else if (h[0] === "fresh") {
          var fEl = document.createElement("span");
          fEl.className = "scan-fresh " + fresh.cls;
          fEl.innerHTML = fresh.label;
          if (r.last_combo_bars != null) fEl.title = r.last_combo_bars + " bars ago";
          td.appendChild(fEl);

        } else if (h[0] === "align") {
          var tfs2 = _allTfs().length;
          var aEl = document.createElement("span");
          aEl.className = "scan-align-badge" + (align >= 3 ? " align-strong" : align >= 2 ? " align-mod" : "");
          aEl.textContent = align + "/" + tfs2;
          aEl.title = align + " of " + tfs2 + " timeframes have an active signal";
          td.appendChild(aEl);

        } else if (h[0] === "ts") {
          var ts2 = r.trend_score;
          var maxTS = (typeof MAX_TREND_SCORE === "number" && MAX_TREND_SCORE > 0) ? MAX_TREND_SCORE : 28.2;
          if (typeof ts2 === "number") {
            var pct2 = Math.round((ts2 / maxTS) * 100);
            td.innerHTML = '<span style="font-weight:700;color:' + (ts2 >= 0 ? "var(--candle-up)" : "var(--candle-down)") + '">' + (pct2 > 0 ? "+" : "") + pct2 + "%</span>";
            td.title = "TrendScore: " + ts2.toFixed(1) + " / " + maxTS.toFixed(1);
          } else { td.textContent = "—"; }

        } else if (h[0] === "action") {
          var act = r.signal_action || "FLAT";
          var aSpan = document.createElement("span");
          aSpan.style.cssText = "font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;white-space:nowrap;";
          if (act.indexOf("ENTRY") === 0 || act.indexOf("SCALE") === 0) {
            aSpan.style.background = "var(--action-entry-bg)"; aSpan.style.color = "var(--action-entry-fg)";
          } else if (act === "HOLD") {
            aSpan.style.background = "var(--action-hold-bg)"; aSpan.style.color = "var(--action-hold-fg)";
          } else {
            aSpan.style.color = "var(--muted)";
          }
          aSpan.textContent = act;
          td.appendChild(aSpan);

        } else if (h[0] === "date_added") {
          td.style.cssText = "font-size:10px;color:var(--muted);text-align:center;";
          td.textContent = r.scan_date_added || "—";
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
  }

  // ── Section 2: Positions at Risk (item 6) ────────────────────────────────
  function _renderPositionsAtRisk() {
    var wrap = document.getElementById("scanPositionsAtRisk");
    var countEl = document.getElementById("scanRiskCount");
    if (!wrap) return;

    var tf = (_scanTf === "all") ? "1D" : _scanTf;
    var rows = _screenerRows(tf).filter(function(r) {
      var a = r.signal_action || "";
      var inPos = a.indexOf("ENTRY") === 0 || a.indexOf("SCALE") === 0 || a === "HOLD";
      if (!inPos) return false;
      var risk = _riskPct(r);
      return risk != null && risk < 10;
    });

    if (countEl) countEl.textContent = rows.length;
    wrap.innerHTML = "";
    if (!rows.length) {
      wrap.innerHTML = '<div class="scan-empty">No open positions approaching their stop on ' + tf + '.</div>';
      return;
    }

    rows.sort(function(a, b) { return (_riskPct(a) || 99) - (_riskPct(b) || 99); });

    var table = document.createElement("table");
    table.className = "scan-table";
    var hdr = [["sym","Stock","16%"],["action","Status","8%"],["entry","Entry","7%"],
               ["stop","ATR Stop","7%"],["price","Current","7%"],["risk","Risk%","7%"],
               ["bars","Held","5%"],["pnl","Unreal. P&L","8%"]];
    var colgroup = document.createElement("colgroup");
    hdr.forEach(function(h) { var c = document.createElement("col"); c.style.width = h[2]; colgroup.appendChild(c); });
    table.appendChild(colgroup);
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    hdr.forEach(function(h) { var th = document.createElement("th"); th.textContent = h[1]; trh.appendChild(th); });
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    rows.forEach(function(r) {
      var tr = document.createElement("tr");
      var risk = _riskPct(r);
      if (risk != null && risk < 3) tr.classList.add("risk-row-critical");
      else if (risk != null && risk < 6) tr.classList.add("risk-row-warn");

      hdr.forEach(function(h) {
        var td = document.createElement("td");
        if (h[0] === "sym") {
          td.innerHTML = "<b>" + r.symbol + "</b>" + (r.name ? '<br><span style="font-size:10px;color:var(--muted)">' + r.name + "</span>" : "");
        } else if (h[0] === "action") {
          var a = r.signal_action || "";
          td.innerHTML = '<span style="font-size:10px;font-weight:700;padding:1px 5px;border-radius:3px;background:var(--action-hold-bg);color:var(--action-hold-fg)">' + a + "</span>";
        } else if (h[0] === "entry") {
          td.textContent = r.entry_price ? r.entry_price.toFixed(2) : "—";
        } else if (h[0] === "stop") {
          td.innerHTML = r.atr_stop
            ? '<span style="color:var(--danger);font-weight:700">' + r.atr_stop.toFixed(2) + "</span>" : "—";
        } else if (h[0] === "price") {
          td.textContent = r.last_close ? r.last_close.toFixed(2) : "—";
        } else if (h[0] === "risk") {
          var rEl = document.createElement("span");
          rEl.className = "scan-risk-badge" + (risk < 3 ? " risk-critical" : risk < 6 ? " risk-high" : " risk-mid");
          rEl.textContent = risk != null ? risk.toFixed(1) + "%" : "—";
          td.appendChild(rEl);
        } else if (h[0] === "bars") {
          td.textContent = r.bars_held != null ? r.bars_held + "b" : "—";
          td.style.color = "var(--muted)"; td.style.fontSize = "11px";
        } else if (h[0] === "pnl") {
          if (r.entry_price && r.last_close) {
            var pnl = (r.last_close - r.entry_price) / r.entry_price * 100;
            td.innerHTML = '<span style="font-weight:700;color:' + (pnl >= 0 ? "var(--candle-up)" : "var(--candle-down)") + '">' + (pnl > 0 ? "+" : "") + pnl.toFixed(1) + "%</span>";
          } else { td.textContent = "—"; td.style.color = "var(--muted)"; }
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
  }

  // ── Section 3: Pre-Signals / Almost There (item 7) ───────────────────────
  function _renderPreSignals() {
    var wrap = document.getElementById("scanPreSignals");
    var countEl = document.getElementById("scanPreCount");
    if (!wrap) return;

    var tf = (_scanTf === "all") ? "1D" : _scanTf;
    var rows = _screenerRows(tf);
    var preRows = [];

    rows.forEach(function(r) {
      if (!r.kpi_states) return;
      var a = r.signal_action || "";
      if (a.indexOf("ENTRY") === 0 || a === "HOLD") return; // already active
      if (r.combo_3 || r.combo_3_new) return; // already has combo

      // Check strat_statuses for partial combos
      var bestPartial = 0;
      var bestStrat = null;
      var strats = r.strat_statuses || {};
      Object.keys(strats).forEach(function(k) {
        var ss = strats[k];
        if (!ss || (ss.signal_action || "").indexOf("ENTRY") === 0 || ss.signal_action === "HOLD") return;
        // Count bullish KPIs by checking kpi_states
        var kpiStates = r.kpi_states || {};
        var setups = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS.setups) || {};
        var sdef = setups[k];
        if (!sdef) return;
        var combos = sdef.combos || {};
        var c3 = combos.c3 || {};
        var kpis = c3.kpis || [];
        var pols = c3.pols || [];
        var bullCount = 0;
        kpis.forEach(function(kpi, i) {
          var state = kpiStates[kpi];
          var pol = pols[i] || 1;
          if (pol === 1 && state === 1) bullCount++;
          if (pol === -1 && state === -1) bullCount++;
        });
        var needed = kpis.length;
        if (needed > 0 && bullCount === needed - 1) {
          if (bullCount > bestPartial) { bestPartial = bullCount; bestStrat = k; }
        }
      });
      if (bestStrat) preRows.push({ row: r, strat: bestStrat, bullCount: bestPartial });
    });

    if (countEl) countEl.textContent = preRows.length;
    wrap.innerHTML = "";
    if (!preRows.length) {
      wrap.innerHTML = '<div class="scan-empty">No stocks at 2/3 KPIs on ' + tf + '.</div>';
      return;
    }

    preRows.sort(function(a, b) { return (b.row.trend_score || 0) - (a.row.trend_score || 0); });
    preRows = preRows.slice(0, 30); // cap at 30

    var table = document.createElement("table");
    table.className = "scan-table";
    var hdr = [["sym","Stock","16%"],["strat","Strategy","10%"],["kpis","KPIs Ready","8%"],
               ["ts","TrendScore","8%"],["price","Price","7%"],["risk","Risk%","7%"],["sector","Sector","10%"]];
    var colgroup = document.createElement("colgroup");
    hdr.forEach(function(h) { var c = document.createElement("col"); c.style.width = h[2]; colgroup.appendChild(c); });
    table.appendChild(colgroup);
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    hdr.forEach(function(h) { var th = document.createElement("th"); th.textContent = h[1]; trh.appendChild(th); });
    thead.appendChild(trh); table.appendChild(thead);

    var tbody = document.createElement("tbody");
    preRows.forEach(function(item) {
      var r = item.row;
      var tr = document.createElement("tr");
      hdr.forEach(function(h) {
        var td = document.createElement("td");
        if (h[0] === "sym") {
          var a = document.createElement("span"); a.className = "link";
          a.innerHTML = "<b>" + r.symbol + "</b>" + (r.name ? '<br><span style="font-size:10px;color:var(--muted)">' + r.name + "</span>" : "");
          a.addEventListener("click", function() {
            if (typeof currentSymbol !== "undefined") {
              currentSymbol = r.symbol;
              if (typeof saveState === "function") saveState({ symbol: r.symbol });
              if (typeof buildSymbolList === "function") buildSymbolList();
              if (typeof switchTab === "function") switchTab("strategy");
              if (typeof renderChart === "function") renderChart();
            }
          });
          td.appendChild(a);
        } else if (h[0] === "strat") {
          var sb = document.createElement("span");
          sb.className = "scan-strat-badge";
          sb.style.borderColor = _stratColor(item.strat);
          sb.style.color = _stratColor(item.strat);
          sb.textContent = _stratLabel(item.strat);
          td.appendChild(sb);
        } else if (h[0] === "kpis") {
          var setups2 = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS.setups) || {};
          var sdef2 = setups2[item.strat];
          var needed2 = sdef2 && sdef2.combos && sdef2.combos.c3 ? (sdef2.combos.c3.kpis || []).length : 3;
          td.innerHTML = '<span style="font-weight:700;color:var(--warning)">' + item.bullCount + "/" + needed2 + '</span> <span style="font-size:10px;color:var(--muted)">bullish</span>';
        } else if (h[0] === "ts") {
          var ts3 = r.trend_score;
          var maxTS3 = (typeof MAX_TREND_SCORE === "number" && MAX_TREND_SCORE > 0) ? MAX_TREND_SCORE : 28.2;
          if (typeof ts3 === "number") {
            var p3 = Math.round((ts3 / maxTS3) * 100);
            td.innerHTML = '<span style="font-weight:700;color:' + (ts3 >= 0 ? "var(--candle-up)" : "var(--candle-down)") + '">' + (p3 > 0 ? "+" : "") + p3 + "%</span>";
          } else { td.textContent = "—"; }
        } else if (h[0] === "price") {
          td.textContent = r.last_close ? r.last_close.toFixed(2) : "—";
        } else if (h[0] === "risk") {
          var rp = _riskPct(r);
          td.textContent = rp != null ? rp.toFixed(1) + "%" : "—";
          td.style.color = rp != null && rp < 5 ? "var(--candle-up)" : "var(--muted)";
        } else if (h[0] === "sector") {
          td.textContent = r.sector || "—"; td.style.fontSize = "11px";
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
  }

  // ── Section 4: Strategy Cards (item 5 — hit rates) ───────────────────────
  function _renderStrategyCards() {
    var wrap = document.getElementById("scanStrategyCards");
    if (!wrap) return;
    wrap.innerHTML = "";
    var strats = _strategies();
    if (!strats.length) {
      wrap.innerHTML = '<div class="scan-empty">No strategy configuration available.</div>';
      return;
    }
    var setups = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS.setups) || {};

    strats.forEach(function(key) {
      var def = setups[key] || {};
      var stats = _stratStats(key);
      var combos = def.combos || {};
      var c3kpis = (combos.c3 && combos.c3.kpis) ? combos.c3.kpis : [];
      var c4kpis = (combos.c4 && combos.c4.kpis) ? combos.c4.kpis : [];
      var c3pols = (combos.c3 && combos.c3.pols) ? combos.c3.pols : [];
      var c4pols = (combos.c4 && combos.c4.pols) ? combos.c4.pols : [];
      var gates = def.entry_gates || {};
      var color = def.color || "#888";

      var card = document.createElement("div");
      card.className = "scan-strategy-card";
      card.style.borderTopColor = color;

      var polIcon = function(pol) { return pol === 1 ? "&#8593;" : "&#8595;"; };

      card.innerHTML =
        '<div class="strat-card-header" style="color:' + color + '">' +
          '<span class="strat-card-name">' + (def.label || key) + '</span>' +
          (def.entry_tf ? '<span class="strat-card-tf">TF: ' + def.entry_tf + '</span>' : '') +
        '</div>' +
        '<div class="strat-card-desc">' + (def.description || "") + '</div>' +
        (stats ? '<div class="strat-card-stats">' +
          '<span class="strat-stat strat-stat-hit" title="Average L12M hit rate across all stocks">&#9654; ' + stats.hit_rate.toFixed(0) + '% hit</span>' +
          (stats.avg_pnl != null ? '<span class="strat-stat strat-stat-pnl" title="Average L12M P&L per trade">&#10006; avg ' + (stats.avg_pnl > 0 ? "+" : "") + stats.avg_pnl.toFixed(1) + '%</span>' : '') +
          '<span class="strat-stat-note">(n=' + stats.n + ' stocks)</span>' +
        '</div>' : '') +
        '<div class="strat-card-section">C3 Entry: <span class="strat-card-combo-badge strat-c3">' +
          c3kpis.map(function(k, i) { return k + ' ' + polIcon(c3pols[i]); }).join(' &middot; ') +
        '</span></div>' +
        (c4kpis.length ? '<div class="strat-card-section">C4 Scale 1.5×: <span class="strat-card-combo-badge strat-c4">' +
          c4kpis.map(function(k, i) { return k + ' ' + polIcon(c4pols[i]); }).join(' &middot; ') +
        '</span></div>' : '') +
        '<div class="strat-card-gates">' +
          (gates.sma20_gt_sma200 ? '<span class="gate-chip gate-on" title="SMA20 > SMA200 required">SMA20>200</span>' : '<span class="gate-chip gate-off">SMA20>200</span>') +
          (gates.volume_spike ? '<span class="gate-chip gate-on" title="Volume spike ≥1.5× MA20 required">Vol spike</span>' : '<span class="gate-chip gate-off">Vol spike</span>') +
          (gates.sr_break ? '<span class="gate-chip gate-on" title="S/R breakout within 10 bars required">SR Break</span>' : '<span class="gate-chip gate-off">SR Break</span>') +
        '</div>';

      wrap.appendChild(card);
    });
  }

  // ── Scan Pass/Fail Stats Bar ──────────────────────────────────────────────
  function _renderScanPassStats(logData) {
    var el = document.getElementById("scanPassStats");
    if (!el) return;
    if (!logData || !logData.length) { el.innerHTML = ""; return; }
    var latest = logData[logData.length - 1];
    var raw = latest.raw_passed;
    var filtered = latest.filtered_open;
    var total = latest.total;
    var ts = latest.ts ? latest.ts.replace("T", " ").replace("Z", " UTC") : "";
    if (typeof raw !== "number") { el.innerHTML = ""; return; }
    var added = latest.added ? latest.added.length : 0;
    var removed = latest.removed ? latest.removed.length : 0;
    el.innerHTML =
      '<span class="scan-stat-pill scan-stat-pass">&#10003; ' + raw + ' passed gate</span>' +
      '<span class="scan-stat-pill scan-stat-filtered">&#128683; ' + filtered + ' filtered (open pos)</span>' +
      '<span class="scan-stat-pill scan-stat-total">&#8853; ' + total + ' in list</span>' +
      (added ? '<span class="scan-stat-pill scan-stat-added">+' + added + ' added</span>' : '') +
      (removed ? '<span class="scan-stat-pill scan-stat-removed">&#8722;' + removed + ' removed</span>' : '') +
      (ts ? '<span class="scan-stat-ts">' + ts + '</span>' : '');
  }

  // ── Download Health (persistent, from scan_download_debug.json) ───────────
  function _renderDownloadHealth(data) {
    var el = document.getElementById("scanDownloadHealth");
    if (!el) return;
    if (!data || !data.universe_total) { el.innerHTML = ""; return; }

    var total   = data.universe_total;
    var ok      = data.downloaded_ok;
    var fail    = data.downloaded_fail;
    var failed  = data.failed_tickers || [];
    var tf      = data.tf || "";
    var ts      = data.ts ? data.ts.replace("T", " ").replace("Z", " UTC") : "";
    var pct     = total > 0 ? Math.round(ok / total * 100) : 0;
    var barColor = fail === 0 ? "var(--candle-up)" : fail < 100 ? "var(--warning)" : "var(--danger)";

    var html =
      '<div class="scan-dl-health">' +
        '<div class="scan-dl-summary">' +
          '<span class="scan-dl-label">Universe</span>' +
          '<span class="scan-dl-value">' + total.toLocaleString() + '</span>' +
          '<span class="scan-dl-sep">·</span>' +
          '<span class="scan-dl-label">Downloaded</span>' +
          '<span class="scan-dl-value" style="color:var(--candle-up)">' + ok.toLocaleString() + '</span>' +
          (fail > 0
            ? '<span class="scan-dl-sep">·</span>' +
              '<span class="scan-dl-label">Failed</span>' +
              '<span class="scan-dl-value" style="color:var(--warning)">' + fail + '</span>'
            : '') +
          '<span class="scan-dl-sep">·</span>' +
          '<span class="scan-dl-bar-wrap" title="' + pct + '% downloaded">' +
            '<span class="scan-dl-bar-fill" style="width:' + pct + '%;background:' + barColor + '"></span>' +
          '</span>' +
          '<span class="scan-dl-pct">' + pct + '%</span>' +
          (tf ? '<span class="scan-dl-tf scan-stat-pill">' + tf + '</span>' : '') +
          (ts ? '<span class="scan-stat-ts">' + ts + '</span>' : '') +
        '</div>';

    if (failed.length) {
      var toggleId = "scanDlFailToggle";
      var listId   = "scanDlFailList";
      html +=
        '<div class="scan-dl-fail-row">' +
          '<button class="scan-dl-fail-toggle" id="' + toggleId + '" onclick="' +
            'var l=document.getElementById(\'' + listId + '\');' +
            'var b=document.getElementById(\'' + toggleId + '\');' +
            'var open=l.style.display!==\'none\';' +
            'l.style.display=open?\'none\':\'flex\';' +
            'b.textContent=(open?\'&#9658;\':\'&#9660;\') + \' ' + failed.length + ' failed tickers\';' +
          '">' +
          '&#9658; ' + failed.length + ' failed tickers' +
          '</button>' +
        '</div>' +
        '<div id="' + listId + '" class="scan-dl-fail-list" style="display:none">' +
          failed.map(function(t) {
            return '<span class="scan-dl-fail-ticker">' + t + '</span>';
          }).join("") +
        '</div>';
    }

    html += '</div>';
    el.innerHTML = html;
  }

  function _fetchDownloadDebug(cb) {
    fetch(_BASE + "/api/scan-download-debug", { cache: "no-store" })
      .then(function(r) { return r.ok ? r.json() : {}; })
      .then(cb)
      .catch(function() { cb({}); });
  }

  // ── Section 5: Scan History (item 9) ─────────────────────────────────────
  function _renderScanHistory(logData) {
    var wrap = document.getElementById("scanHistory");
    if (!wrap) return;
    wrap.innerHTML = "";

    if (!logData || !logData.length) {
      wrap.innerHTML = '<div class="scan-empty">No scan history yet. Run a scan to start tracking.</div>';
      return;
    }

    // Group entries by ts+tf (one scan run can have multiple strategy entries)
    var runs = [];
    var byTs = {};
    logData.forEach(function(e) {
      var key = e.ts + "|" + e.tf;
      if (!byTs[key]) { byTs[key] = { ts: e.ts, tf: e.tf, strategies: {} }; runs.push(byTs[key]); }
      byTs[key].strategies[e.strategy] = { added: e.added || [], removed: e.removed || [], total: e.total || 0 };
    });

    // Sort newest first, show last 50 runs
    runs.sort(function(a, b) { return b.ts.localeCompare(a.ts); });
    runs = runs.slice(0, 50);

    var table = document.createElement("table");
    table.className = "scan-table";
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    ["Date / Time", "TF", "Strategies", "Added", "Removed", "Total"].forEach(function(h) {
      var th = document.createElement("th"); th.textContent = h; trh.appendChild(th);
    });
    thead.appendChild(trh); table.appendChild(thead);

    var tbody = document.createElement("tbody");
    runs.forEach(function(run) {
      var tr = document.createElement("tr");
      var dateStr = run.ts.replace("T", " ").replace("Z", "").replace("+00:00", "") + " UTC";
      var stratKeys = Object.keys(run.strategies);
      var totalAdded = stratKeys.reduce(function(n, k) { return n + run.strategies[k].added.length; }, 0);
      var totalRemoved = stratKeys.reduce(function(n, k) { return n + run.strategies[k].removed.length; }, 0);
      var totalSignals = stratKeys.reduce(function(n, k) { return n + run.strategies[k].total; }, 0);

      var stratHtml = stratKeys.map(function(k) {
        var s = run.strategies[k];
        var col = _stratColor(k);
        return '<span class="scan-strat-badge" style="border-color:' + col + ';color:' + col + '">' + _stratLabel(k) + ': ' + s.total + '</span>';
      }).join(" ");

      var addedTickers = stratKeys.reduce(function(arr, k) { return arr.concat(run.strategies[k].added); }, []);
      var removedTickers = stratKeys.reduce(function(arr, k) { return arr.concat(run.strategies[k].removed); }, []);

      [dateStr, run.tf, stratHtml,
       totalAdded ? '<span style="color:var(--candle-up);font-weight:700">+' + totalAdded + '</span>' + (addedTickers.length ? ' <span title="' + addedTickers.join(", ") + '" style="font-size:10px;color:var(--muted);cursor:default">(' + addedTickers.slice(0,3).join(", ") + (addedTickers.length > 3 ? "…" : "") + ")</span>" : "") : '<span style="color:var(--muted)">0</span>',
       totalRemoved ? '<span style="color:var(--candle-down);font-weight:700">-' + totalRemoved + '</span>' + (removedTickers.length ? ' <span title="' + removedTickers.join(", ") + '" style="font-size:10px;color:var(--muted);cursor:default">(' + removedTickers.slice(0,3).join(", ") + (removedTickers.length > 3 ? "…" : "") + ")</span>" : "") : '<span style="color:var(--muted)">0</span>',
       totalSignals
      ].forEach(function(val, i) {
        var td = document.createElement("td");
        if (i === 2) td.innerHTML = val;
        else if (i === 3 || i === 4) td.innerHTML = val;
        else td.textContent = val;
        if (i === 0) { td.style.fontSize = "11px"; td.style.color = "var(--muted)"; }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
  }

  // ── Fetch scan log and render history ────────────────────────────────────
  function _loadScanLog(cb) {
    if (_scanLogLoaded && _scanLogData) { cb(_scanLogData); return; }
    fetch(_BASE + "/api/scan-log", { cache: "no-store" })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(raw) {
        _scanLogData = (raw && raw.data) ? raw.data : [];
        _scanLogLoaded = true;
        cb(_scanLogData);
      })
      .catch(function() { cb([]); });
  }

  // ── Main entry point ──────────────────────────────────────────────────────
  function buildScanPage() {
    // Sync active TF pill with _scanTf
    document.querySelectorAll(".scan-tf-pill").forEach(function(btn) {
      btn.classList.toggle("active", btn.dataset.tf === _scanTf);
    });

    _renderNewSignals();
    _renderPositionsAtRisk();
    _renderPreSignals();
    _renderStrategyCards();

    // Reload scan log + download debug each time page opens
    _scanLogLoaded = false;
    _loadScanLog(function(entries) {
      _renderScanPassStats(entries);
      _renderScanHistory(entries);
    });
    _fetchDownloadDebug(_renderDownloadHealth);
  }

  // Wire TF pills and export on first load
  (function _init() {
    _initTfPills();
    _initExportAll();
  })();

  D.buildScanPage = buildScanPage;

})(window.Dashboard = window.Dashboard || {});
