"""In-place update action (menu option 6)."""

from __future__ import annotations

from ... import __version__
from .context import ActionContext


def update_installed_version(ctx: ActionContext) -> None:
    """Upgrade or switch the installation in place from the interactive menu.

    Reuses the same :func:`gaming.updater.run_update` flow as the ``gaming
    update`` CLI subcommand, so user state (scan history, settings, custom
    ranges) — which lives outside the install tree — is preserved. When an
    in-place update isn't possible, the updater's error message carries the
    safest manual fallback.
    """
    from ...updater import UpdateError, list_releases, run_update

    ctx.print_(f"\nCurrent installed version: {__version__}")

    try:
        releases = list_releases()
    except UpdateError as exc:
        ctx.print_(f"Update failed: {exc}")
        return
    if releases:
        ctx.print_("Available releases (newest first): " + ", ".join(releases))

    target = ctx.prompt(
        "\nEnter a release to switch to (e.g. v0.1.0), or blank to update to the latest: "
    ).strip()
    ref = target or None

    try:
        result = run_update(ref=ref, log=ctx.print_)
    except UpdateError as exc:
        ctx.print_(f"\nUpdate failed: {exc}")
        return

    if result.ref is not None:
        ctx.print_(
            f"\nSwitched gaming {result.previous_version} -> "
            f"{result.new_version} (release {result.ref})."
        )
    elif result.changed:
        ctx.print_(f"\nUpdated gaming {result.previous_version} -> {result.new_version}.")
    else:
        ctx.print_(f"\ngaming is already up to date (version {result.new_version}).")
    ctx.print_("Restart gaming for the new version to take effect.")
