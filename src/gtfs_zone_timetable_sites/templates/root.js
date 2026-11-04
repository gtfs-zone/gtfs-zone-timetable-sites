// The root and country pages: choropleth maps of countries or regions, and
// agency search. MapLibre and uFuzzy load from jsdelivr when first needed.

const MAPLIBRE = "https://cdn.jsdelivr.net/npm/maplibre-gl@6.16.0/dist/";
const UFUZZY = "https://cdn.jsdelivr.net/npm/@leeoniya/ufuzzy@1.0.19/dist/uFuzzy.mjs";
// Shown search results at most
const MAX_RESULTS = 50;
// Above this many matches uFuzzy skips ranking, as in gtfs-zone-web-common
const RANK_THRESHOLD = 1000;

// The page's theme, set by base.html, or the system's
const prefersDark = matchMedia("(prefers-color-scheme: dark)");
const isDark = () => {
  const theme = document.documentElement.dataset.theme;
  return theme ? theme === "dark" : prefersDark.matches;
};
// Fill colors from no sites up to the most, then sea and borders, per theme
const PALETTE = {
  light: { none: "#e5e7eb", ramp: ["#dbeafe", "#93c5fd", "#3b82f6", "#1d4ed8", "#1e3a8a"], sea: "#ffffff", border: "#ffffff", hover: "#111827" },
  dark: { none: "#2a323c", ramp: ["#1e3a8a", "#1d4ed8", "#3b82f6", "#93c5fd", "#dbeafe"], sea: "#1d232a", border: "#1d232a", hover: "#f9fafb" },
};
const palette = () => PALETTE[isDark() ? "dark" : "light"];

