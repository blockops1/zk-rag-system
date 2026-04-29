"""
pdf_processing.py — Shared PDF extraction module.

Provides offline PDF processing (fitz + optional docling OCR) for Pipeline A and B.
No HTTP dependencies. Callable directly from batch scripts.

Functions:
    ingest_pdf()         — main entry point: PDF → per-page JSON files
    compute_sha256()     — file hash for dedup
    clean_page_text()    — strip headers/footers
    find_visual_refs()   — find Figure/Photo references
    _run_docling()       — subprocess docling OCR
    _extract_images()     — extract images from PDF pages

Usage (no API server needed):
    from pdf_processing import ingest_pdf
    page_count, needs_docling = ingest_pdf(
        pdf_path=Path("$DATA_DIR/source_pdfs/army/fm-21-76.pdf"),
        doc_id="fm-21-76",
        out_dir=Path("$DATA_DIR/extracted/fm-21-76"),
        images_base_dir=Path("$DATA_DIR/images"),
        skip_ocr=True,   # True = fitz only, False = fitz+docling
    )
"""

import json
import re
import subprocess
import hashlib
from datetime import datetime, timezone
from pathlib import Path


# ── Registry helpers (used by both api_server.py and batch_ingest_branch.py) ──

V2_REGISTRY_PATH = Path("$DATA_DIR/registry.json")


def _normalize_doc_id(doc_id: str) -> str:
    """Normalize a doc_id to match how the pipeline generates them from filenames.

    Pipeline: Path(fname).stem → lowercase → replace non-alphanumeric with hyphens.
    Example: army_atp3_34.80.pdf → army-atp3-34-80
    Registry: ARMY-ATP3-34.80 → army-atp3-34.80 (has dot, not hyphen)
    """
    return doc_id.lower().replace(".", "-")


def write_v2_registry_update(doc_id: str, fields: dict):
    """Write updated fields back to the v2 registry entry for doc_id.

    Uses normalized comparison — the v2 registry has mixed-case doc_ids while
    the pipeline normalizes to lowercase and hyphens. Write failures are
    silent since registry updates must never block processing.
    """
    try:
        if not V2_REGISTRY_PATH.exists():
            return
        v2 = json.load(open(V2_REGISTRY_PATH))
        doc_id_norm = _normalize_doc_id(doc_id)
        for doc in v2.get("documents", []):
            if _normalize_doc_id(doc.get("doc_id", "")) == doc_id_norm:
                doc.update(fields)
                with open(V2_REGISTRY_PATH, "w", encoding="utf-8") as f:
                    json.dump(v2, f, indent=2)
                break
    except Exception as e:
        print(f"Warning: could not update v2 registry for {doc_id}: {e}")


# ── Pure utility functions ──────────────────────────────────────────

def compute_sha256(pdf_path: Path) -> str:
    """Compute SHA256 hash of PDF file bytes."""
    sha256_hash = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def clean_page_text(text: str, doc_title: str | None = None) -> str:
    """Strip footer/header lines from page text."""
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^\d+$', stripped):
            continue
        if doc_title and stripped == doc_title:
            continue
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)


def find_visual_refs(text: str) -> list[str]:
    """Find figure and photo references in text."""
    return re.findall(r'(Figure|Photo)\s+\S+', text)


# ── Docling OCR ──────────────────────────────────────────────────────

