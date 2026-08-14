"""Index builder.

Orchestrates the full index build pipeline:
  scan source file directories -> chunk -> embed -> write to ChromaDB

Standalone usage:
  python -m backend.rag.indexer --all       # build everything
  python -m backend.rag.indexer --specs      # build the specs index only
  python -m backend.rag.indexer --cases      # build the cases index only
  python -m backend.rag.indexer --status     # view index status
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from .config import SPECS_DIR, CASES_DIR, SPEC_EXTENSIONS, CASE_EXTENSIONS, embedding_available
from .schemas import IndexStatus, CollectionStatus
from .splitters import split_spec, split_case

logger = logging.getLogger(__name__)


class IndexBuilder:
    """Index builder - orchestrates the scan -> chunk -> index pipeline."""

    def __init__(self):
        from .vector_store import VectorStoreManager
        self.store = VectorStoreManager()

    def _sync_bm25(self) -> None:
        """Sync the BM25 retrieval engine after building the index."""
        try:
            from .bm25_index import BM25IndexManager
            bm25 = BM25IndexManager()
            bm25.sync_from_vector_store(self.store)
            logger.info("BM25 index synced after build")
        except ImportError:
            logger.debug("BM25 not available, skipping sync")
        except Exception as exc:
            logger.warning("BM25 sync skipped: %s", exc)

    # ── Specs index ──

    def build_spec_index(self, specs_dir: Optional[Path] = None) -> int:
        """Build the specs (process handbook) index.

        Parameters
        ----------
        specs_dir : Path, optional
            Source directory of spec files; defaults to config.SPECS_DIR.

        Returns
        -------
        int
            Total number of chunks indexed.
        """
        source_dir = specs_dir or SPECS_DIR
        if not source_dir.exists():
            logger.warning("Specs directory not found: %s", source_dir)
            return 0

        files = [
            f for f in source_dir.iterdir()
            if f.is_file() and f.suffix.lower() in SPEC_EXTENSIONS
        ]
        if not files:
            logger.info("No spec source files found in %s", source_dir)
            return 0

        # Clear the old index first
        self.store.clear("shaftplanner_specs")

        total = 0
        for file_path in files:
            logger.info("Indexing spec file: %s", file_path.name)
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.error("Failed to read spec file %s: %s", file_path.name, exc)
                continue

            chunks = split_spec(str(file_path), content)
            if chunks:
                self.store.add_specs(chunks)
                total += len(chunks)

        logger.info("Spec index built: %d chunks from %d files", total, len(files))
        self._sync_bm25()
        return total

    # ── Cases index ──

    def build_case_index(self, cases_dir: Optional[Path] = None) -> int:
        """Build the cases (case base) index.

        Parameters
        ----------
        cases_dir : Path, optional
            Source directory of case files; defaults to config.CASES_DIR.

        Returns
        -------
        int
            Total number of chunks indexed.
        """
        source_dir = cases_dir or CASES_DIR
        if not source_dir.exists():
            logger.warning("Cases directory not found: %s", source_dir)
            return 0

        files = [
            f for f in source_dir.iterdir()
            if f.is_file() and f.suffix.lower() in CASE_EXTENSIONS
            and not f.name.startswith(".")  # skip .gitkeep
        ]
        if not files:
            logger.info("No case source files found in %s", source_dir)
            return 0

        # Clear the old index first
        self.store.clear("shaftplanner_cases")

        total = 0
        for file_path in files:
            logger.info("Indexing case file: %s", file_path.name)
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.error("Failed to read case file %s: %s", file_path.name, exc)
                continue

            chunks = split_case(str(file_path), content)
            if chunks:
                self.store.add_cases(chunks)
                total += len(chunks)

        logger.info("Case index built: %d chunks from %d files", total, len(files))
        self._sync_bm25()
        return total

    # ── Build all ──

    def build_all(self) -> int:
        """Build both channel indexes in one step."""
        spec_count = self.build_spec_index()
        case_count = self.build_case_index()
        total = spec_count + case_count
        logger.info(
            "RAG index build complete: %d total chunks (specs=%d, cases=%d)",
            total, spec_count, case_count,
        )
        return total

    # ── Status query ──

    def get_status(self) -> IndexStatus:
        """Get the dual-channel index status."""
        specs_info = self.store.get_collection_status("shaftplanner_specs")
        cases_info = self.store.get_collection_status("shaftplanner_cases")

        return IndexStatus(
            specs=CollectionStatus(**specs_info),
            cases=CollectionStatus(**cases_info),
            embedding_available=embedding_available(),
        )


# ── Module-level convenience functions ──

_builder: Optional[IndexBuilder] = None


def _get_builder() -> IndexBuilder:
    global _builder
    if _builder is None:
        _builder = IndexBuilder()
    return _builder


def build_index() -> int:
    """Build both channel indexes in one step."""
    return _get_builder().build_all()


def get_index_status() -> IndexStatus:
    """Get the index status."""
    return _get_builder().get_status()


# ── CLI entry point ──

if __name__ == "__main__":
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    builder = IndexBuilder()

    if "--status" in sys.argv:
        status = builder.get_status()
        print("\n=== RAG Index Status ===")
        print(f"  Specs:  {status.specs.document_count} documents")
        print(f"  Cases:  {status.cases.document_count} documents")
        print(f"  Embedding available:  {status.embedding_available}")
        sys.exit(0)

    if "--specs" in sys.argv:
        count = builder.build_spec_index()
        print(f"Spec index: {count} chunks indexed.")
        sys.exit(0)

    if "--cases" in sys.argv:
        count = builder.build_case_index()
        print(f"Case index: {count} chunks indexed.")
        sys.exit(0)

    # Default: build everything
    total = builder.build_all()
    print(f"Dual-channel index built: {total} total chunks.")
