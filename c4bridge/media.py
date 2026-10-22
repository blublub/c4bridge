"""Local music library: albums and tracks, cached in memory and on disk.

The Director returns all albums in one call and the tracks of an album via
albums/{id}. We load everything once in the background and search it with
fuzzy matching, so a voice request never waits for the Director.
"""
import asyncio
import json
import re
import time
from typing import Optional

from rapidfuzz import fuzz, process

from . import logbus
from .config import DATA_DIR
from .connection import Connection

CACHE = DATA_DIR / "media.json"


def _norm(s: str) -> str:
    return " ".join(str(s if s is not None else "").lower().split())


_UML = str.maketrans("äöüß", "aous")


def koelner(word: str) -> str:
    """Kölner Phonetik: gleich klingende deutsche Schreibweisen → gleicher Code
    (Mai/Mey → „6", Meier/Meyer, Aleks/Alex). Für Whisper-Schreibvarianten bei Namen."""
    w = "".join(c for c in (word or "").lower().translate(str.maketrans("äöüß", "aous")) if c.isalpha())
    out = []
    for i, c in enumerate(w):
        prev = w[i - 1] if i else ""
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if c in "aeijouy":
            code = "0"
        elif c == "h":
            continue
        elif c == "b":
            code = "1"
        elif c == "p":
            code = "3" if nxt == "h" else "1"
        elif c in "dt":
            code = "8" if nxt in "csz" else "2"
        elif c in "fvw":
            code = "3"
        elif c in "gkq":
            code = "4"
        elif c == "c":
            if i == 0:
                code = "4" if nxt in "ahkloqrux" else "8"
            else:
                code = "8" if prev in "sz" or nxt not in "ahkoqux" else "4"
        elif c == "x":
            code = "8" if prev in "ckq" else "48"
        elif c == "l":
            code = "5"
        elif c in "mn":
            code = "6"
        elif c == "r":
            code = "7"
        elif c in "sz":
            code = "8"
        else:
            continue
        out.append(code)
    res = []
    for code in "".join(out):
        if not res or res[-1] != code:
            res.append(code)
    return res[0] + "".join(c for c in res[1:] if c != "0") if res else ""


# Deutsche Genre-Wörter → Genre-Namen, wie sie Musikdatenbanken (und damit die Sammlung) meist führen.
# Die Sprache übergibt Namen wörtlich („Hörbuch“) – die Übersetzung passiert hier, nicht im Sprachmodell.
GENRE_SYNONYMS = {
    "hörbuch": "audiobook", "hörbücher": "audiobook", "hörspiel": "audiobook", "hörspiele": "audiobook",
    "geschichte": "audiobook", "geschichten": "audiobook", "klassik": "classic", "klassische musik": "classic",
    "kindermusik": "kinder", "kinderlieder": "kinder", "kinder lieder": "kinder", "filmmusik": "soundtrack",
    "film musik": "soundtrack", "elektro": "electro", "elektronisch": "dance/electronic", "tanzmusik": "dance/electronic",
    "rap": "hip-hop/rap/r&b", "hiphop": "hip-hop/rap/r&b", "hip hop": "hip-hop/rap/r&b", "soul": "soul / funk / r&b",
    "funk": "soul / funk / r&b", "entspannung": "jazz / easy listening", "naturgeräusche": "natur", "witze": "comedy",
}


def _word_ok(w: str, words: list) -> bool:
    """Suchwort kommt (ähnlich geschrieben ODER gleich klingend) im Namen vor."""
    return any(fuzz.ratio(w, x) >= 80 or (len(w) >= 3 and koelner(w) == koelner(x)) for x in words)


def _word_strong(w: str, words: list) -> bool:
    """Wie _word_ok, aber ein Lauttreffer zählt nur mit ≥ 3 Lautzeichen: „Adda“ und „Eddie“ haben beide nur „02“ –
    das ist Zufall, kein Beleg (Whisper hörte „Adele“ als „Adda“ → vorher spielte Eddie Vedder)."""
    return any(fuzz.ratio(w, x) >= 80 or (len(w) >= 3 and koelner(w) == koelner(x) and len(koelner(w)) >= 3)
               for x in words)


