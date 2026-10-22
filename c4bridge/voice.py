"""c4voice – Sprachdienst der c4bridge (Kindprozess, von der Bridge beaufsichtigt).

Datenfluss:  Mikrofon-Client (WebSocket /mic, PCM16/16 kHz/mono)
             → openWakeWord (CPU, optional) → VAD-Segmentierung
             → faster-whisper STT (CPU) → Ollama (URL konfigurierbar, z. B. anderer PC)
             → Tool-Ausführung über die Bridge-REST (dry_run = nur melden)
             → Antwort an den Client (Text + optional Sprachausgabe per Piper, WAV über denselben WebSocket)

Start:  python -m c4bridge.voice          (normalerweise startet die Bridge diesen Prozess)
Konfiguration: data/voice.json (per Bridge-Weboberfläche pflegbar)
"""
import asyncio
import contextvars
import hmac
import io
import json
import os
import re
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import aiohttp
from aiohttp import web, WSMsgType

from .dialog import Dialog, generic_named
from . import wortschatz                 # Hinweistext + Hörfehler-Korrekturen: EINE Stelle (siehe WORTSCHATZ.md)
from . import voice_en as en             # Englisch (voice.json "lang": "en"): Ausgabe, Prompt, Kurzbefehle

DATA_DIR = Path(os.environ.get("C4BRIDGE_DATA", Path(__file__).resolve().parent.parent / "data"))
CFG_FILE = DATA_DIR / "voice.json"
ZULETZT_FILE = DATA_DIR / "wortschatz_zuletzt.json"   # per Sprache gespielte Namen → Hinweistext (wortschatz.py, Teil f)
MODEL_DIR = DATA_DIR / "models"          # Whisper-Modelle bleiben im Projektverzeichnis
SEEN_FILE = DATA_DIR / "mic_seen.json"   # gesehene Mikrofone überleben Dienst-Neustarts
TTS_DIR = DATA_DIR / "tts"               # Piper-Stimmen (<name>.onnx + <name>.onnx.json)

# Zugang: c4voice lauscht nur auf localhost und nimmt nur Requests mit dem Token der Bridge an.
# Mikrofone verbinden sich über die Bridge (/api/voice/mic, mit Login), nie direkt.
HOST = os.environ.get("C4VOICE_HOST", "127.0.0.1")
TOKEN = os.environ.get("C4VOICE_TOKEN", "")
TOKEN_HEADER = "X-C4-Service"

DEFAULT_CFG = {
    "enabled": False,                    # Autostart + Watchdog (Bridge steuert)
    "lang": "de",                        # Sprache des Assistenten: de | en (voice_en.py)
    "port": 8090,
    "wake": {"enabled": True, "model": "hey_jarvis", "threshold": 0.5},
    "stt": {"model": "base", "language": "de",
            "hint": "Deutsche Smart-Home-Sprachbefehle: Licht, Rollladen, Jalousie, Thermostat, Musik, Raum."},
    "llm": {"url": "http://localhost:11434", "model": "qwen3.5:9b", "timeout_s": 90},
    "bridge_url": "http://127.0.0.1:8080",
    "dry_run": True,                     # True = Entscheidungen ja, Haus-Aktionen NEIN
    "log_commands": True,                # jeden echten Befehl nach data/train/befehle.jsonl (Sammlung fürs Feintuning)
    # Sprachausgabe (Piper, CPU): mode full = ganze Antwort, short = nur „Okay" bei Erfolg (Fragen/Fehler
    # immer ganz); followup = nach einer Rückfrage 8 s ohne Wake-Word auf die Antwort hören
    # engine: piper (CPU, sofort) | pocket (Pocket TTS über llama.cpp/Vulkan, je Satz ~1 s, viele Stimmen)
    #         | qwen3 (Qwen3-TTS als eigener Dienst, GPU/Vulkan). Piper ist immer der Rückfall.
    "tts": {"enabled": False, "voice": "de_DE-thorsten-medium", "speed": 1.0, "volume": 1.0,
            "mode": "full", "followup": True, "engine": "piper",
            "pocket": {"voice": "eponine"},
            "qwen": {"dir": "", "port": 8096, "model": "model-base", "ref": "qwen_ref_ruhig.wav", "ref_text": "",
                     "lang": ""}},    # Sprache der Ausgabe (Qwen3: german, english, …); leer = wie der Assistent
    "mics": {},                          # Mikrofon-Name -> [Raum1, Raum2, Raum3] (Sprecherraum-Kontext)
    # Relative Helligkeits-Stufen (Admin → Config → Sprache pflegbar):
    #   up = % des WEGS bis 100 (anteilig heller); down = % vom AKTUELLEN Wert (relativ dunkler)
    "adjust": {"klein": {"up": 33, "down": 30},     # „ein bisschen“
               "mittel": {"up": 50, "down": 50},
               "gross": {"up": 100, "down": 60},    # „richtig hell“ / „gemütlich“
               "cozy_below": 30, "cozy_down": 25,   # schon ≤30 % → nur −25 %
               "min_level": 5},                     # dunkler endet nie unter diesem Pegel
    # Musik per Sprache: Lautstärke-Stufen „ein bisschen / normal / viel" + Obergrenze (Schutz vor „volle Pulle")
    "music": {"steps": {"klein": 5, "mittel": 10, "gross": 20}, "max_volume": 60},
    # Sprach-Erkennung (VAD): Pegel (RMS 16-bit), ab dem Sprache beginnt bzw. weitergeht – zusätzlich gilt immer
    # das Vielfache des Raumrauschens. Handy-Mikrofone liefern leise Pegel → eher niedrig lassen.
    "vad": {"start": 250, "keep": 200, "noise_x": 2.4},
    # Ducking: beim Wake-Word (bzw. Sprechtaste) die Musik in den Räumen des Mikrofons auf factor × Lautstärke
    # absenken (nie unter min_level), nach dem Befehl zurück; spätestens nach max_s (Bridge, /api/duck)
    "duck": {"enabled": True, "factor": 0.3, "min_level": 8, "max_s": 25},
}
_VAD = dict(DEFAULT_CFG["vad"])


def log(event: str, **kw) -> None:
    """Eine Statuszeile nach stdout – die Bridge liest sie für die Weboberfläche mit."""
    print(json.dumps({"ts": round(time.time(), 1), "event": event, **kw},
                     ensure_ascii=False), flush=True)


_CMD_LOCK = threading.Lock()


