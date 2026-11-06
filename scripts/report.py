#!/usr/bin/env python3
"""report.py — a bug report about video-studio, written to a file for you to read first.

  vs report [--note "what you did, what you expected, what happened"] [--last 10] [--out report.md]

Collects: skill version, OS, tool versions, `vs doctor`, the last N errors from ~/.video-studio/logs/errors.log.
Secrets (keys.env values, tokens) and your home folder path are scrubbed. Nothing is sent anywhere —
read the file, then attach it to an issue at https://github.com/mike43-ai/video-studio/issues if you want.
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import time
from pathlib import Path

from vslib import LOGS, scrub

HERE = Path(__file__).resolve().parent


def sh(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return (p.stdout + p.stderr).strip()
    except Exception as e:  # noqa: BLE001 — a report must never crash
        return f"(failed: {e})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--note", default="")
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--out", type=Path, default=Path(f"vs-report-{time.strftime('%Y%m%d-%H%M%S')}.md"))
    a = ap.parse_args()
    log = LOGS / "errors.log"
    errors = log.read_text(encoding="utf-8").splitlines()[-a.last:] if log.exists() else []
    parts = [
        "# video-studio bug report", f"_{time.strftime('%Y-%m-%d %H:%M:%S')}_", "",
        "## What happened", a.note or "(describe: what you ran, what you expected, what you got)", "",
        "## Versions",
        f"- skill: {sh(['git', '-C', str(HERE.parent), 'describe', '--always', '--dirty'])}",
        f"- OS: {platform.system()} {platform.release()} {platform.machine()}",
        f"- ffmpeg: {sh(['ffmpeg', '-version']).splitlines()[0] if sh(['ffmpeg', '-version']) else '?'}",
        f"- node: {sh(['node', '--version'])}", "",
        "## vs doctor", "```", sh([str(HERE / "vs"), "doctor"]), "```", "",
        f"## Last {len(errors)} error(s)", "```", *errors, "```",
    ]
    a.out.write_text(scrub("\n".join(parts)) + "\n", encoding="utf-8")
    print(f"✓ {a.out} — read it first; nothing was sent. Issues: https://github.com/mike43-ai/video-studio/issues")


if __name__ == "__main__":
    main()
