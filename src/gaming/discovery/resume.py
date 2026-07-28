"""Resume journal for long-running exhaustive country sweeps.

A full-country sweep resolves the ASN and organization of every delegated
prefix, which can mean thousands of network round-trips and many minutes of
wall-clock. If it is interrupted — Ctrl+C, SSH drop, reboot, rate-limit
give-up — restarting from zero would waste all of that work and re-hammer the
upstream APIs.

This module keeps a small JSON journal recording which prefixes have already
been resolved, plus the resolved record itself, so a re-run replays finished
work from disk and only queries what is still missing.

The journal is keyed by ``(country, dataset)`` where ``dataset`` identifies the
delegated-stats snapshot the sweep started from. When the upstream file is
republished the key changes and the sweep starts fresh, so a stale journal can
never silently pin results to yesterday's allocation table.

Writes are atomic (temp file + :func:`os.replace`) so an interrupt mid-write
leaves the previous good journal intact rather than a truncated one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..interactive import paths
from ..logging_setup import get_logger

log = get_logger("gaming.discovery.resume")

#: Bumped if the on-disk journal layout ever changes incompatibly.
_JOURNAL_VERSION = 1


@dataclass(slots=True)
class ResumeJournal:
    """Durable per-prefix progress for one exhaustive sweep.

    ``entries`` maps a prefix to the resolved record payload (asn/organization/
    country). A prefix present in ``entries`` is considered done and is not
    re-queried on a later run.
    """

    country: str
    dataset: str = ""
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None
    #: Number of completed prefixes not yet flushed to disk.
    _pending: int = 0

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def load(
        cls,
        country: str,
        *,
        dataset: str = "",
        path: Path | None = None,
    ) -> ResumeJournal:
        """Load the journal for ``country``, or return an empty one.

        Fail-soft by design: a missing, unreadable, corrupt, or stale-dataset
        journal yields a fresh journal rather than raising, because losing
        resume progress must never block a sweep from running.
        """
        country = country.upper()
        target = path or paths.exhaustive_journal_path(country)
        journal = cls(country=country, dataset=dataset, path=target)
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return journal
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("resume journal unreadable (%s); starting fresh: %s", target, exc)
            return journal

        if not isinstance(raw, dict) or raw.get("version") != _JOURNAL_VERSION:
            log.debug("resume journal version mismatch; starting fresh")
            return journal
        if str(raw.get("country", "")).upper() != country:
            log.debug("resume journal country mismatch; starting fresh")
            return journal

        stored_dataset = str(raw.get("dataset") or "")
        if dataset and stored_dataset and stored_dataset != dataset:
            log.info(
                "delegated dataset changed (%s -> %s); starting exhaustive sweep fresh",
                stored_dataset,
                dataset,
            )
            return journal

        entries = raw.get("entries")
        if isinstance(entries, dict):
            journal.entries = {
                str(k): v for k, v in entries.items() if isinstance(v, dict)
            }
        journal.dataset = dataset or stored_dataset
        if journal.entries:
            log.info(
                "resuming exhaustive sweep for %s: %d prefix(es) already done",
                country,
                len(journal.entries),
            )
        return journal

    # ---- progress --------------------------------------------------------
    def is_done(self, prefix: str) -> bool:
        """True if ``prefix`` was already resolved by an earlier run."""
        return prefix in self.entries

    def record(self, prefix: str, payload: dict[str, Any]) -> None:
        """Mark ``prefix`` resolved. Call :meth:`flush` to persist."""
        self.entries[prefix] = payload
        self._pending += 1

    def resolved(self) -> list[dict[str, Any]]:
        """Every payload recorded so far, in insertion order."""
        return list(self.entries.values())

    def __len__(self) -> int:
        return len(self.entries)

    # ---- persistence -----------------------------------------------------
    def flush(self, *, force: bool = False, every: int = 25) -> None:
        """Write the journal to disk.

        Batched: only writes once ``every`` new prefixes have accumulated,
        so a sweep does not fsync on every single prefix. ``force=True``
        writes unconditionally (used at the end of a sweep and on interrupt).
        """
        if not force and self._pending < every:
            return
        if self.path is None:
            return
        payload = {
            "version": _JOURNAL_VERSION,
            "country": self.country,
            "dataset": self.dataset,
            "entries": self.entries,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.path)
            self._pending = 0
        except OSError as exc:
            log.warning("could not write resume journal %s: %s", self.path, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> None:
        """Delete the journal from disk — the sweep finished cleanly."""
        self.entries.clear()
        self._pending = 0
        if self.path is None:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("could not remove resume journal %s: %s", self.path, exc)
