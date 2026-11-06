#!/usr/bin/env bash
# render.sh — lint + render a HyperFrames composition safely on small machines, or take stills of it.
#
#   vs render <dir> [-o out.mp4] [--quality draft|looks|delivery] [--transparent] [extra hyperframes flags…]
#   vs render <dir> --stills 1.5,4.2,9 [--out-dir stills]          PNG frames + contact sheet
#   vs render <dir> --thumb thumb.jpg [--at 2.0] [--youtube yt.jpg] [--small small.jpg]
#   vs render <page.html|dir> --pdf sheet.pdf [--paper a4|letter] [--landscape]   printable page (Chrome)
#
# Video renders use segmented capture with --resume: if Chrome hangs or the machine sleeps mid-render,
# re-running the same command reuses the finished segments instead of starting over.
# --transparent renders a ProRes 4444 .mov with alpha (overlay layer for vs compose / an editor).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DIR="${1:?usage: vs render <composition dir> [-o out.mp4] [--quality q] | --stills t,… | --thumb f | --pdf f}"; shift
OUT=""; QUALITY="looks"; EXTRA=(); STILLS=""; OUT_DIR=""; THUMB=""; AT=""; YT=""; SMALL=""; PDF=""
PAPER="a4"; LANDSCAPE=0; TRANSPARENT=0
while [ $# -gt 0 ]; do
  case "$1" in
    -o|--output) OUT="$2"; shift 2 ;;
    -q|--quality) QUALITY="$2"; shift 2 ;;
    --stills) STILLS="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --thumb) THUMB="$2"; shift 2 ;;
    --at) AT="$2"; shift 2 ;;
    --youtube) YT="$2"; shift 2 ;;
    --small) SMALL="$2"; shift 2 ;;
    --pdf) PDF="$2"; shift 2 ;;
    --paper) PAPER="$2"; shift 2 ;;
    --landscape) LANDSCAPE=1; shift ;;
    --transparent) TRANSPARENT=1; shift ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done

HF="hyperframes@${VS_HYPERFRAMES_VERSION:-0.8.79}"
export DO_NOT_TRACK=1 HYPERFRAMES_TELEMETRY=0 HF_SEGMENTED_CAPTURE=true
unset GEMINI_API_KEY   # hyperframes snapshot would otherwise call Gemini vision (paid) by itself

snap() { # snap <times> <out dir>
  npx -y "$HF" snapshot "$DIR" --at "$1" --no-end -o "$2" >/dev/null 2>&1 || {
    echo "✗ snapshot failed — try: npx $HF snapshot $DIR --at $1"; exit 1; }
}

if [ -n "$PDF" ]; then
  PAGE="$DIR"; [ -d "$DIR" ] && PAGE="$DIR/index.html"
  ARGS=("file://$(cd "$(dirname "$PAGE")" && pwd)/$(basename "$PAGE")" --pdf "$PDF" --paper "$PAPER")
  [ "$LANDSCAPE" = 1 ] && ARGS+=(--landscape)
  if ! node "$HERE/web-page-capture.cjs" "${ARGS[@]}" >/dev/null || [ ! -s "$PDF" ]; then
    echo "✗ PDF capture failed for $PAGE"; exit 1
  fi
  echo "✓ $PDF ($PAPER)"
  exit 0
fi

if [ -n "$STILLS" ]; then
  OUT_DIR="${OUT_DIR:-$DIR/stills}"; mkdir -p "$OUT_DIR"
  snap "$STILLS" "$OUT_DIR"
  echo "✓ stills → $OUT_DIR: $(ls "$OUT_DIR"/frame-*.png 2>/dev/null | wc -l | tr -d ' ') frame(s) + contact-sheet.jpg"
  exit 0
fi

if [ -n "$THUMB" ]; then
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  snap "${AT:-1.0}" "$TMP"
  SRC="$(ls "$TMP"/frame-*.png | head -1)"
  ffmpeg -v error -i "$SRC" -q:v 2 -y "$THUMB"
  [ -n "$YT" ] && ffmpeg -v error -i "$SRC" -vf "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720" -q:v 2 -y "$YT"
  [ -n "$SMALL" ] && ffmpeg -v error -i "$SRC" -vf "scale=480:-2" -q:v 3 -y "$SMALL"
  echo "✓ thumbnail $THUMB${YT:+ · youtube $YT}${SMALL:+ · small $SMALL} (frame at ${AT:-1.0}s)"
  exit 0
fi

if [ "$TRANSPARENT" = 1 ]; then
  OUT="${OUT:-$DIR/renders/render.mov}"; EXTRA+=(--format mov)
else
  OUT="${OUT:-$DIR/renders/render.mp4}"
fi
mkdir -p "$(dirname "$OUT")"

echo "▸ lint"
npx -y "$HF" lint "$DIR" || { echo "✗ lint failed — fix the composition first"; exit 1; }

echo "▸ render ($QUALITY) → $OUT"
start=$(date +%s)
npx -y "$HF" render "$DIR" -o "$OUT" -q "$QUALITY" --resume ${EXTRA[@]+"${EXTRA[@]}"}
echo "✓ rendered in $(( $(date +%s) - start ))s → $OUT"
[ "$TRANSPARENT" = 1 ] || echo "  next: vs deliver $OUT mix.wav -o final.mp4   (swaps in the master mix + verifies loudness)"
