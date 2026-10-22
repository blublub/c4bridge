"""LLM-Messung: Wo bleibt die Zeit (Prompt einlesen vs. Antwort) und welches Modell trifft die richtigen Werkzeuge?
Nur Trockenlauf – execute_tool wird NICHT aufgerufen, es zählt nur die Werkzeugwahl (plus: fragt es bei Mehrdeutigkeit?).
Aufruf: python tools/llm_bench.py qwen3:8b qwen3:14b gemma4:12b [--rounds 2]"""
from pathlib import Path
import asyncio
import json
import re
import sys
import time

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import voice as v  # noqa: E402

AS = "Arbeit Alex"
WZ = "Wohnzimmer"
EK = "Esszimmer / Küche"


def has(a, key, *words):
    s = str(a.get(key, "")).lower()
    return any(w in s for w in words)


# (Satz, Sprecherraum, erwartete Werkzeuge, Prüfung der Argumente, Rückfrage ohne Werkzeug ebenfalls richtig?)
CASES = [
    ("Mach das Licht an", AS, {"light_set"}, lambda a: a.get("on") is True and not a.get("device"), False),
    ("Mach es etwas dunkler", AS, {"light_adjust"}, lambda a: a.get("direction") == "down", False),
    ("Mach im Wohnzimmer das Licht aus", AS, {"light_set"}, lambda a: a.get("on") is False and has(a, "room", "wohn"), False),
    ("Spiel was von Sting", AS, {"music_play"}, lambda a: has(a, "search", "sting") or has(a, "artist", "sting"), False),
    ("Spiel SWR3", AS, {"stream_play"}, lambda a: a.get("service") == "radio", False),
    ("Spiel Adele auf Qobuz", AS, {"stream_play"}, lambda a: a.get("service") == "qobuz", False),
    ("Zufallswiedergabe an", AS, {"media_control"}, lambda a: a.get("action") == "shuffle_on", False),
    ("Fahr den Rollladen Garten runter", AS, {"blind_action"}, lambda a: a.get("action") == "close", False),
    ("Stell die Heizung zwei Grad wärmer", AS, {"climate_setpoint_delta"}, lambda a: float(a.get("delta_c", 0)) == 2, False),
    ("Schalte die Schreibtischlampe ein", AS, {"switch_set"}, lambda a: a.get("on") is True, False),
    ("Wo läuft gerade Musik?", AS, {"rooms_playing"}, lambda a: True, False),
    ("Wie wird das Wetter morgen?", AS, {"weather"}, lambda a: True, False),
    # --- Problemsätze aus dem GPU-Lauf 18.10. ---
    ("Spiel im Esszimmer mal über Qobuz, die drei Fragezeichen Kids, letzte Folge 104.", AS, {"stream_play"},
     lambda a: a.get("service") == "qobuz" and has(a, "search", "fragezeichen", "???") and has(a, "room", "ess"), False),
    ("Spiel Amy Macdonald in der Küche", EK, {"music_play"}, lambda a: has(a, "search", "amy") or has(a, "artist", "amy"), False),
    ("Machen wir die Schreibbestände aus.", AS, {"light_set", "switch_set"}, lambda a: a.get("on") is False, True),
    ("Mach alles zu", AS, {"blind_action"}, lambda a: a.get("action") == "close", True),
    ("Wie warm ist es hier?", AS, {"get_temperature"}, lambda a: not a.get("room"), False),
    ("Welche Temperatur ist im Bad?", AS, {"get_temperature"}, lambda a: has(a, "room", "bad"), True),
    ("Mach das Licht im Bad aus", AS, {"light_set"}, lambda a: a.get("on") is False and has(a, "room", "bad"), True),
    ("Spiel die drei Fragezeichen Folge 10", WZ, {"music_play", "stream_play"}, lambda a: True, True),
    ("Breaking Bad Folge 10", WZ, {"video_play"}, lambda a: has(a, "search", "breaking") and str(a.get("episode", "")) == "10", False),
    ("Starte den Film Inception", AS, set(), lambda a: True, True),          # kein KODI im Raum → sagen/fragen, kein Werkzeug
    ("Spiel das Hörspiel Benjamin Blümchen Folge 3", WZ, {"music_play"}, lambda a: has(a, "search", "blümchen", "bluemchen"), False),
    ("Mach die Rollos zu", AS, {"blind_action"}, lambda a: a.get("action") == "close" and not has(a, "device", "schreibtisch"), False),
]


