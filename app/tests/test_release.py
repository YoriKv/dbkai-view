"""release.sh, run against a throwaway repository and a local remote."""

import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

RELEASE_SH = Path(__file__).resolve().parents[2] / "release.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash") or not shutil.which("git"),
    reason="release.sh needs bash and git",
)

NOTES = "# Changelog\n\n## v0.1.2 - unreleased\n\n- A fix.\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A checkout at 0.1.1 with notes for 0.1.2, pushed to a bare origin."""
    # Nobody's global config: no signing, no hooks, a fixed identity.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    work = tmp_path / "work"
    (work / "app" / "dbkai").mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    shutil.copy(RELEASE_SH, work / "release.sh")
    (work / "app" / "dbkai" / "__init__.py").write_text('__version__ = "0.1.1"\n')
    (work / "CHANGELOG.md").write_text(NOTES)
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "start")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "origin", "main")
    return work


def _release(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "release.sh", "-y", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_release_bumps_stamps_tags_and_pushes(repo):
    result = _release(repo)
    assert result.returncode == 0, result.stderr
    version = (repo / "app" / "dbkai" / "__init__.py").read_text()
    assert version == '__version__ = "0.1.2"\n'
    today = date.today().isoformat()
    assert f"## v0.1.2 - {today}\n" in (repo / "CHANGELOG.md").read_text()
    assert "refs/tags/v0.1.2" in _git(repo, "ls-remote", "--tags", "origin")
    assert not list(repo.rglob("*.release-tmp"))


def test_a_failed_commit_keeps_uncommitted_notes(repo):
    # With --force, notes still being written stay in the working tree; a
    # failing commit must put them back as they were, not as HEAD has them.
    edited = NOTES + "- Another fix, not committed yet.\n"
    (repo / "CHANGELOG.md").write_text(edited)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    result = _release(repo, "--force")
    assert result.returncode != 0
    assert (repo / "CHANGELOG.md").read_text() == edited
    version = (repo / "app" / "dbkai" / "__init__.py").read_text()
    assert version == '__version__ = "0.1.1"\n'
