#!/usr/bin/env python3
"""doctor.py — report what the video-studio pipeline can and cannot do on this machine.

Stdlib only, so it runs before anything is installed. Exit code 1 when a
required capability is missing; optional ones only warn.
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

VS_HOME = Path(os.environ.get("VS_HOME", Path.home() / ".video-studio"))
INSTALL = Path(__file__).resolve().parent / "install.sh"


def load_config() -> dict:
    cfg = {}
    f = VS_HOME / "config.env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    return cfg


def run(cmd: list, timeout: int = 60) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout or p.stderr).strip().splitlines()[0] if (p.stdout or p.stderr) else ""
    except Exception as e:  # missing binary, timeout…
        return False, str(e)


def py_imports(py: str, module: str) -> tuple[bool, str]:
    if not py or not Path(py).exists():
        return False, "venv not configured"
    return run([py, "-c", f"import {module}; print(getattr({module}, '__version__', 'ok'))"], timeout=120)


def find_chrome() -> str | None:
    mac = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    for p in mac:
        if Path(p).exists():
            return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def main() -> int:
    cfg = load_config()
    rows: list[tuple[str, bool, str, str, bool]] = []  # name, ok, detail, fix, required

    def check(name, ok, detail, fix, required=True):
        rows.append((name, ok, detail, fix, required))

    ok, d = run(["ffmpeg", "-version"])
    check("ffmpeg", ok, d[:60], "brew install ffmpeg  |  apt install ffmpeg")
    ok, d = run(["node", "--version"])
    major = int(d.lstrip("v").split(".")[0]) if ok and d.startswith("v") else 0
    check("Node.js ≥ 18", major >= 18, d, "brew install node  |  https://nodejs.org")
    chrome = find_chrome()
    check("Chrome (render)", bool(chrome), chrome or "", "Install Google Chrome (HyperFrames can also fetch its own)", False)

    ok, d = py_imports(cfg.get("VS_PY_VI", ""), "vieneu")
    check("Vietnamese voice (VieNeu)", ok, d, f"bash {INSTALL}")
    ok, d = py_imports(cfg.get("VS_PY_VI", ""), "librosa")
    check("Beat detection (librosa)", ok, d, f"bash {INSTALL}")
    ok, d = py_imports(cfg.get("VS_PY_EN", ""), "kokoro_onnx")
    kdir = Path(cfg.get("VS_KOKORO_DIR", VS_HOME / "models/kokoro"))
    has_model = (kdir / "kokoro-v1.0.int8.onnx").exists() and (kdir / "voices-v1.0.bin").exists()
    check("English voice (Kokoro)", ok and has_model, d if has_model else "model files missing", f"bash {INSTALL}", False)
    ok, d = py_imports(cfg.get("VS_PY_ASR", ""), "faster_whisper")
    check("Transcription (faster-whisper)", ok, d, f"bash {INSTALL}")

    ytdlp = cfg.get("VS_YTDLP") or shutil.which("yt-dlp") or ""
    ok, d = run([ytdlp, "--version"]) if ytdlp else (False, "")
    check("Download (yt-dlp)", ok, d, f"bash {INSTALL}", False)

    sfx_dirs = [p for p in cfg.get("VS_SFX_DIRS", str(VS_HOME / "sfx")).split(":") if p]
    n_sfx = sum(1 for d_ in sfx_dirs if Path(d_).exists()
                for f in Path(d_).rglob("*") if f.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac"))
    check("Sound effects", n_sfx > 0, f"{n_sfx} files", "vs sfx synth", False)

    total, _, free = shutil.disk_usage(Path.home())
    check("Free disk ≥ 5 GB", free > 5e9, f"{free / 1e9:.1f} GB free", "free some space (models ~3 GB)", False)

    print(f"video-studio doctor · {platform.system()} {platform.release()} · Python {sys.version.split()[0]}")
    print(f"config: {VS_HOME / 'config.env'} {'(found)' if cfg else '(MISSING — run install.sh)'}\n")
    bad_required = 0
    for name, ok, detail, fix, required in rows:
        mark = "✓" if ok else ("✗" if required else "○")
        line = f"  {mark} {name:<32} {detail}"
        if not ok:
            line += f"\n      → {fix}"
            bad_required += required
        print(line)
    print("\nReady to make video." if not bad_required else f"\n{bad_required} required item(s) missing.")
    return 1 if bad_required else 0


if __name__ == "__main__":
    sys.exit(main())
