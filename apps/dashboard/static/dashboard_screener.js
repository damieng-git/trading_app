/* dashboard_screener.js — Screener table rendering, filtering, sorting, search */
/*
 * v2 — Screener UX improvements for decision-making:
 *
 * #1  Table split into sections: Exit · New Signals · Active Positions · Watchlist
 * #3  Strategy badges merged into Action column; priority order from config.json badge_prio.
 *     When multiple strategies fire on the same TF, the lowest badge_prio wins.
 *     badge_prio and badge_label are defined in strategy_setups — no JS changes needed
 *     when adding a new strategy.
 *     If no strategy is active, falls back to v6 combo logic.
 * #4  Inline entry price / ATR-stop / risk% in Price cell (not just tooltip)
 * #5  Analysts + P/E vs Sector removed; replaced by L12M backtest performance
 *     (P&L% + win rate) for the highest-priority active strategy, falling back to v6.
 * #6  TF conflict ⚠ badge on Traffic Light when trend direction disagrees across TFs
 * #7  Bars-held count shown inline on H badges (e.g. H8 = 8 bars in position)
 * #8  Filter bar condensed: All | In Position | New Signals | Improving | Combo
 *     + strategy sub-filters: Dip Buy | Swing | Trend
 * #9  "N new signals: X, Y, Z" banner above the table when combo_new fires
 * #10 Row background tinted by section (new=green, active=blue, exit=red)
 */