def command_log(rec: dict) -> None:
    """Befehl + Entscheidung als eine JSON-Zeile anhängen (data/train/befehle.jsonl) – Sammlung für ein späteres
    Feintuning. Nur lokal; Testläufe (simulate) landen hier nicht."""
    try:
        d = DATA_DIR / "train"
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False)
        with _CMD_LOCK, open(d / "befehle.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # noqa: BLE001
        log("command_log_error", error=str(e)[:200])


def deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_cfg() -> dict:
    try:
        file = json.loads(CFG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        file = {}
    cfg = deep_merge(DEFAULT_CFG, file)
    _VAD.update({k: float(v) for k, v in (cfg.get("vad") or {}).items() if isinstance(v, (int, float))})
    global LANG
    LANG = "en" if cfg.get("lang") == "en" else "de"
    return cfg


LANG = "de"     # aus load_cfg (ein c4voice-Prozess = eine Konfiguration)


def is_en() -> bool:
    return LANG == "en"


def out_text(text: str) -> str:
    """Rückmeldung für Anzeige, Sprache und Verlauf: intern bleibt alles deutsch, auf Englisch erst hier übersetzt."""
    return en.to_en(text) if is_en() else text


# --------------------------------------------------------------------------------------
# Sprachbausteine (alle CPU; Modelle werden lazy geladen, damit der Start schnell ist)
# --------------------------------------------------------------------------------------
def _kill_tree(pid: int) -> None:
    """Prozess samt Kindern beenden – das venv-python startet den echten Interpreter als Kind (terminate() träfe nur
    den Starter, der GPU-Dienst liefe weiter)."""
    import subprocess
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _port_owner(port: int) -> int | None:
    """PID des Prozesses, der auf 127.0.0.1:port lauscht (netstat) – oder None."""
    import subprocess
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:  # noqa: BLE001
        return None
    # Bytes selbst dekodieren: netstat schreibt in der OEM-Codepage („ABHÖREN“) – mit UTF-8-Modus sonst Absturz
    for line in (out or b"").decode("ascii", "replace").splitlines():
        f = line.split()
        # lauschend = Gegenstelle 0.0.0.0:0 – sprachunabhängig (deutsches Windows schreibt „ABHÖREN“ statt LISTENING)
        if (len(f) >= 5 and f[2] in ("0.0.0.0:0", "[::]:0") and f[1].rsplit(":", 1)[-1] == str(port)
                and f[4].isdigit()):
            return int(f[4]) or None
    return None


class VoiceEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._stt = None
        self._stt_lock = threading.Lock()   # zwei Mikrofone gleichzeitig: Modell nur einmal laden
        self._ow = None
        self._ow_failed = False
        self._wake_last_error = ""

    def _load_stt(self) -> None:
        """Erkennung laden bzw. neu laden, wenn Modell/Threads im Admin geändert wurden (ohne Dienst-Neustart).
        CPU: faster-whisper (CTranslate2); GPU: eigener Dienst (GPU_SERVICES)."""
        try:
            self.cfg["stt"] = {**self.cfg["stt"], **(load_cfg().get("stt") or {})}
        except Exception:  # noqa: BLE001
            pass
        s = self.cfg["stt"]
        key = (s.get("model"), int(s.get("threads") or 4))
        if self._stt is not None and getattr(self, "_stt_key", None) == key:
            return
        if getattr(self, "_stt_key", None) is not None:          # Wechsel im Admin: alten GPU-Dienst freigeben
            self._gpu_stop_others(key[0] if key[0] in self.GPU_SERVICES else None)
        self._stt = None
        log("model_load", part="stt", model=key[0], threads=key[1], beam=s.get("beam"))
        if key[0] in self.GPU_SERVICES:
            self._gpu_start(key[0])             # eigener Dienst – hier nur starten, nicht warten
            self._stt = "gpu"
        else:
            from faster_whisper import WhisperModel
            # cpu_threads: faster-whisper nimmt sonst nur 4 Threads – skaliert aber kaum (gemessen: 8 ≈ Optimum)
            # lokale Modelle (Ordner in data/models) unter einem Kurznamen; sonst lädt faster-whisper selbst
            local = {"large-v3-turbo-german": MODEL_DIR / "whisper-turbo-german"}.get(key[0])   # primeline-Feintuning
            self._stt = WhisperModel(str(local) if local else key[0], device="cpu", compute_type="int8",
                                     cpu_threads=key[1], download_root=str(MODEL_DIR))
        self._stt_key = key

    # ---- Whisper auf der Grafikkarte: eigener Dienst, c4voice startet ihn (siehe WORTSCHATZ.md) ------------------------
    #   whisper-vulkan: whisper.cpp (bin/whisper-vulkan/whisper-server.exe, Modell data/models/whisper-turbo-german-ggml),
    #                   eigenständiges Programm, braucht nur den Vulkan-Treiber – ~0,33 s je Satz
    #   whisper-gpu:    DirectML (c4bridge/stt_gpu.py im eigenen venv .venv-gpu, onnx-asr) – ~0,42 s je Satz
    # Gleiches Modell (primeline turbo deutsch), gleicher Hinweistext, gleicher Text.
    _ROOT = Path(__file__).resolve().parent.parent
    GPU_SERVICES = {
        "whisper-vulkan": {"port": 8092, "log": "stt_vulkan.log",
                           "cmd": [str(_ROOT / "bin" / "whisper-vulkan" / "whisper-server.exe"),
                                   "-m", str(MODEL_DIR / "whisper-turbo-german-ggml" / "ggml-large-v3-turbo-german-q8_0.bin"),
                                   "-l", "de", "-nt", "--host", "127.0.0.1", "--port", "8092"]},
        # mehrsprachiges Original (OpenAI large-v3-turbo): englische Namen/Titel im deutschen Satz besser (Test 19.10.:
        # 33/36 Sollwörter gegen 27/36 deutsch ohne Hinweis), deutsche Haus-Sätze gleich gut
        "whisper-vulkan-multi": {"port": 8094, "log": "stt_vulkan_multi.log",
                                 "cmd": [str(_ROOT / "bin" / "whisper-vulkan" / "whisper-server.exe"),
                                         "-m", str(MODEL_DIR / "whisper-turbo-ggml" / "ggml-large-v3-turbo-q8_0.bin"),
                                         "-l", "de", "-nt", "--host", "127.0.0.1", "--port", "8094"]},
        "whisper-gpu": {"port": 8091, "log": "stt_gpu.log",
                        "cmd": [str(_ROOT / ".venv-gpu" / "Scripts" / "python.exe"),
                                str(Path(__file__).resolve().parent / "stt_gpu.py"), "--port", "8091"]},
    }

    def _gpu_stop_others(self, keep: str | None) -> None:
        """GPU-Dienste der NICHT gewählten Erkennung beenden, die noch antworten (Rest eines hart beendeten c4voice oder
        Wechsel im Admin) – sonst belegen beide Grafikspeicher. Beendet wird der Prozess, der den Port des Dienstes
        belegt (nicht eine gemerkte PID: nach einem Neustart kann die zu einem ganz anderen Programm gehören)."""
        for kind, svc in self.GPU_SERVICES.items():
            if kind == keep or not self._gpu_ok(kind, 0.5):
                continue
            pid = _port_owner(svc["port"])
            if pid:
                try:
                    _kill_tree(pid)
                    log("stt_gpu_stop", engine=kind, pid=pid)
                except Exception:  # noqa: BLE001
                    pass

    def _gpu_ok(self, kind: str, timeout: float = 1.0) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.GPU_SERVICES[kind]['port']}/health", timeout=timeout) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def _gpu_start(self, kind: str) -> None:
        """Dienst starten, falls er nicht schon läuft (läuft er – z. B. von einem früheren c4voice –, wird er genutzt)."""
        import subprocess
        procs = self.__dict__.setdefault("_gpu_procs", {})
        if self._gpu_ok(kind, 0.5) or (procs.get(kind) and procs[kind].poll() is None):
            return
        svc = self.GPU_SERVICES[kind]
        missing = [p for p in svc["cmd"] if p.endswith((".exe", ".py", ".bin")) and not Path(p).is_file()]
        if missing:
            log("stt_gpu_missing", engine=kind, files=missing)
            return
        logf = open(DATA_DIR / svc["log"], "ab")             # noqa: SIM115 – bleibt offen, solange der Dienst läuft
        procs[kind] = subprocess.Popen(svc["cmd"], stdout=logf, stderr=subprocess.STDOUT,
                                       env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import atexit
        atexit.register(lambda p=procs[kind]: p.poll() is None and _kill_tree(p.pid))
        log("stt_gpu_start", engine=kind, pid=procs[kind].pid)
        try:                                         # PID merken: wird c4voice hart beendet, bleibt der Dienst sonst liegen
            pids = json.loads((DATA_DIR / "stt_gpu_pids.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pids = {}
        pids[kind] = procs[kind].pid
        (DATA_DIR / "stt_gpu_pids.json").write_text(json.dumps(pids), encoding="utf-8")

        def warm():                                  # erster Satz baut die GPU-Shader (~1,3 s) – mit Stille vorwegnehmen
            for _ in range(120):
                if procs[kind].poll() is not None:
                    return
                if self._gpu_ok(kind, 0.5):
                    self._gpu_stt(kind, np.zeros(16000, dtype=np.float32), "de", "")
                    return
                time.sleep(1)
        threading.Thread(target=warm, daemon=True).start()

    def _gpu_stt(self, kind: str, audio: np.ndarray, language: str, hint: str) -> str | None:
        """Text vom GPU-Dienst – oder None (Dienst startet noch / abgestürzt / Zeitüberschreitung → Rückfall)."""
        import base64
        import io
        import urllib.request
        import uuid
        import wave
        if not self._gpu_ok(kind, 0.3):
            self._gpu_start(kind)
            return None
        port = self.GPU_SERVICES[kind]["port"]
        pcm16 = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        if kind.startswith("whisper-vulkan"):        # whisper.cpp: WAV als Formular-Datei, Hinweis im Feld „prompt“
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(pcm16)
            b = uuid.uuid4().hex
            fields = {"language": language, "prompt": hint, "response_format": "json", "temperature": "0"}
            body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
            body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                     f'Content-Type: audio/wav\r\n\r\n').encode() + buf.getvalue() + f"\r\n--{b}--\r\n".encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/inference", body,
                                         {"Content-Type": f"multipart/form-data; boundary={b}"})
        else:
            body = json.dumps({"pcm16_b64": base64.b64encode(pcm16).decode(), "language": language, "prompt": hint}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/stt", body, {"Content-Type": "application/json"})
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.load(r)
            log("stt_gpu", engine=kind, ms=d.get("ms") or round((time.time() - t0) * 1000))
            return str(d.get("text") or "").strip()
        except Exception as e:  # noqa: BLE001
            log("stt_gpu_error", engine=kind, error=f"{type(e).__name__}: {e}")
            return None

    def _fallback_stt(self, audio: np.ndarray, language: str) -> str:
        """Rückfall, wenn der GPU-Dienst nicht antwortet: Whisper small auf der CPU (~1 s, gleiche Familie, Sprache fest)."""
        if getattr(self, "_fallback", None) is None:
            from faster_whisper import WhisperModel
            self._fallback = WhisperModel("small", device="cpu", compute_type="int8",
                                          cpu_threads=int(self.cfg["stt"].get("threads") or 8), download_root=str(MODEL_DIR))
        log("stt_fallback", engine="whisper-small-cpu")
        segments, _ = self._fallback.transcribe(audio, language=language, beam_size=1, condition_on_previous_text=False,
                                                vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    def stt(self, pcm: bytes, vocab: list | None = None, language: str | None = None) -> str:
        """PCM16/16k/mono → Text (Whisper auf CPU oder GPU-Dienst). vocab = Raum-/Gerätenamen als Schreibhilfe (nur Whisper;
        Whisper schreibt „Esszimmer“ sonst gern „S-Zimmer“). Leise Aufnahmen (Handy-Mikrofon) werden vorher auf
        einen normalen Pegel gebracht – Whispers eigene Stille-Erkennung warf zu leise Befehle sonst weg."""
        with self._stt_lock:
            self._load_stt()
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        if 0.0 < peak < 0.5:                    # Pegel angleichen (Spitze → 0,7), nur anheben, nie verzerren
            audio = audio * (0.7 / peak)
        if self._stt_key[0] in self.GPU_SERVICES:
            lang_ = (language or self.cfg["stt"].get("language") or "de").strip().lower()
            txt = self._gpu_stt(self._stt_key[0], audio, lang_, self.hint(vocab) if vocab else "")
            return txt if txt is not None else self._fallback_stt(audio, lang_)
        lang = language or self.cfg["stt"].get("language") or None
        hint = self.hint(vocab) or None
        # beam_size: 1 = jedes Wort sofort festlegen (schnell); 5 = fünf Varianten parallel, die stimmigste gewinnt
        # (seltene Namen: „Avril Lavigne“ statt „Afri, Robin“)
        segments, _ = self._stt.transcribe(audio, language=lang, beam_size=int(self.cfg["stt"].get("beam") or 1),
                                           condition_on_previous_text=False,
                                           vad_filter=True, initial_prompt=hint)
        return " ".join(s.text.strip() for s in segments).strip()

    def hint(self, vocab) -> str:
        """Hinweistext für Whisper (CPU und GPU) – zusammengesetzt in wortschatz.build_hint (Teile a–e, siehe dort)."""
        rooms_, artists = (vocab, []) if not isinstance(vocab, dict) else (vocab.get("rooms") or [], vocab.get("artists") or [])
        return wortschatz.build_hint(wake_phrase(self.cfg) if self.cfg["wake"].get("enabled") else None,
                                     self.cfg["stt"].get("hint"), rooms_ if vocab else [], artists,
                                     wortschatz.zuletzt(ZULETZT_FILE))

    def wake_model(self):
        """EIN openWakeWord-Modell je Mikrofon-Verbindung: das Modell hat inneren Zustand (Audio- und
        Score-Puffer). Teilen sich mehrere Mikrofone eines, vermischen sich ihre Streams (Fehl- und
        Nichterkennung). Kosten je Modell: wenige MB RAM und ein kleiner Teil eines CPU-Kerns."""
        if self._ow_failed:
            return None
        try:
            from openwakeword.model import Model
            w = self.cfg["wake"]
            if self._ow is None:
                log("model_load", part="openwakeword", model=w["model"])
            m = Model(wakeword_models=[w["model"]], inference_framework="onnx")
            self._ow = True                    # „geladen" für die Statusanzeige
            return m
        except Exception as e:  # noqa: BLE001
            self._ow_failed = True
            self._wake_last_error = str(e)
            log("wake_error", error=str(e))
            return None

    def wake_feed(self, model, pcm: bytes) -> float:
        """Audio an das openWakeWord-Modell dieser Verbindung; liefert den aktuellen Score."""
        if model is None:
            return 0.0
        try:
            res = model.predict(np.frombuffer(pcm, dtype=np.int16))
            if isinstance(res, dict):          # neuere Versionen: {modellname: score}
                name = self.cfg["wake"]["model"]
                res = res.get(name, max(res.values()) if res else 0.0)
            return float(res)
        except Exception as e:  # noqa: BLE001
            log("wake_error", error=str(e))
            return 0.0

    @staticmethod
    def wake_reset(model) -> None:
        """Nach einer Erkennung den Vorhersage-Puffer leeren – sonst feuert dasselbe Wort gleich nochmal."""
        try:
            if model is not None:
                model.reset()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------------------
# Sprachausgabe: Piper (lokal, CPU, ~0,1 s pro Satz)
# --------------------------------------------------------------------------------------
def qwen_ref(q: dict) -> tuple[Path, str]:
    """Stimmvorlage für Qwen3 (data/tts/…wav) und ihr Text: aus der Konfiguration, sonst aus <vorlage>.txt daneben."""
    ref = Path(str(q.get("ref") or "qwen_ref_ruhig.wav"))
    ref = ref if ref.is_absolute() else TTS_DIR / ref
    text = str(q.get("ref_text") or "").strip()
    if not text and ref.with_suffix(".txt").is_file():
        text = ref.with_suffix(".txt").read_text(encoding="utf-8").strip()
    return ref, text


def qwen_voices() -> list[dict]:
    """Wählbare Qwen3-Stimmen = data/tts/qwen_ref_<name>.wav (+ .txt mit dem gesprochenen Text)."""
    out = []
    for p in sorted(TTS_DIR.glob("qwen_ref_*.wav")):
        t = p.with_suffix(".txt")
        out.append({"file": p.name, "name": p.stem[len("qwen_ref_"):],
                    "text": t.read_text(encoding="utf-8").strip() if t.is_file() else ""})
    return out


class Speaker:
    current: "Speaker | None" = None          # die eine Instanz im Dienst (für den Status im Admin)

    def __init__(self):
        self._voices: dict = {}
        self._lock = threading.Lock()
        self._qwen_proc = None
        Speaker.current = self

    # ---- Qwen3-TTS: eigener Dienst (c4bridge/tts_qwen.py im venv des Qwen3-TTS-GGUF-Projekts), c4voice startet ihn ----
    @staticmethod
    def _qwen_cfg(tcfg: dict) -> dict:
        q = dict(DEFAULT_CFG["tts"]["qwen"])
        q.update(tcfg.get("qwen") or {})
        if not q.get("dir"):
            q["dir"] = str(Speaker.POCKET_DIR / "qwen3")
        return q

    def _qwen_ok(self, port: int, timeout: float = 0.5) -> bool:
        return self._qwen_health(port, timeout) is not None

    @staticmethod
    def _qwen_health(port: int, timeout: float = 0.5) -> dict | None:
        """Antwort von /health ({"model": …}) – None, wenn der Dienst (noch) nicht bereit ist."""
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
                if r.status != 200:
                    return None
                try:
                    return json.loads(r.read() or b"{}")
                except ValueError:
                    return {}
        except Exception:  # noqa: BLE001
            return None

    def qwen_start(self, tcfg: dict) -> None:
        """Dienst starten, falls er nicht läuft (läuft er schon – z. B. von einem früheren c4voice –, wird er genutzt)."""
        import subprocess
        q = self._qwen_cfg(tcfg)
        port = int(q["port"])
        if self._qwen_ok(port) or (self._qwen_proc and self._qwen_proc.poll() is None) or _port_owner(port):
            return
        qdir = Path(q["dir"]) if q.get("dir") else None
        py = qdir / ".venv" / "Scripts" / "python.exe" if qdir else None
        ref, ref_text = qwen_ref(q)
        model = str(q.get("model") or "model-base")
        if not (qdir and py.is_file() and (qdir / model).is_dir() and ref.is_file()):
            log("tts_qwen_missing", dir=str(qdir), model=model, ref=str(ref))
            return
        logf = open(DATA_DIR / "tts_qwen.log", "ab")         # noqa: SIM115 – bleibt offen, solange der Dienst läuft
        self._qwen_proc = subprocess.Popen(
            [str(py), str(Path(__file__).resolve().parent / "tts_qwen.py"), "--dir", str(qdir), "--port", str(port),
             "--ref", str(ref), "--ref-text", ref_text, "--model", model],
            cwd=str(qdir), stdout=logf, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import atexit
        atexit.register(lambda p=self._qwen_proc: p.poll() is None and _kill_tree(p.pid))
        log("tts_qwen_start", pid=self._qwen_proc.pid, port=port)

    def qwen_stop(self, tcfg: dict) -> None:
        """Piper gewählt → Qwen-Dienst beenden (belegt sonst ~1,5 GB Grafikspeicher)."""
        pid = _port_owner(int(self._qwen_cfg(tcfg)["port"]))
        if pid:
            _kill_tree(pid)
            log("tts_qwen_stop", pid=pid)
        self._qwen_proc = None

    def _qwen_wav(self, text: str, tcfg: dict) -> bytes | None:
        """WAV vom Qwen-Dienst – oder None (startet noch / Fehler / zu langsam → Piper)."""
        import urllib.request
        q = self._qwen_cfg(tcfg)
        port = int(q["port"])
        h = self._qwen_health(port, 0.3)
        if h is not None and h.get("model", "model-base") != (q.get("model") or "model-base"):
            log("tts_qwen_model_change", running=h.get("model"), wanted=q.get("model"))
            self.qwen_stop(tcfg)                     # anderes Modell gewählt (Admin) → Dienst mit neuem Modell
            h = None
        if h is None:
            self.qwen_start(tcfg)
            # gerade gestartet (Wechsel im Admin, Neustart): ~7 s laden – kurz warten statt gleich Piper zu sprechen
            t_end = time.time() + 15
            while time.time() < t_end and self._qwen_proc is not None and self._qwen_proc.poll() is None:
                if self._qwen_ok(port, 0.5):
                    break
                time.sleep(0.5)
            else:
                return None
        try:
            ref, ref_text = qwen_ref(q)
            req = urllib.request.Request(f"http://127.0.0.1:{port}/tts",
                                         json.dumps({"text": text, "lang": q.get("lang") or ("english" if is_en() else "german"), "ref": str(ref),
                                                     "ref_text": ref_text}).encode("utf-8"),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=max(8.0, len(text) / 8)) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            log("tts_qwen_error", error=str(e)[:200])
            return None

    # ---- Pocket TTS: bin/tts/llama/llama-tts.exe je Satz (Modell bin/tts/pocket, Stimme = Audioprobe voices/<name>.wav) --
    POCKET_DIR = Path(__file__).resolve().parent.parent / "bin" / "tts"

    @classmethod
    def pocket_voices(cls) -> list[str]:
        return sorted(p.stem for p in (cls.POCKET_DIR / "pocket" / "voices").glob("*.wav"))

    def _pocket_loop(self, d: Path, ref: Path, out: Path, text: str) -> bytes | None:
        """llama-tts-loop (eigener Build, siehe bin/tts/README): Modell bleibt geladen, je Satz eine Zeile über stdin.
        None → Rückfall auf llama-tts je Satz (Programm fehlt, abgestürzt, Fehler)."""
        import subprocess
        exe = d / "llama" / "llama-tts-loop.exe"
        if not exe.is_file() or "\t" in text or "\n" in text:
            return None
        with self._lock:
            p = getattr(self, "_pocket_proc", None)
            try:
                if p is None or p.poll() is not None:
                    p = self._pocket_proc = subprocess.Popen(
                        [str(exe), "-m", str(d / "pocket" / "pocket-de24.gguf"), "-mm",
                         str(d / "pocket" / "mmproj-pocket-de24.gguf"), "-ngl", "99"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    import atexit
                    atexit.register(lambda q=p: q.poll() is None and _kill_tree(q.pid))
                    log("tts_pocket_start", pid=p.pid)
                    if p.stdout.readline().decode("utf-8", "replace").strip() != "@@READY":
                        raise RuntimeError("startet nicht")
                p.stdin.write(f"{ref}\t{out}\t{text}\n".encode("utf-8"))
                p.stdin.flush()
                ans = p.stdout.readline().decode("utf-8", "replace").strip()
                if not ans.startswith("@@OK"):
                    raise RuntimeError(ans or "keine Antwort")
                return out.read_bytes()
            except Exception as e:  # noqa: BLE001
                log("tts_pocket_error", error=str(e)[:200])
                if p is not None and p.poll() is None:
                    _kill_tree(p.pid)
                self._pocket_proc = None
                return None

    def _pocket_wav(self, text: str, tcfg: dict) -> bytes | None:
        import subprocess
        import tempfile
        d, voice = self.POCKET_DIR, str((tcfg.get("pocket") or {}).get("voice") or "eponine")
        if not re.fullmatch(r"[\w-]+", voice):
            voice = "eponine"
        exe, ref = d / "llama" / "llama-tts.exe", d / "pocket" / "voices" / f"{voice}.wav"
        if not (exe.is_file() and ref.is_file()):
            log("tts_pocket_missing", voice=voice)
            return None
        out = Path(tempfile.gettempdir()) / f"c4voice_pocket_{os.getpid()}.wav"
        text = zahlen_als_woerter(text)                      # Pocket liest Ziffern schlecht
        data = self._pocket_loop(d, ref, out, text)          # Dauerprozess: ~0,3 s je Satz statt ~1 s
        if data:
            return data
        cmd = [str(exe), "-m", str(d / "pocket" / "pocket-de24.gguf"), "-mm", str(d / "pocket" / "mmproj-pocket-de24.gguf"),
               "-ngl", "99", "-p", text, "--tts-speaker-file", str(ref), "--output", str(out)]
        try:
            with self._lock:                         # ein Satz nach dem anderen (Grafikkarte, gleiche Ausgabedatei)
                r = subprocess.run(cmd, capture_output=True, timeout=max(15.0, len(text) / 5),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode != 0 or not out.is_file():
                    log("tts_pocket_error", code=r.returncode, error=(r.stderr or b"")[-300:].decode("utf-8", "replace"))
                    return None
                return out.read_bytes()
        except Exception as e:  # noqa: BLE001
            log("tts_pocket_error", error=str(e)[:200])
            return None

    def wav(self, text: str, tcfg: dict) -> bytes:
        """Text → WAV-Bytes (mono). engine pocket / qwen3 → das Modell, sonst und als Rückfall Piper."""
        # nicht gewähltes Modell beenden → Grafikspeicher frei (Wechsel im Admin)
        eng = tcfg.get("engine") or "piper"
        if eng != getattr(self, "_eng_last", None):   # Wechsel (oder erster Satz): Qwen-Dienst über den Port beenden –
            self._eng_last = eng                        # er kann noch von einem früheren c4voice stammen
            if eng != "qwen3":
                self.qwen_stop(tcfg)
        lp = getattr(self, "_pocket_proc", None)
        if tcfg.get("engine") != "pocket" and lp is not None and lp.poll() is None:
            _kill_tree(lp.pid)
            self._pocket_proc = None
            log("tts_pocket_stop")
        if tcfg.get("engine") == "pocket":
            data = self._pocket_wav(text, tcfg)
            if data:
                return data
        if tcfg.get("engine") == "qwen3":
            data = self._qwen_wav(text, tcfg)
            if data:
                return data
        from piper import PiperVoice, SynthesisConfig
        name = str(tcfg.get("voice") or DEFAULT_CFG["tts"]["voice"])
        if not re.fullmatch(r"[\w.-]+", name):
            raise ValueError("ungültiger Stimmenname")
        path = TTS_DIR / f"{name}.onnx"
        if not path.is_file():
            raise FileNotFoundError(f"Stimme {name} ist nicht installiert (Admin → Sprache → Sprachausgabe)")
        with self._lock:
            v = self._voices.get(name)
            if v is None:
                log("model_load", part="piper", model=name)
                v = self._voices[name] = PiperVoice.load(str(path))
        speed = min(2.0, max(0.5, float(tcfg.get("speed") or 1.0)))
        syn = SynthesisConfig(length_scale=1.0 / speed,
                              volume=min(2.0, max(0.1, float(tcfg.get("volume") or 1.0))))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            v.synthesize_wav(text, w, syn_config=syn)
        return buf.getvalue()


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001
        return 0.0


# Antworten von execute_tool, die keine Erfolgsmeldung sind (werden auch im Kurzmodus ganz gesprochen)
_PROBLEM = ("Kein ", "Keine ", "In welchem", "Welche", "Ohne ", "Zu „", "Unbekannt", "Fehler", "In ")


# Eine FRAGE nach dem Zustand schaltet nie (das Modell nahm für „Ist im Esszimmer noch Licht an?“ light_set → Licht aus).
# Erkannt am Satzanfang; enthält der Satz eine Aufforderung („ist egal, mach alle an“), ist es keine Frage.
_QUESTION = re.compile(r"^\W*(ist|sind|brennt|brennen|leuchtet|leuchten|läuft|laufen|wo|welche[srnm]?|gibt es|"
                       r"habe? ich|wie ?viele)\b", re.I)
_COLLECTION = re.compile(r"\b(sammlung|(unsere|eigene|meine|lokale)[rnms]? musik|bibliothek|von uns)\b", re.I)
_YESNO = re.compile(r"^\W*(ist|sind|brennt|brennen|leuchtet|leuchten|gibt es|habe? ich)\b", re.I)   # Ja/Nein-Frage
_REQUEST = re.compile(r"\b(mach|schalt|dreh|stell|fahr|spiel|start)\w*", re.I)
_SWITCHING = ("light_set", "light_adjust", "light_color", "switch_set", "blind_action", "room_off", "climate_setpoint",
              "climate_setpoint_delta", "music_play", "stream_play", "video_play", "media_control", "volume_set",
              "volume_adjust")
_PLAYING = ("music_play", "stream_play", "video_play")       # Frage („Gibt es was von Adele?“) → anbieten statt spielen


def asks_state(text: str | None) -> bool:
    if is_en():
        return bool(text and en.QUESTION.search(text) and not en.REQUEST.search(text))
    return bool(text and _QUESTION.search(text) and not _REQUEST.search(text))


class Auswahl(str):
    """Werkzeug-Antwort „bitte aus dieser Trefferliste wählen“ (PLAN-AUSWAHL.md): process_utterance lässt das Sprachmodell
    in einer zweiten Runde desselben Gesprächs (gleicher Anfang → Ollamas Zwischenspeicher hält) mit treffer_waehlen
    wählen und führt den gewählten Eintrag aus. kandidaten = [{zeile, label, call}], call = fertiger Werkzeugaufruf;
    bei_nichts = Antwort, wenn keiner passt; rueckfall = Aufruf nach der bisherigen Regel (Modell antwortet nicht)."""

    def __new__(cls, kandidaten: list, bei_nichts: str, rueckfall: dict):
        obj = super().__new__(cls, "Auswahl")
        obj.kandidaten, obj.bei_nichts, obj.rueckfall = kandidaten, bei_nichts, rueckfall
        return obj


# Gesprächsstand der ersten Modellrunde (gesetzt in process_utterance) – nur dann kann execute_tool eine Auswahl anbieten
_AUSWAHL_CTX: contextvars.ContextVar = contextvars.ContextVar("auswahl_ctx", default=None)
# Wörter, die in einem Musikwunsch nichts über das WAS sagen (alles andere ist ein Zusatz wie „mit Fußball“, „live“)
_FUELL = set("""spiel spiele spielt spielen mal bitte von vom den dem der die das des mit über ueber auf aus im in und
oder ein eine einen einer eines was etwas noch einmal jetzt gerne gern doch mir uns unsere unserer unser unseren
sammlung musik lied lieder song songs titel album alben hörspiel hörbuch folge folgen teil qobuz tunein radio mach
starte start hören hör hey jarvis ich möchte will würde kannst du bitte nochmal wieder dann bei vor zum zur
ganze ganzen als einfach""".split())


# Satz bezieht sich auf das, was gerade läuft („mehr davon“, „von der Band“) – für artist_said und zusatz_woerter
_REF_NOW = re.compile(r"\b(mehr davon|mehr von (der|dem|den|ihm|ihr|denen)|noch (was|etwas|eins|einen) von|"
                      r"(der|dem|den|die) (band|gruppe|interpret\w*|sänger\w*|künstler\w*)|gleiche[nrs]? |"
                      r"vom (selben|gleichen)|von (ihm|ihr|denen))", re.I)


def zusatz_woerter(satz: str | None, bekannt: list, raeume: list) -> list:
    """Wörter im Satz, die weder Füllwort noch Suchbegriff/Interpret/Raum sind – sagt der Nutzer MEHR als den Namen
    („die Folge mit Fußball“, „das Original“, „live“, „die neueste“), wählt das Sprachmodell aus der Trefferliste."""
    from rapidfuzz import fuzz
    known = {w for x in list(bekannt) + list(raeume) for w in re.findall(r"[a-zäöüß0-9]+", str(x or "").lower())}
    out = []
    for w in re.findall(r"[a-zäöüß0-9]+", _REF_NOW.sub(" ", (satz or "").lower())):
        if len(w) < 3 or w.isdigit() or w in _FUELL or any(fuzz.ratio(w, k) >= 80 for k in known):
            continue
        out.append(w)
    return out


_ARTEN = {"Titel": "Lieder", "Album": "Alben", "Interpret": "Interpreten"}


def auswahl_text(kandidaten: list) -> str:
    """Trefferliste für die Auswahlrunde – knapp, weil jedes Token hinter dem langen Systemprompt teuer ist
    (RX 6900 XT liest bei ~9.000 Token Vorlauf nur ~180 Token/s, stt-test/LlamaVulkan/ERGEBNIS.md). Gleiche Quelle/Art
    aller Zeilen steht einmal im Kopf statt in jeder Zeile. Test 19.10. (stt-test/pick_bench.py, 24 Fälle, gemma):
    vorher 22/24 in 4,15 s, so 21/24 in 3,2 s; „Titel – Interpret · Album“ war kürzer, aber gemma sagte öfter -1."""
    quellen = sorted({k.get("quelle", "") for k in kandidaten})
    arten = sorted({k.get("art", "") for k in kandidaten})
    kopf = " ".join(x for x in (quellen[0] if len(quellen) == 1 else "",
                                _ARTEN.get(arten[0], "") if len(arten) == 1 else "") if x)
    spalten = ["Nr"] + (["Quelle"] if len(quellen) > 1 else []) + (["Art"] if len(arten) > 1 else []) \
        + ["Titel", "Interpret", "Album"]
    zeilen = []
    for i, k in enumerate(kandidaten):
        z = [str(i)] + ([k.get("quelle", "")] if len(quellen) > 1 else []) + ([k.get("art", "")] if len(arten) > 1 else []) \
            + [k["titel"], k.get("interpret") or "", k.get("album") or ""]
        zeilen.append(" | ".join(z))
    # Anweisung bewusst NICHT gekürzt: kürzere Fassungen ließen gemma öfter -1 sagen (pick_bench.py)
    return (f"TREFFERLISTE{' ' + kopf if kopf else ''} ({' | '.join(spalten)}). Antworte NUR mit dem Werkzeug "
            "treffer_waehlen, keine Rückfrage als Text: den Eintrag, der zum Wunsch passt (verhörte Namen sind normal); "
            "nur vermutet → sicher=false; keiner passt → nr=-1.\n" + "\n".join(zeilen))


def lies_wahl(msg: dict, kandidaten: list) -> dict | None:
    """Antwort der Auswahlrunde → {nr, sicher, suche} oder None (= nichts Verwertbares → bisherige Regel).
    Fehlt „sicher“, gilt die Wahl als unsicher (Rückfrage statt Fehlschaltung)."""
    wahl = None
    for c in msg.get("tool_calls") or []:
        f = c.get("function") or {}
        if f.get("name") == "treffer_waehlen":
            wahl = f.get("arguments") or {}
            if isinstance(wahl, str):
                try:
                    wahl = json.loads(wahl)
                except ValueError:
                    wahl = None
            if not isinstance(wahl, dict):
                wahl = None
    if wahl is None and (msg.get("content") or "").strip():
        # Freitext statt Auswahl: nennt er GENAU einen Listeneintrag („Meinst du Adele?“) = unsichere Wahl davon,
        # sonst = keiner passt → feste Antwort der Bridge mit ihren Angeboten
        txt_ = str(msg["content"]).lower()
        genannt = [i for i, k in enumerate(kandidaten)
                   if len(k.get("titel", "")) >= 3 and re.search(r"\b" + re.escape(k["titel"].lower()) + r"\b", txt_)]
        log("auswahl", nr=None, text=msg.get("content"), genannt=genannt[:3])
        return {"nr": genannt[0], "sicher": False, "suche": ""} if len(set(kandidaten[i]["label"] for i in genannt)) == 1 \
            else {"nr": -1, "sicher": True, "suche": ""}
    if wahl is None:
        return None
    try:
        nr = int(wahl.get("nr"))
    except (TypeError, ValueError):
        nr = -1
    return {"nr": nr, "sicher": wahl.get("sicher") in (True, "true", "True", 1),
            "suche": str(wahl.get("suche") or "").strip()}


async def waehle(a: "Auswahl", call: dict, ctx: dict, run) -> str:
    """Zweite Runde: Trefferliste als Werkzeug-Antwort, das Modell antwortet mit treffer_waehlen(nr, sicher, suche).
    run(call) führt einen Werkzeugaufruf aus (execute_tool ohne neue Auswahl)."""
    cfg = ctx["cfg"]
    tune = ollama_tuning(cfg["llm"])
    t0 = time.time()
    liste = auswahl_text(a.kandidaten)
    body = {"model": cfg["llm"]["model"], "stream": False, "think": False, "keep_alive": tune["keep_alive"],
            "options": {"temperature": 0.1, "num_predict": 120, **({"num_ctx": tune["num_ctx"]} if "num_ctx" in tune else {})},
            "messages": [*ctx["messages"], {"role": "assistant", "content": "", "tool_calls": [{"function": call}]},
                         {"role": "tool", "content": liste}],
            "tools": ctx["tools"]}
    wahl = None
    # Auswahl dauert normal 3–4 s; hängt Ollama, lieber nach 30 s die bisherige Regel als 90 s Stille
    tmo = min(float(cfg["llm"].get("timeout_s", 90)), 30.0)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=tmo)) as s_:
            for _ in range(2):
                async with s_.post(cfg["llm"]["url"].rstrip("/") + "/api/chat", json=body) as r_:
                    if r_.status == 400 and "think" in body:
                        body.pop("think")            # Modell/Server kennt das Feld nicht → ohne (wie ollama_decide)
                        continue
                    r_.raise_for_status()
                    data = await r_.json()
                    break
        wahl = lies_wahl(data.get("message") or {}, a.kandidaten)
    except Exception as e:  # noqa: BLE001
        log("auswahl_fehler", error=f"{type(e).__name__}: {e}")
    if wahl is None:                                 # Modell hat nicht gewählt → bisherige Regel
        log("auswahl", nr=None, rueckfall=True, took=round(time.time() - t0, 1))
        return await run(a.rueckfall)
    nr, sicher, suche = wahl["nr"], wahl["sicher"], wahl["suche"]
    log("auswahl", nr=nr, sicher=sicher, suche=suche, gewaehlt=a.kandidaten[nr]["label"] if 0 <= nr < len(a.kandidaten) else None,
        took=round(time.time() - t0, 1))
    if 0 <= nr < len(a.kandidaten):
        k = a.kandidaten[nr]
        if sicher:
            return await run(k["call"])
        return Problem(f"Meinst du {k['label']}?", [_offer(k["label"], k["call"]["name"], k["call"]["arguments"])])
    b_ = a.bei_nichts if isinstance(a.bei_nichts, Problem) else Problem(str(a.bei_nichts))
    if suche and b_.offers:                          # z. B. „auf Qobuz suchen?“ – dann gleich mit den besseren Wörtern
        offers = [{**o, "call": {**o["call"], "arguments": {**o["call"]["arguments"], "search": suche}}}
                  if "search" in o["call"].get("arguments", {}) else o for o in b_.offers]
        return Problem(str(b_).replace("?", f" (nach „{suche}“)?", 1) if "?" in b_ else str(b_), offers)
    return b_


class Problem(str):
    """Werkzeug-Antwort, die KEIN Erfolg ist (Fehler, nicht gefunden, Rückfrage) – wird immer ganz gesprochen;
    enthält sie eine Frage, hört das Mikrofon danach ohne Wake-Word auf die Antwort.
    offers = feste Angebote einer Rückfrage [{label, call: {name, arguments}}]: Antwortet der Nutzer „Ja“ oder nennt
    er ein Angebot, führt der Code genau diesen Aufruf aus – ohne Sprachmodell (kleine Modelle griffen sonst wieder
    den alten, verhörten Namen auf; „100 Regeln“ skalieren nicht)."""

    def __new__(cls, text: str, offers: list | None = None):
        obj = super().__new__(cls, text)
        obj.offers = list(offers or [])
        return obj


def _offer(label: str, tool: str, arguments: dict) -> dict:
    return {"label": str(label), "call": {"name": tool, "arguments": {k: v for k, v in arguments.items() if v not in (None, "")}}}


_YES = re.compile(r"(ja|jo|jep|jawohl|genau|richtig|gerne|gern|klar|okay|ok|passt|stimmt|bitte|mach das|mach mal|"
                  r"ja mach|spiel das|das meine ich|den meine ich|die meine ich)"
                  r"( (ja|genau|bitte|gerne|gern|danke|mach das|das|den|die|richtig))*")


def match_offer(text: str, offers: list) -> dict | None:
    """Antwort auf eine Rückfrage mit festen Angeboten: „Ja“ → erstes Angebot; nennt der Satz ein Angebot (auch
    „Ja, Eddie Vedder“) → dieses. Alles andere (z. B. „Nein, Adele“) bleibt beim Sprachmodell."""
    from rapidfuzz import fuzz
    t = " ".join(re.sub(r"[^\wäöüß ]+", " ", (text or "").lower()).split())
    if not t or not offers or re.match(r"(nein|ne|nee|nö|nicht)\b", t) or (is_en() and en.NO.match(t)):
        return None
    named = [(fuzz.partial_ratio(o["label"].lower(), t), o) for o in offers if len(o["label"]) >= 3]
    named = [x for x in named if x[0] >= 88]
    if named:
        return max(named, key=lambda x: x[0])[1]
    if _YES.fullmatch(t) or (is_en() and en.YES.fullmatch(t)):
        return offers[0]
    return None


def speakable(text: str) -> str:
    """Anzeige-Text → Sprechtext: Zeichen, die Piper vorliest oder verschluckt, ersetzen."""
    t = text.replace(" (Simulation – nichts ausgeführt)", ", nur simuliert")
    t = t.replace(" · ", ". ").replace("„", "").replace("“", "").replace('"', "")
    t = re.sub(r"(\d)\s*%", r"\1 Prozent", t)
    t = re.sub(r"(\d)\s*°C", r"\1 Grad", t)
    t = re.sub(r"(\d)\.(\d)", r"\1,\2", t)          # 23.5 → 23,5 (sonst „Punkt“)
    t = re.sub(r"\(z\.\s?B\.[^)]*\)", "", t)          # Beispiel-Klammern nicht vorlesen
    t = t.replace("–", ",").replace("→", "auf").replace("✓", "")
    t = re.sub(r"\s+([.,!?])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


_EINS = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "zwölf",
         "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
_ZEHNER = ["", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig"]


def zahl_wort(n: int) -> str:
    """Ganze Zahl 0–999999 auf Deutsch („40“ → „vierzig“, „125“ → „hundertfünfundzwanzig“)."""
    if n < 20:
        return _EINS[n]
    if n < 100:
        e, z = n % 10, n // 10
        return (("ein" if e == 1 else _EINS[e]) + "und" if e else "") + _ZEHNER[z]
    if n < 1000:
        h, r = divmod(n, 100)
        return ("ein" if h == 1 else _EINS[h]) + "hundert" + (zahl_wort(r) if r else "")
    t, r = divmod(n, 1000)
    return ("ein" if t == 1 else zahl_wort(t)) + "tausend" + (zahl_wort(r) if r else "")


def zahlen_als_woerter(text: str) -> str:
    """Für Pocket TTS: Ziffern liest es schlecht („40 Prozent“ → „Rennprozent“) – Uhrzeiten, Kommazahlen und ganze
    Zahlen vorher ausschreiben. Piper/Qwen3 lesen Ziffern selbst richtig."""
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b",
               lambda m: f"{zahl_wort(int(m[1]))} Uhr" + (f" {zahl_wort(int(m[2]))}" if int(m[2]) else ""), text)
    t = re.sub(r"\b(\d{1,6}),(\d{1,2})\b",
               lambda m: f"{zahl_wort(int(m[1]))} Komma {' '.join(_EINS[int(c)] for c in m[2])}", t)
    t = re.sub(r"\b\d{1,6}\b", lambda m: zahl_wort(int(m[0])), t)
    return t.replace("eins Prozent", "ein Prozent").replace("eins Grad", "ein Grad")


def concise(text: str, speaker_rooms: list | None = None) -> str:
    """Gesprochene Bestätigung kurz halten (Nutzer 18.10.: „im gleichen Zimmer reicht ‚Licht auf 70 %‘“): Sprecherraum
    weglassen, Einzelheiten/Übersprungenes weglassen. Die Anzeige im Mikrofon-Test behält den vollen Text."""
    t = str(text)
    if isinstance(text, Problem):
        return t
    for r in speaker_rooms or []:                  # eigener Raum: Raumname weglassen
        t = t.replace(f"Raum {r} aus", "Aus").replace(f" in {r}", "").replace(f"{r}: ", "")
    sim = " (Simulation – nichts ausgeführt)" if "Simulation" in t else ""
    t = t.replace(" (Simulation – nichts ausgeführt)", "")
    m = re.match(r"^1 Licht (?:in (.+?) )?(heller|gedimmt) \(„[^“]+“ (an|\d+ %)\)", t)
    if m:                                          # ein Licht geändert → „Licht auf 70 %“ (anderer Raum: „… im …“)
        t = "Licht" + (f" in {m.group(1)}" if m.group(1) else "") + (" an" if m.group(3) == "an" else f" auf {m.group(3)}")
    elif re.match(r"^0 Lichter? ", t):
        t = "Schon ganz dunkel." if "dunkel" in t else "Schon voll hell." if "voll" in t else "Da ist nichts zu ändern."
    else:
        t = re.sub(r"^(\d+ Lichter? (?:in .+? )?(?:heller|gedimmt)) \(.*$", r"\1", t)
        t = re.sub(r";\s*übersprungen:.*$", "", t)
    t = re.sub(r"^Lautstärke auf (\d+)", r"Lautstärke \1", t)
    t = re.sub(r"^(Rollläden (?:in .+? )?fahren \w+) \(.*\)$", r"\1", t)          # Liste der Gruppen weglassen
    t = re.sub(r"^Rollladen „[^“]*(gesamt|Gruppe|alle)[^“]*“ fährt (\w+)", r"Rollläden fahren \2", t, flags=re.I)
    t = t.strip()
    return (t[:1].upper() + t[1:] if t else t) + sim


def spoken_answer(results: list, content: str, tcfg: dict, speaker_rooms: list | None = None) -> str:
    """Was gesprochen wird: mode short → bei Erfolg nur „Okay", Fragen/Fehler immer ganz."""
    if not results:
        return content
    results = [concise(r, speaker_rooms) if not isinstance(r, Problem) else r for r in results]
    if tcfg.get("mode") == "short" and not any(isinstance(r, Problem) or r.startswith(_PROBLEM) for r in results):
        return "Okay."
    # viele gleichartige Licht-Meldungen („Licht X aus · Licht Y aus · …") → ein Satz
    lights = [r for r in results if r.startswith("Licht „")]
    if len(lights) >= 3 and len(lights) == len(results):
        states = {r.split("“", 1)[1].split("(")[0].strip().split(" ")[0] for r in lights}
        if len(states) == 1:
            sim = " (Simulation – nichts ausgeführt)" if all("Simulation" in r for r in lights) else ""
            return f"{len(lights)} Lichter {states.pop()}{sim}"
    return " · ".join(results)


class UtteranceCollector:
    """Sammelt eine Äußerung: Sprache beginnt bei Energie, endet nach Stille."""
    FRAME = 960            # Samples je Rahmen = 60 ms bei 16 kHz (1920 Bytes)
    SILENCE_MS = 900
    MIN_SPEECH_MS = 500    # kürzeste nutzbare Sprechdauer (filtert Klicks/Wake-Reste)

    def __init__(self, skip_ms: int = 0, max_ms: int = 15000, min_speech_ms: int | None = None):
        self.MAX_MS = max_ms
        # Antwort auf eine Rückfrage („Ja“, „Nein“) ist oft < 0,5 s (gemessen: „Ja“ 240 ms) – dort reichen 0,15 s Sprache
        self.min_speech_ms = self.MIN_SPEECH_MS if min_speech_ms is None else min_speech_ms
        self.buf = bytearray()
        self.silence_ms = 0
        self.total_ms = 0
        self.has_speech = False
        self.noise_floor = 150.0
        self.speech_ms = 0
        self.skip_ms = skip_ms        # z. B. 350 nach Wake: Wake-Wort-Rest wegwerfen
        self._rest = b""              # angefangener Rahmen vom letzten Paket (Pakete sind selten Vielfache von 60 ms)

    @staticmethod
    def _rms(frame: bytes) -> float:
        a = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(a * a))) if len(a) else 0.0

    def arm(self, skip_ms: int = 0) -> None:
        self.__init__(skip_ms)

    def preload(self, pcm: bytes) -> None:
        """Vorlauf aus dem Ringpuffer übernehmen (Wake-Word + schon gesprochener Befehlsanfang):
        gilt als Sprache, das Ende erkennt danach die normale Stille-Regel."""
        frames = [pcm[i:i + self.FRAME * 2] for i in range(0, len(pcm) - self.FRAME * 2 + 1, self.FRAME * 2)]
        levels = sorted(self._rms(f) for f in frames) or [150.0]
        # Raumrauschen ≈ leisestes Zehntel – gedeckelt, weil der Vorlauf meist fast nur Sprache ist
        self.noise_floor = min(300.0, max(80.0, levels[len(levels) // 10]))
        self.buf = bytearray(pcm)
        self.has_speech = True
        self.silence_ms = 0
        self.speech_ms = sum(60 for lv in levels if lv > max(_VAD["keep"], self.noise_floor * (_VAD["noise_x"] - 0.2)))

    def feed(self, pcm: bytes) -> str | None:
        """'done' mit gesammeltem Audio zurückgeben, sonst None."""
        pcm, step = self._rest + pcm, self.FRAME * 2
        n = len(pcm) - len(pcm) % step
        self._rest = pcm[n:]                         # Rest kommt mit dem nächsten Paket (früher verworfen: ~5 % Audio)
        for i in range(0, n, step):                  # volle 60-ms-Rahmen
            frame = pcm[i:i + step]
            rms = self._rms(frame)
            self.total_ms += 60
            if self.skip_ms > 0:                     # Nach-Wake-Pause: nur Rauschpegel lernen
                self.skip_ms -= 60
                self.noise_floor = 0.9 * self.noise_floor + 0.1 * rms
                continue
            if not self.has_speech:
                self.noise_floor = 0.9 * self.noise_floor + 0.1 * rms
                if rms > max(_VAD["start"], self.noise_floor * _VAD["noise_x"]):
                    self.has_speech = True
                    self.silence_ms = 0
            else:
                if rms > max(_VAD["keep"], self.noise_floor * (_VAD["noise_x"] - 0.2)):
                    self.silence_ms = 0
                    self.speech_ms += 60
                else:
                    self.silence_ms += 60
                self.buf.extend(frame)
            if self.total_ms > self.MAX_MS:
                return "timeout"
            if self.has_speech and self.silence_ms >= self.SILENCE_MS:
                return "done"
        return None

    MAX_MS = 15000

    def usable(self) -> bool:
        return self.speech_ms >= self.min_speech_ms and len(self.buf) >= (16000 if self.min_speech_ms >= 500 else 8000)

    def audio(self) -> bytes:
        return bytes(self.buf)


# --------------------------------------------------------------------------------------
# Gerätekontext + System-Prompt (wie tools/ollama_test.py, live aus der Bridge)
# --------------------------------------------------------------------------------------
SYSTEM_PROMPT = """Du bist der Sprachassistent eines Smart Homes (Control4-Haus, gesteuert über die c4bridge).
Du beantwortest kurze deutsche Sätze und führst Haus-Aufgaben AUSNAHMSLOS über die angeboteten Funktionen (tools) aus.

Räume (was es dort gibt – sonst nicht):
{rooms}

Verfügbare Geräte:
{devices}

Regeln:
1. Die gesprochene Bezeichnung muss NICHT exakt der Liste entsprechen: Wähle das ähnlichste
   gelistete Gerät/den ähnlichsten Raum (z. B. "Arbeitszimmer Aleks" → "Arbeit Alex",
   "Jalousie" → Rollladen-Name). ABER: Passt die ungenaue Bezeichnung auf MEHRERE Geräte/Räume
   ähnlich gut (z. B. nur "Arbeitszimmer" bei den Räumen Arbeit Kim UND Arbeit Alex),
   frag kurz nach, statt zu raten. Die Spracherkennung verhört sich bei Raumnamen gern ("S-Zimmer",
   "Eszimmer" = Esszimmer, "Wonzimmer" = Wohnzimmer): Klingt ein Wort wie genau EIN Raum der Liste, nimm
   diesen Raum; passt es auf mehrere (z. B. Esszimmer ODER Schlafzimmer), frag nach statt zu raten.
   Raumnamen gehören in room, NIE in device ("Licht im Gästezimmer aus" → light_set room=Gästezimmer, ohne device).
   Ohne Raumnennung gilt IMMER der Raum des Sprechers: nimm dann nur
   Geräte aus dessen Liste und lass room weg. Nennt der Nutzer einen anderen Raum (auch als Antwort
   auf eine Rückfrage), gib ihn als room mit – sonst findet die Bridge das Gerät nicht.
2. Ist unklar, welches Gerät gemeint ist, frage kurz nach (kein Werkzeugaufruf): EINE kurze Frage
   mit Fragezeichen, höchstens zwei Möglichkeiten nennen (die Antwort wird vorgelesen).
3. Gibt es kein passendes Gerät/Werkzeug (z. B. Türschloss, Alarmanlage), sag es kurz - erfinde nichts.
5. Antworte kurz auf Deutsch (ein Satz). Nach einer Aktion bestätige sie knapp.
6. "Licht aus"/"Lichter aus" schaltet NUR die Lichter aus (light_set mit on=false für die
   Lichter des Raums) - NICHT room_off! Auch mit Raumnennung ("Licht im Wohnzimmer aus") ALLE Lichter dieses
   Raums: light_set mit room und OHNE device. device nur, wenn ein bestimmtes Licht genannt wird. room_off schaltet nur die MEDIEN (Musik/TV) aus,
   das Licht bleibt unverändert - passend für "Raum aus" im Medienkontext.
7. Bei unverständlichen/widersprüchlichen Aussagen (verkorkster Satz) lieber kurz nachfragen
   als etwas falsch zu schalten.
8. Musik: "Spiel …" → music_play. kind: "artist" bei Interpret/Band ("spiel was von Sting"),
   "album" bei Albumtitel, "song" bei Liedtitel, "genre" bei Musikrichtung ("spiel Jazz" – nimm
   die passende Richtung aus der Liste unten, auch übersetzt: Klassik → Classic, Hörbuch → Audiobook),
   "random" bei "spiel Musik"/"irgendwas" (startet NEUE Musik), "any" wenn unklar ist, was der Name ist.
   search = der gesprochene Name (ohne "spiel", "das Album"). room nur, wenn ein Raum genannt wird.
   Namen GENAU so übernehmen, wie sie im Satz stehen – auch wenn sie falsch klingen ("Set für to the rain",
   "Adda"): die Spracherkennung verhört sich, die Bridge findet den richtigen Namen selbst. Nichts weglassen.
   "Titel von Interpret" ("Set fire to the rain von Adele") → kind song, search = der GANZE Ausdruck inkl. "von …".
   Korrigiert der Nutzer nach einer Rückfrage ("Meinst du Eddie Vedder?" → "Nein, Adele"), gilt NUR der neue Name
   aus seiner Antwort (search = "Adele") – den alten, verhörten Namen nie wiederverwenden. Bestätigt er mit "Ja",
   gilt der Name aus DEINER Frage ("Meinst du Eddie Vedder?" → "Ja" → search = "Eddie Vedder").
9. Wiedergabe: "lauter"/"leiser" (+ "ein bisschen"/"viel") → volume_adjust; "Lautstärke auf 30" →
   volume_set; "Pause"/"Musik anhalten" → media_control pause; "weiter"/"weiterspielen" → play;
   "nächstes Lied"/"überspringen" → next; "vorheriges Lied"/"nochmal von vorn" → previous;
   "stumm"/"Ton aus" → mute, "Ton an" → unmute. "Musik aus"/"Raum aus" → room_off.
   "Zufallswiedergabe an"/"Mix an"/"mische die Lieder" → media_control shuffle_on (NICHT music_play random),
   "Zufall aus" → shuffle_off; "wiederhole das Lied"/"in Dauerschleife" → repeat_one, "alles wiederholen" →
   repeat_all, "Wiederholen aus" → repeat_off.
   Ohne Raumnennung gilt immer der Raum des Sprechers (room weglassen) – bei Wiedergabe-Befehlen
   ("Pause", "weiter", "lauter") NIE nach dem Raum fragen.
10. Wetter ("wie wird das Wetter", "regnet es morgen", "brauche ich einen Schirm") → weather mit day
   (heute, morgen, übermorgen oder Wochentag). Gib die Antwort des Werkzeugs unverändert weiter.
11. Allgemeine Wissens- oder Nachrichtenfragen (nicht das Haus betreffend) → web_search, falls vorhanden, mit
   einer kurzen, präzisen deutschen Suchanfrage. Niemals Geräte-, Raum- oder Personendaten in die Suche schreiben.
   Gibt es das Werkzeug nicht, sag kurz, dass du für solche Fragen keinen Internetzugang hast.
12. Schalter (Typ switch: Steckdosen, Relais, Pumpe, Lüfter) → switch_set – auch wenn der Name „…lampe“ heißt.
   Nur Geräte vom Typ light über light_set schalten.
15. Radio/Sender ("spiel SWR3", "mach hr3 an", "Radio Bob", "TuneIn", "wechsel auf hr3", "stell um auf FFH") →
   stream_play service radio, kind station – auch beim Umschalten IMMER das Werkzeug aufrufen;
   TuneIn heißt für den Nutzer einfach "Radio". "Auf Qobuz"/"von Qobuz" → stream_play service qobuz. Ohne
   Dienstnennung ist Musik (Interpret/Album/Lied) aus der eigenen Sammlung → music_play.
   Qobuz, TuneIn und die Musiksammlung sind IMMER Audio (Musik, Radio, Hörspiele, Hörbücher – auch wenn eine
   „Folge“ genannt wird) und NIE video_play. video_play nur für Filme/Serien der Mediathek (Fernseher).
14. "Wo läuft Musik?"/"Läuft irgendwo der Fernseher?"/"Was läuft gerade?" → rooms_playing (ohne room = alle Räume);
   "Was läuft im Wohnzimmer?" → rooms_playing mit room. Gib die Antwort des Werkzeugs unverändert weiter.
13. Heizung/Temperatur ohne Raumnennung ("zwei Grad wärmer", "wie warm ist es") → climate_*/get_temperature
   OHNE device und room: dann gilt das Thermostat im Raum des Sprechers – nicht nachfragen.

16. Filme/Serien aus der Mediathek ("starte den Film X", "spiel die Serie Y", "Y Staffel 2 Folge 3") → video_play
   (search = Titel, kind movie/series/any, season/episode nur wenn genannt). Nur in Räumen mit "Filme/Serien" in der
   Raumliste; sonst kurz sagen. Netflix/Prime/Fernsehsender lassen sich per Sprache nicht durchsuchen – kurz sagen.

Musikrichtungen in der Sammlung: {genres}
"""

# Beispielsätze für die KI (Admin › Sprache › „Beispielsätze“ sichtbar/änderbar; leer = diese Fassung). Sie stehen im
# System-Prompt und liegen damit im Prompt-Cache – kosten zur Laufzeit praktisch nichts („Training light“, 18.10.).
EXAMPLES = """Ohne Raumnennung gilt immer der Raum des Sprechers (room weglassen).
LICHT
„Licht an“, „Mach mal Licht“, „Lichter an“ → light_set(on=true) – alle Lichter im Raum
„Licht aus“, „Alles dunkel“, „Mach das Licht aus“ → light_set(on=false)
„Licht im Wohnzimmer an“ → light_set(on=true, room="Wohnzimmer") – Raum, KEIN device
„Mach die Stehlampe an“ → light_set(device="Stehlampe", on=true)
„Stehlampe auf 30 Prozent“ → light_set(device="Stehlampe", on=true, level=30); „Licht auf die Hälfte“ → light_set(on=true, level=50)
„Volle Helligkeit“, „Mach richtig hell“ → light_set(on=true, level=100)
„Mach das Licht blau“, „Küchenzeile warmweiß“ → light_color(color="blau") bzw. light_color(device="Küchenzeile", color="warmweiß")
„Etwas heller“, „Ein bisschen mehr Licht“ → light_adjust(direction="up", strength="klein")
„Heller“, „Zu dunkel“, „Ich seh nichts“ → light_adjust(direction="up", strength="mittel")
„Etwas dunkler“, „Dimm mal ein bisschen“ → light_adjust(direction="down", strength="klein")
„Dunkler“, „Zu hell“ → light_adjust(direction="down", strength="mittel")
„Mach es gemütlich“, „Abendstimmung“, „Kuschellicht“ → light_adjust(direction="down", strength="gross")
„Ist im Esszimmer noch Licht an?“, „Wo brennt noch Licht?“, „Welche Lichter sind an?“ → lights_status (Fragen schalten NIE)
„Licht im Bad aus und im Flur an“ → zwei Aufrufe: light_set(on=false, room="Bad …") + light_set(on=true, room="Flur …")
„Mach die Schreibbestände aus“ (unbekanntes Wort, klingt nach Gerät) → light_set(device="Schreibbestände", on=false) – die Bridge fragt nach; NIE room_off
ROLLLÄDEN
„Rollläden runter“, „Rollos zu“, „Jalousien schließen“, „Mach dicht“, „Mach alles zu“ → blind_action(device=<Raumname>, action="close") – Gruppe des Raums
„Rollläden hoch“, „Rollos auf“, „Mach die Rollläden auf“ → blind_action(device=<Raumname>, action="open")
„Rollladen Garten runter“ / „Rollladen Garten hoch“ → blind_action(device="Garten", action="close"/"open")
„Rollladen Strasse runter“ / „Rollladen Strasse hoch“ → blind_action(device="Strasse", action="close"/"open")
„Rollladen stopp“, „Halt die Rollos an“ → blind_action(…, action="stop")
„Verdunkeln“, „Es blendet“, „Sonnenschutz“ → Rollläden zu (blind_action close) – NICHT das Licht
„Rollladen halb runter“, „auf 50 Prozent“ → kein Werkzeug: kurz sagen, dass Positionen per Sprache noch nicht gehen
MEDIEN (Begriffe)
„Mach Musik an“, „Spiel Musik“, „Musik bitte“ → music_play(kind="random")
„Musik aus“, „Ruhe bitte“, „Fernseher aus“, „TV aus“ → room_off()
„Lauter“ / „Leiser“ / „Viel lauter“ / „Ein bisschen leiser“ → volume_adjust(direction, strength)
„Pause“ / „Stopp“ (allein = Musik) / „Weiter“ / „Nächstes Lied“ / „Das gefällt mir nicht“ → media_control(pause/stop/play/next/next)
„Ton aus“, „Stumm“ → media_control(mute); „Ton an“ → media_control(unmute)
„Was läuft?“, „Wo läuft Musik?“ → rooms_playing()
„Spiel von den Imagine Dragons Rise“ → music_play(search="Rise", artist="Imagine Dragons", kind="song")
In der Notiz steht, was gerade läuft („läuft: Qobuz – Imagine Dragons – „Rise Up““). Damit:
„Wie heißt das Lied?“, „Was läuft hier?“ → kein Werkzeug, aus „läuft: …“ antworten („Rise Up von Imagine Dragons“)
„Spiel mehr von der Band“, „Mehr davon“ → gleicher Dienst + gleicher Interpret (hier stream_play qobuz, artist="Imagine Dragons")
Läuft gerade etwas von Qobuz (z. B. „Qobuz: Imagine Dragons – …“) und der Nutzer sagt „Und jetzt den Titel Believer“
→ stream_play(service="qobuz", search="Believer", artist="Imagine Dragons", kind="song") – gleicher Dienst, gleicher Interpret
„Mach Radio an“ (ohne Sender) → kein Werkzeug, kurz fragen: „Welcher Sender?“
„Aus“ (ganz allein) → kein Werkzeug, kurz fragen: „Was soll ich ausschalten – Licht oder Musik?“
FILME / SERIEN (Mediathek – nur Räume mit „Filme/Serien“)
„Starte den Film Inception“, „Spiel Inception“ (Filmtitel) → video_play(search="Inception", kind="movie")
„Spiel Breaking Bad“, „Starte die Serie Breaking Bad“ → video_play(search="Breaking Bad", kind="series") – nächste ungesehene Folge
„Breaking Bad Staffel 2 Folge 3“ → video_play(search="Breaking Bad", kind="series", season=2, episode=3)
„Spiel über Qobuz Bibi Blocksberg Folge 7“ → stream_play(service="qobuz", search="Bibi Blocksberg Folge 7", kind="any") – search = ALLE gesagten Namenswörter
„Spiel das Hörspiel Benjamin Blümchen Folge 12“ → music_play(search="Benjamin Blümchen Folge 12", kind="any") – Hörspiele/Hörbücher liegen in der Musiksammlung, NIE video_play
„Welche Alben gibt es von Adele?“, „Nenne mir die Alben von …“ → music_albums(artist="Adele") – spielt nichts
„Spiel eine andere Version davon“, „Nicht diese Version, das Original“ → stream_play(service="qobuz", search="", version="andere")
„Starte im Wohnzimmer den Film Dune“ → video_play(search="Dune", kind="movie", room="Wohnzimmer")
„Mach Netflix an“, „Such auf Netflix …“ → kein Werkzeug: Netflix lässt sich per Sprache nicht durchsuchen – Fernbedienung"""

TOOLS = [
    {"type": "function", "function": {
        "name": "light_set",
        "description": "Schaltet Lichter ein oder aus; optional Helligkeit in Prozent. "
                       "Ohne device: ALLE Lichter im (Sprecher-)Raum - passend für 'Licht an/aus'.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Nur wenn EIN bestimmtes Licht genannt wird (Name aus der Liste)"},
            "room": {"type": "string", "description": "Raum, falls genannt – ohne device sind das ALLE Lichter dieses "
                                                      "Raums ('Licht im Wohnzimmer aus')"},
            "on": {"type": "boolean"},
            "level": {"type": "integer", "description": "Helligkeit 0-100, optional"}},
            "required": ["on"]}}},
    {"type": "function", "function": {
        "name": "light_color",
        "description": "Stellt Farbe oder Weißton von Farblichtern ein (nur Leuchten, die Farbe können). "
                       "Ohne device: alle Farblichter im (Sprecher-)Raum.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Nur wenn EIN bestimmtes Licht genannt wird"},
            "room": {"type": "string", "description": "Raum, falls genannt"},
            "color": {"type": "string", "description": "Farbe oder Weißton wie gesagt: warmweiß, neutralweiß, kaltweiß, "
                      "rot, orange, gelb, grün, türkis, blau, lila, pink, rosa, eine Kelvinzahl ('3000 K') oder der Name "
                      "einer Lichtstimmung aus der Control4-App (z. B. Relax, Sunset, Nightlight)"}},
            "required": ["color"]}}},
    {"type": "function", "function": {
        "name": "light_adjust",
        "description": "Macht Lichter relativ heller oder dunkler (ohne Zielwert) - für Aussagen wie "
                       "'mach es heller', 'etwas dunkler', 'gemütlicher', 'richtig hell'. "
                       "Ohne device/room: die Lichter im Raum des Sprechers.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Exaktes Gerät, falls benannt (optional)"},
            "room": {"type": "string", "description": "Anderer Raum, falls benannt (optional)"},
            "direction": {"type": "string", "enum": ["up", "down"]},
            "strength": {"type": "string", "enum": ["klein", "mittel", "gross"],
                         "description": "klein = 'ein bisschen' (−30 % bzw. +1/3 zum Ziel); "
                         "mittel = normale Änderung (−50 % bzw. +1/2); gross = 'richtig/viel' "
                         "(100 % bzw. −60 %). 'Gemütlich/gemütlicher/dunkler Stimmung' → "
                         "direction down + gross (eher dunkel)."}},
            "required": ["direction"]}}},
    {"type": "function", "function": {
        "name": "switch_set",
        "description": "Schaltet einen SCHALTER ein/aus: Steckdose, Relais, Pumpe, Lüfter, WeMo (Geräte vom Typ "
                       "switch in der Liste) – auch wenn der Name nach Lampe klingt (z. B. Steckdose "
                       "„Schreibtischlampe“). Für echte Lichter (Typ light) light_set benutzen.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Name des Schalters aus der Liste"},
            "room": {"type": "string", "description": "Raum, nur wenn genannt (sonst Raum des Sprechers)"},
            "on": {"type": "boolean"}},
            "required": ["device", "on"]}}},
    {"type": "function", "function": {
        "name": "blind_action",
        "description": "Fährt einen Rollladen/eine Jalousie: open (auf), close (zu) oder stop.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Name des Rollladens aus der Liste"},
            "room": {"type": "string", "description": "Raum, nur wenn genannt (sonst Raum des Sprechers)"},
            "action": {"type": "string", "enum": ["open", "close", "stop"]}},
            "required": ["device", "action"]}}},
    {"type": "function", "function": {
        "name": "climate_setpoint",
        "description": "Setzt den Sollwert eines Thermostats in Grad Celsius (absolut). Ohne device: das Thermostat "
                       "im Raum des Sprechers bzw. im genannten Raum.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Thermostat-Name, nur wenn genannt (optional)"},
            "room": {"type": "string", "description": "Raum, falls genannt (optional)"}, "celsius": {"type": "number"}},
            "required": ["celsius"]}}},
    {"type": "function", "function": {
        "name": "climate_setpoint_delta",
        "description": "Ändert den Thermostat-Sollwert um +x bzw. -x Grad Celsius ('zwei Grad wärmer'). Ohne device: "
                       "das Thermostat im Raum des Sprechers bzw. im genannten Raum.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Thermostat-Name, nur wenn genannt (optional)"},
            "room": {"type": "string", "description": "Raum, falls genannt (optional)"}, "delta_c": {"type": "number"}},
            "required": ["delta_c"]}}},
    {"type": "function", "function": {
        "name": "lights_status",
        "description": "Sagt, ob bzw. welche Lichter brennen – NUR LESEN, schaltet nichts. Für Fragen wie 'Ist im Bad "
                       "noch Licht an?', 'Welche Lichter sind an?', 'Wo brennt noch Licht?'. Ohne room: Raum des "
                       "Sprechers bzw. ganzes Haus, wenn so gefragt.",
        "parameters": {"type": "object", "properties": {
            "room": {"type": "string", "description": "Raum, falls genannt (optional)"}}}}},
    {"type": "function", "function": {
        "name": "get_temperature",
        "description": "Liest die aktuelle Raumtemperatur (Thermostat, Grad Celsius). Ohne device/room: Raum des Sprechers.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Thermostat-Name, nur wenn genannt (optional)"},
            "room": {"type": "string", "description": "Raum, falls genannt (optional)"}}}}},
    {"type": "function", "function": {
        "name": "music_play",
        "description": "Spielt Musik aus der eigenen Sammlung („Unsere Musik“): ein Album, etwas von einem "
                       "Interpreten, ein Lied (es läuft dann dessen Album), eine Musikrichtung oder zufällig. "
                       "Ohne room: Raum des Sprechers.",
        "parameters": {"type": "object", "properties": {
            "search": {"type": "string", "description": "Gesprochener Name (Interpret, Album, Lied oder Richtung); leer bei random"},
            "artist": {"type": "string", "description": "Interpret/Band, wenn zusätzlich zum Titel genannt ('Rise von den "
                                                        "Imagine Dragons' → search 'Rise', artist 'Imagine Dragons')"},
            "kind": {"type": "string", "enum": ["any", "artist", "album", "song", "genre", "random"]},
            "version": {"type": "string", "enum": ["andere"],
                        "description": "'andere' = gleiches Lied in einer anderen Aufnahme (wie bei stream_play)"},
            "room": {"type": "string", "description": "Nur wenn ein anderer Raum genannt wird"}},
            "required": ["kind"]}}},
    {"type": "function", "function": {
        "name": "stream_play",
        "description": "Spielt Radio (Sender über TuneIn) oder Musik vom Streamingdienst Qobuz. Radio: 'spiel SWR3', "
                       "'mach Radio an', 'hr3 hören', 'TuneIn'. Qobuz: nur wenn Qobuz genannt wird ('spiel Adele auf "
                       "Qobuz') oder der Nutzer nach 'auf Qobuz suchen?' zustimmt. Ohne room: Raum des Sprechers.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "enum": ["radio", "qobuz"]},
            "search": {"type": "string", "description": "Sender bzw. Interpret/Album/Titel – wörtlich wie gehört"},
            "artist": {"type": "string", "description": "Interpret/Band, wenn zusätzlich zum Titel genannt"},
            "kind": {"type": "string", "enum": ["station", "artist", "album", "song", "any"]},
            "version": {"type": "string", "enum": ["andere"],
                        "description": "'andere' = gleiches Lied, aber eine ANDERE Aufnahme als die gerade laufende "
                                       "('spiel eine andere Version', 'nicht diese Version', 'das Original'); "
                                       "search dann leer lassen, außer ein Titel wird genannt"},
            "room": {"type": "string", "description": "Nur wenn ein anderer Raum genannt wird"}},
            "required": ["service", "search"]}}},
    {"type": "function", "function": {
        "name": "music_albums",
        "description": "Nennt die Alben eines Interpreten – NUR AUSKUNFT, spielt nichts ('Welche Alben gibt es von "
                       "Adele?', 'Nenne mir die Alben von …'). Quelle: eigene Sammlung, sonst Qobuz.",
        "parameters": {"type": "object", "properties": {
            "artist": {"type": "string", "description": "Interpret/Band – wörtlich wie gehört"},
            "room": {"type": "string", "description": "Nur wenn ein anderer Raum genannt wird"}},
            "required": ["artist"]}}},
    {"type": "function", "function": {
        "name": "treffer_waehlen",
        "description": "NUR direkt nach einer TREFFERLISTE (Werkzeug-Antwort): wählt den Eintrag, der zum Wunsch des "
                       "Nutzers passt – Inhalt („die Folge mit Fußball“), Version (Original, live, akustisch, Cover), "
                       "Interpret; verhörte Namen zuordnen („Ada“ → „Adele“). Sonst nie benutzen.",
        "parameters": {"type": "object", "properties": {
            "nr": {"type": "integer", "description": "Nummer aus der Trefferliste, -1 = keiner passt"},
            "sicher": {"type": "boolean", "description": "false, wenn nur vermutet (z. B. verhörter Name) – dann fragt die Bridge nach"},
            "suche": {"type": "string", "description": "nur bei -1: bessere Suchwörter"}},
            "required": ["nr", "sicher"]}}},
    {"type": "function", "function": {
        "name": "video_play",
        "description": "Startet einen Film oder eine Serie aus der Mediathek (KODI) auf dem Fernseher: 'Starte den Film "
                       "Inception', 'Spiel Breaking Bad', 'Staffel 2 Folge 3 von Breaking Bad'. Serie ohne Folge: die "
                       "nächste ungesehene Folge. Nur in Räumen mit 'Filme/Serien' in der Raumliste. NICHT für Hörspiele/"
                       "Hörbücher (Hörspiel-'Folge', 'auf Qobuz') – das ist Musik (music_play/stream_play). "
                       "Netflix/Prime lassen sich nicht durchsuchen. Ohne room: Raum des Sprechers.",
        "parameters": {"type": "object", "properties": {
            "search": {"type": "string", "description": "Titel wörtlich wie gehört (ohne 'den Film', 'die Serie')"},
            "kind": {"type": "string", "enum": ["movie", "series", "any"]},
            "season": {"type": "integer", "description": "Staffel, nur wenn genannt"},
            "episode": {"type": "integer", "description": "Folge, nur wenn genannt"},
            "room": {"type": "string", "description": "Nur wenn ein anderer Raum genannt wird"}},
            "required": ["search"]}}},
    {"type": "function", "function": {
        "name": "volume_set",
        "description": "Setzt die Lautstärke eines Raums auf einen Wert 0-100 (z. B. 'Lautstärke auf 30').",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "integer"}, "room": {"type": "string", "description": "optional"}},
            "required": ["level"]}}},
    {"type": "function", "function": {
        "name": "volume_adjust",
        "description": "Lauter oder leiser ohne Zielwert: 'lauter', 'etwas leiser', 'viel lauter'.",
        "parameters": {"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "strength": {"type": "string", "enum": ["klein", "mittel", "gross"]},
            "room": {"type": "string", "description": "optional"}},
            "required": ["direction"]}}},
    {"type": "function", "function": {
        "name": "media_control",
        "description": "Steuert die laufende Wiedergabe: pause, play (weiter), next (nächstes Lied), "
                       "previous (vorheriges), stop, mute, unmute, shuffle_on/shuffle_off (Zufallswiedergabe/Mix "
                       "der LAUFENDEN Musik an/aus – startet keine neue Musik), repeat_one (dieses Lied "
                       "wiederholen/Dauerschleife), repeat_all (alles wiederholen), repeat_off.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["pause", "play", "next", "previous", "stop", "mute", "unmute",
                                                  "shuffle_on", "shuffle_off", "repeat_one", "repeat_all",
                                                  "repeat_off"]},
            "room": {"type": "string", "description": "optional"}},
            "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "rooms_playing",
        "description": "Sagt, in welchen Räumen gerade Musik oder TV/Video läuft (Quelle, Titel, läuft/pausiert). Für "
                       "Fragen wie 'Wo läuft Musik?', 'Läuft irgendwo der Fernseher?', 'Was läuft im Wohnzimmer?'.",
        "parameters": {"type": "object", "properties": {
            "room": {"type": "string", "description": "Nur dieser Raum (optional; ohne Angabe: alle Räume)"}}}}},
    {"type": "function", "function": {
        "name": "room_off",
        "description": "Schaltet alle MEDIEN eines Raums aus (Musik/TV, Control4-ROOM_OFF) - "
                       "das Licht bleibt unverändert (erwartetes Control4-Verhalten). Passend für "
                       "'Raum aus' / 'alles aus im X' im Medienkontext. Für 'Licht aus' immer "
                       "light_set benutzen! Ohne room: der Raum des Sprechers.",
        "parameters": {"type": "object", "properties": {
            "room": {"type": "string", "description": "Raumname (optional, Default Sprecherraum)"}}}}},
]

