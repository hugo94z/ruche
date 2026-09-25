"""Fenêtre principale : connexion, membres et chat.

Le noyau (RoomManager) est purement asyncio et sans dépendance à Qt. Il
prévient l'interface via des événements ; la fenêtre les rediffuse à travers
un signal Qt pour rester sûre et lisible.
"""

from __future__ import annotations

import asyncio
import html
import os
import secrets
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..core.hosting import RendezvousHost
from ..core.media import VIDEO_PROFILES, list_audio_devices, list_cameras, list_monitors
from ..core.room import RoomManager
from ..i18n import t
from .call_window import CallWindow

_ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# Couleurs stables par pseudo (dérivées du nom).
_PALETTE = [
    "#2563eb", "#7c3aed", "#db2777", "#dc2626", "#ea580c",
    "#ca8a04", "#16a34a", "#0d9488", "#0891b2", "#4f46e5",
]


def _color_for(pseudo: str) -> str:
    if not pseudo:
        return _PALETTE[0]
    return _PALETTE[sum(ord(c) for c in pseudo) % len(_PALETTE)]


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            if unit == "o":
                return f"{int(value)} o"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} Go"


def _device_index(entry: str) -> int | None:
    try:
        return int(entry.split(":", 1)[0])
    except (ValueError, IndexError):
        return None


def _open_path(path) -> None:
    target = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except OSError:
        pass


