// Minimal DOM stub so the dashboard's inline script can run under Node.
// Usage: node dom_stub.js <html-file> <ticker-to-click>
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const clickTicker = process.argv[3];
const script = html.split("<script>").pop().split("</script>")[0];
const data = JSON.parse(html.match(/id="data">([\s\S]*?)<\/script>/)[1]);

class El {
  constructor(tag) {
    this.tag = tag; this.children = []; this.attrs = {}; this.handlers = {};
    this.style = {}; this.hidden = false; this.className = ""; this._text = "";
    this.value = ""; this.checked = false; this.id = "";
    const self = this;
    this.classList = {
      add(c) { self.className = (self.className + " " + c).trim(); },
      remove(c) { self.className = self.className.split(/\s+/).filter(x => x && x !== c).join(" "); },
    };
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter(x => x !== c); return c; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === "class") this.className = v; }
  addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); }
  fire(t, e) { (this.handlers[t] || []).forEach(f => f(e || {})); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  scrollIntoView() { global.__scrolled = (global.__scrolled || 0) + 1; }
  getBoundingClientRect() { return { top: -500, bottom: -20, left: 0, width: 720, height: 480 }; }
  walk(fn) { fn(this); this.children.forEach(c => c.walk(fn)); }
}
const byId = {};
global.window = { innerHeight: 800 };
global.document = {
  getElementById(id) { if (!byId[id]) { byId[id] = new El("div"); byId[id].id = id; } return byId[id]; },
  createElement: t => new El(t),
  createElementNS: (ns, t) => new El(t),
  createTextNode: t => { const e = new El("#text"); e._text = String(t); return e; },
  querySelectorAll() { return []; },
  querySelector() { return null; },
};
// Rows are looked up by id ("row-<id>"), so register them as they are created.
const origCreate = document.createElement;
document.createElement = function (t) {
  const e = origCreate(t);
  Object.defineProperty(e, "id", { get() { return this._id || ""; }, set(v) { this._id = v; byId[v] = this; } });
  return e;
};
document.getElementById("data").textContent = html.match(/id="data">([\s\S]*?)<\/script>/)[1];
eval(script);

const target = data.rows.find(r => r.ticker === clickTicker);
const row = byId["row-" + target.id];
if (!row) throw new Error("row not rendered: " + clickTicker);
const CH = ["chart-q", "chart-t"];
const rings = () => CH.map(id => byId[id].children.filter(c => /ring/.test(c.className)).length);
const before = rings();
row.fire("click");
const per = CH.map(id => {
  const ch = byId[id];
  const sel = ch.children.filter(c => /\bsel\b/.test(c.className));
  const lastDot = ch.children.filter(c => /\bdot\b/.test(c.className)).pop();
  return { ring: ch.children.filter(c => /ring/.test(c.className)).length,
           label: ch.children.filter(c => /sellab/.test(c.className)).map(l => l.textContent),
           selDots: sel.length, selIsTopmostDot: lastDot === sel[0] };
});
const row2 = byId["row-" + target.id];
const out = {
  ringBefore: before, charts: per,
  rowHighlighted: /\bhl\b/.test(row2.className),
  chartScrolledIntoView: (global.__scrolled || 0) > 0,
};
// clicking the same row again clears the selection
row2.fire("click");
out.ringAfterSecondClick = rings();
// clicking the bubble on the Value vs Timing chart marks it on both charts and in the table
byId["chart-t"].children.find(c => /\bdot\b/.test(c.className) && c.attrs["data-id"] === String(target.id)).fire("click");
out.afterBubbleClick = { rings: rings(), rowHighlighted: /\bhl\b/.test(byId["row-" + target.id].className) };
console.log(JSON.stringify(out));
