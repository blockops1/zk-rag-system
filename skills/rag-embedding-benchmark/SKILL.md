---
name: rag-embedding-benchmark
description: Benchmark embedding model alternatives for RAG query API — compare speed and retrieval quality without re-encoding the entire corpus. Uses Qdrant's native search as the fast candidate retrieval, then re-scores with candidate models to compare quality.
category: data-science
---

# RAG Embedding Model Benchmark

Benchmark an alternative embedding model (e.g., BGE-large) against the currently deployed model (e.g., Qwen3-Embedding-0.6B) for RAG query quality and speed.

## The Problem

Re-encoding all 90K+ corpus chunks with a new model for comparison is too slow (times out at 300s even for 500 chunks at a time). Need a faster approach.

## The Working Method

**Use Qdrant's native search as the candidate pool, then re-score with the candidate model.**

```
1. Query Qdrant with the CURRENT model (fast — uses HNSW index)
   → Get top-N candidates (e.g., top 20) with payload + vectors
   
2. Re-score those same candidates with the NEW model
   → Encode query with candidate model
   → Encode candidate chunk texts with candidate model
   → Compute cosine similarity in candidate model's embedding space
   
3. Compare: does the new model's top result match the current model's top result?
```

## Key Discovery: Qdrant Path

The running Qdrant service may be at a DIFFERENT path than `QdrantClient(path=...)` in the code expects. Always verify:

```bash
# Find the actual running Qdrant process and its config
ps aux | grep qdrant | grep -v grep
# Shows: /usr/local/bin/qdrant --config-path <DATA>qdrant/config/config.yaml

# Check listening ports
ss -tlnp | grep qdrant
# HTTP: 6333, gRPC: 6334

# Qdrant config
cat <DATA>qdrant/config/config.yaml
# storage_path tells you where data actually lives

# In Python: use HTTP API via requests, not QdrantClient(path=...)
# QdrantClient(path=...) opens a local SQLite/file-based connection
# which may differ from the running service's storage path
```

## Benchmark Script Template

```python
#!/usr/bin/env python3
"""Compare embedding models using Qdrant as fast candidate retrieval."""
import time, requests, numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

QDRANT_URL = "http://127.0.0.1:6333"

# Load the currently deployed model (baseline)
print("Loading baseline model (Qwen)...")
CURRENT_MODEL = "Qwen/Qwen3-Embedding-0.6B"
m_current = SentenceTransformer(CURRENT_MODEL)

# Load the candidate model to benchmark
print("Loading candidate model (BGE)...")
CANDIDATE_MODEL = "BAAI/bge-large-en-v1.5"
m_candidate = SentenceTransformer(CANDIDATE_MODEL)

queries = [
    "enemy prisoner of war handling procedures",
    "rules of engagement escalation of force",
    # ... add military document queries
]

results = []
for q in queries:
    # Step 1: Get top-20 from Qdrant using current model (fast)
    qv = m_current.encode([q], normalize_embeddings=True)
    search_body = {
        "vector": qv[0].tolist(),
        "limit": 20,
        "with_payload": True,
        "with_vectors": True
    }
    r = requests.post(f"{QDRANT_URL}/collections/army/points/search", json=search_body).json()
    candidates = r.get("result", [])
    
    if not candidates:
        continue
    
    # Step 2: Extract texts and current-model vectors
    texts = [c["payload"]["text"][:400] for c in candidates]
    current_vecs = np.array([np.array(c["vector"]) for c in candidates])
    current_scores = np.array([c["score"] for c in candidates])
    
    # Step 3: Re-score with candidate model
    cand_qv = m_candidate.encode([q], normalize_embeddings=True)
    cand_vecs = m_candidate.encode(texts, normalize_embeddings=True)
    cand_scores = cosine_similarity(cand_qv, cand_vecs)[0]
    
    # Step 4: Analysis
    current_top_idx = np.argsort(current_scores)[-1]
    candidate_top_idx = np.argsort(cand_scores)[-1]
    
    current_top5 = set(np.argsort(current_scores)[-5:][::-1])
    candidate_top5 = set(np.argsort(cand_scores)[-5:][::-1])
    
    overlap5 = len(current_top5 & candidate_top5)
    
    # Rank of current #1 in candidate's ordering
    cand_ranking = list(np.argsort(cand_scores)[::-1])
    rank_of_current_top1 = cand_ranking.index(current_top_idx) + 1
    
    print(f"  Query: {q[:40]}")
    print(f"    Top-5 overlap: {overlap5}/5")
    print(f"    Rank of current#1 in candidate ordering: {rank_of_current_top1}")
    print(f"    Candidate's #1: {texts[candidate_top_idx][:50]}")
    
    results.append({
        "query": q,
        "overlap5": overlap5,
        "rank_of_current_top1": rank_of_current_top1,
    })

# Aggregate
avg_overlap5 = np.mean([r["overlap5"] for r in results])
avg_rank = np.mean([r["rank_of_current_top1"] for r in results])
print(f"\nAvg Top-5 overlap: {avg_overlap5:.1f}/5")
print(f"Avg rank of current#1 in candidate: {avg_rank:.1f}")
```

## Speed Benchmark (Query Encode Only)

For pure encode speed comparison (no retrieval):

```python
#!/usr/bin/env python3
"""Measure per-query encode time."""
import time, numpy as np
from sentence_transformers import SentenceTransformer

m1 = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
m2 = SentenceTransformer("BAAI/bge-large-en-v1.5")

queries = ["query1", "query2", ...]

for m, name in [(m1, "Qwen"), (m2, "BGE")]:
    times = []
    for _ in range(5):  # 5 runs per query
        for q in queries:
            t0 = time.time()
            m.encode([q], normalize_embeddings=True)
            times.append((time.time() - t0) * 1000)
    print(f"{name}: {np.mean(times):.1f}ms avg per query")
```

## Critical Notes

- **Dimension must match**: Both models must produce the same-dimension vectors (e.g., 1024-dim) to use the same Qdrant collection without re-creating it. BGE-large-en-v1.5 is 1024-dim, same as Qwen3-Embedding-0.6B.
- **Cross-model comparison caveats**: Comparing model A's score against model B's score is misleading — they're different embedding spaces with different score distributions. Compare *retrieval ordering* (which chunks rank highest), not absolute scores.
- **Corpus re-encoding cost**: If switching models, all chunks must be re-embedded. At ~195ms/chunk for BGE, 92K chunks = ~5 hours. Plan accordingly.
- **Model files cached**: First run downloads models; subsequent runs use cached versions. Cached at `~/.cache/huggingface/hub/models--*`.
