"""Petites fonctions pures : formats de temps, tri naturel, exploration de dossiers."""
from __future__ import annotations

import math
import os
import re

MEDIA_EXT = {
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts",
    ".flv", ".ogv", ".vob", ".3gp", ".mp3", ".m4a", ".aac", ".ac3", ".dts", ".wav", ".ogg",
    ".opus", ".flac", ".wma",
}


def fmt_time(s) -> str:
    if s is None or isinstance(s, str) or not math.isfinite(s):
        return "0:00"
    s = int(s)
    h, m, r = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{r:02d}" if h else f"{m}:{r:02d}"


def parse_time(text: str) -> float | None:
    """'1:25:30', '85:03', '90' ou '12,5' -> secondes ; None si invalide."""
    parts = text.strip().replace(",", ".").split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if any(v < 0 or not math.isfinite(v) for v in vals):
        return None
    total = 0.0
    for v in vals:
        total = total * 60 + v
    return total


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def expand_media(paths, exts=MEDIA_EXT) -> list[str]:
    """Fichiers médias des chemins donnés ; les dossiers sont parcourus en ordre naturel."""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort(key=natural_key)
                for f in sorted(files, key=natural_key):
                    if os.path.splitext(f)[1].lower() in exts:
                        out.append(os.path.join(root, f))
        elif os.path.splitext(p)[1].lower() in exts:
            out.append(p)
    return out
