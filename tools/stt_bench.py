"""Spracherkennung vergleichen: Zeit + Text je Variante (faster-whisper Modell/Threads/Beam, optional Parakeet).
Audio: WAV-Dateien (16 kHz mono) aus einem Ordner, oder – ohne Ordner – Sätze, die Piper (Thorsten) spricht.
Aufruf: python tools/stt_bench.py [ordner_mit_wavs]   (läuft nur auf der CPU, schaltet nichts)"""
import io
import pathlib
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import voice as v  # noqa: E402

SENTENCES = [
    "Hey Jarvis, spiel Avril Lavigne, Sk8er Boi.",
    "Hey Jarvis, spiel im Esszimmer über Qobuz die drei Fragezeichen Kids, Folge 104.",
    "Hey Jarvis, mach mal die Rollos zu.",
    "Hey Jarvis, starte den Film Zurück in die Zukunft drei.",
    "Hey Jarvis, spiel was von Imagine Dragons.",
    "Hey Jarvis, wie warm ist es im Bad oben?",
]
VARIANTS = [  # (Modell, Threads, Beam)
    ("small", 4, 1),          # bisher
    ("small", 10, 5),
    ("large-v3-turbo", 10, 5),
]


def to16k(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as w:
        sr, n = w.getframerate(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)
    if sr != 16000:                                   # einfaches Umrechnen auf 16 kHz (Piper liefert 22,05 kHz)
        a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr)), np.arange(len(a)), a)
    return a.astype(np.int16)


def samples() -> list:
    if len(sys.argv) > 1:
        return [(f.name, to16k(f.read_bytes())) for f in sorted(pathlib.Path(sys.argv[1]).glob("*.wav"))]
    sp = v.Speaker()
    tcfg = {"voice": "de_DE-thorsten-medium", "speed": 1.0, "volume": 1.0}
    return [(t, to16k(sp.wav(t, tcfg))) for t in SENTENCES]


def main():
    from faster_whisper import WhisperModel
    data = samples()
    cfg = v.load_cfg()
    for model, threads, beam in VARIANTS:
        t0 = time.time()
        m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads, download_root=str(v.MODEL_DIR))
        load = time.time() - t0
        m.transcribe(np.zeros(16000, dtype=np.float32), language="de", beam_size=beam)   # aufwärmen
        tot, audio_s = 0.0, 0.0
        print(f"\n=== {model} · {threads} Threads · Beam {beam} (Laden {load:.1f} s)")
        for name, pcm in data:
            eng = v.VoiceEngine({**cfg, "stt": {**cfg["stt"], "model": model, "threads": threads, "beam": beam}})
            eng._stt = m
            t = time.time()
            txt = eng.stt(pcm.tobytes(), {"rooms": ["Esszimmer / Küche", "Bad oben", "Bad mitte", "Wohnzimmer"],
                                          "artists": ["Imagine Dragons", "Adele", "Sting"]})
            dt = time.time() - t
            tot, audio_s = tot + dt, audio_s + len(pcm) / 16000
            print(f"   {dt:5.2f} s  {txt}")
        print(f"   Σ {tot:.1f} s für {audio_s:.1f} s Audio (Echtzeitfaktor {tot / audio_s:.2f})")


if __name__ == "__main__":
    main()
