#!/usr/bin/env bash
# render.sh — lint + render a HyperFrames composition safely on small machines.
#
#   vs render <dir> [-o out.mp4] [--quality draft|looks|delivery] [extra hyperframes flags…]
#
# Uses segmented capture with --resume: if Chrome hangs or the machine sleeps
# mid-render, re-running the same command reuses the finished segments instead
# of starting over (the "render 30 s straight → Chrome freezes" problem).
set -euo pipefail

DIR="${1:?usage: vs render <composition dir> [-o out.mp4] [--quality q]}"; shift
OUT="$DIR/renders/render.mp4"; QUALITY="looks"; EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    -o|--output) OUT="$2"; shift 2 ;;
    -q|--quality) QUALITY="$2"; shift 2 ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done

HF="hyperframes@${VS_HYPERFRAMES_VERSION:-0.8.79}"
export DO_NOT_TRACK=1 HYPERFRAMES_TELEMETRY=0 HF_SEGMENTED_CAPTURE=true
mkdir -p "$(dirname "$OUT")"

echo "▸ lint"
npx -y "$HF" lint "$DIR" || { echo "✗ lint failed — fix the composition first"; exit 1; }

echo "▸ render ($QUALITY) → $OUT"
start=$(date +%s)
npx -y "$HF" render "$DIR" -o "$OUT" -q "$QUALITY" --resume ${EXTRA[@]+"${EXTRA[@]}"}
echo "✓ rendered in $(( $(date +%s) - start ))s → $OUT"
echo "  next: vs deliver $OUT mix.wav -o final.mp4   (swaps in the master mix + verifies loudness)"
