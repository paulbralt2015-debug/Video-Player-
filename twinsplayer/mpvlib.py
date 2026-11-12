"""Chargement de libmpv. À importer AVANT toute utilisation de ``mpv``.

La DLL (libmpv-2.dll) est cherchée à côté de l'exécutable (version compilée)
puis à la racine du projet (développement).
"""
from __future__ import annotations

import os
import sys


def _prepare() -> None:
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(getattr(sys, "_MEIPASS", None))
        dirs.append(os.path.dirname(sys.executable))
    dirs.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for d in dirs:
        if d and os.path.isdir(d):
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_prepare()

try:
    import mpv  # python-mpv
    ERROR = None
except (OSError, ImportError) as exc:  # DLL introuvable ou python-mpv absent
    mpv = None
    ERROR = str(exc)
