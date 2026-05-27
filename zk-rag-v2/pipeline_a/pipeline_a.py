#!/usr/bin/env python3
"""
pipeline_a.py — Pipeline A: fitz-only PDF extraction for military documents.

SINGLE-THREADED. One doc at a time. Rich structured logging.

Image extraction: page.get_pixmap() renders the fully-rotated, right-side-up
page to a PNG. Canonical "what you see when you open the PDF."

Output:
  ../data/extracted/{sha256}/
    manifest.json
    pages/  0000.json  0001.json  ...
  ../data/images/{sha256}/
    manifest.json
    page_0000.png  page_0001.png  ...   (full-page PNG renders)
"""

import argparse
import hashlib
import json
import logging
import sys
import time
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz  # pymupdf

# ── Paths ────────────────────────────────────────────────────────────────────
REGISTRY_PATH = Path("../data/registry.json")
SOURCE_PDFS   = Path("../data/sourcePDF")
EXTRACTED_DIR = Path("../data/extracted")
IMAGES_DIR    = Path("../data/images")
DONE_LOG      = Path("../data/pipeline_a_done.json")
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
LOG_FILE = LOG_DIR / f"pipeline_a_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

_formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

_file_h = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_h.setLevel(logging.DEBUG)
_file_h.setFormatter(_formatter)

_stdout_h = logging.StreamHandler(sys.stdout)
_stdout_h.setLevel(logging.INFO)
_stdout_h.setFormatter(logging.Formatter(fmt="%(message)s"))

log = logging.getLogger("pipeline_a")
log.setLevel(logging.DEBUG)
log.addHandler(_file_h)
log.addHandler(_stdout_h)


# ── Helpers ─────────────────────────────────────────────────────────────────

def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_registry() -> list[dict]:
    with open(REGISTRY_PATH) as f:
        return json.load(f)["documents"]


def save_done(new_ids: list[str]):
    """Append newly done doc_ids to the done log."""
    if DONE_LOG.exists():
        with open(DONE_LOG) as f:
            done = set(json.load(f).get("done", []))
    else:
        done = set()
    done.update(new_ids)
    tmp = DONE_LOG.with_suffix(".tmp")
    json.dump({"done": sorted(done)}, tmp.open("w"), indent=2)
    tmp.rename(DONE_LOG)
    log.debug("done_log: %d total entries", len(done))


def find_pdf(doc: dict) -> Path | None:
    branch = doc.get("branch", "other")
    fname  = doc.get("filename", "")
    if not fname:
        return None
    bdir = BRANCH_DIRS.get(branch, BRANCH_DIRS["other"])
    candidate = bdir / fname
    return candidate if candidate.exists() else None


def clean_page_text(text: str) -> str:
    lines = text.split("\n")
    return "\n".join(line for line in lines if not re.match(r"^\d+$", line.strip()))


# ── Per-page ────────────────────────────────────────────────────────────────

def process_page(page: fitz.Page, page_num: int, doc_id: str,
                 img_out_dir: Path, img_manifest: list) -> dict:
    """
    Extract text and render the full page as a PNG.

    page.get_pixmap() auto-applies /Rotate from the page dict — output is
    always upright. No rotation arithmetic needed.
    """
    # Text
    raw_text  = page.get_text("text")
    cleaned   = clean_page_text(raw_text)
    vis_refs  = re.findall(r"(Figure|Photo)\s+\S+", cleaned)

    # Page render as image
    pix = page.get_pixmap(matrix=fitz.Identity, colorspace=fitz.csRGB)
    pix_bytes = pix.tobytes("png")

    fname = f"page_{page_num:04d}.png"
    (img_out_dir / fname).write_bytes(pix_bytes)

    img_manifest.append({
        "doc_id":   doc_id,
        "page_num": page_num + 1,
        "filename": fname,
        "width":    pix.width,
        "height":   pix.height,
        "bbytes":   len(pix_bytes),
    })

    return {
        "page":          page_num + 1,
        "text":          cleaned,
        "visual_refs":   vis_refs,
        "figure_only":   len(vis_refs) > 0 or len(cleaned.strip()) == 0,
        "ocr_source":    "fitz",
        "ocr_chars":     len(cleaned),
        "image_written": fname,
        "image_width":  pix.width,
        "image_height": pix.height,
    }


