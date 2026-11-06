#!/usr/bin/env python3
"""mix.py — voice + music beds + sound cues → one master at −14 LUFS.

Quick form (one bed):
  vs mix --voice voice.wav --music bed.mp3 --cues cues.json -o mix.wav \\
         [--duration 42.5 | --timeline timeline.json] [--music-db -16] [--duck 9] [--target -14]
Full form (a plan file; flags above override it):
  vs mix mix.json -o mix.wav [--stems stems/]

mix.json (all keys optional except what you use; times in seconds):
{
  "duration": 42.5,                 # default: timeline/voice length + tail
  "voice": "voice.wav", "voice_db": 0,
  "music": {
    "db": -16,                      # bus level before ducking
    "duck": {"db": 9, "attack": 0.03, "release": 0.35, "threshold": -38},   # or true / false
    "beds": [
      {"file": "intro.mp3", "from": 0, "to": 12, "offset": 0, "loop": false, "fade_in": 0.5, "fade_out": 1.5,
       "db": 0, "lufs": -20,        # lufs: level-match this bed before "db" is applied
       "ease": [{"at": 8, "to_db": -6, "over": 1.5}]},          # bed level automation (relative dB)
      {"files": ["a.mp3", "b.mp3"], "from": 12, "crossfade": 2, "loop": true}
    ]
  },
  "cues": [                          # or the short list form [[t, "name", peak_db], …]
    {"sound": "whoosh", "t": 3.2, "db": -12, "align": "start|peak|end", "cut": 0.4},
    {"sound": "boom+whoosh", "t": 12.0, "align": "peak", "gain": "in_band", "weight": "hero"}
  ],
  "fade_out": 1.0, "target": -14, "tp": -1.5
}
- db on a cue = where its PEAK lands (dBFS). gain "in_band" instead sets it 3.5 dB (hero 5) over what already
  plays in the cue's own frequency band at that moment, never more than 6 dB (hero 8) over the local peak.
- "a+b" layers two sounds with their peaks together, the second 4 dB under the first. cut = keep only the
  first N seconds (with a short fade). align peak = the loudest moment lands on t; end = the sound ends on t.
- --stems DIR also writes stem-voice.wav, stem-music.wav (after ducking), stem-sfx.wav (before mastering).
Two-pass loudnorm to --target LUFS integrated, true peak ≤ --tp dBTP, 48 kHz stereo 24-bit.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sfx import resolve  # noqa: E402
from vslib import die, ff  # noqa: E402

SR = 48000


def load(path: str | Path) -> np.ndarray:
    """Any audio file → float32 stereo at 48 kHz."""
    p = Path(path)
    if not p.exists():
        die(f"audio not found: {p}")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                       capture_output=True)
    if r.returncode:
        die(f"cannot decode {p}: {r.stderr.decode()[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).copy()


def db(x: float) -> float:
    return 10 ** (x / 20)


def lufs_of(x: np.ndarray) -> float:
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        sf.write(f.name, x, SR, subtype="FLOAT")
        err = ff(["-i", f.name, "-af", "ebur128", "-f", "null", "-"], check=False)
    s = err[err.rfind("Summary:"):]
    try:
        return float(s.split("I:")[1].split("LUFS")[0])
    except (IndexError, ValueError):
        return -70.0


def place(buf: np.ndarray, x: np.ndarray, start: int) -> None:
    if start >= len(buf) or start + len(x) <= 0:
        return
    a = max(0, -start)
    s = max(0, start)
    n = min(len(x) - a, len(buf) - s)
    buf[s:s + n] += x[a:a + n]


def fade(x: np.ndarray, fin: float, fout: float) -> np.ndarray:
    n = len(x)
    i, o = min(n, int(fin * SR)), min(n, int(fout * SR))
    if i:
        x[:i] *= np.linspace(0, 1, i)[:, None]
    if o:
        x[n - o:] *= np.linspace(1, 0, o)[:, None]
    return x


def build_bed(bed: dict, D: float, base: Path) -> np.ndarray:
    out = np.zeros((int(D * SR), 2), np.float32)
    t0 = float(bed.get("from", 0))
    t1 = min(D, float(bed.get("to", D)))
    n = int((t1 - t0) * SR)
    if n <= 0:
        return out
    files = bed.get("files") or [bed["file"]]
    xf = int(float(bed.get("crossfade", 2.0)) * SR)
    seq = [load(base / f) for f in files]
    seq[0] = seq[0][int(float(bed.get("offset", 0)) * SR):]
    if any(len(x) == 0 for x in seq):
        die(f"bed {files}: a file decodes to no audio (or offset is past its end)")
    track = seq[0]
    for nxt in seq[1:]:  # crossfade the files in order
        k = min(xf, len(track) // 2, len(nxt) // 2)
        ramp = np.linspace(0, 1, k)[:, None] if k else None
        track = np.concatenate([track[:len(track) - k], track[len(track) - k:] * (1 - ramp) + nxt[:k] * ramp, nxt[k:]]) if k \
            else np.concatenate([track, nxt])
    if bed.get("loop", True) and len(track) < n:
        k = min(int(0.05 * SR), len(track) // 4)
        reps = [track]
        while sum(len(r) for r in reps) < n + k:
            reps.append(track)
        joined = reps[0]
        for r in reps[1:]:  # short crossfade at each loop point to avoid clicks
            ramp = np.linspace(0, 1, k)[:, None]
            joined = np.concatenate([joined[:-k], joined[-k:] * (1 - ramp) + r[:k] * ramp, r[k:]])
        track = joined
    track = track[:n].copy()
    if "lufs" in bed:
        track *= db(float(bed["lufs"]) - lufs_of(track))
    track *= db(float(bed.get("db", 0)))
    track = fade(track, float(bed.get("fade_in", 0)), float(bed.get("fade_out", 0)))
    if bed.get("ease"):  # piecewise-linear level automation, in dB, relative to the bed level
        g = np.zeros(len(track))
        cur = 0.0
        tt = np.arange(len(track)) / SR + t0
        for e in sorted(bed["ease"], key=lambda e: e["at"]):
            at, over, to = float(e["at"]), max(0.001, float(e.get("over", 1.0))), float(e["to_db"])
            m = tt >= at
            ramp = np.clip((tt[m] - at) / over, 0, 1)
            g[m] = cur + (to - cur) * ramp
            cur = to
        track *= (10 ** (g / 20))[:, None]
    place(out, track, int(t0 * SR))
    return out


def duck_gain(voice: np.ndarray, cfg: dict) -> np.ndarray:
    """Music gain curve: drops by cfg.db while the voice is above threshold, with attack/release smoothing."""
    mono = np.abs(voice).max(axis=1)
    hop = int(0.01 * SR)
    frames = len(mono) // hop + 1
    pad = np.pad(mono, (0, frames * hop - len(mono)))
    rms = np.sqrt((pad.reshape(frames, hop) ** 2).mean(axis=1) + 1e-12)
    active = (20 * np.log10(rms) > float(cfg.get("threshold", -38))).astype(float)
    a = np.exp(-0.01 / max(0.001, float(cfg.get("attack", 0.03))))
    r = np.exp(-0.01 / max(0.001, float(cfg.get("release", 0.35))))
    env = np.zeros(frames)
    acc = 0.0
    for i, v in enumerate(active):
        coef = a if v > acc else r
        acc = coef * acc + (1 - coef) * v
        env[i] = acc
    depth = 1 - db(-float(cfg.get("db", 9)))
    g = 1 - depth * env
    return np.repeat(g, hop)[:len(voice)].astype(np.float32)


def band_peak(x: np.ndarray, lo: float, hi: float) -> float:
    from scipy.signal import butter, sosfilt
    if len(x) < 64:
        return -91.0
    sos = butter(2, [max(30, lo) / (SR / 2), min(0.95, hi / (SR / 2))], btype="band", output="sos")
    y = sosfilt(sos, x.mean(axis=1))
    return 20 * np.log10(np.max(np.abs(y)) + 1e-9)


def centroid(x: np.ndarray) -> float:
    m = x.mean(axis=1)
    spec = np.abs(np.fft.rfft(m[: min(len(m), SR)]))
    f = np.fft.rfftfreq(min(len(m), SR), 1 / SR)
    return float((spec * f).sum() / (spec.sum() + 1e-9))


def cue_sound(name: str, cut: float | None) -> np.ndarray:
    parts = name.split("+")
    layers = []
    for i, p in enumerate(parts):
        path = resolve(p.strip())
        if not path:
            print(f"  ⚠ cue sound {p!r} not found — skipped (vs sfx need / vs sfx make)")
            return np.zeros((0, 2), np.float32)
        x = load(path)
        if cut:
            x = fade(x[: int(cut * SR)].copy(), 0, min(0.03, cut / 4))
        x /= np.max(np.abs(x)) + 1e-9
        layers.append(x * (db(-4) if i else 1.0))
    if len(layers) == 1:
        return layers[0]
    peaks = [int(np.argmax(np.abs(l).max(axis=1))) for l in layers]
    lead = max(peaks)
    L = max(lead - p + len(l) for p, l in zip(peaks, layers))
    out = np.zeros((L, 2), np.float32)
    for p, l in zip(peaks, layers):
        place(out, l, lead - p)
    return out / (np.max(np.abs(out)) + 1e-9)


def normalize_cues(raw) -> list[dict]:
    out = []
    for c in raw or []:
        if isinstance(c, (list, tuple)):
            out.append({"t": float(c[0]), "sound": str(c[1]), "db": float(c[2]) if len(c) > 2 else -14.0})
        else:
            out.append(c)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", nargs="?", type=Path, help="mix.json")
    ap.add_argument("--voice", type=Path)
    ap.add_argument("--music", type=Path)
    ap.add_argument("--cues", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("mix.wav"))
    ap.add_argument("--duration", type=float)
    ap.add_argument("--timeline", type=Path)
    ap.add_argument("--tail", type=float, default=1.0)
    ap.add_argument("--music-db", type=float)
    ap.add_argument("--duck", type=float, help="dB the music drops under the voice (default 9; 0 = off)")
    ap.add_argument("--fade", type=float, help="music fade-out seconds (quick form)")
    ap.add_argument("--target", type=float)
    ap.add_argument("--tp", type=float)
    ap.add_argument("--stems", type=Path)
    a = ap.parse_args()

    plan: dict = json.loads(a.plan.read_text(encoding="utf-8")) if a.plan else {}
    base = a.plan.parent if a.plan else Path(".")
    if a.voice:
        plan["voice"] = str(a.voice.resolve())
    music = plan.setdefault("music", {})
    if a.music:
        music["beds"] = [{"file": str(a.music.resolve()), "loop": True, "fade_out": a.fade if a.fade is not None else 1.5}]
    if a.music_db is not None:
        music["db"] = a.music_db
    if a.duck is not None:
        music["duck"] = {"db": a.duck} if a.duck > 0 else False
    if a.cues:
        plan["cues"] = json.loads(a.cues.read_text(encoding="utf-8"))
    target = a.target if a.target is not None else float(plan.get("target", -14))
    tp = a.tp if a.tp is not None else float(plan.get("tp", -1.5))
    if not plan.get("voice") and not music.get("beds"):
        die("need a voice and/or music (flags or mix.json)")

    voice = load(base / plan["voice"]) * db(float(plan.get("voice_db", 0))) if plan.get("voice") else None
    if a.duration or plan.get("duration"):
        D = float(a.duration or plan["duration"])
    elif a.timeline:
        D = float(json.loads(a.timeline.read_text(encoding="utf-8"))["duration"])
    elif voice is not None:
        D = len(voice) / SR + a.tail
    else:
        D = max(len(load(base / (b.get("files") or [b["file"]])[0])) / SR for b in music["beds"])
    N = int(D * SR)

    v = np.zeros((N, 2), np.float32)
    if voice is not None:
        place(v, voice, 0)
    m = np.zeros((N, 2), np.float32)
    for bed in music.get("beds", []):
        m += build_bed(bed, D, base)
    m *= db(float(music.get("db", -16)))
    duck = music.get("duck", True)
    if voice is not None and duck and m.any():
        m *= duck_gain(v, duck if isinstance(duck, dict) else {})[:, None]

    s = np.zeros((N, 2), np.float32)
    bed_and_voice = v + m
    placed = 0
    for c in normalize_cues(plan.get("cues")):
        x = cue_sound(str(c["sound"]), c.get("cut"))
        if not len(x):
            continue
        t = float(c["t"])
        align = c.get("align", "start")
        pk = int(np.argmax(np.abs(x).max(axis=1)))
        start = int(t * SR) - (pk if align == "peak" else len(x) if align == "end" else 0)
        if c.get("gain") == "in_band":
            hero = c.get("weight") == "hero"
            cen = centroid(x)
            seg = bed_and_voice[max(0, start):max(0, start) + len(x)]
            local_band = band_peak(seg, cen / 2, cen * 2)
            local_all = 20 * np.log10(np.max(np.abs(seg)) + 1e-9) if len(seg) else -91
            if local_all < -60:  # nothing plays under it: in-band has no reference → use the cue's db
                peak_db = float(c.get("db", -12 if hero else -14))
            else:
                peak_db = min(local_band + (5 if hero else 3.5), local_all + (8 if hero else 6), -1.0)
        else:
            peak_db = float(c.get("db", -14))
        place(s, x * db(peak_db), start)
        placed += 1

    if a.stems:
        a.stems.mkdir(parents=True, exist_ok=True)
        for name, x in (("voice", v), ("music", m), ("sfx", s)):
            sf.write(a.stems / f"stem-{name}.wav", x, SR, subtype="FLOAT")
    pre = v + m + s
    if plan.get("fade_out"):
        pre = fade(pre, 0, float(plan["fade_out"]))
    with tempfile.TemporaryDirectory() as tmp:
        pre_f = Path(tmp) / "pre.wav"
        sf.write(pre_f, pre, SR, subtype="FLOAT")
        err = ff(["-i", pre_f, "-af", f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"])
        meas = json.loads(err[err.rindex("{"): err.rindex("}") + 1])
        ln = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
              f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
              f":offset={meas['target_offset']}:linear=true")
        a.out.parent.mkdir(parents=True, exist_ok=True)
        ff(["-i", pre_f, "-af", f"{ln},aresample={SR}", "-c:a", "pcm_s24le", "-y", a.out])
    beds = len(music.get("beds", []))
    print(f"✓ {a.out} · {D:.2f}s · voice {'yes' if voice is not None else 'no'} · {beds} bed(s) · {placed} cue(s) "
          f"· input {meas['input_i']} LUFS → target {target} LUFS / {tp} dBTP"
          + (f" · stems → {a.stems}" if a.stems else ""))


if __name__ == "__main__":
    main()
