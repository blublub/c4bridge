"""Thread-Skalierung von faster-whisper messen: ein langer Satz (Piper spricht ihn), je Thread-Zahl 3 Durchläufe.
Aufruf: python tools/stt_threads.py [modell] [beam]"""
from pathlib import Path
import io
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import voice as v  # noqa: E402

MODEL = sys.argv[1] if len(sys.argv) > 1 else "large-v3-turbo"
BEAM = int(sys.argv[2]) if len(sys.argv) > 2 else 5
TEXT = "Hey Jarvis, spiel im Esszimmer mal über Qobuz, die drei Fragezeichen Kids, letzte Folge 104."


def main():
    from faster_whisper import WhisperModel
    data = v.Speaker().wav(TEXT, {"voice": "de_DE-thorsten-medium", "speed": 1.0, "volume": 1.0})
    with wave.open(io.BytesIO(data)) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr)), np.arange(len(a)), a).astype(np.float32)
    print(f"{MODEL} · Beam {BEAM} · Satz {len(a) / 16000:.1f} s")
    for threads in (0, 4, 8, 10, 12, 16):
        m = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=threads, download_root=str(v.MODEL_DIR))
        m.transcribe(np.zeros(16000, dtype=np.float32), language="de", beam_size=BEAM)   # aufwärmen
        times, txt = [], ""
        for _ in range(3):
            t = time.time()
            seg, _ = m.transcribe(a, language="de", beam_size=BEAM, condition_on_previous_text=False, vad_filter=True)
            txt = " ".join(s.text.strip() for s in seg)
            times.append(time.time() - t)
        label = "Standard (0)" if threads == 0 else f"{threads} Threads"
        print(f"   {label:13} {min(times):5.2f} s (Ø {sum(times) / 3:5.2f})   {txt}")
        del m


if __name__ == "__main__":
    main()
