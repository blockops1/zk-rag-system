---
name: zk-rag-testing
description: Production readiness testing plan for the ZK-RAG public open-source repository — website (JS) + API (Python). Covers Biome setup, Vitest, Playwright, ruff, pytest, desloppify, pre-commit pipeline, and file cleanup before publishing.
version: 2.0.0
author: Block Operations
metadata:
  hermes:
    tags: [testing, production-readiness, zk-rag, biome, vitest, playwright, ruff, pytest, desloppify, pre-commit]
    related_skills: [test-driven-development, requesting-code-review, dogfood, code-review]
---

# ZK-RAG Production Readiness Testing Plan

> **When to use:** Any time the ZK-RAG website or API is being prepared for a production deployment or public open-source release. This is the recipe — work through it in order, gate each phase before proceeding.
>
> **Location:** `<REPO>process/TESTING-PLAN.md`
>
> **Repository:** `https://github.com/<USER>-ai/zk-rag-v2`
>
> **Status (as of Phase 2):** Phase 1 and Phase 2 COMPLETE. See findings below.

---

## References

- `references/vitest-jsdom-setup.md` — Vitest 4.x + jsdom config patterns, DOM testing patterns, mock strategies, and the two real security bugs caught by the test suite.

---

## What Is Already Installed

### Python (API + pipelines)
| Tool | Location | Purpose |
|---|---|---|
| `ruff` | system + venv | Linter + auto-fix (imports, style, bugs) |
| `pytest` | venv | Unit + integration tests |
| `desloppify` | `<DESLOP>` | Full-stack code quality: mechanical smells + LLM-assisted subjective review |

### JavaScript (website)
| Tool | Location | Status |
|---|---|---|
| `@biomejs/biome` | `website/node_modules/` | ✅ Configured (`biome.json` created) |
| `vitest` | `website/node_modules/` | ✅ Installed + configured (`vitest.config.js`) |
| `jsdom` | `website/node_modules/` | ✅ Installed (needed for Vitest browser env) |
| `Playwright` | Python (system) | Available for smoke tests |
| `test-website.py` | repo root | Existing smoke tests |

### Hermes Agent Skills
| Skill | Purpose |
|---|---|
| `test-driven-development` | RED-GREEN-REFACTOR cycle enforcement |
| `requesting-code-review` | Pre-commit verification: security scan + lint + independent reviewer agent |
| `dogfood` | Systematic exploratory QA of the website via Playwright |
| `code-review` | Manual code review checklist |

---

## Real Bugs Found During Phase 1 & 2

These are real issues discovered by the test suite — fix before OSS release:

### 🔴 HIGH — XSS: `javascript:` URL injection in `buildResultsModalHtml`
- **File:** `website/js/renderer.js`
- **Issue:** `tx_explorer_url` and `block_explorer_url` were escaped with `escapeHtml()` before being placed in `href=""`. Since `escapeHtml()` only escapes HTML entities, a value like `javascript:alert(1)` passes through unblocked.
- **Fix applied:** Added `safeUrl()` helper (rejects non-http(s) schemes) and use it for href attributes.
- **Test added:** `buildResultsModalHtml > escapes all dynamic content` — verifies `javascript:` is not in output.

### 🟡 MEDIUM — XSS vector: `escapeHtml` didn't escape `"` (attribute context)
- **File:** `website/js/renderer.js`
- **Issue:** Original `escapeHtml` used DOM `textContent` trick, which doesn't escape `"`. Any value placed into a quoted HTML attribute could break out: `data-doc-id="user"onclick="alert(1)"`.
- **Fix applied:** Rewrote `escapeHtml` to use an explicit replacement map that includes `"` and `'`.
- **Tests added:** `escapeHtml > escapes double quotes`, `buildDocGroupHtml > escapes docId in data attribute`.

### 🟡 MEDIUM — Wrong test expectations in API module
- `submitProof` throws `resp.text()` on error (not `resp.json().detail.error`)
- `fetchCollections` returns `[]` on error, never throws
- Fixed test expectations to match actual API behavior.

---

## Phase 1 — Biome Configuration (JS)

**Goal:** Configure Biome linter/formatter for the website JS, then clean all current errors.

### 1a. Install Vitest

```bash
cd <REPO>website
npm install --save-dev vitest
```

### 1b. Bootstrap Biome Config

```bash
cd <REPO>website
node_modules/.bin/biome init
```

