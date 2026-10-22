"""Live 2-way communication with the Director via pyControl4's socket.io websocket.

The Director pushes item updates on wss://<ip>/c4_ws/ (undocumented, socket.io).
Wichtig (pyControl4 2.0.2, websocket.py gelesen 17.10.2026): das Abo beim Director
(/api/v1/items/datatoui → subscriptionId → startSubscription) liefert IMMER alle Items des
Projekts. `add_item_callback` ist nur ein Filter im Client – was keinen Callback hat, wird
verworfen. Darum zwei Wege zum Push-first-Zwischenspeicher:
  1. Callback für ALLE Item-IDs des Projekts (inv.all_ids, öffentliche API) – robust gegen
     Bibliotheks-Updates; Ereignisse im Ereignis-Puffer/Log nur für Inventar-Geräte.
  2. Tap auf _process_message (privat) als Netz für Items, die erst nach dem letzten
     Scan dazukamen – und als Diagnose-Mitschnitt.
The director token is only valid 24 h, so the connection is rebuilt with a fresh token
every TOKEN_REFRESH_S (18 h, safety margin).
"""
import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Optional

from pyControl4.websocket import C4Websocket

from . import logbus
from .config import DATA_DIR

TOKEN_REFRESH_S = 18 * 3600  # reconnect with fresh token well before the 24 h expiry
MONITOR_S = 30               # health-check interval while connected

DRIVERS_FILE = DATA_DIR / "drivers.json"
DISCOVER_FILE = DATA_DIR / "ws_discover.json"   # Diagnose-Schalter „alle Ereignisse mitschneiden" (Standard aus)


def _load_discover() -> bool:
    try:
        return bool(json.loads(DISCOVER_FILE.read_text(encoding="utf-8")).get("on"))
    except Exception:  # noqa: BLE001
        return False


