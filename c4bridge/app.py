"""c4bridge web service: REST API + small web UI + live log."""
import asyncio
import json
import os
import re
import sys

import aiohttp
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import HTTPConnection

from . import assist as assist_mod
from . import auth as auth_mod
from . import config as config_mod
from . import logbus
from .connection import Connection
from .inventory import Inventory
from .bluos import BluOS
from . import bluos as bluos_mod
from .kodi import Kodi, KodiError
from .media import MediaIndex
from .streamer import HLS_PORT, Streamer
from .ws import WsManager
from . import state as state_mod
from .state import StateCache

STATIC = Path(__file__).parent / "static"
INVENTORY_REFRESH_S = 30 * 60
MEDIA_REFRESH_S = 6 * 60 * 60

cfg = config_mod.load()
conn = Connection(cfg)
inv = Inventory()
media = MediaIndex()
bluos = BluOS()
BLUOS_REFRESH_S = 30 * 60
kodi = Kodi()
KODI_REFRESH_S = 30 * 60
wsman = WsManager(conn, inv)
# Push-first: Director-Abfragen der Oberfläche laufen über diesen Zwischenspeicher (state.py)
cache = StateCache(inv, lambda: wsman.push_ok)
wsman.on_message = cache.on_message
wsman.on_reconnect = cache.on_reconnect


def _kodi_changed(did: str) -> None:
    """KODI meldet (WebSocket bzw. Control4-Treiber) eine neue Wiedergabe → betroffene Räume an die Oberfläche."""
    p = kodi.players.get(did) or {}
    rooms_ = set(kodi.rooms_seen.get(did, set())) | set(p.get("auto_rooms") or [])
    for rid in rooms_:
        if rid in inv.rooms:
            cache.dirty_room(rid, settle=False)


kodi.on_change = _kodi_changed
cache.prop_hooks = kodi.on_property      # UPDATE_PROPERTY der KODI-Haupttreiber (Player Status, Currently Playing)
cache.prop_devs = kodi.driver_ids


async def _item_info(item_id: int) -> dict:
    """Raum/Name/Proxy eines Items (für die Push-Zuordnung unbekannter Geräte und die Admin-Ansicht)."""
    d = await conn.get(f"/api/v1/items/{item_id}")
    d = d[0] if isinstance(d, list) and d else d
    if not isinstance(d, dict):
        return {}
    return {"roomId": d.get("roomId"), "name": d.get("name"), "proxy": d.get("proxy") or d.get("typeName")}
cache.lookup = _item_info
cache.drivers = wsman.drivers
_room_on: dict = {}   # Raum → an (Quelle gewählt), aus dem letzten Lesen


def _room_names() -> dict:
    return {rid: r.get("name", "") for rid, r in inv.rooms.items()}
streamer = Streamer()


def auth(request: HTTPConnection):
    """Globale Prüfung für jeden HTTP-Request (WebSockets prüfen selbst):
    öffentliche Pfade frei, sonst Sitzung/Basic/Service-Token, Admin-Pfade nur für Admins."""
    if request.scope.get("type") == "websocket":
        return
    path = request.url.path
    if auth_mod.is_public(path):
        return
    p = auth_mod.resolve(request, cfg)
    if p is None:
        raise HTTPException(401, "Anmeldung erforderlich")
    if auth_mod.needs_admin(request.scope.get("method", "GET"), path) and not p.is_admin:
        raise HTTPException(403, "Nur für Administratoren")
    request.state.principal = p
    if p.renew:   # gleitende Sitzung: wer die Oberfläche nutzt, bleibt angemeldet
        u = auth_mod.user_record(p.user)
        if u:
            request.state.renew_cookie = auth_mod.make_session(u)


def _set_session(r: Response, token: str) -> None:
    r.set_cookie(auth_mod.COOKIE, token, max_age=auth_mod.SESSION_S, httponly=True, samesite="lax")


def me(request: Request) -> auth_mod.Principal:
    return getattr(request.state, "principal", None) or auth_mod.resolve(request, cfg) \
        or auth_mod.Principal("?", "user", "none")



async def _refresh_inventory():
    await inv.refresh(conn)
    _apply_meta()
    cache.invalidate("learn_items")
    await _apply_learned()
    asyncio.create_task(_learn_endpoints())
    if wsman.c4ws:  # new/renamed items get their callback after an import
        try:
            await wsman.resubscribe()
        except Exception as e:  # noqa: BLE001
            logbus.warn("ws", f"resubscribe after inventory refresh failed: {e}")


def _apply_meta() -> None:
    """Systemgeräte aus dem Inventar (nach Proxy erkannt) an den Zwischenspeicher geben."""
    ua = inv.meta.get("ui_agent")
    if ua and cache.ui_agent != ua:
        cache.ui_agent = ua
        cache._save_ep()
    cache.agents = {v: k for k, v in inv.meta.items() if k in ("adv_lighting", "custom_buttons")}


def _digital_media_id() -> int:
    """Control4 „Digital Media" (Proxy control4_digitalaudio) – Warteschlangen aller Räume."""
    return int(inv.meta.get("digital_media") or 100002)


_apply_meta()


async def _learn_endpoints():
    """Endpunkte aller Räume laut Composer-Bindungen (auch ausgeschaltete Räume) – Grundlage der Push-Zuordnung."""
    n = 0
    for rid in list(inv.rooms):
        try:
            cache.learn_static(rid, await conn.get(f"/api/v1/items/{rid}/bindings"))
            n += 1
        except Exception as e:  # noqa: BLE001
            logbus.warn("state", f"Bindungen Raum {rid}: {type(e).__name__}: {e}")
        await asyncio.sleep(0.2)                     # Director schonen
    logbus.info("state", f"Endpunkte laut Composer für {n} Räume gelesen")


async def _refresh_media():
    await media.refresh(conn, [r["id"] for r in inv.list_rooms() if r["enabled"]])


async def _background():
    """Connect, import, then keep the caches fresh."""
    last_inv = last_media = last_bluos = last_kodi = 0.0
    while True:
        try:
            if cfg.c4_configured:
                await conn.ensure()  # renews the token before it expires
                now = time.time()
                scan_s = max(0, cfg.scan_min) * 60
                if scan_s and now - last_inv > scan_s:
                    await _refresh_inventory()
                    last_inv = now
                if now - last_media > MEDIA_REFRESH_S and inv.rooms:
                    asyncio.create_task(_refresh_media())
                    last_media = now
                if now - last_bluos > BLUOS_REFRESH_S:
                    asyncio.create_task(bluos.refresh(conn, _room_names()))
                    last_bluos = now
                if now - last_kodi > KODI_REFRESH_S and inv.rooms:
                    asyncio.create_task(kodi.refresh(conn))
                    last_kodi = now
        except Exception as e:  # noqa: BLE001
            logbus.error("background", f"{type(e).__name__}: {e}")
            await asyncio.sleep(30)
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logbus.info("app", f"c4bridge starting; Control4 configured: {cfg.c4_configured}")
    task = asyncio.create_task(_background())
    ws_task = wsman.start()
    await asyncio.to_thread(streamer.start)
    voice_task = asyncio.create_task(_voice_watchdog())
    if _voice_cfg().get("enabled"):
        await voice_start()
    tls_task = None
    if cfg.ssl_port:                                # HTTPS als zweiter Port (Browser-Mikrofon im LAN)
        try:
            from . import tlscert
            from pathlib import Path as _P
            cert, key = (_P(cfg.ssl_cert), _P(cfg.ssl_key)) if cfg.ssl_cert and cfg.ssl_key \
                else await asyncio.to_thread(tlscert.ensure)
            tls_task = asyncio.create_task(tlscert.serve(app, cfg.host, cfg.ssl_port, cert, key))
        except Exception as e:  # noqa: BLE001
            logbus.warn("tls", f"HTTPS nicht gestartet: {type(e).__name__}: {e}")
    yield
    task.cancel()
    ws_task.cancel()
    voice_task.cancel()
    if tls_task:
        tls_task.cancel()
    await voice_stop()
    await wsman.stop()
    await asyncio.to_thread(streamer.stop)
    await conn.close()


# ---- c4voice: Sprachdienst als Kindprozess (Start/Stopp/Watchdog über Web) ---------
VOICE_CFG_FILE = config_mod.DATA_DIR / "voice.json"
VOICE_DEFAULTS = {
    "enabled": False, "port": 8090,
    "wake": {"enabled": True, "model": "hey_jarvis", "threshold": 0.5},
    "stt": {"model": "base", "language": "de"},
    # keep_alive_min: Modell so lange im Speicher halten (-1 = immer, 0 = sofort entladen); num_ctx 0 = Ollama-Standard
    "llm": {"url": "http://localhost:11434", "model": "qwen3.5:9b", "timeout_s": 90, "keep_alive_min": 60, "num_ctx": 0},
    "bridge_url": "http://127.0.0.1:8080",
    "dry_run": True,
    "tts": {"enabled": False, "voice": "de_DE-thorsten-medium", "speed": 1.0, "volume": 1.0,
            "mode": "full", "followup": True},
    "mics": {},
    # relative Helligkeits-Stufen — identisch zu voice.py DEFAULT_CFG.adjust
    "adjust": {"klein": {"up": 33, "down": 30}, "mittel": {"up": 50, "down": 50},
               "gross": {"up": 100, "down": 60}, "cozy_below": 30, "cozy_down": 25,
               "min_level": 5},
    "music": {"steps": {"klein": 5, "mittel": 10, "gross": 20}, "max_volume": 60},
    "vad": {"start": 250, "keep": 200, "noise_x": 2.4},   # Sprach-Erkennung: Pegelschwellen (voice.py DEFAULT_CFG.vad)
    "duck": {"enabled": True, "factor": 0.3, "min_level": 8, "max_s": 25},   # Musik beim Wake-Word leiser
    # Wissen: Wetter (Open-Meteo, ohne Schlüssel) + Websuche (SearXNG-URL hier, Exa-Schlüssel in config.env);
    # answer_llm = optionales zweites Modell, das Suchtreffer zu einer gesprochenen Antwort zusammenfasst
    "assist": {"weather": {"enabled": True, "place": "", "lat": None, "lon": None},
               "web": {"enabled": False, "provider": "searxng", "searxng_url": "", "results": 5},
               "answer_llm": {"url": "", "model": "", "keep_alive_min": 60, "num_ctx": 0}},
    "prompts": {"house": "", "answer": ""},       # leer = eingebaute Anweisungen
}
_voice = {"proc": None, "reader": None, "restarts": 0, "last_error": "",
          "lines": [], "started": 0.0, "manual_stop": False, "next_try": 0.0}


def _voice_deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _voice_deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _voice_cfg() -> dict:
    try:
        file = json.loads(VOICE_CFG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        file = {}
    return _voice_deep_merge(VOICE_DEFAULTS, file)


def _voice_cfg_save(cfg: dict) -> None:
    # atomar: c4voice liest die Datei bei jedem Befehl und darf nie eine halbe Datei sehen
    auth_mod.atomic_write(VOICE_CFG_FILE, json.dumps(cfg, ensure_ascii=False, indent=1))


async def _voice_read(proc) -> None:
    """stdout von c4voice mitlesen (JSON-Zeilen) → Puffer für die Weboberfläche."""
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = line.decode("utf-8", "replace").strip()
        if text:
            _voice["lines"].append(text)
            del _voice["lines"][:-120]


async def voice_start() -> bool:
    if _voice["proc"] and _voice["proc"].returncode is None:
        return False
    cfgv = _voice_cfg()
    try:
        _voice["proc"] = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "c4bridge.voice",
            cwd=str(Path(__file__).resolve().parent.parent),
            # c4voice lauscht nur auf localhost und akzeptiert nur diesen Token (nur die Bridge)
            env={**os.environ, "PYTHONUNBUFFERED": "1", "C4VOICE_HOST": "127.0.0.1",
                 "C4VOICE_TOKEN": auth_mod.SERVICE_TOKEN},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        _voice["reader"] = asyncio.create_task(_voice_read(_voice["proc"]))
        _voice["started"] = time.time()
        _voice["manual_stop"] = False
        _voice["last_error"] = ""
        logbus.info("voice", f"c4voice started (pid {_voice['proc'].pid})")
        return True
    except Exception as e:  # noqa: BLE001
        _voice["last_error"] = f"{type(e).__name__}: {e}"
        logbus.error("voice", _voice["last_error"])
        return False


async def voice_stop() -> bool:
    _voice["manual_stop"] = True
    proc = _voice["proc"]
    if not proc or proc.returncode is not None:
        return False
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), 5)
    except asyncio.TimeoutError:  # noqa: BLE001
        proc.kill()
    logbus.info("voice", "c4voice stopped")
    return True


async def _voice_watchdog():
    """Hält c4voice am Leben, solange 'enabled' an und kein manueller Stopp aktiv."""
    while True:
        await asyncio.sleep(5)
        try:
            cfgv = _voice_cfg()
            proc = _voice["proc"]
            dead = not proc or proc.returncode is not None
            if cfgv.get("enabled") and not _voice["manual_stop"] and dead:
                if time.time() < _voice["next_try"]:
                    continue
                if proc and proc.returncode is not None and _voice["started"]:
                    _voice["restarts"] += 1
                    logbus.warn("voice", f"c4voice exited (code {proc.returncode}), restart #{_voice['restarts']}")
                ok = await voice_start()
                backoff = min(60, 5 * max(1, _voice["restarts"]))
                _voice["next_try"] = time.time() + (backoff if not ok else 0)
        except Exception as e:  # noqa: BLE001
            logbus.error("voice", f"watchdog: {type(e).__name__}: {e}")


app = FastAPI(title="c4bridge", lifespan=lifespan, dependencies=[Depends(auth)])


@app.exception_handler(StarletteHTTPException)
async def _http_errors(request: Request, exc: StarletteHTTPException):
    """Nicht angemeldet + Seitenaufruf → zur Login-Seite statt Browser-Passwortdialog."""
    path = request.url.path
    if exc.status_code == 401 and not path.startswith(("/api/", "/hls/")):
        from urllib.parse import quote
        return RedirectResponse("/login?next=" + quote(path), status_code=303)
    return await http_exception_handler(request, exc)


@app.middleware("http")
async def _csrf_guard(request: Request, call_next):
    """Schreibende Requests nur von der eigenen Seite (Origin = Host), damit eine fremde
    Webseite im Browser eines angemeldeten Nutzers nichts schalten kann."""
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("origin")
        if origin and origin != "null":
            from urllib.parse import urlparse
            if urlparse(origin).netloc != request.headers.get("host", ""):
                return JSONResponse({"detail": "fremde Herkunft abgelehnt"}, status_code=403)
    resp = await call_next(request)
    if request.method != "GET" and request.url.path.startswith("/api/"):
        _invalidate_for(request.url.path)   # nach eigenen Befehlen frisch lesen (Push kommt zusätzlich)
    tok = getattr(request.state, "renew_cookie", None)
    if tok:
        _set_session(resp, tok)
    return resp


def _invalidate_for(path: str) -> None:
    """Nach einem eigenen Befehl nur das Betroffene als veraltet markieren (ein Lautstärke-Regler schickt alle
    300 ms – früher wurde dabei jedes Mal ALLES neu vom Director gelesen)."""
    m = re.match(r"^/api/rooms/(\d+)/", path)
    if m:
        rid = int(m.group(1))
        cache.invalidate(*cache.room_keys(rid), "power")
        for dev, rooms_ in cache.dev_rooms.items():
            if rid in rooms_:
                cache.invalidate(f"queue:{dev}")
        return
    m = re.match(r"^/api/(devices|blinds|switches)/(\d+)/", path)
    if m:
        d = inv.devices.get(int(m.group(2))) or {}
        key = state_mod.TYPE_KEY.get(d.get("type"))
        if key:
            cache.invalidate_room(key, d.get("room_id"))
            return
    if path.startswith("/api/scenes/"):
        cache.invalidate("scenes", "lights")
        return
    if path.startswith(("/api/bluos/", "/api/media/play")):
        cache.invalidate(*[k for k in list(cache._val) if k.startswith(("room:", "bluos:", "queue:"))], "power")
        return
    if path.startswith("/api/learned/"):
        cache.invalidate("learned")              # angelerntes Gerät geschaltet
        return
    if path.startswith("/api/learn/") and not path.startswith("/api/learn/test"):
        return                                   # Anlern-Assistent: liest nur / schreibt data/learned.json
    if path.startswith(("/api/login", "/api/logout", "/api/me/", "/api/kodi/", "/api/voice/", "/api/users", "/api/ui/",
                        "/api/assist/", "/api/cameras/")):
        return                                   # betrifft keinen Hauszustand
    cache.invalidate_all()


# ---- Anmeldung & eigenes Konto ------------------------------------------------------
class LoginBody(BaseModel):
    user: str
    password: str


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "?"
    wait = auth_mod.login_blocked(ip)
    if wait:
        raise HTTPException(429, f"Zu viele Fehlversuche – bitte {wait} s warten")
    p = auth_mod.check_login(body.user.strip(), body.password, cfg)
    if not p:
        auth_mod.login_failed(ip)
        await asyncio.sleep(1)
        logbus.warn("auth", f"Anmeldung fehlgeschlagen: {body.user.strip()!r} von {ip}")
        raise HTTPException(401, "Benutzer oder Passwort falsch")
    auth_mod.login_ok(ip)
    token = (auth_mod.make_legacy_session(p.user, cfg.web_password) if p.kind == "legacy"
             else auth_mod.make_session(next(u for u in auth_mod.load_users() if u["user"] == p.user)))
    r = JSONResponse({"ok": True, "user": p.user, "role": p.role})
    _set_session(r, token)
    logbus.info("auth", f"Angemeldet: {p.user} ({p.role}) von {ip}")
    return r


@app.post("/api/logout")
async def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth_mod.COOKIE)
    return r


@app.get("/api/me")
async def whoami(request: Request):
    p = auth_mod.resolve(request, cfg)
    if not p:
        return {"authenticated": False, "open_mode": False}
    return {"authenticated": True, "user": p.user, "role": p.role, "kind": p.kind,
            "open_mode": p.kind == "open", "prefs": auth_mod.load_prefs(p.prefs_key)}


@app.put("/api/me/prefs")
async def put_prefs(body: dict, request: Request):
    return {"ok": True, "prefs": auth_mod.save_prefs(me(request).prefs_key, body or {})}


class PasswordBody(BaseModel):
    old: str
    new: str


@app.put("/api/me/password")
async def change_password(body: PasswordBody, request: Request):
    p = me(request)
    if p.kind not in ("session", "basic"):
        raise HTTPException(400, "Passwort kann nur für Konten der Benutzerverwaltung geändert werden")
    if not auth_mod.check_user(p.user, body.old):
        raise HTTPException(403, "altes Passwort falsch")
    if len(body.new) < 6:
        raise HTTPException(400, "neues Passwort zu kurz (mindestens 6 Zeichen)")
    users = auth_mod.load_users()
    for u in users:
        if u["user"] == p.user:
            u.update(auth_mod.new_credentials(body.new))
    auth_mod.save_users(users)
    u = next(x for x in users if x["user"] == p.user)
    r = JSONResponse({"ok": True})   # neue Sitzung, alte sind durch den Passwortwechsel ungültig
    _set_session(r, auth_mod.make_session(u))
    return r


# ---- Benutzerverwaltung (nur Admin) -------------------------------------------------
@app.get("/api/users")
async def get_users():
    return [{"user": u["user"], "role": u["role"], "created": u.get("created", "")}
            for u in auth_mod.load_users()]


