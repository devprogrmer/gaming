"""Settings editor action (menu option 5).

These functions mutate ``ctx.settings`` in place (and reassign it on Reset), so
the caller should read ``ctx.settings`` back after invoking :func:`edit_settings`
to pick up any change.
"""

from __future__ import annotations

from ..settings import Settings, save_settings
from .context import ActionContext

# (attribute, human label) pairs shown in the settings editor, in order.
_FIELDS: list[tuple[str, str]] = [
    ("good_latency_ms", "GOOD latency threshold (ms)"),
    ("good_loss_pct", "GOOD loss threshold (%)"),
    ("medium_latency_ms", "MEDIUM latency threshold (ms)"),
    ("medium_loss_pct", "MEDIUM loss threshold (%)"),
    ("ping_count", "Probes per host"),
    ("concurrency", "Concurrency"),
    ("timeout", "Per-probe timeout (s)"),
    ("sample_per_range", "Hosts sampled per range"),
    ("max_hosts", "Max hosts per scan"),
    ("check_global", "Abroad reachability check (on/off)"),
    ("max_global_targets", "Max abroad checks per scan"),
    ("global_min_ok_fraction", "Abroad OK fraction required (0-1)"),
    ("abroad_provider", "Abroad provider (check-host/ripe-atlas/both)"),
    ("export_international_only", "Export whitelist (INTERNATIONAL) only"),
    ("scan_ports", "Common-ports TCP scan (on/off)"),
    ("ports", "Ports to scan (comma-separated)"),
    ("alert_on_change", "Alert on verdict change (scheduled) (on/off)"),
    ("alert_webhook_url", "Alert webhook URL (blank = log only)"),
]


def edit_settings(ctx: ActionContext) -> None:
    """Interactive settings editor loop. Mutates ``ctx.settings``."""
    while True:
        ctx.print_("\nSettings (blank keeps current value):")
        for i, (attr, label) in enumerate(_FIELDS, start=1):
            ctx.print_(f"  {i}) {label}: {getattr(ctx.settings, attr)}")
        ctx.print_("  s) Save    r) Reset to defaults    0) Back")
        choice = ctx.prompt("Select: ").lower()
        if choice in ("0", ""):
            return
        if choice == "s":
            save_settings(ctx.settings)
            ctx.print_("Settings saved.")
            continue
        if choice == "r":
            ctx.settings = Settings()
            ctx.print_("Settings reset to defaults (not yet saved).")
            continue
        if not choice.isdigit() or not (1 <= int(choice) <= len(_FIELDS)):
            ctx.print_("Unknown option.")
            continue
        attr, label = _FIELDS[int(choice) - 1]
        _edit_setting(ctx, attr, label)


def _edit_setting(ctx: ActionContext, attr: str, label: str) -> None:
    current = getattr(ctx.settings, attr)
    # Booleans get a simple yes/no toggle (bool is an int subclass, so it must
    # be checked before the int branch).
    if isinstance(current, bool):
        raw = ctx.prompt(f"{label} [{'on' if current else 'off'}] (on/off): ")
        raw = raw.strip().lower()
        if not raw:
            return
        if raw in ("on", "yes", "y", "true", "1"):
            value: object = True
        elif raw in ("off", "no", "n", "false", "0"):
            value = False
        else:
            ctx.print_("Please enter on or off.")
            return
        setattr(ctx.settings, attr, value)
        ctx.settings = ctx.settings.clamped()
        ctx.print_(f"Set {label} to {getattr(ctx.settings, attr)}.")
        return
    # Free-text (string) settings, e.g. the comma-separated ports list.
    if isinstance(current, str):
        raw = ctx.prompt(f"{label} [{current}]: ").strip()
        if not raw:
            return
        setattr(ctx.settings, attr, raw)
        ctx.settings = ctx.settings.clamped()
        ctx.print_(f"Set {label} to {getattr(ctx.settings, attr)}.")
        return
    raw = ctx.prompt(f"{label} [{current}]: ").strip()
    if not raw:
        return
    try:
        value = int(raw) if isinstance(current, int) else float(raw)
    except ValueError:
        ctx.print_("Please enter a number.")
        return
    setattr(ctx.settings, attr, value)
    ctx.settings = ctx.settings.clamped()
    ctx.print_(f"Set {label} to {getattr(ctx.settings, attr)}.")
