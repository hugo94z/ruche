"""Test de la couche média : cadence réelle, profils, écran, annulation d'écho.

    python tools/media_test.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

from app.core.audio_fx import EchoCanceller  # noqa: E402
from app.core.media import (  # noqa: E402
    VIDEO_PROFILES,
    ScreenVideoTrack,
    TestVideoTrack,
    frame_to_rgb,
    list_monitors,
    profile_for,
)


async def measure_pacing(fps: int, seconds: float = 3.0) -> float:
    track = TestVideoTrack(profile="hd" if fps == 30 else "moyenne")
    # Force la cadence voulue pour la mesure.
    track.fps = fps
    track._start = None
    track._pts = 0
    count = 0
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        await track.recv()
        count += 1
    elapsed = time.monotonic() - start
    track.stop()
    return count / elapsed


def echo_reduction(order_ok: bool = True) -> float:
    rate, frame, delay, gain = 48000, 480, 240, 0.6
    rng = np.random.default_rng(3)
    total = 400 * frame

    far = (rng.standard_normal(total) * 3000).astype(np.int16)
    echo = np.zeros(total)
    echo[delay:] = far[:-delay].astype(float) * gain
    near = np.zeros(total)
    t = np.arange(total) / rate
    near[200 * frame :] = np.sin(2 * np.pi * 650 * t[: total - 200 * frame]) * 2500
    mic = (near + echo).astype(np.int16)

    aec = EchoCanceller()
    if not aec.available:
        return 0.0

    out = []
    for i in range(0, total - frame, frame):
        aec.push_reference(far[i : i + frame].copy())
        out.append(aec.process(mic[i : i + frame].copy()))
    processed = np.concatenate(out).astype(float)

    def rms(x):
        return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0

    before = rms(mic[: 200 * frame].astype(float))
    after = rms(processed[: 200 * frame])
    return 20 * np.log10(before / after) if after > 0 else 0.0


async def main() -> int:
    results: list[tuple[str, bool]] = []

    results.append(("Profils cohérents", profile_for("haute") == VIDEO_PROFILES["haute"]))
    results.append(("Profil inconnu → défaut", profile_for("zzz") == VIDEO_PROFILES["haute"]))

    t = TestVideoTrack(profile="haute")
    frame = await t.recv()
    rgb = frame_to_rgb(frame)
    t.stop()
    results.append(("Mire au format demandé", rgb.shape == (480, 640, 3)))
    print(f"  mire 640×480 : {rgb.shape}")

    measured = await measure_pacing(30)
    ok = measured >= 28.0
    print(f"  cadence mesurée à 30 fps visés : {measured:.1f} fps")
    results.append(("Cadence réelle tenue (≥ 28 fps)", ok))

    monitors = list_monitors()
    results.append(("Écrans détectés", len(monitors) >= 1))
    print(f"  écrans : {monitors}")

    try:
        screen = ScreenVideoTrack(fps=15)
        shot = frame_to_rgb(await screen.recv())
        screen.stop()
        results.append(("Capture d'écran fonctionnelle", shot.ndim == 3 and shot.shape[2] == 3))
        print(f"  capture écran : {shot.shape}")
    except Exception as exc:  # noqa: BLE001
        print(f"  capture écran ÉCHEC : {exc}")
        results.append(("Capture d'écran fonctionnelle", False))

    erle = echo_reduction()
    print(f"  atténuation d'écho : {erle:.1f} dB")
    results.append(("Écho atténué (≥ 6 dB)", erle >= 6.0))

    print("\nRésultats :")
    for label, passed in results:
        print(f"  {'✓' if passed else '✗'} {label}")
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
