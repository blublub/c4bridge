"""Ganze Kette wie am Mikrofon (process_utterance), aber Whisper durch Text ersetzt und simulate=True (nichts schaltet).
Je Dialog: erster Satz → Rückfrage → Antwort; Verlauf + feste Angebote werden wie in handle_mic weitergereicht."""
from pathlib import Path
import asyncio
import sys
import time

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import voice as v  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
v.ZULETZT_FILE = _pl.Path(_tf.gettempdir()) / "c4_test_zuletzt.json"   # Tests schreiben nie in data/


class FakeWS:
    def __init__(self):
        self.msgs = []

    async def send_json(self, m):
        self.msgs.append(m)

    async def send_bytes(self, b):
        pass


class TextEngine:
    def __init__(self):
        self.next = ""

    def stt(self, pcm, vocab=None):
        return self.next


# Vergleichsliste (mit dem Nutzer abgestimmt 19.10.): Alltag gleichmäßig über alle Bereiche + bekannte Stolperfallen.
# Namen so geschrieben, wie man sie spricht (nicht wie im Album: „Skaterboy“). Erwartungen: EXPECT (gleiche Nummern).
AS, EK, WZ, SZ = "Arbeit Alex", "Esszimmer / Küche", "Wohnzimmer", "Schlafzimmer"
DIALOGS = [
    # A. Licht
    (AS, ["Mach mal Licht an"]), (AS, ["Licht aus"]), (EK, ["Mach das Licht im Gästezimmer aus"]),
    (WZ, ["Mach die Stehlampe an"]), (WZ, ["Licht auf 40 Prozent"]), (WZ, ["Stehlampe auf 30 Prozent"]),
    (WZ, ["Mach es mal heller"]), (WZ, ["Mach es mal richtig hell"]), (WZ, ["Dimm mal das Licht ein bisschen"]),
    (WZ, ["Mach es gemütlicher"]), (WZ, ["Mach das Licht mal gemütlich an"]), (AS, ["Mach im Bad das Licht aus"]),
    (AS, ["Licht im Gästezimmer aus und im Flur Mitte an"]),
    # B. Schalter
    (AS, ["Mach die Schreibtischlampe aus"]),
    # C. Rollläden
    (AS, ["Mach die Rollos zu"]), (AS, ["Mach mal die Jalousien runter"]), (AS, ["Fahr im Wohnzimmer die Rollläden runter"]),
    (WZ, ["Fahr den Rollladen runter"]), (AS, ["Rollladen hoch"]), (WZ, ["Es blendet"]), (AS, ["Mach alles zu"]),
    # D. Heizung/Temperatur
    (AS, ["Wie warm ist es hier?"]), (AS, ["Welche Temperatur ist im Bad?"]), (WZ, ["Mach es zwei Grad wärmer"]),
    (SZ, ["Stell die Heizung auf 19 Grad"]),
    # E. Zustand abfragen – nichts schalten
    (EK, ["Ist im Esszimmer noch Licht an?"]), (AS, ["Wo im Haus brennt noch Licht?"]),
    (AS, ["Welche Lichter sind im Wohnzimmer an?"]), (AS, ["Sind die Rollos zu?"]),
    # F. Musik aus der Sammlung
    (AS, ["Spiel was von Amy Macdonald"]), (AS, ["Spiel das Album 21 von Adele"]),
    (AS, ["Spiel Life in a Beautiful Light aus unserer Musik"]), (EK, ["Spiel irgendwas Ruhiges"]),
    (AS, ["Spiel das Hörspiel Benjamin Blümchen Folge 12"]), (AS, ["Spiel Musik"]),
    # G. Namen, Qobuz
    (AS, ["Spiel mal Adele Set Fire to the Rain"]), (AS, ["Spiel Anastacia Sick and Tired"]),
    (AS, ["Spiel mal was von Ada"]), (AS, ["Spiel Believer von Imagine Dragons über Qobuz"]),
    (AS, ["Spiel mal Avril Lavigne Skaterboy", "Andere Version bitte"]),
    (EK, ["Spiel mal die drei Fragezeichen Kids, die Folge mit Fußball", "Ja"]),
    # H. Radio
    (AS, ["Spiel SWR3", "Wechsel auf hr3"]), (EK, ["Mach Radio an"]), (AS, ["Spiel Radio Bob"]),
    # I. Lautstärke, Wiedergabe
    (AS, ["Lauter"]), (AS, ["Ein bisschen leiser"]), (AS, ["Lautstärke auf 20"]), (AS, ["Pause"]),
    (AS, ["Nächstes Lied"]), (AS, ["Was läuft gerade?"]),
    # J. Raum aus
    (AS, ["Mach den Raum aus"]), (AS, ["Mach im Wohnzimmer alles aus"]),
    # K. Filme, Serien
    (WZ, ["Starte den Film Zurück in die Zukunft 3"]), (WZ, ["Breaking Bad Staffel 2 Folge 3"]),
    (AS, ["Starte im Wohnzimmer den Film Das Kanu des Manitu"]), (AS, ["Starte den Film Inception"]),
    (WZ, ["Mach Netflix an"]),
    # L. Dialog, Bezug
    (AS, ["Mach das Licht Schreibtisch aus", "Mach es wieder an"]), (AS, ["Wie warm ist es hier?", "Und im Wohnzimmer?"]),
    (AS, ["Mach die Rollos zu", "Und im Wohnzimmer?"]), (AS, ["Mach das Licht aus", "Und die Rollos auch"]),
    (AS, ["Mach das Licht aus", "Danke"]), (AS, ["Spiel was von Adele", "Mach das Licht aus", "Lauter"]),
    # M. Auskunft
    (AS, ["Wie wird das Wetter morgen?"]), (AS, ["Nenne mir die Alben von Avril Lavigne"]),
    # N. Farblicht
    (EK, ["Mach die Küchenzeile warmweiß"]), (EK, ["Mach das Licht in der Küche blau"]),
]

