#!/usr/bin/env python3
"""redact.py — blur QR codes, faces and marked regions (bank details, customer data) on a clip or photo,
and a gate so a build never uses an unchecked file.

  vs redact scan <video|image> [-o findings.json] [--every 0.5] [--faces]
        finds QR codes (decoded text shown) and, with --faces, faces → time ranges + boxes
  vs redact apply <video|image> -o clean.mp4 [--from findings.json] [--box x,y,w,h@t0-t1 …]
                  [--mode blur|pixelate|black] [--pad 0.15]
        --box adds regions you found by eye (text with account numbers, names, phone numbers…);
        without @t0-t1 the box covers the whole clip
  vs redact verify <clean file> [--from findings.json]   re-scan: exit 1 if any QR still decodes
  vs redact mark-clean <clean file>     the user looked at it and it is clean → record its hash
  vs redact gate <file>                 exit 1 unless the file was marked clean and is unchanged

Automatic detection covers QR codes and faces only — text (card/account numbers, names, addresses)
must be found by LOOKING at frames (vs inspect --only sheet) and added with --box. Always verify by eye.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vslib import die, probe  # noqa: E402

IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def scan(src: Path, every: float, want_faces: bool) -> list[dict]:
    from vision import faces, frames, iou, qrcodes
    found: list[dict] = []
    for t, f in frames(src, every):
        hits = [{"kind": "qr", "box": q["box"], "text": q["text"]} for q in qrcodes(f)]
        if want_faces:
            hits += [{"kind": "face", "box": b} for b in faces(f, 0.04)]
        for h in hits:
            prev = next((r for r in found if r["kind"] == h["kind"] and r["t1"] >= t - every * 1.5
                         and iou(r["box"], h["box"]) > 0.3), None)
            if prev:  # same object still on screen → extend its range and grow its box
                x0, y0 = min(prev["box"][0], h["box"][0]), min(prev["box"][1], h["box"][1])
                x1 = max(prev["box"][0] + prev["box"][2], h["box"][0] + h["box"][2])
                y1 = max(prev["box"][1] + prev["box"][3], h["box"][1] + h["box"][3])
                prev.update(t1=round(t, 3), box=[x0, y0, x1 - x0, y1 - y0])
                if h.get("text"):
                    prev["text"] = h["text"]
            else:
                found.append({**h, "t0": round(t, 3), "t1": round(t, 3)})
    for r in found:  # cover the sampling gap on both sides
        r["t0"] = max(0.0, round(r["t0"] - every, 3))
        r["t1"] = round(r["t1"] + every, 3)
    return found


def parse_box(spec: str) -> dict:
    box, _, rng = spec.partition("@")
    x, y, w, h = (int(float(v)) for v in box.split(","))
    r = {"kind": "manual", "box": [x, y, w, h]}
    if rng:
        t0, t1 = rng.split("-")
        r.update(t0=float(t0), t1=float(t1))
    return r


def apply(src: Path, out: Path, regions: list[dict], mode: str, pad: float) -> None:
    from vision import frames
    _, first = next(frames(src, 1.0))  # the size ffmpeg filters see (rotation applied), same as scan's boxes
    H, W = first.shape[:2]
    is_img = src.suffix.lower() in IMG
    boxes = []
    for r in regions:
        x, y, w, h = r["box"]
        px, py = int(w * pad), int(h * pad)
        x, y = max(0, x - px), max(0, y - py)
        w, h = min(W - x, w + 2 * px) // 2 * 2, min(H - y, h + 2 * py) // 2 * 2
        if w < 4 or h < 4:
            die(f"region {r['box']} is outside the {W}x{H} frame — check the coordinates")
        boxes.append((r, x, y, w, h))
    fc, last = [], "[0:v]"
    n = len(boxes)
    if n:
        fc.append(f"[0:v]split={n + 1}[base]" + "".join(f"[s{i}]" for i in range(n)))
        last = "[base]"
    for i, (r, x, y, w, h) in enumerate(boxes):
        if mode == "black":
            eff = f"drawbox=x=0:y=0:w={w}:h={h}:color=black:t=fill"
        elif mode == "pixelate":
            eff = f"scale={max(2, w // 16)}:{max(2, h // 16)}:flags=neighbor,scale={w}:{h}:flags=neighbor"
        else:
            rad = max(2, min(w, h) // 5)  # chroma planes are half size → their radius must stay < min(w,h)/4
            eff = f"boxblur=luma_radius={rad}:luma_power=4:chroma_radius={max(1, rad // 2)}:chroma_power=4"
        fc.append(f"[s{i}]crop={w}:{h}:{x}:{y},{eff}[r{i}]")
        en = "" if is_img or "t0" not in r else f":enable='between(t,{r['t0']},{r['t1']})'"
        fc.append(f"{last}[r{i}]overlay={x}:{y}{en}[o{i}]")
        last = f"[o{i}]"
    if not regions:
        fc = ["[0:v]null[o]"]; last = "[o]"
    args = ["ffmpeg", "-v", "error", "-i", str(src), "-filter_complex", ";".join(fc), "-map", last]
    if is_img:
        args += ["-frames:v", "1"]
    else:
        args += ["-map", "0:a?", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy"]
    p = subprocess.run([*args, "-y", str(out)], capture_output=True, text=True)
    if p.returncode:
        die(f"ffmpeg failed:\n{p.stderr[-1200:]}")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["scan", "apply", "verify", "mark-clean", "gate"])
    ap.add_argument("src", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--from", dest="findings", type=Path)
    ap.add_argument("--box", action="append", default=[])
    ap.add_argument("--mode", default="blur", choices=["blur", "pixelate", "black"])
    ap.add_argument("--pad", type=float, default=0.15)
    ap.add_argument("--every", type=float, default=0.5)
    ap.add_argument("--faces", action="store_true")
    a = ap.parse_args()
    if not a.src.exists():
        die(f"not found: {a.src}")
    marker = a.src.with_name(a.src.name + ".redact.json")

    if a.cmd == "scan":
        found = scan(a.src, a.every, a.faces)
        out = a.out or a.src.with_name(a.src.stem + "-findings.json")
        out.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
        for r in found:
            print(f"  {r['kind']:<5} {r['t0']:>7.2f}–{r['t1']:<7.2f} box {r['box']}" + (f"  “{r['text'][:50]}”" if r.get("text") else ""))
        print(f"✓ {out} · {len(found)} finding(s). Text/numbers are NOT auto-detected — look at frames and add --box.")
    elif a.cmd == "apply":
        regions = json.loads(a.findings.read_text(encoding="utf-8")) if a.findings else []
        regions += [parse_box(b) for b in a.box]
        if not regions:
            die("nothing to redact: give --from findings.json and/or --box x,y,w,h[@t0-t1]")
        out = a.out or a.src.with_name(a.src.stem + "-redacted" + a.src.suffix)
        apply(a.src, out, regions, a.mode, a.pad)
        done = {"blur": "blurred", "pixelate": "pixelated", "black": "blacked out"}[a.mode]
        print(f"✓ {out} · {len(regions)} region(s) {done} · next: vs redact verify {out}, look, then mark-clean")
    elif a.cmd == "verify":
        left = [r for r in scan(a.src, a.every, False) if r["kind"] == "qr" and r.get("text")]
        if left:
            for r in left:
                print(f"  ✗ QR still readable {r['t0']}–{r['t1']}s at {r['box']}: “{r['text'][:40]}”")
            sys.exit(1)
        print("✓ no readable QR codes left. Faces/text: check by eye before `vs redact mark-clean`.")
    elif a.cmd == "mark-clean":
        try:
            probe(a.src)
        except SystemExit:
            die(f"{a.src.name} is not a readable image/video — redo vs redact apply")
        if any(r["kind"] == "qr" and r.get("text") for r in scan(a.src, a.every, False)):
            die(f"{a.src.name} still has a readable QR code — not marking it clean")
        marker.write_text(json.dumps({"sha256": sha(a.src), "at": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
        print(f"✓ {a.src.name} marked clean")
    elif a.cmd == "gate":
        if not marker.exists():
            print(f"✗ {a.src.name} was never checked — vs redact scan/apply/verify, look, then mark-clean")
            sys.exit(1)
        if json.loads(marker.read_text(encoding="utf-8"))["sha256"] != sha(a.src):
            print(f"✗ {a.src.name} changed after it was marked clean — check it again")
            sys.exit(1)
        print(f"✓ {a.src.name} is marked clean and unchanged")


if __name__ == "__main__":
    main()