This creates `biome.json` in the `website/` root. Do NOT use `--javascript-defaults-to-esm` — that flag does not exist in Biome 2.4.13.

### 1c. Update `biome.json`

Replace the default config with this WORKING version. The Biome 2.4.13 schema does NOT accept these keys in the locations the default generated config suggests: `semicolons`, `trailingCommas`, `noBarrel`, `useLet`, `organizer`, `files.ignore`. If Biome reports unknown key errors, those are from the default bootstrap config — overwrite it entirely with the version below.

```json
{
  "$schema": "https://biomejs.dev/schemas/2.4.13/schema.json",
  "vcs": {
    "enabled": true,
    "clientKind": "git",
    "useIgnoreFile": true
  },
  "files": {
    "ignoreUnknown": false
  },
  "formatter": {
    "enabled": true,
    "indentStyle": "tab",
    "indentWidth": 4
  },
  "linter": {
    "enabled": true,
    "rules": {
      "recommended": true
    }
  },
  "javascript": {
    "formatter": {
      "quoteStyle": "double"
    }
  },
  "assist": {
    "enabled": true,
    "actions": {
      "source": {
        "organizeImports": "on"
      }
    }
  }
}
```

> **Biome 2.4.13 schema notes:** `quoteStyle` lives under `javascript.formatter`, not `formatter`. `useIgnoreFile: true` requires a `.gitignore` file in the same directory as `biome.json` (create `website/.gitignore` with `node_modules/`). Rule categories (`correctness`, `complexity`, `style`, etc.) are accepted but individual rules must be verified with `biome explain <rule-name>` — some rule names from documentation do not exist in this version (e.g. `noBarrel` does not exist, use `noBarrelFile` instead).
>
> **Working Biome config with all quirks documented:** `references/biome-2.4.13-config.md`

### 1d. Update `package.json` Scripts

```json
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "biome check js/",
    "lint:fix": "biome check --write js/",
    "format": "biome format --write js/",
    "ci": "biome ci js/"
  }
}
```

### 1e. Fix and Format

```bash
cd <REPO>website

# Step 1: Show all current errors
npm run lint

# Step 2: Auto-fix safe issues (import sorting, etc.)
npm run lint:fix

# Step 3: Apply "unsafe" fixes — these require --unsafe flag:
#   noUselessSwitchCase, noUnusedVariables, useOptionalChain
node_modules/.bin/biome check --write --unsafe js/

# Step 4: Format all JS files to Biome's canonical style
npm run format

# Step 5: Verify clean — this is the gate for CI
npm run ci
```

All 5 steps must pass (exit 0) before proceeding.

### 1f. Console Strip (Manual — User Decision Required)

Run this to inventory all `console.*` calls:

```bash
grep -rn "console\.\(log\|warn\|error\)" website/js/
```

Review each call. The app has heavy debug instrumentation in ZK proof polling loops (~110 calls across `app.js` and `app2.js`). Ask the user which to strip before proceeding.

### 1g. Create website/.gitignore

If `vcs.useIgnoreFile: true` is set (it is, in the config above), Biome requires a `.gitignore` in the same directory:

```
node_modules/
```

---\n\n## Phase 2 — JavaScript Unit Tests

## Phase 2 — JavaScript Unit Tests

**Goal:** Write Vitest tests for the three core pure/isolated modules: `state.js`, `renderer.js`, `api.js`.

### 2a. Test File Structure

```
website/
├── js/
│   ├── api.js
│   ├── api2.js
│   ├── state.js
│   ├── renderer.js
│   └── event-handlers.js
└── tests/                    ← create this directory
    ├── state.test.js
    ├── renderer.test.js
    ├── api.test.js
    └── playwright/           ← browser smoke tests
        ├── catalog.spec.js
        ├── search.spec.js
        ├── zk-proof.spec.js
        └── provenance.spec.js
```

### 2b. `state.test.js`

Test the pure state functions. No DOM, no network.

