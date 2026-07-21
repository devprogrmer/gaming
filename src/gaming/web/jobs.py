"""In-memory background job tracking for the web dashboard.

Long-running work (discovery searches, reachability scans) must not block the
HTTP server, so handlers start a :class:`Job` and return its id immediately; the
client polls ``GET /api/jobs/<id>``. This is deliberately minimal — a
lock-guarded dict of ``job id -> Job`` and one thread per job — since the
dashboard is single-user and local. No external queue or dependency.

Fail-soft: a worker that raises marks its job ``error`` with the message; it
never takes down the server or other jobs.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..logging_setup import get_logger

log = get_logger("gaming.web.jobs")

_PENDING = "pending"
_RUNNING = "running"
_DONE = "done"
_ERROR = "error"


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    status: str = _PENDING
    progress: float = 0.0  # 0..1, best-effort
    result: Any = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 3),
            "result": self.result,
            "error": self.error,
            "meta": self.meta,
        }


class JobManager:
    """Start background jobs and track their status/results thread-safely."""

    def __init__(self, *, max_jobs: int = 200) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._max_jobs = max_jobs

    def start(
        self,
        kind: str,
        target: Callable[[Job], Any],
        *,
        meta: dict[str, Any] | None = None,
    ) -> Job:
        """Create a job and run ``target(job)`` on a daemon thread.

        ``target`` receives the :class:`Job` so it can set ``job.progress`` and
        return a JSON-serialisable result (assigned to ``job.result``). Any
        exception is captured onto the job as an error.
        """
        job = Job(id=uuid.uuid4().hex, kind=kind, meta=dict(meta or {}))
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._evict_locked()

        def _run() -> None:
            job.status = _RUNNING
            try:
                result = target(job)
                job.result = result
                job.status = _DONE
                job.progress = 1.0
            except Exception as exc:  # noqa: BLE001 - a bad job must not crash server
                job.status = _ERROR
                job.error = f"{type(exc).__name__}: {exc}"
                log.warning("job %s (%s) failed: %s", job.id, kind, job.error)

        threading.Thread(
            target=_run, name=f"job-{kind}-{job.id[:8]}", daemon=True
        ).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _evict_locked(self) -> None:
        # Bound memory: drop the oldest finished jobs beyond the cap.
        while len(self._order) > self._max_jobs:
            oldest = self._order.pop(0)
            self._jobs.pop(oldest, None)
