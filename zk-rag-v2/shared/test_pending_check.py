#!/usr/bin/env python3
"""Simulate the pending logic for army branch, checking dedup for AMCPs."""
import json
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, '$REPO_DIR/scripts')

# Load registry
reg = json.load(open('$DATA_DIR/registry.json'))

# Build sha256_lookup from documents
sha256_lookup = {}
unified_lookup = {}
for entry in reg.get('documents', []):
    doc_id = entry.get('doc_id', '')
    sha256_lookup[doc_id] = doc_id  # doc_id IS the sha256
    unified_lookup[doc_id] = entry

# Simulate check_duplicate
def check_duplicate(title, pub_year, dedup_registry, threshold=0.75):
    if not title:
        return False, None, 0.0
    import difflib
    for existing_id, existing in dedup_registry.items():
        existing_title = existing.get('title', '')
        if not existing_title:
            continue
        score = difflib.SequenceMatcher(None, title.lower(), existing_title.lower()).ratio()
        if score >= threshold:
            return True, existing_id, score
    return False, None, 0.0

dedup_registry = {k: v for k, v in unified_lookup.items() if v.get('title')}

target_pdfs = [
    ('army', 'amcp-706-177-explosives-data-40def2ca.pdf'),
    ('army', 'amcp-706-180-explosive-behavior-215e98c0.pdf'),
    ('army', 'air-fountain-fm-21-76-us-army-survival-manual-50b5d5dd.pdf'),
]

for branch, fname in target_pdfs:
    pdf_path = Path(f'$DATA_DIR/source_pdfs/{branch}/{fname}')
    sha = hashlib.sha256(open(pdf_path, 'rb').read()).hexdigest()
    print(f"\n=== {fname} ===")
    print(f"  SHA256: {sha}")
    print(f"  SHA256 in sha256_lookup: {sha in sha256_lookup}")
    if sha in sha256_lookup:
        print("  -> DEDUP (SHA256)")
        continue
    
    # Title check
    raw_doc_id = fname.lower().replace('.pdf', '')
    doc_id_computed = "".join(c.lower() if c.isalnum() or c == "-" else "-" for c in fname.replace('.pdf', '')).strip("-")
    entry = unified_lookup.get(doc_id_computed)
    if not entry:
        for k, v in unified_lookup.items():
            if k == raw_doc_id or k.replace('-', '') == doc_id_computed.replace('-', ''):
                entry = v
                break
    if entry:
        title = entry.get('title', '')
        pub_year = entry.get('pub_year', 0)
        is_dup, dup_id, score = check_duplicate(title, pub_year, dedup_registry, threshold=0.75)
        print(f"  title: {title!r}")
        print(f"  dedup check: is_dup={is_dup}, dup_id={dup_id}, score={score:.2f}")
        if is_dup:
            print("  -> DEDUP (title)")
        else:
            print("  -> WOULD PROCESS")
    else:
        print(f"  NO REGISTRY ENTRY for doc_id={doc_id_computed}")
        print("  -> (would fail in batch_ingest_branch)")
