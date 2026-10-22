"""Offline-Test Filme/Serien per Sprache: KODI-Suche (kodi.py mit nachgebautem JSON-RPC) und das Werkzeug video_play
(voice.py mit nachgebauter Bridge). Kein Haus, kein KODI, kein Ollama."""
from pathlib import Path
import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")   # Windows-Konsole (cp1252) kann „→“ nicht
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import voice as v  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
v.ZULETZT_FILE = _pl.Path(_tf.gettempdir()) / "c4_test_zuletzt.json"   # Tests schreiben nie in data/
from c4bridge import kodi as km  # noqa: E402

MOVIES = [{"movieid": 43, "title": "Zurück in die Zukunft", "year": 1985, "playcount": 1},
          {"movieid": 118, "title": "Zurück in die Zukunft III", "year": 1990, "originaltitle": "Back to the Future Part III"},
          {"movieid": 472, "title": "Der Astronaut – Project Hail Mary", "year": 2026},
          {"movieid": 469, "title": "Die drei ??? – Toteninsel", "year": 2026},
          {"movieid": 58, "title": "Das Kanu des Manitu", "year": 2025},
          {"movieid": 260, "title": "Star Wars Extras - Archives Episode I-III", "year": 1989}]
SHOWS = [{"tvshowid": 31, "title": "Breaking Bad", "year": 2008, "episode": 62, "watchedepisodes": 10},
         {"tvshowid": 17, "title": "Bad Banks", "year": 2018, "episode": 8, "watchedepisodes": 0}]
EPISODES = [{"episodeid": 1001, "title": "Pilot", "season": 1, "episode": 1, "playcount": 1},
            {"episodeid": 1002, "title": "Cat's in the Bag", "season": 1, "episode": 2, "playcount": 1},
            {"episodeid": 1003, "title": "...And the Bag's in the River", "season": 1, "episode": 3, "playcount": 0,
             "resume": {"position": 600.0}},
            {"episodeid": 1004, "title": "Cancer Man", "season": 1, "episode": 4, "playcount": 0},
            {"episodeid": 2003, "title": "Bit by a Dead Bee", "season": 2, "episode": 3, "playcount": 0}]

ok = True


