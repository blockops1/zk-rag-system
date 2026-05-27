# Qdrant Rebuild Plan

**Date:** 2026-05-22
**Status:** Executed — confirmed working

## Root Cause
Documents were indexed to wrong Qdrant collections:
- Army docs in other collections
- Marine Corps and joint docs in army collection
- `air_force` (underscore form) not mapped in `normalize_branch()` — fell through to `other`
- `joint` not mapped in `normalize_branch()` — fell through to `other`

## Phase Sequence

### Phase 1 — Stop services
```bash
sudo systemctl stop zk-rag-api
sudo systemctl stop qdrant
```

### Phase 2 — Archive Qdrant storage
```bash
DATE=$(date +%Y%m%d)
mkdir -p <DATA>qdrant_archive_$DATE
mv <DATA>storage <DATA>qdrant_archive_$DATE/
sudo systemctl start qdrant
# Verify: curl http://127.0.0.1:6333/collections → {"collections":[]}
```

**Note:** Qdrant data path is `<DATA>storage`, NOT `qdrant/storage`.

### Phase 3 — Archive orphan merkle tree files
```python
import json, shutil
from pathlib import Path

DATE = "20250522"  # use date of archive
orphan_dir = Path(f"<DATA>merkle_trees_orphan_{DATE}")
orphan_dir.mkdir(exist_ok=True)

with open('<DATA>registry.json') as f:
    valid_ids = {d['doc_id'] for d in json.load(f)['documents']}

merkle_dir = Path('<DATA>merkle_trees')
for f in merkle_dir.iterdir():
    if f.stem.split('_')[0] not in valid_ids:
        f.rename(orphan_dir / f.name)
```

### Phase 4 — Update registry: branch reassignment + title prefixes
```python
import json, shutil, re

shutil.copy('<DATA>registry.json',
            '<DATA>registry.json.backup_YYYYMMDD')

with open('<DATA>registry.json') as f:
    reg = json.load(f)

BRANCH_PREFIXES = {
    'marines':  'US Marine Corps ',
    'navy':     'US Navy ',
    'air_force': 'US Air Force ',
    'joint':    'US Joint ',
}

def classify_branch(title):
    t = title
    if re.search(r'(MCRP|MCWP|NAVMC|Marine Corps)', t): return 'marines'
    if re.search(r'(NWP|Naval|l3-\d|U.S. Navy)', t): return 'navy'
    if re.search(r'(JP |Joint|JP-)', t): return 'joint'
    if re.search(r'(AF| Air Force|Airman |USAF )', t): return 'air_force'
    return 'army'

for doc in reg['documents']:
    doc['branch'] = classify_branch(doc['title'])
    for branch, prefix in BRANCH_PREFIXES.items():
        if doc['branch'] == branch and not doc['title'].startswith(prefix):
            doc['title'] = prefix + doc['title']
    if doc['branch'] == 'army' and not doc['title'].startswith('US Army '):
        doc['title'] = 'US Army ' + doc['title']

with open('<DATA>registry.json', 'w') as f:
    json.dump(reg, f, indent=2)
```

### Phase 5 — Create Qdrant collections
```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

client = QdrantClient(location='http://127.0.0.1:6333')
for name in ['army', 'navy', 'marines', 'air_force', 'joint', 'other']:
    client.create_collection(name, vectors_config=VectorParams(size=768, distance=Distance.COSINE))
    print(f"Created: {name}")
```

### Phase 6 — Verify registry internal consistency (pre-flight)
```python
# Run before Pipeline G — check all registry docs have required files
```

### Phase 7 — Run Pipeline G with `--reingest`
```bash
cd <REPO>pipeline_g
python3 pipeline_g.py --batch --reingest
```

**CRITICAL:** Before running, ensure `BRANCH_NORMALIZE_RULES` in `pipeline_g.py` includes all branch values in the registry:
- `army`, `navy`, `marines`, `air_force` (underscore), `air force` (space), `joint`, `coast guard`

Missing entries silently route to `other`.

### Phase 8 — Verify registry ↔ Qdrant consistency
```python
from qdrant_client import QdrantClient
import json

client = QdrantClient(location='http://127.0.0.1:6333')
with open('<DATA>registry.json') as f:
    reg_docs = {d['doc_id']: d for d in json.load(f)['documents']}

# Check each collection for: empty titles, branch mismatches, orphaned doc_ids
```

### Phase 9 — Restart API
```bash
sudo systemctl start zk-rag-api
```

## Results (2026-05-22 run)
- 527 docs processed
- 0 Pipeline G failures
- 2 missing mappings fixed in `BRANCH_NORMALIZE_RULES` after initial run revealed 23 docs in `other` collection
- Re-run required after patch

## Key Bug (2026-05-22)
`normalize_branch()` was missing `"air_force"` (underscore) and `"joint"` — both fell through to `"other"`. Fixed by adding both to `BRANCH_NORMALIZE_RULES`.
