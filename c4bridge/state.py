"""Push-first: Zwischenspeicher für Director-Abfragen, den Push-Ereignisse als veraltet markieren.

Nur die Bridge fragt den Director ab – und nur, wenn ein Eintrag veraltet ist: durch ein Ereignis
(WebSocket), nach eigenen Befehlen oder spätestens nach RECONCILE_S (Abgleich). Ohne funktionierenden
Push (WebSocket getrennt / Mitlesen nicht möglich) gilt FALLBACK_S – dann verhält sich alles wie früher
das 5-s-Polling. Gleichzeitige Anfragen mehrerer Browser teilen sich einen Director-Aufruf.

Die Oberfläche bekommt über /api/state/stream (SSE) einen Hinweis, welche Räume/Bereiche sich geändert
haben, und lädt dann sofort neu (aus diesem Zwischenspeicher).

Erkenntnisse aus dem Mitschnitt 16./17.10.2026 (HANDOFF §8.4):
  * UI-Configuration-Agent (Proxy control4_agent_uiconfiguration): active_state_changed {type, room_id, active}
  * Lautstärke vom Gerät aus CURRENT_VOLUME_DEVICE_ID: Denon volume_changed/mute_changed, BluOS-Endpunkt OutputStatus
  * Digital Media / BluOS-Dienste: EVENT QueueChanged/DashboardChanged, teils mit "ROOMS"
  * Raumvariablen selbst kommen nicht als Push → Ereignis = „Raum neu lesen"
"""
import asyncio
import json
import re
import time
from collections import deque
from typing import Awaitable, Callable, Optional

from . import logbus
from .config import DATA_DIR

EP_FILE = DATA_DIR / "endpoints.json"   # zuletzt erkannte Endpunkte je Raum (überstehen einen Neustart)

RECONCILE_S = 60      # spätester Abgleich, wenn Push funktioniert
FALLBACK_S = 4        # ohne Push: wie das alte Polling
SETTLE_S = 1.5        # Director schreibt Raumvariablen teils kurz nach dem Geräte-Ereignis → zweites Neu-Lesen
NOTIFY_DEBOUNCE_S = 0.25
ROOM_ON_S = 5         # Raum an (Hören/Sehen): Quelle kommt nicht als Push → Raumvariablen alle 5 s (wie die App)
PROJECT_S = 24 * 3600  # Projekt-Aufbau (UI-Konfiguration, Szenen-/Button-Listen): nur beim Composer-Signal neu, sonst 1×/Tag
# Composer „Refresh Navigators" (und Projekt-Änderungen): ALLE Räume melden gleichzeitig {"devices_updated": ""}
# (Mitschnitt 17.10.2026 18:37:52 – in ~2 h Alltags-Mitschnitt kein einziges Mal) → Projekt neu einlesen
REFRESH_SETTLE_S = 3   # Signale aller Räume sammeln, dann EINMAL reagieren

# Treiber, die Raumvariablen beobachten, schreiben Änderungen in ihr Debug-Log, z. B. Netflix/Prime:
# „OnWatchedVariableChanged[7/1000]: 2076" (Raum 7, Variable 1000 = CURRENT_SELECTED_DEVICE, 1007 = Pfad).
# Nur ein Zusatz-Hinweis – verschwindet, wenn das Debug-Log der Treiber aus ist.
_WATCHED = re.compile(r"OnWatchedVariableChanged\[(\d+)/(1000|1007)\]")

# Ereignisse ohne Zustandsänderung (Fortschritt im Sekundentakt, Treiber-Debug, Eigenschaften, Vorlieben)
_IGNORE_EVENTS = {"ProgressChanged", "DriverNotification"}
_IGNORE_KEYS = {"user_preference", "icon", "icon_description", "IntercomDeviceChanged", "event"}

# Lautstärke/Stumm aus Ereignissen des Lautstärke-Geräts (Endpunkt). Das Format hängt am Proxy-Typ, nicht an der
# Marke: receiver (Denon, Marantz, Yamaha …) → volume_changed/mute_changed; amplifier/Endpunkt (Control4-Verstärker,
# BluOS-Endpunkt …) → OutputStatus. Weitere Felder je Proxy in data/drivers.json („push" → volume/mute/output).
VOL_FIELDS = {"volume_changed.volume": "volume", "volume_changed.output": "output",
              "mute_changed.mute": "mute", "mute_changed.output": "output",
              "OutputStatus.VolumeLevel": "volume", "OutputStatus.MuteFlag": "mute", "OutputStatus.OutputID": "output"}
