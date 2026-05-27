---
name: zk-rag-chunk-image-loading
description: "Load document images for ZK-RAG chunks in the military documents website. Use when: fixing or improving how images appear alongside text chunks. Handles the two-pass approach: search results (single chunk) vs reading pane (multiple chunks)."
---

# ZK-RAG Chunk Image Loading

## Problem
Chunks span multiple pages but only the starting page was used to load images — images on subsequent pages were never fetched.

## Key Insight
Chunks in Qdrant have a `page` field (starting page). Adjacent chunks' `page` fields define page ranges — if chunk N has `page=10` and chunk N+1 has `page=15`, chunk N spans pages 10–14.

## Two Contexts

### 1. Search Results (single chunk)
Single chunk, limited context — can't know page range without next chunk.

```javascript
// text has [PAGE N] markers only from newer ingestion runs
function extractPages(text, fallbackPage) {
  if (!text) return fallbackPage ? [fallbackPage] : [];
  const matches = text.match(/\[PAGE (\d+)\]/g);
  if (matches && matches.length > 0) {
    return [...new Set(matches.map(m => parseInt(m.replace('[PAGE ', '').replace(']', ''), 10)))].sort((a, b) => a - b);
  }
  return fallbackPage ? [fallbackPage] : [];
}
```

Call: `extractPages(passage.text || '', page)` — markers if present, else `[page]`

### 2. Reading Pane (multiple chunks)
Full window of adjacent chunks available — use next chunk's `page` to compute exact range.

```javascript
const nextPage = idx + 1 < chunks.length ? (chunks[idx + 1].page || 1) : null;
let allPages;
if (nextPage !== null && nextPage > page) {
  // Build [page, page+1, ..., nextPage-1]
  allPages = [];
  for (let p = page; p < nextPage; p++) allPages.push(p);
} else {
  allPages = [page];
}
```

### Image Loading (both contexts)
```javascript
allPages.forEach(pageNum => loadImage(docId, pageNum, container));
```

## Image Format
Older docs extracted as `.jb2` (JBIG2 binary) — browsers cannot render these. Newer docs use `.png`.

**Conversion script:** `<REPO>scripts/convert_jb2_to_png.py`
```bash
<VENV>venv/bin/python3 <REPO>scripts/convert_jb2_to_png.py  # full corpus
<VENV>venv/bin/python3 <REPO>scripts/convert_jb2_to_png.py --doc-id <id>  # single doc
```

**How it works:** Parses filename (`page_XXXX_img_YY.jb2`) → finds source PDF in `<DATA>source_pdfs/` → re-extracts via `fitz.Pixmap(pdf_doc, xref)` → saves as `.png` → deletes `.jb2`. **Do NOT use manifest `img_idx`** — manifest indices are wrong for some docs; filename parsing is reliable.

**Unrecoverable docs (no source PDF):** 4 docs have JBIG2 files that cannot be converted — no source PDF in `<DATA>source_pdfs/`:
- `3d8e35a...` (442 JB2) — "Op0"
- `f056741c...` (341 JB2) — "Opticalman Navedtra 10215"
- `c7953b5...` (266 JB2) — "?"
- `a0853fd...` (3 JB2) — "Course Clock Mk2"

**Result (2026-04-30):** ~4,888 JB2 files converted to PNG across 62 docs. 1,052 JB2 remain in the 4 unrecoverable docs above.

## Image Format: PNG + WebP Dual-Format — RESOLVED (2026-05-21)

**Problem:** Every page had both `.png` and `.webp` pairs (110K each = 220K total). The API returned both, and the website rendered each figure twice.

**Resolution:** All PNGs were moved to `<DATA>images_png_archive/`. The website now serves only WebP.

**What was done:**
```bash
# 109,800 PNGs moved from images/ to images_png_archive/
# API now returns only .webp (e.g. ["page_0000.webp"])
# Verified: curl http://127.0.0.1:8100/api/images/{doc_id}/1 → single webp
```

**Result:**
- `images/`: 0 PNGs, 109,800 WebP (only served)
- `images_png_archive/`: 230,726 total files (PNG archive, not served)
- `images_png_archive/` is a cold backup — safe to ignore

**`images_png_archive/`** (`<DATA>images_png_archive/`):
- Cold archive only — API's `IMAGES_DIR` points to `images/`, never reads from archive
- 586 doc dirs (more than the 523 active docs — some old/deprecated docs)
- **Do NOT delete** — contains historical PNGs for docs that may not have webp counterparts

**API server extension filter** at `api_server.py:1036`:
```python
image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
```

## Data Notes
- Images: `<DATA>images/{doc_id}/page_XXXX_img_YY.png` (0-indexed files)
- API: `GET /api/images/{doc_id}/{page}` (1-indexed page param, converts internally)
- Not all chunks have `[PAGE N]` markers — older ingestion runs lack them; `page` field is the reliable fallback
- Some chunks have stale `page` metadata — verify against actual chunk content when possible
- **Image format:** The API server's extension filter at `api_server.py:1036` lists allowed extensions. Verify with `curl "http://127.0.0.1:8100/api/images/{doc_id}/1"`. The fix for `.jb2` files is to convert to `.png` using `fitz.Pixmap(doc, xref)` via the conversion script above.
