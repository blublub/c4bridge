"""Offline-Test der Ducking-Logik der Bridge: room_state/_set_room_volume ersetzt – nichts im Haus."""
from pathlib import Path
import asyncio, sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import c4bridge.app as A

ROOMS = {1: {"name": "Wohnzimmer"}, 2: {"name": "Küche"}, 3: {"name": "Bad"}}
STATE = {1: {"selected_device": 9, "volume": 40, "play_state": "play"},
         2: {"selected_device": 9, "volume": 30, "play_state": "pause"},
         3: {"selected_device": 0, "volume": 20}}
sets = []


async def fake_state(rid):
    st = dict(STATE[rid])
    if rid in A._duck:
        st["duck"] = {"orig": A._duck[rid]["orig"]}
    return st


async def fake_set(rid, level, why=""):
    sets.append((rid, level)); STATE[rid]["volume"] = level

A.inv.rooms.clear(); A.inv.rooms.update(ROOMS)
A.room_state = fake_state; A._set_room_volume = fake_set
ok = True
def check(label, cond, info=""):
    global ok; ok &= bool(cond); print(("OK   " if cond else "FEHLER ") + label, info)

async def main():
    r = await A.duck(A.DuckBody(rooms=["Wohnzimmer", "Küche", "Bad"], on=True, dry=True))
    check("Planlauf schaltet nichts", not sets and "40 → 12" in r["rooms"]["Wohnzimmer"], r["rooms"])
    r = await A.duck(A.DuckBody(rooms=["Wohnzimmer", "Küche", "Bad"], on=True))
    check("nur der spielende Raum wird abgesenkt (40 → 12)", sets == [(1, 12)], r["rooms"])
    st = await fake_state(1)
    check("Raumzustand nennt den echten Pegel", st.get("duck", {}).get("orig") == 40, st)
    r = await A.duck(A.DuckBody(rooms=["Wohnzimmer"], on=False))
    check("zurück auf 40", sets[-1] == (1, 40), r["rooms"])
    # jemand dreht dazwischen → nicht zurückstellen
    sets.clear(); STATE[1]["volume"] = 40
    await A.duck(A.DuckBody(rooms=["Wohnzimmer"], on=True)); STATE[1]["volume"] = 25
    r = await A.duck(A.DuckBody(rooms=["Wohnzimmer"], on=False))
    check("dazwischen gedreht → so gelassen", sets == [(1, 12)], r["rooms"])
    # Sprach-/App-Befehl „Lautstärke“ beendet das Ducking ohne Zurückstellen
    sets.clear(); STATE[1]["volume"] = 40
    await A.duck(A.DuckBody(rooms=["Wohnzimmer"], on=True))
    await A.room_volume(1, A.VolumeBody(level=48))
    r = await A.duck(A.DuckBody(rooms=["Wohnzimmer"], on=False))
    check("Lautstärke-Befehl gewinnt (12, dann 48, kein Zurück)", sets == [(1, 12), (1, 48)], (sets, r["rooms"]))
    # Raum aus während des Duckings → nicht wieder einschalten
    sets.clear(); STATE[1].update(volume=40, selected_device=9)
    await A.duck(A.DuckBody(rooms=["Wohnzimmer"], on=True)); STATE[1]["selected_device"] = 0
    r = await A.duck(A.DuckBody(rooms=["Wohnzimmer"], on=False))
    check("Raum aus → nichts zurückgestellt", sets == [(1, 12)], r["rooms"])
    # Sicherheits-Timer
    sets.clear(); STATE[1].update(volume=40, selected_device=9)
    await A.duck(A.DuckBody(rooms=["Wohnzimmer"], on=True, max_s=5))
    await asyncio.sleep(5.5)
    check("Timer stellt nach max_s zurück", sets == [(1, 12), (1, 40)] and 1 not in A._duck, sets)
    print("\nALLES OK" if ok else "\nES GIBT FEHLER")

asyncio.run(main())