# Mehrzonen-Verstärker: das Ereignis nennt den physischen Ausgang (OutputID/output, z. B. 4001), Composer verbindet
# den Raum mit der Raum-Bindung des Geräts (7001). Control4-Konvention, belegt an Denon (7000 ↔ 4000) und
# BluOS-Endpunkt (7001 ↔ 4001): Raum-Bindung = Ausgang + 3000. Jeder Ausgang wird einmal gegengeprüft (Raum nachlesen).
OUTPUT_OFFSET = 3000
# Proxys, deren Ereignisse Lautstärke/Stumm enthalten (s. VOL_FIELDS); weitere über drivers.json „push"
PUSH_PROXIES = {"receiver", "amplifier"}
# Pfad-XML der Raumvariablen: <device><id>…</id><provider_binding_id>…</provider_binding_id><consumer_binding_id>…
_PATH = re.compile(r"<id>(\d+)</id>\s*<provider_binding_id>(\d+)</provider_binding_id>\s*"
                   r"<consumer_binding_id>(\d+)</consumer_binding_id>")


def parse_path(xml) -> list:
    return [{"id": int(a), "out": int(b), "in": int(c)} for a, b, c in _PATH.findall(str(xml or ""))]


def _dig(obj, dotted):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def _short(data: dict) -> str:
    ev = data.get("EVENT")
    if isinstance(ev, dict):
        return str(ev.get("NAME") or "EVENT")
    return ", ".join(list(data.keys())[:3])


# Gerätetyp aus dem Inventar → globaler Schlüssel
TYPE_KEY = {"light": "lights", "thermostat": "climate", "blind": "blinds", "contact": "contacts", "motion": "contacts",
            "switch": "switches", "learned": "learned"}


