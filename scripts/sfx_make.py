#!/usr/bin/env python3
"""sfx_make.py — a sound effect from a short description: free synth, or ElevenLabs (paid).

  vs sfx make "deep cinematic boom" [-o boom.wav] [--len 1.2] [--seed 3]
  vs sfx make "glass shattering on concrete" --engine elevenlabs [--len 2] --paid-ok

Synth understands these families (first match wins), plus modifiers low/deep, high/bright, soft, long, short:
  whoosh/swoosh/swish/swipe · riser/build/uplifter · downer/drop/fall · impact/boom/hit/thud/slam ·
  pop/bubble · click/tap/tick · ding/bell/chime/notification · coin/success/win/correct ·
  error/fail/wrong/buzz · glitch/digital/static · typing/keyboard · shutter/camera · heartbeat ·
  sparkle/shine/magic · whistle · tick-tock/clock
Results go to the library and to ~/.video-studio/sfx/made/ so `vs mix` cues can use them by name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vslib import VS_HOME, die, http, lib_copy_out, lib_get, lib_put, paid_gate, require_key  # noqa: E402

SR = 48000


def t(d: float) -> np.ndarray:
    return np.arange(int(d * SR)) / SR


def bp_noise(n: int, rng, lo: float, hi: float) -> np.ndarray:
    from scipy.signal import butter, sosfilt
    sos = butter(2, [max(20, lo) / (SR / 2), min(0.99, hi / (SR / 2))], btype="band", output="sos")
    return sosfilt(sos, rng.standard_normal(n))


def sweep_noise(d: float, rng, f0: float, f1: float, bw: float = 0.6) -> np.ndarray:
    """Band-passed noise whose centre glides f0→f1 (processed in 40 short blocks)."""
    n = int(d * SR)
    out = np.zeros(n)
    blocks = 40
    for i in range(blocks):
        a, b = i * n // blocks, (i + 1) * n // blocks
        fc = f0 * (f1 / f0) ** (i / (blocks - 1))
        seg = bp_noise(b - a + 2048, rng, fc * (1 - bw / 2), fc * (1 + bw / 2))[1024:1024 + b - a]
        out[a:b] = seg
    return out


def chirp(d: float, f0: float, f1: float) -> np.ndarray:
    f = np.geomspace(max(1, f0), max(1, f1), int(d * SR))
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def bell(d: float, f: float, k: float = 4.0) -> np.ndarray:
    x = t(d)
    return sum(w * np.sin(2 * np.pi * f * r * x) * np.exp(-k * r ** 0.5 * x)
               for r, w in ((1, 1), (2.76, 0.4), (5.4, 0.18), (8.9, 0.08)))


FAMILIES = [
    (r"whoosh|swoosh|swish|swipe|woosh", "whoosh"), (r"riser|build|uplift|rise", "riser"),
    (r"downer|drop|fall|down", "downer"), (r"impact|boom|hit|thud|slam|punch|bass drop", "impact"),
    (r"pop|bubble", "pop"), (r"tick.?tock|clock", "clock"), (r"click|tap|tick", "click"),
    (r"ding|bell|chime|notif", "ding"), (r"coin|success|win|correct|level", "success"),
    (r"error|fail|wrong|buzz|denied", "error"), (r"glitch|digital|static|data", "glitch"),
    (r"typ|keyboard", "typing"), (r"shutter|camera|photo", "shutter"), (r"heart", "heartbeat"),
    (r"sparkle|shine|magic|twinkle|glitter", "sparkle"), (r"whistle", "whistle"),
]


def put(x: np.ndarray, sig: np.ndarray, s: int, gain: float = 1.0) -> None:
    """Add sig into x at sample s, clipped to x (never a shape error when s runs past the end)."""
    if s >= x.size:
        return
    n = min(sig.size, x.size - s)
    x[s:s + n] += sig[:n] * gain


def synth(prompt: str, length: float | None, seed: int) -> tuple[np.ndarray, str]:
    p = prompt.lower()
    fam = next((name for pat, name in FAMILIES if re.search(pat, p)), None)
    if fam is None:
        print(f"  ⚠ no sound family matches {prompt!r} — using a whoosh. Try a family word (see -h) "
              "or a real recording: vs sfx cc0 find …")
        fam = "whoosh"
    rng = np.random.default_rng(seed)
    pitch = 0.6 if re.search(r"\b(low|deep|heavy|big|cinematic)\b", p) else \
        (1.6 if re.search(r"\b(high|bright|light|tiny|small)\b", p) else 1.0)
    mult = 1.6 if re.search(r"\blong\b", p) else (0.6 if re.search(r"\bshort\b", p) else 1.0)
    soft = bool(re.search(r"\b(soft|gentle|subtle)\b", p))

    def L(d: float) -> float:
        return length or d * mult

    if fam == "whoosh":
        d = L(0.6)
        x = sweep_noise(d, rng, 300 * pitch, 3000 * pitch) * np.sin(np.pi * t(d) / d) ** 2
    elif fam == "riser":
        d = L(2.0)
        x = (0.5 * chirp(d, 150 * pitch, 1500 * pitch) + 0.5 * sweep_noise(d, rng, 400 * pitch, 6000)) * (t(d) / d) ** 2
    elif fam == "downer":
        d = L(1.5)
        x = (0.6 * chirp(d, 900 * pitch, 60 * pitch) + 0.4 * sweep_noise(d, rng, 4000, 200)) * (1 - t(d) / d) ** 1.5
    elif fam == "impact":
        d = L(1.2)
        x = chirp(d, 130 * pitch, 35 * pitch) * np.exp(-4 * t(d)) + 0.5 * bp_noise(int(d * SR), rng, 60, 1200) * np.exp(-12 * t(d))
    elif fam == "pop":
        d = L(0.12)
        x = chirp(d, 1100 * pitch, 300 * pitch) * np.exp(-35 * t(d))
    elif fam == "click":
        d = L(0.035)
        x = bp_noise(int(d * SR), rng, 1500 * pitch, 9000) * np.exp(-260 * t(d))
    elif fam == "clock":
        d = L(1.0)
        x = np.zeros(int(d * SR))
        for i, f in enumerate((2600, 2000)):
            c = bp_noise(int(0.03 * SR), rng, f * pitch * 0.8, f * pitch * 1.2) * np.exp(-200 * t(0.03))
            s = int(i * 0.5 * SR)
            put(x, c, s)
    elif fam == "ding":
        d = L(1.4)
        x = bell(d, 1320 * pitch, 3.5)
    elif fam == "success":
        d = L(0.7)
        x = np.zeros(int(d * SR))
        for i, f in enumerate((988, 1319, 1976)):
            s = int(i * 0.09 * SR)
            b = bell(d - i * 0.09, f * pitch, 6)
            put(x, b, s, (0.8 ** i))
    elif fam == "error":
        d = L(0.5)
        sq = np.sign(np.sin(2 * np.pi * 140 * pitch * t(d)))
        x = sq * (np.sin(2 * np.pi * 9 * t(d)) > -0.3) * np.exp(-2 * t(d)) * 0.6
    elif fam == "glitch":
        d = L(0.4)
        g = rng.standard_normal(int(d * SR))
        gate = np.repeat(rng.random(int(d * 60) + 1) > 0.45, int(SR / 60) + 1)[: g.size]
        x = np.sign(g) * np.abs(g) ** 0.3 * gate * 0.6 + chirp(d, 2000, 300) * gate * 0.2
    elif fam == "typing":
        d = L(1.2)
        x = np.zeros(int(d * SR))
        pos = 0.0
        while pos < d - 0.05:
            k = bp_noise(int(0.025 * SR), rng, 1500, 7000) * np.exp(-240 * t(0.025))
            s = int(pos * SR)
            put(x, k, s, rng.uniform(0.6, 1))
            pos += rng.uniform(0.06, 0.15)
    elif fam == "shutter":
        d = L(0.25)
        x = np.zeros(int(d * SR))
        for s0 in (0.0, 0.09):
            c = bp_noise(int(0.04 * SR), rng, 1000, 8000) * np.exp(-120 * t(0.04))
            s = int(s0 * SR)
            put(x, c, s)
    elif fam == "heartbeat":
        d = L(1.0)
        x = np.zeros(int(d * SR))
        for s0, g in ((0.0, 1), (0.22, 0.7)):
            b = chirp(0.18, 70 * pitch, 40 * pitch) * np.exp(-22 * t(0.18)) * g
            s = int(s0 * SR)
            put(x, b, s)
    elif fam == "sparkle":
        d = L(1.0)
        x = np.zeros(int(d * SR))
        for _ in range(int(14 * d)):
            f = rng.uniform(2500, 6000) * pitch
            s = int(rng.uniform(0, d * 0.75) * SR)
            b = bell(0.35, f, 12) * rng.uniform(0.3, 0.8)
            put(x, b, s)
    else:  # whistle
        d = L(0.8)
        x = chirp(d, 900 * pitch, 1800 * pitch) * np.sin(np.pi * t(d) / d)
    if soft:
        a = min(x.size // 3, int(0.03 * SR))
        x[:a] *= np.linspace(0, 1, a)
    r = min(x.size // 4, int(0.01 * SR))
    if r:
        x[-r:] *= np.linspace(1, 0, r)
    x = x / (np.max(np.abs(x)) + 1e-9) * 10 ** (-1 / 20)
    return x.astype(np.float32), fam


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt")
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--len", type=float)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--engine", default="synth", choices=["synth", "elevenlabs"])
    ap.add_argument("--paid-ok", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()

    slug = re.sub(r"[^\w]+", "-", a.prompt.lower()).strip("-")[:40] or "sfx"
    seed = a.seed if a.seed is not None else int(hashlib.sha1(a.prompt.encode()).hexdigest()[:6], 16)
    spec = {"engine": a.engine, "prompt": a.prompt, "len": a.len, "seed": seed if a.engine == "synth" else None}
    made_dir = VS_HOME / "sfx" / "made"
    made_dir.mkdir(parents=True, exist_ok=True)
    out = a.out or made_dir / f"{slug}.wav"
    hit = None if a.fresh else lib_get("sfx", spec)
    if hit:
        print(f"✓ {lib_copy_out(hit, out)} (from library)"); return

    if a.engine == "elevenlabs":
        paid_gate("ElevenLabs sound effects", "billed per generation", a.paid_ok)
        key = require_key("elevenlabs")
        body = {"text": a.prompt, **({"duration_seconds": a.len} if a.len else {})}
        audio = http("https://api.elevenlabs.io/v1/sound-generation", data=json.dumps(body).encode(),
                     headers={"xi-api-key": key, "Content-Type": "application/json"}, timeout=180)
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_name(out.stem + ".el-tmp.mp3")
        raw.write_bytes(audio)
        if out.suffix.lower() == ".mp3":
            raw.replace(out)
        else:  # write exactly -o
            import subprocess
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(raw), "-y", str(out)], check=True)
            raw.unlink()
        fam = "elevenlabs"
    else:
        x, fam = synth(a.prompt, a.len, seed)
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(out, x, SR, subtype="PCM_16")
    lib_put("sfx", spec, out)
    if out.parent.resolve() != made_dir.resolve():  # also keep a copy findable by name for cues
        lib_copy_out(out, made_dir / f"{slug}{out.suffix}")
    print(f"✓ {out} ({fam}) — use in cues as \"{slug}\"")


if __name__ == "__main__":
    main()
