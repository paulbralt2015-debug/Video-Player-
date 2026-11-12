"""Twins Video Player 2 : fenêtre principale (PySide6 + libmpv)."""
from __future__ import annotations

import locale
import random
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPalette, QShortcut, QSurfaceFormat
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget,
    QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressDialog, QPushButton, QSlider,
    QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from . import __version__, mpvlib
from .export import ExportJob
from .filters import (AUDIO_MODES, VIDEO_STANDARDS, AudioSettings, build_audio, build_video)
from .mpvwidget import MpvWidget
from .store import Store, file_key
from .util import MEDIA_EXT, expand_media, fmt_time, parse_time

ROLE_PATH, ROLE_UID = 0x0100, 0x0101          # Qt.UserRole, Qt.UserRole + 1
RESUME_MARGIN = 8                              # s : pas de reprise près du début / de la fin


class JumpSlider(QSlider):
    """Curseur dont un clic dans la rainure saute à la position cliquée."""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setValue(QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(e.position().x()), max(1, self.width())))
        super().mousePressEvent(e)


class Bridge(QObject):
    """Relais thread mpv -> thread Qt."""
    log = Signal(str)


def apply_theme(app: QApplication, dark: bool) -> None:
    app.setStyle("Fusion")
    if dark:
        win, base, text, acc = QColor(20, 21, 27), QColor(29, 31, 40), QColor(233, 232, 228), QColor(63, 208, 212)
        mute = QColor(141, 144, 160)
    else:
        win, base, text, acc = QColor(243, 241, 238), QColor(255, 255, 255), QColor(27, 28, 34), QColor(10, 143, 154)
        mute = QColor(107, 110, 122)
    p, R = QPalette(), QPalette.ColorRole
    for role, color in ((R.Window, win), (R.Base, base), (R.AlternateBase, win), (R.Button, base),
                        (R.WindowText, text), (R.Text, text), (R.ButtonText, text),
                        (R.ToolTipBase, base), (R.ToolTipText, text), (R.PlaceholderText, mute),
                        (R.Highlight, acc), (R.HighlightedText, QColor(0, 0, 0) if dark else QColor(255, 255, 255)),
                        (R.Link, acc)):
        p.setColor(role, color)
    app.setPalette(p)


