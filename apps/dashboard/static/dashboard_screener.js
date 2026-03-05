/* dashboard_screener.js — Screener table rendering, filtering, sorting, search */
(function(D) {
  "use strict";
  if (!D) return;
  function buildScreener() {
    var screenerFilter = (typeof _savedScreenerFilter !== "undefined") ? _savedScreenerFilter : "all";
    var wrap = DOM.screener;
    if (!wrap) return;
    wrap.innerHTML = "";
    var rows = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.rows_by_tf && SCREENER.rows_by_tf[currentTF]) ? SCREENER.rows_by_tf[currentTF].slice() : [];
    var filter = (document.getElementById("screenerSearch") && document.getElementById("screenerSearch").value || "").trim().toUpperCase();
    var allowed = getAllowedSymbolsSet();
    var rowsAllowed = rows.filter(function(r) { return allowed.has(((r && r.symbol) ? String(r.symbol) : "").toUpperCase()); });
    var filtered = filter ? rowsAllowed.filter(function(r) { var s = (r.symbol || "").toUpperCase(), n = (r.name || "").toUpperCase(); return s.indexOf(filter) >= 0 || n.indexOf(filter) >= 0; }) : rowsAllowed;
    if (screenerFilter === "bull") filtered = filtered.filter(function(r) { return (typeof r.trend_score === "number") && r.trend_score > 0; });
    else if (screenerFilter === "bear") filtered = filtered.filter(function(r) { return (typeof r.trend_score === "number") && r.trend_score < 0; });
    else if (screenerFilter === "strong") filtered = filtered.filter(function(r) { return (typeof r.trend_score === "number") && Math.abs(r.trend_score) >= 5; });
    else if (screenerFilter === "combo") filtered = filtered.filter(function(r) { return r.combo_3 || r.combo_4; });
    else if (screenerFilter === "new_combo") filtered = filtered.filter(function(r) { return r.combo_3_new || r.combo_4_new; });
    else if (screenerFilter === "buy") filtered = filtered.filter(function(r) { return (r.recommendation || "").toLowerCase() === "buy" || (r.recommendation || "").toLowerCase() === "strong_buy"; });
    else if (screenerFilter === "improving") filtered = filtered.filter(function(r) { return (typeof r.trend_delta === "number") && r.trend_delta > 0; });
    else if (screenerFilter === "recent_combo") filtered = filtered.filter(function(r) { return typeof r.last_combo_bars === "number" && r.last_combo_bars <= 3; });
    else if (screenerFilter === "active_position") filtered = filtered.filter(function(r) { var a = r.signal_action || ""; return a.indexOf("ENTRY") === 0 || a.indexOf("SCALE") === 0 || a === "HOLD"; });
    else if (screenerFilter === "entry_signal") filtered = filtered.filter(function(r) { var a = r.signal_action || ""; return a.indexOf("ENTRY") === 0 || a.indexOf("SCALE") === 0; });
    else if (screenerFilter === "strat_active") filtered = filtered.filter(function(r) { var ss = r.strat_statuses || {}; for (var sk in ss) { var sa = ss[sk].signal_action || ""; if (sa.indexOf("ENTRY") === 0 || sa === "HOLD") return true; } return false; });
    else if (screenerFilter === "strat_dip") filtered = filtered.filter(function(r) { var ss = r.strat_statuses || {}; var s = ss.dip_buy; return s && (s.signal_action || "").indexOf("ENTRY") === 0; });
    else if (screenerFilter === "strat_swing") filtered = filtered.filter(function(r) { var ss = r.strat_statuses || {}; var s = ss.swing; return s && ((s.signal_action || "").indexOf("ENTRY") === 0 || s.signal_action === "HOLD"); });
    else if (screenerFilter === "strat_trend") filtered = filtered.filter(function(r) { var ss = r.strat_statuses || {}; var s = ss.trend; return s && ((s.signal_action || "").indexOf("ENTRY") === 0 || s.signal_action === "HOLD"); });

    var sortKey = wrap.dataset.sortKey || _savedScreenerSortKey;
    var sortDir = wrap.dataset.sortDir || _savedScreenerSortDir;
    var _sortKeyMap = {
      "_ticker": function(r) { return r.symbol || ""; },
      "_conviction": function(r) { var ts = r.trend_score; var mx = (typeof MAX_TREND_SCORE === "number" && MAX_TREND_SCORE > 0) ? MAX_TREND_SCORE : 28.2; return typeof ts === "number" ? ts / mx : -9999; },
      "_last_combo": function(r) { var a = r.signal_action || ""; var ip = a.indexOf("ENTRY") === 0 || a.indexOf("SCALE") === 0 || a === "HOLD"; if (!ip) return -1; return (typeof r.last_combo_bars === "number") ? (10000 - r.last_combo_bars) : -1; },
      "_recommendation": function(r) { var m = {"strong_buy": 5, "buy": 4, "hold": 3, "sell": 2, "strong_sell": 1}; return m[(r.recommendation || "").toLowerCase()] || 0; },
      "_action_tf": function(r) { var _cs = (typeof window.currentStrategy === "string") ? window.currentStrategy : "v6"; var _ssd = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS && STRATEGY_SETUPS.setups) ? STRATEGY_SETUPS.setups[_cs] : null; var _isPol = _ssd && _ssd.entry_type === "polarity_combo"; var sym = String(r.symbol || "").toUpperCase(); var sc = 0; var allTFs = typeof TIMEFRAMES !== "undefined" ? TIMEFRAMES : []; var n = allTFs.length; allTFs.forEach(function(tfk, tfi) { var rec = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[sym] && SCREENER.by_symbol[sym][tfk]) ? SCREENER.by_symbol[sym][tfk] : null; if (!rec) return; var a; if (_isPol) { var _ss = rec.strat_statuses || {}; var _si = _ss[_cs]; a = _si ? (_si.signal_action || "") : ""; } else { a = rec.signal_action || ""; } if (a === "ENTRY 1.5x" || a.indexOf("SCALE") === 0) sc += (n - tfi) * 1000 + 500; else if (a.indexOf("ENTRY") === 0) sc += (n - tfi) * 1000 + 400; else if (a === "HOLD") sc += (n - tfi) * 1000 + 200; else if (a.indexOf("EXIT") === 0) sc += (n - tfi) * 1000 + 100; }); return sc; },
      "_strat_badges": function(r) { var ss = r.strat_statuses || {}; var sc = 0; var prio = {trend: 3000, swing: 2000, dip_buy: 1000}; for (var sk in ss) { var sa = ss[sk].signal_action || ""; if (sa.indexOf("ENTRY") === 0) sc += (prio[sk] || 0) + 500; else if (sa === "HOLD") sc += (prio[sk] || 0) + 300; } return sc; },
      "_price": function(r) { return (typeof r.last_close === "number") ? r.last_close : -1; },
    };
    var _getSortVal = _sortKeyMap[sortKey] || function(r) { return r[sortKey] != null ? r[sortKey] : ""; };
    filtered.sort(function(a, b) {
      var av = _getSortVal(a);
      var bv = _getSortVal(b);
      var na = (typeof av === "number");
      var nb = (typeof bv === "number");
      var cmp = 0;
      if (na && nb) cmp = av - bv;
      else cmp = String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });

    var comboNote = document.createElement("div");
    comboNote.style.cssText = "font-size:11px;color:var(--muted);padding:6px 10px;line-height:1.5;border:1px solid var(--border);border-radius:6px;margin-bottom:8px;background:var(--card-bg);";
    comboNote.innerHTML = "<b style=\"color:var(--fg);\">" + filtered.length + " symbols</b>";
    wrap.appendChild(comboNote);

    if (!filtered.length) {
      var empty = document.createElement("div");
      empty.style.cssText = "padding:32px;text-align:center;color:var(--muted);font-size:13px;";
      empty.textContent = currentGroup && currentGroup !== "all" ? "No symbols in this group. Select a different group or run a new scan." : "No symbols match your filters.";
      wrap.appendChild(empty);
      return;
    }

    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var COL_SCALE = 1.25; // +25% requested
    function scalePct(pct) {
      var n = parseFloat(String(pct).replace("%", ""));
      if (!isFinite(n)) return pct;
      return (n * COL_SCALE).toFixed(2).replace(/\.00$/, "") + "%";
    }
    var hdr = [
      ["symbol", "Name", "10%"],
      ["_ticker", "Ticker", "5%"],
      ["market_cap", "Mkt Cap", "5%"],
      ["_price", "Price", "5%"],
      ["_recommendation", "Analysts", "4%"],
      ["_conv10", "TrendScore", "9%"],
      ["_confluence", "Traffic Light", "9%"],
      ["_action_tf", (function() { var _cs = (typeof window.currentStrategy === "string") ? window.currentStrategy : "v6"; var _ssd = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS && STRATEGY_SETUPS.setups) ? STRATEGY_SETUPS.setups[_cs] : null; return (_ssd && _ssd.entry_type === "polarity_combo") ? (_ssd.label || _cs) : "Action"; })(), "9%"],
      ["_strat_badges", "Strategy", "9%"],
      ["_vs_bench", "TrendScore vs Bench", "9%"],
      ["pe_vs_sector", "P/E vs Sec", "5%"],
      ["_move_group", "Group", "5%"],
      ["_delete", "", "3%"],
    ];
    var colgroup = document.createElement("colgroup");
    hdr.forEach(function(item) {
      var col = document.createElement("col");
      col.style.width = scalePct(item[2]);
      colgroup.appendChild(col);
    });
    table.appendChild(colgroup);
    var _sortable = new Set(["symbol", "_ticker", "market_cap", "_price", "_action_tf", "_strat_badges", "_recommendation", "pe_vs_sector"]);
    var trh = document.createElement("tr");
    var _centered = new Set(["_conv10", "_confluence", "_action_tf", "_strat_badges", "_vs_bench", "pe_vs_sector", "_recommendation"]);
    hdr.forEach(function(item) {
      var k = item[0], label = item[1];
      var th = document.createElement("th");
      th.textContent = label;
      if (_centered.has(k)) th.style.textAlign = "center";
      if (_sortable.has(k)) {
        var sortK = k.indexOf("_") === 0 ? k : k;
        th.addEventListener("click", function() {
          var curK = wrap.dataset.sortKey || "_action_tf";
          var curD = wrap.dataset.sortDir || "desc";
          wrap.dataset.sortKey = sortK;
          wrap.dataset.sortDir = (curK === sortK) ? (curD === "asc" ? "desc" : "asc") : "desc";
          _savedScreenerSortKey = wrap.dataset.sortKey;
          _savedScreenerSortDir = wrap.dataset.sortDir;
          saveState({ screenerSortKey: _savedScreenerSortKey, screenerSortDir: _savedScreenerSortDir });
          D.buildScreener();
        });
        th.style.cursor = "pointer";
      }
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    filtered.forEach(function(r) {
      var tr = document.createElement("tr");
      hdr.forEach(function(item) {
        var k = item[0];
        var td = document.createElement("td");
        if (_centered.has(k)) td.style.textAlign = "center";
        if (k === "symbol") {
          td.style.maxWidth = "175px";
          td.style.overflow = "hidden";
          td.style.textOverflow = "ellipsis";
          var fullName = r.name || r.symbol || "";
          td.title = r.symbol + (r.name ? " — " + r.name : "");
          var a = document.createElement("span");
          a.className = "link";
          a.textContent = fullName || r.symbol || "";
          a.addEventListener("click", function() {
            currentSymbol = String(r.symbol || "").toUpperCase();
            saveState({ symbol: currentSymbol });
            buildSymbolList();
            switchTab("strategy");
            renderChart();
          });
          td.appendChild(a);
        } else if (k === "_ticker") {
          td.textContent = r.symbol || "";
          td.style.fontSize = "11px";
          td.style.fontWeight = "700";
          td.style.color = "var(--fg)";
          td.style.letterSpacing = "0.3px";
          var secParts = [r.sector, r.industry].filter(Boolean);
          td.title = secParts.length ? secParts.join(" — ") : "";
        } else if (k === "_vs_bench") {
          var wrapVs = document.createElement("div");
          wrapVs.style.cssText = "display:flex;gap:6px;justify-content:center;font-size:11px;font-weight:600;";
          var tooltipParts = [];
          var sDelta = r.sector_ts_delta;
          var sBench = r.sector_etf || "";
          if (sDelta != null && sBench) {
            var sVal = parseFloat(sDelta);
            var sSpan = document.createElement("span");
            sSpan.textContent = (sVal > 0 ? "+" : "") + sVal.toFixed(1);
            sSpan.style.color = sVal > 0 ? "var(--candle-up)" : sVal < 0 ? "var(--candle-down)" : "var(--muted)";
            var sLabel = document.createElement("span");
            sLabel.textContent = "S";
            sLabel.style.cssText = "color:var(--muted);font-size:9px;margin-right:1px;";
            var sGroup = document.createElement("span");
            sGroup.appendChild(sLabel);
            sGroup.appendChild(sSpan);
            wrapVs.appendChild(sGroup);
            var benchName = (typeof SYMBOL_DISPLAY !== "undefined" && SYMBOL_DISPLAY && SYMBOL_DISPLAY[sBench]) || sBench;
            tooltipParts.push("Sector: " + (sVal > 0 ? "+" : "") + sVal.toFixed(1) + " vs " + benchName);
          }
          var mDelta = r.market_ts_delta;
          var mIdx = r.market_index || "";
          if (mDelta != null && mIdx) {
            var mVal = parseFloat(mDelta);
            var mSpan = document.createElement("span");
            mSpan.textContent = (mVal > 0 ? "+" : "") + mVal.toFixed(1);
            mSpan.style.color = mVal > 0 ? "var(--candle-up)" : mVal < 0 ? "var(--candle-down)" : "var(--muted)";
            var mLabel = document.createElement("span");
            mLabel.textContent = "M";
            mLabel.style.cssText = "color:var(--muted);font-size:9px;margin-right:1px;";
            var mGroup = document.createElement("span");
            mGroup.appendChild(mLabel);
            mGroup.appendChild(mSpan);
            wrapVs.appendChild(mGroup);
            var mIdxName = (typeof SYMBOL_DISPLAY !== "undefined" && SYMBOL_DISPLAY && SYMBOL_DISPLAY[mIdx]) || mIdx;
            tooltipParts.push("Market: " + (mVal > 0 ? "+" : "") + mVal.toFixed(1) + " vs " + mIdxName);
          }
          if (wrapVs.childNodes.length) { td.appendChild(wrapVs); td.title = tooltipParts.join("\n"); }
        } else if (k === "_conv10") {
          var vals = r.conv10 || [];
          var wrap10 = document.createElement("div");
          wrap10.style.cssText = "display:flex;align-items:center;justify-content:center;gap:4px;";
          if (vals.length) {
            var w = 70, h = 22, bw = Math.max(2, Math.floor(w / vals.length) - 1);
            var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            svg.setAttribute("width", w);
            svg.setAttribute("height", h);
            svg.style.cssText = "vertical-align:middle;flex-shrink:0;";
            var mid = h / 2;
            var zl = document.createElementNS("http://www.w3.org/2000/svg", "line");
            zl.setAttribute("x1", 0); zl.setAttribute("x2", w);
            zl.setAttribute("y1", mid); zl.setAttribute("y2", mid);
            zl.setAttribute("stroke", "var(--border-strong)"); zl.setAttribute("stroke-width", "0.5");
            svg.appendChild(zl);
            vals.forEach(function(v, i) {
              var bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
              var bh = Math.abs(v) * mid;
              var bx = i * (bw + 1);
              var by = v >= 0 ? mid - bh : mid;
              bar.setAttribute("x", bx);
              bar.setAttribute("y", by);
              bar.setAttribute("width", bw);
              bar.setAttribute("height", Math.max(bh, 0.5));
              bar.setAttribute("fill", v >= 0 ? "var(--candle-up)" : "var(--candle-down)");
              bar.setAttribute("rx", "1");
              svg.appendChild(bar);
            });
            wrap10.appendChild(svg);
          }
          var ts = r.trend_score;
          var maxTS = (typeof MAX_TREND_SCORE === "number" && MAX_TREND_SCORE > 0) ? MAX_TREND_SCORE : 28.2;
          var convPct = (typeof ts === "number") ? Math.round((ts / maxTS) * 100) : null;
          if (convPct !== null) {
            var pctSpan = document.createElement("span");
            pctSpan.style.cssText = "font-size:10px;font-weight:600;white-space:nowrap;";
            pctSpan.textContent = (convPct > 0 ? "+" : "") + convPct + "%";
            pctSpan.style.color = convPct >= 0 ? "var(--candle-up)" : "var(--candle-down)";
            wrap10.appendChild(pctSpan);
          }
          var td3 = r.trend_delta;
          var avg = vals.length ? vals.reduce(function(a, b) { return a + b; }, 0) / vals.length : 0;
          var tsStr = typeof ts === "number" ? "TrendScore: " + (ts > 0 ? "+" : "") + ts.toFixed(1) + " / " + maxTS.toFixed(1) + "\n" : "";
          var deltaStr = typeof td3 === "number" ? "Trend \u0394(3): " + (td3 > 0 ? "+" : "") + td3.toFixed(1) + "\n" : "";
          var convStr = convPct !== null ? "Conviction: " + (convPct > 0 ? "+" : "") + convPct + "%\n" : "";
          td.title = tsStr + convStr + deltaStr + "13-bar avg: " + (avg > 0 ? "+" : "") + avg.toFixed(2);
          td.appendChild(wrap10);
        } else if (k === "_action_tf") {
          var symAt = String(r.symbol || "").toUpperCase();
          var atWrap = document.createElement("div");
          atWrap.style.cssText = "display:flex;gap:3px;align-items:center;justify-content:center;";
          var _curStrat = (typeof window.currentStrategy === "string") ? window.currentStrategy : "v6";
          var _stratSetupsDef = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS && STRATEGY_SETUPS.setups) ? STRATEGY_SETUPS.setups[_curStrat] : null;
          var _isPolarity = _stratSetupsDef && _stratSetupsDef.entry_type === "polarity_combo";
          var _stratColorHex = _isPolarity ? (_stratSetupsDef.color || "#facc15") : null;
          (typeof TIMEFRAMES !== "undefined" ? TIMEFRAMES : []).forEach(function(tfk) {
            var rec = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[symAt] && SCREENER.by_symbol[symAt][tfk]) ? SCREENER.by_symbol[symAt][tfk] : null;
            var act, cb;
            if (_isPolarity && rec) {
              var _ss = rec.strat_statuses || {};
              var _sInfo = _ss[_curStrat];
              act = _sInfo ? (_sInfo.signal_action || "FLAT") : "FLAT";
              cb = _sInfo ? _sInfo.bars_held : null;
            } else {
              act = rec ? (rec.signal_action || "FLAT") : "FLAT";
              cb = rec ? (rec.combo_bars != null ? rec.combo_bars : rec.bars_held) : null;
            }
            var cell = document.createElement("span");
            cell.style.cssText = "display:inline-flex;flex-direction:column;align-items:center;";
            var lbl = document.createElement("span");
            lbl.style.cssText = "font-size:9px;color:var(--muted);";
            lbl.textContent = tfk.replace("1", "");
            cell.appendChild(lbl);
            var badge = document.createElement("span");
            badge.style.cssText = "font-size:9px;font-weight:700;padding:1px 4px;border-radius:3px;";
            var hoverText = act;
            if (_isPolarity) {
              if (act.indexOf("ENTRY") === 0) {
                badge.style.background = "rgba(" + parseInt(_stratColorHex.slice(1,3),16) + "," + parseInt(_stratColorHex.slice(3,5),16) + "," + parseInt(_stratColorHex.slice(5,7),16) + ",0.18)";
                badge.style.color = _stratColorHex; badge.textContent = "E";
                hoverText = _curStrat.replace("_"," ") + " Entry" + (cb != null ? " " + cb + "b" : "");
              } else if (act === "HOLD") {
                badge.style.background = "rgba(" + parseInt(_stratColorHex.slice(1,3),16) + "," + parseInt(_stratColorHex.slice(3,5),16) + "," + parseInt(_stratColorHex.slice(5,7),16) + ",0.10)";
                badge.style.color = _stratColorHex; badge.textContent = "H";
                hoverText = _curStrat.replace("_"," ") + " Hold" + (cb != null ? " " + cb + "b" : "");
              } else {
                badge.style.color = "var(--muted)"; badge.textContent = "—";
                hoverText = _curStrat.replace("_"," ") + " Flat";
              }
            } else {
              if (act === "ENTRY 1.5x" || act.indexOf("SCALE") === 0) {
                badge.style.background = "var(--combo-c4-bg)"; badge.style.color = "var(--combo-c4-fg)"; badge.textContent = "E1.5";
                hoverText = (act.indexOf("SCALE") === 0 ? "Scale 1.5x" : "Entry 1.5x") + (cb != null ? " " + cb + "b" : "");
              } else if (act.indexOf("ENTRY") === 0) {
                badge.style.background = "var(--combo-c3-bg)"; badge.style.color = "var(--combo-c3-fg)"; badge.textContent = "E1";
                hoverText = "Entry 1x" + (cb != null ? " " + cb + "b" : "");
              } else if (act === "HOLD") {
                badge.style.background = "rgba(74,130,184,0.18)"; badge.style.color = "var(--info)"; badge.textContent = "HLD";
                hoverText = "Hold" + (cb != null ? " " + cb + "b" : "");
              } else if (act.indexOf("EXIT") === 0 || (act === "FLAT" && rec && rec.last_exit_bars_ago != null && rec.last_exit_bars_ago <= 2)) {
                badge.style.background = "var(--trade-loss-bg)"; badge.style.color = "var(--danger)"; badge.textContent = "EXT";
                var eb = rec ? rec.last_exit_bars_ago : null;
                hoverText = "Exit" + (eb != null ? " " + eb + "b" : "");
              } else {
                badge.style.color = "var(--muted)"; badge.textContent = "—";
                hoverText = "Flat";
              }
            }
            badge.title = tfk + ": " + hoverText;
            cell.appendChild(badge);
            atWrap.appendChild(cell);
          });
          td.appendChild(atWrap);
        } else if (k === "_strat_badges") {
          var sbWrap = document.createElement("div");
          sbWrap.style.cssText = "display:flex;gap:3px;align-items:center;justify-content:center;";
          var _stratDefs = [
            {key: "trend", label: "T", color: "#c084fc", bgAlpha: "0.15"},
            {key: "swing", label: "S", color: "#60a5fa", bgAlpha: "0.15"},
            {key: "dip_buy", label: "D", color: "#facc15", bgAlpha: "0.18"}
          ];
          var ss = r.strat_statuses || {};
          _stratDefs.forEach(function(sd) {
            var sInfo = ss[sd.key];
            var sAct = sInfo ? (sInfo.signal_action || "FLAT") : "FLAT";
            var sbBadge = document.createElement("span");
            sbBadge.style.cssText = "font-size:9px;font-weight:700;padding:1px 4px;border-radius:3px;min-width:16px;text-align:center;";
            if (sAct.indexOf("ENTRY") === 0) {
              sbBadge.style.background = sd.color.replace(")", "," + sd.bgAlpha + ")").replace("rgb", "rgba").replace("#", "");
              sbBadge.style.background = "rgba(" + parseInt(sd.color.slice(1,3),16) + "," + parseInt(sd.color.slice(3,5),16) + "," + parseInt(sd.color.slice(5,7),16) + "," + sd.bgAlpha + ")";
              sbBadge.style.color = sd.color;
              sbBadge.textContent = sd.label;
              sbBadge.title = sd.key.replace("_"," ") + ": " + sAct + (sInfo.bars_held != null ? " " + sInfo.bars_held + "b" : "");
            } else if (sAct === "HOLD") {
              sbBadge.style.background = "rgba(" + parseInt(sd.color.slice(1,3),16) + "," + parseInt(sd.color.slice(3,5),16) + "," + parseInt(sd.color.slice(5,7),16) + ",0.08)";
              sbBadge.style.color = sd.color;
              sbBadge.style.opacity = "0.7";
              sbBadge.textContent = sd.label;
              sbBadge.title = sd.key.replace("_"," ") + ": Hold" + (sInfo.bars_held != null ? " " + sInfo.bars_held + "b" : "");
            } else {
              sbBadge.style.color = "var(--muted)";
              sbBadge.style.opacity = "0.3";
              sbBadge.textContent = sd.label;
              sbBadge.title = sd.key.replace("_"," ") + ": Flat";
            }
            sbWrap.appendChild(sbBadge);
          });
          td.appendChild(sbWrap);
        } else if (k === "_confluence") {
          var symCf = String(r.symbol || "").toUpperCase();
          var tfConf = document.createElement("div");
          tfConf.className = "tf-conf";
          (typeof TIMEFRAMES !== "undefined" ? TIMEFRAMES : []).forEach(function(tf) {
            var col = document.createElement("div");
            col.className = "tf-col";
            var lbl = document.createElement("div");
            lbl.className = "tf-lbl";
            lbl.textContent = tf.replace("1", "");
            col.appendChild(lbl);
            var dot = document.createElement("div");
            dot.className = "tf-dot";
            var rec = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[symCf] && SCREENER.by_symbol[symCf][tf]) ? SCREENER.by_symbol[symCf][tf] : null;
            var tsDot = rec ? rec.trend_score : null;
            if (typeof tsDot === "number" && tsDot > 0) dot.classList.add("td-bull");
            else if (typeof tsDot === "number" && tsDot < 0) dot.classList.add("td-bear");
            else dot.classList.add("td-neu");
            dot.title = tf + ": " + (tsDot != null ? tsDot : "N/A");
            col.appendChild(dot);
            tfConf.appendChild(col);
          });
          td.appendChild(tfConf);
        } else if (k === "_recommendation") {
          var rec = (r.recommendation || "").toLowerCase();
          if (rec && rec !== "none") {
            var badge = document.createElement("span");
            badge.style.cssText = "font-size:10px;padding:1px 6px;border-radius:999px;font-weight:700;white-space:nowrap;";
            if (rec === "buy" || rec === "strong_buy") {
              badge.style.background = "var(--trade-c4-bg)"; badge.style.color = "var(--success)";
              badge.textContent = rec === "strong_buy" ? "Strong Buy" : "Buy";
            } else if (rec === "sell" || rec === "strong_sell") {
              badge.style.background = "var(--trade-loss-bg)"; badge.style.color = "var(--danger)";
              badge.textContent = rec === "strong_sell" ? "Strong Sell" : "Sell";
            } else {
              badge.style.background = "var(--trade-c3-bg)"; badge.style.color = "var(--warning)";
              badge.textContent = rec.charAt(0).toUpperCase() + rec.slice(1).replace("_", " ");
            }
            td.appendChild(badge);
          }
        } else if (k === "market_cap") {
          var mc = r.market_cap;
          if (typeof mc === "number" && mc > 0) {
            var fmt;
            if (mc >= 1e12) fmt = (mc / 1e12).toFixed(1) + "T";
            else if (mc >= 1e9) fmt = (mc / 1e9).toFixed(1) + "B";
            else if (mc >= 1e6) fmt = (mc / 1e6).toFixed(0) + "M";
            else fmt = mc.toLocaleString();
            td.textContent = fmt;
            td.style.cssText = "font-size:11px;font-weight:700;color:var(--fg);letter-spacing:0.3px;";
            td.title = "$" + mc.toLocaleString();
          }
        } else if (k === "_price") {
          var close = r.last_close;
          if (typeof close === "number" && close > 0) {
            var symPr = String(r.symbol || "").toUpperCase();
            var displayPrice = (typeof _toEur === "function") ? _toEur(close, symPr) : close;
            var eurSuffix = (typeof _eurLabel === "function") ? _eurLabel(symPr) : "";
            var pWrap = document.createElement("div");
            pWrap.style.lineHeight = "1.2";
            var priceSpan = document.createElement("div");
            priceSpan.textContent = displayPrice.toFixed(2) + eurSuffix;
            priceSpan.style.cssText = "font-size:11px;font-weight:600;";
            pWrap.appendChild(priceSpan);
            var dp = r.delta_pct;
            if (typeof dp === "number") {
              var deltaRow = document.createElement("div");
              deltaRow.style.cssText = "font-size:9px;font-weight:700;";
              var tfLabel = currentTF ? currentTF.replace("1", "") : "";
              if (dp >= 0) { deltaRow.style.color = "var(--candle-up)"; deltaRow.textContent = "+" + dp.toFixed(1) + "% " + tfLabel; }
              else { deltaRow.style.color = "var(--candle-down)"; deltaRow.textContent = dp.toFixed(1) + "% " + tfLabel; }
              pWrap.appendChild(deltaRow);
            }
            td.appendChild(pWrap);
            var tipParts = ["Price: " + close.toFixed(2)];
            if (r.entry_price != null) tipParts.push("Entry: " + r.entry_price.toFixed(2));
            if (r.atr_stop != null) tipParts.push("ATR Stop: " + r.atr_stop.toFixed(2));
            if (close > 0 && r.atr_stop != null) { var pctRisk = ((close - r.atr_stop) / close * 100); tipParts.push("% Risk: " + pctRisk.toFixed(1) + "%"); }
            td.title = tipParts.join("\n");
          }
        } else if (k === "pe_vs_sector") {
          var pv = r.pe_vs_sector;
          if (typeof pv === "number") {
            var span = document.createElement("span");
            span.style.fontWeight = "600";
            span.style.fontSize = "11px";
            span.textContent = (pv > 0 ? "+" : "") + pv.toFixed(0) + "%";
            if (pv > 20) span.style.color = "var(--candle-down)";
            else if (pv < -20) span.style.color = "var(--candle-up)";
            else span.style.color = "var(--muted)";
            td.appendChild(span);
            var stockPe = r.trailing_pe;
            td.title = "Stock P/E: " + (stockPe ? stockPe.toFixed(1) : "N/A") + " vs sector ETF average";
          }
        } else if (k === "_move_group") {
          var symMg = String(r.symbol || "").toUpperCase();
          var curGroups = _findSymbolGroups(symMg);
          _pendingMoves.filter(function(m) { return m.symbol === symMg; }).forEach(function(m) {
            var fi = curGroups.indexOf(m.from);
            if (fi >= 0) curGroups.splice(fi, 1);
            if (m.to && curGroups.indexOf(m.to) < 0) curGroups.push(m.to);
          });
          var groupLabel = curGroups.length ? curGroups[0] : "—";
          var sel = document.createElement("select");
          sel.style.cssText = "font-size:10px;padding:2px 4px;border:1px solid var(--border);border-radius:4px;background:var(--panel);color:var(--fg);cursor:pointer;max-width:90px;";
          var current = document.createElement("option");
          current.value = "";
          current.textContent = _groupLabel(groupLabel);
          current.selected = true;
          sel.appendChild(current);
          GROUP_KEYS.filter(function(g) { return curGroups.indexOf(g) < 0; }).forEach(function(g) {
            var opt = document.createElement("option");
            opt.value = g;
            opt.textContent = "\u2192 " + _groupLabel(g);
            sel.appendChild(opt);
          });
          sel.addEventListener("change", function() {
            var target = sel.value;
            if (!target) return;
            var fromGroup = curGroups.length ? curGroups[0] : "unknown";
            _moveStock(symMg, fromGroup, target);
            sel.value = "";
          });
          td.appendChild(sel);
        } else if (k === "_delete") {
          var symDel = String(r.symbol || "").toUpperCase();
          var curGroupsDel = _findSymbolGroups(symDel);
          var btn = document.createElement("button");
          btn.textContent = "\u2715";
          btn.title = "Remove " + symDel + " from CSV & delete data";
          btn.style.cssText = "font-size:10px;padding:1px 5px;border:1px solid var(--danger);border-radius:4px;background:transparent;color:var(--danger);cursor:pointer;line-height:1;";
          btn.addEventListener("click", function() { _deleteStock(symDel, curGroupsDel.length ? curGroupsDel[0] : ""); });
          td.appendChild(btn);
        } else {
          td.textContent = (r[k] != null ? r[k] : "");
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
  }

  function exportCSV() {
    var rows = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.rows_by_tf && SCREENER.rows_by_tf[currentTF]) ? SCREENER.rows_by_tf[currentTF].slice() : [];
    var filter = (document.getElementById("screenerSearch") && document.getElementById("screenerSearch").value || "").trim().toUpperCase();
    var allowed = getAllowedSymbolsSet();
    var rowsAllowed = rows.filter(function(r) { return allowed.has(((r && r.symbol) ? String(r.symbol) : "").toUpperCase()); });
    var filtered = filter ? rowsAllowed.filter(function(r) {
      var s = (r.symbol || "").toUpperCase(), n = (r.name || "").toUpperCase();
      var sec = (r.sector || "").toUpperCase(), ind = (r.industry || "").toUpperCase(), g = (r.geo || "").toUpperCase();
      return s.indexOf(filter) >= 0 || n.indexOf(filter) >= 0 || sec.indexOf(filter) >= 0 || ind.indexOf(filter) >= 0 || g.indexOf(filter) >= 0;
    }) : rowsAllowed;
    var cols = ["symbol", "name", "tf", "sector", "market_cap", "trend_score", "trend_delta", "signal_action", "l12m_pnl", "l12m_trades", "l12m_hit_rate", "recommendation", "sector_ts_delta", "sector_etf", "market_ts_delta", "market_index", "combo_3", "combo_4", "last_combo_bars", "pe_vs_sector", "trailing_pe"];
    var lines = [cols.join(",")].concat(filtered.map(function(r) { return cols.map(function(c) { return JSON.stringify(r[c] != null ? r[c] : ""); }).join(","); }));
    var blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "screener_" + currentTF + ".csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function() { URL.revokeObjectURL(url); }, 2000);
  }

  D.buildScreener = buildScreener;
  D.exportCSV = exportCSV;

  D.initScreener = function() {
    var _initFilter = (typeof _savedScreenerFilter !== "undefined") ? _savedScreenerFilter : "all";
    var searchEl = document.getElementById("screenerSearch");
    var exportBtn = document.getElementById("btnExport");
    if (searchEl) searchEl.addEventListener("input", _debounce(function() { D.buildScreener(); }, 200));
    if (exportBtn) exportBtn.addEventListener("click", function() { D.exportCSV(); });
    document.querySelectorAll("#screenerFilters .btn").forEach(function(b) {
      if (b.dataset.filter === _initFilter) b.classList.add("active");
      b.addEventListener("click", function() {
        _savedScreenerFilter = b.dataset.filter || "all";
        saveState({ screenerFilter: _savedScreenerFilter });
        document.querySelectorAll("#screenerFilters .btn").forEach(function(x) { x.classList.toggle("active", x === b); });
        D.buildScreener();
      });
    });
  };
})(window.Dashboard = window.Dashboard || {});
