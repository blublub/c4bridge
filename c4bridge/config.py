"""Configuration: environment variables, optionally a KEY=VALUE file.

The file is looked up at $C4BRIDGE_CONFIG or ~/.config/c4bridge/config.env
(keep it outside the project, readable only by the service user: chmod 600).
No secrets live in the code or in the repository.
"""
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config" / "c4bridge" / "config.env"
DATA_DIR = Path(os.environ.get("C4BRIDGE_DATA", Path(__file__).resolve().parent.parent / "data"))

# keys editable via the admin UI (env name -> Config field)
EDITABLE_KEYS = {
    "C4_USER": "c4_user",
    "C4_PASS": "c4_password",
    "C4_IP": "c4_ip",
    "BRIDGE_WEB_USER": "web_user",
    "BRIDGE_WEB_PASS": "web_password",
    "BRIDGE_HOST": "host",
    "BRIDGE_PORT": "port",
    "C4_SCAN_MIN": "scan_min",
    "EXA_API_KEY": "exa_key",       # Websuche für Sprachfragen (optional, exa.ai)
}


def config_path() -> Path:
    return Path(os.environ.get("C4BRIDGE_CONFIG", DEFAULT_CONFIG))


def save(updates: dict) -> Path:
    """Write changed env keys back to the config file, preserving comments
    and unknown keys. Empty values are ignored, so a password field left
    blank in the UI never clears the stored password."""
    path = config_path()
    field_to_key = {f: k for k, f in EDITABLE_KEYS.items()}
    wanted = {field_to_key[f]: v for f, v in updates.items()
              if f in field_to_key and v not in (None, "")}
    lines = path.read_text().splitlines() if path.is_file() else []
    out, seen = [], set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in wanted:
                out.append(f"{key}={wanted[key]}")
                seen.add(key)
                continue
        out.append(line)
    out.extend(f"{key}={value}" for key, value in wanted.items() if key not in seen)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
    return path


def _read_env_file(path: Path) -> dict:
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip("'\"")
    return values


@dataclass
class Config:
    c4_user: str
    c4_password: str
    c4_ip: str
    web_user: str
    web_password: str
    host: str
    port: int
    scan_min: int  # Controller-Scan (neue Geräte/Treiber/Räume) alle N Minuten, 0 = aus
    exa_key: str = ""
    ssl_port: int = 0        # zusätzlicher HTTPS-Port (0 = aus) – Browser-Mikrofon im LAN braucht https (tlscert.py)
    ssl_cert: str = ""       # eigenes Zertifikat/Schlüssel (PEM); leer = selbstsigniert in ~/.config/c4bridge
    ssl_key: str = ""

    @property
    def c4_configured(self) -> bool:
        return bool(self.c4_user and self.c4_password and self.c4_ip)


def load() -> Config:
    path = Path(os.environ.get("C4BRIDGE_CONFIG", DEFAULT_CONFIG))
    file_values = _read_env_file(path)

    def get(key, default=""):
        return os.environ.get(key) or file_values.get(key) or default

    return Config(
        c4_user=get("C4_USER"),
        c4_password=get("C4_PASS"),
        c4_ip=get("C4_IP"),
        web_user=get("BRIDGE_WEB_USER"),
        web_password=get("BRIDGE_WEB_PASS"),
        host=get("BRIDGE_HOST", "127.0.0.1"),
        port=int(get("BRIDGE_PORT", "8080")),
        scan_min=int(get("C4_SCAN_MIN", "1440")),
        exa_key=get("EXA_API_KEY"),
        ssl_port=int(get("BRIDGE_SSL_PORT", "0") or 0),
        ssl_cert=get("BRIDGE_SSL_CERT"),
        ssl_key=get("BRIDGE_SSL_KEY"),
    )
