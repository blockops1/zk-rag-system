#!/usr/bin/env python3
"""Direct test of ingest_pdf on the two failing AMCP PDFs."""
import json
import sys
sys.path.insert(0, '$REPO_DIR/scripts')
from pathlib import Path
from pdf_processing import ingest_pdf

test_pdfs = [
    Path('$DATA_DIR/source_pdfs/army/amcp-706-177-explosives-data-40def2ca.pdf'),
    Path('$DATA_DIR/source_pdfs/army/amcp-706-180-explosive-behavior-215e98c0.pdf'),
]

for pdf_path in test_pdfs:
    doc_id = pdf_path.stem.lower()
    out_dir = Path(f'$DATA_DIR/extracted/{doc_id}')
    print(f"\n=== Testing: {pdf_path.name} ===")
    print(f"doc_id: {doc_id}")
    print(f"out_dir: {out_dir}")
    try:
        page_count, needs_docling = ingest_pdf(
            pdf_path=pdf_path,
            doc_id=doc_id,
            out_dir=out_dir,
            images_base_dir=None,
            skip_ocr=True,
        )
        print(f"SUCCESS: page_count={page_count}, needs_docling={needs_docling}")
        # Count pages written
        pages = sorted((out_dir / 'pages').glob('*.json'))
        print(f"Pages written: {len(pages)}")
        if pages:
            data = json.loads(open(pages[0]).read())
            print(f"First page chars: {len(data.get('text',''))}")
    except Exception as e:
        import traceback
        print(f"ERROR: {e}")
        traceback.print_exc()
