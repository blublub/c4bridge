"""GPU-Bausteine für c4voice laden (Admin › Sprachsteuerung): Whisper auf der Grafikkarte (Vulkan) und Qwen3-TTS.

Quellen (feste URLs, Prüfsumme je Datei):
- Unsere Pakete (selbst gebaut, keine feste Quelle anderswo): Release von blublub/c4bridge, Prüfsummen aus SHA256SUMS.txt
  derselben Release – wie bei MediaMTX (streamer.py).
  · whisper-vulkan-win-x64.zip  whisper.cpp mit GGML_VULKAN, statisch gebaut (MIT) → bin/whisper-vulkan/
  · qwen3-engine-win-x64.zip    HaujetZhao/Qwen3-TTS-GGUF-Engine + llama.cpp-DLLs (MIT) → bin/tts/qwen3/
  · qwen3-tts-0.6b-base.zip     Qwen3-TTS-12Hz-0.6B-Base, exportiert nach GGUF/ONNX (Apache-2.0) → bin/tts/qwen3/model-base-small/
- Whisper-Modelle direkt von Hugging Face (Prüfsumme fest hier eingetragen).
Qwen3 bekommt danach ein eigenes venv (requirements-qwen.txt, ohne PyTorch) – Pakete von PyPI.

Läuft im Hintergrund (Thread); Fortschritt über status(). Nur Windows x64 (die Pakete sind Windows-Builds).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import logbus
from .config import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
MODEL_DIR = DATA_DIR / "models"
RELEASE_TAG = "v1.0.0"
RELEASE = os.environ.get("C4BRIDGE_PACKAGES_URL",
                         f"https://github.com/blublub/c4bridge/releases/download/{RELEASE_TAG}/").rstrip("/") + "/"
HF = "https://huggingface.co/{repo}/resolve/main/{file}?download=true"

WHISPER_MODELS = {   # Variante → (Repo, Datei, Zielordner unter data/models, SHA256, Größe)
    "multi": ("ggerganov/whisper.cpp", "ggml-large-v3-turbo-q8_0.bin", "whisper-turbo-ggml",
              "317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1", 874188075),
    "de": ("Pomni/whisper-large-v3-turbo-german-ggml-allquants", "ggml-large-v3-turbo-german-q8_0.bin",
           "whisper-turbo-german-ggml", "8583eb7d71c59cc2d7bd7ca77fccd6108c795f3b072d0bbf50eef2e1e8204020", 874188075),
}
STT_ENGINE = {"multi": "whisper-vulkan-multi", "de": "whisper-vulkan"}     # passende Einstellung in voice.json

_state: dict = {}
_lock = threading.Lock()


def supported() -> bool:
    return os.name == "nt" and sys.maxsize > 2 ** 32


def whisper_installed(variant: str | None = None) -> dict:
    exe = (BIN / "whisper-vulkan" / "whisper-server.exe").is_file()
    models = {v: (MODEL_DIR / m[2] / m[1]).is_file() for v, m in WHISPER_MODELS.items()}
    return {"exe": exe, "models": models, "ok": exe and (models.get(variant) if variant else any(models.values()))}


def qwen_installed() -> dict:
    q = BIN / "tts" / "qwen3"
    parts = {"engine": (q / "qwen3_tts_gguf" / "inference" / "llama.py").is_file(),
             "model": (q / "model-base-small" / "qwen3_tts_talker.q5_k.gguf").is_file(),
             "venv": (q / ".venv" / "Scripts" / "python.exe").is_file()}
    return {**parts, "ok": all(parts.values())}


def status() -> dict:
    with _lock:
        st = {k: dict(v) for k, v in _state.items()}
    return {"supported": supported(), "release": RELEASE,
            "whisper": {**whisper_installed(), **st.get("whisper", {})},
            "qwen": {**qwen_installed(), **st.get("qwen", {})}}


def _set(name: str, **kw) -> None:
    with _lock:
        _state.setdefault(name, {}).update(kw)


def _download(url: str, dest: Path, sha256: str | None, name: str, label: str, size: int = 0) -> None:
    """In Teilen auf die Platte (Fortschritt + SHA256 nebenbei); erst nach erfolgreicher Prüfung umbenennen."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "c4bridge"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:   # folgt GitHub-/HF-Weiterleitungen
        total = int(r.headers.get("Content-Length") or size or 0)
        done, last = 0, 0.0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if time.time() - last > 0.5:
                last = time.time()
                _set(name, step=label, done_mb=done // 1048576, total_mb=total // 1048576,
                     percent=round(100 * done / total) if total else None)
    if sha256 and h.hexdigest() != sha256.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Prüfsumme falsch für {dest.name} – nicht installiert.")
    os.replace(tmp, dest)


