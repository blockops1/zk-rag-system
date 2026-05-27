# Pipeline H (Title Review) & Pipeline J (Failed Doc Cleanup)

## Pipeline H — Title Review

Replaces the informal per-document title workflow with a formal batch process.

**When to run:** In parallel with embedding pipeline. Title review only reads pre-extracted text — no PDF access, no image processing. No filesystem contention with Pipeline D/E.

**Workflow:**
1. Compute BATCH_OFFSET from registry: `pending = [d for d in reg["documents"] if d.get("title_status") == "pending"]`
2. Extract page 1 for 10 docs from `<DATA>extracted/{doc_id}/pages/0000.json`
3. Present 10 docs → user approves → apply `title_status: approved` + `title_review_batch: N`
4. Repeat until `title_status=pending` returns 0 docs

**Important filter:** Use `title_status=pending` — NOT `has_embeddings`. The orphaned batch (59 docs that Pipeline J removed and were later re-registered) all had `has_embeddings=true` but were not pending title review. Using `has_embeddings` as filter would incorrectly include them.

**Completion:** All 527 embedded docs approved as of 2026-05-18 (512 pre-existing + 15 new from today's session).

**Skill:** `pipeline-h-title-review`

## Pipeline J — Failed Doc Cleanup

Final cleanup pass after pipeline completes. Moves permanently-failed PDFs to `failed_pdfs/` and removes entries from registry.

**When to run:** After all pipeline stages have settled. NOT during active pipeline runs — a doc that fails one stage may succeed on retry.

**Permanent failure criteria:**
- `status = skipped_no_url` — no working download source
- `status = error_zero_pages` — PDF had 0 pages or was corrupted

**Do NOT clean up:**
- `status = extracted` — Pipeline A completed (596 pages extracted), D1/D2 never ran. These are stalled, not failed. Run Pipeline D to pick them up.
- `status = ok` — awaiting pipeline processing
- `status = ingested` — successfully processed

**Run:**
```bash
cd <HOME>/zk-rag-v2
./venv/bin/python3 pipeline_j/pipeline_j_cleanup.py --list-only  # always first
./venv/bin/python3 pipeline_j/pipeline_j_cleanup.py --dry-run    # review
./venv/bin/python3 pipeline_j/pipeline_j_cleanup.py              # execute
```

**Last run:** 2026-05-17 (approx) — multiple passes confirmed. As of 2026-05-18: 59 orphaned artifact directories remain on disk (see below).

## Orphaned Artifacts After Pipeline J

Pipeline J removes docs from the registry only — it does NOT delete `extracted/` or `chunks/` directories. After Pipeline J runs, orphaned artifact directories persist:

```
<DATA>extracted/  — 586 total → 527 in registry, 59 orphaned
<DATA>chunks/      — 586 total → 527 in registry, 59 orphaned
<DATA>merkle_trees/ — 586 total → 527 in registry, 59 orphaned
```

These 59 orphaned dirs (docs permanently removed by Pipeline J) are harmless left as forensic evidence. They are NOT re-ingested by Pipeline G because Pipeline G reads from registry only.

## Pipeline J Artifact Orphaning

Pipeline J's `--delete` mode only removes registry entries and moves source PDFs to `failed_pdfs/`. The `extracted/`, `chunks/`, and `merkle_trees/` directories are NOT cleaned — this is the expected behavior. To find orphaned artifact dirs at any time:

```python
import json, os
r = json.load(open('<DATA>registry.json'))['documents']
reg_ids = {d['doc_id'] for d in r}
for subdir in ['extracted', 'chunks', 'merkle_trees']:
    dirs = set(os.listdir(f'<DATA>{subdir}'))
    orphaned = dirs - reg_ids
    print(f'{subdir}: {len(dirs)} total, {len(orphaned)} orphaned')
```

## Qdrant ↔ Registry Sync After Pipeline J

Pipeline J removes docs from the registry. If Qdrant was already populated, orphaned Qdrant records remain. The next Pipeline G `--reingest` run will overwrite them cleanly — no manual Qdrant deletion needed. Orphaned records in Qdrant from Pipeline J are harmless because Pipeline G upserts by point ID (doc_id + chunk_index), replacing stale records with fresh ones on re-ingest.

If Qdrant itself was reset (not just registry updated), run Pipeline G `--batch --reingest` to repopulate all 527 docs from registry state.

**Skill:** `pipeline-j-failed-doc-cleanup`

## Relationship to Other Pipelines

```
Pipeline A (fitz) → Pipeline D (chunk + embed) → Pipeline E (Merkle) → Pipeline F (emit) → Pipeline G (Qdrant)
                                                                                                    ↓
                                                              Pipeline H (title review — runs any time, reads extracted/)
                                                                                                    ↓
Pipeline J (cleanup — run after pipeline settles, moves failed PDFs to failed_pdfs/)
```

**Stalled docs pattern:** When Pipeline A completes but D1/D2 never runs, docs show `status = extracted` with full page counts (e.g., 596 pages). These have `has_embeddings = false` but are recoverable — Pipeline D will pick them up. They are NOT cleanup candidates.
