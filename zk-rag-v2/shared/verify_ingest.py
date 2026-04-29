#!/usr/bin/env python3
"""verify_ingest.py -- Per-document verification checks against Qdrant.

Verifies every doc marked 'ingested' in registry.json against the live
Qdrant army-docs collection. Produces a per-doc PASS/FAIL table and
exits non-zero if any doc fails.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

# Constants
API_BASE_URL = "http://localhost:8100"
REGISTRY_PATH = "$DATA_DIR/registry.json"
SECRETS_PATH = "$ZK_RAG_HOME/.openclaw/workspace/secrets/vps-api-key.txt"
LOGS_DIR = "$REPO_DIR/logs"
COLLECTION_NAME = "army-docs"


def load_api_key() -> str:
    """Load API key from secrets file."""
    secrets_file = Path(SECRETS_PATH)
    if secrets_file.exists():
        return secrets_file.read_text().strip()
    return ""


def load_registry() -> dict[str, dict[str, Any]]:
    """Load registry.json and return docs with status='ingested'."""
    with open(REGISTRY_PATH, "r") as f:
        registry = json.load(f)
    return {k: v for k, v in registry.items() if v.get("status") == "ingested"}


def get_collection_doc_ids() -> set[str]:
    """Get set of doc_ids from the army-docs collection via /collections endpoint."""
    api_key = load_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    
    response = requests.get(f"{API_BASE_URL}/collections", headers=headers, timeout=30)
    response.raise_for_status()
    
    collections = response.json()
    for coll in collections:
        if coll.get("name") == COLLECTION_NAME:
            return set(coll.get("doc_ids", []))
    return set()


def get_chunk_count_for_doc(doc_id: str) -> int:
    """Get chunk count for a doc_id from Qdrant by querying the collection."""
    api_key = load_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    
    # Query with a filter for the doc_id to get results
    payload = {
        "query": "test",
        "collection": COLLECTION_NAME,
        "top_k": 1,
        "filter": {"doc_id": doc_id}
    }
    
    response = requests.post(f"{API_BASE_URL}/query", json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    
    results = response.json().get("results", [])
    # If we get any results, the doc exists. We need to count all chunks.
    # Since we can't get exact count from query, we'll use a workaround:
    # Query with a large top_k and count unique chunk_ids
    if not results:
        return 0
    
    # For accurate count, we need to scroll through all points
    # Use the scroll API to get all points for this doc_id
    scroll_payload = {
        "collection": COLLECTION_NAME,
        "filter": {"doc_id": doc_id},
        "limit": 10000
    }
    
    scroll_response = requests.post(
        f"{API_BASE_URL}/scroll", 
        json=scroll_payload, 
        headers=headers, 
        timeout=60
    )
    
    if scroll_response.status_code == 200:
        scroll_data = scroll_response.json()
        return len(scroll_data.get("result", []))
    
    # Fallback: return count from results if scroll fails
    return len(results)


def check_chunk_count(doc_id: str, collection_doc_ids: set[str]) -> tuple[str, Any]:
    """Check if doc_id exists in Qdrant with chunk_count > 0."""
    if doc_id not in collection_doc_ids:
        return "FAIL", "Doc not in collection"
    
    try:
        chunk_count = get_chunk_count_for_doc(doc_id)
        if chunk_count > 0:
            return "PASS", {"chunk_count": chunk_count}
        return "FAIL", "chunk_count is 0"
    except Exception as e:
        return "FAIL", str(e)


def check_chunk_threshold(page_count: int | None, actual_chunk_count: int) -> tuple[str, Any]:
    """Check if chunk_count meets minimum threshold based on page_count."""
    if page_count is None or page_count < 10:
        threshold = 5
    else:
        threshold = int(page_count * 0.5)
    
    if actual_chunk_count >= threshold:
        return "PASS", {"threshold": threshold, "actual": actual_chunk_count}
    return "FAIL", {"threshold": threshold, "actual": actual_chunk_count}


def check_metadata_present(registry_entry: dict[str, Any]) -> tuple[str, list[str]]:
    """Check that required metadata fields are present.
    
    Returns (status, list of warnings).
    - doc_type: must be non-null -> FAIL if null
    - branch: must be non-null -> FAIL if null  
    - title: must be non-null and non-empty -> FAIL if null/empty
    - pub_year: checked but WARN only if null
    """
    failures = []
    warnings = []
    
    # Check doc_type
    if registry_entry.get("doc_type") is None:
        failures.append("doc_type is null")
    
    # Check branch
    if registry_entry.get("branch") is None:
        failures.append("branch is null")
    
    # Check title
    title = registry_entry.get("title")
    if title is None or (isinstance(title, str) and title.strip() == ""):
        failures.append("title is null or empty")
    
    # Check pub_year (WARN only)
    if registry_entry.get("pub_year") is None:
        warnings.append("pub_year is null")
    
    if failures:
        return "FAIL", failures + warnings
    if warnings:
        return "WARN", warnings
    return "PASS", []


def check_spot_query(doc_id: str, title: str) -> tuple[str, Any]:
    """Perform spot query using first 6 words of title.
    
    Requires at least 1 result with matching doc_id and score > 0.3.
    """
    api_key = load_api_key()
    headers = {"X-API-Key": api_key} if api_key else {}
    
    # Get first 6 words of title
    words = title.split()[:6]
    query_text = " ".join(words)
    
    payload = {
        "query": query_text,
        "collection": COLLECTION_NAME,
        "top_k": 5,
        "filter": {"doc_id": doc_id}
    }
    
    try:
        response = requests.post(f"{API_BASE_URL}/query", json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        results = response.json().get("results", [])
        
        if not results:
            return "FAIL", "No results returned"
        
        # Check for at least one result with matching doc_id and score > 0.3
        valid_results = [
            r for r in results 
            if r.get("doc_id") == doc_id and r.get("score", 0) > 0.3
        ]
        
        if valid_results:
            return "PASS", {"results_count": len(valid_results), "best_score": max(r.get("score", 0) for r in valid_results)}
        
        # Check if any results exist but scores are too low
        matching_doc_id = [r for r in results if r.get("doc_id") == doc_id]
        if matching_doc_id:
            best_score = max(r.get("score", 0) for r in matching_doc_id)
            return "FAIL", f"Results exist but best score {best_score:.3f} <= 0.3"
        
        return "FAIL", "No results with matching doc_id"
        
    except requests.exceptions.RequestException as e:
        return "FAIL", f"API error: {e}"
    except Exception as e:
        return "FAIL", str(e)


def verify_doc(doc_id: str, registry_entry: dict[str, Any], collection_doc_ids: set[str]) -> dict[str, Any]:
    """Run all verification checks for a single document."""
    page_count = registry_entry.get("page_count")
    title = registry_entry.get("title", "")
    
    # Check 1: CHUNK_COUNT
    chunk_count_status, chunk_count_info = check_chunk_count(doc_id, collection_doc_ids)
    
    # Get actual chunk count for threshold check
    if chunk_count_status == "PASS" and isinstance(chunk_count_info, dict):
        actual_chunk_count = chunk_count_info.get("chunk_count", 0)
    else:
        # Try to get chunk count anyway for threshold check
        try:
            actual_chunk_count = get_chunk_count_for_doc(doc_id)
        except Exception:
            actual_chunk_count = 0
    
    # Check 2: CHUNK_THRESHOLD
    threshold_status, threshold_info = check_chunk_threshold(page_count, actual_chunk_count)
    
    # Check 3: METADATA_PRESENT
    metadata_status, metadata_info = check_metadata_present(registry_entry)
    
    # Check 4: SPOT_QUERY
    spot_status, spot_info = check_spot_query(doc_id, title)
    
    # Determine overall status
    # FAIL if any check fails, WARN if any check warns (but no fails), else PASS
    statuses = [chunk_count_status, threshold_status, metadata_status, spot_status]
    
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "WARN" in statuses:
        overall = "WARN"
    else:
        overall = "PASS"
    
    return {
        "doc_id": doc_id,
        "chunk_count": chunk_count_status,
        "chunk_threshold": threshold_status,
        "metadata": metadata_status,
        "spot_query": spot_status,
        "overall": overall,
        "details": {
            "chunk_count_info": chunk_count_info,
            "threshold_info": threshold_info,
            "metadata_info": metadata_info,
            "spot_info": spot_info
        }
    }


def print_table(results: list[dict[str, Any]]) -> None:
    """Print verification results as a table."""
    # Header
    header = f"{'doc_id':<50} | {'CHUNK_COUNT':<11} | {'CHUNK_THRESHOLD':<15} | {'METADATA':<10} | {'SPOT_QUERY':<10} | {'OVERALL':<8}"
    separator = "-" * len(header)
    
    print(separator)
    print(header)
    print(separator)
    
    for r in results:
        doc_id = r["doc_id"][:48] + ".." if len(r["doc_id"]) > 50 else r["doc_id"]
        row = f"{doc_id:<50} | {r['chunk_count']:<11} | {r['chunk_threshold']:<15} | {r['metadata']:<10} | {r['spot_query']:<10} | {r['overall']:<8}"
        print(row)
    
    print(separator)


def print_summary(results: list[dict[str, Any]]) -> None:
    """Print summary counts."""
    total = len(results)
    passed = sum(1 for r in results if r["overall"] == "PASS")
    failed = sum(1 for r in results if r["overall"] == "FAIL")
    warned = sum(1 for r in results if r["overall"] == "WARN")
    
    print(f"\nSummary: total={total}, passed={passed}, failed={failed}, warned={warned}")


def write_json_report(results: list[dict[str, Any]]) -> str:
    """Write JSON report to logs directory. Returns the path."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"verify_{timestamp}.json"
    filepath = os.path.join(LOGS_DIR, filename)
    
    report = {
        "generated_at": datetime.now().isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["overall"] == "PASS"),
        "failed": sum(1 for r in results if r["overall"] == "FAIL"),
        "warned": sum(1 for r in results if r["overall"] == "WARN"),
        "results": results
    }
    
    with open(filepath, "w") as f:
        json.dump(report, f, indent=2)
    
    return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Verify ingested documents against Qdrant army-docs collection."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results but skip actual API calls (use cached/mock data)."
    )
    args = parser.parse_args()
    
    # Load registry
    try:
        registry = load_registry()
    except Exception as e:
        print(f"Error loading registry: {e}", file=sys.stderr)
        sys.exit(1)
    
    if not registry:
        print("No documents with status='ingested' found in registry.")
        sys.exit(0)
    
    # Get collection doc_ids (unless dry-run)
    collection_doc_ids = set()
    if not args.dry_run:
        try:
            collection_doc_ids = get_collection_doc_ids()
        except Exception as e:
            print(f"Error getting collection doc_ids: {e}", file=sys.stderr)
            sys.exit(1)
    
    # Verify each document
    results = []
    for doc_id, registry_entry in registry.items():
        if args.dry_run:
            # In dry-run mode, simulate results
            result = {
                "doc_id": doc_id,
                "chunk_count": "PASS" if registry_entry.get("chunk_count", 0) > 0 else "FAIL",
                "chunk_threshold": "PASS",
                "metadata": "PASS",
                "spot_query": "PASS",
                "overall": "PASS",
                "details": {}
            }
        else:
            result = verify_doc(doc_id, registry_entry, collection_doc_ids)
        results.append(result)
    
    # Print table
    print_table(results)
    
    # Print summary
    print_summary(results)
    
    # Write JSON report
    report_path = write_json_report(results)
    print(f"\nJSON report written to: {report_path}")
    
    # Exit with appropriate code
    failed_count = sum(1 for r in results if r["overall"] == "FAIL")
    if failed_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
