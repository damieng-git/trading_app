// DOM cache for hot-path element lookups
    const DOM = {
      chartUpper: null, chartLower: null, sidebar: null,
      stockTitle: null, screener: null, loadingEl: null,
      fileWarn: null, indicatorStrip: null, themeToggle: null,
      status: null, signalCard: null, dataWarn: null,
    };
    function initDOMCache() {
      DOM.chartUpper = document.getElementById("chartUpper");
      DOM.chartLower = document.getElementById("chartLower");
      DOM.sidebar = document.getElementById("sidebar");
      DOM.stockTitle = document.getElementById("stockTitle");
      DOM.screener = document.getElementById("screener");
      DOM.loadingEl = document.getElementById("loadingOverlay");
      DOM.fileWarn = document.getElementById("fileWarn");
      DOM.indicatorStrip = document.getElementById("indicatorStrip");
      DOM.themeToggle = document.getElementById("themeToggle");
      DOM.status = document.getElementById("status");
      DOM.signalCard = document.getElementById("signalCard");
      DOM.dataWarn = document.getElementById("dataWarn");
    }
    initDOMCache();

    // UI state (persisted)
    // NOTE: bump key to reset prior default indicator selections.
    const _LS_SUFFIX = window.location.pathname.startsWith("/test/") ? "_test" : "";
    const LS_KEY = "td_dash_shell_state_v1_2" + _LS_SUFFIX;
    function loadState() {
      try {
        return JSON.parse(localStorage.getItem(LS_KEY) || "{}") || {};
      } catch (e) {
        return {};
      }
    }
    function saveState(patch) {
      try {
        const cur = loadState();
        const next = Object.assign({}, cur, patch || {});
        localStorage.setItem(LS_KEY, JSON.stringify(next));
      } catch (e) {}
    }

    var _st0 = loadState();
    var currentSymbol = (typeof _st0.symbol === "string" && SYMBOLS.includes(_st0.symbol.toUpperCase())) ? _st0.symbol.toUpperCase() : DEFAULT_SYMBOL;
    var currentTF = (typeof _st0.tf === "string" && TIMEFRAMES.includes(_st0.tf.toUpperCase())) ? _st0.tf.toUpperCase() : DEFAULT_TF;
    var currentTab = (typeof _st0.tab === "string" && ["chart", "strategy", "screener", "info", "pnl"].includes(_st0.tab)) ? _st0.tab : "screener";
    var currentGroup = (typeof _st0.group === "string") ? _st0.group : "all";
    var _savedScreenerFilter = _st0.screenerFilter || "all";
    var _savedScreenerSortKey = _st0.screenerSortKey || "_action_tf";
    var _savedScreenerSortDir = _st0.screenerSortDir || "desc";

    var figCache = {};
    var currentFig = null;
    var indicatorKeys = [];
    var selectedIndicators = new Set(Array.isArray(_st0.indicators) ? _st0.indicators : []);
    let _upperShapesStrategy = [];
    let _upperShapesCharts = [];
    let currentStrategy = (typeof _st0.strategy === "string") ? _st0.strategy : "trend";
    window.currentStrategy = currentStrategy;

    window.Dashboard = window.Dashboard || {};
    var buildScreener = function() { if (window.Dashboard && window.Dashboard.buildScreener) window.Dashboard.buildScreener(); };
    var exportCSV = function() { if (window.Dashboard && window.Dashboard.exportCSV) window.Dashboard.exportCSV(); };
    var buildPnlTab = function() { if (window.Dashboard && window.Dashboard.buildPnlTab) window.Dashboard.buildPnlTab(); };
    var loadTrades = function() { if (window.Dashboard && window.Dashboard.loadTrades) window.Dashboard.loadTrades(); };

    function _debounce(fn, ms) {
      let t;
      return function(...args) { clearTimeout(t); t = setTimeout(() => fn.apply(this, args), ms); };
    }

    // Ensure sticky table headers sit exactly under the sticky topbar.
    // (A fixed pixel value can be wrong when the topbar wraps on smaller screens.)
    function setTopbarHeightVar() {
      try {
        const tb = document.querySelector(".topbar");
        if (!tb) return;
        // Use bottom to be robust to any future transforms/sticky behavior.
        const rect = tb.getBoundingClientRect();
        const h = Math.ceil((rect && typeof rect.bottom === "number") ? rect.bottom : (rect.height || 0));
        document.documentElement.style.setProperty("--topbar-h", `${Math.max(0, h)}px`);
      } catch (e) {}
    }
    setTopbarHeightVar();
    const _chartHeights = { chartUpper: 500, chartPnl: 160, chartTs: 140, chartOsc: 350, chartLower: 1200 };
    function _resizeWidthOnly(gd, id) {
      if (!gd || !gd.data) return;
      const w = gd.parentElement ? gd.parentElement.clientWidth : gd.clientWidth;
      if (w > 0) {
        const h = _chartHeights[id] || (gd._fullLayout && gd._fullLayout.height) || gd.clientHeight;
        Plotly.relayout(gd, { width: w, height: h });
      }
    }
    let _tbResizeT = null;
    window.addEventListener("resize", () => {
      if (_tbResizeT) clearTimeout(_tbResizeT);
      _tbResizeT = setTimeout(() => {
        setTopbarHeightVar();
        ["chartUpper", "chartPnl", "chartOsc", "chartTs", "chartLower"].forEach(id => {
          const gd = (id === "chartUpper" ? DOM.chartUpper : id === "chartLower" ? DOM.chartLower : document.getElementById(id));
          _resizeWidthOnly(gd, id);
        });
      }, 75);
    });

    // --- Theme toggle (dark / light) ---
    let currentTheme = _st0.theme || "dark";
    function applyTheme(theme) {
      currentTheme = theme;
      document.documentElement.setAttribute("data-theme", theme);
      if (DOM.themeToggle) DOM.themeToggle.innerHTML = theme === "dark" ? "&#9788;" : "&#9790;";
      saveState({ theme: currentTheme });
      applyPlotlyTheme();
    }
    function _css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
    function getPlotlyThemeOverrides() {
      const bg = _css("--plotly-bg");
      const grid = _css("--plotly-grid");
      const text = _css("--plotly-text");
      const zeroline = _css("--plotly-zeroline") || grid;
      return {
        paper_bgcolor: bg,
        plot_bgcolor: bg,
        font: { color: text },
        xaxis: { gridcolor: grid, zerolinecolor: zeroline },
        yaxis: { gridcolor: grid, zerolinecolor: zeroline },
      };
    }
    function applyPlotlyTheme() {
      const overrides = getPlotlyThemeOverrides();
      const axisStyle = { gridcolor: overrides.xaxis.gridcolor, zerolinecolor: overrides.xaxis.zerolinecolor };
      const _spkColor = _css("--plotly-spike") || (currentTheme === "dark" ? "rgba(224,221,213,0.7)" : "rgba(42,37,32,0.85)");
      try {
        const upd = { paper_bgcolor: overrides.paper_bgcolor, plot_bgcolor: overrides.plot_bgcolor, font: overrides.font };
        const gdUp = DOM.chartUpper;
        const gdPnl = document.getElementById("chartPnl");
        const gdOsc = document.getElementById("chartOsc");
        const gdTs = document.getElementById("chartTs");
        const gdLo = DOM.chartLower;
        [gdPnl, gdOsc, gdTs].forEach(gd => {
          if (gd && gd.data) Plotly.relayout(gd, { paper_bgcolor: upd.paper_bgcolor, plot_bgcolor: upd.plot_bgcolor, font: upd.font });
        });
        if (gdUp && gdUp.data) {
          const lay = {};
          lay.paper_bgcolor = upd.paper_bgcolor;
          lay.plot_bgcolor = upd.plot_bgcolor;
          lay.font = upd.font;
          const axes = gdUp._fullLayout || {};
          for (const k of Object.keys(axes)) {
            if (/^[xy]axis[0-9]*$/.test(k)) {
              lay[k + ".gridcolor"] = axisStyle.gridcolor;
              lay[k + ".zerolinecolor"] = axisStyle.zerolinecolor;
              if (axes[k].showspikes) lay[k + ".spikecolor"] = _spkColor;
            }
          }
          Plotly.relayout(gdUp, lay);
        }
        if (gdLo && gdLo.data) {
          const lay = {};
          lay.paper_bgcolor = upd.paper_bgcolor;
          lay.plot_bgcolor = upd.plot_bgcolor;
          lay.font = upd.font;
          const axes = gdLo._fullLayout || {};
          for (const k of Object.keys(axes)) {
            if (/^[xy]axis[0-9]*$/.test(k)) {
              lay[k + ".gridcolor"] = axisStyle.gridcolor;
              lay[k + ".zerolinecolor"] = axisStyle.zerolinecolor;
              if (axes[k].showspikes) lay[k + ".spikecolor"] = _spkColor;
            }
          }
          Plotly.relayout(gdLo, lay);
        }
        
      } catch (e) {}
    }
    if (DOM.themeToggle) {
      DOM.themeToggle.setAttribute("aria-label", "Toggle theme");
      DOM.themeToggle.addEventListener("click", () => {
        applyTheme(currentTheme === "dark" ? "light" : "dark");
      });
    }
    const _pdfBtn = document.getElementById("pdfExport");
    if (_pdfBtn) {
      _pdfBtn.addEventListener("click", () => {
        document.body.classList.remove("print-chart", "print-screener");
        document.body.classList.add(currentTab === "screener" ? "print-screener" : "print-chart"); 
        setTimeout(() => {
          window.print();
          document.body.classList.remove("print-chart", "print-screener");
        }, 100);
      });
    }
    applyTheme(currentTheme);

    // Display names (keep internal keys stable). Exposed for chart_builder.js.
    const INDICATOR_LABELS = {
      // <= 20 chars, simple + trader-friendly
      "WT_LB": "WaveTrend",
      "OBVOSC_LB": "OBV Osc",
      "SQZMOM_LB": "Squeeze Mom",
      "CM_Ult_MacD_MFT": "MACD",
      "CM_P-SAR": "Parabolic SAR",
      "Stoch_MTM": "SMI",
      "TuTCI": "Turtle Channels",
      "Nadaraya-Watson Smoother": "NW Smoother",
      "Nadaraya-Watson Envelop (MAE)": "NWE MAE",
      "Nadaraya-Watson Envelop (STD)": "NWE STD",
      "Nadaraya-Watson Envelop (Repainting)": "NWE RP",
      "BB 30": "Bollinger (30)",
      "Volume + MA20": "Volume/MA20",
      "RSI Strength & Consolidation Zones (Zeiierman)": "RSI Strength",
      "RSI Strength & Consolidation Zones (Zeiierman) (breakout)": "RSI Str (BO)",
    };
    window._INDICATOR_LABELS = INDICATOR_LABELS;

    const INDICATOR_HELP = {
      "Nadaraya-Watson Smoother": "Smoother line colored by slope; repainting visual mode.",
      "Nadaraya-Watson Envelop (MAE)": "Mean-reversion envelope; MAE bands; signals on band crosses.",
      "Nadaraya-Watson Envelop (STD)": "Mean-reversion envelope; STD bands; signals on band crosses.",
      "Nadaraya-Watson Envelop (Repainting)": "Repainting (non-causal) envelope to match TradingView visuals.",
      "SuperTrend": "ATR band trend state (+1/-1) with flips.",
      "UT Bot Alert": "ATR trailing stop with buy/sell markers.",
      "CM_Ult_MacD_MFT": "MACD line vs signal (regime) for KPI coloring.",
      "Stoch_MTM": "SMI strategy-style entries persisted as regime.",
    };

    function displayIndicatorKey(k) {
      const raw = (INDICATOR_LABELS && INDICATOR_LABELS[k]) ? INDICATOR_LABELS[k] : (k || "");
      // Hard safety: enforce <= 20 chars even if we missed a mapping.
      if (raw.length <= 20) return raw;
      return raw.slice(0, 19).trimEnd() + "…";
    }

    function formatDeltaPct(x) {
      const v = (typeof x === "number") ? x : (x === null || x === undefined ? NaN : Number(x));
      if (!isFinite(v)) return "";
      const s = (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
      return s;
    }

    function deltaClass(x) {
      const v = (typeof x === "number") ? x : (x === null || x === undefined ? NaN : Number(x));
      if (!isFinite(v)) return "neu";
      if (v > 0) return "pos";
      if (v < 0) return "neg";
      return "neu";
    }

    function setLoading(isLoading, txt) {
      const t = document.getElementById("loadingText");
      const charts = ["chartUpper", "chartPnl", "chartOsc", "chartLower"];
      if (!DOM.loadingEl || !t) return;
      if (isLoading) {
        DOM.loadingEl.style.display = "flex";
        t.textContent = txt || "Loading…";
        charts.forEach(id => { const el = (id === "chartUpper" ? DOM.chartUpper : id === "chartLower" ? DOM.chartLower : document.getElementById(id)); if (el) el.classList.add("skeleton"); });
      } else {
        DOM.loadingEl.style.display = "none";
        charts.forEach(id => { const el = (id === "chartUpper" ? DOM.chartUpper : id === "chartLower" ? DOM.chartLower : document.getElementById(id)); if (el) el.classList.remove("skeleton"); });
      }
    }

    function setActiveTFButton() {
      document.querySelectorAll(".tab-tf-btn[data-tf]").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tf === currentTF);
      });
    }

    // --- Symbol groups (watchlist / portfolio / ...)
    const GROUP_KEYS = (() => {
      try {
        const ks = Object.keys(SYMBOL_GROUPS || {});
        const valid = ks.filter(k => Array.isArray(SYMBOL_GROUPS[k]));
        const preferredOrder = ["Portfolio", "Entry_stocks", "Watchlist", "Benchmark", "Stoof"];
        valid.sort((a, b) => {
          const ia = preferredOrder.findIndex(p => p.toLowerCase() === a.toLowerCase());
          const ib = preferredOrder.findIndex(p => p.toLowerCase() === b.toLowerCase());
          const oa = ia >= 0 ? ia : preferredOrder.length;
          const ob = ib >= 0 ? ib : preferredOrder.length;
          return oa !== ob ? oa - ob : a.localeCompare(b);
        });
        return valid;
      } catch (e) {
        return [];
      }
    })();

    function getAllowedSymbolsSet() {
      if (!GROUP_KEYS.length) return new Set(SYMBOLS);
      if (!currentGroup || currentGroup === "all") return new Set(SYMBOLS);
      const arr = (SYMBOL_GROUPS && Array.isArray(SYMBOL_GROUPS[currentGroup])) ? SYMBOL_GROUPS[currentGroup] : null;
      if (!arr || !arr.length) return new Set();
      const set = new Set();
      arr.forEach(s => {
        const sym = (s || "").toString().trim().toUpperCase();
        if (sym && SYMBOLS.includes(sym)) set.add(sym);
      });
      return set;
    }

    function ensureCurrentSymbolAllowed() {
      const allowed = getAllowedSymbolsSet();
      if (allowed.has(currentSymbol)) return;
      const first = SYMBOLS.find(s => allowed.has(s)) || (SYMBOLS[0] || "");
      if (first) {
        currentSymbol = first;
        saveState({ symbol: currentSymbol });
      }
    }

    const _groupNames = {"entry_stocks":"Entry Stocks","benchmark":"Benchmark","stoof":"Stoof"};
    function _groupLabel(k) {
      if (k === "all") return "All";
      return _groupNames[k] || k.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    }

    function _selectGroup(g) {
      currentGroup = g;
      saveState({ group: currentGroup });
      _updateGroupDropdown();
      ensureCurrentSymbolAllowed();
      buildSymbolList();
      if (currentTab === "pnl") buildPnlTab();
      else if (DOM.chartUpper && DOM.chartUpper.style.display !== "none") renderChart();
      if (document.getElementById("screenerWrap").style.display !== "none") buildScreener();
      setStatus();
    }

    function _updateGroupDropdown() {
      document.querySelectorAll(".tab-group-dropdown:not(#strategyDropdown):not(.strategy-placeholder) .tab-group-trigger").forEach(trigger => {
        trigger.innerHTML = _groupLabel(currentGroup) + " &#9662;";
      });
      document.querySelectorAll(".tab-group-dropdown:not(#strategyDropdown):not(.strategy-placeholder) .group-option").forEach(opt => {
        opt.classList.toggle("active", opt.dataset.group === currentGroup);
      });
    }

    function _buildGroupOptions() {
      const primaryGroups = ["portfolio", "entry_stocks", "watchlist"];
      const secondaryGroups = ["benchmark", "stoof"];
      const ordered = ["all"];
      primaryGroups.forEach(g => { if (GROUP_KEYS.includes(g)) ordered.push(g); });
      ordered.push("__sep__");
      secondaryGroups.forEach(g => { if (GROUP_KEYS.includes(g)) ordered.push(g); });
      GROUP_KEYS.forEach(g => { if (!ordered.includes(g)) ordered.push(g); });
      return ordered;
    }

    let _groupDropdownInited = false;
    function buildGroupTabs() {
      const dropdowns = document.querySelectorAll(".tab-group-dropdown");
      if (!dropdowns.length) return;

      dropdowns.forEach(dropdown => {
        if (dropdown.id === "strategyDropdown" || dropdown.classList.contains("strategy-placeholder")) return;
        if (!GROUP_KEYS.length) { dropdown.style.display = "none"; return; }
        dropdown.style.display = "";
        const trigger = dropdown.querySelector(".tab-group-trigger");
        const menu = dropdown.querySelector(".tab-group-menu");
        if (!trigger || !menu) return;
        menu.innerHTML = "";

        _buildGroupOptions().forEach(g => {
          if (g === "__sep__") {
            const sep = document.createElement("div");
            sep.style.cssText = "border-top:1px solid var(--border-strong);margin:4px 0;";
            menu.appendChild(sep);
            return;
          }
          const opt = document.createElement("div");
          opt.className = "group-option" + (g === currentGroup ? " active" : "");
          opt.dataset.group = g;
          opt.textContent = _groupLabel(g);
          opt.addEventListener("click", (e) => {
            e.stopPropagation();
            _selectGroup(g);
            _closeAllGroupMenus();
          });
          menu.appendChild(opt);
        });

        if (!trigger._inited) {
          trigger._inited = true;
          trigger.addEventListener("click", (e) => {
            e.stopPropagation();
            const wasOpen = menu.classList.contains("open");
            _closeAllGroupMenus();
            if (!wasOpen) menu.classList.add("open");
          });
        }
      });

      if (!_groupDropdownInited) {
        _groupDropdownInited = true;
        document.addEventListener("click", () => _closeAllGroupMenus());
      }

      if (currentGroup !== "all" && !GROUP_KEYS.includes(currentGroup)) {
        currentGroup = "all";
        saveState({ group: currentGroup });
      }
      _updateGroupDropdown();
    }

    function _closeAllGroupMenus() {
      document.querySelectorAll(".tab-group-menu").forEach(m => m.classList.remove("open"));
    }

    // (removed) TF KPI badges per user request

    function _freshness(isoStr) {
      if (!isoStr) return "";
      try {
        const d = new Date(isoStr);
        const now = new Date();
        const diffMs = now - d;
        if (diffMs < 0) return "just now";
        const mins = Math.floor(diffMs / 60000);
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        const days = Math.floor(hrs / 24);
        return `${days}d ago`;
      } catch (e) { return ""; }
    }

    function setStatus() {
      const built = (RUN_META && RUN_META.started_utc) ? RUN_META.started_utc : "";
      const bars = (DATA_HEALTH && DATA_HEALTH[currentSymbol] && DATA_HEALTH[currentSymbol][currentTF] && DATA_HEALTH[currentSymbol][currentTF].bars) ? DATA_HEALTH[currentSymbol][currentTF].bars : "";
      const end = (DATA_HEALTH && DATA_HEALTH[currentSymbol] && DATA_HEALTH[currentSymbol][currentTF] && DATA_HEALTH[currentSymbol][currentTF].end) ? DATA_HEALTH[currentSymbol][currentTF].end : "";
      const warns = (DATA_HEALTH && DATA_HEALTH[currentSymbol] && DATA_HEALTH[currentSymbol][currentTF] && Array.isArray(DATA_HEALTH[currentSymbol][currentTF].warnings)) ? DATA_HEALTH[currentSymbol][currentTF].warnings : [];

      const sc = (SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[currentSymbol] && SCREENER.by_symbol[currentSymbol][currentTF]) ? SCREENER.by_symbol[currentSymbol][currentTF] : null;
      const trendScore = sc ? sc.trend_score : "";
      const breakoutScore = sc ? sc.breakout_score : "";

      const name = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[currentSymbol]) ? String(SYMBOL_DISPLAY[currentSymbol]) : "";
      const label = `${(name || currentSymbol)} (${currentSymbol})`;

      if (DOM.status) DOM.status.textContent = `${label} | ${currentTF} | ${currentGroup} | bars: ${bars} | last: ${end}`;

      // Stock title
      if (DOM.stockTitle) {
        const _dn = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[currentSymbol]) ? String(SYMBOL_DISPLAY[currentSymbol]) : "";
        DOM.stockTitle.innerHTML = _dn ? `${_dn} <span class="st-sub">(${currentSymbol}) — ${currentTF}</span>` : `${currentSymbol} <span class="st-sub">— ${currentTF}</span>`;
      }

      // Signal summary card
      const card = DOM.signalCard;
      if (card && sc) {
        const bullD = sc.dim_bull || {};
        const bearD = sc.dim_bear || {};
        let bullTotal = 0, bearTotal = 0;
        for (const k of Object.keys(bullD)) bullTotal += (bullD[k] || 0);
        for (const k of Object.keys(bearD)) bearTotal += (bearD[k] || 0);
        const freshData = _freshness(end);
        const freshBuild = _freshness(built);
        let comboHtml = "";
        if (sc.combo_4) comboHtml = `<span class="sc-combo combo-4">C4</span>`;
        else if (sc.combo_3) comboHtml = `<span class="sc-combo combo-3">C3</span>`;
        let actionHtml = "";
        const act = sc.signal_action || "";
        if (act.startsWith("ENTRY")) actionHtml = `<span class="sc-action sc-action-entry">${act}</span>`;
        else if (act.startsWith("SCALE")) actionHtml = `<span class="sc-action sc-action-scale">\u25B2 1.5x</span>`;
        else if (act === "HOLD") {
          let holdInfo = "HOLD";
          if (sc.bars_held != null) holdInfo += ` ${sc.bars_held}b`;
          if (sc.atr_stop != null) holdInfo += ` stop:${sc.atr_stop}`;
          actionHtml = `<span class="sc-action sc-action-hold">${holdInfo}</span>`;
        }
        else if (act.startsWith("EXIT") || (act === "FLAT" && sc.last_exit_bars_ago != null && sc.last_exit_bars_ago <= 2)) {
          const eb = sc.last_exit_bars_ago;
          const label = (eb != null && eb > 0) ? `EXIT ${eb}b` : "EXIT";
          const reason = sc.last_exit_reason ? ` (${sc.last_exit_reason})` : "";
          actionHtml = `<span class="sc-action sc-action-exit" title="${label}${reason}">${label}</span>`;
        }
        else if (act === "FLAT" && sc.last_exit_bars_ago != null) {
          actionHtml = `<span class="sc-action" style="color:var(--muted);font-size:10px;">exit ${sc.last_exit_bars_ago}b ago: ${sc.last_exit_reason || ""}</span>`;
        }
        card.innerHTML =
          `<span class="sc-label">Trend</span>` +
          `<span class="sc-score ${Number(trendScore) > 0 ? 'sc-pos' : Number(trendScore) < 0 ? 'sc-neg' : 'sc-zero'}"> ${trendScore}</span>` +
          comboHtml + actionHtml +
          `<span class="sc-label">Breakout</span>` +
          `<span class="sc-score ${Number(breakoutScore) > 0 ? 'sc-pos' : Number(breakoutScore) < 0 ? 'sc-neg' : 'sc-zero'}"> ${breakoutScore}</span>` +
          `<span class="sc-bull">\u25B2 ${bullTotal}</span>` +
          `<span class="sc-bear">\u25BC ${bearTotal}</span>` +
          `<span class="sc-freshness">Data: ${freshData} &middot; Built: ${freshBuild}</span>`;
      } else if (card) {
        card.innerHTML = "";
      }

      const dw = DOM.dataWarn;
      if (dw) {
        if (warns && warns.length) {
          dw.style.display = "block";
          dw.textContent = "Data health warnings:\n- " + warns.join("\n- ");
        } else {
          dw.style.display = "none";
          dw.textContent = "";
        }
      }
    }

    function indicatorKeyForTrace(tr) {
      if (!tr) return "Other";
      if (tr.meta && tr.meta.indicator) return tr.meta.indicator;
      return "Other";
    }

    function buildIndicatorPanel(fig) {
      const wrap = DOM.indicatorStrip;
      if (!wrap) return;
      wrap.innerHTML = "";
      indicatorKeys = [];
      if (!fig || !fig.data) return;

      // Combo KPIs for golden highlight
      const meta = (fig.layout && fig.layout.meta) || {};
      const comboKpis = new Set([...(meta.combo_3_kpis || []), ...(meta.combo_4_kpis || [])]);

      const chartsExclude = new Set(["Price", "KPI Trend", "KPI Breakout", "TrendScore", "P&L", "Combo Signal"]);
      const traceKeys = new Set();
      fig.data.forEach(tr => {
        const k = indicatorKeyForTrace(tr);
        if (!k || chartsExclude.has(k)) return;
        const xa = tr.xaxis || "x";
        const axNum = parseInt((xa.replace("x", "") || "1"), 10);
        if (axNum === 1 || axNum === 3) traceKeys.add(k);
      });
      const plottableKeys = new Set(traceKeys);

      // Strategy filter: if a strategy is active, show only its KPIs
      const heatmapOnlyKeys = new Set();
      const stratKpis = (typeof _getStrategyKpis === "function") ? _getStrategyKpis() : null;
      if (stratKpis) {
        const allowed = new Set(stratKpis);
        plottableKeys.forEach(k => { if (!allowed.has(k)) plottableKeys.delete(k); });
        const rawPayload = fig._rawPayload || {};
        const payloadKpiNames = (rawPayload.kpi && rawPayload.kpi.kpis) ? new Set(rawPayload.kpi.kpis) : new Set();
        stratKpis.forEach(k => {
          if (payloadKpiNames.has(k)) {
            plottableKeys.add(k);
            if (!traceKeys.has(k)) heatmapOnlyKeys.add(k);
          }
        });
      }

      const keys = new Set(plottableKeys);
      indicatorKeys = Array.from(keys);

      // Group indicators by dimension
      const grouped = {};
      const otherKey = "Other";
      indicatorKeys.forEach(k => {
        const dim = (DIMENSION_MAP && DIMENSION_MAP[k]) ? DIMENSION_MAP[k] : otherKey;
        if (!grouped[dim]) grouped[dim] = [];
        grouped[dim].push(k);
      });
      // Sort within each group by KPI_KEYS order (= bar chart order)
      const _kpiIdx = {};
      (KPI_KEYS || []).forEach((k, i) => { _kpiIdx[k] = i; });
      Object.values(grouped).forEach(arr => arr.sort((a, b) => {
        const ia = (_kpiIdx[a] !== undefined) ? _kpiIdx[a] : 9999;
        const ib = (_kpiIdx[b] !== undefined) ? _kpiIdx[b] : 9999;
        if (ia !== ib) return ia - ib;
        return displayIndicatorKey(a).localeCompare(displayIndicatorKey(b));
      }));

      // Ordered dimension groups: follow DIMENSION_ORDER, then any leftover
      const dimOrder = (typeof DIMENSION_ORDER !== "undefined" && Array.isArray(DIMENSION_ORDER)) ? DIMENSION_ORDER : [];
      const orderedDims = [];
      dimOrder.forEach(d => { if (grouped[d]) orderedDims.push(d); });
      Object.keys(grouped).forEach(d => { if (!orderedDims.includes(d)) orderedDims.push(d); });

      // Flatten for stable ordering
      indicatorKeys = [];
      orderedDims.forEach(d => { (grouped[d] || []).forEach(k => indicatorKeys.push(k)); });

      // Get KPI states for current symbol+TF to color chips
      const sc = (SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[currentSymbol] && SCREENER.by_symbol[currentSymbol][currentTF]) ? SCREENER.by_symbol[currentSymbol][currentTF] : null;
      const kpiSt = (sc && sc.kpi_states) ? sc.kpi_states : {};

      orderedDims.forEach((dim, di) => {
        const items = grouped[dim] || [];
        if (!items.length) return;
        if (di > 0) {
          const sep = document.createElement("div");
          sep.className = "dim-sep";
          wrap.appendChild(sep);
        }
        const groupDiv = document.createElement("div");
        groupDiv.className = "dim-group";
        const hdr = document.createElement("div");
        hdr.className = "dim-header";
        hdr.textContent = dim;
        groupDiv.appendChild(hdr);
        const chipsDiv = document.createElement("div");
        chipsDiv.className = "dim-chips";
        items.forEach(k => {
          const label = document.createElement("label");
          label.className = "chip";
          const isCombo = comboKpis.has(k);
          const isHeatmapOnly = heatmapOnlyKeys.has(k);
          if (isCombo) label.classList.add("combo-kpi");
          if (isHeatmapOnly) label.classList.add("heatmap-only");
          const cb = document.createElement("input");
          cb.type = "checkbox";
          if (isHeatmapOnly) {
            cb.checked = true;
            cb.disabled = true;
            label.classList.add("on");
          } else {
            cb.checked = selectedIndicators.has(k);
            label.classList.toggle("on", cb.checked);
          }

          const kState = (kpiSt[k] !== undefined) ? kpiSt[k] : -2;
          function _applyChipStyle() {
            if (isCombo) return;
            const on = cb.checked;
            if (kState === 1) {
              label.style.borderColor = on ? "var(--bull-br)" : "";
              label.style.background = on ? "var(--bull-bg)" : "";
            } else if (kState === -1) {
              label.style.borderColor = on ? "var(--bear-br)" : "";
              label.style.background = on ? "var(--bear-bg)" : "";
            } else {
              label.style.borderColor = "";
              label.style.background = "";
            }
          }
          _applyChipStyle();

          if (!isHeatmapOnly) {
            cb.addEventListener("change", () => {
              if (cb.checked) selectedIndicators.add(k);
              else selectedIndicators.delete(k);
              label.classList.toggle("on", cb.checked);
              _applyChipStyle();
              saveState({ indicators: Array.from(selectedIndicators) });
              applyIndicatorVisibility();
            });
          }
          const dot = document.createElement("span");
          dot.style.cssText = "display:inline-block;width:6px;height:6px;border-radius:50%;flex-shrink:0;";
          if (kState === 1) dot.style.background = "var(--candle-up)";
          else if (kState === -1) dot.style.background = "var(--candle-down)";
          else dot.style.background = "var(--border-strong)";
          label.appendChild(dot);

          const span = document.createElement("span");
          span.textContent = displayIndicatorKey(k);
          const help = (INDICATOR_HELP && INDICATOR_HELP[k]) ? INDICATOR_HELP[k] : "";
          if (help) label.title = isCombo ? ("★ Combo KPI" + (help ? " — " + help : "")) : help;
          label.appendChild(cb);
          label.appendChild(span);
          chipsDiv.appendChild(label);
        });
        groupDiv.appendChild(chipsDiv);
        wrap.appendChild(groupDiv);
      });

      // "Clear" button to deactivate all indicators
      const clearBtn = document.createElement("button");
      clearBtn.className = "chip clear-btn";
      clearBtn.textContent = "Clear";
      clearBtn.title = "Deactivate all indicators";
      clearBtn.addEventListener("click", () => {
        selectedIndicators.clear();
        saveState({ indicators: [] });
        buildIndicatorPanel(fig);
        applyIndicatorVisibility();
      });
      wrap.appendChild(clearBtn);
    }

    let sidebarSortMode = _st0.sidebarSort || "name";
    function _getSymSortVal(sym) {
      const sc = (SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[sym] && SCREENER.by_symbol[sym][currentTF]) ? SCREENER.by_symbol[sym][currentTF] : null;
      if (sidebarSortMode === "delta") return sc ? (sc.delta_pct ?? -999) : -999;
      if (sidebarSortMode === "trend") return sc ? (sc.trend_score ?? -999) : -999;
      return 0;
    }
    function buildSymbolList() {
      const wrap = document.getElementById("symbolList");
      if (!wrap) return;
      wrap.innerHTML = "";
      const q = (document.getElementById("symbolListSearch")?.value || "").trim().toUpperCase();
      const allowed = getAllowedSymbolsSet();
      const isComboFilter = sidebarSortMode === "combo";
      const items = SYMBOLS.filter(s => {
        if (!allowed.has(s)) return false;
        const name = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[s]) ? String(SYMBOL_DISPLAY[s]) : "";
        if (q && !(s.includes(q) || name.toUpperCase().includes(q))) return false;
        if (isComboFilter) {
          const sc = (SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[s] && SCREENER.by_symbol[s][currentTF]) ? SCREENER.by_symbol[s][currentTF] : null;
          if (!sc || !(sc.combo_3 || sc.combo_4)) return false;
        }
        return true;
      });

      if (sidebarSortMode === "name") {
        items.sort((a, b) => {
          const na = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[a]) ? String(SYMBOL_DISPLAY[a]) : a;
          const nb = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[b]) ? String(SYMBOL_DISPLAY[b]) : b;
          return na.localeCompare(nb);
        });
      } else {
        items.sort((a, b) => _getSymSortVal(b) - _getSymSortVal(a));
      }

      if (!items.length) {
        const empty = document.createElement("div");
        empty.style.cssText = "padding:24px 12px;text-align:center;color:var(--muted);font-size:12px;";
        empty.textContent = currentGroup && currentGroup !== "all" ? "No symbols in this group." : "No symbols match your search.";
        wrap.appendChild(empty);
        return;
      }
      items.forEach(sym => {
        const row = document.createElement("div");
        row.className = "symRow";
        if (sym === currentSymbol) row.classList.add("active");

        const left = document.createElement("div");
        left.style.minWidth = "0";
        const name = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[sym]) ? String(SYMBOL_DISPLAY[sym]) : "";
        const titleRow = document.createElement("div");
        titleRow.style.cssText = "display:flex;align-items:center;gap:4px;";
        const title = document.createElement("div");
        title.className = "symName";
        title.textContent = name || sym;
        title.title = `${name || sym} (${sym})`;
        titleRow.appendChild(title);

        const sc = (SCREENER && SCREENER.by_symbol && SCREENER.by_symbol[sym] && SCREENER.by_symbol[sym][currentTF]) ? SCREENER.by_symbol[sym][currentTF] : null;
        if (sc && (sc.combo_3 || sc.combo_4)) {
          const cbLabel = sc.combo_4 ? "C4" : "C3";
          const cbCls = sc.combo_4 ? "combo-4" : "combo-3";
          const cbBadge = document.createElement("span");
          cbBadge.className = "combo-badge " + cbCls;
          cbBadge.style.fontSize = "8px";
          cbBadge.style.padding = "0 4px";
          cbBadge.style.flexShrink = "0";
          cbBadge.textContent = cbLabel;
          titleRow.appendChild(cbBadge);
        }
        left.appendChild(titleRow);

        const d = sc ? sc.delta_pct : null;

        // Sparkline + delta on a single compact line
        const sparkRow = document.createElement("div");
        sparkRow.style.cssText = "display:flex;align-items:center;gap:4px;margin-top:1px;";
        const spark = (sc && Array.isArray(sc.spark) && sc.spark.length >= 2) ? sc.spark : null;
        if (spark) {
          const w = 40, h = 12;
          const pts = spark.map((v, i) => `${(i / (spark.length - 1)) * w},${h - v * h}`).join(" ");
          const clr = (d != null && d >= 0) ? "var(--candle-up)" : "var(--candle-down)";
          const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
          svg.setAttribute("width", w);
          svg.setAttribute("height", h);
          svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
          svg.style.flexShrink = "0";
          const pl = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
          pl.setAttribute("points", pts);
          pl.setAttribute("fill", "none");
          pl.setAttribute("stroke", clr);
          pl.setAttribute("stroke-width", "1.5");
          svg.appendChild(pl);
          sparkRow.appendChild(svg);
        }
        const deltaSpan = document.createElement("span");
        deltaSpan.className = "symDelta " + deltaClass(d);
        deltaSpan.textContent = formatDeltaPct(d);
        sparkRow.appendChild(deltaSpan);
        left.appendChild(sparkRow);

        row.appendChild(left);

        row.addEventListener("click", () => {
          currentSymbol = sym;
          saveState({ symbol: currentSymbol });
          pushHashState();
          buildSymbolList();
          if (DOM.chartUpper && DOM.chartUpper.style.display !== "none") renderChart();
          setStatus();
        });

        row.addEventListener("contextmenu", (e) => {
          e.preventDefault();
          _showMoveMenu(e, sym);
        });

        wrap.appendChild(row);
      });
      // Mark active sort button
      document.querySelectorAll("#sidebarSort .btn").forEach(b => {
        b.classList.toggle("active", b.dataset.sort === sidebarSortMode);
      });
    }
    document.querySelectorAll("#sidebarSort .btn").forEach(b => {
      b.addEventListener("click", () => {
        sidebarSortMode = b.dataset.sort || "name";
        saveState({ sidebarSort: sidebarSortMode });
        buildSymbolList();
      });
    });

    // --- Move-between-groups system ---
    let _pendingMoves = JSON.parse(localStorage.getItem(("td_pending_moves" + _LS_SUFFIX)) || "[]");
    function _savePendingMoves() { localStorage.setItem(("td_pending_moves" + _LS_SUFFIX), JSON.stringify(_pendingMoves)); _updateMoveBadge(); }

    function _findSymbolGroups(sym) {
      const groups = [];
      for (const k of GROUP_KEYS) {
        const arr = SYMBOL_GROUPS[k];
        if (arr && arr.map(s => s.toUpperCase()).includes(sym.toUpperCase())) groups.push(k);
      }
      return groups;
    }

    function _getEffectiveGroups() {
      const eff = {};
      for (const k of GROUP_KEYS) {
        eff[k] = new Set((SYMBOL_GROUPS[k] || []).map(s => s.toUpperCase()));
      }
      for (const m of _pendingMoves) {
        const sym = m.symbol.toUpperCase();
        if (m.from && eff[m.from]) eff[m.from].delete(sym);
        if (m.to && eff[m.to]) eff[m.to].add(sym);
      }
      return eff;
    }

    function _showMoveMenu(e, sym) {
      const existing = document.getElementById("_moveMenu");
      if (existing) existing.remove();

      const currentGroups = _findSymbolGroups(sym);
      const applied = _pendingMoves.filter(m => m.symbol.toUpperCase() === sym.toUpperCase());
      applied.forEach(m => {
        const fi = currentGroups.indexOf(m.from);
        if (fi >= 0) currentGroups.splice(fi, 1);
        if (m.to && !currentGroups.includes(m.to)) currentGroups.push(m.to);
      });

      const menu = document.createElement("div");
      menu.id = "_moveMenu";
      menu.style.cssText = "position:fixed;z-index:9999;background:var(--panel);border:1px solid var(--border-strong);border-radius:8px;padding:6px 0;min-width:180px;box-shadow:0 4px 16px var(--shadow-overlay);font-size:12px;";
      menu.style.left = Math.min(e.clientX, window.innerWidth - 200) + "px";
      menu.style.top = Math.min(e.clientY, window.innerHeight - 250) + "px";

      const header = document.createElement("div");
      header.style.cssText = "padding:6px 12px;font-weight:700;color:var(--fg);border-bottom:1px solid var(--border);margin-bottom:4px;font-size:11px;";
      const dispName = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[sym]) ? SYMBOL_DISPLAY[sym] : sym;
      header.textContent = "Move " + dispName;
      menu.appendChild(header);

      if (currentGroups.length) {
        const inLabel = document.createElement("div");
        inLabel.style.cssText = "padding:2px 12px;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;";
        inLabel.textContent = "Currently in: " + currentGroups.map(g => _groupLabel(g)).join(", ");
        menu.appendChild(inLabel);
      }

      const sep = document.createElement("div");
      sep.style.cssText = "border-top:1px solid var(--border);margin:4px 0;";
      menu.appendChild(sep);

      const targetGroups = GROUP_KEYS.filter(k => !currentGroups.includes(k));
      if (!targetGroups.length) {
        const noOpt = document.createElement("div");
        noOpt.style.cssText = "padding:6px 12px;color:var(--muted);font-style:italic;";
        noOpt.textContent = "Already in all groups";
        menu.appendChild(noOpt);
      } else {
        targetGroups.forEach(target => {
          const item = document.createElement("div");
          item.style.cssText = "padding:6px 12px;cursor:pointer;color:var(--fg);display:flex;align-items:center;gap:6px;";
          item.innerHTML = '<span style="font-size:13px;">&#8594;</span> <span>' + _groupLabel(target) + '</span>';
          item.addEventListener("mouseenter", () => { item.style.background = "var(--active-bg)"; });
          item.addEventListener("mouseleave", () => { item.style.background = ""; });
          item.addEventListener("click", () => {
            const fromGroup = currentGroups.length ? currentGroups[0] : "unknown";
            _pendingMoves.push({ symbol: sym.toUpperCase(), from: fromGroup, to: target, displayName: dispName });

            if (SYMBOL_GROUPS[fromGroup]) {
              SYMBOL_GROUPS[fromGroup] = SYMBOL_GROUPS[fromGroup].filter(s => s.toUpperCase() !== sym.toUpperCase());
            }
            if (!SYMBOL_GROUPS[target]) SYMBOL_GROUPS[target] = [];
            if (!SYMBOL_GROUPS[target].map(s => s.toUpperCase()).includes(sym.toUpperCase())) {
              SYMBOL_GROUPS[target].push(sym.toUpperCase());
            }

            _savePendingMoves();
            buildGroupTabs();
            buildSymbolList();
            if (currentTab === "screener") buildScreener();
            menu.remove();
          });
          menu.appendChild(item);
        });
      }

      document.body.appendChild(menu);
      const _dismiss = (ev) => { if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener("click", _dismiss); } };
      setTimeout(() => document.addEventListener("click", _dismiss), 10);
    }

    function _updateMoveBadge() {
      let badge = document.getElementById("_moveBadge");
      if (!_pendingMoves.length) {
        if (badge) badge.style.display = "none";
        return;
      }
      if (!badge) {
        badge = document.createElement("div");
        badge.id = "_moveBadge";
        badge.style.cssText = "position:fixed;bottom:16px;right:16px;z-index:9998;background:var(--warning);color:#000;border-radius:8px;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer;box-shadow:0 2px 8px var(--shadow-overlay);display:flex;align-items:center;gap:8px;";
        badge.addEventListener("click", _showPendingMovesPanel);
        document.body.appendChild(badge);
      }
      badge.style.display = "flex";
      badge.innerHTML = '<span>' + _pendingMoves.length + ' pending move' + (_pendingMoves.length > 1 ? 's' : '') + '</span><span style="font-size:10px;opacity:0.7;">Click to review</span>';
    }

    function _showPendingMovesPanel() {
      const existing = document.getElementById("_movesPanel");
      if (existing) { existing.remove(); return; }

      const panel = document.createElement("div");
      panel.id = "_movesPanel";
      panel.style.cssText = "position:fixed;bottom:56px;right:16px;z-index:9999;background:var(--panel);border:1px solid var(--border-strong);border-radius:10px;padding:12px;min-width:280px;max-width:380px;max-height:50vh;overflow-y:auto;box-shadow:0 4px 20px var(--shadow-overlay);font-size:12px;";

      const title = document.createElement("div");
      title.style.cssText = "font-weight:700;font-size:13px;margin-bottom:8px;color:var(--fg);";
      title.textContent = "Pending Moves";
      panel.appendChild(title);

      _pendingMoves.forEach((m, i) => {
        const row = document.createElement("div");
        row.style.cssText = "display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border);";
        row.innerHTML = '<span style="flex:1;"><b>' + (m.displayName || m.symbol) + '</b> <span style="color:var(--muted);">' + m.from + ' &#8594; ' + m.to + '</span></span>';
        const undo = document.createElement("button");
        undo.className = "btn-subtle";
        undo.textContent = "Undo";
        undo.style.cssText = "padding:2px 8px;font-size:10px;";
        undo.addEventListener("click", () => {
          if (SYMBOL_GROUPS[m.to]) {
            SYMBOL_GROUPS[m.to] = SYMBOL_GROUPS[m.to].filter(s => s.toUpperCase() !== m.symbol.toUpperCase());
          }
          if (!SYMBOL_GROUPS[m.from]) SYMBOL_GROUPS[m.from] = [];
          if (!SYMBOL_GROUPS[m.from].map(s => s.toUpperCase()).includes(m.symbol.toUpperCase())) {
            SYMBOL_GROUPS[m.from].push(m.symbol.toUpperCase());
          }
          _pendingMoves.splice(i, 1);
          _savePendingMoves();
          buildGroupTabs(); buildSymbolList();
          if (currentTab === "screener") buildScreener();
          panel.remove();
          if (_pendingMoves.length) _showPendingMovesPanel();
        });
        row.appendChild(undo);
        panel.appendChild(row);
      });

      const actions = document.createElement("div");
      actions.style.cssText = "display:flex;gap:8px;margin-top:10px;";

      const exportBtn = document.createElement("button");
      exportBtn.className = "btn-subtle";
      exportBtn.style.cssText = "flex:1;padding:6px;font-weight:700;background:var(--success);color:#fff;border-color:var(--success);";
      exportBtn.textContent = "Export CSVs";
      exportBtn.addEventListener("click", () => { _exportUpdatedCSVs(); });
      actions.appendChild(exportBtn);

      const copyBtn = document.createElement("button");
      copyBtn.className = "btn-subtle";
      copyBtn.style.cssText = "flex:1;padding:6px;font-weight:700;";
      copyBtn.textContent = "Copy CLI";
      copyBtn.addEventListener("click", () => {
        const cmds = _pendingMoves.map(m => "python3 -m apps.dashboard.build_dashboard --move " + m.symbol + " " + m.from + " " + m.to);
        navigator.clipboard.writeText(cmds.join("\n")).then(() => {
          copyBtn.textContent = "Copied!";
          setTimeout(() => { copyBtn.textContent = "Copy CLI"; }, 1500);
        });
      });
      actions.appendChild(copyBtn);

      const clearBtn = document.createElement("button");
      clearBtn.className = "btn-subtle";
      clearBtn.style.cssText = "padding:6px 10px;";
      clearBtn.textContent = "Clear";
      clearBtn.addEventListener("click", () => {
        _pendingMoves = [];
        _savePendingMoves();
        panel.remove();
      });
      actions.appendChild(clearBtn);

      panel.appendChild(actions);

      const hint = document.createElement("div");
      hint.style.cssText = "margin-top:8px;font-size:10px;color:var(--muted);line-height:1.4;";
      hint.textContent = "Export downloads updated CSV files. Alternatively, copy CLI commands to run in terminal for direct file updates.";
      panel.appendChild(hint);

      document.body.appendChild(panel);
    }

    function _exportUpdatedCSVs() {
      const eff = _getEffectiveGroups();
      const zip = [];
      for (const [group, syms] of Object.entries(eff)) {
        const csv = "ticker\n" + Array.from(syms).sort().join("\n") + "\n";
        zip.push({ name: group + ".csv", content: csv });
      }
      zip.forEach(f => {
        const blob = new Blob([f.content], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = f.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
      });
      _pendingMoves = [];
      _savePendingMoves();
      const panel = document.getElementById("_movesPanel");
      if (panel) panel.remove();
    }

    function _moveStock(sym, fromGroup, toGroup) {
      const dispName = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[sym]) ? SYMBOL_DISPLAY[sym] : sym;

      // Try API first (works when served via serve_dashboard.py)
      fetch("/api/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: sym, from: fromGroup, to: toGroup }),
      }).then(res => res.json()).then(envelope => {
        var data = (envelope && envelope.data !== undefined) ? envelope.data : envelope;
        var ok = envelope && envelope.ok;
        if (ok) {
          // API saved to CSV — update in-memory groups
          if (SYMBOL_GROUPS[fromGroup]) {
            SYMBOL_GROUPS[fromGroup] = SYMBOL_GROUPS[fromGroup].filter(s => s.toUpperCase() !== sym);
          }
          if (!SYMBOL_GROUPS[toGroup]) SYMBOL_GROUPS[toGroup] = [];
          if (!SYMBOL_GROUPS[toGroup].map(s => s.toUpperCase()).includes(sym)) {
            SYMBOL_GROUPS[toGroup].push(sym);
          }
          _showToast(dispName + " moved to " + toGroup.replace(/\b\w/g, c => c.toUpperCase()) + " (saved)");
          buildGroupTabs(); buildSymbolList();
          if (currentTab === "screener") buildScreener();
        } else {
          _showToast("Error: " + ((envelope && envelope.error) || (data && data.error) || "Move failed"), true);
        }
      }).catch(() => {
        // Offline / file:// mode — use pending moves + localStorage
        _pendingMoves.push({ symbol: sym, from: fromGroup, to: toGroup, displayName: dispName });
        if (SYMBOL_GROUPS[fromGroup]) {
          SYMBOL_GROUPS[fromGroup] = SYMBOL_GROUPS[fromGroup].filter(s => s.toUpperCase() !== sym);
        }
        if (!SYMBOL_GROUPS[toGroup]) SYMBOL_GROUPS[toGroup] = [];
        if (!SYMBOL_GROUPS[toGroup].map(s => s.toUpperCase()).includes(sym)) {
          SYMBOL_GROUPS[toGroup].push(sym);
        }
        _savePendingMoves();
        _showToast(dispName + " moved to " + toGroup.replace(/\b\w/g, c => c.toUpperCase()) + " (pending save)");
        buildGroupTabs(); buildSymbolList();
        if (currentTab === "screener") buildScreener();
      });
    }

    function _deleteStock(sym, group) {
      const dispName = (SYMBOL_DISPLAY && SYMBOL_DISPLAY[sym]) ? SYMBOL_DISPLAY[sym] : sym;
      if (!confirm("Delete " + dispName + " from " + (group || "all groups") + "?\nThis also removes enriched data files.")) return;
      fetch("/api/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: sym, group: group || "" }),
      }).then(function(res) { return res.json(); }).then(function(envelope) {
        var data = (envelope && envelope.data !== undefined) ? envelope.data : envelope;
        var ok = envelope && envelope.ok;
        if (ok) {
          if (data && data.groups) { Object.keys(SYMBOL_GROUPS).forEach(function(k) { delete SYMBOL_GROUPS[k]; }); Object.assign(SYMBOL_GROUPS, data.groups); }
          const idx = SYMBOLS.indexOf(sym);
          if (idx >= 0 && (!data || !data.still_in_groups || !data.still_in_groups.length)) SYMBOLS.splice(idx, 1);
          if (SCREENER && SCREENER.by_symbol) delete SCREENER.by_symbol[sym];
          if (SCREENER && SCREENER.rows_by_tf) {
            Object.keys(SCREENER.rows_by_tf).forEach(function(tf) {
              SCREENER.rows_by_tf[tf] = SCREENER.rows_by_tf[tf].filter(function(r) { return (r.symbol || "").toUpperCase() !== sym; });
            });
          }
          _showToast(dispName + " deleted" + (data && data.purged_files ? " (" + data.purged_files + " files purged)" : "") + " \u2713");
          buildGroupTabs(); buildSymbolList();
          if (currentTab === "screener") buildScreener();
        } else {
          _showToast("Error: " + ((envelope && envelope.error) || (data && data.error) || "Delete failed"), true);
        }
      }).catch(function() {
        _showToast("Delete requires server mode (serve_dashboard.py)", true);
      });
    }

    function _showToast(msg, isError) {
      const t = document.createElement("div");
      t.style.cssText = "position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:9999;padding:10px 20px;border-radius:8px;font-size:12px;font-weight:600;box-shadow:0 2px 12px var(--shadow-overlay);transition:opacity 0.3s;";
      t.style.background = isError ? "var(--danger)" : "var(--success)";
      t.style.color = "#fff";
      t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, 2500);
    }

    _updateMoveBadge();

    function applyIndicatorVisibility() {
      if (!currentFig || !currentFig.data) return;
      const upperVis = [];
      const pnlVis = [];
      const oscVis = [];
      const tsVis = [];
      const lowerVis = [];
      for (const tr of currentFig.data) {
        const k = indicatorKeyForTrace(tr);
        const alwaysOn = (k === "Price" || k === "P&L" || k === "KPI Trend" || k === "KPI Breakout" || k === "TrendScore" || k === "Combo Signal");
        const show = (alwaysOn || selectedIndicators.has(k));
        const xa = tr.xaxis || "x";
        const axNum = parseInt((xa.replace("x", "") || "1"), 10);
        if (axNum === 2) pnlVis.push(show);
        else if (axNum === 3) oscVis.push(show);
        else if (axNum === 4) tsVis.push(show);
        else if (axNum >= 5) lowerVis.push(show);
        else upperVis.push(show);
      }
      try { Plotly.restyle("chartUpper", { visible: upperVis }); } catch (e) {}
      try { Plotly.restyle("chartPnl", { visible: pnlVis }); } catch (e) {}
      try { Plotly.restyle("chartOsc", { visible: oscVis }); } catch (e) {}
      try { Plotly.restyle("chartTs", { visible: tsVis }); } catch (e) {}
      try { Plotly.restyle("chartLower", { visible: lowerVis }); } catch (e) {}
    }

    function _loadScriptOnce(src) {
      return new Promise((resolve, reject) => {
        if (!src) return reject(new Error("missing src"));
        const id = "asset_" + src.replace(/[^a-zA-Z0-9_]/g, "_");
        if (document.getElementById(id)) return resolve(true);
        const s = document.createElement("script");
        s.id = id;
        s.src = src;
        s.async = true;
        s.onload = () => resolve(true);
        s.onerror = () => reject(new Error("failed to load " + src));
        document.head.appendChild(s);
      });
    }

    function _maybeBuildFig(payload) {
      if (payload && payload.c && payload.x && typeof window.buildFigureFromData === "function") {
        const fig = window.buildFigureFromData(payload);
        fig._rawPayload = payload;
        return fig;
      }
      return payload;
    }

    async function loadFig(symbol, tf) {
      const key = symbol + "|" + tf;
      if (figCache[key]) return figCache[key];

      function _isEmptyFig(raw) {
        return raw && Array.isArray(raw.data) && raw.data.length === 0;
      }

      if (FIG_SOURCE === "static_js") {
        const symDir = (SYMBOL_TO_ASSET && SYMBOL_TO_ASSET[symbol]) ? SYMBOL_TO_ASSET[symbol] : symbol;
        const src = `${ASSETS_DIR}/${encodeURIComponent(symDir)}/${encodeURIComponent(tf)}.js`;
        try {
          await _loadScriptOnce(src);
          const store = window.TD_ASSET_PAYLOADS || {};
          const raw = store[key];
          if (!raw || _isEmptyFig(raw)) { figCache[key] = null; return null; }
          const fig = _maybeBuildFig(raw);
          figCache[key] = fig;
          return fig;
        } catch (e) {
          figCache[key] = null;
          return null;
        }
      }

      // Fallback fetch modes (requires serving over http)
      let url = "";
      if (FIG_SOURCE === "server") {
        url = `/fig?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(tf)}`;
      } else {
        const symDir = (SYMBOL_TO_ASSET && SYMBOL_TO_ASSET[symbol]) ? SYMBOL_TO_ASSET[symbol] : symbol;
        url = `${ASSETS_DIR}/${encodeURIComponent(symDir)}/${encodeURIComponent(tf)}.json`;
      }

      try {
        const r = await fetch(url, { cache: "no-store" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const raw = await r.json();
        if (_isEmptyFig(raw)) { figCache[key] = null; return null; }
        const fig = _maybeBuildFig(raw);
        figCache[key] = fig;
        return fig;
      } catch (e) {
        figCache[key] = null;
        return null;
      }
    }

    // --- Synchronized crosshair across upper + lower charts ---
    let _crosshairBaseShapesUp = [];
    let _crosshairBaseShapesLo = [];
    // Lightweight CSS crosshair (no Plotly.relayout calls)
    function _ensureCrosshairLine(container) {
      let line = container.querySelector(".crosshair-line");
      if (!line) {
        line = document.createElement("div");
        line.className = "crosshair-line";
        container.style.position = "relative";
        container.appendChild(line);
      }
      return line;
    }
    function _setCrosshair(xval, source) {
      ["chartUpper", "chartPnl", "chartOsc", "chartTs", "chartLower"].forEach(id => {
        const gd = document.getElementById(id);
        if (!gd || !gd._fullLayout) return;
        const xa = gd._fullLayout.xaxis;
        if (!xa || !xa.d2p) return;
        const px = xa.l2p(xa.d2c(xval)) + (xa._offset || 0);
        const line = _ensureCrosshairLine(gd);
        line.style.left = px + "px";
        line.style.display = "block";
      });
    }
    function _clearCrosshair() {
      ["chartUpper", "chartPnl", "chartOsc", "chartTs", "chartLower"].forEach(id => {
        const gd = document.getElementById(id);
        if (!gd) return;
        const line = gd.querySelector(".crosshair-line");
        if (line) line.style.display = "none";
      });
    }
    function attachCrosshair(baseShapes) {
      _crosshairBaseShapesUp = Array.isArray(baseShapes) ? baseShapes.slice() : [];
      _crosshairBaseShapesLo = [];
      const gdUp = DOM.chartUpper;
      const gdPnl = document.getElementById("chartPnl");
      const gdOsc = document.getElementById("chartOsc");
      const gdTs = document.getElementById("chartTs");
      const gdLo = DOM.chartLower;
      const _wire = (gd) => {
        if (!gd || !gd.on) return;
        try {
          if (typeof gd.removeAllListeners === "function") {
            gd.removeAllListeners("plotly_hover");
            gd.removeAllListeners("plotly_unhover");
          }
        } catch (e) {}
        gd.on("plotly_hover", (ev) => {
          try {
            if (ev && ev.points && ev.points.length) {
              _setCrosshair(ev.points[0].x, gd.id);
            }
          } catch (e) {}
        });
        gd.on("plotly_unhover", () => { _clearCrosshair(); _hideComboTooltip(); });
      };
      _wire(gdUp);
      _wire(gdPnl);
      _wire(gdOsc);
      _wire(gdTs);
      _wire(gdLo);
      // Apply base shapes once (no relayout on hover)
      try {
        if (gdUp && gdUp.data && _crosshairBaseShapesUp.length) Plotly.relayout(gdUp, { shapes: _crosshairBaseShapesUp });
      } catch (e) {}
    }

    function splitFigure(fig) {
      const data = fig.data || [];
      const layout = fig.layout || {};
      function cleanAxis(src, overrides) {
        const ax = Object.assign({}, src || {}, overrides);
        delete ax.matches;
        delete ax.scaleanchor;
        delete ax.overlaying;
        return ax;
      }
      const upperData = [];
      const pnlData = [];
      const oscData = [];
      const tsData = [];
      const lowerData = [];

      // Row 1 → upper, row 2 → pnl, row 3 → osc, row 4 → ts, rows 5-6 → lower
      const lowerAxisMap = {5: 1, 6: 2};
      for (const tr of data) {
        const xa = tr.xaxis || "x";
        const axNum = parseInt((xa.replace("x", "") || "1"), 10);
        if (axNum >= 5) {
          const newTr = Object.assign({}, tr);
          const newN = lowerAxisMap[axNum] || 1;
          newTr.xaxis = newN === 1 ? "x" : ("x" + newN);
          newTr.yaxis = newN === 1 ? "y" : ("y" + newN);
          lowerData.push(newTr);
        } else if (axNum === 4) {
          const newTr = Object.assign({}, tr);
          newTr.xaxis = "x";
          newTr.yaxis = "y";
          tsData.push(newTr);
        } else if (axNum === 3) {
          const newTr = Object.assign({}, tr);
          newTr.xaxis = "x";
          newTr.yaxis = "y";
          oscData.push(newTr);
        } else if (axNum === 2) {
          const newTr = Object.assign({}, tr);
          newTr.xaxis = "x";
          newTr.yaxis = "y";
          pnlData.push(newTr);
        } else {
          upperData.push(tr);
        }
      }

      // Apply the explicit data range from the figure to all split charts for alignment
      const xRange = (layout.xaxis && layout.xaxis.range) ? layout.xaxis.range : null;
      _initialXRange = xRange;
      const xRangeOvr = xRange ? {range: xRange, autorange: false} : {};

      const allShapes = Array.isArray(layout.shapes) ? layout.shapes : [];
      const upperShapesStrategy = allShapes.filter(s => {
        const xr = s.xref || "";
        const yr = s.yref || "";
        return (xr === "x" || xr === "paper") && (yr === "y" || yr === "y domain" || yr === "paper");
      });
      const upperShapesCharts = upperShapesStrategy.filter(s => !s._strategy);

      // --- Upper layout: row 1 (price only) ---
      const upperLayout = {};
      for (const [k, v] of Object.entries(layout)) {
        if (/^[xy]axis[2-6]$/.test(k)) continue;
        if (k === "annotations" || k === "shapes") continue;
        upperLayout[k] = v;
      }
      upperLayout.shapes = upperShapesStrategy;
      upperLayout.xaxis = Object.assign({}, layout.xaxis || {}, xRange ? {range: xRange, autorange: false} : {});
      upperLayout.yaxis = Object.assign({}, layout.yaxis, {domain: [0, 1]});
      upperLayout.autosize = false;
      upperLayout.height = 500;
      if (layout.annotations) {
        upperLayout.annotations = layout.annotations.filter(a => {
          const xr = a.xref || "x";
          const n = parseInt((xr.replace("x", "") || "1"), 10);
          return n === 1;
        });
      }

      // --- P&L layout: row 2 only ---
      const pnlLayout = {};
      for (const [k, v] of Object.entries(layout)) {
        if (k === "annotations" || k === "title" || k === "shapes") continue;
        if (/^[xy]axis[0-9]*$/.test(k)) continue;
        pnlLayout[k] = v;
      }
      pnlLayout.shapes = [];
      pnlLayout.xaxis = cleanAxis(layout.xaxis2, Object.assign({domain: [0, 1], anchor: "y"}, xRangeOvr));
      pnlLayout.yaxis = cleanAxis(layout.yaxis2, {domain: [0, 1], anchor: "x"});
      pnlLayout.autosize = false;
      pnlLayout.height = 160;
      pnlLayout.margin = Object.assign({}, layout.margin || {}, {t: 6, b: 20});
      if (layout.annotations) {
        pnlLayout.annotations = layout.annotations.filter(a => {
          if (a.xref === "paper" && a.yref === "paper") {
            const txt = (a.text || "").toLowerCase();
            if (txt.includes("return") || txt.includes("hit rate")) return true;
          }
          const xr = a.xref || "x";
          const n = parseInt((xr.replace("x", "") || "1"), 10);
          return n === 2;
        }).map(a => {
          if (a.xref === "paper" && a.yref === "paper") return { ...a, y: 1.0 };
          return { ...a, xref: "x", yref: "y" };
        });
      }

      // --- Oscillator layout: row 3 only ---
      const oscLayout = {};
      for (const [k, v] of Object.entries(layout)) {
        if (k === "annotations" || k === "title" || k === "shapes") continue;
        if (/^[xy]axis[0-9]*$/.test(k)) continue;
        oscLayout[k] = v;
      }
      oscLayout.shapes = [];
      oscLayout.xaxis = cleanAxis(layout.xaxis3, Object.assign({domain: [0, 1], anchor: "y"}, xRangeOvr));
      oscLayout.yaxis = cleanAxis(layout.yaxis3, {domain: [0, 1], anchor: "x"});
      oscLayout.autosize = false;
      oscLayout.height = 350;
      oscLayout.margin = Object.assign({}, layout.margin || {}, {t: 10});
      if (layout.annotations) {
        oscLayout.annotations = layout.annotations.filter(a => {
          const xr = a.xref || "x";
          const n = parseInt((xr.replace("x", "") || "1"), 10);
          return n === 3;
        }).map(a => ({ ...a, xref: "x", yref: "y" }));
      }

      // --- TrendScore layout: row 4 only ---
      const tsLayout = {};
      for (const [k, v] of Object.entries(layout)) {
        if (k === "annotations" || k === "title" || k === "shapes") continue;
        if (/^[xy]axis[0-9]*$/.test(k)) continue;
        tsLayout[k] = v;
      }
      tsLayout.shapes = [];
      tsLayout.xaxis = cleanAxis(layout.xaxis4, Object.assign({domain: [0, 1], anchor: "y"}, xRangeOvr));
      tsLayout.yaxis = cleanAxis(layout.yaxis4, {domain: [0, 1], anchor: "x"});
      tsLayout.autosize = false;
      tsLayout.height = 140;
      tsLayout.margin = Object.assign({}, layout.margin || {}, {t: 6, b: 20});
      tsLayout.barmode = "relative";
      if (layout.annotations) {
        tsLayout.annotations = layout.annotations.filter(a => {
          const xr = a.xref || "x";
          const n = parseInt((xr.replace("x", "") || "1"), 10);
          return n === 4;
        }).map(a => ({ ...a, xref: "x", yref: "y" }));
      }

      // --- Lower layout: rows 5-6 remapped to axes 1-2 (breakout dots + trend heatmap) ---
      const lowerLayout = {};
      for (const [k, v] of Object.entries(layout)) {
        if (k === "annotations" || k === "title" || k === "shapes") continue;
        if (/^[xy]axis[0-9]*$/.test(k)) continue;
        lowerLayout[k] = v;
      }
      lowerLayout.shapes = [];
      lowerLayout.xaxis  = cleanAxis(layout.xaxis5, Object.assign({domain: [0, 1], anchor: "y"}, xRangeOvr));
      lowerLayout.xaxis2 = cleanAxis(layout.xaxis6, Object.assign({domain: [0, 1], anchor: "y2", matches: "x"}, xRangeOvr));
      const _meta = layout.meta || {};
      const _nBr = _meta._nBr || 0;
      const _nTr = _meta._nTr || 0;
      const _totalK = Math.max((_nBr + _nTr), 1);
      const _trFrac = _nTr / _totalK;
      const _brFrac = _nBr / _totalK;
      const _splitGap = 0.04;
      const _trTop = 1.0;
      const _trBot = _trTop - _trFrac * (1.0 - _splitGap);
      const _brTop = _trBot - _splitGap;
      const _brBot = 0.0;
      lowerLayout.yaxis  = cleanAxis(layout.yaxis5, {domain: [_brBot, _brTop], anchor: "x"});
      lowerLayout.yaxis2 = cleanAxis(layout.yaxis6, {domain: [_trBot, _trTop], anchor: "x2"});
      lowerLayout.autosize = false;
      const _kpiRowPx = Math.round(30 * 1.25); // +25% heatmap row size
      const _lowerH = Math.max(200, (_nBr + _nTr) * _kpiRowPx + 80);
      lowerLayout.height = _lowerH;
      _chartHeights.chartLower = _lowerH;
      lowerLayout.hovermode = "closest";
      lowerLayout.barmode = "relative";
      lowerLayout.margin = Object.assign({}, layout.margin || {}, {t: 30});
      if (layout.annotations) {
        lowerLayout.annotations = layout.annotations
          .filter(a => {
            const xr = a.xref || "x";
            const n = parseInt((xr.replace("x", "") || "1"), 10);
            return n >= 5 && n <= 6;
          })
          .map(a => {
            const xr = a.xref || "";
            const yr = a.yref || "";
            const axMap = {"x6":"x2","x5":"x","y6":"y2","y5":"y"};
            return {
              ...a,
              xref: axMap[xr] || xr,
              yref: axMap[yr] || yr,
            };
          });
      }

      return { upperData, upperLayout, upperShapesStrategy, upperShapesCharts, pnlData, pnlLayout, oscData, oscLayout, tsData, tsLayout, lowerData, lowerLayout };
    }

    async function renderChart() {
      setActiveTFButton();
      setStatus();
      if (DOM.fileWarn) DOM.fileWarn.style.display = "none";
      setLoading(true, `Loading ${currentSymbol} (${currentTF})…`);
      const fig = await loadFig(currentSymbol, currentTF);
      const _hasData = fig && Array.isArray(fig.data) && fig.data.length > 0;
      if (!fig || !_hasData) {
        setLoading(false);
        for (const id of ["chartUpper","chartPnl","chartOsc","chartTs","chartLower"]) {
          const el = (id === "chartUpper" ? DOM.chartUpper : id === "chartLower" ? DOM.chartLower : document.getElementById(id));
          if (el) el.innerHTML = "";
        }
        const upper = DOM.chartUpper;
        if (upper) {
          upper.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:300px;color:var(--muted);font-size:14px;">
            No data available for ${currentSymbol} (${currentTF}).<br>
            This symbol may be delisted or temporarily unavailable.<br>
            Re-run <code>python -m apps.dashboard.build_dashboard --mode all</code> to refresh.
          </div>`;
        }
        return;
      }
      // EUR price conversion: scale price-axis traces if EUR mode active
      if (typeof _isEurMode === "function" && _isEurMode() && typeof _toEur === "function") {
        var _fxRate = (function() {
          var ccy = (typeof SYMBOL_CURRENCIES !== "undefined" && SYMBOL_CURRENCIES[currentSymbol]) || "";
          if (!ccy || ccy === "EUR") return 1;
          var r = (typeof FX_TO_EUR !== "undefined" && FX_TO_EUR[ccy]);
          return r || 1;
        })();
        if (_fxRate !== 1 && fig.data) {
          fig.data.forEach(function(tr) {
            var _scale = function(arr) {
              if (!arr || !arr.length) return arr;
              return arr.map(function(v) { return v != null ? v * _fxRate : v; });
            };
            if (tr.type === "candlestick" || tr.type === "ohlc") {
              tr.open = _scale(tr.open); tr.high = _scale(tr.high);
              tr.low = _scale(tr.low); tr.close = _scale(tr.close);
            } else if (tr.yaxis === "y" || !tr.yaxis) {
              if (tr.y && tr.y.length) tr.y = _scale(tr.y);
            }
          });
          if (fig.layout && fig.layout.shapes) {
            fig.layout.shapes.forEach(function(s) {
              if (s.yref === "y") {
                if (s.y0 != null) s.y0 *= _fxRate;
                if (s.y1 != null) s.y1 *= _fxRate;
              }
            });
          }
        }
      }
      const config = Object.assign({displayModeBar: false, responsive: false, scrollZoom: false}, fig.config || {});
      const { upperData, upperLayout, upperShapesStrategy, upperShapesCharts, pnlData, pnlLayout, oscData, oscLayout, tsData, tsLayout, lowerData, lowerLayout } = splitFigure(fig);
      _upperShapesStrategy = upperShapesStrategy;
      _upperShapesCharts = upperShapesCharts;
      const thm = getPlotlyThemeOverrides();
      const _spikeColor = currentTheme === "dark" ? "rgba(226,232,240,0.7)" : "rgba(0,0,0,0.85)";
      const _applyThemeToLayout = (lay) => {
        lay.paper_bgcolor = thm.paper_bgcolor;
        lay.plot_bgcolor = thm.plot_bgcolor;
        lay.font = Object.assign({}, lay.font || {}, thm.font);
        for (const k of Object.keys(lay)) {
          if (/^[xy]axis[0-9]*$/.test(k) && lay[k]) {
            lay[k].gridcolor = thm.xaxis.gridcolor;
            lay[k].zerolinecolor = thm.xaxis.zerolinecolor;
            if (lay[k].showspikes) lay[k].spikecolor = _spikeColor;
          }
        }
      };
      _applyThemeToLayout(upperLayout);
      _applyThemeToLayout(pnlLayout);
      _applyThemeToLayout(oscLayout);
      _applyThemeToLayout(tsLayout);
      _applyThemeToLayout(lowerLayout);
      delete upperLayout.title;
      await Plotly.react("chartUpper", upperData, upperLayout, config);
      await Plotly.react("chartPnl", pnlData, pnlLayout, config);
      await Plotly.react("chartOsc", oscData, oscLayout, config);
      await Plotly.react("chartTs", tsData, tsLayout, config);
      await Plotly.react("chartLower", lowerData, lowerLayout, config);
      currentFig = fig;
      buildIndicatorPanel(fig);
      applyIndicatorVisibility();
      attachCrosshair((fig && fig.layout && Array.isArray(fig.layout.shapes)) ? fig.layout.shapes : []);
      attachZoomSync();
      attachComboTooltip(fig);
      setLoading(false, "");
      ["chartUpper", "chartPnl", "chartOsc", "chartTs", "chartLower"].forEach(id => {
        const gd = (id === "chartUpper" ? DOM.chartUpper : id === "chartLower" ? DOM.chartLower : document.getElementById(id));
        _resizeWidthOnly(gd, id);
      });
      _applyAnnotations();
      const gdUpStash = DOM.chartUpper;
      if (gdUpStash && gdUpStash.data) {
        gdUpStash.data.forEach(tr => { if (!tr._origHover) tr._origHover = tr.hoverinfo; });
      }
      _applySubTabVisibility();
      // Progressive rendering (20): prefetch adjacent symbols
      _prefetchAdjacent();
    }

    function _prefetchAdjacent() {
      try {
        const allowed = getAllowedSymbolsSet();
        const vis = SYMBOLS.filter(s => allowed.has(s));
        const i = vis.indexOf(currentSymbol);
        if (i < 0) return;
        const toFetch = [];
        if (i > 0) toFetch.push(vis[i - 1]);
        if (i < vis.length - 1) toFetch.push(vis[i + 1]);
        toFetch.forEach(sym => {
          const key = sym + "|" + currentTF;
          if (!figCache[key]) loadFig(sym, currentTF).catch(() => {});
        });
      } catch (e) {}
    }

    // --- Combo tooltip: shown only when hovering the green combo bands ---
    let _comboZones = [];
    function attachComboTooltip(fig) {
      _comboZones = (fig && fig.layout && fig.layout.meta && fig.layout.meta.combo_zones) || [];
    }
    function _updateComboTooltip(hoverX, mouseEvent) {
      const tip = document.getElementById("comboTooltip");
      if (!tip) return;
      if (!_comboZones.length || !hoverX) { tip.style.display = "none"; return; }
      const hxDate = new Date(hoverX).getTime();
      const hits = [];
      for (const z of _comboZones) {
        const zStart = new Date(z.x0).getTime();
        const zEnd = new Date(z.x1).getTime();
        if (hxDate >= zStart && hxDate <= zEnd) hits.push(z);
      }
      if (!hits.length) { tip.style.display = "none"; return; }
      const best = hits[hits.length - 1];
      tip.innerHTML = "<b>" + best.name + "</b> activated " + best.start + " — ended " + best.end + " (" + best.bars + " bars)";
      tip.style.display = "block";
      if (mouseEvent) {
        const tipW = tip.offsetWidth || 250;
        const spaceRight = window.innerWidth - mouseEvent.clientX;
        if (spaceRight < tipW + 20) {
          tip.style.left = (mouseEvent.clientX - tipW - 14) + "px";
        } else {
          tip.style.left = (mouseEvent.clientX + 14) + "px";
        }
        tip.style.top = (mouseEvent.clientY - 30) + "px";
      }
    }
    function _hideComboTooltip() {
      const tip = document.getElementById("comboTooltip");
      if (tip) tip.style.display = "none";
    }

    // --- Zoom sync between upper, oscillator, and lower charts ---
    let _syncingZoom = false;
    let _lastSyncedRange = null;
    let _initialXRange = null;

    function _buildRelayoutUpdate(target, r0, r1) {
      const upd = { "xaxis.range": [r0, r1] };
      if (target._fullLayout) {
        for (const k of Object.keys(target._fullLayout)) {
          if (/^xaxis[0-9]+$/.test(k)) upd[k + ".range"] = [r0, r1];
        }
      }
      return upd;
    }

    function _buildAutorangeUpdate(target) {
      if (_initialXRange) {
        return _buildRelayoutUpdate(target, _initialXRange[0], _initialXRange[1]);
      }
      const upd = { "xaxis.autorange": true };
      if (target._fullLayout) {
        for (const k of Object.keys(target._fullLayout)) {
          if (/^xaxis[0-9]+$/.test(k)) upd[k + ".autorange"] = true;
        }
      }
      return upd;
    }

    function _extractRangeFromEvent(ev) {
      for (const axName of ["xaxis", "xaxis2", "xaxis3", "xaxis4"]) {
        let r0 = ev[axName + ".range[0]"], r1 = ev[axName + ".range[1]"];
        if (r0 === undefined && ev[axName + ".range"]) { r0 = ev[axName + ".range"][0]; r1 = ev[axName + ".range"][1]; }
        if (r0 !== undefined && r1 !== undefined) return [r0, r1];
      }
      return null;
    }

    function attachZoomSync() {
      const gdUp = DOM.chartUpper;
      const gdPnl = document.getElementById("chartPnl");
      const gdOsc = document.getElementById("chartOsc");
      const gdTs = document.getElementById("chartTs");
      const gdLo = DOM.chartLower;
      const charts = [gdUp, gdPnl, gdOsc, gdTs, gdLo].filter(Boolean);
      charts.forEach(gd => { try { if (typeof gd.removeAllListeners === "function") gd.removeAllListeners("plotly_relayout"); } catch(e) {} });

      charts.forEach(gd => {
        gd.on("plotly_relayout", (ev) => {
          if (_syncingZoom) return;

          const range = _extractRangeFromEvent(ev);
          const hasAutorange = ["xaxis", "xaxis2", "xaxis3", "xaxis4"].some(ax => ev[ax + ".autorange"]);
          if (!range && !hasAutorange) return;

          _syncingZoom = true;
          if (range) _lastSyncedRange = range;
          else _lastSyncedRange = null;

          const targets = charts.filter(c => c !== gd && c.style.display !== "none" && c._fullLayout);
          const allPromises = targets.map(t => {
            try {
              if (range) return Plotly.relayout(t, _buildRelayoutUpdate(t, range[0], range[1]));
              else return Plotly.relayout(t, _buildAutorangeUpdate(t));
            } catch (e) { return Promise.resolve(); }
          });
          if (!range && _initialXRange) {
            allPromises.push(
              Plotly.relayout(gd, _buildRelayoutUpdate(gd, _initialXRange[0], _initialXRange[1]))
                .catch(() => {})
            );
          }
          Promise.all(allPromises).finally(() => {
            _syncingZoom = false;
            _updatePnlStats(range);
          });
        });
      });
    }

    function _updatePnlStats(range) {
      if (!currentFig || !currentFig.layout || !currentFig.layout.meta) return;
      const meta = currentFig.layout.meta;
      const trades = meta.pnl_trades;
      const statsFn = meta.pnl_stats_fn;
      if (!trades || !trades.length || typeof statsFn !== "function") return;
      const gdPnl = document.getElementById("chartPnl");
      if (!gdPnl || !gdPnl._fullLayout) return;

      let filtered;
      if (range) {
        const r0 = new Date(range[0]).getTime(), r1 = new Date(range[1]).getTime();
        filtered = trades.filter(t => {
          const exit = new Date(t.exitDate).getTime();
          return exit >= r0 && exit <= r1;
        });
      } else {
        filtered = trades;
      }

      const newText = statsFn(filtered);
      const anns = (gdPnl.layout && gdPnl.layout.annotations) ? gdPnl.layout.annotations.slice() : [];
      let found = false;
      for (let i = 0; i < anns.length; i++) {
        const a = anns[i];
        if (a.xref === "paper" && a.yref === "paper" && ((a.text || "").includes("Return") || (a.text || "").includes("HR"))) {
          anns[i] = Object.assign({}, a, { text: newText });
          found = true;
          break;
        }
      }
      if (!found) return;
      try { Plotly.relayout(gdPnl, { annotations: anns }); } catch (e) {}
    }

    function _syncRangeToChart(gd) {
      if (!gd || !gd._fullLayout) return;
      const ref = DOM.chartUpper;
      if (!ref || !ref._fullLayout || !ref._fullLayout.xaxis) return;
      const xa = ref._fullLayout.xaxis;
      if (xa.range && xa.range.length === 2) {
        _syncingZoom = true;
        Plotly.relayout(gd, _buildRelayoutUpdate(gd, xa.range[0], xa.range[1]))
          .finally(() => { _syncingZoom = false; });
      }
    }

    let currentSubTab = (typeof _st0.subTab === "string" && ["strategy", "chart"].includes(_st0.subTab)) ? _st0.subTab : "strategy";

    function _applySubTabVisibility() {
      const isStrategy = currentSubTab === "strategy";
      const ids = {
        strategy: ["chartPnl", "strategySpacing", "chartTs"],
        chart: ["oscWrap", "chartLower"],
      };
      ids.strategy.forEach(id => { const el = document.getElementById(id); if (el) el.style.display = isStrategy ? "" : "none"; });
      ids.chart.forEach(id => { const el = document.getElementById(id); if (el) el.style.display = isStrategy ? "none" : ""; });
      const indWrap = document.getElementById("indicatorWrap");
      if (indWrap) indWrap.style.display = isStrategy ? "none" : "";
      if (currentTab === "strategy" || currentTab === "chart") {
        document.getElementById("tabStrategy").classList.toggle("active", isStrategy);
        document.getElementById("tabChart").classList.toggle("active", !isStrategy);
      }

      const gdUp = DOM.chartUpper;
      if (gdUp && gdUp.data) {
        try {
          Plotly.relayout(gdUp, { shapes: isStrategy ? _upperShapesStrategy : _upperShapesCharts });
        } catch (e) {}

        const _strategyShow = new Set(["Price", "Combo Signal", "SR Breaks"]);
        const _srHoverHide = new Set(["Support", "Resistance", "Break Res", "Break Sup", "Sup Holds", "Res Holds"]);
        const idxs = [];
        const vis = [];
        const hoverInfos = [];
        gdUp.data.forEach((tr, i) => {
          const k = (tr.meta && tr.meta.indicator) ? tr.meta.indicator : "";
          const nm = tr.name || "";
          idxs.push(i);
          const origH = tr._origHover !== undefined ? tr._origHover : tr.hoverinfo;
          if (isStrategy) {
            vis.push(_strategyShow.has(k));
            hoverInfos.push(_srHoverHide.has(nm) ? "skip" : origH);
          } else {
            const show = k === "Price" || selectedIndicators.has(k) || k === "Combo Signal";
            vis.push(show);
            hoverInfos.push(origH);
          }
        });
        if (idxs.length) {
          try { Plotly.restyle(gdUp, { visible: vis, hoverinfo: hoverInfos }, idxs); } catch (e) {}
        }

        try {
          Plotly.relayout(gdUp, {
            "yaxis.showspikes": isStrategy,
            "yaxis.spikemode": "across+toaxis",
            "yaxis.spikesnap": "cursor",
            "yaxis.spikecolor": "var(--plotly-spike)",
            "yaxis.spikethickness": 1,
            "yaxis.spikedash": "dot",
          });
        } catch (e) {}
      }
    }


    function switchTab(tab) {
      const tabs = ["tabScreener", "tabStrategy", "tabChart", "tabPnl", "tabInfo"];
      tabs.forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.classList.remove("active"); el.setAttribute("aria-selected", "false"); el.setAttribute("tabindex", "-1"); }
      });
      document.getElementById("app").style.display = "none";
      document.getElementById("screenerWrap").style.display = "none";
      const infoEl = document.getElementById("infoWrap");
      if (infoEl) infoEl.style.display = "none";
      const pnlEl = document.getElementById("pnlWrap");
      if (pnlEl) pnlEl.style.display = "none";

      function _activateTab(id) {
        const el = document.getElementById(id);
        if (el) { el.classList.add("active"); el.setAttribute("aria-selected", "true"); el.setAttribute("tabindex", "0"); }
      }
      if (tab === "screener") {
        _activateTab("tabScreener");
        document.getElementById("screenerWrap").style.display = "block";
        currentTab = "screener";
      } else if (tab === "info") {
        _activateTab("tabInfo");
        if (infoEl) infoEl.style.display = "block";
        currentTab = "info";
      } else if (tab === "pnl") {
        _activateTab("tabPnl");
        if (pnlEl) pnlEl.style.display = "block";
        currentTab = "pnl";
        buildPnlTab();
      } else {
        _activateTab(tab === "strategy" ? "tabStrategy" : "tabChart");
        document.getElementById("app").style.display = "";
        currentTab = tab;
        currentSubTab = tab === "strategy" ? "strategy" : "chart";
        saveState({ subTab: currentSubTab });
        _applySubTabVisibility();
        setTimeout(() => {
          ["chartUpper", "chartPnl", "chartOsc", "chartTs", "chartLower"].forEach(id => {
            const gd = document.getElementById(id);
            _resizeWidthOnly(gd, id);
          });
        }, 50);
      }
      saveState({ tab: currentTab });
      pushHashState();
      if (currentTab === "screener") buildScreener();
    }

    document.getElementById("tabChart").addEventListener("click", () => switchTab("chart"));
    document.getElementById("tabStrategy").addEventListener("click", () => switchTab("strategy"));
    document.getElementById("tabScreener").addEventListener("click", () => switchTab("screener"));
    document.getElementById("tabInfo").addEventListener("click", () => switchTab("info"));
    document.getElementById("tabPnl").addEventListener("click", () => switchTab("pnl"));

    // Foldable indicator panel
    const _indToggle = document.getElementById("indicatorToggle");
    const _indStrip = DOM.indicatorStrip;
    if (_indToggle && _indStrip) {
      _indToggle.addEventListener("click", () => {
        const collapsed = _indStrip.classList.toggle("collapsed");
        _indToggle.innerHTML = collapsed ? "Indicators &#9654;" : "Indicators &#9660;";
      });
    }

    // Foldable oscillator panel (collapsed by default)
    const _oscToggle = document.getElementById("oscToggle");
    const _oscChart = document.getElementById("chartOsc");
    if (_oscToggle && _oscChart) {
      _oscToggle.addEventListener("click", () => {
        const collapsed = _oscChart.classList.toggle("osc-collapsed");
        _oscToggle.innerHTML = collapsed ? "Oscillators &#9654;" : "Oscillators &#9660;";
        if (!collapsed) {
          try { Plotly.Plots.resize(document.getElementById("chartOsc")); } catch (e) {}
        }
      });
    }
    // Indicator strip + symbol list are always visible.

    // ── Strategy dropdown ──────────────────────────────────────────────
    (function initStrategyDropdown() {
      const trigger = document.getElementById("strategyTrigger");
      const menu = document.getElementById("strategyMenu");
      if (!trigger || !menu) return;
      const setups = (typeof STRATEGY_SETUPS !== "undefined") ? STRATEGY_SETUPS : {};
      const setupDefs = setups.setups || {};
      const kpisByStrategy = setups.kpis_by_strategy || {};

      function _getLabel(key) {
        if (key === "all") return "All Strategies";
        return (setupDefs[key] || {}).label || key;
      }

      function _build() {
        menu.innerHTML = "";
        const entries = [["all", "All Strategies"]];
        Object.keys(setupDefs).forEach(k => entries.push([k, setupDefs[k].label || k]));
        entries.forEach(([key, label]) => {
          const item = document.createElement("div");
          item.className = "group-option" + (key === currentStrategy ? " active" : "");
          item.textContent = label;
          item.addEventListener("click", (e) => {
            e.stopPropagation();
            currentStrategy = key;
            window.currentStrategy = currentStrategy;
            figCache = {};
            saveState({ strategy: currentStrategy });
            _closeAllGroupMenus();
            _updateAllStrategyDropdowns();
            _build();
            _syncStrategyFilter();
            renderChart();
          });
          menu.appendChild(item);
        });
      }

      function _syncStrategyFilter() {
        const _ssd = (typeof STRATEGY_SETUPS !== "undefined" && STRATEGY_SETUPS && STRATEGY_SETUPS.setups) ? STRATEGY_SETUPS.setups[currentStrategy] : null;
        const _isPol = _ssd && _ssd.entry_type === "polarity_combo";
        if (_isPol) {
          const filterMap = {dip_buy: "strat_dip", swing: "strat_swing", trend: "strat_trend"};
          const targetFilter = filterMap[currentStrategy] || "strat_active";
          _savedScreenerFilter = targetFilter;
          saveState({ screenerFilter: targetFilter });
          document.querySelectorAll("#screenerFilters .btn").forEach(b => {
            b.classList.toggle("active", b.dataset.filter === targetFilter);
          });
        } else {
          _savedScreenerFilter = "all";
          saveState({ screenerFilter: "all" });
          document.querySelectorAll("#screenerFilters .btn").forEach(b => {
            b.classList.toggle("active", b.dataset.filter === "all");
          });
        }
        if (typeof Dashboard !== "undefined" && Dashboard.buildScreener) Dashboard.buildScreener();
      }

      _build();
      trigger.innerHTML = _getLabel(currentStrategy) + " &#9662;";

      trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        const wasOpen = menu.classList.contains("open");
        _closeAllGroupMenus();
        if (!wasOpen) menu.classList.add("open");
      });

      function _updateAllStrategyDropdowns() {
        const lbl = _getLabel(currentStrategy) + " &#9662;";
        document.querySelectorAll(".strategy-trigger-sync").forEach(el => {
          el.innerHTML = lbl;
        });
        trigger.innerHTML = lbl;
      }

      // Placeholder strategy dropdowns on other tabs
      document.querySelectorAll(".strategy-placeholder").forEach(dd => {
        const phTrigger = dd.querySelector(".tab-group-trigger");
        const phMenu = dd.querySelector(".tab-group-menu");
        if (!phTrigger || !phMenu) return;
        phTrigger.classList.add("strategy-trigger-sync");
        phTrigger.innerHTML = _getLabel(currentStrategy) + " &#9662;";

        function _buildPlaceholder() {
          phMenu.innerHTML = "";
          const entries2 = [["all", "All Strategies"]];
          Object.keys(setupDefs).forEach(k => entries2.push([k, setupDefs[k].label || k]));
          entries2.forEach(([key, label]) => {
            const item = document.createElement("div");
            item.className = "group-option" + (key === currentStrategy ? " active" : "");
            item.textContent = label;
            item.addEventListener("click", (e) => {
              e.stopPropagation();
              currentStrategy = key;
              window.currentStrategy = currentStrategy;
              figCache = {};
              saveState({ strategy: currentStrategy });
              _closeAllGroupMenus();
              _updateAllStrategyDropdowns();
              _build();
              _buildPlaceholder();
              _syncStrategyFilter();
              renderChart();
            });
            phMenu.appendChild(item);
          });
        }
        _buildPlaceholder();

        phTrigger.addEventListener("click", (e) => {
          e.stopPropagation();
          const wasOpen = phMenu.classList.contains("open");
          _closeAllGroupMenus();
          if (!wasOpen) phMenu.classList.add("open");
        });
      });
    })();

    function _getStrategyKpis() {
      const setups = (typeof STRATEGY_SETUPS !== "undefined") ? STRATEGY_SETUPS : {};
      const kpisByStrategy = setups.kpis_by_strategy || {};
      if (currentStrategy === "all") return null;
      return kpisByStrategy[currentStrategy] || null;
    }

    function _selectTF(tf) {
      currentTF = tf;
      saveState({ tf: currentTF });
      pushHashState();
      setActiveTFButton();
      setStatus();
      buildScreener();
      buildSymbolList();
      if (currentTab === "pnl") buildPnlTab();
      else if (DOM.chartUpper && DOM.chartUpper.style.display !== "none") renderChart();
    }

    (function _initTabTfSelectors() {
      document.querySelectorAll(".tab-tf-btn[data-tf]").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tf === currentTF);
        btn.addEventListener("click", () => _selectTF(btn.dataset.tf));
      });
    })();

    if (window.Dashboard && window.Dashboard.initScreener) window.Dashboard.initScreener();

    document.getElementById("symbolListSearch").addEventListener("input", _debounce(() => buildSymbolList(), 200));

    // Arrow-key navigation inside tab bars
    document.querySelectorAll('[role="tablist"]').forEach(tl => {
      tl.addEventListener("keydown", e => {
        const tabs = Array.from(tl.querySelectorAll('[role="tab"]'));
        const cur = tabs.indexOf(e.target);
        if (cur < 0) return;
        let next = -1;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (cur + 1) % tabs.length;
        else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (cur - 1 + tabs.length) % tabs.length;
        if (next >= 0) { e.preventDefault(); tabs[next].focus(); tabs[next].click(); }
      });
    });

    // --- Keyboard shortcuts (9) ---
    document.addEventListener("keydown", (e) => {
      if (!e) return;
      const inInput = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA");
      const key = e.key || "";

      // Escape clears any focused search input
      if (key === "Escape" && inInput) {
        e.target.value = "";
        e.target.dispatchEvent(new Event("input"));
        e.target.blur();
        return;
      }
      if (inInput) return;

      // Arrow left/right: navigate symbols
      if (key === "ArrowLeft" || key === "ArrowRight") {
        e.preventDefault();
        const allowed = getAllowedSymbolsSet();
        const vis = SYMBOLS.filter(s => allowed.has(s));
        if (!vis.length) return;
        const i = Math.max(0, vis.indexOf(currentSymbol));
        const j = key === "ArrowRight" ? Math.min(vis.length - 1, i + 1) : Math.max(0, i - 1);
        currentSymbol = vis[j];
        saveState({ symbol: currentSymbol });
        pushHashState();
        buildSymbolList();
        renderChart();
        return;
      }
      // 1,2,3 switch TF
      const tfMap = { "1": "4H", "2": "1D", "3": "1W", "4": "2W", "5": "1M" };
      if (tfMap[key]) {
        currentTF = tfMap[key];
        saveState({ tf: currentTF });
        pushHashState();
        setActiveTFButton();
        setStatus();
        buildScreener();
        buildSymbolList();
        if (currentTab === "pnl") buildPnlTab();
        else if (DOM.chartUpper && DOM.chartUpper.style.display !== "none") renderChart();
        return;
      }
      if (key === "s" || key === "S") { switchTab("screener"); return; }
      if (key === "c" || key === "C") { switchTab("chart"); renderChart(); return; }
      if (key === "b" || key === "B") { switchTab("strategy"); renderChart(); return; }
      if (key === "i" || key === "I") { switchTab("info"); return; }
      if (key === "p" || key === "P") { switchTab("pnl"); return; }
      // D = dark mode toggle
      if (key === "d" || key === "D") { applyTheme(currentTheme === "dark" ? "light" : "dark"); return; }
      // / = focus search
      if (key === "/") { e.preventDefault(); document.getElementById("symbolListSearch").focus(); return; }
    });

    // --- Annotations (17): double-click on upper chart to add text ---
    let _userAnnotations = {};
    try { _userAnnotations = JSON.parse(localStorage.getItem(("td_annotations" + _LS_SUFFIX)) || "{}"); } catch (e) {}
    function _getAnnotationKey() { return currentSymbol + "|" + currentTF; }
    function _saveAnnotations() {
      try { localStorage.setItem(("td_annotations" + _LS_SUFFIX), JSON.stringify(_userAnnotations)); } catch (e) {}
    }
    function _applyAnnotations() {
      const gd = DOM.chartUpper;
      if (!gd || !gd._fullLayout) return;
      const _cs = getComputedStyle(document.documentElement);
      const key = _getAnnotationKey();
      const anns = _userAnnotations[key] || [];
      const plotlyAnns = anns.map(a => ({
        x: a.x, y: a.y, xref: "x", yref: "y",
        text: a.text, showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1,
        font: { size: 11, color: _cs.getPropertyValue("--fg").trim() || "#0f172a" },
        bgcolor: _cs.getPropertyValue("--card-bg").trim() || "rgba(255,255,255,0.9)",
        bordercolor: _cs.getPropertyValue("--border-strong").trim() || "#cbd5e1",
        borderwidth: 1, borderpad: 3,
      }));
      try { Plotly.relayout(gd, { annotations: plotlyAnns }); } catch (e) {}
    }
    setTimeout(() => {
      const gd = DOM.chartUpper;
      if (!gd) return;
      gd.addEventListener("dblclick", (e) => {
        if (!gd._fullLayout) return;
        try {
          const rect = gd.getBoundingClientRect();
          const xax = gd._fullLayout.xaxis;
          const yax = gd._fullLayout.yaxis;
          if (!xax || !yax) return;
          const xPx = e.clientX - rect.left - xax._offset;
          const yPx = e.clientY - rect.top - yax._offset;
          const xVal = xax.p2d(xPx);
          const yVal = yax.p2d(yPx);
          if (xVal == null || yVal == null) return;
          const text = prompt("Annotation text (leave empty to cancel):");
          if (!text) return;
          const key = _getAnnotationKey();
          if (!_userAnnotations[key]) _userAnnotations[key] = [];
          _userAnnotations[key].push({ x: xVal, y: yVal, text: text });
          _saveAnnotations();
          _applyAnnotations();
        } catch (err) {}
      });
    }, 500);

    // --- URL hash state (19) ---
    function pushHashState() {
      try {
        const h = `#${currentSymbol}/${currentTF}/${currentTab}`;
        if (window.location.hash !== h) window.history.replaceState(null, "", h);
      } catch (e) {}
    }
    function readHashState() {
      try {
        const h = window.location.hash.replace(/^#/, "");
        if (!h) return false;
        const parts = h.split("/");
        let changed = false;
        if (parts[0] && SYMBOLS.includes(parts[0].toUpperCase())) {
          currentSymbol = parts[0].toUpperCase();
          changed = true;
        }
        if (parts[1] && TIMEFRAMES.includes(parts[1].toUpperCase())) {
          currentTF = parts[1].toUpperCase();
          changed = true;
        }
        if (parts[2] && ["chart", "strategy", "screener", "info", "pnl"].includes(parts[2])) {
          currentTab = parts[2];
          changed = true;
        }
        return changed;
      } catch (e) { return false; }
    }
    window.addEventListener("hashchange", () => {
      if (readHashState()) {
        saveState({ symbol: currentSymbol, tf: currentTF, tab: currentTab });
        setActiveTFButton();
        buildSymbolList();
        if (currentTab === "screener") switchTab("screener");
        else { switchTab(currentTab); renderChart(); }
      }
    });
    readHashState();

    setActiveTFButton();
    buildGroupTabs();
    ensureCurrentSymbolAllowed();
    setStatus();
    buildSymbolList();

    document.querySelectorAll(".panel-toggle").forEach(btn => {
      btn.addEventListener("click", () => {
        const target = document.getElementById(btn.dataset.target);
        if (!target) return;
        const open = btn.dataset.state !== "closed";
        btn.dataset.state = open ? "closed" : "open";
        target.style.display = open ? "none" : "";
        target.dataset.wasOpen = open ? "false" : "true";
        if (!open) {
          setTimeout(() => {
            const gd = target;
            if (gd && gd.data) {
              _resizeWidthOnly(gd, gd.id);
              _syncRangeToChart(gd);
            }
          }, 50);
        }
      });
    });

    // Sidebar drag-to-resize
    (function _initSidebarResizer() {
      const resizer = document.getElementById("sidebarResizer");
      const appEl = document.getElementById("app");
      if (!resizer || !appEl) return;
      const savedW = loadState().sidebarW;
      if (savedW && savedW > 100 && savedW < 600) {
        appEl.style.setProperty("--sidebar-w", savedW + "px");
      }
      let startX = 0, startW = 0, dragging = false;
      resizer.addEventListener("mousedown", (e) => {
        e.preventDefault();
        dragging = true;
        startX = e.clientX;
        const sidebar = document.getElementById("sidebar");
        startW = sidebar ? sidebar.getBoundingClientRect().width : 200;
        resizer.classList.add("dragging");
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
      });
      document.addEventListener("mousemove", (e) => {
        if (!dragging) return;
        const dx = startX - e.clientX;
        const newW = Math.max(120, Math.min(500, startW + dx));
        appEl.style.setProperty("--sidebar-w", newW + "px");
      });
      document.addEventListener("mouseup", () => {
        if (!dragging) return;
        dragging = false;
        resizer.classList.remove("dragging");
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        const sidebar = document.getElementById("sidebar");
        if (sidebar) {
          const w = Math.round(sidebar.getBoundingClientRect().width);
          saveState({ sidebarW: w });
        }
        ["chartUpper", "chartPnl", "chartOsc", "chartTs", "chartLower"].forEach(id => {
          const gd = document.getElementById(id);
          _resizeWidthOnly(gd, id);
        });
      });
    })();

    // Sync SYMBOL_GROUPS from live CSV data via API (overrides stale baked-in data)
    fetch("/api/groups", { cache: "no-store" })
      .then(r => r.ok ? r.json() : null)
      .then(raw => {
        const live = (raw && raw.data !== undefined) ? raw.data : raw;
        if (!live || typeof live !== "object") return;
        for (const g of Object.keys(live)) {
          SYMBOL_GROUPS[g] = live[g].map(s => s.toUpperCase());
        }
        buildGroupTabs();
        ensureCurrentSymbolAllowed();
        buildSymbolList();
        if (currentTab === "screener") buildScreener();
      })
      .catch(() => {});

    if (currentTab === "screener") switchTab("screener");
    else if (currentTab === "chart") switchTab("chart");
    else if (currentTab === "info") switchTab("info");
    else if (currentTab === "pnl") switchTab("pnl");
    else switchTab("strategy");
    renderChart();
    pushHashState();

    /* ── Scan / Refresh buttons & SSE progress bar ────────────────────── */
    (function initScanRefresh() {
      const scanBtn       = document.getElementById("scanBtn");
      const scanStrategy  = document.getElementById("scanStrategy");
      const scanTimeframe = document.getElementById("scanTimeframe");
      const refreshBtn = document.getElementById("refreshBtn");
      const bar        = document.getElementById("scanBar");
      const fill       = document.getElementById("scanFill");
      const label      = document.getElementById("scanLabel");
      const detail     = document.getElementById("scanDetail");
      const eta        = document.getElementById("scanEta");
      const closeBtn   = document.getElementById("scanClose");

      /* Populate strategy select from STRATEGY_SETUPS (skip threshold strategies) */
      (function populateScanStrategies() {
        if (!scanStrategy || typeof STRATEGY_SETUPS === "undefined") return;
        var setups = STRATEGY_SETUPS.setups || {};
        Object.keys(setups).forEach(function(key) {
          var s = setups[key];
          if (s && s.entry_type === "threshold") return; // Stoof: not yet supported
          var opt = document.createElement("option");
          opt.value = key;
          opt.textContent = (s && s.label) ? s.label : key;
          scanStrategy.appendChild(opt);
        });
      })();
      if (!bar) return;

      let evtSource = null;
      let _activeAction = null;

      function _fmtEta(s) {
        if (s == null) return "";
        s = Math.round(s);
        if (s < 60) return "~" + s + "s left";
        const m = Math.floor(s / 60);
        const sec = s % 60;
        return "~" + m + "m " + (sec < 10 ? "0" : "") + sec + "s left";
      }

      function _setRunning(on) {
        if (scanBtn) scanBtn.classList.toggle("running", on);
        if (refreshBtn) refreshBtn.classList.toggle("running", on);
      }

      function showBar(initLabel) {
        bar.classList.remove("hidden");
        fill.classList.remove("done", "partial", "error");
        fill.style.width = "0%";
        closeBtn.classList.add("hidden");
        _setRunning(true);
        label.textContent = initLabel || "Initialising\u2026";
        detail.textContent = "";
        eta.textContent = "";
      }

      function hideBar() {
        bar.classList.add("hidden");
        _setRunning(false);
        _activeAction = null;
        if (evtSource) { evtSource.close(); evtSource = null; }
      }

      function _refreshGroups() {
        _reloadLiveData();
      }

      function _reloadLiveData() {
        Promise.all([
          fetch("/api/symbol-data", { cache: "no-store" }).then(function(r) {
            return r.ok ? r.json() : null;
          }).then(function(raw) {
            return (raw && raw.data !== undefined) ? raw.data : raw;
          }),
          fetch("/api/screener-data", { cache: "no-store" }).then(function(r) {
            return r.ok ? r.json() : null;
          }).then(function(raw) {
            return (raw && raw.data !== undefined) ? raw.data : raw;
          }),
        ]).then(function(arr) {
          var symData = arr[0];
          var screenerData = arr[1];
          var changed = false;

          if (symData) {
            if (symData.groups) {
              for (var g in symData.groups) {
                var tickers = symData.groups[g].map(function(s) { return s.toUpperCase(); });
                SYMBOL_GROUPS[g] = tickers;
                tickers.forEach(function(s) { if (SYMBOLS.indexOf(s) === -1) SYMBOLS.push(s); });
              }
              changed = true;
            }
            if (symData.symbol_display) {
              for (var s in symData.symbol_display) SYMBOL_DISPLAY[s] = symData.symbol_display[s];
            }
            if (symData.symbol_currencies && typeof SYMBOL_CURRENCIES !== "undefined") {
              for (var s2 in symData.symbol_currencies) SYMBOL_CURRENCIES[s2] = symData.symbol_currencies[s2];
            }
            if (symData.fx_to_eur && typeof FX_TO_EUR !== "undefined") {
              for (var c in symData.fx_to_eur) FX_TO_EUR[c] = symData.fx_to_eur[c];
            }
          }

          if (screenerData) {
            if (screenerData.rows_by_tf) SCREENER.rows_by_tf = screenerData.rows_by_tf;
            if (screenerData.by_symbol) SCREENER.by_symbol = screenerData.by_symbol;
            changed = true;
          }

          if (changed) {
            figCache = {};
            _pnlApiData = null;
            _pnlCacheGroup = null;
            buildGroupTabs();
            if (_activeAction === "Scan") {
              var entryKey = Object.keys(SYMBOL_GROUPS).find(function(k) { return k.toLowerCase().replace(/[_ ]/g, "") === "entrystocks"; });
              if (entryKey) { _selectGroup(entryKey); }
            }
            ensureCurrentSymbolAllowed();
            buildSymbolList();
            buildScreener();
            if (currentTab === "pnl") {
              buildPnlTab();
            } else if (currentTab !== "screener" && currentSymbol) {
              loadFig(currentSymbol, currentTF);
            }
          }
        }).catch(function(err) { console.warn("Live data reload failed:", err); });
      }

      window._connectSSE = _connectSSE;
      function _connectSSE(endpoint, actionName) {
        if (evtSource) return;
        if (location.protocol === "file:") {
          showBar();
          fill.classList.add("error");
          fill.style.width = "100%";
          label.textContent = "Server required";
          detail.textContent = "Open via http://localhost:8050 to use " + actionName;
          closeBtn.classList.remove("hidden");
          _setRunning(false);
          return;
        }
        _activeAction = actionName;
        var initLabel = actionName === "Refresh" ? "Refreshing data\u2026" :
                        actionName === "Enrich" ? "Enriching tickers\u2026" : "Initialising\u2026";
        showBar(initLabel);
        evtSource = new EventSource(endpoint);

        evtSource.addEventListener("progress", function (e) {
          try {
            const d = JSON.parse(e.data);
            fill.style.width = Math.min(d.pct || 0, 100) + "%";
            label.textContent = d.label || "";
            detail.textContent = d.detail || "";
            eta.textContent = _fmtEta(d.eta_s);
          } catch (_) {}
        });

        evtSource.addEventListener("complete", function (e) {
          evtSource.close(); evtSource = null;
          fill.style.width = "100%";
          fill.classList.add("done");
          try {
            const d = JSON.parse(e.data);
            if (_activeAction === "Enrich") {
              label.textContent = "Enrichment complete";
              detail.textContent = d.detail || (d.enriched + " stocks added");
            } else if (_activeAction === "Refresh") {
              label.textContent = "Refresh complete";
              detail.textContent = d.detail || "All data refreshed";
            } else {
              label.textContent = "Scan complete";
              detail.textContent = (d.total != null ? d.total + " signals found" : d.detail || "");
            }
          } catch (_) {
            label.textContent = "Done";
          }
          eta.textContent = "";
          closeBtn.classList.remove("hidden");
          _setRunning(false);
          _refreshGroups();
        });

        evtSource.addEventListener("failed", function (e) {
          evtSource.close(); evtSource = null;
          fill.style.width = "100%";
          try {
            var d = JSON.parse(e.data);
            var isPartial = d.severity === "partial";
            fill.classList.add(isPartial ? "partial" : "error");
            if (isPartial) {
              label.textContent = (_activeAction || "Scan") + " partially complete";
              detail.textContent = d.message || (d.enriched + "/" + d.total + " tickers ready");
              _refreshGroups();
            } else {
              label.textContent = (_activeAction || "Scan") + " failed";
              detail.textContent = d.message || "Build failed";
            }
          } catch (_) {
            fill.classList.add("error");
            label.textContent = (_activeAction || "Scan") + " failed";
            detail.textContent = "Unknown error";
          }
          eta.textContent = "";
          closeBtn.classList.remove("hidden");
          _setRunning(false);
        });

        evtSource.addEventListener("error", function (e) {
          if (evtSource) { evtSource.close(); evtSource = null; }
          fill.classList.add("error");
          fill.style.width = "100%";
          try {
            var d = JSON.parse(e.data);
            label.textContent = (_activeAction || "Scan") + " failed";
            detail.textContent = d.message || "";
          } catch (_) {
            label.textContent = (_activeAction || "Scan") + " error";
            detail.textContent = "Connection lost";
          }
          eta.textContent = "";
          closeBtn.classList.remove("hidden");
          _setRunning(false);
        });

        evtSource.onerror = function () {
          if (evtSource) { evtSource.close(); evtSource = null; }
          fill.classList.add("error");
          fill.style.width = "100%";
          label.textContent = (_activeAction || "Scan") + " error";
          detail.textContent = "Connection lost";
          eta.textContent = "";
          closeBtn.classList.remove("hidden");
          _setRunning(false);
        };
      }

      if (scanBtn) scanBtn.addEventListener("click", function() {
        var strategy = scanStrategy ? scanStrategy.value : "";
        if (!strategy) { alert("Please select a strategy before scanning."); return; }
        var timeframe = scanTimeframe ? scanTimeframe.value : "1D";
        var url = "/api/scan?strategy=" + encodeURIComponent(strategy);
        if (timeframe) url += "&timeframe=" + encodeURIComponent(timeframe);
        _connectSSE(url, "Scan");
      });
      if (refreshBtn) refreshBtn.addEventListener("click", function() { _connectSSE("/api/refresh", "Refresh"); });

      fetch("/api/scan/status", { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (raw) {
          var d = (raw && raw.data !== undefined) ? raw.data : raw;
          if (d && d.scan_running) _connectSSE("/api/scan", "Scan");
          else if (d && d.refresh_running) _connectSSE("/api/refresh", "Refresh");
          else if (d && d.enrich_running) _connectSSE("/api/enrich", "Enrich");
        })
        .catch(function () {});

      closeBtn.addEventListener("click", hideBar);
    })();

    // =====================================================================
    // EUR Price Toggle
    // =====================================================================
    (function() {
      const btn = document.getElementById("eurToggle");
      if (!btn) return;
      let showEur = _st0.showEur === true;
      function _updateLabel() {
        btn.textContent = showEur ? "EUR" : "Local";
        btn.classList.toggle("active", showEur);
      }
      _updateLabel();
      btn.addEventListener("click", function() {
        showEur = !showEur;
        _updateLabel();
        saveState({ showEur: showEur });
        buildScreener();
        figCache = {};
        if (DOM.chartUpper && DOM.chartUpper.style.display !== "none") renderChart();
      });
      window._isEurMode = function() { return showEur; };
      window._toEur = function(price, symbol) {
        if (!showEur || !price) return price;
        var ccy = (typeof SYMBOL_CURRENCIES !== "undefined" && SYMBOL_CURRENCIES[symbol]) || "";
        if (!ccy || ccy === "EUR") return price;
        var rate = (typeof FX_TO_EUR !== "undefined" && FX_TO_EUR[ccy]);
        if (!rate) return price;
        return price * rate;
      };
      window._eurLabel = function(symbol) {
        if (!showEur) return "";
        var ccy = (typeof SYMBOL_CURRENCIES !== "undefined" && SYMBOL_CURRENCIES[symbol]) || "";
        if (!ccy || ccy === "EUR") return "";
        return " \u20AC";
      };
    })();


