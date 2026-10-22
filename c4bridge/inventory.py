"""Rooms and devices imported from Control4, plus manual annotations.

Imported automatically (read-only in the UI): id, name, room, type, driver
flag. Maintained by hand (stored in data/overrides.json): note, aliases,
enabled, function. The note ("Deckenleuchte Esszimmer") is what lets a
language model pick the right lamp.
"""
import json
import time
from typing import Optional

from . import logbus
from .config import DATA_DIR
from .connection import Connection

OVERRIDES = DATA_DIR / "overrides.json"
SNAPSHOT = DATA_DIR / "inventory.json"

# proxy name -> our device type
TYPE_BY_PROXY = {
    "light_v2": "light", "axxess_dimmer": "light",
    "blind": "blind",
    "thermostatV2": "thermostat",
    # Sicherheit: Tür-/Fensterkontakte (single-state) und Bewegungsmelder
    "contactsingle_windowcontactsensor_c4": "contact",
    "contactsingle_doorcontactsensor_c4": "contact",
    "contactsingle_motionsensor": "motion",
    "contactsingle_motionsensor_c4": "motion",
    "axxess_motionsensor": "motion",
    "DoorWindow_zb_nyce_3011": "contact",
    # Kameras (Doorstation-Kameras; die Doorstation-Items selbst sind Duplikate)
    "camera": "camera",
    # Schalter: Control4-Relais (Pumpe, Lüfter, Relais – Proxy relaysingle_*: RelayState, OPEN/CLOSE/TOGGLE)
    # und WeMo-Steckdosen (alter Chowmain-Treiber: eigener Proxy, nur „Turn WeMo On/Off", kein Zustand)
    "relaysingle_relay_c4": "switch", "relaysingle_pump_c4": "switch", "relaysingle_fan_c4": "switch",
    "DriverWorks_Chowmain_Belkin_WeMo": "switch",
}
# Ausnahme von „Proxy = Verhalten": Treiber mit dem Allerwelts-Proxy uibutton, die in Wahrheit ein Gerät sind –
# erkannt am Treiber (protocolFilename/filename ohne Endung). Chowmain Outlet (WeMo neu): OUTLET_ON, On/Off.
TYPE_BY_DRIVER = {"driverworks_chowmain_outlet": "switch"}
# Schalter-Art (bestimmt Befehle/Variablen): relay | outlet | wemo
SWITCH_KIND = {"relaysingle_relay_c4": "relay", "relaysingle_pump_c4": "relay", "relaysingle_fan_c4": "relay",
               "DriverWorks_Chowmain_Belkin_WeMo": "wemo", "driverworks_chowmain_outlet": "outlet"}
FUNCTIONS = ("unknown", "on_off", "dimmable", "up_down")

# Systemgeräte/Agenten, die die Bridge braucht – ebenfalls über den Proxy erkannt (nie über IDs oder Namen):
#   digital_media = Control4 „Digital Media" (Warteschlangen/Album-Befehle der Räume, meist Item 100002)
#   ui_agent      = UI-Configuration-Agent (Push „Raum an/aus")
#   adv_lighting  = Lichtszenen (Advanced Lighting), custom_buttons = Custom-Buttons-Agent
META_BY_PROXY = {"control4_digitalaudio": "digital_media", "control4_agent_uiconfiguration": "ui_agent",
                 "control4_agent_adv_lighting": "adv_lighting", "control4_agent_custombuttons": "custom_buttons"}


def _load(path) -> dict:
    """JSON lesen – UTF-8, zur Not die alte System-Kodierung (Dateien von vor dem Umstieg auf UTF-8)."""
    for enc in ("utf-8", None):
        try:
            return json.loads(path.read_text(encoding=enc))
        except UnicodeDecodeError:
            continue
        except Exception:  # noqa: BLE001
            return {}
    return {}


