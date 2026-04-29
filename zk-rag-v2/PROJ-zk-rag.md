# ZK-RAG: Zero-Knowledge Retrieval-Augmented Generation
## Project Plan v1.0

---

## 1. Project Overview

### Purpose
Build a ZK-RAG system that proves—in zero-knowledge—that a language model was grounded in an authentic, untampered document set when generating a response. A verifier can check the proof without seeing the documents, the query, or the model weights.

### Core Insight
A Merkle tree root commits to the document corpus. A ZK proof demonstrates that the retrieved context chunks used in LLM generation are genuine members of that committed set—without revealing which documents, which chunks, or the query itself.

### Threat Model
- **Honest Prover**: Runs the full RAG pipeline and generates an honest proof
- **Malicious Prover**: Cannot forge proof of a document that was not in the committed corpus
- **Verifier**: Checks proof validity; learns nothing beyond "proof is valid"

---

## 2. Architecture

### 2.1 High-Level Data Flow

```
[Document Corpus]
       |
       v
[Chunk + Embed  ]  -->  [Merkle Tree Build]
       |                      |
       |                      v (MerkleCap root = 16 Poseidon hashes)
       |               [Publish root to Horizen EVM contract]
       |               (plain hash data; no ZK proof at this step)
       |
       v
[Query]
       |
       v
[Vector Search]  -->  [Top-K Chunks]
       |
       v
[LLM Generation]  (uses retrieved chunks as context)
       |
       v
[ZK Proof Generation]  (plonky2 STARK)
  - Prove each top-K chunk is in Merkle tree with this root
  - Prove LLM input includes those chunks
  - Output: (proof, public_inputs)
       |
       v
[Kurier / zkVerify]  (on-chain proof verification)
```

### 2.2 Merkle Tree Construction

**Leaf = PoseidonHash(chunk_text_bytes)**
- Each chunk's raw bytes are processed as field elements and hashed with Poseidon
- Chunks are sorted before building the tree (deterministic ordering by leaf hash)
- Tree uses Poseidon permutation internally (plonky2-compatible)

**Root**
- MerkleCap of height 4 → root consists of 16 Poseidon hash values
- The root (as 16 field elements) is the primary public input to the ZK proof

### 2.3 Circuit Design (plonky2 v0.2.2)

**Primary Circuit: `zk_rag_circuit`**

Public Inputs (PI):
1. `cap[16]` — MerkleCap (height 4) — the corpus root commitment
2. `output_hash` — Poseidon hash of the full LLM response text

Private Inputs (witness):
1. `chunk_data[i]` — bytes of the i-th retrieved chunk
2. `chunk_hash[i]` — Poseidon digest of chunk_data[i]
3. `merkle_proof[i]` — sibling digest path for chunk i
4. `chunk_index[i]` — integer index of chunk i in the Merkle tree

Constraints enforced in circuit:
- For each chunk i: `verify_merkle_proof_to_cap(chunk_hash[i], index_bits[i], cap, proof[i])`
- PoseidonHash(chunk_data[i]) == chunk_hash[i] (re-hash to confirm integrity)
- PoseidonHash(llm_full_input) == output_hash

**Note on text in plonky2 circuits**: plonky2 field elements are ~64-bit (Goldilocks). Text bytes are split into 64-bit limbs and fed to Poseidon. The circuit enforces that recomputed Poseidon matches the claimed leaf hash. No actual string comparison is needed inside the circuit.

### 2.4 Multi-chunk Proof (K=5)

- Up to K=5 retrieved chunks per query are supported
- Each chunk has its own Merkle proof path verified in the same circuit
- The set of chunk indices [i1..iK] is part of the public input (acceptable to reveal)
- LLM input hash commits to the full prompt (chunks + query), not to individual chunks

### 2.5 On-Chain Architecture: Two-Tier Model

ZK-RAG uses **two separate on-chain layers** for two separate jobs:

**Layer 1 — Commitment (Horizen Mainnet L3, at ingestion):**
- Full MerkleCap = 16 Poseidon hashes × 32 bytes each = 512 bytes per document
- Published once per document to `MerkleRootRegistry` contract on Horizen Mainnet
- The cap is plain data — no ZK proof involved, just a `push` to a smart contract
- As corpus grows: append new documents; old proofs remain valid against old roots
- This is the "I commit to this document's chunk set" signal

**Horizen Mainnet (Base) — Chain Details:**
| Parameter | Value |
|-----------|-------|
| Chain ID | 26514 |
| RPC (HTTPS) | https://horizen.calderachain.xyz/http |
| RPC (WS) | wss://horizen.calderachain.xyz/ws |
| Gas symbol | ETH |
| Token symbol | ZEN |
| Block Explorer | https://horizen.calderaexplorer.xyz/ |
| Bridge | https://horizen.hub.caldera.xyz/ |

**Layer 2 — Proof Verification (zkVerify / Kurier, at query time):**
- plonky2 STARK proof generated at query time (fastest to prove at query)
- Proof sent to Kurier → verified on zkVerify smart contract
- Verifier checks: "these K chunks exist in the Merkle tree with this root"
- The Merkle root was already published, so the proof only needs to prove membership

