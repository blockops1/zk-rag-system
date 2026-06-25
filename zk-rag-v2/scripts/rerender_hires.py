#!/usr/bin/env python3
"""
Re-render all extracted document pages at higher resolution (2x = 144 DPI) as WebP.

Usage:
    python3 rerender_hires.py --doc-id <doc_id>     # single doc
    python3 rerender_hires.py --limit N              # first N docs
    python3 rerender_hires.py --dry-run              # show what would be done
"""
import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import fitz
from PIL import Image
import io

# ── Paths ────────────────────────────────────────────────────────────────────
REGISTRY_PATH = Path("./data/registry.json")
IMAGES_DIR    = Path("./data/images")
SOURCE_PDFS   = Path("./data/sourcePDF")
LOG_DIR       = Path("./logs")

BRANCH_DIRS = {
    "army":       SOURCE_PDFS / "army",
    "navy":       SOURCE_PDFS / "navy",
    "marines":    SOURCE_PDFS / "marines",
    "airforce":   SOURCE_PDFS / "airforce",
    "coastguard": SOURCE_PDFS / "coastguard",
    "joint":      SOURCE_PDFS / "joint",
    "other":      SOURCE_PDFS / "other",
}

# ── Logging ─────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"rerender_hires_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_handler.setLevel(logging.DEBUG)
_handler.setFormatter(logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))
_stdout_h = logging.StreamHandler(sys.stdout)
_stdout_h.setLevel(logging.INFO)
_stdout_h.setFormatter(logging.Formatter(fmt="%(message)s"))

log = logging.getLogger("rerender")
log.setLevel(logging.DEBUG)
log.addHandler(_handler)
log.addHandler(_stdout_h)


# ── Core rendering ───────────────────────────────────────────────────────────

SCALE = 2  # 2x = 144 DPI
MATRIX = fitz.Matrix(SCALE, SCALE)
WEBP_QUALITY = 90
IMG_DIR_FMT = "page_{page_num:04d}.webp"


def rerender_doc(doc_id: str, branch: str, fname: str, force_new_dir: bool = False) -> dict:
    """Re-render all pages of a document at 2x (144 DPI) as WebP."""
    # Find PDF
    bdir = BRANCH_DIRS.get(branch, BRANCH_DIRS["other"])
    pdf_path = bdir / fname
    if not pdf_path.exists():
        return {"status": "file_not_found", "doc_id": doc_id}

    # Verify SHA256 matches doc_id
    real_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if real_sha != doc_id:
        return {"status": "sha_mismatch", "doc_id": doc_id,
                "expected": doc_id[:16], "got": real_sha[:16]}

    img_dir = IMAGES_DIR / doc_id
    if not img_dir.exists():
        if force_new_dir:
            img_dir.mkdir(parents=True, exist_ok=True)
        else:
            return {"status": "no_images_dir", "doc_id": doc_id}

    try:
        doc_fitz = fitz.open(str(pdf_path))
    except Exception as e:
        return {"status": "open_error", "doc_id": doc_id, "error": str(e)}

    page_count = len(doc_fitz)
    total_bytes = 0


    for page_num in range(page_count):
        page = doc_fitz[page_num]
        pix = page.get_pixmap(matrix=MATRIX, colorspace=fitz.csRGB)

        # Convert to PIL Image and save as WebP
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=WEBP_QUALITY)
        webp_bytes = buf.getvalue()

        out_name = IMG_DIR_FMT.format(page_num=page_num)
        (img_dir / out_name).write_bytes(webp_bytes)
        total_bytes += len(webp_bytes)

        if (page_num + 1) % 50 == 0:
            log.debug("  page %d/%d", page_num + 1, page_count)

    doc_fitz.close()

    log.info(
        "OK  doc_id=%s  pages=%d  scale=%dx  dpi=%d  webp_q=%d  total_bytes=%s",
        doc_id[:16], page_count, SCALE, 72 * SCALE, WEBP_QUALITY, _human_bytes(total_bytes),
    )
    return {
        "status": "ok",
        "doc_id": doc_id,
        "pages": page_count,
        "scale": SCALE,
        "dpi": 72 * SCALE,
        "webp_quality": WEBP_QUALITY,
        "total_bytes": total_bytes,
    }


def _human_bytes(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max docs to process")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done, no writes")
    parser.add_argument("--doc-id", type=str, default=None,
                        help="Process a single doc by doc_id (bypasses registry scan)")
    args = parser.parse_args()

    log.info("=== HiRes re-render start (scale=%dx = %d DPI) ===", SCALE, 72 * SCALE)
    log.info("IMAGES_DIR=%s", IMAGES_DIR)
    log.info("LOG_FILE=%s", LOG_FILE)

    with open(REGISTRY_PATH) as f:
        registry = json.load(f)

    # Build doc_id → doc lookup
    doc_lookup = {doc["doc_id"]: doc for doc in registry["documents"] if "doc_id" in doc}

    if args.doc_id:
        # Single-doc mode: look up by doc_id
        doc = doc_lookup.get(args.doc_id)
        if not doc:
            log.error("doc_id=%s not found in registry", args.doc_id)
            return
        doc_id = doc["doc_id"]
        fname  = doc.get("filename", "")
        branch = doc.get("branch", "other")
        log.info("Single doc mode: doc_id=%s branch=%s fname=%s",
                 doc_id[:16], branch, fname)
        if not args.dry_run:
            result = rerender_doc(doc_id, branch, fname, force_new_dir=True)
            status = result["status"]
            if status == "ok":
                log.info("OK doc_id=%s pages=%d", doc_id[:16], result["pages"])
            else:
                log.error("ERROR %s: doc_id=%s", status, doc_id[:16])
        return

    # Docs that have been extracted (have pages in extracted/)
    extracted_dir = Path("./data/extracted")
    docs = []
    for doc in registry["documents"]:
        doc_id = doc.get("doc_id") or doc.get("sha256", "")
        if not doc_id:
            continue
        if not (extracted_dir / doc_id).exists():
            continue
        docs.append(doc)

    log.info("Found %d extracted docs", len(docs))

    if args.limit:
        docs = docs[: args.limit]

    results = {"ok": [], "errors": []}
    for doc in docs:
        doc_id  = doc.get("doc_id") or doc.get("sha256", "")
        fname   = doc.get("filename", "")
        branch  = doc.get("branch", "other")

        if args.dry_run:
            log.info("DRY RUN: would re-render doc_id=%s branch=%s fname=%s",
                     doc_id[:16], branch, fname)
            continue

        result = rerender_doc(doc_id, branch, fname)
        if result["status"] == "ok":
            results["ok"].append(result)
        else:
            results["errors"].append(result)
            log.error("ERROR %s: doc_id=%s fname=%s",
                      result["status"], doc_id[:16], fname)

    log.info("=== Done ===")
    log.info("Results: ok=%d  errors=%d  total=%d",
             len(results["ok"]), len(results["errors"]),
             len(results["ok"]) + len(results["errors"]))
    log.info("LOG_FILE=%s", LOG_FILE)


if __name__ == "__main__":
    main()
