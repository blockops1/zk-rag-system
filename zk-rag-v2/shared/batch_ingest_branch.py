#!/usr/bin/env python3
"""
Branch-aware batch ingest for Military Docs RAG.
Reads PDFs from ./data/sourcePDF/{branch}/,
looks up metadata from unified-registry.json, and ingests into
per-branch Qdrant collections (army, navy, marines, coastguard, joint, other).
"""

import argparse
import sys
import time
import os
import json
import logging
import fcntl
import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline_a"))

# Shared PDF processing — no HTTP dependency
from pdf_processing import ingest_pdf


# ── File locking ─────────────────────────────────────────────────
@contextmanager
def locked_file(path: Path, mode: str = "r"):
    """Exclusive-file-lock guard around a file operation.

    Uses a sidecar .lock file to avoid locking the data file itself.
    LOCK_EX + LOCK_NB = non-blocking exclusive lock — fails immediately if
    another process holds it (prevents deadlock when a worker crashes mid-run).

    All callers either read once and return, or write once and exit — the
    critical section is just the file I/O, not any processing.
    """
    lock_path = Path(str(path) + ".lock")
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with open(path, mode) as fh:
                yield fh
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    finally:
        lock_fh.close()
        lock_path.unlink(missing_ok=True)


# Dedup: reuse check_duplicate from harvester
sys.path.insert(0, str(Path(__file__).parent.parent / "pipeline_a"))
sys.path.insert(0, str(Path(__file__).parent.parent / "harvester"))
from check_duplicate import check_duplicate  # noqa: E402

# ── Config ────────────────────────────────────────────────────────
UPLOADS_DIR      = Path("./data/sourcePDF")
REGISTRY_FILE    = Path("./data/registry.json")
V2_REGISTRY_PATH = Path("./data/registry.json")
RAG_API          = "http://127.0.0.1:8100"
DONE_LOG         = Path("./data/batch_ingest_branch_done.json")
EXTRACTION_QUEUE = Path("./data/extraction_queue.json")
EXTRACTION_DONE  = Path("./data/extraction_queue_done.json")
FAILED_RETRY_LOG = Path("./data/ingest_failed_retry.log")

# ── Logging ───────────────────────────────────────────────────────
from _log import get_logger  # noqa: E402
log = get_logger(__name__, log_group="main")


# ── Registry update helper ────────────────────────────────────────────────
def write_v2_registry_update(doc_id: str, fields: dict):
    """Update a doc's registry entry with the given fields."""
    doc_id_norm = "".join(
        c.lower() if c.isalnum() or c == "-" else "-" for c in doc_id
    ).strip("-")
    try:
        v2 = json.load(open(V2_REGISTRY_PATH))
        for doc in v2.get("documents", []):
            dnorm = "".join(
                c.lower() if c.isalnum() or c == "-" else "-" for c in doc.get("doc_id", "")
            ).strip("-")
            if dnorm == doc_id_norm:
                doc.update(fields)
                with open(V2_REGISTRY_PATH, "w", encoding="utf-8") as f:
                    json.dump(v2, f, indent=2, ensure_ascii=False)
                return
    except Exception as e:
        log.warning("Could not update registry for %s: %s", doc_id, e)


# ── Registry lookup ────────────────────────────────────────────────
def load_unified_registry():
    """Build filename -> {branch, category, title, doc_id} lookup from v2 registry."""
    if not REGISTRY_FILE.exists():
        return {}
    r = json.load(open(REGISTRY_FILE))
    lookup = {}
    for doc in r.get("documents", []):
        fname = Path(doc.get("local_path", "")).name
        if fname and "________" not in fname:
            lookup[fname] = {
                "branch": doc.get("branch", "other"),
                "category": doc.get("category", "other"),
                "title": doc.get("title", fname),
                "doc_id": doc.get("doc_id", Path(fname).stem.lower()),
                "local_path": doc.get("local_path", ""),
            }
    return lookup


def get_metadata(fname, branch):
    """Look up metadata for a file from the v2 registry, falling back to branch-based defaults."""
    info = unified_lookup.get(fname, {})
    return {
        "branch": branch,
        "category": info.get("category", "other"),
        "title": info.get("title", fname),
        "doc_type": info.get("doc_type", "field_manual"),
        "doc_id": info.get("doc_id", Path(fname).stem.lower()),
    }


