# ZK-RAG Public Repo — Status Review (2026-05-27)

## Current State

**Public repo:** `github.com/blockops1/zk-rag-system`
**Working dir:** `<HOME>/zk-rag-public/`
**Last push:** `45a4d8f` (docs: rewrite README with full usage guide + fix scan_leaks false positive)
**Leak scan:** ✅ Clean

### What's in the public directory

```
zk-rag-public/
├── README.md                    ✅ Solid — architecture, quick start, API examples, pipeline commands
├── README_PUBLISH.md            ⚠️  Internal scaffold instructions — not user-facing
├── scan_leaks.py                ✅ Leak scanner
├── data/                        ✅ Empty scaffold dirs (chunks, embeddings, logs, merkle_trees, zk_proofs)
└── zk-rag-v2/
    ├── .env.example             ✅ All required env vars with placeholder values
    ├── .gitignore               ✅ Standard exclusions
    ├── foundry.toml
    ├── scaffold_zkrag.py        ⚠️  Internal build tool — doesn't belong in published output
    ├── PROJ.md / PROJ-zk-rag*.md  ⚠️  Internal project planning docs
    ├── pipeline_a/              ✅ PDF text extraction (fitz)
    ├── pipeline_b/              ✅ OCR (docling)
    ├── pipeline_c/              ✅ Vision captions (SmolVLM2)
    ├── pipeline_d/              ✅ Chunk + embed + Qdrant upsert
    ├── pipeline_e/              ✅ Poseidon Merkle tree (Rust/plonky2)
    ├── pipeline_f/              ✅ On-chain emission (Foundry)
    ├── pipeline_g/              ✅ Qdrant sync with ZK metadata
    ├── shared/
    │   ├── api_server.py        ✅ FastAPI query + provenance
    │   ├── embedding_service.py ✅ Qwen3 embedding service
    │   ├── provenance.py        ✅ ZK proof generation
    │   ├── x402_paid_download.py ✅ Paid PDF download (EIP-3009)
    │   ├── *.service            ✅ Systemd units
    │   └── ⚠️  INTERNAL SYNC SCRIPTS (push_to_vps.sh, rollback_on_failure.sh,
    │       run_vps_health.sh, VPS-SYNC-PLAN.md, PROD-SYNC-PLAN.md) — not public
    ├── zk-circuit/
    │   ├── circuit/src/         ✅ Rust source
    │   ├── prove-bin/src/       ✅ Prover binary source
    │   ├── verify-zk-proof/src/ ✅ On-chain verifier binary source
    │   ├── kurier_submit.py     ✅ Kurier/zkVerify submit script
    │   ├── circuit_depth*.bin   ✅ Pre-built circuit binaries
    │   ├── Cargo.lock           ✅ Pinned deps
    │   └── rust-toolchain       ✅ Nightly pinned
    ├── website/
    │   ├── index.html           ⚠️  STALE — private repo has api2.js/app2.js with fixes not yet synced
    │   ├── catalog.html
    │   ├── js/api.js            ⚠️  STALE — api2.js has retry logic
    │   ├── js/app.js            ⚠️  STALE — app2.js has async render + ZK polling fixes
    │   ├── js/event-handlers.js
    │   ├── js/renderer.js
    │   ├── js/state.js
    │   ├── package.json
    │   └── llms.txt
    └── docs/
        ├── admin.md             ✅ Full operator guide
        ├── dependency-map.md     ✅ System dependencies
        ├── README.md
        └── ⚠️  Transient/dev: PRDs/, desloppify-mechanical-fix-plan.md,
            PROJ-rag-*.md, SECTION-*.md, KURIER_API.md
```

## What Needs Updating

1. **Website files stale** — api2.js/app2.js (CSS fix, image retry, async render, ZK diagnostics) committed to private `bc0563a` not in public
2. **Internal sync scripts** in `shared/` — `push_to_vps.sh`, `rollback_on_failure.sh`, `run_vps_health.sh`, `VPS-SYNC-PLAN.md`, `PROD-SYNC-PLAN.md` — should be excluded
3. **Scaffold tool** `scaffold_zkrag.py` at root — internal build artifact, not user-facing
4. **Project planning docs** — `PROJ*.md`, PRDs/, `SECTION-*.md`, `desloppify-*.md` — internal only
5. **`README_PUBLISH.md`** — internal scaffold instructions, not user documentation

## The Clean Output Should Contain

For a public release where someone can clone and build their own ZK-RAG system:

- ✅ All pipeline scripts (A–G) — sanitized
- ✅ Shared core (`api_server.py`, `embedding_service.py`, `provenance.py`, `x402_paid_download.py`, systemd units)
- ✅ ZK circuit source + pre-built binaries + `rust-toolchain`
- ✅ Website (HTML/JS/CSS) — current version with all fixes
- ✅ `docs/admin.md` — operator guide
- ✅ `docs/dependency-map.md` — system deps
- ✅ `.env.example` — clean placeholders
- ✅ `README.md` — landing page (already solid)
- ✅ `scan_leaks.py` — for post-clone verification
- ✅ Empty `data/` scaffold dirs
- ❌ NO internal sync scripts
- ❌ NO project planning docs
- ❌ NO scaffold tool
- ❌ NO `README_PUBLISH.md`

## Workflow

When updating the public repo after changes to the private repo:

1. Run `scaffold_zkrag.py` fresh from private repo → generates clean output
2. Manually delete: `README_PUBLISH.md`, `scaffold_zkrag.py`, project planning docs, internal sync scripts
3. Verify website files are current (check api2.js vs api.js, app2.js vs app.js)
4. Run `scan_leaks.py` on output — must pass clean
5. Commit and push

## Pitfalls

**Do NOT make arbitrary fixes to the VPS without explicit user direction.** Status checks are fine; operational changes require approval first. The scope of any task ends where user authorization ends.

## VPS Service Name

On the **VPS**: `zk-rag-api.service` (not `rag-api-vps.service`, not `rag-api.service`).
On the **R730**: `rag-api-local.service`.
Check with `systemctl list-units --type=service --state=running` if unsure.

## /health Endpoint Routing

FastAPI registers `/health` at the root path (not `/api/health`). Nginx has a catch-all `location /api/` that sends `/health` → backend `/health` → 404. Always check nginx proxy rules before assuming a route exists.