def _run_docling(pdf_path: Path) -> tuple[str, int]:
    """Run docling via subprocess with conservative threading to avoid pypdfium2/RapidOCR deadlock.
    Writes a .done sentinel file on success so callers can detect completion
    even if the HTTP request timed out.
    Returns (text, page_count).

    Threading model: OMP_NUM_THREADS=1 (prevents pypdfium2+RapidOCR CPU contention deadlock),
    DOCLING_NUM_THREADS=2, AcceleratorOptions(num_threads=2) — conservative but stable."""
    docling_py = "$REPO_DIR/venv-docling/bin/python3"
    out_txt = "/tmp/_docling_out.txt"
    page_txt = "/tmp/_docling_pages.txt"
    done_sentinel = "/tmp/_docling_done.txt"
    for f in [out_txt, page_txt, done_sentinel]:
        Path(f).unlink(missing_ok=True)

    code = (
        "import os;"
        "os.environ['OMP_NUM_THREADS']='1';"
        "os.environ['DOCLING_NUM_THREADS']='2';"
        "from docling.document_converter import DocumentConverter, PdfFormatOption;"
        "from docling.datamodel.base_models import InputFormat;"
        "from docling.datamodel.pipeline_options import PdfPipelineOptions, AcceleratorOptions, RapidOcrOptions;"
        "from docling.datamodel.settings import settings;"
        "from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend;"
        "settings.debug.profile_pipeline_timings = True;"
        "settings.perf.doc_batch_concurrency = 2;"
        "pipeline_options = PdfPipelineOptions("
        "    document_timeout=300,"   # abort individual doc after 5min; prevents indefinite hangs
        "    do_ocr=True,"
        "    ocr_options=RapidOcrOptions(),"
        "    do_table_structure=False,"
        "    do_picture_description=False,"
        "    do_picture_classification=False,"
        "    generate_page_images=False,"
        "    generate_picture_images=False,"
        "    images_scale=1.0,"
        "    ocr_batch_size=4,"
        "    layout_batch_size=4,"
        "    table_batch_size=2,"
        "    accelerator_options=AcceleratorOptions(num_threads=2, device='cpu'),"
        ");"
        "converter = DocumentConverter("
        "    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend)}"
        ");"
        f"r=converter.convert({repr(str(pdf_path))});"
        f"open({repr(out_txt)},'w',encoding='utf-8').write(r.document.export_to_text());"
        f"open({repr(page_txt)},'w').write(str(len(r.document.pages)));"
        f"open({repr(done_sentinel)},'w').write('ok')"
    )
    proc = subprocess.Popen(
        [docling_py, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = proc.communicate(timeout=7200)  # 2hr timeout for large docs
    if proc.returncode != 0:
        raise RuntimeError(f"docling failed (code {proc.returncode}): {stderr[-300:]}")
    text = open(out_txt, encoding="utf-8").read()
    page_count = int(open(page_txt).read().strip())
    return text, page_count


# ── Image extraction ─────────────────────────────────────────────────

def _extract_images(doc, page_num: int, img_out_dir: Path, img_manifest: list, doc_id: str):
    """Extract images from a single page into img_out_dir.
    Skips decorative elements: tiny files (<10KB), horizontal rules (aspect ratio >8)."""
    img_idx = 0
    for img_info in doc[page_num].get_images(full=True):
        xref = img_info[0]
        try:
            extracted = doc.extract_image(xref)
            w, h = extracted['width'], extracted['height']
            if w < 50 or h < 50:
                continue
            # Skip horizontal rules / decorative dividers (very wide, very short)
            if w / h > 8:
                continue
            cs = extracted['colorspace']
            try:
                import io as _io
                from PIL import Image as _Image
                _img = _Image.open(_io.BytesIO(extracted['image']))
                if cs in (3, 4):
                    _img = _img.convert('L')
                    _buf = _io.BytesIO()
                    _img.save(_buf, format='JPEG', quality=70)
                    compressed, ext = _buf.getvalue(), 'jpg'
                elif cs == 1 and len(extracted['image']) > 100 * 1024:
                    _buf = _io.BytesIO()
                    _img.quantize(256).save(_buf, format='PNG')
                    compressed, ext = _buf.getvalue(), 'png'
                else:
                    compressed = extracted['image']
                    ext = extracted.get('ext', 'png')
            except Exception:
                compressed = extracted['image']
                ext = extracted.get('ext', 'png')
            # Skip tiny decorative images (< 10 KB — logos, rules, decorative elements)
            if len(compressed) < 10 * 1024:
                continue
            fname = f"page_{page_num:04d}_img_{img_idx:02d}.{ext}"
            (img_out_dir / fname).write_bytes(compressed)
            img_manifest.append({
                "doc_id": doc_id, "page_num": page_num + 1,
                "img_idx": img_idx, "filename": fname,
                "width": w, "height": h, "ext": ext,
            })
            img_idx += 1
        except Exception:
            pass


# ── Main PDF ingestion ───────────────────────────────────────────────

def ingest_pdf(pdf_path: Path, doc_id: str, out_dir: Path,
               images_base_dir: Path | None = None,
               force_ocr: bool = False,
               skip_ocr: bool = False) -> tuple[int, bool]:
    """Ingest PDF: extract text to per-page JSON + optionally extract images.

    Uses docling (OCR) when avg chars/page < 300 or force_ocr=True.
    If skip_ocr=True, uses fitz only and returns (page_count, needs_docling=True)
    without writing page files — callers must handle the needs_docling case.

    Returns (page_count, needs_docling).

    Callers:
      - Pipeline A (batch_ingest_branch.py): skip_ocr=True, handle needs_docling flag
      - Pipeline B (write_docling_pages): skip_ocr=False with force_ocr=True
      - Direct import: skip_ocr=False to run full fitz+docling pipeline
    """
    import fitz  # pymupdf — imported here to keep top-level imports light

    sha256 = compute_sha256(pdf_path)
    doc = fitz.open(str(pdf_path))
    page_count = len(doc)

    # Pre-scan: measure text density to decide OCR strategy
    total_chars = sum(len(doc[p].get_text('text')) for p in range(page_count))
    avg_chars_per_page = total_chars / page_count if page_count > 0 else 0
    doc.close()

    use_docling = force_ocr or (avg_chars_per_page < 300)
    needs_docling = False

    # skip_ocr path: use fitz only, signal if docling would be needed
    if skip_ocr:
        use_docling = False
        needs_docling = (avg_chars_per_page < 300)
        if needs_docling:
            # Low density — return immediately, no page files written,
            # no image extraction. Caller (Pipeline A) adds to extraction queue.
            return page_count, True

    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    # Image extraction setup
    img_out_dir = None
    img_manifest = []
    if images_base_dir is not None:
        candidate = images_base_dir / doc_id
        if not (candidate / "manifest.json").exists():
            img_out_dir = candidate
            img_out_dir.mkdir(parents=True, exist_ok=True)

    doc_title = pdf_path.stem

    if use_docling:
        # Run docling to get full OCR text
        docling_text, docling_page_count = _run_docling(pdf_path)
        # Write all docling text to page 1's JSON; remaining pages empty
        # This preserves [PAGE N] markers for chunking
        for page_num in range(page_count):
            if page_num == 0:
                f"[PAGE {page_num + 1}]"
                page_text = docling_text.strip()
            else:
                f"[PAGE {page_num + 1}]"
                page_text = ""
            page_data = {
                "page": page_num + 1,
                "text": page_text,
                "chapter": None,
                "section": None,
                "section_title": None,
                "visual_refs": find_visual_refs(page_text),
                "figure_only": len(find_visual_refs(page_text)) > 0 or len(page_text.strip()) == 0,
                "ocr_source": "docling",
                "ocr_chars": len(docling_text),
            }
            (pages_dir / f"{page_num:04d}.json").write_text(
                json.dumps(page_data, indent=2, ensure_ascii=False)
            )
        # Extract images separately using fitz
        doc = fitz.open(str(pdf_path))
        for page_num in range(page_count):
            if img_out_dir is not None:
                _extract_images(doc, page_num, img_out_dir, img_manifest, doc_id)
        doc.close()
    else:
        # Standard fitz path
        doc = fitz.open(str(pdf_path))
        current_chapter = None
        for page_num in range(page_count):
            page = doc[page_num]
            raw_text = page.get_text('text')
            cleaned_text = clean_page_text(raw_text, doc_title)
            visual_refs = find_visual_refs(cleaned_text)
            page_data = {
                "page": page_num + 1,
                "text": cleaned_text,
                "chapter": current_chapter,
                "section": None,
                "section_title": None,
                "visual_refs": visual_refs,
                "figure_only": len(visual_refs) > 0 or len(cleaned_text.strip()) == 0,
                "ocr_source": "fitz",
                "ocr_chars": len(cleaned_text),
            }
            (pages_dir / f"{page_num:04d}.json").write_text(
                json.dumps(page_data, indent=2, ensure_ascii=False)
            )
            if img_out_dir is not None:
                _extract_images(doc, page_num, img_out_dir, img_manifest, doc_id)
        doc.close()

    if img_out_dir is not None:
        (img_out_dir / "manifest.json").write_text(
            json.dumps(img_manifest, indent=2), encoding='utf-8'
        )

    manifest = {
        "doc_id": doc_id,
        "title": doc_title,
        "page_count": page_count,
        "source_pdf": str(pdf_path),
        "sha256": sha256,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ocr_used": "docling" if use_docling else "fitz",
        "avg_chars_per_page": round(avg_chars_per_page, 1),
        "needs_docling": needs_docling,
    }
    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return page_count, needs_docling