**Why this split:**
- Publishing a MerkleCap on Horizen L3 is cheap (gas is very low on this L3)
- Generating a ZK proof on Horizen EVM would be prohibitively slow/expensive
- Kurier/zkVerify is built exactly for fast on-chain STARK verification
- Two chains, two jobs — each optimized for its task

---

## 3. Technology Stack

| Component | Technology | Version | Notes |
|-----------|-----------|---------|-------|
| ZK Framework | plonky2 | 0.2.2 | Stark proof; Poseidon hash |
| Proof Aggregation | Recursion | plonky2 recursion | Nest proofs to compress final proof |
| Proof Verification On-chain | Kurier / zkVerify | — | On-chain STARK verification |
| Commitment Storage | Horizen Mainnet L3 (chain 26514) | Caldera | MerkleCap published via MerkleRootRegistry.sol |
| Cloud Proving | Local | CLI | CPU-based plonky2 proving for development |
| Off-chain Verifier | kurier.xyz | API | Lightweight verification endpoint |
| Rust Tooling | cargo, rustc | stable | Local dev and testing |
| Embedding Model | sentence-transformers | — | Local CPU embedder for corpus |
| Vector DB | Qdrant | — | Already deployed; hosts corpus embeddings |
| LLM | Local LLM via Ollama | — | Grounded generation |

### 3.2 Kurier API Integration

Lightweight proof verification service.

```bash
curl -X POST https://api.kurier.xyz/verify \
  -H "Content-Type: application/json" \
  -d '{"proof": {...}, "public_inputs": [...]}'
```

**OPEN QUESTION**: Kurier base URL and exact endpoint format need confirmation from `docs.kurier.xyz`. The structure above is illustrative.

---

## 4. Portability and System Setup

### 4.1 Environment Configuration

All system-specific values are driven by environment variables. Create a `.env` file in the project root:

```bash
# Required
CHUNKS_DIR=/data/rag/chunks

# Qdrant (PRD-03, PRD-04)
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=military_docs

# Ollama LLM (PRD-04)
OLLAMA_MODEL=llama3
OLLAMA_BASE_URL=http://localhost:11434

# Kurier verification (PRD-06) — TODO: confirm base URL from docs.kurier.xyz
KURIER_BASE_URL=https://api.kurier.xyz
KURIER_API_KEY=        # optional; leave empty for local-only verification
KURIER_CIRCUIT_ID=zk-rag-v1

# ZK Circuit (PRD-02)
ZK_MAX_CHUNK_LIMBS=8192       # max field limbs per chunk text
ZK_MAX_LLM_INPUT_LIMBS=16384  # max field limbs for full prompt
ZK_MAX_LLM_OUTPUT_LIMBS=8192 # max field limbs for LLM output
ZK_TREE_HEIGHT=8             # Merkle tree height (must match corpus build)
ZK_MERKLE_CAP_HEIGHT=4       # MerkleCap height
ZK_MAX_K=5                   # max chunks per proof
```

### 4.2 Directory Layout

A fresh system must create these directories before running any component:

```bash
zk-rag/
├── .env                    # environment variables (above)
├── Cargo.toml
├── src/
│   ├── circuits/
│   ├── corpus/            # output: corpus_merkle_tree.json, corpus_merkle_proofs.json
│   ├── data/              # chunk files symlinked or copied from CHUNKS_DIR
│   └── target/            # cargo build output
├── configs/
│   └── circuit_config.json
└── scripts/
    └── build_corpus.sh
```

On a fresh system:
1. Clone the repo
2. Copy `.env.example` → `.env` and fill in values
3. Create `src/`, `corpus/`, `configs/` directories
4. Link or copy chunk files to the directory referenced by `CHUNKS_DIR`
5. Run `cargo build` — Rust toolchain required (see `rust-toolchain` file)
6. Run each PRD phase in order (Phase 1 → Phase 2 → Phase 3 → ...)

### 4.3 Portability Notes

- **Chunk paths**: Never hardcode `/data/rag/chunks/` in source code — always read from `CHUNKS_DIR` env var
- **Corpus output**: The Merkle tree build output (`corpus_merkle_tree.json`, `corpus_merkle_proofs.json`) is portable across machines as long as `CHUNKS_DIR` and the chunk content are identical
- **plonky2 version**: Pinned to `v0.2.2` in `Cargo.toml` — changing the version will break circuit compatibility
- **Proof compatibility**: A proof generated on one machine only verifies against the same `CircuitData` (common + verifier_only JSON) — these must be published alongside proofs
- **Kurier**: The `circuit_id` in Kurier must match the `circuit_id` used during proof submission — confirm with Kurier documentation

---

## 5. Reference Implementations

### Ralph Implementation Guide

This section tells Ralph exactly what to do during the Ralph loop. Follow phases in order. Copy means clone verbatim; modify means adapt for this project's data structures; write means implement new code.

---

#### Phase 1 Tasks (PRD-01: Merkle Corpus)

**ALREADY DONE — do not overwrite:**
- `src/circuits/merkle.rs` — Fully implemented (build, get_merkle_proof, cap, verify_merkle_proof, root, index_to_bits, tree_depth). Built from scratch using Hashcloak/0xPolygonZero patterns as reference. Do NOT copy from tutorial — this file is complete and tested (9/9 tests passing).
- `src/circuits/mod.rs` — Module declarations for merkle + zk_rag submodules. Already present.
- `src/lib.rs` — Re-exports of plonky2 types (Field, HashOut, CircuitBuilder, CircuitData, etc.). Already present.

