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
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import paths
from .classify import (
    BAD,
    GOOD,
    MEDIUM,
    CombinedResult,
    ProbeResult,
    classify_bidirectional,
)

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

CREATE TABLE IF NOT EXISTS discoveries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    prefix     TEXT    NOT NULL UNIQUE,
    category   TEXT,
    asn        TEXT,
    org        TEXT,
    country    TEXT,
    first_seen TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discoveries_seen ON discoveries(first_seen);

CREATE TABLE IF NOT EXISTS surface_visits (
    surface      TEXT PRIMARY KEY,
    last_visited TEXT NOT NULL,
    last_id      INTEGER NOT NULL DEFAULT 0
);
"""

# Additive columns introduced for bidirectional reachability + port visibility.
# Applied idempotently over any pre-existing ``results`` table so on-disk
# databases created before this feature keep working (old rows read back NULL,
# surfaced as "not checked"). Each entry is ``(column_name, column_type)``.
_RESULTS_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("abroad_reachable", "INTEGER"),
    ("abroad_nodes_ok", "INTEGER"),
    ("abroad_nodes_total", "INTEGER"),
    ("combined_verdict", "TEXT"),
    ("open_ports", "TEXT"),
    ("abroad_status", "TEXT"),
)


@dataclass(slots=True)
class DiscoveryRow:
    """One range the watcher saw for the first time, with when it saw it."""

    id: int
    prefix: str
    category: str | None
    asn: str | None
    org: str | None
    country: str | None
    first_seen: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "prefix": self.prefix,
            "category": self.category,
            "asn": self.asn,
            "org": self.org,
            "country": self.country,
            "first_seen": self.first_seen,
        }


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
    """A single persisted host result.

    The abroad/port fields are optional so rows written before the migration
    (which have no such columns) read back as ``None``/empty — displayed as
    "not checked" rather than a misleading ``False``.
    """

    host: str
    verdict: str
    avg_ms: float | None
    loss_pct: float | None
    sent: int
    received: int
    abroad_reachable: bool | None = None
    abroad_nodes_ok: int | None = None
    abroad_nodes_total: int | None = None
    combined_verdict: str | None = None
    open_ports: list[int] = field(default_factory=list)
    # Provider-availability signal (Part D): "ok" / "unavailable" /
    # "not_applicable". None for rows written before this column existed, which
    # display as "not checked" — indistinguishable from the old behaviour.
    abroad_status: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _text_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _encode_ports(ports: list[int] | None) -> str | None:
    if not ports:
        return None
    return ",".join(str(int(p)) for p in ports)


def _decode_ports(raw: object) -> list[int]:
    if not raw:
        return []
    text = str(raw).strip()
    if not text:
        return []
    out: list[int] = []
    for tok in text.split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.append(int(tok))
    return out


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
        """Create the schema if needed, then apply additive migrations.

        The migration step reads ``PRAGMA table_info(results)`` and issues an
        ``ALTER TABLE ... ADD COLUMN`` for any of the newer columns that are
        missing, so a database created by an older version is upgraded in place
        with no data loss and no manual step.
        """
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
            self._migrate_results(conn)

    @staticmethod
    def _migrate_results(conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(results)")}
        for column, col_type in _RESULTS_MIGRATIONS:
            if column not in existing:
                conn.execute(f"ALTER TABLE results ADD COLUMN {column} {col_type}")

    def save_scan(
        self,
        scope: str,
        results: list[tuple[ProbeResult, str]],
        *,
        combined: list[CombinedResult] | None = None,
        started_at: str | None = None,
    ) -> int:
        """Persist a completed scan and its per-host results.

        ``results`` is a list of ``(ProbeResult, health-verdict)`` pairs.
        ``combined`` (optional, index-aligned with ``results``) carries the
        bidirectional/port enrichment; when omitted the abroad/port columns are
        stored as NULL ("not checked"). Returns the new scan's id.
        """
        self.initialize()
        counts = {GOOD: 0, MEDIUM: 0, BAD: 0}
        for _probe, verdict in results:
            if verdict in counts:
                counts[verdict] += 1

        combined = combined or []
        combined_by_host = {c.host: c for c in combined}

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
                "(scan_id, host, verdict, avg_ms, loss_pct, sent, received, "
                "abroad_reachable, abroad_nodes_ok, abroad_nodes_total, "
                "combined_verdict, open_ports, abroad_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    self._result_row_values(scan_id, probe, verdict, combined_by_host)
                    for probe, verdict in results
                ],
            )
        return scan_id

    @staticmethod
    def _result_row_values(
        scan_id: int,
        probe: ProbeResult,
        verdict: str,
        combined_by_host: dict[str, CombinedResult],
    ) -> tuple:
        c = combined_by_host.get(probe.host)
        if c is None:
            return (
                scan_id, probe.host, verdict, probe.avg_ms, probe.loss_pct,
                probe.sent, probe.received, None, None, None, None, None, None,
            )
        abroad = (
            None if c.abroad_reachable is None else (1 if c.abroad_reachable else 0)
        )
        combined_verdict = classify_bidirectional(c.iran_reachable, c.abroad_reachable)
        return (
            scan_id,
            probe.host,
            verdict,
            probe.avg_ms,
            probe.loss_pct,
            probe.sent,
            probe.received,
            abroad,
            c.abroad_nodes_ok,
            c.abroad_nodes_total,
            combined_verdict,
            _encode_ports(c.open_ports),
            getattr(c, "abroad_status", None),
        )

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
                "SELECT host, verdict, avg_ms, loss_pct, sent, received, "
                "abroad_reachable, abroad_nodes_ok, abroad_nodes_total, "
                "combined_verdict, open_ports, abroad_status "
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
                abroad_reachable=(
                    None
                    if r["abroad_reachable"] is None
                    else bool(r["abroad_reachable"])
                ),
                abroad_nodes_ok=r["abroad_nodes_ok"],
                abroad_nodes_total=r["abroad_nodes_total"],
                combined_verdict=r["combined_verdict"],
                open_ports=_decode_ports(r["open_ports"]),
                abroad_status=r["abroad_status"],
            )
            for r in rows
        ]

    # ---- discovery ledger ("what's new since you last checked") ----------
    def record_discoveries(self, rows: Iterable[dict[str, object]]) -> list[str]:
        """Insert first-sightings, returning the prefixes actually recorded.

        ``prefix`` is UNIQUE and inserts use ``OR IGNORE``, so a range already in
        the ledger keeps its original ``first_seen`` instead of being bumped
        forward on every sweep — otherwise every stored range would look new
        again after each cycle.
        """
        payload = []
        for row in rows:
            prefix = str(row.get("prefix") or "").strip()
            if not prefix:
                continue
            payload.append(
                (
                    prefix,
                    _text_or_none(row.get("category")),
                    _text_or_none(row.get("asn")),
                    _text_or_none(row.get("org")),
                    _text_or_none(row.get("country")),
                    str(row.get("first_seen") or _utc_now_iso()),
                )
            )
        if not payload:
            return []
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
            recorded = []
            for values in payload:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO discoveries "
                    "(prefix, category, asn, org, country, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    values,
                )
                if cur.rowcount:
                    recorded.append(values[0])
        return recorded

    def discoveries_after(
        self, after_id: int = 0, *, limit: int = 500
    ) -> list[DiscoveryRow]:
        """Ledger rows with an id greater than ``after_id``, oldest first.

        The watermark is the row id rather than a timestamp because
        ``first_seen`` has second resolution: a sweep landing in the same second
        as an acknowledgement would be permanently skipped by a time comparison.
        Ids are monotonic, so nothing can fall through.

        Oldest-first ordering makes ``limit`` a contiguous window starting at the
        watermark, so acknowledging what was shown never jumps over unread rows.
        """
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
            rows = conn.execute(
                "SELECT * FROM discoveries WHERE id > ? ORDER BY id ASC LIMIT ?",
                (int(after_id), int(limit)),
            ).fetchall()
        return [
            DiscoveryRow(
                id=r["id"],
                prefix=r["prefix"],
                category=r["category"],
                asn=r["asn"],
                org=r["org"],
                country=r["country"],
                first_seen=r["first_seen"],
            )
            for r in rows
        ]

    def last_visit(self, surface: str) -> tuple[str, int] | None:
        """``(stamp, last_id)`` for ``surface``, or None if it never looked."""
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT last_visited, last_id FROM surface_visits WHERE surface = ?",
                (str(surface),),
            ).fetchone()
        return (row["last_visited"], row["last_id"]) if row else None

    def mark_visited(
        self, surface: str, *, up_to_id: int | None = None, when: str | None = None
    ) -> str:
        """Record that ``surface`` has seen the ledger up to ``up_to_id``.

        Each surface has its own row, so the menu reading its notice does not
        clear the dashboard's and vice versa. ``up_to_id`` defaults to the whole
        ledger; callers pass the highest row they actually displayed so a
        discovery arriving mid-read is not marked as seen.
        """
        stamp = when or _utc_now_iso()
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
            if up_to_id is None:
                row = conn.execute("SELECT MAX(id) AS m FROM discoveries").fetchone()
                up_to_id = row["m"] or 0
            conn.execute(
                "INSERT INTO surface_visits (surface, last_visited, last_id) "
                "VALUES (?, ?, ?) ON CONFLICT(surface) DO UPDATE SET "
                "last_visited = excluded.last_visited, last_id = excluded.last_id",
                (str(surface), stamp, int(up_to_id)),
            )
        return stamp

    def clear(self) -> None:
        """Delete all saved scans and results."""
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
            conn.execute("DELETE FROM results")
            conn.execute("DELETE FROM scans")
