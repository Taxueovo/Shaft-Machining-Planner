"""Abstract base class for splitters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any



class BaseSplitter(ABC):
    """Abstract base class for splitters - defines a unified chunking interface."""

    @abstractmethod
    def split(self, source_path: str, content: str) -> list[dict[str, Any]]:
        """Split document content into a list of chunk metadata.

        Parameters
        ----------
        source_path : str
            Source file path (used to populate the chunk's source field).
        content : str
            The document's raw text content.

        Returns
        -------
        list of dict
            Chunk metadata list; each dict can be used directly to construct
            a SpecChunk or CaseChunk.
        """
        ...


class SpecSplitter(BaseSplitter):
    """Specs splitter - concrete splitting logic to be implemented."""
    pass


class CaseSplitter(BaseSplitter):
    """Cases splitter - concrete splitting logic to be implemented."""
    pass
