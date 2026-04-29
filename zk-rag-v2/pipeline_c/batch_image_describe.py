#!/usr/bin/env python3
"""
Pipeline C: Parallel batch image description using SmolVLM2 on CPU.

COPY-FIRST architecture: copies from ingested/{doc_id}/ to {output_dir}/{doc_id}/
before processing. The copy is modified, never the original.

Usage:
    python batch_image_describe.py --output-dir $DATA_DIR/extracted-vision    # default
    python batch_image_describe.py --workers 2 --threads 28
    python batch_image_describe.py --limit 100        # test run
    python batch_image_describe.py --resume            # skip already-described pages
    python batch_image_describe.py --doc-id XXX       # single doc

Output: Descriptions written to {output_dir}/{doc_id}/pages/*.json (the COPY).
Original ingested/{doc_id}/ is NEVER modified by this pipeline.
"""

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
import multiprocessing as mp
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty

LLAMA_CLI = "/data/llama.cpp/build/bin/llama-mtmd-cli"
MODEL_PATH = "/data/models/vision/smolVLM2-2.2B/SmolVLM2-2.2B-Instruct-Q4_K_M.gguf"
MMPROJ_PATH = "/data/models/vision/smolVLM2-2.2B/mmproj-SmolVLM2-2.2B-Instruct-Q8_0.gguf"
INGESTED_BASE = Path("$DATA_DIR/extracted")
IMAGES_BASE = Path("$DATA_DIR/images")
LOGS_DIR = Path("$REPO_DIR/logs")
SUBPROCESS_TIMEOUT = 300  # 5 min per image — needed for complex images

# Skip reason codes written to page JSON when a figure_only page is filtered
SKIP_REASONS = {
    "ocr_chars_exceeded": "page has ocr_chars > 500 — text page misclassified as figure",
    "no_image_file": "no image file found for this page in images/ dir",
    "image_too_small": "image dimensions < 50px — decorative element",
    "aspect_ratio_excluded": "image aspect ratio > 8:1 or < 0.125 — decorative artifact",
}


@dataclass
class PageWork:
    doc_id: str
    page_num: int
    image_path: str
    page_file_name: str = ""  # original filename e.g. "0009.json" — used for writing result


def copy_doc_to_output(doc_id: str, output_dir: Path) -> bool:
    """Copy ingested/{doc_id}/ to {output_dir}/{doc_id}/ if not already done.

    Returns True if copy was performed, False if destination already exists.
    """
    src = INGESTED_BASE / doc_id
    dst = output_dir / doc_id

    if dst.exists():
        # Already copied — check if it has all the page files
        src_pages = (src / "pages")
        dst_pages = (dst / "pages")
        if src_pages.exists() and dst_pages.exists():
            return False  # assume already copied

    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return True
    return False


def worker_loop(worker_id: int, work_queue: mp.Queue, result_queue: mp.Queue,
                threads: int, limit: int, log_queue: mp.Queue = None):
    """Worker process: pull pages, run SmolVLM2, push results. Logs via log_queue."""
    completed = 0
    def log(msg):
        return log_queue.put(("INFO", msg)) if log_queue else None

    while completed < limit:
        try:
            work = work_queue.get(timeout=5)
        except Empty:
            break

        result = process_single_page(work, threads)
        result_queue.put(result)
        completed += 1

        if completed % 50 == 0 and log_queue:
            elapsed = time.time() - getattr(worker_loop, '_start_time', time.time())
            rate = completed / elapsed * 3600 if elapsed > 0 else 0
            log(f"[worker-{worker_id}] {completed}/{limit} done, ~{rate:.0f} pages/hr")

    log(f"[worker-{worker_id}] finished {completed} pages")


