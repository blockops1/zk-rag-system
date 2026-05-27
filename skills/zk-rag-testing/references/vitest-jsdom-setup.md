# Vitest + jsdom Setup Notes (zk-rag-v2 website)

## Versions (as of 2026-05)
- **vitest**: 4.1.7 (auto-detects `*.test.js` without config)
- **jsdom**: installed separately (`npm install --save-dev jsdom`) — NOT bundled with vitest

## vitest.config.js (minimum working config)

```javascript
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',        // required for DOM APIs (document.createElement, etc.)
    globals: true,                // test globals: describe, it, expect, vi
    setupFiles: [],              // add setup files here if needed
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
    },
  },
});
```

**`globals: true`** makes `describe`, `it`, `expect`, `vi` available without imports in test files. Without it, either import from `vitest` or add `import { describe, it, expect, vi } from 'vitest'` to each file.

## Common jsdom patterns in tests

### Mocking localStorage (Node has no localStorage)

```javascript
import { beforeEach, vi } from 'vitest';

// In beforeEach:
beforeEach(() => {
  vi.stubGlobal('localStorage', {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  });
});
```

Mark tests that require real localStorage as `vi.skip` when `localStorage` is not available:

```javascript
it('saves search state', () => {
  if (!localStorage) return vi.skip('localStorage not available in Node');
  // ... test
});
```

### Mocking window.matchMedia

```javascript
beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(query => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});
```

### Fetch mocking

```javascript
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

mockFetch.mockResolvedValue({
  ok: true,
  status: 200,
  json: async () => ({ results: [...] }),
  headers: {
    get: vi.fn((k) => ({ 'content-type': 'application/json' }[k])),
  },
  text: async () => '',
});
```

### Testing DOM rendering

escapeHtml returns plain string — test directly:

```javascript
it('escapes double quotes', () => {
  expect(escapeHtml('say "hello"')).toBe('say &quot;hello&quot;');
});
```

buildDocGroupHtml returns HTML string — use jsdom to parse:

```javascript
const { JSDOM } = require('jsdom');
const dom = new JSDOM(buildDocGroupHtml({ docId: 'x" onclick="alert(1)', ... }));
const el = dom.window.document.querySelector('[data-doc-id]');
expect(el.dataset.docId).toBe('x&quot; onclick=&quot;alert(1)&quot;');
```

## npm scripts (website/package.json)

```json
{
  "scripts": {
    "test": "vitest",
    "test:watch": "vitest --watch",
    "lint": "biome check js/",
    "lint:fix": "biome check --write js/",
    "format": "biome format --write js/",
    "ci": "biome format --write js/ && biome check js/"
  }
}
```

## Biome version note

Biome is **v2.4.13** in this project. See `references/biome-2.4.13-config.md` for the working biome.json and all quirks.

## Real bugs caught by this test suite

1. **escapeHtml XSS**: original used DOM `textContent` trick — doesn't escape `"`. Rewritten with explicit char map.
2. **safeUrl() missing**: `javascript:` URLs pass through escapeHtml unblocked. Added safeUrl() helper rejecting non-http(s) schemes.
3. **API expectations wrong**: submitProof throws `resp.text()` on error (not `resp.json().detail.error`). fetchCollections returns `[]` on error.