def _sums() -> dict:
    req = urllib.request.Request(RELEASE + "SHA256SUMS.txt", headers={"User-Agent": "c4bridge"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode("utf-8", "replace")
    return {ln.split()[1].lstrip("*"): ln.split()[0].lower() for ln in txt.splitlines() if len(ln.split()) == 2}


def _unzip(zf: Path, target: Path) -> None:
    """Entpacken ohne Pfade aus dem Archiv nach außen (kein „..“, keine absoluten Pfade)."""
    target.mkdir(parents=True, exist_ok=True)
    base = target.resolve()
    with zipfile.ZipFile(zf) as z:
        for m in z.infolist():
            out = (target / m.filename).resolve()
            if base not in out.parents and out != base:
                raise RuntimeError(f"Ungültiger Pfad im Paket: {m.filename}")
            if m.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)


def _release_part(asset: str, target: Path, name: str, label: str, sums: dict) -> None:
    if asset not in sums:
        raise RuntimeError(f"Keine Prüfsumme für {asset} in der Release {RELEASE_TAG}.")
    dl = DATA_DIR / "downloads" / asset
    _download(RELEASE + asset, dl, sums[asset], name, label)
    _set(name, step=f"{label} – entpacken", percent=None)
    _unzip(dl, target)
    dl.unlink(missing_ok=True)


def install_whisper(variant: str) -> None:
    m = WHISPER_MODELS[variant]
    if not (BIN / "whisper-vulkan" / "whisper-server.exe").is_file():
        _release_part("whisper-vulkan-win-x64.zip", BIN / "whisper-vulkan", "whisper", "Whisper (Vulkan)", _sums())
    target = MODEL_DIR / m[2] / m[1]
    if not target.is_file():
        _download(HF.format(repo=m[0], file=m[1]), target, m[3], "whisper", f"Modell {m[1]}", m[4])


def install_qwen() -> None:
    q = BIN / "tts" / "qwen3"
    st = qwen_installed()
    if not (st["engine"] and st["model"]):
        sums = _sums()
        if not st["engine"]:
            _release_part("qwen3-engine-win-x64.zip", q, "qwen", "Qwen3-TTS (Engine)", sums)
        if not st["model"]:
            _release_part("qwen3-tts-0.6b-base.zip", q / "model-base-small", "qwen", "Qwen3-TTS 0.6B (Modell)", sums)
    py = q / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        _set("qwen", step="Python-Umgebung anlegen", percent=None)
        subprocess.run([sys.executable, "-m", "venv", str(q / ".venv")], check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _set("qwen", step="Python-Pakete installieren (~340 MB)", percent=None)
    r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                        str(ROOT / "requirements-qwen.txt")], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode:
        raise RuntimeError("pip: " + (r.stderr or r.stdout).strip()[-300:])


def start(name: str, variant: str = "multi", on_done=None) -> None:
    """Download im Hintergrund starten; on_done(name, variant) nach Erfolg (z. B. Einstellungen umstellen)."""
    if not supported():
        raise RuntimeError("Die GPU-Pakete gibt es nur für Windows x64.")
    if name not in ("whisper", "qwen") or (name == "whisper" and variant not in WHISPER_MODELS):
        raise ValueError("unbekanntes Paket")
    with _lock:
        if _state.get(name, {}).get("running"):
            raise RuntimeError("Download läuft bereits")
        _state[name] = {"running": True, "step": "Start", "error": "", "percent": None}

    def run():
        try:
            install_whisper(variant) if name == "whisper" else install_qwen()
            _set(name, running=False, step="fertig", percent=100)
            logbus.info("voice", f"GPU-Paket {name} installiert")
            if on_done:
                on_done(name, variant)
        except Exception as e:  # noqa: BLE001
            _set(name, running=False, error=f"{type(e).__name__}: {e}")
            logbus.warn("voice", f"GPU-Paket {name}: {type(e).__name__}: {e}")
    threading.Thread(target=run, daemon=True, name=f"pkg-{name}").start()