@app.post("/api/users/adopt")
async def adopt_legacy_user():
    """Notfall-Login aus config.env als erstes echtes Admin-Konto übernehmen (gleicher Name, gleiches Passwort)."""
    if auth_mod.load_users():
        raise HTTPException(409, "Es gibt bereits Benutzerkonten")
    if not cfg.web_user or not cfg.web_password:
        raise HTTPException(400, "Kein Notfall-Login in der Config-Datei")
    if not auth_mod.valid_name(cfg.web_user):
        raise HTTPException(400, "Login-Name aus der Config ist als Benutzername ungültig")
    u = {"user": cfg.web_user, "role": "admin", **auth_mod.new_credentials(cfg.web_password),
         "created": time.strftime("%Y-%m-%d %H:%M")}
    auth_mod.save_users([u])
    logbus.info("app", f"Notfall-Login als Benutzerkonto übernommen: {u['user']} (admin)")
    r = JSONResponse({"ok": True, "user": u["user"]})
    _set_session(r, auth_mod.make_session(u))      # gleich angemeldet bleiben
    return r


class UserBody(BaseModel):
    user: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None


def _admins(users: list) -> int:
    return sum(1 for u in users if u["role"] == "admin")


@app.post("/api/users")
async def add_user(body: UserBody):
    name = (body.user or "").strip()
    if not auth_mod.valid_name(name):
        raise HTTPException(400, "Benutzername: 2-40 Zeichen (Buchstaben, Zahlen, . _ - @)")
    if len(body.password or "") < 6:
        raise HTTPException(400, "Passwort zu kurz (mindestens 6 Zeichen)")
    users = auth_mod.load_users()
    if any(u["user"] == name for u in users):
        raise HTTPException(409, "Benutzer existiert bereits")
    role = body.role if body.role in auth_mod.ROLES else "user"
    if not users:
        role = "admin"   # das erste Konto muss die Admin-Oberfläche erreichen können
    users.append({"user": name, "role": role, **auth_mod.new_credentials(body.password),
                  "created": time.strftime("%Y-%m-%d %H:%M")})
    auth_mod.save_users(users)
    logbus.info("app", f"Benutzer angelegt: {name} ({role})")
    return {"ok": True, "role": role, "first": len(users) == 1}


@app.put("/api/users/{name}")
async def update_user(name: str, body: UserBody):
    users = auth_mod.load_users()
    u = next((x for x in users if x["user"] == name), None)
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if body.password:
        if len(body.password) < 6:
            raise HTTPException(400, "Passwort zu kurz (mindestens 6 Zeichen)")
        u.update(auth_mod.new_credentials(body.password))
        logbus.info("app", f"Passwort zurückgesetzt: {name}")
    if body.role:
        if body.role not in auth_mod.ROLES:
            raise HTTPException(400, "Rolle: admin oder user")
        if u["role"] == "admin" and body.role != "admin" and _admins(users) == 1:
            raise HTTPException(400, "der letzte Admin kann nicht herabgestuft werden")
        u["role"] = body.role
        logbus.info("app", f"Rolle geändert: {name} → {body.role}")
    auth_mod.save_users(users)
    return {"ok": True}


@app.post("/api/users/{name}/signout")
async def signout_user(name: str, request: Request):
    """Alle Anmeldungen des Kontos beenden (z. B. verlorenes Tablet) – Passwort bleibt."""
    users = auth_mod.load_users()
    u = next((x for x in users if x["user"] == name), None)
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    u["sg"] = int(u.get("sg", 0)) + 1
    auth_mod.save_users(users)
    logbus.info("app", f"Alle Sitzungen beendet: {name}")
    r = JSONResponse({"ok": True})
    p = me(request)
    if p.user == name and p.kind == "session":   # eigenes Konto: dieses Gerät bleibt angemeldet
        _set_session(r, auth_mod.make_session(u))
    return r


@app.delete("/api/users/{name}")
async def del_user(name: str):
    users = auth_mod.load_users()
    u = next((x for x in users if x["user"] == name), None)
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if u["role"] == "admin" and _admins(users) == 1:
        raise HTTPException(400, "der letzte Admin kann nicht gelöscht werden")
    auth_mod.save_users([x for x in users if x["user"] != name])
    logbus.info("app", f"Benutzer gelöscht: {name}")
    return {"ok": True}


# ---- status / log ---------------------------------------------------------
@app.get("/api/status")
async def status():
    return {"control4": conn.status(),
            "inventory": {"rooms": len(inv.rooms), "devices": len(inv.devices),
                          "imported_at": inv.imported_at},
            "media": media.status()}


@app.get("/api/log")
async def get_log(n: int = 200):
    return logbus.recent(n)


@app.get("/api/log/stream")
async def log_stream():
    return StreamingResponse(logbus.stream(), media_type="text/event-stream")


@app.post("/api/refresh")
async def refresh(what: str = "all"):
    if what in ("all", "inventory"):
        await _refresh_inventory()
    if what in ("all", "media"):
        asyncio.create_task(_refresh_media())
    return {"ok": True}


# ---- websocket (2-way communication) ---------------------------------------
@app.get("/api/ws/status")
async def ws_status():
    return wsman.status()


@app.post("/api/ws/restart")
async def ws_restart():
    """WebSocket neu aufbauen (Token wird nur erneuert, wenn er bald abläuft – spart Cloud-Logins)."""
    await wsman.restart()
    return {"ok": True, "note": "reconnect started; see /api/ws/status"}


@app.post("/api/ws/resubscribe")
async def ws_resubscribe():
    """Re-register callbacks for all known item ids (e.g. after inventory changes)."""
    try:
        n = await wsman.resubscribe()
        return {"ok": True, "subscribed_items": n}
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/events")
async def events(n: int = 100):
    return wsman.events.recent(n)


class DiscoverBody(BaseModel):
    on: bool
    reset: bool = False


_item_names: dict = {}   # Namen/Proxy/Raum unbekannter Absender (Agenten, Protokoll-Items …), einmal nachgeschlagen


@app.get("/api/ws/discover")
async def ws_discover():
    """Diagnose-Mitschnitt: alle Absender des Directors mit Feldern und Häufigkeit (Namen werden nachgeschlagen)."""
    rows = wsman.senders_list()
    missing = [r["id"] for r in rows if not r["name"] and isinstance(r["id"], int) and r["id"] not in _item_names][:40]

    async def look(i):
        try:
            d = await conn.get(f"/api/v1/items/{i}")
            d = d[0] if isinstance(d, list) and d else d
            _item_names[i] = {"name": d.get("name"), "proxy": d.get("proxy") or d.get("typeName"),
                              "room": d.get("roomName")}
        except Exception:  # noqa: BLE001
            _item_names[i] = {"name": "?", "proxy": None, "room": None}
    await asyncio.gather(*(look(i) for i in missing))
    for r in rows:
        extra = _item_names.get(r["id"]) or {}
        r["name"] = r["name"] or extra.get("name")
        r["proxy"] = r["proxy"] or extra.get("proxy")
        r["room"] = extra.get("room") or ((inv.devices.get(r["id"]) or {}).get("room") if isinstance(r["id"], int) else None)
    return {"on": wsman.discover, "since": wsman.discover_since, "tap_ok": wsman.tap_ok, "senders": rows}


@app.put("/api/ws/discover")
async def ws_discover_set(body: DiscoverBody):
    wsman.set_discover(body.on, body.reset)
    return {"ok": True, "on": wsman.discover}


@app.get("/api/drivers")
async def drivers():
    """Treiber-Profile aus data/drivers.json (Push-Felder + Befehle je Proxy)."""
    return wsman.drivers


# ---- cameras ----------------------------------------------------------------
_camera_conf_cache: dict = {}
CAMERAS_CFG = config_mod.DATA_DIR / "cameras.json"


