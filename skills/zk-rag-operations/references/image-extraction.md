# ZK-RAG Image Extraction Reference
*Last updated: 2026-05-22*

## Image Directory Layout

| Path | Count | Format | Purpose |
|------|-------|--------|---------|
| `<DATA>images/` | 527 target dirs | WebP (`.webp`) | Live corpus — correct format |
| `<DATA>images_png_archive/` | 586 dirs | PNG (`.png`) | Backup — too low-res for live use |

## Image Format

Live corpus uses **WebP** format (`.webp` files, 144 DPI × 2x scale, quality=90). PNG archives are NOT usable for the live corpus — PNG resolution is too small. Do NOT attempt to convert PNG → WebP; re-render from source PDFs instead.

## Fix: Missing WebP Dirs

Use `scripts/rerender_hires.py` — renders full PDF pages as WebP at 2x scale (144 DPI) directly from source PDFs. This is what created the original 523 WebP images.

**Prerequisites:**
- Activate `pipeline_a` venv: `source pipeline_a/venv/bin/activate`
- `images/{doc_id}/` directory must already exist (create it before calling `rerender_doc`)

**Usage (from zk-rag-v2 directory):**
```bash
# Create image directories for missing docs
python3 -c "
from pathlib import Path
images_dir = Path('<DATA>images')
for doc_id in ['<doc_id1>', '<doc_id2>']:
    (images_dir / doc_id).mkdir(parents=True, exist_ok=True)
"

# Re-render specific docs
python3 << 'PYEOF'
import sys
sys.path.insert(0, '<REPO>scripts')
from rerender_hires import rerender_doc

docs = [
    {"doc_id": "<doc_id>", "branch": "<branch>", "filename": "<filename>.pdf"},
]
for doc in docs:
    result = rerender_doc(doc['doc_id'], doc['branch'], doc['filename'])
    print(result)
PYEOF
```

**Key constraint:** `rerender_hires.py` requires `images/{doc_id}/` directory to already exist. Create it before calling `rerender_doc()`.

**CRITICAL: Scripts in `/tmp` are not persistent.** The OS deletes `/tmp` contents at boot or at will. After using a script from `/tmp`, always move it to `scripts/` before it gets lost.

## Pipeline A Idempotency Rules

- Skips if `extracted/{doc_id}/manifest.json` exists — does NOT check `images/{doc_id}/manifest.json`
- Use `--force` to re-run on a doc that already has `extracted/` output
- Does NOT delete existing image dirs — only creates `images/{doc_id}/` if manifest missing

## Key Scripts

| Script | What it does | Output format |
|--------|-------------|---------------|
| `scripts/rerender_hires.py` | Full page renders from PDF at 2x scale | WebP |
| `pipeline_a/pipeline_a.py` | Full page renders from PDF | PNG |
| `pipeline_a/pdf_processing.py` | Embedded images only (figures) | PNG |
| `scripts/extract_images.py` | Embedded images w/ compression | PNG/JPG |
| `scripts/convert_jb2_to_png.py` | JBIG2 → PNG conversion | PNG |
