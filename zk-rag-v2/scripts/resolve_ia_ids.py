#!/usr/bin/env python3
"""Parallel IA identifier resolution for 202 missing PDFs."""
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

brave_key = os.environ.get('BRAVE_API_KEY', '')
if not brave_key:
    import subprocess
    result = subprocess.run(['bash', '-c', 'source ./.env && echo $BRAVE_API_KEY'],
                           capture_output=True, text=True, timeout=10)
    brave_key = result.stdout.strip()

print(f"Brave key present: {bool(brave_key)}", file=sys.stderr)

def search_brave(query, count=8):
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

def derive_ia_slug(filename):
    base = filename.rsplit('.', 1)[0]
    parts = base.rsplit('-', 1)
    if len(parts) == 2 and len(parts[1]) == 8:
        return parts[0], parts[1]
    return None, None

def try_ia_download(identifier, filename):
    candidates = [
        f"https://archive.org/download/{identifier}/{urllib.parse.quote(filename)}",
    ]
    base = filename.rsplit('.', 1)[0]
    slug_base = base.rsplit('-', 1)[0] if len(base.rsplit('-', 1)[-1]) == 8 else base
    ia_name = slug_base.replace('-', '_') + '.pdf'
    candidates.append(f"https://archive.org/download/{identifier}/{urllib.parse.quote(ia_name)}")
    candidates.append(f"https://archive.org/download/{identifier}/{urllib.parse.quote(base + '.pdf')}")
    
    for url in candidates:
        try:
            req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    return url, resp.headers.get('Content-Length', '0')
        except (urllib.error.HTTPError, Exception):
            continue
    return None, None

def resolve_one(branch, fname):
    slug, hash8 = derive_ia_slug(fname)
    if not slug:
        return {'branch': branch, 'filename': fname, 'identifier': None, 'download_url': None, 'status': 'no_hash'}
    
    # Try milmanual-{slug} first
    identifier = f'milmanual-{slug}'
    url, cl = try_ia_download(identifier, fname)
    if url:
        return {'branch': branch, 'filename': fname, 'identifier': identifier, 'download_url': url, 'status': 'found_direct'}
    
    # Try bare slug
    identifier = slug
    url, cl = try_ia_download(identifier, fname)
    if url:
        return {'branch': branch, 'filename': fname, 'identifier': identifier, 'download_url': url, 'status': 'found_bare'}
    
    # Brave search fallback
    query = f'{slug.replace("-", " ")} military manual {hash8} site:archive.org'
    try:
        results = search_brave(query)
        ia_ids = find_ia_ids(results)
        for ia_id in ia_ids:
            url, cl = try_ia_download(ia_id, fname)
            if url:
                return {'branch': branch, 'filename': fname, 'identifier': ia_id, 'download_url': url, 'status': 'found_search'}
    except Exception:
        pass  # Silently fail search, will be marked not_found
    
    return {'branch': branch, 'filename': fname, 'identifier': None, 'download_url': None, 'status': 'not_found'}

# Load registry and find missing
reg = json.load(open('/data/archive/registry.json'))
docs = reg.get('documents', [])
reg_filenames = set(d.get('filename', '') for d in docs)

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

# Resolve in parallel (max 8 concurrent to be respectful of rate limits)
results = []
done = 0
with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {ex.submit(resolve_one, b, f): (b, f) for b, f in missing}
    for future in as_completed(futures):
        result = future.result()
        results.append(result)
        done += 1
        if done % 20 == 0:
            found = sum(1 for r in results if r['identifier'])
            print(f"Progress: {done}/{len(missing)} | found: {found}", file=sys.stderr)

print(f"\nDone: {len(results)} total", file=sys.stderr)

found = [r for r in results if r['identifier']]
not_found = [r for r in results if not r['identifier']]
print(f"Found: {len(found)}", file=sys.stderr)
print(f"Not found: {len(not_found)}", file=sys.stderr)

# Save
output_path = './scripts/ia_resolution_results.json'
with open(output_path, 'w') as out:
    json.dump(results, out, indent=2)
print(f"Saved: {output_path}", file=sys.stderr)

print("\nNot found:", file=sys.stderr)
for r in not_found:
    print(f"  {r['branch']}: {r['filename']}", file=sys.stderr)
