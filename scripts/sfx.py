#!/usr/bin/env python3
"""sfx.py — find sound effects across your SFX folders (VS_SFX_DIRS in config.env).

  vs sfx list              all sounds, grouped by folder
  vs sfx find whoosh       sounds whose path contains every given word
  vs sfx path pop          print the best match path (for cues.json)
  vs sfx catalog           named sounds available to cues (same as list)
  vs sfx need pop whoosh   check that every name resolves (before a mix); exit 1 if one is missing
  vs sfx make "<prompt>"   generate one (free synth / ElevenLabs) — see `vs sfx make -h`
  vs sfx defaults          (re)build the free synth kit
  vs sfx cc0 find <words> [--limit 15]   CC0 recordings on Openverse (Freesound and others)
  vs sfx cc0 get <N|id> [--name rain]    download into ~/.video-studio/sfx/cc0/ + credits.json

cues.json (consumed by mix.py): [[time_s, "name-or-path", peak_db], …]
A bare name resolves through this same search, so ["1.2", "pop", -16] works.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from vslib import CACHE, die, download, http_json

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


LAST = CACHE / "cc0-last-search.json"


def cc0(args: list[str]) -> None:
    """CC0 sound search/download through the Openverse API (no key needed)."""
    if not args or args[0] not in ("find", "get"):
        die("usage: vs sfx cc0 find <words> | vs sfx cc0 get <N|id> [--name NAME]")
    limit, name = 15, None
    if "--limit" in args:
        i = args.index("--limit"); limit = int(args[i + 1]); del args[i:i + 2]
    if "--name" in args:
        i = args.index("--name"); name = args[i + 1]; del args[i:i + 2]
    if args[0] == "find":
        r = http_json("https://api.openverse.org/v1/audio/", params={
            "q": " ".join(args[1:]), "license": "cc0", "page_size": limit}, cache_ttl=86400)
        hits = [{"id": h["id"], "title": h.get("title") or "", "creator": h.get("creator") or "",
                 "duration": round((h.get("duration") or 0) / 1000, 1), "url": h["url"],
                 "type": h.get("filetype") or Path(h["url"]).suffix.lstrip("."), "source": h.get("source"),
                 "page": h.get("foreign_landing_url")} for h in r.get("results", [])]
        LAST.parent.mkdir(parents=True, exist_ok=True)
        LAST.write_text(json.dumps(hits), encoding="utf-8")
        for n, h in enumerate(hits, 1):
            print(f"{n:>3}. {h['title'][:50]:<50} {h['duration']:>6}s {h['type']:<4} {h['source']} · {h['creator'][:20]}")
        print(f"\n{len(hits)} CC0 result(s). Get one: vs sfx cc0 get <N> --name <short-name>")
        return
    hits = json.loads(LAST.read_text(encoding="utf-8")) if LAST.exists() else []
    key = args[1] if len(args) > 1 else ""
    h = hits[int(key) - 1] if key.isdigit() and 0 < int(key) <= len(hits) else next((x for x in hits if x["id"] == key), None)
    if not h:
        die("pick a number from the last `vs sfx cc0 find`")
    stem = re.sub(r"[^\w]+", "-", (name or h["title"] or h["id"]).lower()).strip("-")[:40]
    ext = Path(h["url"].split("?")[0]).suffix or f".{h['type']}"
    out_dir = VS_HOME / "sfx" / "cc0"
    dst = download(h["url"], out_dir / f"{stem}{ext}")
    cf = out_dir / "credits.json"
    credits = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else []
    credits = [c for c in credits if c["file"] != dst.name] + [{"file": dst.name, "title": h["title"], "creator": h["creator"],
                                                               "license": "CC0", "source_url": h["page"]}]
    cf.write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {dst} (CC0) — use in cues as \"{stem}\"")


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
    elif cmd == "catalog":
        names = sorted({f.stem for f in all_sounds()})
        print(f"{len(names)} named sounds: " + ", ".join(names))
    elif cmd == "need":
        missing = [n for n in args if not resolve(n)]
        for n in args:
            print(f"  {'✗' if n in missing else '✓'} {n}  {resolve(n) or ''}")
        if missing:
            sys.stdout.flush()
            die(f"missing: {', '.join(missing)} — vs sfx make \"<description>\" or vs sfx cc0 find …")
    elif cmd == "cc0":
        cc0(args)
    elif cmd == "path":
        r = resolve(" ".join(args))
        if not r:
            sys.exit(f"no sound matches {args}")
        print(r)
    else:
        sys.exit(f"unknown: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main()