**Still to implement (PRD-01 remaining work):**
- `src/corpus.rs` — `CorpusMerkleStore` struct and `build_corpus_merkle_tree(chunks_dir) -> CorpusMerkleStore`
  - Load all `.txt` files from `CHUNKS_DIR` (env var)
  - Sort chunks deterministically by `PoseidonHash(chunk_bytes)` before building tree
  - Produce `cap: Vec<HashOut<F>>` (length = `2^TREE_HEIGHT / 2^MERKLE_CAP_HEIGHT` — default 16)
  - Produce `corpus_merkle_proofs.json` — map of `doc_id:chunk_index -> {index, siblings[]}`
- `tests/test_corpus.rs` — TDD tests FIRST (write failing tests for CorpusMerkleStore before implementing). Cover empty, single, power-of-2, non-power-of-2 chunk counts.
- `src/commands.rs` — CLI command `zk-rag build-tree --output corpus_merkle_tree.json` (can be added after corpus.rs is solid)

**Env vars used**: `CHUNKS_DIR`, `ZK_TREE_HEIGHT`, `ZK_MERKLE_CAP_HEIGHT`

---

#### Phase 2 Tasks (PRD-02: ZK-RAG Circuit)

**ALREADY DONE — do not overwrite:**
- `src/circuits/zk_rag.rs` — stub only; circuit implementation is the core work for this phase.

**Reference the plonky2 Merkle tree tutorial for API patterns only** (do not copy the file):
- `circuit_tutorials/plonky2/merkle_tree/circuit/src/lib.rs` — use as API reference for `CircuitBuilder`, `PartialWitness`, `prove()`, `verify()` patterns
- The ZK-RAG circuit is purpose-built for ZK-RAG constraints; it is NOT a derivative of the tutorial circuit

**Implement from scratch using plonky2 v0.2.2 API patterns:**
- The ZK-RAG circuit is purpose-built; do NOT copy the tutorial file. Use the tutorial only for API reference.
- **CONFIRM BEFORE BUILDING**: `CircuitBuilder::verify_merkle_proof_to_cap` — this plonky2 stdlib method may not exist in v0.2.2. The Sindri tutorial uses manual per-level hashing instead. Check `plonky2/src/plonk/circuit_builder.rs` and fall back to manual hashing if needed.
- Use `builder.hash_n_to_hash_no_pad::<PoseidonPermutation>()` for text-to-hash (confirmed plonky2 API)
- Use `builder.connect()` for equality constraints

**TDD approach — write tests FIRST:**
1. `tests/test_zk_rag_circuit.rs` — write failing test: build K=2 circuit, prove with known witness, verify. The test defines the expected API.
2. Then implement `src/circuits/zk_rag.rs` — `ZkRagCircuit`, `ZkRagWitness<K>`, `ChunkWitness`, `build_zk_rag_circuit::<K>()`
3. Then implement `src/witness.rs` — `assemble_witness()` — combines retrieval output + LLM output into `ZkRagWitnessInput`
4. Then implement `src/prove.rs` — `prove_locally(witness)` → `ProofWithPublicInputs`
5. Then implement `src/verify.rs` — `verify_local(proof, circuit_data)` using `CircuitData::verify()`
6. `src/serialization.rs` — serialize `CircuitData` and `ProofWithPublicInputs` to JSON

**Constants** (must match across all phases):
```
ZK_MAX_K = 5              // max chunks per proof
ZK_TREE_HEIGHT = 8       // must match corpus build
ZK_MAX_CHUNK_LIMBS = 8192
ZK_MAX_LLM_INPUT_LIMBS = 16384
ZK_MAX_LLM_OUTPUT_LIMBS = 8192
```

---

#### Phase 3 Tasks (PRD-03 + PRD-04: RAG + LLM Integration)

**Copy from existing codebase:**
- Use existing Qdrant client at the existing Qdrant endpoint
- Use existing Ollama client at the existing Ollama endpoint

**Write new:**
- `src/rag.rs` — `retrieve_chunks_with_proofs(query, k, corpus_store) -> Vec<RetrievedChunk>`
  - Embed query via Ollama `embeddings` API → get embedding vector
  - Query Qdrant with `top_k=k` → get `chunk_id` payloads
  - For each `chunk_id`: load chunk text from `{CHUNKS_DIR}/{doc_id}_{chunk_index}.txt`
  - Attach Merkle proof from `corpus_merkle_proofs.json`
- `src/llm.rs` — `generate_with_ollama(query, chunks, model) -> String`
  - Build prompt from query + chunks (format documented in PRD-04)
  - Call `POST ${OLLAMA_BASE_URL}/api/generate`
  - Return raw response text
- `tests/test_rag.rs`, `tests/test_llm.rs`