def build_dedup_registry():
    """Build a doc_id -> entry lookup from Qdrant collections.

    Queries all Qdrant collections via the RAG API to get doc_ids that have
    already been indexed, then looks up their metadata from the v2 registry.
    This correctly identifies ingested docs regardless of whether the API
    has ever written back to the registry.
    """
    reg = {}
    try:
        resp = httpx.get(f"{RAG_API}/collections", timeout=15)
        resp.raise_for_status()
        collections_data = resp.json()
    except Exception as e:
        log(f"WARNING: Could not fetch Qdrant collections: {e}", level=logging.WARNING)
        return reg

    # Collect all doc_ids from all collections
    all_doc_ids = set()
    for coll in collections_data:
        coll_name = coll.get("name")
        if not coll_name:
            continue
        for doc_id in coll.get("doc_ids", []):
            all_doc_ids.add(doc_id)

    if not all_doc_ids:
        log("Dedup registry: no doc_ids found in Qdrant collections")
        return reg

    # Load v2 registry and build doc_id -> entry lookup
    if REGISTRY_FILE.exists():
        try:
            v2 = json.load(open(REGISTRY_FILE))
            for doc in v2.get("documents", []):
                doc_id = doc.get("doc_id")
                if doc_id in all_doc_ids:
                    reg[doc_id] = doc
        except Exception as e:
            log(f"WARNING: Could not load v2 registry: {e}", level=logging.WARNING)

    return reg


def build_sha256_lookup():
    """Build a sha256 -> doc_id reverse index from the v2 registry.

    IMPORTANT: Only index docs where Pipeline A has ALREADY RUN successfully
    (status=extracted or status=ingested). The SHA256 in the registry is set at
    download time — it only proves the file exists on disk, NOT that Pipeline A
    has processed it. Indexing 'downloaded' entries would cause false deduplication
    of files that never made it through extraction.

    This is Layer 1 of Pipeline A's dedup gate: "has Pipeline A already run?"
    Use status as the source of truth, not SHA256 presence alone.
    """
    sha256_lookup = {}
    if REGISTRY_FILE.exists():
        try:
            v2 = json.load(open(REGISTRY_FILE))
            for doc in v2.get("documents", []):
                sha = doc.get("sha256")
                status = doc.get("status", "")
                # Only skip if Pipeline A has already produced output
                if sha and status in ("extracted", "ingested"):
                    sha256_lookup[sha] = doc.get("doc_id", "")
        except Exception as e:
            log(f"WARNING: Could not build sha256 lookup: {e}", level=logging.WARNING)
    return sha256_lookup


def compute_sha256(pdf_path: Path) -> str:
    """Compute SHA256 hash of a PDF file."""
    sha = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


# Build dedup registry and sha256 lookup once at startup
dedup_registry = build_dedup_registry()
log(f"Dedup registry: {len(dedup_registry)} ingested entries (from Qdrant)")

sha256_lookup = build_sha256_lookup()
# Count how many total entries have SHA256 (for log clarity)
total_with_sha = sum(
    1 for doc in json.load(open(REGISTRY_FILE)).get("documents", [])
    if doc.get("sha256")
)
log(f"SHA256 lookup: {len(sha256_lookup)} entries (status=extracted/ingested)")
log(f"  ({total_with_sha} total docs have SHA256 in registry; {total_with_sha - len(sha256_lookup)} are status=downloaded — will be processed)")


