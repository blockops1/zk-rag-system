#!/usr/bin/env python3
"""Smoke test: hybrid RRF search against local Qdrant + BM25 index."""
import sys
import re
import pickle
import argparse
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))


def rrf_merge(vector_results, bm25_results, top_k, k=60):
    vec_ranks = {r.id: i+1 for i, r in enumerate(vector_results)}
    bm25_ranks = {cid: i+1 for i, cid in enumerate(bm25_results)}
    all_ids = set(vec_ranks) | set(bm25_ranks)
    scores = {}
    for cid in all_ids:
        s = 0.0
        if cid in vec_ranks:
            s += 1.0 / (k + vec_ranks[cid])
        if cid in bm25_ranks:
            s += 1.0 / (k + bm25_ranks[cid])
        scores[cid] = s
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]


def main():
    parser = argparse.ArgumentParser(description='Smoke test: hybrid RRF search')
    parser.add_argument('--question', required=True, help='Query string')
    parser.add_argument('--collection', default='public-domain-manuals', help='Qdrant collection name')
    parser.add_argument('--qdrant-path', default='$REPO_DIR/qdrant_data', help='Qdrant storage path')
    parser.add_argument('--bm25-path', default=None, help='BM25 pickle path (default: qdrant_path/bm25_index.pkl)')
    parser.add_argument('--model', default='BAAI/bge-small-en-v1.5', help='Fastembed model name')
    parser.add_argument('--top-k', type=int, default=5, help='Number of results')
    args = parser.parse_args()

    from fastembed import TextEmbedding
    from qdrant_client import QdrantClient

    qdrant_path = Path(args.qdrant_path)
    bm25_path = Path(args.bm25_path) if args.bm25_path else qdrant_path / 'bm25_index.pkl'

    model = TextEmbedding(model_name=args.model)
    query_vec = list(model.embed([args.question]))[0].tolist()

    client = QdrantClient(path=str(qdrant_path))
    vec_results = client.query_points(
        collection_name=args.collection,
        query=query_vec,
        limit=50,
        with_payload=True
    ).points

    bm25_top = []
    if bm25_path.exists():
        with open(bm25_path, 'rb') as f:
            bm25_data = pickle.load(f)
        bm25 = bm25_data['bm25']
        chunk_ids = bm25_data['chunk_ids']
        tokens = re.findall(r'\w+', args.question.lower())
        scores = bm25.get_scores(tokens)
        top_idx = np.argsort(scores)[::-1][:50]
        bm25_top = [chunk_ids[i] for i in top_idx]

    merged = rrf_merge(vec_results, bm25_top, args.top_k)

    id_to_payload = {r.id: r.payload for r in vec_results}

    print(f"Top {args.top_k} results for: '{args.question}'\n")
    for rank, (chunk_id, score) in enumerate(merged, 1):
        payload = id_to_payload.get(chunk_id, {})
        print(f"--- Result {rank} (rrf={score:.4f}) ---")
        print(f"Doc: {payload.get('doc_id')} | Page: {payload.get('page')} | Chapter: {payload.get('chapter')} | Section: {payload.get('section')}")
        print(f"{payload.get('text','')[:300]}")
        print()

    if not merged:
        print("No results found.")
        sys.exit(1)


if __name__ == '__main__':
    main()
