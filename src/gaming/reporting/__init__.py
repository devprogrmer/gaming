"""Reporting/exporters: console, JSON, CSV."""

from __future__ import annotations

from .console import render_console
from .json_export import to_json, write_json
from .csv_export import to_csv, write_csv

__all__ = [
    "render_console",
    "to_json",
    "write_json",
    "to_csv",
    "write_csv",
    "export",
]


def export(records, fmt: str, path=None) -> str:
    """Dispatch to the requested exporter. Returns the produced text.

    ``fmt`` is one of ``console`` | ``json`` | ``csv``. When ``path`` is
    given (json/csv), the content is also written to disk.
    """
    fmt = (fmt or "console").lower()
    if fmt == "console":
        return render_console(records)
    if fmt == "json":
        text = to_json(records)
        if path:
            write_json(records, path)
        return text
    if fmt == "csv":
        text = to_csv(records)
        if path:
            write_csv(records, path)
        return text
    raise ValueError(f"unknown output format: {fmt!r}")
