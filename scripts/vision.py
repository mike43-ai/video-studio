#!/usr/bin/env python3
"""vision.py — shared OpenCV helpers (faces, QR codes, frame sampling) for matte.py and redact.py."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
_FACE = None
_PROFILE = None


def frames(path: Path, every: float):
    """Yield (t, BGR frame) every `every` seconds (an image yields once at t=0)."""
    if path.suffix.lower() in IMG:
        img = cv2.imread(str(path))
        if img is None:
            raise SystemExit(f"cannot read image {path}")
        yield 0.0, img
        return
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"cannot open video {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, round(every * fps))
    i = 0
    while i < n or n == 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if not ok:
            break
        yield i / fps, f
        i += step
    cap.release()


def faces(img: np.ndarray, min_frac: float = 0.06) -> list[list[int]]:
    """Frontal + profile faces, [x, y, w, h], largest first."""
    global _FACE, _PROFILE
    if _FACE is None:
        _FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        _PROFILE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    m = int(min(g.shape) * min_frac)
    found = list(_FACE.detectMultiScale(g, 1.1, 6, minSize=(m, m)))
    if not found:
        found = list(_PROFILE.detectMultiScale(g, 1.1, 5, minSize=(m, m)))
        flipped = _PROFILE.detectMultiScale(cv2.flip(g, 1), 1.1, 5, minSize=(m, m))
        found += [[g.shape[1] - x - w, y, w, h] for x, y, w, h in flipped]
    return sorted(([int(v) for v in f] for f in found), key=lambda b: -b[2] * b[3])


def qrcodes(img: np.ndarray) -> list[dict]:
    det = cv2.QRCodeDetector()
    ok, texts, pts, _ = det.detectAndDecodeMulti(img)
    out = []
    if ok and pts is not None:
        for txt, p in zip(texts, pts):
            x, y, w, h = cv2.boundingRect(p.astype(np.float32))
            out.append({"box": [int(x), int(y), int(w), int(h)], "text": txt})
    return out


def iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter + 1e-9)
