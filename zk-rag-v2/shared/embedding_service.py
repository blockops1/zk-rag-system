"""Embedding Service — owns the Qwen3-Embedding-0.6B model.

Separated from the RAG API to allow independent memory management.
The RAG API calls this service over HTTP; this process enforces
a hard memory ceiling via systemd MemoryMax= and a bounded semaphore
for concurrent encode requests.

Runs on port 8200.
"""

import logging
import logging.handlers
import os
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_DIR = ".../data/logs"
LOG_FILE = f"{LOG_DIR}/embedding_service.log"
os.makedirs(LOG_DIR, exist_ok=True)

_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_FILE, when="midnight", interval=1, backupCount=7
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger(__name__)

# ── Memory guard ─────────────────────────────────────────────────────────────

# Max concurrent encode requests — derived from CPU count, capped at 4 (Ruff pattern).
# Each encode holds ~2-3 GB of model weights + working memory.
MAX_CONCURRENT = min(os.cpu_count() or 1, 4)

_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="encode-")
_semaphore = threading.Semaphore(MAX_CONCURRENT)

# ── Model (loaded once at startup) ────────────────────────────────────────────

_model: SentenceTransformer | None = None
_model_loaded = False


def _load_model() -> SentenceTransformer:
    """Load the embedding model. Called once at startup."""
    global _model_loaded
    logger.info("Loading embedding model Qwen/Qwen3-Embedding-0.6B ...")
    model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
    _model_loaded = True
    logger.info(f"Embedding model loaded. dim={model.get_sentence_embedding_dimension()}")
    return model


# ── FastAPI app ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Eager-load the model on startup. Fail fast if it can't load."""
    global _model
    try:
        _model = _load_model()
    except Exception as e:
        logger.critical(f"Failed to load embedding model: {e}")
        raise  # FastAPI will refuse to start
    logger.info("Embedding service ready.")
    yield
    logger.info("Embedding service shutting down.")


app = FastAPI(
    title="Embedding Service",
    description="Qwen3-Embedding-0.6B vectorization endpoint for RAG API",
    lifespan=lifespan,
)

# ── Request / response models ──────────────────────────────────────────────────


class EncodeRequest(BaseModel):
    texts: List[str]


class EncodeResponse(BaseModel):
    embeddings: List[List[float]]
    model: str = "Qwen/Qwen3-Embedding-0.6B"
    dimension: int


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    """Health check — verifies model is loaded."""
    if not _model_loaded or _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {
        "status": "ok",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "concurrent_limit": MAX_CONCURRENT,
    }


@app.post("/encode", response_model=EncodeResponse)
def encode(req: EncodeRequest):
    """Encode a list of texts into embedding vectors.

    Concurrent requests are queued by the semaphore. If the queue is full,
    the request waits up to 60s then returns 503.
    """
    if not req.texts:
        raise HTTPException(status_code=400, detail="texts cannot be empty")

    # Try to acquire semaphore with timeout
    acquired = _semaphore.acquire(timeout=60)
    if not acquired:
        raise HTTPException(
            status_code=503,
            detail="Server at capacity — try again shortly",
        )

    try:
        logger.info(f"Encode request: {len(req.texts)} texts")

        # Run sync encode in thread pool so we don't block the event loop
        def _encode():
            embeddings = _model.encode(
                req.texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return [emb.tolist() for emb in embeddings]

        # Submit to thread pool and wait
        future = _executor.submit(_encode)
        embeddings = future.result(timeout=60)

        logger.info(f"Encode complete: {len(embeddings)} vectors returned")
        return EncodeResponse(
            embeddings=embeddings,
            model="Qwen/Qwen3-Embedding-0.6B",
            dimension=len(embeddings[0]) if embeddings else 0,
        )
    except Exception as e:
        logger.error(f"Encode error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _semaphore.release()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # Graceful shutdown on SIGTERM (systemd sends this)
    def _sigterm_handler(signum, frame):
        logger.info("Received SIGTERM, shutting down gracefully...")
        _executor.shutdown(wait=True, cancel_futures=False)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)

    uvicorn.run(app, host="127.0.0.1", port=8200, log_level="info")
