#!/usr/bin/env python3
"""
ZK-RAG Registry Builder - Step 0a: Resolve IA identifiers and download URLs.

Approach:
- Local filename:  {slug}-{8hex}.pdf  (e.g. ar-50-5-980e09de.pdf)
- IA identifier:   milmanual-{slug}    (e.g. milmanual-ar-50-5)
- IA metadata API: https://archive.org/metadata/{identifier}  → lists actual filenames
- IA download:     https://archive.org/download/{identifier}/{ia_filename}

For each PDF:
  1. Derive slug and IA identifier
  2. Call IA metadata API to get file listing
  3. Find the best-matching PDF in the file list
  4. Construct download URL
  5. Record IA identifier + IA filename + download URL
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import os
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

# ── Config ────────────────────────────────────────────────────────────────────
BRAVE_KEY = None
def get_brave_key():
    global BRAVE_KEY
    if BRAVE_KEY is None:
        import subprocess
        r = subprocess.run(['bash', '-c', 'source ./.env && echo $BRAVE_API_KEY'],
                          capture_output=True, text=True, timeout=10)
        BRAVE_KEY = r.stdout.strip()
    return BRAVE_KEY

IA_BASE   = "https://archive.org"
HEADERS   = {'User-Agent': 'Mozilla/5.0'}

# ── IA Helpers ─────────────────────────────────────────────────────────────────
def ia_metadata(identifier):
    """Return the JSON metadata dict for an IA identifier, or None on failure."""
    url = f"{IA_BASE}/metadata/{identifier}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

def ia_files_for_identifier(identifier):
    """Return list of {name, sha1} dicts for all files in an IA item."""
    meta = ia_metadata(identifier)
    if not meta:
        return []
    return meta.get('files', [])

def pdf_files_in_ia_item(identifier):
    """Return all PDF file entries in an IA item."""
    files = ia_files_for_identifier(identifier)
    return [f for f in files if f.get('format', '').endswith('PDF') or f.get('name', '').lower().endswith('.pdf')]

def slug_from_filename(filename):
    """Derive the IA slug from a local filename like ar-50-5-980e09de.pdf."""
    # Strip .pdf, split off last -{8hex} chunk
    base = filename.rsplit('.', 1)[0]
    parts = base.rsplit('-', 1)
    if len(parts) == 2 and len(parts[1]) == 8 and all(c in '0123456789abcdef' for c in parts[1]):
        return parts[0]   # slug
    # No hash suffix — return whole base as potential slug
    return base

def derive_ia_identifier(slug):
    """Map a slug to the likely IA identifier."""
    return f"milmanual-{slug}"

def find_best_pdf_match(slug, pdf_files):
    """
    Given a slug and list of {name, sha1} IA file entries, pick the best PDF.
    Prefers: exact slug match > slug with underscores > any PDF.
    Returns (ia_filename, sha1) or (None, None).
    """
    if not pdf_files:
        return None, None

    # Try exact-ish matches first
    slug_underscore = slug.replace('-', '_')
    candidates = []

    for f in pdf_files:
        name = f['name']
        sha1 = f.get('sha1', '')
        # Exact slug match (with _ or -)
        if name == f"{slug}.pdf" or name == f"{slug_underscore}.pdf":
            return name, sha1
        # Name contains slug
        if slug in name or slug_underscore in name:
            candidates.append((f, 1))
        else:
            candidates.append((f, 2))

    # Sort by priority then pick first
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]['name'], candidates[0][0].get('sha1', '')

# ── Brave Search (fallback for truly orphan filenames) ────────────────────────
def search_brave(query, count=8):
    key = get_brave_key()
    if not key:
        return []
    url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={count}"
    req = urllib.request.Request(url, headers={
        'Accept': 'application/json',
        'X-Subscription-Token': key
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get('web', {}).get('results', [])
    except Exception:
        return []

def ia_ids_from_brave_results(results):
    ids = []
    for r in results:
        url = r.get('url', '')
        if 'archive.org/details/' in url:
            identifier = url.rstrip('/').split('/')[-1]
            ids.append(identifier)
    return ids

def resolve_via_brave(filename, slug):
    """Fallback: use Brave to find IA identifier for a filename."""
    queries = [
        f'"{filename}" site:archive.org/details/',
        f'"{slug.replace("-", " ")}" military manual archive.org',
    ]
    for q in queries:
        results = search_brave(q)
        ia_ids = ia_ids_from_brave_results(results)
        for ia_id in ia_ids[:3]:
            pdfs = pdf_files_in_ia_item(ia_id)
            if pdfs:
                ia_fn, sha1 = find_best_pdf_match(slug, pdfs)
                if ia_fn:
                    return ia_id, ia_fn, sha1, 'found_brave'
        time.sleep(0.3)
    return None, None, None, 'not_found'

# ── Core resolver ─────────────────────────────────────────────────────────────
def resolve_one(args):
    branch, filename = args
    slug = slug_from_filename(filename)
    identifier = derive_ia_identifier(slug)

    # Try milmanual-{slug} first
    pdfs = pdf_files_in_ia_item(identifier)
    if pdfs:
        ia_fn, sha1 = find_best_pdf_match(slug, pdfs)
        if ia_fn:
            download_url = f"{IA_BASE}/download/{identifier}/{urllib.parse.quote(ia_fn)}"
            return {
                'branch': branch, 'filename': filename, 'slug': slug,
                'identifier': identifier, 'ia_filename': ia_fn,
                'download_url': download_url,
                'ia_sha1': sha1,
                'status': 'found_milmanual'
            }
        # Item exists but no PDF — might be a different format
        return {
            'branch': branch, 'filename': filename, 'slug': slug,
            'identifier': identifier, 'ia_filename': None,
            'download_url': None,
            'ia_sha1': None,
            'status': 'ia_item_no_pdf'
        }

    # Try bare slug (some items use bare slug as identifier)
    pdfs = pdf_files_in_ia_item(slug)
    if pdfs:
        ia_fn, sha1 = find_best_pdf_match(slug, pdfs)
        if ia_fn:
            download_url = f"{IA_BASE}/download/{slug}/{urllib.parse.quote(ia_fn)}"
            return {
                'branch': branch, 'filename': filename, 'slug': slug,
                'identifier': slug, 'ia_filename': ia_fn,
                'download_url': download_url,
                'ia_sha1': sha1,
                'status': 'found_bare'
            }

    # Try slug with underscores
    slug_us = slug.replace('-', '_')
    identifier_us = f"milmanual-{slug_us}"
    pdfs = pdf_files_in_ia_item(identifier_us)
    if pdfs:
        ia_fn, sha1 = find_best_pdf_match(slug, pdfs)
        if ia_fn:
            download_url = f"{IA_BASE}/download/{identifier_us}/{urllib.parse.quote(ia_fn)}"
            return {
                'branch': branch, 'filename': filename, 'slug': slug,
                'identifier': identifier_us, 'ia_filename': ia_fn,
                'download_url': download_url,
                'ia_sha1': sha1,
                'status': 'found_us_slug'
            }

    # Brave fallback
    ia_id, ia_fn, sha1, brave_status = resolve_via_brave(filename, slug)
    if ia_id and ia_fn:
        dl = f"{IA_BASE}/download/{ia_id}/{urllib.parse.quote(ia_fn)}"
        return {
            'branch': branch, 'filename': filename, 'slug': slug,
            'identifier': ia_id, 'ia_filename': ia_fn,
            'download_url': dl,
            'ia_sha1': sha1,
            'status': brave_status
        }

    return {
        'branch': branch, 'filename': filename, 'slug': slug,
        'identifier': None, 'ia_filename': None,
        'download_url': None,
        'ia_sha1': None,
        'status': 'not_found'
    }

# ── Main ───────────────────────────────────────────────────────────────────────
BASE = './data/sourcePDF'

# Load archive registry to get existing (already-resolved) entries
archive_reg = json.load(open('/data/archive/registry.json'))
archive_docs = {d.get('filename'): d for d in archive_reg.get('documents', [])}
existing_ia_ids = {d['filename']: d for d in archive_docs.values()
                  if d.get('ia_identifier') or d.get('source_url')}

# Scan all PDFs on disk
all_pdfs = []
for branch in sorted(os.listdir(BASE)):
    d = os.path.join(BASE, branch)
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith('.pdf'):
                all_pdfs.append((branch, f))

print(f"Total PDFs on disk: {len(all_pdfs)}", file=sys.stderr)

# For PDFs already in archive registry with ia_identifier, use those directly
already_resolved = {}
unresolved = []
for branch, fname in all_pdfs:
    if fname in archive_docs:
        d = archive_docs[fname]
        ia_id = d.get('ia_identifier')
        src_url = d.get('source_url', '')
        if ia_id:
            # Compute download_url from ia_identifier
            slug = slug_from_filename(fname)
            # Try to find IA filename from archive registry
            ia_fn = d.get('ia_filename', '')
            if not ia_fn:
                # Try to derive from slug
                ia_fn = slug.replace('-', '_') + '.pdf'
            dl = f"{IA_BASE}/download/{ia_id}/{urllib.parse.quote(ia_fn)}" if ia_id else src_url
            already_resolved[fname] = {
                'branch': branch, 'filename': fname, 'slug': slug,
                'identifier': ia_id, 'ia_filename': ia_fn,
                'download_url': dl,
                'ia_sha1': d.get('ia_sha1', ''),
                'status': 'from_archive_registry'
            }
        elif src_url and src_url.startswith('http'):
            already_resolved[fname] = {
                'branch': branch, 'filename': fname, 'slug': slug_from_filename(fname),
                'identifier': None, 'ia_filename': None,
                'download_url': src_url,
                'ia_sha1': None,
                'status': 'from_archive_registry'
            }
        else:
            unresolved.append((branch, fname))
    else:
        unresolved.append((branch, fname))

print(f"Already resolved (from archive registry): {len(already_resolved)}", file=sys.stderr)
print(f"Need to resolve: {len(unresolved)}", file=sys.stderr)

# Resolve unresolved in parallel
results = []
done = 0
with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {ex.submit(resolve_one, (b, f)): (b, f) for b, f in unresolved}
    for future in as_completed(futures):
        r = future.result()
        results.append(r)
        done += 1
        if done % 20 == 0:
            found = sum(1 for r in results if r['download_url'])
            print(f"Progress: {done}/{len(unresolved)} | found: {found}", file=sys.stderr)

# Merge
all_results = list(already_resolved.values()) + results

found = [r for r in all_results if r['download_url']]
not_found = [r for r in all_results if not r['download_url']]

statuses = Counter(r['status'] for r in all_results)

print(f"\nTotal: {len(all_results)} | Resolved: {len(found)} | Unresolved: {len(not_found)}", file=sys.stderr)
print("Status breakdown:", file=sys.stderr)
for s, c in sorted(statuses.items()):
    print(f"  {s}: {c}", file=sys.stderr)

# Save
out = './scripts/ia_resolution_v3.json'
with open(out, 'w') as fh:
    json.dump(all_results, fh, indent=2)
print(f"\nSaved: {out}", file=sys.stderr)

# Show unresolved
if not_found:
    print(f"\nNot found ({len(not_found)}):", file=sys.stderr)
    for r in not_found[:40]:
        print(f"  [{r['branch']}] {r['filename']}  slug={r['slug']}", file=sys.stderr)
    if len(not_found) > 40:
        print(f"  ... and {len(not_found)-40} more", file=sys.stderr)
