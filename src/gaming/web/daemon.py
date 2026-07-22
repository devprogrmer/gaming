"""Background/daemon lifecycle for ``gaming web`` (stdlib only).

Keeps the dashboard running independently of the SSH session or terminal that
launched it. The pieces here are deliberately split so the PID-file and
signal logic can be unit-tested without actually forking a detached process:

* :func:`read_pid` / :func:`write_pid` / :func:`remove_pid` — PID-file I/O.
* :func:`process_alive` — liveness check (``kill(pid, 0)`` on POSIX,
  ``OpenProcess`` on Windows, where signal 0 would send Ctrl+C instead).
* :func:`status` — "running since when?" without touching the process.
* :func:`stop` — graceful ``SIGTERM`` (falls back to ``SIGKILL``) via PID file.
* :func:`daemonize` — POSIX double-fork/``setsid`` detach + stdio redirect.

Windows has no ``fork``; :func:`daemonize` raises :class:`DaemonError` there
with a message pointing at the documented alternatives, and the lifecycle
helpers still work for a process started some other way.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..interactive import paths
from ..logging_setup import get_logger

log = get_logger("gaming.web.daemon")


class DaemonError(RuntimeError):
    """Raised when a daemon lifecycle operation cannot be completed."""


@dataclass(slots=True)
class Status:
    """Snapshot of the daemon's state for ``gaming web --status``."""

    running: bool
    pid: int | None = None
    since: float | None = None  # PID-file mtime (epoch seconds), a start-time proxy


def read_pid(pid_path: Path | None = None) -> int | None:
    """Return the PID recorded in the PID file, or ``None`` if absent/garbage."""
    path = pid_path or paths.web_pid_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def write_pid(pid: int, pid_path: Path | None = None) -> None:
    """Write ``pid`` to the PID file (atomically enough for our single writer)."""
    path = pid_path or paths.web_pid_path()
    path.write_text(f"{pid}\n", encoding="utf-8")


def remove_pid(pid_path: Path | None = None) -> None:
    """Delete the PID file if present; never raise if it is already gone."""
    path = pid_path or paths.web_pid_path()
    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        pass


def process_alive(pid: int) -> bool:
    """True if a process with ``pid`` currently exists.

    On POSIX this is the classic ``kill(pid, 0)`` probe. On Windows that idiom
    is unsafe — ``os.kill``'s signal ``0`` is ``CTRL_C_EVENT``, which would send
    a Ctrl+C to the whole console process group instead of just testing
    liveness — so we query the process handle directly via ``ctypes`` there.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _process_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still "alive" for our purposes.
        return True
    except OSError:
        return False
    return True


def _process_alive_windows(pid: int) -> bool:
    """Windows liveness check via ``OpenProcess`` + ``GetExitCodeProcess``.

    Avoids ``os.kill`` entirely (see :func:`process_alive`). Returns False if the
    process can't be opened (gone) or has already exited; True while it is still
    running. Fail-soft: any ctypes/OS error is treated as "not alive".
    """
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except OSError:
        return False


def status(pid_path: Path | None = None) -> Status:
    """Report whether a daemonized dashboard is currently running.

    A stale PID file (process no longer alive) is cleaned up and reported as
    not running, so ``--status`` never claims a dead daemon is up.
    """
    path = pid_path or paths.web_pid_path()
    pid = read_pid(path)
    if pid is None:
        return Status(running=False)
    if not process_alive(pid):
        remove_pid(path)
        return Status(running=False)
    since: float | None
    try:
        since = path.stat().st_mtime
    except OSError:
        since = None
    return Status(running=True, pid=pid, since=since)


def stop(pid_path: Path | None = None, *, timeout: float = 5.0) -> bool:
    """Gracefully stop a daemonized dashboard via its PID file.

    Sends ``SIGTERM`` and waits up to ``timeout`` seconds for the process to
    exit, escalating to ``SIGKILL`` if it does not. Returns True if a running
    process was stopped, False if none was found. The PID file is removed on
    success. Fail-soft: a missing/stale PID file is a no-op returning False.
    """
    path = pid_path or paths.web_pid_path()
    pid = read_pid(path)
    if pid is None or not process_alive(pid):
        remove_pid(path)
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        remove_pid(path)
        return False
    except OSError as exc:
        raise DaemonError(f"could not signal PID {pid}: {exc}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            remove_pid(path)
            return True
        time.sleep(0.1)

    # Still alive after the grace period — force it. SIGKILL is POSIX-only
    # (the real deployment target); fall back to SIGTERM where it's absent.
    force_sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        os.kill(pid, force_sig)
    except OSError:
        pass
    remove_pid(path)
    return True


def daemonize(log_path: Path | None = None) -> None:
    """Detach the current process from the controlling terminal (POSIX).

    Standard double-fork so the daemon can never reacquire a controlling TTY,
    ``os.setsid`` to start a new session, ``chdir('/')`` so we don't pin a
    mount, and stdin/stdout/stderr redirected (stdout+stderr to ``log_path``).
    The original foreground process exits inside this call; only the detached
    grandchild returns.

    Raises :class:`DaemonError` on platforms without ``os.fork`` (e.g. Windows),
    where the caller should fall back to a foreground run or a service manager.
    """
    if not hasattr(os, "fork"):
        raise DaemonError(
            "--daemon is not supported on this platform (no os.fork). "
            "Run 'gaming web' in the foreground, or use a service manager "
            "(see packaging/gaming-web.service) to keep it running."
        )

    logfile = log_path or paths.web_log_path()

    # First fork: parent returns to the shell; child continues.
    if os.fork() > 0:
        os._exit(0)

    os.setsid()

    # Second fork: ensure we are not a session leader (can't acquire a TTY).
    if os.fork() > 0:
        os._exit(0)

    os.chdir("/")
    os.umask(0o077)

    # Flush and replace the standard streams.
    sys.stdout.flush()
    sys.stderr.flush()

    with open(os.devnull, "rb", 0) as devnull_in:
        os.dup2(devnull_in.fileno(), sys.stdin.fileno())
    out = open(logfile, "ab", 0)
    os.dup2(out.fileno(), sys.stdout.fileno())
    os.dup2(out.fileno(), sys.stderr.fileno())
