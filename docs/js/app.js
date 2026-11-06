/* US Screener dashboard: plain JS, no build step. Reads docs/data/*.json, polls manifest.json. */
(function () {
  "use strict";
  const DATA = "data/";
  const POLL_MS = 5 * 60 * 1000;
  const FILES = ["regime", "bubble", "stocks", "etfs", "portfolio", "events", "backtest", "validation",
    "stock_backtest", "track", "methodology", "crashes", "compare", "leads", "leads_backtest"];
  const S = { manifest: null, data: {}, mode: "absolute_return", page: "regime", stockLimit: 60,
    sort: { key: "score", dir: -1 }, filters: { sector: "", flag: "", liquid: true, q: "" }, open: new Set() };
  const MODES = { absolute_return: "Absolute return", long_term: "Long-term", market_neutral: "Market-neutral (research)" };
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const pct = (v, d = 1) => (isNum(v) ? (v * 100).toFixed(d) + "%" : "–");
  const spct = (v, d = 1) => (isNum(v) ? (v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%" : "–");
  const num = (v, d = 2) => (isNum(v) ? v.toFixed(d) : "–");
  const money = (v) => (!isNum(v) ? "–" : v >= 1e12 ? "$" + (v / 1e12).toFixed(1) + "T" : v >= 1e9 ? "$" + (v / 1e9).toFixed(1) + "B" : v >= 1e6 ? "$" + (v / 1e6).toFixed(0) + "M" : "$" + v.toFixed(0));
  const cls = (v) => (isNum(v) ? (v > 0 ? "pos" : v < 0 ? "neg" : "") : "");
  const statusWord = { green: "Green", amber: "Amber", red: "Red", unavailable: "Unavailable" };
  const st = (s, label) => `<span class="st ${esc(s || "unavailable")}">${esc(label || statusWord[s] || s || "Unavailable")}</span>`;
  const cap = (t) => String(t || "").charAt(0).toUpperCase() + String(t || "").slice(1);
  const info = (text) => text ? `<button class="info" type="button" data-tip="${esc(text)}" aria-label="Source study">source</button>` : "";

  // ------------------------------------------------------------------ theme
  const root = document.documentElement;
  try { const t = localStorage.getItem("theme"); if (t) root.setAttribute("data-theme", t); } catch (e) { /* storage blocked */ }
  $("#theme-toggle").addEventListener("click", () => {
    const dark = root.getAttribute("data-theme") ? root.getAttribute("data-theme") === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) { /* ignore */ }
    render();
  });

  // ------------------------------------------------------------------ tooltip (tap or hover)
  const tip = $("#tip");
  function showTip(text, x, y) {
    tip.innerHTML = text; tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(window.innerWidth - r.width - 8, x - r.width / 2)) + "px";
    tip.style.top = Math.max(8, y - r.height - 12) + "px";
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tip]");
    if (b) { const r = b.getBoundingClientRect(); showTip(esc(b.dataset.tip), r.left + r.width / 2, r.top); e.stopPropagation(); return; }
    tip.hidden = true;
  });
  document.addEventListener("mouseover", (e) => {
    const b = e.target.closest("[data-tip]");
    if (b) { const r = b.getBoundingClientRect(); showTip(esc(b.dataset.tip), r.left + r.width / 2, r.top); }
  });
  document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-tip]")) tip.hidden = true; });

  // ------------------------------------------------------------------ explainer panel (two tabs per metric)
  let XPC = null;
  const xpContent = () => XPC || (XPC = window.ExplainContent ?
    window.ExplainContent({ pct, spct, num, esc, isNum, data: () => S.data, mode: () => S.mode }) : {});
  const ctxAttr = (ctx) => (ctx !== undefined && ctx !== null ? ` data-ctx="${esc(ctx)}"` : "");
  // label: already-escaped HTML. Renders the label itself as the trigger, with a small "i" mark.
  const xb = (key, label, ctx, cls = "", icon = true) =>
    `<button type="button" class="xp ${cls}" data-explain="${esc(key)}"${ctxAttr(ctx)} aria-haspopup="dialog"><span class="xp-l">${label}</span>${icon ? '<span class="xp-i" aria-hidden="true">i</span>' : ""}</button>`;
  // icon-only trigger, for places where the label already does something else (e.g. sort buttons)
  const xi = (key, name, ctx) =>
    `<button type="button" class="xp-icon" data-explain="${esc(key)}"${ctxAttr(ctx)} aria-haspopup="dialog" aria-label="Explain ${esc(name)}" title="What does this mean?">i</button>`;
  const xk = (key, label, ctx) => (xpContent()[key] ? xb(key, label, ctx) : label);
  const xpPanel = $("#xp"), xpBackdrop = $("#xp-backdrop");
  let xpOpener = null;
  function xpTab(which) {
    ["now", "bg"].forEach((k) => {
      const on = k === which, tab = $("#xp-tab-" + k);
      tab.setAttribute("aria-selected", String(on)); tab.tabIndex = on ? 0 : -1;
      $("#xp-" + k).hidden = !on;
    });
    $("#xp-body").scrollTop = 0;
  }
  function openExplain(key, ctx, opener) {
    const m = xpContent()[key];
    if (!m || !xpPanel) return;
    const run = (f) => { try { return f ? f(ctx) || "" : ""; } catch (err) { console.error(err); return `<p class="small">Couldn't read the live value (${esc(err.message)}).</p>`; } };
    const text = (h) => String(h || "").replace(/<[^>]+>/g, "").trim();
    xpOpener = opener || null;
    $("#xp-title").textContent = m.title;
    $("#xp-short").innerHTML = m.short || "";
    $("#xp-now").innerHTML = run(m.now) + (m.use ? `<h3>How to use it</h3><p>${m.use}</p>` : "");
    const ours = run(m.ours);
    $("#xp-bg").innerHTML = (text(m.origin) ? `<h3>Where it comes from</h3>${m.origin}` : "") +
      (text(m.held) ? `<h3>Has it held up?</h3>${m.held}` : "") +
      (text(ours) ? `<h3>In this tool's own tests</h3>${ours}` : "") +
      ((m.refs || []).length ? `<h3>Sources</h3><ul class="xp-refs">${m.refs.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : "");
    xpTab("now");
    xpPanel.hidden = false; xpBackdrop.hidden = false; document.body.classList.add("xp-open");
    requestAnimationFrame(() => xpPanel.classList.add("in"));
    $("#xp-close").focus();
  }
  function closeExplain() {
    if (!xpPanel || xpPanel.hidden) return;
    xpPanel.classList.remove("in"); xpPanel.hidden = true; xpBackdrop.hidden = true; document.body.classList.remove("xp-open");
    if (xpOpener && document.contains(xpOpener)) xpOpener.focus();
  }
  if (xpPanel) {
    xpPanel.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closeExplain(); return; }
      if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && e.target.closest("[role=tab]")) {
        const next = e.target.id === "xp-tab-now" ? "bg" : "now"; xpTab(next); $("#xp-tab-" + next).focus(); e.preventDefault(); return;
      }
      if (e.key === "Tab") {  // keep focus inside the panel while it is open
        const f = [...xpPanel.querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"])')].filter((x) => !x.closest("[hidden]"));
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    });
  }

  // ------------------------------------------------------------------ click-to-sort for every table
  S.tsort = {};
  const MONTHS = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11 };
  function sortKey(td) {
    if (!td) return null;
    if (td.dataset.v !== undefined) { const n = parseFloat(td.dataset.v); return isNaN(n) ? td.dataset.v.toLowerCase() : n; }
    const t = td.textContent.replace(/\s+/g, " ").trim();
    if (!t || /^(–|—|-|no data|n\/a|not yet tested)$/i.test(t) || /^too short/i.test(t)) return null;
    const c = t.replace(/^[≤≥<>]\s*/, "").replace(/\*/g, "").replace(/−/g, "-");
    let m = c.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?/);
    if (m) return Date.UTC(+m[1], +m[2] - 1, m[3] ? +m[3] : 1);
    m = c.match(/^([A-Z][a-z]{2}) (\d{1,2})(?:, (\d{4}))?\b/);
    if (m && m[1] in MONTHS) return Date.UTC(m[3] ? +m[3] : new Date().getFullYear(), MONTHS[m[1]], +m[2]);
    m = c.replace(/,/g, "").match(/^([+-]?)\$?(\d+(?:\.\d+)?)\s*(%|B\b|M\b|K\b|T\b|×)?/);
    if (m) return (m[1] === "-" ? -1 : 1) * parseFloat(m[2]) * ({ B: 1e9, M: 1e6, K: 1e3, T: 1e12 }[m[3]] || 1);
    return t.toLowerCase();
  }
  function sortTable(tb, col, dir) {
    const body = tb.tBodies[0];
    if (!body) return;
    const rows = [...body.rows].map((r, i) => ({ r, i, k: sortKey(r.cells[col]) }));
    rows.sort((a, b) => {
      if (a.k === null && b.k === null) return a.i - b.i;
      if (a.k === null) return 1;
      if (b.k === null) return -1;
      const an = typeof a.k === "number", bn = typeof b.k === "number";
      const c = an && bn ? a.k - b.k : an ? -1 : bn ? 1 : String(a.k).localeCompare(String(b.k));
      return (c || a.i - b.i) * (c ? dir : 1);
    });
    rows.forEach((x) => body.appendChild(x.r));
    [...tb.tHead.rows[0].cells].forEach((th, i) => th.setAttribute("aria-sort", i === col ? (dir > 0 ? "ascending" : "descending") : "none"));
  }
  function enhanceTables() {
    document.querySelectorAll("#view table").forEach((tb, i) => {
      const tid = S.page + ":" + i;
      tb.dataset.tid = tid;
      const head = tb.tHead, body = tb.tBodies[0];
      if (!head || head.rows.length !== 1 || !body || body.rows.length < 2) return;
      if (tb.querySelector("th button[data-sort]") || body.querySelector("tr.detail")) return;   // stocks table sorts itself
      const ths = [...head.rows[0].cells];
      if (ths.some((c) => c.colSpan > 1)) return;
      ths.forEach((th) => {
        if (th.querySelector(".th-sort") || !th.textContent.trim()) return;
        const b = document.createElement("button");
        b.type = "button"; b.className = "th-sort"; b.title = "Sort by this column";
        [...th.childNodes].filter((n) => !(n.nodeType === 1 && (n.classList.contains("xp-icon") || n.classList.contains("xp")))).forEach((n) => b.appendChild(n));
        const ind = document.createElement("span"); ind.className = "th-ind"; ind.setAttribute("aria-hidden", "true");
        b.appendChild(ind);
        th.insertBefore(b, th.firstChild);
        th.setAttribute("aria-sort", "none");
      });
      tb.classList.add("sortable");
      const st = S.tsort[tid];
      if (st && st.col < ths.length) sortTable(tb, st.col, st.dir);
    });
  }
  function onSortClick(btn) {
    const th = btn.closest("th"), tb = btn.closest("table");
    const col = th.cellIndex, tid = tb.dataset.tid;
    const prev = S.tsort[tid];
    let dir;
    if (prev && prev.col === col) dir = -prev.dir;
    else {   // numbers and dates start high-to-low, text starts A-Z
      const k = [...tb.tBodies[0].rows].map((r) => sortKey(r.cells[col])).find((x) => x !== null);
      dir = typeof k === "number" ? -1 : 1;
    }
    S.tsort[tid] = { col, dir };
    sortTable(tb, col, dir);
  }

  // ------------------------------------------------------------------ data loading
  async function getJSON(name, v) {
    const r = await fetch(DATA + name + ".json?v=" + encodeURIComponent(v || Date.now()), { cache: "no-store" });
    if (!r.ok) throw new Error(name + ": HTTP " + r.status);
    return r.json();
  }
  async function refresh(initial) {
    let m;
    try { m = await getJSON("manifest"); } catch (e) { setUpdated(null, e.message); if (initial) render(); return; }
    const prevFiles = (S.manifest && S.manifest.files) || {};
    const changed = FILES.filter((f) => {
      const k = f + ".json"; const h = m.files && m.files[k] && m.files[k].hash;
      return initial || !S.data[f] || (h && (!prevFiles[k] || prevFiles[k].hash !== h));
    });
    S.manifest = m;
    await Promise.all(changed.map(async (f) => {
      const h = m.files && m.files[f + ".json"] && m.files[f + ".json"].hash;
      try { S.data[f] = await getJSON(f, h); } catch (e) { S.data[f] = S.data[f] || null; }
    }));
    setUpdated(m);
    if (initial || changed.length) render();
  }
  function ago(iso) {
    if (!iso) return "never";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return "just now";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 172800) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  }
  // GitHub page where the pipeline can be run by hand (derived from the Pages address)
  const REPO = (() => {
    const h = location.hostname.match(/^([^.]+)\.github\.io$/), p = location.pathname.split("/").filter(Boolean)[0];
    return h && p ? `https://github.com/${h[1]}/${p}` : "https://github.com/akshatp12-wq/us-screener";
  })();
  function setUpdated(m, err) {
    const el = $("#updated-label");
    if (!m) { el.innerHTML = `<span class="stale">Data unavailable</span> ${esc(err || "")}`; return; }
    const t = new Date(m.run_time);
    const stale = (Date.now() - t.getTime()) > 36 * 3600 * 1000;
    el.innerHTML = `Updated <strong class="${stale ? "stale" : ""}" title="${esc(t.toString())}">${esc(ago(m.run_time))}</strong>` +
      `<br><span class="small">${esc(t.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }))} · ${esc(m.job)} · ` +
      `<a href="${REPO}/actions/workflows/pipeline.yml" target="_blank" rel="noopener" title="Open GitHub to run the pipeline now (Run workflow). Takes about 5 to 20 minutes; this page picks up the result automatically.">Recompute ↗</a></span>`;
  }
  async function manualRefresh() {
    const b = $("#refresh-btn");
    if (!b || b.disabled) return;
    b.disabled = true; b.classList.add("spinning");
    const before = S.manifest && S.manifest.run_time;
    try { await refresh(true); } finally {
      b.disabled = false; b.classList.remove("spinning");
      const now = S.manifest && S.manifest.run_time;
      b.title = now && now !== before ? "Loaded newer data" : "Already showing the latest published data";
      announce(b.title);
    }
  }
  function announce(msg) {
    let live = $("#sr-live");
    if (!live) { live = document.createElement("div"); live.id = "sr-live"; live.className = "sr-only"; live.setAttribute("aria-live", "polite"); document.body.appendChild(live); }
    live.textContent = msg;
  }
  setInterval(() => { refresh(false); }, POLL_MS);
  setInterval(() => { if (S.manifest) setUpdated(S.manifest); }, 60000);

  // ------------------------------------------------------------------ charts (SVG)
  function sparkSVG(points, opts = {}) {
    const vals = (points || []).map((p) => p[1]).filter(isNum);
    if (vals.length < 2) return `<span class="small">No history</span>`;
    const w = 220, h = 34, lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const xs = (i) => (i / (points.length - 1)) * w, ys = (v) => h - 3 - ((v - lo) / span) * (h - 6);
    let d = "";
    points.forEach((p, i) => { if (isNum(p[1])) d += (d ? "L" : "M") + xs(i).toFixed(1) + "," + ys(p[1]).toFixed(1); });
    let thr = "";
    (opts.lines || []).forEach((t) => { if (t >= lo && t <= hi) thr += `<line x1="0" x2="${w}" y1="${ys(t)}" y2="${ys(t)}" class="hairline" style="stroke:var(--muted);stroke-dasharray:2 3"/>`; });
    const last = points[points.length - 1];
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${esc(opts.label || "trend")}: from ${esc(points[0][0])} to ${esc(last[0])}">` +
      thr + `<path d="${d}" fill="none" stroke="var(--series-1)" stroke-width="2" vector-effect="non-scaling-stroke"/>` +
      `<circle cx="${xs(points.length - 1)}" cy="${ys(last[1])}" r="3" fill="var(--series-1)" stroke="var(--surface)" stroke-width="2"/></svg>`;
  }

  let chartId = 0;
  const CHARTS = {};
  function lineChart(series, opts = {}) {
    // series: [{name, color, points:[[label, value]], dash}]
    const id = "c" + (++chartId);
    const all = series.filter((s) => s.points && s.points.length);
    if (!all.length) return `<p class="empty">No data yet.</p>`;
    const W = 640, H = opts.height || 260, P = { l: 46, r: 12, t: 10, b: 24 };
    const labels = all.reduce((a, s) => (s.points.length > a.length ? s.points.map((p) => p[0]) : a), []);
    const idx = new Map(labels.map((l, i) => [l, i]));
    const log = !!opts.log;
    const tf = (v) => (log ? Math.log(Math.max(v, 1e-6)) : v);
    let lo = Infinity, hi = -Infinity;
    all.forEach((s) => s.points.forEach((p) => { if (isNum(p[1])) { lo = Math.min(lo, tf(p[1])); hi = Math.max(hi, tf(p[1])); } }));
    if (opts.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
    const x = (i) => P.l + (i / Math.max(1, labels.length - 1)) * (W - P.l - P.r);
    const y = (v) => P.t + (1 - (tf(v) - lo) / (hi - lo)) * (H - P.t - P.b);
    let grid = "";
    const ticks = 4;
    for (let k = 0; k <= ticks; k++) {
      const tv = lo + (k / ticks) * (hi - lo), yy = P.t + (1 - k / ticks) * (H - P.t - P.b);
      const shown = log ? Math.exp(tv) : tv;
      grid += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${yy}" y2="${yy}"/><text class="axis" x="${P.l - 6}" y="${yy + 4}" text-anchor="end">${esc(opts.fmt ? opts.fmt(shown) : shown.toFixed(log ? 1 : 2))}</text>`;
    }
    const step = Math.max(1, Math.round(labels.length / 5));
    for (let i = 0; i < labels.length; i += step) grid += `<text class="axis" x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(String(labels[i]).slice(0, 4))}</text>`;
    let paths = "";
    all.forEach((s) => {
      let d = "";
      s.points.forEach((p) => { const i = idx.get(p[0]); if (isNum(p[1]) && i !== undefined) d += (d ? "L" : "M") + x(i).toFixed(1) + "," + y(p[1]).toFixed(1); });
      paths += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" ${s.dash ? 'stroke-dasharray="5 4"' : ""} stroke-linejoin="round"/>`;
    });
    CHARTS[id] = { all, labels, idx, x, y, W, H, P, fmt: opts.tipFmt || ((v) => num(v, 2)) };
    const legend = all.length > 1 ? `<div class="legend">${all.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
    return `<div class="chart" data-chart="${id}"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || all.map((s) => s.name).join(", "))}">${grid}${paths}<line class="hairline" x1="0" x2="0" y1="${P.t}" y2="${H - P.b}" visibility="hidden"/></svg>${legend}</div>`;
  }
  function wireCharts(scope) {
    scope.querySelectorAll("[data-chart]").forEach((el) => {
      const c = CHARTS[el.dataset.chart]; if (!c) return;
      const svg = el.querySelector("svg"), hair = svg.querySelector(".hairline");
      const move = (ev) => {
        const r = svg.getBoundingClientRect();
        const cx = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) / r.width * c.W;
        const i = Math.round(((cx - c.P.l) / (c.W - c.P.l - c.P.r)) * (c.labels.length - 1));
        if (i < 0 || i >= c.labels.length) { hair.setAttribute("visibility", "hidden"); tip.hidden = true; return; }
        const xx = c.x(i); hair.setAttribute("x1", xx); hair.setAttribute("x2", xx); hair.setAttribute("visibility", "visible");
        const lab = c.labels[i];
        const rows = c.all.map((s) => { const p = s.points.find((q) => q[0] === lab); return p ? `<div><span style="color:${s.color}">■</span> ${esc(s.name)}: <b>${esc(c.fmt(p[1]))}</b></div>` : ""; }).join("");
        showTip(`<div><b>${esc(lab)}</b></div>${rows}`, r.left + (xx / c.W) * r.width, r.top + 10);
      };
      svg.addEventListener("mousemove", move); svg.addEventListener("touchmove", move, { passive: true });
      svg.addEventListener("mouseleave", () => { hair.setAttribute("visibility", "hidden"); tip.hidden = true; });
    });
  }
  function hist(h) {
    if (!h || !h.counts) return "";
    const W = 640, H = 140, n = h.counts.length, mx = Math.max(...h.counts), bw = W / n;
    let bars = "";
    h.counts.forEach((c, i) => {
      const mid = (h.edges[i] + h.edges[i + 1]) / 2, bh = (c / mx) * (H - 24);
      bars += `<rect x="${i * bw + 1}" y="${H - 18 - bh}" width="${bw - 2}" height="${bh}" rx="2" fill="${mid < -0.2 ? "var(--critical)" : "var(--series-1)"}"><title>${spct(h.edges[i])} to ${spct(h.edges[i + 1])}: ${c} paths</title></rect>`;
    });
    const zero = (0 - h.edges[0]) / (h.edges[n] - h.edges[0]) * W;
    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Distribution of simulated 1-year returns">${bars}
      <line x1="${zero}" x2="${zero}" y1="0" y2="${H - 18}" class="hairline"/>
      <text class="axis" x="2" y="${H - 4}">${spct(h.edges[0], 0)}</text><text class="axis" x="${W - 2}" y="${H - 4}" text-anchor="end">${spct(h.edges[n], 0)}</text>
      <text class="axis" x="${zero}" y="${H - 4}" text-anchor="middle">0%</text></svg>
      <div class="legend"><span><i style="background:var(--series-1)"></i>Simulated 1-year return</span><span><i style="background:var(--critical)"></i>Loss worse than 20%</span></div></div>`;
  }
  const curve = (pts) => (pts || []).map((p) => [p[0], p[1]]);

  // ------------------------------------------------------------------ pages
  function unavailable(name) {
    return `<p class="empty">${esc(name)} hasn't been generated yet. It appears after the next scheduled run (see Data health).</p>`;
  }
  function modeSeg() {
    return `<div class="seg" role="group" aria-label="Strategy mode">${Object.entries(MODES).map(([k, v]) =>
      `<button type="button" data-mode="${k}" aria-pressed="${S.mode === k}">${esc(v)}</button>`).join("")}</div>`;
  }

  function pageRegime() {
    const r = S.data.regime;
    if (!r) return unavailable("The regime gauge");
    const g = r.regime, pos = { Calm: 16, Stretched: 50, Fragile: 84 }[g.label] || 50;
    const ev = S.data.events;
    let h = `<section class="hero" aria-label="Overall regime">
      <p class="small">Market regime as of ${esc(r.asof)}</p>
      <h1 class="hero-word">${esc(g.label)}</h1>
      <div class="hero-band" aria-hidden="true"><i style="left:calc(${pos}% - 2px)"></i></div>
      <p class="hero-meta">${esc(cap(g.reasons.join("; ")))}. Risk multiplier for absolute-return mode: <b>${num(g.multiplier, 2)}×</b>. ${xb("regime", "What does this label mean?", null, "xp-link")}
      ${g.missing_inputs && g.missing_inputs.length ? `<br><span class="small">Missing inputs: ${esc(g.missing_inputs.join(", "))}.</span>` : ""}</p>
      <p class="small">Tap any metric name or <span class="xp-i" aria-hidden="true">i</span> mark for what it says now and the research behind it.</p>
      <p class="small">A reading of risk now, not a forecast of when a fall starts: historically the label mostly turned at or after peaks. Fragile = credit warning (gap or red zone) and Stretched. Stretched = euphoric sentiment, ≥20% of sector/industry ETFs bubble-flagged, or a credit warning on its own.</p>
      ${ev && ev.this_week && ev.this_week.length ? `<p><span class="flag">Event this week</span> ${ev.this_week.slice(0, 4).map((e) => esc(e.type + (e.ticker ? " " + e.ticker : "") + " " + e.date.slice(5))).join(", ")}</p>` : ""}
    </section>`;
    const bw = r.dials.buy_watch;
    if (bw && bw.state) {
      h += `<section class="buywatch" aria-label="Buy watch"><h2>${xb("buy_watch", "Buy watch: " + esc(bw.state))}${bw.capitulation ? ' <span class="flag">Capitulation</span>' : ""}</h2>
        <p>${esc(bw.text)}</p>
        <div class="kv"><div><span>Drawdown now</span><b>${spct(bw.drawdown_now)}</b></div><div><span>Worst in 12 months</span><b>${spct(bw.worst_drawdown_lookback)}</b></div>
        <div><span>Above 10-month average</span><b>${bw.above_sma ? "Yes" : "No"}</b></div><div><span>VIX</span><b>${num(bw.vix, 1)}</b></div></div>
        <p class="small">Rule: ${esc(bw.rule)}. Capitulation flag: ${esc(bw.capitulation_rule)}. ${esc(bw.use)} ${info(bw.cite)}</p></section>`;
    }
    const order = [["Drive the label and position sizing", ["credit_gap", "red_zone_business", "red_zone_household", "sentiment"]],
      ["Context only (not used in the label)", ["cape", "yield_curve", "ebp", "vrp"]], ["Crash monitor", ["crash_guard"]]];
    order.forEach(([title, keys]) => {
      h += `<h2>${esc(title)}</h2><ul class="dials">`;
      keys.forEach((k) => { const d = r.dials[k]; if (d) h += dialRow(d); });
      h += `</ul>`;
    });
    h += `<h2>Market trend</h2><p class="lede small">12-month excess return and price versus the 10-month average. ${info("Moskowitz, Ooi & Pedersen (2012), Time Series Momentum; Faber (2007)")}</p>
      <div class="table-wrap"><table><thead><tr><th>Asset</th><th>ETF</th><th class="num">12-month</th><th>Above 10-mo avg</th><th>Trend ${xi("trend", "market trend")}</th></tr></thead><tbody>` +
      Object.entries(r.trend || {}).map(([k, t]) => `<tr><td>${xb("trend", esc(k), k)}</td><td>${esc(t.symbol)}</td><td class="num ${cls(t.ret_12m)}">${spct(t.ret_12m)}</td><td>${t.above_10m_sma === undefined ? "–" : t.above_10m_sma ? "Yes" : "No"}</td><td>${st(t.status, t.signal ? t.signal[0].toUpperCase() + t.signal.slice(1) : "Unavailable")}</td></tr>`).join("") +
      `</tbody></table></div>`;
    return h;
  }
  function dialRow(d) {
    let val = "–", sub = "";
    if (d.key === "crash_guard") { val = d.on ? "On" : "Off"; sub = `Market 24-month return ${spct(d.market_24m)}, volatility at ${pct(d.vol_pct, 0)} percentile`; }
    else if (isNum(d.value)) {
      val = d.key === "yield_curve" ? d.value.toFixed(0) + "%" : d.key === "cape" ? d.value.toFixed(1) + "×" : num(d.value, 2);
    }
    if (d.key === "sentiment" && d.label) sub = `Reading: ${d.label}. ` + (d.baker_wurgler ? `Baker-Wurgler ${num(d.baker_wurgler.value)} (as of ${d.baker_wurgler.as_of}). ` : "");
    if (d.key === "cape" && d.expected_10y_real) { const e = d.expected_10y_real; sub = `Implied 10-year real return ${pct(e.median)} (range ${pct(e.low)} to ${pct(e.high)}); context, not a timing signal. `; }
    if (d.key === "yield_curve" && isNum(d.spread_qavg)) sub = `10y–3m spread, quarter average ${num(d.spread_qavg)} pp. `;
    if (d.key === "ebp" && d.source_used) sub = `${d.source_used}. ` + (isNum(d.recession_prob_ebp) ? `Fed model recession odds ${num(d.recession_prob_ebp, 0)}%. ` : "");
    if (d.key && d.key.startsWith("red_zone") && isNum(d.price_growth_3y)) sub = `3-year price growth ${spct(d.price_growth_3y)}; credit at ${pct(d.credit_pct, 0)}, prices at ${pct(d.price_pct, 0)} of history. `;
    if (d.status === "unavailable") sub = (d.reason || "Source unavailable") + ". ";
    const p = isNum(d.percentile) ? `<div class="pct" title="Percentile vs history"><i style="left:calc(${(d.percentile * 100).toFixed(1)}% - 1px)"></i></div><span class="pct-label">${pct(d.percentile, 0)} percentile of history</span>` : "";
    return `<li class="dial">
      <span class="glyph ${esc(d.status)}" aria-hidden="true">${{ green: "●", amber: "▲", red: "◆" }[d.status] || "○"}</span>
      <div><div class="dial-title">${xk(d.key, esc(d.title))}</div>${d.info_only ? '<span class="small">Info only</span>' : ""}</div>
      <div class="dial-value"><b>${esc(val)}</b>${st(d.status)}</div>
      <div class="dial-sub">${esc(sub)}${esc(d.threshold || "")}<br><span class="small">${esc(d.source)} · ${esc(d.frequency)}${d.as_of ? " · as of " + esc(d.as_of) : ""}</span> ${info(d.cite + " — " + (d.formula || ""))}</div>
      <div class="dial-viz">${sparkSVG(d.sparkline, { label: d.title })}${p}</div>
    </li>`;
  }

  // ------------------------------------------------------------------ lead indicators
  const LEAD_ORDER = ["insider_buying", "mispricing", "resid_mom", "earnings_reaction", "abnormal_volume"];
  const leadStatus = (s) => st(s === "Validated" ? "green" : s === "Promising" ? "amber" : "unavailable", s || "Not yet tested");
  function leadValue(k, r) {
    if (!r) return "–";
    if (k === "mispricing") return isNum(r.v) && r.v > 3 ? `>+300% <span class="small" title="A gap this large usually means a data problem (share count, a one-off item). Check the filings.">check data</span>` : `${spct(r.v, 0)} <span class="small">fair value vs price</span>`;
    if (k === "resid_mom") return `${num(r.v, 2)} <span class="small">z</span>`;
    if (k === "insider_buying") return `${isNum(r.v) ? (r.v > 0 ? "+" : "") + r.v.toFixed(0) : "–"} <span class="small">net insiders</span>`;
    if (k === "earnings_reaction") return `${spct(r.v)} <span class="small">${esc(r.release || "")}</span>`;
    if (k === "abnormal_volume") return `${isNum(r.v) ? Math.exp(r.v).toFixed(1) + "×" : "–"} <span class="small">5-day ${spct(r.ret_5d)}</span>`;
    return num(r.v);
  }
  function leadPeriod(x) {
    if (!x || !x.n) return '<span class="small">no data</span>';
    if (x.n < 24 || !isNum(x.ann_mean)) return `<span class="small">too short (${x.n} mo)</span>`;
    return `<span class="${cls(x.ann_mean)}">${spct(x.ann_mean)}</span> <span class="small">t ${num(x.t, 1)}</span>`;
  }
  function fdate(iso) {
    if (!iso) return "–";
    const d = new Date(iso + "T12:00:00");
    const sameYear = d.getFullYear() === new Date().getFullYear();
    return d.toLocaleDateString([], { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
  }
  const SLOW = { mispricing: true, resid_mom: true };
  function sinceCells(k, r) {
    if (!r || !r.since) return `<td class="num small">–</td><td class="num">–</td><td class="num">–</td>`;
    const rel = isNum(r.ret_since) && isNum(r.spy_since) ? r.ret_since - r.spy_since : null;
    return `<td class="num">${r.capped ? "≤ " : ""}${fdate(r.since)}${SLOW[k] ? '<span class="small">*</span>' : ""}</td><td class="num">${isNum(r.px_since) ? "$" + r.px_since.toFixed(2) : "–"}</td>` +
      `<td class="num"><span class="${cls(r.ret_since)}">${spct(r.ret_since)}</span><span class="nm">${isNum(rel) ? (rel >= 0 ? "+" : "") + (rel * 100).toFixed(1) + " pts vs SPY" : ""}</span></td>`;
  }
  function pageLeads() {
    const L = S.data.leads, B = (S.data.leads_backtest || {}).leads || {};
    if (!L || !L.leads) return unavailable("Lead indicators");
    const ind = (S.data.leads_backtest || {}).industry_residual_momentum;
    let h = `<h1>Lead indicators</h1><p class="lede">Stocks and sector ETFs worth researching, flagged by signals with published support. Each one is backtested on its own; none of them changes the mode scores. A flag is a reason to look closer, not a buy signal. ${xb("leads_overview", "How to use this page", null, "xp-link")}</p>
      <p class="small">As of ${esc(L.asof)} · ${L.universe} liquid stocks (S&P 1500, $5M+/day, price above $5).</p>
      <h2>${xb("lead_status", "Evidence")}</h2>
      <div class="table-wrap"><table><thead><tr><th>Indicator</th><th>Status</th><th class="num">Flagged now</th><th class="num">Before 2011, vs universe / yr</th><th class="num">2011 on</th><th class="num">Full period, net of costs</th><th class="num">Since</th><th>In 2+ list ${xi("convergence", "two-plus list")}</th></tr></thead><tbody>` +
      LEAD_ORDER.filter((k) => L.leads[k]).map((k) => { const x = L.leads[k], b = B[k] || {}, f = b.flagged_minus_universe || {}, n = b.net_of_costs || {};
        const bst = (b.bear_status || {}).status || x.bear_status;
        return `<tr><td>${xb(k, esc(x.label))}<span class="nm">${esc(x.short)}</span></td><td>${leadStatus(b.status || x.status)}<span class="nm" title="${esc(b.reason || x.reason || "")}">${esc(b.reason || x.reason || "")}</span>${bst ? `<span class="nm">Bearish side: ${esc(bst)}</span>` : ""}</td>
          <td class="num">${x.n_bull}${x.n_bear ? ` <span class="small">/ ${x.n_bear} bearish</span>` : ""}</td><td class="num">${leadPeriod(f.pre)}</td><td class="num">${leadPeriod(f.post)}</td><td class="num">${leadPeriod(n.full)}</td><td class="num small">${esc(b.first_month || "–")}</td><td class="small">${x.counts === false ? "No" : "Yes"}</td></tr>`; }).join("") +
      (L.etf_lead ? (() => { const e = L.etf_lead, eb = (S.data.leads_backtest || {}).etf_lead || {}, r = (eb.industry || {}).residual || {};
        return `<tr><td>${xb("etf_resid_mom", esc(e.label))}<span class="nm">Sector and industry ETFs</span></td><td>${leadStatus(eb.status || e.status)}<span class="nm" title="${esc(eb.reason || e.reason || "")}">${esc(eb.reason || e.reason || "")}</span></td>
          <td class="num">top ${e.top} of ${e.eligible}</td><td class="num">${leadPeriod(r.pre)}</td><td class="num">${leadPeriod(r.post)}</td><td class="num"><span class="small">gross; industries</span></td><td class="num small">${r.full && r.full.start ? esc(r.full.start) : "–"}</td><td class="small">n/a</td></tr>`; })() : "") +
      `</tbody></table></div>`;
    if (ind && ind.residual && ind.residual.full) h += `<p class="small">Residual momentum on Ken French's 49 industries (survivorship-free): ${spct(ind.residual.pre && ind.residual.pre.ann_mean)} a year over the market before 2011 (t ${num(ind.residual.pre && ind.residual.pre.t, 1)}), ${spct(ind.residual.post && ind.residual.post.ann_mean)} since (t ${num(ind.residual.post && ind.residual.post.t, 1)}); raw momentum ${spct(ind.raw.pre && ind.raw.pre.ann_mean)} and ${spct(ind.raw.post && ind.raw.post.ann_mean)}.</p>`;
    if (L.etf_lead && (L.etf_lead.rows || []).length) {
      const e = L.etf_lead, eb = (S.data.leads_backtest || {}).etf_lead || {}, et = (eb.etfs || {}).residual || {};
      h += `<h2>${xb("etf_resid_mom", esc(e.label))} ${leadStatus(eb.status || e.status)}</h2><p class="small">${esc(e.short)}. Top ${e.top} flagged. Check on today's ETFs since 2007: ${leadPeriod(et.full)} a year vs all eligible ETFs.</p>
        <div class="table-wrap"><table><thead><tr><th>#</th><th>ETF</th><th>Type</th><th class="num">Residual momentum</th><th class="num">12–1 month return</th></tr></thead><tbody>` +
        e.rows.slice(0, Math.max(e.top + 4, 12)).map((r) => `<tr${r.flag ? ' class="lead-flag"' : ""}><td class="num small">${r.rank}</td><td>${xb("etf_resid_mom", `<span class="tk">${esc(r.t)}</span>`, r.t)}<span class="nm">${esc(r.n)}</span></td><td class="small">${esc(r.kind)}</td><td class="num">${num(r.v, 2)}</td><td class="num ${cls(r.ret_12m)}">${spct(r.ret_12m)}</td></tr>`).join("") +
        `</tbody></table></div>`;
    }
    const conv = L.convergence || [];
    h += `<h2>${xb("convergence", "Flagged by two or more indicators")}</h2><p class="small">${esc(L.convergence_rule || "")}</p>`;
    h += conv.length ? `<div class="table-wrap"><table><thead><tr><th>Stock</th><th>Sector</th><th class="num">Price now</th><th>Bullish leads (signal date)</th></tr></thead><tbody>` +
      conv.map((c) => `<tr><td><span class="tk">${esc(c.t)}</span><span class="nm">${esc(c.n)}</span></td><td class="small">${esc(c.s)}</td><td class="num">${isNum(c.px) ? "$" + c.px.toFixed(2) : "–"}</td><td>${c.leads.map((k) => xb(k, `${esc((L.leads[k] || {}).label || k)}${(c.since || {})[k] ? ` <b>${fdate(c.since[k])}</b>` : ""}`, c.t, "chip", false)).join(" ")}</td></tr>`).join("") +
      `</tbody></table></div>` : `<p class="empty">No stock is flagged by two indicators today.</p>`;
    LEAD_ORDER.filter((k) => L.leads[k]).forEach((k) => {
      const x = L.leads[k];
      const table = (rows, title) => rows.length ? `<div class="table-wrap"><table><thead><tr><th>${title}</th><th>Sector</th><th class="num">${esc(x.unit)}</th><th class="num">Percentile</th><th class="num">Signal date ${xi("lead_since", "signal date")}</th><th class="num">Price then</th><th class="num">Since signal</th><th class="num">Price now</th><th class="num">Market cap</th></tr></thead><tbody>` +
        rows.map((r) => `<tr><td>${xb(k, `<span class="tk">${esc(r.t)}</span>`, r.t)}<span class="nm">${esc(r.n)}</span></td><td class="small">${esc(r.s)}</td><td class="num">${leadValue(k, r)}</td><td class="num">${pct(r.p, 0)}</td>${sinceCells(k, r)}<td class="num">${isNum(r.px) ? "$" + r.px.toFixed(2) : "–"}</td><td class="num">${money(r.mc)}</td></tr>`).join("") +
        `</tbody></table></div>` + (SLOW[k] ? `<p class="small">* Recalculated monthly; the date is the first month end of the current unbroken run. "≤" means it was flagged at every month end checked (12 months), so it started earlier.</p>` : "") : `<p class="empty">Nothing flagged today.</p>`;
      const lv = x.live;
      h += `<h2>${xb(k, esc(x.label))} ${leadStatus((B[k] || {}).status || x.status)}</h2><p class="small">${esc(x.short)}. Data for ${pct(x.coverage, 0)} of liquid stocks. ${x.n_bull} flagged${x.n_bull > x.bull.length ? `, top ${x.bull.length} shown` : ""}.
        ${lv && lv.n ? `Live record since ${fdate(lv.started)}: ${lv.n} new signals, average ${spct(lv.avg_ret)} since the signal vs ${spct(lv.avg_spy)} for SPY.` : `Live record: tracking new signals since ${fdate(L.tracking_started)}.`}</p>` + table(x.bull, "Flagged");
      if (x.bear && x.bear.length) {
        const bb = (B[k] || {}).bear_status || {}, bm = ((B[k] || {}).bearish_minus_universe || {}).full || {};
        const label = k === "insider_buying" ? "net opportunistic insider selling" : "names at the other extreme";
        h += `<details${k === "insider_buying" ? " open" : ""}><summary>Bearish side: ${x.n_bear} ${label} · ${leadStatus(bb.status || x.bear_status)} ${isNum(bm.ann_mean) ? `<span class="small">${spct(bm.ann_mean)} a year vs universe, t ${num(bm.t, 1)}</span>` : ""}</summary>${table(x.bear, "Bearish")}</details>`;
      }
    });
    return h;
  }

  function pageBubbles() {
    const b = S.data.bubble;
    if (!b || !b.rows) return unavailable("Bubble watch");
    const flagged = b.rows.filter((r) => r.status === "amber" || r.status === "red");
    let h = `<h1>Bubble watch</h1><p class="lede">Sector and industry ETFs whose 2-year return beat SPY by ${esc(b.threshold || "")}. Greenwood, Shleifer & You (2019) find such run-ups crash more often, especially with acceleration, rising volatility and issuance. ${info("Greenwood, Shleifer & You (2019), 'Bubbles for Fama', JFE")}</p>
      <div class="kv"><div><span>${xb("bubble_watch", "ETFs flagged")}</span><b>${b.n_flagged ?? "–"} of ${b.n_valid ?? "–"}</b></div><div><span>Share flagged</span><b>${pct(b.flagged_share, 0)}</b></div><div><span>Stretched trigger (20%)</span><b>${b.stretched ? "Hit" : "Not hit"}</b></div></div>`;
    if (!flagged.length) h += `<p class="verdict">No sector or industry ETF is in bubble territory right now.</p>`;
    h += `<div class="table-wrap"><table><thead><tr><th>ETF</th><th>Status ${xi("bubble_watch", "bubble status")}</th><th class="num">2-yr return ${xi("b_ret2y", "two-year return")}</th><th class="num">vs SPY ${xi("b_excess", "return vs SPY")}</th><th class="num">Last 6 mo share ${xi("b_accel", "last six months' share")}</th><th class="num">Vol ratio ${xi("b_volratio", "volatility ratio")}</th><th>Warnings</th></tr></thead><tbody>` +
      b.rows.map((r) => `<tr><td>${xb("bubble", `<span class="tk">${esc(r.ticker)}</span>`, r.ticker)}<span class="nm">${esc(r.name)}</span></td><td>${st(r.status)}</td>
        <td class="num ${cls(r.return_2y)}">${spct(r.return_2y, 0)}</td><td class="num ${cls(r.excess_2y)}">${spct(r.excess_2y, 0)}</td>
        <td class="num">${pct(r.accel_share, 0)}</td><td class="num">${num(r.vol_ratio)}</td>
        <td>${(r.warnings || []).map((w) => `<span class="flag">${esc(w)}</span>`).join("") || (r.reason ? `<span class="small">${esc(r.reason)}</span>` : "")}</td></tr>`).join("") + `</tbody></table></div>`;
    return h;
  }

  function stockRows() {
    const s = S.data.stocks; if (!s) return [];
    const f = S.filters, q = f.q.trim().toUpperCase();
    let rows = s.rows.filter((r) => (!f.sector || r.s === f.sector) && (!f.flag || (r.fl || []).includes(f.flag)) &&
      (!f.liquid || !(r.fl || []).includes("illiquid")) && (!q || r.t.includes(q) || String(r.n || "").toUpperCase().includes(q)));
    const k = S.sort.key, dir = S.sort.dir;
    const val = (r) => k === "score" ? r.m[S.mode] : k === "t" ? r.t : k === "mc" ? r.mc : k === "fl" ? (r.fl || []).length || null : r.f[k];
    rows.sort((a, b) => { const va = val(a), vb = val(b); if (va === vb) return 0; if (va == null) return 1; if (vb == null) return -1; return (va > vb ? 1 : -1) * dir; });
    return rows;
  }
  function pageStocks() {
    const s = S.data.stocks;
    if (!s) return unavailable("Stock rankings");
    const sectors = [...new Set(s.rows.map((r) => r.s).filter(Boolean))].sort();
    const flags = [...new Set(s.rows.flatMap((r) => r.fl || []))].sort();
    const w = s.modes[S.mode].weights;
    const cols = ["value", "momentum", "profitability", "investment", "issuance", "insiders", "short_interest", "sue", "lowvol"];
    const short = { value: "Val", momentum: "Mom", profitability: "Prof", investment: "Inv", issuance: "Iss", insiders: "Ins", short_interest: "SI", sue: "SUE", lowvol: "LowV" };
    const rows = stockRows();
    const LONG = { score: "composite score", value: "value", momentum: "momentum", profitability: "profitability", investment: "investment", issuance: "net share issuance", insiders: "insider buying", short_interest: "short interest", sue: "earnings surprise", lowvol: "low volatility" };
    const head = (k, label, numc = true) => `<th class="${numc ? "num" : ""}" aria-sort="${S.sort.key === k ? (S.sort.dir > 0 ? "ascending" : "descending") : "none"}"><button type="button" data-sort="${k}">${esc(label)}${S.sort.key === k ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""}</button>${LONG[k] ? " " + xi(k, LONG[k]) : ""}</th>`;
    let h = `<h1>Stock rankings</h1><p class="lede">S&P 1500, scored within GICS sector (percentile, 100% = best in sector). Weights for ${esc(MODES[S.mode])}: ${Object.entries(w).map(([k, v]) => esc(k.replace("_", " ")) + " " + Math.round(v * 100) + "%").join(", ")}.${s.penalty_active ? " Euphoric-sentiment penalty is active for small, young, volatile and unprofitable names." : ""}</p>
      <div class="controls">${modeSeg()}
        <select data-filter="sector" aria-label="Sector"><option value="">All sectors</option>${sectors.map((x) => `<option ${S.filters.sector === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
        <select data-filter="flag" aria-label="Flag"><option value="">Any flags</option>${flags.map((x) => `<option ${S.filters.flag === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
        <label class="chk"><input type="checkbox" data-filter="liquid" ${S.filters.liquid ? "checked" : ""}> Liquid only ($5M+/day, price > $5)</label>
        <input type="search" data-filter="q" placeholder="Search ticker or name" value="${esc(S.filters.q)}" aria-label="Search">
      </div>
      <p class="small">${rows.length} stocks. Tap a row for the factor breakdown. Data coverage: ${Object.entries(s.coverage || {}).map(([k, v]) => esc(short[k] || k) + " " + pct(v, 0)).join(", ")}.</p>
      <div class="table-wrap"><table><thead><tr><th>#</th>${head("t", "Stock", false)}${head("score", "Score")}${cols.map((c) => head(c, short[c])).join("")}${head("fl", "Flags", false)}</tr></thead><tbody>`;
    rows.slice(0, S.stockLimit).forEach((r, i) => {
      const sc = r.m[S.mode];
      h += `<tr class="clickable" data-open="${esc(r.t)}" tabindex="0" aria-expanded="${S.open.has(r.t)}"><td class="num small">${i + 1}</td><td><span class="tk">${esc(r.t)}</span><span class="nm">${esc(r.n)} · ${esc(r.s)}</span></td>
        <td class="num"><b>${isNum(sc) ? Math.round(sc * 100) : "–"}</b></td>
        ${cols.map((c) => `<td class="num" ${w[c] ? "" : 'style="opacity:.55"'}>${isNum(r.f[c]) ? `<span class="bar" title="${pct(r.f[c], 0)}"><i style="width:${(r.f[c] * 100).toFixed(0)}%"></i></span>` : "–"}</td>`).join("")}
        <td>${(r.fl || []).map((x) => `<span class="flag">${esc(x)}</span>`).join("")}</td></tr>`;
      if (S.open.has(r.t)) {
        const x = r.raw || {};
        const sig = (c) => xb(c, `${esc(LONG[c] ? cap(LONG[c]) : c)} <b>${isNum(r.f[c]) ? Math.round(r.f[c] * 100) + "%" : "–"}</b>`, r.t, "chip", false);
        const kvx = (k, label, v, c = "") => `<div><span>${xb(k, esc(label), r.t)}</span><b class="${c}">${v}</b></div>`;
        h += `<tr class="detail"><td></td><td colspan="${cols.length + 3}"><p><b>${xb("score", "Why ranked", r.t)}:</b> ${esc((r.why || {})[S.mode] || "")}</p>
          <p class="small">Sector percentiles (tap one for what it means for ${esc(r.t)}):</p><div class="chips">${cols.map(sig).join("")}</div>
          <div class="kv"><div><span>Price</span><b>${isNum(r.px) ? "$" + r.px.toFixed(2) : "–"}</b></div><div><span>Market cap</span><b>${money(r.mc)}</b></div><div><span>$ volume / day</span><b>${money(r.dv)}</b></div>
          ${kvx("raw_bm", "Book / market", num(x.bm))}${kvx("raw_ep", "Earnings yield", pct(x.ep))}${kvx("raw_mom", "12–1 momentum", spct(x.mom), cls(x.mom))}
          ${kvx("raw_gpa", "Gross profit / assets", pct(x.gpa))}${kvx("raw_opa", "Operating income / assets", pct(x.opa))}${kvx("raw_ag", "Asset growth", spct(x.ag))}${kvx("raw_iss", "Net issuance", spct(x.iss))}
          ${kvx("raw_ins", "Net opportunistic insiders", isNum(x.ins) ? x.ins : "–")}${kvx("raw_si", "Short interest", pct(x.si))}${kvx("raw_dtc", "Days to cover", num(x.dtc, 1))}
          ${kvx("raw_sue", "SUE", num(x.sue))}${kvx("raw_vol", "Volatility", pct(x.vol, 0))}${kvx("raw_beta", "Beta", num(x.beta))}</div>
          <p class="small">Sub-industry: ${esc(r.si || "–")}. Sources: SEC companyfacts (filed dates), Yahoo prices, SEC Form 4, FINRA short interest.</p></td></tr>`;
      }
    });
    h += `</tbody></table></div>`;
    if (rows.length > S.stockLimit) h += `<div class="more"><button class="btn" type="button" data-more>Show ${Math.min(100, rows.length - S.stockLimit)} more</button></div>`;
    return h;
  }

  function pageEtfs() {
    const e = S.data.etfs;
    if (!e || !e.modes) return unavailable("ETF rankings");
    const rows = e.modes[S.mode] || [];
    let h = `<h1>ETF rankings</h1><p class="lede">Momentum and trend with a bubble penalty${S.mode === "absolute_return" ? ", using volatility-adjusted momentum" : ""}; look-through is the holdings-weighted stock score where daily holdings are free (SPDR funds). ${info("Moskowitz & Grinblatt (1999); Moskowitz, Ooi & Pedersen (2012)")}</p>
      <div class="controls">${modeSeg()}</div>
      <div class="table-wrap"><table><thead><tr><th>#</th><th>ETF</th><th class="num">Score ${xi("etf", "ETF score")}</th><th class="num">12–1 mom ${xi("etf_mom", "ETF momentum")}</th><th>Trend ${xi("etf_trend", "ETF trend")}</th><th class="num">Look-through ${xi("etf_look", "look-through score")}</th><th>Bubble ${xi("etf_bubble", "bubble flag")}</th><th class="num">Expense ${xi("etf_er", "expense ratio")}</th><th class="num">$ vol/day ${xi("etf_dv", "dollar volume")}</th></tr></thead><tbody>` +
      rows.map((r, i) => `<tr><td class="num small">${i + 1}</td><td>${xb("etf", `<span class="tk">${esc(r.t)}</span>`, r.t)}<span class="nm">${esc(r.name)}</span></td>
        <td class="num"><b>${isNum(r.score) ? Math.round(r.score * 100) : "–"}</b></td><td class="num ${cls(r.momentum)}">${spct(r.momentum, 0)}</td>
        <td>${st(r.trend === 1 ? "green" : r.trend === 0 ? "red" : "amber", r.trend === 1 ? "Up" : r.trend === 0 ? "Down" : "Mixed")}</td>
        <td class="num">${isNum(r.lookthrough) ? Math.round(r.lookthrough * 100) + ` <span class="small">(${pct(r.lookthrough_coverage, 0)})</span>` : "–"}</td>
        <td>${r.bubble && r.bubble !== "n/a" ? st(r.bubble) : '<span class="small">n/a</span>'}</td><td class="num">${isNum(r.er) ? r.er.toFixed(2) + "%" : "–"}</td><td class="num">${money(r.dollar_volume)}</td></tr>`).join("") +
      `</tbody></table></div>${e.unavailable && e.unavailable.length ? `<p class="small">No price history yet: ${esc(e.unavailable.join(", "))}.</p>` : ""}`;
    return h;
  }

  function pagePortfolio() {
    const p = S.data.portfolio;
    if (!p || !p.modes) return unavailable("Model portfolios");
    const m = p.modes[S.mode] || {};
    let h = `<h1>Model portfolio</h1><div class="controls">${modeSeg()}</div>`;
    if (m.status === "unavailable") return h + `<p class="empty">${esc(m.reason)}</p>`;
    if (m.research_only) h += `<p class="verdict">Research only. Market-neutral lost money at the stock level since 2011 (and has been roughly flat at the factor level since 2010), so this portfolio is tracked but not recommended.</p>`;
    const s = m.summary || {}, t = m.turnover || {}, meta = m.meta || {};
    h += `<div class="kv"><div><span>${xb("p_holdings", "Holdings")}</span><b>${s.n ?? "–"}</b></div><div><span>${xb("p_exposure", "Gross / net exposure")}</span><b>${pct(s.gross, 0)} / ${pct(s.net, 0)}</b></div>
      <div><span>${xb("p_beta", "Portfolio beta")}</span><b>${num(s.beta)}</b></div><div><span>${xb("p_turnover", "Turnover vs last run")}</span><b>${t.first_build ? "First build" : pct(t.turnover, 0)}</b></div>
      <div><span>${xb("p_cost", "Est. trading cost")}</span><b>${num(t.cost_bps, 1)} bps</b></div>${isNum(meta.vol_target_exposure) ? `<div><span>${xb("p_voltarget", "Vol-target exposure")}</span><b>${num(meta.vol_target_exposure)}×</b></div>` : ""}</div>
      <p class="small">Rules: liquidity floor $5M/day and price > $5, max ${pct(0.05, 0)} per name, 25% sector cap, turnover limited by keeping holdings that still rank inside 1.5× the target count. Regime: ${esc(p.regime && p.regime.label)}.</p>`;
    const sc = m.scenarios || {};
    h += `<h2>${xb("p_scenarios", "Scenarios (12 months)")}</h2><p class="lede small">Base rates nudged by the dials: ${esc((p.scenario_probs.notes || []).join("; ") || "no nudges active")}. Each holding is broken into market beta, sector, factor and rate exposures.</p>
      <div class="table-wrap"><table><thead><tr><th>Scenario</th><th class="num">Probability</th><th class="num">Basket return</th><th>Biggest detractors</th></tr></thead><tbody>` +
      Object.entries(sc.scenarios || {}).map(([k, v]) => `<tr><td>${esc(cap(k.replace("_", " ")))}</td><td class="num">${pct(v.prob, 0)}</td><td class="num ${cls(v.return)}">${spct(v.return)}</td><td class="small">${esc((v.top_detractors || []).join(", "))}</td></tr>`).join("") +
      `<tr><td><b>Probability-weighted</b></td><td></td><td class="num ${cls(sc.expected)}"><b>${spct(sc.expected)}</b></td><td></td></tr></tbody></table></div>`;
    h += `<h2>${xb("p_stress", "Historical stress tests")}</h2><div class="table-wrap"><table><thead><tr><th>Window</th><th class="num">Replay (today's names)</th><th class="num">Coverage</th><th class="num">Beta estimate</th><th class="num">SPY</th></tr></thead><tbody>` +
      Object.entries(m.stress || {}).map(([k, v]) => `<tr><td>${esc(k)}<span class="nm">${esc((v.window || []).join(" to "))}</span></td><td class="num ${cls(v.replay_return)}">${spct(v.replay_return)}</td><td class="num">${pct(v.coverage, 0)}</td><td class="num ${cls(v.beta_estimate)}">${spct(v.beta_estimate)}</td><td class="num ${cls(v.spy_return)}">${spct(v.spy_return)}</td></tr>`).join("") +
      `</tbody></table></div><p class="small">Replay uses today's holdings that traded in each window, so it flatters (survivors only). The beta estimate is portfolio beta × SPY's move.</p>`;
    const mc = m.monte_carlo || {};
    h += `<h2>${xb("p_mc", "Monte Carlo, 1 year")}</h2><div class="kv"><div><span>Median</span><b class="${cls(mc.median)}">${spct(mc.median)}</b></div><div><span>5th percentile</span><b class="${cls(mc.p5)}">${spct(mc.p5)}</b></div>
      <div><span>Chance of a 20%+ loss</span><b>${pct(mc.prob_loss_20)}</b></div><div><span>Expected max drawdown</span><b>${spct(mc.expected_max_drawdown)}</b></div></div>${hist(mc.hist)}
      <p class="small">${esc(mc.paths)} paths of daily Student-t (${esc(mc.t_df)} d.f.) returns at ${pct(mc.annual_vol)} annual volatility and a ${spct(mc.annual_mu)} drift from the scenario mix.</p>`;
    const hold = m.holdings || [];
    h += `<h2>Holdings</h2><div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Side</th><th class="num">Weight</th><th>Sector</th><th class="num">Score</th><th class="num">Beta</th><th>Why</th></tr></thead><tbody>` +
      hold.map((x) => `<tr><td class="tk">${esc(x.ticker)}</td><td>${esc(x.side || "long")}</td><td class="num ${cls(x.weight)}">${pct(x.weight, 2)}</td><td class="small">${esc(x.sector)}</td><td class="num">${isNum(x.score) ? Math.round(x.score * 100) : "–"}</td><td class="num">${num(x.beta)}</td><td class="small">${esc(x.why || "")}</td></tr>`).join("") +
      `</tbody></table></div>`;
    return h;
  }

  function statHead() {
    return [["CAGR", "m_cagr"], ["Vol", "m_vol"], ["Sharpe", "m_sharpe"], ["Max DD", "m_maxdd"], ["Hit rate", "m_hit"]]
      .map(([l, k]) => `<th class="num">${l} ${xi(k, l)}</th>`).join("");
  }
  function statRow(name, s, extra = "") {
    if (!s || !isNum(s.cagr)) return `<tr><td>${esc(name)}</td><td colspan="6" class="small">Unavailable</td></tr>`;
    return `<tr><td>${esc(name)}</td><td class="num ${cls(s.cagr)}">${spct(s.cagr)}</td><td class="num">${pct(s.ann_vol)}</td><td class="num">${num(s.sharpe)}</td><td class="num neg">${spct(s.max_dd, 0)}</td><td class="num">${pct(s.hit_rate, 0)}</td>${extra}</tr>`;
  }
  function compareSection() {
    const c = S.data.compare;
    if (!c || !c.factor || !c.factor.verdicts) return "";
    const g = (x) => isNum(x) ? (x > 0 ? "+" : "") + x.toFixed(2) : "–";
    const V = c.factor.verdicts;
    let h = `<h2>${xb("m_v2", "Version 2 vs version 1")}</h2><p class="lede small">${esc(c.rule)}</p>
      <div class="table-wrap"><table><thead><tr><th>Change</th><th class="num">Abs. return Sharpe, 1963–2010 (added / kept)</th><th class="num">Long-term Sharpe, 1963–2010</th><th class="num">Abs. return Sharpe, 2011+</th><th>Holds up?</th></tr></thead><tbody>`;
    Object.entries(V).forEach(([k, v]) => {
      const a = v.absolute_return, l = v.long_term;
      h += `<tr><td>${esc(v.label)}</td><td class="num">${a.changed ? g(a.sharpe_gain_added_to_v1["1963-2010"]) + " / " + g(a.sharpe_gain_kept_in_v2["1963-2010"]) : "no effect"}</td>
        <td class="num">${l.changed ? g(l.sharpe_gain_added_to_v1["1963-2010"]) + " / " + g(l.sharpe_gain_kept_in_v2["1963-2010"]) : "no effect"}</td>
        <td class="num">${a.changed ? g(a.sharpe_gain_added_to_v1["2011-now"]) + " / " + g(a.sharpe_gain_kept_in_v2["2011-now"]) : "–"}</td>
        <td>${v.holds_up_1963_2010 ? st("green", "Yes") : st("red", "No")}${v.adopted === false ? ' <span class="small">(not adopted)</span>' : ""}</td></tr>`;
    });
    h += `</tbody></table></div>`;
    const fv = c.factor.variants;
    const row = (name) => ["absolute_return", "long_term"].map((m) => ["1963-2010", "2011-now"].map((p) => {
      const x = (fv[name] && fv[name][m] && fv[name][m][p]) || {};
      return `<td class="num">${pct(x.cagr)}</td><td class="num">${num(x.sharpe)}</td><td class="num neg">${pct(x.max_dd, 0)}</td>`;
    }).join("")).join("");
    h += `<div class="table-wrap"><table><thead><tr><th rowspan="2">Factor level</th><th colspan="6">Absolute return: 1963–2010 | 2011+ (CAGR, Sharpe, max DD)</th><th colspan="6">Long-term: 1963–2010 | 2011+</th></tr></thead><tbody>
      <tr><td>Version 1</td>${row("v1")}</tr><tr><td>Version 2</td>${row("v2")}</tr></tbody></table></div>`;
    const sr = c.stock && c.stock.runs;
    if (sr) {
      h += `<div class="table-wrap"><table><thead><tr><th>Stock level since 2011</th><th class="num">Abs. return CAGR</th><th class="num">Sharpe</th><th class="num">Max DD</th><th class="num">Long-term CAGR</th><th class="num">vs SPY</th><th class="num">SUE spread (t)</th><th class="num">Insider spread (t)</th></tr></thead><tbody>` +
        Object.entries(sr).map(([k, x]) => { const a = x.modes.absolute_return || {}, l = x.modes.long_term || {}, su = x.signals.sue || {}, ins = x.signals.insiders || {};
          return `<tr><td>${esc(k)}${isNum(x.membership_coverage) ? ` <span class="small">(${pct(x.membership_coverage, 0)} of members priced)</span>` : ""}</td><td class="num">${pct(a.cagr)}</td><td class="num">${num(a.sharpe)}</td><td class="num neg">${pct(a.max_dd, 0)}</td><td class="num">${pct(l.cagr)}</td><td class="num ${cls(l.excess_vs_spy)}">${spct(l.excess_vs_spy)}</td><td class="num">${spct(su.spread)} (${num(su.t, 1)})</td><td class="num">${spct(ins.spread)} (${num(ins.t, 1)})</td></tr>`; }).join("") +
        `</tbody></table></div>`;
    }
    return h;
  }

  function pageBacktests() {
    const b = S.data.backtest, v = S.data.validation, sb = S.data.stock_backtest, tr = S.data.track;
    let h = `<h1>Backtests and track record</h1><p class="lede">Does each piece do what it claims? Factor results use Ken French's CRSP portfolios (survivorship-free, since 1963). Stock-level results run the actual screener on point-in-time SEC data but on today's S&P 1500 (survivorship-biased).</p><div class="controls">${modeSeg()}</div>`;
    if (!b || !b.factor) return h + unavailable("The backtest");
    h += compareSection();
    const f = b.factor, md = f.modes[S.mode];
    if (md) {
      h += `<h2>${esc(MODES[S.mode])}: factor-level proxy vs market</h2><p class="small">${esc(md.note)}</p>
        ${lineChart([{ name: "Strategy", color: "var(--series-1)", points: curve(md.curve) }, { name: "US market (CRSP; SPY proxy)", color: "var(--bench)", points: curve(md.market_curve) }], { log: true, fmt: (x) => x >= 10 ? x.toFixed(0) : x.toFixed(1), tipFmt: (x) => "$" + num(x, 2), label: "Growth of $1, log scale" })}
        <div class="table-wrap"><table><thead><tr><th></th>${statHead()}</tr></thead><tbody>
        ${statRow("Strategy", md.stats)}${statRow("Market", md.market)}${statRow("Strategy minus market", md.excess_vs_market)}</tbody></table></div>
        <div class="kv"><div><span>${xb("m_alpha", "CAPM alpha / yr")}</span><b class="${cls(md.capm && md.capm.alpha_ann)}">${spct(md.capm && md.capm.alpha_ann)}</b></div><div><span>${xb("m_tstat", "Alpha t-stat")}</span><b>${num(md.capm && md.capm.alpha_t)}</b></div><div><span>${xb("m_beta", "Beta")}</span><b>${num(md.capm && md.capm.beta)}</b></div>
        <div><span>${xb("m_roll10", "Rolling 10-yr windows beating market")}</span><b>${pct(md.rolling_10y_beat_market, 0)}</b></div><div><span>Since ${esc(md.post_1995_or_pub && md.post_1995_or_pub.since)}: strategy / market CAGR</span><b>${pct(md.post_1995_or_pub && md.post_1995_or_pub.strategy.cagr)} / ${pct(md.post_1995_or_pub && md.post_1995_or_pub.market.cagr)}</b></div></div>`;
    }
    h += `<h2>Each signal: before and after publication</h2><p class="lede small">Long/short factor returns, gross; net subtracts estimated trading costs. McLean & Pontiff (2016) find anomaly returns fall about a third after publication. Health compares the last 36 months with the full record.</p>
      <div class="table-wrap"><table><thead><tr><th>Signal (proxy)</th><th class="num">Sharpe gross ${xi("m_sharpe", "Sharpe ratio")}</th><th class="num">Sharpe net</th><th class="num">t (NW) ${xi("m_tstat", "t-statistic")}</th><th class="num">Pre-pub mean/yr</th><th class="num">Post-pub mean/yr</th><th class="num">Post ÷ pre ${xi("m_mp", "post- vs pre-publication")}</th><th>36-mo health ${xi("m_health", "36-month health")}</th></tr></thead><tbody>` +
      Object.entries(f.signals).map(([k, x]) => { const mp = x.mclean_pontiff || {}; return `<tr><td>${xk(k === "lowbeta" ? "lowvol" : k, esc(k))}<span class="nm">${esc(x.proxy)}</span></td><td class="num">${num(x.long_short_gross.sharpe)}</td><td class="num">${num(x.long_short_net.sharpe)}</td><td class="num">${num(x.t_nw, 1)}</td>
        <td class="num ${cls(mp.pre && mp.pre.ann_mean)}">${spct(mp.pre && mp.pre.ann_mean)}</td><td class="num ${cls(mp.post && mp.post.ann_mean)}">${spct(mp.post && mp.post.ann_mean)}</td><td class="num">${num(mp.post_over_pre)}</td><td>${st(x.health.status, x.health.status === "green" ? "Healthy" : x.health.status === "amber" ? "Fading" : x.health.status === "red" ? "Decayed" : "n/a")} <span class="small">${num(x.health.rolling_sharpe_36m)}</span></td></tr>`; }).join("") +
      `</tbody></table></div>`;
    const cg = f.crash_guard;
    if (cg && cg.mom_unguarded) h += `<p class="small">Momentum crash guard: on in ${esc(cg.months_on)} months; momentum Sharpe ${num(cg.mom_unguarded.sharpe)} unguarded vs ${num(cg.mom_guarded.sharpe)} guarded; worst month ${spct(cg.worst_month_unguarded)} vs ${spct(cg.worst_month_guarded)}.</p>`;

    if (sb && sb.modes && sb.modes[S.mode]) {
      const x = sb.modes[S.mode];
      h += `<h2>Stock-level backtest (actual screener, ${esc((x.stats || {}).start || "")} on)</h2>
        ${lineChart([{ name: "Screener", color: "var(--series-1)", points: curve(x.curve) }, { name: "SPY", color: "var(--bench)", points: curve(x.spy_curve) }, { name: "Universe, equal-weighted", color: "var(--series-2)", points: curve(x.universe_curve), dash: true }], { log: true, tipFmt: (y) => "$" + num(y, 2), label: "Growth of $1" })}
        <div class="table-wrap"><table><thead><tr><th></th>${statHead()}</tr></thead><tbody>
        ${statRow("Screener (net of costs)", x.stats)}${statRow("SPY", x.spy)}${statRow("Screener minus universe", x.excess_vs_universe_ew)}</tbody></table></div>
        <p class="small">Average monthly turnover ${pct(x.avg_turnover, 0)}. ${esc((sb.notes || []).join(" "))}</p>`;
      if (sb.universe && sb.universe.survivorship_gap) h += `<p class="verdict">${xb("m_survivorship", "Survivorship check")}: today's universe, equal-weighted, returned ${spct(sb.universe.equal_weight.cagr)} a year vs ${spct(sb.universe.rsp && sb.universe.rsp.cagr)} for RSP. The gap (${spct(sb.universe.survivorship_gap.cagr)}/yr) is roughly how much the backtest universe is flattered; judge the screener by its spread over the universe.</p>`;
      h += `<details><summary>Stock-level signal spreads</summary><div class="table-wrap"><table><thead><tr><th>Signal</th><th class="num">Top 30% − bottom 30% / yr</th><th class="num">t (NW) ${xi("m_tstat", "t-statistic")}</th><th class="num">Mean rank IC ${xi("m_ic", "rank IC")}</th></tr></thead><tbody>` +
        Object.entries(sb.signals || {}).map(([k, y]) => y.top30_minus_bottom30 ? `<tr><td>${xk(k, esc(k))}</td><td class="num ${cls(y.top30_minus_bottom30.ann_mean)}">${spct(y.top30_minus_bottom30.ann_mean)}</td><td class="num">${num(y.t_nw, 1)}</td><td class="num">${num(y.ic_mean, 3)}</td></tr>` : `<tr><td>${esc(k)}</td><td colspan="3" class="small">${esc(y.reason || "unavailable")}</td></tr>`).join("") + `</tbody></table></div></details>`;
    }

    if (v) h += validationSection(v, b);
    h += `<h2>${xb("m_track", "Live track record")}</h2>`;
    if (tr && tr.by_mode) {
      h += `<div class="table-wrap"><table><thead><tr><th>Mode</th><th class="num">Snapshots</th><th class="num">Mean excess vs SPY</th><th class="num">Hit rate</th><th class="num">Mature (21d+)</th></tr></thead><tbody>` +
        Object.entries(tr.by_mode).map(([k, y]) => `<tr><td>${esc(MODES[k] || k)}</td><td class="num">${y.snapshots}</td><td class="num ${cls(y.mean_excess)}">${spct(y.mean_excess)}</td><td class="num">${pct(y.hit_rate, 0)}</td><td class="num">${y.mature_snapshots}</td></tr>`).join("") + `</tbody></table></div>`;
    } else h += `<p class="empty">The first snapshot is taken on the first nightly run; results build up from there.</p>`;
    if (tr) h += `<p class="small">${esc(tr.note || "")}</p>`;
    return h;
  }
  function validationSection(v, b) {
    let h = `<h2>Do the market dials work?</h2>`;
    const r = v.regime && v.regime.by_label;
    if (r) h += `<h3>${xb("regime", "Regime label")}, forward 12 months (since ${esc(b.regime_history && b.regime_history.since)})</h3><div class="table-wrap"><table><thead><tr><th>Label</th><th class="num">Months</th><th class="num">Mean return</th><th class="num">Volatility</th><th class="num">P(20% drawdown)</th></tr></thead><tbody>` +
      ["Calm", "Stretched", "Fragile"].map((k) => r[k] ? `<tr><td>${esc(k)}</td><td class="num">${r[k].months}</td><td class="num ${cls(r[k].fwd12_mean)}">${spct(r[k].fwd12_mean)}</td><td class="num">${pct(r[k].fwd_vol)}</td><td class="num">${pct(r[k].prob_dd20, 0)}</td></tr>` : "").join("") +
      `<tr><td>All months</td><td class="num">${v.regime.all.months}</td><td class="num">${spct(v.regime.all.fwd12_mean)}</td><td class="num">${pct(v.regime.all.fwd_vol)}</td><td class="num">${pct(v.regime.all.prob_dd20, 0)}</td></tr></tbody></table></div>`;
    const yc = v.yield_curve || {};
    h += `<h3>Other dials</h3><div class="table-wrap"><table><thead><tr><th>Dial</th><th>Test</th><th class="num">Result</th></tr></thead><tbody>
      <tr><td>${xb("yield_curve", "Yield curve")}</td><td>AUC, recession starts within 12 months (0.5 = coin flip) ${xi("m_auc", "AUC")}</td><td class="num">${num(yc.auc_recession_starts_within_12m)} <span class="small">(post-1996: ${num(yc.auc_post_publication)})</span></td></tr>
      <tr><td>${xb("ebp", "Excess bond premium")}</td><td>AUC, recession starts within 12 months ${xi("m_auc", "AUC")}</td><td class="num">${num(v.ebp && v.ebp.auc_recession_starts_within_12m)}</td></tr>
      <tr><td>${xb("cape", "CAPE")}</td><td>Out-of-sample R², 10-year real return vs historical mean ${xi("m_oosr2", "out-of-sample R²")}</td><td class="num">${num(v.cape && v.cape.oos_r2_vs_hist_mean)}</td></tr>
      <tr><td>${xb("vrp", "Variance risk premium")}</td><td>Next-quarter return, lowest → highest VRP quintile</td><td class="num">${((v.vrp && v.vrp.fwd_3m_by_vrp_quintile) || []).map((x) => spct(x, 1)).join(" / ") || "–"}</td></tr>
      <tr><td>${xb("bubble_watch", "Bubble watch")}</td><td>P(40% crash in 2 yrs) after a 100% run-up vs base rate</td><td class="num">${pct(v.bubbles && v.bubbles.crash_prob_after_100pct, 0)} vs ${pct(v.bubbles && v.bubbles.base_crash_prob, 0)} <span class="small">(${v.bubbles && v.bubbles.n_events} events)</span></td></tr>
      <tr><td>${xb("trend", "Trend (stocks)", "stocks")}</td><td>Sharpe, trend rule vs buy-and-hold</td><td class="num">${num(v.trend && v.trend.stocks && v.trend.stocks.trend.sharpe)} vs ${num(v.trend && v.trend.stocks && v.trend.stocks.buy_hold.sharpe)}</td></tr>
      </tbody></table></div>`;
    if (yc.episodes_above_red && yc.episodes_above_red.length) h += `<details><summary>Yield-curve red episodes</summary><p class="small">${yc.episodes_above_red.map((e) => esc(e.signal_start + " → " + e.signal_end + (e.recession_within_24m === null ? "" : e.recession_within_24m ? " (recession followed)" : " (no recession)"))).join("<br>")}</p></details>`;
    const c = v.credit || {};
    h += `<details><summary>Credit dials (few US crises; read cautiously)</summary><p class="small">${Object.entries(c).filter(([k]) => k !== "note").map(([k, y]) => `${esc(k.replace(/_/g, " "))}: flagged ${y.months_flagged} months; 3-yr return when on ${pct(y.fwd3y_ann_when_on)} vs off ${pct(y.fwd3y_ann_when_off)}; P(30% drawdown) ${pct(y.prob_dd30_3y_on, 0)} vs ${pct(y.prob_dd30_3y_off, 0)}`).join("<br>")}<br>${esc(c.note || "")}</p></details>`;
    return h;
  }

  function pageCrashes() {
    const c = S.data.crashes;
    if (!c || !c.replay) return unavailable("The crash replay");
    const eps = c.replay.episodes || [], bt = c.buy_timing || {};
    const short = { market: "Market", long_term: "Long-term", market_neutral: "Mkt-neutral", absolute_return: "Abs. return" };
    let h = `<h1>Crash replay and buy timing</h1><p class="lede">Every US bear market since 1960 (month-end drawdowns of ${pct(c.replay.threshold, 0)} or more), replayed as if this screener had been running: what the dials said beforehand, how each mode held up, and which buy rules would have worked. Buy rules were chosen on 1960–1999 only and judged on 2000 onward.</p>`;
    h += `<h2>How each strategy did, peak to trough</h2><div class="table-wrap"><table><thead><tr><th>Bear market</th><th class="num">Depth</th><th class="num">Months down / recover</th>${Object.values(short).map((x) => `<th class="num">${esc(x)}</th>`).join("")}<th>Regime 12 mo before → at peak</th></tr></thead><tbody>` +
      eps.map((e) => `<tr><td style="white-space:nowrap"><b>${esc(e.peak)}</b><span class="nm">to ${esc(e.trough)}</span></td><td class="num neg">${spct(e.depth, 0)}</td><td class="num">${e.months_down} / ${e.months_to_recover ?? "–"}</td>
        ${Object.keys(short).map((m) => { const x = (e.performance[m] || {}).peak_to_trough; return `<td class="num ${cls(x)}">${spct(x, 0)}</td>`; }).join("")}
        <td class="small">${esc((e.dials_12m_before || {}).label || "–")} → ${esc((e.dials_at_peak || {}).label || "–")}</td></tr>`).join("") + `</tbody></table></div>`;
    h += `<h2>${xb("c_warning", "Did the dials warn ahead of the peak?")}</h2><p class="small">Months before each peak that a warning first fired (within the prior 24 months). Blank = no warning.</p>`;
    const wnames = Object.keys((eps[0] || {}).warning_lead_months || {});
    h += `<div class="table-wrap"><table><thead><tr><th>Warning</th>${eps.map((e) => `<th class="num">${esc(e.peak.slice(0, 4))}</th>`).join("")}<th class="num">Followed by a bear within 24 mo</th></tr></thead><tbody>` +
      wnames.map((w) => { const fa = (c.replay.false_alarms || {})[w] || {}; return `<tr><td>${esc(w)}</td>${eps.map((e) => { const v = e.warning_lead_months[w]; return `<td class="num">${v === null || v === undefined ? "" : v}</td>`; }).join("")}<td class="num">${pct(fa.followed_by_bear_within_24m, 0)} <span class="small">(on ${pct(fa.share_on, 0)} of months)</span></td></tr>`; }).join("") + `</tbody></table></div><p class="small">Followed-by-a-bear rate vs ${pct((c.replay.false_alarms || {})._base_rate, 0)} for any random month. ${esc(c.replay.note)}</p>`;
    const te = c.replay.trend_exits || [];
    if (te.length) h += `<h3>${xb("trend", "Trend rule as an exit and re-entry signal", "stocks")}</h3><div class="table-wrap"><table><thead><tr><th>Peak</th><th class="num">Sold, months after peak</th><th class="num">Already down</th><th class="num">Bought back, months after low</th><th class="num">Above the low</th><th class="num">Loss avoided vs holding</th></tr></thead><tbody>` +
      te.map((t) => `<tr><td>${esc(t.peak)}</td><td class="num">${t.exit_months_after_peak ?? "–"}</td><td class="num">${spct(t.drawdown_at_exit, 0)}</td><td class="num">${t.reentry_months_after_trough ?? "–"}</td><td class="num">${spct(t.above_trough_at_reentry, 0)}</td><td class="num ${cls(t.loss_avoided_vs_hold)}">${spct(t.loss_avoided_vs_hold, 0)}</td></tr>`).join("") + `</tbody></table></div>`;
    if (bt.rules) {
      const ch = bt.chosen_out_of_sample || {};
      h += `<h2>${xb("buy_watch", "When to buy")}</h2><p class="verdict">Best rule chosen on 1960–1999: <b>${esc(bt.chosen_in_sample || "–")}</b>. Out of sample (2000 on) it fired ${ch.n ?? "–"} times; average next-24-month excess return ${spct(ch.fwd24_mean)} vs ${spct(ch.uncond_fwd24)} for any month; median ${esc(ch.months_from_trough_median ?? "–")} months from the bottom.</p>`;
      h += `<div class="table-wrap"><table><thead><tr><th>Rule</th><th class="num">1960–99 signals</th><th class="num">1960–99 edge, 24 mo ${xi("c_edge", "buy-rule edge")}</th><th class="num">2000+ signals</th><th class="num">2000+ edge, 12 mo</th><th class="num">2000+ edge, 24 mo</th><th class="num">2000+ hit rate</th><th class="num">Months from bottom</th></tr></thead><tbody>` +
        bt.rules.map((r) => { const a = r.in_sample || {}, o = r.out_of_sample || {}; return `<tr><td>${esc(r.rule)}${r.rule === bt.chosen_in_sample ? ' <span class="flag">chosen 1960–99</span>' : ""}${r.consistent ? ' <span class="flag">worked in both periods</span>' : ""}</td><td class="num">${a.n ?? 0}</td><td class="num ${cls(a.edge24)}">${spct(a.edge24)}</td><td class="num">${o.n ?? 0}</td><td class="num ${cls(o.edge12)}">${spct(o.edge12)}</td><td class="num ${cls(o.edge24)}">${spct(o.edge24)}</td><td class="num">${pct(o.fwd12_hit, 0)}</td><td class="num">${o.months_from_trough_median ?? "–"}</td></tr>`; }).join("") + `</tbody></table></div><p class="small">Edge = average excess return after a signal minus the average over all months in the same period. Signals are de-duplicated to one per 12 months. Negative "months from bottom" = bought before the low.</p>`;
      const cp = (bt.composite || {}).periods || {};
      if (cp["2000-2026"]) {
        h += `<h3>${xb("c_composite", "Composite buy score")} (drawdown, credit spreads, fear, cheapness; no fitting)</h3>
          <div class="table-wrap"><table><thead><tr><th>Period</th>${[1, 2, 3, 4, 5].map((q) => `<th class="num">Q${q}</th>`).join("")}<th class="num">Rank corr.</th></tr></thead><tbody>` +
          Object.entries(cp).map(([k, v]) => `<tr><td>${esc(k)} next 12 mo</td>${v.quintile_fwd12.map((x) => `<td class="num ${cls(x)}">${spct(x)}</td>`).join("")}<td class="num">${num(v.corr_fwd12)}</td></tr>`).join("") +
          `</tbody></table></div><p class="small">Q5 = strongest buy signal. Latest score: ${num(bt.composite.latest && bt.composite.latest.score)} (${pct(bt.composite.latest && bt.composite.latest.percentile, 0)} percentile, ${esc(bt.composite.latest && bt.composite.latest.date)}).</p>`;
      }
      const deps = bt.deployments || (bt.deployment ? [bt.deployment] : []);
      if (deps.length) {
        const full = (deps[0].periods || {})["1960-2026"] || {};
        h += `<h3>${xb("c_reserve", "Holding a 20% cash reserve for dips vs staying invested (1960–2026)")}</h3><div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="num">CAGR</th><th class="num">Max drawdown</th><th class="num">2000+ CAGR</th></tr></thead><tbody>` +
          deps.map((d) => { const v = d.periods["1960-2026"].buy_the_dip, o = d.periods["2000-2026"].buy_the_dip; return `<tr><td>Reserve deployed on: ${esc(d.rule)}</td><td class="num">${pct(v.cagr, 2)}</td><td class="num">${spct(v.max_dd, 0)}</td><td class="num">${pct(o.cagr, 2)}</td></tr>`; }).join("") +
          `<tr><td>Static 80% stocks / 20% T-bills</td><td class="num">${pct(full.static_80_20 && full.static_80_20.cagr, 2)}</td><td class="num">${spct(full.static_80_20 && full.static_80_20.max_dd, 0)}</td><td class="num">${pct(deps[0].periods["2000-2026"].static_80_20.cagr, 2)}</td></tr>
          <tr><td><b>100% stocks</b></td><td class="num"><b>${pct(full.fully_invested && full.fully_invested.cagr, 2)}</b></td><td class="num">${spct(full.fully_invested && full.fully_invested.max_dd, 0)}</td><td class="num">${pct(deps[0].periods["2000-2026"].fully_invested.cagr, 2)}</td></tr></tbody></table></div><p class="small">The reserve is deployed when the rule fires and rebuilt at the next market high. Every variant beats holding static cash but trails staying fully invested.</p>`;
      }
      const cur = bt.current;
      if (cur) h += `<h3>Right now (${esc(cur.date)})</h3><p>Market ${spct(cur.drawdown_from_high)} from its high, trend ${cur.trend_up ? "up" : "down"}, credit spreads at the ${pct(cur.credit_spread_pct, 0)} percentile, sentiment z ${num(cur.sentiment_z)}. Active buy rules: <b>${esc((cur.active_rules || []).join(", ") || "none")}</b>.</p>`;
    }
    return h;
  }

  function pageMethodology() {
    const m = S.data.methodology;
    if (!m) return unavailable("Methodology");
    return `<h1>Methodology and citations</h1><p class="lede">Every signal, where its data comes from, how it's computed, how often it updates, the scoring rule, and the study behind it.</p>
      <div class="table-wrap"><table><thead><tr><th>#</th><th>Signal</th><th>Source · frequency</th><th>Formula</th><th>Rule</th><th>Study</th></tr></thead><tbody>` +
      m.signals.map((s) => `<tr><td class="num">${s.id}</td><td><b>${esc(s.name)}</b></td><td class="small">${esc(s.source)} · ${esc(s.freq)}</td><td class="small">${esc(s.formula)}</td><td class="small">${esc(s.rule)}</td><td class="small">${esc(s.cite)}</td></tr>`).join("") +
      `</tbody></table></div><h2>Strategy modes</h2><ul>${Object.values(m.modes).map((x) => `<li><b>${esc(x.label)}</b>: ${Object.entries(x.weights).map(([k, w]) => esc(k.replace("_", " ")) + " " + Math.round(w * 100) + "%").join(", ")}</li>`).join("")}</ul>
      <p>${esc(m.regime_rule)}.</p><h2>Excluded on purpose</h2><p>${esc((m.excluded || []).join(", "))}: these failed replication in ${esc(m.excluded_cite)}.</p>
      <h2>Known limitations</h2><ul class="lede">
      <li>Free data only. Stock-level history uses today's S&P 1500, so it carries survivorship bias; the factor-level tests do not.</li>
      <li>Fundamentals come from XBRL (2009 on) and use the first filed value; some firms tag concepts inconsistently, so coverage is below 100%.</li>
      <li>Short interest history for listed stocks is only free from mid-2021; it is used live, not in the long backtest.</li>
      <li>SUE is dated to the earnings press release (8-K item 2.02) where one is found, otherwise to the 10-Q/10-K filing date.</li>
      <li>Baker-Wurgler sentiment is built on the full sample and ends years ago; the live proxy replaces it.</li>
      <li>Scenario shocks are calibrated assumptions in config.yaml, not forecasts.</li></ul>`;
  }

  function pageHealth() {
    const m = S.manifest;
    if (!m) return unavailable("The manifest");
    let h = `<h1>Data health</h1><p class="lede">Last run: ${esc(new Date(m.run_time).toLocaleString())} (${esc(m.job)}, ${esc(m.duration_s)}s). Version ${esc(m.version)}.</p>`;
    if (m.errors && m.errors.length) h += `<p class="verdict">${m.errors.length} pipeline stage(s) failed on the last run: ${esc(m.errors.map((e) => e.stage + ": " + e.error).join("; "))}</p>`;
    h += `<div class="table-wrap"><table><thead><tr><th>Source</th><th>Status</th><th>Last fetched</th><th>Latest data</th><th>Note</th></tr></thead><tbody>` +
      m.sources.map((s) => { const bad = s.status === "error"; const stale = s.stale; const stat = bad ? "red" : stale || s.status === "stale" || s.status === "partial" ? "amber" : "green";
        return `<tr><td><b>${esc(s.source)}</b>${s.url ? `<span class="nm"><a href="${esc(s.url)}" rel="noopener">${esc(s.url.replace(/^https?:\/\//, "").slice(0, 48))}</a></span>` : ""}</td>
        <td>${st(stat, bad ? "Error" : stale ? "Stale" : s.status === "partial" ? "Partial" : s.status === "stale" ? "Using cache" : "OK")}</td><td class="small">${esc(s.fetched_at ? ago(s.fetched_at) : "–")}</td><td class="small">${esc(s.last_obs || "–")}</td><td class="small">${esc(s.error || "")}</td></tr>`; }).join("") +
      `</tbody></table></div><h2>Recent runs</h2><div class="table-wrap"><table><thead><tr><th>Time</th><th>Job</th><th>Result</th><th class="num">Duration</th></tr></thead><tbody>` +
      (m.last_runs || []).map((r) => `<tr><td class="small">${esc(new Date(r.time).toLocaleString())}</td><td>${esc(r.job)}</td><td>${st(r.ok ? "green" : "red", r.ok ? "OK" : "Failed")}</td><td class="num">${esc(r.duration_s ?? "–")}s</td></tr>`).join("") +
      `</tbody></table></div>`;
    return h;
  }

  // ------------------------------------------------------------------ router + events
  const PAGES = { regime: pageRegime, leads: pageLeads, bubbles: pageBubbles, stocks: pageStocks, etfs: pageEtfs, portfolio: pagePortfolio,
    backtests: pageBacktests, crashes: pageCrashes, methodology: pageMethodology, health: pageHealth };
  function render() {
    const page = (location.hash || "#regime").slice(1);
    S.page = PAGES[page] ? page : "regime";
    document.querySelectorAll(".tabs a").forEach((a) => a.setAttribute("aria-current", a.dataset.page === S.page ? "page" : "false"));
    const view = $("#view");
    let html;
    try { html = PAGES[S.page](); } catch (e) { html = `<p class="empty">Couldn't draw this page: ${esc(e.message)}</p>`; console.error(e); }
    view.innerHTML = html;
    wireCharts(view);
    enhanceTables();
    document.title = "US Screener · " + ($(".tabs a[aria-current=page]") || {}).textContent;
  }
  window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
  document.addEventListener("click", (e) => {
    if (e.target.closest("#refresh-btn")) { manualRefresh(); return; }
    const tsb = e.target.closest(".th-sort"); if (tsb) { onSortClick(tsb); return; }
    const xe = e.target.closest("[data-explain]");
    if (xe) { e.preventDefault(); openExplain(xe.dataset.explain, xe.dataset.ctx, xe); return; }
    if (e.target.closest("#xp-close") || e.target === xpBackdrop) { closeExplain(); return; }
    const xt = e.target.closest("[data-xptab]"); if (xt) { xpTab(xt.dataset.xptab); return; }
    if (e.target.closest("#xp")) return;
    const mb = e.target.closest("[data-mode]"); if (mb) { S.mode = mb.dataset.mode; render(); return; }
    const sb = e.target.closest("[data-sort]"); if (sb) { const k = sb.dataset.sort; S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : (k === "t" ? 1 : -1) }; render(); return; }
    if (e.target.closest("[data-more]")) { S.stockLimit += 100; render(); return; }
    const row = e.target.closest("[data-open]");
    if (row && !e.target.closest("button,a")) { const t = row.dataset.open; S.open.has(t) ? S.open.delete(t) : S.open.add(t); render(); }
  });
  document.addEventListener("keydown", (e) => {
    const row = e.target.closest && e.target.closest("[data-open]");
    if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); row.click(); }
  });
  document.addEventListener("change", (e) => {
    const f = e.target.dataset.filter; if (!f) return;
    S.filters[f] = e.target.type === "checkbox" ? e.target.checked : e.target.value; S.stockLimit = 60; render();
  });
  let qt;
  document.addEventListener("input", (e) => {
    if (e.target.dataset.filter !== "q") return;
    clearTimeout(qt); qt = setTimeout(() => { S.filters.q = e.target.value; S.stockLimit = 60; const pos = e.target.selectionStart; render();
      const el = $('input[data-filter="q"]'); if (el) { el.focus(); el.setSelectionRange(pos, pos); } }, 200);
  });
  refresh(true);
})();
