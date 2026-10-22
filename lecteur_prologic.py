"""
Lecteur vidéo Windows avec décodage matriciel Dolby Surround / Pro Logic II.

INSTALLATION
  1. Python 3.10+  ->  pip install PyQt6 python-mpv
  2. Télécharger libmpv pour Windows (mpv-dev-x86_64, "libmpv-2.dll" / "mpv-2.dll")
     et placer la DLL dans le même dossier que ce script.
  3. Windows : Paramètres > Son > votre sortie > Configuration des haut-parleurs = 5.1
     (ou sortie HDMI vers un ampli A/V).
  4. python lecteur_prologic.py [fichier_video]

EMPAQUETAGE .exe
  pip install pyinstaller
  pyinstaller --onefile --windowed --add-binary "libmpv-2.dll;." lecteur_prologic.py

NOTE
  Il s'agit d'un décodeur matriciel ouvert (matrice Lt/Rt + steering FFmpeg),
  pas du décodeur Dolby certifié (propriétaire, sous licence).
  Les sources déjà multicanal (AC-3, E-AC-3, DTS, 5.1 AAC...) sont lues telles quelles.
"""
import os
import sys
import locale

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["PATH"] = HERE + os.pathsep + os.environ.get("PATH", "")
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(HERE)

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

import mpv

K = 0.7071  # -3 dB


def lin(db):
    return 10 ** (db / 20)


def build_af(s):
    """Construit la chaîne de filtres FFmpeg (lavfi) selon les réglages."""
    mode = s["mode"]
    if mode == "off":
        return ""

    music = mode == "music"
    dim = s["dimension"] if music else 0            # -3..+3 (avant <-> arrière)
    cw = (s["center_width"] / 7 * 0.5) if music else 0.0   # largeur du centre
    pano = 0.5 if (music and s["panorama"]) else 0.0       # effet panorama
    gF, gS = lin(-dim * 1.5), lin(dim * 1.5)
    sub = s["sub"]
    lfe = f"{K / 2:.4f}*FL+{K / 2:.4f}*FR" if sub else "0*FL"

    parts = []
    if mode in ("movie", "music"):
        # Upmix stéréo -> 5.1 avec steering actif (domaine fréquentiel)
        parts.append("surround=chl_out=5.1:chl_in=stereo:lfe=0")
        fl = (f"{gF:.4f}*FL+{cw * K * gF:.4f}*FC+{pano * gS:.4f}*BL")
        fr = (f"{gF:.4f}*FR+{cw * K * gF:.4f}*FC+{pano * gS:.4f}*BR")
        fc = f"{(1 - cw) * gF:.4f}*FC"
        parts.append(
            f"pan=5.1|FL={fl}|FR={fr}|FC={fc}|LFE={lfe.replace('FL', 'FL').replace('FR', 'FR')}"
            f"|BL={gS:.4f}*BL|BR={gS:.4f}*BR"
        )
    else:  # 'pl' : matrice Dolby Surround passive classique
        parts.append(
            f"pan=5.1|FL=FL|FR=FR|FC={K:.4f}*FL+{K:.4f}*FR|LFE={lfe}"
            f"|BL={K:.4f}*FL-{K:.4f}*FR|BR={K:.4f}*FL-{K:.4f}*FR"
        )
        parts.append("lowpass=f=7000:c=BL+BR")  # surround limité à 7 kHz

    if sub:
        parts.append("lowpass=f=120:c=LFE")
    if s["delay"] > 0:
        d = s["delay"]
        parts.append(f"adelay={'0|0|0|0|%d|%d' % (d, d)}")
    parts.append("alimiter=limit=0.95")
    return "lavfi=[" + ",".join(parts) + "]"


