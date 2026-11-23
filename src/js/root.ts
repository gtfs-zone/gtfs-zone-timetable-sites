// The root and country pages' script, bundled into templates/root.js by
// `pnpm run build:js`.

import { initChoropleths } from './choropleth';
import { initSearch } from './search';

initChoropleths();
initSearch();
