"""Splitter module - dual-channel chunking for specs (process handbook) and cases (case base)."""

from .spec_splitter import split_spec
from .case_splitter import split_case

__all__ = ["split_spec", "split_case"]
