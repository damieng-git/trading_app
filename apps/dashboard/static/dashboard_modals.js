/* dashboard_modals.js — Add ticker modal, resolve ticker, staging list */
(function() {
  "use strict";
  var modal = document.getElementById("addTickerModal");
  var openBtn = document.getElementById("btnAddTicker");
  var closeBtn = document.getElementById("addTickerClose");
  var searchBtn = document.getElementById("addTickerSearch");
  var input = document.getElementById("addTickerInput");
  var resultsDiv = document.getElementById("addTickerResults");
  var statusDiv = document.getElementById("addTickerStatus");
  var stagingDiv = document.getElementById("addTickerStaging");
  var stagingList = document.getElementById("addStagingList");
  var stagingCount = document.getElementById("addStagingCount");
  var confirmBtn = document.getElementById("addTickerConfirm");
  if (!modal || !openBtn) return;

  var staged = [];

  function _renderStaging() {
    stagingList.innerHTML = "";
    stagingCount.textContent = staged.length;
    stagingDiv.style.display = staged.length ? "" : "none";
    staged.forEach(function(t, i) {
      var chip = document.createElement("span");
      chip.className = "add-staging-chip";
      chip.innerHTML = t + ' <span class="chip-x" data-idx="' + i + '">\u00d7</span>';
      stagingList.appendChild(chip);
    });
    stagingList.querySelectorAll(".chip-x").forEach(function(x) {
      x.addEventListener("click", function() {
        staged.splice(parseInt(x.dataset.idx, 10), 1);
        _renderStaging();
      });
    });
  }

  function show() {
    modal.style.display = "flex";
    input.value = "";
    resultsDiv.innerHTML = "";
    statusDiv.textContent = "";
    statusDiv.className = "modal-status";
    staged = [];
    _renderStaging();
    input.focus();
  }
  function hide() {
    modal.style.display = "none";
  }
  openBtn.addEventListener("click", show);
  closeBtn.addEventListener("click", hide);
  modal.addEventListener("click", function(e) { if (e.target === modal) hide(); });

  function doSearch() {
    var q = (input.value || "").trim();
    if (!q) return;
    resultsDiv.innerHTML = "";
    searchBtn.classList.add("searching");
    searchBtn.textContent = "Searching\u2026";
    statusDiv.textContent = "Searching\u2026";
    statusDiv.className = "modal-status loading";
    fetch("/api/resolve-ticker", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q }),
    })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        searchBtn.classList.remove("searching");
        searchBtn.textContent = "Search";
        statusDiv.textContent = "";
        statusDiv.className = "modal-status";
        var results = (d.data && d.data.results) || d.results || [];
        if (!results.length) {
          statusDiv.textContent = "No results found";
          statusDiv.className = "modal-status err";
          return;
        }
        var existingSyms = (typeof SYMBOLS !== "undefined" && Array.isArray(SYMBOLS)) ? SYMBOLS : [];
        results.forEach(function(r) {
          var item = document.createElement("div");
          item.className = "modal-result-item";
          var alreadyInWatchlist = existingSyms.indexOf(r.ticker) >= 0;
          var alreadyStaged = staged.indexOf(r.ticker) >= 0;
          var info = document.createElement("div");
          info.className = "modal-result-info";
          if (alreadyInWatchlist) {
            item.style.opacity = "0.45";
            item.style.pointerEvents = "none";
            info.innerHTML = "<strong style='color:var(--muted);'>" + (r.name || r.ticker) + "</strong>" +
              "<br><span style='font-size:11px;color:var(--muted);font-weight:600;'>" + r.ticker + "</span>" +
              "<br><small style='color:var(--muted);'>Already in watchlist</small>";
          } else {
            info.innerHTML = "<strong>" + (r.name || r.ticker) + "</strong>" +
              "<br><span style='font-size:11px;color:var(--muted);font-weight:600;'>" + r.ticker + "</span>" +
              "<br><small>" + [r.sector, r.industry, r.exchange, r.currency, r.quoteType].filter(Boolean).join(" \u2022 ") +
              (r.price ? " \u2022 " + r.price.toFixed(2) : "") + "</small>";
          }
          item.appendChild(info);
          if (!alreadyInWatchlist) {
            var btn = document.createElement("button");
            btn.className = "modal-result-btn";
            btn.textContent = alreadyStaged ? "\u2713 Queued" : "Add to watchlist";
            if (alreadyStaged) btn.disabled = true;
            btn.addEventListener("click", function() {
              if (staged.indexOf(r.ticker) < 0) {
                staged.push(r.ticker);
                _renderStaging();
                btn.textContent = "\u2713 Queued";
                btn.disabled = true;
              }
            });
            item.appendChild(btn);
          }
          resultsDiv.appendChild(item);
        });
      })
      .catch(function(err) {
        searchBtn.classList.remove("searching");
        searchBtn.textContent = "Search";
        statusDiv.textContent = "Error: " + err;
        statusDiv.className = "modal-status err";
      });
  }
  searchBtn.addEventListener("click", doSearch);
  input.addEventListener("keydown", function(e) { if (e.key === "Enter") doSearch(); });

  confirmBtn.addEventListener("click", function() {
    if (!staged.length) return;
    var tickers = staged.slice();
    statusDiv.textContent = "Starting enrichment for " + tickers.length + " tickers\u2026";
    statusDiv.className = "modal-status loading";
    confirmBtn.disabled = true;

    fetch("/api/enrich-symbols", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers: tickers, group: "watchlist" }),
    })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (!d.ok && d.error) {
          if (d.error.indexOf("already running") >= 0) {
            hide();
            window._connectSSE("/api/enrich", "Enrich");
            return;
          }
          statusDiv.textContent = d.error;
          statusDiv.className = "modal-status err";
          confirmBtn.disabled = false;
          return;
        }
        hide();
        window._connectSSE("/api/enrich", "Enrich");
      })
      .catch(function(err) {
        statusDiv.textContent = "Error: " + err;
        statusDiv.className = "modal-status err";
        confirmBtn.disabled = false;
      });
  });
})();
