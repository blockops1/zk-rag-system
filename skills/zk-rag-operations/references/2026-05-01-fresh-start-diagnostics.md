# Session Reference — 2026-05-01 Fresh Start Diagnostics

## What Was Found

### Qdrant: Corrupted
- Qdrant collections exist but data is unreliable. Decision: rebuild from scratch.
- Portalocker conflict history: `QdrantClient(path=...)` local mode vs running server has caused silent ingest failures in the past.

### Images: Orientation Wrong
- `images/{doc_id}/page_XXXX.png` have been saved upside-down and/or backwards.
- Root cause NOT yet diagnosed. Pipeline A code may still have the double-rotation bug.
- **Do NOT run Pipeline C** until image orientation is fixed — SmolVLM2 will describe rotated images.

### Pipeline C: Never Ran Successfully
- `extracted-vision/` is empty (0 files).
- Shell script `run_pipeline_c.sh` passed `--output-dir extracted-vision` to `batch_image_describe.py`.
- `batch_image_describe.py` does NOT accept `--output-dir` — it hardcodes `EXTRACTED_BASE = <DATA>extracted`.
- Result: script exited immediately with "unrecognized arguments" error on every invocation.
- Confirmed: no `extracted-vision/` subdirs, no Pipeline C output anywhere.

### Pipeline A Output: Intact
- `extracted/` — 571 docs, 117,222 page JSONs. Pipeline A completed successfully.
- `images/` — 571 doc dirs with PNG files.
- `figure_only=true` pages: 37,268 total, 2,141 blank (ocr_chars=0), **35,127 to process**.
- Vision descriptions for these pages need to be generated once images are orientation-corrected.

## Pipeline A Root Cause (suspected)
The earlier "fix" claimed `page.get_pixmap()` with no matrix argument produces upright images.
But Mr. V says images are still wrong. Either:
1. The fix was not actually deployed/committed
2. A different code path is being used (e.g., `doc.extract_image(xref)` instead of `get_pixmap()`)
3. The /Rotate metadata is being applied differently than documented

**Verification needed:** Load a known-rotated PDF page and compare:
- Input: PDF page with `/Rotate 270`
- Expected: upright image matching what PDF viewer shows
- Actual: what does `page.get_pixmap()` produce right now?

## What Was Fixed Today (2026-05-01)

### Shell script `run_pipeline_c.sh` — fixed
- Removed invalid `--output-dir extracted-vision` flag
- Log dir: `<DATA>logs/` (was `<REPO>logs/`)
- All `log` calls now use `log "LEVEL" "message"` format with `jq -n` for structured JSON lines
- Completion counter now scans `extracted/` (was `extracted-vision/`)

### Python script `batch_image_describe.py` — rewritten cleanly
- `LOGS_DIR` → `<DATA>logs/`
- Log format → JSON lines (structured)
- `import sys` moved to `if __name__ == "__main__"` block
- `log_writer_loop` and `result_writer_loop` moved to module level (not nested inside `run_pipeline()`)
- `Optional[mp.Queue]` used instead of `mp.Queue | None` (multiprocess package in venv)
- Unicode whitespace stripped by `ruff --fix`

### End-to-end test passed
- 2-page dry run: 14.9s, 482 pages/hr
- Descriptions written correctly to `extracted/{doc_id}/pages/*.json`
- Ruff: 1 fixable f-string issue resolved, all checks pass
- `bash -n`: shell syntax OK

## Recovery Sequence
1. Fix Pipeline A image orientation
2. Re-run Pipeline A (or accept existing images and fix orientation in-place)
3. Run Pipeline C (35,127 figure_only pages)
4. Run Pipeline D (chunking)
5. Run Pipeline E (Merkle trees)
6. Run Pipeline F (emit)
7. Wipe and rebuild Qdrant
8. Run Pipeline G (ingest)

## Session 2026-05-01 Afternoon — Pipeline C Reset

### What happened
- Attempted to use `llama-server` (persistent) for Pipeline C instead of per-call `llama-mtmd-cli` subprocess
- Server's `handle_media()` reads only from local filesystem paths, silently ignores inline base64 images
- Result: hallucinations (images not processed), 2.3× speed gain irrelevant
- Decision: revert to CLI approach

### Git history for pipeline_c/
| Commit | Description |
|--------|-------------|
| `fb3c993` | COPY-FIRST architecture — `extracted-vision/` output, desloppify issues present |
| `8b61cc3` | desloppify fixes + structured JSON logging + direct write to `extracted/` |
| `5c308a3` | fix(pipeline_c): add missing SCRIPT_DIR definition before use |

**`8b61cc3` is the clean known-good state.** The SCRIPT_DIR fix was added on top.

### llama-server findings (2026-05-01)
- `llama-server` HTTP API: `/v1/chat/completions` accepts `image_url` and `image_data` fields but `handle_media()` at `server-common.cpp:831` reads only from local `media_path` — silently ignores inline base64
- `llama-batched`: also reads from filesystem only, same limitation
- `llama-mtmd-cli`: handles inline base64 correctly via stdin, produces accurate SmolVLM2 descriptions
- **Correct architecture for SmolVLM2 vision: CLI subprocess per page, NOT server**
- `llama-server` IS correct for text embedding models (Qwen) in Pipeline G

### SCRIPT_DIR bug that passed `bash -n` but crashed at runtime
- `run_pipeline_c.sh` had `bash -n` pass cleanly
- Runtime: `SCRIPT_DIR: unbound variable` crash at line 87
- Root cause: `bash -n` only checks syntax — `set -u` only catches undefined variables when they are *referenced at runtime*
- If the script had exited before reaching line 87 (e.g., lock acquisition failed), the bug would have stayed hidden
- **Fix:** always `grep SCRIPT_DIR` and trace to definition before assuming shell script is safe
- **Also:** always test-run (not just `bash -n`) before considering a shell script verified

### Pipeline C final state (2026-05-01)
- Uses `llama-mtmd-cli` subprocess per page (correct approach)
- Writes directly to `extracted/{doc_id}/pages/*.json`
- Structured JSON logging: `<DATA>logs/pipeline_c_<ts>.jsonl`
- Lock: `<DATA>.lock.pipeline_c`
- Work queue: 35,083 `figure_only=true` pages (blank pages already excluded by `build_work_queue`)
- Workers: 2 × 28 threads
