#!/usr/bin/env python3
"""sfx.py — find sound effects across your SFX folders (VS_SFX_DIRS in config.env).

  vs sfx list              all sounds, grouped by folder
  vs sfx find whoosh       sounds whose path contains every given word
  vs sfx path pop          print the best match path (for cues.json)

cues.json (consumed by mix.py): [[time_s, "name-or-path", peak_db], …]
A bare name resolves through this same search, so ["1.2", "pop", -16] works.
"""
import os
import sys
from pathlib import Path

VS_HOME = Path(os.environ.get("VS_HOME", Path.home() / ".video-studio"))
EXTS = (".wav", ".mp3", ".ogg", ".flac", ".m4a")


def sfx_dirs() -> list[Path]:
    raw = str(VS_HOME / "sfx")
    cfg = VS_HOME / "config.env"
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            if line.startswith("VS_SFX_DIRS="):
                raw = line.split("=", 1)[1].strip().strip('"')
    return [Path(p).expanduser() for p in raw.split(":") if p and Path(p).expanduser().exists()]


def all_sounds() -> list[Path]:
    return sorted(f for d in sfx_dirs() for f in d.rglob("*") if f.suffix.lower() in EXTS)


def find(words: list[str]) -> list[Path]:
    words = [w.lower() for w in words]
    hits = [f for f in all_sounds() if all(w in str(f).lower() for w in words)]
    # exact stem first, then shorter names (usually the cleaner, generic sound)
    return sorted(hits, key=lambda f: (f.stem.lower() != "-".join(words), len(f.stem)))


def resolve(name: str) -> Path | None:
    p = Path(name).expanduser()
    if p.exists():
        return p
    hits = find(name.replace("-", " ").split()) or find([name])
    return hits[0] if hits else None


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__); return
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "list":
        for d in sfx_dirs():
            files = [f for f in d.rglob("*") if f.suffix.lower() in EXTS]
            print(f"{d}  ({len(files)})")
            for f in sorted(files)[:200]:
                print("   ", f.relative_to(d))
    elif cmd == "find":
        for f in find(args)[:40]:
            print(f)
    elif cmd == "path":
        r = resolve(" ".join(args))
        if not r:
            sys.exit(f"no sound matches {args}")
        print(r)
    else:
        sys.exit(f"unknown: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main()
