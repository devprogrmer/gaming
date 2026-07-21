"""In-process recurring scan scheduler (Part C).

A dependency-free background scheduler that re-runs a saved scope scan on a
fixed interval and appends each run to :class:`~gaming.interactive.storage.HistoryStore`,
so the dashboard trend chart keeps filling without the user manually re-running
scans. After each run it invokes the verdict-change alerting in
:mod:`.alerts`.

Built on a single ``threading.Thread`` + an ``Event``-gated sleep loop (stdlib
only), so it stops promptly and never blocks process shutdown. Each scan is
wrapped so one failed run is logged and the schedule simply continues.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from ..logging_setup import get_logger
from . import alerts, scanner
from .settings import Settings, load_settings
from .storage import HistoryStore

log = get_logger("gaming.interactive.scheduler")

# Floor so a mistyped tiny interval can't spin the CPU / hammer check-host.net.
_MIN_INTERVAL_SECONDS = 30.0


@dataclass(slots=True)
class ScheduleState:
    """Live counters for a running schedule (for status display)."""

    scope: str
    interval: float
    runs: int = 0
    last_scan_id: int | None = None
    last_error: str | None = None
    last_changes: int = 0


class ScanScheduler:
    """Re-run a scope scan on an interval in a background thread.

    ``settings_provider`` is called before each run so live Settings edits (from
    the menu or web dashboard) take effect on the next scan without a restart;
    it defaults to :func:`load_settings`.
    """

    def __init__(
        self,
        scope: str,
        interval_seconds: float,
        *,
        store: HistoryStore | None = None,
        settings_provider: Callable[[], Settings] | None = None,
        run_immediately: bool = True,
    ) -> None:
        self.scope = scope
        self.interval = max(_MIN_INTERVAL_SECONDS, float(interval_seconds))
        self._store = store or HistoryStore()
        self._settings_provider = settings_provider or load_settings
        self._run_immediately = run_immediately
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = ScheduleState(scope=scope, interval=self.interval)

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"scan-scheduler-{self.scope}", daemon=True
        )
        self._thread.start()
        log.info(
            "scan scheduler started: scope=%s every %.0fs", self.scope, self.interval
        )

    def stop(self, *, timeout: float | None = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        log.info("scan scheduler stopped: scope=%s", self.scope)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- loop ------------------------------------------------------------
    def _loop(self) -> None:
        if self._run_immediately:
            self.run_once()
        while not self._stop.wait(self.interval):
            self.run_once()

    def run_once(self) -> ScheduleState:
        """Run one scan iteration. Fail-soft: errors are recorded, not raised."""
        try:
            settings = self._settings_provider()
            report = scanner.run_scan(self.scope, settings)
            scan_id = scanner.persist(report, self._store)
            self.state.last_scan_id = scan_id
            self.state.last_error = None
            self.state.runs += 1
            try:
                changes = alerts.process_scan_alerts(
                    self._store, self.scope, settings, scan_id=scan_id
                )
                self.state.last_changes = len(changes)
            except Exception as exc:  # noqa: BLE001 - alerting must never break the loop
                log.warning(
                    "post-scan alerting failed: %s: %s", type(exc).__name__, exc
                )
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the schedule
            self.state.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("scheduled scan failed (scope=%s): %s", self.scope, exc)
        return self.state