let lib;
function maplibre() {
  lib ??= (async () => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${MAPLIBRE}maplibre-gl.css`;
    document.head.append(css);
    return import(`${MAPLIBRE}maplibre-gl.mjs`);
  })();
  return lib;
}

let fuzzy;
const uFuzzy = () => (fuzzy ??= import(UFUZZY).then((m) => new m.default({ intraIns: 1 })));

// Indexes into haystack matching query, best first
function search(uf, haystack, query) {
  const [idxs, info, order] = uf.search(haystack, query, 1, RANK_THRESHOLD);
  return info && order ? order.map((o) => info.idx[o]) : (idxs ?? []);
}

// Runs fn once el comes within 300px of the viewport
function whenNear(el, fn) {
  const seen = new IntersectionObserver(
    (entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      seen.disconnect();
      fn();
    },
    { rootMargin: "300px" },
  );
  seen.observe(el);
}

/* Choropleth */

// Up to 5 class breaks from 1 to max, evenly spaced on a log scale
function breaks(max) {
  const found = new Set([1]);
  for (let k = 1; k < 5; k++) found.add(Math.max(1, Math.round(max ** (k / 5))));
  return [...found].sort((a, b) => a - b);
}

function fillColor(max) {
  const { none, ramp } = palette();
  const [first, ...rest] = breaks(max);
  // The ramp's top colors for the fewest classes, so few sites still read as dark
  const colors = ramp.slice(ramp.length - rest.length - 1);
  const step = ["step", ["get", "count"], colors[0]];
  rest.forEach((b, i) => step.push(b, colors[i + 1]));
  return ["case", ["<", ["get", "count"], first], none, step];
}

function areaStyle(data, max) {
  const { sea, border, hover } = palette();
  return {
    version: 8,
    sources: { areas: { type: "geojson", data, promoteId: "ref" } },
    layers: [
      { id: "sea", type: "background", paint: { "background-color": sea } },
      { id: "fill", type: "fill", source: "areas", paint: { "fill-color": fillColor(max) } },
      { id: "border", type: "line", source: "areas", paint: { "line-color": border, "line-width": 0.6 } },
      {
        id: "hover",
        type: "line",
        source: "areas",
        paint: { "line-color": hover, "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, 0] },
      },
    ],
  };
}

// The areas with sites, each by its largest polygon, so islands across the
// antimeridian (the Aleutians) don't stretch the view around the globe
function boundsOf(L, data) {
  const b = new L.LngLatBounds();
  for (const f of data.features) {
    if (!f.properties.count) continue;
    const { type, coordinates } = f.geometry;
    const rings = type === "Polygon" ? [coordinates[0]] : coordinates.map((p) => p[0]);
    const span = (ring) => {
      const xs = ring.map((c) => c[0]);
      const ys = ring.map((c) => c[1]);
      return (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys));
    };
    const largest = rings.reduce((a, r) => (span(r) > span(a) ? r : a));
    for (const c of largest) b.extend(c);
  }
  return b;
}

// el's data-src is GeoJSON with `count` per feature and data-key the property
// its link is made from: data-href with {} replaced by it
async function initChoropleth(el) {
  const [L, data] = await Promise.all([maplibre(), fetch(el.dataset.src).then((r) => r.json())]);
  const key = el.dataset.key;
  for (const f of data.features) f.properties.ref = f.properties[key];
  const max = Math.max(1, ...data.features.map((f) => f.properties.count));
  const world = key === "key";
  const map = new L.Map({
    container: el.querySelector(".area-map"),
    style: areaStyle(data, max),
    bounds: world ? [[-170, -56], [180, 78]] : boundsOf(L, data),
    fitBoundsOptions: { padding: 16 },
    renderWorldCopies: false,
    cooperativeGestures: true,
    dragRotate: false,
    pitchWithRotate: false,
    attributionControl: false,
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new L.NavigationControl({ showCompass: false }), "top-right");
  document.addEventListener("themechange", () => map.setStyle(areaStyle(data, max), { diff: false }));

  const cap = el.querySelector(".map-cap");
  let hovered = null;
  const setHover = (feature) => {
    if (hovered != null) map.setFeatureState({ source: "areas", id: hovered }, { hover: false });
    hovered = feature?.properties.count ? feature.id : null;
    if (hovered != null) map.setFeatureState({ source: "areas", id: hovered }, { hover: true });
    map.getCanvas().style.cursor = hovered != null ? "pointer" : "";
    if (!cap) return;
    cap.hidden = !feature;
    if (!feature) return;
    const { name, count } = feature.properties;
    cap.textContent = count ? `${name}: ${count} agenc${count === 1 ? "y" : "ies"}` : name;
  };
  const at = (point) => (map.getLayer("fill") ? map.queryRenderedFeatures(point, { layers: ["fill"] })[0] : undefined);
  map.on("mousemove", (e) => setHover(at(e.point)));
  map.on("mouseout", () => setHover(null));
  map.on("click", (e) => {
    const feature = at(e.point);
    if (!feature?.properties.count) return;
    location.href = el.dataset.href.replace("{}", encodeURIComponent(feature.properties.ref));
  });
}

for (const el of document.querySelectorAll(".area")) whenNear(el, () => initChoropleth(el));

/* Search over sites.json, on the root page */

const STATUS_DOT = { ok: "bg-success", expiring: "bg-warning", stale: "bg-warning", expired: "bg-error", unavailable: "bg-base-300" };

function resultItem([slug, title, source, where, status]) {
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.className = "flex min-h-11 items-center gap-3 rounded-box px-4 py-2 hover:bg-base-200 focus-visible:bg-base-200";
  a.href = `${slug}/`;
  const dot = document.createElement("span");
  dot.className = `size-3 shrink-0 rounded-full ${STATUS_DOT[status] ?? "bg-base-300"}`;
  const text = document.createElement("span");
  text.className = "flex min-w-0 flex-1 flex-col";
  const name = document.createElement("span");
  name.className = "font-semibold";
  name.textContent = title;
  const line = document.createElement("span");
  line.className = "break-all text-sm text-base-content/70";
  line.textContent = [where, source].filter(Boolean).join(" - ");
  text.append(name, line);
  a.append(dot, text);
  li.append(a);
  return li;
}

function initSiteSearch(input) {
  const results = document.getElementById(input.getAttribute("aria-controls"));
  const status = document.getElementById(input.dataset.status);
  let ready;
  const load = () =>
    (ready ??= Promise.all([uFuzzy(), fetch(input.dataset.src).then((r) => r.json())]).then(([uf, rows]) => ({
      uf,
      rows,
      haystack: rows.map(([, title, source, where]) => `${title} ${where} ${source}`),
    })));
  const update = async () => {
    const query = input.value.trim();
    if (!query) {
      results.replaceChildren();
      results.hidden = true;
      status.textContent = "";
      return;
    }
    const { uf, rows, haystack } = await load();
    // A newer query was typed while loading
    if (input.value.trim() !== query) return;
    const found = search(uf, haystack, query);
    results.replaceChildren(...found.slice(0, MAX_RESULTS).map((i) => resultItem(rows[i])));
    results.hidden = !found.length;
    status.textContent = found.length
      ? found.length > MAX_RESULTS
        ? `${found.length} agencies, the first ${MAX_RESULTS} shown`
        : `${found.length} agenc${found.length === 1 ? "y" : "ies"}`
      : "No agencies found";
  };
  input.addEventListener("focus", load, { once: true });
  input.addEventListener("input", update);
  update();
}

/* Filter of the listed sites, on a country page */

function initListFilter(input) {
  const sections = [...document.querySelectorAll(".site-section")];
  const items = sections.flatMap((s) => [...s.querySelectorAll("li")]);
  const haystack = items.map((li) => `${li.textContent.replace(/\s+/g, " ")} ${li.closest(".site-section").dataset.name ?? ""}`);
  const status = document.getElementById(input.dataset.status);
  const update = async () => {
    const query = input.value.trim();
    const shown = query ? new Set(search(await uFuzzy(), haystack, query)) : null;
    if (input.value.trim() !== query) return;
    items.forEach((li, i) => (li.hidden = shown != null && !shown.has(i)));
    for (const section of sections) section.hidden = ![...section.querySelectorAll("li")].some((li) => !li.hidden);
    status.textContent = shown ? (shown.size ? `${shown.size} agenc${shown.size === 1 ? "y" : "ies"}` : "No agencies found") : "";
  };
  input.addEventListener("focus", uFuzzy, { once: true });
  input.addEventListener("input", update);
  update();
}

const siteSearch = document.getElementById("site-search");
if (siteSearch?.dataset.src) initSiteSearch(siteSearch);
else if (siteSearch) initListFilter(siteSearch);
