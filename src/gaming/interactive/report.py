"""Plain-text rendering of interactive scan results and summaries.

Dependency-free tables and summary lines with optional ANSI colour on the
GOOD / MEDIUM / BAD verdicts. Mirrors the style of
:mod:`gaming.reporting.console` used by the core CLI.
"""

from __future__ import annotations

import sys
from typing import TextIO

from .classify import BAD, GOOD, MEDIUM, classify_bidirectional
from .progress import colorize
from .scanner import ScanReport
from .storage import ResultRow, ScanSummary


def _fmt_ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _fmt_loss(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}%"


def _fmt_abroad(row: ResultRow) -> str:
    """Render the abroad cell: OK/FAIL (n/total), 'unavailable', or 'not checked'.

    ``abroad_status`` (Part D) distinguishes a provider outage from a genuine
    "not checked": when the status is ``unavailable`` the cell reads
    ``unavailable`` so a user can tell "check-host.net is down right now" apart
    from "this IP simply isn't internationally reachable". Rows written before
    the status column existed have ``abroad_status is None`` and fall back to the
    original reachable-based rendering.
    """
    if getattr(row, "abroad_status", None) == "unavailable":
        return "unavailable"
    if row.abroad_reachable is None:
        return "not checked"
    ok = row.abroad_nodes_ok if row.abroad_nodes_ok is not None else 0
    total = row.abroad_nodes_total if row.abroad_nodes_total is not None else 0
    state = "OK" if row.abroad_reachable else "FAIL"
    return f"{state} ({ok}/{total})"


def _fmt_ports(row: ResultRow) -> str:
    return ",".join(str(p) for p in row.open_ports) if row.open_ports else "-"


def _combined_of(row: ResultRow) -> str:
    """The stored combined verdict, or one derived from the row's fields."""
    if row.combined_verdict:
        return row.combined_verdict
    iran = row.received > 0
    return classify_bidirectional(iran, row.abroad_reachable)


def summary_line(counts: dict[str, int], total: int, stream: TextIO | None = None) -> str:
    """One-line GOOD/MEDIUM/BAD tally with colour."""
    stream = stream or sys.stdout
    parts = [
        f"{colorize(GOOD, stream)}: {counts.get(GOOD, 0)}",
        f"{colorize(MEDIUM, stream)}: {counts.get(MEDIUM, 0)}",
        f"{colorize(BAD, stream)}: {counts.get(BAD, 0)}",
    ]
    return f"Total: {total}   " + "   ".join(parts)


def combined_summary_line(
    counts: dict[str, int], stream: TextIO | None = None
) -> str:
    """One-line INTERNATIONAL/IRAN_ONLY/ABROAD_ONLY/UNREACHABLE tally, coloured."""
    from .classify import ABROAD_ONLY, INTERNATIONAL, IRAN_ONLY, UNREACHABLE

    stream = stream or sys.stdout
    parts = [
        f"{colorize(INTERNATIONAL, stream)}: {counts.get(INTERNATIONAL, 0)}",
        f"{colorize(IRAN_ONLY, stream)}: {counts.get(IRAN_ONLY, 0)}",
        f"{colorize(ABROAD_ONLY, stream)}: {counts.get(ABROAD_ONLY, 0)}",
        f"{colorize(UNREACHABLE, stream)}: {counts.get(UNREACHABLE, 0)}",
    ]
    return "   ".join(parts)


