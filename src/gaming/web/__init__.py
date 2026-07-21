"""Local web dashboard for gaming (the ``gaming web`` subcommand).

Stdlib-only (``http.server``, ``ssl``, ``secrets``, ``hashlib``, ``hmac``,
``json``, ``threading``, ``importlib.resources``) — no third-party runtime
dependency. The web layer is a thin front end: every piece of business logic is
delegated to the existing ``pipeline`` / ``discovery`` / ``reachability`` /
``interactive`` / ``reporting`` modules.
"""

from __future__ import annotations
