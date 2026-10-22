"""Web-Login der Bridge: Benutzer, Rollen, Sitzungs-Cookies, Service-Token.

- Benutzer liegen in data/users.json (PBKDF2-gehasht). Rollen: "admin" (alles,
  inkl. Admin-Oberfläche) und "user" (Bedienen + eigene Favoriten/Einstellungen).
  Konten ohne Rolle stammen aus der Zeit vor den Rollen und gelten als admin.
- Anmeldung über /login → signiertes Cookie (HMAC, Schlüssel in data/secret.key).
  Gültig 1 Jahr und gleitend: wer die Oberfläche benutzt, bekommt einmal am Tag ein frisches
  Cookie – ein Wandtablet bleibt also dauerhaft angemeldet. Ein Passwortwechsel oder
  „Alle Geräte abmelden" (Zähler `sg` am Konto) macht alte Sitzungen des Kontos ungültig.
- Skripte dürfen weiterhin HTTP-Basic schicken.
- c4voice spricht die Bridge mit einem Service-Token an, der bei jedem
  Bridge-Start neu erzeugt und dem Kindprozess per Umgebung übergeben wird.
- Ohne Benutzer und ohne Config-Login ist die Oberfläche offen (wie bisher).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

USERS_FILE = DATA_DIR / "users.json"
PREFS_FILE = DATA_DIR / "userprefs.json"
SECRET_FILE = DATA_DIR / "secret.key"
COOKIE = "c4s"
SESSION_S = 365 * 24 * 3600     # Cookie-Lebensdauer (gleitend verlängert)
RENEW_S = 24 * 3600             # frühestens so oft ein neues Cookie ausstellen
ROLES = ("admin", "user")
PBKDF2_ITERS = 200_000
LEGACY_ITERS = 60_000          # Konten von vor 2026-10-16

SERVICE_HEADER = "x-c4-service"
SERVICE_TOKEN = secrets.token_urlsafe(32)   # pro Bridge-Start neu → c4voice per Umgebung

_lock = threading.Lock()


def atomic_write(path: Path, text: str) -> None:
    """Schreiben über Temp-Datei + Umbenennen: parallele Leser sehen nie eine halbe Datei."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Principal:
    user: str
    role: str        # admin | user | service
    kind: str        # session | basic | service | open | legacy
    renew: bool = False   # Sitzungs-Cookie ist älter als RENEW_S → neu ausstellen

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def prefs_key(self) -> str:
        return "_open" if self.kind == "open" else self.user


# ---- Benutzer ------------------------------------------------------------------
def load_users() -> list:
    try:
        users = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    for u in users:
        u.setdefault("role", "admin")
    return users


def save_users(users: list) -> None:
    with _lock:
        atomic_write(USERS_FILE, json.dumps(users, ensure_ascii=False, indent=1))


def pw_hash(password: str, salt: str, iters: int = PBKDF2_ITERS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iters).hex()


def new_credentials(password: str) -> dict:
    salt = secrets.token_hex(12)
    return {"salt": salt, "hash": pw_hash(password, salt), "it": PBKDF2_ITERS}


def check_user(name: str, password: str) -> Optional[dict]:
    for u in load_users():
        if u["user"] == name:
            h = pw_hash(password, u["salt"], int(u.get("it", LEGACY_ITERS)))
            return u if hmac.compare_digest(h, u["hash"]) else None
    return None


def valid_name(name: str) -> bool:
    return bool(re.fullmatch(r"[\w.@-]{2,40}", name or ""))


# ---- Sitzungen -----------------------------------------------------------------
def _secret() -> bytes:
    with _lock:
        if SECRET_FILE.is_file():
            return SECRET_FILE.read_bytes()
        key = secrets.token_bytes(32)
        SECRET_FILE.write_bytes(key)
        return key


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pwtag(user: dict) -> str:
    """Bindet die Sitzung an Passwort + Abmelde-Zähler: ändert sich eins, ist das Cookie wertlos."""
    tag = user.get("hash", "")[:12]
    return tag + (f".{user['sg']}" if user.get("sg") else "")


def make_session(user: dict) -> str:
    payload = json.dumps({"u": user["user"], "e": int(time.time()) + SESSION_S,
                          "p": _pwtag(user)}, separators=(",", ":")).encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return _b64(payload) + "." + _b64(sig)


def user_record(name: str) -> Optional[dict]:
    return next((u for u in load_users() if u["user"] == name), None)


def make_legacy_session(name: str, password: str) -> str:
    """Sitzung für den einzelnen Config-Benutzer (BRIDGE_WEB_USER)."""
    return make_session({"user": name, "hash": hashlib.sha256(password.encode()).hexdigest()})


