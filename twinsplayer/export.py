"""Export de la conversion (PAL/SECAM/NTSC + audio) via le mode d'encodage de libmpv.

Aucun processus externe : tout se passe dans libmpv, en arrière-plan.
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QThread, Signal

from . import mpvlib


class ExportJob(QThread):
    progress = Signal(int)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, src: str, dst: str, af: str, vf: str, channels: str, parent=None):
        super().__init__(parent)
        self.src, self.dst, self.af, self.vf, self.channels = src, dst, af, vf, channels
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        mpv = mpvlib.mpv
        finished = threading.Event()
        player = None
        try:
            opts = dict(o=self.dst, of="mp4", ovc="mpeg4", oac="aac",
                        ovcopts="qscale=3", oacopts="b=192k",
                        config=False, ytdl=False, input_default_bindings=False,
                        audio_channels=self.channels)
            if self.vf:
                opts["vf"] = self.vf
            if self.af:
                opts["af"] = self.af
            player = mpv.MPV(**opts)

            @player.event_callback("end-file")
            def _end(_event):
                finished.set()

            player.play(self.src)
            while not finished.wait(0.4):
                if self._cancel.is_set():
                    break
                pct = player.percent_pos
                if pct is not None:
                    self.progress.emit(max(0, min(99, int(pct))))
            player.terminate()
            player = None
            if self._cancel.is_set():
                if os.path.exists(self.dst):
                    os.remove(self.dst)
                self.failed.emit("Conversion annulée.")
            elif os.path.exists(self.dst) and os.path.getsize(self.dst) > 0:
                self.progress.emit(100)
                self.succeeded.emit(self.dst)
            else:
                self.failed.emit("Aucun fichier n'a été produit (voir le journal mpv).")
        except Exception as exc:  # noqa: BLE001 - remonté à l'utilisateur
            if player is not None:
                try:
                    player.terminate()
                except Exception:  # noqa: BLE001
                    pass
            self.failed.emit(str(exc))
