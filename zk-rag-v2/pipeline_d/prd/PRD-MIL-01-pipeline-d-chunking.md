# PRD-MIL-01: Pipeline D — Document Chunking

**Status:** Draft
**Author:** Fred (data backbone)
**Date:** 2026-04-02
**Pipeline:** D (Chunking)
**Depends On:** C (Pipeline C complete for the document)
**Git repo:** `$REPO_DIR/scripts/chunk_document.py`

---

## 1. Problem Statement

Pipeline C produces per-page JSON files in `/data/rag/ingested-vision/{doc_id}/pages/`. These need to be split into overlapping text chunks suitable for embedding and vector search. The existing chunking logic in `pipeline_d.py` (untested) is tightly coupled to Qdrant upsert — it must be extracted into a standalone, independently testable step.

---

## 2. Goals

- Split page JSONs into overlapping character chunks
- Preserve page metadata (chapter, section, page number) per chunk
- Handle figure-only pages by including their SmolVLM2 `vision_description` as chunk text
- Output a deterministic `chunks.jsonl` file per document
- Independently testable: no Qdrant, no EVM, no external dependencies
- Chunked output usable as input to Pipeline E (Merkle tree)

---

## 3. Input

```
/data/rag/ingested-vision/{doc_id}/
  manifest.json
  pages/
    0.json   (page 0)
    1.json   (page 1)
    ...
```

**manifest.json fields used:**
```json
{
  "doc_id": "05f9cb1d911b14a6c3fdd8d27753198fce33f0e55f40563114a224a1babd2d78",
  "title": "US Army Survival Manual",
  "page_count": 256,
  "sha256": "abc123..."
}
```

**page.json fields used:**
```json
{
  "page": 7,
  "text": "The launcher must be extended to...",
  "chapter": "1",
  "section": "1-2",
  "section_title": "Launcher Operation",
  "figure_only": false,
  "vision_description": null   // present on figure-only pages from Pipeline C
}
```

---

## 4. Output

```
/data/rag/chunks/{doc_id}/
  chunks.jsonl
  chunk_ids.json       (for Pipeline F compatibility)
```

**chunks.jsonl format** (one JSON object per line):
```json
{
  "chunk_id": "05f9cb1d...-0",
  "doc_id": "05f9cb1d911b14a6c3fdd8d27753198fce33f0e55f40563114a224a1babd2d78",
  "text": "[PAGE 1]\n\nFM 21-76 US ARMY SURVIVAL MANUAL\n\n[PAGE 2]\nThe launcher must be extended...",
  "page": 2,
  "chapter": "1",
  "section": "1-2",
  "section_title": "Launcher Operation",
  "exhibit": null,
  "chunk_index": 0,
  "vision_description_used": false,
  "source": "ingested-vision"   // "ingested-vision" or "ingested"
}
```

**Chunk text construction rules:**
1. Pages processed in ascending page number order
2. For each page:
   - Prepend `\n[PAGE {page_num}]\n`
   - If `page.figure_only == true` AND `page.vision_description` exists: prepend `[VISUAL: {vision_description}]\n\n`
   - Append page text
3. Join all pages in order to form `full_text`
4. Split `full_text` using `RecursiveCharacterTextSplitter` (see parameters below)

---

## 5. Design Decisions

### 5.1 Chunk Size and Overlap

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `chunk_size` | 512 characters | Existing standard in `pipeline_d.py`. Balances semantic coherence with embedding context limits. |
| `chunk_overlap` | 100 characters | Preserves cross-boundary context. Existing standard. |

### 5.2 Splitter Separators (priority order)

```
1. "\n\n\n"   — Major section breaks (paragraph gaps)
2. "\n\n"     — Paragraph breaks
3. "\n"       — Line breaks
4. ". "      — Sentence boundary (harder to split on in military docs with numbered paragraphs)
5. " "       — Word boundary (fallback)
6. ""        — Character boundary (last resort)
```

