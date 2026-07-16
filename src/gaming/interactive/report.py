"""Plain-text rendering of interactive scan results and summaries.

Dependency-free tables and summary lines with optional ANSI colour on the
GOOD / MEDIUM / BAD verdicts. Mirrors the style of
:mod:`gaming.reporting.console` used by the core CLI.
"""

from __future__ import annotations

import sys
from typing import TextIO

from .classify import BAD, GOOD, MEDIUM
from .progress import colorize
from .scanner import ScanReport
from .storage import ResultRow, ScanSummary


def _fmt_ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _fmt_loss(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}%"


def summary_line(counts: dict[str, int], total: int, stream: TextIO | None = None) -> str:
    """One-line GOOD/MEDIUM/BAD tally with colour."""
    stream = stream or sys.stdout
    parts = [
        f"{colorize(GOOD, stream)}: {counts.get(GOOD, 0)}",
        f"{colorize(MEDIUM, stream)}: {counts.get(MEDIUM, 0)}",
        f"{colorize(BAD, stream)}: {counts.get(BAD, 0)}",
    ]
    return f"Total: {total}   " + "   ".join(parts)


def render_results(
    rows: list[ResultRow],
    *,
    stream: TextIO | None = None,
    limit: int | None = None,
) -> str:
    """Render probed hosts as an aligned table (best verdicts first)."""
    stream = stream or sys.stdout
    shown = rows if limit is None else rows[:limit]
    if not shown:
        return "No results.\n"

    headers = ["HOST", "HEALTH", "AVG(ms)", "LOSS", "RECV/SENT"]
    body = [
        [
            r.host,
            r.verdict,
            _fmt_ms(r.avg_ms),
            _fmt_loss(r.loss_pct),
            f"{r.received}/{r.sent}",
        ]
        for r in shown
    ]

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in body)) for i in range(len(headers))
    ]

    def fmt_row(cells: list[str], colored: bool) -> str:
        out = []
        for i, cell in enumerate(cells):
            text = cell.ljust(widths[i])
            if colored and i == 1:  # HEALTH column
                text = text.replace(cell, colorize(cell, stream))
            out.append(text)
        return "  ".join(out)

    lines = [fmt_row(headers, False), "  ".join("-" * w for w in widths)]
    lines.extend(fmt_row(row, True) for row in body)
    if limit is not None and len(rows) > limit:
        lines.append(f"... and {len(rows) - limit} more (see full history).")
    return "\n".join(lines) + "\n"


def render_report(report: ScanReport, stream: TextIO | None = None) -> str:
    """Render a fresh :class:`ScanReport` (probe/verdict pairs)."""
    rows = [
        ResultRow(
            host=probe.host,
            verdict=verdict,
            avg_ms=probe.avg_ms,
            loss_pct=probe.loss_pct,
            sent=probe.sent,
            received=probe.received,
        )
        for probe, verdict in report.results
    ]
    order = {GOOD: 0, MEDIUM: 1, BAD: 2}
    rows.sort(key=lambda r: (order.get(r.verdict, 3), r.avg_ms if r.avg_ms else 1e9))
    return render_results(rows, stream=stream)


def render_history(scans: list[ScanSummary]) -> str:
    """Render a list of saved scans as a compact table."""
    if not scans:
        return "No saved scans yet.\n"
    headers = ["ID", "WHEN (UTC)", "SCOPE", "TOTAL", "GOOD", "MED", "BAD"]
    body = [
        [
            str(s.id),
            s.started_at.replace("T", " ").replace("+00:00", ""),
            s.scope,
            str(s.total),
            str(s.good),
            str(s.medium),
            str(s.bad),
        ]
        for s in scans
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in body)) for i in range(len(headers))
    ]

    def fmt(cells: list[str]) -> str:
        return "  ".join(cells[i].ljust(widths[i]) for i in range(len(cells)))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(row) for row in body)
    return "\n".join(lines) + "\n"
