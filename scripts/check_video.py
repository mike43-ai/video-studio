#!/usr/bin/env python3
"""check_video.py — is a finished video ready to deliver? Measures, never trusts the inputs.

  vs check final.mp4 [--lufs -14] [--size 1080x1920] [--fps 30] [--script script.txt --lang vi]
                     [--platform tiktok|instagram-reels|youtube-shorts|universal|all] [--page comp]
                     [--cues cues.json] [--freeze-min 2] [--silence-min 2] [--motion] [--face x,y,w,h]
                     [--waive 3.0-4.5,…] [--out-dir DIR] [--json]
  vs check --platforms        list the safe-zone presets

Checks (✗ = blocks delivery, ⚠ = look at it):
  ✗ black picture ≥ 0.25 s (except a fade within the first/last 0.5 s) · ✗ frozen picture ≥ --freeze-min s (--motion: ≥ 1 s)
  ✗ single bad frames (flash/black/garbage frame between two similar frames)
  ✗ loudness off by > 1 LU · ✗ true peak > −1 dBTP · ⚠ silence ≥ --silence-min s
  ✗ audio/video length differ > 0.1 s · ✗ size/fps not as asked
  ⚠ speech vs --script: words missing/added (transcribes the final audio)
  ⚠ --cues: a cue whose sound does not rise ≥ 3 dB over what plays under it
  ⚠ safe zones: writes <name>-safezones.jpg (frames with the platform's UI boxes drawn) to LOOK at;
     --page also runs `hyperframes check` on the composition (layout overflow, contrast).
  --face draws the face box on the safe-zone frames so overlays covering it are easy to spot.
--waive start-end ranges are ignored (intentional black/freeze/silence).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from vslib import contact_sheet, die, display_size, emit, ff, loudness, probe

HERE = Path(__file__).resolve().parent
# Safe zones as fractions of the frame: areas the platform UI covers (top, bottom, left, right).
PLATFORMS = {
    "tiktok": {"top": 0.12, "bottom": 0.20, "left": 0.04, "right": 0.15},
    "instagram-reels": {"top": 0.14, "bottom": 0.22, "left": 0.04, "right": 0.12},
    "youtube-shorts": {"top": 0.10, "bottom": 0.18, "left": 0.04, "right": 0.14},
    "universal": {"top": 0.14, "bottom": 0.22, "left": 0.06, "right": 0.15},
}


def ranges(spec: str | None) -> list[tuple[float, float]]:
    out = []
    for r in (spec or "").split(","):
        if "-" in r:
            s, e = r.split("-", 1)
            out.append((float(s), float(e)))
    return out


def waived(s: float, e: float, w: list) -> bool:
    return any(ws <= s + 0.05 and e - 0.05 <= we for ws, we in w)


def intervals(err: str, key: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(rf"{key}_start: ?([\d.]+)", err)]
    ends = [float(x) for x in re.findall(rf"{key}_end: ?([\d.]+)", err)]
    return [(s, ends[i] if i < len(ends) else float("inf")) for i, s in enumerate(starts)]


def bad_frames(path: Path, fps: float) -> list[float]:
    """Frames that differ a lot from BOTH neighbours while the neighbours match each other."""
    w, h = 48, 27
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf", f"scale={w}:{h},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    raw, n = p.stdout, w * h
    frames = [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]

    def diff(a: bytes, b: bytes) -> float:
        return sum(abs(x - y) for x, y in zip(a, b)) / n
    out = []
    for i in range(1, len(frames) - 1):
        d_prev, d_next = diff(frames[i], frames[i - 1]), diff(frames[i], frames[i + 1])
        if d_prev > 25 and d_next > 25 and diff(frames[i - 1], frames[i + 1]) < 6:
            out.append(round(i / fps, 3))
    return out


def norm_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", re.sub(r"#.*", "", text).replace("*", "")).lower()
    return [w for w in re.sub(r"[^\w\s]", " ", text).split() if w]


def speech_vs_script(path: Path, script: Path, lang: str, tmp: Path) -> dict:
    wav = tmp / "speech.wav"
    ff(["-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-y", wav])
    words = tmp / "words.json"
    p = subprocess.run([str(HERE / "vs"), "transcribe", str(wav), "-o", str(words), "--lang", lang],
                       capture_output=True, text=True)
    if p.returncode or not words.exists():
        return {"error": (p.stderr or p.stdout)[-400:]}
    heard = norm_words(" ".join(w["w"] for w in json.loads(words.read_text(encoding="utf-8"))))
    want = norm_words(script.read_text(encoding="utf-8"))
    sm = difflib.SequenceMatcher(a=want, b=heard, autojunk=False)
    missing, extra = [], []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op in ("delete", "replace"):
            missing.append(" ".join(want[a0:a1]))
        if op in ("insert", "replace"):
            extra.append(" ".join(heard[b0:b1]))
    matched = sum(b.size for b in sm.get_matching_blocks())
    return {"match": round(matched / max(1, len(want)), 3), "missing": missing[:15], "extra": extra[:15]}


def cue_rise(path: Path, t: float) -> float:
    """dB the loudest 120 ms after t sits above the 300 ms before t, measured above 2.5 kHz
    (where clicks, pops and whooshes live and the voice is weaker), so a cue under speech still shows."""
    def peak(ss: float, d: float) -> float:
        err = ff(["-ss", f"{max(0, ss):.3f}", "-t", f"{d:.3f}", "-i", path, "-map", "0:a:0",
                  "-af", "highpass=f=2500,volumedetect", "-f", "null", "-"], check=False)
        m = re.search(r"max_volume: (-?[\d.]+) dB", err)
        return float(m.group(1)) if m else -91.0
    before = -91.0 if t < 0.1 else (peak(t - 0.35, 0.3) if t >= 0.35 else peak(0, max(0.05, t - 0.05)))
    return peak(t - 0.02, 0.14) - before


def safezone_sheet(path: Path, dur: float, W: int, H: int, zones: dict, face: str | None, out: Path, tmp: Path) -> Path:
    boxes = []
    z = zones
    boxes.append(f"drawbox=x=0:y=0:w={W}:h={int(H * z['top'])}:color=red@0.35:t=fill")
    boxes.append(f"drawbox=x=0:y={int(H * (1 - z['bottom']))}:w={W}:h={int(H * z['bottom'])}:color=red@0.35:t=fill")
    boxes.append(f"drawbox=x={int(W * (1 - z['right']))}:y=0:w={int(W * z['right'])}:h={H}:color=red@0.25:t=fill")
    boxes.append(f"drawbox=x=0:y=0:w={int(W * z['left'])}:h={H}:color=red@0.25:t=fill")
    if face:
        try:
            x, y, w, h = (int(float(v)) for v in face.split(","))
        except ValueError:
            die("--face needs x,y,w,h in pixels")
        boxes.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=yellow:t=6")
    frames = []
    for i in range(8):
        f = tmp / f"sz{i}.jpg"
        ff(["-ss", f"{dur * (i + 0.5) / 8:.3f}", "-i", path, "-frames:v", "1", "-vf", ",".join(boxes), "-q:v", "3", "-y", f],
           check=False)
        if f.exists():
            frames.append(f)
    return contact_sheet(frames, out, cols=4, cell=360)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path, nargs="?")
    ap.add_argument("--lufs", type=float, default=-14.0)
    ap.add_argument("--size")
    ap.add_argument("--fps", type=float)
    ap.add_argument("--script", type=Path)
    ap.add_argument("--lang", default="vi")
    ap.add_argument("--platform", default="universal")
    ap.add_argument("--platforms", action="store_true")
    ap.add_argument("--page", type=Path, help="composition folder (or its index.html) for hyperframes check")
    ap.add_argument("--cues", type=Path)
    ap.add_argument("--freeze-min", type=float, default=2.0)
    ap.add_argument("--silence-min", type=float, default=2.0)
    ap.add_argument("--motion", action="store_true", help="stricter: picture must move at least every second")
    ap.add_argument("--face")
    ap.add_argument("--waive")
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.platforms:
        for k, z in PLATFORMS.items():
            print(f"  {k:<16} UI covers top {z['top']:.0%} · bottom {z['bottom']:.0%} · right {z['right']:.0%} · left {z['left']:.0%}")
        return
    if not a.video or not a.video.exists():
        die("usage: vs check <video> …")

    info = probe(a.video)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    au = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not v:
        die("no video stream")
    dur = float(info["format"]["duration"])
    num, den = (int(x) for x in v["avg_frame_rate"].split("/"))
    fps = num / den if den else 30.0
    W, H = display_size(v)
    waive = ranges(a.waive)
    issues: list[dict] = []

    def add(level: str, what: str, detail: str = "", t: float | None = None):
        issues.append({"level": level, "check": what, "detail": detail, **({"t": round(t, 3)} if t is not None else {})})

    # size / fps / lengths
    if a.size and a.size != f"{W}x{H}":
        add("fail", "size", f"{W}x{H}, wanted {a.size}")
    if a.fps and abs(fps - a.fps) > 0.01:
        add("fail", "fps", f"{fps:.3f}, wanted {a.fps}")
    if not au:
        add("fail", "audio", "no audio stream")
    else:
        vd, ad = float(v.get("duration", dur)), float(au.get("duration", dur))
        if abs(vd - ad) > 0.1:
            add("fail", "length", f"video {vd:.2f}s vs audio {ad:.2f}s")

    # picture: black / frozen / single bad frames
    err = ff(["-i", a.video, "-map", "0:v:0", "-vf",
              f"blackdetect=d=0.25:pix_th=0.08,freezedetect=n=0.002:d={1.0 if a.motion else a.freeze_min}",
              "-f", "null", "-"], check=False)
    for s, e in intervals(err, "black"):
        e = min(e, dur)
        if (s < 0.05 and e <= 0.5) or (s >= dur - 0.5 and e >= dur - 0.05) or waived(s, e, waive):  # fade in/out
            continue
        add("fail", "black", f"{s:.2f}–{e:.2f}s", s)
    for s, e in intervals(re.sub(r"lavfi\.freezedetect\.freeze_", "freeze_", err), "freeze"):
        e = min(e, dur)
        if not waived(s, e, waive):
            add("fail", "frozen", f"{s:.2f}–{e:.2f}s ({e - s:.1f}s still)", s)
    for t in bad_frames(a.video, fps):
        if not waived(t, t, waive):
            add("fail", "bad frame", f"one odd frame at {t:.3f}s", t)

    # sound
    measured = {}
    if au:
        measured = loudness(a.video)
        if abs(measured["lufs"] - a.lufs) > 1:
            add("fail", "loudness", f"{measured['lufs']} LUFS, wanted {a.lufs} ±1")
        if measured["true_peak"] > -1.0:
            add("fail", "true peak", f"{measured['true_peak']} dBFS > −1")
        err = ff(["-i", a.video, "-map", "0:a:0", "-af", f"silencedetect=n=-45dB:d={a.silence_min}", "-f", "null", "-"],
                 check=False)
        for s, e in intervals(err, "silence"):
            e = min(e, dur)
            if not waived(s, e, waive):
                add("warn", "silence", f"{s:.2f}–{e:.2f}s", s)

    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        if a.script and au:
            r = speech_vs_script(a.video, a.script, a.lang, tmp)
            measured["speech"] = r
            if "error" in r:
                add("warn", "speech", f"transcription failed: {r['error']}")
            elif r["match"] < 0.9 or r["missing"]:
                add("warn", "speech", f"{r['match']:.0%} of script heard; missing: {r['missing'][:5]}; extra: {r['extra'][:5]}")
        if a.cues and au:
            for c in json.loads(a.cues.read_text(encoding="utf-8")):
                t, name = (c["t"], c.get("sound")) if isinstance(c, dict) else (c[0], c[1])
                t = float(t)
                if t > dur:
                    add("warn", "cue", f"{name} at {t:.2f}s is past the end", t); continue
                rise = cue_rise(a.video, t)
                if rise < 3:
                    add("warn", "cue", f"{name} at {t:.2f}s barely audible (+{rise:.1f} dB)", t)
        out_dir = a.out_dir or a.video.parent
        zones = PLATFORMS.get(a.platform, PLATFORMS["universal"]) if a.platform != "all" else {
            k: max(z[k] for z in PLATFORMS.values()) for k in ("top", "bottom", "left", "right")}
        if H > W or a.face or a.platform != "universal":
            measured["safezones_sheet"] = str(safezone_sheet(a.video, dur, W, H, zones, a.face,
                                                             out_dir / f"{a.video.stem}-safezones.jpg", tmp))
    if a.page:
        page = a.page.parent if a.page.suffix == ".html" else a.page
        env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        p = subprocess.run(["npx", "-y", f"hyperframes@{os.environ.get('VS_HYPERFRAMES_VERSION', '0.8.79')}", "check", str(page)],
                           capture_output=True, text=True, env=env)
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-8:])
        if p.returncode:
            add("warn", "page", f"hyperframes check reported problems:\n{tail}")
        measured["page_check"] = tail

    fails = [i for i in issues if i["level"] == "fail"]
    report = {"video": str(a.video), "size": f"{W}x{H}", "fps": round(fps, 3), "duration": round(dur, 3),
              "measured": measured, "issues": issues, "ready": not fails}

    def human(r):
        print(f"{r['video']} · {r['size']} @ {r['fps']} fps · {r['duration']}s")
        if measured.get("lufs") is not None:
            print(f"  loudness {measured['lufs']} LUFS · true peak {measured['true_peak']} dBFS")
        if "speech" in measured and "match" in measured["speech"]:
            print(f"  speech: {measured['speech']['match']:.0%} of the script heard")
        for i in r["issues"]:
            print(f"  {'✗' if i['level'] == 'fail' else '⚠'} {i['check']}: {i['detail']}")
        if "safezones_sheet" in measured:
            print(f"  look → {measured['safezones_sheet']} (red = platform UI; keep text and faces out of it)")
        print("READY to deliver." if r["ready"] else f"NOT READY: {len(fails)} blocking issue(s).")
    emit(report, a.json, human)
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
