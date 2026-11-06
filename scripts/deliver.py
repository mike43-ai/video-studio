#!/usr/bin/env python3
"""deliver.py — the finished file: picture + master mix, verified.

  vs deliver render.mp4 mix.wav -o final.mp4                     (or --audio mix.wav)
            [--lufs -14] [--crf 20] [--max-mb 30] [--intro thumb.jpg [--intro-sec 1]] [--sheet sheet.jpg]

Default: copies the video stream (no re-encode), audio AAC 256 kb/s 48 kHz.
  --lufs      re-measure the mix and correct it to this loudness (two-pass) if it is off by > 0.5 LU
  --crf       re-encode the picture with x264 at this quality (lower = better, 18–23 typical)
  --max-mb    re-encode so the file fits under N MB (bitrate from the duration; the platform cap)
  --intro     put a still (the thumbnail) in front for --intro-sec seconds — some platforms use the
              first frame as the cover; audio is delayed to match
  --sheet     contact sheet of 12 frames of the final file, to look at
Then measures the FINAL file (not the inputs): resolution, fps, stream lengths, LUFS, true peak.
Warns when loudness is off target by > 1 LU or audio/video lengths differ by > 0.1 s.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vslib import contact_sheet, die, display_size, ff, loudness, probe  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("audio", type=Path, nargs="?")
    ap.add_argument("--audio", dest="audio_flag", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("final.mp4"))
    ap.add_argument("--target", "--lufs", dest="target", type=float, default=-14.0)
    ap.add_argument("--crf", type=int)
    ap.add_argument("--max-mb", type=float)
    ap.add_argument("--intro", type=Path)
    ap.add_argument("--intro-sec", type=float, default=1.0)
    ap.add_argument("--sheet", type=Path)
    a = ap.parse_args()
    audio = a.audio_flag or a.audio
    lufs_given = any(x.startswith("--lufs") for x in sys.argv)
    if a.out.exists() and a.out.resolve() == a.video.resolve():
        die("output would overwrite the input video")

    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        # 1. audio: optional loudness correction
        if audio:
            src_audio = audio
            if lufs_given:
                m = loudness(audio)
                if abs(m["lufs"] - a.target) > 0.5:
                    err = ff(["-i", audio, "-af", f"loudnorm=I={a.target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
                    j = json.loads(err[err.rindex("{"): err.rindex("}") + 1])
                    ln = (f"loudnorm=I={a.target}:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}"
                          f":measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
                    src_audio = tmp / "fixed.wav"
                    ff(["-i", audio, "-af", f"{ln},aresample=48000", "-c:a", "pcm_s24le", "-y", src_audio])
                    print(f"  loudness corrected {m['lufs']} → {a.target} LUFS")
        else:
            src_audio = None

        info = probe(a.video)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        dur = float(info["format"]["duration"]) + (a.intro_sec if a.intro else 0)
        num, den = (int(x) for x in v["r_frame_rate"].split("/"))
        fps = num / den if den else 30

        vcodec = ["-c:v", "copy"]
        if a.max_mb:
            kbps = int((a.max_mb * 8e6 * 0.96 / dur - 256e3) / 1000)
            if kbps < 300:
                die(f"{a.max_mb} MB is too small for {dur:.1f}s (needs ≥ {(556e3 * dur) / 8e6:.1f} MB at 300 kb/s video)")
            vcodec = ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k",
                      "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p"]
        elif a.crf is not None or a.intro:
            vcodec = ["-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf if a.crf is not None else 18), "-pix_fmt", "yuv420p"]

        inputs, fc, vmap = [], [], "0:v:0"
        if a.intro:
            inputs = ["-loop", "1", "-t", f"{a.intro_sec}", "-framerate", f"{fps}", "-i", a.intro, "-i", a.video]
            W, H = display_size(v)
            fc.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=yuv420p,fps={fps}[i];"
                      f"[1:v:0]setsar=1,format=yuv420p,fps={fps}[m];[i][m]concat=n=2:v=1:a=0[v]")
            vmap = "[v]"
            vidx = 2
        else:
            inputs = ["-i", a.video]
            vidx = 1
        has_own_audio = any(st["codec_type"] == "audio" for st in info["streams"])
        if src_audio:
            inputs += ["-i", src_audio]
            a_in = f"{vidx}:a:0"
        else:  # keep the render's own sound
            a_in = f"{1 if a.intro else 0}:a:0" if has_own_audio else None
        if a_in and a.intro:  # the intro still pushes the sound later by the same amount
            ms = int(a.intro_sec * 1000)
            fc.append(f"[{a_in}]adelay={ms}|{ms},apad[a]")
            amap = "[a]"
        else:
            amap = a_in or "0:a:0?"
        args = [*inputs]
        if fc:
            args += ["-filter_complex", ";".join(fc)]
        args += ["-map", vmap, "-map", amap, *vcodec, "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                 "-movflags", "+faststart", *(["-t", f"{dur:.3f}"] if a.intro else ["-shortest"]), "-y", a.out]
        ff(args)

    out_info = probe(a.out)
    v = next(s for s in out_info["streams"] if s["codec_type"] == "video")
    au = next((s for s in out_info["streams"] if s["codec_type"] == "audio"), None)
    vd = float(v.get("duration", out_info["format"]["duration"]))
    ad = float(au.get("duration", 0)) if au else 0.0
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    size_mb = int(out_info["format"]["size"]) / 1e6
    print(f"✓ {a.out}")
    print(f"  video  {v['width']}x{v['height']} @ {num / den:.2f} fps · {vd:.2f}s · {v['codec_name']} · {size_mb:.1f} MB")
    if au:
        lufs = loudness(a.out)
        print(f"  audio  {au['codec_name']} {au.get('sample_rate')} Hz · {ad:.2f}s")
        print(f"  loudness {lufs['lufs']:.1f} LUFS integrated · true peak {lufs['true_peak']:.1f} dBFS")
        if abs(lufs["lufs"] - a.target) > 1:
            print(f"  ⚠ loudness off target ({a.target} LUFS) — re-run vs mix, or deliver with --lufs {a.target}")
        if abs(vd - ad) > 0.1:
            print(f"  ⚠ audio/video length differ by {abs(vd - ad):.2f}s — check timeline duration vs mix --timeline")
    else:
        print("  ⚠ no audio stream")
    if a.max_mb and size_mb > a.max_mb:
        print(f"  ⚠ {size_mb:.1f} MB is over --max-mb {a.max_mb}")
    if a.sheet:
        with tempfile.TemporaryDirectory() as tmpd:
            frames = []
            for i in range(12):
                f = Path(tmpd) / f"{i:02d}.jpg"
                ff(["-ss", f"{vd * (i + 0.5) / 12:.3f}", "-i", a.out, "-frames:v", "1", "-q:v", "3", "-y", f], check=False)
                if f.exists():
                    frames.append(f)
            print(f"  sheet → {contact_sheet(frames, a.sheet)}")
    print(f"  next: vs check {a.out}   (black/frozen frames, safe zones, speech vs script)")


if __name__ == "__main__":
    main()
