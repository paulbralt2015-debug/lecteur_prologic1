"""
Lecteur vidéo Windows avec décodage matriciel Dolby Surround / Pro Logic II.
Moteur vidéo/audio : libmpv + FFmpeg. E-AC-3 et DTS sont décodés par FFmpeg
lorsqu'ils sont inclus dans le build libmpv utilisé.
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

K = 0.7071


def lin(db):
    return 10 ** (db / 20)


def build_af(s):
    """Upmix stéréo vers 5.1 ou matrice Lt/Rt vers 5.1 avec FFmpeg."""
    mode = s["mode"]
    if mode == "off":
        return ""

    music = mode == "music"
    dim = s["dimension"] if music else 0
    cw = (s["center_width"] / 7 * 0.5) if music else 0.0
    pano = 0.5 if (music and s["panorama"]) else 0.0
    gF, gS = lin(-dim * 1.5), lin(dim * 1.5)
    lfe = f"{K / 2:.4f}*FL+{K / 2:.4f}*FR" if s["sub"] else "0*FL"

    parts = []
    if mode in ("movie", "music"):
        parts.append("surround=chl_out=5.1:chl_in=stereo:lfe=0")
        fl = f"{gF:.4f}*FL+{cw * K * gF:.4f}*FC+{pano * gS:.4f}*BL"
        fr = f"{gF:.4f}*FR+{cw * K * gF:.4f}*FC+{pano * gS:.4f}*BR"
        fc = f"{(1 - cw) * gF:.4f}*FC"
        parts.append(
            f"pan=5.1|FL={fl}|FR={fr}|FC={fc}|LFE={lfe}"
            f"|BL={gS:.4f}*BL|BR={gS:.4f}*BR"
        )
    else:
        parts.append(
            f"pan=5.1|FL=FL|FR=FR|FC={K:.4f}*FL+{K:.4f}*FR|LFE={lfe}"
            f"|BL={K:.4f}*FL-{K:.4f}*FR|BR={K:.4f}*FL-{K:.4f}*FR"
        )
        parts.append("lowpass=f=7000:c=BL+BR")

    if s["sub"]:
        parts.append("lowpass=f=120:c=LFE")
    if s["delay"] > 0:
        d = s["delay"]
        parts.append(f"adelay=0|0|0|0|{d}|{d}")
    parts.append("alimiter=limit=0.95")
    return "lavfi=[" + ",".join(parts) + "]"


class Lecteur(QMainWindow):
    sig_time = pyqtSignal(float)
    sig_dur = pyqtSignal(float)
    sig_params = pyqtSignal(object)
    sig_tracks = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lecteur Pro Logic II")
        self.resize(1200, 760)
        self.setAcceptDrops(True)
        self.duration = 0.0
        self.src_channels = 0
        self.current_path = ""
        self.audio_codec = "détection…"
        self.audio_rate = 0
        self.audio_layout = ""

        self.video = QWidget()
        self.video.setStyleSheet("background:black;")
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)

        self.btn_open = QPushButton("Ouvrir…")
        self.btn_play = QPushButton("▶ / Pause")
        self.btn_fullscreen = QPushButton("Plein écran")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.lbl_time = QLabel("00:00:00 / 00:00:00")
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 130)
        self.vol.setValue(100)
        self.vol.setFixedWidth(105)

        row1 = QHBoxLayout()
        for w in (self.btn_open, self.btn_play, self.seek, self.lbl_time,
                  QLabel("Volume"), self.vol, self.btn_fullscreen):
            row1.addWidget(w)

        self.track_combo = QComboBox()
        self.track_combo.setMinimumWidth(220)
        self.track_combo.addItem("Piste audio : automatique", "auto")

        self.ratio_combo = QComboBox()
        self.ratio_combo.addItem("Aspect ratio : automatique", "no")
        for label, value in (
            ("4:3", "4:3"), ("16:9", "16:9"), ("16:10", "16:10"),
            ("1.85:1", "1.85:1"), ("2.35:1", "2.35:1"),
            ("2.39:1", "2.39:1"), ("21:9", "21:9"), ("1:1", "1:1"),
        ):
            self.ratio_combo.addItem(label, value)

        self.output_mode = QComboBox()
        self.output_mode.addItem("Sortie automatique (source native)", "auto")
        self.output_mode.addItem("Downmix vers stéréo", "stereo")
        self.output_mode.addItem("Upmix / sortie 5.1", "5.1")

        self.mode = QComboBox()
        self.mode.addItem("Désactivé (son natif)", "off")
        self.mode.addItem("Pro Logic II Movie", "movie")
        self.mode.addItem("Pro Logic II Music", "music")
        self.mode.addItem("Dolby Surround Pro Logic classique", "pl")
        self.mode.setCurrentIndex(1)

        self.cw = self._slider(0, 7, 3)
        self.dim = self._slider(-3, 3, 0)
        self.pano = QCheckBox("Panorama")
        self.sub = QCheckBox("Caisson LFE")
        self.delay = QSpinBox()
        self.delay.setRange(0, 30)
        self.delay.setSuffix(" ms")
        self.delay.setValue(10)
        self.lbl_info = QLabel("Ouvrez un fichier vidéo ou déposez-le dans la fenêtre.")

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Piste :"))
        row2.addWidget(self.track_combo, 2)
        row2.addWidget(self.ratio_combo)
        row2.addWidget(self.output_mode)
        row2.addWidget(QLabel("Mode surround :"))
        row2.addWidget(self.mode, 2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Largeur centre"))
        row3.addWidget(self.cw)
        row3.addWidget(QLabel("Dimension"))
        row3.addWidget(self.dim)
        row3.addWidget(self.pano)
        row3.addWidget(self.sub)
        row3.addWidget(QLabel("Retard surround"))
        row3.addWidget(self.delay)

        self.panel = QWidget()
        pl = QVBoxLayout(self.panel)
        pl.addLayout(row1)
        pl.addLayout(row2)
        pl.addLayout(row3)
        pl.addWidget(self.lbl_info)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.panel)
        self.setCentralWidget(central)

        self.player = mpv.MPV(
            wid=str(int(self.video.winId())),
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            ao="wasapi",
            hwdec="auto-safe",
            keep_open="no",
            # Décodage logiciel FFmpeg : les flux sont décodés avant traitement.
            audio_spdif="",
            audio_resample_filter_size=32,
            audio_resample_phase_shift=10,
            audio_resample_cutoff=0.95,
            audio_resample_linear=False,
            audio_pitch_correction=True,
        )
        self.player.property_observer("time-pos")(
            lambda n, v: v is not None and self.sig_time.emit(float(v)))
        self.player.property_observer("duration")(
            lambda n, v: v is not None and self.sig_dur.emit(float(v)))
        self.player.property_observer("audio-params")(
            lambda n, v: self.sig_params.emit(v))
        self.player.property_observer("audio-codec-name")(
            lambda n, v: self.set_audio_codec(v))
        self.player.property_observer("track-list")(
            lambda n, v: self.sig_tracks.emit(v or []))

        self.sig_time.connect(self.on_time)
        self.sig_dur.connect(lambda d: setattr(self, "duration", d))
        self.sig_params.connect(self.on_params)
        self.sig_tracks.connect(self.populate_audio_tracks)

        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_play.clicked.connect(self.toggle_pause)
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        self.seek.sliderMoved.connect(
            lambda v: self.duration and self.player.seek(
                v / 1000 * self.duration, "absolute"))
        self.vol.valueChanged.connect(
            lambda v: setattr(self.player, "volume", v))
        self.track_combo.currentIndexChanged.connect(self.select_audio_track)
        self.ratio_combo.currentIndexChanged.connect(self.apply_aspect_ratio)
        self.output_mode.currentIndexChanged.connect(self.apply_filters)
        self.mode.currentIndexChanged.connect(self.apply_filters)
        for w in (self.cw, self.dim):
            w.valueChanged.connect(self.apply_filters)
        self.delay.valueChanged.connect(self.apply_filters)
        self.pano.toggled.connect(self.apply_filters)
        self.sub.toggled.connect(self.apply_filters)

        if len(sys.argv) > 1:
            QTimer.singleShot(250, lambda: self.play(sys.argv[1]))
        self.update_controls()

    @staticmethod
    def _slider(lo, hi, val):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.setFixedWidth(85)
        s.setTickPosition(QSlider.TickPosition.TicksBelow)
        return s

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir une vidéo", "",
            "Vidéos (*.mkv *.mp4 *.avi *.mov *.ts *.m2ts *.wmv *.webm *.flv *.vob *.mpg *.mpeg);;Tous les fichiers (*.*)")
        if path:
            self.play(path)

    def play(self, path):
        self.current_path = path
        self.duration = 0.0
        self.src_channels = 0
        self.player.play(path)
        self.player.pause = False
        self.setWindowTitle(f"Lecteur Pro Logic II — {os.path.basename(path)}")
        self.track_combo.clear()
        self.track_combo.addItem("Piste audio : chargement…", "auto")
        QTimer.singleShot(700, self.refresh_tracks)
        QTimer.singleShot(1200, self.apply_filters)

    def refresh_tracks(self):
        try:
            self.populate_audio_tracks(self.player.track_list or [])
        except Exception:
            pass

    def populate_audio_tracks(self, tracks):
        current = self.track_combo.currentData()
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        self.track_combo.addItem("Piste audio : automatique", "auto")
        audio_tracks = [t for t in (tracks or [])
                        if isinstance(t, dict) and t.get("type") == "audio"]
        for t in audio_tracks:
            tid = t.get("id")
            if tid is None:
                continue
            lang = t.get("lang") or t.get("language") or "langue inconnue"
            title = t.get("title") or ""
            codec = t.get("codec") or ""
            channels = t.get("demux-channel-count") or t.get("audio-channels") or ""
            parts = [f"Piste {tid}", str(lang)]
            if title:
                parts.append(str(title))
            if codec:
                parts.append(str(codec).upper())
            if channels:
                parts.append(f"{channels} canaux")
            self.track_combo.addItem(" — ".join(parts), int(tid))
        idx = self.track_combo.findData(current)
        self.track_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.track_combo.blockSignals(False)
        self.lbl_info.setText(
            f"{len(audio_tracks)} piste(s) audio détectée(s). "
            "E-AC-3 / DTS : décodage assuré par FFmpeg/libmpv si les codecs sont inclus.")

    def select_audio_track(self):
        track_id = self.track_combo.currentData()
        try:
            self.player.command("set", "aid",
                                "auto" if track_id == "auto" else str(track_id))
            QTimer.singleShot(250, self.apply_filters)
        except Exception as e:
            self.lbl_info.setText(f"Impossible de sélectionner cette piste : {e}")

    def apply_aspect_ratio(self):
        ratio = self.ratio_combo.currentData() or "no"
        try:
            self.player.command("set", "video-aspect-override", ratio)
        except Exception as e:
            self.lbl_info.setText(f"Erreur aspect ratio : {e}")

    def toggle_pause(self):
        self.player.pause = not self.player.pause

    def on_time(self, t):
        if self.duration and not self.seek.isSliderDown():
            self.seek.setValue(max(0, min(1000, int(t / self.duration * 1000))))
        fmt = lambda x: f"{int(x) // 3600:02d}:{int(x) // 60 % 60:02d}:{int(x) % 60:02d}"
        self.lbl_time.setText(f"{fmt(t)} / {fmt(self.duration)}")

    def set_audio_codec(self, codec):
        self.audio_codec = str(codec or "inconnu").upper()
        self.refresh_audio_status()

    def on_params(self, params):
        if not isinstance(params, dict):
            return
        ch = params.get("channel-count", 0)
        self.audio_rate = int(params.get("samplerate", 0) or 0)
        self.audio_layout = str(params.get("channel-layout", "") or "")
        if ch != self.src_channels:
            self.src_channels = ch
            QTimer.singleShot(100, self.apply_filters)
        else:
            self.refresh_audio_status()

    def refresh_audio_status(self):
        if not self.current_path:
            return
        channels = self.audio_layout or (
            f"{self.src_channels} canaux" if self.src_channels else "analyse…")
        rate = f"{self.audio_rate / 1000:.1f} kHz" if self.audio_rate else "fréquence…"
        self.lbl_info.setText(
            f"Décodage : {self.audio_codec} | {channels} | {rate}. "
            "Traitement logiciel FFmpeg/libmpv actif.")

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
        self.sub.setEnabled(self.output_mode.currentData() != "stereo")

    def apply_filters(self):
        self.update_controls()
        output = self.output_mode.currentData()
        settings = self.settings()
        graph = ""

        try:
            if output == "stereo":
                # libmpv/FFmpeg effectue le downmix des sources 5.1/7.1 vers 2.0.
                self.player.audio_channels = "stereo"
                self.player.command("af", "clear")
                info = "Downmix actif : sortie stéréo 2.0."
            elif output == "5.1":
                self.player.audio_channels = "5.1"
                if self.src_channels and self.src_channels <= 2:
                    graph = build_af(settings)
                if graph:
                    self.player.command("af", "set", graph)
                    info = f"Upmix actif : {self.mode.currentText()} vers 5.1."
                elif self.src_channels > 2:
                    self.player.command("af", "clear")
                    info = f"Source multicanal ({self.src_channels} canaux) : décodage natif, sortie 5.1."
                else:
                    self.player.command("af", "clear")
                    info = "Sortie 5.1 demandée. En attente des informations audio de la source."
            else:
                self.player.audio_channels = "auto-safe"
                if self.src_channels == 0:
                    self.player.command("af", "clear")
                    info = "Analyse de la piste audio en cours…"
                elif self.src_channels > 2:
                    self.player.command("af", "clear")
                    info = f"Source multicanal ({self.src_channels} canaux) : lecture native."
                elif self.src_channels == 1:
                    self.player.command("af", "clear")
                    info = "Source mono : lecture native."
                else:
                    graph = build_af(settings)
                    if graph:
                        self.player.command("af", "set", graph)
                        info = f"Traitement stéréo : {self.mode.currentText()} vers 5.1."
                    else:
                        self.player.command("af", "clear")
                        info = "Lecture audio native."
            channels = self.audio_layout or f"{self.src_channels} canaux"
            rate = f"{self.audio_rate / 1000:.1f} kHz" if self.audio_rate else "fréquence inconnue"
            self.lbl_info.setText(
                f"{info} | Codec : {self.audio_codec} | {channels} | {rate}")
        except Exception as e:
            self.lbl_info.setText(f"Erreur de configuration audio : {e}")

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.toggle_pause()
        elif key == Qt.Key.Key_F:
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Right:
            self.player.seek(10)
        elif key == Qt.Key.Key_Left:
            self.player.seek(-10)
        elif key == Qt.Key.Key_Up:
            self.vol.setValue(min(130, self.vol.value() + 5))
        elif key == Qt.Key.Key_Down:
            self.vol.setValue(max(0, self.vol.value() - 5))
        else:
            super().keyPressEvent(event)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.panel.show()
            self.showNormal()
            self.btn_fullscreen.setText("Plein écran")
        else:
            self.panel.hide()
            self.showFullScreen()
            self.btn_fullscreen.setText("Quitter plein écran")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.play(urls[0].toLocalFile())

    def closeEvent(self, event):
        try:
            self.player.terminate()
        finally:
            super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    try:
        locale.setlocale(locale.LC_NUMERIC, "C")
    except locale.Error:
        pass
    window = Lecteur()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
