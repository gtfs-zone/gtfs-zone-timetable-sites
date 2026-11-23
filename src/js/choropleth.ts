// Choropleth maps of countries (root) or regions (country page). MapLibre
// loads from jsdelivr when a map first comes near the viewport.

import { LANG, t, whenNear } from './text';

const MAPLIBRE = 'https://cdn.jsdelivr.net/npm/maplibre-gl@6.16.0/dist/';

interface AreaProps {
  count: number;
  ref: string;
  key: string;
  name: string;
  [k: string]: unknown;
}
interface Area {
  id?: string | number;
  geometry: { type: 'Polygon' | 'MultiPolygon'; coordinates: unknown };
  properties: AreaProps;
}
interface Areas {
  type: 'FeatureCollection';
  features: Area[];
}
type Ring = [number, number][];

// The page's theme, set by base.html, or the system's
const prefersDark = matchMedia('(prefers-color-scheme: dark)');
const isDark = (): boolean => {
  const theme = document.documentElement.dataset.theme;
  return theme ? theme === 'dark' : prefersDark.matches;
};
// Fill colors from no sites up to the most, then sea and borders, per theme
const PALETTE = {
  light: {
    none: '#e5e7eb',
    ramp: ['#dbeafe', '#93c5fd', '#3b82f6', '#1d4ed8', '#1e3a8a'],
    sea: '#ffffff',
    border: '#ffffff',
    hover: '#111827',
  },
  dark: {
    none: '#2a323c',
    ramp: ['#1e3a8a', '#1d4ed8', '#3b82f6', '#93c5fd', '#dbeafe'],
    sea: '#1d232a',
    border: '#1d232a',
    hover: '#f9fafb',
  },
};
const palette = () => PALETTE[isDark() ? 'dark' : 'light'];

// MapLibre's module, typed loosely: it is not bundled.
type MapLibre = any;
let lib: Promise<MapLibre> | undefined;
function maplibre(): Promise<MapLibre> {
  lib ??= (async () => {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = `${MAPLIBRE}maplibre-gl.css`;
    document.head.append(css);
    return import(`${MAPLIBRE}maplibre-gl.mjs`);
  })();
  return lib;
}

const REGION_NAMES = new Intl.DisplayNames([LANG], { type: 'region', fallback: 'none' });
// A world.json country in the page's language, else as the file names it
function countryName({ key, name }: AreaProps): string {
  try {
    return REGION_NAMES.of(key.toUpperCase()) ?? name;
  } catch {
    return name;
  }
}

// Up to 5 class breaks from 1 to max, evenly spaced on a log scale
function breaks(max: number): number[] {
  const found = new Set([1]);
  for (let k = 1; k < 5; k++) found.add(Math.max(1, Math.round(max ** (k / 5))));
  return [...found].sort((a, b) => a - b);
}

function fillColor(max: number): unknown[] {
  const { none, ramp } = palette();
  const [first, ...rest] = breaks(max);
  // The ramp's top colors for the fewest classes, so few sites still read as dark
  const colors = ramp.slice(ramp.length - rest.length - 1);
  const step: unknown[] = ['step', ['get', 'count'], colors[0]];
  rest.forEach((b, i) => step.push(b, colors[i + 1]));
  return ['case', ['<', ['get', 'count'], first], none, step];
}

function areaStyle(data: Areas, max: number): object {
  const { sea, border, hover } = palette();
  return {
    version: 8,
    sources: { areas: { type: 'geojson', data, promoteId: 'ref' } },
    layers: [
      { id: 'sea', type: 'background', paint: { 'background-color': sea } },
      { id: 'fill', type: 'fill', source: 'areas', paint: { 'fill-color': fillColor(max) } },
      { id: 'border', type: 'line', source: 'areas', paint: { 'line-color': border, 'line-width': 0.6 } },
      {
        id: 'hover',
        type: 'line',
        source: 'areas',
        paint: {
          'line-color': hover,
          'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 2, 0],
        },
      },
    ],
  };
}

// The areas with sites, each by its largest polygon, so islands across the
// antimeridian (the Aleutians) don't stretch the view around the globe
function boundsOf(L: MapLibre, data: Areas): unknown {
  const b = new L.LngLatBounds();
  for (const f of data.features) {
    if (!f.properties.count) continue;
    const { type, coordinates } = f.geometry;
    const rings: Ring[] =
      type === 'Polygon'
        ? [(coordinates as Ring[])[0]]
        : (coordinates as Ring[][]).map((p) => p[0]);
    const span = (ring: Ring) => {
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
async function initChoropleth(el: HTMLElement): Promise<void> {
  const [L, data] = await Promise.all([
    maplibre(),
    fetch(el.dataset.src!).then((r) => r.json() as Promise<Areas>),
  ]);
  const key = el.dataset.key!;
  for (const f of data.features) f.properties.ref = String(f.properties[key]);
  const max = Math.max(1, ...data.features.map((f) => f.properties.count));
  const world = key === 'key';
  const map = new L.Map({
    container: el.querySelector('.area-map'),
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
  map.addControl(new L.NavigationControl({ showCompass: false }), 'top-right');
  document.addEventListener('themechange', () =>
    map.setStyle(areaStyle(data, max), { diff: false })
  );

  const cap = el.querySelector<HTMLElement>('.map-cap');
  let hovered: string | number | null = null;
  const setHover = (feature: Area | undefined) => {
    if (hovered != null) map.setFeatureState({ source: 'areas', id: hovered }, { hover: false });
    hovered = feature?.properties.count ? (feature.id ?? null) : null;
    if (hovered != null) map.setFeatureState({ source: 'areas', id: hovered }, { hover: true });
    map.getCanvas().style.cursor = hovered != null ? 'pointer' : '';
    if (!cap) return;
    cap.hidden = !feature;
    if (!feature) return;
    const { count } = feature.properties;
    const name = world ? countryName(feature.properties) : feature.properties.name;
    cap.textContent = count
      ? t('root.map_count', { name, agencies: t('root.agencies', { n: count }) })
      : name;
  };
  const at = (point: unknown): Area | undefined =>
    map.getLayer('fill') ? map.queryRenderedFeatures(point, { layers: ['fill'] })[0] : undefined;
  map.on('mousemove', (e: { point: unknown }) => setHover(at(e.point)));
  map.on('mouseout', () => setHover(undefined));
  map.on('click', (e: { point: unknown }) => {
    const feature = at(e.point);
    if (!feature?.properties.count) return;
    // The page's own link to the area, in the page's language, else the template
    const link = document.querySelector<HTMLAnchorElement>(
      `a[data-key="${CSS.escape(feature.properties.ref)}"]`
    );
    location.href =
      link?.href ?? el.dataset.href!.replace('{}', encodeURIComponent(feature.properties.ref));
  });
}

export function initChoropleths(): void {
  for (const el of document.querySelectorAll<HTMLElement>('.area')) {
    whenNear(el, () => void initChoropleth(el));
  }
}
