"""Persistent storage for scan results using the stdlib ``sqlite3``.

Two tables:

    scans     one row per scan run (timestamp, scope, host/verdict counts)
    results   one row per probed host, linked to its scan

The database lives under the application home (see :mod:`.paths`) so results
persist between runs. All access goes through :class:`HistoryStore`, which is
safe to open repeatedly (the schema is created on demand).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import paths
from .classify import BAD, GOOD, MEDIUM, ProbeResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT    NOT NULL,
    scope      TEXT    NOT NULL,
    total      INTEGER NOT NULL DEFAULT 0,
    good       INTEGER NOT NULL DEFAULT 0,
    medium     INTEGER NOT NULL DEFAULT 0,
    bad        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS results (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id  INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    host     TEXT    NOT NULL,
    verdict  TEXT    NOT NULL,
    avg_ms   REAL,
    loss_pct REAL,
    sent     INTEGER NOT NULL DEFAULT 0,
    received INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_results_scan ON results(scan_id);
"""


@dataclass(slots=True)
class ScanSummary:
    """A saved scan's headline row."""

    id: int
    started_at: str
    scope: str
    total: int
    good: int
    medium: int
    bad: int


@dataclass(slots=True)
class ResultRow:
    """A single persisted host result."""

    host: str
    verdict: str
    avg_ms: float | None
    loss_pct: float | None
    sent: int
    received: int


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class HistoryStore:
    """Thin wrapper around the SQLite scan-history database."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else paths.database_path()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """Create the schema if it does not already exist."""
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def save_scan(
        self,
        scope: str,
        results: list[tuple[ProbeResult, str]],
        *,
        started_at: str | None = None,
    ) -> int:
        """Persist a completed scan and its per-host results.

        ``results`` is a list of ``(ProbeResult, verdict)`` pairs. Returns the
        new scan's id.
        """
        self.initialize()
        counts = {GOOD: 0, MEDIUM: 0, BAD: 0}
        for _probe, verdict in results:
            if verdict in counts:
                counts[verdict] += 1

        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO scans (started_at, scope, total, good, medium, bad) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    started_at or _utc_now_iso(),
                    scope,
                    len(results),
                    counts[GOOD],
                    counts[MEDIUM],
                    counts[BAD],
                ),
            )
            scan_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO results "
                "(scan_id, host, verdict, avg_ms, loss_pct, sent, received) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        scan_id,
                        probe.host,
                        verdict,
                        probe.avg_ms,
                        probe.loss_pct,
                        probe.sent,
                        probe.received,
                    )
                    for probe, verdict in results
                ],
            )
        return scan_id

    def list_scans(self, limit: int = 20) -> list[ScanSummary]:
        """Return recent scans, newest first."""
        self.initialize()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, started_at, scope, total, good, medium, bad "
                "FROM scans ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            ScanSummary(
                id=r["id"],
                started_at=r["started_at"],
                scope=r["scope"],
                total=r["total"],
                good=r["good"],
                medium=r["medium"],
                bad=r["bad"],
            )
            for r in rows
        ]

    def get_results(self, scan_id: int) -> list[ResultRow]:
        """Return all host results for a given scan, best verdict first."""
        self.initialize()
        order = f"CASE verdict WHEN '{GOOD}' THEN 0 WHEN '{MEDIUM}' THEN 1 ELSE 2 END"
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT host, verdict, avg_ms, loss_pct, sent, received "
                f"FROM results WHERE scan_id = ? ORDER BY {order}, avg_ms",
                (int(scan_id),),
            ).fetchall()
        return [
            ResultRow(
                host=r["host"],
                verdict=r["verdict"],
                avg_ms=r["avg_ms"],
                loss_pct=r["loss_pct"],
                sent=r["sent"],
                received=r["received"],
            )
            for r in rows
        ]

    def clear(self) -> None:
        """Delete all saved scans and results."""
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
            conn.execute("DELETE FROM results")
            conn.execute("DELETE FROM scans")