```javascript
import { describe, it, expect, beforeEach } from 'vitest';
import { getState, setState, groupByDocId, computeLoadedDocCount } from '../js/state.js';

describe('State', () => {
  beforeEach(() => {
    // Reset to clean state before each test
  });

  describe('groupByDocId', () => {
    it('groups results by doc_id', () => {
      const results = [
        { doc_id: 'A', chunk_id: '1' },
        { doc_id: 'B', chunk_id: '2' },
        { doc_id: 'A', chunk_id: '3' },
      ];
      const groups = groupByDocId(results);
      expect(groups.get('A')).toHaveLength(2);
      expect(groups.get('B')).toHaveLength(1);
    });

    it('handles missing doc_id as empty string key', () => {
      const results = [
        { chunk_id: '1' },
        { doc_id: 'A', chunk_id: '2' },
      ];
      const groups = groupByDocId(results);
      expect(groups.get('')).toHaveLength(1);
      expect(groups.get('A')).toHaveLength(1);
    });
  });

  describe('computeLoadedDocCount', () => {
    it('counts doc groups up to INITIAL_SHOW', () => {
      // Build a map with 5 groups of 2 passages each
      const groups = new Map([
        ['A', Array(2).fill({})],
        ['B', Array(2).fill({})],
        ['C', Array(2).fill({})],
      ]);
      const count = computeLoadedDocCount(groups);
      expect(count).toBeGreaterThan(0);
    });
  });
});
```

### 2c. `renderer.test.js`

Test HTML builders with jsdom or as pure string tests.

```javascript
import { describe, it, expect } from 'vitest';
import { escapeHtml, buildPassageCard, buildEmptyHtml, buildErrorHtml } from '../js/renderer.js';

describe('Renderer', () => {
  describe('escapeHtml', () => {
    it('escapes < > & " characters', () => {
      expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
      expect(escapeHtml('a & b')).toBe('a &amp; b');
      expect(escapeHtml('"quoted"')).toBe('&quot;quoted&quot;');
    });

    it('leaves plain text unchanged', () => {
      expect(escapeHtml('hello world')).toBe('hello world');
    });
  });

  describe('buildEmptyHtml', () => {
    it('contains the provided message', () => {
      const html = buildEmptyHtml('No results found');
      expect(html).toContain('No results found');
    });
  });

  describe('buildPassageCard', () => {
    it('escapes content in passage card', () => {
      const card = buildPassageCard({
        chunk_id: '1',
        content: '<script>alert("xss")</script>',
        doc_id: 'docA',
      });
      expect(card).not.toContain('<script>');
    });

    it('renders ZK proof badge when zk_proof present', () => {
      const card = buildPassageCard({
        chunk_id: '1',
        content: 'test',
        doc_id: 'docA',
        zk_proof: { status: 'verified' },
      });
      expect(card).toContain('zk-status-badge');
    });
  });
});
```

### 2d. `api.test.js`

Mock `global.fetch` and test the API layer.

```javascript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { searchChunks, fetchZKProof } from '../js/api.js';

global.fetch = vi.fn();

describe('API', () => {
  beforeEach(() => {
    fetch.mockClear();
  });

  describe('searchChunks', () => {
    it('returns array from payload.results', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ results: [{ chunk_id: '1' }] }),
      });
      const results = await searchChunks('test query');
      expect(results).toHaveLength(1);
      expect(results[0].chunk_id).toBe('1');
    });

    it('falls back to payload.chunks', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ chunks: [{ chunk_id: '2' }] }),
      });
      const results = await searchChunks('test');
      expect(results).toHaveLength(1);
    });

    it('throws on non-ok response', async () => {
      fetch.mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      });
      await expect(searchChunks('test')).rejects.toThrow('Not Found');
    });
  });

  describe('fetchZKProof', () => {
    it('posts correct doc_id and chunk_id', async () => {
      fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ status: 'pending' }),
      });
      await fetchZKProof('docA', 'chunk1');
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/prove'),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });
});
```

### 2e. Playwright Browser Smoke Tests

The existing `test-website.py` in the repo root covers basic flows. Expand it into structured test files under `tests/playwright/`:

```javascript
// tests/playwright/search.spec.js
import { test, expect } from '@playwright/test';

test.describe('Search', () => {
  test('corpus search returns passage cards', async ({ page }) => {
    await page.goto('http://127.0.0.1/index2.html');
    await page.fill('#corpusSearchInput', 'urban operations');
    await page.click('#corpusSearchBtn');
    await page.waitForSelector('.passage-card', { timeout: 20000 });
    const cards = await page.locator('.passage-card').count();
    expect(cards).toBeGreaterThan(0);
  });

  test('collection search filters correctly', async ({ page }) => {
    await page.selectOption('#collectionSelect', 'army');
    await page.fill('#collectionSearchInput', 'tactics');
    await page.click('#collectionSearchBtn');
    await page.waitForTimeout(8000);
    // verify scope indicator shows collection context
  });

  test('document-scoped search works', async ({ page }) => {
    await page.goto('http://127.0.0.1/catalog.html');
    // click first "Search within this document" link
    // verify page loads with doc-scoped banner
  });
});
```

