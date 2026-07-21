"""Static asset loading for the dashboard, bundled via ``importlib.resources``.

The HTML/CSS/JS live in ``gaming/web/static`` and are shipped as package data
(same mechanism as ``gaming/interactive/data``), so the dashboard works fully
offline with no build step and no CDN. Assets are read on demand and cached in
memory for the life of the process.
"""

from __future__ import annotations

from importlib import resources

from ..logging_setup import get_logger

log = get_logger("gaming.web.assets")

_STATIC_PKG = "gaming.web.static"

# Minimal content-type map for the handful of asset types we ship.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}

_cache: dict[str, bytes] = {}


def content_type_for(name: str) -> str:
    for ext, ctype in _CONTENT_TYPES.items():
        if name.endswith(ext):
            return ctype
    return "application/octet-stream"


def load_asset(name: str) -> bytes | None:
    """Return the bytes of a bundled static asset, or ``None`` if absent.

    ``name`` is a bare filename (no directory traversal). Any path separator
    makes it an immediate miss, so a crafted request can never escape the
    static package.
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    if name in _cache:
        return _cache[name]
    try:
        data = resources.files(_STATIC_PKG).joinpath(name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError, IsADirectoryError):
        return None
    _cache[name] = data
    return data


def index_html() -> bytes:
    """Return the SPA shell, or a tiny fallback if the asset is missing."""
    data = load_asset("index.html")
    if data is not None:
        return data
    return b"<!doctype html><title>gaming</title><p>dashboard assets missing.</p>"
