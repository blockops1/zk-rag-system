#!/usr/bin/env python3
"""
Pipeline C: Write vision descriptions directly back to extracted/{doc_id}/pages/*.json.

Usage:
    python batch_image_describe.py --workers 2 --threads 28
    python batch_image_describe.py --limit 100        # test run
    python batch_image_describe.py --resume            # skip already-described pages
    python batch_image_describe.py --doc-id XXX       # single doc

Output: Descriptions written to ../data/extracted/{doc_id}/pages/*.json
Log: ../data/logs/pipeline_c_YYYYMMDD_HHMMSS.jsonl
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
import multiprocessing as mp
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty
from typing import Optional

LLAMA_CLI = "/data/llama.cpp/build/bin/llama-mtmd-cli"
MODEL_PATH = "/data/models/vision/smolVLM2-2.2B/SmolVLM2-2.2B-Instruct-Q4_K_M.gguf"
MMPROJ_PATH = "/data/models/vision/smolVLM2-2.2B/mmproj-SmolVLM2-2.2B-Instruct-Q8_0.gguf"
EXTRACTED_BASE = Path("../data/extracted")
IMAGES_BASE = Path("../data/images")
LOGS_DIR = Path("../data/logs")
SUBPROCESS_TIMEOUT = 300  # 5 min per image

# Image size tiers for adaptive generation params: (max_size_kb, max_tokens, ctx_size)
IMAGE_SIZE_TIERS = [
    (5,    32,   512),
    (20,   64,   1024),
    (100,  128,  2048),
    (300,  160,  3072),
    (float("inf"), 200, 4096),
]

# Progress log interval
PROGRESS_LOG_INTERVAL = 100

# Minimum image dimension to be considered a real image (not a decorative element)
MIN_IMAGE_DIMENSION = 50  # px
# Aspect ratio bounds
MAX_ASPECT_RATIO = 8.0
MIN_ASPECT_RATIO = 0.125

# Skip reason codes
SKIP_REASONS = {
    "blank_page": "page has ocr_chars=0 — blank page, nothing to describe",
    "no_image_file": "no image file found for this page in images/ dir",
    "image_too_small": "image dimensions < 50px — decorative element",
    "aspect_ratio_excluded": "image aspect ratio > 8:1 or < 0.125 — decorative artifact",
}


@dataclass
class PageWork:
    doc_id: str
    page_num: int
    image_path: str
    page_file_name: str  # e.g. "0009.json"


# ── Structured JSON logger (module-level, used by subprocesses) ───────────────

def _jlog(lf, level: str, msg: str):
    """Write one structured JSON log line to an already-open file."""
    line = json.dumps({
        "ts": datetime.now().isoformat(timespec="milliseconds") + "Z",
        "level": level,
        "msg": msg
    }, separators=(",", ":"))
    lf.write(line + "\n")
    lf.flush()


# ── Worker ────────────────────────────────────────────────────────────────────

def worker_loop(worker_id: int, work_queue: mp.Queue, result_queue: mp.Queue,
                threads: int, limit: int, log_queue: Optional[mp.Queue] = None):
    """Worker process: pull pages, run SmolVLM2, push results."""
    completed = 0
    start_time = time.time()

    def log(msg):
        if log_queue:
            log_queue.put(("INFO", msg))

    while completed < limit:
        try:
            work = work_queue.get(timeout=5)
        except Empty:
            break

        result = process_single_page(work, threads)
        result_queue.put(result)
        completed += 1

        if completed % PROGRESS_LOG_INTERVAL == 0 and log_queue:
            elapsed = time.time() - start_time
            rate = completed / elapsed * 3600 if elapsed > 0 else 0
            log(f"[worker-{worker_id}] {completed}/{limit} done, ~{rate:.0f} pages/hr")

    log(f"[worker-{worker_id}] finished {completed} pages")


def process_single_page(work: PageWork, threads: int) -> dict:
    """Run SmolVLM2 on one image. Returns a result dict."""

    try:
        size_kb = os.path.getsize(work.image_path) / 1024
    except OSError:
        return {"doc_id": work.doc_id, "page_num": work.page_num,
                "page_file_name": work.page_file_name,
                "description": "", "success": False, "error": "image not found", "chars": 0}

    for max_kb, max_tokens, ctx_size in IMAGE_SIZE_TIERS:
        if size_kb < max_kb:
            break

    prompt = (
        "Describe this document image briefly. "
        "Identify the visual type (photograph, map, chart, diagram, figure, flowchart, organizational chart, etc.) "
        "and summarize the key content in 1-3 sentences."
    )

    cmd = [
        LLAMA_CLI,
        "-m", MODEL_PATH,
        "--mmproj", MMPROJ_PATH,
        "--image", work.image_path,
        "-p", prompt,
        "-c", str(ctx_size),
        "-n", str(max_tokens),
        "-t", str(threads),
        "--no-warmup",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        stdout = result.stdout
        desc_text = re.sub(
            r"(llama_perf|build:).*", "", stdout, flags=re.DOTALL
        ).strip()

        if not desc_text:
            return {"doc_id": work.doc_id, "page_num": work.page_num,
                    "page_file_name": work.page_file_name,
                    "description": "", "success": False,
                    "error": "empty output", "chars": 0}

        return {"doc_id": work.doc_id, "page_num": work.page_num,
                "page_file_name": work.page_file_name,
                "description": desc_text, "success": True, "error": "", "chars": len(desc_text)}

    except subprocess.TimeoutExpired:
        return {"doc_id": work.doc_id, "page_num": work.page_num,
                "page_file_name": work.page_file_name,
                "description": "", "success": False,
                "error": "timeout", "chars": 0}
    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        print(f"  ERROR subprocess {work.doc_id} p{work.page_num}: {e}", flush=True)
        return {"doc_id": work.doc_id, "page_num": work.page_num,
                "page_file_name": work.page_file_name,
                "description": "", "success": False,
                "error": str(e), "chars": 0}


# ── Page JSON writers ─────────────────────────────────────────────────────────

def write_result_to_json(result: dict):
    """Write vision description directly to extracted/{doc_id}/pages/*.json."""
    abs_path = EXTRACTED_BASE / result["doc_id"] / "pages" / result["page_file_name"]

    try:
        with open(abs_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                page_data = json.load(f)
                page_data["vision_description"] = result["description"]
                page_data["vision_model"] = "SmolVLM2-2.2B"
                page_data["vision_timestamp"] = datetime.now().isoformat()
                f.seek(0)
                json.dump(page_data, f, indent=2)
                f.truncate()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError, IOError) as e:
        print(f"  ERROR writing {abs_path}: {e}", flush=True)


def mark_page_skipped(doc_id: str, page_file_name: str, reason: str):
    """Write vision_skipped_reason directly to extracted/{doc_id}/pages/*.json."""
    abs_path = EXTRACTED_BASE / doc_id / "pages" / page_file_name

    try:
        with open(abs_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                page_data = json.load(f)
                if page_data.get("vision_description"):
                    print(f"  SKIP {abs_path}: already described", flush=True)
                    return  # already described
                page_data["vision_skipped_reason"] = reason
                page_data["vision_timestamp"] = datetime.now().isoformat()
                f.seek(0)
                json.dump(page_data, f, indent=2)
                f.truncate()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        print(f"  SKIP ERROR {abs_path}: {reason}", flush=True)


# ── Work queue builder ─────────────────────────────────────────────────────────

def build_work_queue(doc_id: str | None = None, resume: bool = False,
                     limit: int | None = None) -> list[PageWork]:
    """Build list of figure_only pages from extracted/ that have corresponding images."""
    work_items = []

    if doc_id:
        doc_ids = [doc_id]
    else:
        doc_ids = [d.name for d in EXTRACTED_BASE.iterdir() if d.is_dir()]

    # Pre-load manifests for dimension-based filtering
    manifests = {}
    for did in doc_ids:
        manifest_path = IMAGES_BASE / did / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifests[did] = {e["filename"]: (e.get("width", 0), e.get("height", 0))
                                 for e in json.load(f)}
        else:
            manifests[did] = {}

    for did in doc_ids:
        pages_dir = EXTRACTED_BASE / did / "pages"
        img_dir = IMAGES_BASE / did
        if not pages_dir.exists() or not img_dir.exists():
            continue

        img_manifest = manifests.get(did, {})

        for page_file in sorted(pages_dir.glob("*.json")):
            try:
                data = json.loads(page_file.read_text())
            except (json.JSONDecodeError, IOError):
                continue

            if not data.get("figure_only"):
                continue

            # Skip blank pages
            if data.get("ocr_chars", 0) == 0:
                mark_page_skipped(did, page_file.name, "blank_page")
                continue

            # When resuming: skip pages that already have vision_description
            if resume:
                if data.get("vision_description"):
                    continue

            page_num = data.get("page", int(page_file.stem))

            # Pipeline A outputs full-page renders as page_XXXX.png
            image_path_png = img_dir / f"page_{page_num:04d}.png"
            image_path_jpg = img_dir / f"page_{page_num:04d}.jpg"
            if image_path_png.exists():
                image_path = image_path_png
            elif image_path_jpg.exists():
                image_path = image_path_jpg
            else:
                mark_page_skipped(did, page_file.name, "no_image_file")
                continue

            # Aspect filter
            img_basename = image_path.name
            dims = img_manifest.get(img_basename)
            if dims:
                w, h = dims
                if w < MIN_IMAGE_DIMENSION or h < MIN_IMAGE_DIMENSION:
                    mark_page_skipped(did, page_file.name, "image_too_small")
                    continue
                ratio = w / max(h, 1)
                if ratio > MAX_ASPECT_RATIO or ratio < MIN_ASPECT_RATIO:
                    mark_page_skipped(did, page_file.name, "aspect_ratio_excluded")
                    continue

            work_items.append(PageWork(
                doc_id=did,
                page_num=page_num,
                image_path=str(image_path),
                page_file_name=page_file.name,
            ))

    if limit:
        work_items = work_items[:limit]

    return work_items


# ── Pipeline ──────────────────────────────────────────────────────────────────

def log_writer_loop(log_queue: mp.Queue, log_path: Path):
    """Subprocess: writes structured JSON lines to log file."""
    with open(log_path, "a") as lf:
        while True:
            item = log_queue.get()
            if item is None:
                break
            level, msg = item
            _jlog(lf, level, msg)


def result_writer_loop(result_queue: mp.Queue, log_queue: mp.Queue, total: int):
    """Subprocess: writes results to page JSONs and streams progress to log."""
    done = 0
    errors = 0
    start_time = time.time()
    while done < total:
        try:
            result = result_queue.get(timeout=10)
        except Empty:
            continue

        if result["success"]:
            write_result_to_json(result)
        else:
            errors += 1

        done += 1
        log_queue.put(("PAGE", f"{done}/{total} {result['doc_id'][:8]} p{result['page_num']} "
                             f"success={result['success']} chars={result['chars']}"))

        if done % 100 == 0 or done == total:
            elapsed = time.time() - start_time
            rate = done / elapsed * 3600 if elapsed > 0 else 0
            pct = done / total * 100
            log_queue.put(("INFO", f"[{done}/{total} ({pct:.0f}%)] {rate:.0f} pages/hr — err={errors}"))

    log_queue.put(("INFO", f"Total: {done} pages in {time.time()-start_time:.1f}s — Errors: {errors}"))
    log_queue.put(None)


def run_pipeline(workers: int, threads_per_worker: int, limit: int | None,
                doc_id: str | None, resume: bool):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"pipeline_c_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"

    _jlog(sys.stdout, "INFO", "Pipeline C — SmolVLM2 image description")
    _jlog(sys.stdout, "INFO", "  Output: ../data/extracted/")
    _jlog(sys.stdout, "INFO", f"  Workers: {workers} x {threads_per_worker} threads")
    _jlog(sys.stdout, "INFO", f"  Resume mode: {resume}")
    _jlog(sys.stdout, "INFO", f"  Log: {log_path}")

    _jlog(sys.stdout, "INFO", "Building work queue...")
    work_items = build_work_queue(doc_id=doc_id, resume=resume, limit=limit)
    total = len(work_items)
    _jlog(sys.stdout, "INFO", f"  {total} pages to process")

    if total == 0:
        _jlog(sys.stdout, "INFO", "Nothing to do.")
        return

    # Feed queue
    work_queue = mp.Queue()
    for item in work_items:
        work_queue.put(item)

    result_queue = mp.Queue()
    log_queue = mp.Queue()
    start_time = time.time()

    # Start writer/log processes
    writer_proc = mp.Process(target=result_writer_loop,
                             args=(result_queue, log_queue, total))
    writer_proc.start()

    lw_proc = mp.Process(target=log_writer_loop, args=(log_queue, log_path))
    lw_proc.start()

    # Start workers
    per_worker = (total // workers) + 1
    procs = []
    for i in range(workers):
        p = mp.Process(target=worker_loop,
                       args=(i, work_queue, result_queue, threads_per_worker, per_worker, log_queue))
        p.start()
        procs.append(p)

    for p in procs:
        p.join()

    result_queue.put(None)
    writer_proc.join(timeout=30)
    lw_proc.join(timeout=10)

    elapsed = time.time() - start_time
    rate = total / elapsed * 3600 if elapsed > 0 else 0
    _jlog(sys.stdout, "INFO", f"Done. {total} pages in {elapsed:.1f}s ({rate:.0f} pages/hr)")
    _jlog(sys.stdout, "INFO", f"Log: {log_path}")


def main():
    parser = argparse.ArgumentParser(description="Pipeline C: SmolVLM2 vision description")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--threads", type=int, default=28)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--doc-id", default=None)
    args = parser.parse_args()

    run_pipeline(args.workers, args.threads, args.limit, args.doc_id, args.resume)


if __name__ == "__main__":
    import sys
    main()
