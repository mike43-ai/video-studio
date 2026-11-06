#!/usr/bin/env python3
"""image.py — make pictures for a video, and cut flat backgrounds out of them.

  vs image ls                                   engines and whether they are ready
  vs image create "<prompt>" -o plate.png [--aspect 16:9|9:16|1:1|4:3|3:4] [--ref style.png …]
                  [--engine gemini] [--model gemini-2.5-flash-image] --paid-ok
  vs image cut <image> -o cutout.png [--color auto|#RRGGBB] [--tolerance 28] [--feather 1.5]

create  Gemini image model (PAID, ≈ $0.04 per image — ask the user first, then pass --paid-ok).
        Same prompt + refs + aspect → served from the library, no second charge (--fresh to force).
        Tip for cutouts: ask for "on a flat pure #00FF00 background", then `vs image cut`.
cut     Removes a flat background colour connected to the image border (flood fill, so the same
        colour INSIDE the subject is kept), soft edge, de-spills the key colour. Free, offline.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from vslib import die, get_key, http, lib_copy_out, lib_get, lib_put, paid_gate, require_key

GEMINI_MODEL = "gemini-2.5-flash-image"


def create_gemini(prompt: str, refs: list[Path], aspect: str, model: str) -> bytes:
    key = require_key("gemini")
    parts: list[dict] = [{"text": prompt}]
    for r in refs:
        mime = "image/png" if r.suffix.lower() == ".png" else "image/jpeg"
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(r.read_bytes()).decode()}})
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}}}
    raw = http(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
               data=json.dumps(body).encode(), headers={"x-goog-api-key": key, "Content-Type": "application/json"},
               timeout=180)
    r = json.loads(raw)
    for c in r.get("candidates", []):
        for p in c.get("content", {}).get("parts", []):
            d = p.get("inlineData") or p.get("inline_data")
            if d and d.get("data"):
                return base64.b64decode(d["data"])
    reason = (r.get("candidates") or [{}])[0].get("finishReason") or r.get("promptFeedback")
    die(f"Gemini returned no image (reason: {reason})")
    return b""


def cut(src: Path, out: Path, color: str, tol: float, feather: float) -> dict:
    import numpy as np
    from PIL import Image, ImageFilter
    from scipy import ndimage

    img = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    h, w, _ = img.shape
    border = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    if color == "auto":
        key = np.median(border, axis=0)
    else:
        c = color.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        key = np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)
    dist = np.sqrt(((img - key) ** 2).sum(axis=2))
    near = dist < tol * 2.55 * 1.2
    lab, _ = ndimage.label(near)
    edge_labels = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(edge_labels))
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    a_img = Image.fromarray(alpha)
    if feather > 0:
        a_img = a_img.filter(ImageFilter.GaussianBlur(feather))
    alpha = np.asarray(a_img).astype(np.float32) / 255
    # de-spill: on semi-transparent edge pixels pull the key colour out
    edge = (alpha > 0) & (alpha < 1)
    rgb = img.copy()
    if edge.any():
        a = alpha[edge][:, None]
        rgb[edge] = np.clip((img[edge] - key * (1 - a)) / np.maximum(a, 0.05), 0, 255)
    rgba = np.dstack([rgb, alpha * 255]).astype(np.uint8)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba).save(out)
    return {"key": "#%02x%02x%02x" % tuple(int(v) for v in key), "removed": round(float(bg.mean()), 3), "size": [w, h]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["ls", "create", "cut"])
    ap.add_argument("what", nargs="?")
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--engine", default="gemini", choices=["gemini"])
    ap.add_argument("--model", default=GEMINI_MODEL)
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--ref", type=Path, action="append", default=[])
    ap.add_argument("--paid-ok", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--color", default="auto")
    ap.add_argument("--tolerance", type=float, default=28)
    ap.add_argument("--feather", type=float, default=1.5)
    a = ap.parse_args()

    if a.cmd == "ls":
        print(f"  {'✓' if get_key('gemini') else '○'} gemini   {GEMINI_MODEL} · PAID ≈ $0.04/image · vs keys set gemini")
        print("  ✓ cut      flat-background removal · free, offline")
        print("  (free alternative for a person/object on a real photo: vs matte <image>)")
        return
    if not a.what:
        die("missing prompt / image")
    if a.cmd == "cut":
        if not Path(a.what).exists():
            die(f"not found: {a.what}")
        out = a.out or Path(a.what).with_name(Path(a.what).stem + "-cut.png")
        r = cut(Path(a.what), out, a.color, a.tolerance, a.feather)
        print(f"✓ {out} · key {r['key']} · {r['removed']:.0%} of the frame removed")
        return

    import hashlib
    spec = {"engine": a.engine, "model": a.model, "prompt": a.what, "aspect": a.aspect,
            "refs": [hashlib.sha1(r.read_bytes()).hexdigest()[:16] for r in a.ref]}  # content, not name
    out = a.out or Path("image.png")
    hit = None if a.fresh else lib_get("image", spec)
    if hit:
        print(f"✓ {lib_copy_out(hit, out)} (from library {hit.stem}, no charge)")
        return
    paid_gate("Gemini image", "≈ $0.04 per image", a.paid_ok)
    data = create_gemini(a.what, a.ref, a.aspect, a.model)
    import io
    from PIL import Image
    out.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(data))  # always write exactly -o, in the format its extension names
    (img.convert("RGB") if out.suffix.lower() in (".jpg", ".jpeg") else img).save(out)
    stored = lib_put("image", spec, out, {"cost_note": "≈ $0.04"})
    print(f"✓ {out} · library {stored.stem}")


if __name__ == "__main__":
    sys.exit(main())