**Env vars used**: `CHUNKS_DIR`, `QDRANT_URL`, `QDRANT_COLLECTION`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`

---

#### Phase 4 Tasks (PRD-06: Kurier Verification)

**Write new:**
- `src/kurier.rs` — `verify_via_kurier(proof_json, public_inputs_json) -> Result`
- Local verify is already done in Phase 2 (`src/verify.rs`)
- Kurier verify: `POST ${KURIER_BASE_URL}/verify` with proof + public inputs
- Graceful fallback: if `KURIER_API_KEY` is empty, use local-only

**Env vars used**: `KURIER_BASE_URL`, `KURIER_API_KEY`, `KURIER_CIRCUIT_ID`

---

#### Phase 5 Tasks (PRD-07 + PRD-08: Aggregation + E2E)

**Write new:**
- `src/aggregation.rs` — `build_aggregation_circuit()`, `prove_aggregation()`, `verify_aggregation()`
- `tests/test_e2e.rs` — full pipeline test with small corpus (10 chunks)
- `tests/test_benchmark.rs` — benchmark proving time with corpus sizes [100, 1000]

**Verify plonky2 recursion APIs** before implementing — see PRD-07 Section 6 for the exact files and methods to confirm.

---

### plonky2 v0.2.2 (github.com/0xPolygonZero/plonky2)

| File | Purpose |
|------|---------|
| `plonky2/examples/fibonacci_serialization.rs` | Basic circuit + JSON serialization of proof/circuit data |
| `plonky2/src/hash/merkle_tree.rs` | `MerkleTree`, `MerkleCap<F,H>`, `cap.flatten()` |
| `plonky2/src/hash/merkle_proofs.rs` | `MerkleProof`, `verify_merkle_proof_to_cap()` |
| `plonky2/src/plonk/circuit_builder.rs` | `CircuitBuilder`, `add_virtual_hash_public_input()`, `register_public_input()`, `build()` |
| `plonky2/src/plonk/circuit_data.rs` | `CircuitData::prove()`, `CircuitData::verify()`, `PartialWitness` |

Key plonky2 patterns:
```rust
// Define circuit
type C = PoseidonGoldilocksConfig;
type F = <C as GenericConfig<D>>::F;
const D: usize = 2;

let config = CircuitConfig::standard_recursion_config();
let mut builder = CircuitBuilder::<F, D>::new(config);

// Public inputs
let cap_hash = builder.add_virtual_hash_public_input();
builder.register_public_input(cap_hash);

// Circuit constraints
let leaf_data = builder.add_virtual_targets(n);  // chunk bytes as field elements
// ... build constraints ...

// Build and prove
let data = builder.build::<C>();
let pw = PartialWitness::new();
pw.set_target(cap_hash, cap_value);
let proof = data.prove(pw)?;
data.verify(proof)?;

// Serialize
serde_json::to_string(&proof)?;
serde_json::to_string(&data.common)?;  // for verifier
```

### plonky2 Merkle Tree Tutorial (Reference Implementation)
**Repository**: `github.com/Sindri-Labs/sindri-resources`
**Path**: `circuit_tutorials/plonky2/merkle_tree/` (merged PR #98 by Roee-87, Sep 2024)

Canonical working plonky2 v0.2.2 example. Use as the reference for circuit structure and proving API.

| File | Purpose |
|------|---------|
| `circuit_tutorials/plonky2/merkle_tree/circuit/src/lib.rs` | `MerkleTreeCircuit::prove()` — full plonky2 proving pipeline |
| `circuit_tutorials/plonky2/merkle_tree/circuit/src/merkle_tree.rs` | `MerkleTree::build()`, `MerkleTree::get_merkle_proof()`, `verify_merkle_proof()` |
| `circuit_tutorials/plonky2/merkle_tree/circuit/sindri.json` | plonky2 circuit manifest — `plonky2Version: "0.2.2"`, `circuitType: "plonky2"` |
| `circuit_tutorials/plonky2/merkle_tree/input_1024.json` | Example input with 1024 leaves + index |

**Key code patterns from the tutorial:**
```rust
// ciruit/src/lib.rs — proving with plonky2
let tree: MerkleTree = MerkleTree::build(leaves.clone());
let merkle_proof = tree.clone().get_merkle_proof(prove_leaf_index);
let (circuit_data, targets) = verify_merkle_proof_circuit(prove_leaf_index, nr_layers);

let mut pw = PartialWitness::new();
pw.set_hash_target(targets[0], tree.tree[0][prove_leaf_index]);
for i in 0..nr_layers {
    pw.set_hash_target(targets[i + 1], merkle_proof[i]);
}
let proof_with_pis = circuit_data.prove(pw).unwrap();
data.verify(proof_with_pis.clone()).unwrap();

