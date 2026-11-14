// Route maps, the system map, the route accordion and live vehicles.
// MapLibre and the realtime decoder load from jsdelivr when first needed.

const MAPLIBRE = "https://cdn.jsdelivr.net/npm/maplibre-gl@6.16.0/dist/";
const RT_LIB = "https://cdn.jsdelivr.net/npm/gtfs-realtime-bindings@2.2.0/+esm";
// Same CORS proxy as rt-viewer; most feeds send no CORS headers
const PROXY = "https://cors.gtfs.zone/";
// rt.gtfs.zone sends its own CORS headers
const viaProxy = (url) => (new URL(url).hostname === "rt.gtfs.zone" ? url : PROXY + url);
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
    // Before the page's stylesheet, so its map rules win at equal specificity
    const own = document.querySelector('link[rel="stylesheet"][href$="style.css"]');
    if (own) own.before(css);
    else document.head.append(css);
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

/* Clicks: one handler and one popup per map */

const clickers = new WeakMap();
// Adds handle(e) to the map's click handlers, tried by rank (lowest first);
// the first returning { at, content } shows it in the map's single popup (and
// passes it to hit.opened), and one returning true takes the click without a popup
function onClick(L, map, rank, handle) {
  let c = clickers.get(map);
  if (!c) {
    c = { handlers: [], popup: null };
    clickers.set(map, c);
    map.on("click", (e) => {
      c.popup?.remove();
      c.popup = null;
      for (const { handle } of c.handlers) {
        const hit = handle(e);
        if (!hit) continue;
        if (hit.content) {
          c.popup = new L.Popup({ offset: 10, maxWidth: "18rem", ...hit.options }).setLngLat(hit.at).setDOMContent(hit.content).addTo(map);
          hit.opened?.(c.popup);
        }
        return;
      }
    });
  }
  c.handlers.push({ rank, handle });
  c.handlers.sort((a, b) => a.rank - b.rank);
}
const closePopup = (map) => clickers.get(map)?.popup?.remove();
const RANK_VEHICLE = 0;
const RANK_STOP = 1;
const RANK_LINE = 2;

