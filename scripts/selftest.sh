#!/usr/bin/env bash
# selftest.sh — run the whole pipeline on a tiny Vietnamese script and assert
# MEASURED results (not "the command exited 0"). Used after install and as the
# skill's functional eval. ~4–6 min on a 4-core laptop (no network, no paid service).
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

step "new tools: music · sfx make · mix.json · inspect · check · compose · redact · review · fetch pdf"
"$VS" music make --length 12 --drop 4 --mood upbeat --seed 5 -o bed.wav --fresh
"$VS" music arrange bed.wav --drop-at 6 -o bed-arranged.wav | tee arrange.txt
"$VS_PY_TOOLS" - <<'PY'   # where does the arranged bed's loudness jump most? (direct measurement, no beat tracker)
import numpy as np, soundfile as sf
y, sr = sf.read("bed-arranged.wav"); y = y.mean(1) if y.ndim > 1 else y
hop = int(0.02 * sr); rms = np.sqrt(np.convolve(y ** 2, np.ones(hop) / hop, "same"))[::hop]; w = 20
rise = [rms[i:i + w].mean() - rms[i - w:i].mean() for i in range(w, len(rms) - w)]
open("arranged-drop.txt", "w").write(str(round((int(np.argmax(rise)) + w) * hop / sr, 3)))
PY
"$VS" sfx make "deep impact" -o impact.wav --fresh
cat > mix.json <<'JSON'
{"voice": "voice.wav", "music": {"db": -15, "duck": {"db": 10},
  "beds": [{"file": "bed-arranged.wav", "fade_out": 1, "ease": [{"at": 4, "to_db": -4, "over": 1}]}]},
 "cues": [{"sound": "impact.wav", "t": 1.0, "align": "peak", "gain": "in_band", "weight": "hero"},
          [2.0, "pop", -14]]}
JSON
"$VS" mix mix.json -o mix2.wav --timeline timeline.json --stems stems
"$VS" deliver comp/renders/render.mp4 mix2.wav -o final2.mp4 --sheet sheet.jpg > deliver2.txt
"$VS" inspect final2.mp4 --json --no-sheet > inspect.json
"$VS" check final2.mp4 --size 1080x1920 --fps 30 --script script.txt --json > check.json || true
"$VS_PY_TOOLS" - <<'PY'
import cv2, numpy as np
qr = cv2.resize(cv2.QRCodeEncoder.create().encode("0123456789"), (240, 240), interpolation=cv2.INTER_NEAREST)
img = np.full((720, 1280, 3), 200, np.uint8); img[200:440, 900:1140] = cv2.cvtColor(qr, cv2.COLOR_GRAY2BGR)
cv2.imwrite("qr.png", img)
PY
"$VS" redact scan qr.png -o qr-findings.json
"$VS" redact apply qr.png --from qr-findings.json -o qr-clean.png
"$VS" redact verify qr-clean.png > redact-verify.txt || true
cat > edit.json <<'JSON'
{"size": "540x960", "fps": 30, "clips": [{"src": "qr-clean.png", "duration": 1.5, "kenburns": [1.0, 1.1]},
  {"src": "final2.mp4", "from": 0, "to": 2, "transition": {"type": "fade", "dur": 0.4}}],
 "subtitles": {"srt": "captions.srt", "box": "#00000099"}}
JSON
"$VS" compose edit.json -o composed.mp4
"$VS" review stamp script --project . --note selftest >/dev/null
"$VS" review require script --project . > review.txt
"$VS_PY_TOOLS" - <<'PY'
import pypdfium2 as pdfium
pdf = pdfium.PdfDocument.new(); page = pdf.new_page(400, 300); pdf.save("doc.pdf")
PY
"$VS" fetch doc.pdf --out-dir src-pdf --dpi 72 > fetch.txt

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
# ---- new tools ----
import os
ad = float(open("arranged-drop.txt").read())
check("arrange: drop lands at 6 s ±0.1 (measured)", abs(ad - 6) <= 0.1, f"({ad})")
d2 = open("deliver2.txt").read()
l2 = float(re.search(r"(-?[\d.]+) LUFS integrated", d2).group(1))
check("mix.json master −14 ±0.7 LUFS", abs(l2 + 14) <= 0.7, f"({l2})")
check("stems written", all(os.path.exists(f"stems/stem-{k}.wav") for k in ("voice", "music", "sfx")))
check("contact sheet written", os.path.exists("sheet.jpg"))
ins = json.load(open("inspect.json"))
check("inspect sees 1080x1920 video + audio", any(s.get("width") == 1080 for s in ins["streams"]) and "audio" in ins)
ck = json.load(open("check.json"))
check("check: ready, no blocking issue", ck["ready"], str([i["check"] for i in ck["issues"] if i["level"] == "fail"]))
check("check: speech ≥ 85% of script", ck["measured"].get("speech", {}).get("match", 0) >= 0.85,
      f"({ck['measured'].get('speech', {}).get('match')})")
check("redact: QR found then unreadable", json.load(open("qr-findings.json")) and "no readable QR" in open("redact-verify.txt").read())
cp = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height",
                                "-of", "json", "composed.mp4"], capture_output=True, text=True).stdout)
cd = float(cp["format"]["duration"])
check("compose: 540x960, 1.5+2−0.4 s", cp["streams"][0]["width"] == 540 and abs(cd - 3.1) <= 0.1, f"({cd})")
check("review gate approved", "approved and unchanged" in open("review.txt").read())
check("fetch pdf → page image", os.path.exists("src-pdf/pages/p001.png"))

print(f"\n{'SELFTEST PASSED' if not fails else 'SELFTEST FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
EOF