DIALOGS_ALT = [
    ("Arbeit Alex", ["Spiel was von Adda", "Ja"]),
    ("Arbeit Alex", ["Spiel was von Adda", "Ja, genau"]),
    ("Arbeit Alex", ["Spiel was von Adda", "Nein, Adele"]),
    ("Arbeit Alex", ["Spiel was von Adda", "Nein"]),
    ("Arbeit Alex", ["Spiel Herbert Grönemeyer", "Ja"]),
    ("Esszimmer / Küche", ["Schalte die Heizungspumpe oben ein", "Ja"]),
    ("Esszimmer / Küche", ["Mach die Stehlampe an", "Ja bitte"]),
    ("Arbeit Alex", ["Spiel SWR3", "Wechsel auf hr3"]),
    ("Esszimmer / Küche", ["Schalte die Pumpe ein", "Ja"]),
    ("Esszimmer / Küche", ["Mach das Licht im Gästezimmer aus"]),
    ("Esszimmer / Küche", ["Spiel Amy Macdonald in der Küche"]),
    ("Esszimmer / Küche", ["Mach das Licht an"]),
    ("Arbeit Alex", ["Fahr im Wohnzimmer die Rollläden runter"]),
    ("Arbeit Alex", ["Spiel im Esszimmer mal über Kubos, die drei Fragezeichen Kids, letzte Folge 104.", "Ja"]),
    ("Arbeit Alex", ["Spiel im Esszimmer die drei Fragezeichen", "Nein, das meine ich nicht.", "Such auf Qobuz nach drei Fragezeichen Kids Folge 104"]),
    ("Arbeit Alex", ["Such mal im Esszimmer bei Cobus die drei Fragezeichen.", "Ja und zwar die Folge 104."]),
    ("Arbeit Alex", ["Spiel mal von den Imagine Dragons Rise", "Qobuz"]),
    ("Arbeit Alex", ["Spiel den Titel Rise von den Imagine Dragons über Qobuz", "Und jetzt den Titel Believer"]),
    ("Arbeit Alex", ["Spiel Set für to the rain von Adda", "Ja"]),
    ("Arbeit Alex", ["Spiel Fields of Gold von Sting"]),
    # Dialogzustand (dialog.py): Themenwechsel vs. Rückbezug
    ("Arbeit Alex", ["Machen wir die Schreibbestände aus.", "Die Schreibtischlampe.", "machen wir die Rolle so."]),
    ("Arbeit Alex", ["Mach die Schreibtischlampe aus", "Mach die Rollos zu"]),
    ("Arbeit Alex", ["Mach das Licht Schreibtisch aus", "Mach es wieder an"]),
    ("Arbeit Alex", ["Mach das Licht Schreibtisch aus", "Und die Rollos auch"]),
    ("Arbeit Alex", ["Wie warm ist es hier?", "Und im Wohnzimmer?"]),
    # Review 18.10. (Fable): allgemeine Fälle, nicht nur die beobachteten
    ("Arbeit Alex", ["Schalte die Heizungspumpe ein"]),              # darf NICHT „alle Lichter an“ werden
    ("Arbeit Alex", ["Mach alles zu"]),                               # Rollläden des Raums
    ("Arbeit Alex", ["Mach das Licht aus", "Okay"]),                  # „Okay“ → „Gern.“, keine Rückfrage
    ("Arbeit Alex", ["Mach die Rollos zu", "Und im Wohnzimmer?"]),    # Wiederholung nur bei Erfolg
    ("Arbeit Alex", ["Fahr im Wohnzimmer die Rollläden runter", "Und wieder hoch"]),   # Raum von eben bleibt
    ("Arbeit Alex", ["Spiel was von Adele", "Mach das Licht aus", "Lauter"]),           # Themenwechsel hin und zurück
    ("Arbeit Alex", ["Welche Temperatur ist im Bad?"]),               # kein Werkzeug-Ankündigen ohne Aufruf
    # Filme/Serien (KODI) – Titel aus der echten Mediathek; Trockenlauf
    ("Wohnzimmer", ["Starte den Film Zurück in die Zukunft 3"]),
    ("Wohnzimmer", ["Spiel Breaking Bad"]),
    ("Wohnzimmer", ["Breaking Bad Staffel 2 Folge 3"]),
    ("Wohnzimmer", ["Starte den Film Project Hail Mary"]),
    ("Wohnzimmer", ["Spiel die drei Fragezeichen Toteninsel"]),           # Film, nicht Hörspiel (Musik)
    ("Arbeit Alex", ["Starte im Wohnzimmer den Film Das Kanu des Manitu"]),
    ("Arbeit Alex", ["Starte den Film Inception"]),                   # kein KODI im Raum → Hinweis
    ("Wohnzimmer", ["Mach Netflix an"]),
    # 18.10. nachts: Fragen schalten nie, Lichtzustand, gemütlich an, Alben, andere Version, genannte Quelle
    ("Esszimmer / Küche", ["Ist im Esszimmer noch Licht an?"]),
    ("Arbeit Alex", ["Wo im Haus ist noch Licht an?"]),
    ("Arbeit Alex", ["Welche Lichter sind im Wohnzimmer an?"]),
    ("Arbeit Alex", ["Sind im Gästezimmer noch Lichter an?"]),
    ("Wohnzimmer", ["Mach das Licht im Wohnzimmer mal gemütlich an"]),
    ("Arbeit Alex", ["Nenne mir mal die Alben von Amy Macdonald"]),
    ("Arbeit Alex", ["Welche Alben gibt es von Avril Lavigne?"]),
    ("Arbeit Alex", ["Spiel eine andere Version von dem Lied"]),
    ("Arbeit Alex", ["Spiel Life in a Beautiful Light von Amy Macdonald aus unserer Musik"]),
]