// Features of layers within pad pixels of point; none while a new basemap style loads
function hits(map, point, layers, pad = 6) {
  if (!layers.every((id) => map.getLayer(id))) return [];
  return map.queryRenderedFeatures(
    [
      [point.x - pad, point.y - pad],
      [point.x + pad, point.y + pad],
    ],
    { layers },
  );
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

// Highlights timetable rows or cells for 2 s
function flash(els) {
  for (const el of els) el.classList.add("hl");
  setTimeout(() => els.forEach((el) => el.classList.remove("hl")), 2000);
}

// A popup link that runs fn and closes the popup
function popupLink(map, text, fn) {
  const p = document.createElement("p");
  const a = document.createElement("a");
  a.className = "link";
  a.href = "#";
  a.textContent = text;
  a.addEventListener("click", (e) => {
    e.preventDefault();
    closePopup(map);
    fn();
  });
  p.append(a);
  return p;
}

// Each stop's number and name, with a link to its timetable row
function stopPopup(map, card, stops) {
  const root = document.createElement("div");
  root.className = "map-popup-stops";
  for (const props of stops) {
    const item = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = `${props.num}. ${props.name}`;
    item.append(name);
    const row = card.querySelector(`th[data-num="${props.num}"]`);
    if (row) {
      item.append(
        popupLink(map, t("map.in_timetable"), () => {
          row.scrollIntoView({ behavior: "smooth", block: "center" });
          flash([row.parentElement]);
        }),
      );
    }
    root.append(item);
  }
  return root;
}

function stopHandlers(L, map, card) {
  onClick(L, map, RANK_STOP, (e) => {
    const found = hits(map, e.point, ["stops"], 4);
    if (!found.length) return null;
    const stops = [...new Map(found.map((f) => [f.properties.num, f.properties])).values()].sort((a, b) => a.num - b.num);
    return { at: found[0].geometry.coordinates, content: stopPopup(map, card, stops) };
  });
  map.on("mouseenter", "stops", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "stops", () => (map.getCanvas().style.cursor = ""));
}

// A row's stop as the stop popups name it: "num. name", without the rail and notes
function rowStop(th) {
  const stop = th.querySelector(".stop")?.cloneNode(true);
  if (!stop) return "";
  for (const el of stop.querySelectorAll(".rail, small, svg")) el.remove();
  const name = stop.textContent.trim();
  return th.dataset.num ? `${th.dataset.num}. ${name}` : name;
}

// The headsign over a table column, from the colspans of its "to" row
function columnHeadsign(table, col) {
  const cells = table.tHead?.querySelector("tr.to")?.cells;
  if (!cells) return "";
  let at = 0;
  for (const cell of [...cells].slice(1)) {
    at += cell.colSpan;
    if (col < at) return cell.textContent.trim();
  }
  return "";
}

// A route map vehicle's popup parts from the timetables its trip runs in
// (config is the route's live.json): its headsign, the name of its stop, and
// a link that shows its column
function vehiclePlace(map, card, config, v) {
  const tables = card.querySelectorAll("table.timetable");
  const i = config?.tables.findIndex((data) => data.trips.includes(v.tripId)) ?? -1;
  const table = tables[i];
  if (!table?.tBodies[0]) return {};
  const data = config.tables[i];
  const col = data.trips.indexOf(v.tripId);
  const rows = [...table.tBodies[0].rows];
  const row = v.stopId ? data.stops.indexOf(config.parents[v.stopId] ?? v.stopId) : -1;
  const headsign = columnHeadsign(table, col);
  let head = null;
  if (headsign) {
    head = document.createElement("div");
    head.textContent = t("map.to", { headsign });
  }
  const link = popupLink(map, t("map.in_timetable"), () => {
    const cells = rows.map((r) => r.cells[col + 1]);
    (cells[row] ?? cells[0])?.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    flash(cells.filter(Boolean));
  });
  return { head, stop: row >= 0 && rows[row]?.cells[0] ? rowStop(rows[row].cells[0]) : null, link };
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
      const route = { color: data.color };
      let config = null;
      liveConfig(card).then((c) => (config = c), () => {});
      vehicles = live(
        L,
        map,
        holder,
        data.rt.url,
        (v) => (routes.has(v.routeId) || trips.has(v.tripId) ? route : null),
        (v) => vehiclePlace(map, card, config, v),
      );
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

// A system map vehicle's popup parts: its route's badge and title, and a link
// that opens the route
function vehicleRoute(map, route) {
  const head = document.createElement("div");
  head.className = "map-popup-route";
  const title = document.createElement("span");
  title.textContent = route.title;
  head.append(badge(route), title);
  return { head, link: popupLink(map, t("map.open_timetable"), () => openRoute(route.slug)) };
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
  let vehicles;
  const showModes = () => {
    if (!map.getLayer("sys-lines")) return;
    const filter = modeFilter(enabled);
    for (const id of filtered) map.setFilter(id, filter);
    map.setFilter("sys-hover", ["==", ["get", "rs"], ""]);
    vehicles?.refresh();
  };

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
      vehicles = live(
        L,
        map,
        el,
        data.rt,
        (v) => {
          const i = byId.get(v.routeId);
          return i == null || !enabled.has(data.routes[i].mode) ? null : data.routes[i];
        },
        (v, route) => vehicleRoute(map, route),
      );
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
    map.getCanvas().style.cursor = ids.length || hits(map, e.point, ["vehicles-hit"], 4).length ? "pointer" : "";
    if (map.getLayer("sys-hover")) map.setFilter("sys-hover", ids.length ? ["in", `,${ids[0]},`, ["get", "rs"]] : ["==", ["get", "rs"], ""]);
    if (cap) {
      cap.hidden = !ids.length;
      if (ids.length) cap.textContent = data.routes[ids[0]].title;
    }
  });
  map.on("mouseout", () => cap && (cap.hidden = true));
  onClick(L, map, RANK_LINE, (e) => {
    const ids = routesAt(e.point, 8);
    if (!ids.length) return null;
    if (ids.length === 1) {
      openRoute(data.routes[ids[0]].slug);
      return true;
    }
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
    ul.addEventListener("click", () => closePopup(map));
    return { at: e.lngLat, content: ul, options: { closeButton: false, maxWidth: "16rem" } };
  });
}

