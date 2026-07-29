"""What the 24/7 watcher found since a surface last looked.

The watch loop records every genuinely-new range in the ``discoveries`` ledger
(see :mod:`gaming.interactive.storage`). This module is the single reader of that
ledger: the menu banner, ``gaming watch --whats-new``, and the dashboard's
Overview panel all call :func:`whats_new` so they cannot disagree about what
counts as new.

"Last visited" is tracked **per surface**. Reading the notice in the terminal
must not clear it in the browser — someone who runs the watcher on a server and
checks both would otherwise silently lose one of the two reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .storage import DiscoveryRow, HistoryStore

#: The surfaces that track their own last-visited stamp.
MENU = "menu"
WEB = "web"


@dataclass(slots=True)
class WhatsNew:
    """New ranges for one surface, plus the stamps needed to describe them."""

    surface: str
    rows: list[DiscoveryRow] = field(default_factory=list)
    since: str | None = None
    #: True when this surface has never looked, so "new" means the whole ledger.
    first_visit: bool = False
    #: Highest ledger id included here. :func:`acknowledge` advances the surface's
    #: watermark to exactly this, so a discovery arriving mid-read stays unread.
    up_to_id: int = 0

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def has_new(self) -> bool:
        return bool(self.rows)

    def summary(self) -> str:
        """One plain sentence. Says so explicitly when there is nothing new."""
        if not self.rows:
            if self.first_visit:
                return "No ranges discovered yet. Start the watcher to collect them."
            return f"Nothing new since you last checked here ({self.since})."
        noun = "range" if self.count == 1 else "ranges"
        if self.first_visit:
            return f"{self.count} discovered {noun} on record."
        return f"{self.count} new {noun} discovered since {self.since}."

    def as_dict(self) -> dict[str, object]:
        return {
            "surface": self.surface,
            "count": self.count,
            "has_new": self.has_new,
            "since": self.since,
            "first_visit": self.first_visit,
            "up_to_id": self.up_to_id,
            "summary": self.summary(),
            "rows": [row.as_dict() for row in self.rows],
        }


def whats_new(
    store: HistoryStore, surface: str, *, limit: int = 500
) -> WhatsNew:
    """Ledger entries ``surface`` has not seen. Does not mark them as seen.

    Reading is separated from acknowledging so a surface can show the notice and
    only clear it once the user has actually looked at the detail.
    """
    visit = store.last_visit(surface)
    since, after_id = visit if visit else (None, 0)
    rows = store.discoveries_after(after_id, limit=limit)
    return WhatsNew(
        surface=surface,
        rows=rows,
        since=since,
        first_visit=visit is None,
        up_to_id=rows[-1].id if rows else after_id,
    )


def acknowledge(
    store: HistoryStore, surface: str, *, up_to_id: int | None = None
) -> str:
    """Mark ``surface`` as having seen the ledger up to ``up_to_id``.

    Pass the ``up_to_id`` of the :class:`WhatsNew` that was actually displayed;
    the default of the whole ledger would also swallow anything the watcher
    recorded while the user was reading.
    """
    return store.mark_visited(surface, up_to_id=up_to_id)


def render(result: WhatsNew, stream: object = None) -> str:
    """Render a :class:`WhatsNew` for a terminal, shared by the menu and CLI."""
    from . import theme

    out = [theme.heading("What's new since your last visit", stream), ""]
    if not result.has_new:
        out.append(theme.style(result.summary(), "muted", stream))
        out.append("")
        return "\n".join(out)

    out.append(result.summary())
    out.append("")
    columns = [
        theme.Column("CIDR"),
        theme.Column("CATEGORY"),
        theme.Column("ASN"),
        theme.Column("COUNTRY"),
        theme.Column("ORGANIZATION"),
        theme.Column("FIRST SEEN"),
    ]
    rows = [
        [
            row.prefix,
            row.category or "-",
            row.asn or "-",
            row.country or "-",
            row.org or "-",
            row.first_seen,
        ]
        for row in result.rows
    ]
    out.append(theme.render_table(columns, rows, stream=stream))
    return "\n".join(out)
