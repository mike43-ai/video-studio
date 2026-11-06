#!/usr/bin/env python3
"""music.py — music beds: make one (free synth or ElevenLabs), analyze any track, arrange its drop.

  vs music ls
  vs music plan --length 30 [--drop 12] [--bpm 120] [--mood upbeat] -o plan.json
  vs music make [--plan plan.json | --length 30 --drop 12 --bpm 120 --mood upbeat --key A --seed 1] -o bed.wav
  vs music make --engine elevenlabs --prompt "warm lo-fi beat, soft keys" --length 30 -o bed.mp3 --paid-ok
  vs music analyze <track> [-o beats.json]           tempo, beats, downbeats, drops (= vs beats)
  vs music arrange <track> --drop-at 12.0 [--beats beats.json] [--length 30] -o arranged.wav
        splice whole bars before the track's drop (or start later) so its drop lands exactly on --drop-at

Moods (synth): calm · upbeat · epic · dark · lofi. Keys: C C# D … B, add "m" for minor (e.g. Am).
The synth is a simple 4-on-the-floor arranger (pad, bass, arp, drums, riser) — clean and royalty-free,
not a hit record; for a richer bed use a licensed track + `arrange`, or ElevenLabs (paid).
Generated files go to the library: same plan + seed → same file.
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
from vslib import die, get_key, http, lib_copy_out, lib_get, lib_put, paid_gate, require_key  # noqa: E402

SR = 44100
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MOODS = {
    "calm":   {"bpm": 84,  "key": "C",  "prog": [0, 7, 9, 5],  "drums": 0.4, "arp": 0.7, "swing": 0.0, "lp": 3500},
    "upbeat": {"bpm": 120, "key": "G",  "prog": [0, 7, 9, 5],  "drums": 1.0, "arp": 0.8, "swing": 0.0, "lp": 9000},
    "epic":   {"bpm": 100, "key": "Dm", "prog": [0, 8, 3, 10], "drums": 1.0, "arp": 0.5, "swing": 0.0, "lp": 8000},
    "dark":   {"bpm": 95,  "key": "Em", "prog": [0, 8, 5, 7],  "drums": 0.9, "arp": 0.6, "swing": 0.0, "lp": 5000},
    "lofi":   {"bpm": 82,  "key": "Am", "prog": [0, 5, 10, 3], "drums": 0.7, "arp": 0.5, "swing": 0.12, "lp": 2800},
}


# ------------------------------------------------------------------ plan
def make_plan(length: float, drop: float | None, bpm: float, mood: str, key: str | None, seed: int) -> dict:
    bar = 4 * 60 / bpm
    n_bars = max(2, round(length / bar))
    drop_bar = n_bars // 3 if drop is None else max(1, min(n_bars - 1, round(drop / bar)))
    sections = []
    if drop_bar >= 3:
        sections.append({"name": "intro", "bars": drop_bar - 2, "energy": 0.3})
        sections.append({"name": "build", "bars": 2, "energy": 0.5, "riser": True})
    else:
        sections.append({"name": "build", "bars": drop_bar, "energy": 0.5, "riser": True})
    rest = n_bars - drop_bar
    outro = 2 if rest >= 6 else (1 if rest >= 3 else 0)
    sections.append({"name": "drop", "bars": rest - outro, "energy": 1.0})
    if outro:
        sections.append({"name": "outro", "bars": outro, "energy": 0.4, "fade": True})
    return {"bpm": bpm, "mood": mood, "key": key or MOODS[mood]["key"], "seed": seed, "sections": sections,
            "bar_seconds": round(bar, 4), "drop_at": round(drop_bar * bar, 3), "length": round(n_bars * bar, 3)}


# ------------------------------------------------------------------ synth
def lowpass(x: np.ndarray, hz: float) -> np.ndarray:
    from scipy.signal import butter, lfilter
    b, a = butter(2, min(0.99, hz / (SR / 2)))
    return lfilter(b, a, x)


def highpass(x: np.ndarray, hz: float) -> np.ndarray:
    from scipy.signal import butter, lfilter
    b, a = butter(2, hz / (SR / 2), btype="high")
    return lfilter(b, a, x)


def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def tone(f: float, d: float, kind: str = "sine", decay: float = 0.0) -> np.ndarray:
    t = np.arange(int(d * SR)) / SR
    if kind == "saw":
        x = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 8))
    elif kind == "tri":
        x = sum((-1) ** ((k - 1) // 2) * np.sin(2 * np.pi * f * k * t) / k ** 2 for k in range(1, 9, 2))
    else:
        x = np.sin(2 * np.pi * f * t)
    if decay:
        x = x * np.exp(-decay * t)
    n = len(x)
    a = min(n // 2, int(0.005 * SR))
    if a:
        x[:a] *= np.linspace(0, 1, a)
        x[-a:] *= np.linspace(1, 0, a)
    return x


def add(buf: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * SR)
    if i >= len(buf):
        return
    n = min(len(x), len(buf) - i)
    buf[i:i + n] += gain * x[:n]


def render(plan: dict) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(plan["seed"])
    mood = MOODS[plan["mood"]]
    bpm = plan["bpm"]
    beat = 60 / bpm
    bar = 4 * beat
    key = plan["key"]
    minor = key.endswith("m")
    root = 48 + NOTES.index(key.rstrip("m"))  # C3
    third = 3 if minor else 4
    total_bars = sum(s["bars"] for s in plan["sections"])
    L = total_bars * bar + 2.0
    pad, bass, arp, drums, fx = (np.zeros(int(L * SR)) for _ in range(5))
    tk = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(np.linspace(140, 42, tk.size)) / SR) * np.exp(-9 * tk)
    ts = np.arange(int(0.22 * SR)) / SR
    snare = highpass(rng.standard_normal(ts.size), 1500) * np.exp(-18 * ts) * 0.6 + np.sin(2 * np.pi * 190 * ts) * np.exp(-25 * ts) * 0.5
    th = np.arange(int(0.05 * SR)) / SR
    hat = highpass(rng.standard_normal(th.size), 7000) * np.exp(-70 * th) * 0.35

    bar_i = 0
    for sec in plan["sections"]:
        e = sec["energy"]
        for b in range(sec["bars"]):
            t0 = bar_i * bar
            deg = mood["prog"][bar_i % len(mood["prog"])]
            chord_root = root + deg
            chord = [chord_root, chord_root + (third if deg in (0, 5, 7) or not minor else 3), chord_root + 7]
            # pad: whole-bar chord, slow attack
            p = sum(tone(midi_hz(n + 12), bar, "saw") for n in chord) / 3
            att = np.minimum(1, np.arange(p.size) / (0.25 * SR))
            add(pad, p * att, t0, 0.22)
            # bass: 8th notes from energy ≥ 0.5, else whole notes
            if e >= 0.9:
                for k in range(8):
                    add(bass, tone(midi_hz(chord_root - 12), beat / 2 * 0.9, "saw", 6), t0 + k * beat / 2, 0.35)
            else:
                add(bass, tone(midi_hz(chord_root - 12), bar * 0.95, "sine", 1.2), t0, 0.35)
            # arp: 16ths through the chord
            if mood["arp"] and e >= 0.3:
                for k in range(16):
                    n = chord[k % 3] + 24 + (12 if k % 8 == 7 else 0)
                    sw = mood["swing"] * beat / 4 if k % 2 else 0
                    add(arp, tone(midi_hz(n), beat / 4 * 0.95, "tri", 14), t0 + k * beat / 4 + sw, 0.12 * mood["arp"] * (0.6 + 0.4 * e))
            # drums
            if mood["drums"]:
                for k in range(4):
                    bt = t0 + k * beat
                    if e >= 0.6 or (e >= 0.5 and k in (0, 2)):
                        add(drums, kick, bt, 0.9 * mood["drums"])
                    if e >= 0.9 and k in (1, 3):
                        add(drums, snare, bt, 0.7 * mood["drums"])
                    for h in range(2 if e < 0.9 else 4):
                        sw = mood["swing"] * beat / 2 if h % 2 else 0
                        add(drums, hat, bt + h * beat / (2 if e < 0.9 else 4) + sw, (0.5 + 0.5 * e) * mood["drums"])
            bar_i += 1
        if sec.get("riser"):
            d = sec["bars"] * bar
            tr = np.arange(int(d * SR)) / SR
            noise = highpass(rng.standard_normal(tr.size), 800) * (tr / d) ** 2
            add(fx, noise, (bar_i - sec["bars"]) * bar, 0.25)
        if sec["name"] == "drop":
            add(fx, kick * 1.2 + lowpass(rng.standard_normal(kick.size), 400) * np.exp(-6 * tk) * 0.5,
                (bar_i - sec["bars"]) * bar, 0.8)
    pad = lowpass(pad, mood["lp"])
    mix = pad + lowpass(bass, 900) + arp + drums + fx
    if plan["mood"] == "lofi":
        mix = lowpass(mix, 4500) + highpass(rng.standard_normal(mix.size), 3000) * 0.004
    # energy envelope per section + fades
    env = np.ones_like(mix)
    pos = 0
    for sec in plan["sections"]:
        n = int(sec["bars"] * bar * SR)
        lvl = 0.3 + 0.7 * sec["energy"]
        env[pos:pos + n] *= lvl
        if sec.get("fade"):
            env[pos:pos + n] *= np.linspace(1, 0, n)
        pos += n
    env[pos:] = 0
    mix = (mix * env)[: int(total_bars * bar * SR)]
    mix = np.stack([mix, np.roll(mix, int(0.012 * SR)) * 0.9 + mix * 0.1], axis=1)  # cheap stereo width
    mix /= np.max(np.abs(mix)) + 1e-9
    mix *= 10 ** (-1 / 20)
    return mix.astype(np.float32), {"drop_at": plan["drop_at"], "length": round(len(mix) / SR, 3)}


# ------------------------------------------------------------------ arrange
def decode(path: Path) -> tuple[np.ndarray, int]:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "x.wav"
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ar", str(SR), "-ac", "2", "-y", str(wav)])
        if p.returncode:
            die(f"cannot decode {path}")
        y, sr = sf.read(wav, dtype="float32", always_2d=True)
    return y, sr


def arrange(track: Path, beats: dict, drop_at: float, length: float | None) -> tuple[np.ndarray, dict]:
    y, sr = decode(track)
    if not beats.get("drops"):
        die("no drop found in the track's beats.json")
    drop = beats["drops"][0]
    bar = 4 * 60 / beats["tempo"]
    db = beats.get("downbeats") or []
    if len(db) > 2:  # measured bar length is better than the BPM estimate
        bar = float(np.median(np.diff(db)))
    shift = drop_at - drop
    xf = int(0.03 * sr)

    def splice(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        if len(a) < xf or len(b) < xf:
            return np.concatenate([a, b])
        ramp = np.linspace(0, 1, xf)[:, None]
        return np.concatenate([a[:-xf], a[-xf:] * (1 - ramp) + b[:xf] * ramp, b[xf:]])

    note = ""
    if shift > 0:
        n = int(np.ceil(shift / bar - 1e-6))
        d = int(drop * sr)
        seg_len = int(min(n * bar, drop) * sr)
        block = y[d - seg_len:d] if seg_len > 0 else np.zeros((0, 2), np.float32)
        reps = int(np.ceil(n * bar * sr / max(1, len(block)))) if len(block) else 0
        insert = np.concatenate([block] * reps)[: int(n * bar * sr)] if reps else np.zeros((int(n * bar * sr), 2), np.float32)
        out = splice(splice(y[:d], insert), y[d:])
        lost = (len(y) + len(insert)) - len(out)  # each crossfade splice overlaps xf samples
        trim = int(round((n * bar - shift) * sr)) - lost  # we added a bit more than needed → start later
        out = out[trim:] if trim >= 0 else np.concatenate([np.zeros((-trim, 2), np.float32), out])
        note = f"inserted {n} bar(s) before the drop, trimmed {trim / sr:.2f}s from the start"
    else:
        start = int(-shift * sr)
        out = y[start:]
        note = f"started the track {-shift:.2f}s in"
    fi = int(0.4 * sr) if note.startswith("started") or "trimmed" in note else 0
    if fi:
        out[:fi] *= np.linspace(0, 1, fi)[:, None]
    if length:
        n = int(length * sr)
        if len(out) < n:
            out = np.concatenate([out, np.zeros((n - len(out), 2), np.float32)])
        out = out[:n]
        fo = int(min(2.0, length / 4) * sr)
        out[-fo:] *= np.linspace(1, 0, fo)[:, None]
    return out, {"drop_at": drop_at, "source_drop": drop, "bar": round(bar, 3), "note": note,
                 "length": round(len(out) / sr, 3)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["ls", "plan", "make", "analyze", "arrange"])
    ap.add_argument("track", nargs="?", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--length", type=float)
    ap.add_argument("--drop", type=float)
    ap.add_argument("--drop-at", type=float)
    ap.add_argument("--bpm", type=float)
    ap.add_argument("--mood", default="upbeat", choices=list(MOODS))
    ap.add_argument("--key")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--beats", type=Path)
    ap.add_argument("--engine", default="synth", choices=["synth", "elevenlabs"])
    ap.add_argument("--prompt")
    ap.add_argument("--paid-ok", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()

    if a.cmd == "ls":
        print("  ✓ synth       free, offline — moods: " + ", ".join(MOODS))
        print(f"  {'✓' if get_key('elevenlabs') else '○'} elevenlabs  PAID (music from a prompt) · vs keys set elevenlabs")
        print("  ✓ analyze/arrange  any track you have the rights to")
        return
    if a.cmd == "plan":
        p = make_plan(a.length or 30, a.drop, a.bpm or MOODS[a.mood]["bpm"], a.mood, a.key, a.seed)
        out = a.out or Path("music-plan.json")
        out.write_text(json.dumps(p, indent=1))
        print(f"✓ {out} · {p['length']}s · {p['bpm']} BPM · drop at {p['drop_at']}s · "
              + " → ".join(f"{s['name']}({s['bars']})" for s in p["sections"]))
        return
    if a.cmd == "analyze":
        if not a.track:
            die("usage: vs music analyze <track>")
        cmd = [sys.executable, str(Path(__file__).with_name("beats.py")), str(a.track), "-o", str(a.out or "beats.json")]
        sys.exit(subprocess.run(cmd).returncode)
    if a.cmd == "arrange":
        if not (a.track and a.drop_at is not None):
            die("usage: vs music arrange <track> --drop-at SECONDS [--beats beats.json]")
        bfile = a.beats
        if not bfile:
            bfile = Path(tempfile.mkdtemp()) / "beats.json"
            subprocess.run([sys.executable, str(Path(__file__).with_name("beats.py")), str(a.track), "-o", str(bfile)], check=True)
        y, info = arrange(a.track, json.loads(bfile.read_text()), a.drop_at, a.length)
        out = a.out or a.track.with_name(a.track.stem + "-arranged.wav")
        sf.write(out, y, SR)
        print(f"✓ {out} · drop {info['source_drop']}s → {info['drop_at']}s · bar {info['bar']}s · {info['note']} · {info['length']}s")
        print("  verify: vs music analyze " + str(out))
        return

    # make
    if a.engine == "elevenlabs":
        if not (a.prompt and a.length):
            die("elevenlabs needs --prompt and --length")
        spec = {"engine": "elevenlabs", "prompt": a.prompt, "length": a.length}
        out = a.out or Path("bed.mp3")
        hit = None if a.fresh else lib_get("music", spec)
        if hit:
            print(f"✓ {lib_copy_out(hit, out)} (from library, no charge)"); return
        paid_gate("ElevenLabs music", f"billed per generated second ({a.length:.0f}s)", a.paid_ok)
        key = require_key("elevenlabs")
        audio = http("https://api.elevenlabs.io/v1/music", data=json.dumps(
            {"prompt": a.prompt, "music_length_ms": int(a.length * 1000)}).encode(),
            headers={"xi-api-key": key, "Content-Type": "application/json"}, timeout=600)
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_name(out.stem + ".el-tmp.mp3")
        raw.write_bytes(audio)
        if out.suffix.lower() == ".mp3":
            raw.replace(out)
        else:  # write exactly -o
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(raw), "-y", str(out)], check=True)
            raw.unlink()
        lib_put("music", spec, out)
        print(f"✓ {out} (ElevenLabs) — find its drop: vs music analyze {out}")
        return
    plan = json.loads(a.plan.read_text()) if a.plan else make_plan(
        a.length or 30, a.drop, a.bpm or MOODS[a.mood]["bpm"], a.mood, a.key, a.seed)
    out = a.out or Path("bed.wav")
    hit = None if a.fresh else lib_get("music", plan)
    if hit:
        print(f"✓ {lib_copy_out(hit, out)} (from library) · drop at {plan['drop_at']}s"); return
    y, info = render(plan)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out, y, SR, subtype="PCM_16")
    lib_put("music", plan, out, {"drop_at": plan["drop_at"]})
    print(f"✓ {out} · {info['length']}s · {plan['bpm']} BPM {plan['key']} {plan['mood']} · drop at {plan['drop_at']}s")


if __name__ == "__main__":
    main()