# ── API helpers ───────────────────────────────────────────────────
def check_api():
    try:
        r = httpx.get(f"{RAG_API}/health", timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def extract_one(pdf_path, metadata, timeout=3600):
    """Pipeline A: extract-only — writes page JSONs to ingested/{doc_id}/, no Qdrant writes.

    Calls ingest_pdf() directly (no HTTP). For low-density PDFs, returns needs_docling=True
    and adds doc to extraction_queue. For high-density, writes pages and updates registry.
    """
    raw_doc_id = metadata.get("doc_id", Path(pdf_path).stem.lower())
    doc_id = "".join(c.lower() if c.isalnum() or c == "-" else "-" for c in raw_doc_id).strip("-")
    branch = metadata.get("branch", "other")
    out_dir = Path("./data/extracted") / doc_id
    images_base_dir = Path("./data/images")

    try:
        page_count, needs_docling = ingest_pdf(
            pdf_path=pdf_path,
            doc_id=doc_id,
            out_dir=out_dir,
            images_base_dir=images_base_dir,
            skip_ocr=True,  # always fitz only in Pipeline A
        )
    except Exception as e:
        return {"status": "error", "doc_id": doc_id, "branch": branch, "detail": str(e)[:200]}

    # Measure avg chars for reporting
    import fitz
    doc_tmp = fitz.open(str(pdf_path))
    total_chars = sum(len(doc_tmp[p].get_text('text')) for p in range(page_count))
    avg_chars = total_chars / page_count if page_count > 0 else 0
    doc_tmp.close()

    if needs_docling:
        # Low-density: pages NOT written, added to extraction queue for Pipeline B
        add_to_extraction_queue(doc_id)
        return {
            "status": "needs_docling",
            "doc_id": doc_id,
            "branch": branch,
            "page_count": page_count,
            "needs_docling": True,
            "pages_written": False,
            "avg_chars_per_page": round(avg_chars, 1),
        }

    # High-density: pages written. Update registry to "extracted".
    write_v2_registry_update(doc_id, {
        "status": "extracted",
        "page_count": page_count,
        "avg_chars_per_page": round(avg_chars, 1),
    })
    return {
        "status": "ok",
        "doc_id": doc_id,
        "branch": branch,
        "page_count": page_count,
        "needs_docling": False,
        "pages_written": True,
        "avg_chars_per_page": round(avg_chars, 1),
    }


# ── Load registries ────────────────────────────────────────────────
unified_lookup = load_unified_registry()
log(f"Unified registry: {len(unified_lookup)} entries")


def send_telegram(msg):
    os.system(f'openclaw message send --channel telegram --target 374999219 --message "{msg}" 2>/dev/null')


def load_done():
    if not DONE_LOG.exists():
        return set()
    with locked_file(DONE_LOG, "r") as fh:
        return set(json.load(fh).get("done", []))


def save_done(done):
    with locked_file(DONE_LOG, "w") as fh:
        fh.write(json.dumps({"done": sorted(done)}, indent=2))


# ── Extraction queue (Pipeline B source) ─────────────────────────────────────
def load_extraction_queue():
    """Returns list of doc_ids that need Pipeline B docling."""
    if not EXTRACTION_QUEUE.exists():
        return []
    with locked_file(EXTRACTION_QUEUE, "r") as fh:
        return list(json.load(fh).get("extraction_queue", []))


def save_extraction_queue(doc_ids):
    with locked_file(EXTRACTION_QUEUE, "w") as fh:
        fh.write(json.dumps({"extraction_queue": doc_ids}, indent=2))


def load_extraction_done():
    """Returns set of doc_ids that have completed Pipeline B."""
    if not EXTRACTION_DONE.exists():
        return set()
    with locked_file(EXTRACTION_DONE, "r") as fh:
        return set(json.load(fh).get("done", []))


def save_extraction_done(doc_ids):
    with locked_file(EXTRACTION_DONE, "w") as fh:
        fh.write(json.dumps({"done": sorted(doc_ids)}, indent=2))


def add_to_extraction_queue(doc_id):
    """Atomically append doc_id to extraction_queue if not already present.

    Holds an exclusive blocking lock for the full read-modify-write cycle
    to prevent two processes from racing and overwriting each other's entries.
    """
    lock_path = Path(str(EXTRACTION_QUEUE) + ".lock")
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)        # block until acquired
        # Read-modify-write while holding the lock
        if EXTRACTION_QUEUE.exists():
            lst = json.load(open(EXTRACTION_QUEUE)).get("extraction_queue", [])
        else:
            lst = []
        if doc_id not in lst:
            lst.append(doc_id)
            EXTRACTION_QUEUE.write_text(json.dumps({"extraction_queue": lst}, indent=2))
    finally:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()
        lock_path.unlink(missing_ok=True)