ASSIST_TOOLS = {
    "weather": {"type": "function", "function": {
        "name": "weather",
        "description": "Wettervorhersage für den Wohnort: Bedingungen, Temperatur von/bis, Regenrisiko (ab wann), Wind.",
        "parameters": {"type": "object", "properties": {
            "day": {"type": "string", "description": "heute, morgen, übermorgen oder ein Wochentag (z. B. Samstag)"}},
            "required": ["day"]}}},
    "web_search": {"type": "function", "function": {
        "name": "web_search",
        "description": "Sucht im Internet nach allgemeinem Wissen oder aktuellen Informationen (nicht für das Haus).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Kurze deutsche Suchanfrage"},
            "question": {"type": "string", "description": "Die Frage des Nutzers im Wortlaut"}},
            "required": ["query"]}}},
}
INFO_TOOLS = {"lights_status", "music_albums", "get_temperature", "weather", "web_search", "rooms_playing"}   # Antworten immer vollständig sprechen
_WEEKDAYS = ["montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"]


def day_offset(v) -> int:
    """„heute"/„morgen"/„übermorgen"/Wochentag/Zahl → 0…6 Tage ab heute."""
    t = str(v or "heute").strip().lower()
    if t.isdigit():
        return max(0, min(6, int(t)))
    if "übermorgen" in t or "uebermorgen" in t or "day after tomorrow" in t:
        return 2
    if "morgen" in t or "tomorrow" in t:
        return 1
    for i, d in enumerate(en.DAYS):                    # Englisch: monday … sunday
        if d in t:
            return (i - time.localtime().tm_wday) % 7
    for i, d in enumerate(_WEEKDAYS):
        if d in t:
            return (i - time.localtime().tm_wday) % 7
    return 0


def room_caps(bridge: "Bridge", room: str) -> set:
    """Medienfähigkeiten eines Raums (nach load_caps): Musik, Radio, Qobuz, TV/Video, Filme/Serien – über den
    Medien-Partner (Küche → Esszimmer / Küche) zählen Musik/Radio/Qobuz des Partners."""
    allc = {"Musik", "Radio", "Qobuz", "TV/Video", "Filme/Serien"}
    miss = bridge.missing.get(room)
    caps = allc - set(miss) if miss is not None else set()
    p_ = bridge.partner.get(room)
    if p_ and bridge.missing.get(p_) is not None:
        caps |= {"Musik", "Radio", "Qobuz"} - set(bridge.missing[p_])
    return caps


def tools_for(bridge: "Bridge", cfg: dict, speaker_rooms: list | None = None) -> list:
    """Werkzeuge je nach Einrichtung UND Raum des Sprechers: Wetter, wenn eingeschaltet; Websuche nur, wenn ein Anbieter
    bereit ist; video_play nur, wenn der Raum eine Mediathek hat; stream_play nur mit Radio/Qobuz. Was es im Raum nicht
    gibt, kann das Modell so gar nicht erst wählen (Raumzeile im Prompt ist nur Text – qwen3:8b wählte „über Qobuz …
    Folge 104“ im Esszimmer trotzdem video_play). Ohne Raum (Mikrofon ohne Zuordnung): alle Werkzeuge."""
    a = (cfg.get("assist") or {})
    drop = set()
    # Gemessen 18.10.: Ollama (qwen3:8b, Vulkan) cacht nur EINEN Prompt-Präfix – jede Variante der Werkzeugliste liest
    # beim Raumwechsel 13–25 s neu ein. Daher Standard: eine Liste für alle; llm.tools_per_room=true schaltet die
    # Raumvarianten ein (sinnvoll erst, wenn Ollama mehrere Präfixe hält, z. B. OLLAMA_NUM_PARALLEL > 1).
    if speaker_rooms and (cfg.get("llm") or {}).get("tools_per_room"):
        caps = set()
        for r in speaker_rooms:
            caps |= room_caps(bridge, r)
        if "Filme/Serien" not in caps:
            drop.add("video_play")
        if not caps & {"Radio", "Qobuz"}:
            drop.add("stream_play")
    out = [t for t in TOOLS if t["function"]["name"] not in drop]
    if (a.get("weather") or {}).get("enabled", True):
        out.append(ASSIST_TOOLS["weather"])
    if (bridge.assist.get("web") or {}).get("ready"):
        out.append(ASSIST_TOOLS["web_search"])
    return out


