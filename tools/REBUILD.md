# ZK-RAG Public Repo Rebuild Process

## Purpose
Rebuild the public GitHub repo (`git@github.com:blockops1/zk-rag-system.git`) so a third party can clone it on a different machine and build/operate a completely self-sufficient ZK-RAG system from scratch.

## Source & Destination
- **Private repo:** `<PRIVATE_REPO>` (e.g. `/home/user/document-rag-with-zk/`)
- **Public repo:** `<PUBLIC_REPO>` (e.g. `/home/user/zk-rag-system/`)
- **Branch:** `main`
- **Remote:** `git@github.com:blockops1/zk-rag-system.git`

---

## Data Directory Structure

```
data/
├── registry.json
├── sourcePDF/       ← source PDFs
├── chunks/          ← chunked text
├── embeddings/      ← vector embeddings
├── merkleTrees/     ← ZK merkle trees
├── logs/            ← pipeline logs
├── zk_proofs/       ← ZK proof output
├── images/          ← extracted images
├── extracted/       ← PDF text extraction
├── qdrant/          ← Qdrant vector DB
├── failed_pdfs/     ← failed extraction tracking
├── archive/         ← archived docs
└── extraction_queue.json
```

**Renames applied:**
- `source_pdfs` → `sourcePDF`
- `merkle_trees` → `merkleTrees`
- `extracted-vision/` removed (no longer used)
- `bm25_index.pkl` removed (no longer used)
- `extracted-vision` logic removed from `pipeline_d.py`, `pipeline_d/chunk_document.py`
- BM25 dead code removed from `pipeline_g.py` (5 functions + imports)

---

## Path Mapping System

Two separate contexts: **code files** vs **skill files**.

### Code Files (`zk-rag-v2/`, `tools/`)

Rule: Every path points somewhere inside the public repo or a directory that ships with it. No paths point outside the repo. Absolute paths become relative to the repo root.

| Original | Replacement |
|---|---|
| `<PRIVATE_REPO>/` | `./` |
| `<DATA_DIR>/` | `./data/` |
| `source_pdfs` | `sourcePDF` |
| `merkle_trees` | `merkleTrees` |
| `<FOUNDRY_BIN>/` | `./foundry-bin/` |
| `<VENV>/` | `./.venv/` |
| `<HOME>/` | removed entirely |

### Skill Files (`skills/`)

Skills are operator reference documentation. They use placeholder tokens that an operator substitutes for their own system values.

| Placeholder | Meaning |
|---|---|
| `<REPO>` | Git repository root (e.g. `/home/user/zk-rag-system/`) |
| `<DATA>` | Data directory root (e.g. `/home/user/zk-rag-data/`) |
| `<FOUNDRY_BIN>` | Foundry binaries path (e.g. `/home/user/.foundry/bin/`) |
| `<VENV>` | Python venv root (e.g. `/home/user/zk-rag-venv/`) |
| `<DESLOP>` | Desloppify install dir (e.g. `/home/user/desloppify/`) |
| `<HOME>` | User home directory (e.g. `/home/user/`) |
| `<USER>` | OS username (e.g. `username`) |
| `<SERVER_IP>` | Internal server IP (e.g. `10.0.0.1`) |
| `<PUBLIC_HOST>` | Public DNS hostname (e.g. `example.com`) |

---

## Scan Targets

When running `scan_leaks.py`, target only the artifact directories — not the scanner itself:

```bash
# Scan code + skills, skip the tools/ scanner
python3 tools/scan_leaks.py zk-rag-v2/ skills/
```

The scanner (`tools/scan_leaks.py`) self-flags on its own `LEAK_PATTERNS` definitions — this is expected behavior, not a leak.

---

## Execution Log

### Status: COMPLETE (2026-05-28)

### Step 1 — Archive old content ✓
Not applicable — sync already present in public repo.