# ── Failed-retry log ────────────────────────────────────────────────────────
def load_failed_retry():
    """Returns list of {doc_id, error_type, timestamp} entries."""
    if not FAILED_RETRY_LOG.exists():
        return []
    with locked_file(FAILED_RETRY_LOG, "r") as fh:
        return list(json.load(fh).get("failed", []))


def save_failed_retry(failed_list):
    with locked_file(FAILED_RETRY_LOG, "w") as fh:
        fh.write(json.dumps({"failed": failed_list}, indent=2))


def add_to_failed_retry(doc_id, error_type):
    """Atomically add or update a failure entry."""
    lock_path = Path(str(FAILED_RETRY_LOG) + ".lock")
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)   # block until acquired
        if FAILED_RETRY_LOG.exists() and FAILED_RETRY_LOG.stat().st_size > 0:
            failed = json.load(open(FAILED_RETRY_LOG)).get("failed", [])
        else:
            failed = []
        for entry in failed:
            if entry["doc_id"] == doc_id:
                entry["error_type"] = error_type
                entry["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                break
        else:
            failed.append({
                "doc_id": doc_id,
                "error_type": error_type,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            })
        FAILED_RETRY_LOG.write_text(json.dumps({"failed": failed}, indent=2))
    finally:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()
        lock_path.unlink(missing_ok=True)


def remove_from_failed_retry(doc_id):
    """Atomically remove a failure entry."""
    lock_path = Path(str(FAILED_RETRY_LOG) + ".lock")
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)   # block until acquired
        if FAILED_RETRY_LOG.exists():
            failed = json.load(open(FAILED_RETRY_LOG)).get("failed", [])
        else:
            failed = []
        failed = [e for e in failed if e["doc_id"] != doc_id]
        FAILED_RETRY_LOG.write_text(json.dumps({"failed": failed}, indent=2))
    finally:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()
        lock_path.unlink(missing_ok=True)


# ── Docling helper (runs directly, no HTTP timeout) ───────────────────────
def run_docling_direct(pdf_path: Path, timeout: int = 7200) -> tuple[str, int]:
    """Run docling directly as a subprocess. Returns (text, page_count).
    Writes sentinel files so the result can be detected even on timeout."""
    docling_py = "./venv-docling/bin/python3"
    out_txt = "/tmp/_docling_out.txt"
    page_txt = "/tmp/_docling_pages.txt"
    done_sentinel = "/tmp/_docling_done.txt"
    for f in [out_txt, page_txt, done_sentinel]:
        Path(f).unlink(missing_ok=True)

    # Single-threaded: prevents hangs from OpenMP/RapidOCR concurrency conflicts
    # OMP_NUM_THREADS=1 + all batch sizes = 1 means one document at a time
    # AcceleratorOptions num_threads=1 keeps pypdfium2 rendering sequential
    code = (
        "import os;"
        "os.environ['OMP_NUM_THREADS']='1';"
        "os.environ['DOCLING_NUM_THREADS']='1';"
        "from docling.document_converter import DocumentConverter, PdfFormatOption;"
        "from docling.datamodel.base_models import InputFormat;"
        "from docling.datamodel.pipeline_options import PdfPipelineOptions, AcceleratorOptions, RapidOcrOptions;"
        "from docling.datamodel.settings import settings;"
        "from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend;"
        "settings.debug.profile_pipeline_timings = False;"
        "settings.perf.doc_batch_concurrency = 1;"
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
        "    ocr_batch_size=1,"
        "    layout_batch_size=1,"
        "    table_batch_size=1,"
        "    accelerator_options=AcceleratorOptions(num_threads=1, device='cpu'),"
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
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"docling failed (code {proc.returncode}): {stderr[-300:]}")
    text = open(out_txt, encoding="utf-8").read()
    page_count = int(open(page_txt).read().strip())
    return text, page_count