def _load_cam_cfg() -> dict:
    try:
        return json.loads(CAMERAS_CFG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_cam_cfg(cfg: dict) -> None:
    CAMERAS_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


@app.get("/api/cameras/config")
async def get_cam_cfg():
    """Per-camera stream settings. Passwords are never returned, only pass_set."""
    cfgf = _load_cam_cfg()
    out = []
    for c in inv.list_devices(dtype="camera"):
        e = cfgf.get(str(c["id"]), {})
        out.append({"id": c["id"], "name": c["name"], "room": c["room"],
                    "mode": e.get("mode", "snapshot"),  # snapshot | rtsp
                    "rtsp_url": e.get("rtsp_url", ""), "rtsp_user": e.get("rtsp_user", ""),
                    "pass_set": bool(e.get("rtsp_pass")), "manual": False})
    for mid, e in (cfgf.get("_manual", {}) or {}).items():
        out.append({"id": int(mid), "name": e.get("name", "Kamera"), "room": "manuell",
                    "mode": e.get("mode", "rtsp"),
                    "rtsp_url": e.get("rtsp_url", ""), "rtsp_user": e.get("rtsp_user", ""),
                    "pass_set": bool(e.get("rtsp_pass")), "manual": True})
    return out


@app.put("/api/cameras/config/{camera_id}")
async def put_cam_cfg(camera_id: int, body: dict):
    d = inv.devices.get(camera_id)
    cfgf = _load_cam_cfg()
    manual = str(camera_id) in (cfgf.get("_manual") or {})
    if not manual and (not d or d["type"] != "camera"):
        raise HTTPException(404, "camera not found")
    body = body or {}
    e = cfgf["_manual"][str(camera_id)] if manual else cfgf.setdefault(str(camera_id), {})
    d = d or {"name": e.get("name", "Kamera")}
    if body.get("mode") in ("snapshot", "rtsp"):
        e["mode"] = body["mode"]
    for k in ("rtsp_url", "rtsp_user"):
        if k in body:
            e[k] = str(body.get(k) or "").strip()
    if body.get("rtsp_pass"):  # empty = unchanged
        e["rtsp_pass"] = str(body["rtsp_pass"])
    _save_cam_cfg(cfgf)
    await asyncio.to_thread(streamer.restart)  # Konfiguration sofort übernehmen
    logbus.info("camera", f"Camera config {camera_id} ({d['name']}) updated: "
                f"mode={e.get('mode')}, url={'set' if e.get('rtsp_url') else '-'}")
    return {"ok": True}


class CamTestBody(BaseModel):
    rtsp_url: Optional[str] = None
    rtsp_user: Optional[str] = None
    rtsp_pass: Optional[str] = None


# HLS-Durchleitung: gleiche Herkunft für den Browser; das MediaMTX-Cookie
# (Secure-Flag) wird hier manuell verwaltet, da aiohttp es sonst nie über http sendet
_hls_session: Optional[aiohttp.ClientSession] = None
_mtx_cookie: dict = {"v": None}


def _hls() -> aiohttp.ClientSession:
    global _hls_session
    if _hls_session is None or _hls_session.closed:
        _hls_session = aiohttp.ClientSession()
    return _hls_session


def _mtx_keep_cookies(resp) -> None:
    """Cookies von MediaMTX merken (cookieCheck beim Redirect, hlsSession erst in der Antwort DANACH –
    ohne hlsSession antwortet MediaMTX 1.21 auf main_stream.m3u8/Segmente mit 401 „session not found")."""
    cookies = [c.split(";")[0] for c in resp.headers.getall("Set-Cookie", [])]
    if not cookies:
        return
    have = dict(kv.split("=", 1) for kv in _mtx_cookie["v"].split("; ")) if _mtx_cookie["v"] else {}
    for c in cookies:
        k, _, v = c.partition("=")
        have[k] = v
    _mtx_cookie["v"] = "; ".join(f"{k}={v}" for k, v in have.items())


async def _mtx_get(url: str):
    headers = {"Cookie": _mtx_cookie["v"]} if _mtx_cookie["v"] else {}
    async with _hls().get(url, headers=headers, allow_redirects=False) as r:
        _mtx_keep_cookies(r)
        if r.status in (301, 302, 307):
            from urllib.parse import urljoin
            loc = urljoin(url, r.headers.get("Location", "/"))
            async with _hls().get(loc, headers={"Cookie": _mtx_cookie["v"]}) as r2:
                _mtx_keep_cookies(r2)
                return r2.status, await r2.read(), r2.content_type
        if r.status == 401 and _mtx_cookie["v"] and not url.endswith("index.m3u8"):
            # Sitzung abgelaufen (MediaMTX-Neustart/Leerlauf) → einmal über index.m3u8 neu anmelden
            base = url.rsplit("/", 1)[0]
            _mtx_cookie["v"] = ""
            await _mtx_get(base + "/index.m3u8")
            async with _hls().get(url, headers={"Cookie": _mtx_cookie["v"]}) as r3:
                return r3.status, await r3.read(), r3.content_type
        return r.status, await r.read(), r.content_type


@app.get("/hls/{cam_id}/{file}", include_in_schema=False)
async def hls_proxy(cam_id: int, file: str, request: Request):
    if not re.fullmatch(r"[\w.-]+", file):
        raise HTTPException(400, "bad file name")
    url = f"http://127.0.0.1:{HLS_PORT}/cam_{cam_id}/{file}"
    if request.url.query:  # z. B. ?session=… darf nicht verloren gehen
        url += "?" + request.url.query
    try:
        status, data, ctype = await _mtx_get(url)
        if status != 200:
            raise HTTPException(502, f"mediamtx {status}")
        return Response(data, media_type=ctype, headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.post("/hls/{cam_id}/whep", include_in_schema=False)
async def whep_proxy(cam_id: int, request: Request):
    """WebRTC/WHEP-Durchleitung (stateless, keine Cookies): Browser -> MediaMTX."""
    body = await request.body()
    url = f"http://127.0.0.1:8889/cam_{cam_id}/whep"
    try:
        async with _hls().post(url, data=body, headers={"Content-Type": "application/sdp"}) as r:
            if r.status not in (200, 201):
                raise HTTPException(502, f"mediamtx whep {r.status}")
            return Response(await r.read(), media_type="application/sdp")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.post("/api/cameras/{camera_id}/test")
async def camera_test(camera_id: int, body: CamTestBody):
    """Try an RTSP DESCRIBE against the camera with the given (or stored) credentials."""
    d = inv.devices.get(camera_id)
    cfgf = _load_cam_cfg()
    e = (cfgf.get("_manual") or {}).get(str(camera_id)) or cfgf.get(str(camera_id), {})
    if not e and (not d or d["type"] != "camera"):
        raise HTTPException(404, "camera not found")
    url = (body.rtsp_url or e.get("rtsp_url") or "").strip()
    user = body.rtsp_user if body.rtsp_user is not None else e.get("rtsp_user", "")
    pw = body.rtsp_pass if body.rtsp_pass is not None else e.get("rtsp_pass", "")
    if not url:
        raise HTTPException(400, "keine RTSP-URL konfiguriert")
    import base64
    import socket as _socket
    from urllib.parse import urlparse
    u = urlparse(url if "//" in url else f"rtsp://{url}")
    host, port = u.hostname, u.port or 554
    path = u.path + (f"?{u.query}" if u.query else "")
    try:
        s = _socket.create_connection((host, port), timeout=3)
        s.settimeout(3)
        cred = base64.b64encode(f"{user}:{pw}".encode()).decode()
        req = (f"DESCRIBE rtsp://{host}:{port}{path} RTSP/1.0\r\nCSeq: 1\r\n"
               f"Accept: application/sdp\r\nAuthorization: Basic {cred}\r\n\r\n").encode()
        s.sendall(req)
        first = s.recv(256).decode(errors="replace").splitlines()[0]
        s.close()
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "result": f"Verbindung fehlgeschlagen: {type(ex).__name__}: {ex}"}
    ok = " 200 " in first
    return {"ok": ok, "result": first,
            "hint": "" if ok else ("Authentifizierung abgelehnt (401) – Zugangsdaten prüfen"
                                   if "401" in first else f"Antwort: {first}")}


def _manual_cams() -> dict:
    return _load_cam_cfg().get("_manual", {})


@app.get("/api/cameras")
async def cameras():
    cfgf = _load_cam_cfg()
    out = []
    for d in inv.list_devices(dtype="camera"):
        e = cfgf.get(str(d["id"]), {})
        item = {"id": d["id"], "name": d["name"], "room": d["room"], "room_id": d["room_id"],
                "enabled": d["enabled"], "mode": e.get("mode", "snapshot"), "manual": False}
        if item["mode"] == "rtsp":
            item["live"] = streamer.urls(d["id"]) or None
        out.append(item)
    for mid, e in _manual_cams().items():  # manuell angelegte Kameras (ohne Director-Gerät)
        item = {"id": int(mid), "name": e.get("name", "Kamera"), "room": "", "room_id": None,
                "enabled": True, "mode": e.get("mode", "rtsp"), "manual": True}
        if item["mode"] == "rtsp":
            item["live"] = streamer.urls(int(mid)) or None
        out.append(item)
    return out


class ManualCamBody(BaseModel):
    name: str
    rtsp_url: str
    rtsp_user: str = ""
    rtsp_pass: str = ""


@app.post("/api/cameras/manual")
async def add_manual_camera(body: ManualCamBody):
    """Kamera anlegen, die nicht im Control4-Projekt liegt (nur RTSP)."""
    if not body.name or not body.rtsp_url:
        raise HTTPException(400, "Name und RTSP-URL nötig")
    cfgf = _load_cam_cfg()
    manual = cfgf.setdefault("_manual", {})
    mid = 900001
    while str(mid) in manual:
        mid += 1
    manual[str(mid)] = {"name": body.name.strip(), "mode": "rtsp",
                        "rtsp_url": body.rtsp_url.strip(), "rtsp_user": body.rtsp_user}
    if body.rtsp_pass:
        manual[str(mid)]["rtsp_pass"] = body.rtsp_pass
    _save_cam_cfg(cfgf)
    await asyncio.to_thread(streamer.restart)
    logbus.info("camera", f"Manuelle Kamera angelegt: {body.name} (#{mid})")
    return {"ok": True, "id": mid}


@app.delete("/api/cameras/manual/{camera_id}")
async def del_manual_camera(camera_id: int):
    cfgf = _load_cam_cfg()
    if str(camera_id) not in cfgf.get("_manual", {}):
        raise HTTPException(404, "not a manual camera")
    del cfgf["_manual"][str(camera_id)]
    _save_cam_cfg(cfgf)
    await asyncio.to_thread(streamer.restart)
    return {"ok": True}


_frame_cache: dict = {}            # camera_id -> (zeit, jpeg)
_frame_locks: dict = {}


def _grab_jpeg(url: str) -> bytes:
    """Ein Bild aus einem RTSP-Stream als JPEG (PyAV kommt mit faster-whisper; MJPEG-Encoder, kein Pillow nötig)."""
    import fractions
    import av
    c = av.open(url, options={"rtsp_transport": "tcp"}, timeout=8)
    try:
        s = c.streams.video[0]
        for f in c.decode(s):
            enc = av.CodecContext.create("mjpeg", "w")
            enc.width, enc.height, enc.pix_fmt = f.width, f.height, "yuvj420p"
            enc.time_base = s.time_base or fractions.Fraction(1, 25)
            enc.options = {"qscale": "4"}
            g = f.reformat(format="yuvj420p")
            return b"".join(bytes(p) for p in enc.encode(g)) + b"".join(bytes(p) for p in enc.encode(None))
    finally:
        c.close()
    raise RuntimeError("kein Bild im Stream")


async def _rtsp_frame(camera_id: int) -> bytes:
    """Aktuelles Bild über MediaMTX (rtsp://127.0.0.1:8554/cam_<id>, startet die Kamera bei Bedarf);
    3 s zwischengespeichert, je Kamera nur ein Abruf gleichzeitig."""
    lock = _frame_locks.setdefault(camera_id, asyncio.Lock())
    async with lock:
        ts, data = _frame_cache.get(camera_id, (0.0, b""))
        if time.time() - ts < 3 and data:
            return data
        data = await asyncio.wait_for(
            asyncio.to_thread(_grab_jpeg, f"rtsp://127.0.0.1:8554/cam_{camera_id}"), 12)
        _frame_cache[camera_id] = (time.time(), data)
        return data


async def _camera_snapshot_path(camera_id: int) -> str:
    """Snapshot path on the Director from the camera driver's CONF variable."""
    if camera_id not in _camera_conf_cache:
        v = await conn.get(f"/api/v1/items/{camera_id}/variables?varnames=CONF")
        row = next((r for r in (v if isinstance(v, list) else [])
                    if r.get("varName") == "CONF"), None)
        conf = row.get("value") if row else None
        if isinstance(conf, str):  # the Director delivers CONF as a JSON string
            try:
                conf = json.loads(conf)
            except ValueError:
                conf = None
        prefix = (conf or {}).get("url_prefix", "") if isinstance(conf, dict) else ""
        _camera_conf_cache[camera_id] = prefix.rstrip("/") + "/snapshot.jpg"
    return _camera_conf_cache[camera_id]


@app.get("/api/cameras/{camera_id}/snapshot")
async def camera_snapshot(camera_id: int):
    """Proxy a live JPEG snapshot from a Control4 doorbell/camera driver.
    The browser never sees the Director token."""
    d = inv.devices.get(camera_id)
    cfgf = _load_cam_cfg()
    manual = (cfgf.get("_manual") or {}).get(str(camera_id))
    if manual:                                  # manuelle Kamera (ohne Director-Gerät): nur Livestream-Bild
        try:
            return Response(await _rtsp_frame(camera_id), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Standbild aus Livestream fehlgeschlagen: {type(e).__name__}")
    if not d or d["type"] != "camera":
        raise HTTPException(404, "camera not found")
    # RTSP-Kameras: Bild aus dem Livestream schneiden – das Treiber-Standbild des Directors friert ein
    # (Haustür zeigte 2026-10-16 noch ein Bild vom 15.09.). Fallback: Director-Standbild.
    if (cfgf.get(str(camera_id)) or {}).get("mode") == "rtsp":
        try:
            data = await _rtsp_frame(camera_id)
            return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        except Exception as e:  # noqa: BLE001
            logbus.warn("camera", f"Standbild aus Livestream {d['name']} fehlgeschlagen ({type(e).__name__}: {e}) – nehme Director-Bild")
    path = await _camera_snapshot_path(camera_id)
    if "snapshot" not in path:
        raise HTTPException(502, "camera driver has no snapshot url")
    director = await conn.ensure()
    try:
        async with conn.session.get(f"https://{cfg.c4_ip}{path}",
                                    headers={"Authorization": f"Bearer {director.director_bearer_token}"}) as r:
            if r.status != 200:
                raise HTTPException(502, f"camera driver answered {r.status}")
            data = await r.read()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    logbus.info("camera", f"Snapshot {d['name']} ({len(data)//1024} kB)")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/director")
async def director_raw(path: str):
    """Diagnose (nur Admin, nur lesend): beliebiger GET am Director, z. B. path=/api/v1/items/7/commands."""
    if not re.fullmatch(r"/api/v1/[\w/.,=?&%-]+", path) or ".." in path:
        raise HTTPException(400, "nur /api/v1/… erlaubt")
    return await conn.get(path)


@app.get("/api/events/stream")
async def events_stream():
    return StreamingResponse(wsman.events.stream(), media_type="text/event-stream")


@app.get("/api/state/stream")
async def state_stream():
    """Push-first: Hinweise „Raum X / Bereich Y hat sich geändert" – die Oberfläche lädt dann sofort neu."""
    return StreamingResponse(cache.stream(), media_type="text/event-stream")


@app.get("/api/state/status")
async def state_status():
    return cache.status()


async def _read_room_volume(room_id: int):
    """Mehrzonen-Prüfung: Lautstärke des Raums direkt vom Director (CURRENT_VOLUME)."""
    vs = await conn.get(f"/api/v1/items/{room_id}/variables")
    v = next((x.get("value") for x in vs if isinstance(x, dict) and x.get("varName") == "CURRENT_VOLUME"), None)
    return int(v) if str(v).lstrip("-").isdigit() else None

cache.read_volume = _read_room_volume


async def _project_refresh():
    """Composer „Refresh Navigators" erkannt: Projekt (Räume, Geräte, Agenten, Endpunkte) und BluOS neu einlesen."""
    await _refresh_inventory()
    asyncio.create_task(bluos.refresh(conn, _room_names()))
    asyncio.create_task(kodi.refresh(conn))

cache.on_project_refresh = _project_refresh


@app.get("/api/state/rooms")
async def push_rooms():
    """Admin: Art der Lautstärke-Aktualisierung je Raum (direkt / nachlesen / polling) – nur aus dem Zwischenspeicher."""
    unknown = {e.get("volume_dev") for e in cache.ep.values() if e.get("volume_dev")}
    unknown = [d for d in unknown if d not in inv.devices and not (cache.items.get(d) or {}).get("proxy")]
    await asyncio.gather(*(cache._lookup_item(d) for d in unknown))
    return {str(rid): cache.volume_mode(rid) for rid in inv.rooms}


@app.get("/api/rooms/{room_id}/push")
async def room_push(room_id: int):
    """Admin: woher die Bridge für diesen Raum Push bekommt (Quelle, Lautstärke-Endpunkt, Pfade, letzte Ereignisse)."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    await room_state(room_id)          # Raumvariablen lesen → Endpunkte (neu) erkennen
    return await cache.describe(room_id)


# ---- rooms / devices --------------------------------------------------------
class DeviceUpdate(BaseModel):
    note: Optional[str] = None
    aliases: Optional[list] = None
    enabled: Optional[bool] = None
    function: Optional[str] = None
    voice: Optional[bool] = None          # per Sprache ansprechbar


class RoomUpdate(BaseModel):
    note: Optional[str] = None
    aliases: Optional[list] = None
    enabled: Optional[bool] = None
    favorite: Optional[bool] = None


@app.get("/api/rooms")
async def rooms():
    return inv.list_rooms()


@app.get("/api/rooms/power")
async def rooms_power():
    """Which rooms are currently on (source selected)? One batched Director call."""
    r = await cache.get("power", lambda: conn.get("/api/v1/items/variables?varnames=CURRENT_SELECTED_DEVICE"))
    out = {}
    for row in r if isinstance(r, list) else []:
        rid = row.get("id")
        if rid in inv.rooms:
            out[str(rid)] = str(row.get("value", "0")) not in ("0", "")
    return out


@app.get("/api/rooms/playing")
async def rooms_playing():
    """Haus-Übersicht: was läuft in den eingeschalteten Räumen – Cover (Musik) bzw. Quelle (TV, KODI …) für ein
    Mini-Artwork. Nur Räume, die an sind; Raumzustand kommt aus dem Zwischenspeicher (Push)."""
    power = await rooms_power()
    out = {}
    names: dict = {}
    if any(power.values()):
        try:
            items = await cache.get("learn_items", lambda: conn.get("/api/v1/items"), project=True)
            names = {i.get("id"): i.get("name", "") for i in items if isinstance(i, dict)}
        except Exception:  # noqa: BLE001
            names = {}
    for rid in [int(r) for r, on in power.items() if on]:
        try:
            st = await room_state(rid)
        except Exception:  # noqa: BLE001
            continue
        info = st.get("media_raw") or {}
        mi = info.get("mediainfo", info) if isinstance(info, dict) else {}
        b = st.get("bluos") or st.get("kodi") or {}
        src = st.get("selected_device") or 0
        med_src = mi.get("medSrcDev") if isinstance(mi, dict) and str(mi.get("medSrcDev") or "").isdigit() else None
        img = (b.get("image") if b and not b.get("error") else "") or st.get("media_img") or ""
        title = (b.get("title") if b and not b.get("error") else "") or \
            (mi.get("title") if isinstance(mi, dict) and isinstance(mi.get("title"), str) else "")
        sid = int(med_src or src or 0)
        out[str(rid)] = {"img": img, "title": title or "", "source": sid, "source_name": names.get(sid, ""),
                         "play_state": st.get("play_state") or ""}
    return out


@app.put("/api/rooms/{room_id}")
async def update_room(room_id: int, body: RoomUpdate):
    try:
        return inv.set_room(room_id, **body.model_dump())
    except KeyError:
        raise HTTPException(404, "room not found")


@app.get("/api/devices")
async def devices(room_id: Optional[int] = None, type: Optional[str] = None,
                  enabled_only: bool = False, voice_only: bool = False):
    return inv.list_devices(room_id, type, enabled_only, voice_only)


@app.put("/api/devices/{device_id}")
async def update_device(device_id: int, body: DeviceUpdate):
    try:
        return inv.set_device(device_id, **body.model_dump())
    except KeyError:
        raise HTTPException(404, "device not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/ai/context")
async def ai_context(room_id: Optional[int] = None):
    """Compact device list for the language model prompt."""
    return {"context": inv.ai_context(room_id)}


# ---- device tree & inspection (admin UI) ------------------------------------
@app.get("/api/tree")
async def tree():
    """Floors -> rooms -> devices, including hidden rooms (for diagnostics)."""
    floors: dict = {}
    for r in inv.list_rooms():
        f = floors.setdefault(r.get("floor") or "Ohne Ebene", [])
        f.append({"id": r["id"], "name": r["name"], "favorite": r.get("favorite", False),
                  "enabled": r.get("enabled", True), "hidden": r.get("hidden", False),
                  "devices": [d for d in inv.list_devices(room_id=r["id"])]})
    return {"floors": floors, "websocket": wsman.status()}


@app.get("/api/devices/{device_id}/inspect")
async def device_inspect(device_id: int):
    """Everything we know about one item: inventory entry, live Director info
    (typeName/proxy/parentId/...) and all its current variables."""
    d = inv.devices.get(device_id) or inv.rooms.get(device_id)
    info = variables = None
    try:
        r = await conn.get(f"/api/v1/items/{device_id}")
        if isinstance(r, list):
            r = r[0] if r else {}
        info = r.get("data") or r
    except Exception as e:  # noqa: BLE001
        info = {"error": f"{type(e).__name__}: {e}"}
    try:
        v = await conn.get(f"/api/v1/items/{device_id}/variables")
        if isinstance(v, dict):
            v = v.get("data") or v
        variables = v
    except Exception as e:  # noqa: BLE001
        variables = {"error": f"{type(e).__name__}: {e}"}
    try:   # Befehle, die der Director für das Item anbietet (nur lesen)
        c = await conn.get(f"/api/v1/items/{device_id}/commands")
        commands = c.get("data") or c if isinstance(c, dict) else c
    except Exception as e:  # noqa: BLE001
        commands = {"error": f"{type(e).__name__}: {e}"}
    try:   # Netzwerk-Verbindung des Treibers (IP/Port, z. B. BluOS-Player) – nur lesen
        network = await conn.get(f"/api/v1/items/{device_id}/network")
    except Exception as e:  # noqa: BLE001
        network = {"error": f"{type(e).__name__}: {e}"}
    return {"id": device_id, "inventory": d, "item_info": info, "variables": variables, "commands": commands,
            "network": network}


# ---- actions ----------------------------------------------------------------
class LevelBody(BaseModel):
    level: int  # 0 = off, 1-100 = on / brightness


@app.post("/api/devices/{device_id}/level")
async def set_level(device_id: int, body: LevelBody):
    d = inv.devices.get(device_id)
    if not d or d["type"] != "light":
        raise HTTPException(404, "light not found")
    if not 0 <= body.level <= 100:
        raise HTTPException(400, "level must be 0-100")
    await conn.post(f"/api/v1/items/{device_id}/commands", "SET_LEVEL", {"LEVEL": body.level})
    logbus.info("action", f"Light '{d['name']}' ({d['room']}) -> {body.level}")
    return {"ok": True, "device": d["name"], "level": body.level}


@app.get("/api/media/search")
async def media_search(q: str, limit: int = 8, grouped: bool = False):
    """grouped=1 → {artists, albums, songs} für die Oberfläche; sonst flache Liste (Altbestand)."""
    return media.search_grouped(q, min(limit, 40)) if grouped else media.search(q, limit)


@app.get("/api/media/browse")
async def media_browse(kind: str = "album", artist: Optional[str] = None, genre: Optional[str] = None):
    """Browse 'Unsere Musik' (Control4 library): artists, genres, albums or songs (A–Z, kompakt)."""
    if kind not in ("artist", "genre", "album", "song"):
        raise HTTPException(400, "kind must be artist, genre, album or song")
    if kind == "artist":
        return media.browse("artist")
    if kind == "genre":
        return media.browse("genre")
    if kind == "song":
        return media.song_list()
    if not media.albums:
        raise HTTPException(503, "Bibliothek noch nicht geladen")
    return media.browse("album", filter_artist=artist, filter_genre=genre)


COVER_DIR = config_mod.DATA_DIR / "covers"


@app.get("/api/media/cover/{a}/{b}")
async def media_cover(a: str, b: str):
    """Album-Cover vom Director (http://<ip>/images/album/<img>.jpg, ohne Anmeldung abrufbar),
    auf Platte zwischengespeichert – der Browser bekommt es von der Bridge (lange cachebar)."""
    if not re.fullmatch(r"[0-9a-f]{2}", a) or not re.fullmatch(r"[0-9a-f-]{36}", b):
        raise HTTPException(404)
    f = COVER_DIR / a / f"{b}.jpg"
    if not f.is_file():
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                async with s.get(f"http://{cfg.c4_ip}/images/album/{a}/{b}.jpg") as r:
                    if r.status != 200 or not r.content_type.startswith("image/"):
                        raise HTTPException(404, "kein Cover")
                    data = await r.read()
        except aiohttp.ClientError:
            raise HTTPException(502, "Director nicht erreichbar")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/media/resolve")
async def media_resolve(q: str = "", kind: str = "any", album_id: Optional[int] = None):
    """Sprachwunsch → EIN Album: kind album|artist|genre|song|random|any (für c4voice). album_id = vom Sprachmodell
    aus der Trefferliste gewählt → genau dieses Album (q = Titelname bei einem Lied), ohne unscharfe Suche."""
    if kind not in ("album", "artist", "genre", "song", "random", "any"):
        raise HTTPException(400, "kind: album|artist|genre|song|random|any")
    hit = media.by_id(album_id, kind, q) if album_id else media.resolve(q, kind)
    if not hit:
        raise HTTPException(404, f"nichts Passendes zu „{q}“")
    return hit


@app.get("/api/media/candidates")
async def media_candidates(q: str, n: int = 18):
    """Kandidaten aus der Sammlung für die Auswahl durch das Sprachmodell (breit, nicht entschieden)."""
    return {"q": q, "candidates": media.candidates(q, max(1, min(n, 30)))}


@app.get("/api/media/suggest")
async def media_suggest(q: str = ""):
    """Sprachwunsch nicht sicher gefunden → ähnlichste Namen (Interpret/Album/Titel) für „Meinst du …?“ (c4voice)."""
    return {"q": q, "names": media.suggest(q)}


@app.get("/api/media/albums/{album_id}")
async def media_album(album_id: int):
    a = media.album(album_id)
    if not a:
        raise HTTPException(404, "Album unbekannt")
    return a


class PlayBody(BaseModel):
    room_id: int
    album_id: int
    track: Optional[int] = None   # 1-basiert: ab diesem Titel (Control4: Album + n-1 × SKIP_FWD)
    single: bool = False          # nur diesen einen Titel (BluOS); Control4 kennt das nicht → Album ab Titel
    song_id: Optional[int] = None  # statt track: Titel-ID → Bridge rechnet Position selbst (Mehrfach-CDs!)


HISTORY_FILE = config_mod.DATA_DIR / "media_history.json"
_history_lock = asyncio.Lock()


async def _history_add(room_id: int, album_id: int, user: str) -> None:
    """„Zuletzt gespielt" je Raum (der Director führt ROOM_HISTORY, liefert sie hier aber leer)."""
    async with _history_lock:
        try:
            h = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            h = {}
        lst = [e for e in h.get(str(room_id), []) if e.get("album_id") != album_id]
        lst.insert(0, {"album_id": album_id, "ts": int(time.time()), "user": user})
        h[str(room_id)] = lst[:40]
        auth_mod.atomic_write(HISTORY_FILE, json.dumps(h, ensure_ascii=False))


async def _skip_to(room_id: int, device_id: int, track: int) -> None:
    """Warten, bis die Warteschlange des Raums spielt, dann (track-1) × SKIP_FWD."""
    for _ in range(24):
        await asyncio.sleep(0.5)
        if (await _queue_state(device_id, room_id)).get("play_state") == "play":
            break
    for _ in range(track - 1):
        await conn.post(f"/api/v1/items/{room_id}/commands", "SKIP_FWD", {})
        await asyncio.sleep(0.35)
    logbus.info("action", f"Sprung zu Titel {track} in {inv.rooms[room_id]['name']}")


@app.post("/api/media/play")
async def media_play(body: PlayBody, request: Request):
    """Album im Raum starten (live bestätigt 2026-10-16, Wohnzimmer).
    Wichtig: Befehle mit Variante („SELECT_AUDIO_MEDIA:ALBUM") brauchen tParams als LISTE
    [{name, value}] – als Objekt antwortet der Director „cond.params.push is not a function"."""
    if body.album_id not in media.albums or body.room_id not in inv.rooms:
        raise HTTPException(404, "unknown room or album")
    song_name, c4_track = None, body.track
    if body.song_id is not None:
        pos, raw_pos, song_name = media.song_pos(body.album_id, body.song_id)
        if pos is None:
            raise HTTPException(404, "Titel gehört nicht zu diesem Album")
        body.track, c4_track = pos, raw_pos   # Control4 springt in seiner eigenen (Director-)Reihenfolge vor
    b = bluos.room(body.room_id)
    if b:      # BluOS-Raum: gleiches Album per Titel + Interpret aus der BluOS-Bibliothek (gleiche NAS-Sammlung)
        a = media.albums[body.album_id]
        try:
            await bluos.play_album(b["ip"], str(a.get("title") or "").strip(), str(a.get("artist") or "").strip(),
                                   body.track, single=body.single, title=song_name)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except aiohttp.ClientError as e:
            raise HTTPException(502, f"BluOS-Player nicht erreichbar: {e}")
        logbus.info("action", f"Play '{a.get('artist')} - {(a.get('title') or '').strip()}' in "
                              f"{inv.rooms[body.room_id]['name']} (BluOS{(', nur Titel ' if body.single else ', ab Titel ') + str(body.track) if body.track else ''})")
        await _history_add(body.room_id, body.album_id, me(request).user)
        return {"ok": True, "backend": "bluos", "single": body.single, "title": song_name}
    if not await _c4_media_room(body.room_id):
        raise HTTPException(409, "In diesem Raum gibt es keine abspielbare Musikquelle")
    if body.single:
        # Nur diesen Titel: SELECT_AUDIO_MEDIA:SONG mit der Titel-ID (fehlt in der Befehlsliste des Raums, funktioniert
        # aber – live bestätigt 17.10.2026; eine Liste mehrerer IDs nimmt der Director nicht an)
        songs = media.album(body.album_id)["songs"]
        sid = body.song_id if body.song_id is not None else (
            songs[body.track - 1].get("id") if body.track and 0 < body.track <= len(songs) else None)
        if sid is not None:
            name = song_name or next((x.get("name") for x in songs if x.get("id") == sid), None)
            await conn.post(f"/api/v1/items/{body.room_id}/commands", "SELECT_AUDIO_MEDIA:SONG",
                            [{"name": "mediaid", "value": int(sid)}, {"name": "deselect", "value": 1}])
            logbus.info("action", f"Play Titel '{name}' in {inv.rooms[body.room_id]['name']}")
            await _history_add(body.room_id, body.album_id, me(request).user)
            return {"ok": True, "single": True, "title": name}
    await conn.post(f"/api/v1/items/{body.room_id}/commands", "SELECT_AUDIO_MEDIA:ALBUM",
                    [{"name": "mediaid", "value": body.album_id}, {"name": "deselect", "value": 1}])
    a = media.albums[body.album_id]
    logbus.info("action", f"Play '{a.get('artist')} - {(a.get('title') or '').strip()}' in {inv.rooms[body.room_id]['name']}")
    await _history_add(body.room_id, body.album_id, me(request).user)
    songs = media.album(body.album_id)["songs"]
    n = len(songs)
    target = None
    if c4_track and 1 < c4_track <= n:
        asyncio.create_task(_skip_to(body.room_id, _digital_media_id(), c4_track))
        # Zieltitel für die Oberfläche: sie zeigt ihn, bis er läuft (die Sprünge dazwischen kommen alle per Push)
        target = song_name or (songs[body.track - 1].get("name") if body.track and body.track <= n else None)
    return {"ok": True, "single": False, "title": target}


@app.get("/api/media/recent")
async def media_recent(room_id: Optional[int] = None):
    """Zuletzt gespielte Alben (eines Raums oder hausweit), neueste zuerst."""
    try:
        h = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        h = {}
    rows = h.get(str(room_id), []) if room_id else sorted((e for v in h.values() for e in v), key=lambda e: -e["ts"])
    out, seen = [], set()
    by_id = {a["id"]: a for a in media.browse("album")}
    for e in rows:
        if e["album_id"] in seen or e["album_id"] not in by_id:
            continue
        seen.add(e["album_id"])
        out.append({**by_id[e["album_id"]], "played": e["ts"]})
    return out[:24]


@app.get("/api/media/random")
async def media_random(n: int = 12):
    return media.random_albums(max(1, min(n, 48)))


@app.get("/api/rooms/{room_id}/queue")
async def room_queue(room_id: int):
    """Warteschlange des Raums: Titel (aus QUEUE_INFO_V2 bzw. dem laufenden Album) + aktueller Index."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    b = bluos.room(room_id)
    if b:      # BluOS: Warteschlange direkt vom Player (auch Qobuz/Radio), Sprung per /api/rooms/{id}/queue/{pos}
        songs = await _bluos_call(bluos.playlist(b["ip"]))
        st = await _bluos_call(bluos.status(b["ip"]))
        aid = media.find_album(songs[0]["album"], songs[0]["artist"]) if songs else None
        return {"room_id": room_id, "backend": "bluos", "index": st["song"], "state": st["play_state"],
                "album": ({k: media.album(aid)[k] for k in ("id", "title", "artist", "img", "year")} if aid else None),
                "songs": [{**x, "image": _bluos_img(room_id, x["image"])} for x in songs]}
    st = await room_state(room_id)
    out = {"room_id": room_id, "index": st.get("queue_index"), "state": st.get("play_state"),
           "album": None, "songs": []}
    try:
        vs = await conn.get(f"/api/v1/items/{_digital_media_id()}/variables")
    except Exception:  # noqa: BLE001
        vs = []
    for v in vs if isinstance(vs, list) else []:
        if v.get("varName") != "QUEUE_INFO_V2" or not isinstance(v.get("value"), dict):
            continue
        info = v["value"].get("info") or {}
        if str(info.get("name")) != str(room_id):
            continue
        songs = (info.get("songs") or {})
        songs = songs.get("song", []) if isinstance(songs, dict) else []
        for s in songs if isinstance(songs, list) else [songs]:
            sid = str(s.get("$t", "") if isinstance(s, dict) else s).strip()
            if sid.isdigit() and int(sid) in media.songs:
                x = media.songs[int(sid)]
                out["songs"].append({"id": x["id"], "name": x["name"], "artist": x.get("artist") or "",
                                     "album_id": x["album_id"], "track_no": x.get("track_no")})
    mi = (st.get("media_raw") or {})
    mi = mi.get("mediainfo", mi) if isinstance(mi, dict) else {}
    aid = media.find_album(str(mi.get("album") or ""), str(mi.get("artist") or "")) if isinstance(mi, dict) else None
    if not out["songs"] and aid:      # Fallback: das laufende Album ist die Warteschlange
        out["songs"] = media.album(aid)["songs"]
    if aid:
        a = media.album(aid)
        out["album"] = {k: a[k] for k in ("id", "title", "artist", "img", "year")}
    return out


# ---- BluOS: Raum-Backend, Dienste-Browser, Eingänge, Cover ---------------------------------
_c4_media_cache: dict = {}


async def _c4_media_room(room_id: int) -> bool:
    """Raum kann Control4-„Digital Media" (Album-Befehl): Digital Media (nach Proxy erkannt) steht in seiner
    Hören-Liste. Einmal gelesen, dann gemerkt (bis zum Neustart)."""
    if room_id not in _c4_media_cache:
        try:
            m = await room_media(room_id)
            _c4_media_cache[room_id] = any(s["id"] == _digital_media_id() for s in m["listen"])
        except Exception:  # noqa: BLE001
            return False
    return _c4_media_cache[room_id]


async def _bluos_call(coro):
    try:
        return await coro
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"BluOS-Player nicht erreichbar: {type(e).__name__}")
    except asyncio.TimeoutError:
        raise HTTPException(504, "BluOS-Player antwortet nicht")


def _bluos_img(room_id: int, img: Optional[str]) -> str:
    """Player-relative Bild-Pfade über die Bridge holen (Browser erreicht den Player evtl. nicht direkt)."""
    if not img:
        return ""
    if img.startswith("/"):
        from urllib.parse import quote
        return f"/api/bluos/{room_id}/art?u=" + quote(img, safe="")
    return img


@app.get("/api/rooms/{room_id}/backend")
async def room_backend(room_id: int):
    """Womit spielt der Raum Musik? c4 (Digital Media, Album-Befehl) | bluos (Player-API) | none."""
    b = bluos.room(room_id)
    if b:
        return {"type": "bluos", "player": b.get("name"), "ip": b["ip"], "c4_online": b.get("c4_online"),
                "model": b.get("model", ""), "fixed_volume": b.get("fixed_volume", False), "how": b.get("how", ""),
                "proxy_id": b.get("proxy_id"), "sources": {str(k): v for k, v in (b.get("sources") or {}).items()},
                "dead_sources": bluos.dead_sources(room_id), "volume_via": "bluos" if _bluos_vol(room_id) else "c4"}
    return {"type": "c4" if await _c4_media_room(room_id) else "none"}


# ---- KODI (kodi.py): Status + Mediathek, nur lesend ---------------------------------------
@app.get("/api/kodi")
async def kodi_players():
    """KODI-Geräte laut Control4 (Adresse, Bereiche, zugehörige Items) + Verbindungsstand."""
    return {"players": [{**p, "ws": kodi.ws_state(did), "live": kodi.live.get(did)} for did, p in kodi.players.items()],
            "warnings": kodi.warnings, "updated": kodi.updated}


@app.post("/api/kodi/refresh")
async def kodi_refresh():
    await kodi.refresh(conn)
    return await kodi_players()


def _kodi_or_404(did: str) -> str:
    if str(did) not in kodi.players:
        raise HTTPException(404, "KODI nicht gefunden")
    return str(did)


@app.get("/api/kodi/{did}/library/{kind}")
async def kodi_library(did: str, kind: str, start: int = 0, limit: int = 60, q: str = "", sort: str = "",
                       parent: Optional[int] = None, season: Optional[int] = None, path: str = ""):
    did = _kodi_or_404(did)
    try:
        return await cache.get(f"kodi_lib:{did}:{kind}:{start}:{limit}:{q}:{sort}:{parent}:{season}:{path}",
                               lambda: kodi.library(did, kind, start, limit, q, sort, parent, season, path), ttl=300)
    except KodiError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"KODI: {type(e).__name__}")


@app.get("/api/kodi/{did}/details/{kind}/{item_id}")
async def kodi_details(did: str, kind: str, item_id: int):
    did = _kodi_or_404(did)
    try:
        return await kodi.details(did, kind, item_id)
    except KodiError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"KODI: {type(e).__name__}")


@app.get("/api/kodi/{did}/art/{key}")
async def kodi_art(did: str, key: str):
    did = _kodi_or_404(did)
    try:
        data, ctype = await kodi.art(did, key)
    except Exception:  # noqa: BLE001
        raise HTTPException(404, "Bild nicht verfügbar")
    return Response(data, media_type=ctype, headers={"Cache-Control": "max-age=86400"})


# ---- KODI abspielen (Sprache + Oberfläche): Quelle im Raum per Control4, Start per JSON-RPC ------------------
class VideoBody(BaseModel):
    did: str                      # KODI (Treiber-ID aus /api/kodi)
    kind: str                     # movie | tvshow | episode
    id: int
    season: Optional[int] = None
    episode: Optional[int] = None
    dry: bool = False             # nur ermitteln, was liefe
    via: str = "rpc"              # rpc: SELECT_VIDEO_DEVICE + Player.Open · c4: Control4-Befehl „Play Movie/Episode“ (Versuch)


async def _room_watch_ids(room_id: int) -> set:
    data = await cache.get(f"room_media:{room_id}", lambda: conn.get(f"/api/v1/locations/rooms/{room_id}/media"), ttl=300)
    payload = data.get("data") or data if isinstance(data, dict) else {}
    try:
        return {i.get("id") for i in payload["watch_devices"]["visible"]}
    except (KeyError, TypeError):
        return set()


def _kodi_sections_for_room(watch_ids: set) -> list:
    """(Treiber-ID, Bereich) der KODI-Bereiche Filme/Serien, die im Raum als Quelle sichtbar sind."""
    return [(did, s) for did, p in kodi.players.items() for s in p.get("sections") or []
            if s.get("kind") in ("movies", "tvshows") and s.get("source_id") in watch_ids]


@app.get("/api/kodi/sources")
async def kodi_sources():
    """Quellen-IDs der KODI-Bereiche Filme/Serien (c4voice: hat ein Raum eine Mediathek?)."""
    return {"sources": sorted({s["source_id"] for p in kodi.players.values() for s in p.get("sections") or []
                               if s.get("kind") in ("movies", "tvshows") and s.get("source_id")})}


@app.get("/api/kodi/search")
async def kodi_search(q: str, kind: str = "any", room_id: Optional[int] = None):
    """Unscharfe Titelsuche über Filme/Serien; room_id = nur das KODI, das in diesem Raum Quelle ist."""
    dids = None
    if room_id is not None:
        if room_id not in inv.rooms:
            raise HTTPException(404, "room not found")
        secs = _kodi_sections_for_room(await _room_watch_ids(room_id))
        if not secs:
            raise HTTPException(409, "In diesem Raum gibt es keine KODI-Mediathek")
        dids = sorted({d for d, _ in secs})
    try:
        hit = await kodi.search(q, kind, dids)
    except KodiError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"KODI: {type(e).__name__}")
    if not hit:
        raise HTTPException(404, "nichts gefunden")
    return hit


@app.post("/api/rooms/{room_id}/video")
async def room_video(room_id: int, body: VideoBody):
    """Film/Serie aus der KODI-Mediathek im Raum starten. Standard (via=rpc): Quelle im Raum per Control4 wählen
    (SELECT_VIDEO_DEVICE → TV/Verstärker/Eingänge wie in der App), dann Player.Open an KODI. via=c4: der Control4-Befehl
    „Play Movie“/„Play Episode“ des Minitreibers (Parameterformat unbekannt – nur zum Ausprobieren)."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    did = str(body.did)
    want = "movies" if body.kind == "movie" else "tvshows"
    sec = next((s for d, s in _kodi_sections_for_room(await _room_watch_ids(room_id)) if d == did and s["kind"] == want), None)
    if not sec:
        raise HTTPException(409, "Dieser KODI-Bereich ist in diesem Raum keine Quelle")
    try:
        if body.kind == "movie":
            d = await kodi.details(did, "movie", body.id)
            play_kind, play_id, title, sub = "movie", body.id, d.get("title") or "", str(d.get("year") or "")
        elif body.kind == "tvshow":
            ep = await kodi.episode_pick(did, body.id, body.season, body.episode)
            if not ep:
                raise HTTPException(404, "keine passende Folge")
            show = await kodi.details(did, "tvshow", body.id)
            play_kind, play_id, title = "episode", ep["id"], show.get("title") or ""
            sub = f"S{ep['season']:02d}E{ep['episode']:02d}" + (f" · {ep['title']}" if ep.get("title") else "")
        elif body.kind == "episode":
            play_kind, play_id, title, sub = "episode", body.id, "", ""
        else:
            raise HTTPException(400, "kind: movie | tvshow | episode")
    except KodiError as e:
        raise HTTPException(400, str(e))
    out = {"ok": True, "kind": play_kind, "id": play_id, "title": title, "sub": sub,
           "room": inv.rooms[room_id]["name"], "dry": body.dry, "via": body.via}
    if body.dry:
        return out
    if body.via == "c4":
        cmd, pname = ("Play Movie", "Movie") if play_kind == "movie" else ("Play Episode", "Episode")
        await conn.post(f"/api/v1/items/{sec['driver_id']}/commands", cmd, {pname: play_id, "Room": room_id})
    else:
        await conn.post(f"/api/v1/items/{room_id}/commands", "SELECT_VIDEO_DEVICE", {"deviceid": sec["source_id"]})
        await asyncio.sleep(1.0)                      # Raum schaltet um; KODI selbst läuft schon
        try:
            await kodi.play(did, play_kind, play_id)
        except KodiError as e:
            raise HTTPException(502, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"KODI: {type(e).__name__}")
    logbus.info("action", f"Video „{title}“ {sub} in {inv.rooms[room_id]['name']} ({body.via})")
    return out


@app.get("/api/bluos/players")
async def bluos_players():
    """Selbstkonfiguration BluOS (Admin): Player, Control4-Treiber, Raumzuordnung, Musikquellen, Hinweise."""
    return {"players": bluos.players, "devices": list(bluos.devices.values()), "drivers": bluos.drivers,
            "services": bluos.services, "rooms": bluos.rooms, "overrides": bluos.overrides,
            "warnings": bluos.warnings, "updated": bluos.updated}


@app.post("/api/bluos/refresh")
async def bluos_refresh():
    await bluos.refresh(conn, _room_names())
    return await bluos_players()


def _bluos_or_404(room_id: int) -> dict:
    b = bluos.room(room_id)
    if not b:
        raise HTTPException(404, "kein BluOS-Player in diesem Raum")
    return b


@app.get("/api/bluos/{room_id}/browse")
async def bluos_browse(room_id: int, key: str = "", q: str = ""):
    """BluOS-Menübaum (Dienste, Eingänge, Bibliothek): browseKey → tiefer, play/autoplay/action → /play."""
    b = _bluos_or_404(room_id)
    r = await _bluos_call(bluos.browse(b["ip"], key, q))
    for it in r["items"]:
        it["image"] = _bluos_img(room_id, it.get("image"))
    return r


class BluPlayBody(BaseModel):
    url: str


@app.post("/api/bluos/{room_id}/play")
async def bluos_play(room_id: int, body: BluPlayBody):
    b = _bluos_or_404(room_id)
    try:
        r = await _bluos_call(bluos.play(b["ip"], body.url))
    except ValueError as e:
        raise HTTPException(400, str(e))
    logbus.info("action", f"BluOS {body.url.split('?')[0]} in {inv.rooms.get(room_id, {}).get('name', room_id)}")
    return {"ok": True, **r}


class StreamBody(BaseModel):
    service: str                 # qobuz | radio (= TuneIn)
    q: str
    kind: str = "any"            # station | artist | album | song | any
    dry: bool = False            # nur suchen (Trockenlauf/Test), nicht abspielen
    skip: Optional[dict] = None  # „andere Version“: {title, artist, album} der laufenden Aufnahme überspringen
    url: Optional[str] = None    # gezielt diesen Eintrag spielen (vom Sprachmodell aus der Trefferliste gewählt)
    title: str = ""              # … mit Titel/Untertitel für die Rückmeldung
    sub: str = ""


@app.post("/api/rooms/{room_id}/stream")
async def room_stream(room_id: int, body: StreamBody):
    """Qobuz/Radio per Sprache: in BluOS-Räumen über die Suche des Players (wie die BluOS-App). Control4-Räume
    (Wohnzimmer: Control4-Treiber Qobuz #1857 / TuneIn #365) haben über die REST-Schnittstelle keine Suche → 409."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    if body.service not in ("qobuz", "radio"):
        raise HTTPException(400, "service: qobuz | radio")
    b = bluos.room(room_id)
    if not b:
        raise HTTPException(409, "Qobuz/Radio per Sprache geht bisher nur in Räumen mit BluOS-Player")
    try:
        if body.url:                                # gewählter Eintrag – bluos.play prüft die erlaubten Player-Befehle
            if not body.url.startswith(bluos_mod.PLAY_PATHS):
                raise HTTPException(400, "kein erlaubter Player-Befehl")
            hit = {"title": body.title or body.q, "sub": body.sub, "url": body.url, "kind": body.kind,
                   "service": body.service}
        else:
            hit = await bluos.find_stream(b["ip"], body.service, body.q, body.kind, body.skip)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"BluOS-Player nicht erreichbar: {e}")
    alts = hit.pop("alternatives", [])
    if not body.dry:     # abspielen und prüfen; toter Sender (TuneIn 404) → nächster Kandidat (andere Variante, Airable)
        errors = []
        for cand in [hit, *alts][:4]:
            ok, err = await _bluos_call(bluos.play_checked(b["ip"], cand["url"]))
            if ok:
                hit = cand
                break
            errors.append(f"{cand['title']}: {err}")
            logbus.warn("bluos", f"{inv.rooms[room_id]['name']}: „{cand['title']}“ spielt nicht ({err}) – nächster Kandidat")
        else:
            raise HTTPException(502, "Spielt nicht: " + "; ".join(errors))
        cache.invalidate(*cache.room_keys(room_id))
        logbus.info("action", f"{'Radio' if body.service == 'radio' else 'Qobuz'} „{hit['title']}“ in "
                              f"{inv.rooms[room_id]['name']} (BluOS)")
    return {"ok": True, "played": not body.dry, **{k: v for k, v in hit.items() if k not in ("url", "alternatives")},
            "candidates": [c["title"] for c in alts][:5]}


@app.get("/api/rooms/{room_id}/stream/candidates")
async def room_stream_candidates(room_id: int, q: str, cats: str = "Songs,Albums,Artists", n: int = 18):
    """Qobuz-Trefferliste für die Auswahl durch das Sprachmodell (nur lesend, über die Suche des BluOS-Players)."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    b = bluos.room(room_id)
    if not b:
        raise HTTPException(409, "Qobuz per Sprache geht bisher nur in Räumen mit BluOS-Player")
    cs = tuple(c for c in cats.split(",") if c in ("Songs", "Albums", "Artists")) or ("Songs",)
    try:
        return {"q": q, "candidates": await bluos.qobuz_candidates(b["ip"], q, cs, max(1, min(n, 30)))}
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"BluOS-Player nicht erreichbar: {e}")


@app.get("/api/rooms/{room_id}/stream/albums")
async def room_stream_albums(room_id: int, artist: str):
    """Alben eines Interpreten auf Qobuz (Sprache: „Nenne mir die Alben von …“) – über die Suche des BluOS-Players."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    b = bluos.room(room_id)
    if not b:
        raise HTTPException(409, "Qobuz per Sprache geht bisher nur in Räumen mit BluOS-Player")
    try:
        return {"artist": artist, "albums": await bluos.qobuz_albums(b["ip"], artist)}
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"BluOS-Player nicht erreichbar: {e}")


@app.post("/api/rooms/{room_id}/queue/{pos}")
async def room_queue_jump(room_id: int, pos: int):
    """Zu einem Titel der Warteschlange springen (nur BluOS – Control4 hat keinen Befehl dafür)."""
    b = _bluos_or_404(room_id)
    await _bluos_call(bluos.jump(b["ip"], pos))
    return {"ok": True}


@app.get("/api/bluos/{room_id}/art")
async def bluos_art(room_id: int, u: str):
    b = _bluos_or_404(room_id)
    if not u.startswith(("/Artwork", "/images/", "/Sources/images/")):
        raise HTTPException(400, "kein Bildpfad")
    s = await bluos._s()
    try:
        async with s.get(f"http://{b['ip']}:11000{u}") as r:
            if r.status != 200:
                raise HTTPException(404)
            return Response(await r.read(), media_type=r.content_type or "image/jpeg",
                            headers={"Cache-Control": "public, max-age=3600"})
    except aiohttp.ClientError:
        raise HTTPException(502)


# ---- Sehen: Fernbedienung (Raum-Proxy leitet an die gewählte Videoquelle weiter) -----------
REMOTE_KEYS = {
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "ok": "ENTER",
    "back": "BACK", "exit": "EXIT", "cancel": "CANCEL", "menu": "MENU", "guide": "GUIDE", "info": "INFO",
    "home": "CONTROL4", "pvr": "PVR", "recall": "RECALL",
    "play": "PLAY", "pause": "PAUSE", "stop": "STOP", "rec": "RECORD",
    "ffwd": "SCAN_FWD", "rew": "SCAN_REV", "next": "SKIP_FWD", "prev": "SKIP_REV",
    "ch_up": "PULSE_CH_UP", "ch_down": "PULSE_CH_DOWN", "page_up": "PAGE_UP", "page_down": "PAGE_DOWN",
    "red": "PROGRAM_A", "green": "PROGRAM_B", "yellow": "PROGRAM_C", "blue": "PROGRAM_D",
    "star": "STAR", "pound": "POUND", "dash": "DASH", "cc": "CLOSED_CAPTIONED",
    **{str(i): f"NUMBER_{i}" for i in range(10)},
}


class RemoteBody(BaseModel):
    key: str


@app.post("/api/rooms/{room_id}/remote")
async def room_remote(room_id: int, body: RemoteBody):
    """Taste der TV-Fernbedienung: der Raum leitet sie an die aktuell gewählte Videoquelle weiter."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    cmd = REMOTE_KEYS.get(body.key)
    if not cmd:
        raise HTTPException(400, "unbekannte Taste")
    await conn.post(f"/api/v1/items/{room_id}/commands", cmd, {})
    logbus.info("action", f"Fernbedienung {cmd} in {inv.rooms[room_id]['name']}")
    return {"ok": True}


@app.post("/api/rooms/{room_id}/off")
async def room_off(room_id: int):
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    await conn.post(f"/api/v1/items/{room_id}/commands", "ROOM_OFF", {})
    b = bluos.room(room_id)
    if b:      # Control4-Treiber erreicht den Player nicht → selbst anhalten
        try:
            await bluos.transport(b["ip"], "PAUSE")
        except Exception as e:  # noqa: BLE001
            logbus.warn("bluos", f"Pause bei Raum aus fehlgeschlagen: {e}")
    logbus.info("action", f"Room off: {inv.rooms[room_id]['name']}")
    return {"ok": True}


# ---- web UI -----------------------------------------------------------------
def _html(path):
    # no-cache: Browser sollen HTML-Änderungen sofort übernehmen (kein ?v=n mehr nötig)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/vendor/{name}", include_in_schema=False)
async def vendor(name: str):
    p = STATIC / "vendor" / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.get("/ui/{name}", include_in_schema=False)
async def ui_file(name: str):
    """Gemeinsamer UI-Kern + Themes (static/ui/*.js|css), immer frisch."""
    p = STATIC / "ui" / name
    if not re.fullmatch(r"[\w.-]+\.(js|css)", name) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
async def index():
    """Bedienoberfläche: EIN Gerüst, das Theme (X4/OS 3) kommt aus den Benutzer-Einstellungen."""
    return _html(STATIC / "app.html")


@app.get("/login", include_in_schema=False)
async def login_page():
    return _html(STATIC / "login.html")


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return _html(STATIC / "admin.html")


@app.get("/os3", include_in_schema=False)
async def os3_page():
    return RedirectResponse("/?theme=os3")   # alte Lesezeichen


@app.get("/voicetest", include_in_schema=False)
async def voicetest_page():
    return _html(STATIC / "voicetest.html")


# ---- OS3-Homescreen: Favoriten-Layout pro Raum (data/uilayout.json) ------------
UILAYOUT_FILE = config_mod.DATA_DIR / "uilayout.json"
_layout_lock = asyncio.Lock()


def _load_uilayout() -> dict:
    try:
        return json.loads(UILAYOUT_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"rooms": {}}


@app.get("/api/ui/layout")
async def get_ui_layout(request: Request):
    """Favoriten-Layout: das eigene des Benutzers, sonst der Hausstandard."""
    lay, p = _load_uilayout(), me(request)
    own = (lay.get("users") or {}).get(p.prefs_key)
    src = own if own and own.get("rooms") else lay
    return {"rooms": src.get("rooms") or {}, "sizes": src.get("sizes") or {}, "personal": bool(own),
            "can_set_default": p.is_admin}


def _clean_sizes(sizes) -> dict:
    """X4-Kachelgrößen pro Raum und Favorit: {room_id: {fav: {w, h, sel: [geräte_ids]}}}."""
    out = {}
    for rid, favs in (sizes.items() if isinstance(sizes, dict) else []):
        if not str(rid).lstrip("-").isdigit() or not isinstance(favs, dict):
            continue
        room = {}
        for f, v in favs.items():
            if not isinstance(v, dict) or not re.fullmatch(r"\d+|media|security|cat:[a-z]{2,12}", str(f)):
                continue
            try:
                w, h = max(1, min(12, int(v.get("w", 1)))), max(1, min(8, int(v.get("h", 1))))
            except (TypeError, ValueError):
                continue
            e = {"w": w, "h": h}
            if v.get("mode") in ("auto", "btn", "slider", "both"):
                e["mode"] = v["mode"]
            if isinstance(v.get("sel"), list):
                e["sel"] = [int(x) for x in v["sel"] if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())][:40]
            room[str(f)] = e
        if room:
            out[str(int(rid))] = room
    return out


@app.put("/api/ui/layout")
async def put_ui_layout(body: dict, request: Request):
    """scope 'me' (Standard) = nur für mich; 'default' = Hausstandard (nur Admin)."""
    p = me(request)
    scope = body.get("scope") or ("default" if p.kind == "open" else "me")
    if scope == "default" and not p.is_admin:
        raise HTTPException(403, "Hausstandard dürfen nur Admins ändern")
    rooms = body.get("rooms")
    if not isinstance(rooms, dict):
        raise HTTPException(400, "body must contain {rooms: {room_id: [fav, ...]}}")
    clean = {}
    for rid, favs in rooms.items():
        items = []
        for f in favs if isinstance(favs, list) else []:
            if isinstance(f, str) and re.fullmatch(r"media|security|cat:[a-z]{2,12}", f):
                items.append(f)
            elif isinstance(f, (int, float)) or (isinstance(f, str) and f.isdigit()):
                items.append(int(f))
        if not str(rid).lstrip("-").isdigit():
            raise HTTPException(400, f"ungültige Raum-ID {rid!r}")
        clean[str(int(rid))] = items
    sizes = _clean_sizes(body.get("sizes"))
    async with _layout_lock:   # mehrere Benutzer gleichzeitig: lesen-ändern-schreiben ohne Verlust
        lay = _load_uilayout()
        if scope == "default":
            lay["rooms"], lay["sizes"] = clean, sizes
        else:
            lay.setdefault("users", {})[p.prefs_key] = {"rooms": clean, "sizes": sizes}
        auth_mod.atomic_write(UILAYOUT_FILE, json.dumps(lay, ensure_ascii=False, indent=1))
    return {"ok": True, "scope": scope}


@app.delete("/api/ui/layout")
async def reset_ui_layout(request: Request):
    """Eigenes Layout verwerfen → wieder Hausstandard."""
    p = me(request)
    async with _layout_lock:
        lay = _load_uilayout()
        (lay.get("users") or {}).pop(p.prefs_key, None)
        auth_mod.atomic_write(UILAYOUT_FILE, json.dumps(lay, ensure_ascii=False, indent=1))
    return {"ok": True}


# ---- c4voice-API: Status, Steuerung, Konfiguration ----------------------------------
@app.get("/api/voice/status")
async def voice_status():
    proc = _voice["proc"]
    running = bool(proc and proc.returncode is None)
    svc = {}
    if running:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as s:
                async with s.get(f"http://127.0.0.1:{_voice_cfg().get('port', 8090)}/status",
                                 headers={auth_mod.SERVICE_HEADER: auth_mod.SERVICE_TOKEN}) as r:
                    svc = await r.json()
        except Exception:  # noqa: BLE001
            svc = {}
    return {"enabled": _voice_cfg().get("enabled", False), "dry_run": _voice_cfg().get("dry_run", True),
            "running": running,
            "pid": proc.pid if running else None,
            "uptime_s": round(time.time() - _voice["started"]) if running else 0,
            "restarts": _voice["restarts"], "last_error": _voice["last_error"],
            "lines": _voice["lines"][-40:], "service": svc}


def _voice_lang_defaults(cur: dict) -> None:
    """Zur Sprache des Assistenten passende Bausteine wählen – nur was zur ANDEREN Sprache gehört, wird ersetzt
    (eigene Wahl in der richtigen Sprache bleibt): Erkennung, Qwen3-Stimmvorlage, Piper-Stimme."""
    from . import gpu_packages
    en = cur.get("lang") == "en"
    stt = cur.setdefault("stt", {})
    if en and stt.get("model") == "whisper-vulkan" and gpu_packages.whisper_installed()["models"].get("multi"):
        stt["model"] = "whisper-vulkan-multi"            # deutsches Whisper versteht kein Englisch
    if en and stt.get("model") == "large-v3-turbo-german":
        stt["model"] = "large-v3-turbo"
    t = cur.setdefault("tts", {})
    q = t.setdefault("qwen", {})
    ref = str(q.get("ref") or "")
    if ref and en != ref.startswith("qwen_ref_en_"):
        want = ("qwen_ref_en_adventure.wav", "qwen_ref_en_calm.wav") if en else ("qwen_ref_abenteuer.wav", "qwen_ref_ruhig.wav")
        hit = next((w for w in want if (TTS_DIR / w).is_file()), None)
        if hit:
            q["ref"], q["ref_text"] = hit, ""             # Text kommt dann aus <vorlage>.txt
    voice = str(t.get("voice") or "")
    if en != voice.startswith("en_"):
        inst = [x["voice"] for x in _tts_installed()]
        hit = next((v for v in inst if v.startswith("en_" if en else "de_")), None)
        if hit or voice not in inst:           # sonst Standardstimme der Sprache (Admin bietet sie zum Laden an)
            t["voice"] = hit or ("en_US-lessac-medium" if en else "de_DE-thorsten-medium")


def _pkg_done(name: str, variant: str) -> None:
    """Nach einem GPU-Download gleich nutzen: Erkennung bzw. Sprachausgabe umstellen (wer lädt, will es verwenden)."""
    from . import gpu_packages
    cur = _voice_cfg()
    if name == "whisper":
        cur.setdefault("stt", {})["model"] = gpu_packages.STT_ENGINE[variant]
    else:
        t = cur.setdefault("tts", {})
        t["engine"] = "qwen3"
        q = t.setdefault("qwen", {})
        q["model"], q["dir"] = "model-base-small", ""
        q.setdefault("ref", "")
        if not q["ref"]:
            q["ref"] = "qwen_ref_en_adventure.wav" if cur.get("lang") == "en" else "qwen_ref_ruhig.wav"
            q["ref_text"] = ""
        _voice_lang_defaults(cur)
    _voice_cfg_save(cur)
    logbus.info("voice", f"GPU-Paket {name} aktiv: {'Erkennung ' + cur['stt']['model'] if name == 'whisper' else 'Sprachausgabe Qwen3-TTS'}")


@app.get("/api/voice/packages")
async def voice_packages():
    from . import gpu_packages
    return gpu_packages.status()


@app.post("/api/voice/packages/{name}")
async def voice_package_install(name: str, body: dict | None = None):
    """Whisper (Vulkan) bzw. Qwen3-TTS im Hintergrund laden; Fortschritt über GET /api/voice/packages."""
    from . import gpu_packages
    variant = str((body or {}).get("variant") or ("multi" if _voice_cfg().get("lang") == "en" else "de"))
    try:
        gpu_packages.start(name, variant, on_done=_pkg_done)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return gpu_packages.status()


@app.get("/api/voice/config")
async def get_voice_config():
    c = _voice_cfg()
    c["lang"] = "en" if c.get("lang") == "en" else "de"
    if c["lang"] == "en":                    # Admin zeigt/ändert die Anweisungen der eingestellten Sprache
        c["prompts"] = c.get("prompts_en") or {}
    return c


@app.get("/api/voice/prompts/defaults")
async def voice_prompt_defaults():
    """Eingebaute Anweisungen (Admin → Sprache → Erweitert): Vorlage zum Anpassen / Zurücksetzen."""
    from . import voice as voice_mod, voice_en
    if _voice_cfg().get("lang") == "en":
        return {"house": voice_en.HOUSE_PROMPT, "answer": voice_en.SUMMARY_PROMPT, "examples": voice_en.EXAMPLES}
    return {"house": voice_mod.SYSTEM_PROMPT, "answer": voice_mod.SUMMARY_PROMPT, "examples": voice_mod.EXAMPLES}


@app.get("/api/voice/tts/pocket-voices")
async def pocket_voices():
    """Stimmen für Pocket TTS = Audioproben in bin/tts/pocket/voices (Name ohne .wav)."""
    d = Path(__file__).resolve().parent.parent / "bin" / "tts" / "pocket" / "voices"
    return sorted(p.stem for p in d.glob("*.wav")) if d.is_dir() else []


@app.get("/api/voice/tts/qwen-voices")
async def qwen_voices():
    """Stimmen für Qwen3-TTS = data/tts/qwen_ref_<name>.wav mit Text aus <name>.txt."""
    from .voice import qwen_voices as _qv
    return _qv()


@app.put("/api/voice/config")
async def put_voice_config(body: dict):
    cur = _voice_cfg()
    # Sprache des Assistenten (de/en): Erkennung folgt mit; lang_if_unset = Vorgabe aus der Oberflächensprache
    # (Einrichtungsassistent) – nur, solange noch keine Sprache gewählt ist
    lang = body.get("lang") if body.get("lang") in ("de", "en") else \
        body.get("lang_if_unset") if body.get("lang_if_unset") in ("de", "en") and "lang" not in cur else None
    if lang:
        cur["lang"] = lang
        cur.setdefault("stt", {})["language"] = lang
        _voice_lang_defaults(cur)
    if "enabled" in body:
        cur["enabled"] = bool(body["enabled"])
    if "dry_run" in body:
        cur["dry_run"] = bool(body["dry_run"])
    if isinstance(body.get("port"), int) and 1024 <= body["port"] <= 65535:
        cur["port"] = body["port"]
    if isinstance(body.get("bridge_url"), str) and body["bridge_url"].startswith("http"):
        cur["bridge_url"] = body["bridge_url"].strip().rstrip("/")
    if isinstance(body.get("mics"), dict):
        mics = {}
        for k, v in body["mics"].items():
            if not isinstance(k, str):
                continue
            rooms = [str(x).strip() for x in (v if isinstance(v, list) else [v])
                     if str(x).strip()][:3]
            if rooms:
                mics[k[:60]] = rooms
        cur["mics"] = mics
    if isinstance(body.get("adjust"), dict):
        # relative Helligkeits-Stufen (klein/mittel/gross × up/down + gemütlich-Ausnahme)
        a = body["adjust"]
        adj: dict = {}
        for s in ("klein", "mittel", "gross"):
            e = a.get(s)
            row = {}
            if isinstance(e, dict):
                for k in ("up", "down"):
                    v = e.get(k)
                    if isinstance(v, (int, float)) and 1 <= v <= 100:
                        row[k] = float(v)
            adj[s] = row
        for k in ("cozy_below", "cozy_down", "min_level"):
            v = a.get(k)
            if isinstance(v, (int, float)) and 0 <= v <= 100:
                adj[k] = float(v)
        cur["adjust"] = _voice_deep_merge(cur.get("adjust") or {}, adj)
    if isinstance(body.get("music"), dict):
        # Musik per Sprache: Lautstärke-Stufen + Obergrenze
        mu, out = body["music"], {}
        steps = {s: int(v) for s, v in (mu.get("steps") or {}).items()
                 if s in ("klein", "mittel", "gross") and isinstance(v, (int, float)) and 1 <= v <= 50}
        if steps:
            out["steps"] = steps
        if isinstance(mu.get("max_volume"), (int, float)) and 5 <= mu["max_volume"] <= 100:
            out["max_volume"] = int(mu["max_volume"])
        cur["music"] = _voice_deep_merge(cur.get("music") or {}, out)
    if isinstance(body.get("tts"), dict):
        t, out = body["tts"], {}
        if "enabled" in t:
            out["enabled"] = bool(t["enabled"])
        if "followup" in t:
            out["followup"] = bool(t["followup"])
        if t.get("mode") in ("full", "short"):
            out["mode"] = t["mode"]
        if isinstance(t.get("voice"), str) and _TTS_NAME.fullmatch(t["voice"]):
            out["voice"] = t["voice"]
        for k, lo, hi in (("speed", 0.5, 2.0), ("volume", 0.1, 2.0)):
            if isinstance(t.get(k), (int, float)) and lo <= t[k] <= hi:
                out[k] = float(t[k])
        if t.get("engine") in ("piper", "pocket", "qwen3"):
            out["engine"] = t["engine"]
        if isinstance(t.get("pocket"), dict) and re.fullmatch(r"[\w-]{1,40}", str(t["pocket"].get("voice") or "")):
            out["pocket"] = {"voice": t["pocket"]["voice"]}
        q = t.get("qwen")
        if isinstance(q, dict):              # Qwen3-TTS-Dienst: Projektordner, Stimmvorlage (data/tts/…), deren Text
            qo = {}
            if isinstance(q.get("dir"), str) and len(q["dir"]) < 300:
                qo["dir"] = q["dir"].strip()
            if isinstance(q.get("ref"), str) and re.fullmatch(r"[\w.-]{1,80}\.(wav|mp3|flac)", q["ref"].strip()):
                qo["ref"] = q["ref"].strip()
            if isinstance(q.get("ref_text"), str):
                qo["ref_text"] = q["ref_text"].strip()[:400]
            if q.get("model") in ("model-base", "model-base-small"):     # 1.7B / 0.6B
                qo["model"] = q["model"]
            if q.get("lang") in ("german", "english", "french", "spanish", "italian", "portuguese"):   # Sprache der Ausgabe
                qo["lang"] = q["lang"]
            out["qwen"] = qo
        cur["tts"] = _voice_deep_merge(cur.get("tts") or {}, out)
    if isinstance(body.get("prompts"), dict):
        pkey = "prompts_en" if cur.get("lang") == "en" else "prompts"     # je Sprache eigene Anweisungen
        pr = dict(cur.get(pkey) or {})
        for k in ("house", "answer", "examples"):      # examples = Beispielsätze (fehlten → still verworfen)
            v = body["prompts"].get(k)
            if isinstance(v, str):
                v = v.strip()
                if len(v) > 12000:
                    raise HTTPException(400, "Prompt zu lang (max. 12000 Zeichen)")
                if k == "house" and v and not all(x in v for x in ("{rooms}", "{devices}")):
                    raise HTTPException(400, "Haus-Prompt braucht die Platzhalter {rooms} und {devices}")
                pr[k] = v
        cur[pkey] = pr
    if isinstance(body.get("assist"), dict):
        a, out = body["assist"], {"weather": {}, "web": {}, "answer_llm": {}}
        w = a.get("weather") or {}
        if "enabled" in w:
            out["weather"]["enabled"] = bool(w["enabled"])
        if isinstance(w.get("place"), str):
            out["weather"]["place"] = w["place"].strip()[:60]
        for k, lim in (("lat", 90), ("lon", 180)):
            if w.get(k) is None or isinstance(w.get(k), (int, float)) and -lim <= w[k] <= lim:
                if k in w:
                    out["weather"][k] = None if w[k] is None else round(float(w[k]), 4)
        web = a.get("web") or {}
        if "enabled" in web:
            out["web"]["enabled"] = bool(web["enabled"])
        if web.get("provider") in ("searxng", "exa"):
            out["web"]["provider"] = web["provider"]
        if isinstance(web.get("searxng_url"), str):
            u = web["searxng_url"].strip().rstrip("/")
            if u and not re.match(r"^https?://", u):
                raise HTTPException(400, "SearXNG-URL muss mit http:// oder https:// beginnen")
            out["web"]["searxng_url"] = u
        if isinstance(web.get("results"), int) and 1 <= web["results"] <= 10:
            out["web"]["results"] = web["results"]
        al = a.get("answer_llm") or {}
        for k in ("url", "model"):
            if isinstance(al.get(k), str):
                out["answer_llm"][k] = al[k].strip()
        out["answer_llm"].update(_llm_tuning(al))
        cur["assist"] = _voice_deep_merge(cur.get("assist") or {}, out)
    for k in ("wake", "stt", "llm"):
        if isinstance(body.get(k), dict):
            cur[k] = _voice_deep_merge(cur[k], body[k])
    if lang:
        cur["stt"]["language"] = lang
    cur["llm"].update(_llm_tuning(cur["llm"]))
    if cur["stt"].get("model") not in ("tiny", "base", "small", "medium", "large-v3-turbo", "large-v3-turbo-german",
                                       "whisper-gpu", "whisper-vulkan", "whisper-vulkan-multi"):
        cur["stt"]["model"] = "base"
    for k_, lo, hi, dflt in (("threads", 1, 32, 4), ("beam", 1, 10, 1)):
        try:
            cur["stt"][k_] = max(lo, min(hi, int(cur["stt"].get(k_) or dflt)))
        except (TypeError, ValueError):
            cur["stt"][k_] = dflt
    if not isinstance(cur["stt"].get("hint"), str):
        cur["stt"]["hint"] = ""
    try:                                   # Wake-Word: Schwelle 0,1–0,9, Gegenprüfung Standard an
        cur["wake"]["threshold"] = round(max(0.1, min(0.9, float(cur["wake"].get("threshold", 0.5)))), 2)
    except (TypeError, ValueError):
        cur["wake"]["threshold"] = 0.5
    cur["wake"]["verify"] = bool(cur["wake"].get("verify", True))
    _voice_cfg_save(cur)
    return {"ok": True, "config": cur, "restart_required": True}


def _llm_tuning(d: dict) -> dict:
    """keep_alive_min (-1…1440) und num_ctx (0 = Standard, sonst 1024…131072) prüfen; leer/Unsinn → Standard."""
    out = {}
    for k, lo, hi, dflt in (("keep_alive_min", -1, 1440, 60), ("num_ctx", 0, 131072, 0)):
        if k not in d:
            continue
        try:
            v = int(d.get(k)) if d.get(k) not in (None, "") else dflt
        except (TypeError, ValueError):
            v = dflt
        if v is not None:
            if k == "num_ctx" and 0 < v < 1024:
                raise HTTPException(400, "Kontext: 0 (Standard) oder mindestens 1024 Tokens")
            out[k] = max(lo, min(hi, v))
    return out


class DryRunBody(BaseModel):
    on: bool


@app.put("/api/voice/dryrun")
async def set_dry_run(body: DryRunBody, request: Request):
    """Trockenlauf an/aus – wirkt ab dem nächsten Sprachbefehl (c4voice liest die Config pro Befehl)."""
    cur = _voice_cfg()
    cur["dry_run"] = body.on
    _voice_cfg_save(cur)
    logbus.warn("voice", f"Trockenlauf {'AN (nur simulieren)' if body.on else 'AUS – Sprachbefehle schalten ECHT'}"
                         f" (durch {me(request).user})")
    return {"ok": True, "dry_run": body.on}


@app.websocket("/api/voice/mic")
async def voice_mic_proxy(ws: WebSocket):
    """Mikrofon-Zugang für Browser/Geräte: Login an der Bridge, dann 1:1 an c4voice
    (das nur auf localhost lauscht und nur den Service-Token akzeptiert)."""
    p = auth_mod.resolve(ws, cfg)
    origin = ws.headers.get("origin")
    if origin:
        from urllib.parse import urlparse
        if urlparse(origin).netloc != ws.headers.get("host", ""):
            p = None
    if p is None or p.kind == "service":
        await ws.close(code=4401)
        return
    await ws.accept()
    port = _voice_cfg().get("port", 8090)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"http://127.0.0.1:{port}/mic", max_msg_size=4_000_000,
                                    headers={auth_mod.SERVICE_HEADER: auth_mod.SERVICE_TOKEN}) as up:
                async def to_client():
                    async for m in up:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            await ws.send_text(m.data)
                        elif m.type == aiohttp.WSMsgType.BINARY:
                            await ws.send_bytes(m.data)

                async def to_voice():
                    while True:
                        m = await ws.receive()
                        if m["type"] == "websocket.disconnect":
                            return
                        if m.get("text") is not None:
                            text = m["text"]
                            try:
                                j = json.loads(text)
                                if j.get("type") == "hello":
                                    j["user"] = p.user
                                    j["ip"] = ws.client.host if ws.client else ""
                                    if not p.is_admin:
                                        j.pop("room", None)   # Raum kommt aus der Mikrofon-Zuordnung
                                    text = json.dumps(j)
                            except ValueError:
                                pass
                            await up.send_str(text)
                        elif m.get("bytes") is not None:
                            await up.send_bytes(m["bytes"])

                tasks = [asyncio.create_task(to_client()), asyncio.create_task(to_voice())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in tasks:
                    t.cancel()
    except aiohttp.ClientError:
        try:
            await ws.send_json({"type": "error", "message": "c4voice nicht erreichbar – läuft der Sprachdienst?"})
        except Exception:  # noqa: BLE001
            pass
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass


# ---- Wissen für die Sprachsteuerung: Wetter + Websuche (Abfragen macht nur die Bridge) ---------
@app.get("/api/assist/status")
async def assist_status():
    a = _voice_cfg().get("assist") or {}
    w = a.get("weather") or {}
    return {"weather": {"ready": w.get("enabled") and w.get("lat") is not None, "place": w.get("place") or ""},
            "web": {"ready": assist_mod.provider_ready(a, cfg.exa_key) is None,
                    "reason": assist_mod.provider_ready(a, cfg.exa_key),
                    "provider": (a.get("web") or {}).get("provider", "searxng")},
            "answer_llm": a.get("answer_llm") or {}}


@app.get("/api/assist/weather")
async def assist_weather(day: int = 0, lat: Optional[float] = None, lon: Optional[float] = None,
                         place: Optional[str] = None):
    """Wetter für den hinterlegten Ort (lat/lon/place überschreiben – für die Probe im Admin)."""
    w = (_voice_cfg().get("assist") or {}).get("weather") or {}
    if lat is None or lon is None:
        if not w.get("enabled"):
            raise HTTPException(409, "Wetter ist ausgeschaltet")
        lat, lon, place = w.get("lat"), w.get("lon"), place or w.get("place") or ""
    if lat is None or lon is None:
        raise HTTPException(409, "Kein Ort für das Wetter hinterlegt (Admin → Sprache → Wissen & Wetter)")
    try:
        return await assist_mod.weather(lat, lon, day, place or "")
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"Wetterdienst nicht erreichbar: {e}")


