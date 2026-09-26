"""Traitement audio temps réel : annulation d'écho et réduction de bruit.

L'annulation d'écho a besoin de **deux signaux** :
  * ce que le haut-parleur joue (la « référence ») ;
  * ce que le micro capte (qui contient l'écho de la référence).

On garde donc un tampon tournant de la référence, alimenté par la sortie audio
(`SpeakerSink`), et on y soustrait le micro. Le filtre adaptatif (NLMS) apprend
le trajet acoustique entre haut-parleur et micro.

La réduction de bruit est volontairement simple (soustraction spectrale sur
trames courtes) : c'est peu coûteux et ça atténue bien les bruits continus
(ventilateur, souffle).
"""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger("ruche.audio")

SAMPLE_RATE = 48000
FRAME = 480          # 10 ms
AEC_FILTER = 2048    # ~43 ms de trajet acoustique couvert


class EchoCanceller:
    """Référence partagée entre la lecture et la capture."""

    def __init__(self, frame: int = FRAME, rate: int = SAMPLE_RATE) -> None:
        self.frame = frame
        self.rate = rate
        self._lock = threading.Lock()
        self._reference = np.zeros(frame * 8, dtype=np.int16)
        self._aec = None
        self._aec_cls = None
        self._aec_frame = 0
        self.available = False
        self._try_load()

    def _try_load(self) -> None:
        """Charge la bibliothèque ; l'annuleur est instancié à la première trame."""
        try:
            from pyaec import Aec

            self._aec_cls = Aec
            self.available = True
            log.info("annulation d'écho disponible")
        except Exception as exc:  # DLL absente, plateforme non supportée…
            self._aec_cls = None
            self.available = False
            log.warning("annulation d'écho indisponible : %s", exc)

    def _ensure_aec(self, size: int) -> None:
        """Instancie l'annuleur pour la taille de trame réellement reçue.

        pyaec est paramétré pour une taille de trame fixe et ``cancel_echo``
        refuse deux tampons de longueurs différentes. Or le micro livre des
        trames de 20 ms alors que ``frame`` vaut 10 ms : sans cet ajustement,
        chaque appel lèverait une ``ValueError`` avalée en silence et l'écho ne
        serait jamais annulé.
        """
        if self._aec is not None and self._aec_frame == size:
            return
        try:
            self._aec = self._aec_cls(
                size, filter_length=AEC_FILTER, sample_rate=self.rate,
                enable_preprocess=True,
            )
            self._aec_frame = size
            self.available = True
        except Exception as exc:
            self._aec = None
            self.available = False
            log.warning("annulation d'écho inactive : %s", exc)

    # --- Référence --------------------------------------------------------
    def push_reference(self, samples: np.ndarray) -> None:
        """Appelé par la lecture audio : ce qui sort des haut-parleurs."""
        with self._lock:
            n = len(samples)
            if n >= len(self._reference):
                self._reference[:] = samples[-len(self._reference) :]
                return
            self._reference = np.roll(self._reference, -n)
            self._reference[-n:] = samples

    # --- Traitement du micro ---------------------------------------------
    def process(self, mic_frame: np.ndarray) -> np.ndarray:
        """Retire l'écho du micro puis atténue le bruit continu."""
        if not self.available or self._aec_cls is None:
            return self._noise_gate(mic_frame)
        with self._lock:
            n = len(mic_frame)
            if n > len(self._reference):
                # Trame micro plus longue que l'historique : on l'agrandit.
                grown = np.zeros(max(n, 2 * len(self._reference)), dtype=np.int16)
                grown[-len(self._reference) :] = self._reference
                self._reference = grown
            # pyaec exige une référence de même longueur que la trame du micro.
            reference = self._reference[-n:].copy()
        self._ensure_aec(len(reference))
        if self._aec is None:
            return self._noise_gate(mic_frame)
        try:
            cleaned = np.asarray(
                self._aec.cancel_echo(
                    np.ascontiguousarray(mic_frame, dtype=np.int16),
                    np.ascontiguousarray(reference, dtype=np.int16),
                ),
                dtype=np.int16,
            )
            if cleaned.size != mic_frame.size:
                cleaned = mic_frame
        except Exception:
            cleaned = mic_frame
        return self._noise_gate(cleaned)

    # --- Réduction de bruit ----------------------------------------------
    def _noise_gate(self, frame: np.ndarray) -> np.ndarray:
        """Atténue les composantes très faibles (souffle) et les graves."""
        signal = frame.astype(np.float32)
        spectrum = np.fft.rfft(signal)
        magnitude = np.abs(spectrum)
        if magnitude.size:
            floor = np.percentile(magnitude, 25) * 1.6
            gain = np.clip((magnitude - floor) / (magnitude + 1e-6), 0.0, 1.0)
            spectrum = spectrum * gain
        cleaned = np.fft.irfft(spectrum, n=signal.size)
        # Coupe les fréquences très basses (< 120 Hz) : rumble, vibrations.
        return np.clip(cleaned, -32768, 32767).astype(np.int16)


# Instance partagée : un seul micro et un seul haut-parleur par machine.
CANCELLER = EchoCanceller()