// sindri.json manifest
{
  "name": "merkle_tree_circuit",
  "circuitType": "plonky2",
  "plonky2Version": "0.2.2",
  "provingScheme": "plonky2",
  "structName": "merkle_tree::MerkleTreeCircuit"
}
```

The tutorial uses a direct Poseidon hashing approach (not the higher-level `verify_merkle_proof_to_cap` from plonky2's stdlib) — the circuit manually hashes leaf + siblings per level using `PoseidonHash::two_to_one()`.

### Hashcloak plonky2 Merkle Trees (Merkle implementation source)
**Repository**: `github.com/hashcloak/plonky2-merkle-trees`
**Path**: `src/simple_merkle_tree/simple_merkle_tree.rs`

The `MerkleTree::build()` and `MerkleTree::get_merkle_proof()` code in the plonky2 Merkle tree tutorial was cloned from this repo. The Hashcloak implementation is the authoritative source for the Merkle tree construction algorithm used.

### Kurier
- Docs: `docs.kurier.xyz` — API format unconfirmed
- Base URL: `api.kurier.xyz` — unconfirmed; structure shown in PRD-06 is illustrative
- Verifies proofs without running full plonky2 verifier locally

---

## 6. Project Structure

```
zk-rag/
├── Cargo.toml
├── src/
│   ├── lib.rs
│   ├── circuits/
│   │   ├── mod.rs
│   │   ├── merkle.rs      # Merkle tree construction + cap serialization
│   │   └── zk_rag.rs      # Main ZK-RAG circuit definition
│   ├── prove.rs           # Proof generation (local)
│   ├── verify.rs          # Verification (local + Kurier)
│   ├── embed.rs           # Chunking + embedding (reuse Qdrant pipeline)
│   └── cli.rs             # CLI: build-tree, prove, verify
├── circuits/
│   └── tests/
│       └── merkle_tests.rs
├── configs/
│   └── circuit_config.json
└── scripts/
    └── build_corpus.sh
```

---

## 7. Security Considerations

1. **Corpus Immutability**: Once root is published, corpus is append-only. Rebuilding with different docs produces a different root—old proofs become invalid.
2. **Chunk Sorting**: Sort chunks deterministically (by `PoseidonHash(chunk_bytes)`) before building tree. Non-deterministic ordering breaks verification.
3. **Query Privacy**: Query text is NOT hidden from the verifier. Only document contents and the full LLM input are private.
4. **Merkle Path Leakage**: Revealing sibling hashes for a chunk does not reveal other corpus chunks—Merkle trees are information-theoretically secure.
5. **No Private Keys**: Pure proving system; no signatures or secret keys involved.
6. **ZK Proof Soundness**: plonky2 with Poseidon is a proof system with extractable witness—anyone with the proof can extract the private inputs. This is standard for STARKs; do not confuse with FHE or secure multi-party computation.

---

## 8. Project Phases

### Phase 1: Foundation — Merkle Corpus + Embedding
- Build Merkle tree from existing chunked documents in `/data/rag/chunks/`
- Use existing Qdrant vector DB for top-K retrieval
- Extract and store the MerkleCap (root) for the corpus
- **Publish Merkle root to Horizen EVM contract** (one root per corpus snapshot; append new roots as corpus grows)
- Verify that retrieval returns chunks that map to valid Merkle proofs

### Phase 1b: Updated PRD ✅
- **Completed**: 2026-04-15
- **Output**: `mil-docs-pipelines/PRD-zk-rag-v2.md` — full v2 specification
- **Copied codebase**: `mil-docs-pipelines/zk-rag-v2/` (exact copy of Phase 1 working codebase)
- **Original codebase**: `$REPO_DIR/scripts/zk-rag/` — untouched, 39/39 tests passing
- **PRD scope**: confirmed plonky2 API surface, full circuit spec (including LLM input hash constraint gap), improved limb allocations (512→2048/1024→8192/512→4096), two-phase workflow, EVM contract interface, Ralph implementation guide
- **Key gap identified in Phase 1 code**: `llm_input_hash` is a public input but the circuit does not constraint the LLM input text to produce that hash. The witness sets it but the circuit doesn't enforce it. v2 circuit fixes this.
- **Another key gap**: Corpus build hashes unpadded bytes as leaves; circuit hashes padded limbs. Both must pad identically for consistency. v2 fixes this in `build_corpus_merkle_tree_v2()`.

### Phase 2: ZK Circuit — plonky2 Implementation ✅ (Phase 1 Model A COMPLETE)
- **Completed**: 2026-04-20 — commit `4fff012`
- **Location**: `zk-circuit/` (workspace root, `youruser1/document-rag-with-zk.git`)
- **What works**: Single-document, single-chunk Merkle proof circuit (plonky2 v0.2.2)
  - Public inputs: `merkle_root`, `document_hash`, `chunk_hash`
  - Private witnesses: Merkle proof siblings + index bits
  - `MAX_DEPTH = 12` (supports 4096-leaf trees for 4000 chunks)
  - Binary: `prove-bin/` — `prove <input.json>` → base64-encoded proof + circuit data
  - Python CLI: `prove-chunks.py <doc_id> <chunk_index>` — reads Merkle tree JSON, calls prove-bin, logs to `zk_proofs/prove-chunks.log`
  - 9/9 circuit tests passing (synthetic + wrong-proof negative tests)
  - E2E verified: 6 real documents from Qdrant, proving times 115–408ms
- **Still needed**: Phase 1 Model B (multi-chunk aggregation for K>1 chunks)

### Phase 3: Local Proving
- Local plonky2 proof generation using `plonky2::plonk::circuit_data::CircuitData::prove()`
- Unit tests with small synthetic corpus
- Compare local proof output against reference tutorial proof (compatibility check)

### Phase 4: Verification Layer — Kurier / zkVerify Integration ✅ (2026-04-23)

**Two-button search design implemented** — clean separation between plain search and provenance search.

**Architecture:**
- "Search" button → `POST /api/query` → plain RAG results, no ZK UI
- "Search with Provenance" button → `POST /api/query-provable` → RAG results + ZK proof buttons immediately visible

**Endpoints removed (2026-04-23):**
- `POST /api/provenance/generate` — old two-step generate flow
- `GET /api/provenance/{chunk_id}` — old stateful retrieval
- `GET /api/provenance/manifest` — unused
- `GET /api/query_stats` — unused
- `GET /api/provenance/{chunk_id}/status` — old stateful polling
- `_provenance_jobs`, `_provenance_lock` — server-side state removed

**Endpoints added (2026-04-23):**
- `POST /api/provenance/submit` — accepts `{ proof_hex, public_inputs_hex, vk_hex }`, returns `{ job_id }` immediately
- `GET /api/provenance/status/{job_id}` — polls Kurier, returns `{ job_id, status, verified, message, explorer_url }`

**Website UX:**
- ZK proof buttons appear immediately after provenance search (no async generation phase)
- "Verify on Chain" → submits to Kurier, polls status, enables Results button
- Results button: grayed out while `pending`, shows verification result modal when complete

**Local verification:** `verify-zk-proof` binary (`zk-circuit/verify-zk-proof/src/main.rs`) — standalone, uses plonky2-verifier public API.

### Phase 5: E2E Integration + Benchmarking
- Connect full RAG pipeline: embed → retrieve → LLM → proof → verify
- End-to-end test with real query and corpus
- Benchmark local CPU proving times with small corpus

---

## 9. Implementation Log

### 2026-04-02 — Phase 1 Build Start

**US-001: Cargo Skeleton ✅**
- All files created: Cargo.toml, lib.rs, circuits/mod.rs, stubs, .env.example
- **Decision: Rust nightly required** — plonky2 v0.2.2 uses `#![feature(specialization)]`. Changed `rust-toolchain` from `stable` to `nightly`.
- Compiles clean on `rustc 1.96.0-nightly (2026-04-01)`

