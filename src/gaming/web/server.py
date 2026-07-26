"""HTTP server adapter for the dashboard (``gaming web``).

Bridges the stdlib ``http.server`` onto the transport-agnostic
:class:`gaming.web.handlers.WebApp`. Responsibilities kept here (and out of the
testable handler layer):

* pick/validate a bind address + port (auto-select a free high port),
* detect and print the reachable dashboard URL(s) + one-time credentials,
* serve the SPA shell and bundled static assets,
* optional self-signed TLS via stdlib ``ssl`` (cert cached in the app-data dir).

No third-party dependency.
"""

from __future__ import annotations

import os
import secrets
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..interactive import paths
from ..logging_setup import get_logger
from . import assets, lifecycle
from .auth import CredentialStore
from .handlers import Request, Response, WebApp
from .lifecycle import ShutdownCoordinator

log = get_logger("gaming.web.server")

_PORT_RANGE = (20000, 65000)


def shutdown_active_server() -> bool:
    """Stop the dashboard running in this process, if any. True if one was.

    Re-exported from :mod:`gaming.web.lifecycle` so callers only need the
    server module. Used when ``serve()`` is running on a non-main thread (where
    signal handlers cannot be installed) — same cleanup, no signal required.
    """
    return lifecycle.shutdown_active()


def _pick_free_port(bind: str, attempts: int = 50) -> int:
    """Return a free port in the high range, trying random candidates."""
    lo, hi = _PORT_RANGE
    for _ in range(attempts):
        candidate = secrets.randbelow(hi - lo) + lo
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind if bind != "0.0.0.0" else "", candidate))
                return candidate
            except OSError:
                continue
    # Last resort: let the OS assign one.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind if bind != "0.0.0.0" else "", 0))
        return sock.getsockname()[1]


