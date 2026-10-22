"""KODI (Chowmain „KODI FULL“): Selbstkonfiguration, Wiedergabe-Status und Mediathek – nur lesend.

Aufbau in Control4 (siehe ARCHITEKTUR.md §3):
  - Haupttreiber `kodi_full_main` (Proxys `cable` = „Mediacenter“ + `avswitch`), Eigenschaften „KODI HOST or IP“,
    „KODI Webserver Port“, „Player Status“, „Currently Playing“ … – Änderungen kommen per Director-Push als
    `UPDATE_PROPERTY` vom Treiber-Item.
  - Minitreiber `kodi_full_movies`, `_tvshows`, `_live_tv`, `_video_files` … (je Proxy `media_service`), über
    Steuer-Bindungen (101 … 204) am Haupttreiber; ihr media_service-Kind ist die Quelle im Raum (RF_KODI_*).
Erkennung: Dateiname (fremde API, Regel 2) – plus strukturell: Treiber mit Eigenschaft „… HOST or IP“ + „KODI …“.
KODI selbst: JSON-RPC über HTTP (Webserver-Port) und Benachrichtigungen über WebSocket (Port 9090) = Push.
Schreibend nur über `_write` (feste Liste): Player.SetShuffle/SetRepeat (Mix/Wiederholen) und Player.Open
(Film/Folge aus der Mediathek starten, Sprache/Oberfläche) – alles andere bleibt lesend.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import urllib.parse
from typing import Awaitable, Callable, Optional

import aiohttp

from . import logbus
from .config import DATA_DIR

STATE_FILE = DATA_DIR / "kodi.json"
WS_PORT = 9090
TIMEOUT = aiohttp.ClientTimeout(total=8)
_STEM = re.compile(r"^kodi_(?:full_|lite_)?(.+)$")
# Minitreiber (Dateiname ohne kodi_full_) → Bereich der Mediathek; nicht aufgeführte (Settings, Addons …) nur anzeigen
SECTION_KIND = {"movies": "movies", "tvshows": "tvshows", "live_tv": "channels", "video_files": "files",
                "music": "music", "radio": "radio"}
# Diese Benachrichtigungen ändern, was läuft → Status neu holen
_PLAYER_EVENTS = ("Player.On", "Playlist.OnClear", "System.OnQuit", "System.OnSleep", "System.OnWake",
                  "System.OnRestart")
_ITEM_PROPS = ["title", "showtitle", "season", "episode", "year", "art", "thumbnail", "channel", "channeltype",
               "artist", "album", "file", "streamdetails", "runtime"]


def _props(lst) -> dict:
    return {x.get("name"): x.get("value") for x in lst if isinstance(x, dict)} if isinstance(lst, list) else {}


def _secs(t) -> int:
    return int(t.get("hours", 0)) * 3600 + int(t.get("minutes", 0)) * 60 + int(t.get("seconds", 0)) \
        if isinstance(t, dict) else 0


def _res(sd) -> str:
    """streamdetails → „4K“, „1080p“ …"""
    v = ((sd or {}).get("video") or [{}])[0] if isinstance(sd, dict) else {}
    w = int(v.get("width") or 0)
    return "4K" if w >= 3200 else "1080p" if w >= 1800 else "720p" if w >= 1200 else "SD" if w else ""


class KodiError(Exception):
    pass


class Kodi:
    def __init__(self):
        self.players: dict = {}      # Treiber-ID (str) → Konfiguration (siehe _refresh)
        self.live: dict = {}         # Treiber-ID → was läuft {state, kind, title, subtitle, image, secs, total, at …}
        self.warnings: list = []
        self.updated = 0.0
        self.on_change: Optional[Callable[[str], None]] = None   # von app.py: Räume dieses KODI neu melden
        self.rooms_seen: dict = {}   # Treiber-ID → Räume, in denen KODI zuletzt Quelle war
        self._ws_tasks: dict = {}
        self._ws_ok: dict = {}
        self._pending: dict = {}     # Treiber-ID → geplanter Status-Abruf (Ereignisse bündeln)
        self._art: dict = {}         # Bild-Kennung → KODI-Bildadresse (nie an die Oberfläche)
        self._titles: dict = {}      # (Treiber-ID, movie|tvshow) → (Zeit, Titel) für die unscharfe Suche
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.players = st.get("players", {})
            self.warnings = st.get("warnings", [])
            self.updated = st.get("updated", 0.0)
        except Exception:  # noqa: BLE001
            pass

    def _save(self) -> None:
        from .auth import atomic_write
        atomic_write(STATE_FILE, json.dumps({"players": self.players, "warnings": self.warnings,
                                             "updated": self.updated}, ensure_ascii=False, indent=1))

    async def _s(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT)
        return self._session

    # ---------------------------------------------------------------- Zuordnung
    def owner(self, item_id) -> Optional[str]:
        """Zu welchem KODI gehört dieses Item (Haupttreiber, Mediacenter, Minitreiber, Quelle im Raum)?"""
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return None
        return next((did for did, p in self.players.items() if item_id in p.get("ids", [])), None)

    def driver_ids(self) -> set:
        return {int(d) for d in self.players}

    # ---------------------------------------------------------------- JSON-RPC
    async def rpc(self, did: str, method: str, params: Optional[dict] = None, timeout: float = 6):
        p = self.players.get(str(did))
        if not p or not p.get("host"):
            raise KodiError("KODI nicht konfiguriert")
        if not method.split(".", 1)[-1].startswith(("Get", "Introspect", "Ping", "Version")):
            raise KodiError(f"{method}: nur lesende Aufrufe erlaubt")
        return await self._rpc(p["host"], p.get("port") or 8080, method, params, timeout)

    async def _rpc(self, host: str, port: int, method: str, params: Optional[dict] = None, timeout: float = 6):
        s = await self._s()
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        async with s.post(f"http://{host}:{port}/jsonrpc", json=body,
                          timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 401:
                raise KodiError("KODI verlangt eine Anmeldung (Webserver-Passwort)")
            r.raise_for_status()
            d = await r.json(content_type=None)
        if "error" in d:
            raise KodiError(f"{method}: {d['error'].get('message')}")
        return d.get("result")

    # ---------------------------------------------------------------- Selbstkonfiguration
    async def refresh(self, conn) -> None:
        async with self._lock:
            await self._refresh(conn)
        self._start_ws()

    async def _refresh(self, conn) -> None:
        warn: list = []
        try:
            items = await conn.get("/api/v1/items")
        except Exception as e:  # noqa: BLE001
            logbus.warn("kodi", f"Items nicht lesbar: {e}")
            return
        by_parent: dict = {}
        for i in items:
            by_parent.setdefault(i.get("parentId"), []).append(i)
        stem = lambda i: str(i.get("filename", "")).rsplit(".", 1)[0].lower()  # noqa: E731
        # Haupttreiber: Dateiname kodi_*main, sonst jeder Treiber mit „KODI …“-Eigenschaften und Host/IP
        cands = [i for i in items if i.get("typeName") == "device" and i.get("id") == i.get("protocolId", i.get("id"))
                 and stem(i).startswith("kodi") and not i.get("protocolFilename")]
        mains = [i for i in cands if stem(i).endswith("main")] or cands
        players: dict = {}
        for d in mains:
            try:
                p = _props(await conn.get(f"/api/v1/items/{d['id']}/properties"))
            except Exception:  # noqa: BLE001
                continue
            host_key = next((k for k in p if re.search(r"host|\bip\b", k, re.I)), None)
            if not host_key:
                continue                                      # Minitreiber o. Ä. – kein KODI-Gerät
            port_key = next((k for k in p if re.search(r"webserver.*port|http.*port", k, re.I)), None)
            try:
                binds = await conn.get(f"/api/v1/items/{d['id']}/bindings")
            except Exception:  # noqa: BLE001
                binds = []
            ids = {d["id"]} | {c["id"] for c in by_parent.get(d["id"], [])}
            sections = []
            for b in binds if isinstance(binds, list) else []:
                for c in b.get("connections") or []:
                    if b.get("bindingType") == "Proxy":
                        ids.add(c["id"])
                        continue
                    sd = next((i for i in items if i.get("id") == c.get("id")), None)
                    if not sd or not stem(sd).startswith("kodi"):
                        continue
                    m = _STEM.match(stem(sd))
                    key = m.group(1) if m else stem(sd)
                    src = next((x for x in by_parent.get(sd["id"], []) if x.get("proxy") == "media_service"), None)
                    ids.add(sd["id"])
                    if src:
                        ids.add(src["id"])
                    sections.append({"key": key, "kind": SECTION_KIND.get(key, ""), "name": (src or sd).get("name", ""),
                                     "driver_id": sd["id"], "source_id": (src or {}).get("id"),
                                     "binding": b.get("bindingName", "")})
            await asyncio.sleep(0.1)                          # Director schonen
            auto_rooms = [int(x) for x in re.findall(r"\d+", str(p.get("Auto Room(s) Select") or ""))]
            pl = {"driver_id": d["id"], "name": p.get("KODI Client Name") or d.get("name", ""),
                  "c4_name": d.get("name", ""), "driver": stem(d), "room_id": d.get("roomId"),
                  "host": str(p.get(host_key) or "").strip(), "port": int(p.get(port_key) or 8080) if port_key else 8080,
                  "c4_status": p.get("Connection Status", ""), "c4_player": p.get("Player Status", ""),
                  "auto_rooms": auto_rooms, "sections": sections, "ids": sorted(ids),
                  "ok": False, "version": "", "error": ""}
            if not pl["host"]:
                warn.append(f"KODI „{pl['c4_name']}“ (#{d['id']}): keine Adresse in der Eigenschaft „{host_key}“.")
            else:
                try:
                    v = await self._rpc(pl["host"], pl["port"], "Application.GetProperties",
                                        {"properties": ["name", "version"]}, timeout=4)
                    ver = v.get("version") or {}
                    pl.update(ok=True, version=f"{ver.get('major', '')}.{ver.get('minor', '')}"
                              + (f" ({ver.get('revision')})" if ver.get("revision") else ""))
                except Exception as e:  # noqa: BLE001
                    pl["error"] = f"{type(e).__name__}: {e}" if not isinstance(e, KodiError) else str(e)
                    warn.append(f"KODI „{pl['name']}“ unter {pl['host']}:{pl['port']} nicht erreichbar ({pl['error']}). "
                                f"In KODI: Einstellungen → Dienste → Steuerung → „Steuerung über HTTP erlauben“ "
                                f"und „Fernsteuerung durch Anwendungen auf anderen Systemen“.")
            if not sections:
                warn.append(f"KODI „{pl['name']}“: keine Minitreiber (Filme, Serien, Live TV …) gebunden – "
                            f"Mediathek nicht verfügbar.")
            players[str(d["id"])] = pl
        self.players = players
        self.warnings = warn
        self.updated = time.time()
        self._save()
        logbus.info("kodi", f"{len(players)} KODI gefunden" + "".join(
            f"; {p['name']} {p['host']}:{p['port']} {'ok' if p['ok'] else 'offline'}, {len(p['sections'])} Bereiche"
            for p in players.values()))

    # ---------------------------------------------------------------- Status
    async def status(self, did: str, max_age: float = 0) -> dict:
        """Was läuft gerade? Push (WebSocket/Director) hält es aktuell; ohne WebSocket höchstens max_age alt."""
        did = str(did)
        cur = self.live.get(did)
        if cur and (self._ws_ok.get(did) or time.time() - cur.get("fetched", 0) < max_age):
            return cur
        return await self._fetch(did)

    async def _fetch(self, did: str) -> dict:
        out = {"state": "stop", "kind": "", "title": "", "subtitle": "", "image": "", "secs": 0, "total": 0,
               "at": time.time(), "fetched": time.time(), "resolution": "", "ok": True}
        try:
            act = await self.rpc(did, "Player.GetActivePlayers")
            pl = next((a for a in act or [] if a.get("type") == "video"), None) or (act or [None])[0]
            if pl:
                pid = pl["playerid"]
                it, pr = await asyncio.gather(
                    self.rpc(did, "Player.GetItem", {"playerid": pid, "properties": _ITEM_PROPS}),
                    self.rpc(did, "Player.GetProperties", {"playerid": pid, "properties": ["speed", "time", "totaltime",
                                                                                           "live", "shuffled", "repeat"]}))
                item = (it or {}).get("item") or {}
                kind, art = item.get("type") or "", item.get("art") or {}
                title, sub = item.get("title") or item.get("label") or "", ""
                if kind == "episode":
                    title = item.get("showtitle") or title
                    se = f"S{int(item.get('season') or 0):02d}E{int(item.get('episode') or 0):02d}" \
                        if item.get("season") is not None and int(item.get("season") or -1) >= 0 else ""
                    sub = " · ".join(x for x in (se, item.get("title") or "") if x)
                elif kind == "channel":
                    sub = item.get("channel") or item.get("label") or ""
                    title = item.get("title") or sub
                    if title == sub:
                        sub = ""
                elif kind == "movie":
                    sub = str(item.get("year") or "")
                elif kind == "song":
                    sub = " · ".join(x for x in (", ".join(item.get("artist") or []), item.get("album")) if x)
                img = art.get("poster") or art.get("tvshow.poster") or art.get("thumb") or art.get("icon") \
                    or item.get("thumbnail") or ""
                out.update(state="pause" if (pr or {}).get("speed") == 0 else "play", kind=kind or pl.get("type", ""),
                           title=title, subtitle=sub, image=img, secs=_secs((pr or {}).get("time")),
                           total=_secs((pr or {}).get("totaltime")), resolution=_res(item.get("streamdetails")),
                           live=bool((pr or {}).get("live")) or kind == "channel", media_type=pl.get("type", ""),
                           shuffle=bool((pr or {}).get("shuffled")), repeat=(pr or {}).get("repeat") or "off",
                           channel=item.get("channel") or "")
        except Exception as e:  # noqa: BLE001
            # KODI nicht erreichbar → was der Control4-Treiber zuletzt gemeldet hat
            p = self.players.get(did, {})
            st = str(p.get("c4_player") or "").lower()
            out.update(ok=False, error=f"{type(e).__name__}", title=p.get("c4_playing", ""),
                       state="play" if st == "playing" else "pause" if st == "paused" else "stop")
        self.live[did] = out
        return out

    def on_property(self, did: int, name: str, value) -> None:
        """Director-Push vom Haupttreiber (UPDATE_PROPERTY)."""
        p = self.players.get(str(did))
        if not p:
            return
        if name == "Player Status":
            p["c4_player"] = value
        elif name == "Currently Playing":
            p["c4_playing"] = value
        elif name == "Connection Status":
            p["c4_status"] = value
        else:
            return                       # CPU, Temperatur … – kein Wiedergabe-Status
        self._schedule(str(did))

    def _schedule(self, did: str, delay: float = 0.4) -> None:
        """Status-Abruf bündeln (KODI schickt beim Start eines Films mehrere Meldungen kurz hintereinander)."""
        if did in self._pending:
            return

        async def run():
            await asyncio.sleep(delay)
            self._pending.pop(did, None)
            old = {k: (self.live.get(did) or {}).get(k) for k in ("state", "title", "subtitle", "image")}
            new = await self._fetch(did)
            if {k: new.get(k) for k in old} != old and self.on_change:
                self.on_change(did)
            elif self.on_change and (self.live.get(did) or {}).get("state") == "play":
                self.on_change(did)      # Spulen/Fortsetzen: Zeit neu
        self._pending[did] = asyncio.ensure_future(run())

    # ---------------------------------------------------------------- Zufall / Wiederholen (Schreibaktionen)
    async def _player(self, did: str) -> int:
        act = await self.rpc(did, "Player.GetActivePlayers")
        pl = next((a for a in act or [] if a.get("type") in ("audio", "video")), None)
        if not pl:
            raise KodiError("KODI spielt gerade nichts")
        return pl["playerid"]

    async def _write(self, did: str, method: str, params: dict):
        """Bewusst nur diese Methoden – alles andere bleibt lesend (rpc)."""
        if method not in ("Player.SetShuffle", "Player.SetRepeat", "Player.Open"):
            raise KodiError(f"{method} nicht freigegeben")
        p = self.players[str(did)]
        r = await self._rpc(p["host"], p.get("port") or 8080, method, params)
        self._schedule(str(did), 0.3)
        return r

    async def set_shuffle(self, did: str, on: bool) -> None:
        await self._write(did, "Player.SetShuffle", {"playerid": await self._player(did), "shuffle": bool(on)})

    async def set_repeat(self, did: str, mode: str) -> None:
        await self._write(did, "Player.SetRepeat", {"playerid": await self._player(did), "repeat": mode})

    # ---------------------------------------------------------------- Suche + Abspielen (Sprache, Oberfläche)
    async def titles(self, did: str, kind: str) -> list:
        """Alle Film- bzw. Serientitel eines KODI (5 min zwischengespeichert) – Grundlage der unscharfen Suche."""
        did = str(did)
        hit = self._titles.get((did, kind))
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        lim = {"start": 0, "end": 5000}
        if kind == "movie":
            r = await self.rpc(did, "VideoLibrary.GetMovies",
                               {"properties": ["title", "year", "playcount", "originaltitle"], "limits": lim}, timeout=20)
            out = [{"id": m["movieid"], "type": "movie", "title": m.get("title") or m.get("label") or "",
                    "alt": m.get("originaltitle") or "", "year": m.get("year") or 0, "seen": bool(m.get("playcount"))}
                   for m in (r or {}).get("movies") or []]
        else:
            r = await self.rpc(did, "VideoLibrary.GetTVShows",
                               {"properties": ["title", "year", "episode", "watchedepisodes", "originaltitle"], "limits": lim},
                               timeout=20)
            out = [{"id": m["tvshowid"], "type": "tvshow", "title": m.get("title") or m.get("label") or "",
                    "alt": m.get("originaltitle") or "", "year": m.get("year") or 0,
                    "episodes": m.get("episode") or 0, "watched": m.get("watchedepisodes") or 0}
                   for m in (r or {}).get("tvshows") or []]
        self._titles[(did, kind)] = (time.time(), out)
        return out

    async def search(self, q: str, kind: str = "any", dids: Optional[list] = None) -> Optional[dict]:
        """Unscharfe Titelsuche (Spracherkennung verschreibt sich: „Zurück in die Zukunft drei“). kind: movie | tvshow |
        any. Liefert den besten Treffer {did, type, id, title, year, score, alternatives} oder None."""
        from rapidfuzz import fuzz
        qn = _norm_title(q)
        if not qn:
            return None
        qwords = _content_words(qn)
        kind = {"film": "movie", "movies": "movie", "series": "tvshow", "serie": "tvshow", "tvshows": "tvshow"}.get(kind, kind)
        kinds = ["movie", "tvshow"] if kind not in ("movie", "tvshow") else [kind]
        cands = []
        for did in (dids or list(self.players)):
            for k in kinds:
                try:
                    items = await self.titles(did, k)
                except Exception:  # noqa: BLE001
                    continue
                for it in items:
                    names = [n for n in (it["title"], it.get("alt")) if n]
                    sc = 0
                    for n in names:
                        tn = _norm_title(n)
                        s_ = max(fuzz.token_set_ratio(qn, tn), fuzz.ratio(qn, tn))
                        if qwords:                     # Inhaltswörter müssen (ähnlich) im Titel stehen – sonst nur Zufall
                            tw = _content_words(tn)
                            hit_ = sum(1 for w in qwords if any(fuzz.ratio(w, x) >= 80 for x in tw))
                            if hit_ / len(qwords) < 0.5:
                                s_ //= 2
                        qnum = set(re.findall(r"\d+", qn))
                        if qnum and not qnum <= set(re.findall(r"\d+", tn)):
                            s_ = min(s_, 70)           # „Zurück in die Zukunft 3“ ist nicht Teil I (Zahl fehlt im Titel)
                        sc = max(sc, s_)
                    if _norm_title(it["title"]) == qn:
                        sc = 100                       # genau der Titel schlägt längere Varianten („Dune“ vs „Dune 2“)
                    cands.append((sc, did, it))
        if not cands:
            return None
        cands.sort(key=lambda c: (-c[0], c[2].get("year") or 0))
        sc, did, best = cands[0]
        alts = []
        for s2, _, it in cands[1:8]:
            if s2 >= 60 and it["title"] != best["title"] and it["title"] not in alts:
                alts.append(it["title"])
        return {"did": did, "score": round(sc), "alternatives": alts[:2], **{k: v for k, v in best.items() if k != "alt"}}

    async def episode_pick(self, did: str, tvshowid: int, season: Optional[int] = None,
                           episode: Optional[int] = None) -> Optional[dict]:
        """Welche Folge? Genannte Staffel/Folge; sonst die angefangene, sonst die erste ungesehene, sonst die erste."""
        ps = {"tvshowid": int(tvshowid), "properties": ["title", "season", "episode", "playcount", "resume"],
              "sort": {"method": "episode", "order": "ascending"}}
        if season is not None:
            ps["season"] = int(season)
        r = await self.rpc(str(did), "VideoLibrary.GetEpisodes", ps, timeout=12)
        eps = sorted((r or {}).get("episodes") or [], key=lambda e: (int(e.get("season") or 0), int(e.get("episode") or 0)))
        if episode is not None:
            pick = next((e for e in eps if int(e.get("episode") or 0) == int(episode)), None)
        else:
            pick = next((e for e in eps if float((e.get("resume") or {}).get("position") or 0) > 0), None)                 or next((e for e in eps if not e.get("playcount")), None) or (eps[0] if eps else None)
        if not pick:
            return None
        return {"id": pick["episodeid"], "type": "episode", "title": pick.get("title") or "",
                "season": int(pick.get("season") or 0), "episode": int(pick.get("episode") or 0)}

    async def play(self, did: str, kind: str, item_id: int, resume: bool = True) -> None:
        """Film bzw. Folge starten (Player.Open; resume = angefangene Wiedergabe fortsetzen)."""
        key = {"movie": "movieid", "episode": "episodeid"}.get(kind)
        if not key:
            raise KodiError(f"{kind} ist nicht abspielbar")
        await self._write(str(did), "Player.Open", {"item": {key: int(item_id)}, "options": {"resume": bool(resume)}})

    # ---------------------------------------------------------------- WebSocket (KODI-Push)
    def _start_ws(self) -> None:
        for did, p in self.players.items():
            t = self._ws_tasks.get(did)
            if p.get("host") and (t is None or t.done()):
                self._ws_tasks[did] = asyncio.ensure_future(self._ws_loop(did))
        for did in list(self._ws_tasks):
            if did not in self.players:
                self._ws_tasks.pop(did).cancel()

    async def _ws_loop(self, did: str) -> None:
        wait = 5
        while did in self.players:
            p = self.players[did]
            try:
                s = await self._s()
                async with s.ws_connect(f"ws://{p['host']}:{WS_PORT}/jsonrpc", heartbeat=30,
                                        timeout=aiohttp.ClientWSTimeout(ws_close=5)) as ws:
                    self._ws_ok[did] = True
                    wait = 5
                    logbus.info("kodi", f"{p['name']}: Push über KODI-WebSocket verbunden")
                    self._schedule(did, 0)
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            m = json.loads(msg.data)
                        except ValueError:
                            continue
                        if str(m.get("method", "")).startswith(_PLAYER_EVENTS):
                            self._schedule(did)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                if self._ws_ok.get(did) is not False:
                    logbus.warn("kodi", f"{p.get('name')}: KODI-WebSocket nicht verbunden ({type(e).__name__}) – "
                                        f"Status über Control4-Push bzw. Nachfragen")
            self._ws_ok[did] = False
            await asyncio.sleep(wait)
            wait = min(wait * 2, 300)

    def ws_state(self, did: str) -> bool:
        return bool(self._ws_ok.get(str(did)))

    # ---------------------------------------------------------------- Bilder
    def art_url(self, did: str, art: str) -> str:
        """Bild-Adresse für die Oberfläche: nur eine Kennung – die KODI-Adresse bleibt in der Bridge (Aufnahmen-
        Vorschaubilder enthalten z. B. Zugangsdaten des PVR-Backends in der URL)."""
        if not art:
            return ""
        key = hashlib.sha1(art.encode("utf-8", "replace")).hexdigest()[:20]
        if key not in self._art:
            if len(self._art) > 20000:              # Speicher begrenzen: älteste Hälfte verwerfen
                for k in list(self._art)[:10000]:
                    self._art.pop(k, None)
            self._art[key] = art
        return f"/api/kodi/{did}/art/{key}"

    async def art(self, did: str, key: str) -> tuple:
        p = self.players.get(str(did))
        u = self._art.get(key)
        if not p or not u:
            raise KodiError("kein KODI-Bild")
        if not u.startswith("image://"):            # fremde URL (http …) – KODI holt und speichert sie selbst
            u = "image://" + urllib.parse.quote(u, safe="") + "/"
        s = await self._s()
        async with s.get(f"http://{p['host']}:{p.get('port') or 8080}/image/" + urllib.parse.quote(u, safe="")) as r:
            r.raise_for_status()
            return await r.read(), r.headers.get("Content-Type") or "image/jpeg"

    # ---------------------------------------------------------------- Mediathek (nur lesen)
    async def library(self, did: str, kind: str, start: int = 0, limit: int = 60, q: str = "",
                      sort: str = "", parent: Optional[int] = None, season: Optional[int] = None,
                      path: str = "") -> dict:
        lim = {"start": max(0, start), "end": max(0, start) + max(1, min(limit, 500))}
        flt = {"field": "title", "operator": "contains", "value": q} if q else None
        srt = {"method": "dateadded", "order": "descending"} if sort == "new" else \
            {"method": "title", "order": "ascending", "ignorearticle": True}
        art = lambda a, *keys: next((a.get(k) for k in keys if a.get(k)), "")  # noqa: E731
        if kind == "movies":
            ps = {"properties": ["title", "year", "art", "runtime", "genre", "rating", "playcount", "resume"],
                  "limits": lim, "sort": srt, **({"filter": flt} if flt else {})}
            r = await self.rpc(did, "VideoLibrary.GetMovies", ps, timeout=12)
            items = [{"id": m["movieid"], "type": "movie", "title": m.get("title") or m.get("label"),
                      "sub": " · ".join(x for x in (str(m.get("year") or ""), ", ".join(m.get("genre") or [])[:40]) if x),
                      "img": art(m.get("art") or {}, "poster", "thumb"), "seen": bool(m.get("playcount")),
                      "runtime": int(m.get("runtime") or 0) // 60} for m in (r or {}).get("movies") or []]
        elif kind == "tvshows":
            ps = {"properties": ["title", "year", "art", "genre", "episode", "watchedepisodes", "season"],
                  "limits": lim, "sort": srt, **({"filter": flt} if flt else {})}
            r = await self.rpc(did, "VideoLibrary.GetTVShows", ps, timeout=12)
            items = [{"id": m["tvshowid"], "type": "tvshow", "title": m.get("title") or m.get("label"),
                      "sub": " · ".join(x for x in (str(m.get("year") or ""), f"{m.get('episode') or 0} Folgen") if x),
                      "img": art(m.get("art") or {}, "poster", "thumb"),
                      "seen": bool(m.get("episode")) and m.get("watchedepisodes") == m.get("episode")}
                     for m in (r or {}).get("tvshows") or []]
        elif kind == "seasons":
            r = await self.rpc(did, "VideoLibrary.GetSeasons",
                               {"tvshowid": int(parent or 0), "properties": ["season", "art", "episode",
                                                                             "watchedepisodes", "showtitle"]})
            items = [{"id": m["season"], "type": "season", "title": m.get("label") or f"Staffel {m.get('season')}",
                      "sub": f"{m.get('episode') or 0} Folgen", "img": art(m.get("art") or {}, "poster", "tvshow.poster",
                                                                          "thumb"),
                      "seen": bool(m.get("episode")) and m.get("watchedepisodes") == m.get("episode")}
                     for m in (r or {}).get("seasons") or []]
            r = {"limits": {"total": len(items)}}
        elif kind == "episodes":
            r = await self.rpc(did, "VideoLibrary.GetEpisodes",
                               {"tvshowid": int(parent or 0), **({"season": int(season)} if season is not None else {}),
                                "properties": ["title", "season", "episode", "art", "runtime", "playcount", "firstaired",
                                               "plot"], "sort": {"method": "episode", "order": "ascending"}})
            items = [{"id": m["episodeid"], "type": "episode", "title": f"{m.get('episode')}. {m.get('title')}",
                      "sub": " · ".join(x for x in (str(m.get("firstaired") or ""), f"{int(m.get('runtime') or 0) // 60} min"
                                                    if m.get("runtime") else "") if x),
                      "img": art(m.get("art") or {}, "thumb", "season.poster", "tvshow.poster"),
                      "seen": bool(m.get("playcount")), "plot": (m.get("plot") or "")[:400]}
                     for m in (r or {}).get("episodes") or []]
            r = {"limits": {"total": len(items)}}
        elif kind == "channels":
            r = await self.rpc(did, "PVR.GetChannels", {"channelgroupid": "alltv", "properties":
                               ["thumbnail", "channelnumber", "broadcastnow", "hidden"], "limits": lim}, timeout=12)
            items = [{"id": c["channelid"], "type": "channel", "title": c.get("label"),
                      "sub": ((c.get("broadcastnow") or {}).get("title") or ""), "img": c.get("thumbnail") or "",
                      "no": (c.get("channelnumber") or 0)} for c in (r or {}).get("channels") or []
                     if not c.get("hidden") and (not q or q.lower() in str(c.get("label", "")).lower())]
        elif kind == "recordings":
            r = await self.rpc(did, "PVR.GetRecordings", {"properties": ["title", "channel", "starttime", "runtime",
                                                                          "art", "icon", "playcount"],
                                                           "limits": {"start": 0, "end": 2000}}, timeout=15)
            recs = [m for m in (r or {}).get("recordings") or []
                    if not q or q.lower() in str(m.get("title") or m.get("label") or "").lower()]
            recs.sort(key=lambda m: str(m.get("starttime") or ""), reverse=True)
            total = len(recs)
            items = [{"id": m["recordingid"], "type": "recording", "title": m.get("title") or m.get("label"),
                      "sub": " · ".join(x for x in (m.get("channel") or "", str(m.get("starttime") or "")[:16],
                                                    f"{int(m.get('runtime') or 0) // 60} min" if m.get("runtime") else "")
                                        if x),
                      "img": art(m.get("art") or {}, "thumb", "poster") or m.get("icon") or "",
                      "seen": bool(m.get("playcount"))} for m in recs[lim["start"]:lim["end"]]]
            r = {"limits": {"total": total}}
        elif kind == "files":
            if not path:
                r = await self.rpc(did, "Files.GetSources", {"media": "video"})
                items = [{"id": s["file"], "type": "dir", "title": s.get("label"), "sub": "", "img": ""}
                         for s in (r or {}).get("sources") or []]
            else:
                r = await self.rpc(did, "Files.GetDirectory", {"directory": path, "media": "video",
                                                               "properties": ["title", "art", "size"],
                                                               "sort": {"method": "label", "ignorefolders": False}},
                                   timeout=15)
                items = [{"id": f["file"], "type": "dir" if f.get("filetype") == "directory" else "file",
                          "title": f.get("label"), "sub": "", "img": art(f.get("art") or {}, "poster", "thumb")}
                         for f in (r or {}).get("files") or []]
            r = {"limits": {"total": len(items)}}
        else:
            raise KodiError(f"unbekannter Bereich {kind}")
        for it in items:
            it["img"] = self.art_url(did, it.get("img") or "")
        return {"kind": kind, "items": items, "total": ((r or {}).get("limits") or {}).get("total", len(items)),
                "start": lim["start"]}

    async def details(self, did: str, kind: str, item_id: int) -> dict:
        if kind == "movie":
            r = await self.rpc(did, "VideoLibrary.GetMovieDetails", {"movieid": item_id, "properties": [
                "title", "year", "plot", "art", "runtime", "genre", "rating", "director", "cast", "playcount",
                "resume", "streamdetails", "tagline", "mpaa"]})
            m = (r or {}).get("moviedetails") or {}
            return {"id": item_id, "type": "movie", "title": m.get("title"), "year": m.get("year"),
                    "plot": m.get("plot") or "", "tagline": m.get("tagline") or "", "genre": m.get("genre") or [],
                    "rating": round(float(m.get("rating") or 0), 1), "runtime": int(m.get("runtime") or 0) // 60,
                    "director": m.get("director") or [], "cast": [c.get("name") for c in (m.get("cast") or [])[:8]],
                    "seen": bool(m.get("playcount")), "resume": _secs_float((m.get("resume") or {}).get("position")),
                    "resolution": _res(m.get("streamdetails")), "mpaa": m.get("mpaa") or "",
                    "img": self.art_url(did, (m.get("art") or {}).get("poster") or ""),
                    "fanart": self.art_url(did, (m.get("art") or {}).get("fanart") or "")}
        if kind == "tvshow":
            r = await self.rpc(did, "VideoLibrary.GetTVShowDetails", {"tvshowid": item_id, "properties": [
                "title", "year", "plot", "art", "genre", "rating", "episode", "watchedepisodes", "season", "studio"]})
            m = (r or {}).get("tvshowdetails") or {}
            return {"id": item_id, "type": "tvshow", "title": m.get("title"), "year": m.get("year"),
                    "plot": m.get("plot") or "", "genre": m.get("genre") or [], "studio": m.get("studio") or [],
                    "rating": round(float(m.get("rating") or 0), 1), "episodes": m.get("episode") or 0,
                    "seasons": m.get("season") or 0, "watched": m.get("watchedepisodes") or 0,
                    "img": self.art_url(did, (m.get("art") or {}).get("poster") or ""),
                    "fanart": self.art_url(did, (m.get("art") or {}).get("fanart") or "")}
        raise KodiError(f"keine Details für {kind}")


_ARTICLES = re.compile(r"^(der|die|das|the|ein|eine|a|an)\s+", re.I)
_STOP = {"der", "die", "das", "den", "dem", "des", "und", "von", "the", "of", "and", "ein", "eine", "folge", "staffel",
         "teil", "film", "serie", "episode", "part", "hdr"}


def _content_words(t: str) -> set:
    """Wörter, die einen Titel tragen: ≥ 3 Buchstaben, keine Zahlen, keine Füll-/Gattungswörter."""
    return {w for w in re.findall(r"[\wäöüß]+", t) if len(w) >= 3 and not w.isdigit() and w not in _STOP}


def _norm_title(t: str) -> str:
    """Titel vergleichbar machen: klein, ohne Satzzeichen/Artikel, Ziffern ↔ Zahlwörter grob angeglichen."""
    s = str(t or "").lower().replace("???", " fragezeichen ")     # „Die drei ???“ heißt gesprochen „Fragezeichen“
    s = re.sub(r"[^\wäöüß ]+", " ", s)
    s = _ARTICLES.sub("", " ".join(s.split()))
    for w, n in (("drei", "3"), ("zwei", "2"), ("vier", "4"), ("fünf", "5"), ("eins", "1"), ("sechs", "6"), ("sieben", "7"),
                 ("acht", "8"), ("neun", "9"), ("zehn", "10"), ("ii", "2"), ("iii", "3"), ("iv", "4"), ("part", "teil")):
        s = re.sub(r"\b" + w + r"\b", n, s)
    return s.strip()


def _secs_float(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0
