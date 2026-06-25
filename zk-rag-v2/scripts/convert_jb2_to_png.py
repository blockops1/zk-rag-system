#!/usr/bin/env python3
"""
convert_jb2_to_png.py

Re-extract images from source PDFs and save as PNG instead of raw JBIG2.
For documents that have .jb2 images in ./data/images/,
finds the source PDF and re-extracts images using PyMuPDF rendering.

Usage:
    # Dry run (show what would be converted)
    python3 convert_jb2_to_png.py --dry-run

    # Actually convert
    python3 convert_jb2_to_png.py

    # Convert specific doc
    python3 convert_jb2_to_png.py --doc-id 00cdeace1a76729d9bf611b2277b0cd586c3d3b5e81c7e884e848d2d7b3864f1

    # Limit to N docs (for testing)
    python3 convert_jb2_to_png.py --max-docs 5
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
import fitz  # PyMuPDF

# ── Config ────────────────────────────────────────────────────────────────────

IMAGES_DIR = Path("./data/images")
PDF_ROOT = Path("./data/sourcePDF")
REGISTRY_PATH = Path("./data/registry.json")
LOG_DIR = Path(".../data/logs")
RENDER_SCALE = 2  # 2x scale for good quality

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "jb2_convert.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("jb2_convert")


def find_source_pdf(doc_id: str) -> Path | None:
    """Find the source PDF for a given doc_id by scanning all subdirs."""
    for pdf_path in PDF_ROOT.rglob("*.pdf"):
        if doc_id[:16] in pdf_path.name or doc_id[:8] in pdf_path.name:
            return pdf_path
    return None


def doc_has_jb2(doc_id: str) -> bool:
    """Check if a doc's image directory contains .jb2 files."""
    doc_dir = IMAGES_DIR / doc_id
    if not doc_dir.is_dir():
        return False
    return any(f.suffix == ".jb2" for f in doc_dir.iterdir())


