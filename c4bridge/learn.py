"""Anlern-Assistent (experimentell): unbekannte Control4-Treiber aus ihren eigenen Daten verstehen.

Idee (siehe ARCHITEKTUR.md §6 und die Module bluos.py/kodi.py): Jeder Control4-Treiber legt offen, was er kann –
Eigenschaften, Variablen, Befehle, Bindungen – und meldet Änderungen per Push (Director-WebSocket). Der Assistent
  1. liest ein Item samt „Familie“ (alle Items desselben Treibers = gleiche protocolId: Treiber + Proxys),
  2. schneidet nur für diese Familie den Push mit, während der Nutzer das Gerät in der Control4-App bedient,
  3. leitet aus Namen/Werten ein Schema ab (was die Bridge für Medien, Schalter, Licht … braucht) und schlägt je
     Feld/Aktion Kandidaten vor – der Nutzer ordnet 1:1 zu,
  4. speichert das Ergebnis als **Profil (Daten, kein Code)** in data/learned.json – Schlüssel = Proxy, bei
     Allerwelts-Proxys (uibutton, media_service …) der Treiber-Dateiname (Grundregel 2).
Nur lesend – außer „Aktion testen“ (Befehl an den Director, nur nach Rückfrage in der Oberfläche).
Übernahme in die Bridge: learned.py (Rangfolge Proxy → Modul → Profil, mit Dublettenschutz).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import logbus
from .config import DATA_DIR

STORE = DATA_DIR / "learned.json"
router = APIRouter(prefix="/api/learn")
_x: dict = {}                  # conn, inv, cache, wsman, bluos, kodi (attach)

# Proxys, die viele verschiedene Treiber benutzen → Profil-Schlüssel = Treiber-Dateiname statt Proxy
GENERIC_PROXIES = {"uibutton", "media_service", "media_player", "amplifier", "receiver", "tv", "cable", "dvd",
                   "avswitch", "tuner", "satellite", "relaysingle", "contactsingle"}
# Wofür ein nicht als Gerät genutztes Item vermutlich da ist (nur zur Übersicht im Admin)
INFRA = {"uidevice", "voiceinput", "controller", "control4_sr260", "keypad_proxy", "room_control_keypad",
         "control4_remote_hub", "reboot_driver", "driverCentral-Cloud", "EnOcean_Keypad", "keypad", "intercomproxy",
         "control4_network_mediastorage"}
MEDIA = {"media_service", "media_player", "amplifier", "receiver", "tv", "cable", "dvd", "avswitch", "tuner",
         "rf_fm", "rf_am", "satellite", "doorstation"}

# ---------------------------------------------------------------- Schemata (was die Bridge je Typ braucht)
# Muster gelten für Variablen-/Eigenschafts-/Ereignisnamen bzw. Befehle (ohne Groß/Klein). Abgeleitet aus dem,
# was BluOS (PLAY_STATUS, CURRENT_TRACK, OUTPUT_VOLUME_LEVEL), KODI („Player Status“, „Currently Playing“) und die
# Control4-Standardproxys (LIGHT_LEVEL, RelayState, ContactState …) liefern.
SCHEMAS = {
    "media": {"label": "Medien (Wiedergabe)", "hint": r"media|player|cable|tv|receiver|amplifier|audio|video",
              "fields": {
                  "state": {"label": "Abspielstatus", "req": True, "enum": ["play", "pause", "stop"],
                            "pat": r"play_?status|player.?status|play.?state|transport|^state$|playing|status"},
                  "title": {"label": "Titel", "req": True,
                            "pat": r"current\w*.?(track|title|playing)|now.?playing|^title$|track.?name|song|\.title$"},
                  "artist": {"label": "Interpret / Untertitel", "pat": r"artist|sub.?title|album.?artist|channel|show"},
                  "album": {"label": "Album", "pat": r"album"},
                  # „art“ nur als ganzes Wort (sonst trifft es „artist“); zusätzlich am Wert erkennbar (URL/Artwork)
                  "image": {"label": "Bild (Cover-URL)", "pat": r"image|artwork|(^|[^a-z])art($|[^a-z])|cover|thumb|logo|icon",
                            "valpat": r"^https?://|/artwork|\.(jpe?g|png|webp)(\?|$)"},
                  "volume": {"label": "Lautstärke", "pat": r"volume.?level|^volume$|volumelevel|vol_?level"},
                  "mute": {"label": "Stumm", "enum": ["on", "off"], "pat": r"mute"},
                  "source": {"label": "Quelle / Dienst", "pat": r"service$|source|current.?service|input"},
                  "position": {"label": "Position (s)", "pat": r"offset|position|elapsed|^time$|progress"},
                  "duration": {"label": "Länge (s)", "pat": r"length|duration|total.?time|^total$"},
              },
              "actions": {
                  "play": {"label": "Abspielen", "pat": r"^play$|^play_?media$"},
                  "pause": {"label": "Pause", "pat": r"^pause$|play_?pause"},
                  "stop": {"label": "Stopp", "pat": r"^stop$"},
                  "next": {"label": "Weiter", "pat": r"skip_?fwd|^next|skipfwd|skip.?forward"},
                  "prev": {"label": "Zurück", "pat": r"skip_?rev|^prev|skiprev|skip.?back"},
                  "volume": {"label": "Lautstärke setzen", "pat": r"set_?volume|volume_?level", "param": True},
                  "mute": {"label": "Stumm schalten", "pat": r"mute_?(on|toggle)|^mute"},
              }},
    "switch": {"label": "Schalter / Steckdose", "hint": r"relay|switch|outlet|plug|uibutton",
               "fields": {
                   "state": {"label": "Zustand", "req": True, "enum": ["on", "off"],
                             "pat": r"relay_?state|state|power|^on$|outlet|switch|icon_description|status"},
                   "power": {"label": "Leistung (W)", "pat": r"watt|power.?usage|instantaneous|consumption"},
               },
               "actions": {
                   "on": {"label": "Ein", "pat": r"^on$|turn.?on|close|_on$|^select$"},
                   "off": {"label": "Aus", "pat": r"^off$|turn.?off|open|_off$"},
                   "toggle": {"label": "Umschalten", "pat": r"toggle"},
               }},
    "light": {"label": "Licht / Dimmer", "hint": r"light|dimmer|lamp|hue",
              "fields": {
                  "level": {"label": "Helligkeit 0–100", "req": True, "pat": r"light_?level|brightness|level"},
                  "state": {"label": "An/Aus", "enum": ["on", "off"], "pat": r"light_?state|state|^on$"},
              },
              "actions": {
                  "level": {"label": "Helligkeit setzen", "pat": r"set_?level|ramp|brightness", "param": True},
                  "on": {"label": "Ein", "pat": r"^on$|turn.?on"},
                  "off": {"label": "Aus", "pat": r"^off$|turn.?off"},
              }},
    "blind": {"label": "Rollladen / Jalousie", "hint": r"blind|shade|cover|shutter|motor",
              "fields": {"level": {"label": "Position 0–100 (offen)", "req": True, "pat": r"level|position|percent"},
                         "moving": {"label": "Fährt", "enum": ["on", "off"], "pat": r"moving|opening|closing"}},
              "actions": {"level": {"label": "Position setzen", "pat": r"set_?level|position", "param": True},
                          "open": {"label": "Öffnen", "pat": r"^up$|open"},
                          "close": {"label": "Schließen", "pat": r"^down$|close"},
                          "stop": {"label": "Stopp", "pat": r"^stop$"}}},
    "sensor": {"label": "Sensor / Melder", "hint": r"sensor|contact|motion|door|window|presence|hue.?sensor",
               "fields": {"state": {"label": "Ausgelöst (Kontakt offen / Bewegung)", "req": True, "enum": ["on", "off"],
                                    "pat": r"contact_?state|motion|presence|occup|state|open|triggered|icon_description"},
                          "temperature": {"label": "Temperatur", "pat": r"temp"},
                          "lux": {"label": "Helligkeit (Lux)", "pat": r"lux|illum|light.?level"},
                          "battery": {"label": "Batterie", "pat": r"batt"}},
               "actions": {}},
    "thermostat": {"label": "Heizung / Thermostat", "hint": r"thermostat|heat|hvac|climate",
                   "fields": {"temperature": {"label": "Ist-Temperatur", "req": True, "pat": r"temperature(_c)?$|current.?temp|temp"},
                              "setpoint": {"label": "Soll-Temperatur", "pat": r"setpoint|target"},
                              "mode": {"label": "Modus", "pat": r"mode"}},
                   "actions": {"setpoint": {"label": "Soll setzen", "pat": r"set_?setpoint|setpoint", "param": True}}},
    "info": {"label": "Info-Gerät (nur Werte anzeigen)", "hint": r"weather|wetter|gateway|info|meter|monitor",
             "fields": {}, "actions": {}},
}
# Standardbefehle der Control4-Proxys, die der Director in /commands NICHT auflistet (siehe ARCHITEKTUR.md §7:
# „versteckte, funktionierende Befehle“) – nur als Vorschlag, im Assistenten mit „Standard-Proxy“ markiert.
PROXY_COMMANDS = {
    "amplifier": [("SET_VOLUME_LEVEL", ["LEVEL", "OUTPUT"]), ("MUTE_ON", ["OUTPUT"]), ("MUTE_OFF", ["OUTPUT"]),
                  ("MUTE_TOGGLE", ["OUTPUT"])],
    "receiver": [("SET_VOLUME_LEVEL", ["LEVEL", "OUTPUT"]), ("MUTE_ON", ["OUTPUT"]), ("MUTE_OFF", ["OUTPUT"])],
    "light_v2": [("SET_LEVEL", ["LEVEL"]), ("ON", []), ("OFF", []), ("TOGGLE", [])],
    "blind": [("SET_LEVEL_TARGET", ["LEVEL_TARGET"]), ("UP", []), ("DOWN", []), ("STOP", [])],
    "relaysingle": [("CLOSE", []), ("OPEN", []), ("TOGGLE", [])],
}
_ENUM_WORDS = {
    "play": r"^(play|playing|1|true|started|running)$|play(ing)?\b", "pause": r"pause", "stop": r"stop|idle|^0$|^false$",
    "on": r"^(on|1|true|closed?|yes|active|an)$|^on\b|\bon$", "off": r"^(off|0|false|opened?|no|inactive|aus)$|^off\b|\boff$",
}


# Sensor: „ausgelöst“ = Kontakt OFFEN bzw. Bewegung – CLOSED heißt hier aus (beim Relais dagegen an)
# Zahlen (1/0) bewusst NICHT: ContactState 1 = zu beim Kontakt, aber 1 = Bewegung beim Melder → Mitschnitt/Nutzer
_ENUM_SENSOR = {"on": r"^(open|opened|motion|detected|active|triggered|yes)$|open",
                "off": r"^(closed?|clear|inactive|no|idle)$|clos"}


def _words(t: str) -> dict:
    return _ENUM_SENSOR if t == "sensor" else _ENUM_WORDS


# ---------------------------------------------------------------- Speicher
def _load() -> dict:
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    d.setdefault("profiles", {})
    d.setdefault("items", {})
    d.setdefault("ignored", {})      # Treiber-Dateiname bzw. Item-ID → Begründung („nicht nötig“, Admin › Geräte)
    return d


def _save(d: dict) -> None:
    from .auth import atomic_write
    atomic_write(STORE, json.dumps(d, ensure_ascii=False, indent=1))


_stem = lambda i: str(i.get("protocolFilename") or i.get("filename") or "").rsplit(".", 1)[0]  # noqa: E731


def profile_key(item: dict) -> str:
    p = str(item.get("proxy") or "")
    return f"driver:{_stem(item)}" if p in GENERIC_PROXIES or not p else f"proxy:{p}"


# ---------------------------------------------------------------- Mitschnitt (nur Familie des gewählten Items)
_cap = {"ids": set(), "root": None, "until": 0.0, "msgs": deque(maxlen=600), "since": 0.0}
_last: dict = {}               # (Item, Pfad) → letzter Wert – für Ereignis-Felder in der Vorschau


def _flat(data, prefix="") -> list:
    """Nachricht → [(Pfad, Wert)]; UPDATE_PROPERTY wird zu prop:<Name>."""
    out = []
    if isinstance(data, dict):
        dc = data.get("devicecommand")
        if isinstance(dc, dict) and dc.get("command") == "UPDATE_PROPERTY":
            ps = (dc.get("params") or {}).get("param") or []
            kv = {x.get("name"): (x.get("value") or {}).get("static") for x in ps if isinstance(x, dict)}
            if kv.get("Name"):
                return [(f"prop:{kv['Name']}", kv.get("Value"))]
        if isinstance(dc, dict) and dc.get("command") == "LUA_OUTPUT":
            return []                                    # Treiber-Debug: kein Zustand
        ev = data.get("EVENT")
        if isinstance(ev, dict) and ev.get("NAME"):
            return _flat(ev.get("ARGS") or {}, f"event:{ev['NAME']}.")
        for k, v in data.items():
            out += _flat(v, f"{prefix}{k}.")
        return out
    if isinstance(data, list):
        for n, v in enumerate(data[:5]):
            out += _flat(v, f"{prefix}{n}.")
        return out
    return [(prefix.rstrip("."), data)]


def _on_message(message) -> None:
    try:
        dev = message.get("iddevice") if isinstance(message, dict) else None
        if dev is None:
            return
        tracked = dev in _cap["ids"] and time.time() < _cap["until"]
        learned = str(dev) in _x.get("learned_ids", set())
        if not tracked and not learned:
            return
        flat = [(k, v) for k, v in _flat(message.get("data") or {}) if k]
        for k, v in flat:
            _last[(dev, k)] = v
        if learned and flat and _x.get("on_learned"):
            _x["on_learned"](dev)
        if tracked and flat:
            _cap["msgs"].append({"t": time.time(), "dev": dev, "flat": flat[:40]})
    except Exception:  # noqa: BLE001
        pass


def attach(app, conn, inv, cache, wsman, bluos, kodi) -> None:
    """Router einhängen und den WebSocket-Empfang anzapfen (vorhandene Verarbeitung bleibt unverändert)."""
    _x.update(conn=conn, inv=inv, cache=cache, wsman=wsman, bluos=bluos, kodi=kodi,
              learned_ids=set(_load()["items"]))
    prev = wsman.on_message

    def tap(message):
        _on_message(message)
        if prev:
            prev(message)
    wsman.on_message = tap
    app.include_router(router)


# ---------------------------------------------------------------- Lesen
async def _items() -> list:
    c = _x["conn"]
    return await _x["cache"].get("learn_items", lambda: c.get("/api/v1/items"), project=True)


async def _get(path: str, default):
    try:
        r = await _x["conn"].get(path)
        return r if r is not None else default
    except Exception:  # noqa: BLE001
        return default


def _use(i: dict, bluos_ids: set) -> dict:
    """Wofür nutzt die Bridge dieses Item?"""
    inv, kodi = _x["inv"], _x["kodi"]
    iid, proxy = i["id"], str(i.get("proxy") or "")
    d = inv.devices.get(iid)
    if d and d.get("how") == "profile":
        return {"use": "learned", "label": f"angelernt ({d.get('ltype')})", "how": d.get("key", "")}
    if d:
        from .inventory import TYPE_BY_PROXY
        return {"use": "device", "label": f"Gerät ({d['type']})",
                "how": "Proxy" if proxy in TYPE_BY_PROXY else "Treiber-Ausnahme"}
    meta = {v: k for k, v in (inv.meta or {}).items()}
    if iid in meta:
        return {"use": "agent", "label": f"Systemagent ({meta[iid]})", "how": "Proxy"}
    if kodi.owner(iid):
        return {"use": "module", "label": "Modul KODI", "how": "Modul (Treiber-Dateiname)"}
    if iid in bluos_ids:
        return {"use": "module", "label": "Modul BluOS", "how": "Modul (Treiber-Dateiname)"}
    stx = _load()
    st = stx["items"].get(str(iid))
    if st:
        ref = (stx["profiles"].get(st) or {}).get("reference")
        return {"use": "learned", "label": "Referenz-Profil (nicht übernommen)" if ref else "angelernt (Profil)", "how": st}
    ign = stx["ignored"].get(f"item:{i.get('protocolId') or iid}") or stx["ignored"].get(f"driver:{_stem(i)}")
    if ign is not None:
        return {"use": "ignored", "label": "ausgeblendet (nicht nötig)", "how": ign}
    if proxy in INFRA:
        return {"use": "infra", "label": "Bedienteil / System", "how": ""}
    if proxy in MEDIA:
        return {"use": "media", "label": "Medienquelle / Endpunkt (über Raum)", "how": ""}
    return {"use": "candidate", "label": "nicht genutzt – Kandidat", "how": ""}


async def _feeds(root: int) -> list:
    """Speist dieser Treiber über eine Steuer-Bindung schon ein Gerät, das die Bridge anzeigt?
    (z. B. Opus-Fenstergriff → „Sensor Output“ → Control4-Kontakt) – dann ist er kein Kandidat zum Anlernen."""
    inv = _x["inv"]
    try:
        bs = await _x["cache"].get(f"learn_binds:{root}", lambda: _x["conn"].get(f"/api/v1/items/{root}/bindings"),
                                   project=True)
    except Exception:  # noqa: BLE001
        return []
    out: dict = {}
    for b in bs if isinstance(bs, list) else []:
        if b.get("bindingType") == "Proxy":
            continue
        for c in b.get("connections") or []:
            d = inv.devices.get(c.get("id"))
            if d and d.get("how") != "profile":       # je Gerät einmal, Bindungen zusammengefasst
                e = out.setdefault(d["id"], {"id": d["id"], "name": d.get("name"), "type": d.get("type"), "binding": ""})
                e["binding"] = ", ".join(x for x in (e["binding"], b.get("bindingName") or "") if x)
    return list(out.values())


def _bluos_ids() -> set:
    b = _x["bluos"]
    ids = {x for d in (b.drivers or []) for x in (d.get("driver_id"), d.get("proxy_id"), d.get("endpoint_id")) if x}
    return ids | {int(k) for k in (b.services or {})}


@router.get("/items")
async def items():
    """Alle Control4-Geräte-Items mit Proxy, Treiber und Nutzung durch die Bridge (Admin → Geräte)."""
    bluos_ids = _bluos_ids()
    out = []
    for i in await _items():
        if i.get("typeName") != "device" or not isinstance(i.get("id"), int):
            continue
        out.append({"id": i["id"], "name": i.get("name", ""), "proxy": i.get("proxy") or "", "driver": _stem(i),
                    "room": i.get("roomName", ""), "room_id": i.get("roomId"), "parent": i.get("parentId"),
                    "protocol": i.get("protocolId"), **_use(i, bluos_ids)})
    # Kandidaten, deren Treiber schon ein angezeigtes Gerät speist → eigene Gruppe statt „Kandidat“
    roots = {x["protocol"] or x["id"] for x in out if x["use"] == "candidate"}
    feeds = {r: await _feeds(r) for r in roots}
    for x in out:
        f = feeds.get(x["protocol"] or x["id"]) if x["use"] == "candidate" else None
        if f:
            x.update(use="feeds", label="speist ein angezeigtes Gerät (schon sichtbar)",
                     how=", ".join(f"{d['name']} #{d['id']} ({d['type']})" for d in f[:2]))
    return out


async def _family(item_id: int) -> tuple:
    its = await _items()
    me = next((i for i in its if i.get("id") == item_id), None)
    if not me:
        raise HTTPException(404, "Item nicht im Projekt (Projekt neu einlesen?)")
    root = me.get("protocolId") or item_id
    fam = [i for i in its if i.get("typeName") == "device" and (i.get("protocolId") == root or i.get("id") == root)]
    fam.sort(key=lambda i: (i["id"] != root, i["id"]))
    return me, root, fam


class IgnoreBody(BaseModel):
    key: str                 # "driver:<dateiname>" (alle Geräte dieses Treibers) oder "item:<Treiber-ID>"
    note: str = ""
    on: bool = True


@router.post("/ignore")
async def ignore(body: IgnoreBody):
    """Kandidat ausblenden („nicht nötig“) bzw. wieder einblenden – nur für die Übersicht, ändert nichts im Haus."""
    if not re.fullmatch(r"(driver:[\w.\- +]+|item:\d+)", body.key):
        raise HTTPException(400, "ungültiger Schlüssel")
    st = _load()
    if body.on:
        st["ignored"][body.key] = body.note
    else:
        st["ignored"].pop(body.key, None)
    _save(st)
    return {"ok": True, "ignored": st["ignored"]}


@router.get("/inspect/{item_id}")
async def inspect(item_id: int):
    """Item + Familie: Eigenschaften (am Treiber), Variablen, Befehle, Bindungen – alles nur lesend."""
    me, root, fam = await _family(item_id)
    members = []
    seen_cmds: set = set()
    for i in fam:
        vs = await _get(f"/api/v1/items/{i['id']}/variables", [])
        cs = await _get(f"/api/v1/items/{i['id']}/commands", [])
        cmds = []
        for c in cs if isinstance(cs, list) else []:
            key = (c.get("deviceId") or i["id"], c.get("command"))
            if key in seen_cmds:
                continue                                 # Proxys listen oft die Befehle des Treibers noch einmal
            seen_cmds.add(key)
            cmds.append({"command": c.get("command"), "label": c.get("label") or c.get("command"),
                         "target": c.get("deviceId") or i["id"],
                         "params": [{"name": p.get("name"), "type": p.get("type"), **({"items": p.get("items")}
                                     if p.get("items") else {}), **({k: p[k] for k in ("minimum", "maximum") if k in p})}
                                    for p in (c.get("params") or []) if isinstance(p, dict)]})
        listed = {c["command"] for c in cmds} | {k[1] for k in seen_cmds}
        for name, ps in PROXY_COMMANDS.get(str(i.get("proxy") or "").split("_c4")[0], []):
            if name not in listed:
                cmds.append({"command": name, "label": f"{name} (Standard-Proxy, nicht gelistet)", "target": i["id"],
                             "params": [{"name": x, "type": "NUMBER"} for x in ps], "standard": True})
        members.append({"id": i["id"], "name": i.get("name"), "proxy": i.get("proxy"), "driver": _stem(i),
                        "role": "driver" if i["id"] == root else f"proxy:{i.get('proxy')}",
                        "variables": [{"name": v.get("varName"), "value": v.get("value"), "type": v.get("type")}
                                      for v in vs if isinstance(v, dict)] if isinstance(vs, list) else [],
                        "commands": cmds})
        await asyncio.sleep(0.05)                       # Director schonen
    props = await _get(f"/api/v1/items/{root}/properties", [])
    props = [{"name": p.get("name"), "value": "••••" if re.search(r"pass|secret|token|key$", str(p.get("name")), re.I)
              else p.get("value"), "type": p.get("type"), "readonly": p.get("readonly")}
             for p in props if isinstance(p, dict)] if isinstance(props, list) else []
    binds = await _get(f"/api/v1/items/{root}/bindings", [])
    binds = [{"id": b.get("bindingId"), "name": b.get("bindingName"), "class": b.get("bindingClass"),
              "type": b.get("bindingType"), "to": [{"id": c.get("id"), "name": c.get("name"), "room": c.get("roomName")}
                                                   for c in b.get("connections") or []]}
             for b in binds if isinstance(b, dict) and b.get("connections")] if isinstance(binds, list) else []
    st = _load()
    feeds = await _feeds(root)
    bids = _bluos_ids()
    used = [{"id": i["id"], "name": i.get("name"), **u} for i in fam
            for u in [_use(i, bids)] if u["use"] in ("device", "module", "agent")]
    return {"id": item_id, "root": root, "name": me.get("name"), "proxy": me.get("proxy"), "driver": _stem(me), "feeds": feeds,
            "used": used,
            "room": me.get("roomName"), "key": profile_key(fam[0] if fam else me), "members": members,
            "properties": props, "bindings": binds, "suggest": _suggest(fam, members, props),
            "profile": st["profiles"].get(st["items"].get(str(item_id)) or profile_key(fam[0] if fam else me))}


# ---------------------------------------------------------------- Schema ableiten
def _cands(members: list, props: list, root: int) -> list:
    """Alle möglichen Quellen eines Werts: Variablen (je Item), Eigenschaften (Treiber), Ereignisse (Mitschnitt)."""
    out = []
    for m in members:
        for v in m["variables"]:
            out.append({"src": "var", "role": m["role"], "item": m["id"], "key": v["name"], "value": v["value"]})
    for p in props:
        if p.get("readonly") in (True, "true", "True") or p["name"].lower().startswith(("current", "player", "status")):
            out.append({"src": "prop", "role": "driver", "item": root, "key": p["name"], "value": p["value"]})
    roles = {m["id"]: m["role"] for m in members}
    seen: dict = {}
    for msg in list(_cap["msgs"]):
        if msg["dev"] not in roles:
            continue
        for k, v in msg["flat"]:
            if (msg["dev"], k) not in seen:
                seen[(msg["dev"], k)] = {"n": 0, "vals": []}
                out.append({"src": "event", "role": roles[msg["dev"]], "item": msg["dev"], "key": k,
                            "value": _last.get((msg["dev"], k), v)})
            e = seen[(msg["dev"], k)]
            e["n"] += 1
            if str(v) not in e["vals"] and len(e["vals"]) < 8:
                e["vals"].append(str(v))
    for (dev, k), v in list(_last.items()):         # Push-Felder ohne aktuellen Mitschnitt (gemerkt, learned_last.json)
        if dev in roles and (dev, k) not in seen and not k.startswith("prop:") and not isinstance(v, (dict, list)):
            seen[(dev, k)] = {"n": 0, "vals": [str(v)]}
            out.append({"src": "event", "role": roles[dev], "item": dev, "key": k, "value": v})
    snap = _cap.get("snap") or {}
    for c in out:                                    # gesehen / geändert / beobachtete Werte (für „Alle Werte")
        if c["src"] == "event":
            e = seen.get((c["item"], c["key"]))
            if e:
                c.update(seen=e["n"], values=e["vals"], changed=len(e["vals"]) > 1)
            else:
                c.update(seen=0, values=[str(c["value"])], changed=False)
        else:
            sk = (c["item"], c["key"]) if c["src"] == "var" else (c["item"], f"prop:{c['key']}")
            c.update(seen=0, values=[str(c["value"])], changed=sk in snap and str(snap[sk]) != str(c["value"]))
    return out


def _score(pat: str, key: str, value, valpat: str = "") -> int:
    """10 = Name passt (+ bis 5 für spezifischere, längere Treffer: „Player Status“ vor „Cloud Status“),
    5 = nur der Pfad passt, +1 = hat gerade einen Wert."""
    k = key.lower().split(".")[-1] if not key.startswith("prop:") else key[5:].lower()
    s = 0
    m = re.search(pat, k)
    if m:
        s += 10 + min(5, len(m.group(0)) // 3)
    elif re.search(pat, key.lower()):
        s += 5
    if valpat and value not in (None, "") and re.search(valpat, str(value).lower()):
        s = max(s, 10) + 4                           # Wert sieht aus wie gesucht (z. B. Cover-URL)
    if value not in (None, "", "0", 0):
        s += 1
    if "nowplaying" in key.lower().replace("_", ""):
        s += 1                                       # „läuft gerade“ vor dem ersten Eintrag der Warteschlange
    if isinstance(value, (int, float)) or str(value).lstrip("-").isdigit():
        s += 1 if re.search(r"offset|position|length|duration|volume|level|temp", key.lower()) else 0
    return s


def _suggest(fam: list, members: list, props: list) -> dict:
    root = fam[0]["id"] if fam else 0
    cands = _cands(members, props, root)
    text = " ".join(str(x) for i in fam for x in (i.get("proxy"), _stem(i), i.get("name"))).lower()
    names = " ".join(c["key"] for c in cands).lower() + " " + " ".join(
        c["command"] for m in members for c in m["commands"]).lower()
    types = {}
    for t, sch in SCHEMAS.items():
        s = 5 * len(re.findall(sch["hint"], text))
        s += sum(1 for f in sch["fields"].values() if re.search(f["pat"], names))
        s += sum(1 for a in sch["actions"].values() if re.search(a["pat"], names))
        types[t] = s
    best = max(types, key=types.get) if types else "media"
    return {"type": best, "scores": types, "candidates": cands}


def _rank(sch: dict, cands: list, members: list, ptype: str = "") -> dict:
    fields = {}
    for f, spec in sch["fields"].items():
        def sc(c, spec=spec):
            s = _score(spec["pat"], c["key"], c["value"], spec.get("valpat", ""))
            vals = [str(v).lower() for v in (c.get("values") or [c["value"]]) if str(v) not in ("", "None")]
            if spec.get("enum") and vals and all(any(re.search(_words(ptype).get(e, "^$"), v) for e in spec["enum"])
                                                  for v in vals):
                s = max(s, 10) + 3                     # beobachtete Werte passen zum Zustand (Opened/Closed → on/off)
            if c.get("changed"):
                s += 3                                 # hat sich beim Bedienen geändert → vermutlich wichtig
            return s
        ranked = sorted(((sc(c), c) for c in cands), key=lambda x: -x[0])
        fields[f] = [dict(c, score=s) for s, c in ranked if s >= 10][:6]
    actions = {}
    cmds = [dict(c, role=m["role"]) for m in members for c in m["commands"]]
    for a, spec in sch["actions"].items():
        actions[a] = [c for c in cmds if re.search(spec["pat"], str(c["command"]).lower())
                      or re.search(spec["pat"], str(c["label"]).lower())][:6]
    return {"fields": fields, "actions": actions}


@router.get("/schemas")
async def schemas():
    return {k: {"label": v["label"], "fields": {f: {x: y for x, y in s.items() if x not in ("pat", "valpat")}
                                                for f, s in v["fields"].items()},
                "actions": {a: {x: y for x, y in s.items() if x != "pat"} for a, s in v["actions"].items()}}
            for k, v in SCHEMAS.items()}


@router.get("/suggest/{item_id}")
async def suggest(item_id: int, type: str = ""):
    """Vorschläge je Feld/Aktion für einen Typ (Standard: geschätzter Typ) – inkl. Ereignisse aus dem Mitschnitt."""
    d = await inspect(item_id)
    t = type if type in SCHEMAS else d["suggest"]["type"]
    r = _rank(SCHEMAS[t], d["suggest"]["candidates"], d["members"], t)
    # Werte-Zuordnung (enum) vorschlagen: beobachtete Werte → play/pause/stop bzw. on/off
    for f, spec in SCHEMAS[t]["fields"].items():
        if "enum" in spec:
            for c in r["fields"][f]:
                vals = {str(c["value"])} | {str(v) for (dv, k), v in _last.items() if dv == c["item"] and k == c["key"]}
                vals |= {str(v) for m in _cap["msgs"] if m["dev"] == c["item"] for k, v in m["flat"] if k == c["key"]}
                c["map"] = {v: next((e for e in spec["enum"] if re.search(_words(t).get(e, "^$"), v.lower())), "")
                            for v in sorted(vals) if v not in ("None",)}
    from .learned import INFO_KINDS, guess_kind
    for c in d["suggest"]["candidates"]:
        c["kind"] = guess_kind(c["key"], c["value"])
    cmds = [{"command": c["command"], "label": c["label"], "role": m["role"], "params": c["params"], "target": c["target"]}
            for m in d["members"] for c in m["commands"]]
    return {"type": t, "scores": d["suggest"]["scores"], **r, "all": d["suggest"]["candidates"], "commands": cmds,
            "kinds": {k: {"label": v["label"], "unit": v["unit"]} for k, v in INFO_KINDS.items()},
            "role": next((m["role"] for m in d["members"] if m["id"] == item_id), "driver"),
            "members": [
        {"id": m["id"], "name": m["name"], "proxy": m["proxy"], "role": m["role"]} for m in d["members"]]}


# ---------------------------------------------------------------- Mitschnitt steuern
class CaptureBody(BaseModel):
    seconds: int = 120


@router.post("/capture/{item_id}")
async def capture_start(item_id: int, body: CaptureBody):
    _, root, fam = await _family(item_id)
    snap = {}
    for i in fam:                                   # Ausgangswerte: was sich beim Bedienen ändert, ist wichtig
        for v in await _get(f"/api/v1/items/{i['id']}/variables", []) or []:
            if isinstance(v, dict):
                snap[(i["id"], v.get("varName"))] = v.get("value")
    for pr in await _get(f"/api/v1/items/{root}/properties", []) or []:
        if isinstance(pr, dict):
            snap[(root, f"prop:{pr.get('name')}")] = pr.get("value")
    _cap.update(ids={i["id"] for i in fam}, root=root, until=time.time() + max(10, min(body.seconds, 900)),
                since=time.time(), snap=snap)
    _cap["msgs"].clear()
    logbus.info("learn", f"Mitschnitt für Treiber #{root} ({len(fam)} Items) {body.seconds} s")
    return await capture_get(item_id)


@router.get("/capture/{item_id}")
async def capture_get(item_id: int):
    names = {}
    try:
        _, root, fam = await _family(item_id)
        names = {i["id"]: f"{i.get('name')} ({i.get('proxy')})" for i in fam}
    except HTTPException:
        pass
    left = max(0, int(_cap["until"] - time.time()))
    return {"active": left > 0, "left": left, "root": _cap["root"], "count": len(_cap["msgs"]),
            "messages": [{"t": m["t"], "dev": m["dev"], "who": names.get(m["dev"], m["dev"]),
                          "flat": [[k, v] for k, v in m["flat"]]} for m in list(_cap["msgs"])[-150:]]}


@router.delete("/capture")
async def capture_stop():
    _cap["until"] = 0
    return {"ok": True, "count": len(_cap["msgs"])}


# ---------------------------------------------------------------- Profile
class ProfileBody(BaseModel):
    item_id: int
    key: str
    type: str
    fields: dict = {}      # Feld → {src, role, key, map?}
    actions: dict = {}     # Aktion → {role, target_role?, command, param?, value?}
    infos: list = []       # [{src, role, key, kind, label, place, warn?, map?, tf?}]
    device_role: str = ""  # welches Familien-Item in der Oberfläche das Gerät ist (z. B. proxy:uibutton)
    reference: bool = False  # nur Referenz – die Bridge übernimmt das Profil nicht
    name: str = ""


@router.post("/profile")
async def profile_save(body: ProfileBody):
    if body.type not in SCHEMAS:
        raise HTTPException(400, "unbekannter Typ")
    if not re.fullmatch(r"(proxy|driver):[\w.\- +]+", body.key):
        raise HTTPException(400, "ungültiger Schlüssel")
    st = _load()
    st["profiles"][body.key] = {"type": body.type, "name": body.name, "fields": body.fields, "actions": body.actions,
                                "infos": body.infos, "device_role": body.device_role, "reference": body.reference,
                                "learned_from": body.item_id, "updated": time.time()}
    _, root, fam = await _family(body.item_id)
    for i in fam:
        st["items"][str(i["id"])] = body.key
    _save(st)
    _x["learned_ids"] = set(st["items"])
    logbus.info("learn", f"Profil {body.key} ({body.type}) gespeichert – {len(body.fields)} Felder, "
                         f"{len(body.actions)} Aktionen, {len(body.infos)} Infos{' (Referenz)' if body.reference else ''}")
    n = await _x["on_profiles"]() if _x.get("on_profiles") else 0
    from . import learned
    mine = any(i["id"] in learned._fam_dev for i in fam)
    return {"ok": True, "profile": st["profiles"][body.key], "applied": n, "mine": mine,
            "skip": "" if mine or body.reference else learned._skip.get(root, "")}


@router.get("/profiles")
async def profiles():
    return _load()


@router.delete("/profile")
async def profile_delete(key: str):
    st = _load()
    st["profiles"].pop(key, None)
    st["items"] = {k: v for k, v in st["items"].items() if v != key}
    _save(st)
    _x["learned_ids"] = set(st["items"])
    n = await _x["on_profiles"]() if _x.get("on_profiles") else 0
    return {"ok": True, "applied": n}


def _member(members: list, role: str) -> Optional[dict]:
    return next((m for m in members if m["role"] == role), None)


@router.get("/evaluate/{item_id}")
async def evaluate(item_id: int, key: str = ""):
    """Profil auf dieses Item anwenden und die Werte JETZT zeigen (Variablen/Eigenschaften live, Ereignisse =
    zuletzt gesehener Wert) – so prüft man, ob das Schema stimmt, ohne etwas zu schalten."""
    d = await inspect(item_id)
    st = _load()
    prof = st["profiles"].get(key or st["items"].get(str(item_id)) or d["key"])
    if not prof:
        raise HTTPException(404, "kein Profil")
    from . import learned
    fam: dict = {}
    for m in d["members"]:                      # wie learned.apply: erstes Item je Rolle
        fam.setdefault(m["role"], m["id"])
    varvals = {(m["id"], v["name"]): v["value"] for m in d["members"] for v in m["variables"]}
    props = {p["name"]: p["value"] for p in d["properties"]}
    r = learned.resolve(prof, fam, d["root"], varvals, props)
    out, infos = r["fields"], r["infos"]
    acts = {a: {"command": x.get("command"), "role": x.get("role"), "param": x.get("param")}
            for a, x in (prof.get("actions") or {}).items()}
    return {"key": key or st["items"].get(str(item_id)) or d["key"], "type": prof["type"], "fields": out,
            "infos": infos, "actions": acts, "reference": bool(prof.get("reference")),
            "applied": any(m["id"] in learned._fam_dev for m in d["members"]),
            "skip": learned._skip.get(d["root"], "")}


class TestBody(BaseModel):
    action: str
    value: Optional[str] = None
    key: str = ""


@router.post("/test/{item_id}")
async def test_action(item_id: int, body: TestBody):
    """Aktion des Profils EINMAL ausführen (Schreibaktion – die Oberfläche fragt vorher nach)."""
    d = await inspect(item_id)
    st = _load()
    prof = st["profiles"].get(body.key or st["items"].get(str(item_id)) or d["key"])
    a = (prof or {}).get("actions", {}).get(body.action)
    if not a:
        raise HTTPException(404, "Aktion nicht im Profil")
    mem = _member(d["members"], a.get("role") or "driver") or {}
    cmd = next((c for c in mem.get("commands", []) if c["command"] == a.get("command")), None)
    target = (cmd or {}).get("target") or mem.get("id") or d["root"]
    params = {a["param"]: body.value} if a.get("param") and body.value is not None else {}
    logbus.info("learn", f"Test: {a.get('command')} {params} → #{target}")
    await _x["conn"].post(f"/api/v1/items/{target}/commands", a.get("command"), params)
    return {"ok": True, "target": target, "command": a.get("command"), "params": params}