_wx_cache: dict = {}   # (lat, lon) → (ts, daten): Wetter-Leiste höchstens alle 10 min abrufen


@app.get("/api/weather/panel")
async def weather_panel(lat: Optional[float] = None, lon: Optional[float] = None, hours: int = 6):
    """Wetter-Bereich der Oberfläche (jetzt / nächste Stunden / morgen) für den hinterlegten Ort.
    lat/lon nur zum Ausprobieren; ohne Ort → 409 mit Hinweis."""
    w = (_voice_cfg().get("assist") or {}).get("weather") or {}
    place = w.get("place") or ""
    if lat is None or lon is None:
        lat, lon = w.get("lat"), w.get("lon")
    if lat is None or lon is None:
        raise HTTPException(409, "Kein Ort hinterlegt (Admin → Sprache → Wissen & Wetter)")
    hours = max(1, min(24, hours))
    key = (round(lat, 3), round(lon, 3), hours)
    hit = _wx_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        data = await assist_mod.panel(lat, lon, place, hours)
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"Wetterdienst nicht erreichbar: {e}")
    _wx_cache[key] = (time.time(), data)
    return data


@app.get("/api/assist/geocode")
async def assist_geocode(name: str):
    try:
        return await assist_mod.geocode(name)
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"Ortssuche nicht erreichbar: {e}")


