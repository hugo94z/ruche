"""Capture et restitution média : caméra, micro, haut-parleur.

Les pistes locales sont relayées (`MediaRelay`) pour pouvoir être consommées à
la fois par l'aperçu local et par chaque connexion du maillage.
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

try:  # capture d'écran (partage)
    import mss
except ImportError:  # pragma: no cover
    mss = None  # type: ignore[assignment]

log = logging.getLogger("ruche.media")

VIDEO_W, VIDEO_H, VIDEO_FPS = 640, 480, 24
AUDIO_RATE = 48000
FRAME_MS = 20


def black_frame(pts: int = 0, fps: int = VIDEO_FPS) -> VideoFrame:
    """Trame vidéo noire (utilisée quand la caméra est coupée)."""
    img = np.zeros((VIDEO_H, VIDEO_W, 3), dtype=np.uint8)
    frame = VideoFrame.from_ndarray(img, format="rgb24")
    frame.pts = pts
    frame.time_base = fractions.Fraction(1, fps)
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


# --------------------------------------------------------------------------
# Pistes vidéo
# --------------------------------------------------------------------------
class CameraVideoTrack(VideoStreamTrack):
    """Flux de la webcam, lu via FFmpeg (dshow sur Windows)."""

    kind = "video"

    def __init__(self, device: str) -> None:
        super().__init__()
        self._device = device
        self._container = None
        self._stream = None
        self._pts = 0
        self._executor = None
        self.muted = False
        self._open()

    def _open(self) -> None:
        self._container = av.open(
            f"video={self._device}",
            format="dshow",
            options={"framerate": str(VIDEO_FPS), "video_size": f"{VIDEO_W}x{VIDEO_H}"},
        )
        self._stream = self._container.streams.video[0]
        self._stream.thread_type = "AUTO"

    async def recv(self) -> VideoFrame:
        loop = asyncio.get_event_loop()
        frame = await loop.run_in_executor(self._executor, self._read)
        if self.muted:
            frame = black_frame(self._pts, VIDEO_FPS)
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, VIDEO_FPS)
        self._pts += 1
        return frame

    def _read(self) -> VideoFrame:
        try:
            frame = next(self._container.decode(self._stream))
        except (StopIteration, av.error.EOFError):
            self._open()
            frame = next(self._container.decode(self._stream))
        return frame.reformat(VIDEO_W, VIDEO_H, "yuv420p")

    def stop(self) -> None:
        super().stop()
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass


class TestVideoTrack(VideoStreamTrack):
    """Mire animée de secours (quand aucune caméra n'est disponible)."""

    kind = "video"

    def __init__(self, label: str = "Ruche") -> None:
        super().__init__()
        self._label = label
        self._pts = 0
        self._start = time.monotonic()
        self.muted = False
        width = VIDEO_W
        self._ramp_x = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
        self._ramp_y = np.linspace(0, 255, VIDEO_H, dtype=np.uint8)[:, None]

    async def recv(self) -> VideoFrame:
        await asyncio.sleep(1 / VIDEO_FPS)
        if self.muted:
            frame = black_frame(self._pts, VIDEO_FPS)
        else:
            img = np.zeros((VIDEO_H, VIDEO_W, 3), dtype=np.uint8)
            img[:, :, 0] = self._ramp_x
            img[:, :, 1] = self._ramp_y
            bar = int((time.monotonic() - self._start) * 120) % VIDEO_W
            img[:, max(0, bar - 12) : bar, 2] = 255
            frame = VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts = self._pts
            frame.time_base = fractions.Fraction(1, VIDEO_FPS)
        self._pts += 1
        return frame


# --------------------------------------------------------------------------
# Piste audio (microphone)
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
        frame = AudioFrame.from_ndarray(
            data.reshape(1, -1), format="s16", layout="mono"
        )
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
    """Joue les trames audio reçues sur la sortie choisie."""

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
# Partage d'écran
# --------------------------------------------------------------------------
class ScreenVideoTrack(VideoStreamTrack):
    """Capture de l'écran, redimensionnée pour rester fluide à l'encodage."""

    kind = "video"

    def __init__(self, monitor: int = 1, fps: int = 10, max_width: int = 1280) -> None:
        super().__init__()
        if mss is None:
            raise RuntimeError("Le module 'mss' est nécessaire au partage d'écran.")
        self._monitor = monitor
        self._fps = fps
        self._max_width = max_width
        self._pts = 0
        self.muted = False
        self._lock = threading.Lock()
        self._sct = mss.mss()

    async def recv(self) -> VideoFrame:
        await asyncio.sleep(1 / self._fps)
        loop = asyncio.get_event_loop()
        image = await loop.run_in_executor(None, self._grab)
        frame = VideoFrame.from_ndarray(image, format="bgra")
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, self._fps)
        self._pts += 1
        return frame

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
# Médias locaux
# --------------------------------------------------------------------------
class LocalMedia:
    """Regroupe la caméra et le micro d'un participant."""

    def __init__(self, camera: str | None, microphone: int | None) -> None:
        self._camera = CameraVideoTrack(camera) if camera else TestVideoTrack()
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
        self._screen = ScreenVideoTrack(monitor=monitor)
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