### Step 2 — Rsync from private source ✓
Already completed. `zk-rag-v2/` exists at `<PUBLIC_REPO>/zk-rag-v2/`.

### Step 3 — Add missing required files ✓ DONE (2026-05-28)
- Created `requirements.txt` with all pip-installable dependencies
- Created `.env.example` with all environment variables

### Step 4 — Path replacement ✓ DONE (2026-05-28)

**Code files (35+ files):**
- Root: `foundry.toml`
- Docs: `PROJ.md`, `docs/README.md`, `docs/admin.md`, `docs/dependency-map.md`
- All pipeline dirs, `shared/`, `zk-circuit/`, `tools/scaffold_zkrag.py`

**Skill files (37 files):**
- All `skills/**/*.md` patched with placeholder tokens

**Dead code removed:**
- `pipeline_d.py`: removed `VISION_BASE` and `ingested-vision` logic
- `pipeline_d/chunk_document.py`: removed `VISION_BASE`, simplified source selection
- `pipeline_g.py`: removed BM25 helpers, `pickle`/`re` imports, commented-out BM25 call block

**Path fixes applied:**
- `shared/_log.py`: `rag/logs` → `../data/logs`
- `pipeline_a/harvest.sh`: `RAG_DIR="rag"` → `RAG_DIR="."`
- `pipeline_a/ingest.sh`: `RAG_DIR="rag"` → `RAG_DIR="."`
- `docs/admin.md`: full rewrite for public repo structure
- `docs/README.md`: full rewrite for public repo structure

**Verification:** `grep` confirms zero `<HOME>`, `<DATA>`, `source_pdfs`, `merkle_trees`, `extracted-vision`, `bm25_index` in any `.py` or `.sh` file in `zk-rag-v2/`.

### Step 5 — Run leak scan ✓ DONE (2026-05-28)
All leaks fixed. Skills and code clean.

### Step 6 — Commit and push
```
cd <PUBLIC_REPO>
git add -A
git commit -m "rebuild: relative paths, generic data/, skill placeholder tokens"
git push origin main
```

### Step 7 — Update this document
Mark each step complete with timestamp.

---

## Path Mapping System

### Code Files (`zk-rag-v2/`, `tools/`)

Rule: Every path points somewhere inside the public repo or a directory that ships with it. No paths point outside the repo. Absolute paths become relative to the repo root.

| Original | Replacement |
|---|---|
| `<PRIVATE_REPO>/` | `./` |
| `/data/military-documents/` | `./data/` |
| `source_pdfs` | `sourcePDF` |
| `merkle_trees` | `merkleTrees` |
| `<FOUNDRY_BIN>/` | `./foundry-bin/` |
| `<VENV>/` | `./.venv/` |
| `rag/logs` | `../data/logs` |
| `RAG_DIR="rag"` | `RAG_DIR="."` |

### Skill Files (`skills/`)

Skills are operator reference documentation. They use placeholder tokens that an operator substitutes for their own system values.

| Placeholder | Meaning |
|---|---|
| `<REPO>` | Git repository root (e.g. `/home/user/zk-rag-system/`) |
| `<DATA>` | Data directory root (e.g. `/home/user/zk-rag-data/`) |
| `<FOUNDRY_BIN>` | Foundry binaries path (e.g. `/home/user/.foundry/bin/`) |
| `<VENV>` | Python venv root (e.g. `/home/user/zk-rag-venv/`) |
| `<DESLOP>` | Desloppify install dir (e.g. `/home/user/desloppify/`) |
| `<HOME>` | User home directory (e.g. `/home/user/`) |
| `<USER>` | OS username (e.g. `username`) |
| `<SERVER_IP>` | Internal server IP (e.g. `10.0.0.1`) |
| `<PUBLIC_HOST>` | Public DNS hostname (e.g. `example.com`) |

---

*Created: 2026-05-27*
*Updated: 2026-05-28 — full path mapping system, placeholder tokens for skills, scan target guide*