# ── Per-document ────────────────────────────────────────────────────────────

def process_doc(doc: dict) -> dict:
    """
    Extract text + page images from one PDF.

    Steps:
      1. Locate PDF via branch subdirs
      2. Verify SHA256 against registry
      3. Pre-scan text density
      4. Create output dirs (idempotent — skips if already done)
      5. Page-by-page: extract text + render page image
      6. Write manifest + per-page JSON
      7. Return result
    """
    doc_id  = doc.get("doc_id", "")
    fname   = doc.get("filename", "unknown")
    reg_sha = doc.get("sha256", "")
    branch  = doc.get("branch", "other")

    log.info("START  doc_id=%s  filename=%s  branch=%s", doc_id[:16], fname, branch)

    # 1. Locate PDF
    pdf_path = find_pdf(doc)
    if pdf_path is None or not pdf_path.exists():
        log.error("FILE_NOT_FOUND  doc_id=%s  filename=%s", doc_id[:16], fname)
        return {"status": "file_not_found", "doc_id": doc_id, "filename": fname}

    # 2. SHA256 verify
    try:
        real_sha = compute_sha256(pdf_path)
    except Exception as e:
        log.error("SHA_ERROR  doc_id=%s  filename=%s  error=%s", doc_id[:16], fname, e)
        return {"status": "sha_error", "doc_id": doc_id, "filename": fname, "error": str(e)}

    if reg_sha and real_sha != reg_sha:
        log.error("SHA_MISMATCH  doc_id=%s  filename=%s  expected=%s  got=%s",
                  doc_id[:16], fname, reg_sha[:16], real_sha[:16])
        return {"status": "sha_mismatch", "doc_id": doc_id, "filename": fname,
                "expected": reg_sha[:16], "got": real_sha[:16]}

    doc_id = real_sha  # use content-addressed SHA
    log.debug("SHA256 verified: %s", doc_id)

    # 3. Open PDF + pre-scan
    try:
        doc_fitz = fitz.open(str(pdf_path))
    except Exception as e:
        log.error("OPEN_ERROR  doc_id=%s  filename=%s  error=%s", doc_id[:16], fname, e)
        return {"status": "open_error", "doc_id": doc_id, "filename": fname, "error": str(e)}

    page_count = len(doc_fitz)
    if page_count == 0:
        doc_fitz.close()
        log.error("ZERO_PAGES  doc_id=%s  filename=%s", doc_id[:16], fname)
        return {"status": "zero_pages", "doc_id": doc_id, "filename": fname}

    total_chars = sum(len(doc_fitz[p].get_text("text")) for p in range(page_count))
    avg_chars   = total_chars / page_count
    log.debug("page_count=%d  avg_chars=%.1f  total_chars=%d", page_count, avg_chars, total_chars)

    # 4. Output dirs — idempotent skip
    out_dir    = EXTRACTED_DIR / doc_id
    img_dir    = IMAGES_DIR    / doc_id
    if (out_dir / "manifest.json").exists():
        log.info("ALREADY_DONE  doc_id=%s  filename=%s  skipping", doc_id[:16], fname)
        doc_fitz.close()
        return {"status": "already_done", "doc_id": doc_id, "filename": fname}

    out_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    # 5. Page-by-page
    img_manifest = []
    page_results = []

    for page_num in range(page_count):
        t0 = time.monotonic()
        page_data = process_page(doc_fitz[page_num], page_num, doc_id, img_dir, img_manifest)
        page_results.append(page_data)
        elapsed = time.monotonic() - t0
        log.debug(
            "page %4d/%d  ocr_chars=%-6d  %s  %dx%d  %.3fs",
            page_num + 1, page_count,
            page_data["ocr_chars"],
            page_data["image_written"],
            page_data["image_width"],
            page_data["image_height"],
            elapsed,
        )

    doc_fitz.close()

    # 6. Write output files
    for page_num, page_data in enumerate(page_results):
        (pages_dir / f"{page_num:04d}.json").write_text(
            json.dumps(page_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    (img_dir / "manifest.json").write_text(
        json.dumps(img_manifest, indent=2), encoding="utf-8",
    )

    manifest = {
        "doc_id":             doc_id,
        "filename":           fname,
        "branch":             branch,
        "page_count":         page_count,
        "sha256":             doc_id,
        "source_pdf":         str(pdf_path),
        "ingested_at":        datetime.now(timezone.utc).isoformat(),
        "ocr_used":           "fitz",
        "avg_chars_per_page": round(avg_chars, 1),
        "total_chars":        total_chars,
        "images_written":     len(img_manifest),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    log.info(
        "OK  doc_id=%s  filename=%s  pages=%d  avg_chars=%.1f  images=%d",
        doc_id[:16], fname, page_count, avg_chars, len(img_manifest),
    )

    return {
        "status":         "ok",
        "doc_id":        doc_id,
        "filename":      fname,
        "branch":        branch,
        "page_count":    page_count,
        "avg_chars":     round(avg_chars, 1),
        "total_chars":   total_chars,
        "images_written": len(img_manifest),
    }


# ── Main loop ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max docs to process this run")
    parser.add_argument("--force", action="store_true", help="Re-process even if already done")
    args = parser.parse_args()

    log.info("=== Pipeline A start ===")
    log.info("REGISTRY=%s", REGISTRY_PATH)
    log.info("EXTRACTED_DIR=%s", EXTRACTED_DIR)
    log.info("IMAGES_DIR=%s", IMAGES_DIR)
    log.info("LOG_FILE=%s", LOG_FILE)

    all_docs = load_registry()
    ok_docs  = [d for d in all_docs if d.get("status") == "ok"]
    log.info("Registry: %d docs with status=ok", len(ok_docs))

    # Load done set — done_log stores full SHA256, also check by filename
    done_fnames = set()
    done_sha256s = set()
    if DONE_LOG.exists():
        with open(DONE_LOG) as f:
            done_data = json.load(f)
        for entry in done_data.get("done", []):
            if len(entry) == 64:       # full SHA256
                done_sha256s.add(entry)
            else:                      # legacy short form
                done_fnames.add(entry)
        log.info("Already done: %d (by SHA) + %d (by filename)", len(done_sha256s), len(done_fnames))

    # Build work list
    work = []
    for doc in ok_docs:
        fname  = doc.get("filename", "")
        doc_id = doc.get("doc_id", "")
        sha256 = doc.get("sha256", "")
        if args.force:
            work.append(doc)
        elif fname in done_fnames or doc_id in done_fnames or sha256 in done_sha256s or doc_id in done_sha256s:
            log.debug("SKIP (already done): %s", fname)
        else:
            work.append(doc)

    if args.limit:
        work = work[: args.limit]

    log.info("Work to do: %d docs", len(work))

    if not work:
        log.info("No work. Exiting.")
        return

    results = {
        "ok": [], "already_done": [], "file_not_found": [],
        "sha_mismatch": [], "sha_error": [], "open_error": [], "zero_pages": [],
    }

    for doc in work:
        result = process_doc(doc)
        status = result["status"]
        if status == "ok":
            save_done([result["doc_id"]])
            results["ok"].append(result)
        elif status == "already_done":
            results["already_done"].append(result)
        else:
            results.get(status, results["ok"]).append(result)

    # ── Final summary ──────────────────────────────────────────────────────
    total = sum(len(v) for v in results.values())
    log.info("=== Pipeline A done ===")
    log.info(
        "Results: ok=%d  already_done=%d  file_not_found=%d  "
        "sha_mismatch=%d  other_errors=%d  total=%d",
        len(results["ok"]),
        len(results["already_done"]),
        len(results["file_not_found"]),
        len(results["sha_mismatch"]),
        total - len(results["ok"]) - len(results["already_done"]) -
            len(results["file_not_found"]) - len(results["sha_mismatch"]),
        total,
    )

    # Update registry
    if results["ok"]:
        ok_ids = [r["doc_id"] for r in results["ok"]]
        docs = load_registry()
        updated = 0
        for d in docs:
            if d.get("doc_id") in ok_ids or d.get("sha256") in ok_ids:
                d["status"] = "extracted"
                updated += 1
        with open(REGISTRY_PATH, "w") as f:
            json.dump({"documents": docs}, f, indent=2)
        log.info("Registry: %d docs → extracted", updated)

    log.info("LOG_FILE=%s", LOG_FILE)


if __name__ == "__main__":
    main()
