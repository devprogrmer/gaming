"""Tests for the ``gaming web`` background/daemon lifecycle.

The PID-file and signal logic are exercised directly; the actual double-fork
detach (:func:`gaming.web.daemon.daemonize`) is not invoked in-process — we
test the observable contract around it (PID file, --status, --stop) instead.
"""

from __future__ import annotations

import os

import pytest

from gaming.web import daemon


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMING_HOME", str(tmp_path))
    yield


def test_write_read_remove_pid(tmp_path):
    pid_path = tmp_path / "web.pid"
    assert daemon.read_pid(pid_path) is None
    daemon.write_pid(4242, pid_path)
    assert daemon.read_pid(pid_path) == 4242
    daemon.remove_pid(pid_path)
    assert daemon.read_pid(pid_path) is None
    # Removing an already-absent PID file is a silent no-op.
    daemon.remove_pid(pid_path)


def test_read_pid_ignores_garbage(tmp_path):
    pid_path = tmp_path / "web.pid"
    pid_path.write_text("not-a-number\n", encoding="utf-8")
    assert daemon.read_pid(pid_path) is None


def test_process_alive_for_self_and_bogus():
    # This very process is certainly alive.
    assert daemon.process_alive(os.getpid()) is True
    # PID 0 / negatives are never "alive" for our purposes.
    assert daemon.process_alive(0) is False
    assert daemon.process_alive(-1) is False


def test_process_alive_never_signals_on_windows(monkeypatch):
    """Regression: on Windows ``os.kill(pid, 0)`` sends CTRL_C_EVENT (signal 0 is
    CTRL_C_EVENT) to the whole console group, which killed pytest in CI. The
    Windows path must query the process handle instead and never call os.kill.
    """
    if os.name != "nt":
        pytest.skip("Windows-specific liveness path")

    def _boom(*a, **k):  # pragma: no cover - only runs if the bug returns
        raise AssertionError("process_alive must not call os.kill on Windows")

    monkeypatch.setattr(daemon.os, "kill", _boom)
    # Both a live and a bogus PID must resolve without ever touching os.kill.
    assert daemon.process_alive(os.getpid()) is True
    assert daemon.process_alive(999_999_999) is False


def test_status_running_uses_live_pid(tmp_path):
    pid_path = tmp_path / "web.pid"
    daemon.write_pid(os.getpid(), pid_path)
    st = daemon.status(pid_path)
    assert st.running is True
    assert st.pid == os.getpid()
    assert st.since is not None


def test_status_cleans_stale_pid_file(tmp_path, monkeypatch):
    pid_path = tmp_path / "web.pid"
    # A PID that is (essentially) certain not to be running.
    daemon.write_pid(999_999_999, pid_path)
    monkeypatch.setattr(daemon, "process_alive", lambda pid: False)
    st = daemon.status(pid_path)
    assert st.running is False
    # The stale file was cleaned up so a dead daemon isn't reported as up.
    assert not pid_path.exists()


def test_status_not_running_when_no_pid_file(tmp_path):
    st = daemon.status(tmp_path / "web.pid")
    assert st.running is False
    assert st.pid is None


def test_stop_sends_sigterm_and_removes_pid(tmp_path, monkeypatch):
    pid_path = tmp_path / "web.pid"
    daemon.write_pid(4242, pid_path)

    signals: list[tuple[int, int]] = []
    # Alive until we've signalled it, then "gone" so stop() returns promptly.
    alive_state = {"alive": True}

    def _fake_kill(pid, sig):
        signals.append((pid, sig))
        if sig != 0:
            alive_state["alive"] = False

    monkeypatch.setattr(daemon.os, "kill", _fake_kill)
    monkeypatch.setattr(daemon, "process_alive", lambda pid: alive_state["alive"])

    assert daemon.stop(pid_path, timeout=1.0) is True
    # SIGTERM (not the force signal) was sent to the recorded PID.
    import signal as _signal

    force_sig = getattr(_signal, "SIGKILL", _signal.SIGTERM)
    assert (4242, _signal.SIGTERM) in signals
    assert not any(s == force_sig and s != _signal.SIGTERM for _pid, s in signals)
    assert not pid_path.exists()


def test_stop_escalates_to_sigkill_when_graceful_fails(tmp_path, monkeypatch):
    pid_path = tmp_path / "web.pid"
    daemon.write_pid(4242, pid_path)

    signals: list[int] = []

    def _fake_kill(pid, sig):
        signals.append(sig)

    monkeypatch.setattr(daemon.os, "kill", _fake_kill)
    # Never dies on SIGTERM -> stop() must escalate.
    monkeypatch.setattr(daemon, "process_alive", lambda pid: True)

    assert daemon.stop(pid_path, timeout=0.2) is True
    import signal as _signal

    force_sig = getattr(_signal, "SIGKILL", _signal.SIGTERM)
    assert _signal.SIGTERM in signals
    assert force_sig in signals
    assert not pid_path.exists()


def test_stop_no_running_process_is_false(tmp_path, monkeypatch):
    pid_path = tmp_path / "web.pid"
    daemon.write_pid(4242, pid_path)
    monkeypatch.setattr(daemon, "process_alive", lambda pid: False)
    assert daemon.stop(pid_path) is False
    # Stale PID file cleaned up.
    assert not pid_path.exists()


def test_stop_missing_pid_file_is_false(tmp_path):
    assert daemon.stop(tmp_path / "web.pid") is False


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX-only fork semantics")
def test_daemonize_writes_pid_and_serves(tmp_path, monkeypatch):
    """serve(daemon=True) prints credentials BEFORE detaching, then records a PID.

    We stub the actual fork/redirect so the test process is never detached, and
    fake the HTTP server so serve_forever returns immediately.
    """
    from gaming.web import server

    events: list[str] = []

    class _FakeHTTPD:
        def __init__(self, addr, handler):
            self.socket = object()

        def serve_forever(self, poll_interval=0.5):
            events.append("served")

        def shutdown(self):
            pass

        def server_close(self):
            events.append("closed")

    monkeypatch.setattr(server, "ThreadingHTTPServer", _FakeHTTPD)
    monkeypatch.setattr(server, "_pick_free_port", lambda bind: 40404)
    monkeypatch.setattr(server, "_detect_server_ip", lambda: "203.0.113.9")

    # Stub daemonize so we don't actually fork/redirect this test runner.
    monkeypatch.setattr(daemon, "daemonize", lambda log_path=None: events.append("detached"))

    lines: list[str] = []
    rc = server.serve(bind="0.0.0.0", daemon=True, print_fn=lines.append)

    assert rc == 0
    out = "\n".join(lines)
    # Credentials were printed (before detaching) so the user sees them once.
    assert "Username:" in out
    assert "Password:" in out
    # The detach happened, the PID file was written, then cleaned up on close.
    assert "detached" in events
    assert events.index("detached") < events.index("served")
    # PID file was created during the run (removed again in the finally block).
    assert "served" in events and "closed" in events
