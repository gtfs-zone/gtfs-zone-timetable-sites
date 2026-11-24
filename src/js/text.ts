// Page strings from the #root-text JSON the build writes, in the page's language.

type Forms = Partial<Record<Intl.LDMLPluralRule, string>>;

const TEXT: Record<string, string | Forms> = JSON.parse(
  document.getElementById('root-text')?.textContent ?? '{}'
);
export const LANG = document.documentElement.lang;
const PLURALS = new Intl.PluralRules(LANG);

/**
 * Text for `key` with `{name}` filled from vars, numbers in the page's
 * language; a plural key's form is picked by vars.n.
 */
export function t(key: string, vars: Record<string, string | number> = {}): string {
  let text = TEXT[key] ?? key;
  if (typeof text === 'object') {
    text = text[PLURALS.select(Number(vars.n))] ?? text.other ?? key;
  }
  return text.replace(/\{(\w+)\}/g, (_, k: string) => {
    const value = vars[k] ?? '';
    return typeof value === 'number' ? value.toLocaleString(LANG) : value;
  });
}

/** Runs fn once el comes within 300px of the viewport. */
export function whenNear(el: Element, fn: () => void): void {
  const seen = new IntersectionObserver(
    (entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      seen.disconnect();
      fn();
    },
    { rootMargin: '300px' }
  );
  seen.observe(el);
}