SUMMARY_PROMPT = ("Du bist ein Sprachassistent. Beantworte die Frage in höchstens zwei kurzen deutschen Sätzen, "
                  "AUSSCHLIESSLICH mit Informationen aus den Suchtreffern. Steht die Antwort nicht darin, sag ehrlich, "
                  "dass du es nicht sicher herausfinden konntest. Keine Links, keine Aufzählungen, kein Markdown, "
                  "Zahlen so, wie man sie spricht.")


def ollama_tuning(c: dict) -> dict:
    """keep_alive + num_ctx aus der Config (Haupt- oder Antwort-Modell) für den Ollama-Aufruf."""
    try:
        ka = int(c.get("keep_alive_min", 60))
    except (TypeError, ValueError):
        ka = 60
    out = {"keep_alive": -1 if ka < 0 else f"{ka}m"}
    try:
        n = int(c.get("num_ctx") or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 1024:
        out["num_ctx"] = n
    return out


async def summarize(cfg: dict, question: str, hits: list) -> str:
    """Suchtreffer → gesprochene Antwort. Nutzt das Antwort-Modell (assist.answer_llm), sonst das Hauptmodell."""
    al = (cfg.get("assist") or {}).get("answer_llm") or {}
    url = (al.get("url") or cfg["llm"]["url"]).rstrip("/")
    model = al.get("model") or cfg["llm"]["model"]
    ctx = "\n\n".join(f"[{i + 1}] {h.get('title', '')}: {h.get('snippet', '')}" for i, h in enumerate(hits[:6]))
    tune = ollama_tuning(al if al.get("model") or al.get("url") else cfg["llm"])
    body = {"model": model, "stream": False, "think": False, "keep_alive": tune["keep_alive"],
            "options": {"temperature": 0.2, **({"num_ctx": tune["num_ctx"]} if "num_ctx" in tune else {})},
            "messages": [{"role": "system", "content": (prompts_of(cfg).get("answer") or "").strip()
                          or (en.SUMMARY_PROMPT if is_en() else SUMMARY_PROMPT)},
                         {"role": "user", "content": f"Frage: {question}\n\nSuchtreffer:\n{ctx}"}]}
    timeout = aiohttp.ClientTimeout(total=float(cfg["llm"].get("timeout_s", 90)))
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(url + "/api/chat", json=body) as r:
            if r.status == 400:
                body.pop("think")
                async with s.post(url + "/api/chat", json=body) as r2:
                    r2.raise_for_status()
                    msg = (await r2.json()).get("message", {})
            else:
                r.raise_for_status()
                msg = (await r.json()).get("message", {})
    text = re.sub(r"\s+", " ", (msg.get("content") or "").replace("*", "")).strip()
    return text or "Dazu konnte ich nichts Sicheres finden."


CTX_TTL = 300.0


def fill_prompt(template: str, **vals) -> str:
    """Nur bekannte Platzhalter ersetzen (str.format würde an anderen {} im eigenen Text scheitern)."""
    for k, v in vals.items():
        template = template.replace("{" + k + "}", v)
    return template


def prompts_of(cfg: dict) -> dict:
    """Eigene Anweisungen der eingestellten Sprache (Deutsch: prompts, Englisch: prompts_en)."""
    return (cfg.get("prompts_en" if is_en() else "prompts") or {})


def house_prompt(cfg: dict) -> str | None:
    return (prompts_of(cfg).get("house") or "").strip() or (en.HOUSE_PROMPT if is_en() else None)


def examples_text(cfg: dict) -> str:
    return (prompts_of(cfg).get("examples") or "").strip() or (en.EXAMPLES if is_en() else "")


class Bridge:
    """Dünner Client auf die Bridge-REST (Geräte, Räume, Aktionen)."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.session: aiohttp.ClientSession | None = None
        self._ctx = (0.0, [], [])
        self.genres: list[str] = []
        self.artists: list[str] = []      # Schreibhilfe für Whisper: zuletzt gespielt, dann die größten Sammlungen
        self._caps: dict = {}             # Raumname → (Zeit, Text für die Sprecher-Notiz)
        self.missing: dict = {}           # Raumname → was dort per Sprache fehlt (Musik, Radio, Qobuz, TV/Video, Filme/Serien)
        self.kodi_src: set = set()        # Quellen-IDs der KODI-Bereiche Filme/Serien (/api/kodi/sources)
        self.partner: dict = {}           # Raum ohne Musik → Raum, über den Musik läuft (Küche → Esszimmer / Küche)
        self.examples: str = ""           # eigene Beispielsätze aus dem Admin (leer = EXAMPLES)
        self.assist: dict = {}

    async def _s(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(base_url=self.base,
                                                 headers={TOKEN_HEADER: TOKEN} if TOKEN else None)
        return self.session

    async def get(self, path: str):
        s = await self._s()
        async with s.get(path) as r:
            r.raise_for_status()
            return await r.json()

    async def post(self, path: str, body: dict | None = None):
        s = await self._s()
        async with s.post(path, json=body or {}) as r:
            r.raise_for_status()
            try:
                return await r.json()
            except Exception:  # noqa: BLE001
                return {"ok": r.status < 400}

    async def context(self) -> tuple[list[dict], list[dict]]:
        """(devices, rooms) – 5 Minuten zwischengespeichert."""
        if time.time() - self._ctx[0] > CTX_TTL:
            # Nur SICHTBARE Räume (Admin › Oberfläche & Räume) und deren Geräte: versteckte Räume wie „Sensoren und
            # Switches“, Rack-Room, Hubs sind für Sprache und KI tabu (Nutzer 18.10.: wer ein Relais per Sprache will,
            # legt es in Composer in einen echten Raum).
            rooms = [r for r in await self.get("/api/rooms") if r.get("enabled", not r.get("hidden"))]   # Admin-Schalter
            ids = {r["id"] for r in rooms}
            # nur Geräte mit Haken „Sprache“ (Admin › Geräte): Pumpen, Relais u. Ä. bleiben für das Modell unsichtbar
            devs = [d for d in await self.get("/api/devices?enabled_only=true&voice_only=true") if d.get("room_id") in ids]
            import urllib.parse as _up
            for d in devs:                   # Director liefert manche Namen URL-kodiert („K%C3%BCche unten“)
                if "%" in str(d.get("name") or ""):
                    d["name"] = _up.unquote(d["name"])
            try:
                self.genres = [g["genre"] for g in await self.get("/api/media/browse?kind=genre")
                               if g.get("genre") and g["genre"] != "Ohne Genre"]
            except Exception:  # noqa: BLE001
                self.genres = []
            try:
                self.assist = await self.get("/api/assist/status")
            except Exception:  # noqa: BLE001
                self.assist = {}
            try:
                self.kodi_src = set((await self.get("/api/kodi/sources")).get("sources") or [])
            except Exception:  # noqa: BLE001
                self.kodi_src = set()
            try:
                recent = [a.get("artist") for a in await self.get("/api/media/recent") if a.get("artist")]
                allx = sorted(await self.get("/api/media/browse?kind=artist"), key=lambda a: -(a.get("songs") or 0))
                seen, out = set(), []
                for n in recent + [a.get("artist") for a in allx]:
                    n = str(n or "").strip()
                    if n and n.lower() not in seen and not re.match(r"^(unbekannt|unknown|various|diverse)", n, re.I):
                        seen.add(n.lower())
                        out.append(n)
                self.artists = out
            except Exception:  # noqa: BLE001
                self.artists = []
            self._ctx = (time.time(), devs, rooms)
            try:                            # Medien je Raum für die Raumzeilen im System-Prompt (statisch → Cache)
                await self.load_caps([r["name"] for r in rooms])
            except Exception:  # noqa: BLE001
                pass
        return self._ctx[1], self._ctx[2]

    def prompt(self, devs: list[dict], rooms: list[dict], speaker_rooms: list[str] | None = None,
               template: str | None = None, examples: str = "") -> str:
        """System-Prompt; template = eigene Anweisungen aus dem Admin (leer = SYSTEM_PROMPT).
        Platzhalter {rooms} {devices} {genres} werden ersetzt, andere geschweifte Klammern bleiben stehen."""
        speaker_rooms = speaker_rooms or []
        self.examples = examples or ""

        room_w, also_w = ("room", "also called") if is_en() else ("Raum", "auch genannt")

        def line(d: dict) -> str:
            names = [d.get("note") or d["name"], d["name"], *d.get("aliases", [])]
            return (f"- {d['type']} \"{names[0]}\" ({room_w}: {d['room']})"
                    + (f", {also_w}: {', '.join(d['aliases'])}" if d.get("aliases") else ""))

        def is_ctrl(d: dict) -> bool:
            return d.get("type") in ("light", "blind", "thermostat", "switch")

        kinds = en.KINDS if is_en() else {"light": "Licht", "blind": "Rollladen", "thermostat": "Heizung", "switch": "Schalter"}
        rooms_parts = []
        for r in sorted(rooms, key=lambda x: x["name"].lower()):
            al = [a for a in (r.get("aliases") or []) if a]
            have = [kinds[k] for k in kinds if any(d.get("type") == k and d["room"] == r["name"] for d in devs)]
            miss = self.missing.get(r["name"])
            media = [en.MEDIA[m] if is_en() else m for m in ("Musik", "Radio", "Qobuz", "TV/Video", "Filme/Serien")
                     if miss is not None and m not in miss]
            via = self.partner.get(r["name"])
            if via:
                media.append(f"media (music, radio, Qobuz) play in the room “{via}”" if is_en()
                             else f"Medien (Musik, Radio, Qobuz) laufen im Raum „{via}“")
            rooms_parts.append(f"- {r['name']}" + (f" ({'also' if is_en() else 'auch'}: {', '.join(al)})" if al else "") + ": "
                               + (", ".join(have + media) or ("no controllable devices" if is_en() else "keine steuerbaren Geräte")))
        rooms_txt = "\n".join(rooms_parts)

        # Prompt-Cache: Der GLEICHE Anfang für alle Mikrofone (alle Geräte in fester Reihenfolge, nach Raum sortiert) –
        # nur der kurze Schluss hängt vom Sprecher ab. Vorher standen die Geräte des Sprecherraums vorn → je Raum ein
        # anderer Prompt-Anfang → Ollama las ~7.000 Token neu ein (qwen3:8b auf Vulkan/RDNA2: 42 s statt 0,8 s).
        ctrl = sorted((d for d in devs if is_ctrl(d)), key=lambda d: (str(d["room"]).lower(), d["type"],
                                                                     str(d.get("note") or d["name"]).lower(), d["id"]))
        devices_txt = "\n".join(line(d) for d in ctrl)

        p = fill_prompt(template or SYSTEM_PROMPT, rooms=rooms_txt, devices=devices_txt,
                        genres=", ".join(self.genres) or "–")
        if is_en():
            return p + en.EXAMPLES_HEAD + (self.examples or en.EXAMPLES) + en.PROMPT_TAIL
        p += "\n\nBeispiele (so reagieren):\n" + (self.examples or EXAMPLES)
        return p + ("\n\nVor jedem Satz steht in [eckigen Klammern], in welchem Raum der Sprecher ist. Was ein Raum kann, "
                    "steht oben in der Raumliste; was dort nicht steht, gibt es in dem Raum nicht (dann kurz sagen). "
                    "Wird kein Raum genannt, sind die Geräte DIESES Raums gemeint (room weglassen); nur wenn ausdrücklich "
                    "ein anderer Raum genannt wird, die übrigen Geräte. Nennt der Nutzer ein Gerät, das dort nicht steht, rufe "
                    "trotzdem das Werkzeug mit diesem Namen auf (ohne room) – die Bridge sucht es und fragt selbst nach; "
                    "frag nicht selbst. Steht in der Klammer „zuletzt vor … s: …“, ist das die vorige Aktion – nur nutzen, wenn "
                    "sich der Satz darauf bezieht („wieder an“, „und im Bad?“, „mehr davon“); sonst ist es ein neuer "
                    "Befehl. Eine kurze Anschlussfrage ohne eigenes Verb („Und im Wohnzimmer?“, „Und morgen?“) heißt: "
                    "DASSELBE Werkzeug wie bei „zuletzt“, nur mit dem neuen Raum bzw. Tag. Relative Helligkeit (\"heller\", \"dunkler\", "
                    "\"gemütlicher\") → light_adjust.")

    async def load_caps(self, room_names: list[str] | None) -> None:
        """Was der Raum an Medien kann (für die Sprecher-Notiz; 5 min zwischengespeichert). Steht in der NACHRICHT,
        nicht im System-Prompt – so bleibt der Prompt-Cache für alle Mikrofone gleich."""
        rooms = self._ctx[2] or (await self.context())[1]
        for name in room_names or []:
            hit = self._caps.get(name)
            if hit and time.time() - hit[0] < CTX_TTL:
                continue
            r = next((x for x in rooms if x["name"] == name), None)
            if not r:
                continue
            try:
                media = await self.get(f"/api/rooms/{r['id']}/media")
                backend = await self.get(f"/api/rooms/{r['id']}/backend")
            except Exception:  # noqa: BLE001
                continue
            listen = [x.get("name", "") for x in media.get("listen") or []]
            watch = [x.get("name", "") for x in media.get("watch") or []]
            # Nur, was hier FEHLT (Werkzeuge stehen alle im System-Prompt): kurz = schnell eingelesen
            missing = []
            bluos = backend.get("type") == "bluos"
            srcs = set((backend.get("sources") or {}).values()) if bluos else set()
            if not listen and not bluos:
                missing.append("Musik")
            if not bluos or "TuneIn:" not in srcs:
                missing.append("Radio")
            if not bluos or "Qobuz:" not in srcs:
                missing.append("Qobuz")
            if not watch:
                missing.append("TV/Video")
            if not ({x.get("id") for x in media.get("watch") or []} & self.kodi_src):
                missing.append("Filme/Serien")        # KODI-Mediathek (Filme/Serien) ist hier keine Quelle
            self.missing[name] = missing
            self.partner.pop(name, None)
            self._caps[name] = (time.time(), ", ".join(missing))
        # Raum ohne Musik, aber eindeutiger „Medien-Partner“ (Küche → Esszimmer / Küche): Musik läuft dort
        for name in room_names or []:
            miss = self.missing.get(name) or []
            if "Musik" not in miss:
                continue
            word = re.sub(r"[^\wäöüß ]+", " ", name.lower()).strip()
            partners = [x["name"] for x in rooms if x["name"] != name and word
                        and word in re.sub(r"[^\wäöüß ]+", " ", x["name"].lower())]
            todo = [x for x in partners if x not in self.missing]
            if todo:
                await self.load_caps(todo)
            partners = [x for x in partners if "Musik" not in self.missing.get(x, ["Musik"])]
            if len(partners) == 1:
                self.partner[name] = partners[0]
                rest = [m for m in miss if m not in ("Musik", "Radio", "Qobuz")]
                rest += [m for m in ("Radio", "Qobuz") if m in self.missing[partners[0]]]
                self._caps[name] = (time.time(), ", ".join(rest) + f" (Musik/Radio laufen über „{partners[0]}“)")

    async def now_playing(self, room_names: list[str] | None) -> dict:
        """Was läuft im Raum (bzw. seinem Medien-Partner) – für die Sprecher-Notiz: „Und jetzt den Titel X“ / „mehr von
        der Band“ / „wie heißt das Lied?“ verstehen, auch lange nach dem letzten Befehl (Verlauf gilt nur 30 s)."""
        out = {}
        _, rooms = await self.context()
        for name in room_names or []:
            target = self.partner.get(name) or name
            r = next((x for x in rooms if x["name"] == target), None)
            if not r:
                continue
            try:
                st = await self.get(f"/api/rooms/{r['id']}/state")
            except Exception:  # noqa: BLE001
                continue
            if not st.get("selected_device") or st.get("power") is False:
                out[name] = "nichts"
                continue
            b, k = st.get("bluos") or {}, st.get("kodi") or {}
            state = {"play": "", "pause": " (pausiert)", "stop": " (gestoppt)"}.get(str(st.get("play_state") or ""), "")
            if b and not b.get("error"):
                svc = {"LocalMusic": "eigene Sammlung", "TuneIn": "Radio", "Qobuz": "Qobuz", "Capture": "Line In",
                       "RadioParadise": "Radio Paradise", "Airable": "Radio"}.get(str(b.get("service") or ""), b.get("service") or "Musik")
                what = " – ".join(x for x in (b.get("artist"), f"„{b['title']}“" if b.get("title") else "") if x)
            elif k and not k.get("error"):
                svc, what = "KODI", " – ".join(x for x in (k.get("title"), k.get("subtitle")) if x)
            else:
                mi = st.get("media_raw") or {}
                mi = mi.get("mediainfo", mi) if isinstance(mi, dict) else {}
                svc = "Medien"
                what = " – ".join(x for x in (mi.get("artist"), f"„{mi['title']}“" if mi.get("title") else "")
                                  if isinstance(x, str) and x)
            out[name] = f"{svc}{' – ' + what if what else ''}{state}"
        return out

    def note(self, devs: list[dict], speaker_rooms: list[str] | None, playing: dict | None = None,
             recent: str = "") -> str:
        """Sprecher-Kontext für die NUTZERNACHRICHT (nicht den System-Prompt): System-Prompt + Werkzeuge bleiben für alle
        Mikrofone gleich → Ollama nutzt den Prompt-Cache (Ollama setzt die ~3.500 Token Werkzeuge HINTER den System-Prompt;
        stand der Raum dort, las qwen3:8b auf Vulkan/RDNA2 bei jedem Raumwechsel 22–42 s neu ein)."""
        tail = f" · {recent}" if recent else ""          # „zuletzt vor 14 s: …“ (Dialog, nur bei gleichem Thema)
        if not speaker_rooms:
            return f"[Sprecher: Mikrofon ohne Raum – ohne Raumnennung nachfragen{tail}]"
        # Was der Raum kann, steht in der Raumliste des System-Prompts (Cache) – hier nur, wo der Sprecher ist und was
        # dort gerade läuft (ändert sich ständig → gehört in die Nachricht, nicht in den gecachten System-Prompt)
        pl = [f"{n}: {playing[n]}" if len(speaker_rooms) > 1 else playing[n] for n in speaker_rooms if (playing or {}).get(n)]
        return f"[Sprecher im Raum: {', '.join(speaker_rooms)}" + (f" · läuft: {'; '.join(pl)}" if pl else "") + tail + "]"


def resolve(name: str, items: list[dict], keys: list[str], prefer: list[str] | None = None) -> tuple[dict | None, float]:
    """Namen → Eintrag per Exaktvergleich, sonst Fuzzy (Modelle verschreiben sich gern). Auch „ID 123“ geht.
    prefer = Raumnamen: bei gleich guten Treffern (gleichnamige Geräte in mehreren Räumen) gewinnt dieser Raum."""
    from rapidfuzz import process, fuzz
    n = (name or "").strip().lower()
    if not n:
        return None, 0.0
    m = re.fullmatch(r"(?:id\s*)?(\d+)", n)
    if m:
        hit = next((it for it in items if str(it.get("id")) == m.group(1)), None)
        if hit:
            return hit, 100.0
    pref = set(prefer or [])
    exact = [it for it in items for k in keys
             for v in ([it.get(k)] if isinstance(it.get(k), str) else it.get(k) or [])
             if v and v.strip().lower() == n]
    if exact:
        return next((it for it in exact if it.get("room") in pref), exact[0]), 100.0
    cands = {"idx": [], "txt": []}
    for i, it in enumerate(items):
        parts = []
        for k in keys:
            v = it.get(k)
            parts += [v] if isinstance(v, str) else list(v or [])
        cands["idx"].append(i)
        cands["txt"].append(" | ".join(parts).lower())
    hits = process.extract(n, cands["txt"], scorer=fuzz.WRatio, score_cutoff=62, limit=8)
    if hits:
        best = [h for h in hits if h[1] >= hits[0][1] - 0.5]
        h = next((h for h in best if items[cands["idx"][h[2]]].get("room") in pref), best[0])
        return items[cands["idx"][h[2]]], h[1]
    return None, 0.0


def _lvl(v):
    """Level aus Zustands-Zeile: int oder None (null/fehlt = keine Helligkeit gemeldet)."""
    return int(v) if isinstance(v, (int, float)) else None


def _adjust_plan(cur, on, up, strength, adj=None):
    """Neuer Pegel für light_adjust — PROPORTIONAL, Stufen konfigurierbar (cfg.adjust):
      up   = % des WEGS bis 100 (anteilig heller); down = % vom AKTUELLEN Wert.
      cozy_below/cozy_down: schon dunkel („gemütlich“) → nur noch sanft reduzieren.
      min_level: „dunkler“ endet nie darunter/nie bei 0 — „aus“ ist ein eigener Befehl.
    cur=None → Licht meldet keine Helligkeit (an/aus-Licht): 'heller' setzt idempotent
    100 % (unabhängig vom evtl. veralteten on-Status des Directors), 'dunkler' tut nichts.
    Rückgabe: int 0-100 oder None (= nichts tun)."""
    adj = adj or {}
    min_level = float(adj.get("min_level", 5) or 5)
    if cur is None:
        return 100 if up else None
    if not on:
        if not up:
            return None
        return {"klein": 30, "mittel": 60, "gross": 100}.get(strength, 60)
    if up:
        if cur >= 100:
            return None                       # schon voll
        frac = float((adj.get(strength) or {}).get("up", {"klein": 33, "mittel": 50, "gross": 100}.get(strength, 50)))
        return min(100, round(cur + (100 - cur) * frac / 100))
    # dunkler — relativ zum aktuellen Wert
    if cur <= min_level:
        return None                           # schon ganz dunkel, nicht weiter
    down = float((adj.get(strength) or {}).get("down", {"klein": 30, "mittel": 50, "gross": 60}.get(strength, 50)))
    if strength == "gross":
        below = float(adj.get("cozy_below", 30))
        if cur <= below:
            down = float(adj.get("cozy_down", 25))   # schon dunkel → sanfter
    new = round(cur * (1 - down / 100))
    return new if new >= min_level else None


async def execute_tool(fn: dict, bridge: Bridge, dry_run: bool,
                       speaker_rooms: list[str] | None = None,
                       cfg: dict | None = None, utterance: str | None = None, current: str | None = None) -> str:
    """Führt EINEN Tool-Call über die Bridge aus (oder meldet nur, im Trockenlauf).
    utterance = gesprochener Satz: ein Raum aus dem Werkzeugaufruf gilt nur, wenn er im Satz vorkommt (das Modell setzt
    sonst Räume aus der Geräteliste selbst ein und umgeht die Regel „ohne Raumnennung nur der Mikrofon-Raum“)."""
    name, args = fn.get("name", ""), fn.get("arguments", {})
    speaker_rooms = speaker_rooms or []
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:  # noqa: BLE001
            args = {}
    devs, rooms = await bridge.context()
    sim = " (Simulation – nichts ausgeführt)" if dry_run else ""

    def dev_of(d):
        return f"{d.get('note') or d['name']}"

    def room_words(room: dict) -> list[str]:
        """Woran man einen Raum im Satz erkennt: Name, Teile vor/nach „/“, Aliase und einzelne Namenswörter (≥ 5
        Zeichen), die in KEINEM anderen Raumnamen vorkommen („Dachgeschoss“ → „Wohnen Dachgeschoss“; „Arbeit“ zählt
        nicht, weil es Arbeit Kim und Arbeit Alex gibt)."""
        parts = [room["name"], *re.split(r"\s*/\s*", room["name"]), *(room.get("aliases") or [])]
        others = " ".join(x["name"].lower() for x in rooms if x is not room and x["name"] != room["name"])
        single = [w for w in re.findall(r"[\wäöüß]+", room["name"].lower()) if len(w) >= 5 and w not in others]
        return [x for x in parts + single if x]

    def spoken(room: dict | None, raw: str) -> bool:
        """Kommt der Raum im gesprochenen Satz vor (Name, Namensteil, eindeutiges Namenswort, Alias)?"""
        if utterance is None:
            return True                                # Test/fest gewähltes Angebot: Raum gilt
        from rapidfuzz import fuzz
        u = utterance.lower()
        names = [raw] + (room_words(room) if room else [])
        return any(n and len(n) >= 3 and fuzz.partial_ratio(n.lower(), u) >= 85 for n in names)

    def room_hits(text: str | None) -> list[str]:
        """Räume, deren Name/Namensteil/Alias als ganzes Wort im Text steht (auch mehrere: „Bad“ → Bad mitte, Bad oben)."""
        if not text:
            return []
        u = " " + re.sub(r"[^\wäöüß ]+", " ", text.lower()) + " "
        hits = []
        for r_ in rooms:
            words = [" " + re.sub(r"[^\wäöüß ]+", " ", x.lower()).strip() + " " for x in room_words(r_) if len(x) >= 3]
            if any(w.strip() and w in u for w in words):
                hits.append(r_["name"])
        return hits

    def said_rooms() -> list[str]:
        """Räume, die im Satz EINDEUTIG vorkommen (ganzer Name, Teil vor/nach „/“ oder Alias als ganzes Wort) –
        für den Fall, dass das Modell den genannten Raum weglässt („Fahr im Wohnzimmer die Rollläden runter“)."""
        hits = room_hits(utterance)
        # „Küche“ steckt in „Esszimmer / Küche“, „Küche“ und „Küche unten“ → nur eindeutig, sonst dem Modell überlassen
        return hits if len(hits) == 1 else []

    def ambiguous_rooms() -> list[str]:
        """Der AKTUELLE Satz nennt ein Raumwort, das in MEHREREN Raumnamen steckt („im Bad“ → Bad mitte, Bad oben;
        „Küche“ → Esszimmer / Küche, Küche, Küche unten) und das Modell hat keinen Raum gewählt → nachfragen statt den
        Sprecherraum zu nehmen. Gehört der Sprecherraum selbst dazu, ist er gemeint (Mikrofon Esszimmer / Küche:
        „in der Küche“)."""
        src = current if current is not None else utterance
        if not src:
            return []
        by_word: dict = {}
        for r_ in rooms:
            for w in set(re.findall(r"[\wäöüß]{3,}", r_["name"].lower())):
                by_word.setdefault(w, []).append(r_["name"])
        raw = str(args.get("room") or "").strip().lower()
        full = next((r_ for r_ in rooms if raw and (raw == r_["name"].lower()
                                                   or raw in (a_.lower() for a_ in r_.get("aliases") or []))), None)
        if full and not spoken(full, raw):
            raw = ""                                   # Raum so nicht gesagt (Modell riet „Bad mitte“ bei „im Bad“) → Satz prüfen
        elif full:
            return []                                  # echter, genannter Raumname/Alias → eindeutig
        if raw:                                        # Modell hat room gesetzt – „Bad“ ist aber kein Raum, sondern zwei
            cands: set = set()
            for w in re.findall(r"[\wäöüß]{3,}", raw):
                cands |= set(by_word.get(w) or [])
            if len(cands) <= 1:
                return []                              # room_names() prüft den Rest (unscharf, gesagt?)
            names = sorted(cands)
        else:
            if len(room_hits(src)) == 1:
                return []                              # ein Raum eindeutig genannt
            names = next((by_word[w] for w in re.findall(r"[\wäöüß]{3,}", src.lower()) if len(by_word.get(w) or []) > 1), [])
            if not names:
                return []
        return [] if any(n in speaker_rooms for n in names) else names

    def amb_problem(tool: str):
        amb = ambiguous_rooms()
        if not amb:
            return None
        return Problem(f"Welches – {' oder '.join(amb[:2])}?", [_offer(x, tool, {**args, "room": x}) for x in amb[:2]])

    geklaert: list = []                                # Räume, die amb_device aus Satz + Gerät erschlossen hat

    def amb_device(tool: str, items: list[dict]):
        """Wie amb_problem, aber mit Gerät: gibt es das Gerät nur in EINEM der möglichen Räume, ist der gemeint."""
        amb = ambiguous_rooms()
        if not amb:
            return None
        nm = str(args.get("device") or "")
        has = [x for x in amb if resolve(nm, [d for d in items if d["room"] == x], keys)[0]]
        if len(has) == 1:
            args["room"] = has[0]
            geklaert.append(has[0])                    # aus dem Satz erschlossen → gilt wie gesagt
            return None
        opts = (has or amb)[:2]
        return Problem(f"Welches – {' oder '.join(opts)}?", [_offer(x, tool, {**args, "room": x}) for x in opts])

    def room_names() -> list[str]:
        """Genannte Räume (Name oder Alias, auch ungenau) → echte Raumnamen; ohne Nennung die Räume des Sprechers."""
        out, ignored = [], False
        for x in _as_rooms(args.get("room")):
            r_, _ = resolve(x, rooms, ["name", "aliases"])
            real = r_["name"] if r_ else x
            if real in speaker_rooms or real in geklaert or spoken(r_, x):
                out.append(real)
            else:
                log("room_ignored", room=real, utterance=utterance)   # vom Modell ergänzt, nicht gesagt
                ignored = True
        if ignored:      # aus dem Aufruf streichen: Angebote, Trefferliste und Dialog bauen auf args auf
            args["room"] = out[0] if len(out) == 1 else (out or "")
        return out or said_rooms() or speaker_rooms

    keys = ["note", "name", "aliases"]

    _CMD = re.compile(r"\b(spiel\w*|such\w*|starte?|mach|hören|hör|ich|will|möchte|gerne?|bitte|mal|jetzt|und|"
                      r"über|bei|auf|im|in|vom|aus|der|dem|den|das|titel|lied|song|album|musik|qobuz|tunein|radio|"
                      r"sender|an|ab|doch|nochmal|noch|wechsel\w*|umschalten|schalt\w*|stell\w*|um|zu|hörspiel\w*|"
                      r"hörbuch\w*|letzte[nrs]?|neueste[nrs]?)\b", re.I)

    def spoken_query(q: str) -> str:
        """Fehlt dem Suchbegriff des Modells etwas, das im Satz steht (Zahl wie „Folge 104“) oder ist er fast leer
        („???“), den gesagten Wortlaut nehmen – ohne Befehls-, Dienst- und Raumwörter."""
        src = current if current is not None else utterance   # nur der AKTUELLE Satz, nicht der ganze Dialog
        if src is None or not src.strip():
            return q
        u = src.lower()
        for r_ in rooms:
            for w in room_words(r_):
                u = re.sub(r"\b" + re.escape(w.lower()) + r"\b", " ", u)
        u = " ".join(re.sub(r"[^\wäöüß ]+", " ", self_cmd(u)).split())
        qw = set(re.findall(r"[\wäöüß]+", q.lower()))
        uw = re.findall(r"[\wäöüß]+", u)
        if not uw:
            return q
        missing_digits = any(re.search(r"\d", w) for w in uw if w not in qw)
        from rapidfuzz import fuzz as _fz

        def near(w: str) -> bool:                  # Modellwort steht im Satz oder ähnelt einem Satzwort (verschrieben)
            return w in uw or any(len(w) >= 4 and _fz.ratio(w, x) >= 80 for x in uw)
        all_near = bool(qw) and all(near(w) for w in qw)
        misspelled = all_near and not qw <= set(uw)
        if (len(re.sub(r"\W", "", q)) <= 3 or (all_near and (missing_digits or misspelled))) and len(u) > len(q):
            log("search_from_utterance", model=q, used=u)
            return u
        return q

    def self_cmd(t: str) -> str:
        return _CMD.sub(" ", t)

    def artist_said(who: str) -> bool:
        """Interpret nur übernehmen, wenn er im Satz vorkommt oder der Nutzer sich auf das Laufende bezieht – sonst hängt
        das Modell den gerade laufenden Interpreten an („Spiel Fields of Gold“ während Eddie Vedder läuft → von Eddie
        Vedder)."""
        if not who or utterance is None:
            return True
        return in_utterance(who) or bool(_REF_NOW.search(utterance))

    def in_utterance(dev_name: str) -> bool:
        """Hat der Gerätename etwas mit dem gesagten Satz zu tun? (Wort ≥ 4 Zeichen ähnlich im Satz.) Ohne Satz (Test,
        festes Angebot nach „Ja“) immer ja."""
        if utterance is None or not dev_name:
            return True
        from rapidfuzz import fuzz
        u = re.sub(r"[^\wäöüß ]+", " ", utterance.lower())
        words = [w for w in re.findall(r"[\wäöüß]+", dev_name.lower()) if len(w) >= 4]
        if not words:
            return fuzz.partial_ratio(dev_name.lower(), u) >= 80
        return any(fuzz.partial_ratio(w, u) >= 80 for w in words)

    def device_is_room(items: list[dict]) -> bool:
        """Das Modell schreibt den Raum manchmal in device („Licht im Gästezimmer aus“ → device „Gästezimmer“). Ist der
        Name ein Raum (≥ 90 %) und gibt es im Sprecher-/genannten Raum KEIN so benanntes Gerät, gilt er als Raum."""
        nm = str(args.get("device") or "").strip()
        if not nm or args.get("room"):
            return False
        r_, sc = resolve(nm, rooms, ["name", "aliases"])
        if not r_ or sc < 90:
            return False
        here = [x for x in items if x["room"] in room_names()]
        d_, sc2 = resolve(nm, here, keys) if here else (None, 0)
        if d_ and sc2 >= 95:
            return False                               # wirklich ein Gerät dieses Namens im eigenen Raum
        args["room"], args["device"] = r_["name"], ""
        return True

    def device_in_sentence(items: list[dict], src: str) -> dict | None:
        """Genau EIN Gerät (der passenden Art) im Zielraum, dessen Name/Anmerkung/Alias als ganze Wortfolge im Satz steht."""
        u = " " + " ".join(re.sub(r"[^\wäöüß ]+", " ", src.lower()).split()) + " "
        rn = room_names()
        hits = []
        for d in items:
            if rn and d["room"] not in rn:
                continue
            for n in [d.get("note"), d["name"], *(d.get("aliases") or [])]:
                n = " ".join(re.sub(r"[^\wäöüß ]+", " ", str(n or "").lower()).split())
                if len(n) >= 4 and f" {n} " in u:
                    hits.append(d)
                    break
        return hits[0] if len(hits) == 1 else None

    def unsaid_device(domain: str, items: list[dict] | None = None) -> None:
        """Das Modell hat ein Gerät eingesetzt, das im Satz (und im passenden Dialog) nicht vorkommt. Nennt der Satz genau
        ein Gerät dieser Art („Mach das Licht Schreibtisch aus“, Modell: „Schreibtischlampe“ = Schalter) → dieses; nennt
        er nur den Gerätetyp („Mach die Rollos zu“ → device „Schreibtisch“ von eben) → der ganze Raum."""
        nm = str(args.get("device") or "").strip()
        src = current if current is not None else utterance
        if not nm or src is None or in_utterance(nm):
            return
        d = device_in_sentence(items or [], src)
        if not d and utterance and utterance != src and not generic_named(domain, src, [r_["name"] for r_ in rooms]):
            d = device_in_sentence(items or [], utterance)   # „Mach es wieder an“ → das Gerät aus dem Dialog
        if d:
            log("device_from_sentence", model=nm, used=dev_of(d), utterance=src)
            args["device"] = dev_of(d)
        elif generic_named(domain, src, [r_["name"] for r_ in rooms]):
            log("device_dropped", device=nm, utterance=src)
            args["device"] = ""

    def pick(what: str, items: list[dict]):
        """Gerät NUR im genannten bzw. Sprecher-Raum suchen – nie in anderen Räumen raten (Regel des Nutzers:
        ohne Raumnennung gilt der Raum des Mikrofons; gibt es das Gerät dort nicht, kommt eine Rückmeldung).
        what = „kein Licht“ / „keinen Schalter“ …; Rückgabe (Gerät, None) oder (None, Rückmeldung)."""
        nm = str(args.get("device", ""))
        if not in_utterance(nm):                       # „Heizungspumpe“ → Modell schickte „Garten (gesamt)“
            log("device_not_said", device=nm, utterance=utterance)
            return None, Problem("Dazu finde ich kein passendes Gerät – wie heißt es genau?")
        rn = room_names()
        if rn and not args.get("room"):                # Name ist ein (anderer) Raum → dort nach dem Gerät suchen
            r_, sc = resolve(nm, rooms, ["name", "aliases"])
            if r_ and sc >= 90 and r_["name"] not in rn:
                rn = [r_["name"]]
        if not rn:                                     # Mikrofon ohne Raum und kein Raum genannt: Name muss reichen
            d, _ = resolve(nm, items, keys)
            return (d, None) if d else (None, Problem(f"{what[0].upper() + what[1:]} namens „{nm}“ gefunden."))
        d, _ = resolve(nm, [x for x in items if x["room"] in rn], keys, rn)
        if d:
            return d, None
        other, _ = resolve(nm, items, keys)
        where = "/".join(rn)
        if other:
            return None, Problem(f"In {where} gibt es {what} „{nm}“ – meinst du „{dev_of(other)}“ in {other['room']}?",
                                 [_offer(dev_of(other), name, {**args, "device": dev_of(other), "room": other["room"]})])
        return None, Problem(f"In {where} gibt es {what} „{nm}“ – wie heißt es genau?")

    now_said = current if current is not None else utterance      # NUR der aktuelle Satz (utterance = mit Dialog)

    async def lights_report() -> str:
        """Welche Lichter brennen (nur lesen). Antwort nach Frageform des Satzes (Nutzer 18.10.):
        Ja/Nein-Frage („Sind im Bad Lichter an?“) → nur „Ja, da ist noch Licht an.“ / „Nein, … alles aus.“ (fürs ganze
        Haus mit Räumen); „Wo …?“ → Räume; sonst („Welche …?“) → die Lichter."""
        s_ = now_said or ""
        house = not args.get("room") and re.search(r"\b(im (ganzen )?haus|überall|irgendwo|noch wo)\b|^\W*wo\b", s_, re.I)
        rn = [] if house else (ambiguous_rooms() or room_names())
        lights = [d for d in devs if d["type"] == "light" and (not rn or d["room"] in rn)]
        on_ = []
        for rid in sorted({d["room_id"] for d in lights}):
            try:
                st = {x.get("id"): x for x in await bridge.get(f"/api/devices/state?room_id={rid}")}
            except Exception:  # noqa: BLE001
                return Problem("Den Lichtzustand kann ich gerade nicht lesen.")
            on_ += [d for d in lights if d["room_id"] == rid and st.get(d["id"])
                    and (st[d["id"]].get("on") or (_lvl(st[d["id"]].get("level")) or 0) > 0)]
        rooms_on = list(dict.fromkeys(d["room"] for d in on_))
        def und(xs):
            return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " und " + xs[-1]
        where = f"in {'/'.join(rn)}" if rn else "im Haus"
        if _YESNO.search(s_):
            if not on_:
                return f"Nein, {where} ist alles aus."
            return "Ja, da ist noch Licht an." if len(rn) == 1 else f"Ja, noch in {und(rooms_on)}."
        if not on_:
            return f"{where[0].upper()}{where[1:]} ist alles aus."
        if re.search(r"^\W*wo\b", s_, re.I) and len(rooms_on) > 1:
            return f"In {und(rooms_on)}."
        if len(rooms_on) == 1:
            return f"In {rooms_on[0]} brennt: {und([dev_of(d) for d in on_])}."
        return "; ".join(f"{r}: " + ", ".join(dev_of(d) for d in on_ if d["room"] == r) for r in rooms_on) + "."

    if name == "lights_status":
        return await lights_report()
    if name in _SWITCHING and asks_state(now_said):
        log("question_no_switch", tool=name, utterance=now_said)
        if name in _PLAYING:                           # nicht spielen, aber anbieten – „Ja“ führt genau diesen Aufruf aus
            was = str(args.get("search") or args.get("artist") or "").strip()
            return Problem(f"Soll ich „{was}“ spielen?" if was else "Das klang nach einer Frage – soll ich etwas spielen?",
                           [_offer(was or "ja", name, args)] if was else [])
        if name not in ("light_set", "light_adjust"):
            return Problem("Das klang nach einer Frage – ich habe nichts geschaltet.")
        return await lights_report()

    if name == "light_set":
        on = bool(args.get("on", True))
        level = args.get("level")
        lvl = int(level) if level is not None and on else (100 if on else 0)
        devs_l = [x for x in devs if x["type"] == "light"]
        device_is_room(devs_l)
        unsaid_device("licht", devs_l)
        if args.get("device"):
            if amb_device(name, devs_l):
                return amb_device(name, devs_l)
            d, err = pick("kein Licht", devs_l)
            if err:
                return err
            targets, where = [d], d["room"]
        else:
            # ohne Gerät: alle Lichter im (Sprecher-)Raum — „Licht an/aus“
            if amb_problem(name):
                return amb_problem(name)
            room_names_ = room_names()
            if not room_names_:
                return Problem("Ohne Raumzuordnung des Mikrofons und ohne Raumnennung weiß ich nicht, welches Licht gemeint ist.")
            targets = [d for d in devs_l if d["room"] in room_names_]
            where = "/".join(room_names_)
            if not targets:
                return Problem(f"In {where} gibt es keine Lichter.")
        names = []
        for d in targets:
            if not dry_run:
                await bridge.post(f"/api/devices/{d['id']}/level", {"level": lvl})
            names.append(f"„{dev_of(d)}“")
        if len(names) == 1:
            return f"Licht {names[0]} {'an' if on else 'aus'}" + \
                   (f" ({lvl} %)" if 0 < lvl < 100 else "") + sim
        return f"{len(names)} Lichter in {where} {'an' if on else 'aus'}" + \
               (f" ({lvl} %)" if 0 < lvl < 100 else "") + sim

    if name == "light_color":
        devs_l = [x for x in devs if x["type"] == "light"]
        device_is_room(devs_l)
        if args.get("device"):
            if amb_device(name, devs_l):
                return amb_device(name, devs_l)
            d, err = pick("kein Licht", devs_l)
            if err:
                return err
            targets, where = [d], d["room"]
        else:
            if amb_problem(name):
                return amb_problem(name)
            room_names_ = room_names()
            if not room_names_:
                return Problem("Ohne Raumzuordnung des Mikrofons und ohne Raumnennung weiß ich nicht, welches Licht gemeint ist.")
            targets = [d for d in devs_l if d["room"] in room_names_]
            where = "/".join(room_names_)
        colorful = set()
        for rid in {d["room_id"] for d in targets}:
            try:
                colorful |= {s_.get("id") for s_ in await bridge.get(f"/api/devices/state?room_id={rid}") if s_.get("color")}
            except Exception:  # noqa: BLE001
                pass
        targets = [d for d in targets if d["id"] in colorful]
        if not targets:
            return Problem(f"In {where} gibt es kein Licht, das Farbe kann.")
        color = str(args.get("color") or "").strip()
        names = []
        for d in targets:
            if not dry_run:
                try:
                    await bridge.post(f"/api/devices/{d['id']}/color", {"name": color})
                except Exception:  # noqa: BLE001
                    return Problem(f"Die Farbe „{color}“ kenne ich nicht.")
            names.append(f"„{dev_of(d)}“")
        return (f"Licht {names[0]}" if len(names) == 1 else f"{len(names)} Lichter in {where}") + f" auf {color}" + sim

    if name == "light_adjust":
        # relative Helligkeit: aktuellen Stand lesen, dann PROPORTIONAL verstellen
        strength = str(args.get("strength", "mittel")).lower()
        if strength not in ("klein", "mittel", "gross"):
            strength = "mittel"
        up = str(args.get("direction", "up")).lower() == "up"
        devs_l = [x for x in devs if x["type"] == "light"]
        device_is_room(devs_l)
        unsaid_device("licht", devs_l)
        targets, where = [], None
        if args.get("device"):
            if amb_device(name, devs_l):
                return amb_device(name, devs_l)
            d, err = pick("kein Licht", devs_l)
            if err:
                return err
            targets, where = [d], d["room"]
        else:
            if amb_problem(name):
                return amb_problem(name)
            room_names_ = room_names()
            if not room_names_:
                return Problem("Ohne Raumzuordnung des Mikrofons und ohne Raumnennung weiß ich nicht, welche Lichter gemeint sind.")
            targets = [d for d in devs_l if d["room"] in room_names_]
            where = "/".join(room_names_)
        if not targets:
            return Problem(f"In {where} gibt es keine Lichter.")
        level_by_id: dict = {}
        for rid in {d["room_id"] for d in targets}:          # Mikrofon mit 2–3 Räumen: jeden Raum lesen
            try:
                for s_ in await bridge.get(f"/api/devices/state?room_id={rid}"):
                    level_by_id[s_.get("id")] = s_
            except Exception:  # noqa: BLE001
                pass
        if not level_by_id:     # ohne Zustand würde „heller“ alles auf 100 % setzen
            return f"Den Lichtzustand in {where} kann ich gerade nicht lesen."

        def lit(d):
            st = level_by_id.get(d["id"]) or {}
            cur = _lvl(st.get("level"))
            return cur, (bool(st.get("on")) if st.get("on") is not None else (cur or 0) > 0)
        some_on = any(lit(d)[1] for d in targets)
        if not up and not some_on:
            if not re.search(r"\b(an|ein)(\s+(bitte|mal|jetzt|machen|schalten))*\W*$|\b(an|ein)(mach|schalt)",
                             now_said or "", re.I):
                return f"Die Lichter in {where} sind schon aus."
            # „Mach das Licht gemütlich AN“, alles aus: dunkler geht nicht – gemeint ist gedimmt einschalten
            cozy = int(((cfg or {}).get("adjust") or {}).get("cozy_below", 30) or 30)
            if not dry_run:
                for d in targets:
                    await bridge.post(f"/api/devices/{d['id']}/level", {"level": cozy})
            n_ = len(targets)
            return (f"1 Licht in {where} an" if n_ == 1 else f"{n_} Lichter in {where} an") + f" ({cozy} %)" + sim
        changed, skipped = [], []
        for d in targets:
            cur, on = lit(d)
            if up and some_on and not on and not args.get("device"):
                continue        # „heller“ im Raum: brennende Lichter heller – ausgeschaltete bleiben aus
            new = _adjust_plan(cur, on, up, strength, (cfg or {}).get("adjust") or {})
            if new is None:
                why = ("schon voll an" if (on or cur is None) else "schon ganz dunkel") if up \
                    else ("aus" if not on else "schon ganz dunkel")
                skipped.append(f"„{dev_of(d)}“ ({why})")
                continue
            if not dry_run:
                await bridge.post(f"/api/devices/{d['id']}/level", {"level": new})
            changed.append(f"„{dev_of(d)}“ {'an' if new == 100 else str(new) + ' %'}")
        word = "heller" if up else "gedimmt"
        msg = f"{len(changed)} Licht in {where} {word}" if len(changed) == 1 else \
              f"{len(changed)} Lichter in {where} {word}"
        if changed:
            msg += " (" + ", ".join(changed) + ")"
        if skipped:
            msg += "; übersprungen: " + ", ".join(skipped)
        return msg + sim

    if name == "blind_action":
        act = str(args.get("action", ""))
        word = {"open": "fahren auf", "close": "fahren zu", "stop": "gestoppt"}.get(act, act)
        blinds = [x for x in devs if x["type"] == "blind"]
        unsaid_device("rollladen", blinds)
        r_, sc_ = resolve(str(args.get("device") or ""), rooms, ["name", "aliases"])
        if not args.get("device"):                     # „Rollläden runter“ ohne Namen → alle im (Sprecher-)Raum
            if amb_problem(name):
                return amb_problem(name)
            rn_ = room_names()
            r_, sc_ = (next((x for x in rooms if x["name"] == rn_[0]), None), 100) if rn_ else (None, 0)
            if not r_:
                return Problem("In welchem Raum? (Das Mikrofon ist keinem Raum zugeordnet.)")
        if r_ and sc_ >= 90 and not any(str(x.get("note") or x["name"]).strip().lower() == str(args.get("device")).strip().lower()
                                        for x in blinds):
            # Raum statt Rollladen („alle Rollläden“): jede Gruppe des Raums („… gesamt“, „… Gruppe“) + Einzel-Rollläden,
            # die zu keiner Gruppe gehören (erstes Wort ≠ erstes Wort einer Gruppe: „Esszimmer Carport“ neben „Garten
            # (gesamt)“/„Strasse (gesamt)“). Ohne Gruppen: alle Rollläden des Raums.
            here = [x for x in blinds if x["room"] == r_["name"]]
            if not here:
                return Problem(f"In {r_['name']} gibt es keine Rollläden.")
            grp = [x for x in here if re.search(r"gesamt|gruppe|alle", str(x.get("note") or x["name"]), re.I)]
            einzeln = [x for x in here if x not in grp]
            if len(here) > 1 and now_said and re.search(
                    r"\b(den|dem|der)\s+roll+(laden|o)\b|\bdas\s+rollo\b|\b(die|der)\s+jalousie\b", now_said, re.I):
                # „Fahr DEN Rollladen runter“ bei mehreren im Raum → nachfragen (Mehrzahl „die Rollläden“ = alle)
                namen = [dev_of(x) for x in (einzeln or here)][:4]
                return Problem(f"Welchen Rollladen – alle oder {', '.join(namen[:-1]) + ' oder ' + namen[-1] if len(namen) > 1 else namen[0]}?",
                               [_offer("alle", name, {**args, "device": "", "room": r_["name"]})]
                               + [_offer(n, name, {**args, "device": n, "room": r_["name"]}) for n in namen])
            first = lambda x: (re.findall(r"[\wäöüß]+", str(x.get("note") or x["name"]).lower()) or [""])[0]   # noqa: E731
            gw = {first(g) for g in grp} | {w for g in grp for w in re.findall(r"[\wäöüß]+", str(g.get("note") or g["name"]).lower())
                                           if w not in ("gesamt", "gruppe", "alle")}
            targets = grp + [x for x in here if x not in grp and first(x) not in gw] if grp else here
            if not dry_run:
                for x in targets:
                    await bridge.post(f"/api/blinds/{x['id']}/{act}")
            if len(targets) == 1:
                return f"Rollladen „{dev_of(targets[0])}“ {word.replace('fahren', 'fährt')}" + sim
            return f"Rollläden in {r_['name']} {word} ({', '.join(dev_of(x) for x in targets)})" + sim
        if amb_device(name, blinds):
            return amb_device(name, blinds)
        d, err = pick("keinen Rollladen", blinds)
        if err:
            return err
        if not dry_run:
            await bridge.post(f"/api/blinds/{d['id']}/{act}")
        return f"Rollladen „{dev_of(d)}“ {word.replace('fahren', 'fährt')}" + sim

    if name in ("climate_setpoint", "climate_setpoint_delta", "get_temperature"):
        therms = [x for x in devs if x["type"] == "thermostat"]
        if args.get("device"):
            if amb_device(name, therms):
                return amb_device(name, therms)
            d, err = pick("kein Thermostat", therms)
            if err:
                return err
        else:                                          # ohne Gerät: Thermostat im genannten bzw. Sprecher-Raum
            if amb_problem(name):                      # „im Bad“ bei Bad mitte + Bad oben → fragen, nicht Sprecherraum
                return amb_problem(name)
            rn = room_names()
            here = [x for x in therms if x["room"] in rn]
            if not rn:
                return Problem("In welchem Raum? (Das Mikrofon ist keinem Raum zugeordnet.)")
            if not here:
                return Problem(f"In {'/'.join(rn)} gibt es kein Thermostat.")
            if len(here) > 1:
                return Problem(f"Welches Thermostat – {' oder '.join(dev_of(x) for x in here[:2])}?",
                               [_offer(dev_of(x), name, {**args, "device": dev_of(x), "room": x["room"]}) for x in here[:2]])
            d = here[0]
        climate = []
        try:
            climate = await bridge.get(f"/api/rooms/{d['room_id']}/climate")
        except Exception:  # noqa: BLE001
            pass
        cur = next((c for c in climate if c.get("id") == d["id"]), {})
        if name == "get_temperature":
            t = cur.get("temperature_c")
            if t in (0, 0.0):          # Heizungsregler ohne Fühler/offline melden 0 – kein echter Raumwert
                t = None
            return f"Im Raum {d['room']} sind es {t} °C." if t is not None else \
                f"Thermostat „{dev_of(d)}“ liefert gerade keinen Messwert."
        if name == "climate_setpoint_delta":
            if cur.get("heat_setpoint_c") is None:     # nicht von 20 °C aus raten
                return Problem(f"Den aktuellen Sollwert von „{dev_of(d)}“ kenne ich gerade nicht.")
            new = round((float(cur["heat_setpoint_c"]) + float(args.get("delta_c", 0))) * 2) / 2
        else:
            new = round(float(args.get("celsius", 20)) * 2) / 2
        if not dry_run:
            await bridge.post(f"/api/devices/{d['id']}/climate", {"setpoint_c": new})
        return f"Thermostat „{dev_of(d)}“ auf {new} °C" + sim

    async def media_room(r: dict, said: str) -> dict:
        """Medien-Befehl für einen Raum OHNE Musik („Küche“ #297 hat nur Licht) → sein Medien-Partner, d. h. der Raum,
        der den Namen enthält UND Musik hat („Esszimmer / Küche“ #264) – nur wenn es genau einen gibt (load_caps)."""
        await bridge.load_caps([r["name"]])
        p_ = bridge.partner.get(r["name"])
        if not p_:
            return r
        used = next((x for x in rooms if x["name"] == p_), r)
        log("media_room", said=said, room=r["name"], used=used["name"])
        return used

    async def target_room():
        """Genannter Raum, sonst (erster) Raum des Sprechers → (raum, None) oder (None, Fehlertext)."""
        if amb_problem(name):
            return None, amb_problem(name)
        names = room_names()                 # nur gesagte Räume zählen (sonst Sprecherraum)
        if not names:
            return None, Problem("In welchem Raum? (Das Mikrofon ist keinem Raum zugeordnet.)")
        r, _ = resolve(names[0], rooms, ["name", "aliases"])
        if not r:
            return None, Problem(f"Kein Raum namens „{names[0]}“ gefunden.")
        return await media_room(r, _as_rooms(args.get("room"))[0] if args.get("room") else ""), None

    async def now_service(r: dict) -> str:
        try:
            return str((await bridge.now_playing([r["name"]])).get(r["name"]) or "")
        except Exception:  # noqa: BLE001
            return ""

    def source_said() -> str | None:
        """Vom Nutzer GENANNTE Quelle: „sammlung“ / „qobuz“ / None – erst der aktuelle Satz, dann der offene Dialog
        („Ja, die meine ich“ auf eine Rückfrage zu „… aus unserer Musik“). Genannt schlägt immer „läuft gerade“."""
        for src in (current, utterance):
            if not src:
                continue
            c, q_ = _COLLECTION.search(src), re.search(r"\bqobuz\b", src, re.I)
            if c and not q_:
                return "sammlung"
            if q_ and not c:
                return "qobuz"
        return None

    mcfg = {**DEFAULT_CFG["music"], **((cfg or {}).get("music") or {})}

    def no_media(r: dict, what: str) -> str | None:
        miss = bridge.missing.get(r["name"]) or []
        if what in miss and not bridge.partner.get(r["name"]):
            return Problem(f"In {r['name']} gibt es keine {'Musik' if what == 'Musik' else what} – "
                           f"dort ist keine passende Quelle eingerichtet.")
        return None

    if name == "music_play" and utterance is not None and re.search(r"\b(qobuz|tunein|radio)\b", current or utterance, re.I):
        src = current or utterance
        svc = "qobuz" if re.search(r"\bqobuz\b", src, re.I) else "radio"
        log("tool_redirect", frm="music_play", to="stream_play", service=svc, utterance=src)
        return await execute_tool({"name": "stream_play", "arguments": {"service": svc, "search": "", "room": args.get("room"),
                                                                       "kind": "any" if svc == "qobuz" else "station"}},
                                  bridge, dry_run, speaker_rooms, cfg, utterance=utterance, current=current)

    if name == "music_play" and str(args.get("version") or "").lower() == "andere":
        # andere Aufnahme desselben Titels: die Sammlung hat meist nur eine → Qobuz (stream_play kennt „version“)
        if source_said() == "sammlung":
            return Problem("In unserer Sammlung kann ich nicht nach anderen Versionen suchen – das geht nur über Qobuz.")
        return await execute_tool({"name": "stream_play", "arguments": {**args, "service": "qobuz",
                                                                       "search": args.get("search") or ""}},
                                  bridge, dry_run, speaker_rooms, cfg, utterance=utterance, current=current)

    if name == "music_play":
        r, err = await target_room()
        if err:
            return err
        src_m = current if current is not None else utterance
        if utterance is not None and str(args.get("kind") or "any").lower() not in ("random", "genre") \
                and source_said() != "sammlung" and (await now_service(r)).startswith("Qobuz"):
            # Quelle nicht genannt, Qobuz läuft → dort weitersuchen („Spiel mal X“). Findet Qobuz nichts (auch nicht in
            # der Auswahl), fragt stream_play „soll ich in unserer Musik suchen?“ – „Ja“ sucht in der Sammlung.
            log("tool_redirect", frm="music_play", to="stream_play qobuz", reason="läuft gerade Qobuz", utterance=src_m)
            return await execute_tool({"name": "stream_play", "arguments": {
                "service": "qobuz", "search": args.get("search") or "", "artist": args.get("artist") or "",
                "kind": args.get("kind") if args.get("kind") in ("artist", "album", "song") else "any",
                "version": args.get("version") or "", "room": args.get("room")}},
                bridge, dry_run, speaker_rooms, cfg, utterance=utterance, current=current)
        nm_ = no_media(r, "Musik")
        if nm_:
            return nm_
        kind = str(args.get("kind") or "any").lower()
        q = spoken_query(str(args.get("search") or "").strip()) if str(args.get("search") or "").strip() or args.get("search") == " " else ""
        q0 = q
        q = re.sub(r"^(?:was |etwas |mal )*(?:von|vom)\s+(?:den|der|dem|die)?\s*", "", q, flags=re.I).strip() or q
        if q != q0 and kind in ("any", "song", "album"):
            kind = "artist"                            # „von den Imagine Dragons“ = Interpret
        who = str(args.get("artist") or "").strip()
        if who and not artist_said(who):
            log("artist_ignored", artist=who, utterance=utterance)
            who = ""
        if who and q and who.lower() not in q.lower() and not args.get("_album_id"):
            q = f"{q} von {who}"                       # Titel + Interpret: die Bridge prüft, ob der Interpret passt
        elif who and not q:
            q, kind = who, "artist"
        if kind not in ("any", "artist", "album", "song", "genre", "random"):
            kind = "any"
        if not q:
            kind = "random"
        if _AUSWAHL_CTX.get() and not args.get("_album_id") and kind not in ("random", "genre"):
            # Unsicherer Treffer oder der Satz sagt mehr als den Namen → das Sprachmodell wählt aus der Trefferliste
            try:
                h0 = await bridge.get(f"/api/media/resolve?kind={kind}&q={aiohttp.helpers.quote(q)}")
            except aiohttp.ClientResponseError:
                h0 = None
            extra = zusatz_woerter(current, [q, who], [x["name"] for x in rooms])
            from rapidfuzz import process as _rp0, fuzz as _fz0
            from rapidfuzz.utils import default_process as _dp0
            fremd = bool(who) and bool(bridge.artists) and not _rp0.extractOne(who, bridge.artists, scorer=_fz0.WRatio,
                                                                              processor=_dp0, score_cutoff=85)
            if not fremd and (h0 is None or float(h0.get("score") or 0) < 85 or (h0.get("asked_artist")) or extra):
                try:
                    cands = (await bridge.get(f"/api/media/candidates?q={aiohttp.helpers.quote(q)}")).get("candidates") or []
                except Exception:  # noqa: BLE001
                    cands = []
                if cands:
                    log("auswahl_angebot", quelle="Sammlung", q=q, zusatz=extra, score=(h0 or {}).get("score"))
                    ks = []
                    for c in cands:
                        if c["type"] == "Interpret":
                            call_ = {"name": "music_play", "arguments": {"search": c["title"], "kind": "artist", "room": args.get("room")}}
                        else:
                            call_ = {"name": "music_play", "arguments": {
                                "search": c["title"], "artist": c["artist"], "kind": "song" if c["type"] == "Titel" else "album",
                                "_album_id": c["album_id"], "room": args.get("room")}}
                        call_["arguments"] = {k_: v_ for k_, v_ in call_["arguments"].items() if v_ not in (None, "")}
                        ks.append({"quelle": "Sammlung", "art": c["type"], "titel": c["title"], "interpret": c["artist"],
                                   "album": c["album"],
                                   "label": c["title"] if c["type"] == "Interpret" else f"„{c['title']}“ von {c['artist']}",
                                   "call": call_})
                    qob = Problem(f"Zu „{q}“ finde ich in unserer Sammlung nichts Passendes – soll ich auf Qobuz suchen?",
                                  [_offer("Qobuz", "stream_play", {"service": "qobuz", "search": q, "kind": "any",
                                                                   "room": args.get("room")})])
                    return Auswahl(ks, qob, {"name": "music_play", "arguments": {**args, "_album_id": None}})
        try:
            hit = await bridge.get(f"/api/media/resolve?kind={kind}&q={aiohttp.helpers.quote(q)}"
                                   + (f"&album_id={int(args['_album_id'])}" if args.get("_album_id") else ""))
        except aiohttp.ClientResponseError:
            try:      # Hörfehler bei Namen („Adele“ → „Adda“): ähnlichste Namen anbieten statt nur „nichts gefunden“
                names = (await bridge.get(f"/api/media/suggest?q={aiohttp.helpers.quote(q)}")).get("names") or []
            except Exception:  # noqa: BLE001
                names = []
            vh = None
            src_ = current if current is not None else utterance
            if "Filme/Serien" not in (bridge.missing.get(r["name"]) or []) and not (
                    src_ and re.search(r"\b(hörspiel\w*|hörbuch\w*|musik|lied|song|album|qobuz)\b", src_, re.I)):
                try:      # „die drei Fragezeichen Toteninsel“ = Film – aber nie, wenn ausdrücklich Hörspiel/Musik gesagt wurde
                    vh = await bridge.get(f"/api/kodi/search?q={aiohttp.helpers.quote(q)}&kind=any&room_id={r['id']}")
                except Exception:  # noqa: BLE001
                    vh = None
            if vh and float(vh.get("score") or 0) >= 90:
                what = "die Serie" if vh.get("type") == "tvshow" else "den Film"
                se_ = re.search(r"\bstaffel\s*(\d+)", src_ or "", re.I)
                ep_ = re.search(r"\b(?:folge|episode)\s*(\d+)", src_ or "", re.I)
                return Problem(f"Zu „{q}“ finde ich keine Musik – meinst du {what} „{vh['title']}“?",
                               [_offer(vh["title"], "video_play", {"search": vh["title"], "kind": vh["type"], "room": args.get("room"),
                                                                   "season": int(se_.group(1)) if se_ else None,
                                                                   "episode": int(ep_.group(1)) if ep_ else None})]
                               + [_offer(n, "music_play", {**args, "search": n, "artist": "", "kind": "any"}) for n in names[:1]])
            if names:
                return Problem(f"Zu „{q}“ finde ich nichts – meinst du {' oder '.join(names[:2])}?",
                               [_offer(n, "music_play", {**args, "search": n, "artist": "", "kind": "any"}) for n in names[:2]])
            return Problem(f"Zu „{q}“ finde ich nichts in der Musiksammlung – soll ich auf Qobuz suchen?",
                           [_offer("Qobuz", "stream_play", {"service": "qobuz", "search": q, "kind": "any",
                                                             "room": args.get("room")})])
        if kind != "random" and hit.get("kind") in ("artist", "album", "song") and float(hit.get("score") or 0) < 85:
            # nur ein unsicherer Treffer (z. B. bloß gleich klingend) → erst fragen, dann spielen
            label = hit.get("match") or hit.get("album")
            if hit.get("asked_artist"):                # Titel da, aber von jemand anderem → Vorschlag ODER Qobuz
                mine = _offer(hit.get("artist") or label, "music_play",
                              {**args, "search": f"{label} von {hit.get('artist')}", "artist": "", "kind": hit.get("kind")})
                qob = _offer("Qobuz", "stream_play", {"service": "qobuz", "search": q, "kind": "any", "room": args.get("room")})
                from rapidfuzz import process as _rp, fuzz as _fz
                from rapidfuzz.utils import default_process as _dp
                if bridge.artists and not _rp.extractOne(str(hit["asked_artist"]), bridge.artists, scorer=_fz.WRatio,
                                                         processor=_dp, score_cutoff=85):
                    # Interpret gar nicht in der Sammlung → ein ähnlich klingender Titel von jemand anderem ist kein
                    # Ersatz („Skaterboy von Avril Lavigne“ → „Schocktherapie“ von Fettes Brot) – gleich Qobuz anbieten
                    want = re.split(r"\s+(?:von|vom)\s+", q, maxsplit=1)[0]
                    return Problem(f"Von {who or str(hit['asked_artist']).title()} habe ich nichts in unserer Sammlung – soll ich "
                                   f"„{want}“ auf Qobuz suchen?", [qob])
                return Problem(f"„{label}“ von {who or hit['asked_artist']} habe ich nicht in der Sammlung – meinst du „{label}“ "
                               f"von {hit.get('artist')}, oder soll ich auf Qobuz suchen?", [mine, qob])
            first = _offer(label, "music_play", {**args, "search": label, "artist": "", "kind": hit.get("kind")})
            if hit.get("kind") == "song" and hit.get("artist"):
                first = _offer(label, "music_play", {**args, "search": f"{label} von {hit['artist']}", "artist": "",
                                                       "kind": "song"})
                label = f"„{label}“ von {hit['artist']}"
            alts = [a for a in hit.get("alternatives") or [] if a != hit.get("match")][:1]
            return Problem(f"Meinst du {label}{' oder ' + alts[0] if alts else ''}?",
                           [first] + [_offer(a, "music_play", {**args, "search": a, "artist": "", "kind": "artist"}) for a in alts])
        if not dry_run:
            try:   # BluOS-Räume: Bridge spielt das Album über den Player (gleiche Sammlung, Titel + Interpret)
                await bridge.post("/api/media/play", {"room_id": r["id"], "album_id": hit["album_id"]})
            except aiohttp.ClientResponseError as e:
                if e.status == 404:
                    return Problem(f"„{hit['album']}“ finde ich auf dem Player in {r['name']} nicht.")
                if e.status == 409:
                    return Problem(f"In {r['name']} gibt es keine Musikquelle, die ich abspielen kann.")
                return Problem(f"Der Player in {r['name']} ist gerade nicht erreichbar.")
        alb, art = f"„{hit['album']}“", hit.get("artist") or ""
        how = hit.get("kind")
        if not dry_run:
            wortschatz.merke(ZULETZT_FILE, [art, hit.get("match") if how == "song" else hit.get("album")])
        if how == "song":
            msg = f"„{hit['match']}“ ist auf {alb} ({art}) – ich spiele das Album in {r['name']}"
        elif how == "artist":
            msg = f"Spiele {art}: {alb} in {r['name']}"
        elif how == "genre":
            msg = f"Spiele {hit['match']}: {alb} ({art}) in {r['name']}"
        elif how == "random":
            msg = f"Zufällig: {alb} ({art}) in {r['name']}"
        else:
            msg = f"Spiele {alb} ({art}) in {r['name']}"
        return msg + sim

    if name == "music_albums":
        who = spoken_query(str(args.get("artist") or "").strip())
        if not who:
            return Problem("Von welchem Interpreten?")
        src = source_said()
        if src != "qobuz":                             # eigene Sammlung (Interpret unscharf, „Amy McDonald“)
            from rapidfuzz import process as _rp, fuzz as _fz
            from rapidfuzz.utils import default_process as _dp
            best_ = _rp.extractOne(who, bridge.artists, scorer=_fz.WRatio, processor=_dp, score_cutoff=85)
            if best_:
                import urllib.parse as _up
                try:
                    al = await bridge.get(f"/api/media/browse?kind=album&artist={_up.quote(best_[0])}")
                except Exception:  # noqa: BLE001
                    al = []
                names_ = list(dict.fromkeys(a["title"] for a in al if a.get("title")))
                if names_:
                    return (f"Von {best_[0]} haben wir {len(names_)} Album: {names_[0]}." if len(names_) == 1 else
                            f"Von {best_[0]} haben wir {len(names_)} Alben: " + ", ".join(names_[:-1]) +
                            f" und {names_[-1]}.")
            if src == "sammlung":
                return Problem(f"Von {who} habe ich in unserer Sammlung nichts gefunden.")
        r, err = await target_room()
        if err:
            return err
        try:
            import urllib.parse as _up
            res_ = await bridge.get(f"/api/rooms/{r['id']}/stream/albums?artist={_up.quote(who)}")
        except Exception:  # noqa: BLE001
            return Problem(f"Von {who} habe ich in der Sammlung nichts, und Qobuz kann ich in {r['name']} nicht fragen.")
        names_ = res_.get("albums") or []
        if not names_:
            return Problem(f"Von {who} habe ich weder in der Sammlung noch auf Qobuz Alben gefunden.")
        return f"Auf Qobuz von {who}: " + ", ".join(names_[:-1]) + (f" und {names_[-1]}." if len(names_) > 1 else f"{names_[0]}.")

    if name == "stream_play":
        r, err = await target_room()
        if err:
            return err
        nm_ = no_media(r, "Musik")
        if nm_:
            return nm_
        svc = str(args.get("service") or "radio").lower()
        svc = "radio" if svc in ("tunein", "radio", "sender") else svc
        q = spoken_query(str(args.get("search") or "").strip())
        src_ = current if current is not None else utterance
        skip_ = None
        if str(args.get("version") or "").lower() == "andere":
            # „Spiel eine andere Version (davon)“: gleicher Titel, aber nicht die gerade laufende Aufnahme – auf Qobuz
            try:
                b_ = (await bridge.get(f"/api/rooms/{r['id']}/state")).get("bluos") or {}
            except Exception:  # noqa: BLE001
                b_ = {}
            if not str(args.get("search") or "").strip() or re.search(r"\b(version|aufnahme|fassung)\b", q, re.I):
                q = ""                             # kein Titel genannt (der Satz selbst ist keiner) → laufender Titel
            if not q and not b_.get("title"):
                return Problem("Gerade läuft kein Titel – von welchem Lied soll ich eine andere Version suchen?")
            if b_.get("title"):
                skip_ = {k: str(b_.get(k) or "") for k in ("title", "artist", "album")}
            q, svc = q or str(b_["title"]), "qobuz"
            args = {**args, "kind": "song"}
            log("other_version", skip=skip_, q=q)
        andere_version = str(args.get("version") or "").lower() == "andere"     # gibt es nur auf Qobuz → dort bleiben
        if svc == "qobuz" and q and utterance is not None and source_said() != "qobuz" and not andere_version \
                and (source_said() == "sammlung" or not (await now_service(r)).startswith("Qobuz")):
            # Qobuz nur, wenn im Dialog genannt (utterance = Dialog) – sonst zuerst die eigene Sammlung; findet die nichts,
            # bietet music_play Qobuz selbst an (Angebot → utterance None → gilt dann)
            log("tool_redirect", frm="stream_play qobuz", to="music_play", utterance=src_)
            return await execute_tool({"name": "music_play", "arguments": {"search": q, "artist": args.get("artist") or "",
                                                                          "kind": args.get("kind") if args.get("kind") in
                                                                          ("artist", "album", "song") else "any",
                                                                          "room": args.get("room")}},
                                      bridge, dry_run, speaker_rooms, cfg, utterance=utterance, current=current)
        if svc == "radio" and q and src_ is not None and not re.search(r"\b(radio|sender|tunein|station)\b", src_, re.I):
            from rapidfuzz import process as _rp, fuzz as _fz
            best_ = _rp.extractOne(q.lower(), [a.lower() for a in bridge.artists], scorer=_fz.WRatio, score_cutoff=90)
            if best_:                               # „Spiel Amy Macdonald“ ist kein Sender, sondern ein Interpret der Sammlung
                log("tool_redirect", frm="stream_play", to="music_play", artist=best_[0], utterance=src_)
                return await execute_tool({"name": "music_play", "arguments": {"search": q, "kind": "artist",
                                                                              "room": args.get("room")}},
                                          bridge, dry_run, speaker_rooms, cfg, utterance=utterance, current=current)
        who = str(args.get("artist") or "").strip()
        if who and not artist_said(who):
            log("artist_ignored", artist=who, utterance=utterance)
            who = ""
        if who and q and who.lower() not in q.lower():
            q = f"{q} von {who}"
        elif who and not q:
            q = who
        if not q:
            return Problem("Welchen Sender?" if svc == "radio" else "Was soll ich auf Qobuz spielen?")
        kind = str(args.get("kind") or ("station" if svc == "radio" else "any")).lower()

        def in_sammlung(text: str) -> Problem:
            """Auf Qobuz nichts → fragen, ob in unserer Musik gesucht werden soll („Ja“ = genau dieser Aufruf)."""
            return Problem(f"{text} – soll ich in unserer Musik suchen?",
                           [_offer("unsere Musik", "music_play", {"search": q, "kind": kind if kind in ("artist", "album", "song")
                                                                  else "any", "room": args.get("room")})])
        extra = zusatz_woerter(current, [q, who], [x["name"] for x in rooms]) if svc == "qobuz" else []
        if svc == "qobuz" and _AUSWAHL_CTX.get() and not args.get("_url") and not skip_ and extra:
            # „die Folge mit Fußball“, „das Original“, „live“ … → das Sprachmodell wählt aus der Qobuz-Trefferliste.
            # Erst mit den Zusatzwörtern suchen (engere Liste), sonst nur mit dem Namen.
            serie = bool(re.search(r"\b(folge|teil|episode|hörspiel\w*|hörbuch\w*)\b", current or "", re.I))
            cats = "Albums" if serie else ("Songs" if kind == "song" else ("Albums" if kind == "album" else "Songs,Albums,Artists"))
            cands, seen_ = [], set()
            title_q = re.split(r"\s+(?:von|vom)\s+", q, maxsplit=1)[0]
            suchen = [(f"{title_q} {' '.join(extra)}", cats, 8), (q, cats, 8)]
            if who:                                    # Katalog des Interpreten: dort steht das Original
                suchen.append((who, "Songs" if not serie else "Albums", 8))
            async def such_(qq, cs, n_):
                try:
                    return (await bridge.get(f"/api/rooms/{r['id']}/stream/candidates?q={aiohttp.helpers.quote(qq)}"
                                             f"&cats={cs}&n={n_}")).get("candidates") or []
                except Exception:  # noqa: BLE001
                    return []
            for got in await asyncio.gather(*(such_(*x) for x in suchen)):   # Reihenfolge bleibt: eng, normal, Katalog
                for c in got:
                    key_ = (c["type"], c["title"].lower(), c["artist"].lower(), c["album"].lower())
                    if key_ not in seen_ and len(cands) < 20:
                        seen_.add(key_)
                        cands.append(c)
            if cands:
                log("auswahl_angebot", quelle="Qobuz", q=q, zusatz=extra, n=len(cands))
                ks = []
                for c in cands:
                    if c["type"] == "Interpret":
                        call_ = {"name": "stream_play", "arguments": {"service": "qobuz", "search": c["title"], "kind": "artist",
                                                                      "room": args.get("room")}}
                    else:
                        call_ = {"name": "stream_play", "arguments": {
                            "service": "qobuz", "search": c["title"], "kind": "song" if c["type"] == "Titel" else "album",
                            "_url": c["url"], "_sub": " • ".join(x for x in ("", c["artist"], c["album"]) if x is not None),
                            "room": args.get("room")}}
                    call_["arguments"] = {k_: v_ for k_, v_ in call_["arguments"].items() if v_ not in (None, "")}
                    ks.append({"quelle": "Qobuz", "art": c["type"], "titel": c["title"], "interpret": c["artist"],
                               "album": c["album"],
                               "label": c["title"] if c["type"] == "Interpret" else f"„{c['title']}“ von {c['artist']}",
                               "call": call_})
                return Auswahl(ks, in_sammlung(f"Auf Qobuz finde ich zu „{q}“ nichts Passendes"),
                               {"name": "stream_play", "arguments": {**args, "_url": None}})
        try:     # im Trockenlauf nur suchen (zeigt, was liefe), sonst abspielen
            hit = await bridge.post(f"/api/rooms/{r['id']}/stream", {"service": svc, "q": q, "kind": kind, "dry": dry_run,
                                                                      "skip": skip_, "url": args.get("_url"),
                                                                      "title": args.get("search") or "",
                                                                      "sub": args.get("_sub") or ""})
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                if svc == "qobuz" and not skip_:
                    return in_sammlung(f"Auf Qobuz finde ich zu „{q}“ nichts")
                return Problem(f"{'Keinen Sender' if svc == 'radio' else 'Auf Qobuz nichts'} zu „{q}“ gefunden.")
            if e.status == 409:
                return Problem(f"{'Radio' if svc == 'radio' else 'Qobuz'} per Sprache geht in {r['name']} noch nicht "
                               f"(nur in Räumen mit BluOS-Player).")
            if e.status == 502:
                return Problem(f"„{q}“ lässt sich gerade nicht abspielen – der Dienst liefert keinen Stream.")
            return Problem(f"Der Player in {r['name']} ist gerade nicht erreichbar.")
        what = hit.get("title") or q
        sub = hit.get("sub") or ""
        if not dry_run:                              # Qobuz-Lied: „Komponist • Interpret • Album“ → Interpret + Titel
            p_ = sub.split(" • ")
            wortschatz.merke(ZULETZT_FILE, [what] if svc == "radio" else
                             [p_[1] if len(p_) > 1 else hit.get("artist") or p_[0], what])
        if svc == "radio":
            return f"Radio {what} in {r['name']}" + sim
        parts = sub.split(" • ")                  # Qobuz: „Komponist • Interpret • Album“ → Interpret nennen
        art_ = (parts[1] if len(parts) > 1 else parts[0]).strip()
        if hit.get("kind") == "song":
            return f"Qobuz: „{what}“ ({art_}) in {r['name']}" + sim
        return f"Qobuz: {hit.get('artist') or art_} – „{what}“ in {r['name']}" + sim

    _MUSIC_SAID = re.compile(r"\b(qobuz|tunein|radio|sender|hörspiel\w*|hörbuch\w*|musik|lied|song|album)\b", re.I)

    if name == "video_play" and utterance is not None and _MUSIC_SAID.search(current or utterance):
        # „… über Qobuz die drei Fragezeichen Folge 104“ → Hörspiel, kein Film: das Modell wählte wegen „Folge“ video_play
        src = current or utterance
        if re.search(r"\b(qobuz|tunein|radio|sender)\b", src, re.I):
            svc = "qobuz" if re.search(r"\bqobuz\b", src, re.I) else "radio"
            fn2 = {"name": "stream_play", "arguments": {"service": svc, "search": "", "kind": "any" if svc == "qobuz" else "station",
                                                        "room": args.get("room")}}
        else:
            fn2 = {"name": "music_play", "arguments": {"search": " ", "kind": "any", "room": args.get("room")}}
        log("tool_redirect", frm="video_play", to=fn2["name"], utterance=src)
        return await execute_tool(fn2, bridge, dry_run, speaker_rooms, cfg, utterance=utterance, current=current)

    if name == "video_play":
        rn = room_names()
        if not rn:
            return Problem("In welchem Raum? (Das Mikrofon ist keinem Raum zugeordnet.)")
        r, _ = resolve(rn[0], rooms, ["name", "aliases"])
        if not r:
            return Problem(f"Kein Raum namens „{rn[0]}“ gefunden.")
        await bridge.load_caps([r["name"]])
        if "Filme/Serien" in (bridge.missing.get(r["name"]) or []):
            if utterance is not None and ("Musik" not in (bridge.missing.get(r["name"]) or []) or bridge.partner.get(r["name"])):
                log("tool_redirect", frm="video_play", to="music_play", room=r["name"], utterance=current or utterance)
                return await execute_tool({"name": "music_play", "arguments": {"search": " ", "kind": "any", "room": args.get("room")}},
                                          bridge, dry_run, speaker_rooms, cfg, utterance=utterance, current=current)
            return Problem(f"In {r['name']} gibt es keine Mediathek – Filme und Serien gehen nur im Raum mit dem Mediacenter.")
        kind = {"movie": "movie", "film": "movie", "series": "tvshow", "serie": "tvshow", "tvshow": "tvshow"}.get(
            str(args.get("kind") or "any").lower(), "any")
        q = spoken_query(str(args.get("search") or "").strip())
        q = " ".join(re.sub(r"\b(den|die|das|der|film|filme|serie|serien|starte?|spiel\w*|(staffel|folge|episode)\s*\d+)\b",
                            " ", q, flags=re.I).split())
        if not q:
            return Problem("Welchen Film oder welche Serie?")

        def num(v):
            return int(v) if str(v or "").strip().isdigit() else None
        season, episode = num(args.get("season")), num(args.get("episode"))
        src_v = current if current is not None else (utterance or "")
        folge_said = season is not None or episode is not None or bool(re.search(r"\b(folge|staffel|episode)\b", src_v, re.I))
        try:
            hit = await bridge.get(f"/api/kodi/search?q={aiohttp.helpers.quote(q)}&kind={kind}&room_id={r['id']}")
            if kind != "any" and float(hit.get("score") or 0) < 85:   # Modell hat die Art geraten → alles durchsuchen
                hit2 = await bridge.get(f"/api/kodi/search?q={aiohttp.helpers.quote(q)}&kind=any&room_id={r['id']}")
                if float(hit2.get("score") or 0) > float(hit.get("score") or 0):
                    hit = hit2
            if folge_said and hit.get("type") == "movie":   # „Folge 10“ passt zu keinem Film → nur Serien zählen
                log("video_folge_not_movie", movie=hit.get("title"), utterance=src_v)
                hit = await bridge.get(f"/api/kodi/search?q={aiohttp.helpers.quote(q)}&kind=tvshow&room_id={r['id']}")
            if float(hit.get("score") or 0) < 80:        # zu wenig Ähnlichkeit: gilt als nicht gefunden (→ Musik/Qobuz anbieten)
                raise aiohttp.ClientResponseError(None, (), status=404)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                caps_ = room_caps(bridge, r["name"])
                offers_ = []
                if "Musik" in caps_:                   # vielleicht ein Hörspiel/Album der Sammlung („… Folge 10“)
                    try:
                        mh = await bridge.get(f"/api/media/resolve?kind=any&q={aiohttp.helpers.quote(q)}")
                        if float(mh.get("score") or 0) >= 85:
                            lbl = mh.get("match") or mh.get("album") or q
                            offers_.append(_offer(lbl, "music_play", {"search": lbl, "kind": mh.get("kind") or "any",
                                                                      "room": args.get("room")}))
                    except Exception:  # noqa: BLE001
                        pass
                if "Qobuz" in caps_:
                    offers_.append(_offer("Qobuz", "stream_play", {"service": "qobuz", "search": q, "kind": "any",
                                                                   "room": args.get("room")}))
                if offers_:
                    what_ = " oder ".join(f"„{o['label']}“" if o["label"] != "Qobuz" else "auf Qobuz suchen" for o in offers_)
                    return Problem(f"Zu „{q}“ finde ich keinen Film und keine Serie – meinst du {what_}?", offers_)
                return Problem(f"Zu „{q}“ finde ich in der Mediathek nichts.")
            if e.status == 409:
                return Problem(f"In {r['name']} gibt es keine Mediathek.")
            return Problem("Die Mediathek ist gerade nicht erreichbar.")
        label = str(hit.get("title") or q) + (f" ({hit['year']})" if hit.get("year") else "")
        what = "die Serie" if hit.get("type") == "tvshow" else "den Film"
        if float(hit.get("score") or 0) < 85:        # unsicher (verhört?) → fragen, feste Angebote
            alts = list(hit.get("alternatives") or [])[:1]
            return Problem(f"Meinst du {what} „{label}“{' oder „' + alts[0] + '“' if alts else ''}?",
                           [_offer(hit["title"], "video_play", {**args, "search": hit["title"], "kind": hit["type"]})]
                           + [_offer(a, "video_play", {**args, "search": a, "kind": "any"}) for a in alts])
        try:
            res = await bridge.post(f"/api/rooms/{r['id']}/video", {"did": str(hit["did"]), "kind": hit["type"], "id": hit["id"],
                                                                    "season": season, "episode": episode, "dry": bool(dry_run)})
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return Problem(f"Zu „{label}“ gibt es keine passende Folge.")
            if e.status == 409:
                return Problem(f"„{label}“ ist in {r['name']} keine Quelle.")
            return Problem(f"„{label}“ lässt sich gerade nicht starten – KODI antwortet nicht.")
        if res.get("kind") == "episode":
            return f"Serie „{res.get('title') or hit['title']}“ {res.get('sub') or ''} in {r['name']}".replace("  ", " ") + sim
        return f"Film „{res.get('title') or hit['title']}“ in {r['name']}" + sim

    if name in ("volume_set", "volume_adjust"):
        r, err = await target_room()
        if err:
            return err
        vmax = int(mcfg.get("max_volume", 60))
        if name == "volume_set":
            try:
                new = int(args.get("level"))
            except (TypeError, ValueError):
                return Problem("Welche Lautstärke (0–100)?")
        else:
            try:
                st = await bridge.get(f"/api/rooms/{r['id']}/state")
            except Exception:  # noqa: BLE001
                st = {}
            if st.get("volume") is None:
                return Problem(f"Die aktuelle Lautstärke in {r['name']} kenne ich gerade nicht.")
            cur = int((st.get("duck") or {}).get("orig", st.get("volume")) or 0)   # Ducking: echter Pegel
            strength = str(args.get("strength") or "mittel").lower()
            step = int((mcfg.get("steps") or {}).get(strength, 10))
            new = cur + step if str(args.get("direction", "up")).lower() == "up" else cur - step
        capped = new > vmax
        new = max(0, min(vmax, new))
        if not dry_run:
            await bridge.post(f"/api/rooms/{r['id']}/volume", {"level": new})
        return f"Lautstärke in {r['name']} auf {new}" + (f" (Sprach-Obergrenze {vmax})" if capped else "") + sim

    if name == "switch_set":
        on = bool(args.get("on", True))
        sws = [x for x in devs if x["type"] == "switch"]
        if amb_device(name, sws):
            return amb_device(name, sws)
        d, err = pick("keinen Schalter", sws)
        if err:
            return err
        if not dry_run:
            await bridge.post(f"/api/switches/{d['id']}/{'on' if on else 'off'}")
        return f"„{dev_of(d)}“ {'an' if on else 'aus'}" + sim

    if name == "media_control":
        r, err = await target_room()
        if err:
            return err
        act = str(args.get("action") or "").lower()
        tr = {"pause": "PAUSE", "play": "PLAY", "next": "SKIP_FWD", "previous": "SKIP_REV", "stop": "STOP"}
        mix = {"shuffle_on": ("shuffle", {"on": True}), "shuffle_off": ("shuffle", {"on": False}),
               "repeat_one": ("repeat", {"mode": "one"}), "repeat_all": ("repeat", {"mode": "all"}),
               "repeat_off": ("repeat", {"mode": "off"})}
        if act in tr:
            if not dry_run:
                await bridge.post(f"/api/rooms/{r['id']}/transport", {"cmd": tr[act]})
        elif act in ("mute", "unmute"):
            if not dry_run:
                await bridge.post(f"/api/rooms/{r['id']}/mute", {"on": act == "mute"})
        elif act in mix:
            if not dry_run:
                try:
                    await bridge.post(f"/api/rooms/{r['id']}/{mix[act][0]}", mix[act][1])
                except aiohttp.ClientResponseError as e:
                    if e.status == 409:
                        return Problem(f"Zufall/Wiederholen geht in {r['name']} mit dieser Quelle nicht.")
                    raise
        else:
            return Problem(f"Unbekannte Wiedergabe-Aktion „{act}“.")
        word = {"pause": "pausiert", "play": "läuft weiter", "next": "nächstes Lied", "previous": "vorheriges Lied",
                "stop": "gestoppt", "mute": "stumm", "unmute": "Ton wieder an", "shuffle_on": "Zufallswiedergabe an",
                "shuffle_off": "Zufallswiedergabe aus", "repeat_one": "Lied wird wiederholt",
                "repeat_all": "alles wird wiederholt", "repeat_off": "Wiederholen aus"}[act]
        return f"{r['name']}: {word}" + sim

    if name == "weather":
        day = day_offset(args.get("day"))
        try:
            w = await bridge.get(f"/api/assist/weather?day={day}")
        except aiohttp.ClientResponseError as e:
            return Problem("Für das Wetter ist noch kein Ort hinterlegt." if e.status == 409 else "Der Wetterdienst ist gerade nicht erreichbar.")
        return w.get("text") or "Keine Wetterdaten."

    if name == "web_search":
        q = str(args.get("query") or "").strip()
        if not q:
            return Problem("Wonach soll ich suchen?")
        try:
            r = await bridge.get(f"/api/assist/search?q={aiohttp.helpers.quote(q)}")
        except aiohttp.ClientResponseError as e:
            return Problem("Die Websuche ist nicht eingerichtet." if e.status == 409 else "Die Websuche ist gerade nicht erreichbar.")
        hits = r.get("results") or []
        if not hits:
            return Problem(f"Zu „{q}“ habe ich nichts gefunden.")
        return await summarize(cfg or load_cfg(), str(args.get("question") or q), hits)

    if name == "rooms_playing":
        try:
            pl = await bridge.get("/api/rooms/playing")
        except Exception:  # noqa: BLE001
            return Problem("Den Wiedergabe-Status kann ich gerade nicht lesen.")
        by_id = {r["id"]: r for r in rooms}
        want = room_names() if args.get("room") else []
        lines = []
        for rid, x in (pl or {}).items():
            r = by_id.get(int(rid))
            if not r or (want and r["name"] not in want):
                continue
            state = {"play": "läuft", "pause": "pausiert", "stop": "gestoppt"}.get(str(x.get("play_state") or ""), "an")
            what = ", ".join(t for t in (x.get("source_name"), x.get("title")) if t) or f"Quelle {x.get('source')}"
            lines.append(f"{r['name']}: {what} ({state})")
        if not lines:
            return f"In {'/'.join(want)} läuft gerade nichts." if want else "Gerade läuft in keinem Raum etwas."
        return "; ".join(lines)

    if name == "room_off":
        src_ = current if current is not None else utterance
        if src_ is not None and not said_rooms() and not re.search(
                r"\b(musik|radio|fernseh\w*|tv|medien|raum|alles|zimmer|player|ton|wiedergabe|film|serie|kodi|"
                r"netflix|aus)\s*$|\b(musik|radio|fernseh\w*|tv|medien|raum|alles|player|wiedergabe|anlage)\b", src_, re.I):
            log("room_off_rejected", utterance=src_)
            return Problem("Was soll ich ausschalten – das Licht, die Musik oder ein bestimmtes Gerät?")
        # Raum benannt → den; sonst Sprecherraum des Mikrofons
        if amb_problem(name):
            return amb_problem(name)
        rn = room_names()
        if not rn:
            return Problem("Welcher Raum soll ausgeschaltet werden? (Das Mikrofon ist keinem Raum zugeordnet.)")
        r, _ = resolve(rn[0], rooms, ["name", "aliases"])
        if not r:
            return Problem(f"Kein Raum namens „{rn[0]}“ gefunden.")
        r = await media_room(r, _as_rooms(args.get("room"))[0] if args.get("room") else "")
        if not dry_run:
            await bridge.post(f"/api/rooms/{r['id']}/off")
        return f"Raum {r['name']} aus" + sim

    return Problem(f"Unbekannte Funktion {name}.")


QUICK = {
    "pause": ("media_control", {"action": "pause"}), "musik pause": ("media_control", {"action": "pause"}),
    "anhalten": ("media_control", {"action": "pause"}), "musik anhalten": ("media_control", {"action": "pause"}),
    "stopp": ("media_control", {"action": "stop"}), "stop": ("media_control", {"action": "stop"}),
    "weiter": ("media_control", {"action": "play"}), "weiterspielen": ("media_control", {"action": "play"}),
    "play": ("media_control", {"action": "play"}), "abspielen": ("media_control", {"action": "play"}),
    "nächstes lied": ("media_control", {"action": "next"}), "nächster titel": ("media_control", {"action": "next"}),
    "nächstes": ("media_control", {"action": "next"}), "überspringen": ("media_control", {"action": "next"}),
    "skip": ("media_control", {"action": "next"}),
    "vorheriges lied": ("media_control", {"action": "previous"}), "letztes lied": ("media_control", {"action": "previous"}),
    "stumm": ("media_control", {"action": "mute"}), "ton aus": ("media_control", {"action": "mute"}),
    "ton an": ("media_control", {"action": "unmute"}),
    "lauter": ("volume_adjust", {"direction": "up", "strength": "mittel"}),
    "leiser": ("volume_adjust", {"direction": "down", "strength": "mittel"}),
    "mach lauter": ("volume_adjust", {"direction": "up", "strength": "mittel"}),
    "mach leiser": ("volume_adjust", {"direction": "down", "strength": "mittel"}),
    "das gefällt mir nicht": ("media_control", {"action": "next"}), "gefällt mir nicht": ("media_control", {"action": "next"}),
    "rollläden runter": ("blind_action", {"action": "close"}), "rollladen runter": ("blind_action", {"action": "close"}),
    "rollläden zu": ("blind_action", {"action": "close"}), "rollos zu": ("blind_action", {"action": "close"}),
    "rollos runter": ("blind_action", {"action": "close"}), "jalousien schließen": ("blind_action", {"action": "close"}),
    "mach dicht": ("blind_action", {"action": "close"}), "verdunkeln": ("blind_action", {"action": "close"}),
    "es blendet": ("blind_action", {"action": "close"}), "sonnenschutz": ("blind_action", {"action": "close"}),
    "rollläden hoch": ("blind_action", {"action": "open"}), "rollladen hoch": ("blind_action", {"action": "open"}),
    "rollläden auf": ("blind_action", {"action": "open"}), "rollos auf": ("blind_action", {"action": "open"}),
    "rollos hoch": ("blind_action", {"action": "open"}), "mach die rollläden auf": ("blind_action", {"action": "open"}),
    "mach die rollläden zu": ("blind_action", {"action": "close"}),
    "was läuft": ("rooms_playing", {}), "was läuft gerade": ("rooms_playing", {}), "wo läuft musik": ("rooms_playing", {}),
    "wo läuft was": ("rooms_playing", {}), "läuft irgendwo musik": ("rooms_playing", {}),
}


# Wünsche, für die es (noch) kein Werkzeug gibt → feste, ehrliche Antwort OHNE Sprachmodell (das Modell erfand sonst
# „der Fernseher ist bereits eingeschaltet“ oder bot an, den Film „in einem anderen Raum“ zu starten).
_NO_TOOL = [
    (re.compile(r"\b(rollladen|rollläden|rollo|rollos|jalousie\w*)\b.*\b(halb|halbe|viertel|\d+\s*(prozent|%))"),
     "Rollläden kann ich per Sprache nur ganz hoch, ganz runter oder anhalten – Positionen gehen noch nicht."),
    (re.compile(r"\b(fernseher|fernsehen|tv|glotze|beamer)\b.*\b(an|ein|einschalten|anmachen|starten)\b|"
                r"\b(mach|schalt\w*)\b.*\b(fernseher|fernsehen|tv|glotze|beamer)\b.*\b(an|ein)\b"),
     "Den Fernseher kann ich per Sprache noch nicht einschalten – bitte über die Control4-App oder die Fernbedienung."),
    (re.compile(r"\b(netflix|prime video|amazon prime|disney plus|disney\+|youtube|"
                r"tagesschau|nachrichten im fernsehen|fernsehsender|tv-sender)\b"),
     "Netflix, Prime und Fernsehsender kann ich per Sprache nicht durchsuchen – Filme und Serien aus der Mediathek schon."),
]


def quick_answer(text: str) -> dict | None:
    t = " ".join(re.sub(r"[^\wäöüß\- ]+", " ", (text or "").lower()).split())
    for rx, answer in _NO_TOOL + (en.NO_TOOL if is_en() else []):
        if rx.search(t) and not re.search(r"\b(soundtrack|filmmusik|titelmusik|musik aus dem film)\b", t):
            return {"content": answer, "tool_calls": [], "quick": True}
    return None


def quick_intent(text: str) -> dict | None:
    """Ganz kurze Standardbefehle direkt zuordnen (das Modell lässt Ein-Wort-Sätze wie „Pause“ sonst manchmal
    ohne Werkzeug und behauptet nur, es habe angehalten). Füllwörter wie „bitte“ werden ignoriert."""
    t = re.sub(r"[^\wäöüß ]+", " ", (text or "").lower())
    t = " ".join(w for w in t.split() if w not in ("bitte", "mal", "jetzt", "doch"))
    if is_en():
        t = re.sub(r"^(please|just|can you|could you) |( please| now)$", "", t).strip()
    hit = QUICK.get(t) or (en.QUICK.get(t) if is_en() else None)
    if not hit:
        return None
    return {"content": "", "tool_calls": [{"function": {"name": hit[0], "arguments": dict(hit[1])}}], "quick": True}


# Antworten, die wie eine AUSGEFÜHRTE Aktion klingen (so formuliert execute_tool seine Ergebnisse). Kommt so ein Satz
# OHNE Werkzeugaufruf, hat das Modell nur nachgeahmt (Handy-Test „wechsel auf hr3“ → „Radio HR3 in Esszimmer“, nichts lief).
_CLAIMS_DONE = re.compile(
    r"^(Radio |Qobuz: |Spiele |Zufällig: |Film |Serie |Licht |\d+ Lichter? |Lautstärke |Raum .+ aus|Rollladen |Thermostat .+ auf |"
    r"„[^“]+“ (an|aus)|[^:?]{2,40}: (pausiert|läuft weiter|nächstes Lied|vorheriges Lied|gestoppt|stumm|Ton wieder an|"
    r"Zufallswiedergabe|Lied wird|alles wird|Wiederholen)|[\w„“ ./-]{2,40} (ist )?(an|aus|zu|auf|offen|gedimmt)[.!]?$)", re.I)
_NUDGE = ("ACHTUNG: Deine letzte Antwort hat eine Aktion bestätigt, aber KEIN Werkzeug aufgerufen – dann passiert im Haus "
          "nichts. Rufe jetzt das passende Werkzeug auf. Ist unklar, was gemeint ist, stelle eine kurze Rückfrage.")


_CLAIMS_STATE = re.compile(r"\b(ist|sind|läuft|laufen) (bereits|schon|jetzt|nun|derzeit|aktuell|momentan|gerade) (an|aus|offen|zu|"
                           r"geschlossen|eingeschaltet|ausgeschaltet|gedimmt|heller|dunkler|leiser|lauter)\b|"
                           r"\b\d+([.,]\d+)?\s*(°\s*c?|grad)\b", re.I)   # Messwert ohne Werkzeug = erfunden


_TOOL_AS_TEXT = re.compile(r"\b(light_set|light_adjust|light_color|switch_set|blind_action|climate_setpoint(_delta)?|get_temperature|lights_status|music_albums|"
                           r"music_play|stream_play|video_play|volume_set|volume_adjust|media_control|rooms_playing|room_off|"
                           r"weather)\b")


# Kündigt eine Aktion nur an, statt das Werkzeug aufzurufen („Wie warm ist es hier?“ → „Ich hole die Temperatur...“,
# qwen3:8b reproduzierbar) – ohne Werkzeug passiert danach nichts mehr
_ANNOUNCES = re.compile(r"^((okay|ok|gut|alles klar|moment|gerne?)[,.!]?\s+)?(ich\s+)?(hole|messe|schaue|sehe|prüfe|checke|"
                        r"schalte|mache|spiele|starte|fahre|stelle|suche|frage|lese|ermittle)\b", re.I)
# Behauptete Vergangenheit ohne Werkzeug („… aber ich habe die Medien ausgeschaltet.“, gemma4) – auch mitten im Satz
_DONE_CLAIM = re.compile(r"\bich habe\b.{0,80}\b\w*(schaltet|gestartet|gestellt|gefahren|gespielt|ausgeführt|erledigt|"
                         r"gemacht|geöffnet|geschlossen|gedimmt|aktiviert|deaktiviert)\b", re.I)


def claims_done(content: str) -> bool:
    """Klingt wie eine ausgeführte Aktion bzw. ein behaupteter Zustand („… ist bereits an“) – ohne Werkzeug erfunden –,
    kündigt sie nur an („Ich hole die Temperatur...“) oder der Werkzeugaufruf steht nur als Text da
    („light_set room=Gästezimmer“, qwen3)."""
    c = (content or "").strip()
    if is_en() and c:                 # englische Antworten: gleiche Prüfung mit englischen Mustern
        e = en.plain(c)
        if _TOOL_AS_TEXT.search(c) or en.CLAIMS_STATE.search(e):
            return True
        if en.DONE_CLAIM.search(e) and not en.NEGATION.search(e):
            return True
        if "?" not in c and (en.CLAIMS_DONE.match(c) or (en.ANNOUNCES.match(e) and not en.NEGATION.search(e))):
            return True
    if c and (_TOOL_AS_TEXT.search(c) or _CLAIMS_STATE.search(c)):
        return True
    if c and _DONE_CLAIM.search(c) and not re.search(r"\b(kein\w*|nicht|leider)\b", c):
        return True
    announces = _ANNOUNCES.match(c) and not re.search(r"\b(kein\w*|nicht|leider)\b", c)   # „Ich sehe kein …“ = Erklärung
    return bool(c) and "?" not in c and bool(_CLAIMS_DONE.match(c) or _CLAIMS_STATE.search(c) or announces)


async def ollama_decide(cfg: dict, prompt: str, text: str, history: list | None = None,
                        tools: list | None = None, _retry: bool = True, note: str = "") -> dict:
    """Text + Kontext → Ollama; liefert {content, tool_calls}. history = vorherige Runde
    (ursprünglicher Satz + Rückfrage der KI), damit „Kim" als Antwort auf „Welches Arbeitszimmer?" passt."""
    asked = bool(history and "?" in str(history[-1].get("content", "")))
    if asked:      # „Nein“ auf eine Rückfrage heißt: nichts tun (das Modell spielte sonst z. B. Zufallsmusik)
        t = " ".join(re.sub(r"[^\wäöüß ]+", " ", (text or "").lower()).split())
        if re.fullmatch(r"(nein|ne|nee|nö|nein danke|danke nein|abbrechen|abbruch|lass es|lass mal|vergiss es|"
                        r"stopp|egal|doch nicht|nein doch nicht|nichts)( danke)?", t):
            return {"content": "Okay, dann nicht.", "tool_calls": [], "quick": True}
        if is_en() and en.CANCEL.fullmatch(t):
            return {"content": "Okay, dann nicht.", "tool_calls": [], "quick": True}
        if is_en() and en.WRONG.fullmatch(t):
            return {"content": "Okay – wie heißt es genau?", "tool_calls": [], "quick": True}
        if re.fullmatch(r"(nein|ne|nee|nö)?\s*(das|den|die|so)?\s*(meine|meinte|wollte)\s+ich\s+(nicht|was anderes|etwas anderes)"
                        r"|(nein|ne|nee|nö)\s+(das|den|die)\s+(nicht|falsch)|falsch|das ist falsch", t):
            return {"content": "Okay – wie heißt es genau?", "tool_calls": [], "quick": True}
    if not asked:                                        # Kurzbefehle nicht als Antwort auf eine Rückfrage
        q = quick_intent(text) or quick_answer(text)
        if q:
            return q
    tune = ollama_tuning(cfg["llm"])
    # Obergrenze für die Antwort: ein Befehl braucht 20–40 Tokens (gemessen qwen3:8b Ø 31). gemma4:12b erzeugte bei einem
    # Satz endlos Tokens ohne Text/Werkzeug, bis nach 90 s abgebrochen wurde – so ist nach ~5 s Schluss (llm.max_tokens).
    try:
        max_tok = int(cfg["llm"].get("max_tokens") or 400)
    except (TypeError, ValueError):
        max_tok = 400
    body = {"model": cfg["llm"]["model"], "stream": False,
            "options": {"temperature": 0.1, "num_predict": max_tok,
                        **({"num_ctx": tune["num_ctx"]} if "num_ctx" in tune else {})},
            "messages": [{"role": "system", "content": prompt}, *(history or []),
                         {"role": "user", "content": f"{note}\n{text}" if note else text}],
            "tools": TOOLS if tools is None else tools,
            "keep_alive": tune["keep_alive"],   # Modell bleibt geladen (kein Kaltstart), Admin: Keep-Alive
            "think": False}            # Sprachbefehle brauchen kein Nachdenken: ~6x schneller
    timeout = aiohttp.ClientTimeout(total=float(cfg["llm"].get("timeout_s", 90)))
    async with aiohttp.ClientSession(timeout=timeout) as s:
        url = cfg["llm"]["url"].rstrip("/") + "/api/chat"
        async with s.post(url, json=body) as r:
            if r.status == 400 and "think" in body:
                body.pop("think")         # Modell/Server kennt das Feld nicht → ohne
                async with s.post(url, json=body) as r:
                    r.raise_for_status()
                    data = await r.json()
            else:
                r.raise_for_status()
                data = await r.json()
    msg = data.get("message", {})
    # Zeiten von Ollama (ns): wie viele Prompt-Token neu gerechnet wurden (Rest kam aus dem Zwischenspeicher), Ausgabe
    log("llm_stats", prompt_tok=data.get("prompt_eval_count"), prompt_s=round((data.get("prompt_eval_duration") or 0) / 1e9, 2),
        out_tok=data.get("eval_count"), out_s=round((data.get("eval_duration") or 0) / 1e9, 2),
        load_s=round((data.get("load_duration") or 0) / 1e9, 2), total_s=round((data.get("total_duration") or 0) / 1e9, 2))
    if not msg.get("tool_calls") and not (msg.get("content") or "").strip() and data.get("done_reason") == "length":
        # Antwort an der Längengrenze, aber nichts Verwertbares (angefangener Werkzeugaufruf, der nie endet)
        log("llm_broken", text=text, model=cfg["llm"]["model"], eval=data.get("eval_count"))
        if _retry:     # einmal neu – ohne die „zuletzt“-Notiz (Auslöser im gemessenen Fall), Verlauf bleibt
            plain = re.sub(r" · zuletzt vor .*(?=\]$)", "", note or "", flags=re.S)
            r2 = await ollama_decide(cfg, prompt, text, history, tools, _retry=False, note=plain)
            if r2["tool_calls"] or (r2["content"] and r2["content"] != "Verstanden."):
                return r2
        return {"content": "Das habe ich nicht verstanden – sag es bitte noch einmal.", "tool_calls": []}
    content = re.sub(r"\[[^\]]*(Sprecher|Raum|per Sprache)[^\]]*\]", "", msg.get("content") or "").strip()   # Notiz
    content = re.sub(r"^\[[^\]]*\]\s*", "", content).strip()        # „[Esszimmer / Küche] …“ am Anfang
    if not content and not msg.get("tool_calls") and (msg.get("content") or "").strip():
        content = "Okay."
    out = {"content": content, "tool_calls": msg.get("tool_calls") or [],
           "ctx": {"messages": body["messages"], "tools": body["tools"], "cfg": cfg}}   # für die Auswahl (waehle)
    if not out["tool_calls"] and claims_done(out["content"]):
        log("llm_no_tool", text=text, content=out["content"])
        if _retry:     # einmal nachfassen – sonst ehrlich „nicht ausgeführt“ statt einer falschen Bestätigung
            r2 = await ollama_decide(cfg, prompt, f"{en.NUDGE if is_en() else _NUDGE}\n{text}", history, tools,
                                     _retry=False, note=note)
            if r2["tool_calls"] or not claims_done(r2["content"]):
                return r2
        return {"content": "Das habe ich nicht ausgeführt – sag es bitte noch einmal.", "tool_calls": []}
    return out


_warm = {"hash": None, "hashes": set(), "task": None}


async def warm_prompt(bridge: "Bridge") -> None:
    """System-Prompt + Werkzeuge vorab einlesen lassen (Prompt-Cache von Ollama) – je VARIANTE der Werkzeugliste (alle
    Werkzeuge / ohne Mediathek / ohne Radio+Qobuz …, siehe tools_for): Ollama hält jede als eigenen Cache-Eintrag. Ohne
    das dauert der erste Befehl nach Neustart/Geräteänderung mit qwen3:8b auf Vulkan/RDNA2 ~22 s, danach ~1 s. Nur
    wenn sich Prompt/Werkzeuge geändert haben; schaltet nichts (kein Werkzeug wird ausgeführt)."""
    import hashlib
    try:
        cfg = load_cfg()
        devs, rooms = await bridge.context()
        prompt = bridge.prompt(devs, rooms, [], house_prompt(cfg), examples_text(cfg))
        variants = {json.dumps(tools_for(bridge, cfg), sort_keys=True): None}          # Mikrofon ohne Raum
        for r in rooms:                                                                  # je Raum seine Liste
            variants.setdefault(json.dumps(tools_for(bridge, cfg, [r["name"]]), sort_keys=True), r["name"])
        for tools_json, example in variants.items():
            h = hashlib.sha1((cfg["llm"]["model"] + prompt + tools_json).encode()).hexdigest()
            if h in _warm["hashes"]:
                continue
            t0 = time.time()
            await ollama_decide(cfg, prompt, "Hallo", [], tools=json.loads(tools_json), _retry=False)
            _warm["hashes"].add(h)
            _warm["hash"] = h
            log("llm_warm", model=cfg["llm"]["model"], took=round(time.time() - t0, 1),
                tools=len(json.loads(tools_json)), room=example)
    except Exception as e:  # noqa: BLE001
        log("llm_warm_error", error=str(e))


def warm_soon(bridge: "Bridge") -> None:
    t = _warm.get("task")
    if t is None or t.done():
        _warm["task"] = asyncio.ensure_future(warm_prompt(bridge))


def _as_rooms(v) -> list[str]:
    """Mikrofon-Raumangabe normalisieren: String oder Liste → 1-3 Raumnamen."""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:                       # doppelte Einträge (Admin: 3× derselbe Raum) nur einmal
        x = str(x).strip()
        if x and x.lower() not in {o.lower() for o in out}:
            out.append(x)
    return out[:3]


def wake_phrase(cfg: dict) -> str:
    """Modellname → gesprochene Phrase: hey_jarvis → „Hey Jarvis", alexa → „Alexa"."""
    return " ".join(w.capitalize() for w in str(cfg["wake"].get("model", "hey_jarvis")).split("_") if w and not w.startswith("v"))


def strip_wake(text: str, cfg: dict) -> tuple[str, bool]:
    """Wake-Phrase vorn aus dem Transkript schneiden (Whisper schreibt sie mal „Hey, Jarvis",
    mal „Hey Jervis"/„Jarvis,"). Liefert (Befehl, gefunden). Gesucht wird nur in den ersten Wörtern."""
    from rapidfuzz import fuzz
    from .media import koelner
    key = wake_phrase(cfg).split()[-1].lower()           # „jarvis"
    tokens = text.split()
    for i, tok in enumerate(tokens[:6]):
        w = "".join(c for c in tok.lower() if c.isalpha())
        if len(w) >= 3 and (fuzz.ratio(w, key) >= 70 or koelner(w) == koelner(key)):
            rest = " ".join(tokens[i + 1:]).lstrip(" ,.!?:;-–")
            return rest, True
    return text, False


def wake_in_text(text: str, cfg: dict) -> bool:
    """Gegenprüfung nach openWakeWord: klingt einer der ersten Wörter wie die Wake-Phrase? Absichtlich tolerant (Whisper/
    Whisper schreiben „Hi Javis“, „Hi Yavis“, „H. Jarvis“, zusammengezogen „Hayawis“) – verworfen wird nur, was
    offensichtlich keine Anrede ist („Mach das Licht an“, Fernsehsatz)."""
    from rapidfuzz import fuzz
    from .media import koelner
    phrase = wake_phrase(cfg).lower()                       # „hey jarvis“
    key = phrase.split()[-1]                                # „jarvis“
    words = ["".join(c for c in t.lower() if c.isalpha()) for t in (text or "").split()[:4]]
    words = [w for w in words if w]
    tail = key[-2:]                                         # „is“ – alle beobachteten Varianten enden so (Javis, Yairis, Hayawis)
    joined = phrase.replace(" ", "")
    for i, w in enumerate(words):
        if len(w) >= 3 and w.endswith(tail) and koelner(w) == koelner(key):     # „Harvest“ klingt gleich, endet anders
            return True
        if len(w) >= 3 and w.endswith(tail) and (fuzz.ratio(w, key) >= 55 or fuzz.ratio(w, joined) >= 55):
            return True                                     # „Harvest“, „Harvey“ enden anders → keine Anrede
        if i + 1 < len(words) and words[i + 1].endswith(tail) and fuzz.ratio(w + words[i + 1], joined) >= 70:
            return True
    return False


def fix_rooms(text: str, rooms: list[str]) -> str:
    """Verhörte Raumnamen im Transkript richten („S-Zimmer“ → Esszimmer, „Wonzimmer“ → Wohnzimmer): nur wenn ein Wort
    EINEM Raum klar ähnelt (≥ 85 %, nächster Raum ≥ 8 Punkte schlechter) – sonst bleibt das Wort, das Modell fragt nach."""
    from rapidfuzz import fuzz
    names = [(r, re.sub(r"[^a-zäöüß]", "", r.split("/")[0].lower())) for r in rooms if r]
    out = []
    for tok in text.split():
        core = re.sub(r"[^a-zäöüß]", "", tok.lower())
        rep = tok
        if len(core) >= 5 and not any(core == n for _, n in names):
            sc = sorted(((fuzz.ratio(core, n), r, n) for r, n in names), reverse=True)
            if sc and sc[0][0] >= 85 and (len(sc) < 2 or sc[0][0] - sc[1][0] >= 8):
                head = re.match(r"^[^\wäöüß]*", tok).group(0)
                tail = re.search(r"[^\wäöüß]*$", tok).group(0)
                rep = head + sc[0][1].split("/")[0].strip() + tail
        out.append(rep)
    return " ".join(out)


def _mic_rooms(client: str, mics: dict) -> list[str]:
    """Raum-Zuordnung für ein Mikrofon: exakter Name, sonst normalisiert (Groß/klein)."""
    if client in mics:
        return _as_rooms(mics[client])
    norm = str(client).strip().lower()
    for k, v in (mics or {}).items():
        if str(k).strip().lower() == norm:
            return _as_rooms(v)
    return []


# --------------------------------------------------------------------------------------
# WebSocket /mic – die Mikrofon-Schnittstelle (PCM16, 16 kHz, mono)
# --------------------------------------------------------------------------------------
MIC_SEEN: dict[str, float] = {}          # Mikrofon-Name -> zuletzt gesehen (Unix-Zeit)
MIC_IP: dict[str, str] = {}              # Mikrofon-Name -> zuletzt gesehene IP (nur zur Orientierung im Admin)


def _load_seen() -> None:
    try:
        raw = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            MIC_SEEN.update({str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))})
            MIC_IP.update({str(k): str(v) for k, v in (raw.get("__ip__") or {}).items()})
    except Exception:  # noqa: BLE001
        pass


def _save_seen() -> None:
    try:
        keep = dict(sorted(MIC_SEEN.items(), key=lambda x: -x[1])[:50])
        SEEN_FILE.write_text(json.dumps({**keep, "__ip__": {k: MIC_IP[k] for k in keep if k in MIC_IP}}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


_load_seen()


def _wake_available() -> list[str]:
    """Im openWakeWord-Paket vorhandene Wake-Word-Modelle (ohne Hilfsmodelle)."""
    try:
        import glob
        import os
        import openwakeword
        d = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
        names = set()
        for p in glob.glob(os.path.join(d, "*.onnx")):
            base = os.path.basename(p)
            if "embedding" in base or "melspectrogram" in base:
                continue
            names.add(base.rsplit("_v", 1)[0])
        return sorted(names)
    except Exception:  # noqa: BLE001
        return []


STT_DEVICE = {"whisper-vulkan": "GPU (Vulkan)", "whisper-vulkan-multi": "GPU (Vulkan)", "whisper-gpu": "GPU (DirectML)"}


async def stt_state(engine: VoiceEngine, stt_cfg: dict) -> dict:
    """Was die Erkennung WIRKLICH tut (für den Admin-Status): eingestellt, aktiv, wo sie rechnet, ob sie bereit ist.
    GPU-Dienste: antwortet der Dienst? CPU-Modelle: schon im Speicher (lädt sonst beim ersten Satz)?"""
    model = stt_cfg.get("model")
    active = (getattr(engine, "_stt_key", None) or (None,))[0] if engine._stt is not None else None
    out = {**stt_cfg, "active": active, "device": STT_DEVICE.get(model, "CPU")}
    if model in VoiceEngine.GPU_SERVICES:
        out["ready"] = await asyncio.to_thread(engine._gpu_ok, model, 0.5)
        out["port"] = VoiceEngine.GPU_SERVICES[model]["port"]
    else:
        out["ready"] = active == model
    return out


async def llm_state(llm_cfg: dict) -> dict:
    """Ist das Modell bei Ollama geladen, und wie viel davon liegt auf der Grafikkarte (/api/ps)?"""
    out = dict(llm_cfg)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.5)) as s:
            async with s.get(f"{str(llm_cfg.get('url') or '').rstrip('/')}/api/ps") as r:
                ps = await r.json()
        m = next((x for x in ps.get("models") or [] if x.get("name") == llm_cfg.get("model")
                  or x.get("model") == llm_cfg.get("model")), None)
        out["reachable"], out["loaded"] = True, bool(m)
        if m:
            out["size_gb"] = round((m.get("size") or 0) / 1e9, 1)
            out["gpu_pct"] = round(100 * (m.get("size_vram") or 0) / max(1, m.get("size") or 1))
    except Exception:  # noqa: BLE001
        out["reachable"], out["loaded"] = False, False
    return out


async def tts_state(tcfg: dict) -> dict:
    """Sprachausgabe für den Admin-Status: gewähltes Modell, Stimme, bereit? (Qwen3: Dienst antwortet; Pocket:
    Dauerprozess läuft – startet sonst beim ersten Satz; Piper: Stimme installiert)."""
    eng = str(tcfg.get("engine") or "piper")
    out = {"enabled": bool(tcfg.get("enabled")), "engine": eng}
    sp = Speaker.current
    if eng == "qwen3":
        q = Speaker._qwen_cfg(tcfg)
        out.update(voice=str(q.get("ref") or ""), port=int(q["port"]), model=str(q.get("model") or "model-base"),
                   ready=bool(sp and await asyncio.to_thread(sp._qwen_ok, int(q["port"]), 0.5)))
    elif eng == "pocket":
        p = getattr(sp, "_pocket_proc", None) if sp else None
        out.update(voice=str((tcfg.get("pocket") or {}).get("voice") or "eponine"),
                   ready=bool(p is not None and p.poll() is None))
    else:
        out.update(voice=str(tcfg.get("voice") or ""), ready=(TTS_DIR / f"{tcfg.get('voice')}.onnx").is_file())
    return out


async def handle_status(request, engine: VoiceEngine, cfg: dict):
    wake = cfg["wake"]
    fresh = load_cfg()                   # Admin-Änderungen sofort zeigen (Erkennung wechselt beim nächsten Satz)
    return web.json_response({
        "service": "c4voice", "dry_run": fresh["dry_run"], "port": cfg["port"], "host": HOST,
        "wake": {"enabled": wake["enabled"], "model": wake["model"],
                 "loaded": engine._ow is not None and not engine._ow_failed,
                 "error": None if not engine._ow_failed else engine._wake_last_error,
                 "available": _wake_available()},
        "stt": await stt_state(engine, fresh["stt"]), "llm": await llm_state(fresh["llm"]),
        "tts": await tts_state(fresh.get("tts") or {}),
        "mics": [{"name": n, "last_seen": round(ts, 1), "ip": MIC_IP.get(n, "")} for n, ts in
                 sorted(MIC_SEEN.items(), key=lambda x: -x[1])[:20]],
    })


async def handle_mic(request, engine: VoiceEngine, bridge: Bridge, cfg: dict,
                     speaker: "Speaker") -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=4_000_000)
    await ws.prepare(request)
    hello = {}
    try:
        first = await ws.receive_json(timeout=10)
        if first.get("type") == "hello":
            hello = first
    except Exception:  # noqa: BLE001
        pass
    push_to_talk = bool(hello.get("push_to_talk"))
    client = hello.get("client", "?")
    user = hello.get("user")          # von der Bridge eingetragen (angemeldeter Benutzer)
    # Räume des Sprechers: Client-Meldung oder dauerhafte Zuordnung aus der Config (1-3).
    # Mikro-Zuordnung je Verbindung frisch von der Platte lesen — Speichern im Admin
    # wirkt sofort, ohne Dienst-Neustart (offene Verbindungen nutzen weiterhin den alten Stand).
    speaker_rooms = _as_rooms(hello.get("room")) or _mic_rooms(client, load_cfg().get("mics", {}))
    MIC_SEEN[client] = time.time()
    if hello.get("ip"):
        MIC_IP[client] = str(hello["ip"])
    _save_seen()
    log("client", client=client, user=user, mode="push_to_talk" if push_to_talk else "wake",
        room=",".join(speaker_rooms) or None)
    warm_soon(bridge)                 # Prompt-Cache vorwärmen, solange noch niemand spricht

    # Client darf „nur simulieren" verlangen (Tests) – das macht den Dienst nur sicherer, nie unsicherer
    simulate = bool(hello.get("simulate"))
    # Sprachausgabe nur an Clients, die abspielen können (hello.tts = false → nur Text)
    wants_audio = hello.get("tts", True) is not False
    await ws.send_json({"type": "ready", "wake": cfg["wake"]["enabled"] and not push_to_talk,
                        "dry_run": load_cfg()["dry_run"] or simulate, "room": speaker_rooms or None,
                        "tts": bool(load_cfg()["tts"].get("enabled")) and wants_audio,
                        "format": "pcm16/16000/mono"})
    collector: UtteranceCollector | None = None
    ptt_buf = bytearray()
    collecting = False
    # Wake-Modus: Ringpuffer der letzten Sekunden (nur RAM, laufend überschrieben). Bei Erkennung geht
    # der Vorlauf ab kurz vor dem Wake-Word mit an Whisper → „Hey Jarvis mach das Licht an" ohne Pause.
    ring = bytearray()
    RING_BYTES, PRE_BYTES = 5 * 32000, int(2.2 * 32000)     # 5 s Puffer, 2,2 s Vorlauf
    wake_used = False
    wake = None                       # eigenes openWakeWord-Modell dieser Verbindung (lazy)
    wake_score = 0.0                  # Score des letzten Wake-Words (Gegenprüfung nur bei unsicherem Anschlagen)
    wake_clip = b""                   # Audio direkt vor dem Anschlagen = das Wake-Word
    WAKE_CLIP_BYTES = int(1.6 * 32000)
    # Dialogzustand (offene Rückfrage + Angebote, letzte Aktion) – Regeln zentral in dialog.py
    dialog = Dialog()
    mute_until = 0.0                  # während der eigenen Sprachausgabe nicht zuhören (Echo)
    duck_task: asyncio.Task | None = None
    ducked = False
    awaiting = ""                     # „answer“/„followup“: hört gerade ohne Wake-Word auf die Antwort

    def duck(on: bool) -> None:
        """Musik in den Räumen des Mikrofons leiser (on) bzw. zurück – nie im Trockenlauf/Simulation, nie blockierend."""
        nonlocal duck_task, ducked
        c = load_cfg()
        dc = c.get("duck") or {}
        if not dc.get("enabled", True) or not speaker_rooms or c.get("dry_run") or simulate or on == ducked:
            return
        ducked = on
        prev = duck_task

        async def go():
            if prev:                   # Zurückstellen erst, wenn das Absenken durch ist
                try:
                    await prev
                except Exception:  # noqa: BLE001
                    pass
            try:
                r = await bridge.post("/api/duck", {"rooms": speaker_rooms, "on": on, **{k: dc[k] for k in
                                                    ("factor", "min_level", "max_s") if k in dc}})
                log("duck", on=on, rooms=r.get("rooms"))
            except Exception as e:  # noqa: BLE001
                log("duck_error", on=on, error=str(e))
        duck_task = asyncio.ensure_future(go())

    async def run(pcm: bytes, from_wake: bool = False, score: float | None = None, clip: bytes = b"") -> str:
        nonlocal collector, mute_until
        collector = None
        res = await process_utterance(ws, pcm, engine, bridge, cfg, client, speaker_rooms,
                                      from_wake=from_wake, simulate=simulate, wake_score=score, wake_clip=clip,
                                      speaker=speaker if wants_audio else None, dialog=dialog)
        # Ansage läuft auf dem Gerät erst NACH dem Senden (Übertragung, Dekodieren) → 1 s Puffer gegen das eigene Echo
        mute_until = time.time() + res.get("speak_s", 0.0) + (1.0 if res.get("speak_s") else 0.0)
        return res["next"]

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                m = msg.json()
                if m.get("type") == "start":            # Push-to-Talk Beginn
                    ptt_buf.clear()
                    collecting = True
                    duck(True)
                    await ws.send_json({"type": "state", "stage": "listening"})
                elif m.get("type") == "stop" and collecting:
                    collecting = False
                    if len(ptt_buf) > 32000:            # > 1 s
                        await run(bytes(ptt_buf))
                    ptt_buf.clear()
                    duck(False)
                elif m.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            elif msg.type == WSMsgType.BINARY and msg.data:
                pcm = msg.data
                if push_to_talk:
                    # Push-to-Talk: Audio zählt direkt (start-Signal optional/tolerant)
                    if not collecting:
                        collecting = True
                        await ws.send_json({"type": "state", "stage": "listening"})
                    ptt_buf.extend(pcm)
                    if len(ptt_buf) > 16000 * 30:       # > 30 s Zwangsende
                        collecting = False
                        await run(bytes(ptt_buf)); ptt_buf.clear()
                        duck(False)
                    continue
                if time.time() < mute_until:            # eigene Ansage läuft gerade aus dem Lautsprecher
                    continue
                if cfg["wake"]["enabled"]:
                    ring.extend(pcm)
                    del ring[:-RING_BYTES]
                    if not collecting:
                        if wake is None:
                            wake = await asyncio.to_thread(engine.wake_model)
                        score = await asyncio.to_thread(engine.wake_feed, wake, pcm)
                        if score < float(cfg["wake"]["threshold"]):
                            continue
                        # Wake-Word erkannt: lückenlos weiter aufnehmen, Vorlauf (inkl. bereits
                        # gesprochenem Befehlsanfang) übernehmen – keine Sprechpause mehr nötig
                        engine.wake_reset(wake)
                        collector = UtteranceCollector()
                        collector.preload(bytes(ring[-PRE_BYTES:]))
                        wake_clip = bytes(ring[-WAKE_CLIP_BYTES:])   # das Wake-Word selbst (für die Gegenprüfung)
                        collecting, wake_used = True, True
                        wake_score = float(score)
                        duck(True)
                        await ws.send_json({"type": "wake", "score": round(score, 2)})
                        log("wake", client=client, score=round(float(score), 2))   # zum Beobachten: Fehlalarme bei niedriger Schwelle
                        continue
                if collector is None:
                    collector = UtteranceCollector()
                    collector.arm()                     # ohne Wake: erste Stimme startet
                res = collector.feed(pcm)
                if res in ("done", "timeout"):
                    ok = collector.usable()
                    audio = collector.audio()
                    if awaiting and not ok:
                        log("followup_end", client=client, kind=awaiting, reason="keine Antwort gehört",
                            speech_ms=collector.speech_ms, total_ms=collector.total_ms, min_ms=collector.min_speech_ms,
                            buf=len(collector.buf), result=res)
                    awaiting = ""
                    collector = None
                    if cfg["wake"]["enabled"]:
                        collecting = False              # danach wieder aufs Wake-Word warten (vorher blieb es offen!)
                        ring.clear()
                    result = await run(audio, from_wake=wake_used, score=wake_score if wake_used else None,
                                       clip=wake_clip if wake_used else b"") if ok else "idle"
                    wake_used = False
                    if result in ("followup", "answer"):
                        # followup: nur „Hey Jarvis" gehört → bis 6 s auf den Befehl warten
                        # answer: die KI hat zurückgefragt → bis 12 s auf die Antwort warten (ohne Wake-Word); die
                        # Zeit läuft erst nach der Ansage (während mute_until werden keine Pakete gezählt)
                        collector = UtteranceCollector(max_ms=6000 if result == "followup" else 12000,
                                                       min_speech_ms=500 if result == "followup" else 150)
                        awaiting = result
                        log("followup_listen", client=client, kind=result,
                            seconds=6 if result == "followup" else 12)
                        collecting = True
                        engine.wake_reset(wake)
                        await ws.send_json({"type": "state", "stage": "listening"})
                    elif result == "idle":
                        await ws.send_json({"type": "state", "stage": "idle"})
                    if result not in ("followup", "answer"):
                        duck(False)             # Befehl fertig (bzw. nichts gehört) → Musik wieder wie vorher
    except Exception as e:  # noqa: BLE001
        log("client_error", client=client, error=str(e))
    finally:
        duck(False)                             # Verbindung weg → nichts abgesenkt zurücklassen
        if duck_task:
            try:
                await asyncio.wait_for(duck_task, 10)
            except Exception:  # noqa: BLE001
                pass
        log("client_closed", client=client)
    return ws


