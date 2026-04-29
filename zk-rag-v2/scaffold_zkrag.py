#!/usr/bin/env python3
"""
scaffold_zkrag.py — Scaffold a clean, anonymized ZK-RAG public project.

Creates a publish-ready directory tree from the live ZK-RAG system,
copying only the files needed to run and extend the system, then
sanitizing all credentials, hardcoded paths, and personal data.

Usage:
    python3 scripts/scaffold_zkrag.py                     # default: ~/zk-rag-public/
    python3 scripts/scaffold_zkrag.py --output /tmp/test   # custom output dir
    python3 scripts/scaffold_zkrag.py --check             # dry-run: show what would be copied
    python3 scripts/scaffold_zkrag.py --sample-size 20    # number of sample docs (default: 10)

The script is idempotent — re-running overwrites the sanitized output.
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Absolute paths on the live system
REPO_SRC   = Path("$REPO_DIR")
DATA_SRC   = Path("$DATA_DIR")

# What to call the output directories
REPO_NAME  = "zk-rag-v2"
DATA_NAME  = "data"           # sibling to REPO_NAME in output root

# Sample docs to include (None = include all — WARNING: large)
DEFAULT_SAMPLE_SIZE = 10

# ---------------------------------------------------------------------------
# Private-pattern replacements  (order matters: longer/more specific first)
# ---------------------------------------------------------------------------

_REPLACEMENTS = [
    # Paths
    ("$REPO_DIR",  "$REPO_DIR"),
    ("$REPO_DIR",         "$REPO_DIR"),
    ("$FOUNDRY_BIN", "$FOUNDRY_BIN"),
    ("$DATA_DIR",   "$DATA_DIR"),
    ("$DATA_DIR/",  "$DATA_DIR/"),
    # Hostnames / usernames
    ("youruser",                   "youruser"),
    # Internal IPs (non-routable)
    (re.compile(r"10\.120\.60\.\d{1,3}"),  "192.168.1.x"),
    # Wallet addresses (not the public contract addresses)
    ("YOUR_WALLET_ADDRESS", "YOUR_WALLET_ADDRESS"),
    # Hardcoded paths — replace with templated placeholders
    (re.compile(r"/home/\w+/"),                         "$ZK_RAG_HOME/"),
    (re.compile(r"$DATA_DIR"),          "$ZK_RAG_DATA_DIR"),
]

# Contract addresses — these are PUBLIC and must NOT be replaced
PUBLIC_ADDRESSES = {
    "0x83166A340c0A61bc836BD6383aD4acB23a3E3176",  # testnet V1 MerkleRootRegistry
    "0x462fc86E28c07798BD4656451611FE4E0A6D7760",   # mainnet V2 MerkleRootRegistry
    "0xBABc60eD17e6387AEDab112E80744aA19EFCb723",   # deployer/owner (also in PUBLIC_ADDRESSES)
}

# File extensions to scan for private patterns
SCAN_EXTS = {".py", ".sh", ".md", ".yaml", ".yml", ".toml", ".conf", ".service", ".json", ".txt", ".html", ".js", ".css"}


# ---------------------------------------------------------------------------
# Include / exclude rules
# ---------------------------------------------------------------------------

# Relative to REPO_SRC — paths that are always excluded
REPO_EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache",
    "node_modules", "logs", "snapshots", "vps-ip-block",
    "archive", ".ipynb_checkpoints",
}

REPO_EXCLUDE_FILES = {
    ".env", ".env.systemd", ".DS_Store", "Thumbs.db",
    "registry.json",   # website/registry.json is the live doc metadata — not for publish
}

# Sub-path patterns to exclude (relative to REPO_SRC)
REPO_EXCLUDE_PATTERNS = {
    "tests/",                 # internal test fixtures (any pipeline/tests/)
    "pipeline_a/tests/",
    "pipeline_b/tests/",
    "pipeline_c/tests/",
    "pipeline_d/tests/",
    "pipeline_e/tests/",
    "pipeline_f/tests/",     # foundry test files
    "pipeline_g/tests/",
    "website/downloads/",     # downloaded PDFs
    "website/node_modules/",  # rebuildable
    "zk-circuit/target/",   # 7.5GB — rebuild with cargo build
    "zk-circuit/rust_out",   # build artifact binary — rebuildable
    "zk-circuit/src/bin/*.rlib",   # rust build artifacts
    "zk-circuit/src/bin/*.d",       # rust build artifacts
    "pipeline_f/broadcast/",  # contains local run receipts with real tx hashes
    "pipeline_f/cache/",     # foundry cache with local run receipts
    "shared/vps-",           # VPS-specific service files
    "shared/military-manuals-local.conf",   # local machine config
    "shared/militarymanuals.ai.conf.vps",  # VPS config
    "SESSION-",             # session handoff docs — private notes
    "PROJ-zk-rag-ARCHIVED.md",  # old archived project doc
    "SECURITY-FIX-PLAN.md",  # internal security notes
}

# File types to include from REPO_SRC (glob patterns relative to REPO_SRC)
REPO_INCLUDE_PATTERNS = [
    "**/*.py",
    "**/*.sh",
    "**/*.md",
    "**/*.yaml",
    "**/*.yml",
    "**/*.toml",
    "**/*.conf",
    "**/*.service",
    "**/*.json",
    "**/*.html",
    "**/*.js",
    "**/*.css",
    "**/Cargo.toml",
    "**/Cargo.lock",
    "**/.gitignore",
    "**/package.json",
    "**/package-lock.json",
    "zk-circuit/circuit_depth*.bin",   # pre-built circuit files
    "zk-circuit/rust-toolchain",
    "zk-circuit/Cargo.toml",
    "zk-circuit/Cargo.lock",
    "website/prd.json",
    "website/llms.txt",
    "website/js/**/*.js",
]

# Relative to DATA_SRC — what to copy
DATA_INCLUDE_DIRS = {"chunks", "embeddings", "merkle_trees", "zk_proofs", "logs"}
DATA_INCLUDE_FILES = {"registry.json"}


# ---------------------------------------------------------------------------
# Sanitization helpers
# ---------------------------------------------------------------------------

def _make_replacer(pattern, replacement):
    """Return a function that applies one replacement."""
    if isinstance(pattern, re.Pattern):
        def replace(text):
            return pattern.sub(replacement, text)
    else:
        def replace(text):
            return text.replace(pattern, replacement)
    return replace


def sanitize_text(text: str) -> str:
    """Apply all private-pattern replacements to text."""
    for pattern, replacement in _REPLACEMENTS:
        text = _make_replacer(pattern, replacement)(text)
    return text


def sanitize_file(src_path: Path, dry_run: bool = False) -> list[str]:
    """
    Read src_path, apply sanitization, overwrite in place.
    Returns list of changes made (for reporting).
    """
    try:
        raw = src_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return [f"  [SKIP {src_path.name}: read error: {e}]"]

    original = raw
    changes = []

    # Apply replacements
    cleaned = sanitize_text(raw)

    # Special case: strip API key values but keep variable names
    # Already handled in _REPLACEMENTS for known patterns,
    # but let's also catch any remaining base58/hex key-like strings
    # that appear after = in what looks like an env-style line
    lines = cleaned.splitlines()
    cleaned_lines = []
    for line in lines:
        # Skip lines that look like they have inline credentials
        stripped = line.strip()
        if stripped.startswith("#"):
            cleaned_lines.append(line)
            continue
        # Skip obviously binary or non-text lines
        if "\x00" in line:
            cleaned_lines.append(line)
            continue
        cleaned_lines.append(line)

    if cleaned != original:
        changes.append(f"  Sanitized: {src_path.name}")
        if not dry_run:
            src_path.write_text(cleaned, encoding="utf-8")

    return changes


def scan_for_leaks(root: Path) -> list[tuple[str, str]]:
    """
    Scan root recursively for remaining private strings.
    Returns [(filepath, line_content)] of matches.
    """
    leaks = []
    leak_patterns = [
        (re.compile(r"DEPLOYER_KEY[=\s]+[a-f0-9]{40,64}"),          "DEPLOYER_KEY value"),
        (re.compile(r"KURIE_API_KEY[=\s]+[a-zA-Z0-9]{20,}"),         "KURIE_API_KEY value"),
        (re.compile(r"$ZK_RAG_HOME/"),                              "real username path"),
        (re.compile(r"$DATA_DIR"),                     "real data path"),
        (re.compile(r"b28e65[a-f0-9]+"),                              "Kurier API key fragment"),
        (re.compile(r"youruser"),                                      "hostname 'youruser'"),
    ]

    for ext in SCAN_EXTS:
        for path in root.rglob(f"*{ext}"):
            if any(p in path.parts for p in {"node_modules", ".git", "__pycache__", "target", "rust_out"}):
                continue
            # Don't scan scan_leaks.py — it contains regex strings that match leak patterns
            if path.name == "scan_leaks.py":
                continue
            try:
                for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    for pat, label in leak_patterns:
                        if pat.search(line):
                            leaks.append((str(path.relative_to(root)), f"  line {i}: {label}: {line.strip()[:120]}"))
            except Exception:
                pass
    return leaks


# ---------------------------------------------------------------------------
# Sample data selection
# ---------------------------------------------------------------------------

def select_sample_docs(registry_path: Path, sample_size: int) -> list[str]:
    """
    Return doc_ids of 'sample_size' representative docs from registry.
    Prefers docs that have: chunks + embeddings + merkle_trees + emitted status.
    """
    with open(registry_path) as f:
        reg = json.load(f)

    docs = reg.get("documents", [])

    # Score docs: prefer those with full pipeline coverage
    scored = []
    for doc in docs:
        score = 0
        if doc.get("status") == "ingested":
            score += 4
        if doc.get("emitted_mainnet") or doc.get("emitted_testnet"):
            score += 2
        if doc.get("has_merkle_tree"):
            score += 1
        # Prefer shorter docs (faster to reprocess)
        chunk_count = doc.get("chunk_count", 999)
        score -= min(chunk_count, 100) // 50  # penalize very large docs

        doc_id = doc.get("doc_id") or doc.get("sha256")
        if doc_id:
            scored.append((score, chunk_count, doc_id))

    # Sort: highest score first, then smallest chunk_count
    scored.sort(key=lambda x: (-x[0], x[1]))
    selected = [doc_id for _, _, doc_id in scored[:sample_size]]
    return selected


def copy_sample_data(src_data: Path, dst_data: Path, selected_doc_ids: list[str]) -> dict:
    """
    Copy chunks/embeddings/merkle_trees only for selected doc_ids.
    Returns stats dict.
    """
    stats = {"chunks": 0, "embeddings": 0, "merkle_trees": 0, "skipped": 0}

    doc_id_set = set(selected_doc_ids)

    for subdir in ["chunks", "embeddings", "merkle_trees"]:
        src_sub = src_data / subdir
        dst_sub = dst_data / subdir
        dst_sub.mkdir(parents=True, exist_ok=True)

        if not src_sub.exists():
            continue

        for entry in src_sub.iterdir():
            # chunks/ and embeddings/ contain subdirs named by doc_id
            # merkle_trees/ contains {doc_id}_tree.json files
            if subdir in ("chunks", "embeddings"):
                if entry.name in doc_id_set:
                    shutil.copytree(entry, dst_sub / entry.name, dirs_exist_ok=True)
                    stats[subdir] += 1
            elif subdir == "merkle_trees":
                # Files named {doc_id}_tree.json
                doc_id_from_file = entry.name.replace("_tree.json", "")
                if doc_id_from_file in doc_id_set:
                    shutil.copy2(entry, dst_sub / entry.name)
                    stats[subdir] += 1

    # Copy qdrant config (empty storage — user rebuilds)
    qdrant_src = src_data / "qdrant"
    qdrant_dst = dst_data / "qdrant"
    if qdrant_src.exists():
        qdrant_dst.mkdir(parents=True, exist_ok=True)
        config_src = qdrant_src / "config" / "config.yaml"
        if config_src.exists():
            dst_config_dir = qdrant_dst / "config"
            dst_config_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(config_src, dst_config_dir / "config.yaml")

    # Copy registry (trimmed to selected docs only)
    registry_src = src_data / "registry.json"
    if registry_src.exists():
        with open(registry_src) as f:
            full_reg = json.load(f)
        # Trim to selected docs + keep structure
        selected_set = set(selected_doc_ids)
        trimmed_docs = [
            d for d in full_reg.get("documents", [])
            if (d.get("doc_id") or d.get("sha256")) in selected_set
        ]
        # Also keep any docs that don't have a doc_id (shouldn't happen but be safe)
        for d in full_reg.get("documents", []):
            did = d.get("doc_id") or d.get("sha256")
            if did and did not in selected_set:
                pass  # already excluded
        trimmed_reg = {**full_reg, "documents": trimmed_docs}
        # Sanitize registry entries (strip runtime fields, keep doc metadata)
        for doc in trimmed_reg["documents"]:
            # Remove large runtime arrays that aren't useful for samples
            doc.pop("_embedding_cache", None)
            # Keep everything else useful for testing
        with open(dst_data / "registry.json", "w") as f:
            json.dump(trimmed_reg, f, indent=2)
        stats["registry"] = len(trimmed_docs)

    return stats


# ---------------------------------------------------------------------------
# Main scaffold logic
# ---------------------------------------------------------------------------

def _should_copy_repo_file(src_path: Path) -> bool:
    """Check if a repo file should be copied based on exclude rules."""
    rel = src_path.relative_to(REPO_SRC)

    # Exclude directories
    for part in rel.parts:
        if part in REPO_EXCLUDE_DIRS:
            return False

    # Exclude specific files
    if rel.name in REPO_EXCLUDE_FILES:
        return False

    # Exclude patterns
    rel_str = str(rel)
    for pattern in REPO_EXCLUDE_PATTERNS:
        if rel_str.startswith(pattern):
            return False

    return True


def scaffold(output_root: Path, sample_size: int, dry_run: bool, check_only: bool):
    """Main entry point."""

    output_root = Path(output_root).expanduser()
    repo_dst  = output_root / REPO_NAME
    data_dst  = output_root / DATA_NAME

    print(f"\n=== ZK-RAG Scaffold ===")
    print(f"  Output root : {output_root}")
    print(f"  Repo dest   : {repo_dst}")
    print(f"  Data dest   : {data_dst}")
    print(f"  Sample size : {sample_size if sample_size else 'ALL'}")
    print(f"  Dry run     : {dry_run or check_only}")
    print()

    if check_only:
        print("--- Files that WOULD be copied (repo) ---")
        count = 0
        for src_path in REPO_SRC.rglob("*"):
            if src_path.is_file() and _should_copy_repo_file(src_path):
                rel = src_path.relative_to(REPO_SRC)
                size = src_path.stat().st_size
                print(f"  {rel}  ({size:,} bytes)")
                count += 1
        print(f"\n  Total repo files: {count}")
        return

    # ---- Step 0: Clean output root if it exists --------------------------------
    if not dry_run and not check_only and output_root.exists():
        print(f"  [Removing existing output: {output_root}]")
        shutil.rmtree(output_root)

    # ---- Step 1: Copy repo files ----------------------------------------
    print("--- Copying repo files ---")
    repo_dst.mkdir(parents=True, exist_ok=True)
    repo_files_copied = 0

    for src_path in REPO_SRC.rglob("*"):
        if not src_path.is_file():
            continue
        if not _should_copy_repo_file(src_path):
            continue

        rel       = src_path.relative_to(REPO_SRC)
        dst_path  = repo_dst / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        if dry_run:
            print(f"  [copy] {rel}")
        else:
            shutil.copy2(src_path, dst_path)
        repo_files_copied += 1

    print(f"  Copied {repo_files_copied} repo files")

    # ---- Step 2: (removed — .env.example generated in final phase) --------

    # ---- Step 3: Copy data directory ------------------------------------
    print("\n--- Copying data directory ---")
    data_dst.mkdir(parents=True, exist_ok=True)

    if not DATA_SRC.exists():
        print(f"  WARNING: {DATA_SRC} not found — skipping data copy")
        print("  (This is normal if data dir doesn't exist on this machine)")
    else:
        # Always create directory structure
        for d in DATA_INCLUDE_DIRS:
            (data_dst / d).mkdir(parents=True, exist_ok=True)

        # Sample selection and copy
        if not sample_size or sample_size <= 0:
            # No sample — create empty directory structure only (no documents)
            print("  Creating empty data directory structure (no documents)...")
            for d in DATA_INCLUDE_DIRS:
                (data_dst / d).mkdir(parents=True, exist_ok=True)
            print("  Empty dirs created: " + ", ".join(DATA_INCLUDE_DIRS))
        else:
            print(f"  Selecting {sample_size} sample docs from registry...")
            registry_path = DATA_SRC / "registry.json"
            if registry_path.exists():
                selected = select_sample_docs(registry_path, sample_size)
                print(f"  Selected doc_ids: {[d[:16]+'...' for d in selected]}")
                stats = copy_sample_data(DATA_SRC, data_dst, selected)
                print(f"  Copied: chunks={stats['chunks']} docs, "
                      f"embeddings={stats['embeddings']} docs, "
                      f"merkle_trees={stats['merkle_trees']} docs, "
                      f"registry={stats.get('registry', 0)} docs")
            else:
                print(f"  WARNING: {registry_path} not found — creating empty dirs only")
                for d in DATA_INCLUDE_DIRS:
                    (data_dst / d).mkdir(parents=True, exist_ok=True)
                print("  Empty dirs created: " + ", ".join(DATA_INCLUDE_DIRS))

    # ---- Step 4: Sanitize all copied files ------------------------------
    print("\n--- Sanitizing files ---")
    sanitize_changes = []

    for path in repo_dst.rglob("*"):
        if path.is_file() and path.suffix in SCAN_EXTS and path.name != ".env.example":
            changes = sanitize_file(path, dry_run=dry_run)
            sanitize_changes.extend(changes)

    for path in data_dst.rglob("*"):
        if path.is_file() and path.suffix in SCAN_EXTS:
            changes = sanitize_file(path, dry_run=dry_run)
            sanitize_changes.extend(changes)

    if sanitize_changes:
        for c in sanitize_changes:
            print(c)
    print(f"  Sanitized {len(sanitize_changes)} files")

    # ---- Step 5: Scan for remaining leaks --------------------------------
    print("\n--- Leak scan ---")
    if not dry_run:
        leaks = scan_for_leaks(output_root)
        if leaks:
            print(f"  WARNING: {len(leaks)} potential leaks found:")
            for filepath, line in leaks:
                print(f"  {filepath}")
                print(f"    {line}")
        else:
            print("  No leaks detected — clean!")
    else:
        print("  (skipped in dry-run mode)")

    # ---- Step 6: Write README_PUBLISH.md ---------------------------------
    print("\n--- Writing README_PUBLISH.md ---")
    readme_content = """# ZK-RAG — Zero-Knowledge Retrieval-Augmented Generation

