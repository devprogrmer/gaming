"""In-memory background job tracking for the web dashboard.

Long-running work (discovery searches, reachability scans) must not block the
HTTP server, so handlers start a :class:`Job` and return its id immediately; the
client polls ``GET /api/jobs/<id>``. This is deliberately minimal — a
lock-guarded dict of ``job id -> Job`` and one thread per job — since the
dashboard is single-user and local. No external queue or dependency.

Fail-soft: a worker that raises marks its job ``error`` with the message; it
never takes down the server or other jobs.

Job threads are cooperative-cancellable: each :class:`Job` carries a
``cancel_event`` that long-running workers poll via :meth:`Job.cancelled`, so
shutdown can ask in-flight work to stop at a safe point instead of having the
interpreter kill it mid-write (see :mod:`gaming.web.lifecycle`).
"""

from __future__ import annotations

import threading
import time
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
_CANCELLED = "cancelled"


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    status: str = _PENDING
    progress: float = 0.0  # 0..1, best-effort
    result: Any = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def cancelled(self) -> bool:
        """True once shutdown (or an explicit cancel) has asked this job to stop.

        Long-running workers should poll this between units of work and return
        early — that is what turns an abrupt kill into a clean stop.
        """
        return self.cancel_event.is_set()

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
        self._threads: dict[str, threading.Thread] = {}
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
                if job.cancelled() and job.status == _RUNNING:
                    # Worker returned early because it was asked to stop.
                    job.status = _CANCELLED
                    job.result = result
                else:
                    job.result = result
                    job.status = _DONE
                    job.progress = 1.0
            except Exception as exc:  # noqa: BLE001 - a bad job must not crash server
                job.status = _ERROR
                job.error = f"{type(exc).__name__}: {exc}"
                log.warning("job %s (%s) failed: %s", job.id, kind, job.error)

        thread = threading.Thread(
            target=_run, name=f"job-{kind}-{job.id[:8]}", daemon=True
        )
        with self._lock:
            self._threads[job.id] = thread
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    # ---- shutdown coordination ------------------------------------------
    def active(self) -> list[Job]:
        """Jobs that are still pending or running (snapshot)."""
        with self._lock:
            jobs = list(self._jobs.values())
        return [j for j in jobs if j.status in (_PENDING, _RUNNING)]

    def cancel_all(self) -> int:
        """Signal every in-flight job to stop. Returns how many were signalled."""
        active = self.active()
        for job in active:
            job.cancel_event.set()
        return len(active)

    def drain(self, timeout: float = 5.0) -> list[Job]:
        """Cancel in-flight jobs and wait (bounded) for their threads to finish.

        Returns the jobs that were *still* running when ``timeout`` expired, so
        the caller can report them rather than pretending shutdown was clean.
        Threads are daemonic, so a stuck worker can never block process exit —
        the bound only decides how long we give it to stop politely.
        """
        self.cancel_all()
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            threads = dict(self._threads)
        for thread in threads.values():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if thread is threading.current_thread():
                continue
            thread.join(timeout=remaining)
        return self.active()

    def _evict_locked(self) -> None:
        # Bound memory: drop the oldest finished jobs beyond the cap.
        while len(self._order) > self._max_jobs:
            oldest = self._order.pop(0)
            self._jobs.pop(oldest, None)
            self._threads.pop(oldest, None)
