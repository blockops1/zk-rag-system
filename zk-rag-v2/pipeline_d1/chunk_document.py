#!/usr/bin/env python3
"""
Pipeline D1: Chunking — split page JSONs into text chunks for embedding.

Design decisions (per discussion with Mr. V, 2026-05-01):
  - Algorithm: paragraph-aware recursive character split (no LlamaIndex)
  - Vision pages: inline [VISUAL: description] prepended to page text
  - Chunk_id: SHA256 of normalized chunk text (content-addressable)
  - Overlap: 10% of chunk_size
  - Figure-only pages with no text: skip as standalone (none exist in current data)

Usage:
    python chunk_document.py --doc-id <doc_id>
    python chunk_document.py --doc-id <doc_id> --chunk-size 512 --overlap 50
    python chunk_document.py --all
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ── Configuration ──────────────────────────────────────────────────────────────

EXTRACTED_BASE = Path("./data/extracted")
CHUNKS_BASE = Path("./data/chunks")
DEFAULT_CHUNK_SIZE = 512  # target chars per chunk
DEFAULT_OVERLAP = 50     # 10% of 512 — chars of context shared between chunks
MIN_CHUNK_CHARS = 50     # discard chunks below this size


# ── Chunking ─────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    chunk_index: int
    text: str
    page_nums: list[int]
    char_count: int
    vision_used: bool


def _normalize(text: str) -> str:
    """Normalize text for SHA256 chunk_id — whitespace-collapse, lowercase."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _make_chunk_id(text: str) -> str:
    """Content-addressable ID: SHA256 of normalized text."""
    return hashlib.sha256(_normalize(text).encode()).hexdigest()


