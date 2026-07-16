"""Small HTTP helper built on the stdlib with retries and timeouts.

Kept dependency-free on purpose so the whole tool runs with a bare Python
3.11 install. All network access is best-effort and degrades gracefully.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from ..logging_setup import get_logger

log = get_logger("gaming.http")

_USER_AGENT = "gaming/0.1 (+network-discovery-cli)"


class HTTPError(Exception):
    """Raised when an HTTP request ultimately fails."""


def get_text(
    url: str,
    *,
    timeout: float = 5.0,
    retries: int = 2,
    headers: Optional[dict[str, str]] = None,
) -> str:
    """Fetch a URL and return the response body as text.

    Retries with linear backoff. Raises :class:`HTTPError` on final failure.
    """
    hdrs = {"User-Agent": _USER_AGENT, "Accept": "application/json, */*"}
    if headers:
        hdrs.update(headers)

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            log.debug("HTTP GET failed (attempt %d) %s: %s", attempt + 1, url, exc)
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise HTTPError(f"GET {url} failed: {last_exc}")


def get_json(
    url: str,
    *,
    timeout: float = 5.0,
    retries: int = 2,
    headers: Optional[dict[str, str]] = None,
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
    headers: Optional[dict[str, str]] = None,
) -> Any:
    hdrs = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        hdrs.update(headers)
    data = json.dumps(payload).encode("utf-8")

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                body = resp.read().decode(charset, errors="replace")
            return json.loads(body)
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_exc = exc
            log.debug("HTTP POST failed (attempt %d) %s: %s", attempt + 1, url, exc)
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise HTTPError(f"POST {url} failed: {last_exc}")
