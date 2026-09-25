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
from PySide6.QtGui import QCloseEvent, QColor, QCursor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import config
from ..core import autostart
from ..core.hosting import RendezvousHost
from ..core.media import VIDEO_PROFILES, list_audio_devices, list_cameras, list_monitors
from ..core.room import RoomManager
from ..i18n import t
from .call_window import CallWindow
from .theme import apply as apply_theme

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
        self._build_tray()

        manager.add_listener(self._relay_event)
        self.core_event.connect(self._handle_event)

    # --- Écran d'accueil (premier lancement) ------------------------------
    def maybe_show_welcome(self) -> None:
        settings = config.load_settings()
        if settings.get("welcome_done"):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(t("welcome.title"))
        dialog.resize(600, 420)
        layout = QVBoxLayout(dialog)

        heading = QLabel(t("welcome.heading"))
        heading.setFont(QFont("", 14, QFont.Bold))
        heading.setWordWrap(True)
        layout.addWidget(heading)

        body = QLabel(t("welcome.body"))
        body.setWordWrap(True)
        layout.addWidget(body)

        steps = QLabel(t("welcome.steps"))
        steps.setWordWrap(True)
        layout.addWidget(steps)

        note = QLabel(t("welcome.note"))
        note.setWordWrap(True)
        note.setStyleSheet("color:#b45309;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)

        dialog.exec()
        settings["welcome_done"] = True
        config.save_settings(settings)

    # --- Zone de notification --------------------------------------------
    def _tray_icon(self) -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#2563eb"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 60, 60)
        painter.setPen(QColor("white"))
        font = QFont()
        font.setPointSize(30)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "R")
        painter.end()
        return QIcon(pixmap)

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self._tray_icon(), self)
        tray.setToolTip(config.APP_NAME)
        menu = QMenu()
        menu.addAction(t("tray.open"), self._restore_window)
        menu.addAction(t("tray.quit"), self._quit_application)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._restore_window()
            if reason == QSystemTrayIcon.DoubleClick
            else None
        )
        tray.show()
        self.tray = tray

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _notify(self, title: str, body: str) -> None:
        if self.tray is not None:
            self.tray.showMessage(title, body, self._tray_icon(), 6000)

    def _quit_application(self) -> None:
        asyncio.ensure_future(self._shutdown())

    async def _shutdown(self) -> None:
        if self.manager.rooms:
            await self.manager.close()
        if self.host.hosting:
            await self.host.stop()
        if self.tray is not None:
            self.tray.hide()
        app = QGuiApplication.instance()
        if app is not None:
            app.quit()

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
        self.join_button.clicked.connect(self._join)
        bar.addWidget(self.join_button)

        self.leave_button = QPushButton(t("connect.leave"))
        self.leave_button.clicked.connect(self._leave_active)
        self.leave_button.setEnabled(False)
        bar.addWidget(self.leave_button)

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

        rooms_label = QLabel(t("rooms.title"))
        rooms_label.setFont(QFont("", 10, QFont.Bold))
        left_layout.addWidget(rooms_label)
        self.rooms_list = QListWidget()
        self.rooms_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.rooms_list.customContextMenuRequested.connect(self._room_menu)
        self.rooms_list.itemClicked.connect(self._on_room_clicked)
        self._room_rows: list[dict] = []
        self.rooms_list.setMaximumHeight(180)
        left_layout.addWidget(self.rooms_list)

        self.members_label = QLabel(t("members.title", count=0))
        self.members_label.setFont(QFont("", 10, QFont.Bold))
        left_layout.addWidget(self.members_label)
        self.members_list = QListWidget()
        self.members_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.members_list.customContextMenuRequested.connect(self._member_menu)
        self._member_rows: list[dict] = []
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
        asyncio.ensure_future(self.manager.join(room, pseudo, self._rendezvous_url))

    def _leave_active(self) -> None:
        asyncio.ensure_future(self._leave())

    async def _leave(self) -> None:
        await self.manager.leave()

    def _on_room_clicked(self, item: QListWidgetItem) -> None:
        code = item.data(Qt.UserRole)
        if code:
            asyncio.ensure_future(self.manager.switch_room(code))

    def _room_menu(self, pos) -> None:
        item = self.rooms_list.itemAt(pos)
        if item is None:
            return
        code = item.data(Qt.UserRole)
        row = next((r for r in self._room_rows if r.get("code") == code), {})
        menu = QMenu(self)
        label = t("rooms.close_dm") if row.get("dm") else t("rooms.leave")
        menu.addAction(label, lambda: asyncio.ensure_future(self.manager.leave_room(code)))
        menu.exec(self.rooms_list.mapToGlobal(pos))

    def _on_rooms(self, payload: object) -> None:
        self._room_rows = list(payload or [])  # type: ignore[arg-type]
        self.rooms_list.clear()
        for row in self._room_rows:
            if row.get("dm"):
                text = f"👤 {row.get('title', '?')}"
            else:
                text = f"# {row.get('code', '')}"
            if row.get("unread"):
                text += f"  • {row['unread']}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, row.get("code"))
            if row.get("active"):
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            if row.get("call"):
                item.setText(text + "  📞")
            self.rooms_list.addItem(item)

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
        elif text.startswith("ruche-msg:"):
            self._message_menu(text.split(":", 1)[1])

    def _message_menu(self, message_id: str) -> None:
        view = next(
            (e for e in self.manager.history.all_views() if e.get("id") == message_id),
            None,
        )
        if view is None:
            return
        is_mine = view.get("origin") == self.manager.identity.peer_id
        menu = QMenu(self)
        if is_mine and view.get("kind") == "text":
            menu.addAction(t("chat.edit"), lambda: self._edit_message(view))
        if is_mine:
            menu.addAction(t("chat.delete"), lambda: self._delete_message(message_id))
        if is_mine:
            menu.addSeparator()
        reactions = menu.addMenu(t("chat.react"))
        for emoji in ("👍", "❤️", "😂", "✅"):
            reactions.addAction(
                emoji,
                lambda e=emoji: asyncio.ensure_future(
                    self.manager.toggle_reaction(message_id, e)
                ),
            )
        menu.exec(QCursor.pos())

    def _edit_message(self, view: dict) -> None:
        text, ok = QInputDialog.getText(
            self, t("chat.edit_title"), t("chat.edit"), QLineEdit.Normal, str(view.get("body", ""))
        )
        if ok and text.strip():
            asyncio.ensure_future(self.manager.edit_message(view["id"], text))

    def _delete_message(self, message_id: str) -> None:
        answer = QMessageBox.question(self, t("chat.delete"), t("chat.delete_confirm"))
        if answer == QMessageBox.Yes:
            asyncio.ensure_future(self.manager.delete_message(message_id))

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

        autostart_box = QCheckBox(t("settings.autostart"))
        autostart_box.setChecked(autostart.is_enabled())
        hint = QLabel(t("settings.autostart_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666;")
        layout.addWidget(autostart_box)
        layout.addWidget(hint)

        # Thème
        theme_box = QComboBox()
        theme_box.addItem("Clair", "clair")
        theme_box.addItem("Sombre", "sombre")
        settings = config.load_settings()
        theme_box.setCurrentIndex(1 if settings.get("theme") == "sombre" else 0)
        row_theme = QHBoxLayout()
        row_theme.addWidget(QLabel(t("settings.theme")))
        row_theme.addWidget(theme_box, 1)
        layout.addLayout(row_theme)

        # Relais TURN
        turn_url = QLineEdit()
        turn_user = QLineEdit()
        turn_pass = QLineEdit()
        turn_pass.setEchoMode(QLineEdit.Password)
        for server in config.ice_servers():
            urls = server.get("urls") or ""
            if isinstance(urls, str) and urls.startswith("turn"):
                turn_url.setText(urls)
                turn_user.setText(server.get("username", ""))
                turn_pass.setText(server.get("credential", ""))
        turn_hint = QLabel(t("settings.turn_hint"))
        turn_hint.setWordWrap(True)
        turn_hint.setStyleSheet("color:#666;")
        layout.addWidget(QLabel(t("settings.turn")))
        for label, widget in (
            (t("settings.turn_url"), turn_url),
            (t("settings.turn_user"), turn_user),
            (t("settings.turn_pass"), turn_pass),
        ):
            row_turn = QHBoxLayout()
            row_turn.addWidget(QLabel(label))
            row_turn.addWidget(widget, 1)
            layout.addLayout(row_turn)
        layout.addWidget(turn_hint)

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
            autostart.set_enabled(autostart_box.isChecked())

            # Enregistrer les serveurs ICE (STUN par défaut + TURN si rempli).
            servers = [{"urls": url} for url in config.STUN_SERVERS]
            url = turn_url.text().strip()
            if url:
                entry = {"urls": url}
                if turn_user.text().strip():
                    entry["username"] = turn_user.text().strip()
                if turn_pass.text():
                    entry["credential"] = turn_pass.text()
                servers.append(entry)
            config.write_ice_servers(servers)

            # Thème appliqué immédiatement.
            chosen = theme_box.currentData()
            settings = config.load_settings()
            settings["theme"] = chosen
            config.save_settings(settings)
            app = QGuiApplication.instance()
            if app is not None:
                apply_theme(app, chosen)

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
            "rooms": self._on_rooms,
            "room-activity": self._on_room_activity,
            "members": self._on_members,
            "history": self._on_history,
            "message": self._on_message,
            "host": self._on_host,
            "security": self._on_security,
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

    def _session_title(self, room: str) -> str:
        session = self.manager.rooms.get(room)
        if session is not None and session.is_dm:
            pseudo = session.dm_pseudo or (session.peer_id or "")[:8]
            return t("dm.title", pseudo=pseudo)
        return room

    def _on_joined(self, payload: object) -> None:
        room = str(payload)
        self.setWindowTitle(t("app.title") + f" — {self._session_title(room)}")
        self.pseudo_input.setEnabled(False)
        self.room_input.setEnabled(True)
        self.rendezvous_input.setEnabled(False)
        self.new_room_button.setEnabled(True)
        self.leave_button.setEnabled(True)
        self.message_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.attach_button.setEnabled(True)
        self.call_button.setEnabled(True)
        self.message_input.setFocus()
        self.statusBar().showMessage(t("connect.status_online", room=self._session_title(room)))

    def _on_left(self, _payload: object) -> None:
        if self.manager.active_code is None:
            self.setWindowTitle(t("app.title"))
            self.pseudo_input.setEnabled(True)
            self.room_input.setEnabled(True)
            self.rendezvous_input.setEnabled(True)
            self.new_room_button.setEnabled(True)
            self.leave_button.setEnabled(False)
            self.message_input.setEnabled(False)
            self.send_button.setEnabled(False)
            self.attach_button.setEnabled(False)
            self.call_button.setEnabled(False)
            self.members_list.clear()
            self.members_label.setText(t("members.title", count=0))
            self.transcript.clear()
            self.statusBar().showMessage(t("connect.status_idle"))

    def _on_room_activity(self, payload: object) -> None:
        data = payload or {}
        room = data.get("room", "")
        entry = data.get("entry") or {}
        if self.isActiveWindow():
            return
        session = self.manager.rooms.get(room)
        title = self._session_title(room)
        if isinstance(entry, dict) and entry.get("kind") == "call-invite":
            self._notify(t("call.title"), t("call.incoming", pseudo=entry.get("pseudo", "")))
            return
        if isinstance(entry, dict):
            pseudo = entry.get("pseudo", "")
            body = str(entry.get("body", ""))
            if session is not None and not session.is_dm:
                self._notify(t("tray.new_message_room", pseudo=pseudo, room=title), body[:180])
            else:
                self._notify(t("tray.new_message", pseudo=pseudo), body[:180])

    def _on_members(self, payload: object) -> None:
        members = payload or []
        self._member_rows = list(members)  # type: ignore[arg-type]
        self.members_list.clear()
        for member in members:  # type: ignore[union-attr]
            badges = []
            if member.get("is_host"):
                badges.append(t("members.host"))
            if member.get("is_self"):
                badges.append(t("members.you"))
            if member.get("verified"):
                badges.append("🔒 " + t("members.verified"))
            if member.get("blocked"):
                badges.append(t("members.blocked"))
            if member.get("muted"):
                badges.append(t("members.muted"))
            suffix = f"  ({', '.join(badges)})" if badges else ""
            item = QListWidgetItem(f"{member['pseudo']}{suffix}")
            item.setForeground(Qt.GlobalColor.black)
            self.members_list.addItem(item)
        self.members_label.setText(t("members.title", count=len(members)))

    def _member_menu(self, pos) -> None:
        index = self.members_list.indexAt(pos)
        if not index.isValid() or index.row() >= len(self._member_rows):
            return
        row = self._member_rows[index.row()]
        if row.get("is_self"):
            return
        peer_id = row["id"]
        menu = QMenu(self)
        menu.addAction(
            t("peer.dm"),
            lambda: asyncio.ensure_future(
                self.manager.start_dm(peer_id, row.get("pseudo", ""))
            ),
        )
        menu.addAction(t("peer.show_fingerprint"), lambda: self._show_fingerprint(row))
        menu.addAction(
            t("peer.unverify") if row.get("verified") else t("peer.verify"),
            lambda: self.manager.set_peer_verified(peer_id, not row.get("verified")),
        )
        menu.addAction(
            t("peer.unmute") if row.get("muted") else t("peer.mute"),
            lambda: self.manager.set_peer_muted(peer_id, not row.get("muted")),
        )
        menu.addAction(
            t("peer.unblock") if row.get("blocked") else t("peer.block"),
            lambda: self.manager.set_peer_blocked(peer_id, not row.get("blocked")),
        )
        menu.exec(self.members_list.mapToGlobal(pos))

    def _show_fingerprint(self, row: dict) -> None:
        fingerprint = row.get("fingerprint") or self.manager.fingerprint_of(row["id"]) or "—"
        QMessageBox.information(
            self,
            t("peer.fingerprint_title", pseudo=row["pseudo"]),
            t("peer.fingerprint_body", pseudo=row["pseudo"], fingerprint=fingerprint),
        )

    def _on_security(self, payload: object) -> None:
        data = payload or {}
        if data.get("kind") == "cle-changee":
            QMessageBox.critical(
                self,
                t("app.title"),
                t("security.key_changed", pseudo=data.get("pseudo", "")),
            )

    def _on_history(self, payload: object) -> None:
        self._rerender()

    def _on_message(self, payload: object) -> None:
        entry = payload or {}
        if entry.get("_op"):  # édition / suppression / réaction : on redessine
            self._rerender()
            return
        self._append_entry(entry)  # type: ignore[arg-type]
        if (
            entry.get("origin") != self.manager.identity.peer_id  # type: ignore[union-attr]
            and not self.isActiveWindow()
        ):
            self._notify(
                t("tray.new_message", pseudo=entry.get("pseudo", "")),  # type: ignore[union-attr]
                str(entry.get("body", ""))[:180],  # type: ignore[union-attr]
            )

    def _on_host(self, payload: object) -> None:
        if payload and self.manager.room:
            self.setWindowTitle(
                t("app.title") + f" — {self._session_title(self.manager.room)}"
            )

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
        entries = self.manager.history.all_views()
        if not entries:
            session = self.manager.active
            if session is not None and session.is_dm:
                pseudo = session.dm_pseudo or (session.peer_id or "")[:8]
                self.transcript.setHtml(
                    f"<p style='color:#888'>{html.escape(t('dm.empty', pseudo=pseudo))}</p>"
                )
                return
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
                thumb = self.manager.files.thumbnail(file_id)
                preview = thumb if thumb is not None else path
                url = QUrl.fromLocalFile(str(preview)).toString()
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

        edited = (
            f" <span style='color:#999;font-size:8pt'>{html.escape(t('chat.edited'))}</span>"
            if entry.get("edited")
            else ""
        )
        entry_id = str(entry.get("id", ""))
        actions = (
            f" <a href='ruche-msg:{entry_id}' style='color:#999;text-decoration:none'>⋯</a>"
            if entry_id
            else ""
        )
        reactions = ""
        chips = []
        for emoji, names in (entry.get("reactions") or {}).items():
            if names:
                chips.append(f"{html.escape(emoji)} {len(names)}")
        if chips:
            reactions = (
                "<br><span style='color:#555;font-size:9pt'>"
                + "&nbsp;&nbsp;".join(chips)
                + "</span>"
            )

        block = (
            f"<div style='margin:4px 0'>"
            f"{who} <span style='color:#999;font-size:9pt'>{stamp}</span>{edited}{actions}<br>"
            f"<span>{body}</span>{reactions}</div>"
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
        # Avec une icône de zone de notification, la croix réduit Ruche au lieu
        # de quitter : c'est ce qui permet de continuer à recevoir les messages.
        if self.tray is not None:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                config.APP_NAME, t("tray.still_running"), self._tray_icon(), 4000
            )
            return
        if self.manager.rooms:
            asyncio.ensure_future(self.manager.close())
        if self.host.hosting:
            asyncio.ensure_future(self.host.stop())
        event.accept()
