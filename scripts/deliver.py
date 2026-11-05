#!/usr/bin/env python3
"""deliver.py — put the master mix under the rendered video and verify the result.

  vs deliver render.mp4 mix.wav -o final.mp4

Copies the video stream (no re-encode), encodes audio AAC 256 kb/s 48 kHz,
then measures the FINAL file (not the inputs) and prints: duration of each
stream, resolution, fps, integrated LUFS, true peak. Warns when loudness is
off target by > 1 LU or audio/video lengths differ by > 0.1 s.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True)
    if out.returncode:
        sys.exit(out.stderr)
    return json.loads(out.stdout)


def loudness(path: Path) -> tuple[float, float]:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a",
                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    summary = p.stderr[p.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    return (float(i.group(1)) if i else float("nan"), float(tp.group(1)) if tp else float("nan"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("audio", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("final.mp4"))
    ap.add_argument("--target", type=float, default=-14.0)
    a = ap.parse_args()

    p = subprocess.run(["ffmpeg", "-hide_banner", "-y", "-i", str(a.video), "-i", str(a.audio),
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                        "-ar", "48000", "-movflags", "+faststart", "-shortest", str(a.out)],
                       capture_output=True, text=True)
    if p.returncode:
        sys.exit(p.stderr[-2000:])

    info = probe(a.out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    au = next(s for s in info["streams"] if s["codec_type"] == "audio")
    vd, ad = float(v.get("duration", 0)), float(au.get("duration", 0))
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    lufs, tp = loudness(a.out)
    print(f"✓ {a.out}")
    print(f"  video  {v['width']}x{v['height']} @ {num / den:.2f} fps · {vd:.2f}s · {v['codec_name']}")
    print(f"  audio  {au['codec_name']} {au.get('sample_rate')} Hz · {ad:.2f}s")
    print(f"  loudness {lufs:.1f} LUFS integrated · true peak {tp:.1f} dBFS")
    if abs(lufs - a.target) > 1:
        print(f"  ⚠ loudness off target ({a.target} LUFS) — re-run vs mix")
    if abs(vd - ad) > 0.1:
        print(f"  ⚠ audio/video length differ by {abs(vd - ad):.2f}s — check timeline duration vs mix --timeline")


if __name__ == "__main__":
    main()
