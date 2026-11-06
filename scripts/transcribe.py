#!/usr/bin/env python3
"""transcribe.py — word-level timestamps with faster-whisper, corrected to the script.

Whisper mishears Vietnamese names/terms. When --script is given, recognized
words are aligned to the script's words (difflib) and the script's spelling
wins; script words Whisper missed get times interpolated from neighbours.
With --lines (from tts.py) every word gets the index of the line it falls in.

--terms / --terms-file / --context bias Whisper toward names and jargon (hotwords + prompt); without
--script, recognized words close to a term are respelled to it.

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


_MODEL = None


def get_model(model: str, compute: str):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(model, device="cpu", compute_type=compute)
    return _MODEL


def recognize(audio: str, lang: str, model: str, compute: str, terms: list[str] | None = None,
              context: str | None = None) -> list[dict]:
    m = get_model(model, compute)
    extra = {}
    if terms:
        extra["hotwords"] = " ".join(terms)
    if context or terms:
        extra["initial_prompt"] = " ".join(filter(None, [context, ", ".join(terms or [])]))[:800]
    segs, info = m.transcribe(audio, language=lang, word_timestamps=True, vad_filter=True, beam_size=5, **extra)
    words = []
    for s in segs:
        for w in s.words or []:
            if w.word.strip():
                words.append({"w": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
    print(f"  whisper: {len(words)} words, language={info.language}", file=sys.stderr)
    return words


def fix_terms(words: list[dict], terms: list[str]) -> int:
    """Respell recognized words that are close to a known term (names, brands, jargon). Single-word terms only."""
    singles = {norm(t): t for t in terms if len(t.split()) == 1 and len(norm(t)) >= 5}  # short terms: too risky
    fixed = 0
    for w in words:
        n = norm(w["w"])
        if not n or n in singles:
            continue
        best = [c for c in difflib.get_close_matches(n, list(singles), n=1, cutoff=0.85) if abs(len(c) - len(n)) <= 2]
        if best:
            tail = w["w"][len(w["w"].rstrip(".,!?;:")):]
            w["w"] = singles[best[0]] + tail
            fixed += 1
    return fixed


def score_takes(spec: Path, lang: str, model: str, compute: str) -> None:
    """[{"wav", "text"}] → print JSON [{"wav", "match"}]: share of the text's words heard, in order."""
    items = json.loads(spec.read_text(encoding="utf-8"))
    out = []
    for it in items:
        heard = [norm(w["w"]) for w in recognize(it["wav"], lang, model, compute)]
        want = [norm(w) for w in it["text"].replace("*", "").split() if norm(w)]
        sm = difflib.SequenceMatcher(a=want, b=heard, autojunk=False)
        hit = sum(b.size for b in sm.get_matching_blocks())
        out.append({"wav": it["wav"], "match": round(hit / max(1, len(want)), 3), "extra": max(0, len(heard) - hit)})
    print(json.dumps(out))


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
    ap.add_argument("audio", nargs="?")
    ap.add_argument("-o", "--out", type=Path, default=Path("words.json"))
    ap.add_argument("--lang", default="vi")
    ap.add_argument("--script", type=Path, help="script text: its spelling replaces Whisper's")
    ap.add_argument("--lines", type=Path, help="lines.json from tts.py → word.line")
    ap.add_argument("--model", default="large-v3-turbo", help="tiny|base|small|medium|large-v3|large-v3-turbo")
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--terms", help='comma list of names/terms to bias and respell, e.g. "Hải Đăng,TikTok,LUFS"')
    ap.add_argument("--terms-file", type=Path, help="one term per line")
    ap.add_argument("--context", help="a sentence about the topic (helps Whisper with jargon)")
    ap.add_argument("--score-takes", type=Path, help="internal: JSON list of takes to score (used by vs tts --takes)")
    a = ap.parse_args()

    if a.score_takes:
        score_takes(a.score_takes, a.lang, a.model, a.compute)
        return
    terms = [t.strip() for t in (a.terms or "").split(",") if t.strip()]
    if a.terms_file:
        terms += [l.strip() for l in a.terms_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    words = recognize(a.audio, a.lang, a.model, a.compute, terms, a.context)
    if terms and not a.script:
        n = fix_terms(words, terms)
        print(f"  respelled {n} word(s) to match --terms", file=sys.stderr)
    if a.script:
        words = align(words, script_words(a.script))
    if a.lines:
        tag_lines(words, json.loads(a.lines.read_text(encoding="utf-8")))
    a.out.write_text(json.dumps(words, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {len(words)} words → {a.out}")


if __name__ == "__main__":
    main()