def _detect_server_ip() -> str:
    """Best-effort primary outbound IP of this host (no packets actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _ensure_self_signed_cert() -> tuple[Path, Path] | None:
    """Generate (once) and return a cached self-signed cert/key pair.

    Uses the ``openssl`` CLI when available (ubiquitous on the Linux servers
    this tool targets). Returns ``None`` if a cert can't be produced, so the
    caller falls back to plain HTTP with a warning rather than failing to start.
    """
    cert = paths.tls_cert_path()
    key = paths.tls_key_path()
    if cert.exists() and key.exists():
        return cert, key
    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(key), "-out", str(cert),
                "-days", "825", "-nodes",
                "-subj", "/CN=gaming-dashboard",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return cert, key
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not generate TLS certificate (%s); serving plain HTTP", exc)
        return None


def _make_handler(app: WebApp):
    class _Handler(BaseHTTPRequestHandler):
        server_version = "gaming-web"

        # Quieter logging — route through our logger at DEBUG.
        def log_message(self, fmt: str, *args) -> None:  # noqa: N802
            log.debug("%s - " + fmt, self.address_string(), *args)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(length) if length > 0 else b""

        def _to_request(self, method: str) -> Request:
            return Request(
                method=method,
                path=self.path,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=self._read_body() if method in ("POST", "PUT", "PATCH") else b"",
                client_ip=self.client_address[0] if self.client_address else "0.0.0.0",
            )

        def _dispatch(self, method: str) -> None:
            try:
                resp = self._serve(method)
            except Exception as exc:  # noqa: BLE001 - never leak a traceback to client
                log.warning("request handling error: %s", exc)
                resp = Response.json({"error": "internal error"}, status=500)
            self._send(resp)

        def _serve(self, method: str) -> Response:
            path = self.path.split("?", 1)[0]
            # Static + SPA shell are served directly, before auth (public assets).
            if method == "GET":
                if path == "/" or path == "/index.html":
                    return Response(
                        status=200,
                        body=assets.index_html(),
                        headers={"Content-Type": "text/html; charset=utf-8"},
                    )
                if path.startswith("/static/"):
                    name = path[len("/static/"):]
                    data = assets.load_asset(name)
                    if data is None:
                        return Response.json({"error": "not found"}, status=404)
                    return Response(
                        status=200,
                        body=data,
                        headers={"Content-Type": assets.content_type_for(name)},
                    )
                if path == "/login":
                    # The SPA renders the login view client-side.
                    return Response(
                        status=200,
                        body=assets.index_html(),
                        headers={"Content-Type": "text/html; charset=utf-8"},
                    )
            return app.handle(self._to_request(method))

        def _send(self, resp: Response) -> None:
            self.send_response(resp.status)
            body = resp.body or b""
            has_ct = any(k.lower() == "content-type" for k in resp.headers)
            if not has_ct:
                self.send_header("Content-Type", "application/json")
            for key, value in resp.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

    return _Handler


def serve(
    *,
    bind: str = "0.0.0.0",
    port: int | None = None,
    use_tls: bool = False,
    reset_credentials: bool = False,
    daemon: bool = False,
    scheduler: object | None = None,
    print_fn=print,
) -> int:
    """Start the dashboard and block serving requests. Returns an exit code.

    On first run (or ``reset_credentials``) the generated username/password are
    printed exactly once. ``print_fn`` is injectable for testing.

    When ``daemon`` is set, the process detaches from the controlling terminal
    after the credentials/URL banner is printed (so the user still sees the
    one-time password) and writes a PID file for ``--stop`` / ``--status``.

    Shutdown (Ctrl+C, ``SIGTERM`` from ``gaming web --stop``, or a direct call)
    is handled entirely by :class:`gaming.web.lifecycle.ShutdownCoordinator` —
    see that module for why a bare ``except KeyboardInterrupt`` was not enough.
    An optional ``scheduler`` is stopped as part of that same sequence.
    """
    creds_store = CredentialStore()
    if reset_credentials:
        creds, password = creds_store.reset_credentials()
    else:
        creds, password = creds_store.ensure_credentials()

    app = WebApp(credentials=creds_store)
    if port is None:
        port = _pick_free_port(bind)

    handler = _make_handler(app)
    try:
        httpd = ThreadingHTTPServer((bind, port), handler)
    except OSError as exc:
        print_fn(f"Could not bind {bind}:{port}: {exc}")
        return 1

    scheme = "http"
    if use_tls:
        pair = _ensure_self_signed_cert()
        if pair is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=str(pair[0]), keyfile=str(pair[1]))
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
            scheme = "https"

    server_ip = _detect_server_ip()
    _print_startup(print_fn, scheme, bind, port, server_ip, creds.username, password)

    if daemon:
        # Detach only AFTER the banner (with the one-time password) is printed,
        # so the user always sees the credentials before the process goes to the
        # background and its output is redirected to the log file.
        from . import daemon as daemon_mod

        try:
            daemon_mod.daemonize()
        except daemon_mod.DaemonError as exc:
            print_fn(f"Cannot daemonize: {exc}")
            httpd.server_close()
            return 1
        # We are now the detached grandchild; record our PID for --stop/--status.
        daemon_mod.write_pid(os.getpid())

    def _cleanup() -> None:
        if daemon:
            from . import daemon as daemon_mod

            daemon_mod.remove_pid()

    coordinator = ShutdownCoordinator(
        httpd=httpd,
        jobs=app.jobs,
        scheduler=scheduler,
        print_fn=print_fn,
        on_cleanup=_cleanup,
    )
    # Registered before the serve loop starts so a signal arriving during
    # startup is still handled by us rather than killing the process outright.
    coordinator.install_signal_handlers()
    lifecycle.set_active(coordinator)

    # ``serve_forever`` must be stopped via ``shutdown()`` called from a
    # *different* thread than the one running it (stdlib requirement — calling
    # it from the same thread deadlocks). So it runs on a background thread and
    # the coordinator waits for a stop request on this one.
    def _run() -> None:
        try:
            httpd.serve_forever(poll_interval=0.25)
        except Exception as exc:  # noqa: BLE001 - a dead loop must still unblock us
            log.warning("serve loop exited unexpectedly: %s", exc)
        finally:
            # However the loop ends -- shutdown() called, an unexpected error,
            # or a subclass returning on its own -- the waiter must be released,
            # otherwise serve() would block forever with nothing left serving.
            coordinator.request_stop()

    server_thread = threading.Thread(
        target=_run, name="gaming-web-serve", daemon=True
    )
    coordinator.server_thread = server_thread
    server_thread.start()

    coordinator.wait_for_shutdown()
    return 0


def _print_startup(
    print_fn, scheme: str, bind: str, port: int, server_ip: str,
    username: str, password: str | None,
) -> None:
    print_fn("")
    print_fn("=" * 60)
    print_fn("  gaming web dashboard")
    print_fn("=" * 60)
    host = server_ip if bind == "0.0.0.0" else bind
    print_fn(f"  URL:        {scheme}://{host}:{port}/")
    if bind == "0.0.0.0":
        print_fn(f"  Local:      {scheme}://127.0.0.1:{port}/")
    print_fn(f"  Username:   {username}")
    if password is not None:
        print_fn(f"  Password:   {password}")
        print_fn("  ^ shown ONCE — store it now; it is not recoverable.")
    else:
        print_fn("  Password:   (unchanged — use 'gaming web --reset-credentials')")
    if scheme == "http" and bind == "0.0.0.0":
        print_fn("")
        print_fn("  WARNING: bound to 0.0.0.0 over plain HTTP. On an untrusted")
        print_fn("  network use --tls, or bind to 127.0.0.1 and tunnel via SSH.")
    print_fn("=" * 60)
    print_fn("  Press Ctrl+C to stop.")
    print_fn("")
