# Qdrant Payload Structure — Website Data Source

**All document metadata for the website comes from Qdrant only** — not from registry.json, not from disk. The website never reads the registry directly.

## What Gets Stored Where

| Data | Storage | Website reads from |
|------|---------|-------------------|
| Titles, pub_year, branch, doc_type, category, ia_identifier | Qdrant payload (top level) | `firstPassage.title`, `firstPassage.pub_year`, etc. |
| Passage text, chunk_index | Qdrant payload (top level) | `firstPassage.text`, `firstPassage.chunk_index` |
| Page images | Disk: `<DATA>images/*.webp` | `GET /api/images/{doc_id}/{page_num}` |
| Catalog page titles | Qdrant via `_get_catalog_docs_per_collection()` | `GET /api/catalog` — **previously read registry.json directly (bug, fixed 2026-05-21)** |

> ⚠️ **Catalog vs. search title mismatch (fixed 2026-05-21):** Before the fix, `GET /api/catalog` read `registry.json` directly, so catalog titles could differ from search results (which always used Qdrant). The fix scrolls Qdrant per collection, deduplicates by `doc_id`, and returns the same payload fields as search. Response shape is unchanged so catalog.html JS requires no modifications.

## Pipeline G Payload (pipeline_g/pipeline_g.py lines 265-299)

Each chunk's Qdrant payload structure:

```python
payload = {
    "doc_id": doc_id,
    "chunk_id": chunk["chunk_id"],
    "text": chunk.get("text", ""),
    "page": chunk.get("page"),
    "section_title": chunk.get("section_title"),
    "chunk_index": chunk.get("chunk_index"),
    # Registry fields — at TOP LEVEL (not under metadata sub-object):
    "title": registry_entry.get("title", ""),
    "branch": registry_entry.get("branch", ""),
    "category": registry_entry.get("category", ""),
    "doc_type": registry_entry.get("doc_type", ""),
    "source": registry_entry.get("source", ""),
    "pub_year": registry_entry.get("pub_year"),      # integer or null
    "file_size_bytes": registry_entry.get("file_size_bytes"),
    "ia_identifier": registry_entry.get("ia_identifier"),
    # Merkle + EVM fields also at top level
    "merkle_leaf_hash": merkle["merkle_leaf_hash"],
    "merkle_root": merkle_root,
    "evm_tx_hash": evm["evm_tx_hash"],
    ...
}
```

## Common Bug: Wrong Access Path

**Symptom:** Document header shows blank metadata (empty year, empty doc_type, etc.) despite Qdrant having the data.

**Root cause in renderer.js:** Reading from non-existent `metadata` sub-object:
```javascript
// WRONG — firstPassage.metadata always {} because there's no metadata sub-object
const metadata = firstPassage.metadata || {};
const pubYear = metadata.pub_year || "";      // always ""
const docType = metadata.doc_type || "";      // always ""

// CORRECT — fields are at top level of API response
const pubYear = firstPassage.pub_year || "";  // reads from Qdrant payload
const docType = firstPassage.doc_type || "";  // reads from Qdrant payload
```

## Key Implication

When debugging website display issues for document metadata:
1. Check what Pipeline G stored in Qdrant — not what the registry says
2. The API (`/api/context`, `/api/search`) returns Qdrant payloads as-is
3. Registry is the **source of truth for ingest** but website only sees Qdrant output
4. If Qdrant payload is correct but website shows wrong data → bug is in JS (likely wrong field path)