def convert_doc_images(doc_id: str, dry_run: bool = False) -> dict:
    """Re-extract images from source PDF as PNG, replacing .jb2 files.
    
    Returns dict with keys: converted, failed, skipped, total.
    """
    doc_dir = IMAGES_DIR / doc_id
    result = {"converted": 0, "failed": 0, "skipped": 0, "total": 0}

    if not doc_dir.is_dir():
        log.warning(f"  Image dir not found: {doc_dir}")
        return result

    # Find which pages had images by reading manifest.json
    manifest_path = doc_dir / "manifest.json"
    if not manifest_path.exists():
        log.warning(f"  No manifest.json for {doc_id}")
        return result

    try:
        _manifest = json.loads(manifest_path.read_text())
    except Exception as e:
        log.error(f"  Failed to read manifest for {doc_id}: {e}")
        return result

    # Find source PDF
    pdf_path = find_source_pdf(doc_id)
    if pdf_path is None:
        log.error(f"  Source PDF not found for {doc_id}")
        # All remaining jb2 files fail
        result["total"] = len(list(doc_dir.glob("*.jb2")))
        result["failed"] = result["total"]
        return result

    log.info(f"  Source PDF: {pdf_path.name}")

    if dry_run:
        jb2_files = list(doc_dir.glob("*.jb2"))
        log.info(f"  Would convert {len(jb2_files)} .jb2 files")
        return result

    try:
        pdf_doc = fitz.open(str(pdf_path))
    except Exception as e:
        log.error(f"  Failed to open PDF {pdf_path}: {e}")
        result["total"] = len(list(doc_dir.glob("*.jb2")))
        result["failed"] = result["total"]
        return result

    # Parse page_XXXX_img_YY.jb2 to get page number and image index from filename.
    # The filename encodes the correct page (1-indexed), independent of manifest.
    import re
    filename_pattern = re.compile(r"page_(\d+)_img_(\d+)\.jb2$")

    # Process ALL .jb2 files directly from disk — don't trust manifest img_idx
    jb2_files = list(doc_dir.glob("*.jb2"))
    result["total"] = len(jb2_files)

    for jb2_path in jb2_files:
        try:
            m = filename_pattern.match(jb2_path.name)
            if not m:
                log.warning(f"    Unrecognized filename pattern: {jb2_path.name}")
                result["failed"] += 1
                continue

            page_num_1idx = int(m.group(1))   # 1-indexed page from filename
            img_idx = int(m.group(2))         # image index (usually 0)

            page_idx = page_num_1idx - 1
            if page_idx < 0 or page_idx >= len(pdf_doc):
                log.warning(f"    Page {page_num_1idx} out of range for PDF with {len(pdf_doc)} pages")
                result["failed"] += 1
                continue

            page = pdf_doc[page_idx]
            images_on_page = page.get_images(full=True)

            # Find the xref for the image at img_idx on this page
            if img_idx >= len(images_on_page):
                log.warning(f"    Page {page_num_1idx} img {img_idx} not in PDF (only {len(images_on_page)} images)")
                result["failed"] += 1
                continue

            xref = images_on_page[img_idx][0]
            extracted = page.parent.extract_image(xref)

            if extracted.get("ext") != "jb2":
                # Wrong index — the file is jb2 but PDF says different format.
                # Try to find a jb2 image on this page by checking each image.
                found = False
                for candidate_idx, img_info in enumerate(images_on_page):
                    try:
                        candidate_xref = img_info[0]
                        cand_ext = page.parent.extract_image(candidate_xref).get("ext")
                        if cand_ext == "jb2":
                            pix = fitz.Pixmap(page.parent, candidate_xref)
                            if pix.n - pix.alpha > 1:
                                pix = fitz.Pixmap(fitz.csRGB, pix)
                            png_data = pix.tobytes("png")
                            pix = None
                            new_filename = jb2_path.name.replace(".jb2", ".png")
                            (doc_dir / new_filename).write_bytes(png_data)
                            jb2_path.unlink()
                            result["converted"] += 1
                            found = True
                            break
                    except Exception:
                        continue
                if not found:
                    result["failed"] += 1
                continue

            # JBIG2 — decode via Pixmap
            try:
                pix = fitz.Pixmap(page.parent, xref)
                if pix.n - pix.alpha > 1:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                png_data = pix.tobytes("png")
                pix = None

                new_filename = jb2_path.name.replace(".jb2", ".png")
                out_path = doc_dir / new_filename
                out_path.write_bytes(png_data)
                jb2_path.unlink()  # remove old .jb2
                result["converted"] += 1
            except Exception as e:
                log.error(f"    Failed to convert {jb2_path.name}: {e}")
                result["failed"] += 1

        except Exception as e:
            log.error(f"    Error processing {jb2_path.name}: {e}")
            result["failed"] += 1

    pdf_doc.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="Convert .jb2 images to .png in military docs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be converted without making changes")
    parser.add_argument("--doc-id", type=str, help="Convert specific doc only")
    parser.add_argument("--max-docs", type=int, default=0, help="Limit to N docs (for testing)")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.doc_id:
        doc_ids = [args.doc_id]
    else:
        # Find all docs with .jb2 files
        doc_ids = sorted([
            d.name for d in IMAGES_DIR.iterdir()
            if d.is_dir() and doc_has_jb2(d.name)
        ])
        log.info(f"Found {len(doc_ids)} docs with .jb2 images")

    if args.max_docs > 0:
        doc_ids = doc_ids[: args.max_docs]
        log.info(f"Limited to {args.max_docs} docs")

    total_converted = 0
    total_failed = 0

    for i, doc_id in enumerate(doc_ids, 1):
        log.info(f"[{i}/{len(doc_ids)}] Processing {doc_id}")
        result = convert_doc_images(doc_id, dry_run=args.dry_run)
        total_converted += result["converted"]
        total_failed += result["failed"]

        if result["converted"] > 0 and not args.dry_run:
            log.info(f"  → Converted: {result['converted']}, Failed: {result['failed']}, Skipped: {result['skipped']}")

        # Rate limit to avoid hammering the system
        if i < len(doc_ids):
            time.sleep(0.5)

    log.info(f"\nSUMMARY: converted={total_converted}, failed={total_failed}")
    if args.dry_run:
        log.info("(dry run — no files were modified)")


if __name__ == "__main__":
    main()
