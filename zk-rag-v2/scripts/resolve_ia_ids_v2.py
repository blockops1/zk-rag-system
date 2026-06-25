#!/usr/bin/env python3
"""Smart IA resolver - handles both hash-suffix and non-hash filenames."""
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

brave_key = os.environ.get('BRAVE_API_KEY', '')
if not brave_key:
    import subprocess
    result = subprocess.run(['bash', '-c', 'source ./.env && echo $BRAVE_API_KEY'],
                           capture_output=True, text=True, timeout=10)
    brave_key = result.stdout.strip()

def search_brave(query, count=10):
    url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={count}"
    req = urllib.request.Request(url, headers={
        'Accept': 'application/json',
        'X-Subscription-Token': brave_key
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
        return data.get('web', {}).get('results', [])

def find_ia_ids(results):
    ids = []
    for r in results:
        url = r.get('url', '')
        if 'archive.org/details/' in url:
            identifier = url.rstrip('/').split('/')[-1]
            ids.append(identifier)
    return ids

def try_ia_download(identifier, filename):
    candidates = [
        f"https://archive.org/download/{identifier}/{urllib.parse.quote(filename)}",
    ]
    base = filename.rsplit('.', 1)[0]
    # Try _ variant of filename
    ia_name = base.replace('-', '_') + '.pdf'
    candidates.append(f"https://archive.org/download/{identifier}/{urllib.parse.quote(ia_name)}")
    # Try with slug-only name
    slug_only = base + '.pdf'
    candidates.append(f"https://archive.org/download/{identifier}/{urllib.parse.quote(slug_only)}")
    
    for url in candidates:
        try:
            req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    return url, resp.headers.get('Content-Length', '0')
        except (urllib.error.HTTPError, Exception):
            continue
    return None, None

def derive_slug_hash(filename):
    """For hash-suffix filenames."""
    base = filename.rsplit('.', 1)[0]
    parts = base.rsplit('-', 1)
    if len(parts) == 2 and len(parts[1]) == 8:
        return parts[0], parts[1]
    return None, None

def resolve_hash_filename(branch, fname):
    """Resolve a {slug}-{hash8}.pdf filename."""
    slug, hash8 = derive_slug_hash(fname)
    if not slug:
        return None, None, 'no_hash_format'
    
    # Try milmanual-{slug}
    identifier = f'milmanual-{slug}'
    url, cl = try_ia_download(identifier, fname)
    if url:
        return identifier, url, 'found_milmanual'
    
    # Try bare slug
    url, cl = try_ia_download(slug, fname)
    if url:
        return slug, url, 'found_bare'
    
    # Try with _ instead of - in slug (e.g. fm_3_0 not fm-3-0)
    slug_us = slug.replace('-', '_')
    url, cl = try_ia_download(slug_us, fname)
    if url:
        return slug_us, url, 'found_us_slug'
    
    # Brave search
    query = f'"{fname}" site:archive.org OR "{slug.replace("-", " ")}" {hash8} military manual'
    try:
        results = search_brave(query)
        ia_ids = find_ia_ids(results)
        for ia_id in ia_ids[:5]:
            url, cl = try_ia_download(ia_id, fname)
            if url:
                return ia_id, url, 'found_brave'
        time.sleep(0.5)
    except Exception:
        pass
    
    return None, None, 'not_found'

def resolve_no_hash(branch, fname):
    """Resolve navy mar-* and army_fm*.pdf files without hash suffix."""
    base = fname.rsplit('.', 1)[0]
    
    # Try archive.org search with the bare name
    search_queries = [
        f'"{fname}" site:archive.org',
        f'{base.replace("_", " ").replace("-", " ")} military manual archive.org',
        f'{base} navy manual site:archive.org',
        f'{base} military publication site:archive.org',
    ]
    
    for query in search_queries:
        try:
            results = search_brave(query)
            ia_ids = find_ia_ids(results)
            for ia_id in ia_ids[:5]:
                url, cl = try_ia_download(ia_id, fname)
                if url:
                    return ia_id, url, 'found_brave'
            time.sleep(0.5)
        except Exception:
            pass
    
    # Also try common IA collection patterns for navy
    # mar-* files often in 'naval-war-department' or similar
    common_patterns = [
        f'naval-{base}',
        f'navy-{base}',
        f'maritime-{base}',
        base,
    ]
    for identifier in common_patterns:
        url, cl = try_ia_download(identifier, fname)
        if url:
            return identifier, url, 'found_common_pattern'
    
    return None, None, 'not_found'

def resolve_one(args):
    branch, fname = args
    slug, hash8 = derive_slug_hash(fname)
    
    if slug:
        identifier, url, status = resolve_hash_filename(branch, fname)
    else:
        identifier, url, status = resolve_no_hash(branch, fname)
    
    return {'branch': branch, 'filename': fname, 'identifier': identifier, 'download_url': url, 'status': status}

# Load registry
reg = json.load(open('/data/archive/registry.json'))
docs = reg.get('documents', [])
reg_filenames = set(d.get('filename', '') for d in docs)

# Load missing PDFs
base = './data/sourcePDF'
all_pdfs = []
for branch in os.listdir(base):
    d = os.path.join(base, branch)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith('.pdf'):
                all_pdfs.append((branch, f))

missing = [(b, f) for b, f in all_pdfs if f not in reg_filenames]
print(f"Missing: {len(missing)}", file=sys.stderr)

# Show breakdown before resolving
hash_count = sum(1 for b, f in missing if derive_slug_hash(f)[0])
no_hash_count = len(missing) - hash_count
print(f"  With hash suffix: {hash_count}", file=sys.stderr)
print(f"  Without hash: {no_hash_count}", file=sys.stderr)

# Resolve
results = []
done = 0
with ThreadPoolExecutor(max_workers=10) as ex:
    futures = {ex.submit(resolve_one, (b, f)): (b, f) for b, f in missing}
    for future in as_completed(futures):
        result = future.result()
        results.append(result)
        done += 1
        if done % 20 == 0:
            found = sum(1 for r in results if r['identifier'])
            print(f"Progress: {done}/{len(missing)} | found: {found}", file=sys.stderr)

found = [r for r in results if r['identifier']]
not_found = [r for r in results if not r['identifier']]
print(f"\nResults: {len(found)} found, {len(not_found)} not_found", file=sys.stderr)
statuses = Counter(r['status'] for r in results)
for s, c in sorted(statuses.items()):
    print(f"  {s}: {c}", file=sys.stderr)

# Save
output_path = './scripts/ia_resolution_v2.json'
with open(output_path, 'w') as out:
    json.dump(results, out, indent=2)
print(f"\nSaved: {output_path}", file=sys.stderr)

print(f"\nNot found ({len(not_found)}):", file=sys.stderr)
for r in not_found[:30]:
    print(f"  {r['branch']}: {r['filename']}", file=sys.stderr)
if len(not_found) > 30:
    print(f"  ... and {len(not_found)-30} more", file=sys.stderr)
