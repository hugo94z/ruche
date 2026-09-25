"""Test de bout en bout des appels (sans caméra ni micro réels).

Deux pairs se connectent, démarrent un appel de groupe et échangent des pistes
vidéo et audio synthétiques. On vérifie que la négociation média fonctionne et
que les images circulent bien dans les deux sens.

    python tools/call_test.py
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import sys
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from aiohttp import web  # noqa: E402
from aiortc import MediaStreamTrack  # noqa: E402
from aiortc.contrib.media import MediaRelay  # noqa: E402
from av import AudioFrame  # noqa: E402

from app import config  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="ruche-call-"))
config.data_dir = lambda: _TMP  # type: ignore[assignment]

from app.core.identity import Identity  # noqa: E402
from app.core.media import TestVideoTrack, frame_to_rgb  # noqa: E402
from app.core.room import RoomManager  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.rendezvous.server import build_app  # noqa: E402


class SineAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, freq: float = 440.0) -> None:
        super().__init__()
        self.freq = freq
        self.samples = 960
        self.pts = 0
        self.muted = False
        self._t = np.arange(self.samples)

    async def recv(self) -> AudioFrame:
        await asyncio.sleep(0.02)
        if self.muted:
            data = np.zeros(self.samples, dtype=np.int16)
        else:
            phase = 2 * np.pi * self.freq * (self.pts + self._t) / 48000.0
            data = (np.sin(phase) * 8000).astype(np.int16)
        frame = AudioFrame.from_ndarray(data.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = 48000
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, 48000)
        self.pts += self.samples
        return frame


class FakeLocalMedia:
    """Remplace caméra + micro par une mire et un son synthétiques."""

    def __init__(self, camera=None, microphone=None, profile=None, screen_fps=None) -> None:
        self._video = TestVideoTrack()
        self._audio = SineAudioTrack()
        self._vrelay = MediaRelay()
        self._arelay = MediaRelay()
        self._screen = None
        self._screen_relay = MediaRelay()

    def new_video(self):
        return self._vrelay.subscribe(self._video)

    def new_audio(self):
        return self._arelay.subscribe(self._audio)

    def preview_video(self):
        return self._vrelay.subscribe(self._video)

    def start_screen(self, monitor: int = 1):
        self.stop_screen()
        self._screen = TestVideoTrack()
        return self._screen_relay.subscribe(self._screen)

    def screen_video(self):
        if self._screen is None:
            return None
        return self._screen_relay.subscribe(self._screen)

    def stop_screen(self) -> None:
        if self._screen is not None:
            self._screen.stop()
            self._screen = None

    def set_audio_enabled(self, enabled: bool) -> None:
        self._audio.muted = not enabled

    def set_video_enabled(self, enabled: bool) -> None:
        self._video.muted = not enabled

    def stop(self) -> None:
        self._video.stop()
        self._audio.stop()


class DummySink:
    def __init__(self, device=None) -> None:
        self.frames = 0

    async def feed(self, frame) -> None:
        self.frames += 1

    def close(self) -> None:
        pass


def make_identity(pseudo: str) -> Identity:
    return Identity(
        peer_id=uuid.uuid4().hex[:16], pseudo=pseudo, public_key="", private_key=""
    )


async def wait_for(predicate, timeout: float = 25.0, label: str = "") -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    print(f"  ✗ délai dépassé : {label}")
    return False


async def start_rendezvous() -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"ws://127.0.0.1:{port}/ws"


async def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    runner, url = await start_rendezvous()

    store_a = Storage(_TMP / "a.sqlite3")
    store_b = Storage(_TMP / "b.sqlite3")
    alice = make_identity("Alice")
    bob = make_identity("Bob")
    a = RoomManager(store_a, alice)
    b = RoomManager(store_b, bob)
    for manager in (a, b):
        manager._media_factory = FakeLocalMedia
        manager._speaker_factory = DummySink

    results: list[tuple[str, bool]] = []

    print("1. Connexion des deux pairs…")
    await a.join("CALL01", "Alice", url)
    await b.join("CALL01", "Bob", url)
    ok = await wait_for(
        lambda: bool(a.transport.connected_peers()) and bool(b.transport.connected_peers()),
        label="maillage",
    )
    results.append(("Maillage établi", ok))

    print("2. Alice démarre un appel, Bob le rejoint…")
    await a.start_call()
    await b.join_call()

    def has(manager, kind):
        return any(key[1] == kind for key in manager._remote_tracks)

    ok = await wait_for(
        lambda: has(a, "video") and has(b, "video"), label="pistes vidéo distantes"
    )
    results.append(("Pistes vidéo échangées", ok))
    ok = await wait_for(
        lambda: has(a, "audio") and has(b, "audio"), label="pistes audio distantes"
    )
    results.append(("Pistes audio échangées", ok))

    print("3. Réception d'une image distante…")
    if has(a, "video"):
        track = next(t for k, t in a._remote_tracks.items() if k[1] == "video")
        try:
            frame = await asyncio.wait_for(track.recv(), timeout=10)
            rgb = frame_to_rgb(frame)
            results.append(("Image vidéo reçue (640×480×3)", rgb.shape == (480, 640, 3)))
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ erreur de réception : {exc}")
            results.append(("Image vidéo reçue (640×480×3)", False))
    else:
        results.append(("Image vidéo reçue (640×480×3)", False))

    print("4. Partage d'écran d'Alice…")
    await a.start_screen_share()
    results.append(("Partage d'écran actif", a.is_screen_sharing()))
    if has(b, "video"):
        track = next(t for k, t in b._remote_tracks.items() if k[1] == "video")
        try:
            frame = await asyncio.wait_for(track.recv(), timeout=10)
            results.append(("Bob reçoit l'écran (flux continu)", frame_to_rgb(frame).shape[0] > 0))
        except Exception:  # noqa: BLE001
            results.append(("Bob reçoit l'écran (flux continu)", False))
    else:
        results.append(("Bob reçoit l'écran (flux continu)", False))
    await a.stop_screen_share()
    results.append(("Partage d'écran arrêté", not a.is_screen_sharing()))

    print("5. Fin d'appel propagée…")
    await a.end_call()
    ok = await wait_for(lambda: not b.call_active, label="fin d'appel chez Bob")
    results.append(("Fin d'appel reçue par Bob", ok))
    results.append(("Appel terminé chez Alice", not a.call_active))

    await a.leave()
    await b.leave()
    store_a.close()
    store_b.close()
    await runner.cleanup()

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