def process_single_page(work: PageWork, threads: int) -> dict:
    """Run SmolVLM2 on one image. Returns a result dict."""

    # Quick size check — skip if image is suspiciously small (< 1KB = probably not real content)
    try:
        size_kb = os.path.getsize(work.image_path) / 1024
    except OSError:
        return {"doc_id": work.doc_id, "page_num": work.page_num,
                "page_file_name": work.page_file_name,
                "description": "", "success": False, "error": "image not found", "chars": 0}

    # Adaptive generation params based on image size
    # Small images: faster generation (smaller ctx, fewer tokens)
    # Large images: need more context and tokens
    if size_kb < 5:
        max_tokens, ctx_size = 32, 512
    elif size_kb < 20:
        max_tokens, ctx_size = 64, 1024
    elif size_kb < 100:
        max_tokens, ctx_size = 128, 2048
    elif size_kb < 300:
        max_tokens, ctx_size = 160, 3072
    else:
        max_tokens, ctx_size = 200, 4096

    prompt = (
        "Describe this military document image briefly. "
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

        # Strip llama_perf / build timing lines
        desc_text = re.sub(
            r"(llama_perf|build:).*", "", stdout, flags=re.DOTALL
        ).strip()

        if not desc_text:
            return {"doc_id": work.doc_id, "page_num": work.page_num,
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
    except Exception as e:
        return {"doc_id": work.doc_id, "page_num": work.page_num,
                "page_file_name": work.page_file_name,
                "description": "", "success": False,
                "error": str(e), "chars": 0}


def write_result_to_json(result: dict, output_dir: Path):
    """Write vision description to the page JSON in the output copy (never touches ingested/).

    Uses result['page_file_name'] (e.g. "0009.json") to write to the correct source file,
    not f"{page_num:04d}.json" which would misalign with the stem vs page field offset.
    """
    abs_path = output_dir / result["doc_id"] / "pages" / result.get("page_file_name", f"{result['page_num']:04d}.json")

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


def mark_page_skipped(output_dir: Path, did: str, page_file: Path, reason: str):
    """Write vision_skipped_reason to the output page JSON (ingested-vision copy).

    Creates the output doc dir and pages dir if they don't exist.
    Only writes if the page doesn't already have vision_description.
    """
    out_pages_dir = output_dir / did / "pages"
    out_page_file = out_pages_dir / page_file.name

    try:
        # Read from ingested/ (source)
        page_data = json.loads(page_file.read_text())
    except (json.JSONDecodeError, IOError):
        return

    # Don't overwrite if already described
    if page_data.get("vision_description"):
        return

    page_data["vision_skipped_reason"] = reason
    page_data["vision_skipped_code"] = reason
    page_data["vision_timestamp"] = datetime.now().isoformat()

    out_pages_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(out_page_file, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                existing = json.load(f)
                if not existing.get("vision_description"):
                    existing.update(page_data)
                    f.seek(0)
                    json.dump(existing, f, indent=2)
                    f.truncate()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        # If output file doesn't exist yet, create it
        try:
            with open(out_page_file, "w") as f:
                json.dump(page_data, f, indent=2)
        except (FileNotFoundError, IOError):
            pass  # Dir not ready yet — skip


def build_work_queue(doc_id: str | None = None, resume: bool = False,
                     limit: int | None = None, output_dir: Path = None) -> list[PageWork]:
    """Build list of figure_only pages from ingested/{doc_id}/ that have corresponding images.

    Reads figure_only from the ORIGINAL ingested/. When resume=True, checks output_dir/
    for pages that already have vision_description to skip already-done work.
    """
    work_items = []
    output_dir = output_dir or Path("$DATA_DIR/extracted-vision")

    if doc_id:
        doc_ids = [doc_id]
    else:
        doc_ids = [d.name for d in INGESTED_BASE.iterdir() if d.is_dir()]

    # Pre-load manifests for all docs (for dimension-based aspect filtering)
    manifests = {}
    for did in doc_ids:
        img_dir = IMAGES_BASE / did
        manifest_path = img_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                manifests[did] = {e['filename']: (e.get('width', 0), e.get('height', 0))
                                 for e in json.load(f)}
        else:
            manifests[did] = {}

    for did in doc_ids:
        pages_dir = INGESTED_BASE / did / "pages"
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

            # Skip text pages misclassified as figure_only by Pipeline B
            # (any page with ocr_chars > 500 is a text page, not a true image)
            if data.get("ocr_chars", 0) > 500:
                mark_page_skipped(output_dir, did, page_file, "ocr_chars_exceeded")
                continue

            # When resuming: check the OUTPUT copy for existing vision_description
            # (write_result_to_json writes there, not to ingested/)
            if resume:
                out_page_file = output_dir / did / "pages" / page_file.name
                if out_page_file.exists():
                    try:
                        out_data = json.loads(out_page_file.read_text())
                        if out_data.get("vision_description"):
                            continue
                    except (json.JSONDecodeError, IOError):
                        pass

            page_num = data.get("page", int(page_file.stem))

            # Support both PNG and JPG image formats
            image_path_png = img_dir / f"page_{page_num:04d}_img_00.png"
            image_path_jpg = img_dir / f"page_{page_num:04d}_img_00.jpg"
            if image_path_png.exists():
                image_path = image_path_png
            elif image_path_jpg.exists():
                image_path = image_path_jpg
            else:
                mark_page_skipped(output_dir, did, page_file, "no_image_file")
                continue

            # Aspect filter: skip decorative rules and extreme aspect artifacts
            img_basename = image_path.name
            dims = img_manifest.get(img_basename)
            if dims:
                w, h = dims
                if w < 50 or h < 50:
                    mark_page_skipped(output_dir, did, page_file, "image_too_small")
                    continue  # too small
                ratio = w / max(h, 1)
                if ratio > 8 or ratio < 0.125:
                    mark_page_skipped(output_dir, did, page_file, "aspect_ratio_excluded")
                    continue  # extreme horizontal rule or vertical strip

            work_items.append(PageWork(
                doc_id=did,
                page_num=page_num,
                image_path=str(image_path),
                page_file_name=page_file.name,
            ))

    if limit:
        work_items = work_items[:limit]

    return work_items


def run_pipeline(workers: int, threads_per_worker: int, limit: int | None,
                 doc_id: str | None, resume: bool, output_dir: Path):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"pipeline_c_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Write startup messages directly to log file (before subprocesses start)
    # so they appear at the top, before the log_writer_loop header
    with open(log_path, "w") as lf:
        lf.write("Pipeline C — SmolVLM2 image description\n")
        lf.write(f"  Output: {output_dir}/\n")
        lf.write(f"  Workers: {workers} x {threads_per_worker} threads\n")
        lf.write(f"  CPU cores: {os.cpu_count()}\n")
        lf.write(f"  Resume mode: {resume}\n")
        lf.write(f"  Log: {log_path}\n")
        lf.flush()

    print("Pipeline C — SmolVLM2 image description")
    print(f"  Output: {output_dir}/")
    print(f"  Workers: {workers} x {threads_per_worker} threads")
    print(f"  Resume mode: {resume}")
    print(f"  Log: {log_path}")

    print("Building work queue...")
    work_items = build_work_queue(doc_id=doc_id, resume=resume, limit=limit, output_dir=output_dir)
    total = len(work_items)
    print(f"  {total} pages to process")

    if total == 0:
        print("Nothing to do.")
        return

    # Copy docs to output dir before processing
    # Build set of unique doc_ids in the work queue
    doc_ids_to_copy = sorted(set(w.doc_id for w in work_items))
    copied_count = 0
    skipped_count = 0
    for did in doc_ids_to_copy:
        if copy_doc_to_output(did, output_dir):
            copied_count += 1
        else:
            skipped_count += 1
    print(f"  Copy-first: {copied_count} new, {skipped_count} already present")

    # Ensure every vision dir has a manifest (copied from extracted/)
    manifest_src = Path("$DATA_DIR/extracted")
    for did in doc_ids_to_copy:
        src_manifest = manifest_src / did / "manifest.json"
        dst_manifest = output_dir / did / "manifest.json"
        if src_manifest.exists() and not dst_manifest.exists():
            shutil.copy2(src_manifest, dst_manifest)

    with open(log_path, "a") as lf:
        lf.write(f"  {total} pages to process\n")
        lf.write(f"  Copy-first: {copied_count} new, {skipped_count} already present\n\n")
        lf.flush()

    # Feed queue
    work_queue = mp.Queue()
    for item in work_items:
        work_queue.put(item)

    result_queue = mp.Queue()
    log_queue = mp.Queue()  # single writer queue — all log msgs go here
    worker_loop._start_time = time.time()

    log_queue.put(("INFO", f"Starting {workers} workers..."))
    start_time = time.time()

    def log_writer_loop(log_queue: mp.Queue, log_path: Path):
        """Single writer — receives (level, msg) tuples and writes them sequentially."""
        with open(log_path, "w") as lf:
            while True:
                item = log_queue.get()
                if item is None:
                    break
                level, msg = item
                lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
                lf.flush()

    def result_writer_loop(result_queue: mp.Queue, log_queue: mp.Queue,
                           total: int, output_dir: Path):
        """Result processor — writes descriptions and logs every page via log_queue."""
        done = 0
        errors = 0
        while done < total:
            try:
                result = result_queue.get(timeout=10)
            except Empty:
                continue

            if result["success"]:
                write_result_to_json(result, output_dir)
            else:
                errors += 1

            done += 1
            # Log every page as a separate entry for full traceability
            log_queue.put(("PAGE", f"{done}/{total} {result['doc_id'][:8]} p{result['page_num']} "
                           f"success={result['success']} chars={result['chars']}"))

            if done % 100 == 0 or done == total:
                elapsed = time.time() - start_time
                rate = done / elapsed * 3600 if elapsed > 0 else 0
                pct = done / total * 100
                log_queue.put(("INFO", f"  [{done}/{total} ({pct:.0f}%)] {rate:.0f} pages/hr — err={errors}"))

        log_queue.put(("INFO", f"Total: {done} pages in {time.time()-start_time:.1f}s — Errors: {errors}"))
        log_queue.put(None)  # signal log_writer to stop

    writer_proc = mp.Process(target=result_writer_loop,
                             args=(result_queue, log_queue, total, output_dir))
    writer_proc.start()

    # Start log writer first so it's ready to receive
    lw_proc = mp.Process(target=log_writer_loop, args=(log_queue, log_path))
    lw_proc.start()

    # Start workers — they log via log_queue instead of print()
    procs = []
    per_worker = (total // workers) + 1
    for i in range(workers):
        p = mp.Process(target=worker_loop,
                       args=(i, work_queue, result_queue, threads_per_worker, per_worker, log_queue))
        p.start()
        procs.append(p)

    for p in procs:
        p.join()

    result_queue.put(None)  # signal result_writer
    writer_proc.join(timeout=30)
    lw_proc.join(timeout=10)

    elapsed = time.time() - start_time
    rate = total / elapsed * 3600 if elapsed > 0 else 0
    log_queue.put(("INFO", f"Done. {total} pages in {elapsed:.0f}s ({rate:.0f} pages/hr)"))
    log_queue.put(("INFO", f"Output in: {output_dir}/"))
    log_queue.put(None)  # final flush


def main():
    parser = argparse.ArgumentParser(description="Pipeline C: SmolVLM2 image description (copy-first)")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--threads", type=int, default=28)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--doc-id", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("$DATA_DIR/extracted-vision"),
                        help="Directory to write vision-enhanced copies (default: $DATA_DIR/extracted-vision)")
    args = parser.parse_args()

    run_pipeline(args.workers, args.threads, args.limit, args.doc_id, args.resume, args.output_dir)


if __name__ == "__main__":
    main()
