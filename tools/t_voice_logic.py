"""Offline-Test der Sprach-Werkzeuge mit nachgebauter Bridge (kein Haus, kein Ollama)."""
from pathlib import Path
import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")   # Windows-Konsole (cp1252) kann „→“ nicht
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import voice as v  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
v.ZULETZT_FILE = _pl.Path(_tf.gettempdir()) / "c4_test_zuletzt.json"   # Tests schreiben nie in data/

ROOMS = [{"id": 1, "name": "Wohnzimmer", "aliases": ["Stube"]}, {"id": 2, "name": "Küche", "aliases": []},
         {"id": 3, "name": "Arbeit Alex", "aliases": ["Büro"]}]
DEVS = [
    {"id": 11, "type": "light", "name": "Axxess Universal Dimmer", "room": "Wohnzimmer", "room_id": 1},
    {"id": 12, "type": "light", "name": "Decke", "room": "Wohnzimmer", "room_id": 1},
    {"id": 21, "type": "light", "name": "Axxess Universal Dimmer", "room": "Küche", "room_id": 2},
    {"id": 22, "type": "light", "name": "Spots", "room": "Küche", "room_id": 2},
    {"id": 31, "type": "light", "name": "Schreibtisch", "room": "Arbeit Alex", "room_id": 3},
    {"id": 32, "type": "switch", "name": "Pumpe", "room": "Küche", "room_id": 2},
]
STATE = {1: [{"id": 11, "on": True, "level": 40}, {"id": 12, "on": False, "level": 0}],
         2: [{"id": 21, "on": False, "level": None}, {"id": 22, "on": True, "level": 50}],
         3: [{"id": 31, "on": False, "level": 0}]}


class FakeBridge(v.Bridge):
    def __init__(self, vol=35):
        super().__init__("http://x")
        self.posts, self.vol = [], vol

    async def context(self):
        return DEVS, ROOMS

    async def get(self, path):
        if path.startswith("/api/devices/state?room_id="):
            return STATE[int(path.split("=")[1])]
        if path.startswith("/api/rooms/") and path.endswith("/state"):
            return {"volume": self.vol}
        if "/climate" in path:
            return [{"id": 41, "temperature_c": 21}]
        raise RuntimeError(path)

    async def post(self, path, body=None):
        self.posts.append((path, body))
        return {"ok": True}


async def run(name, args, rooms_=("Wohnzimmer",), vol=35):
    b = FakeBridge(vol)
    r = await v.execute_tool({"name": name, "arguments": args}, b, False, list(rooms_), v.load_cfg())
    return r, b.posts


ok = True


