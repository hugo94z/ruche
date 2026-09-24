"""Fenêtre d'appel : mosaïque vidéo + commandes micro/caméra/raccrocher."""

from __future__ import annotations

import asyncio
import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.media import frame_to_rgb
from ..i18n import t


class CallWindow(QDialog):
    hangup = Signal()
    screen_toggle = Signal()

    def __init__(self, manager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle(t("call.title"))
        self.resize(840, 580)

        self._tiles: dict[str, QLabel] = {}
        self._tasks: dict[str, asyncio.Task] = {}

        root = QVBoxLayout(self)
        self._grid_holder = QWidget()
        self._grid = QGridLayout(self._grid_holder)
        self._grid.setSpacing(6)
        root.addWidget(self._grid_holder, 1)

        controls = QHBoxLayout()
        self.mic_button = QPushButton(t("call.mic_off"))
        self.mic_button.clicked.connect(self._toggle_mic)
        self.cam_button = QPushButton(t("call.cam_off"))
        self.cam_button.clicked.connect(self._toggle_cam)
        self.screen_button = QPushButton(t("call.screen_share"))
        self.screen_button.clicked.connect(self.screen_toggle.emit)
        self.hang_button = QPushButton(t("call.hangup"))
        self.hang_button.clicked.connect(self.hangup.emit)
        controls.addStretch(1)
        controls.addWidget(self.mic_button)
        controls.addWidget(self.cam_button)
        controls.addWidget(self.screen_button)
        controls.addWidget(self.hang_button)
        controls.addStretch(1)
        root.addLayout(controls)

        self._mic_on = True
        self._cam_on = True

    # --- Tuiles -----------------------------------------------------------
    def _add_tile(self, key: str, title: str) -> QLabel:
        existing = self._tiles.get(key)
        if existing is not None:
            return existing
        label = QLabel(title)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumSize(240, 180)
        label.setStyleSheet(
            "border:1px solid #444;background:#101010;color:#dddddd;font-size:11pt;"
        )
        self._tiles[key] = label
        self._relayout()
        return label

    def add_local_video(self, track, pseudo: str) -> None:
        label = self._add_tile("self", t("call.you", pseudo=pseudo))
        self._start_render("self", label, track)

    def add_remote_video(self, peer_id: str, pseudo: str, track) -> None:
        label = self._add_tile(peer_id, pseudo)
        self._start_render(peer_id, label, track)

    def add_remote_audio(self, peer_id: str, pseudo: str) -> None:
        self._add_tile(peer_id, f"🎤 {pseudo}")

    def remove_peer(self, peer_id: str) -> None:
        task = self._tasks.pop(peer_id, None)
        if task is not None:
            task.cancel()
        label = self._tiles.pop(peer_id, None)
        if label is not None:
            label.setParent(None)
            label.deleteLater()
        self._relayout()

    def clear_all(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        for label in self._tiles.values():
            label.setParent(None)
            label.deleteLater()
        self._tiles.clear()
        self._relayout()

    def _relayout(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self._grid_holder)
        keys = list(self._tiles)
        if not keys:
            return
        columns = max(1, math.ceil(math.sqrt(len(keys))))
        for index, key in enumerate(keys):
            self._grid.addWidget(self._tiles[key], index // columns, index % columns)

    # --- Rendu vidéo ------------------------------------------------------
    def _start_render(self, key: str, label: QLabel, track) -> None:
        old = self._tasks.pop(key, None)
        if old is not None:
            old.cancel()

        async def render() -> None:
            try:
                while True:
                    frame = await track.recv()
                    rgb = frame_to_rgb(frame)
                    height, width, _ = rgb.shape
                    image = QImage(
                        rgb.tobytes(), width, height, 3 * width, QImage.Format_RGB888
                    )
                    pixmap = QPixmap.fromImage(image).scaled(
                        label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                    label.setPixmap(pixmap)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        self._tasks[key] = asyncio.ensure_future(render())

    # --- Commandes --------------------------------------------------------
    def _toggle_mic(self) -> None:
        self._mic_on = not self._mic_on
        self.manager.set_microphone_enabled(self._mic_on)
        self.mic_button.setText(t("call.mic_on") if self._mic_on else t("call.mic_off"))

    def _toggle_cam(self) -> None:
        self._cam_on = not self._cam_on
        self.manager.set_camera_enabled(self._cam_on)
        self.cam_button.setText(t("call.cam_on") if self._cam_on else t("call.cam_off"))

    def set_screen_state(self, sharing: bool) -> None:
        self.screen_button.setText(
            t("call.screen_stop") if sharing else t("call.screen_share")
        )

    def closeEvent(self, event) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        super().closeEvent(event)
