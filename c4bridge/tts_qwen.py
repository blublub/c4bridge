"""Qwen3-TTS als kleiner HTTP-Dienst für c4voice (eigener Prozess, eigenes venv – wie stt_gpu.py).

Läuft mit dem Python des Qwen3-TTS-GGUF-Projekts (Talker/Predictor per llama.cpp/Vulkan, Decoder ONNX auf der CPU):
    <qwen-dir>/.venv/Scripts/python.exe tts_qwen.py --dir <qwen-dir> --port 8096 --ref stimme.wav --ref-text "…"
                                                       [--model model-base | model-base-small]

GET  /health  → 200 + {"model": …}, sobald Modell und Stimme geladen sind
POST /tts     {"text": "...", "lang": "german", "ref": "…wav", "ref_text": "…"} → audio/wav (24 kHz mono, 16 bit)
              ref/ref_text optional: andere Stimmvorlage → wird einmal eingelesen und bleibt (Wechsel im Admin ohne Neustart)
Ein Satz nach dem anderen (Lock); c4voice teilt längere Antworten in Sätze und schickt jeden, sobald er fertig ist.
"""
import argparse
import io
import json
import os
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np


class _Sink:
    """Ersetzt die Abspiel-Warteschlange des Projekts: Ton nicht am PC abspielen, nur sammeln (Ergebnis kommt per HTTP)."""
    def put(self, *_a, **_k):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--port", type=int, default=8096)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--ref-text", default="")
    ap.add_argument("--model", default="model-base")      # Unterordner: model-base (1.7B) / model-base-small (0.6B)
    a = ap.parse_args()

    os.chdir(a.dir)
    sys.path.insert(0, a.dir)
    from qwen3_tts_gguf.inference import TTSEngine, TTSConfig

    t0 = time.time()
    engine = TTSEngine(model_dir=a.model, onnx_provider="CPU", verbose=False)
    if getattr(engine, "decoder", None) is not None and hasattr(engine.decoder, "play_q"):
        engine.decoder.play_q = _Sink()
    stream = engine.create_stream()
    stream.set_voice(a.ref, a.ref_text or None)
    lock = threading.Lock()
    state = {"ready": False, "ref": os.path.abspath(a.ref)}

    def synth(text: str, lang: str, ref: str = "", ref_text: str = "") -> bytes:
        cfg = TTSConfig(max_steps=min(600, 40 + 12 * len(text) // 5), temperature=0.7, sub_temperature=0.7,
                        streaming=True)
        with lock:
            if ref and os.path.abspath(ref) != state["ref"] and os.path.isfile(ref):
                t = time.time()
                stream.reset()                     # KV-Zwischenspeicher der alten Stimme leeren (sonst leer/zäh)
                if stream.set_voice(ref, ref_text or None):
                    state["ref"] = os.path.abspath(ref)
                    print(f"stimme {os.path.basename(ref)} in {time.time() - t:.1f}s", flush=True)
            r = stream.clone(text=text, language=lang, config=cfg)
            stream.join()
            if len(text) > 3 and (r is None or r.audio is None or len(r.audio) < 2400):
                # kein Ton trotz Text (Modell beendet sofort – passiert v. a. bei langen Vorlagen): einmal neu würfeln
                print(f"leer, wiederhole: {text[:40]!r}", flush=True)
                r = stream.clone(text=text, language=lang, config=cfg)
                stream.join()
        audio = np.asarray(r.audio if r is not None and r.audio is not None else np.zeros(2400), dtype=np.float32)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        return buf.getvalue()

    synth("Hallo.", "german")                      # aufwärmen (erster Satz baut Shader/Graph)
    state["ready"] = True
    print(f"bereit nach {time.time() - t0:.1f}s auf Port {a.port} ({a.model})", flush=True)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            ok = self.path == "/health" and state["ready"]
            body = json.dumps({"model": a.model}).encode() if ok else b""
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/tts":
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                text = str(body.get("text") or "").strip()[:600]
                if not text:
                    raise ValueError("kein Text")
                t = time.time()
                data = synth(text, str(body.get("lang") or "german"), str(body.get("ref") or ""),
                             str(body.get("ref_text") or ""))
                print(f"tts {time.time() - t:.2f}s {len(text)} Zeichen", flush=True)
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:  # noqa: BLE001
                msg = str(e).encode("utf-8", "replace")
                self.send_response(500)
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)

    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
