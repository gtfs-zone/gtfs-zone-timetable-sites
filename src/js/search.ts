// Search over every catalog feed (root page) and the filter of a country
// page's listed sites.

import UFuzzy from '@leeoniya/ufuzzy';
import {
  type CatalogFeed,
  type FeedState,
  parseSearchDocument,
  realtimeRoles,
} from 'gtfs-zone-web-common/gtfs/feed-catalog';
import { FeedMatcher } from 'gtfs-zone-web-common/gtfs/feed-search';
import { LANG, t } from './text';

// Results shown at first, and added by each "Show more"
const PAGE = 50;
const STATES: FeedState[] = ['up', 'partial', 'down', 'unknown'];
const STATE_BADGE: Record<FeedState, string> = {
  up: 'badge-success',
  partial: 'badge-warning',
  down: 'badge-error',
  unknown: 'badge-ghost',
};
const ROLE_SHORT: Record<string, string> = {
  vehicles: 'VP',
  trip_updates: 'TU',
  alerts: 'SA',
  realtime: 'RT',
};
const DATE = new Intl.DateTimeFormat(LANG, { dateStyle: 'medium', timeZone: 'UTC' });

interface Filters {
  q: string;
  /** States shown; empty shows every state. */
  status: FeedState[];
  /** Only feeds with a realtime role. */
  rt: boolean;
  /** Only feeds with no country. */
  np: boolean;
}

interface Catalog {
  /** The bucket's search.json: each feed's `feedId` is its slug, "" with none. */
  feeds: CatalogFeed[];
  matcher: FeedMatcher;
  /** Per feed, its page sets' languages other than the default. */
  langs: string[][];
  /** Feed indexes by schedule last modified, newest first. */
  recent: number[];
}

function readFilters(): Filters {
  const params = new URLSearchParams(location.hash.slice(1));
  const status = (params.get('status') ?? '').split(',');
  return {
    q: params.get('q') ?? '',
    status: STATES.filter((s) => status.includes(s)),
    rt: params.get('rt') === '1',
    np: params.get('np') === '1',
  };
}

function writeFilters(f: Filters): void {
  const params = new URLSearchParams();
  if (f.q) params.set('q', f.q);
  if (f.status.length) params.set('status', f.status.join(','));
  if (f.rt) params.set('rt', '1');
  if (f.np) params.set('np', '1');
  const hash = params.toString();
  history.replaceState(null, '', hash ? `#${hash}` : location.pathname + location.search);
}

const active = (f: Filters): boolean => Boolean(f.q.trim() || f.status.length || f.rt || f.np);