@app.get("/api/assist/search")
async def assist_search(request: Request, q: str, provider: Optional[str] = None):
    """Websuche (SearXNG oder Exa) → [{title, snippet, url}]. provider= nur für die Probe im Admin."""
    a = _voice_cfg().get("assist") or {}
    web = a.get("web") or {}
    p = me(request)
    if not provider and not web.get("enabled"):
        raise HTTPException(409, "Websuche ist ausgeschaltet")
    if provider and not p.is_admin:
        raise HTTPException(403, "Nur für Administratoren")
    prov = provider or web.get("provider", "searxng")
    try:
        res = await assist_mod.search(prov, q.strip()[:200], int(web.get("results") or 5),
                                      web.get("searxng_url") or "", cfg.exa_key)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"Suche nicht erreichbar: {type(e).__name__}: {e}")
    logbus.info("assist", f"Websuche ({prov}) durch {p.user}: {q.strip()[:80]!r} → {len(res)} Treffer")
    return {"provider": prov, "query": q, "results": res}


# ---- Sprachausgabe (Piper): Stimmen verwalten + Probe-Ansage --------------------------------
TTS_DIR = config_mod.DATA_DIR / "tts"
_TTS_NAME = re.compile(r"[a-z]{2}_[A-Z]{2}-[\w]+-(x_low|low|medium|high)")
# Piper-Stimmen Deutsch + Englisch (rhasspy/piper-voices auf HuggingFace); Größe ≈ low 60 MB, medium 63 MB, high 110 MB
TTS_VOICES_DE = [
    ("de_DE-thorsten-medium", "Thorsten · männlich, klar (Empfehlung)"),
    ("de_DE-thorsten-high", "Thorsten · männlich, beste Qualität, etwas langsamer"),
    ("de_DE-thorsten_emotional-medium", "Thorsten emotional · männlich, lebhafter"),
    ("de_DE-kerstin-low", "Kerstin · weiblich"),
    ("de_DE-ramona-low", "Ramona · weiblich"),
    ("de_DE-eva_k-x_low", "Eva K. · weiblich, sehr klein"),
    ("de_DE-karlsson-low", "Karlsson · männlich"),
    ("de_DE-pavoque-low", "Pavoque · männlich"),
    ("de_DE-mls-medium", "MLS · mehrere Sprecher"),
    # Englisch (Sprache des Assistenten „English“)
    ("en_US-lessac-medium", "Lessac · weiblich, Englisch (US), klar (Empfehlung)"),
    ("en_US-amy-medium", "Amy · weiblich, Englisch (US)"),
    ("en_US-ryan-high", "Ryan · männlich, Englisch (US), beste Qualität, etwas langsamer"),
    ("en_GB-alan-medium", "Alan · männlich, Englisch (UK)"),
    ("en_GB-jenny_dioco-medium", "Jenny · weiblich, Englisch (UK)"),
]
TTS_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/{fam}/{code}/{name}/{q}/"
           "{code}-{name}-{q}{ext}?download=true")


