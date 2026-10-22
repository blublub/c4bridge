"""Sprachbefehle als TEXT testen – standardmäßig im Trockenlauf (schaltet nichts im Haus).
Mit --live werden die Befehle ECHT ausgeführt (nur bewusst und mit Absprache).

Durchläuft die echte Kette ohne Mikrofon/Whisper: Prompt (Geräte/Räume/Genres von der Bridge)
→ Ollama (Modell aus data/voice.json) → Werkzeug-Ausführung mit dry_run=True (die Bridge löst
Musik/Räume/Lautstärke auf, sendet aber keinen Befehl).

  .venv/Scripts/python.exe tools/voice_texttest.py [--room Wohnzimmer] ["Satz 1" "Satz 2" …]
        [--bridge http://127.0.0.1:8080] [--user NAME --password PW]
Ohne Sätze läuft eine Standardliste (Musik + Wiedergabe).
"""
import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import voice  # noqa: E402

DEFAULT = [
    "Spiel was von Sting",
    "Spiel das Album Über den Wolken",
    "Spiel Fields of Gold",
    "Ich möchte Jazz hören",
    "Mach mal ein bisschen Klassik an",
    "Spiel irgendwas",
    "Spiel Amy Macdonald in der Küche",
    "Mach lauter",
    "Ein bisschen leiser bitte",
    "Lautstärke auf 30",
    "Mach volle Pulle, Lautstärke 100",
    "Pause",
    "Weiter",
    "Nächstes Lied",
    "Ton aus",
    "Musik aus",
    "Spiel was von xyzzy unbekannt",
]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sentences", nargs="*")
    ap.add_argument("--room", default="Wohnzimmer", help="Raum des (gedachten) Mikrofons")
    ap.add_argument("--bridge", default="http://127.0.0.1:8080")
    ap.add_argument("--user")
    ap.add_argument("--password", default="")
    ap.add_argument("--live", action="store_true", help="ECHT ausführen statt Trockenlauf (nur mit Absprache)")
    ap.add_argument("--pause", type=float, default=0, help="Sekunden Wartezeit nach jedem Satz")
    a = ap.parse_args()
    dry = not a.live

    cfg = voice.load_cfg()
    bridge = voice.Bridge(a.bridge)
    hdr = {}
    if a.user:
        hdr["Authorization"] = "Basic " + base64.b64encode(f"{a.user}:{a.password}".encode()).decode()
    bridge.session = aiohttp.ClientSession(base_url=a.bridge.rstrip("/"), headers=hdr)
    speaker = [a.room] if a.room else []
    mode = "LIVE – Befehle werden ECHT ausgeführt" if a.live else "TROCKENLAUF"
    print(f"Modell {cfg['llm']['model']} · Sprecherraum {speaker or '–'} · {mode}\n")
    try:
        for text in a.sentences or DEFAULT:
            t0 = time.time()
            devs, rooms = await bridge.context()
            await bridge.load_caps(speaker)
            r = await voice.ollama_decide(cfg, bridge.prompt(devs, rooms, speaker, voice.house_prompt(cfg), voice.examples_text(cfg)), text,
                                          tools=voice.tools_for(bridge, cfg), note=bridge.note(devs, speaker, await bridge.now_playing(speaker)))
            calls = [c.get("function", {}) for c in r["tool_calls"]]
            out = []
            for fn in calls:
                try:
                    out.append(await voice.execute_tool(fn, bridge, dry, speaker, cfg))
                except Exception as e:  # noqa: BLE001
                    out.append(f"FEHLER {type(e).__name__}: {e}")
            tools = ", ".join(f"{fn.get('name')}{json.dumps(fn.get('arguments'), ensure_ascii=False)}" for fn in calls)
            print(f"» {text}\n  Werkzeug: {tools or '–'}\n  Antwort:  {' · '.join(out) or r['content']}  ({time.time() - t0:.1f}s)")
            if a.pause:
                await asyncio.sleep(a.pause)
            if a.live and speaker:      # Live: Raumzustand nach dem Befehl zeigen
                rid = next((x["id"] for x in rooms if x["name"] == speaker[0]), None)
                if rid:
                    st = await bridge.get(f"/api/rooms/{rid}/state")
                    mi = (st.get("media_raw") or {}).get("mediainfo") or {}
                    print(f"  Zustand:  an={st.get('power')} Quelle={st.get('selected_device')} Lautstärke={st.get('volume')}"
                          f" stumm={st.get('muted')} · {mi.get('artist', '')} – {mi.get('title', '')}")
            print()
    finally:
        await bridge.session.close()


if __name__ == "__main__":
    asyncio.run(main())
