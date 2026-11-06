#!/usr/bin/env python3
"""matte.py — cut the person out of a video/photo, find their head, reframe around it.

  vs matte ls
  vs matte <video|image> -o cutout.webm|.mov|.png [--background-out plate.webm] [--quality balanced]
        transparent cutout (local u2net model via HyperFrames; downloads once, runs on CPU) — put text
        BEHIND the speaker: background video → text layer → this cutout on top
  vs matte heads <video|image> [-o heads.json] [--every 0.25]
        the main face per sample: [{t, x, y, w, h}] (smoothed), plus the head's median box
  vs matte reframe <video> -o vertical.mp4 [--aspect 9:16] [--heads heads.json] [--headroom 0.18]
        crop to the aspect following the head smoothly (talking head 16:9 → 9:16), audio kept
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vslib import die, probe  # noqa: E402


def heads(src: Path, every: float) -> dict:
    import numpy as np
    from vision import faces, frames
    raw = []
    W = H = n = 0
    for t, f in frames(src, every):
        n += 1
        H, W = f.shape[:2]
        fs = faces(f)
        if fs:
            raw.append({"t": round(t, 3), "box": fs[0]})
    if not raw:
        return {"size": [W, H], "track": [], "median": None}
    # smooth: median filter of 5 samples on each coordinate, then drop jumps > 40% of face width
    boxes = np.array([r["box"] for r in raw], dtype=float)
    sm = boxes.copy()
    for i in range(len(boxes)):
        sm[i] = np.median(boxes[max(0, i - 2):i + 3], axis=0)
    track = [{"t": r["t"], "x": int(b[0]), "y": int(b[1]), "w": int(b[2]), "h": int(b[3])} for r, b in zip(raw, sm)]
    med = [int(v) for v in np.median(sm, axis=0)]
    return {"size": [W, H], "track": track, "median": med, "coverage": round(len(raw) / max(1, n), 2)}


def reframe(src: Path, out: Path, aspect: str, hd: dict, headroom: float) -> None:
    import cv2
    import numpy as np
    aw, ah = (int(v) for v in aspect.split(":"))
    cap = cv2.VideoCapture(str(src))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    if W * ah > H * aw:  # source wider than target → crop width, full height
        cw, ch = int(H * aw / ah) // 2 * 2, H
    else:
        cw, ch = W, int(W * ah / aw) // 2 * 2
    track = hd["track"] or [{"t": 0, "x": W // 2, "y": H // 3, "w": 1, "h": 1}]
    ts = np.array([p["t"] for p in track])
    cx = np.array([p["x"] + p["w"] / 2 for p in track])
    cy = np.array([p["y"] + p["h"] / 2 for p in track])
    out.parent.mkdir(parents=True, exist_ok=True)
    # extra smoothing: exponential with ~0.6 s time constant so the camera glides, never jitters
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{cw}x{ch}", "-r", f"{fps}",
                            "-i", "-", "-i", str(src), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", "18",
                            "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
                            "-y", str(out)], stdin=subprocess.PIPE)
    sx, sy, i = None, None, 0
    alpha = 1 - np.exp(-1 / (0.6 * fps))
    while True:
        ok, f = cap.read()
        if not ok:
            break
        t = i / fps
        tx, ty = float(np.interp(t, ts, cx)), float(np.interp(t, ts, cy))
        sx = tx if sx is None else sx + alpha * (tx - sx)
        sy = ty if sy is None else sy + alpha * (ty - sy)
        x0 = int(min(max(0, sx - cw / 2), W - cw))
        y0 = int(min(max(0, sy - ch * headroom - ch * 0.15), H - ch))
        enc.stdin.write(np.ascontiguousarray(f[y0:y0 + ch, x0:x0 + cw]).tobytes())
        i += 1
    cap.release()
    enc.stdin.close()
    if enc.wait():
        die("ffmpeg encode failed")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("heads", "reframe", "ls"):
        cmd = sys.argv.pop(1)
    else:
        cmd = "cut"
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, nargs="?")
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--background-out", type=Path)
    ap.add_argument("--quality", default="balanced")
    ap.add_argument("--every", type=float, default=0.25)
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--heads", type=Path)
    ap.add_argument("--headroom", type=float, default=0.18)
    a = ap.parse_args()

    if cmd == "ls":
        print("  ✓ cut      HyperFrames remove-background (u2net, local CPU; first run downloads the model)")
        print("  ✓ heads    OpenCV face detection (offline)")
        print("  ✓ reframe  head-following crop (offline)")
        return
    if not a.src or not a.src.exists():
        die("source file missing")
    if cmd == "heads":
        r = heads(a.src, a.every)
        out = a.out or a.src.with_name(a.src.stem + "-heads.json")
        out.write_text(json.dumps(r, indent=1))
        print(f"✓ {out} · {len(r['track'])} face samples · median box {r['median']} · frame {r['size']}")
        if not r["track"]:
            print("  ⚠ no face found — reframe will hold the upper-middle of the frame")
        return
    if cmd == "reframe":
        hd = json.loads(a.heads.read_text()) if a.heads else heads(a.src, a.every)
        out = a.out or a.src.with_name(a.src.stem + "-" + a.aspect.replace(":", "x") + ".mp4")
        reframe(a.src, out, a.aspect, hd, a.headroom)
        v = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
        print(f"✓ {out} · {v['width']}x{v['height']} · head-tracked from {len(hd['track'])} samples")
        return
    out = a.out or a.src.with_name(a.src.stem + ("-cutout.png" if a.src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else "-cutout.webm"))
    env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
    hf = f"hyperframes@{os.environ.get('VS_HYPERFRAMES_VERSION', '0.8.79')}"
    cmdline = ["npx", "-y", hf, "remove-background", str(a.src), "-o", str(out), "--quality", a.quality]
    if a.background_out:
        cmdline += ["-b", str(a.background_out)]
    print("▸ removing background (local model; first run downloads it)")
    p = subprocess.run(cmdline, env=env)
    if p.returncode or not out.exists():
        die("remove-background failed (see output above)")
    print(f"✓ {out}" + (f" · background plate {a.background_out}" if a.background_out else ""))


if __name__ == "__main__":
    main()
