"""Persistance JSON : reprise de lecture, marque-pages, mots bloqués, préférences.

Aucune dépendance à Qt ou à mpv. L'écriture est atomique et le nombre de
positions mémorisées est plafonné (contrairement à l'ancienne version HTML,
dont les clés s'accumulaient sans fin dans localStorage).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_POSITIONS = 300


def default_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "TwinsVideoPlayer" / "state.json"


def file_key(name: str, size: int) -> str:
    return f"{name}|{size}"


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_path()
        self.data = {"pos": {}, "bkm": {}, "blk": [], "prefs": {}}
        self.dirty = False
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for k in self.data:
                    if isinstance(loaded.get(k), type(self.data[k])):
                        self.data[k] = loaded[k]
        except (OSError, ValueError):
            pass

    # -- reprise de lecture -------------------------------------------------
    def get_pos(self, key: str) -> float | None:
        v = self.data["pos"].get(key)
        return float(v[0]) if v else None

    def set_pos(self, key: str, seconds: float) -> None:
        self.data["pos"][key] = [round(float(seconds), 1), time.time()]
        pos = self.data["pos"]
        if len(pos) > MAX_POSITIONS:
            for k in sorted(pos, key=lambda k: pos[k][1])[: len(pos) - MAX_POSITIONS]:
                del pos[k]
        self.dirty = True

    # -- marque-pages -------------------------------------------------------
    def bookmarks(self, key: str) -> list[int]:
        return list(self.data["bkm"].get(key, []))

    def add_bookmark(self, key: str, seconds: float) -> None:
        arr = set(self.data["bkm"].get(key, []))
        arr.add(int(seconds))
        self.data["bkm"][key] = sorted(arr)
        self.dirty = True

    def remove_bookmark(self, key: str, index: int) -> None:
        arr = self.data["bkm"].get(key, [])
        if 0 <= index < len(arr):
            del arr[index]
            if not arr:
                del self.data["bkm"][key]
            self.dirty = True

    # -- mots bloqués / préférences ----------------------------------------
    def blocked(self) -> list[str]:
        return list(self.data["blk"])

    def set_blocked(self, words) -> None:
        self.data["blk"] = [w.strip().lower() for w in words if w.strip()]
        self.dirty = True

    def pref(self, name: str, default=None):
        return self.data["prefs"].get(name, default)

    def set_pref(self, name: str, value) -> None:
        self.data["prefs"][name] = value
        self.dirty = True

    def is_blocked(self, filename: str) -> bool:
        low = filename.lower()
        return any(w in low for w in self.data["blk"])

    # -- écriture -----------------------------------------------------------
    def flush(self) -> None:
        if not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass
