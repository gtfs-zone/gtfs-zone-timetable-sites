// Route maps, the system map, the route accordion and live vehicles.
// MapLibre and the realtime decoder load from jsdelivr when first needed.

const MAPLIBRE = "https://cdn.jsdelivr.net/npm/maplibre-gl@6.16.0/dist/";
const RT_LIB = "https://cdn.jsdelivr.net/npm/gtfs-realtime-bindings@2.2.0/+esm";
// Same CORS proxy as rt-viewer; most feeds send no CORS headers
const PROXY = "https://cors.kcfam.us/";
const STYLES = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};
const POLL_MS = 30_000;
const FONT = ["Noto Sans Bold"];
const SIZE = { radius: 8, end: 9, text: 10, line: 4, casing: 7 };
// Inset maps per route at most; each is its own WebGL map
const MAX_INSETS = 3;
// Modes drawn wide on the system map; the rest (buses) thinner and below
const WIDE = new Set(["subway", "light rail", "monorail", "train", "ferry", "cable car", "gondola", "funicular"]);

const TEXT = JSON.parse(document.getElementById("map-text")?.textContent ?? "{}");
const LANG = document.documentElement.lang;
// i18n text for key, with _one / _other picked by vars.n as i18n.plural does
function t(key, vars = {}) {
  if (vars.n != null) key += vars.n === 1 || (LANG === "fr" && vars.n === 0) ? "_one" : "_other";
  return (TEXT[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
}

// The page's theme, set by base.html, or the system's
const prefersDark = matchMedia("(prefers-color-scheme: dark)");
const isDark = () => {
  const theme = document.documentElement.dataset.theme;
  return theme ? theme === "dark" : prefersDark.matches;
};
const styleUrl = () => (isDark() ? STYLES.dark : STYLES.light);
const paper = () => (isDark() ? "#1d232a" : "#ffffff");
const ink = () => (isDark() ? "#e5e7eb" : "#1f2937");
// Stop dots are white in both themes
const DOT = "#ffffff";
const DOT_INK = "#1f2937";

// Maps with their draw functions, redrawn on the new basemap when the theme changes
const drawn = [];
function onTheme(map, draw) {
  drawn.push({ map, draw });
}
document.addEventListener("themechange", () => {
  for (const { map, draw } of drawn) {
    map.once("style.load", draw);
    map.setStyle(styleUrl(), { diff: false });
  }
});

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

// Relative luminance and contrast of #rrggbb colors, as WCAG defines them
function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
// A route color, or ink when it would vanish into the basemap
function lineColor(color) {
  if (!color) return ink();
  return contrast(color, paper()) < 1.6 ? ink() : color;
}
// Stop number color in a white dot, as pages.dot_text picks it
function dotText(color) {
  return contrast(color, DOT) >= 3 ? color : DOT_INK;
}
// Text on a filled end-stop dot: the feed's text color, or black or white
function onColor(color, text) {
  if (text && contrast(color, text) >= 3) return text;
  return contrast(color, "#000000") > contrast(color, "#ffffff") ? "#000000" : "#ffffff";
}

function boundsOf(L, coords) {
  const b = new L.LngLatBounds();
  for (const c of coords) b.extend(c);
  return b;
}

// Drops basemap points of interest, which compete with stop numbers
function quietBasemap(map) {
  for (const layer of map.getStyle().layers) {
    if (layer["source-layer"] === "poi") map.removeLayer(layer.id);
  }
}

// The compact attribution starts collapsed; MapLibre opens it on first load
function newMap(L, container, bounds, options = {}) {
  const map = new L.Map({
    container,
    style: styleUrl(),
    bounds,
    fitBoundsOptions: { padding: 30, maxZoom: 15 },
    cooperativeGestures: true,
    attributionControl: { compact: true },
    ...options,
  });
  map.once("load", () => {
    const attrib = map.getContainer().querySelector(".maplibregl-ctrl-attrib");
    attrib?.classList.remove("maplibregl-compact-show");
    attrib?.removeAttribute("open");
  });
  return map;
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

/* Route maps */

function routeGeojson(data) {
  const features = data.lines.map((c) => ({
    type: "Feature",
    properties: { kind: "line" },
    geometry: { type: "LineString", coordinates: c.length > 1 ? c : [c[0], c[0]] },
  }));
  for (const [lon, lat, num, name, end] of data.stops) {
    features.push({
      type: "Feature",
      properties: { kind: "stop", num, name, end },
      geometry: { type: "Point", coordinates: [lon, lat] },
    });
  }
  return { type: "FeatureCollection", features };
}

function allCoords(data) {
  return [...data.lines.flat(), ...data.stops.map((s) => [s[0], s[1]])];
}

function addRouteLayers(map, data) {
  const color = lineColor(data.color);
  const text = onColor(color, data.text);
  const isLine = ["==", ["get", "kind"], "line"];
  const isStop = ["==", ["get", "kind"], "stop"];
  const isEnd = ["==", ["get", "end"], 1];
  const layout = { "line-join": "round", "line-cap": "round" };
  map.addSource("route", { type: "geojson", data: routeGeojson(data) });
  map.addLayer({ id: "casing", type: "line", source: "route", filter: isLine, layout, paint: { "line-color": paper(), "line-width": SIZE.casing } });
  map.addLayer({ id: "line", type: "line", source: "route", filter: isLine, layout, paint: { "line-color": color, "line-width": SIZE.line } });
  map.addLayer({
    id: "stops",
    type: "circle",
    source: "route",
    filter: isStop,
    paint: {
      "circle-radius": ["case", isEnd, SIZE.end, SIZE.radius],
      "circle-color": ["case", isEnd, color, DOT],
      "circle-stroke-color": color,
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: "stop-nums",
    type: "symbol",
    source: "route",
    filter: isStop,
    layout: {
      "text-field": ["to-string", ["get", "num"]],
      "text-font": FONT,
      "text-size": SIZE.text,
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: { "text-color": ["case", isEnd, text, dotText(color)] },
  });
  // End stops named where there is room
  map.addLayer({
    id: "end-names",
    type: "symbol",
    source: "route",
    filter: ["all", isStop, isEnd],
    layout: {
      "text-field": ["get", "name"],
      "text-font": FONT,
      "text-size": 12,
      "text-variable-anchor": ["left", "right", "top", "bottom"],
      "text-radial-offset": 1.1,
      "text-justify": "auto",
      "text-max-width": 10,
    },
    paint: { "text-color": ink(), "text-halo-color": paper(), "text-halo-width": 1.5 },
  });
}

function stopPopup(card, props) {
  const root = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = `${props.num}. ${props.name}`;
  root.append(name);
  const row = card.querySelector(`th[data-num="${props.num}"]`);
  if (row) {
    const p = document.createElement("p");
    const a = document.createElement("a");
    a.className = "link";
    a.href = "#";
    a.textContent = t("map.in_timetable");
    a.addEventListener("click", (e) => {
      e.preventDefault();
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.parentElement.classList.add("hl");
      setTimeout(() => row.parentElement.classList.remove("hl"), 2000);
    });
    p.append(a);
    root.append(p);
  }
  return root;
}

function stopHandlers(L, map, card) {
  const layer = "stops";
  map.on("click", layer, (e) => {
    const f = e.features[0];
    new L.Popup({ offset: 8 }).setLngLat(f.geometry.coordinates).setDOMContent(stopPopup(card, f.properties)).addTo(map);
  });
  map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
}

function stopPoints(map, data) {
  return data.stops.map(([lon, lat, num]) => ({ num, coords: [lon, lat], p: map.project([lon, lat]) }));
}

function pixelBox(points, pad) {
  const xs = points.map((s) => s.p.x);
  const ys = points.map((s) => s.p.y);
  return { x0: Math.min(...xs) - pad, y0: Math.min(...ys) - pad, x1: Math.max(...xs) + pad, y1: Math.max(...ys) + pad };
}
const intersects = (a, b) => a.x0 <= b.x1 && b.x0 <= a.x1 && a.y0 <= b.y1 && b.y0 <= a.y1;

// Groups of stops close enough on screen to hide each other's numbers; groups
// whose padded boxes touch merge into one
function findClusters(map, data) {
  const stops = stopPoints(map, data);
  const parent = stops.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < stops.length; i++) {
    for (let j = i + 1; j < stops.length; j++) {
      if (Math.hypot(stops[i].p.x - stops[j].p.x, stops[i].p.y - stops[j].p.y) < SIZE.radius * 1.1) {
        parent[find(j)] = find(i);
      }
    }
  }
  const byRoot = new Map();
  stops.forEach((s, i) => {
    const r = find(i);
    if (!byRoot.has(r)) byRoot.set(r, []);
    byRoot.get(r).push(s);
  });
  const pad = SIZE.radius * 2;
  const groups = [...byRoot.values()].filter((g) => g.length > 1);
  for (let merged = true; merged; ) {
    merged = false;
    outer: for (let i = 0; i < groups.length; i++) {
      for (let j = i + 1; j < groups.length; j++) {
        if (intersects(pixelBox(groups[i], pad), pixelBox(groups[j], pad))) {
          groups[i] = groups[i].concat(groups[j]);
          groups.splice(j, 1);
          merged = true;
          break outer;
        }
      }
    }
  }
  return groups.map((g) => ({ nums: g.map((s) => s.num), coords: g.map((s) => s.coords), stops: g }));
}

const RANK = { city: 0, town: 1, village: 2, suburb: 3, neighbourhood: 4 };

// The largest place shown near coords, from the basemap's place labels
function placeName(map, coords) {
  const cx = coords.reduce((a, c) => a + c[0], 0) / coords.length;
  const cy = coords.reduce((a, c) => a + c[1], 0) / coords.length;
  const kx = Math.cos((cy * Math.PI) / 180);
  const view = map.getBounds();
  const places = map
    .querySourceFeatures("openmaptiles", { sourceLayer: "place" })
    .filter((f) => f.properties.class in RANK && f.geometry.type === "Point")
    .map((f) => {
      const [x, y] = f.geometry.coordinates;
      return {
        name: f.properties[`name:${LANG}`] || f.properties.name,
        rank: RANK[f.properties.class],
        dist: Math.hypot((x - cx) * kx, y - cy),
        inView: view.contains([x, y]),
      };
    });
  const inView = places.filter((p) => p.inView).sort((a, b) => a.rank - b.rank || a.dist - b.dist);
  return (inView[0] ?? places.sort((a, b) => a.dist - b.dist)[0])?.name ?? "";
}

// Hides clustered stops' numbers on the main map; their insets show them
function hideClustered(map, clusters) {
  const nums = clusters.flatMap((c) => c.nums);
  map.setFilter("stop-nums", ["all", ["==", ["get", "kind"], "stop"], ["!", ["in", ["get", "num"], ["literal", nums]]]]);
}

// The area a map shows, as a closed ring
function viewRing(map) {
  const b = map.getBounds();
  const [w, s, e, n] = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  return [[w, s], [e, s], [e, n], [w, n], [w, s]];
}

// Boxes on the main map around what each inset shows; skipped until the
// route layers are on the current style
function drawBoxes(map, rings) {
  const data = {
    type: "FeatureCollection",
    features: rings.filter(Boolean).map((ring) => ({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } })),
  };
  const source = map.getSource("inset-boxes");
  if (source) return source.setData(data);
  if (!map.getLayer("stops")) return;
  map.addSource("inset-boxes", { type: "geojson", data });
  map.addLayer({ id: "inset-boxes", type: "line", source: "inset-boxes", paint: { "line-color": ink(), "line-width": 1 } });
}

// An inset map per cluster; onView gets each inset's index and view ring
// whenever it settles
function buildInsets(L, container, card, data, clusters, onView) {
  // Every figure goes in before any map, so each map fits its bounds at its final size
  const figs = clusters.map(() => {
    const fig = document.createElement("figure");
    fig.className = "route-inset";
    const cap = document.createElement("figcaption");
    cap.textContent = " ";
    const div = document.createElement("div");
    div.className = "route-inset-map";
    fig.append(cap, div);
    container.append(fig);
    return { cap, div };
  });
  clusters.forEach((c, i) => {
    const { cap, div } = figs[i];
    const map = newMap(L, div, boundsOf(L, c.coords), {
      fitBoundsOptions: { padding: SIZE.radius * 3, maxZoom: 17 },
      attributionControl: false,
    });
    const draw = () => {
      quietBasemap(map);
      addRouteLayers(map, data);
    };
    map.once("load", () => {
      draw();
      stopHandlers(L, map, card);
      map.once("idle", () => (cap.textContent = placeName(map, c.coords) || " "));
      onView(i, viewRing(map));
    });
    map.on("moveend", () => onView(i, viewRing(map)));
    onTheme(map, draw);
  });
}

async function initRouteMap(card) {
  const holder = card.querySelector(".route-maps");
  const data = JSON.parse(card.querySelector(".route-data").textContent);
  if (!holder || !data.lines.length) return;
  const L = await maplibre();
  const bounds = boundsOf(L, allCoords(data));
  const fit = () => map.fitBounds(bounds, { padding: 30, maxZoom: 15, duration: 0 });
  const map = newMap(L, holder.querySelector(".route-map"), bounds);
  map.addControl(new L.NavigationControl({ showCompass: false }), "top-right");
  let clusters = [];
  let crowded = false;
  let vehicles;
  const rings = [];
  // Crowded stops: numbers hidden on collision, or left to the insets
  const showClusters = () => {
    if (crowded) {
      map.setLayoutProperty("stop-nums", "text-allow-overlap", false);
      map.setLayoutProperty("stop-nums", "text-ignore-placement", false);
    }
    if (clusters.length) {
      hideClustered(map, clusters);
      drawBoxes(map, rings);
    }
  };
  const draw = () => {
    quietBasemap(map);
    addRouteLayers(map, data);
    showClusters();
    vehicles?.draw();
  };
  map.once("load", () => {
    draw();
    stopHandlers(L, map, card);
    map.once("idle", () => {
      let found = findClusters(map, data);
      if (found.length && found.length <= MAX_INSETS) {
        // The insets column narrows the map, so stops crowd differently
        holder.classList.add("has-insets");
        map.resize();
        fit();
        found = findClusters(map, data);
        if (!found.length || found.length > MAX_INSETS) {
          holder.classList.remove("has-insets");
          map.resize();
          fit();
        }
      }
      if (found.length > MAX_INSETS) {
        // Too many crowded spots for insets: numbers that collide hide until zoomed in
        crowded = true;
      } else {
        clusters = found;
      }
      showClusters();
      if (clusters.length) {
        buildInsets(L, holder.querySelector(".route-insets"), card, data, clusters, (i, ring) => {
          rings[i] = ring;
          drawBoxes(map, rings);
        });
        // The main map stretches to a taller insets column
        map.resize();
        fit();
      }
    });
    if (data.rt) {
      const routes = new Set(data.rt.routes);
      const trips = new Set(data.rt.trips);
      vehicles = live(L, map, holder, data.rt.url, (v) => (routes.has(v.routeId) || trips.has(v.tripId) ? lineColor(data.color) : null));
    }
  });
  onTheme(map, draw);
}

/* System map */

function systemGeojson(data) {
  const modes = (owners) => `,${[...new Set(owners.map((i) => data.routes[i].mode))].join(",")},`;
  const features = data.lines.map((line) => {
    const first = data.routes[line.r[0]];
    return {
      type: "Feature",
      properties: {
        rs: `,${line.r.join(",")},`,
        ms: modes(line.r),
        color: lineColor(first.color),
        wide: line.r.some((i) => WIDE.has(data.routes[i].mode)) ? 1 : 0,
      },
      geometry: { type: "LineString", coordinates: line.c.length > 1 ? line.c : [line.c[0], line.c[0]] },
    };
  });
  const stops = data.stops.map(([lon, lat, name, owners]) => ({
    type: "Feature",
    properties: { name, ms: modes(owners) },
    geometry: { type: "Point", coordinates: [lon, lat] },
  }));
  return { lines: { type: "FeatureCollection", features }, stops: { type: "FeatureCollection", features: stops } };
}

function modeFilter(enabled) {
  return ["any", ...[...enabled].map((m) => ["in", `,${m},`, ["get", "ms"]])];
}

function badge(route) {
  const span = document.createElement("span");
  span.className = "badge badge-sm h-auto min-w-10 whitespace-normal border-0 font-bold";
  if (route.color) {
    span.style.background = route.color;
    span.style.color = route.text ?? "#fff";
  }
  span.textContent = route.badge;
  return span;
}

function openRoute(slug) {
  if (location.hash === `#r-${slug}`) openFromHash();
  else location.hash = `r-${slug}`;
}

async function initSystemMap(el) {
  const [L, data] = await Promise.all([maplibre(), fetch(el.dataset.src).then((r) => r.json())]);
  if (!data.lines.length) return;
  const bounds = boundsOf(L, data.lines.flatMap((l) => l.c));
  const map = newMap(L, el.querySelector(".system-map"), bounds, { fitBoundsOptions: { padding: 30 } });
  map.addControl(new L.NavigationControl({ showCompass: false }), "top-right");
  const modes = [...new Set(data.routes.map((r) => r.mode))];
  const enabled = new Set(modes);
  const width = (wide, narrow) => ["case", ["==", ["get", "wide"], 1], wide, narrow];
  const layout = { "line-join": "round", "line-cap": "round", "line-sort-key": ["get", "wide"] };
  const filtered = ["sys-casing", "sys-lines", "sys-transfers", "sys-transfer-names"];
  const showModes = () => {
    if (!map.getLayer("sys-lines")) return;
    const filter = modeFilter(enabled);
    for (const id of filtered) map.setFilter(id, filter);
    map.setFilter("sys-hover", ["==", ["get", "rs"], ""]);
  };
  let vehicles;

  // Line colors depend on the basemap, so the GeoJSON is rebuilt per style
  const draw = () => {
    const geo = systemGeojson(data);
    quietBasemap(map);
    map.addSource("lines", { type: "geojson", data: geo.lines });
    map.addSource("transfers", { type: "geojson", data: geo.stops });
    map.addLayer({ id: "sys-casing", type: "line", source: "lines", layout, paint: { "line-color": paper(), "line-width": width(7, 4.5) } });
    map.addLayer({ id: "sys-lines", type: "line", source: "lines", layout, paint: { "line-color": ["get", "color"], "line-width": width(4, 2.5) } });
    map.addLayer({ id: "sys-hover", type: "line", source: "lines", layout, filter: ["==", ["get", "rs"], ""], paint: { "line-color": ["get", "color"], "line-width": width(7, 5) } });
    map.addLayer({
      id: "sys-transfers",
      type: "circle",
      source: "transfers",
      minzoom: 13,
      paint: { "circle-radius": 4, "circle-color": paper(), "circle-stroke-color": ink(), "circle-stroke-width": 1.5 },
    });
    map.addLayer({
      id: "sys-transfer-names",
      type: "symbol",
      source: "transfers",
      minzoom: 14,
      layout: { "text-field": ["get", "name"], "text-font": FONT, "text-size": 11, "text-variable-anchor": ["left", "right"], "text-radial-offset": 0.8, "text-optional": true },
      paint: { "text-color": ink(), "text-halo-color": paper(), "text-halo-width": 1.5 },
    });
    if (enabled.size < modes.length) showModes();
    vehicles?.draw();
  };
  map.once("load", () => {
    draw();
    if (data.rt) {
      const byId = new Map();
      data.routes.forEach((r, i) => r.ids.forEach((id) => byId.set(id, i)));
      vehicles = live(L, map, el, data.rt, (v) => {
        const i = byId.get(v.routeId);
        return i == null || !enabled.has(data.routes[i].mode) ? null : lineColor(data.routes[i].color);
      });
    }
  });
  onTheme(map, draw);

  const chips = el.querySelector(".map-modes");
  if (modes.length > 1 && chips) {
    // Shown modes filled in the text color, hidden ones outlined, for contrast in both themes
    const chipClass = (on) => `btn btn-sm ${on ? "border-base-content bg-base-content text-base-100" : "btn-outline"}`;
    for (const mode of modes) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = chipClass(true);
      button.setAttribute("aria-pressed", "true");
      button.textContent = TEXT.modes?.[mode] ?? mode;
      button.addEventListener("click", () => {
        if (enabled.has(mode) && enabled.size > 1) enabled.delete(mode);
        else enabled.add(mode);
        for (const b of chips.children) {
          const on = enabled.has(b.dataset.mode);
          b.className = chipClass(on);
          b.setAttribute("aria-pressed", String(on));
        }
        showModes();
      });
      button.dataset.mode = mode;
      chips.append(button);
    }
    chips.hidden = false;
  }

  const cap = el.querySelector(".map-cap");
  function routesAt(point, pad) {
    // None while a new basemap style loads
    if (!map.getLayer("sys-lines")) return [];
    const box = [
      [point.x - pad, point.y - pad],
      [point.x + pad, point.y + pad],
    ];
    const ids = new Set();
    for (const f of map.queryRenderedFeatures(box, { layers: ["sys-lines"] })) {
      for (const i of f.properties.rs.split(",").filter(Boolean)) {
        if (enabled.has(data.routes[+i].mode)) ids.add(+i);
      }
    }
    return [...ids];
  }
  map.on("mousemove", (e) => {
    const ids = routesAt(e.point, 4);
    map.getCanvas().style.cursor = ids.length ? "pointer" : "";
    if (map.getLayer("sys-hover")) map.setFilter("sys-hover", ids.length ? ["in", `,${ids[0]},`, ["get", "rs"]] : ["==", ["get", "rs"], ""]);
    if (cap) {
      cap.hidden = !ids.length;
      if (ids.length) cap.textContent = data.routes[ids[0]].title;
    }
  });
  map.on("mouseout", () => cap && (cap.hidden = true));
  let popup;
  map.on("click", (e) => {
    popup?.remove();
    const ids = routesAt(e.point, 8);
    if (ids.length === 1) return openRoute(data.routes[ids[0]].slug);
    if (!ids.length) return;
    const ul = document.createElement("ul");
    ul.className = "map-popup-routes";
    for (const i of ids) {
      const r = data.routes[i];
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = `#r-${r.slug}`;
      a.title = r.title;
      a.append(badge(r));
      li.append(a);
      ul.append(li);
    }
    ul.addEventListener("click", () => popup.remove());
    popup = new L.Popup({ offset: 8, closeButton: false, maxWidth: "16rem" }).setLngLat(e.lngLat).setDOMContent(ul).addTo(map);
  });
}

/* Live vehicles */

let rtLib;
const rt = () => (rtLib ??= import(RT_LIB).then((m) => (m.default ?? m).transit_realtime));


function arrowImage(map) {
  const s = 32;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = s;
  const g = canvas.getContext("2d");
  g.fillStyle = ink();
  g.beginPath();
  g.moveTo(s / 2, 1);
  g.lineTo(s / 2 + 6, 11);
  g.lineTo(s / 2 - 6, 11);
  g.closePath();
  g.fill();
  if (!map.hasImage("bearing")) map.addImage("bearing", g.getImageData(0, 0, s, s), { pixelRatio: 2 });
}

// Polls url every POLL_MS while el is on screen and the tab is visible; colorOf
// gives a vehicle's color, or null to leave it off this map. Returns draw,
// which puts the vehicle layers on a new basemap style
function live(L, map, el, url, colorOf) {
  let vehicles = { type: "FeatureCollection", features: [] };
  const draw = () => {
    arrowImage(map);
    map.addSource("vehicles", { type: "geojson", data: vehicles });
    map.addLayer({
      id: "vehicle-bearing",
      type: "symbol",
      source: "vehicles",
      filter: ["has", "bearing"],
      layout: { "icon-image": "bearing", "icon-rotate": ["get", "bearing"], "icon-rotation-alignment": "map", "icon-allow-overlap": true },
    });
    map.addLayer({
      id: "vehicles",
      type: "circle",
      source: "vehicles",
      paint: { "circle-radius": 6, "circle-color": ["get", "color"], "circle-stroke-color": paper(), "circle-stroke-width": 2 },
    });
  };
  draw();
  map.on("click", "vehicles", (e) => {
    const f = e.features[0];
    new L.Popup({ offset: 10 }).setLngLat(f.geometry.coordinates).setText(f.properties.label).addTo(map);
  });
  const status = el.querySelector(".map-live");
  let visible = false;
  let timer;
  async function poll() {
    clearTimeout(timer);
    if (!visible || document.hidden) return;
    try {
      const { FeedMessage } = await rt();
      const res = await fetch(PROXY + url, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const feed = FeedMessage.decode(new Uint8Array(await res.arrayBuffer()));
      const features = [];
      for (const entity of feed.entity ?? []) {
        const v = entity.vehicle;
        const pos = v?.position;
        if (!pos) continue;
        const color = colorOf({ routeId: v.trip?.routeId, tripId: v.trip?.tripId });
        if (!color) continue;
        const label = v.vehicle?.label || v.vehicle?.id || entity.id;
        const properties = { color, label: t("map.vehicle", { label }) };
        if (pos.bearing != null) properties.bearing = pos.bearing;
        features.push({ type: "Feature", properties, geometry: { type: "Point", coordinates: [pos.longitude, pos.latitude] } });
      }
      vehicles = { type: "FeatureCollection", features };
      map.getSource("vehicles")?.setData(vehicles);
      if (status) {
        status.hidden = false;
        status.textContent = t("map.live", { n: features.length });
      }
    } catch (err) {
      console.warn("vehicles", err);
      if (status) status.hidden = true;
    }
    timer = setTimeout(poll, POLL_MS);
  }
  new IntersectionObserver((entries) => {
    visible = entries.some((e) => e.isIntersecting);
    poll();
  }).observe(el);
  document.addEventListener("visibilitychange", poll);
  return { draw };
}

/* Route accordion on the site's index */

async function loadRoute(details) {
  const body = details.querySelector(".route-body");
  if (!body || body.dataset.loaded) return;
  body.dataset.loaded = "1";
  try {
    const res = await fetch(body.dataset.src);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const doc = new DOMParser().parseFromString(await res.text(), "text/html");
    const content = doc.querySelector("[data-route-body]");
    if (!content) throw new Error("no route body");
    body.querySelector(".route-loading")?.remove();
    // Links in the route page are relative to it
    const base = new URL(body.dataset.src, location.href);
    for (const a of content.querySelectorAll("a[href]")) a.href = new URL(a.getAttribute("href"), base).href;
    // Above the route-page button, which stays last
    const open = body.querySelector(".route-open");
    if (open) open.before(...content.childNodes);
    else body.append(...content.childNodes);
    if (body.querySelector(".route-data")) initRouteMap(body);
  } catch (err) {
    console.warn("route", err);
    const p = body.querySelector(".route-loading");
    if (p) p.textContent = t("index.load_error");
  }
}

function openFromHash() {
  const m = /^#r-(.+)$/.exec(location.hash);
  if (!m) return;
  const details = document.getElementById(`r-${decodeURIComponent(m[1])}`);
  if (!details) return;
  details.open = true;
  details.scrollIntoView({ behavior: "smooth", block: "start" });
}

for (const details of document.querySelectorAll("details.route-item")) {
  details.addEventListener("toggle", () => details.open && loadRoute(details));
}
addEventListener("hashchange", openFromHash);
openFromHash();

/* Start */

for (const card of document.querySelectorAll("[data-route-body]")) {
  const holder = card.querySelector(".route-maps");
  if (holder) whenNear(holder, () => initRouteMap(card));
}
const system = document.querySelector(".system");
if (system) whenNear(system, () => initSystemMap(system));
