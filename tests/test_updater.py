from __future__ import annotations

import subprocess

import pytest

from gaming import updater
from gaming.updater import UpdateError, run_update


def _make_source(tmp_path):
    """Create a minimal 'checkout' with a pyproject.toml."""
    src = tmp_path / "gaming"
    src.mkdir()
    (src / "pyproject.toml").write_text("[project]\nname='gaming'\n", encoding="utf-8")
    return src


class _Recorder:
    """Injectable command runner that records calls and returns scripted output."""

    def __init__(self, responses=None):
        self.calls: list[list[str]] = []
        self._responses = responses or {}

    def __call__(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        # Match a canned response by a substring key, else succeed silently.
        for key, (code, out) in self._responses.items():
            if key in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, code, stdout=out)
        return subprocess.CompletedProcess(cmd, 0, stdout="")


def test_update_installs_without_pull_for_non_git(tmp_path):
    src = _make_source(tmp_path)  # no .git -> no pull by default
    runner = _Recorder({"print(gaming.__version__)": (0, "0.3.0\n")})
    result = run_update(source=src, python="py", runner=runner)

    assert result.new_version == "0.3.0"
    assert result.git_updated is False
    # A pip upgrade against the source ran; no git pull happened.
    joined = [" ".join(c) for c in runner.calls]
    assert any("pip install --upgrade" in c for c in joined)
    assert not any(c.startswith("git ") for c in joined)


def test_update_pulls_when_git_checkout(tmp_path):
    src = _make_source(tmp_path)
    (src / ".git").mkdir()
    runner = _Recorder(
        {
            "pull --ff-only": (0, "Updating 111..222\n"),
            "print(gaming.__version__)": (0, "0.3.0\n"),
        }
    )
    result = run_update(source=src, python="py", runner=runner)

    joined = [" ".join(c) for c in runner.calls]
    assert any("git -C" in c and "pull --ff-only" in c for c in joined)
    assert result.git_updated is True
    # git pull must run before pip install.
    pull_idx = next(i for i, c in enumerate(joined) if "pull --ff-only" in c)
    pip_idx = next(i for i, c in enumerate(joined) if "pip install --upgrade" in c)
    assert pull_idx < pip_idx


def test_update_no_pull_skips_git_even_for_checkout(tmp_path):
    src = _make_source(tmp_path)
    (src / ".git").mkdir()
    runner = _Recorder({"print(gaming.__version__)": (0, "0.3.0\n")})
    run_update(source=src, python="py", pull=False, runner=runner)

    assert not any(c and c[0] == "git" for c in runner.calls)


def test_update_reports_already_up_to_date(tmp_path, monkeypatch):
    src = _make_source(tmp_path)
    (src / ".git").mkdir()
    monkeypatch.setattr(updater, "__version__", "0.3.0")
    runner = _Recorder(
        {
            "pull --ff-only": (0, "Already up to date.\n"),
            "print(gaming.__version__)": (0, "0.3.0\n"),
        }
    )
    result = run_update(source=src, python="py", runner=runner)
    assert result.git_updated is False
    assert result.changed is False
    assert result.previous_version == "0.3.0"


def test_update_raises_when_git_pull_fails(tmp_path):
    src = _make_source(tmp_path)
    (src / ".git").mkdir()
    runner = _Recorder({"pull --ff-only": (1, "fatal: conflict\n")})
    with pytest.raises(UpdateError, match="git pull failed"):
        run_update(source=src, python="py", runner=runner)


def test_update_raises_when_pip_fails(tmp_path):
    src = _make_source(tmp_path)
    runner = _Recorder({"pip install --upgrade": (1, "ERROR: boom\n")})
    with pytest.raises(UpdateError, match="pip install failed"):
        run_update(source=src, python="py", runner=runner)


def test_update_raises_on_missing_source(tmp_path):
    with pytest.raises(UpdateError, match="does not exist"):
        run_update(source=tmp_path / "nope", python="py", runner=_Recorder())


