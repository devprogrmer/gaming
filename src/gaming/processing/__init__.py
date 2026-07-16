"""Processing: normalization and filtering."""

from __future__ import annotations

from .normalize import normalize_records, collapse_prefixes
from .filters import apply_filters, matches

__all__ = [
    "normalize_records",
    "collapse_prefixes",
    "apply_filters",
    "matches",
]