# ---- Erwartungen je Satz (für den Modellvergleich): E(tools, text, verboten, kein_text) ----------------------------
# tools = mindestens eines dieser Werkzeuge muss aufgerufen sein (None = egal, set() = KEIN Werkzeug)
# text = regulärer Ausdruck, der in der Antwort vorkommen muss; verboten = Werkzeuge, die NICHT vorkommen dürfen;
# kein_text = Ausdruck, der NICHT in der Antwort stehen darf. ANY = nicht bewertet (hängt vom Satz davor ab).
import re as _re  # noqa: E402


def E(tools=None, text=None, verboten=(), kein_text=None):
    return {"tools": tools, "text": text, "verboten": set(verboten), "kein_text": kein_text}


ANY = None
MUSIK = {"music_play", "stream_play"}
FALSCH_SCHALTEN = ("light_set", "room_off", "light_adjust")
LICHT = {"light_set", "light_adjust"}
EXPECT = {
    # A. Licht
    0: [E({"light_set"}, r" an")], 1: [E({"light_set"}, r" aus")], 2: [E({"light_set"}, r"Gästezimmer.* aus")],
    3: [E({"light_set"}, r"Stehlampe")], 4: [E({"light_set"}, r"40")], 5: [E({"light_set"}, r"Stehlampe.*30|30.*Stehlampe")],
    6: [E(LICHT, r"heller| an|100")], 7: [E(LICHT, r"100| an|heller")], 8: [E(LICHT, r"gedimmt|%|schon aus")],
    9: [E(LICHT, r"gedimmt|%|schon aus")], 10: [E(LICHT, r" an")], 11: [E(None, r"Bad mitte oder Bad oben", ("room_off",))],
    12: [E({"light_set"}, r"Gästezimmer.*aus.*Flur.*an|Flur.*an.*Gästezimmer.*aus| aus.*·.* an| an.*·.* aus")],
    # B. Schalter
    13: [E({"switch_set"}, r"Schreibtischlampe.* aus")],
    # C. Rollläden
    14: [E({"blind_action"}, r"zu")], 15: [E({"blind_action"}, r"zu")], 16: [E({"blind_action"}, r"Wohnz.*zu|zu.*Wohnz")],
    17: [E(None, r"\?")], 18: [E({"blind_action"}, r"auf")], 19: [E({"blind_action"}, r"zu", ("light_set", "light_adjust"))],
    20: [E({"blind_action"}, None, ("room_off",))],
    # D. Heizung
    21: [E({"get_temperature"}, r"°C")], 22: [E(None, r"Bad mitte oder Bad oben")],
    23: [E({"climate_setpoint_delta", "climate_setpoint"}, None)], 24: [E({"climate_setpoint"}, r"19|Thermostat|kein")],
    # E. Zustand
    25: [E({"lights_status"}, r"^(Ja|Nein)")], 26: [E({"lights_status"}, r"^In |alles aus")],
    27: [E({"lights_status"}, None)], 28: [E(None, None, (), r"fährt|fahren")],   # Bridge fängt Schalten ab = richtig
    # F. Sammlung
    29: [E(MUSIK, r"Amy")], 30: [E(MUSIK, r"21")], 31: [E(MUSIK, r"Life in a Beautiful Light", (), r"^Qobuz")],
    32: [E(MUSIK, None)], 33: [E(MUSIK, r"(?i)blümchen|\?", ("video_play",))], 34: [E(MUSIK, None)],
    # G. Namen, Qobuz
    35: [E(MUSIK, r"Set Fire")], 36: [E(MUSIK, r"(?i)sick and tired")], 37: [E(None, r"Adele")],
    38: [E({"stream_play"}, r"Believer")], 39: [E(MUSIK, r"Sk8er Boi|Skater"), E(MUSIK, r"Sk8er|Skater|\?")],   # läuft nichts: nachfragen ist richtig
    40: [E(None, r"(?i)fu(ß|ss)ball|\?"), E(None, r"(?i)fu(ß|ss)ball|nichts|\?")],
    # H. Radio
    41: [E({"stream_play"}, r"(?i)swr ?3"), E({"stream_play"}, r"(?i)hr ?3")], 42: [E(None, r"(?i)radio| \?|\?$")],   # Sender nachfragen ist auch richtig
    43: [E({"stream_play"}, r"(?i)bob")],
    # I. Lautstärke, Wiedergabe (Kurzbefehle laufen meist ohne Modell)
    44: [E({"volume_adjust"}, None)], 45: [E({"volume_adjust"}, None)], 46: [E({"volume_set"}, r"20")],
    47: [E({"media_control"}, None)], 48: [E({"media_control"}, None)], 49: [E({"rooms_playing"}, None)],
    # J. Raum aus
    50: [E({"room_off"}, None, ("light_set",))], 51: [E({"room_off"}, r"Wohnzimmer", ("light_set",))],
    # K. Filme
    52: [E({"video_play"}, r"Zukunft")], 53: [E({"video_play"}, r"S02E03")], 54: [E({"video_play"}, r"Manitu")],
    55: [E(None, None, (), r"Film „Inception“ in")], 56: [E(set(), r"Netflix")],
    # L. Dialog
    57: [E({"light_set"}, r"Schreibtisch.* aus"), E({"light_set"}, r"Schreibtisch.* an")],
    58: [E({"get_temperature"}, r"°C"), E({"get_temperature"}, r"Wohnzimmer")],
    59: [E({"blind_action"}, r"zu"), E({"blind_action"}, r"Wohnz")],
    60: [E({"light_set"}, r" aus"), E({"blind_action"}, r"zu", ("light_set",))],
    61: [E({"light_set"}, r" aus"), E(set(), r"Gern")],
    62: [E(MUSIK, r"Adele"), E({"light_set"}, r" aus"), E({"volume_adjust"}, None)],
    # M. Auskunft
    63: [E({"weather"}, None)], 64: [E({"music_albums"}, r"Alben|Qobuz")],
    # N. Farblicht
    65: [E({"light_color"}, r"warmwei")], 66: [E({"light_color"}, r"blau")],
}


