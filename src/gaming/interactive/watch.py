"""Continuous 24/7 country watch: discover -> persist -> scan -> repeat.

Where :class:`~gaming.interactive.scheduler.ScanScheduler` re-scans a *fixed*
set of ranges, the watch loop also keeps that set current: each iteration runs
an exhaustive country sweep, folds any newly delegated prefixes into the stored
ranges, and only then runs the bidirectional scan. A range that appeared in RIR
data an hour ago is therefore scanned on the next tick without anyone touching
the tool.

Built on the same ``threading.Thread`` + ``Event``-gated sleep as the scheduler,
and on the same PID-file machinery as ``gaming web``, so ``gaming watch
--daemon`` survives an SSH disconnect.

Every stage is independently fail-soft: a rate-limited sweep still lets the scan
run against previously stored ranges, and a failed scan still leaves the newly
discovered ranges persisted. One bad iteration is logged and the loop continues
— an unattended watcher that dies on the first 429 is worthless.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..logging_setup import get_logger
from . import alerts, scanner
from . import ranges as ranges_mod
from .settings import Settings, load_settings
from .storage import HistoryStore

log = get_logger("gaming.interactive.watch")

#: Floor on the loop interval. An exhaustive sweep issues thousands of registry
#: requests; running it more often than this would be abusive to RIPEstat/RDAP
#: and pointless, since delegated statistics are republished daily.
MIN_INTERVAL_SECONDS = 300.0

#: Default cadence: hourly.
DEFAULT_INTERVAL_SECONDS = 3600.0


@dataclass(slots=True)
class WatchState:
    """Live counters for a running watch (for ``--status`` and the dashboard)."""

    country: str
    interval: float
    scope: str = "iran"
    iterations: int = 0
    #: Prefixes returned by the most recent sweep (including resumed ones).
    last_discovered: int = 0
    #: ``{category: newly_added}`` from the most recent persist step.
    last_persisted: dict[str, int] = field(default_factory=dict)
    #: The CIDRs the most recent persist step actually inserted. Distinct from
    #: ``last_discovered``, which counts everything the sweep returned including
    #: ranges already stored.
    last_new_prefixes: list[str] = field(default_factory=list)
    last_scan_id: int | None = None
    last_scanned: int = 0
    last_changes: int = 0
    #: Per-stage errors from the most recent iteration; empty when all was well.
    errors: list[str] = field(default_factory=list)
    last_run_at: float | None = None

    @property
    def healthy(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "country": self.country,
            "interval": self.interval,
            "scope": self.scope,
            "iterations": self.iterations,
            "last_discovered": self.last_discovered,
            "last_persisted": dict(self.last_persisted),
            "last_new_prefixes": list(self.last_new_prefixes),
            "last_scan_id": self.last_scan_id,
            "last_scanned": self.last_scanned,
            "last_changes": self.last_changes,
            "errors": list(self.errors),
            "last_run_at": self.last_run_at,
            "healthy": self.healthy,
        }


class WatchLoop:
    """Run discover -> persist -> scan on an interval, indefinitely.

    ``settings_provider`` is consulted at the start of every iteration so
    Settings edits made in the menu or dashboard take effect on the next tick
    without a restart.
    """

    def __init__(
        self,
        country: str = "IR",
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        *,
        scope: str = "iran",
        include_ipv6: bool = True,
        store: HistoryStore | None = None,
        settings_provider: Callable[[], Settings] | None = None,
        on_iteration: Callable[[WatchState], None] | None = None,
    ) -> None:
        self.country = (country or "IR").upper()
        self.interval = max(MIN_INTERVAL_SECONDS, float(interval_seconds))
        self.scope = scope
        self.include_ipv6 = include_ipv6
        self._store = store or HistoryStore()
        self._settings_provider = settings_provider or load_settings
        self._on_iteration = on_iteration
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = WatchState(
            country=self.country, interval=self.interval, scope=scope
        )

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run_forever, name=f"watch-{self.country}", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float | None = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        log.info("watch stopped: country=%s", self.country)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- loop ------------------------------------------------------------
    def run_forever(self, *, max_iterations: int = 0) -> WatchState:
        """Loop until stopped (or ``max_iterations`` runs, for tests).

        The sleep is ``Event.wait``-gated rather than ``time.sleep`` so a stop
        request during the idle hour takes effect immediately instead of after
        the remaining interval.
        """
        log.info(
            "watch started: country=%s scope=%s every %.0fs",
            self.country,
            self.scope,
            self.interval,
        )
        count = 0
        while not self._stop.is_set():
            self.run_once()
            count += 1
            if max_iterations and count >= max_iterations:
                break
            if self._stop.wait(self.interval):
                break
        return self.state

    def run_once(self) -> WatchState:
        """Run one full iteration. Never raises: each stage is caught."""
        state = self.state
        state.errors = []
        records: list = []

        try:
            settings = self._settings_provider()
        except Exception as exc:  # noqa: BLE001 - fall back to defaults
            self._note(state, "settings", exc)
            settings = Settings()

        records = self._discover(state)
        self._persist(state, records)
        self._scan(state, settings)

        state.iterations += 1
        state.last_run_at = time.time()
        if self._on_iteration is not None:
            try:
                self._on_iteration(state)
            except Exception as exc:  # noqa: BLE001 - observers must not break the loop
                log.debug("watch iteration callback failed: %s", exc)
        return state

    # ---- stages ----------------------------------------------------------
    def _discover(self, state: WatchState) -> list:
        """Exhaustive sweep for the watched country. Resumable across ticks."""
        from ..discovery.exhaustive import discover_country

        try:
            records = discover_country(
                self.country, include_ipv6=self.include_ipv6, resume=True
            )
        except Exception as exc:  # noqa: BLE001 - keep scanning known ranges
            self._note(state, "discover", exc)
            return []
        state.last_discovered = len(records)
        return records

    def _persist(self, state: WatchState, records: list) -> None:
        if not records:
            state.last_persisted = {}
            state.last_new_prefixes = []
            return
        try:
            inserted = ranges_mod.persist_exhaustive_prefixes(
                records, home_country=self.country
            )
        except Exception as exc:  # noqa: BLE001 - scan can still proceed
            self._note(state, "persist", exc)
            state.last_persisted = {}
            state.last_new_prefixes = []
            return

        state.last_persisted = {k: len(v) for k, v in inserted.items()}
        state.last_new_prefixes = [p for prefixes in inserted.values() for p in prefixes]
        self._record_ledger(state, records, inserted)

    def _record_ledger(
        self, state: WatchState, records: list, inserted: dict[str, list[str]]
    ) -> None:
        """Write the genuinely-new ranges to the durable "what's new" ledger.

        Only prefixes the persist step actually inserted are recorded, so a
        re-run of the same sweep adds nothing and the menu/dashboard notice does
        not resurrect ranges the user has already been told about. Failing here
        must not abort the iteration: the ranges are already stored and scannable
        either way.
        """
        by_prefix: dict[str, object] = {}
        for rec in records:
            raw = str(getattr(rec, "prefix", "") or "")
            if not raw:
                continue
            by_prefix[raw] = rec
            # Persisting normalizes the prefix, so the inserted string need not
            # equal the record's own; index both forms to keep the metadata.
            try:
                by_prefix.setdefault(
                    str(ipaddress.ip_network(raw, strict=False)), rec
                )
            except ValueError:
                pass
        rows: list[dict[str, object]] = []
        for category, prefixes in inserted.items():
            for prefix in prefixes:
                rec = by_prefix.get(prefix)
                rows.append(
                    {
                        "prefix": prefix,
                        "category": category,
                        "asn": getattr(rec, "asn", None),
                        "org": getattr(rec, "organization", None)
                        or getattr(rec, "provider", None),
                        "country": getattr(rec, "country", None),
                    }
                )
        if not rows:
            return
        try:
            self._store.record_discoveries(rows)
        except Exception as exc:  # noqa: BLE001 - the ledger is not load-bearing
            self._note(state, "ledger", exc)

    def _scan(self, state: WatchState, settings: Settings) -> None:
        """Bidirectional scan of the watched scope, appended to history."""
        try:
            report = scanner.run_scan(self.scope, settings)
            state.last_scanned = report.total
            state.last_scan_id = scanner.persist(report, self._store)
        except Exception as exc:  # noqa: BLE001 - next tick tries again
            self._note(state, "scan", exc)
            return

        try:
            changes = alerts.process_scan_alerts(
                self._store, self.scope, settings, scan_id=state.last_scan_id
            )
            state.last_changes = len(changes)
        except Exception as exc:  # noqa: BLE001 - alerting is not load-bearing
            self._note(state, "alerts", exc)

    @staticmethod
    def _note(state: WatchState, stage: str, exc: BaseException) -> None:
        message = f"{stage}: {type(exc).__name__}: {exc}"
        state.errors.append(message)
        log.warning("watch iteration stage failed (%s)", message)


def parse_interval(raw: object, *, default: float = DEFAULT_INTERVAL_SECONDS) -> float:
    """Parse a human interval (``30m``, ``1h``, ``2d``, ``900``) into seconds.

    Bare numbers are seconds. Unrecognised input falls back to ``default``
    rather than raising, so a typo in a config file cannot stop the watcher
    from starting.
    """
    text = str(raw or "").strip().lower()
    if not text:
        return default
    units = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    multiplier = 1.0
    if text[-1] in units:
        multiplier = units[text[-1]]
        text = text[:-1].strip()
    try:
        return float(text) * multiplier
    except ValueError:
        return default