def read_session(token: str, cfg) -> Optional[Principal]:
    try:
        p64, s64 = token.split(".", 1)
        payload = _unb64(p64)
        if not hmac.compare_digest(hmac.new(_secret(), payload, hashlib.sha256).digest(), _unb64(s64)):
            return None
        data = json.loads(payload)
    except Exception:  # noqa: BLE001
        return None
    if data.get("e", 0) < time.time():
        return None
    renew = data["e"] - time.time() < SESSION_S - RENEW_S
    users = load_users()
    for u in users:
        if u["user"] == data.get("u"):
            return Principal(u["user"], u["role"], "session", renew) if _pwtag(u) == data.get("p") else None
    if not users and cfg.web_user and data.get("u") == cfg.web_user:
        tag = hashlib.sha256(cfg.web_password.encode()).hexdigest()[:12]
        return Principal(cfg.web_user, "admin", "legacy") if tag == data.get("p") else None
    return None


# ---- Auflösen eines Requests ---------------------------------------------------------
def open_mode(cfg) -> bool:
    return not load_users() and not cfg.web_user


def check_login(name: str, password: str, cfg) -> Optional[Principal]:
    users = load_users()
    if users:
        u = check_user(name, password)
        return Principal(u["user"], u["role"], "session") if u else None
    if cfg.web_user and hmac.compare_digest(name, cfg.web_user) \
            and hmac.compare_digest(password, cfg.web_password):
        return Principal(cfg.web_user, "admin", "legacy")
    return None


def resolve(conn, cfg) -> Optional[Principal]:
    """conn = Request oder WebSocket (Starlette HTTPConnection)."""
    svc = conn.headers.get(SERVICE_HEADER)
    if svc and hmac.compare_digest(svc, SERVICE_TOKEN):
        return Principal("c4voice", "service", "service")
    if open_mode(cfg):
        return Principal("admin", "admin", "open")
    tok = conn.cookies.get(COOKIE)
    if tok:
        p = read_session(tok, cfg)
        if p:
            return p
    h = conn.headers.get("authorization", "")
    if h.lower().startswith("basic "):
        try:
            name, _, pw = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:  # noqa: BLE001
            return None
        p = check_login(name, pw, cfg)
        if p:
            return Principal(p.user, p.role, "basic")
    return None


# Seiten/Endpunkte ohne Login (Login-Seite selbst + deren Dateien)
PUBLIC = re.compile(r"^/(login|api/login|api/logout|api/me|vendor/.+|ui/.+|favicon\.ico)$")

# Nur für Admins: Konfiguration, Benutzer, Diagnose, Projektpflege
ADMIN_ONLY = [
    (None, re.compile(r"^/admin$")),
    (None, re.compile(r"^/api/(users|config|setup|scan|refresh|ws|log|tree|drivers|director|events$|ai/)")),
    (None, re.compile(r"^/api/cameras/(config|manual)")),
    ("POST", re.compile(r"^/api/cameras/\d+/test$")),
    (None, re.compile(r"^/api/voice/(config|wake|start|stop|restart|dryrun|tts)")),
    ("PUT", re.compile(r"^/api/(devices|rooms)/\d+$")),
    (None, re.compile(r"^/api/devices/\d+/inspect$")),
    (None, re.compile(r"^/api/(rooms/\d+/push|state/(status|rooms))$")),
    (None, re.compile(r"^/api/assist/geocode")),
    (None, re.compile(r"^/api/bluos/(players|refresh)$")),
    ("POST", re.compile(r"^/api/kodi/refresh$")),
    (None, re.compile(r"^/api/learn/")),
]


def is_public(path: str) -> bool:
    return bool(PUBLIC.match(path))


def needs_admin(method: str, path: str) -> bool:
    return any((m is None or m == method) and rx.match(path) for m, rx in ADMIN_ONLY)


# ---- Login-Bremse (pro IP) -------------------------------------------------------------
_fails: dict = {}


def login_blocked(ip: str) -> int:
    """Sekunden bis zur nächsten erlaubten Anmeldung (0 = sofort)."""
    n, until = _fails.get(ip, (0, 0.0))
    return max(0, int(until - time.time()))


def login_failed(ip: str) -> None:
    n, _ = _fails.get(ip, (0, 0.0))
    n += 1
    _fails[ip] = (n, time.time() + (300 if n >= 8 else 0))


def login_ok(ip: str) -> None:
    _fails.pop(ip, None)


# ---- Persönliche Einstellungen -----------------------------------------------------------
PREF_KEYS = {"theme": ("x4", "os3"), "lang": ("de", "en")}


def load_prefs(key: str) -> dict:
    try:
        return json.loads(PREFS_FILE.read_text(encoding="utf-8")).get(key, {})
    except Exception:  # noqa: BLE001
        return {}


def save_prefs(key: str, upd: dict) -> dict:
    with _lock:
        try:
            allp = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            allp = {}
        cur = allp.get(key, {})
        for k, v in upd.items():
            if k in PREF_KEYS and v in PREF_KEYS[k]:
                cur[k] = v
            elif k == "room" and isinstance(v, int):
                cur[k] = v
            elif k == "fav_rooms" and isinstance(v, list):     # Lieblingsräume (Stern), Reihenfolge = Anzeige
                cur[k] = [x for x in v if isinstance(x, int)][:50]
        allp[key] = cur
        atomic_write(PREFS_FILE, json.dumps(allp, ensure_ascii=False, indent=1))
        return cur