def bewerte(e, tools, text):
    """Fehlergrund oder '' (= richtig)."""
    if e is None:
        return ""
    if e["tools"] is not None:
        if e["tools"] == set() and tools:
            return "Werkzeug, obwohl keins erwartet"
        if e["tools"] and not (set(tools) & e["tools"]):
            return "falsches/kein Werkzeug"
    if set(tools) & e["verboten"]:
        return "verbotenes Werkzeug " + ",".join(set(tools) & e["verboten"])
    if e["text"] and not _re.search(e["text"], text):
        return "Text passt nicht"
    if e["kein_text"] and _re.search(e["kein_text"], text):
        return "falscher Text"
    return ""


async def main():
    import json as _json
    import os as _os
    modell = _os.environ.get("MODELL")            # anderes Modell testen, ohne voice.json (Live-Betrieb) anzufassen
    if modell:
        _orig = v.load_cfg

        def _cfg():
            c = _orig()
            c["llm"]["model"] = modell
            return c
        v.load_cfg = _cfg
    llm_url = _os.environ.get("LLM_URL")           # anderer Ollama-Server (z. B. Testinstanz auf 11435)
    if llm_url:
        _orig3 = v.load_cfg

        def _cfg3():
            c = _orig3()
            c["llm"]["url"] = llm_url
            if _os.environ.get("LLM_CTX"):         # andere Kontextgröße (nur mit Testinstanz sinnvoll)
                c["llm"]["num_ctx"] = int(_os.environ["LLM_CTX"])
            return c
        v.load_cfg = _cfg3
    pdir = _os.environ.get("PROMPT_DIR")          # anderen Prompt testen (house.txt + examples.txt), ohne voice.json
    if pdir:
        _orig2 = v.load_cfg

        def _cfg2():
            c = _orig2()
            c.setdefault("prompts", {})
            c["prompts"]["house"] = open(_os.path.join(pdir, "house.txt"), encoding="utf-8").read()
            c["prompts"]["examples"] = open(_os.path.join(pdir, "examples.txt"), encoding="utf-8").read()
            return c
        v.load_cfg = _cfg2
    name = v.load_cfg()["llm"]["model"] + (f" · Prompt {pdir}" if pdir else "")
    b = v.Bridge("http://127.0.0.1:8080")
    b.session = aiohttp.ClientSession(base_url="http://127.0.0.1:8080")
    eng = TextEngine()
    global DIALOGS, EXPECT
    if "--alt" in sys.argv:                                      # alte Fehlersammlung, ohne Bewertung
        sys.argv.remove("--alt")
        DIALOGS, EXPECT = DIALOGS_ALT, {}
    last = int(sys.argv[1]) if len(sys.argv) > 1 else 0         # nur die letzten N Dialoge
    start = len(DIALOGS) - last if last else 0
    ok_n = n = 0
    zeiten, ergebnis = [], []
    for i, (room, turns) in enumerate(DIALOGS[start:], start):
        dialog = v.Dialog()
        line = []
        for k, t in enumerate(turns):
            ws = FakeWS()
            eng.next = t
            t0 = time.time()
            await v.process_utterance(ws, b"\0" * 32000, eng, b, v.load_cfg(), "test", [room], simulate=True,
                                      dialog=dialog)
            dt = time.time() - t0
            dec = next((m for m in ws.msgs if m.get("type") == "decision"), {})
            names = [c.get("function", {}).get("name", "") for c in dec.get("tools") or []]
            text = str(dec.get("text", ""))
            e = (EXPECT.get(i) or [ANY] * len(turns))[k] if k < len(EXPECT.get(i) or []) else ANY
            fehler = bewerte(e, names, text)
            if e is not None:
                n += 1
                ok_n += not fehler
                zeiten.append(dt)
            mark = "  " if e is None else ("✓ " if not fehler else "✗ ")
            line.append(f"{mark}„{t}“ → [{','.join(names) or '–'}] {text[:80]} ({dt:.1f}s)" + (f"  ← {fehler}" if fehler else ""))
            ergebnis.append({"dialog": i, "satz": t, "tools": names, "text": text, "s": round(dt, 2), "fehler": fehler,
                             "bewertet": e is not None})
        print(f"{i:2} [{room}]\n   " + "\n   ".join(line))
    await b.session.close()
    zeiten.sort()
    med = zeiten[len(zeiten) // 2] if zeiten else 0
    print(f"\n=== {name}: {ok_n}/{n} richtig · Median {med:.1f} s je Satz · langsamster {max(zeiten or [0]):.1f} s")
    out = _pl.Path(__file__).resolve().parent / "dialog_results"
    out.mkdir(exist_ok=True)
    (out / (name.replace(":", "_").replace("/", "_") + ".json")).write_text(
        _json.dumps({"modell": name, "richtig": ok_n, "bewertet": n, "median_s": round(med, 2), "saetze": ergebnis},
                    ensure_ascii=False, indent=1), encoding="utf-8")

asyncio.run(main())
