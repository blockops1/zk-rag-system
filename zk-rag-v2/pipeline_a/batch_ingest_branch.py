#!/usr/bin/env python3
"""
batch_ingest_branch.py — Pipeline A: fitz-only extraction for all 572 docs.

Reads ../data/registry.json, processes all docs with status=ok,
writes page JSONs to extracted/{doc_id}/pages/ and images to images/{doc_id}/,
then updates registry status: ok → extracted.

Parallelism: 4 workers (I/O bound, cap prevents memory pressure).
"""

import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pdf_processing import ingest_pdf

# ─── Config ───────────────────────────────────────────────────────────────
REGISTRY_PATH = Path("../data/registry.json")
SOURCE_PDFS   = Path("../data/sourcePDF")
EXTRACTED_DIR = Path("../data/extracted")
IMAGES_DIR    = Path("../data/images")
DONE_LOG      = Path("../data/pipeline_a_done.json")
LOG_DIR       = Path("./logs")
LOG_FILE      = LOG_DIR / f"pipeline_a_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

MAX_WORKERS   = 4

# Branch subdirs
BRANCH_DIRS = {
    "army":      SOURCE_PDFS / "army",
    "navy":      SOURCE_PDFS / "navy",
    "marines":   SOURCE_PDFS / "marines",
    "airforce":  SOURCE_PDFS / "airforce",
    "coastguard":SOURCE_PDFS / "coastguard",
    "joint":     SOURCE_PDFS / "joint",
    "other":     SOURCE_PDFS / "other",
}

# ─── Logging ─────────────────────────────────────────────────────────────
LOG_DIR.mkdir(exist_ok=True, parents=True)

class JSONLogger:
    """One JSON line per event — machine-parseable, no buffering."""
    def __init__(self, path: Path):
        self._fh = open(path, "a", encoding="utf-8")
        self._fh.write(f'{{"ts":"{datetime.now(timezone.utc).isoformat()}","event":"start"}}\n')
        self._fh.flush()

    def log(self, level: str, msg: str, **kwargs):
        d = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg": msg,
        }
        d.update(kwargs)
        self._fh.write(json.dumps(d) + "\n")
        self._fh.flush()
        print(f"[{level}] {msg}", file=sys.stderr, **kwargs)

    def info(self, msg, **kw): self.log("INFO", msg, **kw)
    def warn(self, msg, **kw): self.log("WARN", msg, **kw)
    def error(self, msg, **kw): self.log("ERROR", msg, **kw)
    def close(self):
        self._fh.write(f'{{"ts":"{datetime.now(timezone.utc).isoformat()}","event":"done"}}\n')
        self._fh.close()

logger = JSONLogger(LOG_FILE)

# ─── Helpers ─────────────────────────────────────────────────────────────

def load_done() -> set[str]:
    if not DONE_LOG.exists():
        return set()
    return set(json.load(open(DONE_LOG)).get("done", []))


_done_lock = threading.Lock()

def save_done(done: set[str]):
    with _done_lock:
        # Atomic write: temp file + rename
        tmp = DONE_LOG.with_suffix(".tmp")
        json.dump({"done": sorted(done)}, open(tmp, "w"), indent=2)
        tmp.rename(DONE_LOG)


