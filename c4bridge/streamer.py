"""Bundled MediaMTX livestream server.

Bridges RTSP cameras to browser-friendly HLS/WebRTC. The bridge generates the
MediaMTX config from data/cameras.json (mode=rtsp) and manages the process
lifecycle. Credentials live only in the local config file, never in the browser.
"""
import hashlib
import io
import os
import platform
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse, quote

from . import logbus
from .config import DATA_DIR

TOOLS = Path(__file__).resolve().parent.parent / "tools"
# Windows: mediamtx.exe, Linux/macOS: mediamtx (ohne Endung)
EXE = TOOLS / ("mediamtx.exe" if os.name == "nt" else "mediamtx")
# Feste Version (Prüfsumme kommt aus derselben Release); neue Version erst nach Test hier eintragen
MEDIAMTX_VERSION = "v1.21.2"
_RELEASE = f"https://github.com/bluenviron/mediamtx/releases/download/{MEDIAMTX_VERSION}/"
# Python-Namen → MediaMTX-Paketnamen (Release-Dateien mediamtx_<ver>_<os>_<arch>.zip|tar.gz)
_ARCH = {"amd64": "amd64", "x86_64": "amd64", "x64": "amd64", "aarch64": "arm64", "arm64": "arm64",
         "armv8l": "arm64", "armv7l": "armv7", "armv7": "armv7", "armv6l": "armv6"}
_OFFERED = {("windows", "amd64"), ("linux", "amd64"), ("linux", "arm64"), ("linux", "armv7"), ("linux", "armv6"),
            ("darwin", "amd64"), ("darwin", "arm64")}
_OS_LABEL = {"windows": "Windows", "linux": "Linux", "darwin": "macOS"}
CFG = DATA_DIR / "mediamtx.yml"

RTSP_PORT = 8554
HLS_PORT = 8888


