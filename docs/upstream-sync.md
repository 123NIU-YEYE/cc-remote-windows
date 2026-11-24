# Following upstream

This fork follows [muggle-stack/cc-remote](https://github.com/muggle-stack/cc-remote)
by cherry-picking each upstream commit onto `main` with `git cherry-pick -x`.
The recorded upstream ID lets the next sync skip commits that are already here.

## Sync

Start a new branch from the fork's latest `main` with a clean tracked tree.
Below, the fork's remote is named `github`:

```powershell
git fetch github
git switch -c sync/upstream-YYYYMMDD github/main
.venv\Scripts\python.exe scripts\sync-upstream.py
.venv\Scripts\python.exe scripts\sync-upstream.py --apply
```

The first command adds the `upstream` remote when it is missing, fetches it with
tags and lists the upstream commits that this branch does not have yet. `--apply`
cherry-picks them in order.

When a commit conflicts, the script stops. Conflicts usually touch Windows
adaptations: keep the Windows behavior and take the upstream change around it.
Then run `git add` and `git cherry-pick --continue`, and start the script again
for the remaining commits. A change that was already ported by hand becomes an
empty commit that still records its upstream ID.

## How pending commits are found

The script starts from the upstream tag that matches `__version__` in
`cc_remote/__init__.py`, then skips commits named in a
`(cherry picked from commit …)` line on this branch. Picking upstream's
`chore(release): prepare vX.Y.Z` commit moves the starting point forward.

- Do not change `__version__` separately from upstream.
- Upstream merge commits are listed and skipped; take their changes by hand.
- If you squash a sync into one commit, end it exactly at an upstream release
  tag and pick that release commit, or keep one cherry-pick line per upstream
  commit in the message.

## Checks before pushing

- Commits marked `[protocol]` touch the wire protocol. If `PROTOCOL_VERSION`
  changes, deploy Relay, Web and every Wrapper together.
- On Windows, run the tests of the files the sync touched, the
  `tests/test_windows_*.py` suite, Ruff and the Web gate from
  [AGENTS.md](../AGENTS.md#commit-and-pr-gate) (build, reliability and history
  browser tests, lint). Run pytest with `PYTHONUTF8=1` and
  `PYTHON_DOTENV_DISABLED=1`.
- The complete Python suite is a Linux gate: CI runs it on push. Native Windows
  still fails many Unix-only tests, so compare against the previous `main`
  rather than expecting zero failures. See
  [Windows adaptation notes](windows-upstream-migration.md).
- A checkout change does not reach a running Wrapper. Activate a release through
  the [deployment workflow](../.agents/skills/cc-remote-deploy/SKILL.md).
