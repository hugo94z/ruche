"""Capture et restitution média : caméra, micro, haut-parleur, écran.

Deux points importants :

* **Cadence réelle** — les pistes se calent sur l'horloge murale (et non sur un
  ``sleep(1/fps)`` qui s'ajoute au temps d'encodage). Sans cela, une cible de
  30 fps n'en produit que ~20.
* **Annulation d'écho** — le micro retire ce que le haut-parleur joue, via un
  annuleur partagé (voir ``audio_fx``).
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import threading
import time

import av
import numpy as np
import sounddevice as sd
from aiortc import MediaStreamTrack, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from av import AudioFrame, VideoFrame
from av.audio.resampler import AudioResampler

from .audio_fx import CANCELLER

try:  # capture d'écran (partage)
    import mss
except ImportError:  # pragma: no cover
    mss = None  # type: ignore[assignment]

log = logging.getLogger("ruche.media")

# Profils vidéo : (largeur, hauteur, images par seconde)
VIDEO_PROFILES: dict[str, tuple[int, int, int]] = {
    "basse": (320, 240, 15),
    "moyenne": (480, 360, 24),
    "haute": (640, 480, 30),
    "hd": (960, 540, 30),
}
DEFAULT_PROFILE = "haute"

AUDIO_RATE = 48000
FRAME_MS = 20


def profile_for(name: str) -> tuple[int, int, int]:
    return VIDEO_PROFILES.get(name, VIDEO_PROFILES[DEFAULT_PROFILE])


def black_frame(pts: int = 0, fps: int = 30, size: tuple[int, int] = (640, 480)) -> VideoFrame:
    """Trame vidéo noire (caméra coupée)."""
    width, height = size
    img = np.zeros((height, width, 3), dtype=np.uint8)
    frame = VideoFrame.from_ndarray(img, format="rgb24")
    frame.pts = pts
    frame.time_base = fractions.Fraction(1, fps)
    return frame


class _PacedTrack:
    """Cadence sur l'horloge réelle, ancrée au premier appel.

    Sans cela, chaque trame coûte ``1/fps + temps de traitement`` : à 30 fps
    demandés on n'en obtient que ~20.
    """

    fps: int

    def _init_pacing(self, fps: int) -> None:
        self.fps = fps
        self._start: float | None = None
        self._pts = 0

    async def _next_slot(self) -> None:
        now = time.monotonic()
        if self._start is None:
            self._start = now
        delay = (self._start + (self._pts + 1) / self.fps) - now
        if delay > 0:
            await asyncio.sleep(delay)

    def _stamp(self, frame: VideoFrame) -> VideoFrame:
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, self.fps)
        self._pts += 1
        return frame


# --------------------------------------------------------------------------
# Énumération des périphériques
# --------------------------------------------------------------------------
def list_cameras() -> list[str]:
    try:
        from pygrabber.dshow_graph import FilterGraph  # Windows uniquement

        return list(FilterGraph().get_input_devices())
    except Exception:
        return []


def list_audio_devices() -> tuple[list[str], list[str]]:
    inputs: list[str] = []
    outputs: list[str] = []
    try:
        for index, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                inputs.append(f"{index}: {dev['name']}")
            if dev.get("max_output_channels", 0) > 0:
                outputs.append(f"{index}: {dev['name']}")
    except Exception:
        pass
    return inputs, outputs


def list_monitors() -> list[str]:
    """Écrans disponibles pour le partage (1 = premier écran)."""
    if mss is None:
        return ["Écran principal"]
    try:
        with mss.mss() as sct:
            names = []
            for index, monitor in enumerate(sct.monitors):
                if index == 0:
                    continue  # 0 = tous les écrans combinés
                label = "Écran principal" if index == 1 else f"Écran {index}"
                names.append(f"{label} ({monitor['width']}×{monitor['height']})")
            return names or ["Écran principal"]
    except Exception:
        return ["Écran principal"]


# --------------------------------------------------------------------------
# Pistes vidéo
# --------------------------------------------------------------------------
class CameraVideoTrack(VideoStreamTrack, _PacedTrack):
    """Flux de la webcam, lu via FFmpeg (dshow sur Windows)."""

    kind = "video"

    def __init__(self, device: str, profile: str = DEFAULT_PROFILE) -> None:
        VideoStreamTrack.__init__(self)
        self._device = device
        self._width, self._height, fps = profile_for(profile)
        self._init_pacing(fps)
        self._container = None
        self._stream = None
        self.muted = False
        self._open()

    def _open(self) -> None:
        self._container = av.open(
            f"video={self._device}",
            format="dshow",
            options={"framerate": str(self.fps), "video_size": f"{self._width}x{self._height}"},
        )
        self._stream = self._container.streams.video[0]
        self._stream.thread_type = "AUTO"

    async def recv(self) -> VideoFrame:
        await self._next_slot()
        loop = asyncio.get_event_loop()
        try:
            frame = await loop.run_in_executor(None, self._read)
        except Exception:
            frame = None
        if self.muted or frame is None:
            return self._stamp(black_frame(self._pts, self.fps, (self._width, self._height)))
        return self._stamp(frame)

    def _read(self) -> VideoFrame:
        try:
            raw = next(self._container.decode(self._stream))
        except (StopIteration, av.error.EOFError):
            self._open()
            raw = next(self._container.decode(self._stream))
        return raw.reformat(self._width, self._height, "yuv420p")

    def stop(self) -> None:
        super().stop()
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass


class TestVideoTrack(VideoStreamTrack, _PacedTrack):
    """Mire animée de secours (quand aucune caméra n'est disponible)."""

    kind = "video"

    def __init__(self, label: str = "Ruche", profile: str = DEFAULT_PROFILE) -> None:
        VideoStreamTrack.__init__(self)
        self._label = label
        self._width, self._height, fps = profile_for(profile)
        self._init_pacing(fps)
        self.muted = False
        self._start_time = time.monotonic()
        self._ramp_x = np.linspace(0, 255, self._width, dtype=np.uint8)[None, :]
        self._ramp_y = np.linspace(0, 255, self._height, dtype=np.uint8)[:, None]

    async def recv(self) -> VideoFrame:
        await self._next_slot()
        if self.muted:
            return self._stamp(black_frame(self._pts, self.fps, (self._width, self._height)))
        img = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        img[:, :, 0] = self._ramp_x
        img[:, :, 1] = self._ramp_y
        bar = int((time.monotonic() - self._start_time) * 120) % max(1, self._width)
        img[:, max(0, bar - 12) : bar, 2] = 255
        frame = VideoFrame.from_ndarray(img, format="rgb24")
        return self._stamp(frame)


class ScreenVideoTrack(VideoStreamTrack, _PacedTrack):
    """Capture de l'écran, redimensionnée pour rester fluide."""

    kind = "video"

    def __init__(self, monitor: int = 1, fps: int = 30, max_width: int = 1280) -> None:
        VideoStreamTrack.__init__(self)
        if mss is None:
            raise RuntimeError("Le module 'mss' est nécessaire au partage d'écran.")
        self._monitor = monitor
        self._max_width = max_width
        self._init_pacing(fps)
        self.muted = False
        self._lock = threading.Lock()
        self._sct = mss.mss()

    async def recv(self) -> VideoFrame:
        await self._next_slot()
        loop = asyncio.get_event_loop()
        image = await loop.run_in_executor(None, self._grab)
        frame = VideoFrame.from_ndarray(image, format="bgra")
        return self._stamp(frame)

    def _grab(self) -> np.ndarray:
        with self._lock:
            monitors = self._sct.monitors
            index = self._monitor if 0 <= self._monitor < len(monitors) else 1
            shot = self._sct.grab(monitors[index])
        image = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(
            shot.height, shot.width, 4
        ).copy()
        if self.muted:
            return np.zeros_like(image)
        if shot.width > self._max_width:
            step = int(np.ceil(shot.width / self._max_width))
            image = np.ascontiguousarray(image[::step, ::step])
        return image

    def stop(self) -> None:
        super().stop()
        try:
            self._sct.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Piste audio (microphone, écho annulé)
# --------------------------------------------------------------------------
class MicrophoneAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, device: int | None = None) -> None:
        super().__init__()
        self.samples = int(AUDIO_RATE * FRAME_MS / 1000)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pts = 0
        self.muted = False
        self._stream = sd.InputStream(
            device=device,
            channels=1,
            samplerate=AUDIO_RATE,
            dtype="int16",
            blocksize=self.samples,
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata, frames, time_info, status) -> None:  # thread audio
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._enqueue, indata.copy())

    def _enqueue(self, data: np.ndarray) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(data)

    async def recv(self) -> AudioFrame:
        if self._loop is None:
            self._loop = asyncio.get_event_loop()
        data = await self._queue.get()
        if self.muted:
            data = np.zeros(self.samples, dtype=np.int16)
        else:
            # Annulation d'écho + réduction de bruit.
            data = CANCELLER.process(data.reshape(-1))
        frame = AudioFrame.from_ndarray(data.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = AUDIO_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, AUDIO_RATE)
        self._pts += self.samples
        return frame

    def stop(self) -> None:
        super().stop()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Restitution audio