def _cam_cfg() -> dict:
    import json
    try:
        return json.loads((DATA_DIR / "cameras.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def write_config() -> list:
    """(Re)generate mediamtx.yml from cameras.json; returns active path names."""
    cams = _cam_cfg()
    lines = [
        f"rtspAddress: :{RTSP_PORT}",
        "hls: yes",
        f"hlsAddress: :{HLS_PORT}",
        "hlsAllowOrigin: '*'",
        "hlsVariant: mpegts",       # klassisch: funktioniert ohne Secure-Cookie auch plain HTTP
        "hlsAlwaysRemux: yes",       # Stream sofort bereit, ohne Anfrage-Verzögerung
        "webrtc: yes",
        "webrtcAddress: :8889",
        "webrtcAdditionalHosts: [localhost]",
        "api: no",
        "metrics: no",
        "playback: no",
        "srt: no",
        "rtmp: no",
        "paths:",
    ]
    active = []

    def _add(name, e):
        url = (e or {}).get("rtsp_url", "")
        if (e or {}).get("mode") != "rtsp" or not url:
            return
        u = urlparse(url if "//" in url else f"rtsp://{url}")
        user, pw = e.get("rtsp_user", ""), e.get("rtsp_pass", "")
        if user:
            u = u._replace(netloc=f"{quote(user, safe='')}:{quote(pw, safe='')}@{u.hostname}:{u.port or 554}")
        lines.extend([f"  {name}:", f"    source: {u.geturl()}", "    sourceOnDemand: yes"])
        active.append(name)

    for cam_id, e in cams.items():          # Director-Kameras + manuelle (unter _manual)
        if cam_id == "_manual":
            for mid, me in e.items():
                _add(f"cam_{mid}", me)
        else:
            _add(f"cam_{cam_id}", e)
    CFG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return active


class Streamer:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.paths: list = []

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        if not EXE.is_file():
            logbus.warn("streamer", f"tools/{EXE.name} fehlt - Livestreams deaktiviert (Admin/Assistent: herunterladen)")
            return
        self.paths = write_config()
        if not self.paths:
            logbus.info("streamer", "keine Kamera im RTSP-Modus - MediaMTX nicht gestartet")
            return
        self.proc = subprocess.Popen(
            [str(EXE), str(CFG)], cwd=str(TOOLS),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        if self.running:
            logbus.info("streamer", f"MediaMTX gestartet (PID {self.proc.pid}); Pfade: {self.paths}")
        else:
            logbus.error("streamer", "MediaMTX startete nicht")
            self.proc = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            logbus.info("streamer", "MediaMTX gestoppt")
            self.proc = None

    def urls(self, cam_id: int, host: str = "127.0.0.1") -> dict:
        """Browser endpoints for one camera's livestream (same-origin via bridge proxy).
        WHEP (WebRTC) ist der primäre Weg - stateless ohne Session-Cookies."""
        name = f"cam_{cam_id}"
        if name not in self.paths:
            return {}
        return {"whep": f"/hls/{cam_id}/whep"}


# ---- MediaMTX passend zum Betriebssystem besorgen (Installationsassistent / Admin) --------------------------
def target() -> dict:
    """Welches MediaMTX-Paket passt zu diesem Rechner?"""
    osn = platform.system().lower()            # windows | linux | darwin
    arch = _ARCH.get(platform.machine().lower(), platform.machine().lower())
    ok = (osn, arch) in _OFFERED
    ext = "zip" if osn == "windows" else "tar.gz"
    return {"os": osn, "arch": arch, "label": f"{_OS_LABEL.get(osn, platform.system())} ({arch})", "supported": ok,
            "asset": f"mediamtx_{MEDIAMTX_VERSION}_{osn}_{arch}.{ext}" if ok else "", "version": MEDIAMTX_VERSION}


def installed_version() -> str:
    if not EXE.is_file():
        return ""
    try:
        r = subprocess.run([str(EXE), "--version"], capture_output=True, text=True, timeout=10, cwd=str(TOOLS))
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else "?"
    except Exception:  # noqa: BLE001
        return "?"


def status() -> dict:
    return {**target(), "installed": EXE.is_file(), "installed_version": installed_version(),
            "exe": f"tools/{EXE.name}"}


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "c4bridge"})
    with urllib.request.urlopen(req, timeout=timeout) as r:     # folgt der GitHub-Weiterleitung
        return r.read()


def _extract(data: bytes, asset: str) -> bytes:
    """Nur die Programmdatei aus dem Paket lesen (nichts entpacken, keine Pfade aus dem Archiv übernehmen)."""
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return z.read(next(n for n in z.namelist() if n.rsplit("/", 1)[-1] == "mediamtx.exe"))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        m = next(m for m in t.getmembers() if m.isfile() and m.name.rsplit("/", 1)[-1] == "mediamtx")
        return t.extractfile(m).read()


def install() -> dict:
    """Paket der festen Version laden, SHA256 gegen checksums.sha256 derselben Release prüfen, Programm nach
    tools/ legen (unter Linux/macOS ausführbar machen). Ein laufendes MediaMTX vorher stoppen (Aufrufer)."""
    t = target()
    if not t["supported"]:
        raise RuntimeError(f"Für {t['label']} gibt es kein MediaMTX-Paket.")
    sums = _get(_RELEASE + "checksums.sha256", 30).decode("utf-8", "replace")
    want = next((ln.split()[0].lower() for ln in sums.splitlines()
                 if len(ln.split()) == 2 and ln.split()[1].lstrip("*") == t["asset"]), None)
    if not want:
        raise RuntimeError(f"Keine Prüfsumme für {t['asset']}.")
    data = _get(_RELEASE + t["asset"], 300)
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise RuntimeError(f"Prüfsumme falsch für {t['asset']} – nicht installiert.")
    exe = _extract(data, t["asset"])
    TOOLS.mkdir(parents=True, exist_ok=True)
    tmp = EXE.with_name(EXE.name + ".neu")
    tmp.write_bytes(exe)
    if os.name != "nt":
        tmp.chmod(0o755)
    os.replace(tmp, EXE)
    logbus.info("streamer", f"MediaMTX {MEDIAMTX_VERSION} für {t['label']} installiert ({len(exe) // 1048576} MB, SHA256 ok)")
    return status()
