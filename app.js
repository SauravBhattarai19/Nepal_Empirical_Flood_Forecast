/* Nepal flood forecast page.
   Reads data/latest.json, data/summary.json, data/rain_grid.json, data/series/b<basin>.json
   and static/{reaches,basins,nepal}.geojson + static/targets.csv.
   Levels always carry a shape and a word, the chart has a table view. */
(function () {
  "use strict";
  var HOUR = 3600e3;
  var S = { latest: null, sum: null, targets: [], reaches: null, model: null, sel: null,
            method: "scs", list: "hot", layer: "flow", rainWin: "step", step: 0,
            series: {}, rainGrid: null, playing: null, query: "", tableOpen: false,
            hide: {}, open: {}, redraw: [] };   // small charts: hidden methods, open foldouts, redraw on resize
  var map, L_ = {};

  /* ── helpers ──────────────────────────────────────────────────────────── */
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function sv(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function getJSON(url) {
    return fetch(url + (url.indexOf("?") < 0 ? "?" : "&") + "t=" + Math.floor(Date.now() / 60000))
      .then(function (r) { if (!r.ok) throw new Error(url + ": " + r.status); return r.json(); });
  }
  function getText(url) {
    return fetch(url).then(function (r) { if (!r.ok) throw new Error(url + ": " + r.status); return r.text(); });
  }
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function tz() { return (S.latest && S.latest.display_timezone) || "Asia/Kathmandu"; }
  function fmt(ms, o) {
    var opt = { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz() };
    if (o && o.day) { opt.weekday = "short"; opt.day = "numeric"; opt.month = "short"; }
    if (o && o.dayOnly) opt = { weekday: "short", day: "numeric", month: "short", timeZone: tz() };
    try { return new Intl.DateTimeFormat("en-GB", opt).format(new Date(ms)); }
    catch (e) { return new Date(ms).toISOString().slice(0, 16).replace("T", " "); }
  }
  function q(x) {
    if (x === null || x === undefined || !isFinite(x)) return "–";
    if (x >= 100) return Math.round(x).toLocaleString("en-GB");
    if (x >= 10) return x.toFixed(1);
    return x.toFixed(x >= 1 ? 1 : 2);
  }
  function mm(x) { return x == null ? "–" : (x >= 10 ? Math.round(x) : x.toFixed(1)) + " mm"; }
  function parseCSV(text) {
    var rows = [], row = [], f = "", inq = false;
    for (var i = 0; i < text.length; i++) {
      var c = text[i];
      if (inq) {
        if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else inq = false; }
        else f += c;
      } else if (c === '"') inq = true;
      else if (c === ",") { row.push(f); f = ""; }
      else if (c === "\n") { row.push(f); rows.push(row); row = []; f = ""; }
      else if (c !== "\r") f += c;
    }
    if (f || row.length) { row.push(f); rows.push(row); }
    var head = rows.shift();
    return rows.filter(function (r) { return r.length === head.length; }).map(function (r) {
      var o = {};
      head.forEach(function (h, k) { var v = r[k]; o[h] = v !== "" && isFinite(v) ? +v : v; });
      return o;
    });
  }
  function T0() { return Date.parse(S.latest.series_start_utc); }
  function tAt(i) { return T0() + (i + 0) * HOUR; }        // end of hour i
  function models() { return S.latest.models.map(function (m) { return m.id; }); }
  function mname(id) {
    var m = S.latest.models.filter(function (x) { return x.id === id; })[0];
    return m ? m.name : id;
  }
  function rps() { return S.latest.return_periods; }

  /* levels ------------------------------------------------------------- */
  var PALETTE = [2, 5, 10, 20, 50, 100];
  function lvlVar(T) {            // any return period gets the colour of the nearest one below
    if (!T) return "--l0";
    var c = 2;
    PALETTE.forEach(function (p) { if (T >= p) c = p; });
    return "--l" + c;
  }
  function lvlClass(T) { return "l" + lvlVar(T).slice(3); }
  function lvlWord(T) { return T ? "≥ " + T + "-year flood" : "Below 2-year flood"; }
  function shape(T, size) {
    var s = size || 12, g = sv("svg", { width: s, height: s, viewBox: "0 0 12 12", "aria-hidden": "true" });
    var fill = "var(" + lvlVar(T) + ")", m;
    if (!T) m = sv("circle", { cx: 6, cy: 6, r: 4.2 });
    else if (T <= 5) m = sv("path", { d: "M6 0.8 11.5 11 0.5 11Z" });
    else if (T <= 20) m = sv("path", { d: "M6 0.4 11.6 6 6 11.6 0.4 6Z" });
    else m = sv("path", { d: "M3.5 .6h5l2.9 2.9v5l-2.9 2.9h-5L.6 8.5v-5Z" });
    m.setAttribute("fill", fill);
    m.setAttribute("stroke", "rgba(0,0,0,.35)");
    m.setAttribute("stroke-width", ".6");
    g.appendChild(m);
    return g;
  }
  function badge(T, text) {
    var b = el("span", { class: "badge " + lvlClass(T) });
    b.appendChild(shape(T));
    b.appendChild(document.createTextNode(text || lvlWord(T)));
    return b;
  }

  /* per-target values for the chosen model --------------------------- */
  /* The view of a point for the chosen model: one model, the worst of all
     ("any"), or the second worst ("agree": at least two models say so). */
  function one(i, m) {
    var pr = S.sum.prog ? S.sum.prog[m][i] : 0;
    return { cls: S.sum.cls[m][i], v: S.sum.peak[m][i], t: S.sum.tpeak[m][i], m: m,
             prog: pr == null ? 0 : pr, rise: S.sum.rise ? S.sum.rise[m][i] : null };
  }
  function view(i) {
    if (S.model !== "any" && S.model !== "agree") return one(i, S.model);
    var all = models().map(function (m) { return one(i, m); });
    all.sort(function (a, b) { return (b.cls * 1000 + Math.min(b.prog, 999)) - (a.cls * 1000 + Math.min(a.prog, 999)); });
    return S.model === "agree" && all.length > 1 ? all[1] : all[0];
  }
  function multi() { return S.model === "any" || S.model === "agree"; }
  function clsOf(i) { return view(i).cls; }
  function peakOf(i) { return view(i); }
  function agreeOf(i) {                       // models that reach this point's level
    var c = Math.max(clsOf(i), 1), n = 0;
    models().forEach(function (m) { if (S.sum.cls[m][i] >= c) n++; });
    return n;
  }
  function leadH(i) { return view(i).t - S.latest.now_index; }
  /* rank: flood level, then models agreeing, then the rise towards the first
     flood level, then the soonest peak */
  function rankCmp(a, b) {
    var va = view(a), vb = view(b);
    return (vb.cls - va.cls) || (agreeOf(b) - agreeOf(a)) || (vb.prog - va.prog) || (va.t - vb.t);
  }
  function inH(h) { return h <= 0 ? "now" : h < 48 ? "in " + h + " h" : "in " + Math.round(h / 24) + " days"; }
  function thr(i) {
    var o = {};
    rps().forEach(function (T) { o[T] = S.sum["q" + T][i]; });
    return o;
  }
  function tname(t) {
    var p = S.targets[t];
    if (!p) return "Point " + t;
    if (p.name) return p.name;
    var into = p.reach >= 0 ? flowsInto(p.reach) : "";
    return (into ? "Tributary of " + into : "Unnamed river") + " (" + q(p.area_km2) + " km²)";
  }
  /* the main stem a reach belongs to: walk down while it is its downstream
     reach's largest tributary */
  function stem(r) {
    if (S.stemOf[r] !== undefined) return S.stemOf[r];
    var path = [r], cur = r;
    while (true) {
      var f = S.reachById[cur], d = f ? f.properties.ds : -1;
      if (d < 0 || S.mainChild[d] !== cur) break;
      cur = d; path.push(cur);
      if (S.stemOf[cur] !== undefined) { cur = S.stemOf[cur]; break; }
    }
    path.forEach(function (x) { S.stemOf[x] = cur; });
    return cur;
  }
  function flowsInto(r) {
    var guard = 0, cur = stem(r);
    while (guard++ < 200) {
      var f = S.reachById[cur], d = f ? f.properties.ds : -1;
      if (d < 0) return "";
      var nd = S.reachById[d].properties.n;
      if (nd) return nd;
      cur = stem(d);
    }
    return "";
  }
  function where(t) {
    var p = S.targets[t];
    var b = p.basin_name ? p.basin_name + " basin" : "";
    return [b, p.lat.toFixed(3) + "° N, " + p.lon.toFixed(3) + "° E"].filter(Boolean).join(" · ");
  }

  /* ── header, model pick, alerts ───────────────────────────────────────── */
  function header() {
    var d = S.latest, u = $("updated");
    u.textContent = "";
    var issued = Date.parse(d.issued_utc), next = Date.parse(d.next_update_utc);
    var a = el("span"); a.appendChild(document.createTextNode("Updated "));
    a.appendChild(el("strong", {}, fmt(issued, { day: true }) + " NPT"));
    u.appendChild(a);
    u.appendChild(el("span", {}, "next ≈ " + fmt(next) + " · rain: " +
      (d.source === "open-meteo" ? "Open-Meteo" : d.source)));
    if (Date.now() - issued > 6 * HOUR) {
      u.appendChild(el("span", { class: "pill late" }, "Late: last update " +
        Math.round((Date.now() - issued) / HOUR) + " h ago"));
    }
    var mp = $("model-pick");
    mp.textContent = "";
    var opts = models().map(function (m) { return [m, mname(m)]; });
    if (opts.length > 2) opts.push(["agree", "2+ agree"]);
    if (opts.length > 1) opts.push(["any", "Any model"]);
    opts.forEach(function (o) {
      var b = el("button", { type: "button", "aria-pressed": String(o[0] === S.model) }, o[1]);
      b.addEventListener("click", function () { S.model = o[0]; hashOut(); refresh(); });
      mp.appendChild(b);
    });
  }

  function alerts() {
    var box = $("alerts"); box.textContent = "";
    var n = {};
    rps().forEach(function (T) { n[T] = 0; });
    S.targets.forEach(function (p, i) {
      if (p.reach < 0) return;
      var c = clsOf(i);
      rps().forEach(function (T) { if (c >= T) n[T]++; });
    });
    var chips = [2, 5, 20, 100].filter(function (T) { return rps().indexOf(T) >= 0; });
    if (chips.length < 2) chips = rps().slice(0, 4);
    chips.forEach(function (T) {
      var c = el("span", { class: "chip" + (n[T] ? " on badge " + lvlClass(T) : "") });
      c.appendChild(shape(n[T] ? T : 0, 14));
      c.appendChild(el("span", { class: "n" }, String(n[T])));
      c.appendChild(el("span", { class: "long" }, " river reaches"));
      c.appendChild(document.createTextNode(" ≥ " + T + "-yr flood"));
      box.appendChild(c);
    });
    var msg = el("span", { class: "notice-line" }, "Forecast for the next " +
      Math.round((S.latest.n_hours - S.latest.now_index) / 24) + " days · " +
      (S.model === "any" ? "worst of " + models().length + " weather models" :
       S.model === "agree" ? "what at least 2 of " + models().length + " weather models agree on" : mname(S.model)) +
      ". Experimental, uncalibrated: compare with DHM's official forecasts.");
    if (S.latest.errors && S.latest.errors.length && S.latest.source !== "open-meteo") {
      msg.className = "notice-line warn";
      msg.textContent = "Open-Meteo could not be reached at the last update; the backup " +
        S.latest.source + " forecast is shown (one model only).";
    }
    box.appendChild(msg);
  }

  /* ── list (hotspots / basins / stations / search) ──────────────────── */
  function listItems() {
    var qy = S.query.trim().toLowerCase();
    var out = [];
    if (qy) {
      S.targets.forEach(function (p, i) {
        var hay = (p.name + " " + p.basin_name + " " + (p.station ? "DHM " + p.station : "")).toLowerCase();
        if (p.name && hay.indexOf(qy) >= 0) out.push(i);
      });
      out.sort(function (a, b) { return S.targets[b].area_km2 - S.targets[a].area_km2; });
      return out.slice(0, 80).map(function (i) { return { t: i }; });
    }
    if (S.list === "basins") {
      return S.latest.basins.map(function (b) { return { t: b.outlet, basin: b }; });
    }
    if (S.list === "stations") {
      S.targets.forEach(function (p, i) { if (p.station || p.kind === "point") out.push(i); });
      out.sort(rankCmp);
      return out.map(function (i) { return { t: i }; });
    }
    // hotspots: every river reach, station and point, most serious first, one per river stem
    S.targets.forEach(function (p, i) { if (p.reach >= 0 || p.station || p.kind === "point") out.push(i); });
    out.sort(rankCmp);
    var seen = {}, res = [];
    for (var k = 0; k < out.length && res.length < 60; k++) {
      var i = out[k], p = S.targets[i], v = view(i);
      if (!v.cls && v.prog < 0.05 && res.length >= 10) break;
      var key = p.kind === "point" ? "t" + i : p.station ? "s" + p.station : "r" + stem(p.reach);
      if (seen[key]) continue;
      seen[key] = 1;
      res.push({ t: i });
    }
    return res;
  }

  function renderList() {
    var ul = $("list"); ul.textContent = "";
    var items = listItems();
    var quiet = S.list === "hot" && items.length && !clsOf(items[0].t) && view(items[0].t).prog < 0.05;
    var cap = S.query ? "Matches" : { hot: quiet ? "No river is forecast to rise noticeably" :
      "Most serious first: flood level, models agreeing, rise, soonest",
      basins: "River systems leaving Nepal", stations: "DHM stations and your points, most serious first" }[S.list];
    ul.appendChild(el("li", { class: "cap" }, cap));
    if (!items.length) { ul.appendChild(el("li", { class: "empty" }, "Nothing matches.")); return; }
    items.forEach(function (it) {
      var i = it.t, p = S.targets[i], li = el("li");
      var b = el("button", { type: "button", "aria-current": String(S.sel === i) });
      var c = clsOf(i), pk = peakOf(i);
      if (it.basin) {
        b.appendChild(el("span", { class: "nm" }, it.basin.name));
        b.appendChild(badge(it.basin.cls_max_model !== undefined ? it.basin.cls_max_model : basinCls(it.basin.basin)));
        var r72 = multi() ? Math.max.apply(null, models().map(function (m) { return it.basin.rain72[m]; }))
                                    : it.basin.rain72[S.model];
        b.appendChild(el("span", { class: "sub" }, q(it.basin.area_km2) + " km² · rain next 3 days " + mm(r72) +
          " (catchment mean)"));
      } else {
        b.appendChild(el("span", { class: "nm" }, tname(i)));
        b.appendChild(badge(c, c ? "≥ " + c + "-yr" : "< " + rps()[0] + "-yr"));
        var flat = pk.rise !== null && pk.rise < 0.05 * pk.v, sub;
        if (flat) sub = "no rise forecast, " + q(pk.v) + " m³/s";
        else if (c) sub = agreeOf(i) + "/" + models().length + " models · peak " + q(pk.v) + " m³/s " +
                          fmt(tAt(pk.t), { day: true }) + " (" + inH(leadH(i)) + ")";
        else sub = "rises " + Math.round(Math.min(pk.prog, 0.99) * 100) + "% of the way to its " + rps()[0] +
                   "-yr flood · peak " + q(pk.v) + " m³/s " + inH(leadH(i));
        b.appendChild(el("span", { class: "sub" }, (p.kind === "point" ? "Your point · " : "") +
          (p.basin_name ? p.basin_name + " · " : "") + q(p.area_km2) + " km² · " + sub));
      }
      b.addEventListener("click", function () { select(i, true); });
      li.appendChild(b);
      ul.appendChild(li);
    });
  }
  function basinCls(b) {
    var mx = 0;
    S.targets.forEach(function (p, i) { if (p.basin === b && p.reach >= 0) mx = Math.max(mx, clsOf(i)); });
    return mx;
  }

  /* ── map ──────────────────────────────────────────────────────────────── */
  var BASES = {
    terrain: ["https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", "© OpenStreetMap contributors, SRTM | © OpenTopoMap (CC-BY-SA)", 15],
    streets: ["https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", "© OpenStreetMap contributors", 18],
    satellite: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                "Imagery © Esri, Maxar, Earthstar Geographics", 17]
  };
  function initMap() {
    map = L.map("map", { preferCanvas: true, zoomSnap: 0.25, minZoom: 5, maxZoom: 15,   // 5: all of Nepal fits a phone
                         maxBounds: [[24.5, 77.5], [32.5, 90.5]] });
    map.fitBounds([[26.3, 80.0], [30.45, 88.2]]);
    L_.renderer = L.canvas({ padding: 0.3, tolerance: 6 });
    setBase(localGet("base") || "none");          // rivers on a plain background unless the reader picks a base map
    $("base-pick").value = localGet("base") || "none";
    $("base-pick").addEventListener("change", function () { setBase(this.value); localSet("base", this.value); });
  }
  function setBase(k) {
    if (L_.base) { map.removeLayer(L_.base); L_.base = null; }
    if (BASES[k]) {
      L_.base = L.tileLayer(BASES[k][0], { attribution: BASES[k][1], maxZoom: BASES[k][2],
                                           opacity: k === "satellite" ? 0.9 : 0.75 }).addTo(map);
    }
  }
  function riverWeight(a) { return Math.max(1, Math.min(7, 0.6 + 1.25 * Math.log10(a / 10))); }
  function riverStyle(f) {
    var t = f.properties.t, c = t >= 0 ? clsOf(t) : 0;
    if (S.layer === "rain" && !c) {
      return { color: css("--fg-2"), weight: Math.max(0.8, riverWeight(f.properties.a) * 0.6),
               opacity: 0.55, lineCap: "round", renderer: L_.renderer };
    }
    return { color: css(lvlVar(c)), weight: riverWeight(f.properties.a) + (c ? 0.8 : 0),
             opacity: c ? 0.95 : 0.75, lineCap: "round", renderer: L_.renderer };
  }
  function drawLayers(nepal, basins, reaches) {
    L_.nepal = L.geoJSON(nepal, { interactive: false, style: { color: css("--fg-2"), weight: 1.6, fill: false,
      dashArray: "6 4", opacity: 0.7 } }).addTo(map);
    L_.basins = L.geoJSON(basins, { interactive: false, style: { color: css("--accent"), weight: 1,
      opacity: 0.55, fill: false } }).addTo(map);
    L_.rivers = L.geoJSON(reaches, {
      style: riverStyle,
      onEachFeature: function (f, layer) {
        layer.on("click", function () { if (f.properties.t >= 0) select(f.properties.t, false); });
        layer.bindTooltip(function () { return tipHTML(f.properties.t); }, { sticky: true, className: "tip" });
      }
    }).addTo(map);
    // stations
    L_.stations = L.layerGroup().addTo(map);
    S.targets.forEach(function (p, i) {
      if (p.kind === "point") {
        var pm = L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "pt-icon", iconSize: [14, 14] }) });
        pm.on("click", function () { select(i, false); });
        pm.bindTooltip(function () { return tipHTML(i, "Your point"); }, { className: "tip" });
        L_.stations.addLayer(pm);
        return;
      }
      if (!p.station) return;
      var mk = L.circleMarker([p.lat, p.lon], { radius: 4.5, color: css("--fg"), weight: 1.5, fillColor: css("--panel"),
                                                 fillOpacity: 1, renderer: L_.renderer });
      mk.on("click", function () { select(i, false); });
      mk.bindTooltip(function () { return tipHTML(i, "DHM station " + p.station); }, { className: "tip" });
      L_.stations.addLayer(mk);
    });
    L_.hl = L.layerGroup().addTo(map);
    // children for upstream highlighting
    S.children = {};
    S.reachById = {};
    reaches.features.forEach(function (f) {
      S.reachById[f.properties.id] = f;
      var d = f.properties.ds;
      if (d >= 0) (S.children[d] = S.children[d] || []).push(f.properties.id);
    });
    S.mainChild = {}; S.stemOf = {};
    Object.keys(S.children).forEach(function (d) {
      var best = null;
      S.children[d].forEach(function (c) { if (best === null || S.reachById[c].properties.a > S.reachById[best].properties.a) best = c; });
      S.mainChild[d] = best;
    });
    // a named reach names its whole main stem when the stem has no other name
    var stemName = {};
    reaches.features.forEach(function (f) { if (f.properties.n) stemName[stem(f.properties.id)] = stemName[stem(f.properties.id)] || f.properties.n; });
    reaches.features.forEach(function (f) { if (!f.properties.n && stemName[stem(f.properties.id)]) f.properties.n = stemName[stem(f.properties.id)]; });
    S.targets.forEach(function (p) { if (!p.name && p.reach >= 0 && S.reachById[p.reach] && S.reachById[p.reach].properties.n) p.name = S.reachById[p.reach].properties.n; });
  }
  function tipHTML(t, extra) {
    if (t < 0 || t === undefined) return "";
    var p = S.targets[t], pk = peakOf(t), c = clsOf(t);
    return "<strong>" + esc(tname(t)) + "</strong>" + (extra ? esc(extra) + "<br>" : "") +
      q(p.area_km2) + " km² · peak " + q(pk.v) + " m³/s<br>" + esc(lvlWord(c)) +
      " · 2-yr flood " + q(thr(t)[2]) + " m³/s";
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function restyleRivers() { if (L_.rivers) L_.rivers.setStyle(riverStyle); }

  function highlight(t) {
    L_.hl.clearLayers();
    var p = S.targets[t];
    if (!p) return;
    var ids = [];
    if (p.reach >= 0 && S.reachById[p.reach]) {
      var stack = [p.reach];
      while (stack.length) {
        var r = stack.pop(); ids.push(r);
        (S.children[r] || []).forEach(function (c) { stack.push(c); });
      }
    }
    ids.forEach(function (r) {
      var f = S.reachById[r];
      L.geoJSON(f, { interactive: false, style: { color: css("--fg"), weight: riverWeight(f.properties.a) + 4,
        opacity: 0.18, renderer: L_.renderer } }).addTo(L_.hl);
    });
    L.circleMarker([p.lat, p.lon], { radius: 8, color: css("--fg"), weight: 2.5, fill: false,
                                     interactive: false }).addTo(L_.hl);
  }

  /* rain layer ------------------------------------------------------- */
  var RAIN = [[1, "#d6eaf8"], [5, "#a9d0f0"], [10, "#6fb1e4"], [25, "#3a86cf"], [50, "#2357b0"],
              [100, "#6a3fb5"], [150, "#a03ab8"], [250, "#d03a8a"], [400, "#e8484e"]];
  function rainColor(v) {
    var c = null;
    for (var k = 0; k < RAIN.length; k++) if (v >= RAIN[k][0]) c = RAIN[k][1];
    return c;
  }
  function rainValues() {
    var g = S.rainGrid, m = multi() ? null : S.model;
    var ms = m ? [m] : models();
    var n = g.lat.length, out = new Array(n).fill(0);
    var now = g.now_index, steps = g.models[ms[0]].length;
    var a, b;
    if (S.rainWin === "step") { a = S.step; b = S.step + 1; }
    else if (S.rainWin === "past24") { a = Math.max(now - 8, 0); b = now; }
    else { a = now; b = Math.min(now + ({ next24: 8, next72: 24, next168: 56 })[S.rainWin], steps); }
    ms.forEach(function (mm_) {
      var arr = g.models[mm_];
      for (var i = 0; i < n; i++) {
        var s = 0;
        for (var k = a; k < b; k++) s += arr[k][i] || 0;
        out[i] = Math.max(out[i], s);
      }
    });
    return out;
  }
  function drawRain() {
    if (!L_.rain) L_.rain = L.layerGroup();
    L_.rain.clearLayers();
    if (S.layer !== "rain" || !S.rainGrid) { if (map.hasLayer(L_.rain)) map.removeLayer(L_.rain); return; }
    var g = S.rainGrid, v = rainValues(), h = 0.125;
    for (var i = 0; i < g.lat.length; i++) {
      var col = rainColor(v[i]);
      if (!col) continue;
      var r = L.rectangle([[g.lat[i] - h, g.lon[i] - h], [g.lat[i] + h, g.lon[i] + h]],
        { stroke: false, fillColor: col, fillOpacity: 0.62, renderer: L_.renderer, interactive: true });
      r.bindTooltip(mm(v[i]) + " at " + g.lat[i].toFixed(2) + "° N " + g.lon[i].toFixed(2) + "° E", { className: "tip" });
      L_.rain.addLayer(r);
    }
    if (!map.hasLayer(L_.rain)) L_.rain.addTo(map);
    L_.rivers.bringToFront();
    var lbl = $("step-label");
    if (S.rainWin === "step") {
      var t0 = Date.parse(g.start_utc) + S.step * g.step_h * HOUR;
      lbl.textContent = fmt(t0, { day: true }) + "–" + fmt(t0 + g.step_h * HOUR);
    }
  }

  function legend() {
    var lg = $("legend"); lg.textContent = "";
    if (S.layer === "rain") {
      lg.appendChild(el("div", { class: "title" }, "Rain (mm) " + $("rain-win").selectedOptions[0].text +
        (multi() ? " · wettest model" : " · " + mname(S.model))));
      var r = el("div", { class: "ramp" });
      RAIN.forEach(function (x) {
        var row = el("span", { class: "row" });
        var sw = el("span", { class: "sq" }); sw.style.background = x[1];
        row.appendChild(sw); row.appendChild(document.createTextNode("≥" + x[0]));
        r.appendChild(row);
      });
      lg.appendChild(r);
    }
    lg.appendChild(el("div", { class: "title" }, "Forecast river flow"));
    var rr = el("div", { class: "ramp" });
    [0].concat(rps()).forEach(function (T) {
      var row = el("span", { class: "row" });
      var sw = el("span", { class: "sw" }); sw.style.background = css(lvlVar(T));
      row.appendChild(sw);
      row.appendChild(document.createTextNode(T ? "≥" + T + "-yr" : "< 2-yr"));
      rr.appendChild(row);
    });
    lg.appendChild(rr);
  }

  /* ── selected point ───────────────────────────────────────────────── */
  function select(t, fly) {
    S.sel = t;
    hashOut();
    var p = S.targets[t];
    highlight(t);
    if (fly) map.flyToBounds(L.latLng(p.lat, p.lon).toBounds(Math.max(8000, Math.sqrt(p.area_km2) * 1500)),
                             { maxZoom: 11, duration: 0.6 });
    renderList();
    focusPanel();
    loadSeries(S.sum.grp[t]).then(function () { if (S.sel === t) { chart(); focusPanel(); } });
  }
  function loadSeries(g) {
    if (S.series[g]) return Promise.resolve(S.series[g]);
    return getJSON("data/series/" + g + ".json").then(function (d) { S.series[g] = d; return d; });
  }
  function seriesOf(t) { var g = S.sum.grp[t]; return S.series[g] && S.series[g][t]; }

  function focusPanel() {
    var box = $("focus"); box.textContent = "";
    S.redraw = [];
    var t = S.sel;
    if (t === null || t === undefined) {
      box.appendChild(el("p", { class: "muted" }, "Click a river on the map, or pick one from the list."));
      return;
    }
    var p = S.targets[t], pk = peakOf(t), c = clsOf(t), th = thr(t);
    var head = el("div", { class: "focus-title" });
    head.appendChild(el("h3", {}, tname(t)));
    head.appendChild(el("span", { class: "where" }, (p.station ? "DHM station " + p.station + " · " : "") + where(t)));
    box.appendChild(head);
    box.appendChild(badge(c));
    var k = el("div", { class: "kpis" });
    function kpi(label, val, unit) {
      var d = el("div", { class: "kpi" });
      d.appendChild(el("div", { class: "k" }, label));
      var v = el("div", { class: "v" }, val);
      if (unit) v.appendChild(el("span", { class: "u" }, " " + unit));
      d.appendChild(v);
      k.appendChild(d);
    }
    kpi("Forecast peak", q(pk.v), "m³/s");
    if (pk.rise !== null && pk.rise < 0.05 * pk.v) kpi("When", "No rise forecast");
    else kpi("When", fmt(tAt(pk.t), { day: true }) + " (" + inH(leadH(t)) + ")");
    kpi("2-yr flood", q(th[2]), "m³/s");
    var r72 = multi() ? Math.max.apply(null, models().map(function (m) { return S.sum.rain72[m][t]; }))
                                : S.sum.rain72[S.model][t];
    kpi("Rain, 3 days", r72 == null ? "–" : String(Math.round(r72)), "mm");
    if (models().length > 1) kpi(c ? "Models at this level" : "Models ≥ " + rps()[0] + "-yr",
                                 (c ? agreeOf(t) : models().filter(function (m) { return S.sum.cls[m][t] > 0; }).length) +
                                 " of " + models().length);
    box.appendChild(k);

    // peaks by model and method
    var tb = el("table", { class: "t" });
    var thd = el("thead"), hr = el("tr");
    ["Peak (m³/s)", "SCS-CN", "Rational", "Mononobe", "Richards"].forEach(function (h) { hr.appendChild(el("th", {}, h)); });
    thd.appendChild(hr); tb.appendChild(thd);
    var tbo = el("tbody");
    models().forEach(function (m) {
      var tr = el("tr", { class: m === pk.m ? "hl" : "" });
      tr.appendChild(el("td", {}, mname(m)));
      [(S.sum.scs_peak || S.sum.peak)[m][t], S.sum.rat_peak[m][t], S.sum.mono_peak[m][t], S.sum.rich_peak[m][t]].forEach(function (v) {
        tr.appendChild(el("td", {}, q(v)));
      });
      tbo.appendChild(tr);
    });
    tb.appendChild(tbo);
    box.appendChild(tb);
    var prim = S.latest.primary_method === "rational" ? "Rational" : "SCS-CN";
    box.appendChild(el("p", { class: "note" }, prim + " colours the map; the other methods are " +
      "rainfall-based cross-checks. All include the month's normal flow."));

    // design floods and the mean monthly flows, each as a chart or a table
    var se = seriesOf(t);
    var det = fold("design", "Design floods by method (m³/s)", true);
    box.appendChild(det);
    chartOrTable(det, "design", function (w) { designChart(w, t, se, th, pk); },
                 function (w) { designTable(w, se, th); });
    var nm = fold("monthly", "Mean monthly flows by method (m³/s)", false);
    box.appendChild(nm);
    chartOrTable(nm, "monthly", function (w) { monthlyChart(w, t, se); },
                 function (w) { monthlyTable(w, t, se); });

    // descriptors
    var dd = fold("desc", "Catchment descriptors", false);
    var dl = el("dl", { class: "desc" });
    [["Area", q(p.area_km2) + " km²"], ["Area below 3,000 m", q(p.a3000_km2) + " km²"],
     ["Area below 5,000 m", q(p.a5000_km2) + " km²"], ["Mean elevation", Math.round(p.elev_mean_m) + " m"],
     ["Outlet elevation", Math.round(p.elev_outlet_m) + " m"], ["Longest flow path", q(p.flow_length_km) + " km"],
     ["Drop along it", Math.round(p.drop_m) + " m"], ["Time of concentration (Kirpich)", q(p.tc_h) + " h"],
     ["Runoff coefficient C", p.c_rational], ["Curve number (AMC II)", p.cn2],
     ["Monsoon wetness index", p.mwi_mm === "" ? "–" : Math.round(p.mwi_mm) + " mm"],
     ["Annual rain", p.awi_mm === "" ? "–" : Math.round(p.awi_mm) + " mm"],
     ["Glacier and snow", Math.round(p.glacier_frac * 100) + "%"], ["MHSP region", p.mhsp_region]
    ].forEach(function (r) { dl.appendChild(el("dt", {}, r[0])); dl.appendChild(el("dd", {}, String(r[1]))); });
    dd.appendChild(dl);
    box.appendChild(dd);
    var warn = [];
    if (p.area_km2 > 50) warn.push("The Rational method is meant for catchments up to 50 km² (WECS 2019).");
    if (p.glacier_frac > 0.05 || p.a5000_km2 < 0.9 * p.area_km2)
      warn.push("Part of this catchment is above 5,000 m: snow and glacier melt are not modelled.");
    if (p.tc_h > 24) warn.push("Time of concentration over 24 h: the daily peak methods do not apply.");
    if (p.area_km2 > 5000) warn.push("A large river: its flow comes from far upstream over days; empirical methods are rough here.");
    if (warn.length) box.appendChild(el("p", { class: "warnbox" }, warn.join(" ")));
    var link = el("a", { href: "#t=" + t }, "Link to this point");
    box.appendChild(link);
  }

  /* ── the point panel's small charts: design floods, mean monthly flows ── */
  /* A foldout stays open or closed as the reader left it; inside, a Chart | Table
     switch (remembered in this browser). Colours follow the method, never its
     rank: NEA 1997 is the monthly half of the MHSP 1997 study, so both are slot 3. */
  var SLOTS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];
  var DESIGN_COL = { "WECS/DHM 1990": "--s1", "DHM 2004": "--s2", "MHSP 1997": "--s3", "WECS 2019": "--s4",
                     "Modified Dickens": "--s5", "Given in config.yaml": "--s6" };
  var MONTHLY_COL = { "WECS/DHM 1990": "--s1", "DHM 2004": "--s2", "NEA 1997": "--s3" };
  function fold(key, title, open) {
    var d = el("details");
    if (S.open[key] !== undefined ? S.open[key] : open) d.setAttribute("open", "");
    d.appendChild(el("summary", {}, title));
    d.addEventListener("toggle", function () { S.open[key] = d.open; });
    return d;
  }
  function chartOrTable(det, key, drawChart, drawTable) {
    var view = localGet("view." + key) === "table" ? "table" : "chart";
    var row = el("div", { class: "view-row" });
    var seg = el("div", { class: "seg small", role: "group", "aria-label": "Show as" });
    var body = el("div");
    ["chart", "table"].forEach(function (v) {
      var b = el("button", { type: "button", "aria-pressed": String(v === view) }, v === "chart" ? "Chart" : "Table");
      b.addEventListener("click", function () {
        view = v; localSet("view." + key, v);
        seg.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        draw();
      });
      seg.appendChild(b);
    });
    row.appendChild(seg); det.appendChild(row); det.appendChild(body);
    function draw() {                 // a chart needs its width, so it is drawn once the foldout is open
      body.textContent = "";
      if (det.open) (view === "chart" ? drawChart : drawTable)(body);
    }
    det.addEventListener("toggle", function () { if (det.open && !body.firstChild) draw(); });
    S.redraw.push(function () { if (view === "chart") draw(); });
    draw();
  }
  function monthName(m, style) {
    return new Date(Date.UTC(2000, m - 1, 15)).toLocaleString("en-GB", { month: style || "long", timeZone: "UTC" });
  }
  function baseMethod() {
    var b = S.latest.baseflow || {};
    return (b.method || "WECS/DHM 1990") + (b.factor && b.factor !== 1 ? " × " + b.factor : "");
  }

  function designChart(w, t, se, th, pk) {
    var T = rps(), series = [{ name: "Used: " + S.latest.threshold_statistic, col: "--fg", emph: true,
                               vals: T.map(function (x) { return th[x]; }) }];
    if (se && se.design) Object.keys(se.design).forEach(function (m, k) {
      series.push({ name: m, col: DESIGN_COL[m] || SLOTS[k % SLOTS.length], vals: se.design[m] });
    });
    var c = el("div"); w.appendChild(c);
    miniChart(c, {
      id: "design", series: series, unit: "m³/s",
      xs: T.map(function (x) { return Math.log10(x); }), xLabels: T.map(String), xTitle: "Return period (years)",
      refs: [{ key: "peak", v: pk.v, label: "Forecast peak " + q(pk.v),
               name: "Forecast peak" + (models().length > 1 ? " (" + mname(pk.m) + ")" : "") }],
      tipTitle: function (i) { return T[i] + "-year flood"; }, start: 0,
      aria: "Design floods of " + tname(t) + " by method, " + T[0] + "- to " + T[T.length - 1] + "-year, with the forecast peak."
    });
    if (!se) { w.appendChild(el("p", { class: "note" }, "Loading the methods…")); return; }
    var gaps = [];
    Object.keys(se.design).forEach(function (m) {
      if (m === "Given in config.yaml") return;
      var miss = T.filter(function (x, k) { return se.design[m][k] == null; });
      if (miss.length === T.length) gaps.push(m + " does not apply here" + (m === "WECS 2019" ? " (below 12 km²)" : ""));
      else if (miss.length) gaps.push(m + " gives no " + miss.join(", ") + "-year flood");
    });
    w.appendChild(el("p", { class: "note" }, "Bold: the flood levels the map and chart use. Dashed: the forecast peak; " +
      "where it crosses the bold line is roughly its return period." + (gaps.length ? " " + gaps.join("; ") + "." : "")));
  }
  function designTable(w, se, th) {
    var dt = el("table", { class: "t" }), dh = el("tr");
    dh.appendChild(el("th", {}, "Method"));
    rps().forEach(function (T) { dh.appendChild(el("th", {}, T + "-yr")); });
    var dthead = el("thead"); dthead.appendChild(dh); dt.appendChild(dthead);
    var dbody = el("tbody");
    if (se && se.design) {
      Object.keys(se.design).forEach(function (m) {
        var tr = el("tr"); tr.appendChild(el("td", {}, m));
        se.design[m].forEach(function (v) { tr.appendChild(el("td", {}, q(v))); });
        dbody.appendChild(tr);
      });
    }
    var tr2 = el("tr", { class: "hl" }); tr2.appendChild(el("td", {}, "Used: " + S.latest.threshold_statistic));
    rps().forEach(function (T) { tr2.appendChild(el("td", {}, q(th[T]))); });
    dbody.appendChild(tr2);
    dt.appendChild(dbody); w.appendChild(dt);
    if (!se) w.appendChild(el("p", { class: "note" }, "Loading the methods…"));
  }

  function monthlyChart(w, t, se) {
    var mo = se && se.monthly, cur = S.latest.month - 1, ix = [];
    if (!mo) {                                  // hydrograph files written before the twelve months were added
      w.appendChild(el("p", { class: "note" }, se ? "The chart of the twelve months comes with the next forecast " +
        "update. The table shows this month." : "Loading the methods…"));
      return;
    }
    for (var k = 0; k < 12; k++) ix.push(k);
    var c = el("div"); w.appendChild(c);
    miniChart(c, {
      id: "monthly", unit: "m³/s", xPad: 0.5, band: cur, bandLabel: "now",
      series: Object.keys(mo).map(function (m, k) { return { name: m, col: MONTHLY_COL[m] || SLOTS[k % SLOTS.length], vals: mo[m] }; }),
      xs: ix, xLabels: ix.map(function (k) { return monthName(k + 1, "short"); }),
      xShort: ix.map(function (k) { return monthName(k + 1, "narrow"); }),
      tipTitle: function (i) { return monthName(i + 1) + (i === cur ? ", this month" : ""); }, start: cur,
      aria: "Mean flow of each month, January to December, of " + tname(t) + " by three formulas."
    });
    w.appendChild(el("p", { class: "note" }, "The mean flow of each month (the shaded one is now). " + baseMethod() +
      " is the normal flow every forecast is built on: the dotted line of the forecast chart."));
  }
  function monthlyTable(w, t, se) {
    var mo = se && se.monthly, cur = S.latest.month - 1;
    var src = mo || S.sum.normal, ms = Object.keys(src);
    var tb = el("table", { class: "t" }), hr = el("tr");
    hr.appendChild(el("th", {}, "Month"));
    ms.forEach(function (m) { hr.appendChild(el("th", {}, m)); });
    var th = el("thead"); th.appendChild(hr); tb.appendChild(th);
    var body = el("tbody");
    for (var k = 0; k < 12; k++) {
      if (!mo && k !== cur) continue;            // older files: this month only, from the summary
      var tr = el("tr", { class: k === cur ? "hl" : "" });
      tr.appendChild(el("td", {}, monthName(k + 1, "short") + (k === cur ? " (now)" : "")));
      ms.forEach(function (m) { tr.appendChild(el("td", {}, q(mo ? mo[m][k] : S.sum.normal[m][t]))); });
      body.appendChild(tr);
    }
    tb.appendChild(body); w.appendChild(tb);
    w.appendChild(el("p", { class: "note" }, baseMethod() + " is the normal flow every forecast is built on."));
  }

  /* A small line chart: legend buttons that show or hide a series, a crosshair
     that snaps to the nearest x with every series in one tooltip (pointer, touch,
     or the arrow keys once focused), optional dashed reference lines and a shaded
     column. o: {id, series: [{name, col, vals, emph}], xs, xLabels, xShort, xTitle,
     xPad, refs: [{v, label, name}], band, bandLabel, tipTitle(i), start, unit, aria} */
  function lineKey(s) {
    var k = sv("svg", { width: 18, height: 8, viewBox: "0 0 18 8", "aria-hidden": "true", class: "key" });
    var ln = sv("line", { x1: 1.5, x2: 16.5, y1: 4, y2: 4, stroke: "var(" + s.col + ")", "stroke-width": s.emph ? 3 : 2,
                          "stroke-linecap": s.dash ? "butt" : "round" });
    if (s.dash) ln.setAttribute("stroke-dasharray", s.dash);
    k.appendChild(ln);
    return k;
  }
  function niceTicks(max) {
    if (!(max > 0)) max = 1;
    var raw = max / 4, e = Math.pow(10, Math.floor(Math.log10(raw))), step = 10 * e;
    [1, 2, 2.5, 5].some(function (f) { if (f * e >= raw) { step = f * e; return true; } return false; });
    var n = Math.ceil(max / step - 1e-9), ticks = [];
    for (var k = 0; k <= n; k++) ticks.push(+(k * step).toPrecision(12));
    return { top: ticks[n], ticks: ticks };
  }
  function tickTxt(v, top) {          // in full under 10,000, compact on an axis that reaches it
    if (top >= 10000) return v ? +(v / 1000).toFixed(1) + "k" : "0";
    return v.toLocaleString("en-GB", { maximumFractionDigits: 3 });
  }
  function fin(v) { return v !== null && v !== undefined && isFinite(v); }
  function miniChart(box, o) {
    var hide = S.hide[o.id] || (S.hide[o.id] = {});
    box.textContent = "";
    box.className = "mc";
    var lg = el("div", { class: "mc-legend", role: "group", "aria-label": "Show or hide a method" });
    o.series.forEach(function (s, k) {
      var none = !s.vals.some(fin);
      var b = el("button", { type: "button", "data-k": k, "aria-pressed": String(!hide[s.name] && !none),
                             class: s.emph ? "emph" : "" });
      if (none) { b.disabled = true; b.title = "No value for this point"; }
      b.appendChild(lineKey(s));
      b.appendChild(document.createTextNode(s.name + (none ? " (n/a)" : "")));
      b.addEventListener("click", function () {
        hide[s.name] = !hide[s.name];
        miniChart(box, o);
        var nb = box.querySelector('.mc-legend button[data-k="' + k + '"]');
        if (nb) nb.focus();
      });
      lg.appendChild(b);
    });
    (o.refs || []).forEach(function (r) {
      if (!fin(r.v)) return;
      var b = el("button", { type: "button", "data-k": "ref:" + r.key, "aria-pressed": String(!hide["ref:" + r.key]) });
      b.appendChild(lineKey({ col: "--fg-2", dash: "4 2" }));
      b.appendChild(document.createTextNode(r.label + " " + o.unit));
      b.addEventListener("click", function () {
        hide["ref:" + r.key] = !hide["ref:" + r.key];
        miniChart(box, o);
        var nb = box.querySelector('.mc-legend button[data-k="ref:' + r.key + '"]');
        if (nb) nb.focus();
      });
      lg.appendChild(b);
    });
    box.appendChild(lg);

    var vis = o.series.filter(function (s) { return !hide[s.name] && s.vals.some(fin); });
    var refs = (o.refs || []).filter(function (r) { return fin(r.v) && !hide["ref:" + r.key]; });
    var W = Math.max(box.clientWidth || 340, 240), H = Math.round(Math.min(300, Math.max(210, 0.3 * W)));   // taller when wide
    var vmax = 0;
    vis.forEach(function (s) { s.vals.forEach(function (v) { if (fin(v) && v > vmax) vmax = v; }); });
    refs.forEach(function (r) { if (r.v > vmax) vmax = r.v; });
    var yt = niceTicks(vmax * 1.06), lab = yt.ticks.map(function (v) { return tickTxt(v, yt.top); });
    var padL = 14 + 6.4 * Math.max(4, Math.max.apply(null, lab.map(function (s) { return s.length; })));
    var padR = 8, padT = 22, padB = o.xTitle ? 38 : 22;
    var xmin = Math.min.apply(null, o.xs), xmax = Math.max.apply(null, o.xs);
    var xp = o.xPad !== undefined ? o.xPad : 0.08 * (xmax - xmin);
    var X = function (x) { return padL + (x - xmin + xp) / (xmax - xmin + 2 * xp) * (W - padL - padR); };
    var Y = function (v) { return H - padB - v / yt.top * (H - padB - padT); };
    var g = sv("svg", { viewBox: "0 0 " + W + " " + H, role: "img", tabindex: "0",
                        "aria-label": o.aria + " The arrow keys read the values; the Table button lists them." });
    if (o.band !== undefined && o.band !== null) {          // the current month
      var bx0 = X(o.xs[o.band] - 0.5), bx1 = X(o.xs[o.band] + 0.5);
      g.appendChild(sv("rect", { x: bx0, y: padT, width: bx1 - bx0, height: H - padB - padT, fill: "var(--accent-soft)" }));
      var bl = sv("text", { x: (bx0 + bx1) / 2, y: padT - 6, "text-anchor": "middle", class: "tick" });
      bl.textContent = o.bandLabel; g.appendChild(bl);
    }
    yt.ticks.forEach(function (v, k) {
      var y = Y(v);
      g.appendChild(sv("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: k ? "var(--grid)" : "var(--axis)" }));
      var tx = sv("text", { x: padL - 6, y: y + 4, "text-anchor": "end", class: "tick" });
      tx.textContent = lab[k]; g.appendChild(tx);
    });
    var un = sv("text", { x: padL - 6, y: padT - 9, "text-anchor": "end", class: "tick" });
    un.textContent = o.unit; g.appendChild(un);
    var xl = o.xShort && (W - padL - padR) / o.xs.length < 26 ? o.xShort : o.xLabels;
    o.xs.forEach(function (x, k) {
      var tx = sv("text", { x: X(x), y: H - padB + 15, "text-anchor": "middle", class: "tick" });
      tx.textContent = xl[k]; g.appendChild(tx);
    });
    if (o.xTitle) {
      var xt = sv("text", { x: (padL + W - padR) / 2, y: H - 5, "text-anchor": "middle" });
      xt.textContent = o.xTitle; g.appendChild(xt);
    }
    // reference lines (the forecast peak), labelled in the plot only where no curve crosses the label
    // (the legend always names them)
    var segs = [];
    vis.forEach(function (s) {
      var prev = null;
      s.vals.forEach(function (v, k) {
        if (!fin(v)) { prev = null; return; }
        var p = [X(o.xs[k]), Y(v)];
        segs.push(prev ? [prev[0], prev[1], p[0], p[1]] : [p[0] - 5, p[1], p[0] + 5, p[1]]);   // the dot itself
        prev = p;
      });
    });
    function crossings(b) {
      return segs.filter(function (sg) {
        var lo = Math.max(b[0], sg[0]), hi = Math.min(b[1], sg[2]);
        if (lo > hi) return false;
        var f = function (x) { return sg[2] === sg[0] ? sg[1] : sg[1] + (sg[3] - sg[1]) * (x - sg[0]) / (sg[2] - sg[0]); };
        return Math.max(f(lo), f(hi)) >= b[2] - 3 && Math.min(f(lo), f(hi)) <= b[3] + 3;
      }).length;
    }
    refs.forEach(function (r) {
      var y = Y(r.v), txt = r.label + " " + o.unit, lw = txt.length * 6.1, best = null;
      g.appendChild(sv("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: "var(--fg-2)", "stroke-width": 1.3,
                                 "stroke-dasharray": "5 3" }));
      [[1, -6], [0, -6], [1, 14], [0, 14]].forEach(function (c) {       // [right?, baseline offset]
        var x0 = c[0] ? W - padR - 4 - lw : padL + 6, b = [x0, x0 + lw, y + c[1] - 10, y + c[1] + 2];
        if (!best && b[2] >= padT - 6 && b[3] <= H - padB - 1 && !crossings(b)) best = c;
      });
      if (!best) return;
      var c = best;
      var lt = sv("text", { x: c[0] ? W - padR - 4 : padL + 6, y: y + c[1], "text-anchor": c[0] ? "end" : "start", class: "mc-ref" });
      lt.textContent = txt; g.appendChild(lt);
    });
    vis.slice().sort(function (a, b) { return (a.emph ? 1 : 0) - (b.emph ? 1 : 0); }).forEach(function (s) {
      var d = "", pen = false;
      s.vals.forEach(function (v, k) {
        if (!fin(v)) { pen = false; return; }
        d += (pen ? "L" : "M") + X(o.xs[k]).toFixed(1) + " " + Y(v).toFixed(1);
        pen = true;
      });
      g.appendChild(sv("path", { d: d, fill: "none", stroke: "var(" + s.col + ")", "stroke-width": s.emph ? 2.75 : 2,
                                 "stroke-linejoin": "round", "stroke-linecap": "round" }));
      s.vals.forEach(function (v, k) {
        if (fin(v)) g.appendChild(sv("circle", { cx: X(o.xs[k]), cy: Y(v), r: s.emph ? 4.5 : 4, fill: "var(" + s.col + ")",
                                                 stroke: "var(--panel)", "stroke-width": 2 }));
      });
    });
    var cross = sv("line", { y1: padT, y2: H - padB, stroke: "var(--fg-2)", "stroke-width": 1, visibility: "hidden" });
    var hl = sv("g");
    g.appendChild(cross); g.appendChild(hl);
    box.appendChild(g);
    var tip = el("div", { class: "hover-tip mc-tip", hidden: "", "aria-live": "polite" });
    box.appendChild(tip);

    var cur = null;
    function show(i) {
      cur = i;
      var x = X(o.xs[i]);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
      hl.textContent = ""; tip.textContent = "";
      tip.appendChild(el("div", { class: "mc-tt" }, o.tipTitle(i) + " · " + o.unit));
      var rows = el("div", { class: "mc-rows" });
      vis.forEach(function (s) {
        var v = s.vals[i];
        rows.appendChild(lineKey(s));
        rows.appendChild(el("b", {}, fin(v) ? q(v) : "–"));
        rows.appendChild(el("span", { class: s.emph ? "emph" : "" }, s.name));
        if (fin(v)) hl.appendChild(sv("circle", { cx: x, cy: Y(v), r: s.emph ? 6 : 5.5, fill: "var(" + s.col + ")",
                                                  stroke: "var(--panel)", "stroke-width": 2 }));
      });
      refs.forEach(function (r) {
        rows.appendChild(lineKey({ col: "--fg-2", dash: "4 2" }));
        rows.appendChild(el("b", {}, q(r.v)));
        rows.appendChild(el("span", {}, r.name));
      });
      tip.appendChild(rows);
      tip.hidden = false;
      // beside the crosshair, else (narrow panel, pointer mid-chart) under the plot so it hides no data
      var gr = g.getBoundingClientRect(), br = box.getBoundingClientRect(), sc = gr.width / W;
      var px = gr.left - br.left + x * sc, tw = tip.offsetWidth, top = gr.top - br.top + padT * sc, left;
      if (px + 14 + tw <= br.width) left = px + 14;
      else if (px - 14 - tw >= 0) left = px - 14 - tw;
      else {
        left = Math.max(0, Math.min(px - tw / 2, br.width - tw));
        top = gr.top - br.top + (H - padB + 20) * sc;
      }
      tip.style.left = left + "px";
      tip.style.top = top + "px";
    }
    function hideTip() { cur = null; tip.hidden = true; cross.setAttribute("visibility", "hidden"); hl.textContent = ""; }
    box._hideTip = hideTip;
    function nearest(ev) {
      var gr = g.getBoundingClientRect(), sx = (ev.clientX - gr.left) / gr.width * W, best = 0, bd = Infinity;
      o.xs.forEach(function (x, k) { var d = Math.abs(X(x) - sx); if (d < bd) { bd = d; best = k; } });
      return best;
    }
    g.addEventListener("pointermove", function (ev) { var i = nearest(ev); if (i !== cur) show(i); });
    g.addEventListener("pointerdown", function (ev) { show(nearest(ev)); });
    g.addEventListener("pointerleave", function (ev) { if (ev.pointerType === "mouse") hideTip(); });
    g.addEventListener("focus", function () { if (cur === null) show(o.start || 0); });
    g.addEventListener("blur", hideTip);
    g.addEventListener("keydown", function (ev) {
      var n = o.xs.length, i = cur === null ? (o.start || 0) : cur;
      if (ev.key === "ArrowRight") i = Math.min(i + 1, n - 1);
      else if (ev.key === "ArrowLeft") i = Math.max(i - 1, 0);
      else if (ev.key === "Home") i = 0;
      else if (ev.key === "End") i = n - 1;
      else if (ev.key === "Escape") { hideTip(); return; }
      else return;
      ev.preventDefault();
      show(i);
    });
  }

  /* ── chart ────────────────────────────────────────────────────────── */
  var MSTYLE = ["--s1", "--s2", "--s3", "--s4"], DASH = ["", "7 4", "2 3", "10 3 2 3"];
  function rationalSeries(t, m, se) {
    var p = S.targets[t], rain = se.rain[m], base = baseSeries(se, rain.length);
    var n = Math.max(1, Math.round(p.tc_h)), out = [], csum = [0];
    for (var i = 0; i < rain.length; i++) csum.push(csum[i] + (rain[i] || 0));
    for (i = 0; i < rain.length; i++) {
      var lo = Math.max(0, i + 1 - n);
      out.push(base[i] + p.c_rational * ((csum[i + 1] - csum[lo]) / n) * p.area_km2 / 3.6);
    }
    return out;
  }
  function baseSeries(se, len) {
    var out = [];
    for (var i = 0; i < len; i++) {
      var k = Math.floor(i / 24), f = (i % 24) / 24;
      var a = se.base[Math.min(k, se.base.length - 1)], b = se.base[Math.min(k + 1, se.base.length - 1)];
      out.push(a + (b - a) * f);
    }
    return out;
  }
  function dayMs(iso) {   // local noon of a Nepal calendar day
    return Date.parse(iso + "T12:00:00+05:45");
  }
  function chartData(t) {
    var se = seriesOf(t);
    if (!se) return null;
    var ms = models(), lines = [];
    ms.forEach(function (m, k) {
      if (!multi() && m !== S.model && S.method !== "daily") { /* others shown faint */ }
      var vals = S.method === "rational" ? rationalSeries(t, m, se) : se.scs[m];
      lines.push({ m: m, vals: vals, k: k, faint: !multi() && m !== S.model });
    });
    return { se: se, lines: lines };
  }
  function chart() {
    var box = $("chart"); box.textContent = "";
    var t = S.sel;
    if (t === null || t === undefined) { box.appendChild(el("p", { class: "muted" }, "Pick a river to see its forecast.")); return; }
    var cd = chartData(t);
    if (!cd) { box.appendChild(el("p", { class: "muted" }, "Loading…")); return; }
    var se = cd.se, n = se.rain[models()[0]].length;
    var W = Math.max(box.clientWidth || 700, 320), Hh = 340;
    var padL = 52, padR = 78, top = 84, bot = 30, rainH = 46;
    var x0 = T0(), x1 = tAt(n - 1);
    var X = function (ms) { return padL + (ms - x0) / (x1 - x0) * (W - padL - padR); };
    var th = thr(t), base = baseSeries(se, n);
    var ymax = 0;
    cd.lines.forEach(function (l) { l.vals.forEach(function (v) { if (v > ymax) ymax = v; }); });
    if (S.method === "daily") {
      models().forEach(function (m) { se.mono[m].concat(se.rich[m]).forEach(function (v) { if (v > ymax) ymax = v; }); });
    }
    ymax = Math.max(ymax * 1.25, base[0] * 1.5, 0.1);
    // pull in the next flood level when it is close above the forecast (only that one: checking each
    // level against the raised axis used to pull in all six and flatten the hydrograph)
    var nxt = rps().filter(function (T) { return th[T] > ymax; })[0];
    if (nxt && th[nxt] < ymax * 1.8) ymax = th[nxt] * 1.05;
    var nice = niceMax(ymax);
    var off = rps().filter(function (T) { return th[T] > nice; });
    var Y = function (v) { return Hh - bot - v / nice * (Hh - bot - top); };
    var g = sv("svg", { viewBox: "0 0 " + W + " " + Hh, role: "presentation" });
    // past shading
    var xn = X(tAt(S.latest.now_index));
    g.appendChild(sv("rect", { x: padL, y: top - rainH - 8, width: Math.max(xn - padL, 0), height: Hh - bot - top + rainH + 8, fill: "var(--past)" }));
    // grid + y ticks
    for (var k = 0; k <= 4; k++) {
      var v = nice * k / 4, y = Y(v);
      g.appendChild(sv("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: "var(--grid)" }));
      var tx = sv("text", { x: padL - 6, y: y + 4, "text-anchor": "end", class: "tick" });
      tx.textContent = v >= 1000 ? Math.round(v).toLocaleString("en-GB") : compact(v);   // exact: 1,250 not 1.3k
      g.appendChild(tx);
    }
    var yl = sv("text", { x: 12, y: (top + Hh - bot) / 2, transform: "rotate(-90 12 " + (top + Hh - bot) / 2 + ")", "text-anchor": "middle" });
    yl.textContent = "Discharge (m³/s)"; g.appendChild(yl);
    // day ticks
    var d0 = new Date(x0);
    for (var ms = Date.parse(fmtISOday(d0) + "T00:00:00+05:45"); ms <= x1; ms += 24 * HOUR) {
      if (ms < x0) continue;
      var xx = X(ms);
      g.appendChild(sv("line", { x1: xx, x2: xx, y1: top - rainH - 8, y2: Hh - bot, stroke: "var(--grid)" }));
      var dtx = sv("text", { x: xx + 4, y: Hh - bot + 16 }); dtx.textContent = fmt(ms, { dayOnly: true });
      g.appendChild(dtx);
    }
    // rain bars (hanging from the top), the chosen model (or the first)
    var rm = multi() ? models()[0] : S.model, rain = se.rain[rm], rmax = 1;
    rain.forEach(function (r) { if (r > rmax) rmax = r; });
    var rtop = top - rainH - 6, bw = Math.max((W - padL - padR) / n, 1);
    rain.forEach(function (r, i) {
      if (!r) return;
      var hgt = r / rmax * rainH;
      g.appendChild(sv("rect", { x: X(tAt(i)) - bw, y: rtop, width: bw * 0.9, height: hgt, fill: "var(--accent)", opacity: 0.55 }));
    });
    var rl = sv("text", { x: W - padR + 6, y: rtop + 10 }); rl.textContent = "Rain";
    var rl2 = sv("text", { x: W - padR + 6, y: rtop + 23 }); rl2.textContent = "max " + q(rmax) + " mm/h";
    g.appendChild(rl); g.appendChild(rl2);
    // thresholds
    rps().forEach(function (T) {
      var v = th[T];
      if (!(v > 0) || v > nice) return;
      var y = Y(v);
      g.appendChild(sv("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: "var(" + lvlVar(T) + ")", "stroke-width": 1.6, "stroke-dasharray": "5 3" }));
      var lt = sv("text", { x: W - padR + 6, y: y + 4, class: "lbl", fill: "var(" + lvlVar(T) + ")" });
      lt.textContent = T + "-yr " + compact(v);
      lt.style.fill = "var(" + lvlVar(T) + ")";
      g.appendChild(lt);
    });
    if (off.length) {
      var ot = sv("text", { x: W - padR, y: top - rainH - 14, "text-anchor": "end", class: "lbl" });
      ot.textContent = "Above the chart: " + off.slice(0, 3).map(function (T) { return T + "-yr " + compact(th[T]); }).join(" · ") + " m³/s ↑";
      ot.style.fill = "var(" + lvlVar(off[0]) + ")";
      g.appendChild(ot);
    }
    // base flow
    g.appendChild(sv("path", { d: pathOf(base, X, Y), fill: "none", stroke: "var(--muted)", "stroke-width": 1.2, "stroke-dasharray": "1 3" }));
    // lines
    if (S.method !== "daily") {
      cd.lines.slice().sort(function (a, b) { return (b.faint ? 1 : 0) - (a.faint ? 1 : 0); }).forEach(function (l) {
        g.appendChild(sv("path", { d: pathOf(l.vals, X, Y), fill: "none", stroke: "var(" + MSTYLE[l.k % 4] + ")",
          "stroke-width": l.faint ? 1.4 : 2.4, opacity: l.faint ? 0.45 : 1, "stroke-dasharray": DASH[l.k % 4], "stroke-linejoin": "round" }));
      });
    } else {
      models().forEach(function (m, k) {
        S.latest.days.forEach(function (d, j) {
          var xm = X(dayMs(d)) + (k - 1) * 6;
          if (xm < padL || xm > W - padR) return;
          var vm = se.mono[m][j], vr = se.rich[m][j];
          g.appendChild(sv("circle", { cx: xm, cy: Y(vm), r: 4, fill: "var(" + MSTYLE[k % 4] + ")" }));
          g.appendChild(sv("path", { d: "M" + (xm - 4) + " " + (Y(vr) + 3.5) + "l4 -7 4 7z", fill: "none", stroke: "var(" + MSTYLE[k % 4] + ")", "stroke-width": 1.6 }));
        });
      });
    }
    // now line
    g.appendChild(sv("line", { x1: xn, x2: xn, y1: top - rainH - 8, y2: Hh - bot, stroke: "var(--fg-2)", "stroke-width": 1.2 }));
    var nt = sv("text", { x: xn + 4, y: Hh - bot - 6 }); nt.textContent = "now"; g.appendChild(nt);
    // legend
    var lx = padL;
    models().forEach(function (m, k) {
      var gg = sv("g", { transform: "translate(" + lx + "," + 12 + ")" });
      gg.appendChild(sv("line", { x1: 0, x2: 22, y1: 0, y2: 0, stroke: "var(" + MSTYLE[k % 4] + ")", "stroke-width": 2.4, "stroke-dasharray": DASH[k % 4] }));
      var tt = sv("text", { x: 26, y: 4 }); tt.textContent = mname(m); gg.appendChild(tt);
      g.appendChild(gg); lx += 34 + mname(m).length * 6.6;
    });
    var gb = sv("g", { transform: "translate(" + lx + "," + 12 + ")" });
    gb.appendChild(sv("line", { x1: 0, x2: 22, y1: 0, y2: 0, stroke: "var(--muted)", "stroke-width": 1.2, "stroke-dasharray": "1 3" }));
    var tb = sv("text", { x: 26, y: 4 }); tb.textContent = "normal flow of the month"; gb.appendChild(tb); g.appendChild(gb);
    if (S.method === "daily") {
      var gd = sv("text", { x: padL, y: 28 }); gd.textContent = "● Mononobe-Rational   △ B.D. Richards (one peak per day, from the day's rain)";
      g.appendChild(gd);
    }
    box.appendChild(g);
    hover(box, g, X, Y, n, cd, base, W, padL, padR);
    var note = { scs: "SCS curve number (AMC from the 5-day rain) with the SCS unit hydrograph, lag 0.6 t_c; t_c = " + q(S.targets[t].tc_h) + " h.",
                 rational: "Rational method: Q = C·i·A/3.6, i = mean rain over the last t_c (" + q(S.targets[t].tc_h) + " h), C = " + S.targets[t].c_rational + ".",
                 daily: "Daily peaks from each Nepal day's rain total: Mononobe intensity with the Rational method, and B.D. Richards." }[S.method];
    $("chart-note").textContent = note + " Rain bars: " + mname(rm) + ". Times in Nepal time.";
    if (S.tableOpen) table(t, cd, base);
  }
  function fmtISOday(d) {
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz(), year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  }
  function pathOf(vals, X, Y) {
    var d = "", pen = false;
    vals.forEach(function (v, i) {
      if (v == null) { pen = false; return; }
      d += (pen ? "L" : "M") + X(tAt(i)).toFixed(1) + " " + Y(v).toFixed(1);
      pen = true;
    });
    return d;
  }
  function niceMax(v) {
    var e = Math.pow(10, Math.floor(Math.log10(v))), f = v / e;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e;
  }
  function compact(v) {
    if (v >= 10000) return Math.round(v / 1000) + "k";
    if (v >= 1000) return (v / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return v >= 10 ? String(Math.round(v)) : String(+v.toFixed(2));
  }
  function hover(box, g, X, Y, n, cd, base, W, padL, padR) {
    var tip = el("div", { class: "hover-tip", hidden: "" }), line = sv("line", { stroke: "var(--fg-2)", "stroke-dasharray": "2 2", y1: 0, y2: 340, visibility: "hidden" });
    g.appendChild(line); box.appendChild(tip);
    g.addEventListener("mousemove", function (ev) {
      var r = g.getBoundingClientRect(), sx = (ev.clientX - r.left) / r.width * W;
      var i = Math.round((sx - padL) / (W - padL - padR) * (n - 1));
      if (i < 0 || i >= n) { tip.hidden = true; line.setAttribute("visibility", "hidden"); return; }
      var x = X(tAt(i));
      line.setAttribute("x1", x); line.setAttribute("x2", x); line.setAttribute("visibility", "visible");
      tip.textContent = "";
      tip.appendChild(el("strong", {}, fmt(tAt(i), { day: true })));
      cd.lines.forEach(function (l) {
        var row = el("div", { class: "r" });
        row.appendChild(el("span", {}, mname(l.m)));
        row.appendChild(el("span", {}, q(l.vals[i]) + " m³/s · " + q(cd.se.rain[l.m][i]) + " mm"));
        tip.appendChild(row);
      });
      tip.hidden = false;
      var left = (x / W) * r.width + 12;
      tip.style.left = Math.min(left, r.width - tip.offsetWidth - 4) + "px";
      tip.style.top = "60px";
    });
    g.addEventListener("mouseleave", function () { tip.hidden = true; line.setAttribute("visibility", "hidden"); });
  }
  function table(t, cd, base) {
    var w = $("chart-table"); w.textContent = "";
    var tb = el("table", { class: "t" }), hr = el("tr");
    hr.appendChild(el("th", {}, "Time (NPT)"));
    cd.lines.forEach(function (l) { hr.appendChild(el("th", {}, mname(l.m) + " m³/s")); hr.appendChild(el("th", {}, "rain mm")); });
    hr.appendChild(el("th", {}, "normal m³/s"));
    var th = el("thead"); th.appendChild(hr); tb.appendChild(th);
    var body = el("tbody");
    for (var i = 0; i < cd.lines[0].vals.length; i += 3) {
      var tr = el("tr");
      tr.appendChild(el("td", {}, fmt(tAt(i), { day: true })));
      cd.lines.forEach(function (l) {
        tr.appendChild(el("td", {}, q(l.vals[i])));
        var r3 = 0; for (var k = i; k < i + 3 && k < l.vals.length; k++) r3 += cd.se.rain[l.m][k] || 0;
        tr.appendChild(el("td", {}, r3.toFixed(1)));
      });
      tr.appendChild(el("td", {}, q(base[i])));
      body.appendChild(tr);
    }
    tb.appendChild(body); w.appendChild(tb);
  }

  /* ── state, wiring ────────────────────────────────────────────────── */
  function localGet(k) { try { return localStorage.getItem("nff." + k); } catch (e) { return null; } }
  function localSet(k, v) { try { localStorage.setItem("nff." + k, v); } catch (e) { /* private mode */ } }
  function hashOut() {
    var h = [];
    if (S.sel !== null && S.sel !== undefined) h.push("t=" + S.sel);
    if (S.model) h.push("m=" + S.model);
    history.replaceState(null, "", "#" + h.join("&"));
  }
  function hashIn() {
    var o = {};
    location.hash.replace(/^#/, "").split("&").forEach(function (kv) { var p = kv.split("="); if (p[0]) o[p[0]] = decodeURIComponent(p[1] || ""); });
    return o;
  }
  function refresh() {
    header(); alerts(); restyleRivers(); renderList(); legend(); drawRain();
    if (S.sel !== null && S.sel !== undefined) { focusPanel(); chart(); }
  }
  function segWire(id, attr, key, after) {
    $(id).querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        S[key] = b.getAttribute(attr);
        $(id).querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
        after();
      });
    });
  }
  function wire() {
    segWire("list-pick", "data-list", "list", renderList);
    segWire("method-pick", "data-method", "method", chart);
    segWire("layer-pick", "data-layer", "layer", function () {
      document.querySelectorAll(".rain-only").forEach(function (e) { e.style.display = S.layer === "rain" ? "" : "none"; });
      $("player").hidden = !(S.layer === "rain" && S.rainWin === "step");
      restyleRivers();
      ensureRain().then(function () { drawRain(); legend(); });
    });
    document.querySelectorAll(".rain-only").forEach(function (e) { e.style.display = "none"; });
    $("rain-win").addEventListener("change", function () {
      S.rainWin = this.value;
      $("player").hidden = S.rainWin !== "step";
      if (S.rainWin === "step") {
        $("step").max = S.rainGrid.models[models()[0]].length - 1;
        S.step = S.rainGrid.now_index; $("step").value = S.step;
      }
      drawRain(); legend();
    });
    $("step").addEventListener("input", function () { S.step = +this.value; drawRain(); });
    $("play").addEventListener("click", function () {
      if (S.playing) { clearInterval(S.playing); S.playing = null; this.textContent = "▶"; return; }
      this.textContent = "❚❚";
      S.playing = setInterval(function () {
        S.step = (S.step + 1) % (+$("step").max + 1); $("step").value = S.step; drawRain();
      }, 700);
    });
    $("search").addEventListener("input", function () { S.query = this.value; renderList(); });
    $("table-toggle").addEventListener("click", function () {
      S.tableOpen = !S.tableOpen;
      this.setAttribute("aria-expanded", String(S.tableOpen));
      $("chart-table").hidden = !S.tableOpen;
      chart();
    });
    var rt, fw = 0;
    window.addEventListener("resize", function () {
      clearTimeout(rt);
      rt = setTimeout(function () {
        chart();
        var w = $("focus").clientWidth;               // the small charts only when their width changed
        if (w !== fw) { fw = w; S.redraw.forEach(function (f) { f(); }); }
      }, 150);
    });
    // a tap outside a small chart closes its tooltip (touch has no pointer leaving)
    document.addEventListener("pointerdown", function (ev) {
      document.querySelectorAll(".mc").forEach(function (m) { if (m._hideTip && !m.contains(ev.target)) m._hideTip(); });
    });
  }
  function ensureRain() {
    if (S.rainGrid) return Promise.resolve();
    return getJSON("data/rain_grid.json").then(function (g) {
      S.rainGrid = g;
      $("step").max = g.models[models()[0]].length - 1;   // the 3-hourly player opens on the current step
      S.step = g.now_index; $("step").value = S.step;
    });
  }

  function start() {
    initMap();
    Promise.all([getJSON("data/latest.json"), getJSON("data/summary.json"), getText("static/targets.csv"),
                 getJSON("static/reaches.geojson"), getJSON("static/basins.geojson"), getJSON("static/nepal.geojson")])
      .then(function (r) {
        S.latest = r[0]; S.sum = r[1]; S.targets = parseCSV(r[2]);
        var h = hashIn();
        S.model = h.m && (h.m === "any" || h.m === "agree" || models().indexOf(h.m) >= 0) ? h.m : (models().length > 1 ? "any" : models()[0]);
        if (S.latest.primary_method === "rational") {      // open the chart on the method that colours the map
          S.method = "rational";
          $("method-pick").querySelectorAll("button").forEach(function (b) {
            b.setAttribute("aria-pressed", String(b.getAttribute("data-method") === "rational"));
          });
        }
        drawLayers(r[5], r[4], r[3]);
        wire();
        refresh();
        var first = listItems()[0];
        var t = h.t !== undefined && h.t !== "" ? +h.t : (first ? first.t : null);
        if (t !== null && S.targets[t]) select(t, h.t !== undefined);
        ensureRain();
      })
      .catch(function (e) {
        $("updated").textContent = "The forecast could not be loaded (" + e.message + ").";
        console.error(e);
      });
  }
  start();
})();
