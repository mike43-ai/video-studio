#!/usr/bin/env python3
"""beats.py — beat grid, downbeats and "drops" of a music track (librosa).

Output beats.json:
  {"tempo", "duration", "beats": [s…], "downbeats": [s…], "drops": [s…], "onsets": [s…]}

downbeats: every 4th beat, phase chosen where onset energy is strongest
           (good enough for 4/4 pop/EDM; check by ear for other metres).
drops:     moments where loudness jumps most (big statement / title goes here).
"""
import argparse
import json
from pathlib import Path

import librosa
import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("music")
    ap.add_argument("-o", "--out", type=Path, default=Path("beats.json"))
    ap.add_argument("--drops", type=int, default=3, help="how many drops to report")
    a = ap.parse_args()

    y, sr = librosa.load(a.music, sr=22050, mono=True)
    dur = len(y) / sr
    hop = 512
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=hop)
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=hop)
    tempo = float(np.atleast_1d(tempo)[0])

    # Downbeat phase: which of the 4 offsets has the most onset energy on average.
    strength = env[np.clip(frames, 0, len(env) - 1)] if len(frames) else np.array([])
    phase = int(np.argmax([strength[p::4].mean() if strength[p::4].size else 0 for p in range(4)])) if strength.size else 0
    downbeats = beats[phase::4]

    # Drops: largest rise in RMS between the 2 s before and the 2 s after a downbeat.
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    t_rms = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    win = 2.0
    scored = []
    for t in downbeats:
        before = rms[(t_rms >= t - win) & (t_rms < t)]
        after = rms[(t_rms >= t) & (t_rms < t + win)]
        if before.size and after.size:
            scored.append((float(after.mean() - before.mean()), float(t)))
    scored.sort(reverse=True)
    drops: list[float] = []
    for _, t in scored:  # keep drops at least 8 s apart
        if all(abs(t - d) > 8 for d in drops):
            drops.append(t)
        if len(drops) >= a.drops:
            break

    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop, units="time")
    r = lambda xs: [round(float(x), 3) for x in xs]
    out = {"tempo": round(tempo, 2), "duration": round(dur, 3), "beats": r(beats),
           "downbeats": r(downbeats), "drops": r(sorted(drops)), "onsets": r(onsets)}
    a.out.write_text(json.dumps(out, indent=1))
    print(f"✓ {tempo:.1f} BPM · {len(beats)} beats · {len(downbeats)} downbeats · drops {out['drops']} → {a.out}")


if __name__ == "__main__":
    main()