class MainWindow(QMainWindow):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.bridge = Bridge()
        self.bridge.log.connect(lambda m: self.statusBar().showMessage(m, 10000))
        self.current_uid: str | None = None
        self.current_key: str | None = None
        self.current_path: str | None = None
        self._resume: float | None = None
        self._eof_done = False
        self._last_save = 0.0
        self._seeking = False
        self._saved_view: dict | None = None
        self._fullscreen = False
        self._surf = False
        self._job: ExportJob | None = None

        self.setWindowTitle("Twins Video Player 2")
        self.setAcceptDrops(True)
        self.resize(1280, 780)

        self.player = self._create_player()
        self._build_ui()
        self._build_shortcuts()

        self.audio_timer = QTimer(self, singleShot=True, interval=220)
        self.audio_timer.timeout.connect(self.apply_audio)
        self.timer = QTimer(self, interval=200)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

        vol = int(self.store.pref("volume", 100))
        self.s_vol.setValue(vol)
        self.ed_blk.setText(", ".join(self.store.blocked()))

    # ------------------------------------------------------------------ mpv --
    def _create_player(self):
        def log_handler(level, component, message):
            if level in ("error", "fatal"):
                self.bridge.log.emit(f"mpv : {message.strip()}")

        return mpvlib.mpv.MPV(
            vo="libmpv", hwdec="auto-safe", keep_open="yes", idle=True, osc=False,
            input_default_bindings=False, input_vo_keyboard=False, config=False, ytdl=False,
            sub_codepage="utf8:cp1252", loglevel="warn", log_handler=log_handler,
            audio_client_name="Twins Video Player")

    def osd(self, text: str) -> None:
        self.statusBar().showMessage(text, 3000)
        try:
            self.player.show_text(text, 2000)
        except Exception:  # noqa: BLE001 - l'OSD n'est jamais critique
            pass

    # ------------------------------------------------------------------- UI --
    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(8, 8, 8, 4)
        self.video = MpvWidget(self.player)
        self.video.setMinimumSize(320, 180)
        self.video.clicked.connect(self._single_click)
        self.video.double_clicked.connect(self._double_click)
        self.video.wheel_steps.connect(lambda n: self.change_volume(5 * n))
        self._click_timer = QTimer(self, singleShot=True)
        self._click_timer.timeout.connect(self.toggle)
        lay.addWidget(self.video, 1)

        self.title = QLabel("Aucun fichier — déposez des vidéos ici ou appuyez sur O")
        self.title.setStyleSheet("font-weight:600")
        lay.addWidget(self.title)

        row = QHBoxLayout()
        self.lbl_cur, self.lbl_dur = QLabel("0:00"), QLabel("0:00")
        self.seek = JumpSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderReleased.connect(self._seek_released)
        row.addWidget(self.lbl_cur)
        row.addWidget(self.seek, 1)
        row.addWidget(self.lbl_dur)
        lay.addLayout(row)

        self.controls = QWidget()
        bar = QHBoxLayout(self.controls)
        bar.setContentsMargins(0, 0, 0, 0)

        def button(text, tip, fn):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, f=fn: self._guard(f))
            bar.addWidget(b)
            return b

        button("Préc.", "Précédent (V)", self.prev)
        self.btn_play = button("Lecture", "Lecture / pause (Z, X ou Espace)", self.toggle)
        button("Arrêt", "Arrêt (C)", self.stop)
        button("Suiv.", "Suivant (B)", lambda: self.next(auto=False))
        button("−10 s", "Reculer de 10 s (−)", lambda: self.seek_by(-10))
        button("+10 s", "Avancer de 10 s (+)", lambda: self.seek_by(10))
        bar.addStretch(1)
        bar.addWidget(QLabel("Vol."))
        self.s_vol = QSlider(Qt.Orientation.Horizontal)
        self.s_vol.setRange(0, 100)
        self.s_vol.setFixedWidth(120)
        self.s_vol.valueChanged.connect(self._volume_changed)
        bar.addWidget(self.s_vol)
        self.btn_mute = button("Muet", "Muet (M)", self.toggle_mute)
        self.cb_rate = QComboBox()
        for r in ("0.5", "0.75", "1", "1.25", "1.5", "2"):
            self.cb_rate.addItem(f"×{r}", float(r))
        self.cb_rate.setCurrentIndex(2)
        self.cb_rate.currentIndexChanged.connect(lambda: setattr(self.player, "speed", self.cb_rate.currentData()))
        bar.addWidget(self.cb_rate)
        self.btn_ab = button("A-B", "Boucle A-B (L)", self.ab_loop)
        button("Capture", "Capture d'image (P)", self.screenshot)
        button("Surf", "Mode Surf : petite fenêtre au premier plan (S)", self.toggle_surf)
        button("Plein écran", "Plein écran (F ou F11)", self.toggle_fullscreen)
        lay.addWidget(self.controls)
        self.setCentralWidget(central)

        self.dock = QDockWidget("Panneau", self)
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_list(), "Liste")
        self.tabs.addTab(self._tab_bookmarks(), "Marque-pages")
        self.tabs.addTab(self._tab_audio(), "Audio")
        self.tabs.addTab(self._tab_video(), "Vidéo")
        self.tabs.addTab(self._tab_options(), "Options")
        self.dock.setWidget(self.tabs)
        self.dock.setMinimumWidth(340)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.statusBar()

        # Les boutons/curseurs ne gardent pas le focus : les raccourcis (Espace, flèches…) restent actifs.
        for w in self.findChildren(QAbstractButton) + self.findChildren(QSlider):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _tab_list(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        r = QHBoxLayout()
        for text, fn in (("Fichiers", self.open_files), ("Dossier", self.open_folder),
                         ("Sous-titres", self.add_subtitles), ("Vider", self.clear_list)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            r.addWidget(b)
        v.addLayout(r)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemActivated.connect(lambda it: self.load_row(self.list.row(it)))
        self.list.itemDoubleClicked.connect(lambda it: self.load_row(self.list.row(it)))
        v.addWidget(self.list, 1)
        r2 = QHBoxLayout()
        for text, fn in (("↑", lambda: self.move_selected(-1)), ("↓", lambda: self.move_selected(1)),
                         ("Retirer", self.remove_selected)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            r2.addWidget(b)
        v.addLayout(r2)
        return w

    def _tab_bookmarks(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        b = QPushButton("Marquer ce moment (Q)")
        b.clicked.connect(self.add_bookmark)
        v.addWidget(b)
        self.bm_list = QListWidget()
        self.bm_list.itemActivated.connect(self._goto_bookmark)
        self.bm_list.itemDoubleClicked.connect(self._goto_bookmark)
        v.addWidget(self.bm_list, 1)
        d = QPushButton("Supprimer le marque-page")
        d.clicked.connect(self.delete_bookmark)
        v.addWidget(d)
        return w

    def _slider(self, lo, hi, val) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.valueChanged.connect(self._audio_changed)
        return s

    def _tab_audio(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.cb_amode = QComboBox()
        for k, label in AUDIO_MODES.items():
            self.cb_amode.addItem(label, k)
        self.cb_aout = QComboBox()
        self.cb_aout.addItem("Casque (repli stéréo + crossfeed)", "hp")
        self.cb_aout.addItem("Enceintes 5.1", "51")
        for cb in (self.cb_amode, self.cb_aout):
            cb.currentIndexChanged.connect(self._audio_changed)
        self.s_sur = self._slider(0, 300, 100)
        self.s_cen = self._slider(0, 200, 100)
        self.s_del = self._slider(0, 60, 15)
        self.s_bass = self._slider(-6, 12, 0)
        self.s_room = self._slider(0, 100, 0)
        f.addRow("Décodage", self.cb_amode)
        f.addRow("Sortie", self.cb_aout)
        f.addRow("Surround / largeur", self.s_sur)
        f.addRow("Centre", self.s_cen)
        f.addRow("Retard surround", self.s_del)
        f.addRow("Basses (dB)", self.s_bass)
        f.addRow("Salle (écho)", self.s_room)
        note = QLabel("Le décodage matriciel s'applique à une bande son stéréo encodée Lt/Rt "
                      "(DVD, VHS, TV) ; toute autre source est d'abord ramenée en stéréo. "
                      "Le mode 5.1 exige une sortie audio à 6 canaux.")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _tab_video(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.cb_std = QComboBox()
        for k, label in VIDEO_STANDARDS.items():
            self.cb_std.addItem(label, k)
        self.cb_dar = QComboBox()
        self.cb_dar.addItems(["4:3", "16:9"])
        self.chk_scan = QCheckBox("Lignes de balayage")
        for sig in (self.cb_std.currentIndexChanged, self.cb_dar.currentIndexChanged, self.chk_scan.toggled):
            sig.connect(self.apply_video)
        f.addRow("Standard", self.cb_std)
        f.addRow("Format", self.cb_dar)
        f.addRow(self.chk_scan)
        b = QPushButton("Enregistrer la conversion (MP4)…")
        b.clicked.connect(self.export_conversion)
        f.addRow(b)
        note = QLabel("La conversion change la cadence, la définition et la bande passante couleur "
                      "(approximation de PAL, SECAM et NTSC) en temps réel. L'enregistrement relit le "
                      "fichier avec les mêmes filtres vidéo et audio et écrit un MP4.")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _tab_options(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.cb_mode = QComboBox()
        for k, label in (("normal", "Normal"), ("repeat", "Répéter la liste"),
                         ("one", "Répéter le fichier"), ("rand", "Aléatoire")):
            self.cb_mode.addItem(label, k)
        self.cb_mode.currentIndexChanged.connect(
            lambda: setattr(self.player, "loop_file", "inf" if self.cb_mode.currentData() == "one" else "no"))
        self.cb_fit = QComboBox()
        for k, label in (("contain", "Ajustée"), ("cover", "Remplie"), ("fill", "Étirée")):
            self.cb_fit.addItem(label, k)
        self.cb_fit.currentIndexChanged.connect(self._fit_changed)
        self.s_bri, self.s_con, self.s_sat = (self._eq_slider(n) for n in ("brightness", "contrast", "saturation"))
        reset = QPushButton("Réinitialiser l'image")
        reset.clicked.connect(self._reset_image)
        self.cb_theme = QComboBox()
        self.cb_theme.addItems(["Sombre", "Clair"])
        self.cb_theme.setCurrentIndex(0 if self.store.pref("dark", True) else 1)
        self.cb_theme.currentIndexChanged.connect(self._theme_changed)
        self.ed_blk = QLineEdit()
        self.ed_blk.setPlaceholderText("mot1, mot2")
        sb = QPushButton("Enregistrer les mots bloqués")
        sb.clicked.connect(self._save_blocked)
        f.addRow("Mode", self.cb_mode)
        f.addRow("Image", self.cb_fit)
        f.addRow("Luminosité", self.s_bri)
        f.addRow("Contraste", self.s_con)
        f.addRow("Saturation", self.s_sat)
        f.addRow(reset)
        f.addRow("Thème", self.cb_theme)
        f.addRow("Mots bloqués", self.ed_blk)
        f.addRow(sb)
        hint = QLabel("Les fichiers dont le nom contient un de ces mots ne sont pas ajoutés à la liste.\n\n"
                      "Raccourcis : Z/X/Espace lecture · C arrêt · V/B précédent/suivant · +/− ±10 s · "
                      "←/→ ±5 s · ↑/↓ volume · T aller à · F/F11 plein écran · S Surf · Q marque-page · "
                      "O/A fichiers · D dossier · I infos · M muet · L boucle A-B · P capture · "
                      ", et . (ou ;) image par image")
        hint.setWordWrap(True)
        f.addRow(hint)
        return w

    def _eq_slider(self, prop: str) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(50, 150)
        s.setValue(100)
        s.valueChanged.connect(lambda v, p=prop: setattr(self.player, p, max(-100, min(100, (v - 100) * 2))))
        return s

    # ------------------------------------------------------------ raccourcis --
    def _build_shortcuts(self) -> None:
        K = Qt.Key
        table = [
            (K.Key_Z, self.toggle), (K.Key_X, self.toggle), (K.Key_Space, self.toggle),
            (K.Key_C, self.stop), (K.Key_V, self.prev), (K.Key_B, lambda: self.next(auto=False)),
            (K.Key_Plus, lambda: self.seek_by(10)), (K.Key_Equal, lambda: self.seek_by(10)),
            (K.Key_Minus, lambda: self.seek_by(-10)),
            (K.Key_Right, lambda: self.seek_by(5)), (K.Key_Left, lambda: self.seek_by(-5)),
            (K.Key_Up, lambda: self.change_volume(5)), (K.Key_Down, lambda: self.change_volume(-5)),
            (K.Key_T, self.jump), (K.Key_F, self.toggle_fullscreen), (K.Key_F11, self.toggle_fullscreen),
            (K.Key_Escape, self.leave_special_views), (K.Key_S, self.toggle_surf),
            (K.Key_Q, self.add_bookmark_and_show), (K.Key_O, self.open_files), (K.Key_A, self.open_files),
            (K.Key_D, self.open_folder), (K.Key_I, self.info), (K.Key_M, self.toggle_mute),
            (K.Key_L, self.ab_loop), (K.Key_P, self.screenshot),
            (K.Key_Period, lambda: self.frame_step(1)), (K.Key_Semicolon, lambda: self.frame_step(1)),
            (K.Key_Comma, lambda: self.frame_step(-1)),
        ]
        for key, fn in table:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda fn=fn: self._guard(fn))

    def _guard(self, fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - une commande mpv refusée ne doit pas fermer l'application
            self.statusBar().showMessage(f"Commande impossible : {exc}", 5000)

    # ------------------------------------------------------------- playlist --
    def add_paths(self, paths) -> None:
        files = expand_media([str(p) for p in paths])
        if not files:
            self.osd("Aucun fichier audio ou vidéo reconnu")
            return
        added, blocked, first_row = 0, 0, None
        for path in files:
            name = Path(path).name
            if self.store.is_blocked(name):
                blocked += 1
                continue
            item = QListWidgetItem(name)
            item.setToolTip(path)
            item.setData(ROLE_PATH, path)
            item.setData(ROLE_UID, uuid.uuid4().hex)
            self.list.addItem(item)
            if first_row is None:
                first_row = self.list.count() - 1
            added += 1
        if blocked:
            self.osd(f"{blocked} fichier(s) bloqué(s) par vos mots bloqués")
        if added and self.current_uid is None and first_row is not None:
            self.load_row(first_row)

    def open_files(self) -> None:
        exts = " ".join("*" + e for e in sorted(MEDIA_EXT))
        files, _ = QFileDialog.getOpenFileNames(self, "Ajouter des fichiers", "", f"Médias ({exts});;Tous (*.*)")
        if files:
            self.add_paths(files)

    def open_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Ajouter un dossier")
        if d:
            self.add_paths([d])

    def current_row(self) -> int:
        for i in range(self.list.count()):
            if self.list.item(i).data(ROLE_UID) == self.current_uid:
                return i
        return -1

    def _refresh_marks(self) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            f = it.font()
            f.setBold(it.data(ROLE_UID) == self.current_uid)
            it.setFont(f)

    def load_row(self, row: int) -> None:
        if not 0 <= row < self.list.count():
            return
        self._remember_position()
        item = self.list.item(row)
        path = item.data(ROLE_PATH)
        try:
            size = Path(path).stat().st_size
        except OSError:
            self.osd("Fichier introuvable : " + Path(path).name)
            return
        self.current_uid, self.current_path = item.data(ROLE_UID), path
        self.current_key = file_key(Path(path).name, size)
        self._eof_done = False
        self._last_save = time.monotonic()
        self._resume = self.store.get_pos(self.current_key)
        self.title.setText(Path(path).name)
        self.player["ab-loop-a"] = "no"
        self.player["ab-loop-b"] = "no"
        self.player.play(path)
        self.player.pause = False
        self._refresh_marks()
        self.refresh_bookmarks()

    def _unload(self) -> None:
        self.player.command("stop")
        self.current_uid = self.current_key = self.current_path = None
        self._resume = None
        self.title.setText("Aucun fichier")
        self.lbl_cur.setText("0:00")
        self.lbl_dur.setText("0:00")
        self.seek.setValue(0)
        self.refresh_bookmarks()

    def remove_selected(self) -> None:
        for it in self.list.selectedItems():
            if it.data(ROLE_UID) == self.current_uid:
                self._remember_position()
                self._unload()
            self.list.takeItem(self.list.row(it))
        self._refresh_marks()

    def move_selected(self, d: int) -> None:
        row, new = self.list.currentRow(), self.list.currentRow() + d
        if row < 0 or not 0 <= new < self.list.count():
            return
        it = self.list.takeItem(row)
        self.list.insertItem(new, it)
        self.list.setCurrentRow(new)

    def clear_list(self) -> None:
        if self.list.count() and QMessageBox.question(self, "Vider la liste", "Vider la liste de lecture ?") \
                == QMessageBox.StandardButton.Yes:
            self._remember_position()
            self.list.clear()
            self._unload()

    # --------------------------------------------------------------- lecture --
    def toggle(self) -> None:
        if self.current_uid is None:
            if self.list.count():
                self.load_row(0)
            else:
                self.open_files()
            return
        if self.player.eof_reached:
            self._eof_done = False
            self.player.seek(0, "absolute")
            self.player.pause = False
        else:
            self.player.pause = not self.player.pause

    def stop(self) -> None:
        if self.current_uid is None:
            return
        self.player.pause = True
        self.player.seek(0, "absolute")

    def next(self, auto: bool = False) -> None:
        n = self.list.count()
        if not n:
            return
        mode = self.cb_mode.currentData()
        cur = self.current_row()
        if mode == "rand":
            row = random.randrange(n)
        else:
            row = cur + 1
            if row >= n:
                if mode == "repeat" or not auto:
                    row = 0
                else:
                    return
        self.load_row(row)

    def prev(self) -> None:
        n = self.list.count()
        if not n:
            return
        if (self.player.time_pos or 0) > 3:
            self.player.seek(0, "absolute")
            return
        self.load_row((self.current_row() - 1) % n)

    def seek_by(self, delta: float) -> None:
        if self.current_uid is not None:
            self.player.seek(delta, "relative")

    def frame_step(self, direction: int) -> None:
        if self.current_uid is None:
            return
        if direction > 0:
            self.player.frame_step()
        else:
            self.player.frame_back_step()

    def jump(self) -> None:
        if self.current_uid is None:
            return
        text, ok = QInputDialog.getText(self, "Aller à", "Temps (mm:ss ou hh:mm:ss) :",
                                        text=fmt_time(self.player.time_pos))
        if not ok:
            return
        t = parse_time(text)
        if t is None:
            self.osd("Format invalide. Exemple : 1:25:30")
        else:
            self.player.seek(t, "absolute")

    def _seek_released(self) -> None:
        self._seeking = False
        dur = self.player.duration
        if dur and self.current_uid is not None:
            self.player.seek(self.seek.value() / 1000 * dur, "absolute")

    def ab_loop(self) -> None:
        if self.current_uid is not None:
            self.player.command("ab-loop")      # A, puis B, puis désactivation (géré par mpv)

    def screenshot(self) -> None:
        if self.current_uid is None:
            self.osd("Aucune image à capturer")
            return
        pics = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation) or str(Path.home())
        t = int(self.player.time_pos or 0)
        out = Path(pics) / f"{Path(self.current_path).stem}_{t}s.png"
        self.player.screenshot_to_file(str(out), includes="video")
        self.osd(f"Capture enregistrée : {out.name}")

    def info(self) -> None:
        if self.current_uid is None:
            self.osd("Aucun fichier chargé")
            return
        p = self.player
        size = Path(self.current_path).stat().st_size / 1048576
        parts = [Path(self.current_path).name]
        if p.width:
            parts.append(f"{p.width}×{p.height}")
        for prop in ("video-format", "audio-codec-name"):
            try:
                v = p[prop]
                if v:
                    parts.append(str(v))
            except Exception:  # noqa: BLE001
                pass
        parts += [fmt_time(p.duration), f"{size:.1f} Mo"]
        self.osd(" · ".join(parts))

    # ----------------------------------------------------------------- son --
    def _volume_changed(self, v: int) -> None:
        self.player.volume = v
        self.store.set_pref("volume", v)

    def change_volume(self, delta: int) -> None:
        self.s_vol.setValue(max(0, min(100, self.s_vol.value() + delta)))
        self.osd(f"Volume {self.s_vol.value()} %")

    def toggle_mute(self) -> None:
        self.player.mute = not self.player.mute
        self.btn_mute.setText("Son" if self.player.mute else "Muet")

    def audio_settings(self) -> AudioSettings:
        return AudioSettings(mode=self.cb_amode.currentData(), output=self.cb_aout.currentData(),
                             surround=self.s_sur.value(), center=self.s_cen.value(),
                             delay_ms=self.s_del.value(), bass_db=self.s_bass.value(), room=self.s_room.value())

    def _audio_changed(self, *_):
        self.audio_timer.start()        # regroupe les mouvements de curseur (évite de relancer la chaîne à chaque pas)

    def apply_audio(self) -> None:
        plan = build_audio(self.audio_settings())
        try:
            self.player["audio-channels"] = plan.channels
            self.player["af"] = plan.af
        except Exception as exc:  # noqa: BLE001
            self.osd(f"Filtre audio refusé : {exc}")

    # --------------------------------------------------------------- image --
    def apply_video(self, *_):
        vf = build_video(self.cb_std.currentData(), self.cb_dar.currentText(), self.chk_scan.isChecked())
        try:
            self.player["vf"] = vf
        except Exception as exc:  # noqa: BLE001
            self.osd(f"Filtre vidéo refusé : {exc}")

    def _fit_changed(self) -> None:
        mode = self.cb_fit.currentData()
        self.player.keepaspect = mode != "fill"
        self.player.panscan = 1.0 if mode == "cover" else 0.0

    def _reset_image(self) -> None:
        for s in (self.s_bri, self.s_con, self.s_sat):
            s.setValue(100)
        self.cb_fit.setCurrentIndex(0)

    def _theme_changed(self) -> None:
        dark = self.cb_theme.currentIndex() == 0
        self.store.set_pref("dark", dark)
        apply_theme(QApplication.instance(), dark)

    def _save_blocked(self) -> None:
        self.store.set_blocked(self.ed_blk.text().split(","))
        self.store.flush()
        self.osd("Mots bloqués enregistrés")

    # ------------------------------------------------------ marque-pages --
    def refresh_bookmarks(self) -> None:
        self.bm_list.clear()
        if self.current_key:
            for t in self.store.bookmarks(self.current_key):
                it = QListWidgetItem(fmt_time(t))
                it.setData(ROLE_PATH, t)
                self.bm_list.addItem(it)

    def add_bookmark(self) -> None:
        if self.current_key is None or self.player.time_pos is None:
            return
        self.store.add_bookmark(self.current_key, self.player.time_pos)
        self.store.flush()
        self.refresh_bookmarks()
        self.osd(f"Marque-page ajouté à {fmt_time(self.player.time_pos)}")

    def add_bookmark_and_show(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.add_bookmark()

    def _goto_bookmark(self, item: QListWidgetItem) -> None:
        self.player.seek(float(item.data(ROLE_PATH)), "absolute")

    def delete_bookmark(self) -> None:
        row = self.bm_list.currentRow()
        if self.current_key and row >= 0:
            self.store.remove_bookmark(self.current_key, row)
            self.store.flush()
            self.refresh_bookmarks()

    def add_subtitles(self) -> None:
        if self.current_uid is None:
            self.osd("Chargez d'abord une vidéo")
            return
        f, _ = QFileDialog.getOpenFileName(self, "Sous-titres", "", "Sous-titres (*.srt *.vtt *.ass *.ssa *.sub);;Tous (*.*)")
        if f:
            self.player.sub_add(f)
            self.osd("Sous-titres chargés")

    # -------------------------------------------------------- export MP4 --
    def export_conversion(self) -> None:
        std = self.cb_std.currentData()
        if std == "off":
            self.osd("Choisissez d'abord PAL, SECAM ou NTSC")
            return
        if self.current_uid is None:
            self.osd("Chargez d'abord une vidéo")
            return
        if self._job is not None and self._job.isRunning():
            self.osd("Une conversion est déjà en cours")
            return
        default = str(Path(self.current_path).with_name(f"{Path(self.current_path).stem}_{std}.mp4"))
        dst, _ = QFileDialog.getSaveFileName(self, "Enregistrer la conversion", default, "MP4 (*.mp4)")
        if not dst:
            return
        plan = build_audio(self.audio_settings())
        vf = build_video(std, self.cb_dar.currentText(), self.chk_scan.isChecked())
        job = ExportJob(self.current_path, dst, plan.af, vf, plan.channels, self)
        dlg = QProgressDialog("Conversion en cours…", "Annuler", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        job.progress.connect(dlg.setValue)
        dlg.canceled.connect(job.cancel)

        def ok(path):
            self.osd(f"Conversion enregistrée : {Path(path).name}")

        def ko(msg):
            dlg.cancel()
            QMessageBox.warning(self, "Conversion", msg)

        job.succeeded.connect(ok)
        job.failed.connect(ko)
        self._job = job
        job.start()

    # ------------------------------------------------------ vues spéciales --
    def _single_click(self) -> None:
        self._click_timer.start(QApplication.doubleClickInterval())

    def _double_click(self) -> None:
        self._click_timer.stop()
        self.toggle_fullscreen()

    def _hide_chrome(self, hidden: bool) -> None:
        self.dock.setVisible(not hidden)
        self.controls.setVisible(not hidden)
        self.statusBar().setVisible(not hidden)

    def toggle_fullscreen(self) -> None:
        if self._surf:
            self.toggle_surf()
        if self._fullscreen:
            self._fullscreen = False
            self._hide_chrome(False)
            self.showNormal()
        else:
            self._fullscreen = True
            self._hide_chrome(True)
            self.showFullScreen()

    def _set_topmost(self, on: bool) -> None:
        # SetWindowPos ne recrée pas la fenêtre (changer les « window flags » détruirait le contexte OpenGL).
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes
        fn = ctypes.windll.user32.SetWindowPos
        fn.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                       ctypes.c_int, ctypes.c_int, wintypes.UINT]
        fn(wintypes.HWND(int(self.winId())), wintypes.HWND(-1 if on else -2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def toggle_surf(self) -> None:
        if self._fullscreen:
            self.toggle_fullscreen()
        if not self._surf:
            self._surf = True
            self._saved_view = {"geometry": self.saveGeometry()}
            self._hide_chrome(True)
            self._set_topmost(True)
            self.resize(480, 270)
        else:
            self._surf = False
            self._set_topmost(False)
            self._hide_chrome(False)
            if self._saved_view:
                self.restoreGeometry(self._saved_view["geometry"])

    def leave_special_views(self) -> None:
        if self._fullscreen:
            self.toggle_fullscreen()
        elif self._surf:
            self.toggle_surf()

    # -------------------------------------------------------------- horloge --
    def _remember_position(self) -> None:
        if self.current_key and not self.player.eof_reached:
            pos = self.player.time_pos
            if pos is not None:
                self.store.set_pos(self.current_key, pos)
                self.store.flush()

    def tick(self) -> None:
        p = self.player
        self.btn_play.setText("Pause" if self.current_uid and not p.pause else "Lecture")
        if self.current_uid is None:
            return
        pos, dur = p.time_pos, p.duration
        if dur:
            self.lbl_dur.setText(fmt_time(dur))
        if pos is not None:
            self.lbl_cur.setText(fmt_time(pos))
            if dur and not self._seeking:
                self.seek.setValue(int(pos / dur * 1000))
        if self._resume is not None and dur:
            t, self._resume = self._resume, None
            if RESUME_MARGIN < t < dur - RESUME_MARGIN:
                p.seek(t, "absolute")
                self.osd(f"Reprise à {fmt_time(t)}")
        try:
            a, b = p["ab-loop-a"], p["ab-loop-b"]
            self.btn_ab.setText("A-B" if not isinstance(a, (int, float)) else
                                ("A…" if not isinstance(b, (int, float)) else "A-B on"))
        except Exception:  # noqa: BLE001
            pass
        now = time.monotonic()
        if pos is not None and now - self._last_save > 4:
            self._last_save = now
            self.store.set_pos(self.current_key, pos)
            self.store.flush()
        if p.eof_reached and not self._eof_done:
            self._eof_done = True
            self.store.set_pos(self.current_key, 0)      # le fichier qui vient de finir, pas le suivant
            self.store.flush()
            self.next(auto=True)

    # ------------------------------------------------------ glisser-déposer --
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)

    def closeEvent(self, e):
        self.timer.stop()
        if self._job is not None and self._job.isRunning():
            self._job.cancel()
            self._job.wait(5000)
        self._remember_position()
        self.store.flush()
        self.video.shutdown()
        self.player.terminate()
        super().closeEvent(e)


def main() -> int:
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)
    app.setApplicationName("Twins Video Player 2")
    app.setApplicationVersion(__version__)
    locale.setlocale(locale.LC_NUMERIC, "C")     # exigé par libmpv

    if mpvlib.mpv is None:
        QMessageBox.critical(None, "Twins Video Player 2",
                             "libmpv-2.dll est introuvable.\n\nPlacez-la à côté de l'exécutable.\n\n" + str(mpvlib.ERROR))
        return 1

    store = Store()
    apply_theme(app, bool(store.pref("dark", True)))
    win = MainWindow(store)
    win.show()
    if len(sys.argv) > 1:
        win.add_paths(sys.argv[1:])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