This differs from `pipeline_d.py` which used `["\n\n", "\n", " "]` only. The expanded separator list from the archived `chunk_document.py` produces better semantic splits.

### 5.3 Chunk Filtering

- **Skip chunks < 50 characters** after stripping whitespace. Prevents page markers and orphans from becoming chunks.
- **Skip chunks that are pure page markers** (e.g., `[PAGE N]` with no actual text).

### 5.4 Chunk ID Format

`{doc_id}-{chunk_index}` where `chunk_index` is the 0-based position in the split output.

Example: `05f9cb1d911b14a6c3fdd8d27753198fce33f0e55f40563114a224a1babd2d78-0`

Rationale: Stable, deterministic, reproducible across re-runs. UUIDs were used in the archived chunker but are unnecessary here.

### 5.5 Source Selection

If both `/data/rag/ingested/{doc_id}/` and `/data/rag/ingested-vision/{doc_id}/` exist:
- Use the one with the newer modification time (or: prefer ingested-vision always when it has the same page count as ingested)
- `source` field in output reflects which was used

If only one exists, use that one.

### 5.6 Metadata Propagation

- `page`: Page number of the page where the chunk starts
- `chapter`, `section`, `section_title`: From the page where the chunk starts
- `exhibit`: First exhibit reference found across pages involved in the chunk (or `null`)
- `vision_description_used`: `true` if any figure-only page with `vision_description` contributed to this chunk
- `chunk_index`: 0-based position in document chunk list

---

## 6. CLI Interface

```bash
python chunk_document.py \
    --doc-id <doc_id> \
    [--chunk-size 512] \
    [--overlap 100] \
    [--out-dir /data/rag/chunks]
```

**Arguments:**
| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--doc-id` | Yes | — | Document ID (directory name in ingested-vision/) |
| `--chunk-size` | No | 512 | Target chunk size in characters |
| `--overlap` | No | 100 | Overlap between chunks in characters |
| `--out-dir` | No | /data/rag/chunks | Parent output directory |

**Output files:**
- `{out-dir}/{doc_id}/chunks.jsonl`
- `{out-dir}/{doc_id}/chunk_ids.json`

**Exit codes:**
- 0: Success
- 1: Error (manifest.json not found, no pages, etc.)

---

## 7. Error Handling

| Error | Behavior |
|-------|----------|
| `manifest.json` not found | Exit with error, log message |
| `pages/` directory not found or empty | Exit with error |
| Zero valid chunks after filtering | Write empty `chunks.jsonl`, log warning |
| Page JSON parse error | Skip that page, log warning, continue |

---

## 8. Testing

### Unit Tests
1. **Figure-only page**: Document with `figure_only=true` and `vision_description` — verify `vision_description_used=true` in output chunks
2. **Multi-page chunk**: Document where a single chunk spans two pages — verify page/chapter metadata from first page
3. **Short chunk filter**: Document with pages shorter than 50 chars — verify no empty/small chunks output
4. **Determinism**: Running twice on same input produces identical output (same chunk_ids and order)

### Integration Test
1. Run Pipeline D on a known document
2. Verify `chunks.jsonl` line count matches chunk_ids.json length
3. Verify each chunk_id matches `doc_id-chunk_index`
4. Verify all page numbers in chunks are valid page numbers from manifest

---

## 9. Blocking Issues (Must Resolve Before Proceeding)

None — all design decisions are captured above.

---

## 10. Open Questions

| Question | Decision Needed | Recommendation |
|----------|----------------|----------------|
| Should we support `dry-run` mode? | Yes/no | Yes — show what would be output without writing files |
| Do we need a batch runner? | Yes/no | Initially no — scripts/run_chunk_batch.sh can be added later |
| Should we track per-doc chunk counts in the registry? | Yes/no | Yes — add `chunk_count` to registry after Pipeline D completes |
