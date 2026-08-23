"""Index builder.

Orchestrates the index build pipeline:
  scan source file directories -> chunk -> embed -> upsert into ChromaDB

The build is incremental and idempotent:
  - source files are fingerprinted by (mtime_ns, size) in a small state file
  - unchanged files are skipped, changed/new files are re-embedded and upserted
    (deterministic content-hash chunk ids), deleted files have their chunks removed
  - an embedding failure on one file is logged and does not abort the rest of the build,
    and the failed file's state is not advanced so it is retried on the next run

Standalone usage:
  python -m backend.rag.indexer --all       # build everything
  python -m backend.rag.indexer --specs      # build the specs index only
  python -m backend.rag.indexer --cases      # build the cases index only
  python -m backend.rag.indexer --status     # view index status
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from .config import (
    SPECS_DIR,
    CASES_DIR,
    SPEC_EXTENSIONS,
    CASE_EXTENSIONS,
    COLLECTION_SPECS,
    COLLECTION_CASES,
    DATA_DIR,
    embedding_available,
)
from .schemas import IndexStatus, CollectionStatus, Channel
from .splitters import split_spec, split_case

logger = logging.getLogger(__name__)

_STATE_FILE = DATA_DIR / "index_state.json"


class IndexBuilder:
    """Index builder - orchestrates the scan -> chunk -> index pipeline."""

    def __init__(self):
        from .vector_store import VectorStoreManager

        self.store = VectorStoreManager()

    # ── Build-state helpers ──

    def _load_state(self) -> dict[str, dict[str, tuple[int, int]]]:
        """Load the per-channel file fingerprint state. Returns {"specs": {name: [mtime, size]}, ...}."""
        default: dict[str, dict[str, tuple[int, int]]] = {"specs": {}, "cases": {}}
        if not _STATE_FILE.exists():
            return default
        try:
            raw = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            return {
                "specs": {name: tuple(sig) for name, sig in raw.get("specs", {}).items()},
                "cases": {name: tuple(sig) for name, sig in raw.get("cases", {}).items()},
            }
        except Exception as exc:
            logger.warning("Failed to read index state (%s); rebuilding from scratch.", exc)
            return default

    def _save_state(self, state: dict[str, dict[str, tuple[int, int]]]) -> None:
        """Atomically persist the build state."""
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _STATE_FILE.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {
                        channel: {name: list(sig) for name, sig in files.items()}
                        for channel, files in state.items()
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(_STATE_FILE)
        except Exception as exc:
            logger.warning("Failed to persist index state: %s", exc)

    def _build_channel(
        self,
        channel: Channel,
        source_dir: Optional[Path],
        extensions: set[str],
        split_fn,
        default_dir: Path,
        collection_name: str,
        state_key: str,
    ) -> int:
        """Shared incremental build for one channel (specs or cases)."""
        source_dir = source_dir or default_dir
        if not source_dir.exists():
            logger.warning("Source directory not found: %s", source_dir)
            return 0

        files = sorted(
            f
            for f in source_dir.iterdir()
            if f.is_file()
            and f.suffix.lower() in extensions
            and not f.name.startswith(".")  # skip .gitkeep
            and f.name.lower() != "readme.md"  # the manifest is not process knowledge
        )
        if not files:
            logger.info("No source files found in %s", source_dir)
            return 0

        state = self._load_state()
        prev = state.get(state_key, {})
        current: dict[str, tuple[int, int]] = {}
        changed: list[Path] = []
        for file_path in files:
            st = file_path.stat()
            sig = (st.st_mtime_ns, st.st_size)
            current[file_path.name] = sig
            if prev.get(file_path.name) != sig:
                changed.append(file_path)

        # If the collection was wiped (e.g. an embedding-model mismatch triggered a
        # recreate) but the state file still claims files were indexed, force a full
        # re-index instead of skipping everything.
        try:
            col_count = self.store.get_collection_status(collection_name)["document_count"]
        except Exception:
            col_count = 0
        if col_count == 0 and prev:
            logger.warning(
                "%s collection is empty but state shows %d indexed files; forcing full re-index.",
                state_key,
                len(prev),
            )
            changed = list(files)

        # Remove chunks belonging to deleted source files.
        for name in prev:
            if name not in current:
                try:
                    removed = self.store.delete_by_source(name, channel)
                    logger.info("Removed %d stale chunks for deleted source %s", removed, name)
                except Exception as exc:
                    logger.warning("Failed to clean up deleted source %s: %s", name, exc)

        total = 0
        ok: list[str] = []
        for file_path in changed:
            logger.info("Indexing %s file: %s", state_key, file_path.name)
            try:
                content = file_path.read_text(encoding="utf-8")
                chunks = split_fn(str(file_path), content)
                if chunks:
                    # Idempotent upsert: drop any previous chunks for this source, then re-add.
                    try:
                        self.store.delete_by_source(file_path.name, channel)
                    except Exception as exc:
                        logger.warning("Pre-add cleanup for %s skipped: %s", file_path.name, exc)
                    self.store.add_specs(
                        chunks
                    ) if channel == Channel.SPECS else self.store.add_cases(chunks)
                    total += len(chunks)
                ok.append(file_path.name)
            except Exception as exc:
                # A single bad file must not abort the whole build; its state is not
                # advanced so the next run will retry it.
                logger.error("Failed to index %s file %s: %s", state_key, file_path.name, exc)

        changed_names = {f.name for f in changed}
        unchanged = {name: sig for name, sig in current.items() if name not in changed_names}
        updated = {name: current[name] for name in ok if name in current}
        state[state_key] = {**unchanged, **updated}
        self._save_state(state)

        logger.info(
            "%s index built: %d chunks from %d files (%d skipped, %d removed)",
            state_key,
            total,
            len(files),
            len(files) - len(changed),
            len(prev) - len(current),
        )
        return total

    # ── Specs index ──

    def build_spec_index(self, specs_dir: Optional[Path] = None) -> int:
        """Build the specs (process handbook) index incrementally.

        Parameters
        ----------
        specs_dir : Path, optional
            Source directory of spec files; defaults to config.SPECS_DIR.

        Returns
        -------
        int
            Number of chunks indexed in this run.
        """
        return self._build_channel(
            Channel.SPECS,
            specs_dir,
            SPEC_EXTENSIONS,
            split_spec,
            SPECS_DIR,
            COLLECTION_SPECS,
            "specs",
        )

    # ── Cases index ──

    def build_case_index(self, cases_dir: Optional[Path] = None) -> int:
        """Build the cases (case base) index incrementally.

        Parameters
        ----------
        cases_dir : Path, optional
            Source directory of case files; defaults to config.CASES_DIR.

        Returns
        -------
        int
            Number of chunks indexed in this run.
        """
        return self._build_channel(
            Channel.CASES,
            cases_dir,
            CASE_EXTENSIONS,
            split_case,
            CASES_DIR,
            COLLECTION_CASES,
            "cases",
        )

    # ── Build all ──

    def build_all(self) -> int:
        """Build both channel indexes in one step."""
        spec_count = self.build_spec_index()
        case_count = self.build_case_index()
        total = spec_count + case_count
        logger.info(
            "RAG index build complete: %d chunks indexed this run (specs=%d, cases=%d)",
            total,
            spec_count,
            case_count,
        )
        self._sync_bm25()
        return total

    def _sync_bm25(self) -> None:
        """Invalidate live retrievers' cached BM25 indexes after a build.

        Live HybridRetriever instances resync from the (now updated) vector store on
        their next search, instead of serving a stale BM25 snapshot until restart.
        """
        try:
            from .retriever import invalidate_all_bm25

            invalidate_all_bm25()
        except ImportError:
            logger.debug("Retriever not available, skipping BM25 invalidation")
        except Exception as exc:
            logger.warning("BM25 invalidation skipped: %s", exc)

    # ── Status query ──

    def get_status(self) -> IndexStatus:
        """Get the dual-channel index status."""
        specs_info = self.store.get_collection_status(COLLECTION_SPECS)
        cases_info = self.store.get_collection_status(COLLECTION_CASES)

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