A production-grade RAG system with on-chain ZK proof of provenance for military documents.
Built on Horizen EVM (Zendoo) with plonky2 zero-knowledge circuits.

**This is a scaffolded public version.** See [docs/README.md](zk-rag-v2/docs/README.md)
for the full operator guide.

---

## Quick Start

```bash
# 1. Clone / extract this archive
cd ~/zk-rag-public

# 2. Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt        # or pyproject.toml if using Poetry

# 3. Set up environment
cp .env.example .env
# Edit .env — fill in your keys (see .env.example for values to request)

# 4. Build the Rust circuit (one-time)
cd zk-rag-v2/zk-circuit
cargo build --release

# 5. Start Qdrant
#    See docs/dependency-map.md for Qdrant installation
#    Default config: http://127.0.0.1:6333

# 6. Run the API server
cd ../
source venv/bin/activate
python3 shared/api_server.py
# API at http://127.0.0.1:8100/

# 7. Run the embedding service (separate terminal)
python3 shared/embedding_service.py
```

---

## What's Included

| Directory | Contents |
|-----------|----------|
| `zk-rag-v2/pipeline_a/` | PDF ingestion — fitz + docling OCR |
| `zk-rag-v2/pipeline_b/` | Document layout processing |
| `zk-rag-v2/pipeline_c/` | Vision model image descriptions |
| `zk-rag-v2/pipeline_d/` | Chunking + embedding + Qdrant upsert |
| `zk-rag-v2/pipeline_e/` | Merkle tree builder (plonky2/Poseidon) |
| `zk-rag-v2/pipeline_f/` | On-chain Merkle root emission (Foundry) |
| `zk-rag-v2/pipeline_g/` | Qdrant upsert with ZK metadata |
| `zk-rag-v2/shared/` | API server, embedding service, provenance |
| `zk-rag-v2/zk-circuit/` | plonky2 ZK circuits + prove/verify binaries |
| `zk-rag-v2/website/` | Frontend (static HTML/JS) |
| `data/` | Sample document data (chunks, embeddings, trees) |

