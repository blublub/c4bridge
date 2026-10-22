"""Angelernte Geräte zur Laufzeit (Profile aus dem Anlern-Assistenten, data/learned.json).

Rangfolge der Erkennung (ARCHITEKTUR.md §1/§6): **Proxy → Modul → Profil.** Ein Profil wird nur auf Treiber-Familien
angewendet, die weder als Standard-Gerät (TYPE_BY_PROXY) noch von einem Modul (BluOS, KODI) genutzt werden; Profile
mit „reference“ (z. B. das BluOS-Testprofil) laufen nie mit.
- apply():   Profile → Geräte im Inventar (type "learned", ltype = Schema-Typ), je Treiber-Familie ein Gerät.
- state():   Werte lesen – Variablen in EINEM Director-Abruf, Eigenschaften/Ereignisse aus dem Push (zuletzt gesehen,
             auf Platte gemerkt: data/learned_last.json, damit z. B. Fenstergriffe nach einem Neustart nicht „unbekannt“ sind).
- act():     Aktion des Profils → Control4-Befehl an das richtige Familien-Item.
Push: learn.py zapft den WebSocket an; Nachrichten einer angelernten Familie machen den Zwischenspeicher „learned“
für deren Raum veraltet und melden das an die Oberfläche (SSE devs.learned).
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import learn, logbus
from .config import DATA_DIR

LAST_FILE = DATA_DIR / "learned_last.json"
router = APIRouter(prefix="/api/learned")
_dev: dict = {}        # Geräte-ID → {key, fam: {Rolle: ID}, root, room_id}
_fam_dev: dict = {}    # jede ID einer angelernten Familie → Geräte-ID
_skip: dict = {}       # Treiber-ID (Familie) → warum ein passendes Profil NICHT übernommen wurde (Dublettenschutz)
_persist_pending = False
_dirty = False
# Info-Arten: wie ein Wert in der Oberfläche erscheint (Einheit, Format)
INFO_KINDS = {
    "temperature": {"label": "Temperatur", "unit": "°C", "pat": r"temp"},
    "humidity": {"label": "Luftfeuchte", "unit": "%", "pat": r"humid|feucht"},
    "lux": {"label": "Helligkeit", "unit": "lx", "pat": r"lux|illum"},
    "battery": {"label": "Batterie", "unit": "%", "pat": r"batt"},
    "power": {"label": "Leistung", "unit": "W", "pat": r"watt|instantaneous|power.?usage|^power$"},
    "energy": {"label": "Energie", "unit": "kWh", "pat": r"kwh|energy|consumption|today|total"},
    "signal": {"label": "Signal / Verbindung", "unit": "", "pat": r"signal|rssi|online|network|connection|link"},
    "state": {"label": "Zustand", "unit": "", "pat": r"state|status|position|mode"},
    "event": {"label": "Ereignis", "unit": "", "pat": r"press|ring|button|event|trigger"},
    "text": {"label": "Text", "unit": "", "pat": r"."},
}


def guess_kind(key: str, value) -> str:
    k = key.lower().split(".")[-1].replace("prop:", "")
    for kind, spec in INFO_KINDS.items():
        if kind != "text" and re.search(spec["pat"], k):
            return kind
    if isinstance(value, str) and re.search(r"°c$", value.lower()):
        return "temperature"
    return "text"


# ---------------------------------------------------------------- Werte umrechnen
def tf(raw, spec: dict, ptype: str = "", enum: Optional[list] = None):
    """Rohwert → Wert: Werte-Map, „enthält“, Zeit → Sekunden, Faktor, umkehren; Rückgabe (Wert, Hinweis)."""
    if raw is None:
        return None, ""
    v, how = raw, ""
    t = spec.get("tf") or {}
    m = spec.get("map") or {}
    if str(raw) in m and m[str(raw)] not in ("", None):
        v = m[str(raw)]
    else:
        for needle, val in t.get("contains") or []:
            if needle and str(needle).lower() in str(raw).lower():
                v = val
                break
        else:
            if enum and raw not in ("",):           # nicht angelernter Wert → über die Wortmuster schätzen
                g = next((e for e in enum if re.search(learn._words(ptype).get(e, "^$"), str(raw).lower())), None)
                if g:
                    v, how = g, "geschätzt"
    if t.get("seconds") and isinstance(v, str) and re.fullmatch(r"\d+:\d{2}(:\d{2})?", v.strip()):
        parts = [int(x) for x in v.strip().split(":")]
        v = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]
    if t.get("factor") not in (None, "", 0, "0"):
        try:
            v = round(float(v) * float(t["factor"]), 2)
        except (TypeError, ValueError):
            pass
    if t.get("invert"):
        v = {"on": "off", "off": "on", True: False, False: True, "1": "0", "0": "1", 1: 0, 0: 1}.get(v, v)
    return v, how


def _raw(spec: dict, fam: dict, root: int, varvals: dict, props: dict):
    role = spec.get("role") or "driver"
    iid = fam.get(role, root)
    key = spec.get("key")
    if spec.get("src") == "var":
        v = varvals.get((iid, key))
        return v if v is not None else learn._last.get((iid, key))
    if spec.get("src") == "prop":
        v = learn._last.get((root, f"prop:{key}"))
        return v if v is not None else props.get(key)
    return learn._last.get((iid, key))


def resolve(prof: dict, fam: dict, root: int, varvals: dict, props: dict) -> dict:
    ptype = prof.get("type", "")
    sch = learn.SCHEMAS.get(ptype, {"fields": {}})
    fields = {}
    for f, spec in (prof.get("fields") or {}).items():
        raw = _raw(spec, fam, root, varvals, props)
        val, how = tf(raw, spec, ptype, (sch["fields"].get(f) or {}).get("enum"))
        fields[f] = {"raw": raw, "value": val, "guess": how, "from": f"{spec.get('src')} {spec.get('key')} @ {spec.get('role')}"}
    infos = []
    for spec in prof.get("infos") or []:
        raw = _raw(spec, fam, root, varvals, props)
        val, _ = tf(raw, spec)
        kind = spec.get("kind") or "text"
        warn = False
        if kind == "battery":
            if isinstance(val, bool) or str(val).lower() in ("true", "false"):
                warn = str(val).lower() == "true" and "low" in str(spec.get("key", "")).lower()
            else:
                try:
                    warn = float(val) < float(spec.get("warn") or 20)
                except (TypeError, ValueError):
                    pass
        infos.append({"kind": kind, "label": spec.get("label") or spec.get("key"), "value": val,
                      "unit": INFO_KINDS.get(kind, {}).get("unit", ""), "place": spec.get("place") or "detail",
                      "warn": warn})
    return {"fields": fields, "infos": infos}


# ---------------------------------------------------------------- Profile → Geräte
def _role_of(i: dict, root: int) -> str:
    return "driver" if i["id"] == root else f"proxy:{i.get('proxy')}"


async def apply(inv, items: list, owned: Callable[[int], bool]) -> int:
    """Profile auf das Projekt anwenden: angelernte Geräte ins Inventar (nur im Speicher; nach jedem Import neu)."""
    st = learn._load()
    for i in [k for k, d in inv.devices.items() if d.get("how") == "profile"]:
        inv.devices.pop(i, None)
    _dev.clear()
    _fam_dev.clear()
    _skip.clear()
    by_root: dict = {}
    for i in items:
        if i.get("typeName") == "device" and isinstance(i.get("id"), int):
            by_root.setdefault(i.get("protocolId") or i["id"], []).append(i)
    for key, prof in st["profiles"].items():
        if prof.get("reference"):
            continue
        kind, _, val = key.partition(":")
        roots = {r for r, fam in by_root.items() for i in fam
                 if (kind == "proxy" and i.get("proxy") == val) or (kind == "driver" and learn._stem(i) == val)}
        for r in roots:
            fam = sorted(by_root[r], key=lambda i: (i["id"] != r, i["id"]))
            ign = next((k for k in (f"item:{r}", *(f"driver:{learn._stem(i)}" for i in fam)) if k in st["ignored"]), None)
            if ign:                                        # im Admin ausgeblendet („nicht nötig“)
                _skip[r] = f"ausgeblendet ({ign}: {st['ignored'][ign] or 'nicht nötig'})"
                continue
            mod = [i for i in fam if owned(i["id"])]
            std = [inv.devices[i["id"]] for i in fam if i["id"] in inv.devices]
            if mod or std:                                 # Standard-Gerät oder Modul hat Vorrang
                _skip[r] = (f"Modul hat Vorrang ({mod[0].get('name')} #{mod[0]['id']})" if mod else
                            f"schon als Gerät angezeigt ({std[0].get('name')} #{std[0]['id']}, {std[0].get('type')})")
                continue
            feeds = await learn._feeds(r)                  # speist über eine Steuer-Bindung schon ein Gerät
            if feeds:
                _skip[r] = "speist schon " + ", ".join(f"{f['name']} #{f['id']} ({f['type']})" for f in feeds[:2])
                continue
            want = prof.get("device_role") or "driver"
            dev = next((i for i in fam if _role_of(i, r) == want and i["id"] != r), None) \
                or next((i for i in fam if _role_of(i, r) == want), None) or fam[0]
            roles = {}
            for i in fam:
                roles.setdefault(_role_of(i, r), i["id"])
            inv.devices[dev["id"]] = {
                "id": dev["id"], "name": dev.get("name", ""), "type": "learned", "ltype": prof.get("type"),
                "room_id": dev.get("roomId"), "room": dev.get("roomName", ""), "proxy": dev.get("proxy"),
                "driver": learn._stem(dev), "how": "profile", "key": key, "driver_wrapper": False,
                "parent": dev.get("parentId")}
            _dev[dev["id"]] = {"key": key, "fam": roles, "root": r, "room_id": dev.get("roomId")}
            for i in fam:
                _fam_dev[i["id"]] = dev["id"]
    learn._x["learned_ids"] = {str(i) for i in _fam_dev}
    if _dev or _skip:
        logbus.info("learn", f"{len(_dev)} angelernte Geräte aus {len(st['profiles'])} Profil(en) übernommen"
                             + (f"; {len(_skip)} nicht (Dublettenschutz): " + "; ".join(
                                 f"#{r} {w}" for r, w in list(_skip.items())[:5]) if _skip else ""))
    return len(_dev)


def on_message(dev: int) -> None:
    """Push einer angelernten Familie → Raum veraltet + Oberfläche benachrichtigen; Werte demnächst sichern."""
    global _dirty
    d = _dev.get(_fam_dev.get(dev))
    cache = learn._x.get("cache")
    if not d or not cache:
        return
    cache.invalidate_room("learned", d["room_id"])
    cache._queue_notify(devs={"learned": {d["room_id"]}})
    _dirty = True
    _persist_soon()


def _persist_soon() -> None:
    """Höchstens alle 20 s auf die Platte – eine Änderung innerhalb dieser Zeit wird mit dem nächsten Sichern
    erfasst (früher ging die letzte Meldung verloren, wenn sie kurz nach dem vorigen Sichern kam)."""
    global _persist_pending
    if _persist_pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _persist()
        return
    _persist_pending = True
    loop.call_later(20, _persist)


def _persist() -> None:
    global _dirty, _persist_pending
    _persist_pending = False
    if not _dirty:
        return
    _dirty = False
    data = {f"{i}|{k}": v for (i, k), v in learn._last.items() if i in _fam_dev and not isinstance(v, (dict, list))}
    try:
        from .auth import atomic_write
        atomic_write(LAST_FILE, json.dumps(data, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logbus.warn("learn", f"Werte nicht gesichert: {e}")


def load_last() -> None:
    try:
        for k, v in json.loads(LAST_FILE.read_text(encoding="utf-8")).items():
            i, _, key = k.partition("|")
            learn._last.setdefault((int(i), key), v)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- Zustand lesen
async def _state_all() -> dict:
    x = learn._x
    st = learn._load()
    names = sorted({s["key"] for d in _dev.values() for s in list((st["profiles"].get(d["key"]) or {}).get("fields", {}).values())
                    + ((st["profiles"].get(d["key"]) or {}).get("infos") or []) if s.get("src") == "var"})
    varvals = {}
    if names:
        try:
            rows = await x["conn"].get("/api/v1/items/variables?varnames="
                                       + ",".join(urllib.parse.quote(n, safe="") for n in names))
            varvals = {(r.get("id"), r.get("varName")): r.get("value") for r in rows if isinstance(r, dict)}
        except Exception as e:  # noqa: BLE001
            logbus.warn("learn", f"Variablen nicht lesbar: {e}")
    out = {}
    for did, d in _dev.items():
        prof = st["profiles"].get(d["key"]) or {}
        props = {}
        specs = list((prof.get("fields") or {}).values()) + (prof.get("infos") or [])
        if any(s.get("src") == "prop" and learn._last.get((d["root"], f"prop:{s.get('key')}")) is None for s in specs):
            root = d["root"]
            try:
                ps = await x["cache"].get(f"learn_props:{root}", lambda r=root: x["conn"].get(f"/api/v1/items/{r}/properties"),
                                          project=True)
                props = {p.get("name"): p.get("value") for p in ps if isinstance(p, dict)}
            except Exception:  # noqa: BLE001
                props = {}
        r = resolve(prof, d["fam"], d["root"], varvals, props)
        vals = {f: v["value"] for f, v in r["fields"].items()}
        state = vals.get("state")
        active = None if state in (None, "") else state not in ("off", "stop", False, "0", 0)
        if active is None and prof.get("type") in ("light", "blind") and vals.get("level") not in (None, ""):
            try:
                active = float(vals["level"]) > 0
            except (TypeError, ValueError):
                pass
        out[did] = {"id": did, "ltype": prof.get("type"), "room_id": d["room_id"], "state": state, "active": active,
                    "known": state is not None or not (prof.get("fields") or {}).get("state"),
                    "values": vals, "infos": r["infos"], "actions": sorted((prof.get("actions") or {}).keys())}
    return out


@router.get("/state")
async def state(room_id: Optional[int] = None):
    if not _dev:
        return []
    allv = await learn._x["cache"].get("learned", _state_all, room=room_id)
    return [v for v in allv.values() if room_id is None or v["room_id"] == room_id]


class ActBody(BaseModel):
    value: Optional[str] = None


@router.post("/{device_id}/{action}")
async def act(device_id: int, action: str, body: ActBody = ActBody()):
    d = _dev.get(device_id)
    if not d:
        raise HTTPException(404, "kein angelerntes Gerät")
    prof = learn._load()["profiles"].get(d["key"]) or {}
    a = (prof.get("actions") or {}).get(action)
    if not a:
        raise HTTPException(404, "Aktion nicht im Profil")
    target = d["fam"].get(a.get("target_role") or a.get("role") or "driver", d["root"])
    params = {a["param"]: body.value} if a.get("param") and body.value is not None else {}
    await learn._x["conn"].post(f"/api/v1/items/{target}/commands", a.get("command"), params)
    learn._x["cache"].invalidate_room("learned", d["room_id"])
    return {"ok": True, "command": a.get("command"), "target": target}
