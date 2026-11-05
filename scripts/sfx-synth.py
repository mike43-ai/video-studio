#!/usr/bin/env python3
"""sfx-synth.py — generate a small royalty-free SFX kit from pure math (numpy).

Everything here is synthesized, so the kit can be shared freely with the skill.
Sounds: whoosh, whoosh-short, pop, click, tick, ding, chime, impact, riser,
glitch, swoosh-down, typing. 48 kHz mono 16-bit WAV, peak −1 dBFS.
"""
import argparse
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 48000
rng = np.random.default_rng(7)  # deterministic: same kit on every machine


def t(d): return np.arange(int(d * SR)) / SR
def env_exp(d, k): return np.exp(-k * t(d))


def adsr(n, a=0.005, r=0.05):
    e = np.ones(n)
    na = min(max(1, int(a * SR)), n // 2)
    nr = min(max(1, int(r * SR)), n // 2)
    e[:na] = np.linspace(0, 1, na)
    e[-nr:] *= np.linspace(1, 0, nr)
    return e


def lowpass(x, alpha):  # one-pole; alpha 0..1 (smaller = darker)
    y = np.empty_like(x); acc = 0.0
    for i, v in enumerate(x):
        acc += alpha * (v - acc); y[i] = acc
    return y


def sweep_noise(d, a0, a1):
    n = rng.standard_normal(int(d * SR))
    alphas = np.linspace(a0, a1, n.size)
    y = np.empty_like(n); acc = 0.0
    for i, v in enumerate(n):
        acc += alphas[i] * (v - acc); y[i] = acc
    return y


def tone(d, f, k=6.0, harm=(1.0,)):
    x = sum(w * np.sin(2 * np.pi * f * (i + 1) * t(d)) for i, w in enumerate(harm))
    return x * env_exp(d, k)


def kit() -> dict[str, np.ndarray]:
    s = {}
    d = 0.6; s["whoosh"] = sweep_noise(d, 0.02, 0.35) * np.sin(np.pi * t(d) / d) ** 2
    d = 0.3; s["whoosh-short"] = sweep_noise(d, 0.05, 0.5) * np.sin(np.pi * t(d) / d) ** 2
    d = 0.5; s["swoosh-down"] = sweep_noise(d, 0.4, 0.03) * np.sin(np.pi * t(d) / d) ** 1.5
    d = 0.12; f = np.linspace(900, 300, int(d * SR)); s["pop"] = np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(d, 35)
    d = 0.03; s["click"] = rng.standard_normal(int(d * SR)) * env_exp(d, 300)
    d = 0.05; s["tick"] = tone(d, 2400, 120)
    s["ding"] = tone(1.2, 1318.5, 4.5, (1.0, 0.35, 0.12))
    s["chime"] = np.concatenate([tone(0.18, 1046.5, 8)[: int(0.15 * SR)], tone(1.0, 1568.0, 4, (1.0, 0.3))])
    d = 0.8; f = np.linspace(120, 38, int(d * SR))
    s["impact"] = np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(d, 5) + 0.5 * lowpass(rng.standard_normal(int(d * SR)), 0.08) * env_exp(d, 14)
    d = 2.0; f = np.geomspace(150, 1600, int(d * SR))
    s["riser"] = (0.6 * np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.4 * sweep_noise(d, 0.02, 0.6)) * (t(d) / d) ** 2
    g = rng.standard_normal(int(0.35 * SR)); gate = (np.sin(2 * np.pi * 37 * t(0.35)) > 0.2).astype(float)
    s["glitch"] = np.sign(g) * np.abs(g) ** 0.3 * gate * 0.6
    keys = []
    for _ in range(10):
        k = rng.standard_normal(int(0.025 * SR)) * env_exp(0.025, 250)
        keys += [k, np.zeros(int(rng.uniform(0.05, 0.14) * SR))]
    s["typing"] = np.concatenate(keys)
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path.home() / ".video-studio/sfx/synth")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for name, x in kit().items():
        x = x * adsr(x.size)
        x = x / (np.max(np.abs(x)) + 1e-9) * 10 ** (-1 / 20)
        sf.write(a.out / f"{name}.wav", x.astype(np.float32), SR, subtype="PCM_16")
    print(f"✓ {len(kit())} synth sounds → {a.out}")


if __name__ == "__main__":
    main()
