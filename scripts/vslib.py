#!/usr/bin/env python3
"""vslib.py — shared helpers for every vs subcommand (stdlib only).

- config.env / keys.env reading (keys: env var wins, then ~/.video-studio/keys.env, chmod 600)
- paid-service gate: a paid call needs --paid-ok (the agent asks the user first)
- library: content-addressed cache of generated assets (same prompt → same file, no second charge)
- ffmpeg/ffprobe wrappers, HTTP JSON with a generic User-Agent and an on-disk cache
- error log in ~/.video-studio/logs/errors.log (secrets scrubbed) for `vs report`
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VS_HOME = Path(os.environ.get("VS_HOME", Path.home() / ".video-studio"))
CONFIG = VS_HOME / "config.env"
KEYS = VS_HOME / "keys.env"
LIBRARY = VS_HOME / "library"
LOGS = VS_HOME / "logs"
CACHE = VS_HOME / "cache"
UA = "video-studio/1.0 (+https://github.com/mike43-ai/video-studio)"

# Known API keys: name → (env var, what it unlocks, free?)
KEY_INFO = {
    "pixabay": ("PIXABAY_API_KEY", "stock photos/videos (vs pixabay)", "free key: pixabay.com/api/docs"),
    "gemini": ("GEMINI_API_KEY", "image create --engine gemini", "PAID per image: aistudio.google.com/apikey"),
    "elevenlabs": ("ELEVENLABS_API_KEY", "tts/sfx/music --engine elevenlabs", "PAID per character/second: elevenlabs.io"),
}


# ---------------------------------------------------------------- config / keys
def _read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    return out


def config(key: str, default: str = "") -> str:
    return os.environ.get(key) or _read_env(CONFIG).get(key, default)


def get_key(name: str) -> str:
    env = KEY_INFO[name][0] if name in KEY_INFO else name
    return os.environ.get(env) or _read_env(KEYS).get(env, "")


def set_key(name: str, value: str) -> None:
    env = KEY_INFO[name][0] if name in KEY_INFO else name
    data = _read_env(KEYS)
    if value:
        data[env] = value
    else:
        data.pop(env, None)
    VS_HOME.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEYS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never world-readable, even briefly
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(f'{k}="{v}"\n' for k, v in data.items()))
    os.chmod(KEYS, 0o600)


def require_key(name: str) -> str:
    k = get_key(name)
    if not k:
        env, what, how = KEY_INFO[name]
        die(f"needs the {name} key ({what}). Get it: {how}\n  then: vs keys set {name}")
    return k


def paid_gate(service: str, estimate: str, ok: bool) -> None:
    """Refuse a paid call unless the caller passed --paid-ok (after asking the user)."""
    if not ok:
        die(f"{service} is a PAID service ({estimate}). Ask the user, then re-run with --paid-ok.", code=3)


# ---------------------------------------------------------------- output / errors
def scrub(text: str) -> str:
    secrets = dict(_read_env(KEYS))
    for env, _, _ in KEY_INFO.values():  # keys given through the environment too
        if os.environ.get(env):
            secrets[env] = os.environ[env]
    for k, v in secrets.items():
        if v and len(v) >= 6:
            text = text.replace(v, f"<{k}>")
    text = re.sub(r"(?i)(key|token|secret|authorization)([\"'=:\s]+)[A-Za-z0-9_\-\.]{12,}", r"\1\2<redacted>", text)
    return text.replace(str(Path.home()), "~")


def log_error(msg: str) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with open(LOGS / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(sys.argv[:3])} :: {scrub(msg)[:2000]}\n")
    except OSError:
        pass


def die(msg: str, code: int = 1) -> None:
    log_error(msg)
    print(f"✗ {scrub(msg)}", file=sys.stderr)
    sys.exit(code)


def emit(data, as_json: bool, human=None) -> None:
    """Print JSON when --json, else the human callback (or pretty JSON)."""
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif human:
        human(data)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- ffmpeg
def ff(args: list, check: bool = True) -> str:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *map(str, args)], capture_output=True, text=True)
    if check and p.returncode != 0:
        die(f"ffmpeg failed:\n{p.stderr[-1500:]}")
    return p.stderr


def probe(path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if p.returncode:
        die(f"ffprobe cannot read {path}: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout)


def display_size(stream: dict) -> tuple[int, int]:
    """Width/height as shown (phone videos store 1920x1080 + a 90° rotation flag for a portrait picture)."""
    w, h = int(stream.get("width", 0)), int(stream.get("height", 0))
    rot = stream.get("tags", {}).get("rotate")
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd["rotation"]
    try:
        if int(float(rot or 0)) % 180:
            return h, w
    except ValueError:
        pass
    return w, h


def duration(path) -> float:
    info = probe(path)
    return float(info.get("format", {}).get("duration") or 0)


def loudness(path) -> dict:
    """Integrated LUFS, true peak, LRA of the first audio stream."""
    err = ff(["-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"], check=False)
    s = err[err.rfind("Summary:"):]
    g = lambda pat: float(m.group(1)) if (m := re.search(pat, s)) else float("nan")  # noqa: E731
    return {"lufs": g(r"I:\s+(-?[\d.]+) LUFS"), "true_peak": g(r"Peak:\s+(-?[\d.]+) dBFS"),
            "lra": g(r"LRA:\s+(-?[\d.]+) LU")}


def find_chrome() -> str | None:
    if config("VS_CHROME") and Path(config("VS_CHROME")).exists():
        return config("VS_CHROME")
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if Path(p).exists():
            return p
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(n):
            return shutil.which(n)
    return None


# ---------------------------------------------------------------- HTTP
def http(url: str, params: dict | None = None, data: bytes | None = None, headers: dict | None = None,
         timeout: int = 60, cache_ttl: int = 0, retry: bool | None = None) -> bytes:
    """GET/POST with a generic UA. cache_ttl>0 caches GET bodies on disk (polite to public APIs).
    Retries on 429/5xx/network errors only for GETs (or retry=True): a retried paid POST could bill twice."""
    if retry is None:
        retry = data is None
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    cfile = None
    if cache_ttl and data is None:
        cfile = CACHE / "http" / hashlib.sha1(url.encode()).hexdigest()
        if cfile.exists() and time.time() - cfile.stat().st_mtime < cache_ttl:
            return cfile.read_bytes()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            break
        except urllib.error.HTTPError as e:
            if retry and e.code in (429, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            die(f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}: {e.read()[:400].decode('utf-8', 'replace')}")
        except (urllib.error.URLError, TimeoutError) as e:
            if retry and attempt < 2:
                time.sleep(2); continue
            die(f"network error for {urllib.parse.urlsplit(url).netloc}: {getattr(e, 'reason', e)}"
                + ("" if retry else " (not retried: a paid request may have gone through — check your usage first)"))
    if cfile:
        cfile.parent.mkdir(parents=True, exist_ok=True)
        cfile.write_bytes(body)
    return body


def http_json(url: str, **kw):
    return json.loads(http(url, **kw))


def download(url: str, out: Path, headers: dict | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(http(url, headers=headers, timeout=180))
    return out


# ---------------------------------------------------------------- library
def lib_key(kind: str, spec: dict) -> str:
    return hashlib.sha1(json.dumps({"kind": kind, **spec}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def lib_get(kind: str, spec: dict) -> Path | None:
    d = LIBRARY / kind
    hits = list(d.glob(lib_key(kind, spec) + ".*")) if d.exists() else []
    hits = [h for h in hits if h.suffix != ".json"]
    return hits[0] if hits else None


def lib_put(kind: str, spec: dict, src: Path, meta: dict | None = None) -> Path:
    """Store a generated file under library/<kind>/<hash>.<ext> with a sidecar .json."""
    d = LIBRARY / kind
    d.mkdir(parents=True, exist_ok=True)
    key = lib_key(kind, spec)
    dst = d / f"{key}{src.suffix}"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    (d / f"{key}.json").write_text(json.dumps({"id": key, "kind": kind, "spec": spec, "file": dst.name,
                                                 "created": time.strftime("%Y-%m-%d %H:%M:%S"), **(meta or {})},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def lib_copy_out(path: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.resolve() != path.resolve():
        shutil.copy2(path, out)
    return out


# ---------------------------------------------------------------- contact sheet (ffmpeg only)
def contact_sheet(images: list, out: Path, cols: int = 6, cell: int = 320) -> Path:
    """Grid of images (or video frames already extracted) → one JPG/PNG, aspect kept, letterboxed cells."""
    images = [Path(i) for i in images][:120]
    if not images:
        die("contact sheet: no images")
    cols = max(1, min(cols, len(images)))
    try:  # cell shape follows the first image (portrait frames get portrait cells)
        v = next(s for s in probe(images[0])["streams"] if s["codec_type"] == "video")
        cw, ch = (cell, max(2, round(cell * v["height"] / v["width"] / 2) * 2)) if v["width"] >= v["height"] \
            else (max(2, round(cell * v["width"] / v["height"] / 2) * 2), cell)
    except Exception:  # noqa: BLE001 — unreadable first image: square cells
        cw = ch = cell
    args, chains = [], []
    for i, img in enumerate(images):
        args += ["-i", img]
        chains.append(f"[{i}:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                      f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color=0x202020,setsar=1,format=rgb24[c{i}]")
    n = len(images)
    if n == 1:
        graph = chains[0].replace("[c0]", "[out]")
    else:
        layout = "|".join(f"{(i % cols) * cw}_{(i // cols) * ch}" for i in range(n))
        graph = ";".join(chains) + ";" + "".join(f"[c{i}]" for i in range(n)) + \
            f"xstack=inputs={n}:layout={layout}:fill=0x101010[out]"
    out.parent.mkdir(parents=True, exist_ok=True)
    ff([*args, "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-q:v", "3", "-y", out])
    return out