class Lecteur(QMainWindow):
    sig_time = pyqtSignal(float)
    sig_dur = pyqtSignal(float)
    sig_params = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lecteur Pro Logic II")
        self.resize(1100, 700)
        self.setAcceptDrops(True)
        self.duration = 0.0
        self.src_channels = 0

        # --- UI ---
        self.video = QWidget()
        self.video.setStyleSheet("background:black;")
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)

        self.btn_open = QPushButton("Ouvrir…")
        self.btn_play = QPushButton("⏯")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.lbl_time = QLabel("00:00 / 00:00")
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 130)
        self.vol.setValue(100)
        self.vol.setFixedWidth(110)

        row1 = QHBoxLayout()
        for w in (self.btn_open, self.btn_play, self.seek, self.lbl_time,
                  QLabel("🔊"), self.vol):
            row1.addWidget(w)

        self.mode = QComboBox()
        self.mode.addItem("Désactivé (natif / stéréo)", "off")
        self.mode.addItem("Pro Logic II Movie", "movie")
        self.mode.addItem("Pro Logic II Music", "music")
        self.mode.addItem("Dolby Surround Pro Logic (matriciel classique)", "pl")
        self.mode.setCurrentIndex(1)

        self.cw = self._slider(0, 7, 3)
        self.dim = self._slider(-3, 3, 0)
        self.pano = QCheckBox("Panorama")
        self.sub = QCheckBox("Caisson de basses (LFE)")
        self.delay = QSpinBox()
        self.delay.setRange(0, 30)
        self.delay.setSuffix(" ms")
        self.delay.setValue(10)
        self.lbl_info = QLabel("")

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Mode :"))
        row2.addWidget(self.mode)
        row2.addWidget(QLabel("Largeur centre"))
        row2.addWidget(self.cw)
        row2.addWidget(QLabel("Dimension"))
        row2.addWidget(self.dim)
        row2.addWidget(self.pano)
        row2.addWidget(self.sub)
        row2.addWidget(QLabel("Retard surround"))
        row2.addWidget(self.delay)

        self.panel = QWidget()
        pl = QVBoxLayout(self.panel)
        pl.addLayout(row1)
        pl.addLayout(row2)
        pl.addWidget(self.lbl_info)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.panel)
        self.setCentralWidget(central)

        # --- mpv ---
        self.player = mpv.MPV(
            wid=str(int(self.video.winId())),
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            ao="wasapi",
            hwdec="auto-safe",
        )
        self.player.property_observer("time-pos")(lambda n, v: v is not None and self.sig_time.emit(v))
        self.player.property_observer("duration")(lambda n, v: v is not None and self.sig_dur.emit(v))
        self.player.property_observer("audio-params")(lambda n, v: self.sig_params.emit(v))

        # --- signaux ---
        self.sig_time.connect(self.on_time)
        self.sig_dur.connect(lambda d: setattr(self, "duration", d))
        self.sig_params.connect(self.on_params)
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_play.clicked.connect(self.toggle_pause)
        self.seek.sliderMoved.connect(
            lambda v: self.duration and self.player.seek(v / 1000 * self.duration, "absolute"))
        self.vol.valueChanged.connect(lambda v: setattr(self.player, "volume", v))
        self.mode.currentIndexChanged.connect(self.apply_filters)
        for w in (self.cw, self.dim):
            w.valueChanged.connect(self.apply_filters)
        self.delay.valueChanged.connect(self.apply_filters)
        self.pano.toggled.connect(self.apply_filters)
        self.sub.toggled.connect(self.apply_filters)

        if len(sys.argv) > 1:
            QTimer.singleShot(200, lambda: self.play(sys.argv[1]))
        self.update_controls()

    @staticmethod
    def _slider(lo, hi, val):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.setFixedWidth(90)
        s.setTickPosition(QSlider.TickPosition.TicksBelow)
        return s

    # --- lecture ---
    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir une vidéo", "",
            "Vidéos (*.mkv *.mp4 *.avi *.mov *.ts *.m2ts *.wmv *.webm *.flv *.vob *.mpg);;Tous (*.*)")
        if path:
            self.play(path)

    def play(self, path):
        self.player.play(path)
        self.player.pause = False
        self.setWindowTitle(f"Lecteur Pro Logic II — {os.path.basename(path)}")

    def toggle_pause(self):
        self.player.pause = not self.player.pause

    def on_time(self, t):
        if self.duration and not self.seek.isSliderDown():
            self.seek.setValue(int(t / self.duration * 1000))
        fmt = lambda x: f"{int(x) // 3600:02d}:{int(x) // 60 % 60:02d}:{int(x) % 60:02d}"
        self.lbl_time.setText(f"{fmt(t)} / {fmt(self.duration)}")

    # --- audio ---
    def on_params(self, p):
        ch = p.get("channel-count", 0) if isinstance(p, dict) else 0
        if ch != self.src_channels:
            self.src_channels = ch
            self.apply_filters()

    def settings(self):
        return {
            "mode": self.mode.currentData(),
            "center_width": self.cw.value(),
            "dimension": self.dim.value(),
            "panorama": self.pano.isChecked(),
            "sub": self.sub.isChecked(),
            "delay": self.delay.value(),
        }

    def update_controls(self):
        music = self.mode.currentData() == "music"
        for w in (self.cw, self.dim, self.pano):
            w.setEnabled(music)
        self.delay.setEnabled(self.mode.currentData() != "off")

    def apply_filters(self):
        self.update_controls()
        s = self.settings()
        if self.src_channels > 2:
            graph, info = "", f"Source native {self.src_channels} canaux : lecture directe."
        elif self.src_channels == 1:
            graph, info = "", "Source mono : aucun décodage."
        else:
            graph = build_af(s)
            info = ("Stéréo : aucun décodage." if not graph
                    else f"Décodage actif : {self.mode.currentText()} -> 5.1")
        try:
            self.player.audio_channels = "5.1,stereo" if graph else "auto-safe"
            if graph:
                self.player.command("af", "set", graph)
            else:
                self.player.command("af", "clear", "")
        except Exception as e:  # filtre refusé, DLL trop ancienne...
            info = f"Erreur filtre audio : {e}"
        self.lbl_info.setText(info)

    # --- événements ---
    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Space:
            self.toggle_pause()
        elif k == Qt.Key.Key_F:
            self.toggle_fullscreen()
        elif k == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
        elif k == Qt.Key.Key_Right:
            self.player.seek(10)
        elif k == Qt.Key.Key_Left:
            self.player.seek(-10)
        elif k == Qt.Key.Key_Up:
            self.vol.setValue(min(130, self.vol.value() + 5))
        elif k == Qt.Key.Key_Down:
            self.vol.setValue(max(0, self.vol.value() - 5))

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.panel.show()
            self.showNormal()
        else:
            self.panel.hide()
            self.showFullScreen()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.play(urls[0].toLocalFile())

    def closeEvent(self, e):
        self.player.terminate()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    try:
        locale.setlocale(locale.LC_NUMERIC, "C")  # requis par libmpv
    except locale.Error:
        pass
    w = Lecteur()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