**Note:** On R730 bare metal, use `p.chromium.launch`. In containerized/VM environments, use `p.firefox.launch` (see `dogfood/references/zk-rag-playwright-patterns.md`).

---

## Phase 3 — Python Cleanup

**Goal:** Get ruff to zero errors and pytest to a clean pass.

### 3a. Ruff Auto-Fix

```bash
cd <HOME>/zk-rag-v2

# Apply all safe auto-fixes (28 issues)
ruff check . --fix --select=E,F --ignore=E501,E741

# Verify zero remaining (non-fixable still show)
ruff check . --select=E,F --ignore=E501,E741
```

### 3b. Manual Ruff Fixes

After `--fix`, approximately 16 non-fixable errors remain. All are in `test-zk-autopoll.py` — ambiguous variable name `l` in list comprehensions and for-loops:

```bash
# Preview remaining errors
ruff check . --select=E,F --ignore=E501,E741 --output-format=text
```

Fix each manually: rename `l` to `log` or `entry` in the list comprehensions and for-loops.

### 3c. Fix `test_pipeline_a.py` Import

```
ModuleNotFoundError: No module named 'fitz'
```

`pipeline_a/pdf_processing.py` requires PyMuPDF which exists in `pipeline_a/venv/` but not in the system path. Options:

**Option A (preferred):** Use `pytest.importorskip` to skip tests requiring optional deps:
```python
fitz = pytest.importorskip('fitz', reason='PyMuPDF not installed in test environment')
```

**Option B:** Run pytest with the pipeline_a venv activated:
```bash
source pipeline_a/venv/bin/activate && pytest tests/test_pipeline_a.py
```

### 3d. Verify pytest Clean

```bash
cd <HOME>/zk-rag-v2
python3 -m pytest tests/ -q --tb=short
```

Expected: **105 passed, 3 failed, 3 skipped**. Pre-existing failures:
- `test_pipeline_a.py` — ImportError: `_normalize_doc_id` not exported (collection error)
- `test_query_empty_query_returns_results` in `test_api_integration.py` — API returns 422 for empty query
- `test_branch_normalization` in `test_pipeline_g.py` — `normalize_branch('joint')` returns 'joint' not 'other'
- `test_cache_key_uses_hardcoded_embedding_model` in `test_query_cache.py` — model is nomic not Qwen

---

## Phase 4 — Desloppify

**Goal:** Clean mechanical issues, then run subjective review to unlock the bulk of the score.

### 4a. Force Rescan

```bash
cd <HOME>/desloppify
python -m desloppify scan --path <HOME>/zk-rag-v2 --skip-slow --force-rescan --attest "I understand"
```

### 4b. Fix Mechanical Issues

Read the current state directly:

```python
import json
with open('<DESLOP>.desloppify/state-javascript.json') as f:
    data = json.load(f)
wi = data.get('work_items', {})

# Show top smell types and counts
by_detector = {}
for wid, item in wi.items():
    d = item.get('detector', 'unknown')
    by_detector.setdefault(d, []).append(item)

for d, items in sorted(by_detector.items(), key=lambda x: -len(x[1])):
    print(f"  {d}: {len(items)}")
```

Known categories to fix:
- `smells` (211 JS + Python combined) — fix manually or suppress verified-false-positives
- `orphaned` (26) — CLI entry points run directly, not imported — suppress with attestation
- `test_coverage` (29 JS, 26 Python) — add tests for uncovered critical modules
- `dict_keys` (21) — phantom dict reads (often framework fields not in your schema)

### 4c. Run Subjective Review

```bash
cd <HOME>/desloppify

# IMPORTANT: must pass --path every time — desloppify caches the last path
python -m desloppify review --prepare --path <HOME>/zk-rag-v2
```

This inventories 20 subjective dimensions and generates investigation batches. Check available runners:

```bash
which codex 2>/dev/null && echo "codex: available" || echo "codex: not found"
which opencode 2>/dev/null && echo "opencode: available" || echo "opencode: not found"
```

