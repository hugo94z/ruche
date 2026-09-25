"""Vérification de l'annulation d'écho (AEC) avant de s'engager.

On simule un cas d'école : ce qu'on joue dans le haut-parleur (référence) part
dans le micro par un trajet retardé et atténué (l'écho), auquel s'ajoute la
voix locale. On mesure ensuite l'atténuation de l'écho (ERLE).

    python tools/aec_poc.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

RATE = 48000
FRAME = 480          # 10 ms
DELAY = 240          # 5 ms de trajet
GAIN = 0.6           # atténuation de l'écho
ECHO_FRAMES = 100    # phase « écho seul »
TALK_FRAMES = 100    # phase « double parole »


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64))))) if len(x) else 0.0


def build_signals() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    total = (ECHO_FRAMES + TALK_FRAMES) * FRAME

    # Référence : ce que le haut-parleur joue (bruit large bande = pire cas).
    far = (rng.standard_normal(total) * 3000).astype(np.int16)

    # Trajet d'écho : retard + atténuation.
    echo = np.zeros(total, dtype=np.float64)
    echo[DELAY:] = far[:-DELAY].astype(np.float64) * GAIN

    # Voix locale : uniquement pendant la phase de double parole.
    near = np.zeros(total, dtype=np.float64)
    start = ECHO_FRAMES * FRAME
    t = np.arange(total - start) / RATE
    near[start:] = np.sin(2 * np.pi * 650 * t) * 2500

    mic = (near + echo).astype(np.int16)
    return far, mic, echo.astype(np.int16)


def run(order: str) -> dict:
    from pyaec import Aec

    far, mic, echo = build_signals()
    aec = Aec(FRAME, filter_length=2048, sample_rate=RATE, enable_preprocess=False)

    out = []
    for i in range(0, len(far) - FRAME, FRAME):
        a, b = mic[i : i + FRAME].copy(), far[i : i + FRAME].copy()
        rec, ref = (a, b) if order == "mic,ref" else (b, a)
        res = aec.cancel_echo(rec, ref)
        arr = np.asarray(res, dtype=np.float64)
        out.append(arr if arr.size == FRAME else np.zeros(FRAME))
    processed = np.concatenate(out)

    e0, e1 = 0, ECHO_FRAMES * FRAME
    before = rms(mic[e0:e1])
    after = rms(processed[e0:e1])
    erle = 20 * np.log10(before / after) if after > 0 else float("inf")

    return {
        "order": order,
        "erle_db": erle,
        "echo_before": before,
        "echo_after": after,
    }


def main() -> int:
    print("Simulation : écho = référence retardée de 5 ms et atténuée de 40 %\n")
    for order in ("mic,ref", "ref,mic"):
        try:
            r = run(order)
            verdict = "UTILE" if r["erle_db"] >= 6 else "INSUFFISANT"
            print(
                f"  ordre {order:8s} : ERLE = {r['erle_db']:6.1f} dB  "
                f"(avant {r['echo_before']:7.0f} → après {r['echo_after']:7.0f})  → {verdict}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ordre {order:8s} : ÉCHEC ({type(exc).__name__}: {exc})")
    print("\nRepère : au-delà de ~10 dB, l'écho devient nettement moins gênant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
