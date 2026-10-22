"""Englischer Dialogtest: ganze Kette wie tools/t_dialog.py (simulate=True, nichts schaltet), aber mit "lang": "en"
(eingebaute englische Anweisungen aus voice_en.py, sofern unter prompts_en nichts Eigenes steht).
Prüft je Satz das Werkzeug und ob in der Antwort noch deutsche Wörter stehen (Raum-/Gerätenamen und „…“ ausgenommen).
Rooms/devices below are from the development house – adapt them to your project."""
import asyncio
import re
import sys
import time

import aiohttp
import pathlib as _P

sys.path.insert(0, str(_P.Path(__file__).resolve().parent.parent))
from c4bridge import voice as v  # noqa: E402
import tempfile as _tf, pathlib as _pl  # noqa: E402
v.ZULETZT_FILE = _pl.Path(_tf.gettempdir()) / "c4_test_zuletzt.json"   # Tests schreiben nie in data/

_orig = v.load_cfg


def _cfg_en():
    """Live-Konfiguration, nur im Test auf Englisch (voice.json bleibt unverändert)."""
    c = _orig()
    c["lang"] = "en"
    v.LANG = "en"
    return c


v.load_cfg = _cfg_en


class FakeWS:
    def __init__(self):
        self.msgs = []

    async def send_json(self, m):
        self.msgs.append(m)

    async def send_bytes(self, b):
        pass


class TextEngine:
    next = ""

    def stt(self, pcm, vocab=None):
        return self.next


WZ, AS, EK = "Wohnzimmer", "Arbeit Alex", "Esszimmer / Küche"
ANY = None
# (Sprecherraum, [(Satz, erwartete Werkzeuge oder None = nicht bewertet, set() = kein Werkzeug)])
DIALOGS = [
    (EK, [("Make the kitchen counter warm white", {"light_color"})]), (EK, [("Make the light in the Küche blue", {"light_color"})]),
    (WZ, [("Turn the lights on", {"light_set"})]), (WZ, [("Make it a bit brighter", {"light_adjust"})]),
    (WZ, [("Lights off", {"light_set"})]), (WZ, [("Make it cosy", {"light_adjust"})]),
    (WZ, [("Close the blinds", {"blind_action"})]), (WZ, [("Blinds up", {"blind_action"})]),
    (WZ, [("Play some music", {"music_play"})]), (WZ, [("Louder please", {"volume_adjust"})]),
    (WZ, [("Volume to 20", {"volume_set"})]), (WZ, [("Pause", {"media_control"})]),
    (WZ, [("Next song", {"media_control"})]), (WZ, [("Play Coldplay Yellow", {"music_play", "stream_play"}), ("yes", ANY)]),
    (WZ, [("Which albums do we have by Adele?", {"music_albums"})]),
    (WZ, [("Play BBC Radio 2", {"stream_play"})]), (WZ, [("Turn the TV off", {"room_off"})]),
    (WZ, [("What's the weather like tomorrow?", {"weather"})]), (WZ, [("Do I need an umbrella today?", {"weather"})]),
    (WZ, [("Where are lights still on?", {"lights_status"})]), (WZ, [("What is playing right now?", {"rooms_playing"})]),
    (AS, [("Two degrees warmer", {"climate_setpoint_delta", "climate_setpoint"})]),
    (AS, [("How warm is it in here?", {"get_temperature"})]),
    (WZ, [("Blinds to 50 percent", set())]), (WZ, [("Turn on Netflix", set())]),
    (WZ, [("Thanks", set())]), (EK, [("Turn on the radio", set()), ("BBC Radio 1", {"stream_play"})]),
    (WZ, [("Turn off the thingamajig", ANY), ("never mind", set())]),
]
# deutsche Wörter, die in einer englischen Antwort nichts zu suchen haben
_DE = re.compile(r"\b(ist|sind|nicht|kein\w*|und|oder|Licht|Lichter|Raum|läuft|finde|habe|gibt|soll|ich|auf|aus|an|Grad|"
                 r"Prozent|Uhr|bitte|welche\w*|meinst|gefunden|Simulation|nichts|Moment|gerade|schon|Spiele|Zufällig|"
                 r"Lautstärke|Rollläden|Rollladen|fahren|pausiert|gestoppt)\b")


def german_rest(text: str, names: list[str]) -> list[str]:
    t = re.sub(r"[“„\"][^”“\"]*[”“\"]", " ", text)           # Zitate (Titel, Gerätenamen)
    for n in sorted(names, key=len, reverse=True):
        t = t.replace(n, " ")
    return _DE.findall(t)


async def main():
    b = v.Bridge("http://127.0.0.1:8080")
    b.session = aiohttp.ClientSession(base_url="http://127.0.0.1:8080")
    eng = TextEngine()
    devs, rooms = await b.context()
    names = [r["name"] for r in rooms] + [d.get("note") or d["name"] for d in devs] + [d["name"] for d in devs]
    ok = n = rest_n = 0
    times = []
    for room, turns in DIALOGS:
        dialog = v.Dialog()
        lines = []
        for text, want in turns:
            ws = FakeWS()
            eng.next = text
            t0 = time.time()
            await v.process_utterance(ws, b"\0" * 32000, eng, b, v.load_cfg(), "test", [room], simulate=True, dialog=dialog)
            dt = time.time() - t0
            dec = next((m for m in ws.msgs if m.get("type") == "decision"), {})
            got = {c.get("function", {}).get("name", "") for c in dec.get("tools") or []}
            answer = str(dec.get("text", ""))
            rest = german_rest(answer, names)
            rest_n += bool(rest)
            good = want is None or (got & want if want else not got)
            if want is not None:
                n += 1
                ok += bool(good)
                times.append(dt)
            mark = "  " if want is None else ("✓ " if good else "✗ ")
            lines.append(f"{mark}„{text}“ → [{','.join(sorted(got)) or '–'}] {answer[:110]} ({dt:.1f}s)"
                         + (f"  ← deutsch: {', '.join(rest)}" if rest else ""))
        print(f"[{room}]\n   " + "\n   ".join(lines))
    await b.session.close()
    times.sort()
    print(f"\n=== English: {ok}/{n} right tool · {rest_n} answers with German words · median "
          f"{times[len(times) // 2] if times else 0:.1f} s")

sys.stdout.reconfigure(encoding="utf-8")
asyncio.run(main())