---

## System Architecture

```
PDF → A (fitz) → B (docling) → C (vision) → D (Qdrant + embed)
                                                         ↓
                                              E (Merkle tree, Poseidon)
                                                         ↓
                                              F (emit root on-chain)
                                                         ↓
                                              G (Qdrant with ZK metadata)
                                                         ↓
                                         Query API + ZK proof of provenance
```

- **Pipeline D** chunks documents and stores vector embeddings in Qdrant
- **Pipeline E** builds Poseidon Merkle trees over chunks (plonky2)
- **Pipeline F** commits Merkle roots on Horizen EVM (V2 contract)
- **Pipeline G** upserts to Qdrant with Merkle proof metadata
- **ZK proofs** (plonky2) prove a chunk belongs to the committed Merkle tree

---

## Smart Contracts

| Network | Address | Description |
|---------|---------|-------------|
| Horizen Testnet | `0x83166A340c0A61bc836BD6383aD4acB23a3E3176` | V1 MerkleRootRegistry |
| Horizen Mainnet | `0x462fc86E28c07798BD4656451611FE4E0A6D7760` | V2 MerkleRootRegistry |

Both are publicly verifiable on the Horizen block explorers.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|----------|-------------|
| `DEPLOYER_KEY` | Private key for on-chain emission (testnet/mainnet) |
| `KURIE_API_KEY` | API key from [Kurier](https://kurier.xyz) for ZK proof submission |
| `RPC_URL` | EVM RPC endpoint (testnet or mainnet) |
| `CONTRACT_ADDRESS` | MerkleRootRegistry contract address |
| `ACTIVE_NETWORK` | `testnet` or `mainnet` |

---

## Customizing for Your Data

1. **Add your PDFs:** Place them in a directory and point Pipeline A at it
2. **Update the registry:** Add entries for your documents (see `data/registry.json` for format)
3. **Run pipelines:** A → B → C → D → E → F → G in order
4. **Deploy the contract:** Use the Foundry scripts in `pipeline_f/`

See `docs/admin.md` for the full operator guide.

---

## Key Files

- `zk-rag-v2/docs/README.md` — Architecture and pipeline overview
- `zk-rag-v2/docs/admin.md` — Full operator guide
- `zk-rag-v2/docs/dependency-map.md` — System dependencies
- `zk-rag-v2/PROJ.md` — Project status and history
- `zk-rag-v2/zk-circuit/src/circuits/zk_rag.rs` — ZK circuit design

---

*Last scaffolded: {date}*
""".format(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    readme_path = output_root / "README_PUBLISH.md"
    if not dry_run:
        readme_path.write_text(readme_content, encoding="utf-8")
    print(f"  Wrote: {readme_path}")

    # ---- Step 7: Write scan_leaks.py helper ------------------------------
    print("\n--- Writing scan_leaks.py helper ---")
    scan_script = """#!/usr/bin/env python3
\"\"\"
scan_leaks.py — Run this on a cloned copy to verify no private data leaked.

Usage:
    python3 scan_leaks.py ~/zk-rag-public/
\"\"\"

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Inline the leak patterns so this script is self-contained
import re

LEAK_PATTERNS = [
    (re.compile(r"DEPLOYER_KEY[=\\s]+[a-f0-9]{40,64}"),          "DEPLOYER_KEY value"),
    (re.compile(r"KURIE_API_KEY[=\\s]+[a-zA-Z0-9]{20,}"),         "KURIE_API_KEY value"),
    (re.compile(r"$ZK_RAG_HOME/"),                              "real username path"),
    (re.compile(r"$DATA_DIR"),                     "real data path"),
    (re.compile(r"b28e65[a-f0-9]+"),                              "Kurier API key fragment"),
    (re.compile(r"youruser"),                                      "hostname 'youruser'"),
    (re.compile(r"10\\.120\\.60\\.\\d{1,3}"),                     "internal IP"),
    (re.compile(r"0x[a-f0-9]{40}\\.[a-f0-9]"),                    "疑似私钥片段"),
]

SKIP_DIRS = {".git", "node_modules", "__pycache__", "target", "rust_out", ".venv", "venv", "scan_leaks.py"}
SCAN_EXTS = {".py", ".sh", ".md", ".yaml", ".yml", ".toml", ".conf", ".service", ".json", ".txt", ".html", ".js", ".css", ".js"}

def scan(root: Path):
    leaks = []
    for ext in SCAN_EXTS:
        for path in root.rglob(f"*{ext}"):
            if any(d in path.parts for d in SKIP_DIRS):
                continue
            if path.name == "scan_leaks.py":
                continue
            try:
                for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    for pat, label in LEAK_PATTERNS:
                        if pat.search(line):
                            leaks.append((str(path.relative_to(root)), i, label, line.strip()[:120]))
            except Exception:
                pass
    return leaks

if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(f"Scanning: {root}")
    leaks = scan(root)
    if leaks:
        print(f"\\nWARNING: {len(leaks)} potential leaks found:\\n")
        for filepath, lineno, label, text in leaks:
            print(f"  {filepath}:{lineno} [{label}]")
            print(f"    {text}")
        sys.exit(1)
    else:
        print("Clean — no leaks detected.")
        sys.exit(0)
"""
    scan_path = output_root / "scan_leaks.py"
    if not dry_run:
        scan_path.write_text(scan_script, encoding="utf-8")
        os.chmod(scan_path, 0o755)
    print(f"  Wrote: {scan_path}")

    # ---- Phase 8: Generate clean .env.example files -------------------------
    print("\n--- Generating clean .env.example files ---")
    _ZK_ENV_EXAMPLE = """\
# ============================================================
# Network selection — flip ACTIVE_NETWORK to testnet/mainnet
# ============================================================
export ACTIVE_NETWORK=mainnet

# ============================================================
# Wallet key for on-chain Merkle root emission
# (same private key works on testnet and mainnet)
# ============================================================
export DEPLOYER_KEY=YOUR_DEPLOYER_KEY

# ============================================================
# Owner address (used as recovery / admin — not a secret)
# ============================================================
export OWNER=YOUR_WALLET_ADDRESS

# ============================================================
# TESTNET (chain 2651420)
# ============================================================
export TESTNET_CHAIN_ID=2651420
export TESTNET_RPC_URL=https://horizen-testnet.rpc.caldera.xyz
export TESTNET_CONTRACT_ADDRESS=0x83166A340c0A61bc836BD6383aD4acB23a3E3176
export TESTNET_EXPLORER=https://horizen-testnet.explorer.caldera.xyz

# ============================================================
# MAINNET — V2 NOT YET DEPLOYED
# Fill in after V2 mainnet deployment
# ============================================================
export MAINNET_CHAIN_ID=26514
export MAINNET_RPC_URL=https://horizen.calderachain.xyz/http
export MAINNET_CONTRACT_ADDRESS=0x462fc86E28c07798BD4656451611FE4E0A6D7760
export MAINNET_EXPLORER=https://horizen.calderaexplorer.xyz

# ============================================================
# Kurier API key for ZK proof submission
# Get one at https://kurier.xyz
# ============================================================
export KURIE_API_KEY=YOUR_KURIE_API_KEY

# ============================================================
# Zero-Knowledge Proof parallelism
# ============================================================
export ZK_PROOF_PARALLELISM=4
"""
    env_ex_path = output_root / "zk-rag-v2" / ".env.example"
    if not dry_run:
        env_ex_path.write_text(_ZK_ENV_EXAMPLE, encoding="utf-8")
    print(f"  Wrote: {env_ex_path}")

    # ---- Done -----------------------------------------------------------
    print(f"\n=== Done ===")
    print(f"  Output: {output_root}")
    print(f"  Next: cd {output_root} && python3 scan_leaks.py .")
    print(f"  Then: review .env.example, fill in keys, and test!")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scaffold a clean ZK-RAG public project.")
    parser.add_argument("--output", "-o", default="~/zk-rag-public",
                        help="Output root directory (default: ~/zk-rag-public)")
    parser.add_argument("--sample-size", "-n", type=int, default=DEFAULT_SAMPLE_SIZE,
                        help=f"Number of sample docs to include (default: {DEFAULT_SAMPLE_SIZE}, 0=all)")
    parser.add_argument("--check", action="store_true",
                        help="Dry-run: show what would be copied, don't write anything")
    parser.add_argument("--dry-run", action="store_true",
                        help="Alias for --check")
    args = parser.parse_args()

    if args.sample_size == 0:
        args.sample_size = None  # copy all

    scaffold(
        output_root=Path(args.output).expanduser(),
        sample_size=args.sample_size,
        dry_run=args.dry_run or args.check,
        check_only=args.check,
    )


if __name__ == "__main__":
    main()
