"""scripts/sync-upstream.py against throwaway repositories. No network access."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync-upstream.py"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=ENV, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def commit(repo, files, message):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def sync(fork, *args):
    return subprocess.run([sys.executable, str(SCRIPT), "--no-fetch", *args], cwd=fork, env=ENV,
                          capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def repos(tmp_path):
    upstream = tmp_path / "muggle-stack" / "cc-remote"
    upstream.mkdir(parents=True)
    git(upstream, "init", "-q", "-b", "master")
    commit(upstream, {"cc_remote/__init__.py": '__version__ = "1.0.0"\n', "a.txt": "one\n"}, "release 1.0.0")
    git(upstream, "tag", "v1.0.0")
    fork = tmp_path / "fork"
    fork.mkdir()
    git(fork, "init", "-q", "-b", "main")
    git(fork, "remote", "add", "upstream", upstream.as_posix())
    git(fork, "fetch", "-q", "--tags", "upstream")
    git(fork, "reset", "-q", "--hard", "v1.0.0")
    commit(fork, {"windows.txt": "adaptation\n"}, "feat(windows): adapt")
    return upstream, fork


def refresh(fork):
    git(fork, "fetch", "-q", "--tags", "upstream")


def test_lists_marks_protocol_and_applies_each_commit_once(repos):
    upstream, fork = repos
    commit(upstream, {"b.txt": "b\n"}, "fix: b")
    commit(upstream, {"cc_remote/protocol.py": "PROTOCOL_VERSION = 2\n"}, "feat: protocol")
    refresh(fork)
    listed = sync(fork)
    assert listed.returncode == 0, listed.stderr
    assert "2 upstream commit(s) after v1.0.0" in listed.stdout
    assert "fix: b" in listed.stdout and "feat: protocol  [protocol]" in listed.stdout
    assert "PROTOCOL_VERSION changes" in listed.stdout
    applied = sync(fork, "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert "cherry picked from commit" in git(fork, "log", "-1", "--format=%B")
    assert (fork / "windows.txt").exists() and (fork / "b.txt").exists()
    assert "0 upstream commit(s)" in sync(fork).stdout


def test_conflict_stops_for_manual_resolution(repos):
    upstream, fork = repos
    commit(fork, {"a.txt": "windows\n"}, "feat(windows): change a")
    commit(upstream, {"a.txt": "upstream\n"}, "fix: change a")
    refresh(fork)
    stopped = sync(fork, "--apply")
    assert stopped.returncode == 1
    assert "Stopped at a conflict" in stopped.stdout and "a.txt" in stopped.stdout
    again = sync(fork, "--apply")
    assert again.returncode == 1 and "cherry-pick is in progress" in again.stderr
    git(fork, "cherry-pick", "--abort")
    assert (fork / "a.txt").read_text(encoding="utf-8") == "windows\n"


def test_already_ported_change_is_recorded_and_not_listed_again(repos):
    upstream, fork = repos
    commit(fork, {"d.txt": "d\n"}, "fix: port d by hand")
    commit(upstream, {"d.txt": "d\n"}, "fix: d")
    refresh(fork)
    assert sync(fork, "--apply").returncode == 0
    assert "0 upstream commit(s)" in sync(fork).stdout


def test_dirty_tree_and_unrelated_remote_are_refused(repos):
    upstream, fork = repos
    commit(upstream, {"b.txt": "b\n"}, "fix: b")
    refresh(fork)
    (fork / "windows.txt").write_text("edited\n", encoding="utf-8")
    dirty = sync(fork, "--apply")
    assert dirty.returncode == 1 and "tracked changes" in dirty.stderr
    git(fork, "remote", "add", "other", (upstream.parent / "elsewhere").as_posix())
    unrelated = sync(fork, "--remote", "other")
    assert unrelated.returncode == 1 and "not muggle-stack/cc-remote" in unrelated.stderr