def _tts_installed() -> list:
    out = []
    for f in sorted(TTS_DIR.glob("*.onnx")):
        if (f.parent / (f.name + ".json")).is_file():
            out.append({"voice": f.stem, "mb": round(f.stat().st_size / 1e6)})
    return out


@app.get("/api/voice/tts/voices")
async def tts_voices():
    inst = {v["voice"] for v in _tts_installed()}
    import importlib.util   # nur nachsehen – Piper selbst läuft im c4voice-Prozess
    engine_ok = importlib.util.find_spec("piper") is not None
    return {"engine": engine_ok, "installed": _tts_installed(),
            "available": [{"voice": v, "label": l, "installed": v in inst} for v, l in TTS_VOICES_DE]}


class TtsVoiceBody(BaseModel):
    voice: str


@app.post("/api/voice/tts/download")
async def tts_download(body: TtsVoiceBody):
    """Lädt eine Piper-Stimme (.onnx + .onnx.json) von HuggingFace (rhasspy/piper-voices)."""
    if body.voice not in dict(TTS_VOICES_DE):
        raise HTTPException(400, "unbekannte Stimme")
    code, name, q = body.voice.split("-", 2)
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
            for ext in (".onnx.json", ".onnx"):
                url = TTS_URL.format(fam=code.split("_")[0], code=code, name=name, q=q, ext=ext)
                tmp = TTS_DIR / (body.voice + ext + ".part")
                async with s.get(url) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        async for chunk in r.content.iter_chunked(1 << 16):
                            f.write(chunk)
                os.replace(tmp, TTS_DIR / (body.voice + ext))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Download fehlgeschlagen: {e}")
    logbus.info("voice", f"Piper-Stimme geladen: {body.voice}")
    return {"ok": True, "installed": _tts_installed()}


@app.delete("/api/voice/tts/voices/{voice}")
async def tts_delete(voice: str):
    if not _TTS_NAME.fullmatch(voice):
        raise HTTPException(400, "ungültiger Name")
    if _voice_cfg().get("tts", {}).get("voice") == voice:
        raise HTTPException(400, "aktive Stimme – erst eine andere wählen und speichern")
    for ext in (".onnx", ".onnx.json"):
        (TTS_DIR / (voice + ext)).unlink(missing_ok=True)
    return {"ok": True, "installed": _tts_installed()}


@app.post("/api/voice/tts")
async def tts_test(body: dict):
    """Probe-Ansage: Text → WAV (synthetisiert im laufenden c4voice)."""
    if not (_voice["proc"] and _voice["proc"].returncode is None):
        raise HTTPException(503, "c4voice läuft nicht – erst starten")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(f"http://127.0.0.1:{_voice_cfg().get('port', 8090)}/tts", json=body,
                              headers={auth_mod.SERVICE_HEADER: auth_mod.SERVICE_TOKEN}) as r:
                data = await r.read()
                if r.status != 200:
                    try:
                        detail = json.loads(data).get("detail")
                    except Exception:  # noqa: BLE001
                        detail = data[:200].decode("utf-8", "replace")
                    raise HTTPException(r.status, detail)
    except aiohttp.ClientError as e:
        raise HTTPException(502, f"c4voice nicht erreichbar: {e}")
    return Response(data, media_type="audio/wav")


@app.post("/api/voice/wake/download")
async def voice_wake_download():
    """Lädt fehlende openWakeWord-Modelle nach (einmalig, ~60 MB)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "from openwakeword.utils import download_models; download_models()",
        cwd=str(Path(__file__).resolve().parent.parent),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 300)
    except asyncio.TimeoutError:  # noqa: BLE001
        proc.kill()
        return {"ok": False, "error": "Timeout (5 min) – später erneut versuchen"}
    return {"ok": proc.returncode == 0,
            "log": out.decode("utf-8", "replace")[-600:]}


@app.post("/api/voice/{action}")
async def voice_control(action: str):
    if action == "start":
        ok = await voice_start()
    elif action == "stop":
        ok = await voice_stop()
    elif action == "restart":
        await voice_stop()
        ok = await voice_start()
    else:
        raise HTTPException(404)
    return {"ok": ok}


@app.get("/api/scenes")
async def list_scenes():
    """Szenen/Button-Items im Director suchen (read-only). OS-3-Szenen hängen
    typischerweise als uibutton-Items oder typeName 'scene'/'macro' im Projekt."""
    try:
        items = await conn.get("/api/v1/items")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Director: {type(e).__name__}: {e}")
    out = []
    for i in items:
        tn = (i.get("typeName") or "").lower()
        proxy = i.get("proxy") or ""
        if "scene" in tn or "macro" in tn or proxy == "uibutton" or "button" in tn:
            out.append({"id": i.get("id"), "name": i.get("name", ""),
                        "type_name": i.get("typeName", ""), "proxy": proxy,
                        "room": i.get("roomName", "")})
    return sorted(out, key=lambda s: (s["room"], s["name"].lower()))


# ---- settings (admin UI) ------------------------------------------------------
class ConfigUpdate(BaseModel):
    c4_user: Optional[str] = None
    c4_password: Optional[str] = None  # only written when non-empty
    c4_ip: Optional[str] = None
    web_user: Optional[str] = None
    web_password: Optional[str] = None  # only written when non-empty
    host: Optional[str] = None
    port: Optional[int] = None
    scan_min: Optional[int] = None  # controller scan interval in minutes, 0 = off
    exa_key: Optional[str] = None   # only written when non-empty


@app.get("/api/config")
async def get_config():
    """Settings for the admin form. Passwords are never returned, only set/unset."""
    return {"c4_user": cfg.c4_user, "c4_ip": cfg.c4_ip, "c4_pass_set": bool(cfg.c4_password),
            "web_user": cfg.web_user, "web_pass_set": bool(cfg.web_password),
            "host": cfg.host, "port": cfg.port, "scan_min": cfg.scan_min,
            "exa_key_set": bool(cfg.exa_key),
            "config_path": str(config_mod.config_path())}


@app.put("/api/config")
async def put_config(body: ConfigUpdate, request: Request):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates.get("web_password") is not None and len(updates["web_password"]) < 6:
        raise HTTPException(400, "Passwort zu kurz (mindestens 6 Zeichen)")
    path = config_mod.save(updates)
    # Control4-Zugang sofort am lebenden Config-Objekt anwenden (kein Neustart nötig);
    # Host/Port brauchen weiterhin einen Neustart.
    if updates.get("c4_user"):
        cfg.c4_user = updates["c4_user"]
    if updates.get("c4_password"):
        cfg.c4_password = updates["c4_password"]
    if updates.get("c4_ip"):
        cfg.c4_ip = updates["c4_ip"]
    if updates.get("exa_key"):
        cfg.exa_key = updates["exa_key"]
    if "scan_min" in updates:       # wirkt sofort im Hintergrundjob (vorher erst nach Neustart → Anzeige sprang zurück)
        cfg.scan_min = max(0, int(updates["scan_min"]))
    # Notfall-Login wirkt sofort; wer gerade damit angemeldet ist, bekommt ein frisches Cookie
    renew = None
    if updates.get("web_user") or updates.get("web_password"):
        cfg.web_user = updates.get("web_user") or cfg.web_user
        cfg.web_password = updates.get("web_password") or cfg.web_password
        if me(request).kind == "legacy":
            renew = auth_mod.make_legacy_session(cfg.web_user, cfg.web_password)
    logbus.info("app", f"Config updated via admin UI: {sorted(updates)} -> {path}")
    r = JSONResponse({"ok": True, "restart_required": bool(updates.get("host") or updates.get("port"))})
    if renew:
        _set_session(r, renew)
    return r


class SetupTestBody(BaseModel):
    c4_user: str
    c4_password: str
    c4_ip: Optional[str] = None


@app.post("/api/setup/test")
async def setup_test(body: SetupTestBody):
    """Einrichtungsassistent: Control4-Login testen (EIN Cloud-Login, nicht speichern)."""
    from pyControl4.account import C4Account
    try:
        account = C4Account(body.c4_user, body.c4_password)
        await account.get_account_bearer_token()
        controllers = await account.get_account_controllers()
        name = controllers.get("controllerCommonName", "")
        return {"ok": True, "controller": name,
                "controllers": [v for k, v in controllers.items() if k != "controllerCommonName"]
                if isinstance(controllers, dict) else []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/api/setup/mediamtx")
async def setup_mediamtx():
    """Einrichtungsassistent: Kamera-Livestream (MediaMTX) – vorhanden? passendes Paket für dieses Betriebssystem?"""
    from . import streamer as streamer_mod
    return await asyncio.to_thread(streamer_mod.status)


_mtx_lock = asyncio.Lock()


@app.post("/api/setup/mediamtx")
async def setup_mediamtx_install():
    """MediaMTX der festen Version für dieses Betriebssystem laden (SHA256 geprüft) und Livestreams neu starten."""
    from . import streamer as streamer_mod
    if _mtx_lock.locked():
        raise HTTPException(409, "Download läuft bereits")
    async with _mtx_lock:
        await asyncio.to_thread(streamer.stop)
        try:
            st = await asyncio.to_thread(streamer_mod.install)
        except Exception as e:  # noqa: BLE001
            logbus.warn("streamer", f"MediaMTX-Download: {type(e).__name__}: {e}")
            raise HTTPException(502, f"{e}")
        finally:
            await asyncio.to_thread(streamer.start)
    return st


@app.post("/api/scan")
async def scan_now():
    """Scan the controller now: new rooms/devices/drivers + resubscribe push callbacks."""
    before = len(inv.devices)
    await _refresh_inventory()
    return {"ok": True, "rooms": len(inv.rooms), "devices": len(inv.devices),
            "new_devices": len(inv.devices) - before,
            "ws_items": len(wsman.subscribed_ids)}


# ---- climate -----------------------------------------------------------------
CLIMATE_VARS = ("TEMPERATURE_C", "HEAT_SETPOINT_C", "COOL_SETPOINT_C", "HVAC_MODE",
                "HVAC_STATE", "HVAC_MODES_LIST", "HOLD_MODE", "HUMIDITY", "SCALE")


@app.get("/api/rooms/{room_id}/climate")
async def room_climate(room_id: int):
    """Thermostat state for a room: current temp (push-updated in the UI via SSE),
    setpoint, mode, available modes, heating state."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    r = await cache.get("climate", lambda: conn.get("/api/v1/items/variables?varnames=" + ",".join(CLIMATE_VARS)), room=room_id)
    by_id: dict = {}
    for row in r if isinstance(r, list) else []:
        by_id.setdefault(row.get("id"), {})[row.get("varName")] = row.get("value")
    out = []
    for d in inv.list_devices(room_id=room_id, dtype="thermostat", enabled_only=True):
        v = by_id.get(d["id"], {})
        if v.get("TEMPERATURE_C") is None:
            continue  # driver wrapper without own readings
        modes = [m.strip() for m in str(v.get("HVAC_MODES_LIST") or "").split(",") if m.strip()]
        out.append({"id": d["id"], "name": d["name"],
                    "temperature_c": v.get("TEMPERATURE_C"),
                    "heat_setpoint_c": v.get("HEAT_SETPOINT_C"),
                    "mode": v.get("HVAC_MODE"), "modes": modes,
                    "state": v.get("HVAC_STATE"), "hold": v.get("HOLD_MODE"),
                    "humidity": v.get("HUMIDITY") or None})
    return out