def check(label, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FEHLER ") + label + (f"  → {info}" if info else ""))


async def main():
    # gleichnamige Geräte: Sprecherraum bzw. genannter Raum gewinnt
    r, p = await run("light_set", {"device": "Axxess Universal Dimmer", "on": True}, ("Küche",))
    check("gleicher Name → Gerät im Sprecherraum (Küche #21)", p and p[0][0] == "/api/devices/21/level", p)
    r, p = await run("light_set", {"device": "Axxess Universal Dimmer", "room": "Stube", "on": True}, ("Küche",))
    check("gleicher Name + genannter Raum per Alias (Stube → Wohnzimmer #11)", p and p[0][0] == "/api/devices/11/level", p)
    r, p = await run("light_set", {"device": "ID 21", "on": False}, ("Küche",))
    check("Gerät per ID (im eigenen Raum)", p and p[0][0] == "/api/devices/21/level", p)
    # Raum per Alias/ungenau (vorher: „In Büro gibt es keine Lichter")
    r, p = await run("light_set", {"room": "Büro", "on": True})
    check("Licht an im Büro (Alias) → Schreibtisch", p == [("/api/devices/31/level", {"level": 100})], r)
    r, p = await run("light_set", {"room": "Arbeitszimmer Alex", "on": True})
    check("ungenauer Raumname → Arbeit Alex", p == [("/api/devices/31/level", {"level": 100})], r)
    # heller: Mikrofon für 2 Räume – beide Räume gelesen, ausgeschaltete Lichter bleiben aus
    r, p = await run("light_adjust", {"direction": "up", "strength": "mittel"}, ("Wohnzimmer", "Küche"))
    ids = sorted(x[0] for x in p)
    check("heller (2 Räume): nur die brennenden #11/#22, nichts auf 100 % geraten",
          ids == ["/api/devices/11/level", "/api/devices/22/level"] and all(x[1]["level"] < 100 for x in p), (r, p))
    r, p = await run("light_adjust", {"direction": "up", "strength": "klein"}, ("Arbeit Alex",))
    check("heller, alles aus → Licht geht an (wie bisher)", p == [("/api/devices/31/level", {"level": 30})], r)
    r, p = await run("light_adjust", {"direction": "down", "strength": "mittel"}, ("Wohnzimmer",))
    check("dunkler → nur #11 auf 20 %", p == [("/api/devices/11/level", {"level": 20})], r)
    # Lautstärke unbekannt → nichts setzen
    r, p = await run("volume_adjust", {"direction": "up"}, vol=None)
    check("lauter ohne bekannte Lautstärke → kein Befehl, Problem", not p and isinstance(r, v.Problem), r)
    r, p = await run("volume_adjust", {"direction": "up"}, vol=35)
    check("lauter bei 35 → 43 (Stufe mittel = 8 laut voice.json)", p == [("/api/rooms/1/volume", {"level": 43})], r)
    # Schalter
    r, p = await run("switch_set", {"device": "Pumpe", "on": True}, ("Küche",))
    check("Schalter Pumpe an (Sprecher in der Küche)", p == [("/api/switches/32/on", None)], r)
    # Raumregel: ohne Raumnennung nur der Sprecherraum – nicht in anderen Räumen raten
    r, p = await run("switch_set", {"device": "Pumpe", "on": True}, ("Wohnzimmer",))
    check("Pumpe aus dem Wohnzimmer: kein Befehl, Rückfrage mit Vorschlag Küche", not p and isinstance(r, v.Problem) and "Küche" in r and "?" in r, r)
    r, p = await run("switch_set", {"device": "Pumpe", "room": "Küche", "on": True}, ("Wohnzimmer",))
    check("Pumpe mit genannter Küche → geschaltet", p == [("/api/switches/32/on", None)], r)
    r, p = await run("light_set", {"device": "Spots", "on": True}, ("Arbeit Alex",))
    check("Spots aus dem Büro: Rückfrage statt Küche schalten", not p and isinstance(r, v.Problem), r)
    r, p = await run("light_set", {"device": "Spots", "on": True}, ())
    check("Mikrofon ohne Raum: Name reicht (Spots #22)", p and p[0][0] == "/api/devices/22/level", r)
    r, p = await run("switch_set", {"device": "Kühlschrank", "on": True})
    check("unbekannter Schalter → Problem", not p and isinstance(r, v.Problem), r)
    # Kurzmodus: Fehler werden ganz gesprochen
    sp = v.spoken_answer([r], r, {"mode": "short"})
    check("Kurzmodus spricht Fehler ganz (nicht „Okay.“)", sp != "Okay.", sp)
    ok_r, _ = await run("light_set", {"room": "Küche", "on": False})
    check("Kurzmodus bei Erfolg „Okay.“", v.spoken_answer([ok_r], ok_r, {"mode": "short"}) == "Okay.", ok_r)
    # Raum ohne Mikrofon-Zuordnung → Rückfrage als Problem
    r, p = await run("media_control", {"action": "pause"}, ())
    check("Pause ohne Raum → Rückfrage (Problem mit ?)", isinstance(r, v.Problem) and "?" in r, r)
    # Kurzbefehle auch im Verlauf, aber nicht als Antwort auf eine Rückfrage
    h1 = [{"role": "user", "content": "Licht an"}, {"role": "assistant", "content": "2 Lichter an"}]
    h2 = [{"role": "user", "content": "Licht an"}, {"role": "assistant", "content": "Welches Licht meinst du?"}]
    q1 = await v.ollama_decide(v.load_cfg(), "", "Pause", h1)
    check("„Pause“ im 30-s-Verlauf → Kurzbefehl", q1.get("quick"), q1)
    check("quick_intent erkennt „bitte lauter“", v.quick_intent("Bitte lauter!") is not None)
    print("vor Rückfrage bleibt es beim Modell (kein quick):", "ja" if "?" in h2[-1]["content"] else "nein")
    # Qobuz-Zusammenhang: läuft Qobuz, bleibt eine Musik-Anfrage dort (außer „Sammlung“)
    class QB(FakeBridge):
        def __init__(self, svc):
            super().__init__()
            self.svc, self.seen = svc, []

        async def now_playing(self, names):
            return {n: self.svc for n in names or []}

        async def get(self, path):
            self.seen.append(path)
            if path.startswith("/api/media/resolve"):
                return {"kind": "song", "score": 100, "album": "21", "artist": "Adele", "match": "Set Fire to the Rain", "album_id": 1}
            return await super().get(path)

        async def post(self, path, body=None):
            self.seen.append(path)
            if path.endswith("/stream"):
                return {"title": "Set Fire to the Rain", "kind": "song", "sub": "Adele • Adele • 21"}
            return await super().post(path, body)
    for svc, said, want in (("Qobuz – Imagine Dragons – „Rise“", "Spiel Set Fire to the Rain von Adele", "/stream"),
                            ("Qobuz – Imagine Dragons – „Rise“", "Spiel aus unserer Sammlung Set Fire to the Rain", "/api/media/play"),
                            ("eigene Sammlung – Sting", "Spiel Set Fire to the Rain von Adele", "/api/media/play")):
        b = QB(svc)
        r = await v.execute_tool({"name": "music_play", "arguments": {"search": "Set Fire to the Rain", "artist": "Adele", "kind": "song"}},
                                 b, False, ["Wohnzimmer"], v.load_cfg(), utterance=said, current=said)
        check(f"läuft {svc.split(' –')[0]}, „{said}“ → {'Qobuz' if want == '/stream' else 'Sammlung'}",
              any(want in x for x in b.seen), (str(r)[:80], b.seen[-2:]))
    b = QB("Qobuz – Adele – „Hello“")
    r = await v.execute_tool({"name": "stream_play", "arguments": {"service": "qobuz", "search": "Skyfall"}}, b, False,
                             ["Wohnzimmer"], v.load_cfg(), utterance="Spiel Skyfall", current="Spiel Skyfall")
    check("stream_play qobuz ohne „Qobuz“ im Satz, aber Qobuz läuft → bleibt Qobuz", any("/stream" in x for x in b.seen), b.seen[-2:])

    # Quelle: genannt schlägt „läuft gerade“; Qobuz-Vermutung ohne Treffer → Sammlung; andere Version; Alben
    class QB2(QB):
        def __init__(self, svc, qobuz_hit=True):
            super().__init__(svc)
            self.qobuz_hit, self.bodies = qobuz_hit, []
            self.artists = ["Amy Macdonald", "Adele"]

        async def get(self, path):
            self.seen.append(path)
            if path.startswith("/api/rooms/") and path.endswith("/state"):
                return {"volume": 20, "bluos": {"title": "Sk8er Boi", "artist": "Avril Lavigne", "album": "Rock Covers"}}
            if path.startswith("/api/media/browse"):
                return [{"title": "This Is the Life"}, {"title": "Life in a Beautiful Light"}] if "Amy" in path else []
            if "/stream/albums" in path:
                return {"albums": ["Let Go", "Under My Skin"]}
            if path.startswith("/api/media/resolve"):
                return {"kind": "song", "score": 100, "album": "Life in a Beautiful Light", "artist": "Amy Macdonald",
                        "match": "Life in a Beautiful Light", "album_id": 2}
            return await FakeBridge.get(self, path)

        async def post(self, path, body=None):
            self.seen.append(path)
            self.bodies.append(body)
            if path.endswith("/stream"):
                if not self.qobuz_hit:
                    import aiohttp
                    raise aiohttp.ClientResponseError(None, (), status=404)
                return {"title": "Sk8er Boi", "kind": "song", "sub": "Avril Lavigne • Avril Lavigne • Let Go"}
            return {"ok": True}

    async def m(name, args, cur, svc="Qobuz – Adele – „Hello“", dialog=None, hit=True):
        b = QB2(svc, hit)
        r = await v.execute_tool({"name": name, "arguments": args}, b, False, ["Wohnzimmer"], v.load_cfg(),
                                 utterance=dialog or cur, current=cur)
        return r, b
    r, b = await m("stream_play", {"service": "qobuz", "search": "Life in a Beautiful Light", "artist": "Amy Macdonald"},
                   "Spiel Amy Macdonald Life in a Beautiful Light aus unserer Musik")
    check("Qobuz läuft, „aus unserer Musik“, Modell nimmt stream_play → Sammlung", "/api/media/play" in b.seen
          and not any(x.endswith("/stream") for x in b.seen), (str(r)[:70], b.seen))
    r, b = await m("music_play", {"search": "Life in a Beautiful Light", "kind": "song"}, "Ja, die meine ich.",
                   dialog="Spiel Life in a Beautiful Light aus unserer Musik Meinst du X? Ja, die meine ich.")
    check("Antwort „Ja, die meine ich“, Sammlung im offenen Dialog genannt → Sammlung", "/api/media/play" in b.seen
          and not any(x.endswith("/stream") for x in b.seen), b.seen)
    r, b = await m("music_play", {"search": "Life in a Beautiful Light", "kind": "song"}, "Spiel Life in a Beautiful Light",
                   hit=False)
    check("Qobuz läuft, Quelle nicht genannt, Qobuz findet nichts → Rückfrage „in unserer Musik suchen?“ mit Angebot",
          "unserer Musik suchen?" in r and "/api/media/play" not in b.seen
          and getattr(r, "offers", [{}])[0].get("call", {}).get("name") == "music_play", (str(r)[:90], b.seen))
    off_ = r.offers[0]["call"]
    r, b = await m(off_["name"], off_["arguments"], None)
    check("… „Ja“ → sucht und spielt in der Sammlung", "/api/media/play" in b.seen and not any(x.endswith("/stream") for x in b.seen),
          (str(r)[:70], b.seen))
    r, b = await m("stream_play", {"service": "qobuz", "search": "", "version": "andere"}, "Spiel eine andere Version davon")
    body = next((x for x in b.bodies if x and "skip" in x), {})
    check("„andere Version“ → Qobuz-Suche nach laufendem Titel, laufende Aufnahme übersprungen",
          body.get("q") == "Sk8er Boi" and body.get("skip", {}).get("album") == "Rock Covers" and body.get("kind") == "song", body)
    r, b = await m("music_play", {"search": "", "kind": "song", "version": "andere"}, "Nicht diese Version, eine andere")
    check("music_play mit version=andere → Qobuz mit skip", any(x and x.get("skip") for x in b.bodies), b.bodies)
    r, b = await m("music_albums", {"artist": "Amy McDonald"}, "Nenne mir mal die Alben von Amy McDonald")
    check("Alben aus der Sammlung (Name unscharf)", r == "Von Amy Macdonald haben wir 2 Alben: This Is the Life und "
          "Life in a Beautiful Light.", r)
    r, b = await m("music_albums", {"artist": "Avril Lavigne"}, "Welche Alben gibt es von Avril Lavigne?")
    check("nicht in der Sammlung → Qobuz-Alben", r == "Auf Qobuz von Avril Lavigne: Let Go und Under My Skin.", r)

    # Frage schaltet nie; „gemütlich an“ bei ausgeschalteten Lichtern → gedimmt einschalten
    async def say(name, args, said, rooms_=("Wohnzimmer",)):
        b = FakeBridge()
        r = await v.execute_tool({"name": name, "arguments": args}, b, False, list(rooms_), v.load_cfg(), utterance=said)
        return r, b.posts
    r, p = await say("light_set", {"on": False, "room": "Küche"}, "Ist in der Küche noch Licht an?")
    check("Frage „Ist in der Küche noch Licht an?“ → nichts geschaltet, „Ja, da ist noch Licht an.“",
          not p and r == "Ja, da ist noch Licht an.", (r, p))
    r, p = await say("light_set", {"on": False}, "Wo im Haus ist noch Licht an?")
    check("„Wo im Haus ist noch Licht an?“ → Räume", not p and r == "In Wohnzimmer und Küche.", (r, p))
    r, p = await say("light_set", {"on": False}, "Ist im Büro noch Licht an?", ("Arbeit Alex",))
    check("Ja/Nein-Frage, alles aus → „Nein, …“", not p and r == "Nein, in Arbeit Alex ist alles aus.", (r, p))
    r, p = await say("lights_status", {"room": "Küche"}, "Welche Lichter sind in der Küche an?")
    check("lights_status „Welche …“ → Lichter nennen", not p and r == "In Küche brennt: Spots.", (r, p))
    r, p = await say("lights_status", {}, "Ist im Haus noch irgendwo Licht an?")
    check("lights_status Ja/Nein fürs Haus → mit Räumen", r == "Ja, noch in Wohnzimmer und Küche.", r)
    r, p = await say("lights_status", {}, "Welche Lichter brennen im ganzen Haus?")
    check("lights_status „Welche … im ganzen Haus“ → je Raum", r == "Wohnzimmer: Axxess Universal Dimmer; Küche: Spots.", r)
    r, p = await say("blind_action", {"action": "close"}, "Sind die Rollläden zu?")
    check("Frage an Rollladen → nichts geschaltet", not p and "Frage" in r, (r, p))
    for s_, want in (("Ist egal, mach alle an", False), ("Kannst du das Licht ausmachen?", False),
                     ("Brennt im Bad noch Licht?", True), ("Welche Lichter sind an?", True), ("Mach das Licht aus", False)):
        check(f"asks_state(„{s_}“) = {want}", v.asks_state(s_) == want)
    r, p = await say("light_set", {"on": True}, "Ist egal, mach alle an", ("Arbeit Alex",))
    check("Antwort „Ist egal, mach alle an“ → schaltet", p, (r, p))
    b = FakeBridge()
    r = await v.execute_tool({"name": "light_set", "arguments": {"on": True}}, b, False, ["Arbeit Alex"], v.load_cfg(),
                             utterance="Ist im Büro Licht an? Nein. Dann mach es an", current="Dann mach es an")
    check("frühere Frage im Dialog, aktueller Satz „Dann mach es an“ → schaltet", b.posts, (r, b.posts))
    r, p = await say("light_adjust", {"direction": "down", "strength": "gross"}, "Mach das Licht im Büro mal gemütlich an",
                     ("Arbeit Alex",))
    check("„gemütlich an“, alles aus → an mit 30 %", p == [("/api/devices/31/level", {"level": 30})] and "an (30 %)" in r, (r, p))
    r, p = await say("light_adjust", {"direction": "down", "strength": "gross"}, "Mach es gemütlich", ("Arbeit Alex",))
    check("„mach es gemütlich“, alles aus → nichts, „schon aus“", not p and "schon aus" in r, (r, p))
    r, p = await say("light_adjust", {"direction": "down", "strength": "gross"}, "Mach das Licht gemütlich an")
    check("„gemütlich an“, ein Licht brennt → wie bisher dimmen", p and p[0][0] == "/api/devices/11/level", (r, p))

    # „DEN Rollladen“ bei mehreren im Raum → nachfragen; Mehrzahl → alle; Antwort „alle“ (Angebot) → ohne zweite Frage
    BL = DEVS + [{"id": 51, "type": "blind", "name": "Gesamt Wohnz.", "room": "Wohnzimmer", "room_id": 1},
                 {"id": 52, "type": "blind", "name": "Wohnz. Mitte", "room": "Wohnzimmer", "room_id": 1},
                 {"id": 53, "type": "blind", "name": "Wohnz. TV", "room": "Wohnzimmer", "room_id": 1},
                 {"id": 54, "type": "blind", "name": "Küche Fenster", "room": "Küche", "room_id": 2}]

    class BlindBridge(FakeBridge):
        async def context(self):
            return BL, ROOMS

    async def blind(said, rooms_=("Wohnzimmer",), args=None):
        b = BlindBridge()
        r = await v.execute_tool({"name": "blind_action", "arguments": args or {"action": "close"}}, b, False,
                                 list(rooms_), v.load_cfg(), utterance=said, current=said)
        return r, b.posts
    r, p = await blind("Fahr den Rollladen runter")
    check("„den Rollladen“ bei mehreren → Rückfrage mit Angeboten", not p and "?" in r and getattr(r, "offers", None)
          and r.offers[0]["label"] == "alle", (r, p))
    r, p = await blind("Fahr die Rollläden runter")
    check("„die Rollläden“ → alle (Gruppe)", p and "/api/blinds/51/close" in [x[0] for x in p], (r, p))
    r, p = await blind(None)
    check("Angebot „alle“ (ohne Satz) → fährt ohne zweite Frage", p, (r, p))
    r, p = await blind("Mach das Rollo zu", ("Küche",))
    check("„das Rollo“, nur eines im Raum → fährt", p == [("/api/blinds/54/close", None)], (r, p))

    # Interpret gar nicht in der Sammlung → kein fremder Titel als Ersatz, gleich Qobuz anbieten
    class Coll(FakeBridge):
        def __init__(self, asked):
            super().__init__()
            self.asked, self.artists = asked, ["Adele", "Fettes Brot", "Amy MacDonald"]

        async def now_playing(self, names):
            return {n: "nichts" for n in names or []}

        async def get(self, path):
            if path.startswith("/api/media/resolve"):
                return {"kind": "song", "score": 70, "match": "Schocktherapie", "album": "Big Book of Fettes Brot",
                        "artist": "Fettes Brot", "asked_artist": self.asked, "album_id": 5}
            return await super().get(path)
    for asked, said, want_qobuz in (("avril lavigne", "Spiel Skaterboy von Avril Lavigne", True),
                                    ("adele", "Spiel Schocktherapie von Adele", False)):
        b = Coll(asked)
        r = await v.execute_tool({"name": "music_play", "arguments": {"search": said.split("Spiel ")[1].split(" von")[0],
                                                                      "artist": asked.title(), "kind": "song"}},
                                 b, False, ["Wohnzimmer"], v.load_cfg(), utterance=said, current=said)
        got_qobuz = r.startswith("Von ") and "Sammlung" in r and "meinst du" not in r
        check(f"„{said}“ → {'nur Qobuz anbieten' if want_qobuz else 'fremden Titel anbieten wie bisher'}",
              got_qobuz == want_qobuz, r)

    # ---- Code-Review 19.10. -------------------------------------------------------------------------------------------
    check("asks_state: „bitte“ macht keine Aufforderung", v.asks_state("Ist im Bad noch Licht an, bitte?"))
    check("asks_state: „…? Spiel mehr davon“ ist eine Aufforderung", not v.asks_state("Läuft Adele? Spiel mehr davon"))
    r, p = await say("music_play", {"search": "Adele", "kind": "artist"}, "Gibt es was von Adele?")
    check("Frage „Gibt es was von Adele?“ + music_play → nicht spielen, anbieten", not p and "Soll ich" in r
          and r.offers and r.offers[0]["call"]["name"] == "music_play", (r, p))
    r, p = await say("light_adjust", {"direction": "down", "strength": "klein"}, "Mach ein bisschen dunkler", ("Arbeit Alex",))
    check("„ein bisschen dunkler“, alles aus → bleibt aus", not p and "schon aus" in r, (r, p))
    r, p = await say("light_adjust", {"direction": "down", "strength": "gross"}, "Schalte das Licht gemütlich ein bitte",
                     ("Arbeit Alex",))
    check("„gemütlich ein bitte“, alles aus → an mit 30 %", p == [("/api/devices/31/level", {"level": 30})], (r, p))
    a_ = {"on": True, "room": "Küche"}
    b = FakeBridge()
    r = await v.execute_tool({"name": "light_set", "arguments": a_}, b, False, ["Arbeit Alex"], v.load_cfg(),
                             utterance="Mach das Licht an", current="Mach das Licht an")
    check("erfundener Raum (Küche nicht gesagt) → Sprecherraum UND aus dem Aufruf gestrichen (Angebote erben ihn nicht)",
          b.posts == [("/api/devices/31/level", {"level": 100})] and not a_.get("room"), (r, b.posts, a_))

    BL2 = BL + [{"id": 61, "type": "blind", "name": "Gruppe Küche", "room": "Küche", "room_id": 2}]

    class GrpBridge(FakeBridge):
        async def context(self):
            return [x for x in BL2 if x["id"] != 54], ROOMS
    b = GrpBridge()
    r = await v.execute_tool({"name": "blind_action", "arguments": {"action": "close"}}, b, False, ["Küche"], v.load_cfg(),
                             utterance="Fahr den Rollladen runter", current="Fahr den Rollladen runter")
    check("Raum nur mit Gruppen-Rollladen → kein Absturz", "Fehler" not in str(r), (r, b.posts))

    BATH = [{"id": 7, "name": "Bad mitte", "aliases": []}, {"id": 8, "name": "Bad oben", "aliases": []}, *ROOMS]
    BDEVS = DEVS + [{"id": 71, "type": "light", "name": "Decke", "room": "Bad mitte", "room_id": 7},
                    {"id": 81, "type": "light", "name": "Decke", "room": "Bad oben", "room_id": 8},
                    {"id": 82, "type": "light", "name": "Spiegel", "room": "Bad oben", "room_id": 8}]
    STATE.update({7: [{"id": 71, "on": False, "level": 0}], 8: [{"id": 81, "on": True, "level": 100},
                                                               {"id": 82, "on": False, "level": 0}]})

    class BathBridge(FakeBridge):
        async def context(self):
            return BDEVS, BATH

    async def bad(name, args, said):
        b = BathBridge()
        r = await v.execute_tool({"name": name, "arguments": args}, b, False, ["Wohnzimmer"], v.load_cfg(),
                                 utterance=said, current=said)
        return r, b.posts
    r, p = await bad("light_set", {"on": False, "device": "Decke", "room": "Bad"}, "Mach im Bad das Deckenlicht aus")
    check("„im Bad“ + Gerät in beiden Bädern → Rückfrage", not p and "Bad mitte" in r and "Bad oben" in r, (r, p))
    r, p = await bad("light_set", {"on": True, "device": "Spiegel", "room": "Bad"}, "Mach im Bad den Spiegel an")
    check("„im Bad“ + Gerät nur in einem Bad → dort schalten", p == [("/api/devices/82/level", {"level": 100})], (r, p))
    r, p = await bad("lights_status", {"room": "Bad"}, "Ist im Bad noch Licht an?")
    check("„Ist im Bad noch Licht an?“ → beide Bäder gelesen", "Bad oben" in r, (r, p))

    K = [{"titel": "Hello", "label": "„Hello“ von Adele"}, {"titel": "Skyfall", "label": "„Skyfall“ von Adele"}]
    check("lies_wahl: ohne „sicher“ → unsicher",
          v.lies_wahl({"tool_calls": [{"function": {"name": "treffer_waehlen", "arguments": {"nr": 1}}}]}, K)
          == {"nr": 1, "sicher": False, "suche": ""})
    check("lies_wahl: kaputtes JSON → None (bisherige Regel)",
          v.lies_wahl({"tool_calls": [{"function": {"name": "treffer_waehlen", "arguments": "{nr: 1"}}]}, K) is None)
    check("lies_wahl: Freitext nennt einen Eintrag → unsicher",
          v.lies_wahl({"content": "Meinst du Skyfall?"}, K)["nr"] == 1)
    check("zusatz_woerter: „Spiel mehr davon“ → keine", v.zusatz_woerter("Spiel mehr davon", ["Adele"], []) == [])
    check("zusatz_woerter: „die Folge mit Fußball“ → fußball",
          v.zusatz_woerter("Spiel die Folge mit Fußball", ["Die drei ???"], []) == ["fußball"])
    print("\nALLES OK" if ok else "\nES GIBT FEHLER")


asyncio.run(main())
