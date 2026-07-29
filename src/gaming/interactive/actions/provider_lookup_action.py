"""Look one named datacenter/provider up on demand (menu option 10).

Thin wrapper: the prompt and the optional save are the only things this adds.
The lookup itself and the rendering both come from
:mod:`gaming.discovery.provider_lookup`, the same function
``gaming discover --provider-name`` and ``POST /api/provider-lookup`` call, so
the three surfaces cannot disagree about what a name resolves to.
"""

from __future__ import annotations

from ...discovery import provider_lookup
from .context import ActionContext


def lookup_provider(ctx: ActionContext) -> None:
    name = ctx.prompt("Datacenter / provider name: ").strip()
    if not name:
        ctx.print_("No name given.")
        return

    ctx.print_(f"\nAsking the registries about {name!r}...")
    result = provider_lookup.lookup_provider_by_name(name, timeout=15.0)
    ctx.print_(provider_lookup.render_lookup(result, ctx.stdout))

    if not result.found:
        return
    answer = ctx.prompt("Save these ranges for later scans? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        return

    from .. import ranges as ranges_mod

    added = ranges_mod.persist_records(result.records)
    total = sum(added.values())
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(added.items())) or "none"
    ctx.print_(f"Saved {total} new range(s) ({detail}).")