/* Live vehicles */

let rtLib;
const rt = () => (rtLib ??= import(RT_LIB).then((m) => (m.default ?? m).transit_realtime));

// Protobuf longs as numbers
function num(v) {
  if (v == null) return null;
  return typeof v === "number" ? v : typeof v.toNumber === "function" ? v.toNumber() : Number(v);
}

async function fetchFeed(url) {
  const { FeedMessage } = await rt();
  const res = await fetch(viaProxy(url), { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return FeedMessage.decode(new Uint8Array(await res.arrayBuffer()));
}

// Calls fn every POLL_MS while el is on screen and the tab is visible
function pollWhileShown(el, fn) {
  let visible = false;
  let timer;
  let running = false;
  async function tick() {
    clearTimeout(timer);
    if (!visible || document.hidden || running) return;
    running = true;
    try {
      await fn();
    } catch (err) {
      console.warn("realtime", err);
    }
    running = false;
    timer = setTimeout(tick, POLL_MS);
  }
  new IntersectionObserver((entries) => {
    visible = entries.some((e) => e.isIntersecting);
    tick();
  }).observe(el);
  document.addEventListener("visibilitychange", tick);
}

// Status lines say how long ago their feed was updated, rewritten every second
const statuses = new Set();
const ago = new Intl.RelativeTimeFormat(LANG, { numeric: "auto" });
function agoText(seconds) {
  if (seconds < 60) return ago.format(-Math.max(0, Math.round(seconds)), "second");
  if (seconds < 3600) return ago.format(-Math.floor(seconds / 60), "minute");
  return ago.format(-Math.floor(seconds / 3600), "hour");
}
function showStatus(el) {
  const live = t("map.live", { n: +el.dataset.n });
  el.textContent = t("map.updated", { live, ago: agoText(Date.now() / 1000 - +el.dataset.ts) });
}
// Vehicle popups' "Position ... ago" lines, by their data-ts
const showAgo = (el) => (el.textContent = t("map.position_ago", { ago: agoText(Date.now() / 1000 - +el.dataset.ts) }));
setInterval(() => {
  for (const el of statuses) if (!el.hidden) showStatus(el);
  for (const el of document.querySelectorAll(".vehicle-ago")) showAgo(el);
}, 1000);

// Bearing in degrees from a to b, both [lon, lat]
function bearingOf(a, b) {
  const rad = Math.PI / 180;
  const [x1, y1, x2, y2] = [a[0] * rad, a[1] * rad, b[0] * rad, b[1] * rad];
  const y = Math.sin(x2 - x1) * Math.cos(y2);
  const x = Math.cos(y1) * Math.sin(y2) - Math.sin(y1) * Math.cos(y2) * Math.cos(x2 - x1);
  return ((Math.atan2(y, x) / rad) + 360) % 360;
}
// Metres between two [lon, lat], flat-earth, fine for a poll's movement
function metres(a, b) {
  const k = Math.cos((a[1] * Math.PI) / 180);
  return Math.hypot((b[0] - a[0]) * k, b[1] - a[1]) * 111_320;
}
// Movement below this between polls gives no bearing
const MIN_MOVE_M = 15;

// Vehicle layers as rt-viewer draws them: a route-colored dot on a dark
// casing, or a route-colored arrow with a dark halo for vehicles with a bearing. Sizes grow with
// zoom from a floor of about 8px, which stays visible zoomed out over whole
// systems, to rt-viewer's size close in
const VEHICLE_CASING = "#0f172a";
const VEHICLE_LAYERS = ["vehicles-halo", "vehicles-casing", "vehicles-dot", "vehicles-arrow", "vehicles-hit"];
const byZoom = (...stops) => ["interpolate", ["linear"], ["zoom"], ...stops];
const FOCUSED = ["boolean", ["feature-state", "focused"], false];

// A north-pointing arrow as an SDF, so icon-color tints it; blurred so the
// edge has a ramp for the distance field
function addArrowImage(map) {
  if (map.hasImage("vehicle-arrow")) return;
  const size = 48;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const g = canvas.getContext("2d");
  g.filter = "blur(2px)";
  g.fillStyle = "#000";
  g.beginPath();
  g.moveTo(size / 2, 6);
  g.lineTo(size - 10, size - 8);
  g.lineTo(size / 2, size - 16);
  g.lineTo(10, size - 8);
  g.closePath();
  g.fill();
  map.addImage("vehicle-arrow", g.getImageData(0, 0, size, size), { sdf: true });
}

function addVehicleLayers(map, data) {
  addArrowImage(map);
  map.addSource("vehicles", { type: "geojson", data, promoteId: "id" });
  const noBearing = ["==", ["get", "has_bearing"], false];
  map.addLayer({
    id: "vehicles-halo",
    type: "circle",
    source: "vehicles",
    paint: {
      "circle-radius": byZoom(10, ["case", FOCUSED, 9, 0], 12, ["case", FOCUSED, 10, 0], 15, ["case", FOCUSED, 13, 0], 18, ["case", FOCUSED, 20, 0]),
      "circle-color": ink(),
      "circle-opacity": 0.25,
      "circle-stroke-color": ink(),
      "circle-stroke-width": ["case", FOCUSED, 2, 0],
    },
  });
  map.addLayer({
    id: "vehicles-casing",
    type: "circle",
    source: "vehicles",
    filter: noBearing,
    paint: { "circle-radius": byZoom(10, 4, 12, 4.5, 15, 7, 18, 12), "circle-color": VEHICLE_CASING },
  });
  map.addLayer({
    id: "vehicles-dot",
    type: "circle",
    source: "vehicles",
    filter: noBearing,
    paint: {
      "circle-radius": byZoom(10, 3, 12, 3.5, 15, 5.5, 18, 10),
      "circle-color": ["get", "color"],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": byZoom(10, ["case", FOCUSED, 2, 1], 15, ["case", FOCUSED, 3, 1.5]),
    },
  });
  map.addLayer({
    id: "vehicles-arrow",
    type: "symbol",
    source: "vehicles",
    filter: ["==", ["get", "has_bearing"], true],
    layout: {
      "icon-image": "vehicle-arrow",
      "icon-size": byZoom(10, 0.3, 12, 0.36, 15, 0.6, 18, 1),
      "icon-rotate": ["get", "bearing"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
    paint: {
      "icon-color": ["get", "color"],
      "icon-halo-color": VEHICLE_CASING,
      "icon-halo-width": byZoom(10, ["case", FOCUSED, 2, 1.25], 15, ["case", FOCUSED, 3, 2]),
    },
  });
  // The only layer clicks and hovers query, never smaller than a drawn vehicle
  map.addLayer({
    id: "vehicles-hit",
    type: "circle",
    source: "vehicles",
    paint: { "circle-radius": byZoom(10, 10, 15, 14, 18, 20), "circle-color": "transparent", "circle-opacity": 0 },
  });
}

// Where a vehicle is relative to its stop, from currentStatus
const STATUS_TEXT = ["map.arriving_at", "map.stopped_at", "map.next_stop"];

// Polls url every POLL_MS while el is on screen and the tab is visible.
// routeOf(v) gives a vehicle's route ({ color, ... }), or null to leave it off
// this map; describe(v, route) gives { head, stop, link } for its popup: a
// node above the label, the name of v.stopId and a node below. Returns draw,
// which puts the vehicle layers on a new basemap style, and refresh, which
// rebuilds them after routeOf changes
function live(L, map, el, url, routeOf, describe) {
  // Vehicles from the last poll: { id, label, routeId, tripId, stopId, status, at, bearing, ts }
  let list = [];
  // Last position and bearing per vehicle, for feeds that send no bearing
  const last = new Map();
  // The vehicle whose popup is open
  let focused = null;
  let popup = null;

  const features = () => {
    const out = [];
    for (const v of list) {
      const route = routeOf(v);
      if (!route) continue;
      const properties = { id: v.id, color: lineColor(route.color), has_bearing: v.bearing != null };
      if (v.bearing != null) properties.bearing = v.bearing;
      out.push({ type: "Feature", properties, geometry: { type: "Point", coordinates: v.at } });
    }
    return { type: "FeatureCollection", features: out };
  };
  const setFocus = (id) => {
    if (focused != null && map.getSource("vehicles")) map.setFeatureState({ source: "vehicles", id: focused }, { focused: false });
    focused = id;
    if (focused != null && map.getSource("vehicles")) map.setFeatureState({ source: "vehicles", id: focused }, { focused: true });
  };
  const draw = () => {
    addVehicleLayers(map, features());
    setFocus(focused);
  };
  const refresh = () => {
    map.getSource("vehicles")?.setData(features());
    if (focused != null && !list.some((v) => v.id === focused && routeOf(v))) popup?.remove();
  };

  const content = (v) => {
    const route = routeOf(v);
    const { head, stop, link } = describe(v, route);
    const root = document.createElement("div");
    root.className = "map-popup-vehicle";
    if (head) root.append(head);
    const label = document.createElement("strong");
    label.textContent = t("map.vehicle", { label: v.label });
    root.append(label);
    if (stop) {
      const line = document.createElement("div");
      line.textContent = t(STATUS_TEXT[v.status] ?? STATUS_TEXT[2], { stop });
      root.append(line);
    }
    if (v.ts) {
      const line = document.createElement("div");
      line.className = "vehicle-ago";
      line.dataset.ts = v.ts;
      showAgo(line);
      root.append(line);
    }
    if (link) root.append(link);
    return root;
  };

  draw();
  onClick(L, map, RANK_VEHICLE, (e) => {
    const [found] = hits(map, e.point, ["vehicles-hit"], 4);
    const v = found && list.find((x) => x.id === found.properties.id);
    if (!v) return null;
    return {
      at: v.at,
      content: content(v),
      opened: (p) => {
        popup = p;
        setFocus(v.id);
        p.on("close", () => {
          if (popup !== p) return;
          popup = null;
          setFocus(null);
        });
      },
    };
  });
  map.on("mouseenter", "vehicles-hit", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "vehicles-hit", () => (map.getCanvas().style.cursor = ""));

  const status = el.querySelector(".map-live");
  if (status) statuses.add(status);
  pollWhileShown(el, async () => {
    let feed;
    try {
      feed = await fetchFeed(url);
    } catch (err) {
      if (status) status.hidden = true;
      throw err;
    }
    const feedTs = num(feed.header?.timestamp) || Date.now() / 1000;
    list = [];
    for (const entity of feed.entity ?? []) {
      const v = entity.vehicle;
      const pos = v?.position;
      if (!pos) continue;
      const id = v.vehicle?.id || entity.id;
      const at = [pos.longitude, pos.latitude];
      const seen = last.get(id);
      let bearing = Object.hasOwn(pos, "bearing") ? pos.bearing : null;
      if (bearing == null && seen) bearing = metres(seen.at, at) >= MIN_MOVE_M ? bearingOf(seen.at, at) : seen.bearing;
      if (!seen || metres(seen.at, at) >= MIN_MOVE_M) last.set(id, { at, bearing });
      list.push({
        id,
        label: v.vehicle?.label || id,
        routeId: v.trip?.routeId,
        tripId: v.trip?.tripId,
        stopId: v.stopId || null,
        // Unset means IN_TRANSIT_TO, as the spec defaults it
        status: Object.hasOwn(v, "currentStatus") ? v.currentStatus : 2,
        at,
        bearing,
        ts: num(v.timestamp) || feedTs,
      });
    }
    const data = features();
    map.getSource("vehicles")?.setData(data);
    // The open popup follows its vehicle, and closes once it leaves the map
    const v = focused != null && list.find((x) => x.id === focused && routeOf(x));
    if (v && popup) popup.setLngLat(v.at).setDOMContent(content(v));
    else popup?.remove();
    if (status) {
      status.dataset.n = data.features.length;
      status.dataset.ts = feedTs;
      status.hidden = false;
      showStatus(status);
    }
  });
  return { draw, refresh };
}

/* Live times in the timetable */

const dayClocks = new Map();
// Seconds after local midnight in tz for an epoch in seconds
function secondsOfDay(epoch, tz) {
  let f = dayClocks.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { timeZone: tz || undefined, hourCycle: "h23", hour: "numeric", minute: "numeric", second: "numeric" });
    dayClocks.set(tz, f);
  }
  const parts = Object.fromEntries(f.formatToParts(new Date(epoch * 1000)).map((p) => [p.type, p.value]));
  return +parts.hour * 3600 + +parts.minute * 60 + +parts.second;
}
// sec moved by whole days to within half a day of ref, for times past midnight
function nearDay(sec, ref) {
  return sec + Math.round((ref - sec) / 86400) * 86400;
}
// A clock time as the tables show it in prose, like pages.clock_label
function clockText(sec, fmt) {
  const minutes = Math.floor((((sec % 86400) + 86400) % 86400) / 60);
  const [h, m] = [Math.floor(minutes / 60), String(minutes % 60).padStart(2, "0")];
  if (fmt === "24h") return `${String(h).padStart(2, "0")}:${m}`;
  return `${((h + 11) % 12) + 1}:${m} ${h >= 12 ? "PM" : "AM"}`;
}

// Seconds from pages.minute_steps: the first minute, then minutes since the previous
function fromSteps(steps) {
  let minute = null;
  return steps.map((step) => {
    if (step == null) return null;
    minute = minute == null ? step : minute + step;
    return minute * 60;
  });
}

// The route body's live.json, fetched once and shared by its map and tables;
// null without one. A failed fetch is retried on the next call
const liveConfigs = new WeakMap();
function liveConfig(body) {
  const el = body.querySelector(".rt-data");
  if (!el) return Promise.resolve(null);
  let config = liveConfigs.get(body);
  if (!config) {
    config = fetch(el.dataset.src).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    });
    config.catch(() => liveConfigs.delete(body));
    liveConfigs.set(body, config);
  }
  return config;
}