class StateCache:
    def __init__(self, inv, push_ok: Callable[[], bool]):
        self.inv = inv
        self.push_ok = push_ok
        self._val: dict = {}          # key → (ts, value)
        self._dirty: set = set()
        self._dirty_rooms: dict = {}  # hausweite Schlüssel (lights, climate …): veraltet nur für diese Räume
        self._inflight: dict = {}     # key → asyncio.Task
        self._subs: set = set()
        self._pending: dict = {"rooms": set(), "keys": set(), "devs": {}, "all": False}
        self._notify_task: Optional[asyncio.Task] = None
        self._last_payload: dict = {}  # Gerät → letzte Nutzlast (Wiederholungen wie TV powerstate alle 5 s ignorieren)
        self.dev_rooms: dict = {}      # Lautstärke-/Quellgerät → Räume (aus den Raumvariablen gelernt)
        self._item_room: dict = {}     # unbekannte Geräte → Raum (einmal nachgeschlagen)
        self.items: dict = {}          # Gerät → {name, proxy, room} (nachgeschlagen, für Auswertung + Admin-Ansicht)
        self.ep: dict = {}             # Raum → Endpunkte: Quelle, Lautstärke-Gerät, Audio-/Video-Pfad (Raumvariablen)
        self.vol_stats: dict = {}      # Raum → letzte Lautstärke aus Ereignis {dev, volume, muted, at, mode}
        self.room_log: dict = {}       # Raum → die letzten Ereignisse (Admin: „was kommt als Push")
        self.drivers: dict = {}        # data/drivers.json (von app.py gesetzt)
        self.ui_agent: Optional[int] = None
        self.outputs: dict = {}        # Mehrzonen-Gerät → {Ausgang: Raum} (aus den Composer-Bindungen abgeleitet)
        self.out_check: dict = {}      # "Gerät:Ausgang" → {state: ungeprüft|geprüft|falsch, ok, bad} (gespeichert)
        self._vol_last: dict = {}      # "Gerät:Ausgang" → (Lautstärke, Zeit) des letzten Ereignisses
        self._verifying: set = set()
        self.read_volume: Optional[Callable[[int], Awaitable]] = None   # von app.py: Raum → CURRENT_VOLUME (Director)
        self.agents: dict = {}         # Agenten-Item-ID → Schlüssel (adv_lighting, custom_buttons) – app._apply_meta
        self.on_project_refresh: Optional[Callable[[], Awaitable]] = None   # von app.py: Projekt neu einlesen
        self._refresh_task: Optional[asyncio.Task] = None
        # Treiber-Eigenschaften als Push (UPDATE_PROPERTY): von app.py – KODI-Haupttreiber → kodi.on_property
        self.prop_hooks: Optional[Callable[[int, str, object], None]] = None
        self.prop_devs: Callable[[], set] = set
        self.project_refreshed_at = 0.0
        self._load_ep()
        self.stats = {"hits": 0, "loads": 0, "events": 0, "ignored": 0, "invalidations": 0}

    # ---- Zwischenspeicher ------------------------------------------------------------------
    def ttl(self) -> float:
        return RECONCILE_S if self.push_ok() else FALLBACK_S

    async def get(self, key: str, loader: Callable[[], Awaitable], ttl: Optional[float] = None,
                  room: Optional[int] = None, project: bool = False):
        """room: Anfrage betrifft nur diesen Raum – Änderungen in anderen Räumen lösen kein Neu-Lesen aus.
        project: Projekt-Aufbau – mit Push gilt PROJECT_S (neu nur beim Composer-Signal), ohne Push wie sonst."""
        hit = self._val.get(key)
        if project and self.push_ok():
            limit = PROJECT_S
        else:
            limit = self.ttl() if ttl is None else min(ttl, self.ttl())
        rooms = self._dirty_rooms.get(key)
        stale = key in self._dirty or bool(rooms) and (room is None or room in rooms)
        if hit and not stale and time.time() - hit[0] < limit:
            self.stats["hits"] += 1
            return hit[1]
        task = self._inflight.get(key)
        if task is None:
            task = self._inflight[key] = asyncio.ensure_future(self._load(key, loader))
        return await asyncio.shield(task)

    async def _load(self, key, loader):
        self._dirty.discard(key)          # vor dem Laden: ein Ereignis währenddessen markiert erneut
        self._dirty_rooms.pop(key, None)
        try:
            v = await loader()
            self.stats["loads"] += 1
            self._val[key] = (time.time(), v)
            return v
        finally:
            self._inflight.pop(key, None)

    def peek(self, key: str):
        hit = self._val.get(key)
        return hit[1] if hit else None

    def invalidate(self, *keys: str) -> None:
        self._dirty.update(keys)
        self.stats["invalidations"] += 1

    def invalidate_room(self, key: str, room: Optional[int]) -> None:
        if room is None:
            self.invalidate(key)
        else:
            self._dirty_rooms.setdefault(key, set()).add(room)
            self.stats["invalidations"] += 1

    def invalidate_all(self) -> None:
        self._dirty.update(self._val.keys())
        self.stats["invalidations"] += 1

    # ---- Raum-Zuordnung ------------------------------------------------------------------
    def learn_room(self, room_id: int, *device_ids) -> None:
        """Aus den Raumvariablen: welches Gerät liefert Lautstärke/Quelle für diesen Raum."""
        for d in device_ids:
            if d:
                self.dev_rooms.setdefault(int(d), set()).add(room_id)

    def learn_room_vars(self, room_id: int, v: dict) -> None:
        """Endpunkt-Erkennung aus den Raumvariablen (ohne Markenwissen): Lautstärke-Gerät = CURRENT_VOLUME_DEVICE_ID
        (= Endpunkt des Raums), Quelle = CURRENT_SELECTED_DEVICE, Pfade = Geräte-Kette mit Bindungen. Ist der Raum
        aus, bleiben die zuletzt bekannten Endpunkte stehen (markiert als nicht aktiv)."""
        num = lambda x: int(x) if str(x).isdigit() else 0  # noqa: E731
        sel, vol = num(v.get("CURRENT_SELECTED_DEVICE")), num(v.get("CURRENT_VOLUME_DEVICE_ID"))
        prev = self.ep.get(room_id) or {}
        # Lautstärke-Gerät: zuerst laut Composer (Audio-/Video-Volume-Bindung); passt der Live-Wert zu einer der beiden
        # Bindungen, gilt er (Hören vs. Sehen) – Live allein nur als Rückfall, wenn der Raum keine Bindung hat.
        st = prev.get("static") or {}
        bound = [x["id"] for x in st.get("volume", []) + st.get("video_volume", [])]
        vol = vol if (not bound or vol in bound) else bound[0]
        ep = {"on": bool(sel), "at": time.time(), "source": sel or prev.get("source", 0),
              "volume_dev": vol or prev.get("volume_dev", 0), "static": st, "volume_from": "composer" if bound else "live",
              "audio_path": parse_path(v.get("CURRENT_AUDIO_PATH")) or ([] if sel else prev.get("audio_path", [])),
              "video_path": parse_path(v.get("CURRENT_VIDEO_PATH")) or ([] if sel else prev.get("video_path", [])),
              "video_audio_path": parse_path(v.get("CURRENT_VIDEO_AUDIO_PATH"))
              or ([] if sel else prev.get("video_audio_path", []))}
        changed = {k: ep[k] for k in ep if k not in ("at", "on", "static")} != {k: prev.get(k) for k in ep if k not in ("at", "on")}
        self.ep[room_id] = ep
        if changed:
            self._save_ep()
        ids = {sel, vol, num(v.get("PLAYING_AUDIO_DEVICE"))}
        for p in ("audio_path", "video_path", "video_audio_path"):
            ids |= {x["id"] for x in ep[p] or []}
        self.learn_room(room_id, *ids)
        for i in ids:                                  # Name/Proxy einmal nachschlagen (Auswertung + Admin)
            if i and i not in self.items and i not in self.inv.rooms:
                self.items[i] = {}
                asyncio.ensure_future(self._lookup_item(i))

    # Raum-Bindungen aus Composer (statisch, auch bei ausgeschaltetem Raum): bindingClass → Bedeutung
    STATIC_CLASSES = {"AUDIO_VOLUME": "volume", "AUDIO_SELECTION": "audio_endpoint", "VIDEO_SELECTION": "video_endpoint"}

    def learn_static(self, room_id: int, bindings: list) -> None:
        """Endpunkte laut Composer (GET /items/{raum}/bindings): Audio-Lautstärke, Audio-/Video-Endpunkt. Grundlage für
        die Erkennung schon bei der Erstinstallation, ohne dass ein Raum eingeschaltet sein muss."""
        st: dict = {}
        for b in bindings if isinstance(bindings, list) else []:
            kind = self.STATIC_CLASSES.get(b.get("bindingClass"))
            if not kind:
                continue
            name = str(b.get("bindingName") or "")
            if kind == "volume" and name.lower().startswith("video"):
                kind = "video_volume"
            if kind == "audio_endpoint" and "video" in name.lower():
                kind = "video_audio_endpoint"
            for c in b.get("connections") or []:
                if isinstance(c.get("id"), int):
                    st.setdefault(kind, [])
                    e = {"id": c["id"], "name": c.get("name"), "binding": c.get("bindingId")}
                    if e not in st[kind]:
                        st[kind].append(e)
                    if c["id"] not in self.items or not (self.items.get(c["id"]) or {}).get("name"):
                        self.items[c["id"]] = {**(self.items.get(c["id"]) or {}), "name": c.get("name")}
        e = self.ep.setdefault(room_id, {"on": False})
        if e.get("static") != st:
            e["static"] = st
            if st.get("volume"):                      # Composer legt fest; Live-Wert nur ohne Bindung
                e["volume_dev"] = st["volume"][0]["id"]
                e["volume_from"] = "composer"
            self.learn_room(room_id, *[x["id"] for v in st.values() for x in v])
            self._build_outputs()
            self._save_ep()

    def _build_outputs(self) -> None:
        """Mehrzonen: Gerät, das laut Composer Lautstärke-Gerät mehrerer Räume ist → Ausgang je Raum-Bindung. Teilen sich
        zwei Räume dieselbe Bindung, bleibt der Ausgang ohne Zuordnung (→ nachlesen)."""
        by_dev: dict = {}
        for rid, e in self.ep.items():
            for x in (e.get("static") or {}).get("volume", []):
                if isinstance(x.get("binding"), int):
                    by_dev.setdefault(x["id"], {}).setdefault(x["binding"], set()).add(rid)
        out: dict = {}
        for dev, binds in by_dev.items():
            if sum(len(r) for r in binds.values()) < 2:
                continue                               # nur ein Raum → ohnehin direkt
            for b, rooms in binds.items():
                if len(rooms) == 1:
                    out.setdefault(dev, {})[b - OUTPUT_OFFSET] = next(iter(rooms))
        self.outputs = out

    def _load_ep(self) -> None:
        try:
            raw = json.loads(EP_FILE.read_text(encoding="utf-8"))
            self.ui_agent = raw.pop("_ui_agent", None)
            self.out_check = raw.pop("_out_check", None) or {}
            for k, e in raw.items():
                e["on"] = False                        # Zustand ist unbekannt, bis der Raum gelesen wurde
                self.ep[int(k)] = e
                ids = {e.get("source"), e.get("volume_dev")}
                for p in ("audio_path", "video_path", "video_audio_path"):
                    ids |= {x["id"] for x in e.get(p) or []}
                self.learn_room(int(k), *ids)
            self._build_outputs()
        except Exception:  # noqa: BLE001  fehlt/kaputt → neu lernen
            pass

    def _save_ep(self) -> None:
        try:
            from .auth import atomic_write
            atomic_write(EP_FILE, json.dumps({"_ui_agent": self.ui_agent, "_out_check": self.out_check,
                                             **{str(k): v for k, v in self.ep.items()}},
                                             ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            logbus.warn("state", f"endpoints.json nicht gespeichert: {e}")

    # ---- Lautstärke direkt aus dem Ereignis --------------------------------------------------
    def _volume_fields(self, dev: int, data: dict) -> dict:
        proxy = (self.items.get(dev) or {}).get("proxy")
        fields = dict(VOL_FIELDS)
        for f, meaning in ((self.drivers.get(proxy) or {}).get("push") or {}).items():
            if meaning in ("volume", "mute", "output"):
                fields[f] = meaning
        out = {}
        for f, meaning in fields.items():
            x = _dig(data, f)
            if x is not None:
                out[meaning] = x
        vol = out.get("volume")
        if vol is not None and not (isinstance(vol, (int, float)) and 0 <= vol <= 100):
            out.pop("volume")                          # z. B. -1 = „unverändert"
        return out if ("volume" in out or "mute" in out) else {}

    def _apply_volume(self, rid: int, dev: int, vals: dict, mode: str = "direkt") -> None:
        """Wert ins Zwischengespeicherte schreiben (Raumvariablen + BluOS-Status) – kein Director-Abruf."""
        hit = self._val.get(f"room:{rid}")
        if hit and isinstance(hit[1], list):
            rows = []
            for row in hit[1]:
                if isinstance(row, dict) and row.get("varName") == "CURRENT_VOLUME" and "volume" in vals:
                    row = {**row, "value": int(vals["volume"])}
                elif isinstance(row, dict) and row.get("varName") == "IS_MUTED" and "mute" in vals:
                    row = {**row, "value": 1 if vals["mute"] else 0}
                rows.append(row)
            self._val[f"room:{rid}"] = (hit[0], rows)
        b = self._val.get(f"bluos:{rid}")
        if b and isinstance(b[1], dict):
            st = dict(b[1])
            if "volume" in vals:
                st["volume"] = int(vals["volume"])
            if "mute" in vals:
                st["mute"] = bool(vals["mute"])
            self._val[f"bluos:{rid}"] = (b[0], st)
        s = self.vol_stats.setdefault(rid, {})
        s.update({"dev": dev, "at": time.time(), "mode": mode, "events": s.get("events", 0) + 1})
        if "volume" in vals:
            s["volume"] = int(vals["volume"])
        if "mute" in vals:
            s["muted"] = bool(vals["mute"])
        self._queue_notify(rooms={rid})

    def _log(self, rid: int, dev: int, what: str) -> None:
        self.room_log.setdefault(rid, deque(maxlen=12)).appendleft({"t": time.time(), "dev": dev, "what": what[:120]})

    def room_keys(self, rid: int) -> list:
        return [f"room:{rid}", f"bluos:{rid}"]

    def dirty_room(self, rid: int, settle: bool = True) -> None:
        self.invalidate(*self.room_keys(rid), "power")
        # Abspielstatus hängt am Quellgerät des Raums
        for dev, rooms in self.dev_rooms.items():
            if rid in rooms:
                self.invalidate(f"queue:{dev}")
        self._queue_notify(rooms={rid}, keys={"power"})
        if settle:
            asyncio.get_event_loop().call_later(SETTLE_S, self.dirty_room, rid, False)

    # ---- Ereignisse -------------------------------------------------------------------------
    def on_message(self, message) -> None:
        """Jede Nachricht des Directors (auch die, die pyControl4 verwirft). Darf nie werfen."""
        try:
            self._on_message(message)
        except Exception as e:  # noqa: BLE001
            logbus.warn("state", f"Ereignis nicht ausgewertet: {type(e).__name__}: {e}")

    def _on_message(self, message) -> None:
        if not isinstance(message, dict):
            return
        dev = message.get("iddevice")
        data = message.get("data")
        if not isinstance(dev, int) or not isinstance(data, dict):
            return
        if "devicecommand" in data:                    # LUA_OUTPUT, UPDATE_PROPERTY: Treiber-Interna
            if self.prop_hooks and dev in self.prop_devs():
                dc = data["devicecommand"] if isinstance(data["devicecommand"], dict) else {}
                if dc.get("command") == "UPDATE_PROPERTY":
                    ps = (dc.get("params") or {}).get("param") or []
                    kv = {x.get("name"): (x.get("value") or {}).get("static") for x in ps if isinstance(x, dict)}
                    self.stats["events"] += 1
                    self.prop_hooks(dev, kv.get("Name"), kv.get("Value"))
                    return
            m = _WATCHED.search(json.dumps(data)) if "OnWatchedVariableChanged" in json.dumps(data) else None
            if m and int(m.group(1)) in self.inv.rooms:
                self.stats["events"] += 1
                self.dirty_room(int(m.group(1)), settle=False)
                return
            self.stats["ignored"] += 1
            return
        ev = data.get("EVENT")
        if isinstance(ev, dict) and ev.get("NAME") in _IGNORE_EVENTS:
            self.stats["ignored"] += 1
            return
        d = self.inv.devices.get(dev)
        # Symbole/Vorlieben sind normalerweise Rauschen – außer bei Schaltern (Chowmain-Outlet meldet den Zustand
        # NUR als icon_description „On"/„Off")
        if "RESPONSE" in data or (not (set(data) - _IGNORE_KEYS) and not (d and d.get("type") == "switch")):
            self.stats["ignored"] += 1
            return
        # Composer „Refresh Navigators": VOR der Wiederholungs-Sperre – die Nachricht ist bei jedem Refresh identisch
        # und würde sonst ab dem zweiten Mal verworfen
        if dev in self.inv.rooms and "devices_updated" in data:
            self.stats["events"] += 1
            self._project_signal()
            return
        sig = json.dumps(data, sort_keys=True, default=str)
        if self._last_payload.get(dev) == sig:         # Wiederholung ohne Änderung
            self.stats["ignored"] += 1
            return
        self._last_payload[dev] = sig
        self.stats["events"] += 1

        # 1) UI-Configuration-Agent: Raum an/aus (listen/watch/lights)
        asc = data.get("active_state_changed")
        if isinstance(asc, dict) and asc.get("room_id") is not None:
            if self.ui_agent != dev:
                self.ui_agent = dev
                self._save_ep()
            rid = int(asc["room_id"])
            self._log(rid, dev, ("Raum an " if asc.get("active") else "Raum aus ") + str(asc.get("type") or ""))
            if rid in self.ep and asc.get("type") in ("listen", "watch"):
                self.ep[rid]["on"] = bool(asc.get("active"))   # an: Lautstärke ab sofort direkt (Endpunkt bekannt)
            self.dirty_room(rid)
            if asc.get("type") == "lights":
                self.invalidate("lights")
                self._queue_notify(keys={"lights"})
            return

        # 1b) Lichtszenen-Agent: Szene (de)aktiviert → Szenenstatus neu lesen (alle Räume)
        if self.agents.get(dev) == "adv_lighting":
            self.invalidate("scenes")
            self._queue_notify(keys={"scenes"})
            return

        # 2) Geräte aus dem Inventar (Licht, Heizung, Rollläden, Kontakte, Schalter, Räume)
        if d:
            key = TYPE_KEY.get(d.get("type"))
            if key == "lights":
                self.invalidate("scenes")       # „Szene aktiv" hängt am Stand der Lichter
            if key:
                self.invalidate_room(key, d.get("room_id"))
                # mit Raum: die Oberfläche lädt nur neu, wenn sie diesen Raum gerade zeigt
                self._queue_notify(devs={key: {d.get("room_id")}})
                return
        if dev in self.inv.rooms:
            self.dirty_room(dev)
            return

        # 3) Endpunkt eines Raums meldet Lautstärke/Stumm → direkt übernehmen, wenn genau ein (aktiver) Raum dieses
        #    Gerät als Lautstärke-Gerät nutzt; sonst (Mehrzonen ohne Zuordnung) Raum neu lesen.
        vals = self._volume_fields(dev, data)
        if vals:
            users = [r for r, e in self.ep.items() if e.get("volume_dev") == dev and e.get("on")]
            shared = [r for r, e in self.ep.items()
                      if dev in {x["id"] for x in (e.get("static") or {}).get("volume", [])}]
            if len(users) == 1 and len(shared) <= 1:
                self._log(users[0], dev, "Lautstärke " + ", ".join(f"{k} {v}" for k, v in vals.items()))
                self._apply_volume(users[0], dev, vals)
                return
            if self._zone_volume(dev, vals):
                return
            for r in users:
                self.vol_stats.setdefault(r, {}).update({"dev": dev, "at": time.time(), "mode": "nachlesen"})

        # 4) Raum direkt im Ereignis (Digital Media: "ROOMS": 7 bzw. "7,8")
        rooms = set()
        if isinstance(ev, dict) and ev.get("ROOMS") not in (None, ""):
            for x in str(ev["ROOMS"]).split(","):
                if x.strip().isdigit():
                    rooms.add(int(x))
        # 5) Geräte aus Pfad/Quelle/Lautstärke eines Raums (gelernt aus den Raumvariablen)
        rooms |= self.dev_rooms.get(dev, set())
        self.invalidate(f"queue:{dev}")
        if not rooms and not d:
            rid = self._item_room.get(dev)
            if rid is None and dev not in self._item_room:
                self._item_room[dev] = 0       # einmal nachschlagen (Hintergrund), bis dahin nichts
                asyncio.ensure_future(self._lookup_room(dev))
            elif rid:
                rooms.add(rid)
        elif d and d.get("room_id") and not TYPE_KEY.get(d.get("type")):
            rooms.add(int(d["room_id"]))
        for rid in rooms:
            if rid in self.inv.rooms:
                self._log(rid, dev, _short(data))
                self.dirty_room(rid)

    # ---- Mehrzonen-Verstärker: Ausgang → Raum ------------------------------------------------
    def _zone_volume(self, dev: int, vals: dict) -> bool:
        """Ereignis eines Mehrzonen-Geräts mit Ausgang: Wert direkt in den Raum dieses Ausgangs. Solange der Ausgang
        nicht geprüft ist, wird der Raum zusätzlich nachgelesen und verglichen; passt es zweimal nicht → „falsch",
        danach für diesen Ausgang wieder nachlesen wie bisher."""
        try:
            out = int(vals.get("output"))
        except (TypeError, ValueError):
            return False
        rid = (self.outputs.get(dev) or {}).get(out)
        key = f"{dev}:{out}"
        chk = self.out_check.get(key) or {}
        if rid is None or chk.get("state") == "falsch":
            return False
        if "volume" in vals:
            self._vol_last[key] = (int(vals["volume"]), time.time())
        state = chk.get("state", "ungeprüft")
        self._log(rid, dev, f"Ausgang {out}: " + ", ".join(f"{k} {v}" for k, v in vals.items() if k != "output"))
        self._apply_volume(rid, dev, vals, mode=f"direkt (Ausgang {out}, {state})")
        if state != "geprüft":
            self.dirty_room(rid)
            if "volume" in vals and (self.ep.get(rid) or {}).get("on") and self.read_volume \
                    and key not in self._verifying:
                self._verifying.add(key)
                asyncio.ensure_future(self._verify(rid, dev, out, key))
        return True

    async def _verify(self, rid: int, dev: int, out: int, key: str) -> None:
        try:
            for _ in range(5):                         # warten, bis am Regler nicht mehr gezogen wird
                await asyncio.sleep(SETTLE_S + 0.5)
                if time.time() - self._vol_last.get(key, (0, 0))[1] > SETTLE_S:
                    break
            want = self._vol_last.get(key, (None, 0))[0]
            got = await self.read_volume(rid)
            if want is None or got is None:
                return
            c = self.out_check.setdefault(key, {"ok": 0, "bad": 0})
            c["ok" if abs(int(got) - int(want)) <= 1 else "bad"] += 1
            old = c.get("state", "ungeprüft")
            c["state"] = "falsch" if c["bad"] >= 2 else "geprüft" if c["ok"] > c["bad"] else "ungeprüft"
            c["room"], c["at"] = rid, time.time()
            if c["state"] != old:
                name = (self.inv.rooms.get(rid) or {}).get("name", rid)
                logbus.info("state", f"Mehrzonen: Gerät {dev} Ausgang {out} -> {name}: {c['state']} "
                                     f"(Ereignis {want}, Raum {got})")
            self._save_ep()
        except Exception as e:  # noqa: BLE001
            logbus.warn("state", f"Ausgang {key} nicht geprüft: {type(e).__name__}: {e}")
        finally:
            self._verifying.discard(key)

    def volume_mode(self, rid: int) -> dict:
        """Wie kommt die Lautstärke dieses Raums in die Oberfläche? direkt (Ereignis) | nachlesen (Ereignis → Raum neu
        lesen) | polling (Gerät meldet nichts / Push aus) | none (kein Lautstärke-Gerät)."""
        ep = self.ep.get(rid) or {}
        vd = ep.get("volume_dev")
        if not vd:
            return {"kind": "none"}
        info = self.items.get(vd) or {}
        proxy = (self.inv.devices.get(vd) or {}).get("proxy") or info.get("proxy")
        base = {"dev": vd, "name": (self.inv.devices.get(vd) or {}).get("name") or info.get("name"), "proxy": proxy}
        if not self.push_ok():
            return {**base, "kind": "polling", "why": "Push aus"}
        pushes = proxy in PUSH_PROXIES or any(m in ("volume", "mute") for m in
                                              ((self.drivers.get(proxy) or {}).get("push") or {}).values()) \
            or any(s.get("dev") == vd and s.get("events") for s in self.vol_stats.values())
        if not pushes:
            return {**base, "kind": "polling", "why": "Gerät meldet keine Lautstärke" if proxy else "Gerät unbekannt"}
        users = {r for r, e in self.ep.items() if e.get("volume_dev") == vd
                 or vd in {x["id"] for x in (e.get("static") or {}).get("volume", [])}} | {rid}
        if len(users) == 1:
            return {**base, "kind": "direkt"}
        out = next((o for o, r in (self.outputs.get(vd) or {}).items() if r == rid), None)
        shared = sorted(users - {rid})
        if out is None:
            return {**base, "kind": "nachlesen", "shared_with": shared, "why": "Ausgang mit anderem Raum geteilt"}
        st = (self.out_check.get(f"{vd}:{out}") or {}).get("state", "ungeprüft")
        if st == "falsch":
            return {**base, "kind": "nachlesen", "shared_with": shared, "output": out,
                    "why": f"Ausgang {out} passte nicht zum Raum"}
        return {**base, "kind": "direkt", "shared_with": shared, "output": out, "check": st}

    # ---- Composer „Refresh Navigators" ------------------------------------------------------
    def _project_signal(self) -> None:
        """devices_updated kommt von allen Räumen fast gleichzeitig → einmal (nach kurzer Sammelzeit) reagieren."""
        if self._refresh_task and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.ensure_future(self._project_refresh())

    async def _project_refresh(self) -> None:
        await asyncio.sleep(REFRESH_SETTLE_S)
        logbus.info("state", "Composer-Signal (devices_updated): Projekt wird neu eingelesen")
        for k in [k for k in self._val if k in ("uicfg", "scenes", "power") or k.startswith(("room:", "bluos:"))]:
            self._dirty.add(k)
        try:
            if self.on_project_refresh:
                await self.on_project_refresh()
        except Exception as e:  # noqa: BLE001
            logbus.warn("state", f"Projekt neu einlesen fehlgeschlagen: {type(e).__name__}: {e}")
        self.project_refreshed_at = time.time()
        self._queue_notify(keys={"project"}, all_=True)   # Oberflächen laden Raum, Szenen, Buttons neu

    lookup: Optional[Callable[[int], Awaitable]] = None   # von app.py gesetzt: Item → {roomId, name, proxy}

    async def _lookup_item(self, dev: int) -> dict:
        info = {}
        if self.lookup:
            try:
                info = await self.lookup(dev) or {}
            except Exception:  # noqa: BLE001
                info = {}
        self.items[dev] = {"name": info.get("name"), "proxy": info.get("proxy"), "room": info.get("roomId")}
        return self.items[dev]

    async def _lookup_room(self, dev: int) -> None:
        rid = (await self._lookup_item(dev)).get("room")
        self._item_room[dev] = int(rid) if rid else 0
        if rid and int(rid) in self.inv.rooms:
            self.dirty_room(int(rid))

    async def describe(self, rid: int) -> dict:
        """Admin-Ansicht „Push-Quellen" eines Raums: Endpunkte mit Namen/Proxy, Art der Lautstärke-Übernahme,
        letzte Ereignisse."""
        ep = self.ep.get(rid) or {}
        ids = {ep.get("source"), ep.get("volume_dev"), self.ui_agent}
        for k in ("audio_path", "video_path", "video_audio_path"):
            ids |= {x["id"] for x in ep.get(k) or []}
        ids |= {e["dev"] for e in self.room_log.get(rid, [])}
        await asyncio.gather(*(self._lookup_item(i) for i in ids
                               if i and i not in self.inv.devices and not (self.items.get(i) or {}).get("proxy")))

        def info(i):
            if not i:
                return None
            d = self.inv.devices.get(i) or {}
            it = self.items.get(i) or {}
            return {"id": i, "name": d.get("name") or it.get("name") or "?", "proxy": d.get("proxy") or it.get("proxy")}

        vd = ep.get("volume_dev")
        vol = None
        if vd:
            vm = self.volume_mode(rid)
            vol = {**info(vd), **(self.vol_stats.get(rid) or {}), "shared_with": vm.get("shared_with", []),
                   "mode": vm["kind"], "output": vm.get("output"), "check": vm.get("check"), "why": vm.get("why")}
        return {
            "known": bool(ep), "on": ep.get("on", False), "read_at": ep.get("at"),
            "source": info(ep.get("source")), "volume": vol,
            "paths": {k: [{**info(x["id"]), "in": x["in"], "out": x["out"]} for x in ep.get(k) or []]
                      for k in ("audio_path", "video_path", "video_audio_path")},
            "static": {k: [{**info(x["id"]), "binding": x.get("binding")} for x in v]
                       for k, v in (ep.get("static") or {}).items()},
            "ui_agent": info(self.ui_agent), "push": self.push_ok(),
            "log": [{**e, "name": (info(e["dev"]) or {}).get("name")} for e in self.room_log.get(rid, [])],
        }

    def on_reconnect(self) -> None:
        self.invalidate_all()
        self._queue_notify(all_=True)

    # ---- Hinweise an die Oberfläche (SSE) ----------------------------------------------------
    def _queue_notify(self, rooms=(), keys=(), all_=False, devs=None) -> None:
        p = self._pending
        p["rooms"].update(rooms)
        p["keys"].update(keys)
        for k, rs in (devs or {}).items():
            p["devs"].setdefault(k, set()).update(r for r in rs if r)
        p["all"] = p["all"] or all_
        if not self._notify_task or self._notify_task.done():
            self._notify_task = asyncio.ensure_future(self._flush())

    async def _flush(self) -> None:
        await asyncio.sleep(NOTIFY_DEBOUNCE_S)
        p, self._pending = self._pending, {"rooms": set(), "keys": set(), "devs": {}, "all": False}
        msg = json.dumps({"rooms": sorted(p["rooms"]), "keys": sorted(p["keys"]), "all": p["all"],
                          "devs": {k: sorted(v) for k, v in p["devs"].items()}})
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def stream(self):
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs.add(q)
        try:
            yield f"data: {json.dumps({'hello': True, 'push': self.push_ok()})}\n\n"
            while True:
                try:
                    yield f"data: {await asyncio.wait_for(q.get(), timeout=15)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._subs.discard(q)

    def status(self) -> dict:
        return {**self.stats, "push": self.push_ok(), "ttl_s": self.ttl(), "keys": len(self._val),
                "project_refreshed_at": self.project_refreshed_at,
                "dirty": len(self._dirty), "ui_agent": self.ui_agent, "subscribers": len(self._subs),
                "dev_rooms": {str(k): sorted(v) for k, v in self.dev_rooms.items()}}
