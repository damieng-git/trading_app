// DOM stub for the hub page: tab switching under Node.
// Usage: node dom_stub_hub.js <html-file>
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.split("<script>").pop().split("</script>")[0];
const rawData = html.match(/id="data">([\s\S]*?)<\/script>/)[1];
const data = JSON.parse(rawData);

class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attrs = {}; this.handlers = {};
    this.style = {}; this.hidden = false; this.className = ""; this._text = "";
    this.value = ""; this.checked = false;
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter(x => x !== c); return c; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === "class") this.className = v; }
  addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); }
  fire(t, e) { (this.handlers[t] || []).forEach(f => f(e || {})); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  scrollIntoView() {}
  getBoundingClientRect() { return { top: -500, bottom: -20, left: 0, width: 720, height: 480 }; }
}
const byId = {};
global.window = { innerHeight: 800 };
global.location = { hash: process.argv[3] || "" };
function make(t) {
  const e = new El(t);
  Object.defineProperty(e, "id", { get() { return this._id || ""; }, set(v) { this._id = v; byId[v] = this; } });
  return e;
}
global.document = {
  getElementById(id) { if (!byId[id]) { const e = make("div"); e.id = id; } return byId[id]; },
  createElement: t => make(t),
  createElementNS: (ns, t) => make(t),
  createTextNode: t => { const e = make("#text"); e._text = String(t); return e; },
};
document.getElementById("data").textContent = rawData;
eval(script);

const tbodyRows = () => byId["tbl"].children[1].children.length;
const dd = k => byId["filters"].children.find(g => g.attrs["data-k"] === k);
const countryBoxes = () => dd("country").children[1].children.length - 1;   // panel minus the All/None bar
const ringCount = () => ["chart-q", "chart-t"].map(id => byId[id].children.filter(c => /ring/.test(c.className)).length)
                          .reduce((a, b) => a + b, 0);
const tabs = byId["tabs"].children;
const scoredN = v => v.records.filter(r => r.score !== null).length;
const countries = v => new Set(v.records.map(r => r.country || "n/a")).size;

const out = { nTabs: tabs.length, tabLabels: tabs.map(t => t.textContent),
              initialHash: location.hash, initialRows: tbodyRows(),
              initialBanner: byId["banner"].textContent };
// select a row on the default tab, then switch: selection must clear
const first = data.views[0].records.filter(r => r.score !== null)[0];
byId["row-" + first.id].fire("click");
out.ringOnAll = ringCount();
tabs[1].fire("click");
const v1 = data.views[1];
out.switched = { rows: tbodyRows(), wantRows: scoredN(v1), countryBoxes: countryBoxes(),
                 wantCountries: countries(v1), ring: ringCount(), hash: location.hash,
                 wantHash: "#" + v1.id, banner: byId["banner"].textContent,
                 wantBanner: v1.banner, onClass: tabs.map(t => t.className) };
// row-click highlight inside a non-default tab
const target = v1.records.filter(r => r.score !== null)[0];
byId["row-" + target.id].fire("click");
out.inTab = { ring: ringCount(), hl: /\bhl\b/.test(byId["row-" + target.id].className) };
// back to All: selection cleared again, rows restored
tabs[0].fire("click");
out.back = { rows: tbodyRows(), wantRows: scoredN(data.views[0]), ring: ringCount(),
             hash: location.hash };
// switch one country off: tab counts follow the filter, and the filter survives a tab switch
const tierPanel = dd("tier").children[1];
const firstTierLabel = tierPanel.children[1];
const cb = firstTierLabel.children[0];
cb.checked = false; cb.fire("change");
out.filtered = { tabLabels: tabs.map(t => t.textContent), button: dd("tier").children[0].textContent };
tabs[1].fire("click");
out.filtered.afterSwitch = { tabLabels: tabs.map(t => t.textContent), button: dd("tier").children[0].textContent };
console.log(JSON.stringify(out));