# ── Docling → page files (Pipeline B, no Qdrant) ──────────────────────────
def write_docling_pages(doc_id: str, branch: str, pdf_path: Path) -> dict:
    """Run docling OCR and write page JSONs to ingested/{doc_id}/.

    Pipeline B workhorse. Writes the same page JSON
    format that ingest_pdf() produces for the docling path, then updates the registry.
    Does NOT touch Qdrant.
    """
    try:
        docling_text, page_count = run_docling_direct(pdf_path, timeout=7200)
    except Exception as e:
        return {"status": "error", "doc_id": doc_id, "detail": str(e)}

    out_dir = Path("./data/extracted") / doc_id
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    # Write docling text to page 1's JSON (matching ingest_pdf docling path)
    # docling returns consolidated text — page boundaries are lost, so page 1 gets all
    for page_num in range(page_count):
        if page_num == 0:
            page_text = docling_text.strip()
        else:
            page_text = ""
        visual_refs = re.findall(r'(Figure|Photo)\s+\S+', page_text)
        page_data = {
            "page": page_num + 1,
            "text": page_text,
            "chapter": None,
            "section": None,
            "section_title": None,
            "visual_refs": visual_refs,
            "figure_only": len(visual_refs) > 0,
            "ocr_source": "docling",
            "ocr_chars": len(docling_text),
        }
        (pages_dir / f"{page_num:04d}.json").write_text(
            json.dumps(page_data, indent=2, ensure_ascii=False)
        )

    # Write manifest (matching ingest_pdf output)
    sha256 = compute_sha256(pdf_path)
    manifest = {
        "doc_id": doc_id,
        "title": pdf_path.stem,
        "page_count": page_count,
        "source_pdf": str(pdf_path),
        "sha256": sha256,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ocr_used": "docling",
        "avg_chars_per_page": round(len(docling_text) / page_count, 1) if page_count > 0 else 0,
        "needs_docling": False,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)
    )

    # Update extraction done log
    extraction_done_ids = load_extraction_done()
    extraction_done_ids.add(doc_id)
    save_extraction_done(sorted(extraction_done_ids))

    # Update registry to extracted
    try:
        v2 = json.load(open(V2_REGISTRY_PATH))
        doc_id_norm = "".join(c.lower() if c.isalnum() or c == "-" else "-" for c in doc_id).strip("-")
        for doc in v2.get("documents", []):
            doc_norm = "".join(c.lower() if c.isalnum() or c == "-" else "-" for c in doc.get("doc_id", "")).strip("-")
            if doc_norm == doc_id_norm:
                doc["status"] = "extracted"
                doc["page_count"] = page_count
                with open(V2_REGISTRY_PATH, "w", encoding="utf-8") as f:
                    json.dump(v2, f, indent=2, ensure_ascii=False)
                break
    except Exception as e:
        print(f"Warning: could not update v2 registry for {doc_id}: {e}")

    return {"status": "ok", "doc_id": doc_id, "page_count": page_count}


# ── Reindex helper (for Pass 2) ────────────────────────────────────────────
def reindex_one(doc_id, branch, timeout=3600):
    """Call /reindex with force_ocr=True for a doc that needs docling."""
    payload = {
        "doc_id": doc_id,
        "collection": branch,
        "force_ocr": True,
    }
    try:
        r = httpx.post(f"{RAG_API}/reindex", json=payload, timeout=timeout)
    except httpx.TimeoutException:
        return {"status": "timeout", "doc_id": doc_id}
    if r.status_code == 200:
        result = r.json()
        return {
            "status": "ok",
            "doc_id": doc_id,
            "chunk_count": result.get("chunk_count"),
            "page_count": result.get("page_count"),
        }
    elif r.status_code == 404:
        return {"status": "not_found", "doc_id": doc_id, "detail": r.text[:200]}
    else:
        return {"status": "error", "doc_id": doc_id, "code": r.status_code, "detail": r.text[:200]}


# ── Docling + Qdrant upsert (for Pipeline B daemon) ──────────────────────