class ClimateBody(BaseModel):
    setpoint_c: Optional[float] = None
    mode: Optional[str] = None  # Off | Heat | Cool | Auto (per HVAC_MODES_LIST)


@app.post("/api/devices/{device_id}/climate")
async def set_climate(device_id: int, body: ClimateBody):
    """Control a thermostat: setpoint (heating) and HVAC mode."""
    d = inv.devices.get(device_id)
    if not d or d["type"] != "thermostat":
        raise HTTPException(404, "thermostat not found")
    if body.setpoint_c is not None:
        if not 5 <= body.setpoint_c <= 30:
            raise HTTPException(400, "setpoint_c must be 5-30")
        await conn.post(f"/api/v1/items/{device_id}/commands", "SET_SETPOINT_HEAT",
                        {"CELSIUS": body.setpoint_c})
        logbus.info("action", f"Climate {d['name']}: setpoint -> {body.setpoint_c} °C")
    if body.mode:
        await conn.post(f"/api/v1/items/{device_id}/commands", "SET_MODE_HVAC",
                        {"MODE": body.mode})
        logbus.info("action", f"Climate {d['name']}: mode -> {body.mode}")
    return {"ok": True}


# ---- blinds -------------------------------------------------------------------
@app.get("/api/blinds/state")
async def blinds_state(room_id: Optional[int] = None):
    """Live blind positions in one batched Director call (Level = % open)."""
    r = await cache.get("blinds", lambda: conn.get("/api/v1/items/variables?varnames=Level,Opening,Closing,Type"), room=room_id)
    by_id: dict = {}
    for row in r if isinstance(r, list) else []:
        by_id.setdefault(row.get("id"), {})[row.get("varName")] = row.get("value")
    out = []
    for d in inv.list_devices(room_id=room_id, dtype="blind", enabled_only=True):
        v = by_id.get(d["id"], {})
        if v.get("Level") is None:
            continue  # motor driver wrappers have no state
        lvl = v.get("Level")
        out.append({"id": d["id"], "name": d["name"],
                    "level": None if lvl is None or int(lvl) >= 9000 else int(lvl),  # 9999 = unbekannt (Gruppen ohne gemeinsame Position)
                    "moving": bool(v.get("Opening") or v.get("Closing")),
                    "type": v.get("Type"),  # Shade | Blind | Group
                    # Gruppen: SET_LEVEL_TARGET mit Wert fehlt in der Befehlsliste, funktioniert aber
                    # (wie die Control4-App; live bestätigt 17.10.2026) → alle Mitglieder fahren mit
                    "has_level_cmd": True})
    return out


class BlindBody(BaseModel):
    level: Optional[int] = None  # 0-100 % open


@app.post("/api/blinds/{blind_id}/level")
async def blind_level(blind_id: int, body: BlindBody):
    d = inv.devices.get(blind_id)
    if not d or d["type"] != "blind":
        raise HTTPException(404, "blind not found")
    if body.level is None or not 0 <= body.level <= 100:
        raise HTTPException(400, "level must be 0-100")
    await conn.post(f"/api/v1/items/{blind_id}/commands", "SET_LEVEL_TARGET",
                    {"LEVEL_TARGET": body.level})
    logbus.info("action", f"Blind '{d['name']}' -> {body.level}% offen")
    return {"ok": True, "level": body.level}


@app.post("/api/blinds/{blind_id}/{action}")
async def blind_action(blind_id: int, action: str):
    """open | close | stop | toggle (works for groups too)."""
    if action not in ("open", "close", "stop", "toggle"):
        raise HTTPException(400, "action must be open, close, stop or toggle")
    d = inv.devices.get(blind_id)
    if not d or d["type"] != "blind":
        raise HTTPException(404, "blind not found")
    cmd = {"open": "SET_LEVEL_TARGET:LEVEL_TARGET_OPEN",
           "close": "SET_LEVEL_TARGET:LEVEL_TARGET_CLOSED",
           "stop": "STOP", "toggle": "TOGGLE"}[action]
    await conn.post(f"/api/v1/items/{blind_id}/commands", cmd, {})
    logbus.info("action", f"Blind '{d['name']}' -> {action}")
    return {"ok": True}


# ---- App-Aufbau je Raum (UI-Configuration-Agent): Lichtszenen, Custom Buttons ------------------
# /api/v1/agents/ui_configuration = genau das, was die Control4-App je Raum zeigt: experiences je Raum mit
# lights.items (Szenen), custom_buttons.screens, contactrelay (Schlösser & Sensoren), comfort, shades …
def _lst(x) -> list:
    return x if isinstance(x, list) else ([x] if x else [])


async def _uicfg() -> list:
    # Projekt-Aufbau: neu nur beim Composer-Signal „Refresh Navigators" (state.py) bzw. 1×/Tag
    u = await cache.get("uicfg", lambda: conn.get("/api/v1/agents/ui_configuration"), project=True)
    return (u or {}).get("experiences") or [] if isinstance(u, dict) else []


def _exp(exps: list, room_id: int, typ: str) -> dict:
    return next((e for e in exps if e.get("room_id") == room_id and e.get("type") == typ), {}) or {}


def _agent(key: str) -> int:
    aid = inv.meta.get(key)
    if not aid:
        raise HTTPException(503, f"Control4-Agent „{key}“ im Projekt nicht gefunden (Projekt neu einlesen)")
    return int(aid)


@app.get("/api/rooms/{room_id}/scenes")
async def room_scenes(room_id: int):
    """Lichtszenen des Raums wie in der App (Licht → Szenen) + aktiv/inaktiv aus Advanced Lighting."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    items = _lst(((_exp(await _uicfg(), room_id, "lights").get("items")) or {}).get("item"))
    if not items:
        return []
    st = await cache.get("scenes", lambda: conn.get("/api/v1/agents/advanced_lighting"))
    by_id = {s.get("scene_id"): s for s in st if isinstance(s, dict)} if isinstance(st, list) else {}
    return [{"id": i.get("id"), "name": i.get("name") or (by_id.get(i.get("id")) or {}).get("name", "?"),
             "active": bool((by_id.get(i.get("id")) or {}).get("is_active"))} for i in items if isinstance(i, dict)]


@app.post("/api/scenes/{scene_id}/{action}")
async def scene_action(scene_id: int, action: str):
    """activate | deactivate | toggle (Advanced Lighting – wie in der App)."""
    cmd = {"activate": "ACTIVATE_SCENE", "deactivate": "DEACTIVATE_SCENE", "toggle": "TOGGLE_SCENE"}.get(action)
    if not cmd:
        raise HTTPException(400, "action must be activate, deactivate or toggle")
    st = await cache.get("scenes", lambda: conn.get("/api/v1/agents/advanced_lighting"))
    name = next((s.get("name") for s in st if isinstance(s, dict) and s.get("scene_id") == scene_id), None) \
        if isinstance(st, list) else None
    if name is None:
        raise HTTPException(404, "Szene unbekannt")
    await conn.post(f"/api/v1/items/{_agent('adv_lighting')}/commands", cmd, {"SCENE_ID": scene_id})
    logbus.info("action", f"Lichtszene '{name}' -> {action}")
    return {"ok": True, "scene": name}


@app.get("/api/rooms/{room_id}/buttons")
async def room_buttons(room_id: int):
    """Custom Buttons des Raums wie in der App (seitliches Menü): Bildschirme mit ihren Knöpfen."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    screens = _lst(((_exp(await _uicfg(), room_id, "custom_buttons").get("screens")) or {}).get("screen"))
    num = lambda b: int(re.sub(r"\D", "", str(b.get("title") or "")) or 0)  # noqa: E731  button1, button2 …
    out = []
    for sc in sorted((s for s in screens if isinstance(s, dict)), key=lambda s: s.get("id") or 0):
        btns = sorted((b for b in _lst((sc.get("buttons") or {}).get("button")) if isinstance(b, dict)), key=num)
        if btns:
            out.append({"id": sc.get("id"), "name": sc.get("name") or "",
                        "buttons": [{"id": b.get("id"), "name": b.get("name") or ""} for b in btns]})
    return out


@app.post("/api/buttons/{button_id}/press")
async def button_press(button_id: int):
    """Custom Button drücken und loslassen (PRESS_BUTTON + RELEASE_BUTTON am Custom-Buttons-Agenten)."""
    name = None
    for e in await _uicfg():
        if e.get("type") != "custom_buttons":
            continue
        for sc in _lst((e.get("screens") or {}).get("screen")):
            for b in _lst((sc.get("buttons") or {}).get("button")):
                if isinstance(b, dict) and b.get("id") == button_id:
                    name = f"{sc.get('name')} / {b.get('name')}"
    if name is None:
        raise HTTPException(404, "Button unbekannt")
    aid = _agent("custom_buttons")
    await conn.post(f"/api/v1/items/{aid}/commands", "PRESS_BUTTON", {"ID": button_id})
    await asyncio.sleep(0.15)
    await conn.post(f"/api/v1/items/{aid}/commands", "RELEASE_BUTTON", {"ID": button_id})
    logbus.info("action", f"Custom Button '{name}'")
    return {"ok": True, "button": name}


# ---- switches (Relais, WeMo-Steckdosen) --------------------------------------------
# Befehle je Schalter-Art (inventory.SWITCH_KIND). Relais: CLOSE = Kontakt geschlossen = Gerät AN (RelayState 1).
SWITCH_CMDS = {"relay": {"on": "CLOSE", "off": "OPEN", "toggle": "TOGGLE"},
               "outlet": {"on": "On", "off": "Off"},
               "wemo": {"on": "Turn WeMo On", "off": "Turn WeMo Off"}}


@app.get("/api/switches/state")
async def switches_state(room_id: Optional[int] = None):
    """Schalter an/aus in einem Batch-Aufruf: Relais → RelayState, Outlet → OUTLET_ON; alte WeMo ohne Zustand."""
    r = await cache.get("switches", lambda: conn.get("/api/v1/items/variables?varnames=RelayState,OUTLET_ON"), room=room_id)
    by_id: dict = {}
    for row in r if isinstance(r, list) else []:
        by_id.setdefault(row.get("id"), {})[row.get("varName")] = row.get("value")
    out = []
    for d in inv.list_devices(room_id=room_id, dtype="switch", enabled_only=True):
        # Chowmain-Outlet: die Variable OUTLET_ON hängt am Treiber-Item (Hülle), das benannte Kind meldet darüber
        v = by_id.get(d["id"]) or by_id.get(d.get("parent"), {})
        raw = v.get("RelayState") if d.get("switch_kind") == "relay" else v.get("OUTLET_ON")
        on = None if raw is None else str(raw).strip().lower() in ("1", "true", "on", "closed")
        out.append({"id": d["id"], "name": d["name"], "type": "switch", "kind": d.get("switch_kind"), "on": on})
    return out


@app.post("/api/switches/{switch_id}/{action}")
async def switch_action(switch_id: int, action: str):
    """on | off | toggle (toggle bei Outlet/WeMo über den gelesenen Zustand)."""
    if action not in ("on", "off", "toggle"):
        raise HTTPException(400, "action must be on, off or toggle")
    d = inv.devices.get(switch_id)
    if not d or d["type"] != "switch":
        raise HTTPException(404, "switch not found")
    cmds = SWITCH_CMDS.get(d.get("switch_kind") or "relay", SWITCH_CMDS["relay"])
    if action == "toggle" and "toggle" not in cmds:
        cur = next((s for s in await switches_state(d.get("room_id")) if s["id"] == switch_id), {})
        if cur.get("on") is None:
            raise HTTPException(409, "Zustand unbekannt – bitte an oder aus wählen")
        action = "off" if cur["on"] else "on"
    # Chowmain-Outlet: Befehle wirken nur am Treiber-Item (Hülle), nicht am benannten Kind (live 17.10.2026)
    target = d.get("parent") if d.get("switch_kind") == "outlet" and d.get("parent") else switch_id
    await conn.post(f"/api/v1/items/{target}/commands", cmds[action], {})
    logbus.info("action", f"Switch '{d['name']}' ({d['room']}) -> {action}")
    return {"ok": True, "action": action}


# ---- live device state --------------------------------------------------------
@app.get("/api/devices/{device_id}/state")
async def device_state(device_id: int):
    """Live values from the Director: on/off, brightness, capabilities."""
    d = inv.devices.get(device_id)
    if not d:
        raise HTTPException(404, "device not found")
    variables = await conn.get(f"/api/v1/items/{device_id}/variables")
    return _parse_light_state(d, variables)


@app.get("/api/devices/state")
async def devices_state(room_id: Optional[int] = None):
    """Live on/off+brightness for many lights in ONE batched Director call."""
    r = await cache.get("lights", lambda: conn.get("/api/v1/items/variables?varnames=LIGHT_STATE,Brightness Percent"), room=room_id)
    by_id = {}
    for row in r if isinstance(r, list) else []:
        e = by_id.setdefault(row.get("id"), {})
        if row.get("varName") == "LIGHT_STATE":
            e["on"] = bool(row.get("value"))
        elif row.get("varName") == "Brightness Percent":
            e["level"] = row.get("value")
    out = []
    for d in inv.list_devices(room_id=room_id, dtype="light", enabled_only=True):
        e = by_id.get(d["id"], {})
        out.append({"id": d["id"], "name": d["name"], "type": "light",
                    "on": e.get("on", False), "level": e.get("level")})
    return out


@app.get("/api/contacts/state")
async def contacts_state(room_id: Optional[int] = None):
    """Live open/closed for door/window contacts and motion sensors (one batched call).
    ContactState: 1/0; meaning per sensor family verified empirically — the UI
    shows the raw flag as well so a wrong mapping is obvious."""
    r = await cache.get("contacts", lambda: conn.get("/api/v1/items/variables?varnames=ContactState"), room=room_id)
    by_id = {row.get("id"): row.get("value") for row in r if isinstance(r, list)}
    out = []
    for d in inv.list_devices(room_id=room_id, enabled_only=True):
        if d["type"] not in ("contact", "motion"):
            continue
        v = by_id.get(d["id"])
        out.append({"id": d["id"], "name": d["name"], "type": d["type"],
                    "state": None if v is None else int(v)})
    return out


def _parse_light_state(d: dict, variables) -> dict:
    out = {"id": d["id"], "name": d["name"], "type": d["type"]}
    for v in variables if isinstance(variables, list) else []:
        name, value = v.get("varName"), v.get("value")
        if name == "LIGHT_STATE":
            out["on"] = bool(value)
        elif name == "Brightness Percent":
            out["level"] = value
        elif name == "Navigator Dynamic Capabilities" and isinstance(value, dict):
            caps = value.get("DynamicCapabilities", {})
            out["dimmable"] = bool(caps.get("dimmer"))
            out["color"] = bool(caps.get("supports_color"))
        elif name == "Device_Type" and isinstance(value, str) and "dimmer" in value.lower():
            out.setdefault("dimmable", True)
    return out


@app.post("/api/rooms/{room_id}/transport")
async def room_transport(room_id: int, body: dict):
    """Room media transport: PLAY, PAUSE, STOP, SKIP_FWD, SKIP_REV (experimental)."""
    cmd = (body or {}).get("cmd", "")
    if cmd not in ("PLAY", "PAUSE", "STOP", "SKIP_FWD", "SKIP_REV"):
        raise HTTPException(400, "cmd must be PLAY, PAUSE, STOP, SKIP_FWD or SKIP_REV")
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    b = bluos.room(room_id)
    if b:      # BluOS-Raum: direkt am Player (Control4-Treiber sind offline/verschlüsselt)
        await _bluos_call(bluos.transport(b["ip"], cmd))
    else:
        await conn.post(f"/api/v1/items/{room_id}/commands", cmd, {})
    logbus.info("action", f"Transport {cmd} in {inv.rooms[room_id]['name']}{' (BluOS)' if b else ''}")
    return {"ok": True}


class ShuffleBody(BaseModel):
    on: bool


class RepeatBody(BaseModel):
    mode: str            # off | all | one


async def _room_kodi(room_id: int) -> Optional[str]:
    st = await room_state(room_id)
    return kodi.owner(st.get("selected_device"))