def tts_parts(text: str, engine: str) -> list[str]:
    """Qwen3: Satz für Satz erzeugen und schicken (erster Ton nach dem ersten Satz statt nach der ganzen Antwort);
    sehr kurze Sätze bleiben beim nächsten. Pocket: ganze Antwort auf einmal – einzeln erzeugte Sätze klangen
    abgehackt (Betonung beginnt je Satz neu, kurze Sätze schwach); ~0,3 s je Satz ist schnell genug. Piper: alles auf einmal."""
    if engine != "qwen3":
        return [text]
    out: list[str] = []
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        if out and len(out[-1]) < 25:
            out[-1] += " " + s
        elif s:
            out.append(s)
    return out or [text]


async def speak(ws, speaker: "Speaker | None", text: str, cfg: dict) -> float:
    """Antwort sprechen (Piper oder Qwen3-TTS) und als WAV an den Client schicken; liefert, wie lange der Client
    ab jetzt noch spricht (s).
    Mehrere Sätze gehen als mehrere WAV-Nachrichten raus – der Client spielt sie nacheinander."""
    tcfg = cfg.get("tts") or {}
    if not speaker or not tcfg.get("enabled") or not text.strip():
        return 0.0
    play_end = time.time()          # Ende der Wiedergabe beim Client: Teile laufen nacheinander, der nächste wird
                                    # erzeugt, während der vorige schon spielt → nicht einfach Längen addieren
    text = out_text(text)
    for part in tts_parts(en.speakable(text) if is_en() else speakable(text), str(tcfg.get("engine") or "piper")):
        try:
            data = await asyncio.to_thread(speaker.wav, part, tcfg)
        except Exception as e:  # noqa: BLE001
            log("tts_error", error=str(e))
            await ws.send_json({"type": "tts_error", "message": str(e)})
            return max(0.0, play_end - time.time())
        secs = wav_seconds(data)
        await ws.send_json({"type": "tts", "format": "wav", "seconds": round(secs, 2), "text": part})
        await ws.send_bytes(data)
        play_end = max(play_end, time.time()) + secs
    return max(0.0, play_end - time.time())