# ── Main ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Branch-aware military docs batch ingest")
    parser.add_argument("limit", nargs="?", default="all")
    parser.add_argument("--pass", dest="pass_num", type=int, default=1)
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retry true failures from ingest_failed_retry.log before new work")
    parser.add_argument("--force", action="store_true",
                        help="Skip SHA256 dedup gate — force re-ingest of all pending files "
                             "regardless of whether their SHA256 appears in the registry. "
                             "Use for fresh Qdrant starts where the registry SHA256 list "
                             "reflects prior (now-archived) ingest attempts, not current Qdrant state.")
    args = parser.parse_args()

    if args.limit == "all":
        limit = None
    else:
        limit = int(args.limit)

    # ── Pipeline B: Docling daemon (long-running queue worker) ───────────────
    if args.pass_num == 2:
        # Build doc_id → {branch, local_path} lookup from unified registry
        doc_info_map = {}
        for fname, info in unified_lookup.items():
            doc_info_map[info["doc_id"]] = info

        log("=== Pipeline B: Docling daemon started — watching needs_docling_queue ===")
        send_telegram("Pipeline B: Docling daemon started")

        # ── retry_batch helper (called inside the daemon loop) ─────────────────
        def _retry_batch():
            failed_entries = load_failed_retry()
            if not failed_entries:
                return
            log(f"=== Retrying {len(failed_entries)} failed docs from ingest_failed_retry.log ===")
            recovered = []
            still_failing = []
            for entry in failed_entries:
                doc_id = entry["doc_id"]
                info = doc_info_map.get(doc_id, {})
                if not info:
                    # doc_id not found in registry — search disk by doc_id prefix
                    found = None
                    for branch in ["army", "navy", "marines", "airforce", "coast_guard", "other"]:
                        for pdf_path in sorted((UPLOADS_DIR / branch).glob("*.pdf")):
                            if pdf_path.stem.lower().endswith(doc_id[:16].lower()):
                                found = (branch, pdf_path)
                                break
                        if found:
                            break
                    if found:
                        branch, pdf_path = found
                        log(f"  Retry {doc_id[:16]}...: registry miss, found {pdf_path.name}")
                    else:
                        log(f"  Retry {doc_id[:16]}...: NOT FOUND on disk", logging.ERROR)
                        continue
                else:
                    branch = info.get("branch", "other")
                    local_path = info.get("local_path", "")
                    pdf_path = Path(local_path) if local_path else (UPLOADS_DIR / branch / info.get("filename", doc_id + ".pdf"))
                    log(f"  Retry: {doc_id}")
                result = write_docling_pages(doc_id, branch, pdf_path)
                if result["status"] == "ok":
                    recovered.append(doc_id)
                    remove_from_failed_retry(doc_id)
                    send_telegram(f"✓ Retry {branch}/{doc_id[:12]} recovered")
                else:
                    still_failing.append(doc_id)
                    add_to_failed_retry(doc_id, result.get("status"))
                    detail = result.get("detail", "")
                    log(f"  Retry FAIL: {result.get('status')} — {detail}", logging.ERROR)
                time.sleep(2)
            log(f"  Retry done: {len(recovered)} recovered, {len(still_failing)} still failing")

        while True:
            # Retry any previously-failed docs before picking new queue work
            if args.retry_failed:
                _retry_batch()

            extraction_queue_ids = load_extraction_queue()

            if not extraction_queue_ids:
                log("Queue empty — sleeping 60s before next poll")
                time.sleep(60)
                continue

            # Pop the first doc from the queue
            doc_id = extraction_queue_ids.pop(0)
            save_extraction_queue(extraction_queue_ids)

            # Skip if already processed by a previous B run
            extraction_done = load_extraction_done()
            if doc_id in extraction_done:
                log(f"  SKIP {doc_id}: already in extraction_queue_done — removing from queue")
                continue

            info = doc_info_map.get(doc_id, {})
            branch = info.get("branch", "other")
            local_path = info.get("local_path", "")
            pdf_path = Path(local_path) if local_path else (UPLOADS_DIR / info.get("branch", "other") / info.get("filename", doc_id + ".pdf"))

            log(f"[Queue {len(extraction_queue_ids)+1} remaining] Pipeline B: docling {doc_id} ({branch})")

            # Run docling directly (bypasses HTTP timeout), write page JSONs to ingested/
            result = write_docling_pages(doc_id, branch, pdf_path)

            if result["status"] == "ok":
                remove_from_failed_retry(doc_id)
                send_telegram(f"✓ {branch}/{doc_id} — docling done, {result.get('page_count')} pages written to ingested/")
                log(f"  Done: {doc_id}")
            else:
                add_to_failed_retry(doc_id, result.get("status"))
                log(f"  FAIL: {result.get('status')} — added to failed_retry", logging.ERROR)

            time.sleep(2)

    # ── Pass 1 (default) or --retry-failed ─────────────────────────────────
    log(f"=== Branch-aware ingest started — PASS {args.pass_num} ===")

    time.sleep(3)

    if not check_api():
        log("ERROR: RAG API not reachable — aborting", logging.ERROR)
        send_telegram(" mil-docs ingest: RAG API unreachable, batch skipped")
        sys.exit(1)

    done = load_done()
    pending = []

    # Collect PDFs by branch
    for branch in sorted(os.listdir(UPLOADS_DIR)):
        branch_dir = UPLOADS_DIR / branch
        if not branch_dir.is_dir():
            continue
        for fname in sorted(os.listdir(branch_dir)):
            if not fname.endswith(".pdf"):
                continue
            pdf_path = branch_dir / fname
            if pdf_path in done:
                continue
            pending.append((branch, fname, pdf_path))

    if not pending:
        log("No pending files — nothing to do")
        sys.exit(0)

    if limit:
        pending = pending[:limit]

    log(f"PASS {args.pass_num}: {len(pending)} docs to process (limit: {limit or 'all'})")

    ingested = []
    failed = []
    needs_docling = []

    for i, (branch, fname, pdf_path) in enumerate(pending):
        metadata = get_metadata(fname, branch)
        doc_id = metadata["doc_id"]

        log(f"[{i+1}/{len(pending)}] {branch}/{fname}")

        # Dedup gate: Layer 1 — SHA256 exact match
        # Bypass with --force (used for fresh Qdrant starts where registry SHA256s
        # reflect prior archived ingest attempts, not current Qdrant state)
        if not args.force:
            try:
                sha = compute_sha256(pdf_path)
            except Exception as e:
                log(f"  SHA256 failed for {fname}: {e} — skipping")
                done.add(str(pdf_path))
                continue
            if sha in sha256_lookup:
                matched_doc_id = sha256_lookup[sha]
                log(f"  DEDUP {fname}: SHA256 match with {matched_doc_id[:16]} (Pipeline A already ran) — skipping")
                done.add(str(pdf_path))
                continue

        # Dedup gate: Layer 2 — title + pub_year similarity
        title = metadata.get("title", "")
        pub_year = metadata.get("pub_year") or 0
        is_dup, dup_id, score = check_duplicate(title, pub_year, dedup_registry, threshold=0.75)
        if is_dup:
            log(f"  DEDUP {fname}: title match ({score:.2f}) with existing {dup_id[:16]} — skipping")
            done.add(str(pdf_path))
            continue

        result = extract_one(pdf_path, metadata)

        if result["status"] == "ok":
            # High-density: page JSONs written, registry updated to "extracted" by /extract
            ingested.append(str(pdf_path))
            done.add(str(pdf_path))
            send_telegram(f"✓ {branch}/{fname} — {result.get('page_count')} pages, avg {result.get('avg_chars_per_page')} chars/page")
        elif result["status"] == "needs_docling":
            # Low-density: page files NOT written (skip_ocr path), added to extraction_queue
            ingested.append(str(pdf_path))
            done.add(str(pdf_path))
            needs_docling.append(doc_id)
            # add_to_extraction_queue already called inside /extract for low-density
            send_telegram(f"~ {branch}/{fname} — fitz low-density ({result.get('avg_chars_per_page')} chars/page), queued for docling")
        else:
            # True failure — save to retry log
            failed.append(fname)
            add_to_failed_retry(doc_id, result.get("status"))
            log(f"  ERROR: {result}", logging.ERROR)

        time.sleep(2)  # rate limit

    log(f"\nDone: {len(ingested)} ingested, {len(failed)} failed, {len(needs_docling)} need docling")
    save_done(done)

    if needs_docling:
        log(f"\n{len(needs_docling)} docs need Pass 2 (docling): {needs_docling[:5]}{'...' if len(needs_docling)>5 else ''}")
        send_telegram(f" mil-docs: {len(needs_docling)} docs flagged for Pass 2 (docling)")

    if failed:
        send_telegram(f" mil-docs ingest: {len(failed)} failed — check logs")


if __name__ == "__main__":
    main()
