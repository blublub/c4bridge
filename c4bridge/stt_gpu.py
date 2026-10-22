"""Spracherkennung auf der GRAFIKKARTE: Whisper large-v3-turbo deutsch (primeline) über DirectML – eigener kleiner Dienst.

Warum ein eigenes Programm: Die GPU-Fassung von onnxruntime (onnxruntime-directml) und die CPU-Fassung, die c4voice für
Canary/Parakeet/Piper/openWakeWord nutzt, lassen sich nicht in EINER Python-Umgebung haben. Deshalb läuft dieser Dienst
in einem eigenen venv (c4bridge/.venv-gpu) – wie Ollama ein eigenes Programm ist. c4voice startet ihn bei Bedarf und
schickt ihm die Aufnahme; antwortet er nicht, nimmt c4voice Canary (Rückfall).

Modell: data/models/whisper-turbo-german-onnx (onnx-community/whisper-large-v3-turbo-german-ONNX, fp16).
Hinweistext: wie bei faster-whisper – Text in Whisper-Wortbausteine zerlegen und vor die Steuerzeichen setzen
(<|startofprev|> Hinweis … <|startoftranscript|><|de|><|transcribe|><|notimestamps|>). Was Whisper kennen soll, steht
NICHT hier, sondern in wortschatz.py (siehe WORTSCHATZ.md) – c4voice schickt den fertigen Hinweistext mit.
Nur Whisper liest den Hinweis; onnx-asr kann das selbst nicht → `recognize()` unten (eigener Code, onnx-asr 0.12.0 fest,
weil er interne Teile von onnx-asr nutzt: _encode, _decoding, _decode_tokens, _transcribe_input, _tokens).

Start (macht c4voice): .venv-gpu\\Scripts\\python.exe c4bridge\\stt_gpu.py [--port 8091]
Schnittstelle (nur 127.0.0.1):
  GET  /health → {"ok": true, "provider": "DmlExecutionProvider"}
  POST /stt    {"pcm16_b64": "...", "language": "de", "prompt": "..."} → {"text": "...", "ms": 412, "prompt_tokens": 120}
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "data" / "models" / "whisper-turbo-german-onnx"
MAX_PROMPT = 223                       # Whisper: höchstens n_text_ctx/2 − 1 Bausteine Vorgabe (wie faster-whisper)


def log(event: str, **kw) -> None:
    print(json.dumps({"ts": round(time.time(), 1), "event": event, **kw}, ensure_ascii=False), flush=True)


class Engine:
    def __init__(self):
        import onnx_asr
        import onnxruntime as ort
        from tokenizers import Tokenizer
        ort.set_default_logger_severity(3)     # nur Fehler – sonst je Teilnetz „nodes not assigned to DML“ (harmlos)
        t = time.time()
        self.provider = "DmlExecutionProvider"
        try:
            m = onnx_asr.load_model("onnx-community/whisper-large-v3-turbo", str(MODEL_DIR), quantization="fp16",
                                    providers=["DmlExecutionProvider"])
        except Exception as e:  # noqa: BLE001
            log("gpu_unavailable", error=f"{type(e).__name__}: {e}")
            raise
        self.asr = m.asr                       # WhisperHf (onnx-asr) – interne Teile, siehe Kopf
        self.adapter = m                       # für die Audio-Vorbereitung genau wie onnx-asr selbst (read_wav_files + resampler)
        self.tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
        self.sop = self.asr._tokens["<|startofprev|>"]
        self.recognize(np.zeros(16000, dtype=np.float32), "de", "")     # aufwärmen (GPU-Shader kompilieren)
        log("ready", provider=self.provider, load_s=round(time.time() - t, 1))

    def recognize(self, audio: np.ndarray, language: str, prompt: str) -> tuple[str, int]:
        from onnx_asr.utils import read_wav_files
        a = self.asr
        # Audio so vorbereiten wie onnx-asr selbst (direkt übergeben stürzte die Vorverarbeitung mit access violation ab)
        waves, lens = self.adapter.resampler(*read_wav_files(np.ascontiguousarray(audio, dtype=np.float32), 16000, None))
        enc = a._encode(waves, lens)
        head = a._transcribe_input.copy()                       # [[sot, Sprache, transcribe, notimestamps]]
        if language:
            head[:, 1] = a._tokens[f"<|{language}|>"]
        ids = self.tok.encode(" " + prompt.strip(), add_special_tokens=False).ids[-MAX_PROMPT:] if prompt.strip() else []
        tokens = np.hstack([np.array([[self.sop, *ids]], dtype=np.int64), head]) if ids else head
        out = a._decoding(enc, tokens.astype(np.int64))
        text = a._decode_tokens(out[0, tokens.shape[1]:]).text      # nur das Neue – der Hinweis gehört nicht dazu
        return text.strip(), len(ids)


class Handler(BaseHTTPRequestHandler):
    engine: Engine

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send(200, {"ok": True, "provider": self.engine.provider})
        else:
            self._send(404, {"error": "unbekannt"})

    def do_POST(self):  # noqa: N802
        if self.path != "/stt":
            self._send(404, {"error": "unbekannt"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            pcm = np.frombuffer(base64.b64decode(body["pcm16_b64"]), dtype=np.int16).astype(np.float32) / 32768.0
            t = time.time()
            text, n = self.engine.recognize(pcm, str(body.get("language") or "de"), str(body.get("prompt") or ""))
            self._send(200, {"text": text, "ms": round((time.time() - t) * 1000), "prompt_tokens": n})
        except Exception as e:  # noqa: BLE001
            log("stt_error", error=f"{type(e).__name__}: {e}")
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *args):          # kein Zugriffs-Log je Satz
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8091)
    args = ap.parse_args()
    Handler.engine = Engine()
    srv = HTTPServer(("127.0.0.1", args.port), Handler)          # nur lokal; Anfragen nacheinander (eine GPU)
    log("listen", port=args.port)
    srv.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("fatal", error=f"{type(e).__name__}: {e}")
        sys.exit(1)