async function loadCatalog(el: HTMLElement): Promise<Catalog> {
  const response = await fetch(el.dataset.src!);
  if (!response.ok) throw new Error(`${el.dataset.src}: HTTP ${response.status}`);
  const doc = (await response.json()) as { feeds?: { lg?: string[] }[] };
  const feeds = parseSearchDocument(doc);
  const langs = (doc.feeds ?? []).map((f) => f.lg ?? []);
  const recent = feeds
    .map((_, i) => i)
    .sort((a, b) => {
      const ma = feeds[a].lastModified ?? '';
      const mb = feeds[b].lastModified ?? '';
      return ma === mb ? feeds[a].name.localeCompare(feeds[b].name) : mb.localeCompare(ma);
    });
  return { feeds, matcher: new FeedMatcher(feeds), langs, recent };
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className: string,
  text?: string
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

// A feed's row: its page in the page's language when it has that page set,
// else its default set; unlinked when it has no page
function resultItem(index: number, catalog: Catalog, root: string): HTMLLIElement {
  const li = document.createElement('li');
  const feed = catalog.feeds[index];
  const slug = feed.feedId;
  const row = el(slug ? 'a' : 'div', 'flex min-h-11 items-center gap-3 rounded-box px-4 py-2');
  if (slug && row instanceof HTMLAnchorElement) {
    const lang = catalog.langs[index].includes(LANG) ? `${LANG.toLowerCase()}/` : '';
    row.href = `${root}${slug}/${lang}`;
    row.classList.add('hover:bg-base-200', 'focus-visible:bg-base-200');
  }
  const text = el('span', 'flex min-w-0 flex-1 flex-col');
  const name = el('span', 'font-semibold', feed.name);
  if (feed.subtitle) name.append(el('span', 'font-normal', ` - ${feed.subtitle}`));
  const line = [feed.place.join(', ')];
  if (feed.lastModified) {
    line.push(t('root.modified', { date: DATE.format(new Date(`${feed.lastModified}T00:00:00Z`)) }));
  }
  text.append(name, el('span', 'break-words text-sm text-base-content/70', line.filter(Boolean).join(' - ')));
  const chips = el('span', 'flex shrink-0 flex-wrap items-center justify-end gap-1');
  for (const role of realtimeRoles(feed)) {
    const state = feed.roleState[role] ?? 'unknown';
    const chip = el('span', `badge badge-sm font-mono ${STATE_BADGE[state]}`, ROLE_SHORT[role]);
    const [chipKey, roleKey] = [`role.chip_${state}`, `role.${role}`];
    chip.title = t(chipKey, { role: t(roleKey) });
    chips.append(chip);
  }
  const stateKey = `state.${feed.state}`;
  chips.append(el('span', `badge badge-sm ${STATE_BADGE[feed.state]}`, t(stateKey)));
  row.append(text, chips);
  li.append(row);
  return li;
}

function initCatalogSearch(box: HTMLElement): void {
  const input = box.querySelector<HTMLInputElement>('input[type=search]')!;
  const results = box.querySelector<HTMLUListElement>('.search-results')!;
  const status = box.querySelector<HTMLElement>('.search-status')!;
  const more = box.querySelector<HTMLButtonElement>('.search-more')!;
  const stateBoxes = [...box.querySelectorAll<HTMLInputElement>('input[name=status]')];
  const rtBox = box.querySelector<HTMLInputElement>('input[name=rt]')!;
  const npBox = box.querySelector<HTMLInputElement>('input[name=np]')!;
  const root = box.dataset.root ?? '';
  let catalog: Promise<Catalog> | undefined;
  let loaded: Catalog | undefined;
  let shown = PAGE;
  // Indexes into the catalog's feeds
  let found: number[] = [];

  const filters = (): Filters => ({
    q: input.value,
    status: stateBoxes.filter((b) => b.checked).map((b) => b.value as FeedState),
    rt: rtBox.checked,
    np: npBox.checked,
  });

  // The filter labels with their counts over the whole catalog
  const label = (b: HTMLInputElement, n: number) => {
    const text = `${b.dataset.label} (${n.toLocaleString(LANG)})`;
    b.setAttribute('aria-label', text);
    if (b.parentElement instanceof HTMLLabelElement) {
      b.parentElement.querySelector('span')!.textContent = text;
    }
  };
  const load = () => {
    catalog ??= loadCatalog(box).then((c) => {
      for (const b of stateBoxes) label(b, c.feeds.filter((f) => f.state === b.value).length);
      label(rtBox, c.feeds.filter((f) => realtimeRoles(f).length).length);
      label(npBox, c.feeds.filter((f) => !f.countryCode).length);
      loaded = c;
      return c;
    });
    return catalog;
  };

  const draw = () => {
    results.replaceChildren(
      ...(loaded ? found.slice(0, shown).map((i) => resultItem(i, loaded!, root)) : [])
    );
    results.hidden = !found.length;
    more.hidden = found.length <= shown;
  };

  const update = async () => {
    const f = filters();
    writeFilters(f);
    shown = PAGE;
    if (!active(f)) {
      found = [];
      status.textContent = '';
      draw();
      return;
    }
    status.textContent = t('root.loading');
    let c: Catalog;
    try {
      c = await load();
    } catch {
      catalog = undefined;
      status.textContent = t('root.load_error');
      return;
    }
    // Filters changed while loading
    if (JSON.stringify(filters()) !== JSON.stringify(f)) return;
    const order = c.matcher.match(f.q) ?? c.recent;
    const states = new Set(f.status);
    found = order.filter((i) => {
      const feed = c.feeds[i];
      return (
        (!states.size || states.has(feed.state)) &&
        (!f.rt || realtimeRoles(feed).length > 0) &&
        (!f.np || !feed.countryCode)
      );
    });
    status.textContent = found.length ? t('root.feeds', { n: found.length }) : t('root.no_feeds');
    draw();
  };

  const start = readFilters();
  input.value = start.q;
  for (const b of stateBoxes) b.checked = start.status.includes(b.value as FeedState);
  rtBox.checked = start.rt;
  npBox.checked = start.np;

  input.addEventListener('focus', () => void load().catch(() => undefined), { once: true });
  input.addEventListener('input', () => void update());
  for (const b of [...stateBoxes, rtBox, npBox]) b.addEventListener('change', () => void update());
  more.addEventListener('click', () => {
    shown += PAGE;
    draw();
  });
  if (active(start)) void update();
}

/* Filter of the listed sites, on a country page */

const uf = new UFuzzy({ intraIns: 1 });
// Above this many matches uFuzzy skips ranking, as in gtfs-zone-web-common
const RANK_THRESHOLD = 1000;

function initListFilter(input: HTMLInputElement): void {
  const sections = [...document.querySelectorAll<HTMLElement>('.site-section')];
  const items = sections.flatMap((s) => [...s.querySelectorAll('li')]);
  const haystack = items.map(
    (li) =>
      `${(li.textContent ?? '').replace(/\s+/g, ' ')} ${li.closest<HTMLElement>('.site-section')?.dataset.name ?? ''}`
  );
  const status = document.getElementById(input.dataset.status!)!;
  const update = () => {
    const query = input.value.trim();
    let shown: Set<number> | null = null;
    if (query) {
      const [idxs, info, order] = uf.search(haystack, query, 1, RANK_THRESHOLD);
      shown = new Set(info && order ? order.map((o) => info.idx[o]) : (idxs ?? []));
    }
    items.forEach((li, i) => (li.hidden = shown != null && !shown.has(i)));
    for (const section of sections) {
      section.hidden = ![...section.querySelectorAll('li')].some((li) => !li.hidden);
    }
    status.textContent = shown
      ? shown.size
        ? t('root.agencies', { n: shown.size })
        : t('root.none_found')
      : '';
  };
  input.addEventListener('input', update);
  update();
}

export function initSearch(): void {
  const box = document.getElementById('catalog-search');
  if (box) initCatalogSearch(box);
  const filter = document.querySelector<HTMLInputElement>('#site-search');
  if (filter) initListFilter(filter);
}