# --------------------------------------------------------------------------
class SpeakerSink:
    """Joue les trames audio reçues et alimente l'annuleur d'écho."""

    def __init__(self, device: int | None = None) -> None:
        self._resampler = AudioResampler(format="s16", layout="mono", rate=AUDIO_RATE)
        self._stream = sd.OutputStream(
            device=device,
            channels=1,
            samplerate=AUDIO_RATE,
            dtype="int16",
            blocksize=int(AUDIO_RATE * FRAME_MS / 1000),
        )
        self._stream.start()

    async def feed(self, frame: AudioFrame) -> None:
        for resampled in self._resampler.resample(frame):
            data = resampled.to_ndarray().reshape(-1)
            CANCELLER.push_reference(data)  # référence pour l'annulation d'écho
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._write, data)

    def _write(self, data: np.ndarray) -> None:
        try:
            self._stream.write(np.ascontiguousarray(data, dtype="int16"))
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Médias locaux
# --------------------------------------------------------------------------
class LocalMedia:
    """Regroupe la caméra, le micro et (au besoin) le partage d'écran."""

    def __init__(
        self,
        camera: str | None,
        microphone: int | None,
        profile: str = DEFAULT_PROFILE,
        screen_fps: int = 30,
    ) -> None:
        self.profile = profile
        self.screen_fps = screen_fps
        self._camera = CameraVideoTrack(camera, profile) if camera else TestVideoTrack(profile=profile)
        self._mic = MicrophoneAudioTrack(microphone)
        self._video_relay = MediaRelay()
        self._audio_relay = MediaRelay()
        self._screen = None
        self._screen_relay = MediaRelay()

    def new_video(self) -> MediaStreamTrack:
        return self._video_relay.subscribe(self._camera)

    def new_audio(self) -> MediaStreamTrack:
        return self._audio_relay.subscribe(self._mic)

    def preview_video(self) -> MediaStreamTrack:
        return self._video_relay.subscribe(self._camera)

    # --- Partage d'écran --------------------------------------------------
    def start_screen(self, monitor: int = 1) -> MediaStreamTrack:
        self.stop_screen()
        self._screen = ScreenVideoTrack(monitor=monitor, fps=self.screen_fps)
        return self._screen_relay.subscribe(self._screen)

    def screen_video(self) -> MediaStreamTrack | None:
        if self._screen is None:
            return None
        return self._screen_relay.subscribe(self._screen)

    def stop_screen(self) -> None:
        if self._screen is not None:
            try:
                self._screen.stop()
            except Exception:
                pass
            self._screen = None

    def set_audio_enabled(self, enabled: bool) -> None:
        self._mic.muted = not enabled

    def set_video_enabled(self, enabled: bool) -> None:
        self._camera.muted = not enabled

    def video_size(self) -> tuple[int, int]:
        width, height, _ = profile_for(self.profile)
        return width, height

    def stop(self) -> None:
        self.stop_screen()
        for track in (self._camera, self._mic):
            try:
                track.stop()
            except Exception:
                pass


def frame_to_rgb(frame) -> np.ndarray:
    """Convertit une trame vidéo en tableau RGB (H, W, 3) uint8."""
    return frame.to_ndarray(format="rgb24")