async def process_utterance(ws, pcm: bytes, engine: VoiceEngine, bridge: Bridge,
                            cfg: dict, client: str, speaker_rooms: list[str] | None = None,
                            from_wake: bool = False, simulate: bool = False, wake_score: float | None = None,
                            wake_clip: bytes = b"",
                            speaker: "Speaker | None" = None, dialog: Dialog | None = None) -> dict:
    """Rückgabe {next, history, speak_s}: next = 'done' | 'followup' (nur Wake-Word gehört → auf Befehl
    warten) | 'answer' (KI hat zurückgefragt → Antwort ohne Wake-Word annehmen, history mitgeben)."""
    t0 = time.time()
    cfg = load_cfg()   # frisch lesen: Config-Änderungen (Stufen, dry_run, LLM, TTS) wirken sofort
    if simulate:
        cfg["dry_run"] = True
    dialog = dialog if dialog is not None else Dialog()
    history, pending = dialog.history(), dialog.pending()      # nur bei offener Rückfrage (dialog.py)
    log("utterance", client=client, seconds=round(len(pcm) / 32000, 1), followup=bool(history))
    out = {"next": "done", "history": [], "speak_s": 0.0}     # speak_s = Restdauer der Ansage bei Rückgabe

    speak_end = [0.0]                  # Zeitpunkt, an dem die letzte Ansage beim Client ausgesprochen ist

    async def say(msg: str, c: dict) -> None:
        speak_end[0] = max(speak_end[0], time.time() + await speak(ws, speaker, msg, c))
        out["speak_s"] = max(0.0, speak_end[0] - time.time())
    try:
        await ws.send_json({"type": "state", "stage": "stt"})
        try:                                    # Raum-/Interpretennamen als Schreibhilfe für Whisper (5 min zwischengesp.)
            vocab = sorted({r["name"] for r in (await bridge.context())[1]})
        except Exception:  # noqa: BLE001
            vocab = []
        raw = await asyncio.to_thread(engine.stt, pcm, {"rooms": vocab, "artists": bridge.artists})
        text, had_wake = strip_wake(raw, cfg) if from_wake else (raw, False)
        wcfg = cfg.get("wake") or {}
        unsure = wake_score is None or wake_score < float(wcfg.get("verify_below", 0.5))
        rest_de = wortschatz.strip_wake_deutsch(raw) if from_wake else None
        if from_wake and not had_wake and rest_de is not None:   # Canary schreibt „Jarvis“ als „Ja, das“ (wortschatz.py)
            had_wake, text = True, rest_de
        if from_wake and not had_wake and wcfg.get("verify", True) and unsure:
            # (Versuch 18.10.: Wake-Stück auf Englisch erkennen – Canary übersetzt dann statt abzuschreiben, verworfen.
            #  wake_clip wird weiter mitgegeben, falls eine spätere Prüfung ihn braucht.)
            if wake_in_text(raw, cfg):
                had_wake = True                         # tolerant erkannt („Hi Yavis …“): Anrede bis zum ersten Komma weg
                text = raw.split(",", 1)[1].strip() if "," in raw[:20] else raw
            else:
                # openWakeWord hat angeschlagen, im Text steht aber keine Anrede → Fehlalarm (Fernseher, ähnliches Wort):
                # nichts tun, nichts sagen. So kann die Schwelle niedriger stehen, ohne dass mehr falsch ausgelöst wird.
                log("wake_rejected", client=client, text=raw, score=wake_score)
                await ws.send_json({"type": "wake_rejected", "text": raw})
                await ws.send_json({"type": "state", "stage": "idle"})     # sonst steht das Gerät auf „transkribiere“
                return out
        text = fix_rooms(text, vocab)           # „S-Zimmer“ → Esszimmer (eindeutige Hörfehler)
        text = wortschatz.correct(text)         # Qobuz/TuneIn-Hörfehler, Klang-Korrekturen (wortschatz.py)
        await ws.send_json({"type": "transcript", "text": text, "raw": raw})
        log("transcript", text=text, raw=raw if had_wake else None)
        if from_wake and had_wake and len(text) < 3:
            await ws.send_json({"type": "wake_only"})
            out["next"] = "followup"
            return out
        if not text:
            msg = out_text("Ich habe nichts verstanden.")
            await ws.send_json({"type": "decision", "text": msg})
            if not history:   # bei ausbleibender Antwort auf eine Rückfrage nicht nochmal reden
                await say(msg, cfg)
            return out
        await ws.send_json({"type": "state", "stage": "llm"})
        devs, rooms = await bridge.context()
        dialog.rooms = [r_["name"] for r_ in rooms]
        try:   # Wetter/Websuche-Bereitschaft frisch (Admin-Änderung wirkt ab dem nächsten Satz)
            bridge.assist = await bridge.get("/api/assist/status")
        except Exception:  # noqa: BLE001
            pass
        # Für Raum-/Geräteprüfung zählt der Dialog: „Spiel … im Esszimmer“ → Rückfrage → „Ja, Folge 104“ bleibt im
        # Esszimmer; „Mach sie wieder an“ → Gerät von eben. Ein abgeschlossenes anderes Thema zählt nicht (dialog.py).
        if dialog.begin(text):                  # Antwort auf Rückfrage nennt anderes Thema → neuer Befehl
            log("dialog_topic_change", text=text)
            history, pending = [], []
        said = dialog.said(text)
        recent = dialog.note(text)
        if recent:
            log("dialog_recent", note=recent)
        chosen = match_offer(text, pending or [])
        again = None if chosen else dialog.repeat_for_room(text, rooms)
        bare = " ".join(re.sub(r"[^\wäöüß ]+", " ", text.lower()).split())
        if not history and (re.fullmatch(r"(ok|okay|passt|stimmt|alles klar|gut|super|prima|danke|danke schön|dankeschön|"
                                        r"vielen dank|danke dir)( danke| dir| schön)?", bare) or (is_en() and en.ACK.fullmatch(bare))):
            log("bare_ack", text=text)              # Bestätigung ohne Auftrag: kurz quittieren, keine Rückfrage öffnen
            r = {"content": "Gern.", "tool_calls": [], "quick": True}
        elif not history and (_YES.fullmatch(bare) or re.fullmatch(r"(nein|ne|nee|nö)( danke)?", bare)
                              or (is_en() and (en.YES.fullmatch(bare)
                                               or re.fullmatch(r"(no|nope|nah)( thanks| thank you)?", bare)))):
            # „Ja“/„Nein“ ohne offene Rückfrage: nichts zu bestätigen (das Modell machte daraus z. B. „Raum aus“)
            log("bare_yes_no", text=text)
            r = {"content": "Was soll ich tun?", "tool_calls": [], "quick": True}
        elif chosen:                            # Antwort auf „Meinst du …?“: festes Angebot ausführen, ohne Modell
            log("offer", text=text, chosen=chosen["label"])
            r = {"content": "", "tool_calls": [{"function": dict(chosen["call"])}], "quick": True}
        elif again:                             # „Und im Wohnzimmer?“ → letzte Aktion dort (dialog.py)
            log("repeat_for_room", text=text, calls=again)
            r = {"content": "", "tool_calls": [{"function": c} for c in again], "quick": True}
        else:
            await bridge.load_caps(speaker_rooms)
            playing = await bridge.now_playing(speaker_rooms)
            r = await ollama_decide(cfg, bridge.prompt(devs, rooms, speaker_rooms, house_prompt(cfg), examples_text(cfg)), text, history,
                                    tools=tools_for(bridge, cfg, speaker_rooms), note=bridge.note(devs, speaker_rooms, playing, recent))
        if any(c.get("function", {}).get("name") == "web_search" for c in r["tool_calls"]):
            await say("Moment, ich schaue nach.", cfg)   # Suche dauert ein paar Sekunden
        results, names = [], []
        ctx_tok = _AUSWAHL_CTX.set(r.get("ctx"))       # nur nach einer echten Modellrunde (nicht bei Angeboten/Kurzbefehlen)

        async def run_(c):                             # gewählten Eintrag ausführen – ohne neue Auswahl
            tok = _AUSWAHL_CTX.set(None)
            try:
                return await execute_tool(c, bridge, cfg["dry_run"], speaker_rooms, cfg, utterance=None, current=None)
            finally:
                _AUSWAHL_CTX.reset(tok)
        for call in r["tool_calls"]:
            await ws.send_json({"type": "state", "stage": "exec"})
            names.append(call.get("function", {}).get("name"))
            try:
                res = await execute_tool(call.get("function", {}), bridge, cfg["dry_run"], speaker_rooms, cfg,
                                         utterance=None if chosen else said,
                                         current=None if chosen else text)
                if isinstance(res, Auswahl) and r.get("ctx"):
                    await ws.send_json({"type": "state", "stage": "llm"})          # zweite Runde: „denkt nach …“
                    f_ = dict(call.get("function") or {})
                    if isinstance(f_.get("arguments"), str):
                        f_["arguments"] = json.loads(f_["arguments"] or "{}")
                    res = await waehle(res, f_, r["ctx"], run_)
                elif isinstance(res, Auswahl):
                    res = await run_(res.rueckfall)
                results.append(res)
            except Exception as e:  # noqa: BLE001
                results.append(f"Fehler bei {call.get('function', {}).get('name')}: {e}")
        _AUSWAHL_CTX.reset(ctx_tok)
        answer = out_text(" · ".join(results) if results else (r["content"] or "Verstanden."))
        log("decision", text=answer, tools=names, took=round(time.time() - t0, 1))
        await ws.send_json({"type": "decision", "text": answer,
                            "tools": r["tool_calls"], "dry_run": cfg["dry_run"]})
        tcfg = dict(cfg.get("tts") or {})
        if INFO_TOOLS & set(names):         # Auskünfte immer ganz sprechen, auch im Kurzmodus
            tcfg["mode"] = "full"
        await say(spoken_answer(results, answer, tcfg, speaker_rooms), {**cfg, "tts": tcfg})
        # Rückfrage (kein Werkzeug, endet mit „?") → Antwort ohne Wake-Word erlauben; der Dialog (dialog.py) hält den
        # Verlauf nur, solange die Frage offen ist – sonst nur die letzte Aktion als Notiz
        asks = "?" in answer or re.search(r"\b(welche[nrms]?|nenne|meinst|sag mir)\b", answer.lower()) \
            or (is_en() and en.ASKS.search(answer.lower()))
        tool_asks = any(isinstance(x, Problem) and "?" in x for x in results)
        is_question = bool((not r["tool_calls"] and asks) or tool_asks)
        offers = next((x.offers for x in results if isinstance(x, Problem) and x.offers), [])
        calls = []
        for c in r["tool_calls"]:
            f = dict(c.get("function") or {})
            if isinstance(f.get("arguments"), str):
                try:
                    f["arguments"] = json.loads(f["arguments"])
                except Exception:  # noqa: BLE001
                    f["arguments"] = {}
            calls.append({"name": f.get("name"), "arguments": dict(f.get("arguments") or {})})
        dialog.end(text, answer, names, is_question, offers,
                   speak_end=time.time() + out["speak_s"] + (1.0 if out["speak_s"] else 0.0), calls=calls,
                   ok=not any(isinstance(x, Problem) or str(x).startswith("Fehler bei ") for x in results))
        if not simulate and cfg.get("log_commands", True):
            command_log({
                "client": client, "rooms": speaker_rooms or [], "text": text, "raw": raw,
                "history": history or [],                         # offene Rückfrage davor (Frage + Antwort)
                "via": ("offer" if chosen else "repeat" if again else "quick" if r.get("quick") else "llm"),
                "model": (cfg.get("llm") or {}).get("model"), "calls": calls, "answer": answer,
                "question": is_question, "ok": not any(isinstance(x, Problem) or str(x).startswith("Fehler bei ")
                                                       for x in results),
                "dry_run": bool(cfg.get("dry_run")), "took": round(time.time() - t0, 2)})
        warm_soon(bridge)
        if is_question and (cfg.get("tts") or {}).get("followup", True):
            out["next"] = "answer"
            await ws.send_json({"type": "question", "text": answer})
    except Exception as e:  # noqa: BLE001
        log("pipeline_error", error=str(e))
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:  # noqa: BLE001
            pass
    return out


