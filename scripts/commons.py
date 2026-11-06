#!/usr/bin/env python3
"""commons.py — archival photos from Wikimedia Commons, reusable licenses only, with credits.

  vs commons search "<query>" [--limit 20] [--min-width 1200] [--json]
  vs commons get <"File:Name.jpg" | N from the last search> [-o images/] [--width 1920]

Only CC0 / Public domain / CC BY / CC BY-SA files are listed (NC, ND, fair-use are dropped).
`get` downloads (scaled to --width) and appends an entry to <out>/credits.json:
  {file, title, author, license, license_url, source_url, credit}
Put the `credit` lines in the video's end card or description — CC BY/BY-SA REQUIRE it.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

from vslib import CACHE, die, download, emit, http_json

API = "https://commons.wikimedia.org/w/api.php"
LAST = CACHE / "commons-last-search.json"
OK_LICENSE = re.compile(r"^(cc0|pd|public domain|cc[- ]by(-sa)?([- ]\d(\.\d)?)?)", re.I)


def clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def info_of(page: dict) -> dict | None:
    ii = (page.get("imageinfo") or [{}])[0]
    meta = ii.get("extmetadata", {})
    lic = clean(meta.get("LicenseShortName", {}).get("value", ""))
    if not OK_LICENSE.match(lic) or re.search(r"\bnc\b|\bnd\b|fair use", lic, re.I):
        return None
    author = clean(meta.get("Artist", {}).get("value", "")) or "unknown"
    return {"title": page["title"], "width": ii.get("width"), "height": ii.get("height"), "mime": ii.get("mime"),
            "license": lic, "license_url": meta.get("LicenseUrl", {}).get("value", ""),
            "author": author[:120], "description": clean(meta.get("ImageDescription", {}).get("value", ""))[:200],
            "date": clean(meta.get("DateTimeOriginal", {}).get("value", ""))[:40],
            "source_url": ii.get("descriptionurl"), "url": ii.get("url"), "thumb": ii.get("thumburl")}


def query(params: dict, width: int) -> list[dict]:
    r = http_json(API, params={"action": "query", "format": "json", "prop": "imageinfo",
                               "iiprop": "url|size|mime|extmetadata", "iiurlwidth": width, **params}, cache_ttl=86400)
    pages = sorted(r.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    return [i for p in pages if (i := info_of(p))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["search", "get"])
    ap.add_argument("what")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--min-width", type=int, default=1000)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("-o", "--out", type=Path, default=Path("images"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "search":
        hits = query({"generator": "search", "gsrsearch": f"{a.what} filetype:bitmap", "gsrnamespace": 6,
                      "gsrlimit": min(50, a.limit * 2)}, a.width)
        hits = [h for h in hits if (h["width"] or 0) >= a.min_width][:a.limit]
        LAST.parent.mkdir(parents=True, exist_ok=True)
        LAST.write_text(json.dumps(hits, ensure_ascii=False), encoding="utf-8")

        def human(hits):
            for n, h in enumerate(hits, 1):
                print(f"{n:>3}. {h['title']}\n     {h['width']}x{h['height']} · {h['license']} · {h['author'][:50]} · {h['date']}")
                if h["description"]:
                    print(f"     {h['description'][:110]}")
            print(f"\n{len(hits)} reusable result(s). Get one: vs commons get <N> -o images/")
        emit(hits, a.json, human)
        return

    if a.what.isdigit():
        if not LAST.exists():
            die("no previous search — run vs commons search first")
        hits = json.loads(LAST.read_text(encoding="utf-8"))
        if not 1 <= int(a.what) <= len(hits):
            die(f"pick 1…{len(hits)}")
        title = hits[int(a.what) - 1]["title"]
    else:
        title = a.what if a.what.startswith("File:") else f"File:{a.what}"
    found = query({"titles": title}, a.width)
    if not found:
        die(f"{title}: not found or its license is not reusable")
    h = found[0]
    url = h["thumb"] if h["thumb"] and (h["width"] or 0) > a.width else h["url"]
    ext = Path(url.split("?")[0]).suffix or ".jpg"
    name = re.sub(r"[^\w\-.]+", "_", title.removeprefix("File:"))[:80]
    name = str(Path(name).with_suffix(ext))
    dst = download(url, a.out / name)
    credits_file = a.out / "credits.json"
    credits = json.loads(credits_file.read_text(encoding="utf-8")) if credits_file.exists() else []
    credits = [c for c in credits if c["file"] != name]
    credits.append({"file": name, "title": title, "author": h["author"], "license": h["license"],
                    "license_url": h["license_url"], "source_url": h["source_url"],
                    "credit": f"{h['author']} / Wikimedia Commons / {h['license']}"})
    credits_file.write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {dst} · {h['license']} · credit: {credits[-1]['credit']}")


if __name__ == "__main__":
    main()