def load_registry() -> list[dict]:
    return json.load(open(REGISTRY_PATH))["documents"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def find_pdf(doc: dict) -> Path | None:
    """Locate the PDF on disk for a registry entry."""
    fname = doc.get("filename", "")
    if not fname:
        return None
    for b, bdir in BRANCH_DIRS.items():
        candidate = bdir / fname
        if candidate.exists():
            return candidate
    return None


def update_registry_status(doc_ids: list[str], new_status: str):
    """Update status field for matching docs."""
    docs = load_registry()
    updated = 0
    for doc in docs:
        if doc.get("doc_id") in doc_ids or doc.get("sha256") in doc_ids:
            doc["status"] = new_status
            updated += 1
    json.dump({"documents": docs}, open(REGISTRY_PATH, "w"), indent=2)
    logger.info(f"Registry updated: {updated} docs → {new_status}")


# ─── Per-doc worker ─────────────────────────────────────────────────────

def process_one(doc: dict) -> dict:
    """Extract one PDF. Returns result dict for the caller."""
    doc_id = doc.get("doc_id", "")
    fname = doc.get("filename", "unknown")

    # Skip if already done
    done = load_done()
    if fname in done or doc_id in done:
        return {"status": "already_done", "doc_id": doc_id, "filename": fname}

    # Find PDF on disk
    pdf_path = find_pdf(doc)
    if pdf_path is None or not pdf_path.exists():
        return {"status": "file_not_found", "doc_id": doc_id, "filename": fname}

    # Verify SHA256 matches registry
    try:
        real_sha = sha256_of(pdf_path)
    except Exception as e:
        return {"status": "sha_error", "doc_id": doc_id, "filename": fname, "detail": str(e)}

    reg_sha = doc.get("sha256", "")
    if reg_sha and real_sha != reg_sha:
        return {
            "status": "sha_mismatch",
            "doc_id": doc_id,
            "filename": fname,
            "expected": reg_sha[:16],
            "got": real_sha[:16],
        }

    # Use SHA256 as doc_id (deterministic, content-based)
    doc_id = real_sha

    out_dir = EXTRACTED_DIR / doc_id
    images_base_dir = IMAGES_DIR

    try:
        result = ingest_pdf(
            pdf_path=pdf_path,
            doc_id=doc_id,
            out_dir=out_dir,
            images_base_dir=images_base_dir,
        )
        return {
            "status": "ok",
            "doc_id": doc_id,
            "filename": fname,
            "page_count": result["page_count"],
            "avg_chars": result["avg_chars_per_page"],
            "images_written": result["images_written"],
        }
    except Exception as e:
        import traceback
        return {
            "status": "error",
            "doc_id": doc_id,
            "filename": fname,
            "detail": str(e)[:200],
            "traceback": traceback.format_exc()[:500],
        }


# ─── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max docs to process")
    parser.add_argument("--force", action="store_true", help="Re-process even if done")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Concurrent workers")
    args = parser.parse_args()

    logger.info(f"Pipeline A start — {args.workers} workers, limit={args.limit or 'all'}")

    # Load registry
    all_docs = load_registry()
    ok_docs = [d for d in all_docs if d.get("status") == "ok"]
    logger.info(f"Registry: {len(ok_docs)} docs with status=ok")

    # Load done set
    done = load_done()
    logger.info(f"Already done: {len(done)}")

    # Build work list
    if args.force:
        work = ok_docs
        logger.info("Force mode: re-processing all ok docs")
    else:
        work = [d for d in ok_docs if d.get("filename") not in done and d.get("doc_id") not in done]
        logger.info(f"Pending work: {len(work)} docs")

    if args.limit:
        work = work[: args.limit]

    if not work:
        logger.info("No work to do.")
        return

    # Process with thread pool
    results = {"ok": [], "error": [], "already_done": [], "file_not_found": [], "sha_mismatch": []}
    done_this_run = set(done)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_one, doc): doc for doc in work}

        for future in as_completed(futures):
            doc = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = {"status": "exception", "doc_id": doc.get("doc_id"), "detail": str(e)}

            status = result["status"]
            results.get(status, results["error"]).append(result)

            if status == "ok":
                done_this_run.add(result["filename"])
                done_this_run.add(result["doc_id"])
                save_done(done_this_run)
                logger.info(
                    f'OK  {result["filename"]}  pages={result["page_count"]}  '
                    f'avg={result["avg_chars"]}  imgs={result["images_written"]}'
                )
            elif status == "already_done":
                logger.info(f'SKIP {result["filename"]} — already done')
            elif status == "file_not_found":
                logger.error(f'NOT_FOUND {result["filename"]}')
            elif status == "sha_mismatch":
                logger.error(
                    f'SHA_MISMATCH {result["filename"]}  '
                    f'expected={result["expected"]} got={result["got"]}'
                )
            elif status == "exception":
                logger.error(
                    f'THREAD_ERROR {result.get("filename","?")}  {result.get("detail","?")}  '
                    f'tb={result.get("traceback","?")[:200]}'
                )
            else:
                logger.error(f'ERROR {result.get("filename","?")}  {result.get("detail","?")}')

    # Final summary
    ok_ids = [r["doc_id"] for r in results["ok"]]
    if ok_ids:
        update_registry_status(ok_ids, "extracted")

    total = sum(len(v) for v in results.values())
    logger.info(
        f"Pipeline A complete — "
        f"ok={len(results['ok'])}  "
        f"error={len(results['error'])}  "
        f"file_not_found={len(results['file_not_found'])}  "
        f"sha_mismatch={len(results['sha_mismatch'])}  "
        f"total={total}"
    )

    # Telegram notification
    err_count = len(results["error"]) + len(results["file_not_found"]) + len(results["sha_mismatch"])
    if err_count == 0:
        msg = f"✅ Pipeline A done: {len(results['ok'])} docs extracted, 0 errors"
    else:
        msg = f"⚠️ Pipeline A done: {len(results['ok'])} ok, {err_count} errors — check logs"
    os.system(
        f'openclaw message send --channel telegram --target 374999219 '
        f'--message "{msg}" 2>/dev/null'
    )

    logger.close()


if __name__ == "__main__":
    main()