class Inventory:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        snap = _load(SNAPSHOT)
        self.rooms: dict = {int(k): v for k, v in snap.get("rooms", {}).items()}
        self.devices: dict = {int(k): v for k, v in snap.get("devices", {}).items()}
        self.imported_at: float = snap.get("imported_at", 0)
        self.meta: dict = snap.get("meta", {})          # digital_media, ui_agent (Item-IDs, s. META_BY_PROXY)
        self.all_ids: list = snap.get("all_ids", [])    # ALLE Item-IDs des Projekts (Push-Abo auf alles, s. ws.py)
        self.overrides: dict = _load(OVERRIDES) or {"rooms": {}, "devices": {}}
        self.overrides.setdefault("rooms", {})
        self.overrides.setdefault("devices", {})

    # ---- import -------------------------------------------------------
    async def refresh(self, conn: Connection) -> None:
        items = await conn.get("/api/v1/items")
        by_parent: dict = {}
        for i in items:
            by_parent.setdefault(i.get("parentId"), []).append(i)

        rooms = {}
        for i in items:
            if i.get("typeName") == "room":
                rooms[i["id"]] = {
                    "id": i["id"], "name": i["name"], "floor": i.get("floorName", ""),
                    "floor_id": i.get("floorId", 0), "order": i.get("roomOrder", 0),
                    # Composer-Flags: in der Control4-App nicht sichtbare Räume
                    "hidden": bool(i.get("roomHidden") or i.get("tempHidden")),
                }

        meta = {}
        for i in items:
            key = META_BY_PROXY.get(i.get("proxy"))
            if key and key not in meta and isinstance(i.get("id"), int):
                meta[key] = i["id"]

        devices = {}
        for i in items:
            drv = str(i.get("protocolFilename") or i.get("filename") or "").rsplit(".", 1)[0]
            dtype = TYPE_BY_PROXY.get(i.get("proxy")) or (TYPE_BY_DRIVER.get(drv) if i.get("proxy") == "uibutton" else None)
            if i.get("typeName") != "device" or not dtype:
                continue
            # Hardware/driver wrapper: it has a child of the same proxy type
            # (the user-named light/blind/thermostat). Keep it, but flag it.
            wrapper = any(c.get("proxy") == i.get("proxy") for c in by_parent.get(i["id"], []))
            devices[i["id"]] = {
                "id": i["id"], "name": i["name"], "type": dtype,
                "room_id": i.get("roomId"), "room": i.get("roomName", ""),
                "proxy": i.get("proxy"), "driver_wrapper": wrapper, "parent": i.get("parentId"),
                "driver": drv, "how": "proxy" if TYPE_BY_PROXY.get(i.get("proxy")) else "driver",
                **({"switch_kind": SWITCH_KIND.get(i.get("proxy")) or SWITCH_KIND.get(drv) or "relay"}
                   if dtype == "switch" else {}),
            }
        self.rooms, self.devices, self.meta, self.imported_at = rooms, devices, meta, time.time()
        self.all_ids = sorted({i["id"] for i in items if isinstance(i.get("id"), int)})
        SNAPSHOT.write_text(json.dumps(
            {"rooms": rooms, "devices": devices, "meta": meta, "all_ids": self.all_ids, "imported_at": self.imported_at},
            ensure_ascii=False, indent=1), encoding="utf-8")
        logbus.info("inventory", f"Imported {len(rooms)} rooms, {len(devices)} devices "
                                 f"({sum(1 for d in devices.values() if d['driver_wrapper'])} driver wrappers)")

    # ---- annotations --------------------------------------------------
    def _save_overrides(self) -> None:
        OVERRIDES.write_text(json.dumps(self.overrides, ensure_ascii=False, indent=1), encoding="utf-8")

    def set_device(self, device_id: int, note=None, aliases=None, enabled=None, function=None, voice=None) -> dict:
        if device_id not in self.devices:
            raise KeyError(device_id)
        o = self.overrides["devices"].setdefault(str(device_id), {})
        if note is not None:
            o["note"] = note
        if voice is not None:
            o["voice"] = bool(voice)              # per Sprache ansprechbar (Standard ja; Pumpen u. Ä. ausnehmen)
        if aliases is not None:
            o["aliases"] = [a.strip() for a in aliases if a.strip()]
        if enabled is not None:
            o["enabled"] = bool(enabled)
        if function is not None:
            if function not in FUNCTIONS:
                raise ValueError(f"function must be one of {FUNCTIONS}")
            o["function"] = function
        self._save_overrides()
        logbus.info("inventory", f"Device {device_id} annotated: {o}")
        return self.device(device_id)

    def set_room(self, room_id: int, aliases=None, note=None, enabled=None, favorite=None) -> dict:
        if room_id not in self.rooms:
            raise KeyError(room_id)
        o = self.overrides["rooms"].setdefault(str(room_id), {})
        if aliases is not None:
            o["aliases"] = [a.strip() for a in aliases if a.strip()]
        if note is not None:
            o["note"] = note
        if enabled is not None:
            o["enabled"] = bool(enabled)
        if favorite is not None:
            o["favorite"] = bool(favorite)
        self._save_overrides()
        return self.room(room_id)

    # ---- views --------------------------------------------------------
    def device(self, device_id: int) -> dict:
        d = dict(self.devices[device_id])
        o = self.overrides["devices"].get(str(device_id), {})
        d["note"] = o.get("note", "")
        d["aliases"] = o.get("aliases", [])
        # Funktion: Override gewinnt; Rollläden sind von Haus aus hoch/runter
        d["function"] = o.get("function", "up_down" if d.get("proxy") == "blind" else
                        "on_off" if d.get("type") == "switch" else "unknown")
        d["enabled"] = o.get("enabled", not d["driver_wrapper"])
        d["voice"] = o.get("voice", True)
        return d

    def room(self, room_id: int) -> dict:
        r = dict(self.rooms[room_id])
        o = self.overrides["rooms"].get(str(room_id), {})
        r["aliases"] = o.get("aliases", [])
        r["note"] = o.get("note", "")
        # Composer-Vorgabe: versteckte Räume bleiben unsichtbar, Override gewinnt
        r["enabled"] = o.get("enabled", not r.get("hidden", False))
        r["favorite"] = o.get("favorite", False)
        r["device_count"] = sum(1 for d in self.list_devices(room_id=room_id))
        return r

    def list_rooms(self) -> list:
        return sorted((self.room(i) for i in self.rooms),
                      key=lambda r: (r["floor_id"], r["order"], r["name"].lower()))

    def list_devices(self, room_id: Optional[int] = None, dtype: Optional[str] = None,
                     enabled_only: bool = False, voice_only: bool = False) -> list:
        out = []
        for i in self.devices:
            d = self.device(i)
            if room_id is not None and d["room_id"] != room_id:
                continue
            if dtype and d["type"] != dtype:
                continue
            if enabled_only and not d["enabled"]:
                continue
            if voice_only and not d["voice"]:
                continue
            out.append(d)
        return sorted(out, key=lambda d: (d["room"].lower(), d["type"], d["name"].lower()))

    def ai_context(self, room_id: Optional[int] = None) -> str:
        """Compact text for a language model: one line per enabled device."""
        lines = []
        for d in self.list_devices(room_id=room_id, enabled_only=True, voice_only=True):
            extra = f"; {d['note']}" if d["note"] else ""
            alias = f"; also called: {', '.join(d['aliases'])}" if d["aliases"] else ""
            lines.append(f"[{d['id']}] {d['room']} / {d['name']} ({d['type']}, {d['function']}){extra}{alias}")
        return "\n".join(lines)