class MediaIndex:
    def __init__(self):
        self.albums: dict = {}
        self.songs: dict = {}  # song id -> {id, name, artist, album_id, track_no}
        self.room_id: Optional[int] = None
        self.loaded_at: float = 0
        self.loading = False
        self.progress = (0, 0)
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            self.albums = {int(k): v for k, v in data["albums"].items()}
            self.songs = {int(k): v for k, v in data["songs"].items()}
            self.room_id = data.get("room_id")
            self.loaded_at = data.get("loaded_at", 0)
            self._build()
        except Exception:  # noqa: BLE001
            self._build()

    def _build(self) -> None:
        # Der Director liefert Titel wie „21" (Adele) als Zahl → überall Text, sonst scheitert .strip()
        for a in self.albums.values():
            for k in ("title", "artist", "genre"):
                if a.get(k) is not None and not isinstance(a[k], str):
                    a[k] = str(a[k])
        for s in self.songs.values():
            for k in ("name", "artist"):
                if s.get(k) is not None and not isinstance(s[k], str):
                    s[k] = str(s[k])
        self._album_keys = [(i, _norm(f"{a.get('artist') or ''} {a.get('title') or ''}"))
                            for i, a in self.albums.items()]
        self._song_keys = [(i, _norm(f"{s.get('artist') or ''} {s.get('name') or ''}"))
                           for i, s in self.songs.items()]
        self._by_album: dict = {}           # album_id -> [song, ...] (Titelzahl/Detail ohne Vollscan)
        for s in self.songs.values():
            self._by_album.setdefault(s["album_id"], []).append(s)
        self._song_list = None              # Titel-Tab: einmal sortiert, dann aus dem Speicher

    def status(self) -> dict:
        return {"albums": len(self.albums), "songs": len(self.songs), "room_id": self.room_id,
                "loaded_at": self.loaded_at, "loading": self.loading,
                "progress": list(self.progress)}

    async def refresh(self, conn: Connection, room_ids: list) -> None:
        if self.loading:
            return
        self.loading = True
        try:
            albums, room = [], None
            for rid in room_ids:  # the library is the same for every room that has one
                try:
                    albums = await conn.get(f"/api/v1/locations/rooms/{rid}/media/albums")
                except Exception:  # noqa: BLE001
                    continue
                if albums:
                    room = rid
                    break
            if not albums:
                logbus.warn("media", "No room returned a music library")
                return
            logbus.info("media", f"{len(albums)} albums via room {room}; loading tracks ...")
            new_albums = {a["id"]: {k: a.get(k) for k in
                                    ("id", "title", "artist", "genre", "release_date", "device_id", "img")}
                          for a in albums}
            new_songs: dict = {}
            self.progress = (0, len(albums))
            sem = asyncio.Semaphore(5)

            async def one(a):
                async with sem:
                    try:
                        detail = await conn.get(f"/api/v1/locations/rooms/{room}/media/albums/{a['id']}")
                    except Exception as e:  # noqa: BLE001
                        logbus.warn("media", f"album {a['id']} failed: {type(e).__name__}")
                        return
                    for s in detail.get("songs", []):
                        new_songs[s["id"]] = {"id": s["id"], "name": s.get("name", ""),
                                              "artist": s.get("artist", ""), "album_id": a["id"],
                                              "track_no": s.get("track_no")}
                    self.progress = (self.progress[0] + 1, self.progress[1])

            await asyncio.gather(*(one(a) for a in albums))
            self.albums, self.songs, self.room_id = new_albums, new_songs, room
            self.loaded_at = time.time()
            self._build()
            CACHE.write_text(json.dumps({"albums": self.albums, "songs": self.songs,
                                         "room_id": room, "loaded_at": self.loaded_at},
                                        ensure_ascii=False), encoding="utf-8")
            logbus.info("media", f"Library ready: {len(self.albums)} albums, {len(self.songs)} songs")
        finally:
            self.loading = False

    def browse(self, kind: str, filter_artist: str = None, filter_genre: str = None) -> list:
        """Browse the local library: artists, genres or albums (optionally filtered)."""
        if kind == "artist":
            out: dict = {}
            for a in self.albums.values():
                name = (a.get("artist") or "Unbekannt").strip() or "Unbekannt"
                out.setdefault(name, {"artist": name, "albums": 0, "songs": 0})
                out[name]["albums"] += 1
                out[name]["songs"] += len(self._by_album.get(a["id"], ()))
            return sorted(out.values(), key=lambda x: x["artist"].lower())
        if kind == "genre":
            out = {}
            for a in self.albums.values():
                name = (a.get("genre") or "").strip() or "Ohne Genre"
                out.setdefault(name, {"genre": name, "albums": 0})
                out[name]["albums"] += 1
            return sorted(out.values(), key=lambda x: x["genre"].lower())
        # albums, optionally filtered by artist/genre
        def _s(v):
            return str(v).strip() if v is not None else ""
        res = []
        for a in self.albums.values():
            if filter_artist and _s(a.get("artist")) != filter_artist:
                continue
            if filter_genre and (_s(a.get("genre")) or "Ohne Genre") != filter_genre:
                continue
            songs = self._by_album.get(a["id"], ())
            res.append({"id": a["id"], "title": _s(a.get("title")) or "?",
                        "artist": _s(a.get("artist")) or "Unbekannt",
                        "genre": _s(a.get("genre")),
                        "year": _s(a.get("release_date"))[:4], "songs": len(songs),
                        "img": a.get("img") or ""})
        return sorted(res, key=lambda x: (x["artist"].lower(), x["title"].lower()))

    def ordered_songs(self, album_id: int) -> list:
        """Titel in Hörreihenfolge. Mehrfach-CDs zählen je CD ab 1 → CD-Grenze aus der Titel-ID-Reihenfolge
        (IDs vergibt der Director beim Einlesen fortlaufend): fällt die Titelnummer, beginnt eine neue CD."""
        songs = sorted(self._by_album.get(album_id, ()), key=lambda s: s["id"])
        disc, last, out = 1, 0, []
        for s in songs:
            t = s.get("track_no") or 0
            if t and last and t <= last:
                disc += 1
            last = t or last
            out.append((disc, t, s["id"], s))
        return [x[3] for x in sorted(out, key=lambda x: x[:3])]

    def song_pos(self, album_id: int, song_id: int) -> tuple:
        """(Position in Hörreihenfolge, Position in Director-Reihenfolge, Name) eines Titels, 1-basiert.
        Die Director-Reihenfolge (so wie das Album geliefert wurde) bestimmt das Überspringen bei Control4."""
        ordered = self.ordered_songs(album_id)
        raw = list(self._by_album.get(album_id, ()))
        pos = next((i + 1 for i, s in enumerate(ordered) if s["id"] == song_id), None)
        raw_pos = next((i + 1 for i, s in enumerate(raw) if s["id"] == song_id), None)
        name = next((s["name"] for s in ordered if s["id"] == song_id), None)
        return pos, raw_pos, name

    def album(self, album_id: int) -> dict | None:
        """Ein Album mit Titelliste in Hörreihenfolge (CD 1, CD 2 …; „disc" nur bei mehreren CDs)."""
        a = self.albums.get(album_id)
        if not a:
            return None
        songs = self.ordered_songs(album_id)
        discs = {}
        d, last = 1, 0
        for s in sorted(self._by_album.get(album_id, ()), key=lambda s: s["id"]):
            t = s.get("track_no") or 0
            if t and last and t <= last:
                d += 1
            last = t or last
            discs[s["id"]] = d
        return {"id": album_id, "title": (a.get("title") or "").strip() or "?",
                "artist": a.get("artist") or "Unbekannt", "genre": a.get("genre") or "",
                "year": str(a.get("release_date") or "")[:4], "img": a.get("img") or "",
                "songs": [{"id": s["id"], "name": s["name"], "artist": s.get("artist") or "",
                           "track_no": s.get("track_no"),
                           **({"disc": discs.get(s["id"], 1)} if max(discs.values(), default=1) > 1 else {})}
                          for s in songs]}

    # ---- Sprachwunsch → abspielbares Album ---------------------------------------------
    @staticmethod
    def _score(q: str, cand: str) -> float:
        """Ähnlichkeit Suchtext ↔ Name. Kurze Suchtexte nur gegen ganze Wörter (sonst passt
        „sting" auf „Fighting"), lange auch als Teilstring („fields of gold" in längeren Titeln)."""
        c = _norm(cand)
        if not c:
            return 0.0
        if q == c:
            return 100.0
        sc = fuzz.ratio(q, c)
        words = c.split()
        if set(q.split()) <= set(words):
            sc = max(sc, 95.0 if len(q) >= 3 else 0.0)        # „mey" → „Reinhard Mey"
        elif len(q) >= 3 and all(_word_ok(w, words) for w in q.split()):
            strong = any(_word_strong(w, words) for w in q.split())
            # „Reinhard Mai" klingt wie „Mey" (Reinhard trägt) – nur schwache Lauttreffer → unsicher (Rückfrage)
            sc = max(sc, (90.0 if len(q.split()) == len(words) else 85.0) if strong else 70.0)
        if len(q) >= 6 and len(c) >= len(q):     # Suchtext als Teil eines längeren Namens
            sc = max(sc, fuzz.partial_ratio(q, c) * 0.95, fuzz.token_set_ratio(q, c) * 0.97)
        # mehrteilige Suche: JEDES Wort muss ähnlich vorkommen, sonst kein starker Treffer
        # („xyzzy unbekannt" darf nicht über „unbekannt" auf „Unbekannt Artist" springen)
        qw = [w for w in q.split() if len(w) >= 3]
        if len(qw) > 1 and not all(_word_ok(w, words) for w in qw):
            sc = min(sc, 60.0)
        return float(sc)

    def resolve(self, query: str, kind: str = "any") -> dict | None:
        """kind: album | artist | genre | song | random | any. Liefert EIN Album (+ Begründung)."""
        import random
        q = _norm(query)
        albums = list(self.albums.values())
        if not albums:
            return None

        def pack(a, how, match="", score=100.0, track=None):
            return {"album_id": a["id"], "album": str(a.get("title") or "").strip() or "?",
                    "artist": str(a.get("artist") or "").strip(), "genre": str(a.get("genre") or "").strip(),
                    "img": a.get("img") or "", "kind": how, "match": match, "score": round(score, 1),
                    "track": track}

        if kind == "random" or not q:
            return pack(random.choice(albums), "random")

        m = re.match(r"^(.+?)\s+(?:von|vom|by)\s+(?:der |dem |den |die )?(.+)$", q)
        if m and kind in ("song", "album", "any"):
            t, who = m.group(1), m.group(2)
            art_of = lambda a: _norm((a or {}).get("artist") or "")   # noqa: E731
            cands = []
            if kind in ("song", "any"):
                for sg in self.songs.values():
                    ts = self._score(t, sg.get("name") or "")
                    if ts >= 75:
                        a = self.albums.get(sg.get("album_id"))
                        cands.append((ts + 0.3 * self._score(who, art_of(a)), ts, "song", sg, a))
            if kind in ("album", "any"):
                for a in albums:
                    ts = self._score(t, a.get("title") or "")
                    if ts >= 75:
                        cands.append((ts + 0.3 * self._score(who, art_of(a)), ts, "album", None, a))
            if cands:
                cands.sort(key=lambda x: -x[0])
                _, ts, how, sg, a = cands[0]
                # Interpret genannt, aber der Treffer ist von jemand anderem („Rise von Imagine Dragons“ → Eddie Vedder):
                # nicht einfach spielen – Score drücken, damit die Sprache „Meinst du …?“ fragt (Hörfehler wie „Adda“
                # für Adele bleiben so per „Ja“ erreichbar)
                other = self._score(who, art_of(a)) < 60
                score = min(ts, 70.0) if other else ts
                if a and how == "song":
                    r = pack(a, "song", sg["name"], score, {"name": sg["name"], "track_no": sg.get("track_no")})
                elif a:
                    r = pack(a, "album", str(a.get("title") or ""), score)
                if a and other:
                    r["asked_artist"] = who
                if a:
                    return r
            q = who if kind == "any" else t           # Titel unbekannt → ggf. nur den Interpreten versuchen

        def best(cands, key):
            sc = [(self._score(q, key(c)), c) for c in cands]
            sc.sort(key=lambda x: -x[0])
            return sc[0] if sc else (0.0, None)

        placeholder = re.compile(r"^(unbekannt|unknown|various|diverse|va)\b", re.I)
        artists = sorted({str(a.get("artist")).strip() for a in albums
                          if a.get("artist") and not placeholder.match(str(a.get("artist")).strip())})
        genres = sorted({str(a.get("genre")).strip() for a in albums if a.get("genre")})
        sa, art = best(artists, lambda x: x)
        alts = [n for sc_, n in sorted(((self._score(q, n), n) for n in artists), key=lambda x: -x[0])[:4]
                if sc_ >= 60]
        gq = GENRE_SYNONYMS.get(q)
        sg, gen = best(genres, lambda x: x)
        if gq:                                # „Hörbuch“ → Audiobook usw.
            g2 = next((g for g in genres if _norm(g) == gq), None)
            if g2:
                sg, gen = 100.0, g2
        sal, alb = best(albums, lambda a: a.get("title") or "")
        ss, song = best(list(self.songs.values()), lambda s: s.get("name") or "")

        def by_artist():
            r = pack(random.choice([a for a in albums if str(a.get("artist") or "").strip() == art]), "artist", art, sa)
            r["alternatives"] = [n for n in alts if n != art][:2]
            return r

        def by_genre():
            return pack(random.choice([a for a in albums if str(a.get("genre") or "").strip() == gen]), "genre", gen, sg)

        def by_song():
            a = self.albums.get(song["album_id"])
            return pack(a, "song", song["name"], ss, {"name": song["name"], "track_no": song.get("track_no")}) if a else None

        if kind == "artist":
            return by_artist() if sa >= 70 else None
        if kind == "genre":
            return by_genre() if sg >= 70 else None
        if kind == "album":
            return pack(alb, "album", str(alb.get("title") or ""), sal) if sal >= 65 else None
        if kind == "song":
            return by_song() if ss >= 70 else None
        # any: eindeutiger Interpret schlägt Titel-Teiltreffer; sonst der beste Album-/Titel-Treffer
        if sa >= 90 and sa >= max(sal, ss) - 3:
            return by_artist()
        if sal >= 70 and sal >= ss - 3:
            return pack(alb, "album", str(alb.get("title") or ""), sal)
        if sg >= 85 and sg >= ss:              # „Jazz" = Genre, nicht der Liedtitel „Swamp Jazz"
            return by_genre()
        if ss >= 75:
            return by_song()
        if sa >= 75:
            return by_artist()
        return None

    def by_id(self, album_id: int, kind: str = "album", match: str = "") -> dict | None:
        """Genau dieses Album (vom Sprachmodell aus der Trefferliste gewählt) – gleiche Form wie resolve()."""
        a = self.albums.get(int(album_id))
        if not a:
            return None
        return {"album_id": a["id"], "album": str(a.get("title") or "").strip() or "?",
                "artist": str(a.get("artist") or "").strip(), "genre": str(a.get("genre") or "").strip(),
                "img": a.get("img") or "", "kind": "song" if kind == "song" else "album",
                "match": match or str(a.get("title") or ""), "score": 100.0, "track": None}

    def candidates(self, query: str, n: int = 18) -> list:
        """Kandidaten für die Auswahl durch das Sprachmodell (PLAN-AUSWAHL.md): breit statt genau – das Richtige soll
        DABEI sein, entscheiden tut das Modell. Je Art die Besten (Interpreten, Alben, Titel), damit z. B. „Ada“ auch
        „Adele“ enthält (kurze Namen: Anfang zählt mehr – Jaro-Winkler). Rückgabe [{type, title, artist, album, album_id}]."""
        from rapidfuzz.distance import JaroWinkler
        from rapidfuzz.utils import default_process as dp
        q = query.strip()
        m = re.match(r"^(.+?)\s+(?:von|vom|by)\s+(.+)$", q, re.I)
        title_q, who_q = (m.group(1), m.group(2)) if m else (q, "")
        nq = _norm(q)

        def short_bonus(name: str) -> float:
            words = [_norm(name)] + _norm(name).split()
            return max([JaroWinkler.similarity(nq, w) * 100 for w in words if w[:1] == nq[:1]] or [0.0]) \
                if len(nq) <= 5 else 0.0
        artists = sorted({str(a.get("artist") or "").strip() for a in self.albums.values()
                          if a.get("artist") and not re.match(r"^(unbekannt|unknown|various)", str(a["artist"]), re.I)})
        sa = sorted(((max(fuzz.WRatio(who_q or q, a, processor=dp), short_bonus(a)), a) for a in artists), reverse=True)
        albums = list(self.albums.values())
        sal = sorted(((max(fuzz.WRatio(q, f"{a.get('title')} {a.get('artist')}", processor=dp),
                           fuzz.WRatio(title_q, str(a.get("title") or ""), processor=dp)), a) for a in albums),
                     key=lambda x: -x[0])
        songs = list(self.songs.values())
        names = [f"{s.get('name')} {s.get('artist') or ''}" for s in songs]
        ss = process.extract(q, names, scorer=fuzz.WRatio, processor=dp, limit=40)
        if who_q:
            ss += process.extract(title_q, [str(s.get("name") or "") for s in songs], scorer=fuzz.WRatio, processor=dp, limit=20)
        out, seen = [], set()

        def add(kind, title, artist, album, album_id):
            key = (kind, _norm(title), _norm(artist), _norm(album))
            if key not in seen:
                seen.add(key)
                out.append({"type": kind, "title": title, "artist": artist, "album": album, "album_id": album_id})
        kurz = len(nq) <= 5 and " " not in nq          # ein kurzes Wort ist fast immer ein (verhörter) Name
        for _, a in sa[:10 if kurz else 5]:
            add("Interpret", a, a, "", None)
        for _, a in sal[:3 if kurz else 6]:
            add("Album", str(a.get("title") or ""), str(a.get("artist") or ""), "", a["id"])
        for _, _sc, i in sorted(ss, key=lambda x: -x[1])[:12]:
            s = songs[i]
            alb = self.albums.get(s.get("album_id")) or {}
            add("Titel", str(s.get("name") or ""), str(s.get("artist") or alb.get("artist") or ""), str(alb.get("title") or ""),
                s.get("album_id"))
            if len(out) >= n:
                break
        return out[:n]

    def suggest(self, query: str, n: int = 3) -> list:
        """Nichts sicher gefunden → die ähnlichsten Namen (Interpret, Album, Titel) für die Rückfrage „Meinst du …?“."""
        q = _norm(query)
        m = re.match(r"^(.+?)\s+(?:von|vom|by)\s+(.+)$", q)
        parts = [q] + ([m.group(1), m.group(2)] if m else [])
        artists = {str(a.get("artist") or "").strip() for a in self.albums.values()}
        names = artists | {str(a.get("title") or "").strip() for a in self.albums.values()}
        if len(q.split()) > 1:      # ein einzelnes (verhörtes) Wort nur gegen Interpreten/Alben – Liedtitel gaben Unsinn
            names |= {str(s.get("name") or "").strip() for s in self.songs.values()}   # („Adder“ → „Klavierkonzert a-Dur“)

        def sc_(x, nm):         # Teilstring-Vergleich nur für längere Suchtexte (sonst passt „adda“ auf „Heidi – Folge …“)
            v = self._score(x, nm)
            if len(x) >= 6 and len(nm) >= 0.5 * len(x):   # „Herbert Grönemeier“ ≠ „Cher“ (bloß Teilstring)
                v = max(v, fuzz.WRatio(x, _norm(nm)) * 0.9)
            return v + (3 if nm in artists else 0)
        sc = sorted(((max(sc_(x, nm) for x in parts), nm)
                     for nm in names if nm and not re.match(r"^(unbekannt|unknown|various)", nm, re.I)),
                    key=lambda x: -x[0])
        top, out = (sc[0][0] if sc else 0), []
        for v, nm in sc:                      # nur echte Kandidaten – sonst „nichts gefunden“; Schreibvarianten einmal
            if v < 72 or v < top - 6 or len(out) >= n:
                break
            if nm.lower() not in {x.lower() for x in out}:
                out.append(nm)
        return out

    def song_list(self) -> list:
        """Alle Titel A–Z (kompakt: i=id, n=Name, a=Interpret, b=Album-ID, t=Albumtitel, k=Tracknummer)."""
        if self._song_list is None:
            rows = []
            for s in self.songs.values():
                a = self.albums.get(s["album_id"]) or {}
                name = str(s.get("name") or "").strip()
                if not name:
                    continue
                rows.append({"i": s["id"], "n": name, "a": str(s.get("artist") or a.get("artist") or "").strip(),
                             "b": s["album_id"], "t": str(a.get("title") or "").strip(), "k": s.get("track_no")})
            # gleiche Regel wie C4.letterOf: führende Zeichen weg, Umlaute wie Grundbuchstaben
            rows.sort(key=lambda r: (re.sub(r"^[^0-9a-zäöüß]+", "", _norm(r["n"])).translate(_UML), r["a"].lower()))
            self._song_list = rows
        return self._song_list

    def random_albums(self, n: int = 12) -> list:
        import random
        ids = random.sample(list(self.albums), min(n, len(self.albums)))
        return [x for x in self.browse("album") if x["id"] in ids]

    def find_album(self, title: str, artist: str = "") -> int | None:
        """Album-ID zu Titel (+ Interpret) aus der Wiedergabe-Info des Directors."""
        t, ar = _norm(title), _norm(artist)
        if not t:
            return None
        best = None
        for i, a in self.albums.items():
            if _norm(a.get("title")) == t:
                if not ar or _norm(a.get("artist")) == ar:
                    return i
                best = best or i
        return best

    def search_grouped(self, query: str, limit: int = 12) -> dict:
        """Suche für die Oberfläche: Interpreten, Alben, Titel getrennt. Teilstring-Treffer zählen voll
        („fields" findet „Fields of Gold"), sonst Fuzzy ab 75 – keine Zufallstreffer bei kurzen Suchen."""
        q = _norm(query)
        if not q:
            return {"artists": [], "albums": [], "songs": []}

        def score(name: str) -> float:
            c = _norm(name)
            if not c:
                return 0.0
            if c.startswith(q):
                return 100.0
            if f" {q}" in f" {c}":            # Wortanfang: „gold" → „Fields of Gold"
                return 96.0
            if q in c:                         # mitten im Wort („sting" in „Twisting") nur schwach
                return 76.0
            if len(q) < 4:
                return 0.0
            return max(fuzz.ratio(q, c), fuzz.partial_ratio(q, c) * 0.92 if len(c) >= len(q) else 0) * 0.95

        arts: dict = {}
        for a in self.albums.values():
            n = str(a.get("artist") or "").strip()
            if n and n not in arts:
                arts[n] = score(n)
        artists = sorted(((s, n) for n, s in arts.items() if s >= 75), key=lambda x: (-x[0], x[1].lower()))[:limit]
        albums = sorted(((score(a.get("title") or ""), a) for a in self.albums.values()), key=lambda x: -x[0])
        albums = [(s, a) for s, a in albums if s >= 75][:limit]
        songs = sorted(((score(s.get("name") or ""), s) for s in self.songs.values()), key=lambda x: -x[0])
        songs = [(sc, s) for sc, s in songs if sc >= 75][:limit * 2]
        return {
            "artists": [{"artist": n, "albums": sum(1 for a in self.albums.values()
                                                   if str(a.get("artist") or "").strip() == n)} for _, n in artists],
            "albums": [{"id": a["id"], "title": str(a.get("title") or "").strip(), "artist": str(a.get("artist") or ""),
                        "img": a.get("img") or "", "year": str(a.get("release_date") or "")[:4]} for _, a in albums],
            "songs": [{"id": s["id"], "name": s.get("name") or "", "artist": s.get("artist") or "",
                       "album_id": s["album_id"], "track_no": s.get("track_no"),
                       "album": str((self.albums.get(s["album_id"]) or {}).get("title") or "").strip(),
                       "img": (self.albums.get(s["album_id"]) or {}).get("img") or ""} for _, s in songs],
        }

    def search(self, query: str, limit: int = 8) -> list:
        q = _norm(query)
        if not q:
            return []
        results = []
        # process.extract on a dict yields (text, score, key); key is the id
        for _text, score, i in process.extract(q, dict(self._album_keys),
                                               scorer=fuzz.WRatio, limit=limit):
            a = self.albums[i]
            results.append({"kind": "album", "album_id": i, "img": a.get("img") or "", "title": (a.get("title") or "").strip(),
                            "artist": a.get("artist") or "", "genre": a.get("genre"), "score": round(score, 1)})
        for _text, score, i in process.extract(q, dict(self._song_keys),
                                               scorer=fuzz.WRatio, limit=limit):
            s = self.songs[i]
            a = self.albums.get(s["album_id"], {})
            results.append({"kind": "song", "song_id": i, "album_id": s["album_id"], "img": a.get("img") or "",
                            "title": s.get("name") or "", "artist": s.get("artist") or "",
                            "album": (a.get("title") or "").strip(), "score": round(score, 1)})
        results.sort(key=lambda r: -r["score"])
        return results[:limit]
