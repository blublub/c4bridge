"""BluOS-Player (Bluesound/NAD) direkt über ihre HTTP-API (Port 11000, GET, XML).

Warum direkt und nicht über die Control4-Treiber: deren Lua ist verschlüsselt, und das Browsen läuft nur über
Navigator-Bildschirme der Touchpanels.

Selbstkonfiguration (`refresh`, nach dem Inventar-Import und alle 30 min, nur lesend):
- Player finden: mDNS `_musc._tcp.local` + die Adressen aus den Control4-Treibern; jeder erreichbare Player meldet
  über /SyncStatus Name, Modell, MAC, Firmware und ob die Lautstärke fest ist, über /Browse seine Dienste/Eingänge.
- Control4-Treiber (bluesound_media_service.c4z) ↔ Player über die MAC (Eigenschaft „Device MAC Address"), sonst
  „Device ID" (IP), sonst gleicher Name. So bleibt die Zuordnung auch bei neuer DHCP-Adresse richtig.
- Raum ↔ Player über den Endpunkt (Kind-Proxy amplifier) laut Composer: AUDIO_SELECTION/AUDIO_VOLUME-Bindung zum
  Raum – ein Player kann mehrere Räume versorgen. Fallbacks (mit Warnung): Raum des Treibers, dann ein noch
  freier Player mit dem Namen des Raums. `overrides` in data/bluos.json (Raum-ID → IP) gehen allem vor.
- Musikquellen: die Control4-Dienst-Treiber (TuneIn, Bibliothek, Qobuz …) werden dem BluOS-Dienst (browseKey) des
  Players zugeordnet – über den Treiber-Dateinamen, sonst den Namen; geprüft gegen das, was der Player anbietet.
- Bibliothek: BluOS kennt Alben über Titel + Interpret (gleiche NAS-Sammlung wie Control4 „Unsere Musik"),
  daher spielt die Bridge Alben aus ihrem eigenen Index ab; „ab Titel n" nativ über listindex.
"""
import asyncio
import json
import re
import socket
import struct
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Optional

import aiohttp
import yarl

from . import logbus
from .config import DATA_DIR

STATE_FILE = DATA_DIR / "bluos.json"
PORT = 11000
TIMEOUT = aiohttp.ClientTimeout(total=10)
# Nur diese Pfade darf die Oberfläche an einen Player schicken (aus Browse-Ergebnissen: playURL/actionURL)
PLAY_PATHS = ("/Add?", "/Play?", "/Load?", "/Preset?", "/AddFavourite?", "/RemoveFavourite?", "/Action?")


