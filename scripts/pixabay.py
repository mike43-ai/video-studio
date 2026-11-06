#!/usr/bin/env python3
"""pixabay.py — stock photos and video clips from Pixabay (free key: vs keys set pixabay).

  vs pixabay search "<query>" [--type photo|video|illustration|vector] [--orientation horizontal|vertical]
                    [--limit 20] [--lang en] [--json]
  vs pixabay get <id | N from the last search> [--type photo|video] [-o media/] [--size large|medium|small]

Pixabay Content License: free for commercial use, no attribution required (credit is still written to
<out>/credits.json). Not allowed: selling the file as-is, implying endorsement by people shown.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from vslib import CACHE, die, download, emit, http_json, require_key

LAST = CACHE / "pixabay-last-search.json"


def search(q: str, kind: str, orientation: str | None, limit: int, lang: str, key: str) -> list[dict]:
    url = "https://pixabay.com/api/videos/" if kind == "video" else "https://pixabay.com/api/"
    params = {"key": key, "q": q, "per_page": max(3, min(200, limit)), "safesearch": "true", "lang": lang}
    if kind != "video":
        params["image_type"] = kind
    if orientation:
        params["orientation"] = orientation
    r = http_json(url, params=params, cache_ttl=86400)
    out = []
    for h in r.get("hits", [])[:limit]:
        if kind == "video":
            v = h["videos"]
            out.append({"id": h["id"], "type": "video", "tags": h["tags"], "duration": h.get("duration"),
                        "user": h["user"], "page": h["pageURL"],
                        "sizes": {k: {"url": v[k]["url"], "w": v[k]["width"], "h": v[k]["height"]}
                                  for k in ("large", "medium", "small") if v.get(k, {}).get("url")}})
        else:
            out.append({"id": h["id"], "type": "photo", "tags": h["tags"], "user": h["user"], "page": h["pageURL"],
                        "w": h["imageWidth"], "h": h["imageHeight"],
                        "sizes": {"large": {"url": h["largeImageURL"]}, "medium": {"url": h["webformatURL"]},
                                  "small": {"url": h["previewURL"]}}})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["search", "get"])
    ap.add_argument("what")
    ap.add_argument("--type", default="photo")
    ap.add_argument("--orientation")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--size", default="large")
    ap.add_argument("-o", "--out", type=Path, default=Path("media"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    key = require_key("pixabay")

    if a.cmd == "search":
        hits = search(a.what, a.type, a.orientation, a.limit, a.lang, key)
        LAST.parent.mkdir(parents=True, exist_ok=True)
        LAST.write_text(json.dumps(hits), encoding="utf-8")

        def human(hits):
            for n, h in enumerate(hits, 1):
                size = (f"{h['duration']}s " + " ".join(f"{k}:{s['w']}x{s['h']}" for k, s in h["sizes"].items())
                        if h["type"] == "video" else f"{h['w']}x{h['h']}")
                print(f"{n:>3}. id {h['id']} · {size} · {h['tags']} · by {h['user']}")
            print(f"\n{len(hits)} result(s). Get one: vs pixabay get <N> --type {a.type}")
        emit(hits, a.json, human)
        return

    hits = json.loads(LAST.read_text(encoding="utf-8")) if LAST.exists() else []
    if a.what.isdigit() and int(a.what) <= len(hits) and len(a.what) < 4:
        h = hits[int(a.what) - 1]
    else:
        h = next((x for x in hits if str(x["id"]) == a.what), None)
        if not h:
            url = "https://pixabay.com/api/videos/" if a.type == "video" else "https://pixabay.com/api/"
            r = http_json(url, params={"key": key, "id": a.what})
            if not r.get("hits"):
                die(f"pixabay id {a.what} not found")
            raw = r["hits"][0]
            h = ({"id": raw["id"], "type": "video", "user": raw["user"], "page": raw["pageURL"],
                  "sizes": {k: {"url": raw["videos"][k]["url"]} for k in ("large", "medium", "small")
                            if raw["videos"].get(k, {}).get("url")}}
                 if a.type == "video" else
                 {"id": raw["id"], "type": "photo", "user": raw["user"], "page": raw["pageURL"],
                  "sizes": {"large": {"url": raw["largeImageURL"]}, "medium": {"url": raw["webformatURL"]}}})
    s = h["sizes"].get(a.size) or next(iter(h["sizes"].values()))
    ext = ".mp4" if h["type"] == "video" else (Path(s["url"].split("?")[0]).suffix or ".jpg")
    dst = download(s["url"], a.out / f"pixabay-{h['id']}{ext}")
    cf = a.out / "credits.json"
    credits = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else []
    credits = [c for c in credits if c["file"] != dst.name] + [
        {"file": dst.name, "author": h["user"], "license": "Pixabay Content License", "source_url": h["page"],
         "credit": f"{h['user']} / Pixabay"}]
    cf.write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {dst} (by {h['user']}, Pixabay Content License)")


if __name__ == "__main__":
    main()