class MainWindow(QMainWindow):
    core_event = Signal(str, object)

    def __init__(self, manager: RoomManager) -> None:
        super().__init__()
        self.manager = manager
        self._rendezvous_url = config.DEFAULT_RENDEZVOUS_URL
        self._call_window: CallWindow | None = None
        self.host = RendezvousHost()
        self._host_dialog: QDialog | None = None

        self.setWindowTitle(t("app.title"))
        self.resize(960, 640)
        self._build_ui()

        manager.add_listener(self._relay_event)
        self.core_event.connect(self._handle_event)

    # --- Construction de l'interface -------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)

        subtitle = QLabel(t("app.subtitle"))
        subtitle.setStyleSheet("color:#666;")
        layout.addWidget(subtitle)

        # Barre de connexion
        bar = QHBoxLayout()
        bar.addWidget(QLabel(t("connect.pseudo")))
        self.pseudo_input = QLineEdit(self.manager.identity.pseudo)
        self.pseudo_input.setPlaceholderText(t("connect.pseudo.placeholder"))
        self.pseudo_input.setMaxLength(32)
        self.pseudo_input.setFixedWidth(140)
        bar.addWidget(self.pseudo_input)

        bar.addWidget(QLabel(t("connect.room")))
        self.room_input = QLineEdit()
        self.room_input.setPlaceholderText(t("connect.room.placeholder"))
        self.room_input.setMaxLength(64)
        bar.addWidget(self.room_input, 1)

        self.new_room_button = QPushButton(t("connect.new_room"))
        self.new_room_button.clicked.connect(self._new_room)
        bar.addWidget(self.new_room_button)

        self.join_button = QPushButton(t("connect.join"))
        self.join_button.clicked.connect(self._toggle_join)
        bar.addWidget(self.join_button)

        self.call_button = QPushButton(t("call.start"))
        self.call_button.clicked.connect(self._start_call)
        self.call_button.setEnabled(False)
        bar.addWidget(self.call_button)

        self.devices_button = QPushButton(t("call.settings"))
        self.devices_button.clicked.connect(self._open_devices)
        bar.addWidget(self.devices_button)
        layout.addLayout(bar)

        # Barre du rendez-vous
        rv = QHBoxLayout()
        rv.addWidget(QLabel(t("connect.rendezvous")))
        self.rendezvous_input = QLineEdit(self._rendezvous_url)
        rv.addWidget(self.rendezvous_input, 1)
        self.host_button = QPushButton(t("host.button"))
        self.host_button.clicked.connect(self._open_host_dialog)
        rv.addWidget(self.host_button)
        layout.addLayout(rv)

        # Corps : membres | chat
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.members_label = QLabel(t("members.title", count=0))
        self.members_label.setFont(QFont("", 10, QFont.Bold))
        left_layout.addWidget(self.members_label)
        self.members_list = QListWidget()
        left_layout.addWidget(self.members_list)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self._on_anchor)
        right_layout.addWidget(self.transcript)

        input_row = QHBoxLayout()
        self.attach_button = QPushButton(t("chat.attach"))
        self.attach_button.clicked.connect(self._attach_file)
        self.attach_button.setEnabled(False)
        input_row.addWidget(self.attach_button)
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText(t("chat.placeholder"))
        self.message_input.returnPressed.connect(self._send_message)
        self.message_input.setEnabled(False)
        input_row.addWidget(self.message_input)
        self.send_button = QPushButton(t("chat.send"))
        self.send_button.clicked.connect(self._send_message)
        self.send_button.setEnabled(False)
        input_row.addWidget(self.send_button)
        right_layout.addLayout(input_row)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 740])
        layout.addWidget(splitter, 1)

        self.setCentralWidget(root)
        self.statusBar().showMessage(t("connect.status_idle"))

    # --- Actions ----------------------------------------------------------
    def _new_room(self) -> None:
        code = "".join(secrets.choice(_ROOM_ALPHABET) for _ in range(6))
        self.room_input.setText(code)

    def _toggle_join(self) -> None:
        if self.manager.room is None:
            self._join()
        else:
            asyncio.ensure_future(self._leave())

    def _join(self) -> None:
        pseudo = self.pseudo_input.text().strip()
        room = self.room_input.text().strip().upper()
        if not pseudo:
            self.statusBar().showMessage(t("connect.pseudo_required"))
            self.pseudo_input.setFocus()
            return
        if not room:
            self._new_room()
            room = self.room_input.text().strip().upper()
        self._rendezvous_url = self.rendezvous_input.text().strip()
        asyncio.ensure_future(
            self.manager.join(room, pseudo, self._rendezvous_url)
        )

    async def _leave(self) -> None:
        await self.manager.leave()

    def _send_message(self) -> None:
        text = self.message_input.text().strip()
        if not text:
            return
        self.message_input.clear()
        asyncio.ensure_future(self.manager.send_text(text))

    # --- Hébergement d'un rendez-vous -------------------------------------
    def _open_host_dialog(self) -> None:
        asyncio.ensure_future(self._start_host_and_show())

    async def _start_host_and_show(self) -> None:
        if not self.host.hosting:
            try:
                port = await self.host.start()
            except Exception as exc:  # port occupé, permissions…
                QMessageBox.warning(self, t("host.title"), t("host.failed", error=exc))
                return
            self.statusBar().showMessage(t("host.started", port=port))
            # L'hôte se connecte à son propre rendez-vous via la boucle locale.
            self.rendezvous_input.setText(self.host.loopback_url())
            asyncio.ensure_future(self._refresh_host_addresses())
        self._show_host_dialog()

    async def _refresh_host_addresses(self) -> None:
        await self.host.refresh_public_ip()
        if self._host_dialog is not None:
            self._show_host_dialog()  # rafraîchit la liste des adresses

    def _copy_address(self, address: str) -> None:
        QGuiApplication.clipboard().setText(address)
        self.statusBar().showMessage(t("host.copied"))

    def _show_host_dialog(self) -> None:
        if self._host_dialog is not None:
            self._host_dialog.close()
            self._host_dialog = None

        dialog = QDialog(self)
        dialog.setWindowTitle(t("host.title"))
        dialog.resize(600, 290)
        layout = QVBoxLayout(dialog)

        intro = QLabel(t("host.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(QLabel(t("host.address")))
        row = QHBoxLayout()
        addresses = QComboBox()
        addresses.addItems(self.host.share_urls() or ["—"])
        row.addWidget(addresses, 1)
        copy_button = QPushButton(t("host.copy"))
        copy_button.clicked.connect(lambda: self._copy_address(addresses.currentText()))
        row.addWidget(copy_button)
        layout.addLayout(row)

        public = self.host.public_url()
        if public:
            public_label = QLabel(t("host.public_ok", url=public, port=self.host.port))
        else:
            public_label = QLabel(t("host.public_unknown"))
        public_label.setWordWrap(True)
        layout.addWidget(public_label)

        warning = QLabel(t("host.warning"))
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#b45309;")
        layout.addWidget(warning)

        buttons = QDialogButtonBox()
        use_button = buttons.addButton(t("host.use"), QDialogButtonBox.AcceptRole)
        stop_button = buttons.addButton(t("host.stop"), QDialogButtonBox.DestructiveRole)
        buttons.addButton(t("host.close"), QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

        use_button.clicked.connect(
            lambda: self._use_host_address(dialog, addresses.currentText())
        )
        stop_button.clicked.connect(lambda: asyncio.ensure_future(self._stop_host(dialog)))
        dialog.finished.connect(lambda _result: setattr(self, "_host_dialog", None))

        self._host_dialog = dialog
        dialog.open()

    def _use_host_address(self, dialog: QDialog, address: str) -> None:
        if address and address != "—":
            self.rendezvous_input.setText(address)
        dialog.accept()

    async def _stop_host(self, dialog: QDialog) -> None:
        await self.host.stop()
        self.statusBar().showMessage(t("host.stopped"))
        dialog.accept()

    def _attach_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t("chat.attach"), "", t("chat.attach_filter")
        )
        if path:
            asyncio.ensure_future(self.manager.send_file(path))

    def _on_anchor(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith("ruche-file:"):
            asyncio.ensure_future(self.manager.request_file(text.split(":", 1)[1]))
        elif text.startswith("ruche-open:"):
            path = self.manager.files.path(text.split(":", 1)[1])
            if path is not None:
                _open_path(path)

    def _start_call(self) -> None:
        asyncio.ensure_future(self.manager.start_call())

    def _open_devices(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(t("call.settings"))
        layout = QVBoxLayout(dialog)

        cam_box = QComboBox()
        cam_box.addItem(t("call.no_camera"))
        cam_box.addItems(list_cameras())

        mic_box = QComboBox()
        mic_box.addItem(t("call.system_default"), None)
        speaker_box = QComboBox()
        speaker_box.addItem(t("call.system_default"), None)
        inputs, outputs = list_audio_devices()
        for entry in inputs:
            mic_box.addItem(entry, _device_index(entry))
        for entry in outputs:
            speaker_box.addItem(entry, _device_index(entry))

        quality_box = QComboBox()
        for name, (width, height, fps) in VIDEO_PROFILES.items():
            quality_box.addItem(f"{name} — {width}×{height} · {fps} ips", name)

        screen_fps_box = QComboBox()
        for value in (10, 15, 24, 30):
            screen_fps_box.addItem(f"{value} ips", value)

        monitor_box = QComboBox()
        monitors = list_monitors()
        for index, label in enumerate(monitors, start=1):
            monitor_box.addItem(label, index)

        for label, box in (
            (t("call.camera"), cam_box),
            (t("call.microphone"), mic_box),
            (t("call.speaker"), speaker_box),
            (t("call.quality"), quality_box),
            (t("call.monitor"), monitor_box),
            (t("call.screen_fps"), screen_fps_box),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(box, 1)
            layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.Accepted:
            camera = cam_box.currentText()
            camera = None if camera == t("call.no_camera") else camera
            self.manager.set_media_devices(
                camera,
                mic_box.currentData(),
                speaker_box.currentData(),
                quality_box.currentData(),
                screen_fps_box.currentData(),
                monitor_box.currentData(),
            )

    # --- Appels -----------------------------------------------------------
    def _ensure_call_window(self) -> CallWindow:
        if self._call_window is None:
            window = CallWindow(self.manager, self)
            window.hangup.connect(
                lambda: asyncio.ensure_future(self.manager.end_call())
            )
            window.screen_toggle.connect(self._toggle_screen_share)
            window.finished.connect(self._on_call_closed)
            self._call_window = window
        return self._call_window

    def _on_call_started(self, _payload: object) -> None:
        window = self._ensure_call_window()
        window.show()
        window.raise_()

    def _on_call_local_video(self, payload: object) -> None:
        data = payload or {}
        track = data.get("track")
        if track is None:
            return
        window = self._ensure_call_window()
        window.show()
        window.add_local_video(track, data.get("pseudo", self.manager.identity.pseudo))

    def _on_call_track(self, payload: object) -> None:
        data = payload or {}
        track = data.get("track")
        peer_id = data.get("peer_id")
        if track is None or peer_id is None:
            return
        window = self._ensure_call_window()
        window.show()
        if data.get("kind") == "video":
            window.add_remote_video(peer_id, data.get("pseudo", peer_id[:8]), track)
        else:
            window.add_remote_audio(peer_id, data.get("pseudo", peer_id[:8]))

    def _on_call_peer_left(self, payload: object) -> None:
        if self._call_window is not None:
            self._call_window.remove_peer((payload or {}).get("peer_id", ""))

    def _toggle_screen_share(self) -> None:
        if self.manager.is_screen_sharing():
            asyncio.ensure_future(self.manager.stop_screen_share())
        else:
            asyncio.ensure_future(self.manager.start_screen_share())

    def _on_screen_shared(self, payload: object) -> None:
        data = payload or {}
        window = self._ensure_call_window()
        window.show()
        track = data.get("track")
        if track is not None:
            window.add_local_video(track, data.get("pseudo", self.manager.identity.pseudo))
        window.set_screen_state(True)

    def _on_screen_stopped(self, _payload: object) -> None:
        if self._call_window is not None:
            self._call_window.set_screen_state(False)

    def _on_call_ended(self, _payload: object) -> None:
        if self._call_window is not None:
            self._call_window.clear_all()
            self._call_window.close()
            self._call_window = None

    def _on_call_closed(self, _result: int) -> None:
        self._call_window = None
        if self.manager.call_active:
            asyncio.ensure_future(self.manager.end_call())

    def _on_call_invite(self, payload: object) -> None:
        data = payload or {}
        answer = QMessageBox.question(
            self,
            t("call.title"),
            t("call.incoming", pseudo=data.get("pseudo", "")),
        )
        if answer == QMessageBox.Yes:
            asyncio.ensure_future(self.manager.join_call())

    # --- Réception des événements du noyau -------------------------------
    def _relay_event(self, event: str, payload: object) -> None:
        self.core_event.emit(event, payload)

    def _handle_event(self, event: str, payload: object) -> None:
        handler = {
            "status": self._on_status,
            "joined": self._on_joined,
            "left": self._on_left,
            "members": self._on_members,
            "history": self._on_history,
            "message": self._on_message,
            "host": self._on_host,
            "file-start": self._on_file_progress,
            "file-progress": self._on_file_progress,
            "file-ready": self._on_file_ready,
            "call-started": self._on_call_started,
            "call-ended": self._on_call_ended,
            "call-local-video": self._on_call_local_video,
            "call-track": self._on_call_track,
            "call-invite": self._on_call_invite,
            "call-peer-left": self._on_call_peer_left,
            "screen-shared": self._on_screen_shared,
            "screen-stopped": self._on_screen_stopped,
        }.get(event)
        if handler is not None:
            handler(payload)

    def _on_status(self, payload: object) -> None:
        self.statusBar().showMessage(str(payload))

    def _on_joined(self, payload: object) -> None:
        room = str(payload)
        self.setWindowTitle(t("app.title") + f" — {room}")
        self.pseudo_input.setEnabled(False)
        self.room_input.setEnabled(False)
        self.rendezvous_input.setEnabled(False)
        self.new_room_button.setEnabled(False)
        self.join_button.setText(t("connect.leave"))
        self.message_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.attach_button.setEnabled(True)
        self.call_button.setEnabled(True)
        self.message_input.setFocus()
        self.statusBar().showMessage(t("connect.status_online", room=room))

    def _on_left(self, _payload: object) -> None:
        self.setWindowTitle(t("app.title"))
        self.pseudo_input.setEnabled(True)
        self.room_input.setEnabled(True)
        self.rendezvous_input.setEnabled(True)
        self.new_room_button.setEnabled(True)
        self.join_button.setText(t("connect.join"))
        self.message_input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.attach_button.setEnabled(False)
        self.call_button.setEnabled(False)
        self.members_list.clear()
        self.members_label.setText(t("members.title", count=0))
        self.statusBar().showMessage(t("connect.status_idle"))

    def _on_members(self, payload: object) -> None:
        members = payload or []
        self.members_list.clear()
        for member in members:  # type: ignore[union-attr]
            badges = []
            if member.get("is_host"):
                badges.append(t("members.host"))
            if member.get("is_self"):
                badges.append(t("members.you"))
            suffix = f"  ({', '.join(badges)})" if badges else ""
            item = QListWidgetItem(f"{member['pseudo']}{suffix}")
            item.setForeground(Qt.GlobalColor.black)
            self.members_list.addItem(item)
        self.members_label.setText(t("members.title", count=len(members)))

    def _on_history(self, payload: object) -> None:
        self._rerender()

    def _on_message(self, payload: object) -> None:
        self._append_entry(payload)  # type: ignore[arg-type]

    def _on_host(self, payload: object) -> None:
        if payload:
            self.setWindowTitle(t("app.title") + f" — {self.manager.room}")

    def _on_file_progress(self, payload: object) -> None:
        data = payload or {}
        total = int(data.get("total", 0) or 0)
        received = int(data.get("received", 0) or 0)
        name = data.get("name")
        if total > 0:
            percent = int(received * 100 / total)
            label = name or data.get("file_id", "")
            self.statusBar().showMessage(f"Transfert de {label} : {percent} %")
        elif name:
            self.statusBar().showMessage(f"Transfert de {name}…")

    def _on_file_ready(self, payload: object) -> None:
        # Un fichier vient d'arriver (par ex. une image) : on redessine pour
        # l'afficher en ligne le cas échéant.
        self._rerender()

    def _rerender(self) -> None:
        self.transcript.clear()
        entries = self.manager.history.all_sorted()
        if not entries:
            self.transcript.setHtml(
                f"<p style='color:#888'>{html.escape(t('chat.empty'))}</p>"
            )
            return
        for entry in entries:
            self._append_entry(entry)

    def _render_file(self, entry: dict) -> str:
        extra = entry.get("extra") or {}
        file_id = str(extra.get("file_id") or "")
        name = html.escape(str(extra.get("name") or entry.get("body") or "fichier"))
        size = int(extra.get("size", 0) or 0)
        mime = str(extra.get("mime", ""))
        human = _human_size(size)

        path = self.manager.files.path(file_id) if file_id else None
        if path is not None:
            if mime.startswith("image/"):
                url = QUrl.fromLocalFile(str(path)).toString()
                return (
                    f"<img src='{url}' width='240'><br>"
                    f"<a href='ruche-open:{file_id}'>{name}</a> "
                    f"<span style='color:#888'>({human})</span>"
                )
            return (
                f"<a href='ruche-open:{file_id}'>📄 {name}</a> "
                f"<span style='color:#888'>({human})</span>"
            )
        return (
            f"<a href='ruche-file:{file_id}'>⬇ {name}</a> "
            f"<span style='color:#888'>({human} — cliquer pour télécharger)</span>"
        )

    def _append_entry(self, entry: dict) -> None:
        pseudo = html.escape(str(entry.get("pseudo", "")))
        when = entry.get("at") or 0
        try:
            stamp = datetime.fromtimestamp(float(when)).strftime("%H:%M")
        except (ValueError, OSError, TypeError):
            stamp = ""
        is_self = entry.get("origin") == self.manager.identity.peer_id
        color = _color_for(str(entry.get("pseudo", "")))

        who = f"<b style='color:{color}'>{pseudo}</b>"
        if is_self:
            who += " <span style='color:#888'>(vous)</span>"

        if entry.get("kind") == "file":
            body = self._render_file(entry)
        else:
            body = html.escape(str(entry.get("body", ""))).replace("\n", "<br>")

        block = (
            f"<div style='margin:4px 0'>"
            f"{who} <span style='color:#999;font-size:9pt'>{stamp}</span><br>"
            f"<span>{body}</span></div>"
        )
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.transcript.setTextCursor(cursor)
        self.transcript.insertHtml(block)
        self.transcript.verticalScrollBar().setValue(
            self.transcript.verticalScrollBar().maximum()
        )

    # --- Fermeture --------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.manager.room is not None:
            asyncio.ensure_future(self.manager.leave())
        if self.host.hosting:
            asyncio.ensure_future(self.host.stop())
        event.accept()
