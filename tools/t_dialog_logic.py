"""Offline-Test des Dialogzustands (c4bridge/dialog.py) und seiner Wirkung in execute_tool – kein Haus, kein Ollama.
Spielt die Regeln ALLGEMEIN durch (Themenwechsel, Rückverweis, Ablauf, Raum-Wiederholung), nicht nur die am Handy
beobachteten Fälle."""
from pathlib import Path
import asyncio
import sys
sys.stdout.reconfigure(encoding="utf-8")   # Windows-Konsole (cp1252) kann „→“ nicht
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import voice as v  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
v.ZULETZT_FILE = _pl.Path(_tf.gettempdir()) / "c4_test_zuletzt.json"   # Tests schreiben nie in data/
from c4bridge.dialog import Dialog, domains_of, refers_back  # noqa: E402

ROOMS = [{"id": 1, "name": "Wohnzimmer", "aliases": ["Stube"]}, {"id": 2, "name": "Küche", "aliases": []},
         {"id": 3, "name": "Arbeit Alex", "aliases": ["Büro"]}, {"id": 4, "name": "Esszimmer / Küche", "aliases": []},
         {"id": 5, "name": "Spielzimmer", "aliases": []}, {"id": 6, "name": "Bad mitte", "aliases": []},
         {"id": 7, "name": "Bad oben", "aliases": []}]
DEVS = [
    {"id": 11, "type": "light", "name": "Stehlampe", "room": "Wohnzimmer", "room_id": 1},
    {"id": 12, "type": "light", "name": "Decke", "room": "Wohnzimmer", "room_id": 1},
    {"id": 13, "type": "blind", "name": "Gesamt Wohnz.", "room": "Wohnzimmer", "room_id": 1},
    {"id": 31, "type": "light", "name": "Schreibtisch", "room": "Arbeit Alex", "room_id": 3},
    {"id": 32, "type": "light", "name": "Arbeit Alex", "room": "Arbeit Alex", "room_id": 3},
    {"id": 33, "type": "switch", "name": "Schreibtischlampe", "room": "Arbeit Alex", "room_id": 3},
    {"id": 34, "type": "blind", "name": "Alex Garten", "room": "Arbeit Alex", "room_id": 3},
    {"id": 35, "type": "blind", "name": "Alex Westseite", "room": "Arbeit Alex", "room_id": 3},
    {"id": 36, "type": "blind", "name": "Alex Arbeit (Gruppe)", "room": "Arbeit Alex", "room_id": 3},
    {"id": 41, "type": "light", "name": "Esszimmer", "room": "Esszimmer / Küche", "room_id": 4},
    {"id": 42, "type": "blind", "name": "Garten (gesamt)", "room": "Esszimmer / Küche", "room_id": 4},
]
STATE = {1: [{"id": 11, "on": True, "level": 40}, {"id": 12, "on": False, "level": 0}],
         3: [{"id": 31, "on": False, "level": 0}, {"id": 32, "on": True, "level": 70}],
         4: [{"id": 41, "on": True, "level": 50}]}


class FakeBridge(v.Bridge):
    def __init__(self):
        super().__init__("http://x")
        self.posts = []

    async def context(self):
        return DEVS, ROOMS

    async def get(self, path):
        if path.startswith("/api/devices/state?room_id="):
            return STATE.get(int(path.split("=")[1]), [])
        raise RuntimeError(path)

    async def post(self, path, body=None):
        self.posts.append((path, body))
        return {"ok": True}


ok = True