def _load_drivers() -> dict:
    try:
        return json.loads(DRIVERS_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logbus.warn("ws", f"drivers.json nicht lesbar ({e}); nur Heuristik")
        return {"_unknown": {"push": {"LIGHT_LEVEL": "brightness", "current_temperature_c": "temperature_c"}}}


def _dig(obj, dotted: str):
    """Resolve 'a.b.c' against nested dicts; None if missing."""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def normalize(driver_map: dict, payload: dict) -> dict:
    """Map raw push fields to normalized meanings via drivers.json ('push' block)."""
    out = {}
    for field, target in (driver_map or {}).get("push", {}).items():
        v = _dig(payload, field)
        if v is not None:
            out[target] = v
    return out


class EventLog:
    """Ring buffer of device events with SSE subscribers (like logbus, but events)."""

    def __init__(self, maxlen: int = 500):
        self._buffer: deque = deque(maxlen=maxlen)
        self._subscribers: set = set()

    def add(self, entry: dict) -> None:
        self._buffer.append(entry)
        for q in list(self._subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    def recent(self, n: int = 100) -> list:
        return list(self._buffer)[-n:]

    async def stream(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        try:
            for entry in self.recent(50):
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            while True:
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._subscribers.discard(q)


class WsManager:
    """Owns the C4Websocket connection; restarted by app lifespan."""

    def __init__(self, conn, inv):
        self.conn = conn
        self.inv = inv
        self.events = EventLog()
        self.drivers = _load_drivers()  # data/drivers.json: Push/Befehle je Treiber-Proxy
        self.c4ws: Optional[C4Websocket] = None
        self.subscribed_ids: list = []
        self.connected_at = 0.0
        self.last_event_at = 0.0
        self.event_count = 0
        self.last_error = ""
        self._reconnect_now = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        # Diagnose: Absender-Statistik über ALLE Nachrichten des Directors (nur wenn eingeschaltet)
        self.discover = _load_discover()
        self.discover_since = time.time() if self.discover else 0.0
        self.senders: dict = {}
        self.tap_ok = False
        self.on_message = None        # Push-first: StateCache.on_message (jede Nachricht, auch unregistrierte)
        self.on_reconnect = None

    # ---- status -------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return bool(self.connected_at) and self.c4ws is not None

    @property
    def push_ok(self) -> bool:
        """Push nutzbar = verbunden und Mitlesen aller Nachrichten aktiv."""
        return self.connected and self.tap_ok

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "connected_at": self.connected_at,
            "uptime_s": int(time.time() - self.connected_at) if self.connected_at else 0,
            "token_refresh_s": TOKEN_REFRESH_S,
            "subscribed_items": len(self.subscribed_ids),
            "events_total": self.event_count,
            "last_event_at": self.last_event_at,
            "last_error": self.last_error,
            "discover": self.discover,
            "discover_since": self.discover_since,
            "discover_senders": len(self.senders),
            "tap_ok": self.tap_ok,
        }

    # ---- Diagnose: alle Absender mitschneiden ---------------------------------
    def set_discover(self, on: bool, reset: bool = False) -> None:
        from .auth import atomic_write
        if reset or (on and not self.discover):
            self.senders = {}
        self.discover = bool(on)
        self.discover_since = time.time() if on else 0.0
        atomic_write(DISCOVER_FILE, json.dumps({"on": self.discover}))
        logbus.info("ws", f"Diagnose-Mitschnitt {'AN' if on else 'aus'}")

    def _record_sender(self, message, routed: bool) -> None:
        if not isinstance(message, dict):
            return
        dev = message.get("iddevice")
        data = message.get("data")
        if isinstance(data, dict):
            keys = [k if k != "devicecommand" else "devicecommand:" + str((data[k] or {}).get("command"))
                    for k in data.keys()]
        else:
            keys = ["(kein dict)"]
        if message.get("evtName") and message.get("evtName") != "OnDataToUI":
            keys = [f"[{message['evtName']}]"] + keys
        s = self.senders.get(dev)
        if s is None:
            if len(self.senders) >= 2000:       # Schutz vor endlosem Wachstum
                return
            s = self.senders[dev] = {"id": dev, "count": 0, "keys": {}, "first": time.time(), "routed": routed}
        s["count"] += 1
        s["last"] = time.time()
        s["routed"] = routed
        for k in keys[:12]:
            s["keys"][k] = s["keys"].get(k, 0) + 1
        s["sample"] = json.dumps(data, ensure_ascii=False)[:400]

    def senders_list(self) -> list:
        out = []
        for s in sorted(self.senders.values(), key=lambda x: -x["count"]):
            d = self.inv.devices.get(s["id"]) or self.inv.rooms.get(s["id"]) or {}
            out.append({**s, "keys": sorted(s["keys"].items(), key=lambda kv: -kv[1]),
                        "name": d.get("name"), "proxy": d.get("proxy")})
        return out

    # ---- event handling -----------------------------------------------------
    def _device_name(self, item_id: int) -> str:
        d = self.inv.devices.get(item_id) or self.inv.rooms.get(item_id)
        return d["name"] if d else "?"

    async def _on_any(self, item_id: int, message: dict) -> None:
        """Alle übrigen Projekt-Items (Agenten, Medien-Dienste, Endpunkte …): nur an den Zwischenspeicher –
        wenn der Tap aktiv ist, hat der die Nachricht schon weitergegeben."""
        if self.on_message and not self.tap_ok:
            self.on_message(message)

    async def _on_event(self, item_id: int, message: dict) -> None:
        if self.on_message and not self.tap_ok:
            self.on_message(message)        # ohne Tap: Inventar-Geräte ebenfalls an den Zwischenspeicher
        payload = (message or {}).get("data") or {}
        # Debug-Spam mancher Treiber (LUA_OUTPUT-Konsolenausgabe) nicht in den Event-Puffer
        if "devicecommand" in payload and "LUA_OUTPUT" in json.dumps(payload):
            return
        self.event_count += 1
        self.last_event_at = time.time()
        name = self._device_name(item_id)
        proxy = (self.inv.devices.get(item_id) or self.inv.rooms.get(item_id) or {}).get("proxy")
        dmap = self.drivers.get(proxy) or self.drivers.get("_unknown") or {}
        if proxy == "room" or proxy is None and item_id in self.inv.rooms:
            dmap = self.drivers.get("room") or dmap
        norm = normalize(dmap, payload)
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "id": item_id,
                 "name": name, "driver": proxy, "data": message, "norm": norm}
        self.events.add(entry)
        logbus.info("event", f"{name} (#{item_id}{', ' + proxy if proxy else ''}): "
                    f"{json.dumps(norm or payload, ensure_ascii=False)[:200]}")

    async def _on_connect(self) -> None:
        self.connected_at = time.time()
        self.last_error = ""
        if self.on_reconnect:
            self.on_reconnect()      # verpasste Ereignisse: alles einmal neu lesen
        logbus.info("ws", f"Websocket connected; subscribed to {len(self.subscribed_ids)} items")

    async def _on_disconnect(self) -> None:
        self.connected_at = 0.0      # → Zwischenspeicher fällt auf kurzes Polling zurück
        logbus.warn("ws", "Websocket disconnected")

    def _tap_unrouted(self, c4ws: C4Websocket) -> None:
        """pyControl4 reicht nur Nachrichten für registrierte Items weiter und verwirft den Rest stillschweigend.
        Der Director schickt aber ALLES (UI-Agent, Lautstärke-Geräte, Medien-Dienste …). Wir lesen daher jede
        Nachricht mit: immer für den Push-first-Zwischenspeicher (on_message), zusätzlich bei eingeschaltetem
        Diagnose-Mitschnitt Absender-Statistik + '(unrouted)' im Event-Puffer. Hängt an der internen Methode
        _process_message (fehlt sie nach einem Update → kein Push, Zwischenspeicher pollt wie früher)."""
        orig = getattr(c4ws, "_process_message", None)
        if orig is None:
            self.tap_ok = False
            logbus.warn("ws", "pyControl4 ohne _process_message – Push-first und Diagnose nicht verfügbar")
            return

        async def tapped(message):
            if self.on_message:
                self.on_message(message)     # fängt selbst alle Fehler ab
            if self.discover:
                try:
                    dev = message.get("iddevice") if isinstance(message, dict) else None
                    routed = dev is not None and dev in c4ws.item_callbacks
                    self._record_sender(message, routed)
                    if not routed:
                        self.event_count += 1
                        self.last_event_at = time.time()
                        self.events.add({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "id": dev or 0,
                                         "name": "(unrouted)", "driver": None,
                                         "data": message if isinstance(message, dict) else {"raw": message},
                                         "norm": {}})
                except Exception as e:  # noqa: BLE001  Diagnose darf den Empfang nie stören
                    logbus.warn("ws", f"Diagnose-Mitschnitt: {type(e).__name__}: {e}")
            await orig(message)
        c4ws._process_message = tapped
        self.tap_ok = True

    # ---- subscription management ---------------------------------------------
    def _register_all(self, c4ws: C4Websocket) -> None:
        ids = list(self.inv.devices.keys()) + list(self.inv.rooms.keys())
        for item_id in ids:
            c4ws.add_item_callback(item_id, self._on_event)
        known = set(ids)
        for item_id in getattr(self.inv, "all_ids", []) or []:
            if item_id not in known:
                c4ws.add_item_callback(item_id, self._on_any)
                ids.append(item_id)
        self.subscribed_ids = ids

    async def resubscribe(self) -> int:
        """Re-register callbacks for all current inventory ids (e.g. after a new import)."""
        if not self.c4ws:
            raise RuntimeError("websocket not connected")
        self._register_all(self.c4ws)
        return len(self.subscribed_ids)

    async def restart(self) -> None:
        """Force a reconnect now (fresh token); returns when the loop noticed."""
        self._reconnect_now.set()

    # ---- main loop ------------------------------------------------------------
    async def run(self) -> None:
        # wait for the first inventory import so we can subscribe to all ids
        waited = 0
        while not self.inv.devices:
            await asyncio.sleep(2)
            waited += 2
            if waited >= 120:
                logbus.warn("ws", "no inventory after 120 s - connecting without item callbacks")
                break
        while True:
            try:
                await self.conn.ensure()
                token = self.conn.director.director_bearer_token
                self.c4ws = C4Websocket(
                    self.conn.cfg.c4_ip,
                    session_no_verify_ssl=self.conn.session,
                    connect_callback=self._on_connect,
                    disconnect_callback=self._on_disconnect,
                )
                self._register_all(self.c4ws)
                self._tap_unrouted(self.c4ws)
                await self.c4ws.sio_connect(token)
                # Wait until the 18 h token refresh is due, someone requests a
                # restart, or the connection dies.
                # spätestens, wenn der (evtl. gespeicherte, schon ältere) Token erneuert werden muss
                deadline = min(time.time() + TOKEN_REFRESH_S, self.conn.expires_at)
                while time.time() < deadline:
                    if self._reconnect_now.is_set():
                        self._reconnect_now.clear()
                        logbus.info("ws", "Manual reconnect requested")
                        break
                    await asyncio.sleep(MONITOR_S)
                else:
                    logbus.info("ws", "Token-Erneuerung fällig – neu verbinden")
                # Nur neu anmelden, wenn der Token nicht schon (vom Hintergrundjob) erneuert wurde
                if self.conn.expires_at - time.time() < 600:
                    await self.conn.relogin()
                await self.c4ws.sio_disconnect()
                self.c4ws = None
                continue
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                logbus.error("ws", f"Websocket error: {self.last_error}; retry in 30 s")
                self.c4ws = None
                self._reconnect_now.clear()
                await asyncio.sleep(30)

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.c4ws:
            try:
                await self.c4ws.sio_disconnect()
            except Exception:  # noqa: BLE001
                pass