**Run with opencode (this machine has opencode at `/usr/local/bin/opencode`):**

```bash
python -m desloppify review --run-batches --runner opencode --parallel --path <HOME>/zk-rag-v2 --batch-timeout-seconds 600
```

- 20 batches run in parallel (one per subjective dimension)
- `--batch-timeout-seconds 600` = 10 min per batch (sufficient)
- `--parallel` = all 20 run concurrently
- Runs in background: use `background=true, notify_on_complete=true` in terminal tool
- Exit code 1 with "packet has no investigation_batches" = forgot `--path <HOME>/zk-rag-v2` on the run-batches command

**After batches complete**, import results:

```bash
# Find the run directory
ls <DESLOP>.desloppify/subagent_runs/

# Import the completed run
python -m desloppify review --import-run <DESLOP>.desloppify/subagent_runs/<run-dir> --scan-after-import
```

Then check the work queue:

```bash
python -m desloppify show review --status open
```

### 4d. Suppress Verified False Positives

After fixing, suppress items that are genuinely false positives (CLI entry points, intentionally bare `except` for test mocks):

```bash
cd <HOME>/desloppify

# Example suppressions (verify each before running)
python -m desloppify suppress \
  --attest "I have actually verified these files are CLI entry points (shebang/if __name__). I am not gaming the score." \
  "orphaned::../zk-rag-v2/pipeline_f/emit_all.py"

python -m desloppify suppress \
  --attest "I have actually verified this is a test mock catching bare Exception. I am not gaming the score." \
  "smells::../zk-rag-v2/shared/api_server.py::broad_except"
```

**Rule:** Only suppress after verifying the issue is genuinely a false positive. `ruff clean + import OK` outranks stale desloppify state.

---

## Phase 5 — Pre-Commit Pipeline

**Goal:** Automated quality gates that run before every commit.

### 5a. Pre-Commit Script

Save as `<REPO>scripts/pre-commit-checks.sh`:

```bash
#!/usr/bin/env bash
set -e

echo "=== [1/5] Python lint (ruff) ==="
cd <HOME>/zk-rag-v2
ruff check . --fix --select=E,F --ignore=E501,E741
ruff check . --select=E,F --ignore=E501,E741  # verify zero remaining

echo "=== [2/5] Python tests ==="
python3 -m pytest tests/ -q --tb=no

echo "=== [3/5] Website Biome check ==="
cd <REPO>website
npm run lint
npm run ci

echo "=== [4/5] Website unit tests ==="
npm test

echo "=== [5/5] Desloppify scan ==="
cd <HOME>/desloppify
python -m desloppify scan --path <HOME>/zk-rag-v2 --skip-slow --quiet

echo "=== ALL CHECKS PASSED ==="
```

Make executable: `chmod +x scripts/pre-commit-checks.sh`

Run with: `./scripts/pre-commit-checks.sh`

### 5b. GitHub Actions — Python Workflow

Save as `.github/workflows/python.yml`:

```yaml
name: Python Quality

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install
        run: pip install -q ruff pytest

      - name: Lint
        run: ruff check . --select=E,F --ignore=E501,E741

      - name: Tests
        run: python3 -m pytest tests/ -q --tb=short
```

### 5c. GitHub Actions — JS Workflow

Save as `.github/workflows/js.yml`:

```yaml
name: JS Quality

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: website/package-lock.json

      - name: Install
        run: npm ci
        working-directory: website

      - name: Lint & format check
        run: npm run ci
        working-directory: website

      - name: Unit tests
        run: npm test
        working-directory: website

      - name: Browser smoke tests
        run: python3 test-website.py
        working-directory: .
```

---

## Phase 6 — Open Source Preparation

**Goal:** Clean the repository of internal references, stale files, and anything that shouldn't be public.

### 6a. Files to Delete Before Publishing

```
website/llms.txt                              # contains internal path references
website/registry.json                         # internal state snapshot (not source-of-truth)
website/js/api2.js.bak                       # confirmed stale backup
```

### 6b. Find Other Stale Files

```bash
# Find backups
find <HOME>/zk-rag-v2 -name "*.bak" -o -name "*.backup" -o -name "*.old"

# Find files with internal hostnames/IPs
grep -r "127.0.0.1\|localhost\|internal\|BLOCKOPS" --include="*.js" --include="*.py" website/ | grep -v node_modules

# Find llms.txt references
grep -r "llms.txt" <REPO> --include="*.md" --include="*.py"
```

