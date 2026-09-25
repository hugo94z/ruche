"""Vérification de la tenue des 30 images/seconde avant de s'engager.

On monte une vraie session WebRTC locale (deux pairs, comme en appel) et on
mesure le coût réel de l'encodage vidéo à 30 fps.

Le motif utilisé est du **bruit aléatoire** : c'est le pire cas possible pour
un encodeur. Une vraie caméra compresse bien mieux, donc les chiffres obtenus
sont pessimistes.

    python tools/fps_poc.py
"""

from __future__ import annotations

import asyncio
import fractions
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from aiortc import RTCPeerConnection, VideoStreamTrack  # noqa: E402
from av import VideoFrame  # noqa: E402

DURATION = 15.0
WIDTH, HEIGHT = 640, 480


class NoiseVideoTrack(VideoStreamTrack):
    """Bruit aléatoire animé : pire cas pour l'encodeur."""

    kind = "video"

    def __init__(self, fps: int, paced: bool = True) -> None:
        super().__init__()
        self.fps = fps
        self.paced = paced
        self.pts = 0
        self._start: float | None = None
        rng = np.random.default_rng(1)
        self._base = rng.integers(0, 256, (HEIGHT, WIDTH, 3), dtype=np.uint8)

    async def recv(self) -> VideoFrame:
        if self.paced:
            # Cadence sur l'horloge réelle : on attend seulement le temps
            # restant avant la prochaine échéance (le temps de traitement ne
            # s'ajoute donc plus à l'intervalle). L'horloge est ancrée au
            # PREMIER appel, pas à la construction (sinon la piste se croit
            # en retard après le temps de connexion et ne dort plus).
            now = time.monotonic()
            if self._start is None:
                self._start = now
            target = self._start + (self.pts + 1) / self.fps
            delay = target - now
            if delay > 0:
                await asyncio.sleep(delay)
        else:
            await asyncio.sleep(1 / self.fps)
        img = np.roll(self._base, self.pts % WIDTH, axis=1)
        frame = VideoFrame.from_ndarray(np.ascontiguousarray(img), format="rgb24")
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, self.fps)
        self.pts += 1
        return frame


async def measure(fps: int, paced: bool = True) -> dict:
    pc1 = RTCPeerConnection()
    pc2 = RTCPeerConnection()
    track = NoiseVideoTrack(fps, paced)
    pc1.addTrack(track)

    received = {"count": 0, "bytes": 0, "track": None}

    @pc2.on("track")
    def _on_track(remote) -> None:
        received["track"] = remote

    offer = await pc1.createOffer()
    await pc1.setLocalDescription(offer)
    await pc2.setRemoteDescription(pc1.localDescription)
    answer = await pc2.createAnswer()
    await pc2.setLocalDescription(answer)
    await pc1.setRemoteDescription(pc2.localDescription)

    # Attendre que la piste arrive.
    deadline = time.monotonic() + 5
    while received["track"] is None and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    if received["track"] is None:
        await pc1.close()
        await pc2.close()
        return {"fps": fps, "error": "aucune piste reçue"}

    remote = received["track"]
    stats = None
    cpu0 = time.process_time()
    wall0 = time.monotonic()
    end = wall0 + DURATION
    while time.monotonic() < end:
        try:
            frame = await asyncio.wait_for(remote.recv(), timeout=2.0)
        except Exception:
            break
        received["count"] += 1
        received["bytes"] += frame.width * frame.height

    cpu = time.process_time() - cpu0
    wall = time.monotonic() - wall0

    for sender in pc1.getSenders():
        try:
            report = await sender.getStats()
        except Exception:
            report = None
        if report:
            stats = report
    track.stop()
    await pc1.close()
    await pc2.close()

    return {
        "fps": fps,
        "paced": paced,
        "frames": received["count"],
        "wall": wall,
        "cpu": cpu,
        "cpu_pct": 100 * cpu / wall if wall else 0,
    }


async def main() -> int:
    print(f"Mesure sur {DURATION:.0f} s, vidéo 640×480, bruit aléatoire (pire cas)\n")
    for paced in (False, True):
        label = "cadence corrigée" if paced else "cadence naïve  "
        for fps in (24, 30):
            r = await measure(fps, paced)
            if "error" in r:
                print(f"  {label}  {fps} fps : ÉCHEC — {r['error']}")
                continue
            attendu = fps * r["wall"]
            ratio = 100 * r["frames"] / attendu if attendu else 0
            verdict = "TENU" if ratio >= 90 else "DÉGRADÉ"
            print(
                f"  {label}  cible {fps:2d} fps : reçues {r['frames']:4d} en {r['wall']:4.1f} s "
                f"({ratio:5.1f} %)   CPU {r['cpu_pct']:4.0f} % d'un cœur   → {verdict}"
            )
        print()
    print("Rappel : ce motif est le pire cas ; une vraie caméra coûte moins cher.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
