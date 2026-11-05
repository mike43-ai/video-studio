#!/usr/bin/env python3
"""mix.py — voice + music bed + sound cues → one master at −14 LUFS (ffmpeg only).

  vs mix --voice voice.wav --music bed.mp3 --cues cues.json -o mix.wav \
         [--duration 42.5 | --timeline timeline.json] [--music-db -16] [--target -14]

- Music loops to fill the length, sits --music-db under unity, fades out at the end,
  and DUCKS under the voice (sidechaincompress keyed by the voice).
- cues.json: [[time_s, "pop" | "path/to.wav", peak_db], …] — peak_db is where the
  sound's peak lands (voice peaks ≈ −3…−6 dBFS → hits −9…−12, clicks/pops −14…−20).
- Two-pass loudnorm to --target LUFS integrated, true peak ≤ --tp dBTP, 48 kHz stereo.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sfx import resolve  # noqa: E402  (same folder)


def ff(args: list[str]) -> str:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{p.stderr[-2000:]}")
    return p.stderr


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def peak_db(path: Path) -> float:
    m = re.search(r"max_volume: (-?[\d.]+) dB", ff(["-i", str(path), "-af", "volumedetect", "-f", "null", "-"]))
    return float(m.group(1)) if m else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice", type=Path)
    ap.add_argument("--music", type=Path)
    ap.add_argument("--cues", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("mix.wav"))
    ap.add_argument("--duration", type=float, help="master length (default: voice + --tail)")
    ap.add_argument("--timeline", type=Path, help="take duration from timeline.json")
    ap.add_argument("--tail", type=float, default=1.0)
    ap.add_argument("--music-db", type=float, default=-16.0, help="bed level before ducking")
    ap.add_argument("--duck", type=float, default=8.0, help="duck ratio (higher = music drops more under voice)")
    ap.add_argument("--fade", type=float, default=1.5, help="music fade-out seconds")
    ap.add_argument("--target", type=float, default=-14.0, help="LUFS integrated")
    ap.add_argument("--tp", type=float, default=-1.5, help="true-peak ceiling dBTP")
    a = ap.parse_args()
    if not (a.voice or a.music):
        ap.error("need --voice and/or --music")

    if a.duration:
        D = a.duration
    elif a.timeline:
        D = json.loads(a.timeline.read_text(encoding="utf-8"))["duration"]
    elif a.voice:
        D = duration(a.voice) + a.tail
    else:
        D = duration(a.music)

    inputs, chains, mixins = [], [], []
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    idx = 0
    if a.voice:
        inputs += ["-i", str(a.voice)]
        chains.append(f"[{idx}:a]{fmt},apad=whole_dur={D:.3f}" + (",asplit=2[v][vkey]" if a.music else "[v]"))
        mixins.append("[v]"); idx += 1
    if a.music:
        inputs += ["-stream_loop", "-1", "-i", str(a.music)]
        st = max(0.0, D - a.fade)
        chain = f"[{idx}:a]{fmt},atrim=0:{D:.3f},volume={a.music_db}dB,afade=t=out:st={st:.3f}:d={a.fade}"
        if a.voice:
            chains.append(chain + "[m0]")
            chains.append(f"[m0][vkey]sidechaincompress=threshold=0.02:ratio={a.duck}:attack=15:release=400:knee=4[m]")
        else:
            chains.append(chain + "[m]")
        mixins.append("[m]"); idx += 1

    cues = json.loads(a.cues.read_text()) if a.cues else []
    for n, (t, name, pk) in enumerate(cues):
        path = resolve(str(name))
        if not path:
            print(f"  ⚠ cue {name!r} not found — skipped (vs sfx find …)"); continue
        gain = float(pk) - peak_db(path)
        ms = int(float(t) * 1000)
        inputs += ["-i", str(path)]
        chains.append(f"[{idx}:a]{fmt},volume={gain:.2f}dB,adelay={ms}|{ms}[s{n}]")
        mixins.append(f"[s{n}]"); idx += 1

    graph = ";".join(chains) + f";{''.join(mixins)}amix=inputs={len(mixins)}:normalize=0:duration=longest,atrim=0:{D:.3f}[pre]"
    with tempfile.TemporaryDirectory() as tmp:
        pre = Path(tmp) / "pre.wav"
        ff([*inputs, "-filter_complex", graph, "-map", "[pre]", "-ar", "48000", "-c:a", "pcm_f32le", "-y", str(pre)])
        # pass 1: measure
        err = ff(["-i", str(pre), "-af", f"loudnorm=I={a.target}:TP={a.tp}:LRA=11:print_format=json", "-f", "null", "-"])
        m = json.loads(err[err.rindex("{"): err.rindex("}") + 1])
        ln = (f"loudnorm=I={a.target}:TP={a.tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
              f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
        # pass 2: apply
        ff(["-i", str(pre), "-af", f"{ln},aresample=48000", "-c:a", "pcm_s24le", "-y", str(a.out)])
    print(f"✓ {a.out} · {D:.2f}s · {len(mixins)} layers · input {m['input_i']} LUFS → target {a.target} LUFS / {a.tp} dBTP")


if __name__ == "__main__":
    main()
