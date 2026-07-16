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