async def run(model: str, rounds: int = 1, ctx: int = 0):
    cfg = v.load_cfg()
    cfg["llm"]["model"] = model
    opts = {"temperature": 0.1, **({"num_ctx": ctx} if ctx else {})}
    b = v.Bridge("http://127.0.0.1:8080")
    b.session = aiohttp.ClientSession(base_url="http://127.0.0.1:8080")
    devs, rooms = await b.context()
    await b.load_caps([AS, WZ, EK])
    prompt = b.prompt(devs, rooms, [], v.house_prompt(cfg), v.examples_text(cfg))
    tools = v.tools_for(b, cfg)
    url = cfg["llm"]["url"].rstrip("/") + "/api/chat"
    ok, times, pe, ev, fails = 0, [], [], [], []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
        # Aufwärmen (Modell laden, Prompt-Cache füllen)
        async with s.post(url, json={"model": model, "stream": False, "think": False, "keep_alive": "60m",
                                     "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": "Hallo"}],
                                     "tools": tools, "options": opts}) as r:
            d = await r.json()
            if "error" in d:
                print(f"{model}: {d['error']}")
                await b.session.close()
                return
            warm = d.get("prompt_eval_duration", 0) / 1e9
        for _ in range(rounds):
            for text, room, want, chk, ask_ok in CASES:
                note = b.note(devs, [room], {})
                t0 = time.time()
                async with s.post(url, json={"model": model, "stream": False, "think": False, "keep_alive": "60m",
                                             "messages": [{"role": "system", "content": prompt},
                                                          {"role": "user", "content": f"{note}\n{text}"}],
                                             "tools": tools, "options": opts}) as r:
                    d = await r.json()
                dt = time.time() - t0
                msg = d.get("message") or {}
                calls = msg.get("tool_calls") or []
                fn = calls[0]["function"] if calls else {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                content = re.sub(r"\s+", " ", msg.get("content") or "").strip()
                asked = not calls and "?" in content
                if calls:
                    good = fn.get("name") in want and chk(args)
                else:
                    good = (ask_ok and asked) or (not want and bool(content))
                ok += good
                times.append(dt)
                pe.append((d.get("prompt_eval_count", 0), d.get("prompt_eval_duration", 0) / 1e9))
                ev.append((d.get("eval_count", 0), d.get("eval_duration", 0) / 1e9))
                if not good:
                    fails.append(f"   ✗ {text!r} [{room}]: {fn.get('name') or '–'} {json.dumps(args, ensure_ascii=False) if calls else ''} {content[:90]!r}")
    await b.session.close()
    n = len(times)
    print(f"{model:14}{' ctx=' + str(ctx) if ctx else '':10} richtig {ok}/{n} | Ø {sum(times)/n:.2f} s (max {max(times):.2f}) | Aufwärmen {warm:.1f} s | "
          f"Antwort Ø {sum(x[0] for x in ev)/n:.0f} Tok in {sum(x[1] for x in ev)/n:.2f} s "
          f"({(sum(x[0] for x in ev) / max(0.01, sum(x[1] for x in ev))):.0f} Tok/s)")
    for f in fails:
        print(f)


if __name__ == "__main__":
    argv = sys.argv[1:]
    rounds, ctx = 1, 0
    for flag in ("--rounds", "--ctx"):
        if flag in argv:
            i = argv.index(flag)
            val = int(argv[i + 1])
            del argv[i:i + 2]
            rounds, ctx = (val, ctx) if flag == "--rounds" else (rounds, val)
    for m in argv:
        asyncio.run(run(m, rounds, ctx))
