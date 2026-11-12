"""Surface vidéo : mpv dessine dans un QOpenGLWidget (API de rendu de libmpv).

Qt garde tous les évènements clavier/souris ; il n'y a pas de fenêtre fille
native qui les avalerait.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from . import mpvlib


class MpvWidget(QOpenGLWidget):
    frame_ready = Signal()
    clicked = Signal()
    double_clicked = Signal()
    wheel_steps = Signal(int)

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.ctx = None
        self.frame_ready.connect(self.update)   # file d'attente : mpv appelle depuis son thread

    # -- cycle de vie GL -----------------------------------------------------
    def initializeGL(self):
        def get_proc_address(_ctx, name):
            gl = QOpenGLContext.currentContext()
            if gl is None:
                return 0
            try:
                return int(gl.getProcAddress(name) or 0)
            except TypeError:
                return 0

        self._free_ctx()
        self.ctx = mpvlib.mpv.MpvRenderContext(
            self.player, "opengl", opengl_init_params={"get_proc_address": get_proc_address})
        self.ctx.update_cb = self.frame_ready.emit
        # Si la fenêtre est recréée, libérer le contexte de rendu avec le contexte GL.
        self.context().aboutToBeDestroyed.connect(self._free_ctx)

    def _free_ctx(self):
        if self.ctx is not None:
            try:
                self.ctx.free()
            finally:
                self.ctx = None

    def paintGL(self):
        if self.ctx is None:
            return
        ratio = self.devicePixelRatioF()
        self.ctx.render(flip_y=False, opengl_fbo={
            "w": int(self.width() * ratio), "h": int(self.height() * ratio),
            "fbo": self.defaultFramebufferObject()})

    def shutdown(self):
        """À appeler avant player.terminate()."""
        if self.ctx is not None and self.isValid():
            self.makeCurrent()
            self._free_ctx()
            self.doneCurrent()
        else:
            self._free_ctx()

    # -- souris --------------------------------------------------------------
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit()

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy:
            self.wheel_steps.emit(1 if dy > 0 else -1)