// Polls the trip updates feed for the route body's tables: running trips'
// columns marked, predicted times under the scheduled ones, canceled trips
// struck. The route's live.json is fetched once the body is first on screen
function initLiveTimes(body) {
  let config;
  // trip id -> [{ table, col, stops, sched }]
  const byTrip = new Map();
  async function load() {
    config = await liveConfig(body);
    // Tables in page order, as build.py lists them
    const tables = body.querySelectorAll("table.timetable");
    config.tables.forEach((data, i) => {
      const table = tables[i];
      if (!table?.tBodies[0]) return;
      data.trips.forEach((trip, col) => {
        if (!trip) return;
        if (!byTrip.has(trip)) byTrip.set(trip, []);
        byTrip.get(trip).push({ table, col, stops: data.stops, sched: fromSteps(data.sched[col]) });
      });
    });
  }
  const cellAt = (table, row, col) => table.tBodies[0].rows[row]?.cells[col + 1];
  const marked = new Set();
  const clear = () => {
    for (const el of marked) {
      el.classList.remove("live", "canceled");
      el.removeAttribute("title");
      el.querySelector(".rt")?.remove();
    }
    marked.clear();
  };
  const mark = (el, cls) => {
    el.classList.add(cls);
    marked.add(el);
  };

  pollWhileShown(body, async () => {
    if (!config) await load();
    if (!byTrip.size) return;
    const { TripDescriptor, TripUpdate } = await rt();
    const feed = await fetchFeed(config.url);
    const SKIPPED = TripUpdate.StopTimeUpdate.ScheduleRelationship.SKIPPED;
    const NO_DATA = TripUpdate.StopTimeUpdate.ScheduleRelationship.NO_DATA;
    const now = secondsOfDay(Date.now() / 1000, config.tz);
    clear();
    for (const entity of feed.entity ?? []) {
      const tu = entity.tripUpdate;
      const places = byTrip.get(tu?.trip?.tripId);
      if (!places) continue;
      // By the stop ids the rows use: platforms under their station
      const updates = new Map((tu.stopTimeUpdate ?? []).filter((u) => u.stopId).map((u) => [config.parents[u.stopId] ?? u.stopId, u]));
      for (const { table, col, stops, sched } of places) {
        const cells = sched.map((_, row) => cellAt(table, row, col));
        if (tu.trip.scheduleRelationship === TripDescriptor.ScheduleRelationship.CANCELED) {
          for (const el of cells) {
            if (!el) continue;
            mark(el, "canceled");
            el.title = t("rt.canceled");
          }
          continue;
        }
        // The last known delay carries forward to later stops
        let delay = null;
        const shown = [];
        sched.forEach((time, row) => {
          if (time == null) return;
          const u = updates.get(stops[row]);
          let skipped = false;
          if (u) {
            if (u.scheduleRelationship === SKIPPED) skipped = true;
            else if (u.scheduleRelationship === NO_DATA) delay = null;
            else {
              const event = u.departure ?? u.arrival;
              if (event && Object.hasOwn(event, "delay")) delay = event.delay;
              else if (event && Object.hasOwn(event, "time")) delay = nearDay(secondsOfDay(num(event.time), config.tz), time) - time;
            }
          }
          shown.push({ row, time, skipped, delay });
        });
        const ref = (time) => nearDay(now, time);
        const started = shown.some((p) => p.delay != null && p.time + p.delay <= ref(p.time));
        const upcoming = shown.filter((p) => p.skipped || (p.delay != null && p.time + p.delay >= ref(p.time) - 60));
        if (started && upcoming.length) for (const el of cells) if (el) mark(el, "live");
        for (const p of upcoming) {
          const el = cells[p.row];
          if (!el) continue;
          const tag = document.createElement("small");
          tag.className = "rt";
          if (p.skipped) {
            tag.textContent = t("rt.skipped");
            tag.dataset.state = "late";
          } else {
            const late = Math.round(p.delay / 60);
            tag.textContent = late === 0 ? t("rt.on_time") : `${clockText(p.time + p.delay, config.fmt)} (${late > 0 ? "+" : ""}${late})`;
            tag.dataset.state = late > 1 ? "late" : "ontime";
          }
          el.append(tag);
          marked.add(el);
        }
      }
    }
  });
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
    for (const el of content.querySelectorAll(".rt-data")) el.dataset.src = new URL(el.dataset.src, base).href;
    // Above the route-page button, which stays last
    const open = body.querySelector(".route-open");
    if (open) open.before(...content.childNodes);
    else body.append(...content.childNodes);
    if (body.querySelector(".route-data")) initRouteMap(body);
    if (body.querySelector(".rt-data")) initLiveTimes(body);
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
  if (card.querySelector(".rt-data")) initLiveTimes(card);
}
const system = document.querySelector(".system");
if (system) whenNear(system, () => initSystemMap(system));
