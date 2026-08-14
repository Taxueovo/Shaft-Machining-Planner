"""RAG module configuration center.

Central management of all paths, collection names, and chunking parameters.
The embedding model uses its own environment variables, separate from the main LLM.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Module root directory ──

MODULE_DIR = Path(__file__).resolve().parent
DATA_DIR = MODULE_DIR / "data"
SPECS_DIR = DATA_DIR / "specs"
CASES_DIR = DATA_DIR / "cases"
CHROMA_DIR = DATA_DIR / "chroma"

# ── Embedding model configuration ──
# The model can be overridden independently (default text-embedding-3-small);
# when base_url / api_key are not set separately, fall back to the main LLM
# configuration so the API stays consistent with the main LLM.

EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL", "")
EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# ── ChromaDB configuration ──

COLLECTION_SPECS: str = "shaftplanner_specs"
COLLECTION_CASES: str = "shaftplanner_cases"

# ── Supported file extensions ──

SPEC_EXTENSIONS: set[str] = {".md", ".txt", ".rst"}
CASE_EXTENSIONS: set[str] = {".json"}

# ── Specs chunking parameters ──

SPEC_CHUNK_SIZE: int = 1200       # max characters per chunk
SPEC_CHUNK_OVERLAP: int = 150     # overlap characters between adjacent chunks
SPEC_MIN_CHUNK_SIZE: int = 80     # chunks smaller than this are merged into the previous one

# ── Cases chunking parameters ──

CASE_CHUNK_AS_WHOLE: bool = True  # each case is indexed as one complete chunk (preserving structural integrity)

# ── Hybrid retrieval parameters ──

HYBRID_TOP_K_RECALL: int = 10     # candidate count per recall path (BM25/Vector)
HYBRID_TOP_K_FINAL: int = 5       # number of final results
RRF_K: int = 60                   # RRF smoothing parameter (larger = smoother, default 60)

# ── Reranker configuration ──
# Disabled by default: bge-reranker-v2-m3 requires downloading 2.2GB from HuggingFace,
# the first call blocks for minutes, and the download fails outright when no mirror
# is reachable.
# To enable, set RERANKER_ENABLED=true in .env and configure
# HF_ENDPOINT=https://hf-mirror.com to download the model locally first.

RERANKER_ENABLED: bool = (
    os.getenv("RERANKER_ENABLED", "false").strip().lower()
    in ("1", "true", "yes", "on")
)
RERANKER_MODEL: str = os.getenv(
    "RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"
)


def embedding_available() -> bool:
    """Check whether an embedding model is configured."""
    return bool(EMBEDDING_API_KEY)
