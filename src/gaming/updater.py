"""In-place update mechanism for a deployed installation.

The installer (``install.sh`` / ``install.ps1``) lays out a deployment as::

    <project>/
        .venv/            # isolated virtual environment
        pyproject.toml    # the project source
        src/gaming/...

and installs the package into ``.venv`` with a ``python -m gaming`` launcher.

Updating in place means: pick up a release *source* (via ``git pull`` for the
latest, or ``git checkout <tag>`` to switch to a specific release — including an
older one), then ``pip install`` it into the **same** virtual environment. pip
replaces the package files atomically — the previous installation is never
deleted first, and because the launcher runs ``python -m gaming`` (not a locked
console ``.exe``), a pure-Python reinstall is safe even while the tool is
installed and in use. Switching to an older release works too: the reinstall
uses ``--force-reinstall`` so pip does not refuse to "downgrade".

User state (scan history, settings, custom ranges) lives under the application
data directory (see :mod:`gaming.interactive.paths`), which is entirely
separate from the install tree, so upgrades never touch it.

Everything here is standard-library only and the subprocess runner is
injectable, so the flow is fully unit-testable offline.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import __version__

#: Environment variable that pins the project source directory to update from.
SOURCE_ENV = "GAMING_SOURCE_DIR"

# A runner takes the command argv and an optional working directory and returns
# a CompletedProcess. Injectable so tests never spawn real processes.
CommandRunner = Callable[[Sequence[str], "Path | None"], subprocess.CompletedProcess]


class UpdateError(RuntimeError):
    """Raised when an in-place update cannot be completed."""


@dataclass(slots=True)
class UpdateResult:
    """Outcome of an in-place update."""

    previous_version: str
    new_version: str
    source: Path
    git_updated: bool
    #: The release ref that was checked out (e.g. ``v0.1.0``), or ``None`` when
    #: updating to the latest of the current branch rather than a pinned ref.
    ref: str | None = None

    @property
    def changed(self) -> bool:
        return self.previous_version != self.new_version


def _default_runner(
    cmd: Sequence[str], cwd: Path | None = None
) -> subprocess.CompletedProcess:
    """Run a command, capturing combined stdout/stderr as text."""
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


def default_source_dir() -> Path | None:
    """Best-effort discovery of the project source directory to update from.

    Resolution order:
      1. ``$GAMING_SOURCE_DIR`` if set.
      2. The parent of the active virtual environment (``sys.prefix``), which
         matches the installer layout (venv lives at ``<project>/.venv``), when
         it contains a ``pyproject.toml``.
    """
    env = os.environ.get(SOURCE_ENV)
    if env:
        return Path(env).expanduser()
    # Installer layout: the venv is created at "<project>/.venv", so the
    # project root is the venv's parent directory.
    candidate = Path(sys.prefix).parent
    if (candidate / "pyproject.toml").exists():
        return candidate
    return None


def _resolve_source(source: str | Path | None) -> Path:
    if source is not None:
        src = Path(source).expanduser()
    else:
        detected = default_source_dir()
        if detected is None:
            raise UpdateError(
                "could not locate the project source directory. Pass "
                "--source PATH pointing at the gaming checkout, or set "
                f"{SOURCE_ENV}."
            )
        src = detected
    src = src.resolve()
    if not src.exists():
        raise UpdateError(f"source directory does not exist: {src}")
    if not (src / "pyproject.toml").exists():
        raise UpdateError(f"no pyproject.toml in {src}; not a gaming checkout.")
    return src


def _installed_version(python: str, runner: CommandRunner) -> str:
    """Read the version pip actually installed, in a fresh interpreter.

    The current process still holds the *old* ``__version__`` in memory, so we
    ask a new interpreter to import the freshly-installed package. Run from a
    neutral directory (the interpreter's own folder) so a src-layout checkout in
    the caller's cwd can't shadow the installed package.
    """
    neutral = Path(python).resolve().parent
    proc = runner(
        [python, "-c", "import gaming; print(gaming.__version__)"],
        neutral,
    )
    if proc.returncode == 0 and (proc.stdout or "").strip():
        return proc.stdout.strip().splitlines()[-1].strip()
    return "unknown"


def _git(src: Path, args: Sequence[str], runner: CommandRunner):
    """Run a git subcommand inside ``src``."""
    return runner(["git", "-C", str(src), *args], src)


def _ref_exists(src: Path, ref: str, runner: CommandRunner) -> bool:
    """Return True if ``ref`` resolves to a commit in the checkout."""
    proc = _git(src, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], runner)
    return proc.returncode == 0


def list_releases(
    source: str | Path | None = None,
    *,
    runner: CommandRunner | None = None,
) -> list[str]:
    """Return the available release tags in the source checkout, newest first.

    Tags are sorted by version (``git tag --sort=-version:refname``) so the
    most recent release comes first. Returns an empty list when the source is
    not a git checkout or has no tags.

    Raises:
        UpdateError: if the source directory can't be resolved.
    """
    runner = runner or _default_runner
    src = _resolve_source(source)
    if not (src / ".git").exists():
        return []
    # Refresh tags from the remote first so releases published after the last
    # pull are visible; ignore failures (e.g. offline) and use local tags.
    _git(src, ["fetch", "--tags", "--quiet"], runner)
    proc = _git(src, ["tag", "--list", "--sort=-version:refname"], runner)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def run_update(
    *,
    source: str | Path | None = None,
    pull: bool | None = None,
    python: str | None = None,
    runner: CommandRunner | None = None,
    log: Callable[[str], None] | None = None,
    ref: str | None = None,
) -> UpdateResult:
    """Upgrade or switch the current installation in place to a release.

    Args:
        source: Path to the project checkout to install from. Defaults to
            auto-detection (see :func:`default_source_dir`).
        pull: Whether to ``git pull --ff-only`` the source first. ``None``
            (default) means "pull if the source is a git checkout"; ``False``
            installs the source exactly as it is on disk. Ignored when ``ref``
            is given (switching to a pinned release checks out that ref instead
            of pulling the branch tip).
        python: The virtual-environment interpreter to install into. Defaults
            to the running interpreter (``sys.executable``), which is the venv
            python when launched through the installed launcher.
        runner: Injectable command runner (for testing).
        log: Optional progress callback.
        ref: A specific release to switch to (e.g. ``"v0.1.0"``). May be any
            git ref — a tag, branch, or commit. When given, the source is a git
            checkout, the ref is fetched and checked out, and the package is
            reinstalled with ``--force-reinstall`` so switching to an *older*
            release is not refused as a downgrade.

    Returns:
        An :class:`UpdateResult` describing the version transition.

    Raises:
        UpdateError: if the source can't be resolved or a step fails.
    """
    runner = runner or _default_runner
    emit = log or (lambda _m: None)
    python = python or sys.executable

    src = _resolve_source(source)
    is_git = (src / ".git").exists()

    if ref is not None:
        return _switch_to_ref(
            src, ref, python=python, runner=runner, emit=emit, is_git=is_git
        )

    emit(f"Updating from source: {src}")

    do_pull = is_git if pull is None else pull
    git_updated = False
    if do_pull:
        if not is_git:
            raise UpdateError(
                f"{src} is not a git checkout; cannot pull. Re-run with "
                "--no-pull to install the source as-is."
            )
        emit("Fetching the latest release (git pull --ff-only) ...")
        proc = _git(src, ["pull", "--ff-only"], runner)
        if proc.returncode != 0:
            raise UpdateError(f"git pull failed:\n{(proc.stdout or '').strip()}")
        git_updated = "up to date" not in (proc.stdout or "").lower()

    previous = __version__

    emit("Installing the new version in place (pip install --upgrade) ...")
    proc = runner([python, "-m", "pip", "install", "--upgrade", str(src)], src)
    if proc.returncode != 0:
        raise UpdateError(f"pip install failed:\n{(proc.stdout or '').strip()}")

    new_version = _installed_version(python, runner)
    emit("Update complete. Your scan history and settings are preserved.")
    return UpdateResult(
        previous_version=previous,
        new_version=new_version,
        source=src,
        git_updated=git_updated,
    )


def _switch_to_ref(
    src: Path,
    ref: str,
    *,
    python: str,
    runner: CommandRunner,
    emit: Callable[[str], None],
    is_git: bool,
) -> UpdateResult:
    """Check out a specific release ref and reinstall it in place.

    Uses ``--force-reinstall`` so switching to an older release is installed
    rather than refused as a downgrade. The previous installation is replaced
    atomically by pip; nothing is deleted up front.
    """
    if not is_git:
        raise UpdateError(
            f"{src} is not a git checkout, so it cannot switch to release "
            f"{ref!r}. A git checkout is required to select a specific version."
        )

    # Make sure the requested tag/commit is available locally, then verify it.
    emit(f"Fetching release refs to locate {ref!r} ...")
    _git(src, ["fetch", "--tags", "--quiet"], runner)
    if not _ref_exists(src, ref, runner):
        available = list_releases(src, runner=runner)
        hint = f" Available releases: {', '.join(available)}." if available else ""
        raise UpdateError(f"release {ref!r} was not found.{hint}")

    previous = __version__

    emit(f"Switching source to release {ref} (git checkout) ...")
    proc = _git(src, ["checkout", "--quiet", ref], runner)
    if proc.returncode != 0:
        raise UpdateError(
            f"git checkout of {ref!r} failed:\n{(proc.stdout or '').strip()}"
        )

    emit(f"Installing release {ref} in place (pip install --force-reinstall) ...")
    proc = runner(
        [python, "-m", "pip", "install", "--force-reinstall", str(src)], src
    )
    if proc.returncode != 0:
        raise UpdateError(f"pip install failed:\n{(proc.stdout or '').strip()}")

    new_version = _installed_version(python, runner)
    emit("Switch complete. Your scan history and settings are preserved.")
    return UpdateResult(
        previous_version=previous,
        new_version=new_version,
        source=src,
        git_updated=True,
        ref=ref,
    )