# ---------------------------------------------------------------- mDNS-Suche
def _qname(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def _read_name(data: bytes, off: int):
    labels, jumped, end = [], False, off
    for _ in range(64):
        ln = data[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            ptr = struct.unpack(">H", data[off:off + 2])[0] & 0x3FFF
            if not jumped:
                end = off + 2
            off, jumped = ptr, True
            continue
        labels.append(data[off + 1:off + 1 + ln].decode("utf-8", "replace"))
        off += 1 + ln
    return ".".join(labels), (end if jumped else off)


def _local_ips() -> list:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:   # Adresse der Standardroute (ohne Paket zu senden)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


def discover(wait_s: float = 3.0) -> dict:
    """mDNS-Anfrage auf allen Netzwerkkarten → {Name: IP} der BluOS-Player."""
    q = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + _qname("_musc._tcp.local") + struct.pack(">HH", 12, 1)
    found: dict = {}
    socks = []
    for ip in _local_ips():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.settimeout(0.3)
            s.sendto(q, ("224.0.0.251", 5353))
            socks.append(s)
        except OSError:
            continue
    t0 = time.time()
    while socks and time.time() - t0 < wait_s:
        for s in socks:
            try:
                data, addr = s.recvfrom(9000)
            except (socket.timeout, OSError):
                continue
            try:
                _, _, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
                off = 12
                for _ in range(qd):
                    _, off = _read_name(data, off)
                    off += 4
                for _ in range(an + ns + ar):
                    name, off = _read_name(data, off)
                    rtype, _, _, rdlen = struct.unpack(">HHIH", data[off:off + 10])
                    off += 10
                    if rtype == 12 and name == "_musc._tcp.local":
                        inst = _read_name(data, off)[0]
                        found[inst.split("._musc")[0]] = addr[0]
                    off += rdlen
            except Exception:  # noqa: BLE001
                continue
    for s in socks:
        s.close()
    return found


# ---------------------------------------------------------------- XML-Hilfen
def _xml(text: str) -> ET.Element:
    return ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)


def _status_dict(root: ET.Element) -> dict:
    g = lambda t: (root.findtext(t) or "").strip()  # noqa: E731
    state = g("state")
    return {
        "state": state,                                    # play | stream | pause | stop | connecting
        "play_state": {"play": "play", "stream": "play", "pause": "pause", "stop": "stop"}.get(state, state),
        "volume": int(g("volume") or 0) if g("volume").lstrip("-").isdigit() else 0,
        "mute": g("mute") == "1",
        "service": g("service"), "title": g("title1"), "artist": g("title2") or g("artist"),
        "album": g("title3") or g("album"), "image": g("image"),
        "secs": int(g("secs") or 0) if g("secs").isdigit() else 0,
        "total": int(float(g("totlen") or 0)) if g("totlen").replace(".", "", 1).isdigit() else 0,
        "song": int(g("song") or 0) if g("song").isdigit() else 0,
        "shuffle": g("shuffle") == "1", "repeat": g("repeat"),
        "etag": root.get("etag", ""),
        "input": g("inputId") or (g("title1") if g("service") == "Capture" else ""),
    }


def _items(root: ET.Element) -> list:
    out = []
    for it in root.iter("item"):
        a = it.attrib
        out.append({k: v for k, v in {
            "text": a.get("text", ""), "text2": a.get("text2", ""), "type": a.get("type", ""),
            "browse": a.get("browseKey"), "play": a.get("playURL"), "autoplay": a.get("autoplayURL"),
            "action": a.get("actionURL"), "menu": a.get("contextMenuKey"), "image": a.get("image"),
            "input": a.get("inputType"), "tracks": a.get("tracks"), "duration": a.get("duration"),
        }.items() if v})
    return out


# ---------------------------------------------------------------- Player-Verwaltung
PLAYER_DRIVER = "bluesound_media_service"
# Control4-Dienst-Treiber (Dateiname ohne Endung) → BluOS-browseKey; unbekannte über den Namen des Dienstes
SERVICE_KEYS = {"bluesound_tunein": "TuneIn:", "bluesound_library": "LocalMusic:", "bluesound_playlists": "BluOS:",
                "bluesound_qobuz": "Qobuz:", "bluesound_amazon": "Amazon:", "bluesound_iheartradio": "iHeartRadio:",
                "bluesound_tidal": "Tidal:", "bluesound_spotify": "Spotify:", "bluesound_deezer": "Deezer:",
                "bluesound_napster": "Napster:", "bluesound_radioparadise": "RadioParadise:",
                "bluesound_sirius": "SiriusXM:", "bluesound_soundcloud": "SoundCloud:"}
_norm = lambda s: "".join(ch for ch in str(s or "").lower() if ch.isalnum())  # noqa: E731


def _props(lst) -> dict:
    return {x.get("name"): x.get("value") for x in lst if isinstance(x, dict)} if isinstance(lst, list) else {}


class BluOS:
    def __init__(self):
        self.players: dict = {}        # Name -> IP (mDNS)
        self.devices: dict = {}        # MAC -> Player laut /SyncStatus {ip, name, model, ..., services, inputs, ok}
        self.drivers: list = []        # Control4-Player-Treiber mit Endpunkt, Räumen, aufgelöster IP
        self.services: dict = {}       # Control4-Quelle (Proxy-ID) -> {name, key}
        self.rooms: dict = {}          # room_id -> {ip, name, mac, model, how, proxy_id, endpoint_id, sources, ...}
        self.overrides: dict = {}      # room_id -> IP (Hand-Zuordnung)
        self.warnings: list = []
        self.updated = 0.0
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.players = st.get("players", {})
            self.devices = st.get("devices", {})
            self.drivers = st.get("drivers", [])
            self.services = {int(k): v for k, v in st.get("services", {}).items()}
            self.rooms = {int(k): v for k, v in st.get("rooms", {}).items()}
            self.overrides = {int(k): v for k, v in st.get("overrides", {}).items()}
            self.warnings = st.get("warnings", [])
            self.updated = st.get("updated", 0.0)
        except Exception:  # noqa: BLE001
            pass

    def _save(self) -> None:
        from .auth import atomic_write
        atomic_write(STATE_FILE, json.dumps({
            "players": self.players, "devices": self.devices, "drivers": self.drivers, "services": self.services,
            "rooms": self.rooms, "overrides": self.overrides, "warnings": self.warnings, "updated": self.updated,
        }, ensure_ascii=False, indent=1))

    async def _s(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT)
        return self._session

    async def probe(self, ip: str) -> Optional[dict]:
        """Player unter ip befragen (/SyncStatus + oberste Browse-Ebene) – None, wenn nicht erreichbar."""
        try:
            s = await self._s()
            async with s.get(f"http://{ip}:{PORT}/SyncStatus", timeout=aiohttp.ClientTimeout(total=3)) as r:
                r.raise_for_status()
                root = _xml(await r.text())
        except Exception:  # noqa: BLE001
            return None
        a = root.attrib
        mac = (a.get("mac") or "").upper()
        if not mac:
            return None
        d = {"ip": ip, "mac": mac, "name": a.get("name", ""), "model": a.get("model", ""),
             "model_name": a.get("modelName", ""), "brand": a.get("brand", ""), "class": a.get("class", ""),
             "version": a.get("version", ""), "fixed_volume": a.get("volume") == "-1",
             "zone": root.findtext("zone") or "", "master": root.findtext("master") or "",
             "slaves": [x.get("id") for x in root.iter("slave")], "services": [], "inputs": [], "ok": True,
             "seen": time.time()}
        try:
            top = (await self.browse(ip))["items"]
            d["services"] = [{"key": i["browse"], "text": i.get("text", "")} for i in top if i.get("browse")]
            d["inputs"] = [{"text": i.get("text", ""), "type": i.get("input", ""), "play": i["play"]}
                           for i in top if i.get("input") and i.get("play")]
        except Exception:  # noqa: BLE001
            pass
        return d

    async def refresh(self, conn, room_names: Optional[dict] = None) -> None:
        """Player suchen, mit den Control4-Treibern abgleichen und den Räumen zuordnen (nur lesend)."""
        async with self._lock:
            await self._refresh(conn, room_names or {})

    async def _refresh(self, conn, room_names: dict) -> None:
        warn: list = []
        found = await asyncio.to_thread(discover)
        if found:
            self.players.update(found)
        try:
            items = await conn.get("/api/v1/items")
        except Exception as e:  # noqa: BLE001
            logbus.warn("bluos", f"Items nicht lesbar: {e}")
            return
        by_id = {i.get("id"): i for i in items}
        by_parent: dict = {}
        for i in items:
            by_parent.setdefault(i.get("parentId"), []).append(i)
        stem = lambda i: str(i.get("filename", "")).rsplit(".", 1)[0].lower()  # noqa: E731
        child = lambda pid, proxy: next((c for c in by_parent.get(pid, []) if c.get("proxy") == proxy), None)  # noqa: E731

        # --- Control4-Player-Treiber: Eigenschaften + Endpunkt mit Raum-Bindungen
        # Player-Treiber: Dateiname bluesound_media_service*, oder (anderer/umbenannter BluOS-Treiber) ein
        # media_service-Treiber mit Endpunkt-Kind (amplifier) und BluOS-Eigenschaften (Proxy-Struktur statt Name)
        drivers = []
        cands = [i for i in items if i.get("typeName") == "device" and i.get("proxy") == "media_service"
                 and i.get("parentId") != i.get("id") and child(i["id"], "amplifier")
                 and not stem(i).startswith("bluesound_zone")]
        cands += [i for i in items if stem(i).startswith(PLAYER_DRIVER) and i not in cands]
        for d in cands:
            try:
                p = _props(await conn.get(f"/api/v1/items/{d['id']}/properties"))
            except Exception:  # noqa: BLE001
                p = {}
            if not stem(d).startswith(PLAYER_DRIVER) and not any(k.startswith("Device BluOS") for k in p):
                continue                               # Endpunkt-Treiber eines anderen Herstellers
            ms, ep = child(d["id"], "media_service"), child(d["id"], "amplifier")
            ep_rooms: list = []
            if ep:
                try:
                    for b in await conn.get(f"/api/v1/items/{ep['id']}/bindings"):
                        if b.get("bindingClass") in ("AUDIO_SELECTION", "AUDIO_VOLUME"):
                            for c in b.get("connections") or []:
                                rid = c.get("roomId") or c.get("id")
                                if rid and rid not in ep_rooms:
                                    ep_rooms.append(rid)
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(0.1)                     # Director schonen
            dev_id = str(p.get("Device ID") or "")
            drivers.append({
                "driver_id": d["id"], "proxy_id": (ms or {}).get("id"), "endpoint_id": (ep or {}).get("id"),
                "endpoint_name": (ep or {}).get("name", ""), "c4_name": p.get("Device Name") or p.get("Assigned Device")
                or (ms or d).get("name", ""), "c4_mac": str(p.get("Device MAC Address") or "").upper(),
                "c4_address": dev_id.split(":")[0], "c4_model": p.get("Device Model", ""),
                "c4_online": str(p.get("Network Status", "")).lower() == "online",
                "group_role": p.get("Fixed Group Role", ""), "driver_room": d.get("roomId"),
                "ep_rooms": ep_rooms, "ip": None, "mac": None, "how_ip": ""})

        # --- alle Kandidaten befragen: mDNS, Control4-Adressen, bisher bekannte
        ips = set(self.players.values()) | {x["c4_address"] for x in drivers if x["c4_address"]} \
            | {v.get("ip") for v in self.devices.values() if v.get("ip")} | set(self.overrides.values())
        res = await asyncio.gather(*(self.probe(ip) for ip in sorted(i for i in ips if i)))
        devices = {d["mac"]: d for d in res if d}
        for mac, old in self.devices.items():             # gerade nicht erreichbar → als offline behalten
            if mac not in devices:
                devices[mac] = {**old, "ok": False}
        self.players.update({d["name"]: d["ip"] for d in devices.values() if d.get("ok") and d.get("name")})

        # --- Treiber ↔ Player: MAC, sonst IP, sonst Name
        claimed: set = set()
        for x in drivers:
            dv = devices.get(x["c4_mac"]) if x["c4_mac"] else None
            how = "MAC" if dv and dv.get("ok") else ""
            if not how:
                dv = next((v for v in devices.values() if v.get("ok") and v["ip"] == x["c4_address"]), None)
                how = "IP" if dv else ""
            if not how:
                dv = next((v for v in devices.values() if v.get("ok") and _norm(v["name"]) == _norm(x["c4_name"])), None)
                how = "Name" if dv else ""
            if dv and how:
                x.update(ip=dv["ip"], mac=dv["mac"], how_ip=how)
                claimed.add(dv["mac"])
                if dv["ip"] != x["c4_address"]:
                    warn.append(f"„{x['c4_name']}“: Control4 kennt {x['c4_address'] or '–'}, der Player ist jetzt "
                                f"unter {dv['ip']} erreichbar (DHCP?) – Bridge nutzt {dv['ip']}.")
                if how == "Name":
                    warn.append(f"„{x['c4_name']}“: MAC laut Control4 ({x['c4_mac'] or '–'}) passt nicht, Player "
                                f"„{dv['name']}“ über den Namen gefunden.")
            else:
                warn.append(f"Player „{x['c4_name']}“ (Treiber #{x['driver_id']}, {x['c4_address'] or '–'}, "
                            f"MAC {x['c4_mac'] or '–'}) ist nicht erreichbar.")
            if x["endpoint_id"] and not x["ep_rooms"]:
                warn.append(f"Endpunkt „{x['endpoint_name']}“ (#{x['endpoint_id']}) ist in Composer keinem Raum "
                            f"als Audio-Endpunkt zugeordnet.")

        # --- Control4-Dienste (Musikquellen) → BluOS-browseKey
        services: dict = {}
        all_keys = {s["key"]: s["text"] for v in devices.values() for s in v.get("services", [])}
        for d in items:
            st = stem(d)
            if not st.startswith(("bluesound_", "bluos_")) or st.startswith((PLAYER_DRIVER, "bluesound_zone")):
                continue
            ms = child(d["id"], "media_service")
            if not ms:
                continue
            short = st.split("_", 1)[1] if "_" in st else st          # bluesound_qobuz → qobuz
            key = SERVICE_KEYS.get(st) or next((k for k, t in all_keys.items()
                                                if _norm(t) and _norm(t) in (_norm(ms.get("name")), _norm(short))), None)
            services[ms["id"]] = {"name": ms.get("name", ""), "key": key, "driver": st}
            if not key:
                warn.append(f"Control4-Quelle „{ms.get('name')}“ ({st}) hat keinen passenden BluOS-Dienst.")
            elif all_keys and key not in all_keys:
                warn.append(f"Control4-Quelle „{ms.get('name')}“: Dienst {key.rstrip(':')} ist auf keinem Player "
                            f"eingerichtet.")

        # --- Räume: Composer-Endpunkt → Raum des Treibers → freier Player mit Raumnamen → Override
        rooms: dict = {}

        def put(rid, x, how):
            dv = devices.get(x.get("mac") or "") or {}
            svc = {sid: s["key"] for sid, s in services.items()
                   if s["key"] and any(k["key"] == s["key"] for k in dv.get("services", []))}
            rooms[rid] = {"ip": x.get("ip"), "name": dv.get("name") or x.get("c4_name", ""), "mac": x.get("mac"),
                          "model": dv.get("model_name") or x.get("c4_model", ""), "brand": dv.get("brand", ""),
                          "fixed_volume": bool(dv.get("fixed_volume")), "how": how,
                          "c4_name": x.get("c4_name", ""), "c4_address": x.get("c4_address", ""),
                          "c4_online": x.get("c4_online", False), "proxy_id": x.get("proxy_id"),
                          "endpoint_id": x.get("endpoint_id"), "driver_id": x.get("driver_id"),
                          "room": room_names.get(rid) or (by_id.get(rid) or {}).get("name", ""), "sources": svc}

        for x in drivers:
            for rid in x["ep_rooms"]:
                if rid not in rooms or not rooms[rid].get("ip"):
                    put(rid, x, "Composer")
        for x in drivers:
            rid = x["driver_room"]
            if rid and not x["ep_rooms"] and (rid not in rooms or not rooms[rid].get("ip")):
                put(rid, x, "Raum des Treibers")
        for dv in devices.values():
            if dv["mac"] in claimed or not dv.get("ok"):
                continue
            rid = next((r for r, n in room_names.items() if _norm(n) == _norm(dv["name"])), None)
            if rid and (rid not in rooms or not rooms[rid].get("ip")):
                old = rooms.get(rid, {})
                put(rid, {**{k: old.get(k) for k in ("c4_name", "c4_address", "c4_online", "proxy_id", "endpoint_id",
                                                       "driver_id")}, "ip": dv["ip"], "mac": dv["mac"]}, "Name")
                claimed.add(dv["mac"])
                warn.append(f"Raum „{room_names[rid]}“: Player „{dv['name']}“ ({dv['ip']}) nur über den Namen "
                            f"zugeordnet – in Control4 gehört kein erreichbarer Player-Treiber zu diesem Raum.")
        for dv in devices.values():
            if dv.get("ok") and dv["mac"] not in claimed:
                warn.append(f"Player „{dv['name']}“ ({dv['ip']}, {dv.get('model_name')}) gehört zu keinem Raum.")
        for rid, ip in self.overrides.items():
            dv = next((v for v in devices.values() if v.get("ip") == ip), {})
            old = rooms.get(rid, {})
            put(rid, {**old, "ip": ip, "mac": dv.get("mac"), "c4_name": old.get("c4_name", "")}, "Hand")

        self.devices, self.drivers, self.services = devices, drivers, services
        self.rooms, self.warnings, self.updated = rooms, warn, time.time()
        self._save()
        ok = sum(1 for r in rooms.values() if r.get("ip"))
        n_ok = sum(1 for d in devices.values() if d.get("ok"))
        logbus.info("bluos", f"{n_ok} Player erreichbar, {ok}/{len(rooms)} Räume zugeordnet, {len(warn)} Hinweise")

    def room(self, room_id: int) -> Optional[dict]:
        r = self.rooms.get(room_id)
        return r if r and r.get("ip") else None

    def dead_sources(self, room_id: int) -> list:
        """Control4-Quellen, die in diesem BluOS-Raum nichts bringen: Dienst am Player nicht eingerichtet bzw. ohne
        BluOS-Gegenstück, oder Player-Quelle eines nicht erreichbaren Players (z. B. „Player Kellerbad")."""
        have = (self.rooms.get(room_id) or {}).get("sources") or {}
        dead = [sid for sid in self.services if sid not in have]
        dead += [x["proxy_id"] for x in self.drivers if x.get("proxy_id") and not x.get("ip")]
        return dead

    # ---------------------------------------------------------------- API
    async def get(self, ip: str, path: str) -> ET.Element:
        s = await self._s()
        # Adresse UNVERÄNDERT senden: aiohttp/yarl dekodiert sonst %3A/%2F/%3F in Adressen, die der Player selbst
        # geliefert hat (/Play?url=TuneIn%3As57109&image=http%3A%2F%2F…) → Player meldete „404 Not Found TuneIn:s57109“,
        # obwohl derselbe Sender über die BluOS-App spielt. Nur Zeichen, die in einer Adresse nie roh stehen dürfen
        # (Leerzeichen in Qobuz-Adressen „expr=Set fire …“), werden kodiert.
        raw = re.sub(r'[ "<>`{}|\\^]', lambda m: urllib.parse.quote(m.group(0)), path)
        url = yarl.URL(f"http://{ip}:{PORT}{raw}", encoded=True)
        for attempt in range(2):   # Dienste wie „Radio" antworten beim ersten Aufruf gelegentlich mit 5xx
            async with s.get(url) as r:
                if r.status >= 500 and attempt == 0:
                    await asyncio.sleep(0.8)
                    continue
                r.raise_for_status()
                return _xml(await r.text())

    async def status(self, ip: str) -> dict:
        return _status_dict(await self.get(ip, "/Status"))

    async def volume(self, ip: str, level: int) -> int:
        root = await self.get(ip, f"/Volume?level={max(0, min(100, int(level)))}")
        return int(root.text or level) if (root.text or "").strip().isdigit() else int(level)

    async def mute(self, ip: str, on: bool) -> None:
        await self.get(ip, f"/Volume?mute={1 if on else 0}")

    REPEAT_STATE = {"all": 0, "one": 1, "off": 2}      # /Status → repeat: 0 = Warteschlange, 1 = Titel, 2 = aus

    async def shuffle(self, ip: str, on: bool) -> None:
        await self.get(ip, f"/Shuffle?state={1 if on else 0}")

    async def repeat(self, ip: str, mode: str) -> None:
        await self.get(ip, f"/Repeat?state={self.REPEAT_STATE[mode]}")

    async def seek(self, ip: str, secs: int) -> None:
        await self.get(ip, f"/Play?seek={max(0, int(secs))}")

    async def transport(self, ip: str, cmd: str) -> None:
        path = {"PLAY": "/Play", "PAUSE": "/Pause", "STOP": "/Stop", "SKIP_FWD": "/Skip", "SKIP_REV": "/Back"}[cmd]
        await self.get(ip, path)

    async def browse(self, ip: str, key: str = "", q: str = "") -> dict:
        path = "/Browse" + ("?key=" + urllib.parse.quote(key, safe="") if key else "")
        if q:
            path += ("&" if key else "?") + "q=" + urllib.parse.quote(q, safe="")
        root = await self.get(ip, path)
        return {"service": root.get("serviceName", ""), "type": root.get("type", ""),
                "search": root.get("searchKey"), "next": root.get("nextKey"),
                "play": root.get("playURL"), "items": _items(root)}

    async def play(self, ip: str, url: str) -> dict:
        """playURL/autoplayURL/actionURL aus einem Browse-Ergebnis ausführen (nur erlaubte Pfade)."""
        if not url.startswith(PLAY_PATHS):
            raise ValueError("nicht erlaubter Player-Befehl")
        root = await self.get(ip, url)
        return {"tag": root.tag, **{k: v for k, v in root.attrib.items() if k in ("length", "id", "count")}}

    # ---------------------------------------------------------------- Streaming per Sprache (Qobuz, Radio = TuneIn)
    STREAM_ROOT = {"qobuz": "Qobuz:", "radio": "TuneIn:"}

    async def qobuz_candidates(self, ip: str, q: str, cats: tuple = ("Songs", "Albums", "Artists"), n: int = 18) -> list:
        """Kandidaten für die Auswahl durch das Sprachmodell (PLAN-AUSWAHL.md): erste Trefferseite je Kategorie, kompakt
        (Titel · Interpret · Album, ohne Komponist), Doppelte (gleicher Titel+Interpret+Album) zusammengefasst.
        Rückgabe [{type, title, artist, album, url}] – url = Abspiel-Adresse des Players (nur lesend geholt)."""
        root = self.STREAM_ROOT["qobuz"]
        r0 = await self.browse(ip, f"{root}Search", q)
        links = {it.get("text"): it.get("browse") for it in r0["items"]}
        per = {"Songs": 10, "Albums": 8, "Artists": 3} if len(cats) > 1 else {c: n for c in cats}
        typ = {"Songs": "Titel", "Albums": "Album", "Artists": "Interpret"}
        out, seen = [], set()
        for cat in cats:
            if not links.get(cat):
                continue
            k = 0
            for it in (await self.browse(ip, links[cat]))["items"]:
                url = it.get("autoplay") or it.get("play")
                if cat == "Artists":
                    url = url or it.get("browse")          # Interpret: wird über find_stream(kind=artist) gespielt
                if not url:
                    continue
                parts = [p.strip() for p in str(it.get("text2", "")).split("•")]
                artist = parts[1] if len(parts) >= 3 else parts[0]
                album = parts[2] if len(parts) >= 3 else ""
                title = it.get("text", "")
                key = (cat, _norm(title), _norm(artist), _norm(album))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"type": typ[cat], "title": title, "artist": artist if cat != "Artists" else title,
                            "album": album, "url": url if cat != "Artists" else ""})
                k += 1
                if k >= per.get(cat, n):
                    break
        return out[:n]

    async def qobuz_albums(self, ip: str, artist: str, limit: int = 8) -> list:
        """Alben eines Interpreten auf Qobuz (nur lesend, über die Suche des Players) – Ausgaben wie „(Deluxe)“,
        „(Remastered)“ zählen als ein Album. Rückgabe: Titel in Qobuz-Reihenfolge."""
        from rapidfuzz import fuzz
        r0 = await self.browse(ip, f"{self.STREAM_ROOT['qobuz']}Search", artist)
        link = next((it.get("browse") for it in r0["items"] if it.get("text") == "Albums"), None)
        items = (await self.browse(ip, link))["items"] if link else []
        out, seen = [], set()
        for it in items:
            if fuzz.WRatio(_norm(artist), _norm(it.get("text2", ""))) < 88:
                continue
            title = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", it.get("text", "")).strip()
            if title and _norm(title) not in seen:
                seen.add(_norm(title))
                out.append(title)
        return out[:limit]

    async def find_stream(self, ip: str, service: str, q: str, kind: str = "any", skip: Optional[dict] = None) -> dict:
        """Sprachwunsch → EIN abspielbarer Eintrag beim Dienst (über die Suche des Players, nur lesend).
        service: qobuz | radio (TuneIn); kind: station | artist | album | song | any. Rückgabe {title, sub, url, kind}
        oder LookupError. „Titel von Interpret“ wird getrennt bewertet."""
        from rapidfuzz import fuzz
        root = self.STREAM_ROOT.get(service)
        if not root:
            raise LookupError(f"Dienst {service} unbekannt")
        top = await self.browse(ip)
        if not any(it.get("browse") == root for it in top["items"]):
            raise LookupError(f"{'Qobuz' if service == 'qobuz' else 'TuneIn'} ist auf diesem Player nicht eingerichtet")
        # Gesprochen → wie Dienste es schreiben: „Die drei Fragezeichen“ = „Die drei ???“; Füllwörter („letzte Folge“) raus
        q = re.sub(r"\bfragezeichen\b", "???", q, flags=re.I)
        q = re.sub(r"\b(die letzte|letzte|neueste|aktuelle|mal|bitte)\b", " ", q, flags=re.I)
        q = re.sub(r"\s+", " ", q).strip(" ,.")
        m = re.match(r"^(.+?)\s+(?:von|vom|by)\s+(?:der |dem |den |die )?(.+)$", q.strip(), re.I)
        title, who = (m.group(1), m.group(2)) if m else (q.strip(), "")
        sim = lambda a, b: fuzz.WRatio(_norm(a), _norm(b)) if a and b else 0   # noqa: E731

        def words_ok(want, it):
            """Jedes Suchwort (≥ 3 Zeichen) kommt ähnlich im Treffer vor – sonst passt „xyzzy unbekannt“ auf „Unbekannt“."""
            have = re.findall(r"\w+", f"{it.get('text', '')} {it.get('text2', '')}".lower())
            return all(any(fuzz.ratio(w, h) >= 80 for h in have) for w in re.findall(r"\w+", want.lower()) if len(w) >= 3)

        def skipped(it):
            """„Eine andere Version“: die gerade laufende Aufnahme (gleicher Titel + gleiches Album, ohne Album: gleicher
            Interpret) überspringen – dann gewinnt die nächstbeste Aufnahme desselben Titels."""
            if not skip or sim(skip.get("title", ""), it.get("text", "")) < 90:
                return False
            parts = [p.strip() for p in str(it.get("text2", "")).split("•")]
            ref = skip.get("album") or skip.get("artist") or ""
            return bool(ref) and any(sim(ref, p) >= 90 for p in parts)

        def performer(it):
            """Interpret eines Treffers: Qobuz-Titel „Komponist • Interpret • Album“ – NICHT der Komponist (Cover-Versionen
            nennen den Original-Interpreten als Komponisten und sahen sonst aus wie „von Avril Lavigne“)."""
            parts = [p.strip() for p in str(it.get("text2", "")).split("•")]
            return parts[1] if len(parts) >= 3 else parts[0]

        def best(items, want, key="text", sub_want=""):
            # Titel allein ODER Reihe/Interpret + Titel („Die Drei ??? Kids“ + „Folge 104: Teuflische Küche“)
            sc = [(max(sim(want, it.get(key, "")), sim(want, f"{it.get('text2', '')} {it.get(key, '')}"))
                   + (0.3 * sim(sub_want, performer(it)) if sub_want else 0), it)
                  for it in items if (it.get("play") or it.get("autoplay")) and words_ok(want, it) and not skipped(it)]
            if sub_want:                               # Interpret genannt → nur seine Titel („Rise Up“ von Imagine
                mine = [x for x in sc if sim(sub_want, performer(x[1])) >= 70]   # Dragons, nicht „Rise“ von …)
                sc = mine or sc
            sc.sort(key=lambda x: -x[0])
            return sc[0] if sc and sc[0][0] >= 80 else (0, None)

        def station(items, want):
            """Sender: Füllwörter („Hit Radio FFH“ ist der Sender, „FFH Die 2010er“ ein Ableger) zählen nicht."""
            filler = r"\b(hit|radio|sender|fm|live|stream|livestream|national|webradio)\b"
            key = lambda t: re.sub(r"[^a-z0-9äöüß]", "", re.sub(filler, "", t.lower()))   # noqa: E731
            w = key(want)
            full = lambda t: re.sub(r"[^a-z0-9äöüß]", "", t.lower())   # noqa: E731
            sc = [(fuzz.ratio(w, key(it.get("text", ""))), full(want) in full(it.get("text", "")),
                   fuzz.ratio(full(want), full(it.get("text", ""))), -n, it)
                  for n, it in enumerate(items) if it.get("type") == "audio" and it.get("play")]
            # Gleichstand → der Sender, dessen Name den ganzen Wunsch enthält („Radio Bob“ ≠ „BOB FM“), dann Ähnlichkeit
            sc.sort(key=lambda x: (-x[0], -x[1], -x[2], -x[3]))
            if sc and sc[0][0] >= 75:
                return sc[0][4]
            return next((it for it in items if it.get("type") == "audio" and it.get("play") and words_ok(want, it)), None)

        cache: dict = {}

        async def search(cat: str, expr: str) -> list:
            """Qobuz-Suche: erst die Übersicht (Artists/Albums/Songs/Playlists), dann die Kategorie-Seite."""
            if (cat, expr) not in cache:
                r0 = await self.browse(ip, f"{root}Search", expr)
                link = next((it.get("browse") for it in r0["items"] if it.get("text") == cat), None)
                cache[(cat, expr)] = (await self.browse(ip, link))["items"] if link else []
            return cache[(cat, expr)]

        def out(it, how):
            return {"title": it.get("text", ""), "sub": it.get("text2", ""), "url": it.get("autoplay") or it.get("play"),
                    "kind": how, "service": service}

        if service == "radio":
            filler = r"\b(hit|radio|sender|fm|live|stream|livestream|national|webradio)\b"
            key = lambda t: re.sub(r"[^a-z0-9äöüß]", "", re.sub(filler, "", t.lower()))   # noqa: E731
            w = key(q)

            def ranked(items):
                """(Güte, Eintrag): 2 = gleicher Sendername, 1 = Variante desselben Senders (hr3 Mittelhessen)."""
                out_ = []
                for it in items:
                    if it.get("type") != "audio" or not it.get("play"):
                        continue
                    k = key(it.get("text", ""))
                    if k and fuzz.ratio(w, k) >= 90:
                        out_.append((2, it))
                    elif w and k.startswith(w) and len(w) >= 3:
                        out_.append((1, it))
                return out_

            async def fetch(src, search=True):
                if not any(it.get("browse") == src for it in top["items"]):
                    return []
                if not search:                         # TuneIn-Favoriten (vom Nutzer gespeichert, erprobt)
                    return (await self.browse(ip, "TuneIn:BrowseMenu/%2FRadioBrowse%3Fservice=TuneIn&url=presets"))["items"]
                items = (await self.browse(ip, f"{src}Search", q))["items"]
                if src == "Airable:":                  # Airable: Suche liefert erst „Stations“/„Podcasts“
                    link = next((it.get("browse") for it in items if it.get("text") == "Stations"), None)
                    items = (await self.browse(ip, link))["items"] if link else []
                return items

            fav, tune, air = await fetch("TuneIn:", False), await fetch("TuneIn:"), await fetch("Airable:")
            exact, variants = [], []
            for src_items, how in ((fav, "Favorit"), (tune, "TuneIn"), (air, "Radio")):
                for g, it in ranked(src_items):
                    (exact if g == 2 else variants).append({**out(it, "station"), "source": how})
            if not exact and not variants:             # nichts Gleichnamiges: bisherige Bewertung (Füllwörter)
                it = station(tune, q) or station(air, q)
                if it:
                    exact = [{**out(it, "station"), "source": "TuneIn"}]
            cands, seen = [], set()
            for c in exact + variants:
                if c["url"] not in seen:
                    seen.add(c["url"])
                    cands.append(c)
            if not cands:
                raise LookupError(f"Keinen Sender „{q}“ gefunden")
            return {**cands[0], "alternatives": cands[1:6]}
        async def own_song(title_, who_):
            """Titel im Katalog des genannten Interpreten, mit lockererem Titelvergleich: gesprochen/erkannt wird oft
            anders geschrieben als veröffentlicht („Skater Boy“ ↔ „Sk8er Boi“, 71 %). Nur bei klarem Abstand zum
            nächsten Titel desselben Interpreten (sonst lieber nichts als ein falsches Lied)."""
            mine = [it for it in await search("Songs", who_) if (it.get("play") or it.get("autoplay"))
                    and sim(who_, performer(it)) >= 88 and not skipped(it)]
            by_title: dict = {}                        # gleicher Titel auf mehreren Alben = EIN Lied (erstes zählt)
            for it in mine:
                by_title.setdefault(_norm(it.get("text", "")), it)
            sc = sorted(((fuzz.ratio(_norm(title_), k), it) for k, it in by_title.items()), key=lambda x: -x[0])
            if sc and sc[0][0] >= 65 and (len(sc) == 1 or sc[0][0] - sc[1][0] >= 15):
                return sc[0][1]
            return None

        if not who and kind in ("any", "song") and len(title.split()) >= 3:
            # Interpret im Suchtext ohne „von“ („Avril Lavigne Skater Boy“): steht ein Name aus den Treffern (Interpret/
            # Komponist) ganz im Text und bleibt ein Rest, ist das der Interpret und der Rest der Titel
            words = title.split()
            names = {p.strip() for it in await search("Songs", title) for p in str(it.get("text2", "")).split("•")}
            for an in sorted(names, key=len, reverse=True):
                aw = an.split()
                if len(aw) < 2:                        # Ein-Wort-Namen („Rain“) stecken zu leicht in Titeln
                    continue
                hit = [any(fuzz.ratio(w.lower(), x.lower()) >= 85 for x in aw) for w in words]
                n = len(aw)                            # Name vorn oder hinten, vollständig, und ein Rest bleibt
                if n < len(words) and (all(hit[:n]) or all(hit[-n:])):
                    title, who = " ".join(w for w, h in zip(words, hit) if not h), an
                    break

        tries = {"song": ["Songs"], "album": ["Albums"], "artist": ["Artists"]}.get(kind, ["Artists", "Albums", "Songs"])
        if who and kind in ("any", "song"):
            tries = ["Songs", "Albums"]
        if re.search(r"\b(folge|teil|episode|kapitel|band)\s*\d+", f"{title} {who}", re.I):
            tries = ["Albums", "Songs"]                # Hörspiel-/Hörbuch-Folge = Album, nicht ein Kapitel daraus
        for cat in tries:
            if cat == "Artists":                       # Interpret → sein bekanntestes Album (Qobuz-Reihenfolge)
                arts = await search("Artists", who or title)
                # ganzer Name ähnlich (ratio) – WRatio wertet Teilstücke hoch: „Skyfall“ ≈ Interpret „Skyfall Beats“
                a = next((x for x in arts if fuzz.ratio(_norm(who or title), _norm(x.get("text", ""))) >= 88), None)
                if not a:
                    continue
                items = await search("Albums", a["text"])
                it = next((x for x in items if sim(a["text"], x.get("text2", "")) >= 90 and x.get("play")), None)
                if it:
                    return {**out(it, "artist"), "artist": a["text"]}
                continue
            items = await search(cat, f"{title} {who}".strip() if cat == "Songs" else title)
            sc, it = best(items, title, "text", who)
            if cat == "Songs" and who and (not it or sim(who, performer(it)) < 70):
                own = await own_song(title, who)       # kein Treffer VOM Interpreten (nur Cover) → in seinem Katalog
                if own:
                    return out(own, "song")
            if it:
                return out(it, "song" if cat == "Songs" else "album")
        raise LookupError(f"Zu „{q}“ nichts auf Qobuz gefunden")

    async def play_checked(self, ip: str, url: str, wait: float = 4.0) -> tuple:
        """Abspielen und prüfen, ob der Player wirklich spielt (TuneIn meldet tote Sender erst danach als Fehler:
        state stop + notifyurl /error?… „404 Not Found“). Rückgabe (ok, Fehlertext)."""
        await self.play(ip, url)
        await asyncio.sleep(wait)
        root = await self.get(ip, "/Status")
        state = (root.findtext("state") or "").strip()
        note = (root.findtext("notifyurl") or "").strip()
        if state in ("play", "stream", "connecting") and not note.startswith("/error"):
            return True, ""
        msg = ""
        if note.startswith("/error"):
            try:
                n = await self.get(ip, note)
                msg = " ".join((n.findtext("message") or "").split())
                clear = (n.findtext("url") or "").strip()
                if clear.startswith("/error?clear"):
                    await self.get(ip, clear)         # Fehlermeldung am Player quittieren
            except Exception:  # noqa: BLE001
                pass
        return False, msg or f"Player-Status {state or '?'}"

    async def album_songs(self, ip: str, album: str, artist: str) -> list:
        """Titel eines Bibliotheks-Albums in BluOS-Reihenfolge (/Songs leitet auf Port 11004 um)."""
        root = await self.get(ip, "/Songs?" + urllib.parse.urlencode(
            {"service": "LocalMusic", "album": album, "artist": artist}))
        return list(root.iter("song"))

    @staticmethod
    def _pick(songs: list, n: int, title: Optional[str]) -> Optional[int]:
        """Index des gewünschten Titels: per Name (bei gleichen Namen der zur Position n nächste),
        sonst Position n (1-basiert). Titelnummern taugen nicht – Mehrfach-CDs beginnen je CD wieder bei 1."""
        norm = lambda t: re.sub(r"\W+", "", str(t or "").lower())   # noqa: E731
        if title:
            want = norm(title)
            cands = [i for i, x in enumerate(songs) if norm(x.findtext("title")) == want]
            if cands:
                return min(cands, key=lambda i: abs(i - (n - 1)))
        return n - 1 if 1 <= n <= len(songs) else None

    async def play_album(self, ip: str, album: str, artist: str, track: Optional[int] = None,
                         single: bool = False, title: Optional[str] = None) -> dict:
        """Album aus der BluOS-Bibliothek (Titel + Interpret); track = ab Position n (1-basiert), title = Name des
        Titels (sicherer als die Position). single = nur dieser Titel: Datei aus /Songs, Warteschlange leeren, nur
        sie hinzufügen (listindex ohne nextlist hängt trotzdem den Rest an – live getestet)."""
        p = {"service": "LocalMusic", "playnow": 1, "album": album, "artist": artist}
        n = max(1, track or 1)
        if single or (title and n > 1) or (title and track):
            songs = await self.album_songs(ip, album, artist)
            idx = self._pick(songs, n, title)
            if idx is None:
                raise LookupError(f"Titel {title or n} von „{album}“ ({artist}) ist in der BluOS-Bibliothek nicht vorhanden")
            if single:
                fn = songs[idx].findtext("fn") or ""
                if not fn:
                    raise LookupError(f"Titel {title or n} von „{album}“ ({artist}) hat keinen Dateipfad")
                await self.get(ip, "/Clear")
                await self.get(ip, "/Add?" + urllib.parse.urlencode({"playnow": 1, "file": fn}))
                return {"length": 1}
            if idx > 0:
                p.update({"listindex": idx, "nextlist": 1, "where": "last", "cursor": "last"})
        elif n > 1:
            p.update({"listindex": n - 1, "nextlist": 1, "where": "last", "cursor": "last"})
        root = await self.get(ip, "/Add?" + urllib.parse.urlencode(p))
        cnt = int(root.get("length") or 0)
        if not cnt:
            raise LookupError(f"„{album}“ ({artist}) ist in der BluOS-Bibliothek nicht vorhanden")
        return {"length": cnt}

    async def playlist(self, ip: str, limit: int = 200) -> list:
        """Aktuelle Warteschlange des Players (Titel in Abspielreihenfolge, id = Position)."""
        root = await self.get(ip, f"/Playlist?start=0&end={limit - 1}")
        return [{"pos": int(s.get("id", i)), "name": s.findtext("title") or "", "artist": s.findtext("art") or "",
                 "album": s.findtext("alb") or "", "track_no": s.findtext("track"), "image": s.findtext("image") or ""}
                for i, s in enumerate(root.iter("song"))]

    async def jump(self, ip: str, pos: int) -> None:
        """In der Warteschlange zu Position pos springen und abspielen."""
        await self.get(ip, f"/Play?id={int(pos)}")

    async def inputs(self, ip: str) -> list:
        """Eingänge (Line-In, Optisch, Bluetooth …) aus der obersten Browse-Ebene."""
        return [i for i in (await self.browse(ip))["items"] if i.get("input") and i.get("play")]
