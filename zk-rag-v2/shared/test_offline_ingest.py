#!/usr/bin/env python3
"""Test offline ingest_pdf — no API server needed."""
import sys
sys.path.insert(0, '$REPO_DIR/scripts')
from pathlib import Path
from pdf_processing import ingest_pdf

test_pdf = Path('$DATA_DIR/source_pdfs/army/air-fountain-fm-21-76-us-army-survival-manual-50b5d5dd.pdf')
doc_id = test_pdf.stem.lower()
out_dir = Path('/tmp/test_ingested') / doc_id

print(f"Testing ingest_pdf on: {test_pdf.name}")
print(f"Output dir: {out_dir}")

result = ingest_pdf(
    pdf_path=test_pdf,
    doc_id=doc_id,
    out_dir=out_dir,
    images_base_dir=None,  # skip image extraction for test speed
    skip_ocr=True,  # fitz only
)
print(f"Result: page_count={result[0]}, needs_docling={result[1]}")

if out_dir.exists():
    pages = sorted((out_dir / "pages").glob("*.json"))
    print(f"Pages written: {len(pages)}")
    if pages:
        import json
        data = json.loads(open(pages[0]).read())
        print(f"First page text length: {len(data.get('text',''))} chars")
        print(f"OCR source: {data.get('ocr_source')}")
        print(f"Sample: {data.get('text','')[:80]!r}")
        manifest = json.loads(open(out_dir / "manifest.json").read())
        print(f"Manifest avg_chars: {manifest.get('avg_chars_per_page')}")
else:
    print("out_dir not created (needs_docling=True path)")
