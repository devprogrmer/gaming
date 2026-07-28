"""Small HTTP helper built on the stdlib with retries and timeouts.

Kept dependency-free on purpose so the whole tool runs with a bare Python
3.11 install. All network access is best-effort and degrades gracefully.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from ..logging_setup import get_logger

log = get_logger("gaming.http")

_USER_AGENT = "gaming/0.1 (+network-discovery-cli)"


class HTTPError(Exception):
    """Raised when an HTTP request ultimately fails.

    ``status`` carries the HTTP status code when the failure was an HTTP error
    response (rather than a transport/timeout error), so callers can tell
    "this resource does not exist" (404) apart from "try again later" (429).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def not_found(self) -> bool:
        """True when the server answered 404/410 — the resource is absent."""
        return self.status in (404, 410)

    @property
    def rate_limited(self) -> bool:
        """True when the server answered 429 — we are being throttled."""
        return self.status == 429


# Statuses that are pointless to retry: asking again will not change the
# answer, so a long exhaustive sweep should skip on immediately instead of
# burning its remaining attempts (and wall-clock) on each missing resource.
_FAST_SKIP_STATUSES = frozenset({400, 401, 403, 404, 405, 410, 451})

# Ceiling for a single backoff sleep, so a hostile or buggy Retry-After header
# cannot stall a long-running job for hours.
_MAX_BACKOFF_SECONDS = 60.0


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date), if present."""
    try:
        raw = exc.headers.get("Retry-After") if exc.headers else None
    except AttributeError:
        return None
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _backoff_seconds(exc: BaseException, attempt: int) -> float:
    """Delay before the next retry of a failed attempt.

    Rate limiting (429) gets exponential backoff — 2s, 4s, 8s … — because
    polite pacing is what gets a long sweep un-throttled, and honors a longer
    server-advertised ``Retry-After``. Everything else keeps the original
    gentle linear delay.
    """
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        delay = 2.0 * (2**attempt)
        advertised = _retry_after_seconds(exc)
        if advertised is not None:
            delay = max(delay, advertised)
        return min(delay, _MAX_BACKOFF_SECONDS)
    return min(0.5 * (attempt + 1), _MAX_BACKOFF_SECONDS)


def get_text(
    url: str,
    *,
    timeout: float = 5.0,
    retries: int = 2,
    headers: dict[str, str] | None = None,
) -> str:
    """Fetch a URL and return the response body as text.

    Transport errors retry with linear backoff; 429s retry with exponential
    backoff honoring ``Retry-After``; statuses that cannot change on retry
    (notably 404) fail fast without consuming the remaining attempts. Raises
    :class:`HTTPError` on final failure, with ``.status`` set for HTTP errors.
    """
    hdrs = {"User-Agent": _USER_AGENT, "Accept": "application/json, */*"}
    if headers:
        hdrs.update(headers)

    last_exc: Exception | None = None
    status: int | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            status = exc.code
            if exc.code in _FAST_SKIP_STATUSES:
                log.debug("HTTP GET %s: %d (not retrying)", url, exc.code)
                break
            log.debug("HTTP GET failed (attempt %d) %s: %s", attempt + 1, url, exc)
            if attempt < retries:
                time.sleep(_backoff_seconds(exc, attempt))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            log.debug("HTTP GET failed (attempt %d) %s: %s", attempt + 1, url, exc)
            if attempt < retries:
                time.sleep(_backoff_seconds(exc, attempt))
    raise HTTPError(f"GET {url} failed: {last_exc}", status=status)


def get_json(
    url: str,
    *,
    timeout: float = 5.0,
    retries: int = 2,
    headers: dict[str, str] | None = None,
) -> Any:
    """Fetch a URL and parse the body as JSON."""
    body = get_text(url, timeout=timeout, retries=retries, headers=headers)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPError(f"invalid JSON from {url}: {exc}") from exc


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = 5.0,
    retries: int = 1,
    headers: dict[str, str] | None = None,
) -> Any:
    hdrs = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        hdrs.update(headers)
    data = json.dumps(payload).encode("utf-8")

    last_exc: Exception | None = None
    status: int | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                body = resp.read().decode(charset, errors="replace")
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            status = exc.code
            if exc.code in _FAST_SKIP_STATUSES:
                log.debug("HTTP POST %s: %d (not retrying)", url, exc.code)
                break
            log.debug("HTTP POST failed (attempt %d) %s: %s", attempt + 1, url, exc)
            if attempt < retries:
                time.sleep(_backoff_seconds(exc, attempt))
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_exc = exc
            log.debug("HTTP POST failed (attempt %d) %s: %s", attempt + 1, url, exc)
            if attempt < retries:
                time.sleep(_backoff_seconds(exc, attempt))
    raise HTTPError(f"POST {url} failed: {last_exc}", status=status)
