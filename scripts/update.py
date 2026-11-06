#!/usr/bin/env python3
"""update.py — update the video-studio skill itself (it is a git clone).

  vs update --check        how many new commits are on the remote, and what they are
  vs update                pull them (fast-forward only; refuses if you edited the skill's files)
  vs update --rollback     go back to the version before the last update
After an update: vs doctor (new tools may need `bash scripts/install.sh` again — it is safe to re-run).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from vslib import VS_HOME, die

SKILL = Path(__file__).resolve().parent.parent
STATE = VS_HOME / "update.json"


def git(*args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(SKILL), *args], capture_output=True, text=True)
    if check and p.returncode:
        die(f"git {' '.join(args)} failed: {p.stderr.strip()[:400]}")
    return p.stdout.strip()


def main() -> None:
    a = sys.argv[1:]
    if "-h" in a or "--help" in a:
        print(__doc__); return
    if not (SKILL / ".git").exists():
        die(f"{SKILL} is not a git clone — reinstall with: git clone https://github.com/mike43-ai/video-studio")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    head = git("rev-parse", "--short", "HEAD")

    if "--rollback" in a:
        if not STATE.exists():
            die("no previous update recorded")
        prev = json.loads(STATE.read_text())["previous"]
        if git("status", "--porcelain"):
            die("the skill folder has local edits — commit or stash them first")
        git("reset", "--hard", prev)
        print(f"✓ rolled back {head} → {prev}")
        return

    git("fetch", "--quiet", "origin")
    behind = int(git("rev-list", "--count", f"HEAD..origin/{branch}") or 0)
    if "--check" in a:
        print(f"video-studio {head} on {branch}: {behind} new commit(s)")
        if behind:
            print(git("log", "--oneline", f"HEAD..origin/{branch}"))
        return
    if not behind:
        print(f"✓ up to date ({head})"); return
    if git("status", "--porcelain", "--untracked-files=no"):
        die("the skill folder has local edits — commit or stash them, then vs update")
    git("merge", "--ff-only", f"origin/{branch}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"previous": head, "at": time.strftime("%Y-%m-%d %H:%M:%S")}))
    print(f"✓ updated {head} → {git('rev-parse', '--short', 'HEAD')} ({behind} commit(s)). Undo: vs update --rollback")
    print("  then: vs doctor")


if __name__ == "__main__":
    main()