def check(label, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FEHLER ") + label + (f"  → {info}" if info else ""))


async def tool(name, args, said, current, rooms=("Arbeit Alex",)):
    b = FakeBridge()
    r = await v.execute_tool({"name": name, "arguments": dict(args)}, b, False, list(rooms), v.load_cfg(),
                             utterance=said, current=current)
    return str(r), b.posts


def done(d, text, answer, tools, calls=None):
    d.end(text, answer, tools, False, [], calls=calls or [{"name": t, "arguments": {}} for t in tools])


def asked(d, text, answer, offers=None):
    d.end(text, answer, [], True, offers or [])


async def main():
    # ---------- Gerätetyp-Erkennung ------------------------------------------------------------------------------
    check("Komposita: Schreibtischlampe = Licht", domains_of("Die Schreibtischlampe.") == {"licht"})
    check("„Rolle“ (Whisper für Rollo) = Rollladen", domains_of("machen wir die Rolle so") == {"rollladen"})
    check("„Kontrolle“ ist kein Rollladen", "rollladen" not in domains_of("Kontrolle bitte"))
    check("„Ja“/„Kim“/„Und im Wohnzimmer?“ ohne Typ", not (domains_of("Ja") | domains_of("Kim") | domains_of("Und im Wohnzimmer?")))
    check("„Pflicht“ ist kein Licht", "licht" not in domains_of("Pflicht"), domains_of("Pflicht"))
    check("Raumname „Spielzimmer“ zählt nicht als Medien", "medien" not in domains_of("Und im Spielzimmer?"),
          domains_of("Und im Spielzimmer?"))
    check("„es“ gilt als Rückverweis", refers_back("Mach es wieder an"))

    # ---------- Themenwechsel nach abgeschlossener Aktion ------------------------------------------------------------
    d = Dialog()
    done(d, "Mach die Schreibtischlampe aus", "„Schreibtischlampe“ aus", ["switch_set"])
    check("Rollos nach Lampe: keine Notiz", d.note("Mach die Rollos zu") == "")
    check("Rollos nach Lampe: said = nur der Satz", d.said("Mach die Rollos zu") == "Mach die Rollos zu")
    check("Rückverweis „wieder an“: Notiz da", "zuletzt" in d.note("Mach sie wieder an"))
    check("Rückverweis: said enthält das Gerät", "Schreibtischlampe" in d.said("Mach sie wieder an"))
    check("„Und die Rollos auch“: Notiz ja, Gerät nein", "zuletzt" in d.note("Und die Rollos auch")
          and "Schreibtischlampe" not in d.said("Und die Rollos auch"))
    check("gleicher Typ ohne Rückverweis: Notiz da", "zuletzt" in d.note("Mach das Licht heller"))
    check("Verlauf leer (keine offene Frage)", d.history() == [] and d.pending() == [])

    # ---------- Ablauf der Zeiten --------------------------------------------------------------------------------
    d = Dialog()
    done(d, "Licht aus", "Licht aus", ["light_set"])
    t_far = d.last_until + 1
    check("letzte Aktion nach 45 s weg", d.note("Mach es wieder an", now=t_far) == "")
    d = Dialog()
    asked(d, "Spiel was von Adda", "Meinst du Eddie Vedder?", [{"label": "Eddie Vedder", "call": {"name": "music_play", "arguments": {}}}])
    check("offene Frage: Verlauf + Angebote", len(d.history()) == 2 and len(d.pending()) == 1)
    check("offene Frage nach 90 s zu", d.history(now=d.question_until + 1) == [] and d.pending(now=d.question_until + 1) == [])

    # ---------- Offene Rückfrage + Themenwechsel (begin) ---------------------------------------------------------------
    d = Dialog()
    asked(d, "Mach die Stehlampe an", "In Arbeit Alex gibt es kein Licht „Stehlampe“ – meinst du „Stehlampe“ in Wohnzimmer?",
          [{"label": "Stehlampe", "call": {"name": "light_set", "arguments": {"device": "Stehlampe", "room": "Wohnzimmer"}}}])
    check("„Ja“ bleibt bei der Frage", not d.begin("Ja") and d.pending())
    check("„Nein, die Decke“ (gleicher Typ) bleibt", not d.begin("Nein, die Decke") and d.pending())
    check("„Mach die Rollos zu“ schließt die Frage", d.begin("Mach die Rollos zu") and not d.pending() and not d.history())
    d = Dialog()
    asked(d, "Machen wir die Schreibbestände aus.", "Schreibbestände sind nicht im System – was möchtest du ausschalten?")
    check("Frage ohne Typ: Antwort „Die Rollos“ bleibt Antwort", not d.begin("Die Rollos") and d.history())
    d = Dialog()
    done(d, "Mach das Licht aus", "Licht aus", ["light_set"])
    asked(d, "Spiel was von Adda", "Meinst du Eddie Vedder?")
    check("bei offener Frage keine „zuletzt“-Notiz", d.note("Ja") == "")
    d.begin("Mach die Rollos zu")
    check("nach Themenwechsel: alte Licht-Aktion nicht für Rollos", d.note("Mach die Rollos zu") == "")

    # ---------- Raum-Wiederholung ----------------------------------------------------------------------------------
    d = Dialog()
    done(d, "Wie warm ist es hier?", "Im Raum Arbeit Alex sind es 26 °C.", ["get_temperature"])
    r = d.repeat_for_room("Und im Wohnzimmer?", ROOMS)
    check("„Und im Wohnzimmer?“ → get_temperature Wohnzimmer", r == [{"name": "get_temperature", "arguments": {"room": "Wohnzimmer"}}], r)
    check("Alias „Und in der Stube?“", (d.repeat_for_room("Und in der Stube?", ROOMS) or [{}])[0].get("arguments", {}).get("room") == "Wohnzimmer")
    check("„Esszimmer“ = Namensteil", (d.repeat_for_room("Und im Esszimmer?", ROOMS) or [{}])[0].get("arguments", {}).get("room") == "Esszimmer / Küche")
    check("„Küche“ = ganzer Name gewinnt vor Namensteil", (d.repeat_for_room("Und in der Küche?", ROOMS) or [{}])[0].get("arguments", {}).get("room") == "Küche")
    check("mit eigenem Inhalt → kein Wiederholen", d.repeat_for_room("Und im Wohnzimmer das Licht?", ROOMS) is None)
    check("Raum mit Typwort („Spielzimmer“) wiederholbar", (d.repeat_for_room("Und im Spielzimmer?", ROOMS) or [{}])[0].get("arguments", {}).get("room") == "Spielzimmer",
          d.repeat_for_room("Und im Spielzimmer?", ROOMS))
    d = Dialog()
    done(d, "Mach das Licht Schreibtisch aus", "Licht „Schreibtisch“ aus", ["light_set"],
         calls=[{"name": "light_set", "arguments": {"device": "Schreibtisch", "on": False}}])
    r = d.repeat_for_room("Und im Wohnzimmer", ROOMS)
    check("Gerätename wird nicht mitgenommen", r == [{"name": "light_set", "arguments": {"on": False, "room": "Wohnzimmer"}}], r)
    d = Dialog()
    done(d, "Mach die Schreibtischlampe aus", "„Schreibtischlampe“ aus", ["switch_set"],
         calls=[{"name": "switch_set", "arguments": {"device": "Schreibtischlampe", "on": False}}])
    check("Schalter nicht raumweit wiederholbar", d.repeat_for_room("Und im Wohnzimmer?", ROOMS) is None)
    d = Dialog()
    d.end("Mach die Rollos zu", "In Arbeit Alex gibt es keinen Rollladen „Schreibtisch“.", ["blind_action"], False, [],
          calls=[{"name": "blind_action", "arguments": {"device": "Schreibtisch", "action": "close"}}], ok=False)
    check("FEHLGESCHLAGENE Aktion nicht in anderem Raum wiederholen", d.repeat_for_room("Und im Wohnzimmer?", ROOMS) is None,
          d.repeat_for_room("Und im Wohnzimmer?", ROOMS))

    # ---------- Wirkung in execute_tool ----------------------------------------------------------------------------
    d = Dialog()
    done(d, "Mach die Schreibtischlampe aus", "„Schreibtischlampe“ aus", ["switch_set"])
    s = d.said("Mach die Rollos zu")
    r, posts = await tool("blind_action", {"action": "close", "device": "Schreibtisch"}, s, "Mach die Rollos zu")
    check("Rollos zu + altes Gerät → Rollläden des Raums", "Gruppe" in r and posts, r)
    d = Dialog()
    done(d, "Mach das Licht Schreibtisch aus", "Licht „Schreibtisch“ aus", ["light_set"])
    s = d.said("Mach es wieder an")
    r, posts = await tool("light_set", {"on": True, "device": "Schreibtisch"}, s, "Mach es wieder an")
    check("„wieder an“ → Gerät von eben", "Schreibtisch" in r and posts, r)
    # Gefahr: Modell setzt ein FREMDES Gerät ein, der Satz nennt nur ein Wort aus dem Licht-Bereich („Heizungspumpe“)
    r, posts = await tool("light_set", {"on": True, "device": "Garten (gesamt)"}, "Schalte die Heizungspumpe ein",
                          "Schalte die Heizungspumpe ein")
    check("„Heizungspumpe ein“ + fremdes Gerät → NICHT alle Lichter an", not posts, r)
    r, posts = await tool("light_set", {"on": True, "device": "Garten (gesamt)"}, "Mach das Licht an", "Mach das Licht an")
    check("„Licht an“ + fremdes Gerät → alle Lichter des Raums", len(posts) == 2, r)
    r, posts = await tool("blind_action", {"action": "close", "device": "Decke"}, "Mach alles zu", "Mach alles zu")
    check("„alles zu“ + fremdes Gerät → Rollläden des Raums", posts and "Gruppe" in r, r)
    # Gerät aus dem Satz statt aus dem Modell
    r, posts = await tool("light_set", {"on": False, "device": "Schreibtischlampe"}, "Mach das Licht Schreibtisch aus",
                          "Mach das Licht Schreibtisch aus")
    check("Modell nimmt Schalter-Namen, Satz nennt Licht „Schreibtisch“ → genau dieses Licht",
          "Schreibtisch" in r and len(posts) == 1 and "/31/" in posts[0][0], (r, posts))
    # mehrdeutiges Raumwort
    r, posts = await tool("light_set", {"on": False}, "Mach das Licht in der Küche aus", "Mach das Licht in der Küche aus")
    check("„Küche“ (3 Räume) vom fremden Raum → Rückfrage", "Welches" in r and not posts, r)
    r, posts = await tool("get_temperature", {"room": "Bad"}, "Welche Temperatur ist im Bad?", "Welche Temperatur ist im Bad?",
                          rooms=("Arbeit Alex",))
    check("Modell gibt room=„Bad“ (zwei Bäder) → Rückfrage", "Welches" in r and not posts, r)
    r, posts = await tool("get_temperature", {"room": "Bad oben"}, "Wie warm ist es im Bad oben?", "Wie warm ist es im Bad oben?",
                          rooms=("Arbeit Alex",))
    check("room=„Bad oben“ eindeutig → keine Rückfrage", "Welches" not in r, r)
    r, posts = await tool("light_set", {"on": False}, "Mach das Licht in der Küche aus", "Mach das Licht in der Küche aus",
                          rooms=("Esszimmer / Küche",))
    check("„Küche“ vom Mikrofon Esszimmer / Küche → eigener Raum", not isinstance(r, v.Problem) and posts, r)
    r, posts = await tool("light_set", {"on": False, "room": "Küche"}, "Mach das Licht in der Küche aus", "Mach das Licht in der Küche aus")
    check("Modell hat den Raum gewählt → keine Rückfrage", not isinstance(r, v.Problem) or "Welches" not in r, r)

    print("\nALLES OK" if ok else "\nFEHLER vorhanden")


asyncio.run(main())
