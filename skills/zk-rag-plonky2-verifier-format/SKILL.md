---
name: zk-rag-plonky2-verifier-format
description: Serialize Plonky2 proofs for zkVerify on-chain verification
triggers:
  - plonky2 proof serialization
  - zkverify integration
  - on-chain proof verification
---

# ZK-RAG Plonky2 Verifier Format (zkVerify)

## Context
ZK-RAG outputs Plonky2 proofs that may need to be verified on-chain via zkVerify's `settlementPlonky2Pallet`. This skill documents the serialization format required.

## Statement Hash Components
```
context = keccak256(b"plonky2")
vk      = keccak256(vk.encode())
pubs    = keccak256(pubs)
final   = keccak256(context || vk || pubs)
```

## Serialization Steps

### 1. Verification Key
```rust
use plonky2_verifier::ZKVerifyGateSerializer;
let vk_bytes = data.verifier_data().to_bytes(&ZKVerifyGateSerializer)?;
```
- Requires `ZKVerifyGateSerializer` from `plonky2_verifier` crate
- Config preset: only `Keccak+Goldilocks` or `Poseidon+Goldilocks` supported
- VK JSON format: `{"config": "Poseidon", "bytes": "<hex>"}`

### 2. Proof
```rust
let mut proof_bytes = Vec::new();
proof_bytes.write_proof(&proof.proof)?;
```

### 3. Public Inputs (split from proof)
```rust
let mut pubs_bytes = Vec::new();
pubs_bytes.write_usize(proof.public_inputs.len())?;
pubs_bytes.write_field_vec(proof.public_inputs.as_slice())?;
```

Note: Plonky2 keeps Proof+Pubs in one struct; zkVerify requires them split.

## Relevant Source
- zkVerify Plonky2 verifier: https://github.com/zkVerify/zkVerify/tree/main/verifiers/plonky2
- Docs: https://docs.zkverify.io/architecture/verification_pallets/plonky2
- Plonky2 config.rs: https://github.com/0xPolygonZero/plonky2/blob/main/plonky2/src/plonk/config.rs

## Known: ZKVerifyGateSerializer IS Public

`ZKVerifyGateSerializer` is re-exported publicly from `plonky2_verifier` — confirmed working import:
```rust
use plonky2_verifier::ZKVerifyGateSerializer;
```
This compiles and works in `prove-bin` with plonky2 v0.2.2 + plonky2-verifier from zkVerify fork.

## Confirmed Working Serialization (prove-bin, 2026-04-20)

**Full working code in:** `zk-circuit/prove-bin/src/main.rs`

```rust
use plonky2::util::serialization::{DefaultGateSerializer, Write as P2Write};
use plonky2_verifier::ZKVerifyGateSerializer;

// Proof bytes (no public inputs embedded)
let mut proof_bytes = Vec::new();
proof_bytes.write_proof(&proof.proof)?;

// Public inputs: length prefix + field elements (split from proof per zkVerify format)
let mut pubs_bytes = Vec::new();
pubs_bytes.write_usize(proof.public_inputs.len())?;
pubs_bytes.write_field_vec(proof.public_inputs.as_slice())?;

// Verification key using ZKVerifyGateSerializer
let vk_bytes = data.verifier_data().to_bytes(&ZKVerifyGateSerializer)?;
```

**Trait name collision:** `plonky2::util::serialization::Write` and `std::io::Write` both have a `write` method. Rename with `use ... Write as P2Write` to disambiguate.

**Pipeline F:** Already emits using `write_proof` — format matches zkVerify.

## Critical: plonky2 Version Conflict
plonky2-verifier uses a forked plonky2 (v0.1.0 from zkVerify) which may be incompatible with plonky2 v0.2.2. See skill `zk-rag-plonky2-version-conflict` before attempting to mix plonky2-verifier with zk-circuit. The working prove-bin manages this by keeping the verifier crate usage isolated to serialization only.