**US-002: Merkle Tree Implementation ✅** (10/10 tests passing)
- **Decision: Use plonky2's native MerkleTree** — v0.2.2 ships `MerkleTree::new()`, `.prove()`, and `verify_merkle_proof_to_cap()` on CircuitBuilder. No need to reimplement from Hashcloak/Sindri. Wrapping instead.
- **Decision: No Poseidon2** — plonky2 v0.2.2 does NOT include Poseidon2 (grep confirmed zero hits). All hashing uses PoseidonHash throughout. The Grok supplementary doc recommended Poseidon2 but it's not available in this version.
- **Decision: 7-byte packing** for bytes→field elements (56 bits per element, safely within Goldilocks modulus 2^64 - 2^32 + 1). Original PRD suggested 64-bit limbs which would risk overflow.
- **Decision: Cap height clamping** — `build()` auto-reduces cap height when tree is too small (e.g., 2 leaves can't support cap_height=4). Prevents runtime panics.
- **US-003 (Merkle tests)** — absorbed into merkle.rs inline tests rather than separate test file. All 10 tests cover: build (2/3/16 leaves), prove+verify all indices, wrong leaf/index rejection, byte encoding roundtrip, hash determinism, empty panic.

**US-004: Corpus Store ✅** (9 tests passing)
- `load_chunk_texts()` — loads .txt files, sorted by filename
- `build_corpus_merkle_tree()` — hash chunks with Poseidon, sort by hash, build tree, generate all proofs
- `save()` / `load_corpus_json()` / `load_proofs_json()` — JSON serialization roundtrip
- Tests: loading, empty/missing errors, 16-chunk build, deterministic rebuild, sort verification, proof verification, JSON roundtrip

**US-005 + US-006: ZK-RAG Circuit ✅** (5 tests passing)
- `build_zk_rag_circuit()` → `ZkRagCircuitTargets` with K=5 chunk slots
- `fill_zk_rag_witness()` — fills partial witness from tree + leaves + text
- Uses `verify_merkle_proof_to_cap` natively from plonky2 (not manual per-level hashing)
- **Decision: Reduced limb sizes** — 8192/16384/8192 → 512/1024/512 to keep circuit practical. Can scale up later.
- **Critical: Leaf padding** — tree leaves MUST be padded to ZK_MAX_CHUNK_LIMBS before tree build, otherwise in-circuit hash_or_noop produces different hash than tree hash. Both sides must hash identical padded data.
- Unused chunk slots filled with first valid leaf's data to satisfy constraints.

**US-007: Witness Assembly ✅** (6 tests passing)
- `ZkRagWitnessInput` + `ChunkWitness` data structures
- `assemble_witness()` — bridges RAG pipeline output to circuit witness
- `text_to_field_limbs()` / `compute_text_hash()` — encoding utilities
- Error handling: too many chunks, text overflow, no chunks

**US-008: Prove + Verify ✅** (6 tests passing)
- `build_circuit()` → cached `ZkRagCircuit` (~2s, reuse for multiple proofs)
- `prove_locally()` → fills witness + generates STARK proof
- `serialize_proof()` / `deserialize_proof()` — JSON roundtrip for proofs
- `verify_proof()` — in-memory verification
- `verify_proof_from_json()` — rebuilds circuit (deterministic) + verifies
- **Decision: No JSON transport of CircuitData** — plonky2 v0.2.2 CommonCircuitData doesn't impl Deserialize. Circuit builds are deterministic so verifier rebuilds. Proof JSON alone is portable.
- Tampered proofs correctly rejected ✅

**US-009: E2E Synthetic Test ✅** (3 tests passing)
- Full pipeline: create chunks on disk → corpus build → select chunks → prove → serialize → deserialize → verify
- Proof JSON size: ~324KB
- Prove time: ~2-3s (16-chunk corpus, K=3, debug build)
- Tampered proof rejection verified
- Single-chunk edge case verified

**Key API surface confirmed in plonky2 v0.2.2:**
- `MerkleTree::new(leaves, cap_height)` ✅
- `MerkleTree::prove(leaf_index)` → `MerkleProof` ✅
- `verify_merkle_proof_to_cap(leaf_data, index, cap, proof)` ✅ (both standalone fn and CircuitBuilder method)
- `CircuitBuilder::hash_or_noop()` and `hash_n_to_hash_no_pad()` ✅
- `CircuitBuilder::add_virtual_cap(cap_height)` ✅
- `CircuitBuilder::verify_merkle_proof_to_cap::<H>()` ✅ (takes leaf_data, index_bits, cap, proof)

---

### 2026-04-15 — Pipeline F Debug + emit_all.py Fix

**Problem: Zero-chunk docs causing contract reverts**
- `emit_all.py` iterates all `_tree.json` files in `/data/rag/merkle_trees/` (697 total)
- 9 of them have `chunk_count = 0` (PDFs that failed the chunking pipeline)
- Contract `appendRoot()` reverts with `chunkCount must be > 0` on these
- Fix: added pre-check in `process_single_doc()` that skips zero-chunk docs as `[SKIP] reason=zero_chunk_count`

**Problem: State file out of sync with on-chain state**
- Early test runs (before auth was working) partially emitted docs to chain
- `emitted_roots.json` didn't track them → script re-tried them → `cap already emitted`
- Also caused confusion: "not authorized" errors were logged but later proved to be working
- Fix: manually corrected state file. Also clarified: `cap already emitted` = on chain, `not authorized` = auth issue

**Problem: "not authorized" errors on every doc**
- Root cause traced: `onlyAuthorized` modifier uses `msg.sender == owner()` — owner = DEPLOYER_KEY = `0xBABc60eD17e6387AEDab112E80744aA19EFCb723`
- Direct `cast call` with private key succeeds
- `forge script` was failing because env vars (`CONTRACT_ADDRESS`) weren't being passed to the subprocess properly
- Fixed by ensuring `env['PATH']` includes foundry bin in `run_append_root()`
- After fix: batch emit working, RC=0, on-chain entries confirmed

**On-chain state after session:**
- totalEntries: 9 (4 before today, 5 emitted today)
- emitted_roots.json: synced, 4 docs tracked
- 9 zero-chunk docs skipped
- ~679 docs remaining to emit

**emit_all.py current behavior:**
- `[SKIP] reason=already_emitted` — doc already on chain (tracked in state or cap already emitted)
- `[SKIP] reason=zero_chunk_count` — chunk_count == 0 in tree file
- `[EMIT] tx=unknown` — success, RC=0, tx broadcast
- `[FAIL] error=...` — contract revert or error

---

### Phase 1 Final Summary (2026-04-02)

**Status: ✅ COMPLETE — all 9 user stories delivered, 39/39 tests passing**

| Module | File | Tests | Purpose |
|--------|------|-------|---------|
| merkle | src/circuits/merkle.rs | 10 | Merkle tree build/prove/verify + byte encoding |
| corpus | src/corpus.rs | 9 | Chunk loading, Poseidon sort, JSON serialization |
| zk_rag | src/circuits/zk_rag.rs | 5 | plonky2 circuit: K=5 Merkle inclusion + text hashing |
| witness | src/witness.rs | 6 | Witness assembly from RAG pipeline output |
| prove | src/prove.rs | 3 | Circuit build, proof generation, serialization |
| verify | src/verify.rs | 3 | Proof verification (in-memory + JSON roundtrip) |
| e2e | tests/test_e2e_synthetic.rs | 3 | Full pipeline integration test |

**Key technical decisions:**
1. Rust nightly required (plonky2 v0.2.2 uses `#![feature]`)
2. PoseidonHash throughout (Poseidon2 not in v0.2.2)
3. plonky2 native MerkleTree API (not Hashcloak reimplementation)
4. 7-byte field element packing (56-bit, safe for Goldilocks)
5. Cap height auto-clamping for small trees
6. Reduced limb sizes (512/1024/512) for practical circuit size
7. Leaf padding mandatory — both tree and circuit must hash identical padded data
8. No CircuitData JSON transport — verifier rebuilds deterministic circuit

---

## 11. Smart Contract Operations — MerkleRootRegistry

### Contract Details
|| Field | Value ||
|-------|-------|-------|
| **Contract name** | `MerkleRootRegistry` ||
| **Language** | Solidity 0.8.24 ||
| **Source file** | `$REPO_DIR/pipeline_f/contracts/MerkleRootRegistry.sol` ||
| **Deploy script** | `$REPO_DIR/pipeline_f/script/Deploy.s.sol` ||
| **Emit script** | `$REPO_DIR/pipeline_f/emit_all.py` ||
| **Owner** | `0xBABc60eD17e6387AEDab112E80744aA19EFCb723` (matches DEPLOYER_KEY) ||

### Deployed Addresses
|| Chain | Chain ID | Contract Address ||
|-------|----------|-----------------||
| **Horizen Testnet** | 2651420 | `0x2E276196d82252aac48854bf1F044B095468A310` ||
| Horizen Mainnet | 26514 | TBD ||

### Contract Access Control
`appendRoot()` uses `onlyAuthorized` modifier:
```
msg.sender == owner() OR EnumerableSet.contains(_allowlist, msg.sender)
```
Owner (`0xBABc60eD17e6387AEDab112E80744aA19EFCb723`) has direct access. No separate REGISTRAR_ROLE — the owner IS the authorized caller.

### Env Vars (required)
```
DEPLOYER_KEY   # Private key — stored in $REPO_DIR/.env
OWNER          # Owner address — stored in $ZK_RAG_HOME/.env
RPC_URL        # https://horizen-testnet.rpc.caldera.xyz/http
```

### Deploying
```bash
cd $REPO_DIR/pipeline_f
source $REPO_DIR/.env
forge script script/Deploy.s.sol \
  --rpc-url $RPC_URL \
  --private-key $DEPLOYER_KEY \
  --broadcast -vvv
```

### Running Pipeline F
```bash
source $REPO_DIR/.env
cd $REPO_DIR/pipeline_f

# Dry run (simulates, no tx)
python3 emit_all.py --dry-run --batch

# Single doc test with debug
python3 emit_all.py --batch --limit 1 --debug

# Full batch emit (~679 docs remaining, ~20-30 min)
python3 emit_all.py --batch
```

### Key Paths
```
Contract address:   emit_all.py line 46 — CONTRACT_ADDRESS
Merkle tree input: /data/rag/merkle_trees/{doc_id}_tree.json
Registry (PDF hash): /data/rag/mil-docs-staging/registry-backup-20260401-220137/new-unified-registry-v2.json
State file:        /data/rag/merkle_trees/emitted_roots.json
Logs:              /data/logs/emit_all_debug_YYYYMMDD.log
                     /data/logs/emit_all_errors_YYYYMMDD.log
Broadcast output:  $REPO_DIR/pipeline_f/broadcast/AppendRoot.s.sol/2651420/
```

### On-Chain State (as of 2026-04-15)
- **totalEntries**: 9
- **emitted_roots.json**: 4 docs tracked (2 from before today + 2 from today)
- **Docs with zero chunks (skip)**: 9 (found in `/data/rag/merkle_trees/` — PDFs that failed chunking pipeline)
- **Docs remaining to emit**: ~679

### Bugs Found and Fixed

**1. Zero-chunk skip bug (fixed 2026-04-15)**
- `emit_all.py` iterated ALL `_tree.json` files including docs with `chunk_count = 0`
- Contract `appendRoot()` reverts if `chunkCount <= 0`
- Fix: added pre-check in `process_single_doc()` that skips zero-chunk docs with `[SKIP] reason=zero_chunk_count`
- 9 affected docs identified and skipped

**2. State sync issue (fixed 2026-04-15)**
- `emitted_roots.json` got out of sync with on-chain state during early test runs
- Some docs were emitted to chain but not recorded in state file
- When script re-processed them, contract reverted with `cap already emitted` (not `not authorized`)
- Fix: manually corrected state file entries marked `failed` → `emitted` where chain proved they succeeded
- Now: always run `--dry-run` first to catch sync issues before batch emit

**3. "not authorized" errors — explained**
- Early test runs failed with `MerkleRootRegistry: not authorized` — this was real auth failures
- Root cause: contract was being called before the deployer key was set as owner, OR before the contract was fully deployed
- After re-deployment on 2026-04-15: owner = DEPLOYER_KEY = `0xBABc60eD17e6387AEDab112E80744aA19EFCb723` — no longer an issue
- Contract access control: `onlyAuthorized` allows `owner()` directly — no role granting needed

### Known Issues / Limitations
- **tx_hash shows "unknown"**: Script doesn't parse forge's `Transaction hash:` output. Cosmetic — tx succeeds if RC=0.
- **MerkleCap padding**: Docs with <16 merkle_cap entries have zero-padded slots (expected behavior)
- **No on-chain deduplication feedback**: Contract reverts on duplicate cap with generic error — `emit_all.py` logs it but can't distinguish first-time vs duplicate emit without querying chain

---

## 10. Open Questions (for Mr. V)

1. **Kurier API**: Base URL and `/verify` endpoint format need confirmation from `docs.kurier.xyz`
2. **Initial corpus**: Is the corpus the existing military doctrine docs in `/data/rag/chunks/`?
3. ~~On-chain publication~~: ✅ Merkle root published to Horizen EVM contract at ingestion (ZEND); new roots appended per corpus snapshot. Proof verification via Kurier / zkVerify at query time.
4. **Corpus update cadence**: How often does corpus change? New corpus = new circuit or just new root?
5. **Performance targets**: Acceptable proving time bounds?
6. **ZK Circuit Authoring**: Should Ralph write the plonky2 circuit code during the Ralph loop?
7. ~~**Horizen EVM contract**~~: ✅ Resolved — see Section 11 below
