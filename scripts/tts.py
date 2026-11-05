#!/usr/bin/env python3
"""tts.py — narration from a script file, one line at a time.

Each non-empty line of the script is synthesized separately and joined with a
short pause (blank line = paragraph = longer pause). Because we place every
line ourselves, line start/end times are exact without any transcription.

Outputs in -o DIR:
  voice.wav    mono narration
  lines.json   [{"i", "text", "start", "end", "para"}]
  .tts-cache/  per-line wavs keyed by (engine, voice, speed, text) → editing one
               line of the script only re-synthesizes that line.

Engines: vieneu (Vietnamese, offline, 48 kHz) · kokoro (English, offline, 24 kHz)
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

LINE_GAP = 0.28   # seconds between lines inside a paragraph
PARA_GAP = 0.65   # seconds between paragraphs
VS_HOME = Path(os.environ.get("VS_HOME", Path.home() / ".video-studio"))


def kokoro_dir() -> Path:
    cfg = VS_HOME / "config.env"
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            if line.startswith("VS_KOKORO_DIR="):
                return Path(line.split("=", 1)[1].strip().strip('"'))
    return VS_HOME / "models" / "kokoro"


class Engine:
    """Thin adapter so the rest of the script is engine-agnostic."""

    def __init__(self, name: str, voice: str | None, speed: float, lang: str):
        self.name, self.speed, self.lang = name, speed, lang
        if name == "vieneu":
            from vieneu import Vieneu
            self.tts = Vieneu()  # v3turbo, ONNX on CPU
            self.sr = self.tts.sample_rate
            self.voice = voice or "Hải Đăng"
        elif name == "kokoro":
            from kokoro_onnx import Kokoro
            d = kokoro_dir()
            self.tts = Kokoro(str(d / "kokoro-v1.0.int8.onnx"), str(d / "voices-v1.0.bin"))
            self.sr = 24000
            self.voice = voice or "af_heart"
        else:
            sys.exit(f"unknown engine {name}")

    def voices(self) -> list[str]:
        if self.name == "vieneu":
            return [f"{vid}  ({label})" for label, vid in self.tts.list_preset_voices()]
        return self.tts.get_voices()

    def say(self, text: str) -> np.ndarray:
        if self.name == "vieneu":
            wav = self.tts.infer(text, voice=self.tts.get_preset_voice(self.voice))
        else:
            wav, sr = self.tts.create(text, voice=self.voice, speed=self.speed, lang=self.lang)
            self.sr = sr
        return np.asarray(wav, dtype=np.float32).reshape(-1)


def trim_silence(wav: np.ndarray, sr: int, thresh: float = 0.01, pad: float = 0.04) -> np.ndarray:
    """Cut leading/trailing near-silence so our own gaps control the pacing."""
    idx = np.where(np.abs(wav) > thresh)[0]
    if idx.size == 0:
        return wav
    a = max(0, idx[0] - int(pad * sr))
    b = min(len(wav), idx[-1] + int(pad * sr))
    return wav[a:b]


def parse_script(path: Path) -> list[tuple[str, int]]:
    """→ [(line text, paragraph index)]; lines starting with # are comments."""
    out, para, prev_blank = [], 0, False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            prev_blank = bool(out)
            continue
        if line.startswith("#"):
            continue
        if prev_blank:
            para += 1
            prev_blank = False
        out.append((line, para))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", nargs="?", type=Path)
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("."))
    ap.add_argument("--engine", default="vieneu", choices=["vieneu", "kokoro"])
    ap.add_argument("--voice", help='vieneu: "Hải Đăng", "Mai Anh"… · kokoro: af_heart, am_michael…')
    ap.add_argument("--speed", type=float, default=1.0, help="kokoro only")
    ap.add_argument("--lang", default="en-us", help="kokoro only: en-us, en-gb")
    ap.add_argument("--line-gap", type=float, default=LINE_GAP)
    ap.add_argument("--para-gap", type=float, default=PARA_GAP)
    ap.add_argument("--list-voices", action="store_true")
    a = ap.parse_args()

    eng = Engine(a.engine, a.voice, a.speed, a.lang)
    if a.list_voices:
        print("\n".join(eng.voices()))
        return
    if not a.script:
        ap.error("script file required")

    lines = parse_script(a.script)
    if not lines:
        sys.exit("script is empty")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    cache = a.out_dir / ".tts-cache"
    cache.mkdir(exist_ok=True)

    pieces, meta, t, prev_para = [], [], 0.0, None
    for i, (text, para) in enumerate(lines):
        spoken = text.replace("*", "")
        key = hashlib.sha1(f"{a.engine}|{eng.voice}|{a.speed}|{a.lang}|{spoken}".encode()).hexdigest()[:16]
        cpath = cache / f"{key}.wav"
        if cpath.exists():
            wav, sr = sf.read(cpath, dtype="float32")
            eng.sr = sr
            status = "cached"
        else:
            wav = trim_silence(eng.say(text.replace("*", "")), eng.sr)  # *word* = visual accent only
            sf.write(cpath, wav, eng.sr)
            status = "new"
        if prev_para is not None:
            gap = a.para_gap if para != prev_para else a.line_gap
            pieces.append(np.zeros(int(gap * eng.sr), dtype=np.float32))
            t += gap
        dur = len(wav) / eng.sr
        meta.append({"i": i, "text": text, "start": round(t, 3), "end": round(t + dur, 3), "para": para})
        pieces.append(wav)
        t += dur
        prev_para = para
        print(f"  [{i + 1}/{len(lines)}] {status:6} {dur:5.2f}s  {text[:60]}")

    voice = np.concatenate(pieces)
    sf.write(a.out_dir / "voice.wav", voice, eng.sr)
    (a.out_dir / "lines.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ voice.wav {t:.2f}s @ {eng.sr} Hz · lines.json ({len(meta)} lines) → {a.out_dir}")


if __name__ == "__main__":
    main()
