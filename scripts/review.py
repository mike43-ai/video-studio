#!/usr/bin/env python3
"""review.py — opt-in review gates for a video project: the user's OK is recorded with a hash of what they saw.

  vs review stamp <gate> --project P [--note "…"]   record the user's approval of a gate (hashes its files)
  vs review require <gate> --project P              exit 1 unless stamped AND files unchanged since
  vs review status --project P [--json]             every gate: stamped / stale / missing
  vs review animatic --project P [--comp comp] [--audio mix.wav] [-o animatic.mp4]
        one still per scene (from the composition, at each scene's middle) held for the scene's length +
        the audio → a quick video to approve pacing BEFORE the slow full render
  vs review receipt --project P <final.mp4>         run `vs check` and write review/receipt.json (hashes + result)

Gates and the files they cover (missing files are skipped):
  script   script.txt                     voice    voice.wav lines.json
  timeline timeline.json cues.json        animatic review/animatic.mp4
  frames   review/frames/*                render   final.mp4 (or the receipt's file)
Stamps live in <project>/review/stamps.json. Change a file after its stamp → that gate is stale.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from vslib import die, emit, ff

HERE = Path(__file__).resolve().parent
GATES = {
    "script": ["script.txt"],
    "voice": ["voice.wav", "lines.json"],
    "timeline": ["timeline.json", "cues.json"],
    "animatic": ["review/animatic.mp4"],
    "frames": ["review/frames"],
    "render": ["final.mp4"],
}


def q(p: Path) -> str:
    """Quote a path for the ffmpeg concat list (handles apostrophes: Bob's project)."""
    return "'" + str(p.resolve()).replace("'", "'\\''") + "'"


def files_for(project: Path, gate: str) -> list[Path]:
    if gate not in GATES and (".." in Path(gate).parts or Path(gate).is_absolute()):
        die(f"gate {gate!r} must be a gate name or a path inside the project")
    out = []
    for rel in GATES.get(gate, [gate]):
        p = project / rel
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.is_file())
        elif p.exists():
            out.append(p)
    return out


def digest(files: list[Path]) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()[:20]


def load(project: Path) -> dict:
    f = project / "review" / "stamps.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save(project: Path, data: dict) -> None:
    (project / "review").mkdir(parents=True, exist_ok=True)
    (project / "review" / "stamps.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def state(project: Path, gate: str, stamps: dict) -> str:
    files = files_for(project, gate)
    if gate not in stamps:
        return "missing" if files else "n/a"
    return "ok" if files and digest(files) == stamps[gate]["hash"] else "stale"


def animatic(project: Path, comp: Path, audio: Path | None, out: Path) -> Path:
    tl_file = project / "timeline.json"
    if not tl_file.exists():
        die(f"no {tl_file} — build the timeline first (vs timeline)")
    tl = json.loads(tl_file.read_text(encoding="utf-8"))
    scenes = tl.get("scenes") or [{"cut": 0, "out": tl["duration"]}]
    times = [round((s["cut"] + (s["out"] if "out" in s else s["end"])) / 2, 3) for s in scenes]
    frames_dir = project / "review" / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("frame-*.png"):  # stale frames from an earlier animatic
        old.unlink()
    env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}  # snapshot would call Gemini (paid)
    hf = f"hyperframes@{os.environ.get('VS_HYPERFRAMES_VERSION', '0.8.79')}"
    p = subprocess.run(["npx", "-y", hf, "snapshot", str(comp), "--at", ",".join(map(str, times)), "--no-end",
                        "-o", str(frames_dir)], capture_output=True, text=True, env=env)
    def at(f: Path) -> float:  # frame-07-at-12.5s.png → 12.5 (numeric order, not text order)
        m = re.search(r"at-([\d.]+)s", f.name)
        return float(m.group(1)) if m else 0.0
    shots = sorted(frames_dir.glob("frame-*.png"), key=at)
    if p.returncode or len(shots) < len(times):
        die(f"snapshot failed ({len(shots)}/{len(times)} frames):\n{(p.stdout + p.stderr)[-800:]}")
    with tempfile.TemporaryDirectory() as tmp:
        lst = Path(tmp) / "list.txt"
        lines = []
        for i, (s, img) in enumerate(zip(scenes, shots)):
            start = s["cut"]
            end = scenes[i + 1]["cut"] if i + 1 < len(scenes) else tl["duration"]
            lines += [f"file {q(img)}", f"duration {max(0.04, end - start):.3f}"]
        lines.append(f"file {q(shots[-1])}")
        lst.write_text("\n".join(lines))
        args = ["-f", "concat", "-safe", "0", "-i", lst]
        if audio and audio.exists():
            args += ["-i", audio, "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest"]
        out.parent.mkdir(parents=True, exist_ok=True)
        ff([*args, "-vf", "scale='min(720,iw)':-2,format=yuv420p,fps=10", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "28", "-y", out])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["stamp", "require", "status", "animatic", "receipt", "hash"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--project", type=Path, default=Path("."))
    ap.add_argument("--note", default="")
    ap.add_argument("--comp", type=Path)
    ap.add_argument("--audio", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--json", action="store_true")
    a, rest = ap.parse_known_args()
    P = a.project.resolve()
    stamps = load(P)

    if a.cmd == "stamp":
        files = files_for(P, a.arg or "")
        if not files:
            die(f"gate {a.arg!r}: no files to stamp in {P}")
        stamps[a.arg] = {"hash": digest(files), "files": [str(f.relative_to(P)) for f in files][:50],
                         "at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": a.note}
        save(P, stamps)
        print(f"✓ {a.arg} approved ({len(files)} files)")
    elif a.cmd == "require":
        st = state(P, a.arg or "", stamps)
        if st != "ok":
            print(f"✗ gate {a.arg}: {st} — show it to the user and `vs review stamp {a.arg}` after their OK")
            sys.exit(1)
        print(f"✓ gate {a.arg} approved and unchanged")
    elif a.cmd == "hash":
        print(digest(files_for(P, a.arg or "")))
    elif a.cmd == "status":
        rows = {g: {"state": state(P, g, stamps), **({"at": stamps[g]["at"]} if g in stamps else {})} for g in GATES}

        def human(rows):
            for g, r in rows.items():
                mark = {"ok": "✓", "stale": "✗", "missing": "○", "n/a": "·"}[r["state"]]
                print(f"  {mark} {g:<9} {r['state']:<8} {r.get('at', '')}")
        emit(rows, a.json, human)
    elif a.cmd == "animatic":
        comp = a.comp or P / "comp"
        audio = a.audio or (P / "mix.wav" if (P / "mix.wav").exists() else P / "voice.wav")
        out = animatic(P, comp, audio, a.out or P / "review" / "animatic.mp4")
        print(f"✓ {out} — show it; after the OK: vs review stamp animatic --project {a.project}")
    elif a.cmd == "receipt":
        video = Path(a.arg or P / "final.mp4")
        p = subprocess.run([sys.executable, str(HERE / "check_video.py"), str(video), "--json", *rest],
                           capture_output=True, text=True)
        try:
            result = json.loads(p.stdout)
        except ValueError:
            die(f"vs check failed:\n{p.stderr[-800:]}")
        receipt = {"file": str(video), "hash": digest([video]), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "check": result, "gates": {g: state(P, g, stamps) for g in GATES}}
        (P / "review").mkdir(parents=True, exist_ok=True)
        (P / "review" / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{'✓' if result['ready'] else '✗'} receipt → {P / 'review' / 'receipt.json'} · ready={result['ready']}")
        sys.exit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
