"""The one and only shutdown path for the dashboard.

Every way of stopping ``gaming web`` — Ctrl+C in the foreground, the
interactive menu's launch option, and ``gaming web --stop`` (which sends
``SIGTERM`` via the PID file) — routes through :class:`ShutdownCoordinator`.
Having exactly one implementation is the point: the previous release had the
cleanup logic inline in ``serve()`` guarded by a bare ``except
KeyboardInterrupt``, which meant ``SIGTERM`` skipped it entirely and in-flight
jobs were never considered.

Why a coordinator rather than ``try/except KeyboardInterrupt``:

* ``SIGTERM`` does not raise ``KeyboardInterrupt`` at all, so the daemon
  ``stop()`` path got no cleanup whatsoever — the process was simply killed.
* A ``KeyboardInterrupt`` is delivered to whichever thread the interpreter
  happens to pick (in CPython, the main thread) at an arbitrary bytecode
  boundary. Catching it at one call site says nothing about the other threads
  still touching the database.
* ``ThreadingHTTPServer.shutdown()`` deadlocks if called from the thread
  running ``serve_forever()``, so the stop must be issued from elsewhere.

Ordering is deliberate and is what makes a restart on the same port work:

1. stop accepting new connections (``httpd.shutdown()``, from another thread),
2. cancel + bounded-join in-flight job threads so no worker is killed
   mid-SQLite-write,
3. stop the scan scheduler thread if one is running,
4. ``server_close()`` to release the listening socket,
5. remove the PID file, then print the final message.

The whole sequence is idempotent: a second Ctrl+C (or a ``SIGTERM`` racing a
``SIGINT``) is ignored rather than re-entering cleanup, and repeated signals
escalate to an immediate exit so the user is never stuck.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from typing import Any

from ..logging_setup import get_logger

log = get_logger("gaming.web.lifecycle")

# How long in-flight jobs get to reach a safe stopping point before we give up
# waiting. Bounded so shutdown always terminates; jobs are daemon threads, so
# anything still running past this can never block process exit.
DEFAULT_JOB_DRAIN_TIMEOUT = 5.0
DEFAULT_SERVER_JOIN_TIMEOUT = 5.0

# The coordinator for the server running in this process, if any. Lets callers
# that did not create it (the interactive menu on a worker thread, an embedding
# script, tests) trigger the *same* cleanup without a signal round-trip.
_active: ShutdownCoordinator | None = None
_active_lock = threading.Lock()


def set_active(coordinator: ShutdownCoordinator | None) -> None:
    global _active
    with _active_lock:
        _active = coordinator


def get_active() -> ShutdownCoordinator | None:
    with _active_lock:
        return _active


def shutdown_active() -> bool:
    """Stop the server running in this process. True if there was one.

    The programmatic equivalent of pressing Ctrl+C — it runs the identical
    sequence, because it is the identical object.
    """
    coordinator = get_active()
    if coordinator is None:
        return False
    coordinator.request_stop()
    return True


class ShutdownCoordinator:
    """Coordinates an orderly stop of the HTTP server, jobs, and scheduler.

    Constructed by :func:`gaming.web.server.serve`; the registered signal
    handlers and the direct :meth:`shutdown` call share this one instance, so
    there is no second cleanup implementation that can drift out of sync.
    """

    def __init__(
        self,
        *,
        httpd: Any = None,
        jobs: Any = None,
        scheduler: Any = None,
        print_fn: Callable[[str], None] = print,
        job_drain_timeout: float = DEFAULT_JOB_DRAIN_TIMEOUT,
        server_join_timeout: float = DEFAULT_SERVER_JOIN_TIMEOUT,
        on_cleanup: Callable[[], None] | None = None,
    ) -> None:
        self.httpd = httpd
        self.jobs = jobs
        self.scheduler = scheduler
        self.print_fn = print_fn
        self.job_drain_timeout = job_drain_timeout
        self.server_join_timeout = server_join_timeout
        self.on_cleanup = on_cleanup

        self.server_thread: threading.Thread | None = None
        # Set once the stop sequence has been *requested*; the main loop waits
        # on this rather than on a KeyboardInterrupt landing in the right place.
        self.stopping = threading.Event()
        # Set once cleanup has fully finished.
        self.finished = threading.Event()
        self._lock = threading.Lock()
        self._signal_count = 0
        self._previous_handlers: dict[int, Any] = {}

    # ---- signal wiring ---------------------------------------------------
    def install_signal_handlers(self) -> None:
        """Register SIGINT/SIGTERM handlers, remembering the previous ones.

        Signal handlers can only be installed from the main thread; when
        ``serve()`` runs off the main thread (the interactive menu may do this,
        and tests certainly do) registration is skipped and the direct
        :meth:`shutdown` call remains the stop path. That is why cleanup lives
        here and not inside the handler.
        """
        if threading.current_thread() is not threading.main_thread():
            log.debug("not on main thread; skipping signal handler installation")
            return
        for signum in self._signals():
            try:
                self._previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
            except (OSError, ValueError, RuntimeError) as exc:
                log.debug("could not install handler for signal %s: %s", signum, exc)

    def restore_signal_handlers(self) -> None:
        """Put the previous handlers back (so the menu keeps working after a stop)."""
        if threading.current_thread() is not threading.main_thread():
            return
        for signum, handler in self._previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError, RuntimeError):
                pass
        self._previous_handlers.clear()

    @staticmethod
    def _signals() -> tuple[int, ...]:
        # SIGTERM matters for parity with daemon.stop(); it exists on Windows
        # too, though only SIGINT is actually delivered there in practice.
        out = [signal.SIGINT]
        term = getattr(signal, "SIGTERM", None)
        if term is not None:
            out.append(term)
        return tuple(out)

    def _handle_signal(self, signum, frame) -> None:  # noqa: ARG002 - signal ABI
        """Signal-handler entry point: request shutdown, escalate if repeated.

        Deliberately does almost nothing — it flips the flag and returns so the
        interpreter leaves the handler promptly. The real work happens on the
        thread waiting in :meth:`wait_for_shutdown`, because doing a
        multi-second drain inside a signal handler is how you end up wedged.
        """
        self._signal_count += 1
        if self._signal_count == 1:
            name = getattr(signal.Signals(signum), "name", str(signum))
            self.print_fn(f"\nReceived {name} — stopping the web panel...")
            self.request_stop()
            return
        # Second signal: the user is insisting. Stop waiting politely.
        self.print_fn("Interrupted again — exiting immediately.")
        raise SystemExit(130)

    # ---- shutdown --------------------------------------------------------
    def request_stop(self) -> None:
        """Ask for shutdown without blocking (safe from a signal handler).

        The HTTP loop is stopped from a separate thread because
        ``shutdown()`` deadlocks when called from the thread running
        ``serve_forever()``.
        """
        if self.stopping.is_set():
            return
        self.stopping.set()
        if self.httpd is not None:
            threading.Thread(
                target=self._stop_httpd, name="gaming-web-stop", daemon=True
            ).start()

    def _stop_httpd(self) -> None:
        try:
            self.httpd.shutdown()
        except Exception as exc:  # noqa: BLE001 - never let cleanup raise
            log.debug("httpd.shutdown() raised: %s", exc)

    def shutdown(self) -> None:
        """Run the full cleanup sequence. Idempotent and never raises.

        This is the single implementation shared by the signal handlers, the
        CLI, the interactive menu, and the daemon stop path.
        """
        with self._lock:
            if self.finished.is_set():
                return
            self.stopping.set()

            # 1. Stop accepting connections and let the serve loop exit.
            if self.httpd is not None:
                self._stop_httpd()
            if self.server_thread is not None and (
                self.server_thread is not threading.current_thread()
            ):
                self.server_thread.join(timeout=self.server_join_timeout)

            # 2. Let in-flight jobs reach a safe stopping point.
            stuck = []
            if self.jobs is not None:
                try:
                    stuck = self.jobs.drain(timeout=self.job_drain_timeout)
                except Exception as exc:  # noqa: BLE001
                    log.debug("job drain raised: %s", exc)
            if stuck:
                self.print_fn(
                    f"  {len(stuck)} background job(s) did not stop in time; "
                    "they will be abandoned."
                )

            # 3. Stop the recurring-scan scheduler, if one is attached.
            if self.scheduler is not None:
                try:
                    self.scheduler.stop()
                except Exception as exc:  # noqa: BLE001
                    log.debug("scheduler.stop() raised: %s", exc)

            # 4. Release the listening socket so an immediate restart on the
            #    same port does not hit "address already in use".
            if self.httpd is not None:
                try:
                    self.httpd.server_close()
                except Exception as exc:  # noqa: BLE001
                    log.debug("server_close() raised: %s", exc)

            # 5. PID file removal / other caller-supplied cleanup.
            if self.on_cleanup is not None:
                try:
                    self.on_cleanup()
                except Exception as exc:  # noqa: BLE001
                    log.debug("on_cleanup raised: %s", exc)

            self.finished.set()
            self.print_fn("Web panel stopped.")
        if get_active() is self:
            set_active(None)

    def wait_for_shutdown(self) -> None:
        """Block until a stop is requested, then run cleanup.

        Waits on an ``Event`` rather than sleeping, so the signal handler's
        flag is observed promptly. ``KeyboardInterrupt`` is still caught as a
        belt-and-braces fallback for the case where handler installation was
        skipped (non-main-thread ``serve()``).
        """
        try:
            while not self.stopping.wait(timeout=0.5):
                pass
        except KeyboardInterrupt:
            self.print_fn("\nStopping the web panel...")
        finally:
            self.shutdown()
            self.restore_signal_handlers()
