#!/usr/bin/env python3.14
"""
Pipeline D — Document Chunking (PRD-MIL-01 spec)

Uses LlamaIndex HierarchicalNodeParser + SemanticDoubleMergingSplitterNodeParser
to split page JSONs into hierarchical, semantically coherent chunks suitable for
embedding and vector search.

Usage:
    python chunk_document.py --doc-id <doc_id> [--chunk-size 512] [--overlap 100] [--out-dir ../data/chunks]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llama_index.core.node_parser import HierarchicalNodeParser
from llama_index.core.schema import Document


# ── Configuration ──────────────────────────────────────────────────────────────

CHUNK_SIZE    = 512
CHUNK_OVERLAP = 20   # HierarchicalNodeParser uses its own overlap; this is a placeholder

INGESTED_BASE   = Path("../data/extracted")
DEFAULT_OUT_DIR = Path("../data/chunks")

MIN_CHUNK_CHARS = 50


# ── Core logic (importable by pipeline_d.py) ─────────────────────────────────

def _chunk_document(
    source_dir: Path,
    doc_id: str,
    out_dir: Path | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> dict:
    """Chunk a document's pages into hierarchical semantic chunks.

    Uses HierarchicalNodeParser as the top-level hierarchy builder, then
    SemanticDoubleMergingSplitterNodeParser on the leaf nodes to merge
    semantically similar adjacent chunks and split only when similarity drops.

    Args:
        source_dir: Path to document directory (contains manifest.json + pages/)
        doc_id: Document ID (used for output path)
        out_dir: Output parent directory (default: ../data/chunks)
        chunk_size: Target chunk size in characters (passed to semantic splitter)

    Returns:
        dict with keys: doc_id, chunk_count, source, chunks_path, chunk_ids_path
    """
    if out_dir is None:
        out_dir = DEFAULT_OUT_DIR

    out_doc_dir = out_dir / doc_id
    out_doc_dir.mkdir(parents=True, exist_ok=True)

    # Load manifest
    manifest = json.loads((source_dir / "manifest.json").read_text())
    assert manifest["doc_id"] == doc_id, f"doc_id mismatch: {manifest['doc_id']} != {doc_id}"

    # Load all pages in order
    pages_dir = source_dir / "pages"
    page_files = sorted(pages_dir.glob("*.json"), key=lambda p: int(p.stem))
    pages = [json.loads(p.read_text()) for p in page_files]

    # Build full text with page markers, track per-page metadata
    full_text_parts: list[str]   = []
    page_metadata:    list[tuple[int, str, dict]] = []  # (start_offset, text, meta)

    for page in pages:
        start_offset = len("".join(full_text_parts))
        page_num     = page.get("page", 0)

        # Build page text: [VISUAL: ...] marker if figure-only with vision desc,
        # then the page text
        page_text_parts = []
        if page.get("figure_only") and page.get("vision_description"):
            page_text_parts.append(f"[VISUAL: {page['vision_description']}]\n\n")

        page_text = page.get("text", "")
        page_text_parts.append(page_text)

        page_marker = f"\n[PAGE {page_num}]\n"
        page_text_parts.insert(0, page_marker)

        combined = "".join(page_text_parts)
        full_text_parts.append(combined)

        page_meta = {
            "page":    page_num,
            "chapter": page.get("chapter"),
            "section": page.get("section"),
            "section_title": page.get("section_title"),
            "exhibit_refs": page.get("visual_refs", []),
            "vision_description_used": (
                page.get("figure_only") and bool(page.get("vision_description"))
            ),
        }
        page_metadata.append((start_offset, combined, page_meta))

    full_text = "".join(full_text_parts)

    # ── Build LlamaIndex Documents for hierarchical parsing ──────────────────
    # Use a single Document wrapping the full text; the node parsers will split it
    llmadoc = Document(text=full_text, metadata={"doc_id": doc_id})

    # Hierarchical parser — respects document hierarchy (chapter → section → paragraph).
    # Leaf chunks are ~512 chars, parent chunks ~2048 chars for broader context.
    hier_parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=[2048, 512],
        chunk_overlap=20,
    )

    # Get all nodes from the document
    nodes = hier_parser.get_nodes_from_documents([llmadoc])

    # Reverse offset index for page lookup
    def find_page_metadata(offset: int) -> dict:
        for start, combined, meta in reversed(page_metadata):
            if offset >= start:
                return meta
        return page_metadata[0][2] if page_metadata else {}

    def merge_exhibit_refs(metadata_list: list) -> str | None:
        for meta in metadata_list:
            refs = meta.get("visual_refs", [])
            if refs:
                return refs[0]
        return None

    
    # Write chunks
    chunks_path = out_doc_dir / "chunks.jsonl"
    chunk_ids: list[str] = []
    chunk_count = 0

    with open(chunks_path, "w", encoding="utf-8") as f:
        for node in nodes:
            text = node.get_content()
            stripped = text.strip()
            if len(stripped) < MIN_CHUNK_CHARS:
                continue

            # Find offset within full_text
            offset = full_text.find(stripped)
            offset = offset if offset >= 0 else 0
            end = offset + len(stripped)

            # Collect all page metas involved in this chunk
            involved = [
                meta
                for start, combined, meta in page_metadata
                if start < end and (start + len(combined)) > offset
            ]

            primary_meta  = find_page_metadata(offset)
            chunk_id      = f"{doc_id}-{chunk_count}"

            chunk = {
                "chunk_id":      chunk_id,
                "doc_id":        doc_id,
                "text":          stripped,
                "page":          primary_meta.get("page"),
                "chapter":       primary_meta.get("chapter"),
                "section":       primary_meta.get("section"),
                "section_title": primary_meta.get("section_title"),
                "exhibit":       merge_exhibit_refs(involved),
                "chunk_index":   chunk_count,
                "vision_description_used": any(
                    m.get("vision_description_used") for m in involved
                ),
                "source": source_label,
            }
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            chunk_ids.append(chunk_id)
            chunk_count += 1

    # Write chunk_ids.json (for Pipeline F / api_server compatibility)
    chunk_ids_path = out_doc_dir / "chunk_ids.json"
    chunk_ids_path.write_text(json.dumps(chunk_ids, indent=2))

    return {
        "doc_id":       doc_id,
        "chunk_count":  chunk_count,
        "source":       source_label,
        "chunks_path":  str(chunks_path),
        "chunk_ids_path": str(chunk_ids_path),
    }


# ── CLI entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Chunk document pages using LlamaIndex HierarchicalNodeParser + SemanticDoubleMergingSplitterNodeParser"
    )
    parser.add_argument(
        "--doc-id",
        required=True,
        help="Document ID (directory name in ../data/extracted/)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_SIZE,
        help=f"Target chunk size in characters (default: {CHUNK_SIZE})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output parent directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing files",
    )
    args = parser.parse_args()

    doc_id = args.doc_id

    # Pick source: use extracted/{doc_id}/
    plain_dir    = INGESTED_BASE / doc_id

    if not plain_dir.exists():
        print(f"ERROR: No extracted directory found for doc_id={doc_id}", file=sys.stderr)
        sys.exit(1)

    source_dir    = plain_dir
    source_label  = "extracted"

    if args.dry_run:
        page_count = len(list((source_dir / "pages").glob("*.json")))
        print(f"Dry run: would chunk doc_id={doc_id} from {source_label}/ ({page_count} pages)")
        print(f"  chunk_size={args.chunk_size}, out_dir={args.out_dir / doc_id}")
        sys.exit(0)

    result = _chunk_document(
        source_dir=source_dir,
        doc_id=doc_id,
        out_dir=args.out_dir,
        chunk_size=args.chunk_size,
    )

    print(
        f"Done. {result['chunk_count']} chunks written to {result['chunks_path']} "
        f"(source: {result['source']})"
    )


if __name__ == "__main__":
    main()
