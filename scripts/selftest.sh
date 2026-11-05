#!/usr/bin/env bash
# selftest.sh — run the whole pipeline on a tiny Vietnamese script and assert
# MEASURED results (not "the command exited 0"). Used after install and as the
# skill's functional eval. ~2–4 min on a 4-core laptop.
#
#   vs selftest [--keep]     (--keep leaves the work folder for inspection)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VS="$HERE/vs"
VS_HOME="${VS_HOME:-$HOME/.video-studio}"
# shellcheck disable=SC1090
source "$VS_HOME/config.env"
W="$(mktemp -d "${TMPDIR:-/tmp}/vs-selftest.XXXX")"
KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1
cleanup() { [ "$KEEP" = 1 ] && echo "work folder: $W" || rm -rf "$W"; }
trap cleanup EXIT
cd "$W"

step() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

cat > script.txt <<'EOF'
Bạn có biết não người chỉ tập trung được khoảng *tám giây*?

Tin tốt là, bạn có thể luyện ngược lại.
EOF

step "music (synthetic 120 BPM, energy jump at 6 s)"
"$VS_PY_VI" - <<'EOF'
import numpy as np, soundfile as sf
sr = 44100; dur = 12; t = np.arange(sr * dur) / sr; y = np.zeros_like(t)
for k in range(int(dur / 0.5)):
    s = int(k * 0.5 * sr); n = int(0.25 * sr); tt = np.arange(n) / sr
    f = np.linspace(110, 45, n)
    y[s:s + n] += (1.0 if k % 4 == 0 else 0.55) * np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-12 * tt)
y = y * 0.6 + 0.12 * np.sin(2 * np.pi * 220 * t) * (t > 6)
sf.write("music.wav", y.astype(np.float32), sr)
EOF

step "tts";        "$VS" tts script.txt -o . 2>&1 | grep -E '\[|✓'
step "transcribe"; "$VS" transcribe voice.wav -o words.json --script script.txt --lines lines.json 2>&1 | grep -vi warn | tail -3
step "beats";      "$VS" beats music.wav -o beats.json
step "timeline";   "$VS" timeline --lines lines.json --words words.json --beats beats.json -o . --max-words 3
echo '[[0.0,"whoosh",-12],[1.7,"pop",-16]]' > cues.json
step "mix";        "$VS" mix --voice voice.wav --music music.wav --cues cues.json --timeline timeline.json -o mix.wav
step "compose";    "$VS" new comp --portrait --timeline timeline.js --mix mix.wav
step "render";     "$VS" render comp -o comp/renders/render.mp4 -q draft 2>&1 | grep -E '✓|✗|Error' || true
step "deliver";    "$VS" deliver comp/renders/render.mp4 mix.wav -o final.mp4 | tee deliver.txt

step "assertions"
python3 - <<'EOF'
import json, re, subprocess, sys
fails = []
def check(name, ok, detail=""):
    print(f"  {'✓' if ok else '✗'} {name} {detail}")
    if not ok: fails.append(name)

lines = json.load(open("lines.json")); words = json.load(open("words.json"))
tl = json.load(open("timeline.json")); beats = json.load(open("beats.json"))
check("2 lines voiced", len(lines) == 2, f"({len(lines)})")
check("no '*' in captions.srt", "*" not in open("captions.srt", encoding="utf-8").read())
script_words = [w for w in open("script.txt", encoding="utf-8").read().split() if w.strip("*?,.")]
check("one timed word per script word", len(words) == len(script_words), f"({len(words)}/{len(script_words)})")
check("words monotonic", all(a["start"] <= b["start"] for a, b in zip(words, words[1:])))
check("tempo ≈ 120 BPM (±6%)", abs(beats["tempo"] - 120) <= 7.2 or abs(beats["tempo"] - 60) <= 3.6, f"({beats['tempo']})")
check("drop found near 6 s", any(abs(d - 6) < 0.6 for d in beats["drops"]), f"({beats['drops']})")
check("captions ≤ 3 words", all(len(c["words"]) <= 3 for c in tl["captions"]))
check("scene cut never after speech", all(s["cut"] <= s["start"] + 1e-6 for s in tl["scenes"]))
d = open("deliver.txt").read()
lufs = float(re.search(r"(-?[\d.]+) LUFS integrated", d).group(1))
check("final loudness −14 ±0.7 LUFS", abs(lufs + 14) <= 0.7, f"({lufs})")
vd = float(re.search(r"fps · ([\d.]+)s", d).group(1)); ad = float(re.search(r"Hz · ([\d.]+)s", d).group(1))
check("A/V length match ±0.15 s", abs(vd - ad) <= 0.15, f"({vd}/{ad})")
check("video 1080x1920", "1080x1920" in d)
check("duration = timeline", abs(vd - tl["duration"]) <= 0.15, f"({vd} vs {tl['duration']})")
print(f"\n{'SELFTEST PASSED' if not fails else 'SELFTEST FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
EOF
