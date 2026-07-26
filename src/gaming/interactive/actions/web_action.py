"""Launch the web dashboard from the interactive menu (menu option 9)."""

from __future__ import annotations

from .context import ActionContext


def launch_web_panel(ctx: ActionContext) -> None:
    """Start the same web dashboard as ``gaming web``, in-process.

    Reuses :func:`gaming.web.server.serve` directly (no subprocess), so the
    startup banner (credentials, bound URL, ``--bind 127.0.0.1`` warning) and
    the shutdown sequence are literally the same code as the CLI subcommand —
    :class:`gaming.web.lifecycle.ShutdownCoordinator` handles Ctrl+C here too,
    restoring the previous signal handlers afterwards so the menu keeps
    responding to Ctrl+C normally once the panel stops. ``serve`` blocks until
    stopped and never re-raises ``KeyboardInterrupt``, so control returns to
    the menu loop cleanly.
    """
    # Imported lazily so normal menu paths don't pull in the http server stack.
    from ...web.server import serve

    ctx.print_("\nLaunch web panel")
    bind = ctx.prompt("Bind address [0.0.0.0]: ").strip() or "0.0.0.0"
    port_raw = ctx.prompt("Port [auto]: ").strip()
    port = int(port_raw) if port_raw.isdigit() else None
    tls_raw = ctx.prompt("Serve over HTTPS with a self-signed cert? [y/N]: ").strip()
    use_tls = tls_raw.lower() in ("y", "yes")

    serve(bind=bind, port=port, use_tls=use_tls, print_fn=ctx.print_)
