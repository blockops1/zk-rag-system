"""
pdf_processing.py — Pipeline A: fitz-only PDF extraction.

No docling. No pass 2. Fixes PDF /Rotate metadata for images.

Output:
  extracted/{doc_id}/
    manifest.json          — doc metadata
    pages/
      0000.json            — per-page: text, page_num, ocr_source=fitz
      0001.json
      ...
  images/{doc_id}/
    manifest.json          — image manifest
    page_0000_img_00.png   — upright PNG images
    page_0000_img_01.jpg
    ...
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz  # pymupdf


# ─── Hashing ────────────────────────────────────────────────────────────────

def compute_sha256(pdf_path: Path) -> str:
    sha = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


# ─── Text extraction ─────────────────────────────────────────────────────────

def clean_page_text(text: str) -> str:
    """Strip page-number lines (pure digits) from page text."""
    lines = text.split("\n")
    cleaned = [line for line in lines if not re.match(r"^\d+$", line.strip())]
    return "\n".join(cleaned)


def find_visual_refs(text: str) -> list[str]:
    return re.findall(r"(Figure|Photo)\s+\S+", text)


# ─── Image extraction ───────────────────────────────────────────────────────

def _extract_page_images(doc: fitz.Document, page_num: int,
                          img_out_dir: Path, manifest: list, doc_id: str) -> None:
    """
    Extract embedded images from one page, applying rotation correction so
    saved PNGs/JPEGs are upright.

    Rotation fix:
      - Read /Rotate from the page dict via xref_object regex.
      - Raw embedded images carry the rotation of the page they live on.
      - Rotate PIL image by the /Rotate value (degrees, counter-clockwise)
        with expand=True so the canvas grows to fit, fill with white.
      - Then save as PNG (not JPEG) to avoid lossy recompression artifacts.
    """
    xref = doc[page_num].xref
    pdict_str = doc.xref_object(xref)
    m = re.search(r"/Rotate\s+(\d+)", pdict_str)
    rot = int(m.group(1)) if m else 0

    img_idx = 0
    for img_info in doc[page_num].get_images(full=True):
        xref = img_info[0]
        try:
            extracted = doc.extract_image(xref)
        except Exception:
            continue

        w, h = extracted["width"], extracted["height"]
        if w < 50 or h < 50:
            continue

        # Skip decorative horizontal strips (aspect ratio > 8)
        if w / h > 8:
            continue

        image_bytes = extracted["image"]
        ext = extracted.get("ext", "png").lower()

        # Skip tiny decorative images (< 10 KB)
        if len(image_bytes) < 10 * 1024:
            continue

        # Load as PIL for rotation fix
        try:
            from PIL import Image as PILImage
            import io
            pil_img = PILImage.open(io.BytesIO(image_bytes))

            # Apply rotation correction (rotate counter-clockwise by rot degrees)
            if rot != 0:
                pil_img = pil_img.rotate(rot, expand=True, fillcolor=(255, 255, 255))

            # Convert to PNG for storage (lossless, no recompression artifacts)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
            ext = "png"
        except Exception:
            # Fallback: save as-is
            pass

        fname = f"page_{page_num:04d}_img_{img_idx:02d}.{ext}"
        (img_out_dir / fname).write_bytes(image_bytes)

        manifest.append({
            "doc_id": doc_id,
            "page_num": page_num + 1,
            "img_idx": img_idx,
            "filename": fname,
            "width": w,
            "height": h,
            "ext": ext,
            "rotation_applied": rot,
        })
        img_idx += 1


# ─── Main entry point ───────────────────────────────────────────────────────

def ingest_pdf(pdf_path: Path, doc_id: str, out_dir: Path,
               images_base_dir: Path | None = None) -> dict:
    """
    Extract text and images from one PDF using fitz only.

    Args:
        pdf_path:   Path to the PDF file on disk.
        doc_id:     Document identifier (e.g. SHA256 hex or slug).
        out_dir:    Where to write extracted/ (manifest + pages/).
        images_base_dir:  If provided, images are written under
                         images_base_dir/doc_id/ instead of out_dir/images/.

    Returns:
        dict with keys: page_count, avg_chars_per_page, needs_docling=False,
                        status, images_written (int)
    """
    sha256 = compute_sha256(pdf_path)
    doc = fitz.open(str(pdf_path))
    page_count = len(doc)

    # ── Pre-scan: measure text density ──────────────────────────────────
    total_chars = sum(len(doc[p].get_text("text")) for p in range(page_count))
    avg_chars = total_chars / page_count if page_count > 0 else 0.0
    doc.close()

    # ── Output directories ───────────────────────────────────────────────
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    img_out_dir = None
    img_manifest = []
    if images_base_dir is not None:
        candidate = images_base_dir / doc_id
        if not (candidate / "manifest.json").exists():
            img_out_dir = candidate
            img_out_dir.mkdir(parents=True, exist_ok=True)

    # ── Page-by-page extraction ──────────────────────────────────────────
    doc = fitz.open(str(pdf_path))
    for page_num in range(page_count):
        page = doc[page_num]
        raw_text = page.get_text("text")
        cleaned_text = clean_page_text(raw_text)
        visual_refs = find_visual_refs(cleaned_text)

        page_data = {
            "page": page_num + 1,
            "text": cleaned_text,
            "chapter": None,
            "section": None,
            "section_title": None,
            "visual_refs": visual_refs,
            "figure_only": len(visual_refs) > 0 or len(cleaned_text.strip()) == 0,
            "ocr_source": "fitz",
            "ocr_chars": len(cleaned_text),
        }
        (pages_dir / f"{page_num:04d}.json").write_text(
            json.dumps(page_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        if img_out_dir is not None:
            _extract_page_images(doc, page_num, img_out_dir, img_manifest, doc_id)

    doc.close()

    # ── Image manifest ───────────────────────────────────────────────────
    if img_out_dir is not None:
        (img_out_dir / "manifest.json").write_text(
            json.dumps(img_manifest, indent=2), encoding="utf-8",
        )

    # ── Manifest ────────────────────────────────────────────────────────
    manifest = {
        "doc_id": doc_id,
        "page_count": page_count,
        "source_pdf": str(pdf_path),
        "sha256": sha256,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ocr_used": "fitz",
        "avg_chars_per_page": round(avg_chars, 1),
        "needs_docling": False,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    return {
        "status": "ok",
        "page_count": page_count,
        "avg_chars_per_page": round(avg_chars, 1),
        "needs_docling": False,
        "images_written": len(img_manifest),
    }
