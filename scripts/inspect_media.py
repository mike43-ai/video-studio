#!/usr/bin/env python3
"""inspect_media.py — facts about a media file, or a contact sheet of a folder of images.

  vs inspect <file> [--probe] [--only audio,frame,cuts,sheet] [--out-dir DIR] [--frames 12]
                    [--cut-threshold 10] [--silence-db -35] [--silence-min 300] [--no-sheet] [--json]
  vs inspect <folder> [--out-dir DIR] [--cols 6]

  --probe       streams only (fast)
  audio         integrated LUFS, true peak, LRA, silences ≥ --silence-min ms under --silence-db
  frame         letterbox / pillarbox (cropdetect) and mean brightness
  cuts          scene cuts (scdet score ≥ --cut-threshold, 0–100)
  sheet         contact sheet of --frames evenly spaced frames → <out-dir>/<name>-sheet.jpg
"""
import argparse
import re
import tempfile
from pathlib import Path

from vslib import contact_sheet, die, display_size, emit, ff, loudness, probe

IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def streams(path: Path) -> dict:
    info = probe(path)
    out = {"file": str(path), "duration": round(float(info["format"].get("duration") or 0), 3),
           "size_mb": round(int(info["format"].get("size", 0)) / 1e6, 2), "streams": []}
    for s in info["streams"]:
        d = {"type": s["codec_type"], "codec": s.get("codec_name")}
        if s["codec_type"] == "video":
            num, den = (int(x) for x in s.get("avg_frame_rate", "0/1").split("/")) if "/" in s.get("avg_frame_rate", "") else (0, 1)
            dw, dh = display_size(s)
            d.update(width=dw, height=dh, fps=round(num / den, 3) if den else 0,
                     pix_fmt=s.get("pix_fmt"), frames=int(s.get("nb_frames", 0) or 0))
        elif s["codec_type"] == "audio":
            d.update(sample_rate=int(s.get("sample_rate", 0)), channels=s.get("channels"))
        if s.get("duration"):
            d["duration"] = round(float(s["duration"]), 3)
        out["streams"].append(d)
    return out


def silences(path: Path, db: float, min_ms: int) -> list:
    err = ff(["-i", path, "-map", "0:a:0", "-af", f"silencedetect=n={db}dB:d={min_ms / 1000}", "-f", "null", "-"], check=False)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", err)]
    dur = streams(path)["duration"]
    return [[round(max(0, s), 3), round(ends[i] if i < len(ends) else dur, 3)] for i, s in enumerate(starts)]


def frame_facts(path: Path, w: int, h: int) -> dict:
    err = ff(["-i", path, "-map", "0:v:0", "-vf", "fps=2,cropdetect=limit=24:round=2:reset=0,signalstats",
              "-f", "null", "-"], check=False)
    crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", err)
    out = {"letterbox": None}
    if crops:
        cw, ch, cx, cy = (int(v) for v in crops[-1])
        if ch < h - 4 or cw < w - 4:
            out["letterbox"] = {"content": [cw, ch, cx, cy], "bars_top_bottom": h - ch, "bars_left_right": w - cw}
    return out


def cuts(path: Path, threshold: float) -> list:
    err = ff(["-i", path, "-map", "0:v:0", "-vf", f"scdet=threshold={threshold}", "-f", "null", "-"], check=False)
    return [{"t": round(float(t), 3), "score": round(float(s), 1)}
            for s, t in re.findall(r"lavfi\.scd\.score: ([\d.]+), lavfi\.scd\.time: ([\d.]+)", err)]


def sheet(path: Path, dur: float, n: int, out: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i in range(n):
            t = dur * (i + 0.5) / n
            f = Path(tmp) / f"f{i:03d}.jpg"
            ff(["-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-q:v", "3", "-y", f], check=False)
            if f.exists():
                frames.append(f)
        return contact_sheet(frames, out, cols=min(6, n))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--only", default="audio,frame,cuts,sheet")
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--cut-threshold", type=float, default=10)
    ap.add_argument("--silence-db", type=float, default=-35)
    ap.add_argument("--silence-min", type=int, default=300)
    ap.add_argument("--no-sheet", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.target.is_dir():
        imgs = sorted(p for p in a.target.iterdir() if p.suffix.lower() in IMG)
        out = (a.out_dir or a.target) / f"{a.target.name}-sheet.jpg"
        print(f"✓ {contact_sheet(imgs, out, cols=a.cols)} ({len(imgs)} images)")
        return
    if not a.target.exists():
        die(f"not found: {a.target}")

    r = streams(a.target)
    only = set(a.only.split(","))
    if not a.probe:
        has_a = any(s["type"] == "audio" for s in r["streams"])
        v = next((s for s in r["streams"] if s["type"] == "video" and s["codec"] not in ("png", "mjpeg")), None)
        if has_a and "audio" in only:
            r["audio"] = {**loudness(a.target), "silences": silences(a.target, a.silence_db, a.silence_min)}
        if v and "frame" in only:
            r["frame"] = frame_facts(a.target, *display_size(next(s for s in probe(a.target)["streams"] if s["codec_type"] == "video")))
        if v and "cuts" in only:
            r["cuts"] = cuts(a.target, a.cut_threshold)
        if v and "sheet" in only and not a.no_sheet:
            out_dir = a.out_dir or a.target.parent
            r["sheet"] = str(sheet(a.target, r["duration"], a.frames, out_dir / f"{a.target.stem}-sheet.jpg"))

    def human(r):
        print(f"{r['file']} · {r['duration']}s · {r['size_mb']} MB")
        for s in r["streams"]:
            extra = (f"{s['width']}x{s['height']} @ {s['fps']} fps {s.get('pix_fmt', '')}" if s["type"] == "video"
                     else f"{s.get('sample_rate')} Hz · {s.get('channels')} ch")
            print(f"  {s['type']:<6} {s['codec']:<8} {extra} · {s.get('duration', '?')}s")
        if "audio" in r:
            au = r["audio"]
            print(f"  loudness {au['lufs']} LUFS · true peak {au['true_peak']} dBFS · LRA {au['lra']} LU")
            print(f"  silences ≥{a.silence_min} ms: {len(au['silences'])} " +
                  " ".join(f"{s}–{e}" for s, e in au["silences"][:12]))
        if r.get("frame", {}).get("letterbox"):
            print(f"  letterbox: {r['frame']['letterbox']}")
        if "cuts" in r:
            print(f"  cuts (≥{a.cut_threshold}): {len(r['cuts'])} " + " ".join(str(c["t"]) for c in r["cuts"][:20]))
        if "sheet" in r:
            print(f"  sheet → {r['sheet']}")
    emit(r, a.json, human)


if __name__ == "__main__":
    main()