(function(D) {
  "use strict";
  if (!D) return;

  /* ── Strategy badge priority: built from config.json badge_prio / badge_label ─
   * Adding a new strategy = add badge_prio + badge_label to its strategy_setups
   * entry in config.json. No JS changes required.                               */
  var STRAT_PRIO = (function() {
    var _setups = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS.setups) ? STRATEGY_SETUPS.setups : {};
    return Object.keys(_setups)
      .filter(function(k) { return _setups[k].badge_prio != null; })
      .sort(function(a, b) { return (_setups[a].badge_prio || 99) - (_setups[b].badge_prio || 99); })
      .map(function(k) { return {key: k, label: _setups[k].badge_label || k, color: _setups[k].color || "#888"}; });
  })();

  /* Returns the highest-priority polarity strategy with an active signal,
   * or null if none. Checks ENTRY, SCALE, and HOLD states. */
  // Returns the highest-priority strategy with an active signal (ENTRY/SCALE/HOLD)
  // or, if none active, the most recently exited strategy (≤2 bars ago).
  // This ensures all three views (screener badge, price cell, row section) share
  // the same strategy as the source of truth for entry/exit display.
  function _bestStrat(stratStatuses) {
    var ss = stratStatuses || {};
    var recentExit = null;
    for (var i = 0; i < STRAT_PRIO.length; i++) {
      var sp = STRAT_PRIO[i];
      var sInfo = ss[sp.key];
      if (!sInfo) continue;
      var sa = sInfo.signal_action || "FLAT";
      if (sa.indexOf("ENTRY") === 0 || sa.indexOf("SCALE") === 0 || sa === "HOLD") {
        return {key: sp.key, label: sp.label, color: sp.color, info: sInfo, signal_action: sa};
      }
      // Track the most recent exit across all strategies as fallback
      if (sa === "FLAT" && sInfo.last_exit_bars_ago != null && sInfo.last_exit_bars_ago <= 2) {
        if (!recentExit || sInfo.last_exit_bars_ago < recentExit.info.last_exit_bars_ago) {
          recentExit = {key: sp.key, label: sp.label, color: sp.color, info: sInfo, signal_action: sa};
        }
      }
    }
    return recentExit; // null if no active signal and no recent exit
  }

  /* Classifies a row into one of four sections based on signal state. */
  function _rowSection(r) {
    var v6act = r.signal_action || "";
    var ss = r.strat_statuses || {};

    /* EXIT: active exit signal, very recent v6 exit, or very recent polarity/stoof exit (≤2 bars ago) */
    if (v6act.indexOf("EXIT") === 0 || (r.last_exit_bars_ago != null && r.last_exit_bars_ago <= 2)) {
      return "exit";
    }
    for (var ei = 0; ei < STRAT_PRIO.length; ei++) {
      var eSI = ss[STRAT_PRIO[ei].key];
      if (eSI && eSI.last_exit_bars_ago != null && eSI.last_exit_bars_ago <= 2) { return "exit"; }
    }

    /* NEW SIGNAL: combo just appeared, or polarity strategy just entered (≤1 bar) */
    if (r.combo_3_new || r.combo_4_new) return "new_signal";
    for (var i = 0; i < STRAT_PRIO.length; i++) {
      var sInfo = ss[STRAT_PRIO[i].key];
      if (sInfo) {
        var sa = sInfo.signal_action || "";
        if (sa.indexOf("ENTRY") === 0 && (sInfo.bars_held == null || sInfo.bars_held <= 1)) {
          return "new_signal";
        }
      }
    }

    /* ACTIVE POSITION: holding via v6 or any polarity strategy */
    if (v6act.indexOf("ENTRY") === 0 || v6act.indexOf("SCALE") === 0 || v6act === "HOLD") return "active";
    for (var j = 0; j < STRAT_PRIO.length; j++) {
      var sInfo2 = ss[STRAT_PRIO[j].key];
      if (sInfo2) {
        var sa2 = sInfo2.signal_action || "";
        if (sa2.indexOf("ENTRY") === 0 || sa2 === "HOLD") return "active";
      }
    }

    return "watchlist";
  }

  /* Rgba helper from hex color string */
  function _hexRgba(hex, alpha) {
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  /* ── Main render function ─────────────────────────────────────────────────── */
  function buildScreener() {
    var screenerFilter = (typeof _savedScreenerFilter !== "undefined") ? _savedScreenerFilter : "all";
    var wrap = DOM.screener;
    if (!wrap) return;
    wrap.innerHTML = "";

    var rows = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.rows_by_tf && SCREENER.rows_by_tf[currentTF])
      ? SCREENER.rows_by_tf[currentTF].slice() : [];
    var filter = (document.getElementById("screenerSearch") && document.getElementById("screenerSearch").value || "").trim().toUpperCase();
    var allowed = getAllowedSymbolsSet();
    var rowsAllowed = rows.filter(function(r) { return allowed.has(((r && r.symbol) ? String(r.symbol) : "").toUpperCase()); });
    var filtered = filter
      ? rowsAllowed.filter(function(r) { var s = (r.symbol || "").toUpperCase(), n = (r.name || "").toUpperCase(); return s.indexOf(filter) >= 0 || n.indexOf(filter) >= 0; })
      : rowsAllowed;

    /* ── Filters ─────────────────────────────────────────────────────────── */
    if (screenerFilter === "bull")            filtered = filtered.filter(function(r) { return (typeof r.trend_score === "number") && r.trend_score > 0; });
    else if (screenerFilter === "bear")       filtered = filtered.filter(function(r) { return (typeof r.trend_score === "number") && r.trend_score < 0; });
    else if (screenerFilter === "strong")     filtered = filtered.filter(function(r) { return (typeof r.trend_score === "number") && Math.abs(r.trend_score) >= 5; });
    else if (screenerFilter === "combo")      filtered = filtered.filter(function(r) { return r.combo_3 || r.combo_4; });
    else if (screenerFilter === "new_combo")  filtered = filtered.filter(function(r) { return r.combo_3_new || r.combo_4_new; });
    else if (screenerFilter === "improving")  filtered = filtered.filter(function(r) { return (typeof r.trend_delta === "number") && r.trend_delta > 0; });
    else if (screenerFilter === "active_position") filtered = filtered.filter(function(r) {
      var a = r.signal_action || "";
      if (a.indexOf("ENTRY") === 0 || a.indexOf("SCALE") === 0 || a === "HOLD") return true;
      var ss = r.strat_statuses || {};
      for (var sk in ss) { var sa = ss[sk].signal_action || ""; if (sa.indexOf("ENTRY") === 0 || sa === "HOLD") return true; }
      return false;
    });
    else if (screenerFilter === "entry_signal") filtered = filtered.filter(function(r) { var a = r.signal_action || ""; return a.indexOf("ENTRY") === 0 || a.indexOf("SCALE") === 0; });
    else if (screenerFilter === "recent_combo") filtered = filtered.filter(function(r) { return typeof r.last_combo_bars === "number" && r.last_combo_bars <= 3; });
    else if (screenerFilter === "buy")        filtered = filtered.filter(function(r) { return (r.recommendation || "").toLowerCase() === "buy" || (r.recommendation || "").toLowerCase() === "strong_buy"; });
    else if (screenerFilter === "strat_active") filtered = filtered.filter(function(r) { var ss = r.strat_statuses || {}; for (var sk in ss) { var sa = ss[sk].signal_action || ""; if (sa.indexOf("ENTRY") === 0 || sa === "HOLD") return true; } return false; });
    else if (screenerFilter === "strat_any")   filtered = filtered.filter(function(r) { var ss = r.strat_statuses || {}; return STRAT_PRIO.some(function(sp) { var s = ss[sp.key]; if (!s) return false; var a = s.signal_action || ""; return a.indexOf("ENTRY") === 0 || a === "HOLD"; }); });
    else if (screenerFilter === "strat_dip")  filtered = filtered.filter(function(r) { var s = (r.strat_statuses || {}).dip_buy; return s && (s.signal_action || "").indexOf("ENTRY") === 0; });
    else if (screenerFilter === "strat_swing") filtered = filtered.filter(function(r) { var s = (r.strat_statuses || {}).swing; return s && ((s.signal_action || "").indexOf("ENTRY") === 0 || s.signal_action === "HOLD"); });
    else if (screenerFilter === "strat_trend") filtered = filtered.filter(function(r) { var s = (r.strat_statuses || {}).trend; return s && ((s.signal_action || "").indexOf("ENTRY") === 0 || s.signal_action === "HOLD"); });
    else if (screenerFilter === "strat_stoof") filtered = filtered.filter(function(r) { var s = (r.strat_statuses || {}).stoof; return s && ((s.signal_action || "").indexOf("ENTRY") === 0 || s.signal_action === "HOLD"); });

    /* ── Sort ────────────────────────────────────────────────────────────── */
    var sortKey = wrap.dataset.sortKey || _savedScreenerSortKey;
    var sortDir = wrap.dataset.sortDir || _savedScreenerSortDir;
    var _sortKeyMap = {
      "_ticker":     function(r) { return r.symbol || ""; },
      "_conviction": function(r) { var ts = r.trend_score; var mx = (typeof MAX_TREND_SCORE === "number" && MAX_TREND_SCORE > 0) ? MAX_TREND_SCORE : 28.2; return typeof ts === "number" ? ts / mx : -9999; },
      "_price":      function(r) { return (typeof r.last_close === "number") ? r.last_close : -1; },
      "_perf":       function(r) {
        var ss = r.strat_statuses || {};
        for (var i = 0; i < STRAT_PRIO.length; i++) { var si = ss[STRAT_PRIO[i].key]; if (si && si.l12m_pnl != null) return si.l12m_pnl; }
        return r.l12m_pnl != null ? r.l12m_pnl : -9999;
      },
      "_action_tf":  function(r) {
        var sym = String(r.symbol || "").toUpperCase();
        var sc = 0;
        var allTFs = typeof TIMEFRAMES !== "undefined" ? TIMEFRAMES : [];
        var n = allTFs.length;
        allTFs.forEach(function(tfk, tfi) {
          var rec = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[sym] && SCREENER.by_symbol[sym][tfk]) ? SCREENER.by_symbol[sym][tfk] : null;
          if (!rec) return;
          var bs = _bestStrat(rec.strat_statuses);
          var a = bs ? bs.signal_action : (rec.signal_action || "");
          if (a === "ENTRY 1.5x" || a.indexOf("SCALE") === 0) sc += (n - tfi) * 1000 + 500;
          else if (a.indexOf("ENTRY") === 0)                  sc += (n - tfi) * 1000 + 400;
          else if (a === "HOLD")                               sc += (n - tfi) * 1000 + 200;
          else if (a.indexOf("EXIT") === 0)                   sc += (n - tfi) * 1000 + 100;
        });
        return sc;
      },
    };
    var _getSortVal = _sortKeyMap[sortKey] || function(r) { return r[sortKey] != null ? r[sortKey] : ""; };
    filtered.sort(function(a, b) {
      var av = _getSortVal(a), bv = _getSortVal(b);
      var cmp = (typeof av === "number" && typeof bv === "number") ? av - bv : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });

    /* ── #1: Split into sections ─────────────────────────────────────────── */
    var sections = {exit: [], new_signal: [], active: [], watchlist: []};
    filtered.forEach(function(r) { sections[_rowSection(r)].push(r); });

    /* Symbol count */
    var comboNote = document.createElement("div");
    comboNote.style.cssText = "font-size:10px;color:var(--muted);padding:8px 2px 4px 2px;letter-spacing:0.3px;";
    comboNote.innerHTML = "<b style=\"color:var(--fg);font-weight:600;\">" + filtered.length + "</b> symbols · " + currentTF;
    wrap.appendChild(comboNote);

    /* ── #9: New signals banner ──────────────────────────────────────────── */
    if (sections.new_signal.length > 0) {
      var banner = document.createElement("div");
      banner.style.cssText = "padding:7px 12px;margin-bottom:8px;border-radius:6px;background:rgba(34,197,94,0.10);border:1px solid rgba(34,197,94,0.28);font-size:12px;font-weight:600;color:var(--candle-up);display:flex;align-items:center;gap:8px;flex-wrap:wrap;";
      var bannerSyms = sections.new_signal.slice(0, 7).map(function(r) { return r.symbol; }).join(", ");
      if (sections.new_signal.length > 7) bannerSyms += " +" + (sections.new_signal.length - 7) + " more";
      banner.innerHTML = "🔔 <span>" + sections.new_signal.length + " new signal" + (sections.new_signal.length > 1 ? "s" : "") + ":</span>"
        + " <span style='font-weight:400;color:var(--fg);'>" + bannerSyms + "</span>";
      wrap.appendChild(banner);
    }

    if (!filtered.length) {
      var empty = document.createElement("div");
      empty.style.cssText = "padding:32px;text-align:center;color:var(--muted);font-size:13px;";
      empty.textContent = currentGroup && currentGroup !== "all"
        ? "No symbols in this group. Select a different group or run a new scan."
        : "No symbols match your filters.";
      wrap.appendChild(empty);
      return;
    }

    /* ── Table structure ─────────────────────────────────────────────────── */
    var table = document.createElement("table");
    var COL_SCALE = 1.0;
    function scalePct(pct) {
      var n = parseFloat(String(pct).replace("%", ""));
      if (!isFinite(n)) return pct;
      return (n * COL_SCALE).toFixed(2).replace(/\.00$/, "") + "%";
    }

    var hdr = [
      ["symbol",     "Name",           "10%"],
      ["_ticker",    "Ticker",         "5%"],
      ["market_cap", "Mkt Cap",        "5%"],
      ["_price",     "Price",          "5%"],
      ["_conv10",    "TrendScore",     "9%"],
      ["_confluence","Traffic Light",  "9%"],
      ["_action_tf", "Action",         "11%"],
      ["_vs_bench",  "vs Bench",       "8%"],
      ["_perf",      "L12M Perf",      "7%"],
      ["_move_group","Group",          "5%"],
      ["_delete",    "",               "3%"],
    ];

    var colgroup = document.createElement("colgroup");
    hdr.forEach(function(item) { var col = document.createElement("col"); col.style.width = scalePct(item[2]); colgroup.appendChild(col); });
    table.appendChild(colgroup);

    var _sortable = new Set(["symbol", "_ticker", "market_cap", "_price", "_action_tf", "_perf"]);
    var _centered = new Set(["_conv10", "_confluence", "_action_tf", "_vs_bench", "_perf"]);
    var totalCols = hdr.length;

    /* thead */
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    hdr.forEach(function(item) {
      var k = item[0], label = item[1];
      var th = document.createElement("th");
      th.textContent = label;
      if (_centered.has(k)) th.style.textAlign = "center";
      if (_sortable.has(k)) {
        th.style.cursor = "pointer";
        th.addEventListener("click", (function(colKey) {
          return function() {
            var curK = wrap.dataset.sortKey || "_action_tf";
            var curD = wrap.dataset.sortDir || "desc";
            wrap.dataset.sortKey = colKey;
            wrap.dataset.sortDir = (curK === colKey) ? (curD === "asc" ? "desc" : "asc") : "desc";
            _savedScreenerSortKey = wrap.dataset.sortKey;
            _savedScreenerSortDir = wrap.dataset.sortDir;
            saveState({ screenerSortKey: _savedScreenerSortKey, screenerSortDir: _savedScreenerSortDir });
            D.buildScreener();
          };
        })(k));
      }
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    table.appendChild(thead);

    /* ── #1: Section config ──────────────────────────────────────────────── */
    var SECTION_CONFIG = [
      {key: "exit",       label: "Exit signals",     color: "#ef4444", hdrBg: "rgba(239,68,68,0.13)"},
      {key: "new_signal", label: "New signals",      color: "#22c55e", hdrBg: "rgba(34,197,94,0.13)"},
      {key: "active",     label: "Active positions", color: "#60a5fa", hdrBg: "rgba(96,165,250,0.13)"},
      {key: "watchlist",  label: "Long List",          color: "#a3a3a3", hdrBg: "rgba(163,163,163,0.10)"},
    ];

    SECTION_CONFIG.forEach(function(sec) {
      var secRows = sections[sec.key];
      if (!secRows.length) return;

      var tbody = document.createElement("tbody");

      /* Section header row (not for watchlist) */
      if (sec.label) {
        var trhSec = document.createElement("tr");
        trhSec.className = "scr-shdr";
        var tdSec = document.createElement("td");
        tdSec.colSpan = totalCols;
        tdSec.style.cssText = "font-size:11px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;"
          + "padding:20px 12px 6px 14px;color:" + sec.color + ";"
          + "border-top:2px solid " + sec.color + "99;"
          + "box-shadow:inset 3px 0 0 " + sec.color + ";";
        var countBadge = "<span style='font-weight:400;opacity:0.6;margin-left:5px;'>(" + secRows.length + ")</span>";
        tdSec.innerHTML = sec.label + countBadge;
        trhSec.appendChild(tdSec);
        tbody.appendChild(trhSec);
      }

      /* ── #10: Row rendering ──────────────────────────────────────────── */
      secRows.forEach(function(r) {
        var tr = document.createElement("tr");
        if (sec.color) tr.style.boxShadow = "inset 3px 0 0 " + sec.color + "44";

        hdr.forEach(function(item) {
          var k = item[0];
          var td = document.createElement("td");
          if (_centered.has(k)) td.style.textAlign = "center";

          /* ── Name ──────────────────────────────────────────────────── */
          if (k === "symbol") {
            td.style.maxWidth = "175px";
            td.style.overflow = "hidden";
            td.style.textOverflow = "ellipsis";
            td.title = r.symbol + (r.name ? " — " + r.name : "");
            var a = document.createElement("span");
            a.className = "link";
            a.textContent = r.name || r.symbol || "";
            a.addEventListener("click", function() {
              currentSymbol = String(r.symbol || "").toUpperCase();
              saveState({ symbol: currentSymbol });
              buildSymbolList();
              switchTab("strategy");
              renderChart();
            });
            td.appendChild(a);

          /* ── Ticker ────────────────────────────────────────────────── */
          } else if (k === "_ticker") {
            td.textContent = r.symbol || "";
            td.style.cssText = "font-size:11px;font-weight:700;color:var(--fg);letter-spacing:0.3px;";
            var secParts = [r.sector, r.industry].filter(Boolean);
            td.title = secParts.length ? secParts.join(" — ") : "";

          /* ── Market Cap ────────────────────────────────────────────── */
          } else if (k === "market_cap") {
            var mc = r.market_cap;
            if (typeof mc === "number" && mc > 0) {
              var fmt = mc >= 1e12 ? (mc / 1e12).toFixed(1) + "T"
                      : mc >= 1e9  ? (mc / 1e9).toFixed(1) + "B"
                      : mc >= 1e6  ? (mc / 1e6).toFixed(0) + "M"
                      : mc.toLocaleString();
              td.textContent = fmt;
              td.style.cssText = "font-size:11px;font-weight:700;color:var(--fg);letter-spacing:0.3px;";
              td.title = "$" + mc.toLocaleString();
            }

          /* ── Price + #4 inline risk ────────────────────────────────── */
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
                deltaRow.style.color = dp >= 0 ? "var(--candle-up)" : "var(--candle-down)";
                deltaRow.textContent = (dp >= 0 ? "+" : "") + dp.toFixed(1) + "% " + tfLabel;
                pWrap.appendChild(deltaRow);
              }

              /* #4: Find best active strategy's entry/stop for inline risk */
              var riskEntry = null, riskStop = null;
              var riskSS = r.strat_statuses || {};
              for (var pi = 0; pi < STRAT_PRIO.length; pi++) {
                var riskSI = riskSS[STRAT_PRIO[pi].key];
                if (riskSI && riskSI.entry_price != null) {
                  riskEntry = riskSI.entry_price;
                  riskStop = riskSI.atr_stop;
                  break;
                }
              }
              if (riskEntry == null && r.entry_price != null) {
                riskEntry = r.entry_price;
                riskStop = r.atr_stop;
              }
              if (riskEntry != null) {
                var riskLine = document.createElement("div");
                var riskPct = (riskStop != null && close > 0) ? ((close - riskStop) / close * 100) : null;
                riskLine.style.cssText = "font-size:9px;color:var(--muted);margin-top:2px;white-space:nowrap;";
                riskLine.textContent = "↑" + riskEntry.toFixed(2)
                  + " ⊘" + (riskStop != null ? riskStop.toFixed(2) : "—")
                  + (riskPct != null ? " " + riskPct.toFixed(1) + "%" : "");
                pWrap.appendChild(riskLine);
              }

              td.appendChild(pWrap);
              /* Keep full tooltip — use best strategy entry/stop (same source as inline display) */
              var tipParts = ["Price: " + close.toFixed(2)];
              var tipEntry = riskEntry != null ? riskEntry : r.entry_price;
              var tipStop = riskStop != null ? riskStop : r.atr_stop;
              if (tipEntry != null) tipParts.push("Entry: " + tipEntry.toFixed(2));
              if (tipStop != null) tipParts.push("ATR Stop: " + tipStop.toFixed(2));
              if (close > 0 && tipStop != null) tipParts.push("% Risk: " + ((close - tipStop) / close * 100).toFixed(1) + "%");
              td.title = tipParts.join("\n");
            }

          /* ── TrendScore ────────────────────────────────────────────── */
          } else if (k === "_conv10") {
            var vals = r.conv10 || [];
            var wrap10 = document.createElement("div");
            wrap10.style.cssText = "display:flex;align-items:center;justify-content:center;gap:4px;";
            if (vals.length) {
              var w = 70, h = 22, bw = Math.max(2, Math.floor(w / vals.length) - 1);
              var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
              svg.setAttribute("width", w); svg.setAttribute("height", h);
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
                bar.setAttribute("x", i * (bw + 1));
                bar.setAttribute("y", v >= 0 ? mid - bh : mid);
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
            td.title = (typeof ts === "number" ? "TrendScore: " + (ts > 0 ? "+" : "") + ts.toFixed(1) + " / " + maxTS.toFixed(1) + "\n" : "")
              + (convPct !== null ? "Conviction: " + (convPct > 0 ? "+" : "") + convPct + "%\n" : "")
              + (typeof td3 === "number" ? "Trend Δ(3): " + (td3 > 0 ? "+" : "") + td3.toFixed(1) + "\n" : "")
              + "13-bar avg: " + (avg > 0 ? "+" : "") + avg.toFixed(2);
            td.appendChild(wrap10);

          /* ── Traffic Light + #6 TF conflict ───────────────────────── */
          } else if (k === "_confluence") {
            var symCf = String(r.symbol || "").toUpperCase();
            var tfConf = document.createElement("div");
            tfConf.className = "tf-conf";
            tfConf.style.cssText = "display:inline-flex;align-items:center;gap:2px;";
            (typeof TIMEFRAMES !== "undefined" ? TIMEFRAMES : []).forEach(function(tf) {
              var col = document.createElement("div");
              col.className = "tf-col";
              var lbl = document.createElement("div");
              lbl.className = "tf-lbl";
              lbl.textContent = tf.replace("1", "");
              col.appendChild(lbl);
              var dot = document.createElement("div");
              dot.className = "tf-dot";
              var rec = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[symCf] && SCREENER.by_symbol[symCf][tf])
                ? SCREENER.by_symbol[symCf][tf] : null;
              var tsDot = rec ? rec.trend_score : null;
              if (typeof tsDot === "number" && tsDot > 0)       dot.classList.add("td-bull");
              else if (typeof tsDot === "number" && tsDot < 0)  dot.classList.add("td-bear");
              else                                               dot.classList.add("td-neu");
              dot.title = tf + ": " + (tsDot != null ? tsDot : "N/A");
              col.appendChild(dot);
              tfConf.appendChild(col);
            });
            td.appendChild(tfConf);

          /* ── Action (merged Strategy D/S/T) ───────────────────────── */
          } else if (k === "_action_tf") {
            var symAt = String(r.symbol || "").toUpperCase();
            var atWrap = document.createElement("div");
            atWrap.style.cssText = "display:flex;gap:3px;align-items:center;justify-content:center;";

            (typeof TIMEFRAMES !== "undefined" ? TIMEFRAMES : []).forEach(function(tfk) {
              var rec = (typeof SCREENER !== "undefined" && SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[symAt] && SCREENER.by_symbol[symAt][tfk])
                ? SCREENER.by_symbol[symAt][tfk] : null;

              var cell = document.createElement("span");
              cell.style.cssText = "display:inline-flex;flex-direction:column;align-items:center;";
              var lbl = document.createElement("span");
              lbl.style.cssText = "font-size:9px;color:var(--muted);";
              lbl.textContent = tfk.replace("1", "");
              cell.appendChild(lbl);

              var badge = document.createElement("span");
              badge.style.cssText = "font-size:9px;font-weight:700;padding:1px 4px;border-radius:3px;";

              /* #3: Check polarity strategies with priority Dip > Swing > Trend */
              var bs = rec ? _bestStrat(rec.strat_statuses) : null;

              if (bs) {
                /* Polarity/stoof strategy badge — single source of truth via _bestStrat() */
                var bsAct = bs.signal_action;
                var bsCol = bs.color;
                var bsInfo = bs.info;
                var bsHeld = bsInfo ? bsInfo.bars_held : null;
                // combo_bars = bars since entry/scale-up (0 on signal bar); fall back to bars_held
                var bsCb = bsInfo ? (bsInfo.combo_bars != null ? bsInfo.combo_bars : bsInfo.bars_held) : null;
                var isScale = bsAct === "ENTRY 1.5x" || bsAct.indexOf("SCALE") === 0;
                var isEntry = bsAct.indexOf("ENTRY") === 0;
                var isExit = bsAct === "FLAT" && bsInfo && bsInfo.last_exit_bars_ago != null;

                badge.style.background = _hexRgba(bsCol, 0.18);
                badge.style.color = bsCol;

                if (isScale) {
                  badge.textContent = bs.label + "·E+";
                  badge.title = tfk + ": " + bs.key.replace("_", " ") + " Scale 1.5x" + (bsCb != null ? " " + bsCb + "b" : "");
                } else if (isEntry) {
                  badge.textContent = bs.label + "·E";
                  badge.title = tfk + ": " + bs.key.replace("_", " ") + " Entry" + (bsCb != null ? " " + bsCb + "b" : "");
                } else if (isExit) {
                  /* Recently exited polarity/stoof strategy */
                  badge.style.background = "var(--trade-loss-bg)"; badge.style.color = bsCol;
                  badge.textContent = bs.label + "·X";
                  var bsEb = bsInfo.last_exit_bars_ago;
                  var bsEr = bsInfo.last_exit_reason ? " (" + bsInfo.last_exit_reason + ")" : "";
                  badge.title = tfk + ": " + bs.key.replace("_", " ") + " Exit " + bsEb + "b ago" + bsEr;
                } else {
                  /* HOLD — show bars_held inline */
                  badge.style.background = _hexRgba(bsCol, 0.10);
                  badge.textContent = bs.label + "·H" + (bsHeld != null ? bsHeld : "");
                  badge.title = tfk + ": " + bs.key.replace("_", " ") + " Hold" + (bsHeld != null ? " " + bsHeld + "b" : "");
                }
              } else {
                /* v6 fallback */
                var act = rec ? (rec.signal_action || "FLAT") : "FLAT";
                var cb = rec ? (rec.combo_bars != null ? rec.combo_bars : rec.bars_held) : null;

                if (act === "ENTRY 1.5x" || act.indexOf("SCALE") === 0) {
                  badge.style.background = "var(--combo-c4-bg)"; badge.style.color = "var(--combo-c4-fg)";
                  badge.textContent = "E+";
                  badge.title = tfk + ": " + (act.indexOf("SCALE") === 0 ? "Scale 1.5x" : "Entry 1.5x") + (cb != null ? " " + cb + "b" : "");
                } else if (act.indexOf("ENTRY") === 0) {
                  badge.style.background = "var(--combo-c3-bg)"; badge.style.color = "var(--combo-c3-fg)";
                  badge.textContent = "E";
                  badge.title = tfk + ": Entry 1x" + (cb != null ? " " + cb + "b" : "");
                } else if (act === "HOLD") {
                  badge.style.background = "rgba(74,130,184,0.18)"; badge.style.color = "var(--info)";
                  /* #7: bars inline */
                  badge.textContent = "H" + (cb != null ? cb : "");
                  badge.title = tfk + ": Hold" + (cb != null ? " " + cb + "b" : "");
                } else if (act.indexOf("EXIT") === 0 || (act === "FLAT" && rec && rec.last_exit_bars_ago != null && rec.last_exit_bars_ago <= 2)) {
                  badge.style.background = "var(--trade-loss-bg)"; badge.style.color = "var(--danger)";
                  badge.textContent = "X";
                  var eb = rec ? rec.last_exit_bars_ago : null;
                  badge.title = tfk + ": Exit" + (eb != null ? " " + eb + "b" : "");
                } else {
                  badge.style.color = "var(--muted)";
                  badge.textContent = "—";
                  badge.title = tfk + ": Flat";
                }
              }

              cell.appendChild(badge);
              atWrap.appendChild(cell);
            });
            td.appendChild(atWrap);

          /* ── vs Bench ──────────────────────────────────────────────── */
          } else if (k === "_vs_bench") {
            var wrapVs = document.createElement("div");
            wrapVs.style.cssText = "display:flex;gap:6px;justify-content:center;font-size:11px;font-weight:600;";
            var tooltipParts = [];
            var sDelta = r.sector_ts_delta, sBench = r.sector_etf || "";
            if (sDelta != null && sBench) {
              var sVal = parseFloat(sDelta);
              var sSpan = document.createElement("span");
              sSpan.textContent = (sVal > 0 ? "+" : "") + sVal.toFixed(1);
              sSpan.style.color = sVal > 0 ? "var(--candle-up)" : sVal < 0 ? "var(--candle-down)" : "var(--muted)";
              var sLabel = document.createElement("span");
              sLabel.textContent = "S";
              sLabel.style.cssText = "color:var(--muted);font-size:9px;margin-right:1px;";
              var sGroup = document.createElement("span");
              sGroup.appendChild(sLabel); sGroup.appendChild(sSpan);
              wrapVs.appendChild(sGroup);
              var benchName = (typeof SYMBOL_DISPLAY !== "undefined" && SYMBOL_DISPLAY && SYMBOL_DISPLAY[sBench]) || sBench;
              tooltipParts.push("Sector: " + (sVal > 0 ? "+" : "") + sVal.toFixed(1) + " vs " + benchName);
            }
            var mDelta = r.market_ts_delta, mIdx = r.market_index || "";
            if (mDelta != null && mIdx) {
              var mVal = parseFloat(mDelta);
              var mSpan = document.createElement("span");
              mSpan.textContent = (mVal > 0 ? "+" : "") + mVal.toFixed(1);
              mSpan.style.color = mVal > 0 ? "var(--candle-up)" : mVal < 0 ? "var(--candle-down)" : "var(--muted)";
              var mLabel = document.createElement("span");
              mLabel.textContent = "M";
              mLabel.style.cssText = "color:var(--muted);font-size:9px;margin-right:1px;";
              var mGroup = document.createElement("span");
              mGroup.appendChild(mLabel); mGroup.appendChild(mSpan);
              wrapVs.appendChild(mGroup);
              var mIdxName = (typeof SYMBOL_DISPLAY !== "undefined" && SYMBOL_DISPLAY && SYMBOL_DISPLAY[mIdx]) || mIdx;
              tooltipParts.push("Market: " + (mVal > 0 ? "+" : "") + mVal.toFixed(1) + " vs " + mIdxName);
            }
            if (wrapVs.childNodes.length) { td.appendChild(wrapVs); td.title = tooltipParts.join("\n"); }

          /* ── #5: L12M Perf (replaces Analysts + P/E vs Sector) ────── */
          } else if (k === "_perf") {
            /* Use highest-priority active strategy's stats, fallback to v6 */
            var perfSS = r.strat_statuses || {};
            var perfInfo = null;
            var perfLabel = "";
            for (var pi2 = 0; pi2 < STRAT_PRIO.length; pi2++) {
              var pSI = perfSS[STRAT_PRIO[pi2].key];
              if (pSI && pSI.l12m_pnl != null) {
                perfInfo = pSI;
                perfLabel = STRAT_PRIO[pi2].label;
                break;
              }
            }
            if (!perfInfo && r.l12m_pnl != null) {
              perfInfo = {l12m_pnl: r.l12m_pnl, l12m_hit_rate: r.l12m_hit_rate, l12m_trades: r.l12m_trades};
              perfLabel = "v6";
            }
            if (perfInfo) {
              var perfWrap = document.createElement("div");
              perfWrap.style.cssText = "line-height:1.3;";
              var pnl = perfInfo.l12m_pnl;
              var pnlSpan = document.createElement("div");
              pnlSpan.textContent = (pnl >= 0 ? "+" : "") + pnl.toFixed(1) + "%";
              pnlSpan.style.cssText = "font-size:11px;font-weight:700;color:" + (pnl >= 0 ? "var(--candle-up)" : "var(--candle-down)") + ";";
              perfWrap.appendChild(pnlSpan);
              var hr2 = perfInfo.l12m_hit_rate;
              if (hr2 != null) {
                var hrSpan = document.createElement("div");
                hrSpan.textContent = hr2.toFixed(0) + "% W";
                hrSpan.style.cssText = "font-size:9px;color:var(--muted);";
                perfWrap.appendChild(hrSpan);
              }
              td.appendChild(perfWrap);
              var trades2 = perfInfo.l12m_trades;
              td.title = "L12M [" + perfLabel + "]: " + (pnl >= 0 ? "+" : "") + pnl.toFixed(1) + "% P&L"
                + (hr2 != null ? " | " + hr2.toFixed(1) + "% win rate" : "")
                + (trades2 ? " | " + trades2 + " trades" : "");
            }

          /* ── Group selector ────────────────────────────────────────── */
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
            current.value = ""; current.textContent = _groupLabel(groupLabel); current.selected = true;
            sel.appendChild(current);
            GROUP_KEYS.filter(function(g) { return curGroups.indexOf(g) < 0; }).forEach(function(g) {
              var opt = document.createElement("option");
              opt.value = g; opt.textContent = "→ " + _groupLabel(g);
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

          /* ── Delete button ─────────────────────────────────────────── */
          } else if (k === "_delete") {
            var symDel = String(r.symbol || "").toUpperCase();
            var curGroupsDel = _findSymbolGroups(symDel);
            var btn = document.createElement("button");
            btn.textContent = "✕";
            btn.title = "Remove " + symDel + " from CSV & delete data";
            btn.style.cssText = "font-size:10px;padding:1px 5px;border:1px solid var(--danger);border-radius:4px;background:transparent;color:var(--danger);cursor:pointer;line-height:1;";
            btn.addEventListener("click", function() { _deleteStock(symDel, curGroupsDel.length ? curGroupsDel[0] : ""); });
            td.appendChild(btn);

          } else {
            td.textContent = (r[k] != null ? r[k] : "");
          }

          tr.appendChild(td);
        }); /* end hdr.forEach */

        tbody.appendChild(tr);
      }); /* end secRows.forEach */

      table.appendChild(tbody);
    }); /* end SECTION_CONFIG.forEach */

    wrap.appendChild(table);
  } /* end buildScreener */

  /* ── CSV export ───────────────────────────────────────────────────────────── */
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
    a.href = url; a.download = "screener_" + currentTF + ".csv";
    document.body.appendChild(a); a.click(); a.remove();
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
