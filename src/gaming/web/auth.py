"""Authentication for the web dashboard: credentials, sessions, rate limiting.

Design goals (all stdlib):

* Credentials are generated on first run and stored **hashed** — the plaintext
  password is shown once and never persisted or logged.
* Passwords use ``hashlib.pbkdf2_hmac`` with a random per-install salt and a
  high iteration count; verification is constant-time (``hmac.compare_digest``).
* Sessions are stateless signed tokens (``hmac`` over ``user|expiry`` with a
  random per-install secret). Rotating the secret invalidates every session,
  which is how ``--reset-credentials`` and password changes log everyone out.
* A small in-memory, per-source-IP limiter blunts login brute-forcing.

The store is a single JSON file next to ``settings.json`` / ``history.db``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..interactive import paths
from ..logging_setup import get_logger

log = get_logger("gaming.web.auth")

_PBKDF2_ROUNDS = 240_000
_PBKDF2_ALGO = "sha256"
_SALT_BYTES = 16
_SESSION_SECRET_BYTES = 32
_TOKEN_SECRET_BYTES = 32
# Default session lifetime; short enough to limit a stolen-cookie window.
_SESSION_TTL_SECONDS = 12 * 3600
_MIN_PASSWORD_LEN = 10


class AuthError(Exception):
    """Raised for recoverable auth problems (bad current password, weak new)."""


@dataclass(slots=True)
class Credentials:
    username: str
    salt: str  # hex
    password_hash: str  # hex
    session_secret: str  # hex; rotating this invalidates all sessions
    auth_token: str  # bearer token for scripting/automation


def _hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ROUNDS
    )
    return dk.hex()


def check_password_strength(password: str) -> None:
    """Raise :class:`AuthError` if ``password`` is too weak (stdlib-only rule)."""
    if len(password) < _MIN_PASSWORD_LEN:
        raise AuthError(
            f"password must be at least {_MIN_PASSWORD_LEN} characters long"
        )
    classes = (
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    )
    if sum(bool(c) for c in classes) < 3:
        raise AuthError(
            "password must mix at least three of: lowercase, uppercase, "
            "digits, symbols"
        )


class CredentialStore:
    """Load/save dashboard credentials and mint/verify session tokens.

    Thread-safe: the dashboard serves requests from many threads, so all
    mutating operations hold a lock and rewrite the JSON file atomically.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else paths.credentials_path()
        self._lock = threading.Lock()
        self._creds: Credentials | None = None

    # ---- persistence -----------------------------------------------------
    def _load(self) -> Credentials | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read credentials file: %s", exc)
            return None
        try:
            return Credentials(
                username=str(raw["username"]),
                salt=str(raw["salt"]),
                password_hash=str(raw["password_hash"]),
                session_secret=str(raw["session_secret"]),
                auth_token=str(raw.get("auth_token", "")),
            )
        except (KeyError, TypeError):
            log.warning("credentials file is malformed; ignoring")
            return None

    def _write(self, creds: Credentials) -> None:
        payload = {
            "username": creds.username,
            "salt": creds.salt,
            "password_hash": creds.password_hash,
            "session_secret": creds.session_secret,
            "auth_token": creds.auth_token,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._creds = creds

    def _current(self) -> Credentials | None:
        if self._creds is None:
            self._creds = self._load()
        return self._creds

    # ---- provisioning ----------------------------------------------------
    def ensure_credentials(self) -> tuple[Credentials, str | None]:
        """Return existing credentials, generating them on first run.

        On generation, the returned tuple's second element is the **plaintext**
        password (to print once); on subsequent calls it is ``None`` since the
        plaintext is never stored.
        """
        with self._lock:
            existing = self._current()
            if existing is not None:
                return existing, None
            return self._generate_locked()

    def reset_credentials(self) -> tuple[Credentials, str]:
        """Regenerate username + password + secrets (recovery path).

        Rotating ``session_secret`` invalidates every outstanding session.
        Returns the new credentials and the new plaintext password to print.
        """
        with self._lock:
            return self._generate_locked()

    def _generate_locked(self) -> tuple[Credentials, str]:
        username = "admin-" + secrets.token_hex(3)
        password = secrets.token_urlsafe(15)
        salt = secrets.token_bytes(_SALT_BYTES)
        creds = Credentials(
            username=username,
            salt=salt.hex(),
            password_hash=_hash_password(password, salt),
            session_secret=secrets.token_hex(_SESSION_SECRET_BYTES),
            auth_token=secrets.token_urlsafe(_TOKEN_SECRET_BYTES),
        )
        self._write(creds)
        return creds, password

    # ---- verification ----------------------------------------------------
    def verify_password(self, username: str, password: str) -> bool:
        creds = self._current()
        if creds is None:
            return False
        salt = bytes.fromhex(creds.salt)
        candidate = _hash_password(password, salt)
        user_ok = hmac.compare_digest(username, creds.username)
        pass_ok = hmac.compare_digest(candidate, creds.password_hash)
        return user_ok and pass_ok

    def change_credentials(
        self, current_password: str, new_username: str, new_password: str
    ) -> None:
        """Rewrite stored credentials after confirming the current password.

        Raises :class:`AuthError` on a wrong current password or a weak new
        password. Rotates the session secret so existing sessions are dropped.
        """
        with self._lock:
            creds = self._current()
            if creds is None:
                raise AuthError("no credentials configured")
            if not self.verify_password(creds.username, current_password):
                raise AuthError("current password is incorrect")
            username = (new_username or creds.username).strip()
            if not username:
                raise AuthError("username cannot be empty")
            check_password_strength(new_password)
            salt = secrets.token_bytes(_SALT_BYTES)
            self._write(
                Credentials(
                    username=username,
                    salt=salt.hex(),
                    password_hash=_hash_password(new_password, salt),
                    # Rotate the session secret -> all current sessions invalid.
                    session_secret=secrets.token_hex(_SESSION_SECRET_BYTES),
                    auth_token=creds.auth_token,
                )
            )

    # ---- sessions --------------------------------------------------------
    def issue_session(self, *, now: float | None = None) -> str:
        """Return a signed session token for the configured user."""
        creds = self._current()
        if creds is None:
            raise AuthError("no credentials configured")
        now = time.time() if now is None else now
        expiry = int(now + _SESSION_TTL_SECONDS)
        return self._sign(creds, creds.username, expiry)

    def validate_session(self, token: str, *, now: float | None = None) -> bool:
        """True if ``token`` is a valid, unexpired session for the current user."""
        creds = self._current()
        if creds is None or not token:
            return False
        parts = token.split(".")
        if len(parts) != 3:
            return False
        username, expiry_s, _sig = parts
        try:
            expiry = int(expiry_s)
        except ValueError:
            return False
        now = time.time() if now is None else now
        if expiry < now:
            return False
        if not hmac.compare_digest(username, creds.username):
            return False
        expected = self._sign(creds, username, expiry)
        return hmac.compare_digest(token, expected)

    def verify_bearer(self, token: str) -> bool:
        """Constant-time check of the automation bearer token."""
        creds = self._current()
        if creds is None or not token or not creds.auth_token:
            return False
        return hmac.compare_digest(token, creds.auth_token)

    @staticmethod
    def _sign(creds: Credentials, username: str, expiry: int) -> str:
        msg = f"{username}.{expiry}".encode()
        key = bytes.fromhex(creds.session_secret)
        sig = hmac.new(key, msg, hashlib.sha256).hexdigest()
        return f"{username}.{expiry}.{sig}"


class RateLimiter:
    """In-memory, per-key login limiter with a fixed window + lockout.

    Not distributed and intentionally simple: it blunts online brute-forcing of
    the single dashboard account without any external dependency. Keyed by
    source IP.
    """

    def __init__(self, *, max_attempts: int = 5, window: float = 300.0) -> None:
        self.max_attempts = max_attempts
        self.window = window
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._hits.get(key, []) if now - t < self.window]
        self._hits[key] = recent
        return recent

    def is_blocked(self, key: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            return len(self._prune(key, now)) >= self.max_attempts

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            recent = self._prune(key, now)
            recent.append(now)
            self._hits[key] = recent

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
