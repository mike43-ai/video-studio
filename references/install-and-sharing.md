# Install & sharing

## What install.sh does
| Step | Where | Size |
|---|---|---|
| ffmpeg, Node ≥ 18 (brew/apt if missing) | system | — |
| Python 3.11/3.12 (needed: onnxruntime wheels) | system / uv | — |
| venv `main`: vieneu, librosa, soundfile, numpy, yt-dlp | `~/.video-studio/venvs/main` | ~600 MB |
| VieNeu v3 Turbo model (first `vs tts`) | `~/.cache/huggingface` | ~1 GB |
| venv `en`: kokoro-onnx + model files | `~/.video-studio/venvs/en`, `models/kokoro` | ~200 MB |
| venv `asr`: faster-whisper; large-v3-turbo on first use | `~/.video-studio/venvs/asr`, HF cache | ~1.8 GB |
| HyperFrames (npx cache, pinned version) | `~/.npm/_npx` | ~300 MB |
| Synth SFX kit (12 sounds, generated, free to share) | `~/.video-studio/sfx/synth` | <2 MB |
| venv `tools`: numpy, scipy, Pillow, soundfile, pypdfium2, opencv-python-headless (wheels only) | `~/.video-studio/venvs/tools` | ~450 MB |
| puppeteer-core (uses your Chrome; no browser download) | `~/.video-studio/node` | ~20 MB |
| u2net cutout model (first `vs matte`) | HyperFrames cache | ~170 MB |

Config lives in `~/.video-studio/config.env` (`VS_PY_VI`, `VS_PY_EN`, `VS_PY_ASR`, `VS_PY_TOOLS`, `VS_KOKORO_DIR`,
`VS_YTDLP`, `VS_SFX_DIRS`, optional `VS_HYPERFRAMES_VERSION`, `VS_CHROME`). API keys: `~/.video-studio/keys.env`
via `vs keys set` (never in config.env). Other folders: `library/` (generated assets), `cache/` (API responses),
`logs/errors.log` (for `vs report`). Edit it to point at existing venvs or add SFX folders
(colon-separated).

Flags: `--skip-en`, `--skip-asr`, `--reuse-reelcrew` (no downloads; points config at `~/.reelcrew/runtime`).

## Platforms
- **macOS** (Intel or Apple Silicon): tested path. Needs Homebrew for missing ffmpeg/node.
- **Linux** (Debian/Ubuntu): apt path; install Chrome or let HyperFrames download one.
- **Windows**: run inside WSL2 (Ubuntu) — the scripts are bash + Python.

## Sharing with friends
The skill folder is self-contained (no paid or proprietary code). Friend's steps:
```bash
git clone https://github.com/mike43-ai/video-studio ~/.claude/skills/video-studio
bash ~/.claude/skills/video-studio/scripts/install.sh
~/.claude/skills/video-studio/scripts/vs doctor
```
Then in Claude Code: "làm video ngắn 30 giây về …" → the skill triggers.
Update later: `vs update` (or `git -C ~/.claude/skills/video-studio pull`), then re-run `install.sh` if `vs doctor` shows ✗.

## Third-party licenses
VieNeu-TTS (Apache-2.0), kokoro-onnx (MIT) + Kokoro model (Apache-2.0), faster-whisper (MIT), librosa (ISC), yt-dlp (Unlicense),
HyperFrames (see its npm license), GSAP (free "Standard" license), Be Vietnam Pro (OFL), OpenCV (Apache-2.0),
pypdfium2 (Apache-2.0/BSD), puppeteer-core (Apache-2.0), Pillow (MIT-CMU), SciPy/NumPy (BSD).
Online data: Wikimedia Commons (per-file licence, in credits.json), Pixabay (Pixabay Content License),
OpenStreetMap (ODbL — credit "© OpenStreetMap contributors"), Openverse CC0 audio. Models are downloaded
by each user from their official sources, not redistributed here.
