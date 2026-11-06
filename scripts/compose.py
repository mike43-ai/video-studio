#!/usr/bin/env python3
"""compose.py — cut real footage, photos and overlays into one video with ffmpeg (no browser).

  vs compose edit.json [-o out.mp4] [--draft] [--print-ffmpeg]

edit.json (paths relative to the file; times in seconds):
{
  "size": "1080x1920", "fps": 30, "background": "#000000",
  "clips": [                                   # played one after another
    {"src": "talk.mp4", "from": 12.4, "to": 18.0, "fit": "cover", "speed": 1, "volume_db": 0,
     "crop": [x, y, w, h], "transition": {"type": "fade", "dur": 0.4}},          # transition INTO this clip
    {"src": "photo.jpg", "duration": 3, "kenburns": [1.0, 1.12], "fit": "cover", "transition": "slideleft"}
  ],
  "overlays": [{"src": "logo.png", "at": 0, "until": 5, "x": 40, "y": 60, "w": 220, "fade": 0.3},
               {"src": "cutout.mov", "at": 2, "x": 0, "y": 0, "w": 1080}],        # alpha .mov/.webm/.png
  "audio": [{"src": "mix.wav", "at": 0, "db": 0}], "replace_audio": true,        # drop the clips' own sound
  "subtitles": {"srt": "captions.srt", "size": 64, "color": "#FFFFFF", "stroke": "#000000", "stroke_width": 6,
                "y": 0.72, "box": "#00000099", "max_width": 0.86, "font": "/path/font.ttf", "offset": 0}
}
fit: cover (fill + crop, default) · contain (letterbox on background). kenburns [start_zoom, end_zoom] for photos.
transition types: any ffmpeg xfade name — fade, wipeleft, wiperight, slideleft, slideup, circleopen, dissolve,
  smoothleft, zoomin, fadeblack … (default none = hard cut).
Subtitles are drawn with Pillow (works on ffmpeg builds without libass/drawtext) in a font that covers
Vietnamese. Output: H.264 yuv420p + AAC 48 kHz. --draft = half size, fast preset.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vslib import die, probe  # noqa: E402

IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
FONTS = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
         "C:/Windows/Fonts/arialbd.ttf"]


def run(cmd: list, show: bool) -> None:
    cmd = [str(c) for c in cmd]
    if show:
        print(" ".join(shlex.quote(c) for c in cmd))
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        die(f"ffmpeg failed:\n{p.stderr[-1500:]}")


def has_audio(path: Path) -> bool:
    return any(s["codec_type"] == "audio" for s in probe(path)["streams"])


def fit_filter(fit: str, W: int, H: int, bg: str) -> str:
    if fit == "contain":
        return f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={bg}"
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"


def atempo(speed: float) -> str:
    parts, s = [], speed
    while s > 2:
        parts.append("atempo=2"); s /= 2
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def segment(clip: dict, base: Path, W: int, H: int, fps: int, bg: str, out: Path, fast: bool, show: bool) -> float:
    """One clip → normalized mp4 (size, fps, stereo 48 kHz audio, silent if none). Returns its duration."""
    src = (base / clip["src"]).resolve()
    if not src.exists():
        die(f"clip not found: {src}")
    fit = fit_filter(clip.get("fit", "cover"), W, H, bg)
    crop = f"crop={','.join(map(str, clip['crop'])).replace(',', ':')}," if clip.get("crop") else ""
    enc = ["-c:v", "libx264", "-preset", "veryfast" if fast else "medium", "-crf", "23" if fast else "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    if src.suffix.lower() in IMG:
        d = float(clip.get("duration", 3))
        z0, z1 = clip.get("kenburns", [1.0, 1.0])
        n = max(1, int(d * fps))
        vf = (f"{crop}scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
              f"zoompan=z='{z0}+({z1}-{z0})*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps},"
              f"setsar=1,format=yuv420p") if (z0, z1) != (1.0, 1.0) else f"{crop}{fit},setsar=1,fps={fps},format=yuv420p"
        run(["ffmpeg", "-v", "error", "-loop", "1", "-framerate", fps, "-t", d, "-i", src,
             "-f", "lavfi", "-t", d, "-i", "anullsrc=r=48000:cl=stereo",
             "-vf", vf, "-frames:v", n, "-map", "0:v", "-map", "1:a", *enc, "-shortest", "-y", out], show)
        return n / fps
    t0 = float(clip.get("from", 0))
    full = float(probe(src)["format"]["duration"])
    t1 = float(clip.get("to", full))
    speed = float(clip.get("speed", 1))
    d = (t1 - t0) / speed
    vf = f"{crop}{fit},setsar=1,setpts=(PTS-STARTPTS)/{speed},fps={fps},format=yuv420p"
    args = ["ffmpeg", "-v", "error", "-ss", t0, "-to", t1, "-i", src]
    if has_audio(src) and not clip.get("mute"):
        af = f"asetpts=PTS-STARTPTS,{atempo(speed)},volume={float(clip.get('volume_db', 0))}dB,aresample=48000,apad"
        args += ["-vf", vf, "-af", af, "-map", "0:v:0", "-map", "0:a:0"]
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-vf", vf, "-map", "0:v:0", "-map", "1:a"]
    run([*args, *enc, "-t", f"{d:.3f}", "-y", out], show)
    return d


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    def ts(s: str) -> float:
        h, m, rest = s.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        tl = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if tl is None:
            continue
        s, e = (x.strip() for x in lines[tl].split("-->"))
        out.append((ts(s), ts(e.split()[0]), "\n".join(lines[tl + 1:]).replace("*", "")))
    return out


def subtitle_pngs(cfg: dict, base: Path, W: int, H: int, tmp: Path) -> list[tuple[Path, float, float, int, int]]:
    from PIL import Image, ImageDraw, ImageFont

    font_path = cfg.get("font") or next((f for f in FONTS if Path(f).exists()), None)
    if not font_path:
        die("no font found for subtitles — set subtitles.font to a .ttf that covers Vietnamese")
    size = int(cfg.get("size", round(H * 0.033)))
    font = ImageFont.truetype(font_path, size)
    color, stroke = cfg.get("color", "#FFFFFF"), cfg.get("stroke", "#000000")
    sw = int(cfg.get("stroke_width", max(2, size // 10)))
    maxw = int(W * float(cfg.get("max_width", 0.86)))
    box = cfg.get("box")
    out = []
    shift = float(cfg.get("offset", 0))
    for i, (s, e, text) in enumerate(parse_srt((base / cfg["srt"]).read_text(encoding="utf-8"))):
        s, e = s + shift, e + shift
        words, lines, cur = text.split(), [], ""
        probe_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        for w in words:  # wrap to max width
            trial = f"{cur} {w}".strip()
            if probe_draw.textlength(trial, font=font) <= maxw or not cur:
                cur = trial
            else:
                lines.append(cur); cur = w
        lines.append(cur)
        txt = "\n".join(lines)
        l, t, r, b = probe_draw.multiline_textbbox((0, 0), txt, font=font, stroke_width=sw, align="center", spacing=size // 4)
        pad = size // 3
        img = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if box:
            d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=pad, fill=box)
        d.multiline_text((pad - l, pad - t), txt, font=font, fill=color, stroke_width=0 if box else sw,
                         stroke_fill=stroke, align="center", spacing=size // 4)
        f = tmp / f"sub{i:04d}.png"
        img.save(f)
        x = (W - img.width) // 2
        y = int(H * float(cfg.get("y", 0.72)) - img.height / 2)
        out.append((f, s, e, x, y))
    return out


def subtitle_layer(subs: list, W: int, H: int, total: float, fps: int, tmp: Path, show: bool) -> Path:
    """All captions as ONE transparent video: full-frame PNGs (blank between cues) joined by the concat demuxer.
    One ffmpeg input instead of one per cue — long videos stay under the open-file limit."""
    from PIL import Image
    blank = tmp / "blank.png"
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    lines, t = [], 0.0
    for k, (f, s, e, x, y) in enumerate(sorted(subs, key=lambda c: c[1])):
        s, e = max(s, t), min(e, total)
        if e <= s:
            continue
        if s > t:
            lines += [f"file '{blank}'", f"duration {s - t:.3f}"]
        frame = tmp / f"subf{k:04d}.png"
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        canvas.alpha_composite(Image.open(f), (max(0, x), max(0, y)))
        canvas.save(frame)
        lines += [f"file '{frame}'", f"duration {e - s:.3f}"]
        t = e
    if total > t:
        lines += [f"file '{blank}'", f"duration {total - t:.3f}"]
    lines.append(f"file '{blank}'")
    lst = tmp / "subs.txt"
    lst.write_text("\n".join(lines))
    out = tmp / "subs.mov"
    run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-vf", f"fps={fps},format=rgba",
         "-c:v", "qtrle", "-y", out], show)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edit", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--print-ffmpeg", action="store_true")
    a = ap.parse_args()
    spec = json.loads(a.edit.read_text(encoding="utf-8"))
    base = a.edit.resolve().parent
    W, H = (int(v) for v in spec.get("size", "1920x1080").lower().split("x"))
    if a.draft:
        W, H = W // 2 // 2 * 2, H // 2 // 2 * 2
    fps = int(spec.get("fps", 30))
    bg = spec.get("background", "#000000").replace("#", "0x")
    out = a.out or a.edit.with_suffix(".mp4")
    clips = spec.get("clips") or die("edit.json has no clips")
    show = a.print_ffmpeg

    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        segs, durs = [], []
        for i, c in enumerate(clips):
            f = tmp / f"seg{i:03d}.mp4"
            durs.append(segment(c, base, W, H, fps, bg, f, a.draft, show))
            segs.append(f)
            print(f"  [{i + 1}/{len(clips)}] {c['src']} · {durs[-1]:.2f}s")

        # join with xfade / acrossfade where a clip asks for a transition
        joined = tmp / "joined.mp4"
        if len(segs) == 1:
            joined = segs[0]
        else:
            ins, fc, vlast, alast, t = [], [], "[n0]", "[0:a]", durs[0]
            for i, f in enumerate(segs):
                ins += ["-i", f]
                # one timebase everywhere: xfade refuses inputs whose timebases differ (e.g. after concat)
                fc.append(f"[{i}:v]settb=AVTB[n{i}]")
            for i in range(1, len(segs)):
                tr = clips[i].get("transition")
                if isinstance(tr, str):
                    tr = {"type": tr}
                vo, ao = f"[v{i}]", f"[a{i}]"
                if tr:
                    d = min(float(tr.get("dur", 0.4)), durs[i - 1] / 2, durs[i] / 2)
                    fc.append(f"{vlast}[n{i}]xfade=transition={tr['type']}:duration={d:.3f}:offset={t - d:.3f},settb=AVTB{vo}")
                    fc.append(f"{alast}[{i}:a]acrossfade=d={d:.3f}{ao}")
                    t += durs[i] - d
                else:
                    fc.append(f"{vlast}{alast}[n{i}][{i}:a]concat=n=2:v=1:a=1[c{i}]{ao}")
                    fc.append(f"[c{i}]settb=AVTB{vo}")
                    t += durs[i]
                vlast, alast = vo, ao
            run(["ffmpeg", "-v", "error", *ins, "-filter_complex", ";".join(fc), "-map", vlast, "-map", alast,
                 "-c:v", "libx264", "-preset", "veryfast" if a.draft else "medium", "-crf", "23" if a.draft else "18",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-y", joined], show)
        total = float(probe(joined)["format"]["duration"])

        # final pass: overlays, subtitles, extra audio
        ins, fc, v, idx = ["-i", joined], [], "[0:v]", 1
        for k, o in enumerate(spec.get("overlays", [])):
            src = (base / o["src"]).resolve()
            if not src.exists():
                die(f"overlay not found: {src}")
            ins += (["-loop", "1", "-i", src] if src.suffix.lower() in IMG else ["-i", src])
            at, until = float(o.get("at", 0)), float(o.get("until", total))
            ow = f"scale={int(o['w'] * (0.5 if a.draft else 1))}:-2," if o.get("w") else ""
            fd = float(o.get("fade", 0))
            fades = (f",fade=t=in:st={at}:d={fd}:alpha=1,fade=t=out:st={until - fd}:d={fd}:alpha=1" if fd else "")
            fc.append(f"[{idx}:v]{ow}format=rgba,setpts=PTS-STARTPTS+{at}/TB{fades}[o{k}]")
            x = int(o.get("x", 0) * (0.5 if a.draft else 1)); y = int(o.get("y", 0) * (0.5 if a.draft else 1))
            fc.append(f"{v}[o{k}]overlay=x={x}:y={y}:enable='between(t,{at},{until})':eof_action=pass[ov{k}]")
            v, idx = f"[ov{k}]", idx + 1
        if spec.get("subtitles"):
            layer = subtitle_layer(subtitle_pngs(spec["subtitles"], base, W, H, tmp), W, H, total, fps, tmp, show)
            ins += ["-i", layer]
            fc.append(f"{v}[{idx}:v]overlay=0:0:eof_action=pass[subs]")
            v, idx = "[subs]", idx + 1
        amix_in = [] if spec.get("replace_audio") else ["[0:a]"]
        for k, au in enumerate(spec.get("audio", [])):
            src = (base / au["src"]).resolve()
            ins += ["-i", src]
            ms = int(float(au.get("at", 0)) * 1000)
            fc.append(f"[{idx}:a]aresample=48000,volume={float(au.get('db', 0))}dB,adelay={ms}|{ms}[x{k}]")
            amix_in.append(f"[x{k}]")
            idx += 1
        if not amix_in:
            die("replace_audio is true but no audio tracks given")
        fc.append(f"{''.join(amix_in)}amix=inputs={len(amix_in)}:normalize=0:duration=longest,atrim=0:{total:.3f}[aout]")
        fc.append(f"{v}null[vout]")
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-v", "error", *ins, "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
             "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast" if a.draft else "medium",
             "-crf", "23" if a.draft else "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
             "-movflags", "+faststart", "-y", out], show)
    print(f"✓ {out} · {W}x{H} @ {fps} · {total:.2f}s · {len(clips)} clip(s)"
          f" · next: vs check {out}")


if __name__ == "__main__":
    main()
