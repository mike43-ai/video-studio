#!/usr/bin/env python3
"""transcribe.py — word-level timestamps with faster-whisper, corrected to the script.

Whisper mishears Vietnamese names/terms. When --script is given, recognized
words are aligned to the script's words (difflib) and the script's spelling
wins; script words Whisper missed get times interpolated from neighbours.
With --lines (from tts.py) every word gets the index of the line it falls in.

Output: words.json  [{"w", "start", "end", "line"}]
"""
import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

PUNCT = re.compile(r"[^\w]+", re.UNICODE)


def norm(w: str) -> str:
    """Lowercase, strip punctuation; keep Vietnamese diacritics (NFC)."""
    return PUNCT.sub("", unicodedata.normalize("NFC", w).lower())


def recognize(audio: str, lang: str, model: str, compute: str) -> list[dict]:
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device="cpu", compute_type=compute)
    segs, info = m.transcribe(audio, language=lang, word_timestamps=True, vad_filter=True, beam_size=5)
    words = []
    for s in segs:
        for w in s.words or []:
            if w.word.strip():
                words.append({"w": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
    print(f"  whisper: {len(words)} words, language={info.language}", file=sys.stderr)
    return words


def script_words(path: Path) -> list[str]:
    text = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines() if not l.strip().startswith("#"))
    return [w for w in text.split() if norm(w)]


def align(rec: list[dict], ref: list[str]) -> list[dict]:
    """Return one timed entry per script word, using recognized times where they match."""
    a = [norm(r["w"]) for r in rec]
    b = [norm(w) for w in ref]
    out: list[dict | None] = [None] * len(ref)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(j2 - j1):
                r = rec[i1 + k]
                out[j1 + k] = {"w": ref[j1 + k], "start": r["start"], "end": r["end"]}
        elif tag == "replace":  # n recognized ↔ m script words: spread the span evenly
            t0, t1 = rec[i1]["start"], rec[i2 - 1]["end"]
            step = (t1 - t0) / (j2 - j1)
            for k in range(j2 - j1):
                out[j1 + k] = {"w": ref[j1 + k], "start": round(t0 + k * step, 3), "end": round(t0 + (k + 1) * step, 3)}
    # Fill script words Whisper skipped entirely ("insert" from the script side).
    n = len(out)
    i = 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < n and out[j] is None:
            j += 1
        t0 = out[i - 1]["end"] if i > 0 else (out[j]["start"] if j < n else 0.0)
        t1 = out[j]["start"] if j < n else t0 + 0.3 * (j - i)
        step = max(t1 - t0, 0.05 * (j - i)) / (j - i)
        for k in range(i, j):
            s = t0 + (k - i) * step
            out[k] = {"w": ref[k], "start": round(s, 3), "end": round(s + step, 3)}
        i = j
    matched = sum(1 for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag == "equal" for _ in range(j2 - j1))
    print(f"  aligned to script: {matched}/{len(ref)} words matched exactly", file=sys.stderr)
    return out  # type: ignore[return-value]


def tag_lines(words: list[dict], lines: list[dict]) -> None:
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        best = min(lines, key=lambda L: 0 if L["start"] <= mid <= L["end"] else min(abs(mid - L["start"]), abs(mid - L["end"])))
        w["line"] = best["i"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("-o", "--out", type=Path, default=Path("words.json"))
    ap.add_argument("--lang", default="vi")
    ap.add_argument("--script", type=Path, help="script text: its spelling replaces Whisper's")
    ap.add_argument("--lines", type=Path, help="lines.json from tts.py → word.line")
    ap.add_argument("--model", default="large-v3-turbo", help="tiny|base|small|medium|large-v3|large-v3-turbo")
    ap.add_argument("--compute", default="int8")
    a = ap.parse_args()

    words = recognize(a.audio, a.lang, a.model, a.compute)
    if a.script:
        words = align(words, script_words(a.script))
    if a.lines:
        tag_lines(words, json.loads(a.lines.read_text(encoding="utf-8")))
    a.out.write_text(json.dumps(words, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {len(words)} words → {a.out}")


if __name__ == "__main__":
    main()
