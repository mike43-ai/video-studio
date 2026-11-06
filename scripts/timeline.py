#!/usr/bin/env python3
"""timeline.py — merge lines, words and beats into ONE timeline every layer reads.

The single source of truth: the composition (visuals, captions) and the mix
(sound cues) both read timeline.json, so nothing drifts.

Scenes: one per line by default (--scene-by para → one per paragraph). With
--beats, each scene cut snaps to the nearest beat within --snap seconds, but
never AFTER the first word of the scene (a cut that lands late looks wrong;
a cut slightly early feels intentional).

Captions: words grouped into short karaoke chunks (≤ --max-words, ≤ --max-chars,
break on punctuation or a pause > 0.35 s).

Outputs in -o DIR: timeline.json · timeline.js (window.TIMELINE = …, loads from
file:// without fetch) · captions.srt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PAUSE_BREAK = 0.35
END_PUNCT = tuple(".,!?;:…—")


def load(p: Path | None):
    return json.loads(p.read_text(encoding="utf-8")) if p else None


def make_scenes(lines: list[dict], by: str) -> list[dict]:
    if by == "line":
        return [{"id": f"s{L['i']}", "start": L["start"], "end": L["end"], "text": L["text"], "lines": [L["i"]]} for L in lines]
    scenes: dict[int, dict] = {}
    for L in lines:
        s = scenes.setdefault(L["para"], {"id": f"p{L['para']}", "start": L["start"], "end": L["end"], "text": "", "lines": []})
        s["end"] = L["end"]
        s["text"] = (s["text"] + " " + L["text"]).strip()
        s["lines"].append(L["i"])
    return list(scenes.values())


def snap_scenes(scenes: list[dict], beats: list[float], tol: float) -> int:
    snapped = 0
    for s in scenes[1:]:  # the first scene always starts at 0
        speech = s["start"]
        cands = [b for b in beats if speech - tol <= b <= speech]
        if cands:
            s["cut"] = round(max(cands), 3)
            snapped += 1
        else:
            s["cut"] = round(max(0.0, speech - 0.08), 3)
    if scenes:
        scenes[0]["cut"] = 0.0
    # Visual windows: each scene is on screen from its cut to the next cut.
    for a, b in zip(scenes, scenes[1:]):
        a["out"] = b["cut"]
    return snapped


def group_captions(words: list[dict], max_words: int, max_chars: int) -> list[dict]:
    caps, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        brk = (nxt is None or len(cur) >= max_words or len(text) >= max_chars
               or w["w"].endswith(END_PUNCT) or (nxt["start"] - w["end"]) > PAUSE_BREAK)
        if brk:
            caps.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": text,
                         "words": [{"w": x["w"], "start": x["start"], "end": x["end"]} for x in cur]})
            cur = []
    return caps


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lines", type=Path, required=True, help="lines.json from tts.py")
    ap.add_argument("--words", type=Path, help="words.json from transcribe.py (captions need it)")
    ap.add_argument("--beats", type=Path, help="beats.json from beats.py (snap cuts)")
    ap.add_argument("-o", "--out-dir", type=Path, default=Path("."))
    ap.add_argument("--scene-by", choices=["line", "para"], default="line")
    ap.add_argument("--snap", type=float, default=0.25, help="max seconds a cut may move earlier to hit a beat")
    ap.add_argument("--max-words", type=int, default=4)
    ap.add_argument("--max-chars", type=int, default=24)
    ap.add_argument("--tail", type=float, default=1.0, help="seconds after the last word")
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()

    lines = load(a.lines)
    words = load(a.words) or []
    beats = load(a.beats)
    scenes = make_scenes(lines, a.scene_by)
    duration = round(max(lines[-1]["end"], words[-1]["end"] if words else 0) + a.tail, 3)
    snapped = snap_scenes(scenes, beats["beats"] if beats else [], a.snap if beats else 0.0)
    if scenes:
        scenes[-1]["out"] = duration
    captions = group_captions(words, a.max_words, a.max_chars) if words else []

    tl = {"fps": a.fps, "duration": duration, "lines": lines, "scenes": scenes,
          "captions": captions, "words": words,
          "beats": beats or {"tempo": None, "beats": [], "downbeats": [], "drops": []}}
    a.out_dir.mkdir(parents=True, exist_ok=True)
    js = json.dumps(tl, ensure_ascii=False)
    (a.out_dir / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    (a.out_dir / "timeline.js").write_text(f"window.TIMELINE = {js};\n", encoding="utf-8")
    srt = "\n".join(f"{i + 1}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text'].replace('*', '')}\n" for i, c in enumerate(captions))
    (a.out_dir / "captions.srt").write_text(srt, encoding="utf-8")
    print(f"✓ {duration:.2f}s · {len(scenes)} scenes ({snapped} cuts on beat) · {len(captions)} captions → {a.out_dir}")


if __name__ == "__main__":
    main()