@app.post("/api/rooms/{room_id}/shuffle")
async def room_shuffle(room_id: int, body: ShuffleBody):
    """Zufall (Mix) an/aus: BluOS-Player direkt, KODI über JSON-RPC. Control4-Warteschlange: Befehl unbekannt (409)."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    b = bluos.room(room_id)
    if b:
        await _bluos_call(bluos.shuffle(b["ip"], body.on))
    elif kid := await _room_kodi(room_id):
        try:
            await kodi.set_shuffle(kid, body.on)
        except KodiError as e:
            raise HTTPException(409, str(e))
    else:
        raise HTTPException(409, "Zufall ist für diese Quelle noch nicht verfügbar")
    logbus.info("action", f"Zufall {'an' if body.on else 'aus'} in {inv.rooms[room_id]['name']}")
    return {"ok": True}


@app.post("/api/rooms/{room_id}/repeat")
async def room_repeat(room_id: int, body: RepeatBody):
    """Wiederholen aus / alle / ein Titel – BluOS-Player direkt, KODI über JSON-RPC."""
    if body.mode not in ("off", "all", "one"):
        raise HTTPException(400, "mode must be off, all or one")
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    b = bluos.room(room_id)
    if b:
        await _bluos_call(bluos.repeat(b["ip"], body.mode))
    elif kid := await _room_kodi(room_id):
        try:
            await kodi.set_repeat(kid, body.mode)
        except KodiError as e:
            raise HTTPException(409, str(e))
    else:
        raise HTTPException(409, "Wiederholen ist für diese Quelle noch nicht verfügbar")
    logbus.info("action", f"Wiederholen {body.mode} in {inv.rooms[room_id]['name']}")
    return {"ok": True}


async def _queue_state(device_id: int, room_id: int) -> dict:
    """Abspielstatus aus der Warteschlange des Medien-Geräts (Digital Media, QUEUE_STATUS_V2):
    state Play/Pause/Stop je Raum (owner) – live bestätigt 2026-10-16. Der streamStatus in
    CURRENT MEDIA INFO bleibt bei Pause auf „OK_playing" und taugt dafür nicht."""
    try:
        vs = await cache.get(f"queue:{device_id}", lambda: conn.get(f"/api/v1/items/{device_id}/variables"))
    except Exception:  # noqa: BLE001
        return {}
    for v in vs if isinstance(vs, list) else []:
        if v.get("varName") != "QUEUE_STATUS_V2" or not isinstance(v.get("value"), dict):
            continue
        qs = (v["value"].get("queues") or {})
        qs = qs.get("queue", []) if isinstance(qs, dict) else []
        for q in qs if isinstance(qs, list) else [qs]:
            rooms = q.get("rooms") or {}
            ids = [r.get("id") for r in (rooms if isinstance(rooms, list) else [rooms]) if isinstance(r, dict)]
            if q.get("owner") == room_id or room_id in ids:
                return {"play_state": str(q.get("state") or "").lower(),   # play | pause | stop
                        "shuffle": bool(q.get("shuffle")), "repeat": bool(q.get("repeat")),
                        "queue_index": q.get("index")}
    return {}


@app.get("/api/rooms/{room_id}/state")
async def room_state(room_id: int):
    """Live room state from Director variables: volume, mute, selected source, media."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    # Raum an: Quellwechsel ohne Eingangswechsel (z. B. Netflix im TV) kommt nicht als Push → alle 5 s nachlesen
    variables = await cache.get(f"room:{room_id}", lambda: conn.get(f"/api/v1/items/{room_id}/variables"),
                                ttl=state_mod.ROOM_ON_S if _room_on.get(room_id) else None)
    out = {"room_id": room_id}
    for v in variables if isinstance(variables, list) else []:
        name, value = v.get("varName"), v.get("value")
        if name == "CURRENT_VOLUME":
            out["volume"] = value
        elif name == "IS_MUTED":
            out["muted"] = bool(value)
        elif name == "CURRENT_SELECTED_DEVICE":
            out["selected_device"] = int(value) if str(value).isdigit() else 0
        elif name == "PLAYING_AUDIO_DEVICE":
            out["playing_device"] = int(value) if str(value).isdigit() else 0
        elif name == "POWER_STATE":
            out["power"] = bool(value)
        elif name == "CURRENT MEDIA INFO" and isinstance(value, dict):
            out["media_raw"] = value
    info = out.get("media_raw") or {}
    mi = info.get("mediainfo", info) if isinstance(info, dict) else {}
    _room_on[room_id] = bool(out.get("selected_device"))
    # Push-Zuordnung lernen: Endpunkte/Pfade des Raums (Ereignisse dieser Geräte betreffen diesen Raum)
    cache.learn_room_vars(room_id, {v.get("varName"): v.get("value") for v in variables if isinstance(v, dict)}
                          if isinstance(variables, list) else {})
    if out.get("selected_device"):
        out.update(await _queue_state(out["selected_device"], room_id))
    b = bluos.room(room_id)
    if b:      # BluOS-Raum: Wiedergabe/Lautstärke kommen vom Player selbst
        try:
            # BluOS-Player: Ereignisse kommen über dessen Control4-Treiber (falls vorhanden) → spätestens alle 10 s
            async def _blu():
                s = await asyncio.wait_for(bluos.status(b["ip"]), 4)
                s["at"] = time.time()      # Zeitpunkt zu „secs" – die Oberfläche rechnet die Liedzeit weiter
                return s
            st = dict(await cache.get(f"bluos:{room_id}", _blu, ttl=10))
            st["image"] = _bluos_img(room_id, st.get("image"))
            out["bluos"] = st
            out["play_state"] = st["play_state"]
            if _bluos_vol(room_id):          # sonst gilt die Raum-Lautstärke von Control4 (Verstärker/feste Lautstärke)
                out["volume"], out["muted"] = st["volume"], st["mute"]
        except Exception as e:  # noqa: BLE001
            out["bluos"] = {"error": f"{type(e).__name__}"}
    if room_id in _duck:       # Ducking aktiv: eigentliche Lautstärke mitliefern
        out["duck"] = {"orig": _duck[room_id]["orig"], "level": _duck[room_id]["level"]}
    kid = kodi.owner(out.get("selected_device"))
    if kid:    # KODI ist Quelle: was läuft, kommt von KODI selbst (Push über WebSocket, sonst höchstens 10 s alt)
        kodi.rooms_seen.setdefault(kid, set()).add(room_id)
        try:
            k = dict(await kodi.status(kid, max_age=10))
            k["image"] = kodi.art_url(kid, k.get("image") or "")
            k["did"] = kid
            out["kodi"] = k
            out["play_state"] = k["state"]
        except Exception as e:  # noqa: BLE001
            out["kodi"] = {"error": f"{type(e).__name__}", "did": kid}
    if isinstance(mi, dict):
        out["media_type"] = mi.get("mediatype", "")
        img = mi.get("img", "")
        if img:
            try:  # Director liefert die Cover-URL base64-kodiert, z. B. controller://images/album/24/….jpg
                import base64
                url = base64.b64decode(img).decode()
                m = re.fullmatch(r"controller://images/album/([0-9a-f]{2}/[0-9a-f-]{36})\.jpg", url)
                out["media_img"] = f"/api/media/cover/{m.group(1)}" if m else url
            except Exception:  # noqa: BLE001
                pass
    return out


class VolumeBody(BaseModel):
    level: Optional[int] = None  # absolute 0-100
    delta: Optional[int] = None  # relative, e.g. +5 / -3


@app.post("/api/rooms/{room_id}/volume")
async def room_volume(room_id: int, body: VolumeBody):
    """Set volume via the hidden SET_VOLUME_LEVEL command (not listed in the
    Director's command list, but confirmed working; pyControl4 uses it too).
    PULSE_VOL_UP/DOWN are ignored by this Director for the audio path."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    if body.level is None and body.delta is not None:
        state = await room_state(room_id)
        level = int(state.get("volume") or 0) + body.delta
    elif body.level is not None:
        level = body.level
    else:
        raise HTTPException(400, "level or delta required")
    level = max(0, min(100, level))
    d = _duck.pop(room_id, None)          # jemand stellt die Lautstärke (App, Sprache) → Ducking endet ohne Zurückstellen
    if d and d.get("task"):
        d["task"].cancel()
    await _set_room_volume(room_id, level)
    return {"ok": True, "level": level}


async def _set_room_volume(room_id: int, level: int, why: str = "") -> None:
    b = _bluos_vol(room_id)
    if b:
        await _bluos_call(bluos.volume(b["ip"], level))
    else:
        await conn.post(f"/api/v1/items/{room_id}/commands", "SET_VOLUME_LEVEL", {"LEVEL": level})
    cache.invalidate(*cache.room_keys(room_id))
    logbus.info("action", f"Volume -> {level} in {inv.rooms[room_id]['name']}{' (BluOS)' if b else ''}{why}")


# ---- Ducking: beim Wake-Word die Musik im Raum des Mikrofons leiser, nach dem Befehl zurück (c4voice) -------------
# Warum: Musik im Raum übertönt den Sprachbefehl. Die Echounterdrückung von Mikrofonen kennt nur ihren eigenen
# Lautsprecher, nicht die Musik der Control4-/BluOS-Verstärker. Absenken nur, wenn im Raum etwas läuft; Zurückstellen
# nur, wenn niemand dazwischen gedreht hat und der Raum noch an ist; spätestens nach max_s automatisch.
_duck: dict = {}       # Raum-ID → {orig, level, until, task}


class DuckBody(BaseModel):
    rooms: list = []            # Raumnamen (Mikrofon-Zuordnung, 1–3)
    on: bool = True
    factor: float = 0.3         # Anteil der aktuellen Lautstärke
    min_level: int = 8          # nie leiser als das; Räume darunter bleiben unverändert
    max_s: float = 25           # Sicherheit: spätestens dann zurück
    dry: bool = False           # nur planen (Test) – nichts schalten


def _duck_rooms(names: list) -> list:
    want = {str(n).strip().lower() for n in names or []}
    return [rid for rid, r in inv.rooms.items() if str(r.get("name", "")).strip().lower() in want]


async def _duck_restore(rid: int, why: str) -> str:
    d = _duck.pop(rid, None)
    if not d:
        return "nicht abgesenkt"
    if d.get("task") and why != "Zeit":
        d["task"].cancel()
    try:
        st = await room_state(rid)
        if not st.get("selected_device") or st.get("power") is False:
            return "Raum inzwischen aus – nichts zurückgestellt"
        cur = st.get("volume")
        if cur is not None and abs(int(cur) - d["level"]) > 2:
            return f"inzwischen auf {cur} gestellt – so gelassen"
        await _set_room_volume(rid, d["orig"], f" (Ducking zurück, {why})")
        return f"zurück auf {d['orig']}"
    except Exception as e:  # noqa: BLE001
        logbus.warn("voice", f"Ducking zurückstellen in {inv.rooms.get(rid, {}).get('name')}: {type(e).__name__}: {e}")
        return "Fehler beim Zurückstellen"


async def _duck_timer(rid: int, secs: float) -> None:
    await asyncio.sleep(secs)
    if rid in _duck:
        _duck[rid]["task"] = None
        await _duck_restore(rid, "Zeit")


@app.post("/api/duck")
async def duck(body: DuckBody):
    """Ducking an/aus für die Räume eines Mikrofons (c4voice beim Wake-Word bzw. nach dem Befehl)."""
    out = {}
    for rid in _duck_rooms(body.rooms):
        name = inv.rooms[rid]["name"]
        if not body.on:
            out[name] = await _duck_restore(rid, "Befehl fertig") if not body.dry else (
                f"würde auf {_duck[rid]['orig']} zurückstellen" if rid in _duck else "nicht abgesenkt")
            continue
        if rid in _duck:                                 # schon abgesenkt (Folgefrage): nur Frist verlängern
            _duck[rid]["until"] = time.time() + body.max_s
            out[name] = f"bleibt auf {_duck[rid]['level']}"
            continue
        st = await room_state(rid)
        vol = st.get("volume")
        playing = bool(st.get("selected_device")) and st.get("power") is not False \
            and st.get("play_state") not in ("pause", "stop")
        if not playing or vol is None or int(vol) <= body.min_level:
            out[name] = "läuft nichts / schon leise – unverändert"
            continue
        new = max(int(body.min_level), round(int(vol) * max(0.05, min(1.0, body.factor))))
        if body.dry:
            out[name] = f"würde {vol} → {new} absenken"
            continue
        await _set_room_volume(rid, new, " (Ducking)")
        _duck[rid] = {"orig": int(vol), "level": new, "until": time.time() + body.max_s,
                      "task": asyncio.create_task(_duck_timer(rid, max(5.0, min(120.0, body.max_s))))}
        out[name] = f"{vol} → {new}"
    return {"ok": True, "rooms": out}


def _bluos_vol(room_id: int) -> Optional[dict]:
    """BluOS-Player, wenn die Raum-Lautstärke direkt am Player gestellt wird. Nicht bei fester Player-Lautstärke
    (Pegel macht ein Verstärker) oder wenn Composer einen anderen Lautstärke-Endpunkt nennt → dann über Control4."""
    b = bluos.room(room_id)
    if not b or b.get("fixed_volume"):
        return None
    vd = (cache.ep.get(room_id) or {}).get("volume_dev")
    return b if not vd or not b.get("endpoint_id") or vd == b["endpoint_id"] else None


class MuteBody(BaseModel):
    on: bool


@app.post("/api/rooms/{room_id}/mute")
async def room_mute(room_id: int, body: MuteBody):
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    b = _bluos_vol(room_id)
    if b:
        await _bluos_call(bluos.mute(b["ip"], body.on))
        return {"ok": True}
    await conn.post(f"/api/v1/items/{room_id}/commands", "MUTE_ON" if body.on else "MUTE_OFF", {})
    logbus.info("action", f"Mute {'on' if body.on else 'off'} in {inv.rooms[room_id]['name']}")
    return {"ok": True}


@app.get("/api/rooms/{room_id}/media")
async def room_media(room_id: int):
    """Listen/watch sources of a room, as delivered by the Director."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    data = await conn.get(f"/api/v1/locations/rooms/{room_id}/media")
    payload = data.get("data") or data if isinstance(data, dict) else {}

    def visible(key: str) -> list:
        try:
            items = payload[key]["visible"]
        except (KeyError, TypeError):
            return []
        return [{"id": i["id"], "name": i.get("name", ""), "type": i.get("type", "")}
                for i in items
                if i.get("name") and not i["name"].startswith("[")]

    listen, watch = visible("listen_devices"), visible("watch_devices")
    icons = await asyncio.gather(*(_source_icon(s["id"]) for s in listen + watch))
    for s, icon in zip(listen + watch, icons):
        s["icon"] = f"/api/media/srcicon/{s['id']}" if icon else ""
    return {"room_id": room_id, "listen": listen, "watch": watch}


# Quellen-Icons wie in der Control4-App: der Treiber nennt sie in capabilities.navigator_display_option
# (controller://driver/<treiber>/icons/… = http://<controller>/driver/<treiber>/icons/…, ohne Login abrufbar)
_icon_path: dict = {}     # item_id -> "driver/…png" | "" (keins)
_icon_bytes: dict = {}    # item_id -> (bytes, content_type)


async def _source_icon(item_id: int) -> str:
    if item_id in _icon_path:
        return _icon_path[item_id]
    path = ""
    try:
        d = await conn.get(f"/api/v1/items/{item_id}")
        d = d[0] if isinstance(d, list) and d else d
        icons = (((d or {}).get("capabilities") or {}).get("navigator_display_option") or {}).get("display_icons") or {}
        lst = icons.get("Icon") or []
        lst = lst if isinstance(lst, list) else [lst]
        cand = [(int(i.get("width") or 0), str(i.get("$t") or "")) for i in lst if isinstance(i, dict)]
        cand = [(w, u) for w, u in cand if u.startswith("controller://driver/")]
        if cand:   # ~90–150 px reicht für Kacheln; sonst das kleinste, das größer ist, bzw. das größte
            best = min(cand, key=lambda c: (0 if 90 <= c[0] <= 150 else 1, abs(c[0] - 120)))
            path = best[1][len("controller://"):]
        stem = str((d or {}).get("protocolFilename") or (d or {}).get("filename") or "").rsplit(".", 1)[0]
        if not path and stem:
            # Rückfall: übliches Treiber-Icon, sonst das (kleine) Composer-Bild aus dem Treiber
            tries = [f"driver/{stem}/icons/device/experience_90.png"]
            for m in (d or {}).get("proxyMeta") or []:
                if m.get("largeSource") == "c4z" and m.get("largeImage"):
                    tries.append(f"driver/{stem}/{str(m['largeImage']).replace(chr(92), '/')}")
            for t in tries:
                async with conn.session.get(f"http://{cfg.c4_ip}/{t}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200 and r.headers.get("Content-Type", "").startswith("image/"):
                        path = t
                        break
    except Exception as e:  # noqa: BLE001
        logbus.warn("media", f"Icon für Quelle {item_id} nicht lesbar: {type(e).__name__}")
        return ""          # nicht cachen → nächster Versuch beim nächsten Laden
    _icon_path[item_id] = path
    return path


@app.get("/api/media/srcicon/{item_id}")
async def source_icon(item_id: int):
    if item_id not in _icon_bytes:
        path = await _source_icon(item_id)
        if not path:
            raise HTTPException(404, "kein Icon")
        try:
            async with conn.session.get(f"http://{cfg.c4_ip}/{path}", timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    raise HTTPException(502, f"Controller antwortet {r.status}")
                _icon_bytes[item_id] = (await r.read(), r.headers.get("Content-Type", "image/png"))
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"{type(e).__name__}: {e}")
    data, ctype = _icon_bytes[item_id]
    return Response(data, media_type=ctype, headers={"Cache-Control": "max-age=86400"})


class SourceBody(BaseModel):
    device_id: int
    kind: str  # "audio" or "video"


@app.post("/api/rooms/{room_id}/select_source")
async def select_source(room_id: int, body: SourceBody):
    """Select a listen/watch source (SELECT_AUDIO_DEVICE / SELECT_VIDEO_DEVICE)."""
    if room_id not in inv.rooms:
        raise HTTPException(404, "room not found")
    cmd = {"audio": "SELECT_AUDIO_DEVICE", "video": "SELECT_VIDEO_DEVICE"}.get(body.kind)
    if not cmd:
        raise HTTPException(400, "kind must be audio or video")
    await conn.post(f"/api/v1/items/{room_id}/commands", cmd, {"deviceid": body.device_id})
    logbus.info("action", f"Select {body.kind} source {body.device_id} in {inv.rooms[room_id]['name']}")
    return {"ok": True}


# ---- Anlern-Assistent (experimentell, learn.py) – zuletzt einhängen: zapft wsman.on_message an -------------------
from . import learn as learn_mod  # noqa: E402

learn_mod.attach(app, conn, inv, cache, wsman, bluos, kodi)


# ---- Angelernte Geräte (learned.py): Rangfolge Proxy → Modul → Profil ------------------------------------------------
from . import learned as learned_mod  # noqa: E402


def _module_owned(item_id: int) -> bool:
    """Gehört das Item zu einem Modul (BluOS, KODI)? Dann hat das Modul Vorrang vor einem angelernten Profil."""
    if kodi.owner(item_id):
        return True
    ids = {x for d in (bluos.drivers or []) for x in (d.get("driver_id"), d.get("proxy_id"), d.get("endpoint_id")) if x}
    return item_id in ids or str(item_id) in (bluos.services or {})


async def _apply_learned(notify: bool = False) -> int:
    try:
        items = await cache.get("learn_items", lambda: conn.get("/api/v1/items"), project=True)
        n = await learned_mod.apply(inv, items, _module_owned)
    except Exception as e:  # noqa: BLE001
        logbus.warn("learn", f"Profile nicht übernommen: {type(e).__name__}: {e}")
        return 0
    cache.invalidate("learned")
    if notify:                                   # nach dem Speichern im Assistenten: Oberflächen laden Geräte neu
        cache._queue_notify(keys={"project"})
    return n


learned_mod.load_last()
learn_mod._x["on_learned"] = learned_mod.on_message
learn_mod._x["on_profiles"] = lambda: _apply_learned(True)
app.include_router(learned_mod.router)
