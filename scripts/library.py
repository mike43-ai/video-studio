#!/usr/bin/env python3
"""library.py — generated assets kept for reuse (~/.video-studio/library/<kind>/<id>.<ext> + .json).

Every generator (image create, music make, sfx make) stores its result here keyed by
its full spec, so asking for the same thing again costs nothing and returns the same file.

  vs library ls [kind] [--json]      kinds: image, music, sfx  newest first
  vs library get <id> [-o file]      copy an asset out (default: ./<id>.<ext>)
  vs library path <id>               print the absolute path
  vs library sheet [kind] [-o sheet.jpg]   contact sheet of the image assets
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from vslib import LIBRARY, contact_sheet, die, emit, lib_copy_out


def entries(kind: str | None = None) -> list[dict]:
    out = []
    if not LIBRARY.exists():
        return out
    for meta in LIBRARY.glob(f"{kind or '*'}/*.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            m["path"] = str(meta.parent / m["file"])
            out.append(m)
        except (ValueError, KeyError):
            continue
    return sorted(out, key=lambda m: m.get("created", ""), reverse=True)


def find(asset_id: str) -> dict:
    hits = [e for e in entries() if e["id"].startswith(asset_id)]
    if not hits:
        die(f"no library asset {asset_id}")
    return hits[0]


def main() -> None:
    a = sys.argv[1:]
    as_json = "--json" in a
    a = [x for x in a if x != "--json"]
    out = None
    if "-o" in a:
        i = a.index("-o"); out = Path(a[i + 1]); del a[i:i + 2]
    cmd = a[0] if a else "ls"
    if cmd in ("-h", "--help"):
        print(__doc__); return
    if cmd == "ls":
        es = entries(a[1] if len(a) > 1 else None)

        def human(es):
            print(f"{LIBRARY}  ({len(es)} assets)")
            for e in es:
                spec = e.get("spec", {})
                desc = spec.get("prompt") or spec.get("text") or json.dumps(spec, ensure_ascii=False)
                print(f"  {e['id']}  {e['kind']:<6} {e.get('created', '')}  {desc[:70]}")
        emit(es, as_json, human)
    elif cmd in ("get", "path"):
        if len(a) < 2:
            die(f"usage: vs library {cmd} <id>")
        e = find(a[1])
        if cmd == "path":
            print(e["path"]); return
        dst = lib_copy_out(Path(e["path"]), out or Path(Path(e["path"]).name))
        print(f"✓ {dst}")
    elif cmd == "sheet":
        imgs = [e["path"] for e in entries(a[1] if len(a) > 1 else None)
                if Path(e["path"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        print(f"✓ {contact_sheet(imgs, out or Path('library-sheet.jpg'))} ({len(imgs)} images)")
    else:
        die(f"unknown: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main()
