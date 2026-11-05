#!/usr/bin/env bash
# new-composition.sh — copy the starter composition into a project folder.
#
#   vs new <dir> [--portrait | --landscape | --square] [--timeline path/timeline.js] [--mix path/mix.wav]
#
# Fills width/height and the duration from timeline.js. One folder per version:
# refuses to overwrite an existing index.html (copy to v2/ instead).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TPL="$HERE/../assets/template"

DIR="${1:?usage: vs new <dir> [--portrait|--landscape|--square] [--timeline t.js] [--mix mix.wav]}"; shift
W=1080; H=1920; TL=""; MIX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --portrait) W=1080; H=1920; shift ;;
    --landscape) W=1920; H=1080; shift ;;
    --square) W=1080; H=1080; shift ;;
    --timeline) TL="$2"; shift 2 ;;
    --mix) MIX="$2"; shift 2 ;;
    *) echo "unknown flag $1"; exit 1 ;;
  esac
done

[ -f "$DIR/index.html" ] && { echo "✗ $DIR/index.html exists — make a new version folder instead"; exit 1; }
mkdir -p "$DIR/assets"
[ -n "$TL" ] && cp "$TL" "$DIR/timeline.js"
[ -n "$MIX" ] && cp "$MIX" "$DIR/assets/mix.wav"

DUR="10"
if [ -f "$DIR/timeline.js" ]; then
  DUR="$(sed -E 's/^window\.TIMELINE = //; s/;[[:space:]]*$//' "$DIR/timeline.js" | python3 -c 'import json,sys;print(json.load(sys.stdin)["duration"])')"
fi
sed -e "s/__WIDTH__/$W/g" -e "s/__HEIGHT__/$H/g" -e "s/__DURATION__/$DUR/g" "$TPL/index.html" > "$DIR/index.html"
echo "✓ $DIR/index.html (${W}x${H}, ${DUR}s)"
[ -f "$DIR/timeline.js" ] || echo "  ⚠ no timeline.js yet — run: vs timeline … -o $DIR"
[ -f "$DIR/assets/mix.wav" ] || echo "  ⚠ no assets/mix.wav yet — run: vs mix … -o $DIR/assets/mix.wav"
echo "  preview: npx hyperframes preview $DIR   ·   render: vs render $DIR"
