#!/usr/bin/env python3
"""Fail-closed publication retry for the Actions-only GitHub writer."""
import argparse
import subprocess
from pathlib import Path

PROTECTED_PREFIXES = ("state.json", "ledger/")


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=check)


def push_with_retry(repo, attempts=3, branch="main"):
    """Push HEAD to ``branch``, rebasing only over unrelated upstream commits."""
    repo = Path(repo)
    if not branch or branch.startswith("-"):
        raise ValueError("publication branch is invalid")
    remote_branch = f"origin/{branch}"
    for _ in range(attempts):
        pushed = _git(repo, "push", "origin", f"HEAD:{branch}", check=False)
        if pushed.returncode == 0:
            return True
        _git(repo, "fetch", "origin", branch)
        # Three-dot compares the merge base to upstream only. Two-dot would also
        # report this collector's own state/ledger changes and falsely block all
        # legitimate retry attempts.
        changed = _git(repo, "diff", "--name-only", f"HEAD...{remote_branch}").stdout.splitlines()
        if any(path == "state.json" or path.startswith("ledger/") for path in changed):
            return False
        rebased = _git(repo, "rebase", remote_branch, check=False)
        if rebased.returncode != 0:
            _git(repo, "rebase", "--abort", check=False)
            return False
    return False


def main():
    parser = argparse.ArgumentParser(description="Push a collector commit without overwriting protected state.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()
    if not push_with_retry(args.repo, args.attempts, args.branch):
        raise SystemExit("protected overlap, rebase conflict, or exhausted push retry budget")


if __name__ == "__main__":
    main()
