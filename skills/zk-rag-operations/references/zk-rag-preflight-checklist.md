# ZK-RAG Pre-Flight Verification

**Purpose:** Inventory check for all 527 registry documents vs disk.
**When to run:** Phase 0 (before any work), Phase 6 (before Pipeline G), post-rebuild QA.

## Phase 0 Checklist (before any rebuild work)

1. Run full inventory script below — no modifications yet
2. Fix header mismatch (`total_documents` ≠ actual count)
3. Fix missing images — check `images_png_archive/`
4. Archive orphan files (never delete)
5. Resolve INVALID_BRANCH before Phase 4

## Inventory Script

```python
#!/usr/bin/env python3
import json
from pathlib import Path

REGISTRY = '<DATA>registry.json'
IMAGES   = Path('<DATA>images')
MERKLES  = Path('<DATA>merkle_trees')
CHUNKS   = Path('<DATA>chunks')
EMBEDS   = Path('<DATA>embeddings')

with open(REGISTRY) as f:
    reg = json.load(f)

docs = reg['documents']
reg_ids = {d['doc_id'] for d in docs}

missing_images = []
missing_merkle = []
missing_embeds = []
missing_chunks = []
invalid_branch = []
empty_title = []

for d in docs:
    doc_id = d['doc_id']
    if not d.get('title', '').strip():
        empty_title.append(doc_id)
    if d.get('branch', '') not in ('army', 'navy', 'marines', 'air_force', 'joint', 'other'):
        invalid_branch.append((doc_id, d.get('branch')))
    if not (MERKLES / f'{doc_id}_tree.json').exists():
        missing_merkle.append(doc_id)
    if not (EMBEDS / doc_id).exists():
        missing_embeds.append(doc_id)
    if not (CHUNKS / doc_id).exists():
        missing_chunks.append(doc_id)
    if not (IMAGES / doc_id).exists():
        missing_images.append(doc_id)

orphan_merkle = [f.name for f in MERKLES.iterdir()
                 if f.is_file() and f.stem.replace('_tree','') not in reg_ids]
orphan_embeds = [d.name for d in EMBEDS.iterdir()
                 if d.is_dir() and d.name not in reg_ids]
orphan_images = [d.name for d in IMAGES.iterdir()
                 if d.is_dir() and d.name not in reg_ids]

print(f"Registry docs: {len(docs)}")
print(f"Header total_documents: {reg.get('total_documents')} "
      f"{'✓' if reg.get('total_documents') == len(docs) else '✗ MISMATCH'}")
print(f"EMPTY titles: {len(empty_title)}")
print(f"INVALID branches: {len(invalid_branch)}")
print(f"MISSING merkle trees: {len(missing_merkle)}")
print(f"MISSING embeddings: {len(missing_embeds)}")
print(f"MISSING chunks: {len(missing_chunks)}")
print(f"MISSING images: {len(missing_images)}")
print(f"ORPHAN merkle trees: {len(orphan_merkle)}")
print(f"ORPHAN embeddings: {len(orphan_embeds)}")
print(f"ORPHAN images: {len(orphan_images)}")
print()
if missing_images:
    print(f"Missing images ({len(missing_images)}): {missing_images}")
if invalid_branch:
    print(f"Invalid branches: {invalid_branch[:5]}")
if orphan_merkle:
    print(f"Orphan merkle trees ({len(orphan_merkle)}): {sorted(orphan_merkle)[:3]}")
```

## Missing Images Fix

Images sometimes in `images_png_archive/` but not in `images/`:

```python
import json
from pathlib import Path

archive = Path('<DATA>images_png_archive')
images  = Path('<DATA>images')
reg_path = '<DATA>registry.json'

with open(reg_path) as f:
    reg_ids = {d['doc_id'] for d in json.load(f)['documents']}

archive_docs = {d.name for d in archive.iterdir() if d.is_dir()}
images_docs  = {d.name for d in images.iterdir()  if d.is_dir()}
to_move = archive_docs - images_docs

for doc_id in to_move:
    if doc_id in reg_ids:
        (archive / doc_id).rename(images / doc_id)
        print(f"Moved: {doc_id}")
```

## Branch Inference Rules

| Title contains | → branch |
|---|---|
| `JP `, `JP-`, `Joint ` | `joint` |
| `MCRP`, `MCWP`, `NAVMC`, `Paine` | `marines` |
| `NWP`, `SECNAVINST`, `OPNAVINST`, `Naval TR` | `navy` |
| `AFPAM`, `AFMAN`, `AFI`, `HAF` | `air_force` |
| `FM `, `TC `, `TM `, `STP `, `AR `, `DA Pam`, `EM `, `TB `, `LFM `, `ST ` | `army` |
| anything else | `other` |

**`Joint*` → `joint` not `other`:** `Joint UFC`, `Joint Staff HDBK`, etc.

## Title Prefixes

| branch | prefix to add |
|---|---|
| `army` | `US Army ` |
| `navy` | `US Navy ` |
| `marines` | `US Marine Corps ` |
| `air_force` | `US Air Force ` |
| `joint` | `US Joint ` |
| `other` | **no change** |

## Pipeline G BRANCH_NORMALIZE_RULES (must match registry values)

```python
BRANCH_NORMALIZE_RULES = [
    ("army",       "army"),
    ("navy",       "navy"),
    ("marines",    "marines"),
    ("air_force",  "air_force"),   # underscore — registry stores this form
    ("air force",  "air_force"),   # space — fallback
    ("joint",      "joint"),       # was missing → 13 joint docs landed in 'other'
    ("coast guard", "coast_guard"),
]
# everything else → "other"
```

**Bug 2026-05-22:** Missing `"air_force"` caused 10 air_force docs in `other`. Missing `"joint"` caused 13 joint docs in `other`.
