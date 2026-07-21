"""Manage IP ranges action group (menu option 3): list / add / remove."""

from __future__ import annotations

from .. import ranges as ranges_mod
from .context import ActionContext


def manage_ranges(ctx: ActionContext) -> None:
    """The Manage IP ranges submenu loop."""
    while True:
        ctx.print_(
            "\nManage IP ranges\n"
            "  1) List ranges by category\n"
            "  2) List Iranian ranges (all)\n"
            "  3) List foreign ranges (all)\n"
            "  4) Add a custom range\n"
            "  5) Remove a custom range\n"
            "  0) Back"
        )
        choice = ctx.prompt("Select: ")
        if choice == "1":
            list_categories(ctx)
        elif choice == "2":
            list_ranges(ctx, "iran")
        elif choice == "3":
            list_ranges(ctx, "foreign")
        elif choice == "4":
            add_range(ctx)
        elif choice == "5":
            remove_range(ctx)
        elif choice in ("0", ""):
            return
        else:
            ctx.print_("Unknown option.")


def list_categories(ctx: ActionContext) -> None:
    """Show saved CIDRs grouped by the four categories, with origin tags."""
    for category in ranges_mod.CATEGORIES:
        entries = ranges_mod.category_entries(category)
        ctx.print_(f"\n{category} ({len(entries)}):")
        if not entries:
            ctx.print_("  (none)")
            continue
        for e in entries:
            origin_tag = "[discovered]" if e.origin == "discovered" else "[custom]"
            meta = f" {e.country or '?'} / {e.provider or '?'}"
            ctx.print_(f"  {e.cidr:<20} {origin_tag}{meta}")


def list_ranges(ctx: ActionContext, scope: str) -> None:
    all_ranges = ranges_mod.load_ranges(scope)
    custom = set(ranges_mod.custom_ranges(scope))
    # A scope also aggregates its two categories.
    for category in ranges_mod._SCOPE_CATEGORIES[scope]:
        custom |= set(ranges_mod.custom_ranges(category))
    ctx.print_(f"\n{scope.title()} ranges ({len(all_ranges)} total):")
    for cidr in all_ranges:
        tag = " [saved]" if cidr in custom else ""
        ctx.print_(f"  {cidr}{tag}")


def _group_prompt(ctx: ActionContext) -> str | None:
    """Prompt for a scope or category name for add/remove."""
    groups = (*ranges_mod.SCOPES, *ranges_mod.CATEGORIES)
    ctx.print_("\nGroups: " + ", ".join(groups))
    group = ctx.prompt("Group: ").strip().lower()
    if group not in groups:
        ctx.print_(f"Group must be one of: {', '.join(groups)}.")
        return None
    return group


def add_range(ctx: ActionContext) -> None:
    group = _group_prompt(ctx)
    if group is None:
        return
    cidr = ctx.prompt("CIDR to add (e.g. 185.51.200.0/22): ")
    try:
        normalized = ranges_mod.add_custom_range(group, cidr)
    except ValueError as exc:
        ctx.print_(f"Could not add range: {exc}")
        return
    ctx.print_(f"Added {normalized} to {group} ranges.")


def remove_range(ctx: ActionContext) -> None:
    group = _group_prompt(ctx)
    if group is None:
        return
    custom = ranges_mod.custom_ranges(group)
    if not custom:
        ctx.print_(f"No custom/discovered {group} ranges to remove.")
        return
    ctx.print_(f"Saved {group} ranges:")
    for cidr in custom:
        ctx.print_(f"  {cidr}")
    cidr = ctx.prompt("CIDR to remove: ")
    if ranges_mod.remove_custom_range(group, cidr):
        ctx.print_(f"Removed {cidr}.")
    else:
        ctx.print_("That range was not found among saved ranges.")