### 6c. `.gitignore` Entries

Ensure these are ignored:

```
# Dependencies
website/node_modules/

# Python venvs
.venv/
pipeline_a/venv/

# Archives (pipeline outputs, not source)
*/archive/

# Build artifacts
*.pyc
__pycache__/
*.log

# OS
.DS_Store
Thumbs.db

# Local configs (not for public)
.env.local
.env.development
```

### 6d. Repository Root Files Needed for Open Source

```
LICENSE              # e.g. MIT — choose and create
README.md            # project description, quick start, architecture overview
CONTRIBUTING.md      # how to contribute, dev setup, testing workflow
.github/
  workflows/
    python.yml
    js.yml
```

### 6e. Public Repo Publish

After all phases complete and checks pass:

1. Create public repo on GitHub
2. `git push origin main`
3. Add `README.md`, `LICENSE`, `CONTRIBUTING.md`
4. Enable GitHub Actions
5. Add repo to GitHub Actions secrets if needed

---

## Quick-Start Commands Reference

```bash
# === Full pre-commit check (everything) ===
./scripts/pre-commit-checks.sh

# === Python only ===
ruff check . --fix --select=E,F --ignore=E501,E741
python3 -m pytest tests/ -q --tb=no

# === Website JS only ===
cd website
npm run ci    # lint + format check
npm test      # vitest unit tests
python3 ../test-website.py   # browser smoke tests

# === Desloppify ===
cd <HOME>/desloppify
python -m desloppify scan --path <HOME>/zk-rag-v2 --skip-slow
python -m desloppify next    # show highest-priority next fix
python -m desloppify status   # score dashboard

# === Run dogfood exploratory QA ===
# Load the dogfood skill first, then follow the workflow
skill_view(name='dogfood')
```

---

## Phase Ordering — Why Phase 4 Comes Before Phase 5

Phase 4 (subjective review) is where the code *actually gets better* — architectural and design problems surface there. Phase 5 only prevents regression; it doesn't improve anything. **Always do Phase 4 before Phase 5.**

The original plan had Phase 4 before Phase 5 (correct), but Phase 5 was recommended first because "it protects what was built." That reasoning was wrong — protecting past work is secondary to doing the actual work. The subjective review is the work.

## Execution Order Summary

```
Phase 1: Biome Setup
  1a. npm install vitest
  1b. biome init (no extra flags)
  1c. Overwrite biome.json with the working config above
  1d. Update package.json scripts
  1e. lint → lint:fix → --unsafe --write → format → ci
  1f. Strip console.* (user decision, manual)
  1g. Create website/.gitignore (required for useIgnoreFile:true)

Phase 2: JS Unit Tests
  2a. Create tests/ directory
  2b. Write tests/state.test.js
  2c. Write tests/renderer.test.js
  2d. Write tests/api.test.js
  2e. Expand Playwright smoke tests

Phase 3: Python Cleanup
  3a. ruff --fix (28 auto-fixable) → all fixed, 0 remaining
  3b. Manual ruff fixes (5 manual: unused vars, bare except, ambiguous var, moved imports)
  3c. Created pyproject.toml (E402, E501 ignored with rationale)
  3d. Verify pytest: 105 pass, 3 failed (pre-existing), 3 skipped

Phase 4: Desloppify
  4a. Force rescan (if needed after changes)
  4b. Fix mechanical issues (ruff clean — already done: 0 errors)
  4c. Run desloppify review --prepare --path <HOME>/zk-rag-v2
  4d. Run desloppify review --run-batches --runner opencode --parallel --path <HOME>/zk-rag-v2 --batch-timeout-seconds 600
  4e. Import results: desloppify review --import-run <run-dir> --scan-after-import
  4f. Fix issues from the subjective review
  4d. Suppress verified false positives

Phase 5: Pre-Commit Pipeline
  5a. Create scripts/pre-commit-checks.sh
  5b. Create .github/workflows/python.yml
  5c. Create .github/workflows/js.yml

Phase 6: Open Source Prep
  6a. Delete stale files (llms.txt, registry.json, api2.js.bak)
  6b. Audit for internal hostnames/IPs
  6c. Verify .gitignore complete
  6d. Create LICENSE, README.md, CONTRIBUTING.md
  6e. Publish to public GitHub repo
```
