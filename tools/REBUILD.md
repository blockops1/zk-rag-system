# ZK-RAG Public Repo Rebuild Process

## Purpose

Rebuild the public GitHub repo (`git@github.com:blockops1/zk-rag-system.git`) so a third party can clone it on a different machine and build/operate a completely self-sufficient ZK-RAG system from scratch.

## Source & Destination

- **Private repo:** `<PRIVATE_REPO>` (e.g. `/home/user/document-rag-with-zk/`)
- **Public repo:** `<PUBLIC_REPO>` (e.g. `/home/user/zk-rag-system/`)
- **Branch:** `main`
- **Remote:** `git@github.com:blockops1/zk-rag-system.git`

---

## Path Mapping System

All path mapping is one-way: private → public. Paths in the private repo are sanitized before being committed to the public repo.

### Contexts

Two separate contexts, each with its own rules:

| Context | Applies To |
|---|---|
| **Code files** | `zk-rag-v2/`, `tools/`, `website/` — code that runs |
| **Skill files** | `skills/` — operator reference documentation |

### Code Files: Relative Path Rule

**Rule:** Every path points somewhere inside the public repo or a directory that ships with it. No paths point outside the repo. Absolute paths become relative to the repo root.

#### Data Subdirectory Structure (ships with repo)

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

#### Path Mapping Table: Private → Public

| Original (private) | Replacement (public) | Notes |
|---|---|---|
| `/home/<USER>/` | removed, or `./` | Never hardcode home paths |
| `./data/` | `./data/` | Data directory root |
| `source_pdfs/` | `sourcePDF/` | Canonical name |
| `merkle_trees/` | `merkleTrees/` | Canonical name |
| `extracted-vision/` | **removed** | No longer used |
| `bm25_index.pkl` | **removed** | No longer used |
| `<PRIVATE_REPO>/` | `./` | Repo root becomes relative |
| `<FOUNDRY_BIN>/` | `./foundry-bin/` | Ships with repo |
| `<VENV>/` | `./.venv/` | Ships with repo |
| `/data/logs` | `../data/logs` | Relative from pipeline dirs |
| `RAG_DIR="rag"` | `RAG_DIR="."` | Pipeline shell scripts |

#### Shell Script Convention

Pipeline shell scripts (`pipeline_*/run_*.sh`, `harvest.sh`, `ingest.sh`) use:
```bash
RAG_DIR="."   # repo root — all paths are ../data/*
CHUNKS_DIR="../data/chunks"
OUT_DIR="../data/merkleTrees"
```

#### Python Convention

Pipeline Python scripts use relative paths from the pipeline subdirectory:
```python
REGISTRY_PATH = Path("../data/registry.json")
CHUNKS_DIR    = Path(os.getenv("CHUNKS_DIR", "../data/chunks"))
```

### Skill Files: Placeholder Token Rule

**Rule:** Skills are operator reference documentation. They use `<PLACEHOLDER>` tokens that an operator substitutes with their own system values. No concrete paths, hostnames, IPs, or usernames appear in skills.

#### Standard Placeholder Tokens

| Token | Meaning | Example |
|---|---|---|
| `<REPO>` | Git repository root | `/home/user/zk-rag-system/` |
| `<DATA>` | Data directory root | `/home/user/zk-rag-data/` |
| `<FOUNDRY_BIN>` | Foundry binaries path | `/home/user/.foundry/bin/` |
| `<VENV>` | Python venv root | `/home/user/zk-rag-venv/` |
| `<DESLOP>` | Desloppify install dir | `/home/user/desloppify/` |
| `<HOME>` | User home directory | `/home/user/` |
| `<USER>` | OS username | `username` |
| `<SERVER_IP>` | Internal server IP | `10.0.0.1` |
| `<PUBLIC_HOST>` | Public DNS hostname | `example.com` |
| `<QDRANT_URL>` | Qdrant HTTP URL | `http://127.0.0.1:6333` |

#### Applying Placeholders

Example — before (private):
```bash
cd /home/deruyter/rag
python3 scripts/ingest.py
```

Example — after (public skill):
```bash
cd <REPO>
python3 scripts/ingest.py
```

---

## Scan Targets

When running `scan_leaks.py`, target only the artifact directories — not the scanner itself:

```bash
# Scan code + skills, skip the tools/ scanner
python3 tools/scan_leaks.py zk-rag-v2/ skills/
```

The scanner (`tools/scan_leaks.py`) self-flags on its own `LEAK_PATTERNS` definitions — this is expected behavior, not a leak.

### Leak Patterns Scanned

- `/home/<username>/` — real home directory paths
- `./data/` — private data directory
- `source_pdfs/` — old directory name
- `merkle_trees/` — old directory name
- `extracted-vision/` — removed feature
- `bm25_index.pkl` — removed feature
- Internal IPs (`10.60.x.x`, `10.50.x.x`)
- Hostnames (`deruyter`, `blockops`)
- API keys, tokens, credentials

---

## Execution Log

### Status: COMPLETE (2026-05-28)

#### Step 1 — Archive old content
Not applicable — sync already present in public repo.

#### Step 2 — Rsync from private source
Already completed. `zk-rag-v2/` exists at `<PUBLIC_REPO>/zk-rag-v2/`.

#### Step 3 — Add missing required files ✓
- Created `requirements.txt` with all pip-installable dependencies
- Created `.env.example` with all environment variables

#### Step 4 — Path replacement ✓ DONE (2026-05-28)

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
- `pipeline_f/run_remaining.py`: `/data/logs` → `../data/logs`
- `pipeline_f/sync_merkle_cap_to_registry.py`: `/data/logs` → `../data/logs`
- `pipeline_f/emit_all.py`: `/data/logs` → `../data/logs` (comment)
- `docs/admin.md`: full rewrite for public repo structure
- `docs/README.md`: full rewrite for public repo structure

**Additional sanitization:**
- `shared/api_server.py`: removed x402 paid download endpoints (501 stubs), removed hardcoded `militarymanuals.ai` CORS origin → `CORS_ORIGIN` env var
- `pipeline_c/batch_image_describe.py`: `"military document image"` → `"document image"`
- `shared/batch_ingest_branch.py`: "Military Docs RAG" → "ZK-RAG"
- `skills/git-workflow/SKILL.md`: private repo URL → `zk-rag-system.git`
- `skills/zk-rag-operations/SKILL.md`: 3× `/data/military-documents` → `<DATA>`

**Verification:** `scan_leaks.py` reports clean for both `zk-rag-v2/` and `skills/`.

#### Step 5 — Commit and push ✓
```
cd <PUBLIC_REPO>
git add -A
git commit -m "<message>"
git push origin main
```

Commit history (2026-05-28):
- `b9e7f13` — rebuild: relative paths, generic data/, skill placeholder tokens
- `feb8ade` — fix rag/logs paths, create requirements.txt and .env.example, sanitize docs, update REBUILD.md path mapping
- `9eac844` — remove x402_paid_download import and endpoints, add .env.example
- `c2095de` — sanitize: CORS env var, VPS path comments, SmolVLM2 prompt, batch_ingest_branch description, git-workflow PR URL
- `80784ad` — remove duplicate scan_leaks.py from repo root
- `b2d7f60` — remove README_PUBLISH.md, add .gitignore

---

*Created: 2026-05-27*
*Updated: 2026-05-28 — full path mapping system with relative paths (code) and placeholder tokens (skills), scan target guide, execution log*