def test_update_raises_on_non_checkout(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()  # exists but has no pyproject.toml
    with pytest.raises(UpdateError, match="no pyproject.toml"):
        run_update(source=empty, python="py", runner=_Recorder())


def test_update_no_pull_on_non_git_when_explicitly_requested(tmp_path):
    src = _make_source(tmp_path)  # no .git
    with pytest.raises(UpdateError, match="not a git checkout"):
        run_update(source=src, python="py", pull=True, runner=_Recorder())


def test_default_source_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(updater.SOURCE_ENV, str(tmp_path))
    assert updater.default_source_dir() == tmp_path


def test_default_source_dir_detects_venv_parent(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / ".venv").mkdir(parents=True)
    (project / "pyproject.toml").write_text("x", encoding="utf-8")
    monkeypatch.delenv(updater.SOURCE_ENV, raising=False)
    monkeypatch.setattr(updater.sys, "prefix", str(project / ".venv"))
    assert updater.default_source_dir() == project


def test_log_callback_receives_progress(tmp_path):
    src = _make_source(tmp_path)
    runner = _Recorder({"print(gaming.__version__)": (0, "0.3.0\n")})
    messages: list[str] = []
    run_update(source=src, python="py", runner=runner, log=messages.append)
    assert any("pip install" in m for m in messages)
    assert any("preserved" in m for m in messages)


def test_list_releases_returns_tags_newest_first(tmp_path):
    src = _make_source(tmp_path)
    (src / ".git").mkdir()
    runner = _Recorder(
        {"tag --list": (0, "v0.3.0\nv0.2.0\nv0.1.0\n")}
    )
    releases = updater.list_releases(src, runner=runner)
    assert releases == ["v0.3.0", "v0.2.0", "v0.1.0"]
    # Tags are refreshed from the remote before listing.
    joined = [" ".join(c) for c in runner.calls]
    assert any("fetch --tags" in c for c in joined)


def test_list_releases_empty_for_non_git(tmp_path):
    src = _make_source(tmp_path)  # no .git
    runner = _Recorder()
    assert updater.list_releases(src, runner=runner) == []
    # No git commands should have run.
    assert not any(c and c[0] == "git" for c in runner.calls)


def test_switch_to_ref_uses_force_reinstall(tmp_path, monkeypatch):
    src = _make_source(tmp_path)
    (src / ".git").mkdir()
    monkeypatch.setattr(updater, "__version__", "0.3.0")
    runner = _Recorder(
        {
            "rev-parse": (0, ""),  # ref exists
            "print(gaming.__version__)": (0, "0.1.0\n"),
        }
    )
    result = run_update(source=src, python="py", runner=runner, ref="v0.1.0")

    assert result.ref == "v0.1.0"
    assert result.previous_version == "0.3.0"
    assert result.new_version == "0.1.0"
    assert result.git_updated is True
    assert result.changed is True

    joined = [" ".join(c) for c in runner.calls]
    # Downgrade is installed via --force-reinstall, not --upgrade.
    assert any("pip install --force-reinstall" in c for c in joined)
    assert not any("--upgrade" in c for c in joined)
    # The ref is checked out before it is reinstalled.
    checkout_idx = next(i for i, c in enumerate(joined) if "checkout --quiet v0.1.0" in c)
    pip_idx = next(i for i, c in enumerate(joined) if "--force-reinstall" in c)
    assert checkout_idx < pip_idx


def test_switch_to_ref_raises_when_ref_missing(tmp_path):
    src = _make_source(tmp_path)
    (src / ".git").mkdir()
    runner = _Recorder(
        {
            "rev-parse": (1, ""),  # ref does not exist
            "tag --list": (0, "v0.3.0\nv0.2.0\n"),
        }
    )
    with pytest.raises(UpdateError, match="was not found.*v0.3.0"):
        run_update(source=src, python="py", runner=runner, ref="v9.9.9")


def test_switch_to_ref_requires_git_checkout(tmp_path):
    src = _make_source(tmp_path)  # no .git
    with pytest.raises(UpdateError, match="not a git checkout"):
        run_update(source=src, python="py", runner=_Recorder(), ref="v0.1.0")


def test_switch_to_ref_raises_when_checkout_fails(tmp_path):
    src = _make_source(tmp_path)
    (src / ".git").mkdir()
    runner = _Recorder(
        {
            "rev-parse": (0, ""),
            "checkout --quiet": (1, "error: pathspec\n"),
        }
    )
    with pytest.raises(UpdateError, match="git checkout"):
        run_update(source=src, python="py", runner=runner, ref="v0.1.0")
