"""Gestion d'un salon : appartenance, élection d'hôte, chat et réplication.

Aucun serveur central ne « fait tourner » le salon. Les pairs forment un
maillage et l'un d'eux joue le rôle d'hôte (coordinateur). La règle d'élection
est déterministe — le membre connecté dont l'identifiant est le plus petit —
si bien que tous les pairs désignent le même hôte sans se concerter. Si l'hôte
se déconnecte, le suivant dans l'ordre prend le relais automatiquement.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from pathlib import Path
from typing import Callable

from .. import config
from . import crypto
from .files import CHUNK_SIZE, FileStore
from .history import HistoryLog
from .identity import Identity, save as save_identity
from .media import DEFAULT_PROFILE, LocalMedia, SpeakerSink
from .network.lan import LanNetwork
from .network.rendezvous import RendezvousClient
from .network.transport import MeshTransport
from .storage import Storage

log = logging.getLogger("ruche.room")

Listener = Callable[[str, object], None]

AUTO_FETCH_MAX = 8 * 1024 * 1024  # images récupérées automatiquement jusqu'à 8 Mo
RATE_LIMIT = 40      # messages acceptés par pair…
RATE_WINDOW = 60.0   # …et par minute


class RoomManager:
    def __init__(self, storage: Storage, identity: Identity) -> None:
        self.storage = storage
        self.identity = identity
        self.history = HistoryLog(storage)
        self.transport = MeshTransport(self._send_signal)
        self.transport.on_message = self._on_transport_message
        self.transport.on_binary = self._on_binary
        self.transport.on_link_open = self._on_link_open
        self.transport.on_link_closed = self._on_link_closed
        self.files = FileStore(storage, config.files_dir())
        self.transport.on_track = self._on_track

        self.rendezvous: RendezvousClient | None = None
        self.lan: LanNetwork | None = None
        self.members: dict[str, str] = {}
        self._member_sources: dict[str, set[str]] = {}
        self.room: str | None = None
        self.host_id: str | None = None
        # Confiance et modération
        self.room_key = None
        self._trust: dict[str, dict] = {}
        self._blocked: set[str] = set()
        self._muted: set[str] = set()
        self._rate: dict[str, list[float]] = {}
        self._rate_warned: set[str] = set()

        # Appels
        self.call_active = False
        self.call_peers: set[str] = set()
        self._screen_sharing = False
        self._local_media: LocalMedia | None = None
        self._remote_tracks: dict[tuple[str, str], object] = {}
        self._speakers: dict[tuple[str, str], SpeakerSink] = {}
        self._camera_device: str | None = None
        self._microphone_device: int | None = None
        self._speaker_device: int | None = None
        self._video_profile = DEFAULT_PROFILE
        self._screen_fps = 30
        self._screen_monitor = 1
        self._media_factory = LocalMedia
        self._speaker_factory = SpeakerSink

        self._listeners: list[Listener] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_progress: dict[str, int] = {}

    def set_media_devices(
        self,
        camera: str | None = None,
        microphone: int | None = None,
        speaker: int | None = None,
        profile: str | None = None,
        screen_fps: int | None = None,
        screen_monitor: int | None = None,
    ) -> None:
        self._camera_device = camera or None
        self._microphone_device = microphone
        self._speaker_device = speaker
        if profile:
            self._video_profile = profile
        if screen_fps:
            self._screen_fps = screen_fps
        if screen_monitor is not None:
            self._screen_monitor = screen_monitor

    # --- Observateurs -----------------------------------------------------
    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _emit(self, event: str, payload: object = None) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, payload)
            except Exception:  # une UI défaillante ne doit pas casser le noyau
                log.exception("observateur en échec pour l'événement %s", event)

    # --- Entrée / sortie de salon ----------------------------------------
    async def join(
        self,
        room: str,
        pseudo: str,
        rendezvous_url: str = "",
        use_lan: bool = True,
        password: str = "",
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self.room = room
        self.identity.pseudo = pseudo
        save_identity(self.identity)
        self.transport.configure(self.identity.peer_id, pseudo)

        # Chiffrement de bout en bout si un mot de passe est fourni.
        self.room_key = crypto.derive_room_key(password, room) if password else None
        self.history.configure(
            self.identity.private_key, self.identity.public_key, self.room_key
        )
        self._load_trust()

        self.members = {self.identity.peer_id: pseudo}
        self._member_sources = {}
        entries = self.history.load_room(room)
        self.storage.remember_room(room)

        self._emit("joined", room)
        self._emit("history", entries)
        self._recompute_host()
        self._emit_members()

        if rendezvous_url:
            self._emit("status", "connexion au rendez-vous…")
            self.rendezvous = RendezvousClient(
                rendezvous_url,
                room,
                self.identity.peer_id,
                pseudo,
                on_peers=self._on_peers,
                on_peer_joined=self._on_peer_joined,
                on_peer_left=self._on_peer_left,
                on_signal=self._handle_signal,
                on_status=lambda text: self._emit("status", text),
            )
            await self.rendezvous.start()

        # Découverte locale : fonctionne même sans serveur de rendez-vous.
        if use_lan:
            self.lan = LanNetwork(
                self.identity.peer_id,
                pseudo,
                on_peer_found=lambda pid, ps: self._add_member(pid, ps, "lan"),
                on_peer_lost=lambda pid: self._remove_member(pid, "lan"),
                on_signal=self._handle_signal,
                on_status=lambda text: self._emit("status", text),
            )
            await self.lan.start(room)

    async def leave(self) -> None:
        if self.call_active:
            await self.end_call(notify=True)
        # Prévenir les pairs qu'on part tout de suite (mDNS peut être lent).
        try:
            await self.transport.broadcast({"t": "bye", "id": self.identity.peer_id})
            await asyncio.sleep(0.1)
        except Exception:
            pass
        if self.lan is not None:
            await self.lan.stop()
            self.lan = None
        if self.rendezvous is not None:
            await self.rendezvous.stop()
            self.rendezvous = None
        await self.transport.close_all()
        for sink in self._speakers.values():
            sink.close()
        self._speakers.clear()
        self._remote_tracks.clear()
        if self._local_media is not None:
            self._local_media.stop()
            self._local_media = None
        self.members = {}
        self.host_id = None
        self.room = None
        self.room_key = None
        self._rate.clear()
        self._rate_warned.clear()
        self._emit("left", None)

    # --- Découverte : rappels du rendez-vous -----------------------------
    def _on_peers(self, peers: list[dict]) -> None:
        for peer in peers:
            self._add_member(peer["id"], peer.get("pseudo", ""), "rv")

    def _on_peer_joined(self, peer_id: str, pseudo: str) -> None:
        self._add_member(peer_id, pseudo, "rv")

    def _on_peer_left(self, peer_id: str) -> None:
        self._remove_member(peer_id, "rv")

    def _add_member(self, peer_id: str, pseudo: str, source: str = "rv") -> None:
        if not peer_id or peer_id == self.identity.peer_id:
            return
        sources = self._member_sources.setdefault(peer_id, set())
        is_new = not sources
        sources.add(source)
        if is_new:
            self.members[peer_id] = pseudo or peer_id[:8]
            self._emit("status", f"{self.members[peer_id]} a rejoint le salon")
        elif pseudo:
            self.members[peer_id] = pseudo
        self._emit_members()
        self._recompute_host()
        if self._loop is not None:
            self._loop.create_task(self.transport.add_peer(peer_id, self.members[peer_id]))

    def _forget_member(self, peer_id: str) -> None:
        """Retire un pair immédiatement (départ annoncé explicitement)."""
        self._member_sources.pop(peer_id, None)
        if peer_id not in self.members:
            return
        self._remove_member_now(peer_id)

    # --- Confiance : clés, blocage, sourdine ------------------------------
    def _load_trust(self) -> None:
        self._trust = {row["peer_id"]: row for row in self.storage.all_peers()}
        self._blocked = {pid for pid, row in self._trust.items() if row.get("blocked")}
        self._muted = {pid for pid, row in self._trust.items() if row.get("muted")}

    def _register_peer(self, peer_id: str, pseudo: str, public_key: str) -> None:
        """Enregistre un pair et sa clé (confiance à la première vue).

        Une clé déjà connue n'est jamais remplacée : si elle change, c'est le
        signe d'une usurpation, et on refuse le pair."""
        if not peer_id or peer_id == self.identity.peer_id:
            return
        if public_key:
            if crypto.peer_id_for(public_key) != peer_id:
                self._emit("status", f"identité incohérente reçue de {pseudo or peer_id[:8]} — ignorée")
                return
            known = self._trust.get(peer_id)
            if known and known.get("public_key") and known["public_key"] != public_key:
                self._emit(
                    "security",
                    {
                        "kind": "cle-changee",
                        "peer_id": peer_id,
                        "pseudo": pseudo or peer_id[:8],
                    },
                )
                return
            self.storage.remember_peer(peer_id, public_key, pseudo)
        self._load_trust()
        self._add_member(peer_id, pseudo)

    def _is_blocked(self, peer_id: str) -> bool:
        return peer_id in self._blocked

    def set_peer_blocked(self, peer_id: str, blocked: bool) -> None:
        self.storage.remember_peer(peer_id, "", self.members.get(peer_id, ""))
        self.storage.set_peer_flag(peer_id, "blocked", blocked)
        self._load_trust()
        self._emit("status", ("pair bloqué" if blocked else "pair débloqué"))
        self._emit_members()

    def set_peer_muted(self, peer_id: str, muted: bool) -> None:
        self.storage.remember_peer(peer_id, "", self.members.get(peer_id, ""))
        self.storage.set_peer_flag(peer_id, "muted", muted)
        self._load_trust()
        self._emit_members()

    def set_peer_verified(self, peer_id: str, verified: bool) -> None:
        self.storage.remember_peer(peer_id, "", self.members.get(peer_id, ""))
        self.storage.set_peer_flag(peer_id, "verified", verified)
        self._load_trust()
        self._emit_members()

    def is_muted(self, peer_id: str) -> bool:
        return peer_id in self._muted

    def peers(self) -> list[dict]:
        return self.storage.all_peers()

    def fingerprint_of(self, peer_id: str) -> str | None:
        row = self._trust.get(peer_id)
        key = (row or {}).get("public_key") or ""
        return crypto.fingerprint(key) if key else None

    def room_password_hint(self, password: str) -> str:
        return crypto.password_hint(password, self.room or "")

    def _remove_member(self, peer_id: str, source: str = "rv") -> None:
        sources = self._member_sources.get(peer_id)
        if sources is None:
            return
        sources.discard(source)
        if sources:
            return  # encore présent via une autre source de découverte
        self._member_sources.pop(peer_id, None)
        self._remove_member_now(peer_id)

    def _remove_member_now(self, peer_id: str) -> None:
        pseudo = self.members.pop(peer_id, None)
        if pseudo is None:
            return
        self._emit("status", f"{pseudo} a quitté le salon")
        # Nettoyer les médias du pair parti.
        for key in [k for k in self._speakers if k[0] == peer_id]:
            self._speakers.pop(key).close()
        for key in [k for k in self._remote_tracks if k[0] == peer_id]:
            self._remote_tracks.pop(key, None)
        self.call_peers.discard(peer_id)
        self._emit("call-peer-left", {"peer_id": peer_id})
        self._emit_members()
        self._recompute_host()
        if self._loop is not None:
            self._loop.create_task(self.transport.remove_peer(peer_id))

    # --- Élection d'hôte --------------------------------------------------
    def _recompute_host(self) -> None:
        if not self.members:
            new_host = None
        else:
            new_host = min(self.members)
        if new_host == self.host_id:
            return
        self.host_id = new_host
        self._emit("host", new_host)
        if new_host is not None:
            self._emit("status", f"hôte du salon : {self.members.get(new_host, new_host[:8])}")

    def is_host(self) -> bool:
        return self.host_id == self.identity.peer_id

    # --- Signalisation ----------------------------------------------------
    async def _send_signal(self, to: str, payload: dict) -> bool:
        if self.rendezvous is not None and self.rendezvous.connected:
            if await self.rendezvous.send_signal(to, payload):
                return True
        if self.lan is not None and self.lan.has_peer(to):
            return await self.lan.send_signal(to, payload)
        return False

    async def _handle_signal(self, peer_id: str, payload: dict) -> None:
        await self.transport.handle_signal(peer_id, payload)

    # --- Maillage : rappels ----------------------------------------------
    def _on_link_open(self, peer_id: str) -> None:
        if self._loop is not None:
            self._loop.create_task(self._send_hello_and_sync(peer_id))
            if self.call_active:
                link = self.transport.links.get(peer_id)
                if link is not None:
                    self._loop.create_task(self._sync_media_to_link(link))

    def _on_link_closed(self, peer_id: str) -> None:
        pseudo = self.members.get(peer_id)
        if pseudo:
            self._emit("status", f"connexion directe perdue avec {pseudo}")

    async def _send_hello_and_sync(self, peer_id: str) -> None:
        await self.transport.send_to(
            peer_id,
            {
                "t": "hello",
                "id": self.identity.peer_id,
                "pseudo": self.identity.pseudo,
                "pub": self.identity.public_key,
            },
        )
        await self.transport.send_to(
            peer_id, {"t": "sync", "entries": self.history.raw_entries()}
        )

    def _on_transport_message(self, peer_id: str, data: dict) -> None:
        kind = data.get("t")
        if kind == "hello":
            self._register_peer(
                data.get("id", peer_id), data.get("pseudo", ""), data.get("pub", "")
            )
        elif kind == "chat":
            entry = data.get("entry", {})
            if isinstance(entry, dict) and self._rate_ok(entry.get("origin", "")):
                self._merge_and_emit([entry])
        elif kind == "sync":
            self._merge_and_emit(data.get("entries", []))
        elif kind == "file-want":
            file_id = data.get("id", "")
            if file_id and self.files.is_local(file_id) and self._loop is not None:
                self._loop.create_task(self._serve_file(peer_id, file_id))
        elif kind == "file-begin":
            self._on_file_begin(peer_id, data)
        elif kind == "file-end":
            self._on_file_end(data)
        elif kind == "media-request":
            if self._loop is not None:
                self._loop.create_task(self._handle_media_request(peer_id))
        elif kind == "call-invite":
            self._emit(
                "call-invite",
                {"peer_id": peer_id, "pseudo": data.get("pseudo") or self.members.get(peer_id, peer_id[:8])},
            )
        elif kind == "call-join":
            if self.call_active and self._loop is not None:
                link = self.transport.links.get(peer_id)
                if link is not None:
                    self._loop.create_task(self._sync_media_to_link(link))
        elif kind == "call-end":
            if self.call_active and self._loop is not None:
                self._loop.create_task(self.end_call(notify=False))
        elif kind == "bye":
            self._forget_member(peer_id)

    def _rate_ok(self, peer_id: str) -> bool:
        """Limite simple : au-delà de 40 messages par minute, on ignore."""
        if not peer_id or peer_id == self.identity.peer_id:
            return True
        now = time.time()
        bucket = self._rate.setdefault(peer_id, [])
        bucket[:] = [stamp for stamp in bucket if now - stamp < RATE_WINDOW]
        if len(bucket) >= RATE_LIMIT:
            if peer_id not in self._rate_warned:
                self._rate_warned.add(peer_id)
                self._emit(
                    "status",
                    f"{self.members.get(peer_id, peer_id[:8])} envoie trop de messages — limité",
                )
            return False
        bucket.append(now)
        return True

    def _merge_and_emit(self, entries: list[dict]) -> None:
        clean = [
            e
            for e in entries
            if isinstance(e, dict) and e.get("id") and not self._is_blocked(e.get("origin", ""))
        ]
        for entry in self.history.merge(clean):
            self._emit("message", entry)
            if entry.get("kind") == "file":
                self._maybe_auto_fetch(entry)

    # --- Envoi d'un message ----------------------------------------------
    async def send_text(self, body: str) -> None:
        body = body.strip()
        if not body or not self.room:
            return
        raw = self.history.add_local(
            "text",
            body,
            origin=self.identity.peer_id,
            pseudo=self.identity.pseudo,
        )
        view = self.history.display(raw)
        if view is not None:
            self._emit("message", view)
        await self.transport.broadcast({"t": "chat", "entry": raw})

    # --- Fichiers ---------------------------------------------------------
    async def send_file(self, path) -> None:
        if not self.room:
            return
        record = self.files.prepare(Path(path))
        raw = self.history.add_local(
            "file",
            record.name,
            origin=self.identity.peer_id,
            pseudo=self.identity.pseudo,
            extra=record.as_extra(),
        )
        view = self.history.display(raw)
        if view is not None:
            self._emit("message", view)
        await self.transport.broadcast({"t": "chat", "entry": raw})

    async def request_file(self, file_id: str) -> None:
        local = self.files.path(file_id)
        if local is not None:
            self._emit(
                "file-ready",
                {"file_id": file_id, "name": local.name, "path": str(local), "mime": ""},
            )
            return
        await self.transport.broadcast({"t": "file-want", "id": file_id})

    def _maybe_auto_fetch(self, entry: dict) -> None:
        extra = entry.get("extra") or {}
        file_id = extra.get("file_id")
        mime = str(extra.get("mime", ""))
        size = int(extra.get("size", 0) or 0)
        if not file_id or not mime.startswith("image/"):
            return
        if size and size > AUTO_FETCH_MAX:
            return
        if self.files.is_local(file_id) or self._loop is None:
            return
        self._loop.create_task(self.request_file(file_id))

    async def _serve_file(self, peer_id: str, file_id: str) -> None:
        record = self.files.get(file_id)
        if record is None or not record.path:
            return
        source = Path(record.path)
        if not source.exists():
            return
        await self.transport.send_to(
            peer_id,
            {
                "t": "file-begin",
                "id": record.id,
                "name": record.name,
                "size": record.size,
                "mime": record.mime,
                "sha256": record.sha256,
            },
        )
        tag = file_id.encode("utf-8")
        prefix = struct.pack(">H", len(tag)) + tag
        with open(source, "rb") as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                if not await self.transport.send_frame_to(peer_id, prefix + chunk):
                    self._emit("status", f"transfert interrompu vers {peer_id[:8]}")
                    return
        await self.transport.send_to(peer_id, {"t": "file-end", "id": file_id})
        self._emit("status", f"« {record.name} » envoyé")

    def _on_file_begin(self, peer_id: str, data: dict) -> None:
        file_id = data.get("id")
        if not file_id:
            return
        started = self.files.begin_receive(
            file_id,
            data.get("name", "fichier"),
            int(data.get("size", 0) or 0),
            data.get("mime", ""),
        )
        if started:
            self._emit("file-start", {"file_id": file_id, "name": data.get("name", "")})
            self._emit("status", f"réception de « {data.get('name', 'fichier')} »…")

    def _on_file_end(self, data: dict) -> None:
        file_id = data.get("id")
        if not file_id:
            return
        record = self.files.finish_receive(file_id)
        if record is None:
            self._emit("status", "transfert échoué (intégrité)")
            return
        self._emit(
            "file-ready",
            {
                "file_id": record.id,
                "name": record.name,
                "path": record.path,
                "mime": record.mime,
            },
        )
        self._emit("status", f"« {record.name} » reçu")

    def _on_binary(self, peer_id: str, frame: bytes) -> None:
        if len(frame) < 2:
            return
        tag_length = struct.unpack_from(">H", frame, 0)[0]
        if len(frame) < 2 + tag_length:
            return
        file_id = frame[2 : 2 + tag_length].decode("utf-8", "ignore")
        payload = frame[2 + tag_length :]
        received = self.files.write_chunk(file_id, payload)
        if received < 0:
            return
        progress = self.files.progress(file_id)
        if progress is None:
            return
        received, total = progress
        previous = self._last_progress.get(file_id, 0)
        if received - previous >= 512 * 1024 or received >= total:
            self._last_progress[file_id] = received
            self._emit("file-progress", {"file_id": file_id, "received": received, "total": total})

    # --- Appels -----------------------------------------------------------
    async def start_call(self) -> None:
        if self.call_active or not self.room:
            return
        self._begin_local_call()
        self._emit("call-started", {"initiator": self.identity.pseudo})
        await self._sync_media_all()
        await self.transport.broadcast(
            {"t": "call-invite", "from": self.identity.peer_id, "pseudo": self.identity.pseudo}
        )

    async def join_call(self) -> None:
        if self.call_active:
            return
        self._begin_local_call()
        self._emit("call-started", {"initiator": self.identity.pseudo})
        await self._sync_media_all()
        await self.transport.broadcast(
            {"t": "call-join", "from": self.identity.peer_id, "pseudo": self.identity.pseudo}
        )

    def _begin_local_call(self) -> None:
        self.call_active = True
        self.call_peers = {self.identity.peer_id}
        self._local_media = self._media_factory(
            self._camera_device, self._microphone_device, self._video_profile, self._screen_fps
        )
        self._emit(
            "call-local-video",
            {"track": self._local_media.preview_video(), "pseudo": self.identity.pseudo},
        )

    def set_microphone_enabled(self, enabled: bool) -> None:
        if self._local_media is not None:
            self._local_media.set_audio_enabled(enabled)

    def set_camera_enabled(self, enabled: bool) -> None:
        if self._local_media is not None:
            self._local_media.set_video_enabled(enabled)

    async def _sync_media_all(self) -> None:
        links = list(self.transport.links.values())
        results = await asyncio.gather(
            *(self._sync_media_to_link(link) for link in links), return_exceptions=True
        )
        for link, result in zip(links, results):
            if isinstance(result, Exception):
                log.warning(
                    "négociation média avec %s échouée : %s", link.peer_id[:8], result
                )

    async def _handle_media_request(self, peer_id: str) -> None:
        try:
            await self.transport.handle_media_request(peer_id)
        except Exception:
            log.exception("échec du traitement de la demande média")

    async def _sync_media_to_link(self, link) -> None:
        if not self.call_active or self._local_media is None:
            return
        video = None
        if self._screen_sharing:
            video = self._local_media.screen_video()
        if video is None:
            video = self._local_media.new_video()
        link.set_local_media(self._local_media.new_audio(), video)
        await link.request_media()

    async def start_screen_share(self, monitor: int | None = None) -> None:
        if not self.call_active or self._local_media is None or self._screen_sharing:
            return
        try:
            preview = self._local_media.start_screen(
                monitor if monitor is not None else self._screen_monitor
            )
        except Exception as exc:  # mss absent, écran inaccessible…
            self._emit("status", f"partage d'écran impossible : {exc}")
            return
        self._screen_sharing = True
        for link in self.transport.links.values():
            screen = self._local_media.screen_video()
            if screen is not None:
                link.set_video_track(screen)
        self._emit("screen-shared", {"track": preview, "pseudo": self.identity.pseudo})
        self._emit("status", "partage d'écran démarré")

    async def stop_screen_share(self) -> None:
        if not self._screen_sharing or self._local_media is None:
            return
        self._local_media.stop_screen()
        self._screen_sharing = False
        for link in self.transport.links.values():
            link.set_video_track(self._local_media.new_video())
        self._emit("screen-stopped", None)
        self._emit(
            "call-local-video",
            {"track": self._local_media.preview_video(), "pseudo": self.identity.pseudo},
        )
        self._emit("status", "partage d'écran arrêté")

    def is_screen_sharing(self) -> bool:
        return self._screen_sharing

    async def end_call(self, notify: bool = True) -> None:
        if not self.call_active:
            return
        self.call_active = False
        self.call_peers = set()
        self._screen_sharing = False
        self._remote_tracks.clear()
        for sink in self._speakers.values():
            sink.close()
        self._speakers.clear()
        await self.transport.clear_media()
        if self._local_media is not None:
            self._local_media.stop()
            self._local_media = None
        self._emit("call-ended", None)
        if notify:
            await self.transport.broadcast(
                {"t": "call-end", "from": self.identity.peer_id}
            )

    def _on_track(self, peer_id: str, track) -> None:
        kind = getattr(track, "kind", "")
        self._remote_tracks[(peer_id, kind)] = track
        self._emit(
            "call-track",
            {"peer_id": peer_id, "kind": kind, "track": track,
             "pseudo": self.members.get(peer_id, peer_id[:8])},
        )
        if kind == "audio" and self._loop is not None:
            sink = self._speaker_factory(self._speaker_device)
            self._speakers[(peer_id, "audio")] = sink
            self._loop.create_task(self._pump_audio(sink, track))

    async def _pump_audio(self, sink: SpeakerSink, track) -> None:
        try:
            while True:
                frame = await track.recv()
                await sink.feed(frame)
        except Exception:
            pass
        finally:
            sink.close()

    # --- Divers -----------------------------------------------------------
    def _emit_members(self) -> None:
        rows = []
        for peer_id, pseudo in self.members.items():
            trust = self._trust.get(peer_id) or {}
            rows.append(
                {
                    "id": peer_id,
                    "pseudo": pseudo,
                    "is_self": peer_id == self.identity.peer_id,
                    "is_host": peer_id == self.host_id,
                    "verified": bool(trust.get("verified")),
                    "blocked": peer_id in self._blocked,
                    "muted": peer_id in self._muted,
                    "fingerprint": self.fingerprint_of(peer_id),
                }
            )
        rows.sort(key=lambda r: (not r["is_host"], r["pseudo"].lower()))
        self._emit("members", rows)
