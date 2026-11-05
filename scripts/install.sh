#!/usr/bin/env bash
# install.sh — set up everything the video-studio skill needs on macOS/Linux.
#
#   bash install.sh                 full install into ~/.video-studio
#   bash install.sh --reuse-reelcrew  point at an existing Reelcrew runtime (no downloads)
#   bash install.sh --skip-en       skip English (Kokoro) voice
#   bash install.sh --skip-asr      skip transcription (faster-whisper)
#
# Creates Python 3.11/3.12 venvs (onnxruntime has no wheels for 3.13+ on some
# platforms), downloads the Kokoro model, synthesizes a free SFX kit, and
# writes ~/.video-studio/config.env. Safe to re-run: finished steps are skipped.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VS_HOME="${VS_HOME:-$HOME/.video-studio}"
VENVS="$VS_HOME/venvs"
CONFIG="$VS_HOME/config.env"
KOKORO_DIR="$VS_HOME/models/kokoro"
KOKORO_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

REUSE=0; SKIP_EN=0; SKIP_ASR=0
for a in "$@"; do
  case "$a" in
    --reuse-reelcrew) REUSE=1 ;;
    --skip-en) SKIP_EN=1 ;;
    --skip-asr) SKIP_ASR=1 ;;
    *) echo "unknown flag $a"; exit 1 ;;
  esac
done

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
mkdir -p "$VS_HOME" "$VENVS" "$VS_HOME/sfx"

write_config() { # key value — upsert a line in config.env
  touch "$CONFIG"
  grep -v "^$1=" "$CONFIG" > "$CONFIG.tmp" || true
  echo "$1=\"$2\"" >> "$CONFIG.tmp"
  mv "$CONFIG.tmp" "$CONFIG"
}

# ---------- reuse an existing Reelcrew runtime (open-source venvs only) ----------
if [ "$REUSE" = 1 ]; then
  RC="$HOME/.reelcrew"
  [ -x "$RC/runtime/vieneu/bin/python" ] && write_config VS_PY_VI "$RC/runtime/vieneu/bin/python"
  [ -x "$RC/runtime/whisper/bin/python" ] && write_config VS_PY_ASR "$RC/runtime/whisper/bin/python"
  KPY="$(python3 -c "import json;print(json.load(open('$RC/config.json'))['tts']['adapters']['kokoro']['python'])" 2>/dev/null || true)"
  [ -n "$KPY" ] && [ -x "$KPY" ] && write_config VS_PY_EN "$KPY"
  [ -f "$RC/models/kokoro/kokoro-v1.0.int8.onnx" ] && write_config VS_KOKORO_DIR "$RC/models/kokoro"
  for y in "$RC"/runtime/yt-dlp/yt-dlp "$RC"/runtime/yt-dlp/yt-dlp_*; do
    if [ -f "$y" ] && [ -x "$y" ]; then write_config VS_YTDLP "$y"; break; fi
  done
  [ -d "$RC/runtime/sfx-freesound" ] && write_config VS_SFX_DIRS "$VS_HOME/sfx:$RC/runtime/sfx-freesound"
  write_config VS_PY_SYSTEM "$(command -v python3)"
  say "Pointed config at Reelcrew runtime → $CONFIG"
  "$(grep VS_PY_VI "$CONFIG" | cut -d'"' -f2)" "$HERE/sfx-synth.py" --out "$VS_HOME/sfx/synth" || true
  python3 "$HERE/doctor.py"; exit 0
fi

# ---------- system tools ----------
say "Checking system tools"
OS="$(uname -s)"
missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
command -v node >/dev/null || missing+=(node)
if [ ${#missing[@]} -gt 0 ]; then
  if [ "$OS" = "Darwin" ] && command -v brew >/dev/null; then
    echo "Installing via Homebrew: ${missing[*]}"; brew install "${missing[@]}"
  elif command -v apt-get >/dev/null; then
    echo "Installing via apt: ${missing[*]}"; sudo apt-get update && sudo apt-get install -y ffmpeg nodejs npm
  else
    echo "✗ Please install manually: ${missing[*]} (ffmpeg ≥ 6, Node ≥ 18)"; exit 1
  fi
fi

PY=""
for c in python3.11 python3.12 "$HOME/.local/bin/python3.11"; do
  if command -v "$c" >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
done
if [ -z "$PY" ]; then
  if command -v uv >/dev/null; then uv python install 3.11 && PY="$(uv python find 3.11)"
  else
    echo "✗ Need Python 3.11 or 3.12. Install one of:"
    echo "    brew install python@3.11      (macOS)"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.11"
    exit 1
  fi
fi
echo "Python for venvs: $PY ($("$PY" --version))"
write_config VS_PY_SYSTEM "$(command -v python3)"

make_venv() { # name packages...
  local name="$1"; shift
  if [ ! -x "$VENVS/$name/bin/python" ]; then "$PY" -m venv "$VENVS/$name"; fi
  "$VENVS/$name/bin/python" -m pip install -q --upgrade pip
  "$VENVS/$name/bin/python" -m pip install -q "$@"
}

# ---------- Vietnamese voice + audio analysis + yt-dlp (main venv) ----------
say "Vietnamese voice (VieNeu-TTS), librosa, yt-dlp → venv 'main'"
# Intel Macs: llvmlite ≥ 0.45 (numba → librosa) ships no x86_64 macOS wheel and fails to compile → pin.
PINS=()
if [ "$OS" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ]; then PINS=("llvmlite<0.45" "numba<0.62"); fi
make_venv main vieneu librosa soundfile numpy yt-dlp ${PINS[@]+"${PINS[@]}"}
write_config VS_PY_VI "$VENVS/main/bin/python"
write_config VS_YTDLP "$VENVS/main/bin/yt-dlp"
echo "(VieNeu downloads its model from Hugging Face on the first 'vs tts' run, ~1 GB)"

# ---------- English voice ----------
if [ "$SKIP_EN" = 0 ]; then
  say "English voice (Kokoro ONNX) → venv 'en'"
  make_venv en kokoro-onnx soundfile
  mkdir -p "$KOKORO_DIR"
  for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
    [ -s "$KOKORO_DIR/$f" ] || curl -fL --progress-bar -o "$KOKORO_DIR/$f" "$KOKORO_URL/$f"
  done
  write_config VS_PY_EN "$VENVS/en/bin/python"
  write_config VS_KOKORO_DIR "$KOKORO_DIR"
fi

# ---------- transcription ----------
if [ "$SKIP_ASR" = 0 ]; then
  say "Transcription (faster-whisper) → venv 'asr'"
  make_venv asr faster-whisper
  write_config VS_PY_ASR "$VENVS/asr/bin/python"
  echo "(Whisper large-v3-turbo downloads on the first 'vs transcribe', ~1.6 GB)"
fi

# ---------- HyperFrames + free SFX kit ----------
say "Warming HyperFrames (npx cache)"
npx -y "hyperframes@${VS_HYPERFRAMES_VERSION:-0.8.79}" --version || echo "⚠ HyperFrames warm-up failed; 'vs render' will retry"

say "Synthesizing free SFX kit"
"$VENVS/main/bin/python" "$HERE/sfx-synth.py" --out "$VS_HOME/sfx/synth"
grep -q '^VS_SFX_DIRS=' "$CONFIG" 2>/dev/null || write_config VS_SFX_DIRS "$VS_HOME/sfx"

chmod +x "$HERE/vs"
say "Done. Config: $CONFIG"
echo "Tip: add an alias →  alias vs='$HERE/vs'"
python3 "$HERE/doctor.py" || true