def _split_into_chunks(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """Paragraph-aware recursive split.

    Splits on double newlines first (paragraphs), then single newlines, then
    hard char boundary. Respects natural document structure before breaking
    mid-sentence.
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    # Try splitting on double newline (paragraph boundary)
    paragraphs = re.split(r"\n\n+", text)
    if len(paragraphs) > 1:
        chunks: list[str] = []
        current: str = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) + 2 <= chunk_size:
                current = current + "\n\n" + para if current else para
            else:
                if current:
                    chunks.append(current)
                # If single paragraph exceeds chunk_size, recurse on it
                if len(para) > chunk_size:
                    sub_chunks = _split_into_chunks(para, chunk_size, overlap)
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = para
        if current:
            chunks.append(current)
        if chunks:
            return chunks

    # Try splitting on single newline (line boundary)
    lines = re.split(r"\n", text)
    if len(lines) > 1:
        chunks: list[str] = []
        current: str = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if len(current) + len(line) + 1 <= chunk_size:
                current = current + "\n" + line if current else line
            else:
                if current:
                    chunks.append(current)
                if len(line) > chunk_size:
                    sub_chunks = _split_into_chunks(line, chunk_size, overlap)
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = line
        if current:
            chunks.append(current)
        if chunks:
            return chunks

    # Hard split on char boundary — last resort
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap  # overlap so we don't lose context
    return chunks


def chunk_document(
    doc_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    output_dir: Optional[Path] = None,
) -> dict:
    """Chunk a document's page JSONs into text chunks.

    Args:
        doc_id: Document ID (directory name in extracted/)
        chunk_size: Target chars per chunk
        overlap: Chars of overlap between adjacent chunks
        output_dir: Output directory (default: ./data/chunks)

    Returns:
        dict with doc_id, chunk_count, chunks_path, chunk_ids_path
    """
    if output_dir is None:
        output_dir = CHUNKS_BASE

    doc_pages_dir = EXTRACTED_BASE / doc_id / "pages"
    if not doc_pages_dir.exists():
        raise FileNotFoundError(f"No pages dir for doc_id={doc_id}")

    # Load pages in order
    page_files = sorted(doc_pages_dir.glob("*.json"), key=lambda p: int(p.stem))
    if not page_files:
        raise ValueError(f"No page files in {doc_pages_dir}")

    # Build full text with page markers, track page metadata for chunk attribution
    full_text_parts: list[str] = []
    page_meta_for_offset: list[dict] = []  # {start_offset, page_num, vision_used}

    for page_file in page_files:
        page_data = json.loads(page_file.read_text())
        page_num = page_data.get("page", 0)
        start_offset = sum(len(p) for p in full_text_parts)

        page_text_parts: list[str] = []

        # Inline vision description for figure-only pages
        if page_data.get("figure_only") and page_data.get("vision_description"):
            page_text_parts.append(f"[VISUAL: {page_data['vision_description']}]")

        page_text = page_data.get("text", "").strip()
        if page_text:
            page_text_parts.append(page_text)

        combined = "\n".join(page_text_parts)
        if combined:
            full_text_parts.append(combined)

        page_meta_for_offset.append({
            "start_offset": start_offset,
            "page_num": page_num,
            "vision_used": bool(page_data.get("figure_only") and page_data.get("vision_description")),
            "text_len": len(combined),
        })

    if not full_text_parts:
        raise ValueError(f"Document {doc_id} has no text content")

    full_text = "\n\n".join(full_text_parts)

    # Split into chunks
    raw_chunks = _split_into_chunks(full_text, chunk_size, overlap)
    raw_chunks = [c for c in raw_chunks if len(c.strip()) >= MIN_CHUNK_CHARS]

    # Attribute each chunk to source pages
    out_doc_dir = (output_dir or CHUNKS_BASE) / doc_id
    out_doc_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = out_doc_dir / "chunks.jsonl"

    chunk_ids: list[str] = []
    chunks_written = 0

    with open(chunks_path, "w", encoding="utf-8") as f:
        for idx, chunk_text in enumerate(raw_chunks):
            chunk_id = _make_chunk_id(chunk_text)
            chunk_ids.append(chunk_id)

            # Find which pages this chunk overlaps
            chunk_start = full_text.find(chunk_text)
            if chunk_start < 0:
                chunk_start = 0
            chunk_end = chunk_start + len(chunk_text)

            involved_pages: list[int] = []
            vision_used = False
            for pm in page_meta_for_offset:
                pm_start = pm["start_offset"]
                pm_end = pm_start + pm["text_len"]
                if pm_start < chunk_end and pm_end > chunk_start:
                    involved_pages.append(pm["page_num"])
                    vision_used = vision_used or pm["vision_used"]

            chunk_record = {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "chunk_index": idx,
                "text": chunk_text.strip(),
                "page_nums": sorted(set(involved_pages)),
                "char_count": len(chunk_text.strip()),
                "vision_used": vision_used,
            }
            f.write(json.dumps(chunk_record, ensure_ascii=False) + "\n")
            chunks_written += 1

    chunk_ids_path = out_doc_dir / "chunk_ids.json"
    chunk_ids_path.write_text(json.dumps(chunk_ids, indent=2))

    return {
        "doc_id": doc_id,
        "chunk_count": chunks_written,
        "chunks_path": str(chunks_path),
        "chunk_ids_path": str(chunk_ids_path),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Pipeline D1: chunk page JSONs into text")
    parser.add_argument("--doc-id", help="Process single doc by ID")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Target chars per chunk (default: {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=DEFAULT_OVERLAP,
        help=f"Overlap chars between chunks (default: {DEFAULT_OVERLAP})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=CHUNKS_BASE,
        help=f"Output parent dir (default: {CHUNKS_BASE})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all docs in extracted/",
    )
    args = parser.parse_args()

    if not args.doc_id and not args.all:
        parser.error("--doc-id or --all required")

    if args.all:
        doc_ids = [d.name for d in EXTRACTED_BASE.iterdir() if d.is_dir()]
        print(f"D1: processing {len(doc_ids)} docs ...")
        ok, skipped, errors = 0, 0, 0
        for doc_id in sorted(doc_ids):
            try:
                result = chunk_document(
                    doc_id=doc_id,
                    chunk_size=args.chunk_size,
                    overlap=args.overlap,
                    output_dir=args.out_dir,
                )
                print(f"  {doc_id[:8]}: {result['chunk_count']} chunks")
                ok += 1
            except FileNotFoundError as e:
                print(f"  {doc_id[:8]}: SKIP — {e}")
                skipped += 1
            except Exception as e:
                print(f"  {doc_id[:8]}: ERROR — {e}")
                errors += 1
        print(f"\nDone: {ok} ok, {skipped} skipped, {errors} errors")
        return

    result = chunk_document(
        doc_id=args.doc_id,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        output_dir=args.out_dir,
    )
    print(
        f"Done. {result['chunk_count']} chunks → {result['chunks_path']}"
    )


if __name__ == "__main__":
    main()
