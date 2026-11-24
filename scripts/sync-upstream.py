#!/usr/bin/env python3
"""List or cherry-pick upstream cc-remote commits that this fork has not taken.

The fork follows https://github.com/muggle-stack/cc-remote by cherry-picking
each upstream commit with ``-x``. The starting point is the upstream release
tag matching this branch's ``cc_remote.__version__``; commits picked since then
are recognised by their "cherry picked from commit" line. A conflict stops the
run for manual resolution, because it usually touches Windows adaptations.
See docs/upstream-sync.md.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

UPSTREAM_URL = "https://github.com/muggle-stack/cc-remote.git"
PROTOCOL_FILES = {"cc_remote/protocol.py", "web/src/protocol.ts"}
_PICKED = re.compile(r"^\(cherry picked from commit ([0-9a-f]{40})\)$", re.M)
_VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.M)


class SyncError(RuntimeError):
    pass


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if check and result.returncode:
        raise SyncError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def ensure_remote(remote: str) -> None:
    if remote not in git("remote").split():
        git("remote", "add", remote, UPSTREAM_URL)
        print(f"Added remote {remote}: {UPSTREAM_URL}")
        return
    url = git("remote", "get-url", remote).strip()
    if not url.rstrip("/").removesuffix(".git").lower().endswith("muggle-stack/cc-remote"):
        raise SyncError(f"remote {remote} is {url}, not muggle-stack/cc-remote; choose another --remote")


def base_tag(upstream: str) -> str:
    match = _VERSION.search(git("show", "HEAD:cc_remote/__init__.py"))
    if match is None:
        raise SyncError("cannot read __version__ from cc_remote/__init__.py")
    tag = f"v{match[1]}"
    if not git("rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}", check=False).strip():
        raise SyncError(f"no tag {tag}; fetch upstream tags or check this branch's version")
    if subprocess.run(["git", "merge-base", "--is-ancestor", tag, upstream]).returncode:
        raise SyncError(f"{tag} is not part of {upstream}")
    return tag


def pending(tag: str, upstream: str) -> tuple[list[str], list[str]]:
    picked = set(_PICKED.findall(git("log", "--format=%B", f"{upstream}..HEAD")))
    merges = set(git("rev-list", "--merges", f"{tag}..{upstream}").split())
    commits = [commit for commit in git("rev-list", "--reverse", f"{tag}..{upstream}").split()
               if commit not in picked]
    return [c for c in commits if c not in merges], [c for c in commits if c in merges]


def describe(commit: str) -> tuple[str, bool]:
    line = git("show", "-s", "--format=%h %cs %s", commit).strip()
    files = set(git("diff-tree", "--no-commit-id", "--name-only", "-r", commit).split())
    return line, bool(files & PROTOCOL_FILES)


def apply(commits: list[str]) -> int:
    if git("rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD", check=False).strip():
        raise SyncError("a cherry-pick is in progress; finish it with --continue or --abort first")
    if git("status", "--porcelain", "--untracked-files=no").strip():
        raise SyncError("commit or stash tracked changes first")
    for commit in commits:
        print("Picking", describe(commit)[0])
        # An already-ported change becomes an empty commit that still records
        # the upstream ID, so it is not listed again.
        if subprocess.run(["git", "cherry-pick", "-x", "--allow-empty", "--empty=keep", commit]).returncode:
            conflicted = git("diff", "--name-only", "--diff-filter=U").split()
            print("\nStopped at a conflict:", *conflicted, sep="\n  ")
            print("\nResolve it while keeping the Windows adaptation, then run"
                  "\n  git add <files> && git cherry-pick --continue"
                  "\nand start this script again for the remaining commits."
                  "\nTo leave the current commit out: git cherry-pick --abort")
            return 1
    print(f"\nPicked {len(commits)} commit(s). Run the checks in docs/upstream-sync.md before pushing.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remote", default="upstream", help="upstream remote name (added if missing)")
    parser.add_argument("--branch", default="master", help="upstream branch")
    parser.add_argument("--no-fetch", action="store_true", help="use the refs fetched last time")
    parser.add_argument("--apply", action="store_true", help="cherry-pick them onto the current branch")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")
    try:
        ensure_remote(args.remote)
        if not args.no_fetch:
            git("fetch", "--tags", args.remote)
        upstream = f"{args.remote}/{args.branch}"
        tag = base_tag(upstream)
        commits, merges = pending(tag, upstream)
        print(f"{len(commits)} upstream commit(s) after {tag} are not on this branch:")
        protocol = False
        for commit in commits:
            line, changes_protocol = describe(commit)
            protocol |= changes_protocol
            print(f"  {line}{'  [protocol]' if changes_protocol else ''}")
        for merge in merges:
            print(f"  skipped merge {describe(merge)[0]}; pick its changes by hand")
        if protocol:
            print("\n[protocol] touches the wire protocol. If PROTOCOL_VERSION changes, deploy"
                  " Relay, Web and every Wrapper together.")
        if not commits:
            return 0
        if not args.apply:
            print("\nRun again with --apply to cherry-pick them onto the current branch.")
            return 0
        return apply(commits)
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
