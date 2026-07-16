"""Human-readable console rendering (plain text table, no dependencies)."""

from __future__ import annotations

from typing import Iterable

from ..models import IPRecord


def _fmt_bool(value) -> str:
    if value is None:
        return "-"
    return "yes" if value else "no"


def _cell(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return ",".join(str(v) for v in value) if value else "-"
    return str(value)


def render_console(records: Iterable[IPRecord]) -> str:
    recs = list(records)
    headers = [
        "SOURCE",
        "ASN",
        "ORG",
        "CC",
        "PREFIX",
        "ALIVE",
        "GLOBAL",
        "PORTS",
        "NOTES",
    ]

    def row_for(r: IPRecord) -> list[str]:
        return [
            _cell(r.source),
            _cell(r.asn),
            _cell(r.organization),
            _cell(r.country),
            _cell(r.prefix),
            _fmt_bool(r.alive),
            _fmt_bool(r.global_reachable),
            _cell(r.open_ports),
            _cell(r.notes),
        ]

    rows = [row_for(r) for r in recs]

    if not rows:
        return "No records.\n"

    # Compute column widths (cap the widest columns for terminal sanity).
    caps = [14, 10, 26, 3, 20, 5, 6, 14, 40]
    widths = []
    for i, head in enumerate(headers):
        longest = max([len(head)] + [len(r[i]) for r in rows])
        widths.append(min(longest, caps[i]))

    def clip(text: str, width: int) -> str:
        return text if len(text) <= width else text[: width - 1] + "…"

    def fmt_row(cells: list[str]) -> str:
        return "  ".join(clip(c, widths[i]).ljust(widths[i]) for i, c in enumerate(cells))

    lines = [fmt_row(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt_row(r) for r in rows)
    lines.append("")
    lines.append(f"{len(recs)} record(s).")
    return "\n".join(lines) + "\n"
