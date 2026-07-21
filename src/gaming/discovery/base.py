"""Base classes shared by all discovery sources."""

from __future__ import annotations

import abc
from dataclasses import dataclass

from ..logging_setup import get_logger
from ..models import Filters, IPRecord


@dataclass(slots=True)
class DiscoveryContext:
    """Runtime context handed to every source."""

    filters: Filters
    timeout: float = 5.0
    offline: bool = False
    # When True, per-request failures inside a source are surfaced at WARNING
    # with the real exception type + message (not just a terse DEBUG line), so
    # a live run can show *why* discovery fell back to sample data.
    verbose_errors: bool = False


class Source(abc.ABC):
    """Common interface for a discovery source.

    Subclasses must define :attr:`name` and implement :meth:`_discover_online`
    and :meth:`_sample_data`. The public :meth:`discover` wraps them with
    error handling and offline fallback so a single failing source never
    aborts the pipeline.
    """

    name: str = "base"

    def __init__(self, context: DiscoveryContext) -> None:
        self.context = context
        self.log = get_logger(f"gaming.discovery.{self.name}")

    # ---- error reporting -------------------------------------------------
    def _report_request_error(self, what: str, exc: BaseException) -> None:
        """Log a single failed per-request lookup, with its real cause.

        Normally this is a terse DEBUG line so a healthy run stays quiet. When
        ``context.verbose_errors`` is set (the interactive "discover & save"
        pass, or ``--log-level DEBUG``), it is raised to WARNING and carries the
        exception *type* and message so real failures — DNS, refused, timeout,
        TLS, HTTP status, parse — are visible instead of being silently masked
        as a generic sample-data fallback.
        """
        msg = "%s failed: %s: %s"
        args = (what, type(exc).__name__, exc)
        if self.context.verbose_errors:
            self.log.warning(msg, *args)
        else:
            self.log.debug(msg, *args)

    # ---- public API ------------------------------------------------------
    def discover(self) -> list[IPRecord]:
        if self.context.offline:
            self.log.debug("offline mode: using bundled sample data")
            return list(self._sample_data())
        try:
            records = self._discover_online()
            if not records:
                self.log.debug("no online results; falling back to sample data")
                return list(self._sample_data())
            return records
        except Exception as exc:  # noqa: BLE001 - sources must never crash pipeline
            self.log.warning("source failed (%s); using sample data: %s", self.name, exc)
            return list(self._sample_data())

    # ---- to be implemented by subclasses ---------------------------------
    @abc.abstractmethod
    def _discover_online(self) -> list[IPRecord]:
        """Perform the real network lookup. May raise; errors are handled."""

    @abc.abstractmethod
    def _sample_data(self) -> list[IPRecord]:
        """Return representative offline sample records for this source."""