def render_group_latency(groups: list, *, title: str = "COUNTRY") -> str:
    """Render per-destination-group latency, lowest (best) first.

    ``groups`` is a list of :class:`gaming.interactive.scanner.GroupLatency`.
    Because RTTs are measured from the machine running the scan (an Iranian
    server in production), the top row is the destination grouping that answers
    fastest *from Iran*. Groups with no measurable latency are listed last as
    ``-``.
    """
    if not groups:
        return "No live results to group by latency.\n"

    headers = [title, "LIVE", "AVG(ms)"]
    body = [
        [
            g.key,
            f"{g.live}/{g.total}",
            _fmt_ms(g.avg_ms),
        ]
        for g in groups
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in body)) for i in range(len(headers))
    ]

    def fmt(cells: list[str]) -> str:
        return "  ".join(cells[i].ljust(widths[i]) for i in range(len(cells)))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(row) for row in body)

    # Call out the winner explicitly when at least one group has a measurement.
    best = next((g for g in groups if g.avg_ms is not None), None)
    if best is not None:
        lines.append(
            f"\nLowest latency from here: {best.key} "
            f"({best.avg_ms:.1f} ms avg over {best.live} live host(s))."
        )
    return "\n".join(lines) + "\n"


def render_results(
    rows: list[ResultRow],
    *,
    stream: TextIO | None = None,
    limit: int | None = None,
) -> str:
    """Render probed hosts as an aligned table (best verdicts first).

    Columns: HOST, HEALTH (Iran latency verdict), AVG(ms), LOSS, RECV/SENT,
    ABROAD (check-host.net OK/FAIL + node count, or "not checked"), WHITELIST
    (combined INTERNATIONAL/IRAN_ONLY/ABROAD_ONLY/UNREACHABLE verdict). A PORTS
    column is appended only when at least one row has open ports, so ordinary
    (no-port-scan) output stays unchanged in width.
    """
    stream = stream or sys.stdout
    shown = rows if limit is None else rows[:limit]
    if not shown:
        return "No results.\n"

    show_ports = any(r.open_ports for r in shown)
    headers = ["HOST", "HEALTH", "AVG(ms)", "LOSS", "RECV/SENT", "ABROAD", "WHITELIST"]
    if show_ports:
        headers.append("PORTS")

    body = []
    combined_col = headers.index("WHITELIST")
    for r in shown:
        cells = [
            r.host,
            r.verdict,
            _fmt_ms(r.avg_ms),
            _fmt_loss(r.loss_pct),
            f"{r.received}/{r.sent}",
            _fmt_abroad(r),
            _combined_of(r),
        ]
        if show_ports:
            cells.append(_fmt_ports(r))
        body.append(cells)

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in body)) for i in range(len(headers))
    ]

    def fmt_row(cells: list[str], colored: bool) -> str:
        out = []
        for i, cell in enumerate(cells):
            text = cell.ljust(widths[i])
            # Colour the HEALTH (1) and WHITELIST (combined) columns.
            if colored and i in (1, combined_col):
                text = text.replace(cell, colorize(cell, stream))
            out.append(text)
        return "  ".join(out)

    lines = [fmt_row(headers, False), "  ".join("-" * w for w in widths)]
    lines.extend(fmt_row(row, True) for row in body)
    if limit is not None and len(rows) > limit:
        lines.append(f"... and {len(rows) - limit} more (see full history).")
    return "\n".join(lines) + "\n"


def render_report(report: ScanReport, stream: TextIO | None = None) -> str:
    """Render a fresh :class:`ScanReport` (probe/verdict pairs + combined data)."""
    combined_by_host = {c.host: c for c in report.combined}
    rows = []
    for probe, verdict in report.results:
        c = combined_by_host.get(probe.host)
        rows.append(
            ResultRow(
                host=probe.host,
                verdict=verdict,
                avg_ms=probe.avg_ms,
                loss_pct=probe.loss_pct,
                sent=probe.sent,
                received=probe.received,
                abroad_reachable=(c.abroad_reachable if c else None),
                abroad_nodes_ok=(c.abroad_nodes_ok if c else None),
                abroad_nodes_total=(c.abroad_nodes_total if c else None),
                combined_verdict=(
                    classify_bidirectional(c.iran_reachable, c.abroad_reachable)
                    if c
                    else None
                ),
                open_ports=(list(c.open_ports) if c else []),
                abroad_status=(getattr(c, "abroad_status", None) if c else None),
            )
        )
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
