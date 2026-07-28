"""Reporting/exporters: console, JSON, CSV, bare IP list."""

from __future__ import annotations

from .console import render_console
from .csv_export import to_csv, write_csv
from .ip_list import to_ip_list, write_ip_list
from .json_export import to_json, write_json

__all__ = [
    "render_console",
    "to_json",
    "write_json",
    "to_csv",
    "write_csv",
    "to_ip_list",
    "write_ip_list",
    "export",
]

#: Formats that emit only machine-consumable data (no decoration/headers).
PLAIN_FORMATS = frozenset({"json", "csv", "ip-list"})


def export(records, fmt: str, path=None) -> str:
    """Dispatch to the requested exporter. Returns the produced text.

    ``fmt`` is one of ``console`` | ``json`` | ``csv`` | ``ip-list``. When
    ``path`` is given, the content is also written to disk.
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
    if fmt in ("ip-list", "ip_list", "iplist"):
        text = to_ip_list(records)
        if path:
            write_ip_list(records, path)
        return text
    raise ValueError(f"unknown output format: {fmt!r}")
