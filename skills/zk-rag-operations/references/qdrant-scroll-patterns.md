# Qdrant Scroll Patterns

## Basic scroll (all points in collection)

```python
from qdrant_client import QdrantClient
client = QdrantClient(location="http://127.0.0.1:6333")

offset = None
while True:
    resp = client.scroll(
        collection_name="army",
        scroll_request={
            "limit": 1000,
            "with_payload": ["doc_id", "title", "branch"],
            "offset": offset,
        }
    )
    points = resp[0]
    offset = resp[1]
    if not offset:
        break
    # process points
```

## Scroll-to-deduplicate (unique doc_ids)

Collect all points, deduplicate by `doc_id` keeping first occurrence:

```python
docs: dict[str, dict] = {}
for point in points:
    doc_id = point.payload.get("doc_id")
    if not doc_id or doc_id in docs:
        continue
    docs[doc_id] = {
        "doc_id": doc_id,
        "title": point.payload.get("title") or "Untitled",
        "branch": point.payload.get("branch") or collection,
    }
```

## Get point counts per collection

```python
from qdrant_client import QdrantClient
client = QdrantClient(location="http://127.0.0.1:6333")
collections = client.get_collections()
for c in collections.collections:
    info = client.get_collection(c.name)
    print(f"{c.name}: {info.points_count} points")
```

## Delete all collections (full wipe)

Stop both qdrant and the API service first:

```bash
sudo systemctl stop zk-rag-api
sudo systemctl stop qdrant
sudo rm -rf <DATA>qdrant/storage/*
sudo systemctl start qdrant
# Verify:
curl http://127.0.0.1:6333/collections  # should show empty
```