async def handle_tts(request, speaker: "Speaker"):
    """Probe-Ansage für die Admin-Seite (über die Bridge): {text, voice?, speed?, volume?} → audio/wav."""
    body = await request.json()
    tcfg = {**load_cfg()["tts"], **{k: body[k] for k in ("voice", "speed", "volume") if body.get(k)}}
    text = str(body.get("text") or "").strip()[:400] or "Hallo, ich bin die Sprachausgabe der Bridge."
    try:
        t = time.time()
        data = await asyncio.to_thread(speaker.wav, speakable(text), tcfg)
        log("tts_test", voice=tcfg.get("voice"), took=round(time.time() - t, 2))
    except FileNotFoundError as e:
        return web.json_response({"detail": str(e)}, status=404)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"detail": f"{type(e).__name__}: {e}"}, status=500)
    return web.Response(body=data, content_type="audio/wav")


async def handle_info(request, cfg: dict):
    return web.json_response({"service": "c4voice", "dry_run": cfg["dry_run"],
                              "wake": cfg["wake"], "stt": cfg["stt"], "llm": cfg["llm"]})


def main() -> None:
    cfg = load_cfg()
    engine = VoiceEngine(cfg)
    engine._gpu_stop_others((cfg.get("stt") or {}).get("model"))
    if (cfg.get("stt") or {}).get("model") in VoiceEngine.GPU_SERVICES:
        engine._gpu_start(cfg["stt"]["model"])  # GPU-Dienst gleich hochfahren – sonst erkennt der erste Satz noch der CPU-Rückfall
    bridge = Bridge(cfg["bridge_url"])
    speaker = Speaker()
    _t = cfg.get("tts") or {}
    if _t.get("enabled") and _t.get("engine") == "qwen3":   # gleich laden (~7 s), sonst spricht der erste Satz Piper
        threading.Thread(target=speaker.qwen_start, args=(_t,), daemon=True).start()
    if _t.get("enabled") and _t.get("engine") == "pocket":  # Dauerprozess laden + Grafikkarte aufwärmen (erster Satz)
        threading.Thread(target=speaker.wav, args=("Bereit.", _t), daemon=True).start()
    elif _t.get("engine") != "qwen3":                       # übrig gebliebenen Qwen-Dienst beenden (Grafikspeicher)
        threading.Thread(target=speaker.qwen_stop, args=(_t,), daemon=True).start()

    @web.middleware
    async def only_bridge(request, handler):
        # ohne gültigen Token (= nicht die Bridge) wird nichts beantwortet
        if TOKEN and not hmac.compare_digest(request.headers.get(TOKEN_HEADER, ""), TOKEN):
            log("rejected", peer=request.remote, path=request.path)
            raise web.HTTPForbidden(text="c4voice: nur über die Bridge erreichbar")
        return await handler(request)

    app = web.Application(middlewares=[only_bridge])
    app.router.add_get("/", lambda r: handle_info(r, cfg))
    app.router.add_get("/status", lambda r: handle_status(r, engine, cfg))
    app.router.add_get("/mic", lambda r: handle_mic(r, engine, bridge, cfg, speaker))
    app.router.add_post("/tts", lambda r: handle_tts(r, speaker))

    async def warm_on_start(_app):
        async def loop():          # die Bridge startet c4voice, bevor sie selbst Anfragen annimmt → kurz warten
            for _ in range(12):
                await asyncio.sleep(10)
                await warm_prompt(bridge)
                if _warm["hash"]:
                    return
        asyncio.ensure_future(loop())
    app.on_startup.append(warm_on_start)
    log("ready", host=HOST, port=cfg["port"], token=bool(TOKEN), wake=cfg["wake"]["enabled"],
        stt_model=cfg["stt"]["model"], llm=cfg["llm"]["url"], dry_run=cfg["dry_run"])
    web.run_app(app, host=HOST, port=int(cfg["port"]), print=None)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("fatal", error=str(e))
        sys.exit(1)
