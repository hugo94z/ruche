"""Maillage WebRTC pair-à-pair.

Chaque paire de pairs dans un salon partage une connexion `RTCPeerConnection`
avec deux canaux de données fiables :

* ``ruche``       — messages de chat, synchronisation d'historique, contrôle ;
* ``ruche-files`` — octets bruts des fichiers (images, vidéos…).

Séparer les flux garde le chat fluide pendant un gros transfert. Les appels
ajouteront simplement des pistes média sur les mêmes connexions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)

from ... import config

log = logging.getLogger("ruche.transport")

SignalSender = Callable[[str, dict], Awaitable[bool]]

CONTROL_LABEL = "ruche"
FILES_LABEL = "ruche-files"
MAX_BUFFERED = 2 * 1024 * 1024  # contre-pression du canal fichiers


async def _wait_ice_candidates(
    pc: RTCPeerConnection, *, settle: float = 1.0, max_wait: float = 8.0
) -> None:
    """Attend que la description locale contienne des candidats ICE.

    On ne patiente pas jusqu'à l'état « complete » (ce qui peut prendre très
    long si un serveur STUN est injoignable). Dès qu'au moins un candidat est
    présent et que la liste n'évolue plus depuis `settle` secondes, on poursuit.
    """
    if pc.iceGatheringState == "complete":
        return

    loop = asyncio.get_running_loop()
    start = loop.time()
    last_count = -1
    last_change = start

    while True:
        sdp = pc.localDescription.sdp if pc.localDescription else ""
        count = sdp.count("a=candidate:")
        now = loop.time()

        if count != last_count:
            last_count = count
            last_change = now
        elif count > 0 and now - last_change >= settle:
            return
        elif now - start >= max_wait:
            return

        if pc.iceGatheringState == "complete" and count > 0:
            return

        await asyncio.sleep(0.1)


def _rtc_config() -> RTCConfiguration:
    servers = []
    for entry in config.ice_servers():
        urls = entry.get("urls")
        if not urls:
            continue
        kwargs: dict = {"urls": urls}
        if entry.get("username"):
            kwargs["username"] = entry["username"]
        if entry.get("credential"):
            kwargs["credential"] = entry["credential"]
        servers.append(RTCIceServer(**kwargs))
    return RTCConfiguration(iceServers=servers)


class PeerLink:
    """Connexion à un pair unique : canal de contrôle + canal de fichiers."""

    def __init__(self, mesh: "MeshTransport", peer_id: str, pseudo: str, initiator: bool):
        self.mesh = mesh
        self.peer_id = peer_id
        self.pseudo = pseudo
        self.initiator = initiator
        self.pc = RTCPeerConnection(_rtc_config())

        self.control = None
        self.files = None
        self.ready = asyncio.Event()        # canal de contrôle ouvert
        self.files_ready = asyncio.Event()  # canal de fichiers ouvert
        self.closed = False
        self._started = False

        # Médias (appels)
        self._local_audio = None
        self._local_video = None
        self._media_added = False
        self._clear_pending = False
        self._negotiating = False
        self._renegotiate_pending = False
        self._senders: list = []
        self._video_sender = None
        self._audio_sender = None

        self.pc.on("connectionstatechange", self._on_connection_state)
        self.pc.on("datachannel", self._on_datachannel)
        self.pc.on("track", self._on_track)

        if initiator:
            self.control = self.pc.createDataChannel(CONTROL_LABEL, ordered=True)
            self._bind_channel(self.control)
            self.files = self.pc.createDataChannel(FILES_LABEL, ordered=True)
            self._bind_channel(self.files)

    # --- Canaux -----------------------------------------------------------
    def _on_datachannel(self, channel) -> None:
        log.debug("canal « %s » reçu de %s", channel.label, self.peer_id[:8])
        if channel.label == FILES_LABEL:
            self.files = channel
        else:
            self.control = channel
        self._bind_channel(channel)

    def _bind_channel(self, channel) -> None:
        is_files = channel.label == FILES_LABEL

        @channel.on("open")
        def _on_open() -> None:
            if is_files:
                self.files_ready.set()
            else:
                self._mark_open()

        @channel.on("message")
        def _on_message(message) -> None:
            if is_files:
                self.mesh._notify_binary(self, message)
            else:
                self.mesh._notify_message(self, message)

        @channel.on("close")
        def _on_close() -> None:
            self.mesh._notify_closed(self)

        # L'événement « open » peut avoir été émis avant notre branchement
        # (course classique côté répondant) : on vérifie l'état courant.
        if getattr(channel, "readyState", "") == "open":
            if is_files:
                self.files_ready.set()
            else:
                self._mark_open()

    def _mark_open(self) -> None:
        if self.ready.is_set():
            return
        log.info("canal de contrôle ouvert avec %s", self.peer_id[:8])
        self.ready.set()
        self.mesh._notify_open(self)

    def _on_connection_state(self) -> None:
        state = self.pc.connectionState
        log.debug("connexion %s -> %s", self.peer_id[:8], state)
        if state in ("failed", "closed"):
            self.mesh._notify_closed(self)

    # --- Négociation ------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self.pc.setLocalDescription(await self.pc.createOffer())
        await _wait_ice_candidates(self.pc)
        log.debug("offre -> %s", self.peer_id[:8])
        await self.mesh.send_signal(
            self.peer_id,
            {"type": "offer", "sdp": self.pc.localDescription.sdp, "pseudo": self.mesh.local_pseudo},
        )

    async def handle_offer(self, sdp: str, pseudo: str = "") -> None:
        if pseudo:
            self.pseudo = pseudo
        self._negotiating = True
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        # Si un appel est en cours, on renvoie nos propres pistes dans la réponse.
        self._attach_incoming()
        await self.pc.setLocalDescription(await self.pc.createAnswer())
        await _wait_ice_candidates(self.pc)
        await self.mesh.send_signal(
            self.peer_id,
            {"type": "answer", "sdp": self.pc.localDescription.sdp},
        )
        self._negotiating = False
        await self._maybe_renegotiate()

    async def handle_answer(self, sdp: str) -> None:
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
        self._negotiating = False
        await self._maybe_renegotiate()

    # --- Médias (appels) --------------------------------------------------
    def set_local_media(self, audio=None, video=None) -> None:
        """Prépare les pistes à envoyer (appelées avant une négociation)."""
        if audio is not None:
            self._local_audio = audio
        if video is not None:
            self._local_video = video
        self._clear_pending = False

    def clear_local_media(self) -> None:
        self._clear_pending = True

    def _apply_outgoing(self) -> None:
        """Côté qui propose : ajoute/retire nos pistes avant d'émettre l'offre."""
        if self._clear_pending:
            for sender in list(self.pc.getSenders()):
                if sender.track is not None:
                    try:
                        self.pc.removeTrack(sender)
                    except Exception:
                        pass
            self._senders.clear()
            self._media_added = False
            self._local_audio = None
            self._local_video = None
            self._clear_pending = False
        if not self._media_added and (self._local_audio or self._local_video):
            if self._local_audio is not None:
                self._audio_sender = self.pc.addTrack(self._local_audio)
                self._senders.append(self._audio_sender)
            if self._local_video is not None:
                self._video_sender = self.pc.addTrack(self._local_video)
                self._senders.append(self._video_sender)
            self._media_added = True

    def _attach_incoming(self) -> None:
        """Côté qui répond : réutilise les transceivers de l'offre.

        On ne peut pas appeler `addTrack` ici (cela créerait des sections
        média absentes de l'offre et la réponse serait invalide) : on s'appuie
        sur les transceivers négociés et on y branche nos pistes.
        """
        if self._clear_pending:
            for transceiver in self.pc.getTransceivers():
                if transceiver.kind in ("audio", "video"):
                    try:
                        transceiver.direction = "recvonly"
                    except Exception:
                        pass
            self._clear_pending = False
            self._media_added = False
            self._local_audio = None
            self._local_video = None
            return
        if not (self._local_audio or self._local_video):
            return
        for transceiver in self.pc.getTransceivers():
            try:
                if (
                    transceiver.kind == "audio"
                    and self._local_audio is not None
                    and transceiver.sender.track is None
                ):
                    transceiver.direction = "sendrecv"
                    transceiver.sender.replaceTrack(self._local_audio)
                    self._audio_sender = transceiver.sender
                elif (
                    transceiver.kind == "video"
                    and self._local_video is not None
                    and transceiver.sender.track is None
                ):
                    transceiver.direction = "sendrecv"
                    transceiver.sender.replaceTrack(self._local_video)
                    self._video_sender = transceiver.sender
            except Exception:
                pass
        self._media_added = True

    def set_video_track(self, track) -> None:
        """Change la piste vidéo envoyée sans renégocier (caméra ↔ écran)."""
        self._local_video = track
        sender = self._video_sender
        if sender is not None:
            try:
                sender.replaceTrack(track)
            except Exception:
                pass

    async def request_media(self) -> None:
        """Demande une négociation média (l'initiateur du lien l'exécute)."""
        if self.initiator:
            await self._offer_media()
        else:
            await self.send({"t": "media-request"})

    async def _offer_media(self) -> None:
        if self.closed:
            return
        if self._negotiating:
            # Une négociation est déjà en cours : on la relancera ensuite.
            self._renegotiate_pending = True
            return
        self._negotiating = True
        self._apply_outgoing()
        try:
            await self.pc.setLocalDescription(await self.pc.createOffer())
            await _wait_ice_candidates(self.pc)
            await self.mesh.send_signal(
                self.peer_id,
                {
                    "type": "offer",
                    "sdp": self.pc.localDescription.sdp,
                    "pseudo": self.mesh.local_pseudo,
                    "media": True,
                },
            )
        except Exception:
            self._negotiating = False
            raise

    async def _maybe_renegotiate(self) -> None:
        if self._renegotiate_pending and not self.closed:
            self._renegotiate_pending = False
            await self._offer_media()

    def _on_track(self, track) -> None:
        self.mesh._notify_track(self, track)

    # --- Envoi / fermeture -----------------------------------------------
    async def send(self, obj: dict) -> bool:
        """Message de contrôle (JSON)."""
        if self.closed or self.control is None:
            return False
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=10)
        except asyncio.TimeoutError:
            return False
        try:
            self.control.send(json.dumps(obj, ensure_ascii=False))
            return True
        except Exception as exc:
            log.debug("envoi vers %s échoué : %s", self.peer_id[:8], exc)
            return False

    async def send_frame(self, data: bytes) -> bool:
        """Octets bruts sur le canal fichiers, avec contre-pression."""
        channel = self.files
        if self.closed or channel is None:
            return False
        try:
            await asyncio.wait_for(self.files_ready.wait(), timeout=10)
        except asyncio.TimeoutError:
            return False
        while not self.closed and channel.bufferedAmount > MAX_BUFFERED:
            await asyncio.sleep(0.005)
        try:
            channel.send(data)
            return True
        except Exception as exc:
            log.debug("envoi de fichier vers %s échoué : %s", self.peer_id[:8], exc)
            return False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.ready.clear()
        self.files_ready.clear()
        try:
            await self.pc.close()
        except Exception:
            pass


class MeshTransport:
    """Ensemble des connexions d'un pair vers les autres membres du salon."""

    def __init__(self, send_signal: SignalSender) -> None:
        self.send_signal = send_signal
        self.local_id = ""
        self.local_pseudo = ""
        self.links: dict[str, PeerLink] = {}

        # Renseignés par le gestionnaire de salon.
        self.on_message: Callable[[str, dict], None] | None = None
        self.on_binary: Callable[[str, bytes], None] | None = None
        self.on_link_open: Callable[[str], None] | None = None
        self.on_link_closed: Callable[[str], None] | None = None
        self.on_track: Callable[[str, object], None] | None = None

    def configure(self, local_id: str, local_pseudo: str) -> None:
        self.local_id = local_id
        self.local_pseudo = local_pseudo

    # --- Gestion des pairs -----------------------------------------------
    def ensure_link(self, peer_id: str, pseudo: str = "") -> PeerLink | None:
        if not peer_id or peer_id == self.local_id:
            return None
        link = self.links.get(peer_id)
        if link is not None:
            if pseudo:
                link.pseudo = pseudo
            return link
        # Le pair ayant le plus petit identifiant initie la négociation :
        # les deux côtés calculent la même règle, donc pas de conflit.
        initiator = self.local_id < peer_id
        link = PeerLink(self, peer_id, pseudo, initiator)
        self.links[peer_id] = link
        return link

    async def add_peer(self, peer_id: str, pseudo: str = "") -> None:
        link = self.ensure_link(peer_id, pseudo)
        if link is not None and link.initiator and not link._started:
            await link.start()

    async def remove_peer(self, peer_id: str) -> None:
        link = self.links.pop(peer_id, None)
        if link is not None:
            await link.close()

    async def handle_signal(self, from_peer: str, payload: dict) -> None:
        kind = payload.get("type")
        log.debug("signal %s de %s", kind, from_peer[:8])
        if kind == "offer":
            link = self.ensure_link(from_peer, payload.get("pseudo", ""))
            if link is None:
                return
            await link.handle_offer(payload.get("sdp", ""), payload.get("pseudo", ""))
        elif kind == "answer":
            link = self.links.get(from_peer)
            if link is not None:
                await link.handle_answer(payload.get("sdp", ""))

    # --- Diffusion --------------------------------------------------------
    async def broadcast(self, obj: dict, exclude: str | None = None) -> None:
        targets = [link for pid, link in self.links.items() if pid != exclude]
        if targets:
            await asyncio.gather(*(link.send(obj) for link in targets), return_exceptions=True)

    async def send_to(self, peer_id: str, obj: dict) -> bool:
        link = self.links.get(peer_id)
        return await link.send(obj) if link else False

    async def send_frame_to(self, peer_id: str, data: bytes) -> bool:
        link = self.links.get(peer_id)
        return await link.send_frame(data) if link else False

    async def handle_media_request(self, peer_id: str) -> None:
        link = self.links.get(peer_id)
        if link is not None and link.initiator:
            await link._offer_media()

    async def broadcast_media(self, audio=None, video=None) -> None:
        """Prépare/relance la négociation média sur toutes les connexions."""
        links = list(self.links.values())
        for link in links:
            link.set_local_media(audio, video)
        await asyncio.gather(
            *(link.request_media() for link in links), return_exceptions=True
        )

    async def clear_media(self) -> None:
        links = list(self.links.values())
        for link in links:
            link.clear_local_media()
        await asyncio.gather(
            *(link.request_media() for link in links), return_exceptions=True
        )

    def connected_peers(self) -> list[str]:
        return [pid for pid, link in self.links.items() if link.ready.is_set()]

    def peers_with_files_channel(self) -> list[str]:
        return [pid for pid, link in self.links.items() if link.files_ready.is_set()]

    async def close_all(self) -> None:
        links = list(self.links.values())
        self.links.clear()
        await asyncio.gather(*(link.close() for link in links), return_exceptions=True)

    # --- Rappels internes -------------------------------------------------
    def _notify_open(self, link: PeerLink) -> None:
        if self.on_link_open:
            self.on_link_open(link.peer_id)

    def _notify_message(self, link: PeerLink, message) -> None:
        if self.on_message is None:
            return
        try:
            data = json.loads(message if isinstance(message, str) else message.decode())
        except (ValueError, AttributeError):
            return
        self.on_message(link.peer_id, data)

    def _notify_binary(self, link: PeerLink, message) -> None:
        if self.on_binary is None:
            return
        if isinstance(message, str):
            return
        self.on_binary(link.peer_id, bytes(message))

    def _notify_closed(self, link: PeerLink) -> None:
        # On ne signale la fermeture que si le lien est encore actif : une
        # fermeture volontaire (remove_peer) l'a déjà retiré du maillage.
        if self.links.get(link.peer_id) is not link:
            return
        self.links.pop(link.peer_id, None)
        link.closed = True
        if self.on_link_closed:
            self.on_link_closed(link.peer_id)

    def _notify_track(self, link: PeerLink, track) -> None:
        if self.on_track:
            self.on_track(link.peer_id, track)
