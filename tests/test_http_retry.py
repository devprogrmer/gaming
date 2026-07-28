"""HTTP retry semantics: 429 backoff (honouring Retry-After) and 404 fast-skip."""

from __future__ import annotations

import io
import urllib.error
from email.message import Message

import pytest

from gaming.utils import http as http_mod


class _Resp(io.BytesIO):
    """Minimal stand-in for the object urlopen returns."""

    def __init__(self, payload: bytes = b"ok") -> None:
        super().__init__(payload)
        self.headers = Message()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.test/x", code, "boom", headers or {}, None
    )


@pytest.fixture
def net(monkeypatch):
    """Record every urlopen attempt and every sleep the retry logic performs."""
    state = {"attempts": 0, "sleeps": [], "responses": []}

    def fake_urlopen(request, timeout=None, context=None):
        state["attempts"] += 1
        outcome = state["responses"].pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(http_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: state["sleeps"].append(s))
    return state


# ---- 404: fast-skip ------------------------------------------------------
@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 410, 451])
def test_non_retryable_status_is_not_retried(net, status):
    """A 404 will still be a 404 in two seconds. Don't burn a sweep on it."""
    net["responses"] = [_http_error(status)]
    with pytest.raises(http_mod.HTTPError) as excinfo:
        http_mod.get_text("https://example.test/x", retries=3)
    assert net["attempts"] == 1
    assert net["sleeps"] == []
    assert excinfo.value.status == status


def test_not_found_is_introspectable(net):
    net["responses"] = [_http_error(404)]
    with pytest.raises(http_mod.HTTPError) as excinfo:
        http_mod.get_text("https://example.test/x", retries=3)
    assert excinfo.value.not_found is True
    assert excinfo.value.rate_limited is False


# ---- 429: exponential backoff -------------------------------------------
def test_rate_limit_backs_off_exponentially(net):
    net["responses"] = [_http_error(429), _http_error(429), _Resp(b"finally")]
    body = http_mod.get_text("https://example.test/x", retries=3)
    assert body == "finally"
    assert net["attempts"] == 3
    assert net["sleeps"] == [2.0, 4.0]


def test_rate_limit_honours_retry_after_seconds(net):
    net["responses"] = [_http_error(429, {"Retry-After": "7"}), _Resp(b"ok")]
    http_mod.get_text("https://example.test/x", retries=3)
    # Advertised delay wins when it exceeds our own backoff.
    assert net["sleeps"] == [7.0]


def test_retry_after_is_capped(net):
    """A hostile or broken Retry-After must not park the process for a day."""
    net["responses"] = [_http_error(429, {"Retry-After": "99999"}), _Resp(b"ok")]
    http_mod.get_text("https://example.test/x", retries=3)
    assert net["sleeps"] == [http_mod._MAX_BACKOFF_SECONDS]


def test_retry_after_http_date_is_understood(net):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    when = datetime.now(timezone.utc) + timedelta(seconds=20)
    net["responses"] = [
        _http_error(429, {"Retry-After": format_datetime(when)}),
        _Resp(b"ok"),
    ]
    http_mod.get_text("https://example.test/x", retries=3)
    assert net["sleeps"] and 15.0 <= net["sleeps"][0] <= 25.0


def test_garbage_retry_after_falls_back_to_our_own_backoff(net):
    net["responses"] = [_http_error(429, {"Retry-After": "soon-ish"}), _Resp(b"ok")]
    http_mod.get_text("https://example.test/x", retries=3)
    assert net["sleeps"] == [2.0]


def test_persistent_rate_limit_eventually_raises(net):
    net["responses"] = [_http_error(429) for _ in range(4)]
    with pytest.raises(http_mod.HTTPError) as excinfo:
        http_mod.get_text("https://example.test/x", retries=3)
    assert excinfo.value.rate_limited is True
    assert net["attempts"] == 4


# ---- transient server errors still retry --------------------------------
def test_server_error_is_retried_with_short_backoff(net):
    net["responses"] = [_http_error(503), _Resp(b"ok")]
    assert http_mod.get_text("https://example.test/x", retries=3) == "ok"
    assert net["sleeps"] == [0.5]


def test_url_error_is_retried(net):
    net["responses"] = [urllib.error.URLError("dns"), _Resp(b"ok")]
    assert http_mod.get_text("https://example.test/x", retries=3) == "ok"
    assert net["attempts"] == 2