def check(label, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FEHLER ") + label + (f"  → {info}" if info else ""))


class FakeKodi(km.Kodi):
    def __init__(self):
        super().__init__()
        self.players = {"491": {"driver_id": 491, "name": "Mediacenter", "host": "x", "port": 1, "sections": []}}
        self.writes = []

    async def rpc(self, did, method, params=None, timeout=6):
        if method == "VideoLibrary.GetMovies":
            return {"movies": MOVIES}
        if method == "VideoLibrary.GetTVShows":
            return {"tvshows": SHOWS}
        if method == "VideoLibrary.GetEpisodes":
            eps = [e for e in EPISODES if params.get("season") is None or e["season"] == params["season"]]
            return {"episodes": eps}
        raise km.KodiError(method)

    async def _rpc(self, host, port, method, params=None, timeout=6):
        self.writes.append((method, params))
        return "OK"


async def kodi_tests():
    k = FakeKodi()
    h = await k.search("Zurück in die Zukunft drei", "any")
    check("„… drei“ → Teil III (nicht Teil I)", h and h["id"] == 118, h and h["title"])
    h = await k.search("zurück in die zukunft", "movie")
    check("genauer Titel schlägt längere Variante", h and h["id"] == 43 and h["score"] == 100, h and (h["title"], h["score"]))
    check("Alternative genannt", h and "Zurück in die Zukunft III" in h["alternatives"], h and h["alternatives"])
    h = await k.search("Breaking Bad", "any")
    check("Serie gefunden (any)", h and h["type"] == "tvshow" and h["id"] == 31, h)
    h = await k.search("Breaking Bett", "any")
    check("verhört: Treffer, aber unsicher (< 85)", h and h["id"] == 31 and h["score"] < 85, h and (h["title"], h["score"]))
    h = await k.search("die drei Fragezeichen Folge 10", "any")
    check("Hörspiel-Folge trifft keinen Film sicher (vorher: Star Wars Extras ≥ 85)", h is None or h["score"] < 80, h and (h["title"], h["score"]))
    h = await k.search("die drei Fragezeichen Toteninsel", "any")
    check("„???“ = Fragezeichen: Toteninsel sicher gefunden", h and h["id"] == 469 and h["score"] >= 85, h and (h["title"], h["score"]))
    h = await k.search("Project Hail Mary", "movie")
    check("Teil des Titels reicht", h and h["id"] == 472 and h["score"] >= 85, h and (h["title"], h["score"]))
    h = await k.search("Kanu des Manitu", "series")
    check("Filmtitel als Serie gesucht → kein Treffer bei Serien", h is None or h["type"] == "tvshow", h)
    e = await k.episode_pick("491", 31)
    check("ohne Angabe: angefangene Folge zuerst", e and e["id"] == 1003, e)
    EPISODES[2]["resume"] = {"position": 0}
    e = await k.episode_pick("491", 31)
    check("sonst erste ungesehene", e and e["id"] == 1003, e)
    e = await k.episode_pick("491", 31, 2, 3)
    check("Staffel 2 Folge 3", e and e["id"] == 2003 and e["season"] == 2, e)
    e = await k.episode_pick("491", 31, None, 4)
    check("nur Folge 4 (erste Staffel mit Folge 4)", e and e["id"] == 1004, e)
    await k.play("491", "movie", 43)
    check("Player.Open mit movieid + resume", k.writes[-1] == ("Player.Open", {"item": {"movieid": 43}, "options": {"resume": True}}), k.writes[-1])
    try:
        await k._write("491", "Player.Stop", {})
        check("andere Schreibmethoden bleiben gesperrt", False)
    except km.KodiError:
        check("andere Schreibmethoden bleiben gesperrt", True)
    check("Normierung", km._norm_title("Der Astronaut – Project Hail Mary") == "astronaut project hail mary", km._norm_title("Der Astronaut – Project Hail Mary"))


ROOMS = [{"id": 7, "name": "Wohnzimmer", "aliases": []}, {"id": 260, "name": "Arbeit Alex", "aliases": []}]


class FakeBridge(v.Bridge):
    def __init__(self, score=100, hit_type="movie"):
        super().__init__("http://x")
        self.posts, self.score, self.hit_type = [], score, hit_type
        self.missing = {"Wohnzimmer": ["Radio", "Qobuz"], "Arbeit Alex": ["TV/Video", "Filme/Serien"]}
        self._caps = {"Wohnzimmer": (9e12, ""), "Arbeit Alex": (9e12, "")}

    async def context(self):
        return [], ROOMS

    async def load_caps(self, names):
        return None

    async def get(self, path):
        import aiohttp
        if path.startswith("/api/media/resolve"):
            raise aiohttp.ClientResponseError(None, (), status=404)
        if path.startswith("/api/media/suggest"):
            return {"names": []}
        if path.startswith("/api/kodi/search"):
            import aiohttp
            if "q=xyz" in path or ("kind=tvshow" in path and self.hit_type == "movie"):
                raise aiohttp.ClientResponseError(None, (), status=404)
            if self.hit_type == "tvshow":
                return {"did": "491", "type": "tvshow", "id": 31, "title": "Breaking Bad", "year": 2008, "score": self.score,
                        "alternatives": ["Bad Banks"]}
            return {"did": "491", "type": "movie", "id": 118, "title": "Zurück in die Zukunft III", "year": 1990,
                    "score": self.score, "alternatives": ["Zurück in die Zukunft"]}
        raise RuntimeError(path)

    async def post(self, path, body=None):
        self.posts.append((path, body))
        if body.get("kind") == "tvshow":
            return {"ok": True, "kind": "episode", "id": 2003, "title": "Breaking Bad", "sub": "S02E03 · Bit by a Dead Bee", "dry": body.get("dry")}
        return {"ok": True, "kind": "movie", "id": body["id"], "title": "Zurück in die Zukunft III", "sub": "1990", "dry": body.get("dry")}


async def tool(args, rooms=("Wohnzimmer",), said=None, score=100, hit_type="movie"):
    b = FakeBridge(score, hit_type)
    r = await v.execute_tool({"name": "video_play", "arguments": dict(args)}, b, True, list(rooms), v.load_cfg(),
                             utterance=said, current=said)
    return r, b.posts


async def voice_tests():
    r, posts = await tool({"search": "Zurück in die Zukunft 3", "kind": "movie"}, said="Starte den Film Zurück in die Zukunft 3")
    check("Film startet (Trockenlauf)", str(r).startswith("Film „Zurück in die Zukunft III“ in Wohnzimmer") and posts[0][1]["dry"] is True, r)
    r, posts = await tool({"search": "den Film Zurück in die Zukunft", "kind": "movie"}, score=82, said="Starte den Film Zurück in die Zukunft")
    check("unsicher → Rückfrage mit Angeboten", isinstance(r, v.Problem) and "Meinst du den Film" in r and len(r.offers) == 2, r)
    check("Angebot trägt den gefundenen Titel", r.offers[0]["call"]["arguments"]["search"] == "Zurück in die Zukunft III", r.offers[0])
    r, posts = await tool({"search": "Breaking Bad", "kind": "series", "season": 2, "episode": "3"}, hit_type="tvshow",
                          said="Breaking Bad Staffel 2 Folge 3")
    check("Serie mit Staffel/Folge", "S02E03" in str(r) and posts[0][1]["season"] == 2 and posts[0][1]["episode"] == 3, (r, posts))
    r, posts = await tool({"search": "Breaking Bad", "kind": "any"}, rooms=("Arbeit Alex",), hit_type="tvshow", said="Spiel Breaking Bad")
    check("Raum ohne Mediathek, aber Musik → Umleitung auf Musik (Qobuz-Angebot), kein Video-Aufruf",
          isinstance(r, v.Problem) and "Qobuz" in r and not any("/video" in p[0] for p in posts), r)
    r, posts = await tool({"search": "xyz", "kind": "any"}, said="Starte xyz")
    check("nichts gefunden", isinstance(r, v.Problem) and "finde ich in der Mediathek nichts" in r, r)
    r, posts = await tool({"search": "Breaking Bad", "kind": "series", "room": "Wohnzimmer"}, rooms=("Arbeit Alex",),
                          hit_type="tvshow", said="Starte im Wohnzimmer die Serie Breaking Bad")
    check("anderer Raum genannt → dort", "in Wohnzimmer" in str(r) and posts and "/api/rooms/7/video" in posts[0][0], r)
    b = FakeBridge()
    names = lambda rs: {x["function"]["name"] for x in v.tools_for(b, v.load_cfg(), rs)}   # noqa: E731
    cfg_pr = {**v.load_cfg(), "llm": {**v.load_cfg()["llm"], "tools_per_room": True}}
    names = lambda rs: {x["function"]["name"] for x in v.tools_for(b, cfg_pr, rs)}   # noqa: E731
    check("Standard: eine Liste für alle Räume", "video_play" in {x["function"]["name"] for x in v.tools_for(b, v.load_cfg(), ["Arbeit Alex"])})
    check("Werkzeuge Wohnzimmer (tools_per_room): video_play ja, stream_play nein (kein Radio/Qobuz)",
          "video_play" in names(["Wohnzimmer"]) and "stream_play" not in names(["Wohnzimmer"]), sorted(names(["Wohnzimmer"])))
    check("Werkzeuge Arbeit Alex: kein video_play", "video_play" not in names(["Arbeit Alex"]))
    check("Mikrofon ohne Raum: alle Werkzeuge", "video_play" in names([]) and "stream_play" in names([]))
    r, posts = await tool({"search": "die drei Fragezeichen", "kind": "any", "episode": 10}, said="Spiel die drei Fragezeichen Folge 10")
    check("„Folge 10“ + Film-Treffer → kein Film, nicht gefunden", isinstance(r, v.Problem) and "nichts" in r and not any("/video" in p[0] for p in posts), r)
    q = v.quick_answer("Mach Netflix an")
    check("Netflix → feste Antwort", q and "Netflix" in q["content"], q)
    check("„Starte den Film Inception“ → KEINE feste Absage mehr", v.quick_answer("Starte den Film Inception") is None)
    check("video_play behauptet ohne Werkzeug → nachfassen", v.claims_done("Film „Inception“ in Wohnzimmer"))
    from c4bridge.dialog import domains_of, _TOOL_DOMAIN
    check("Dialog: Staffel/Film = TV-Thema", domains_of("Breaking Bad Staffel 2") == {"tv"} and _TOOL_DOMAIN["video_play"] == "tv",
          domains_of("Breaking Bad Staffel 2"))


async def main():
    await kodi_tests()
    await voice_tests()
    print("\nALLES OK" if ok else "\nFEHLER vorhanden")


asyncio.run(main())
