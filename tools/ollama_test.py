"""Trockentest: Lokales LLM (Ollama) als Sprach-Assistent fuer die c4bridge.

Fragt mehrere deutsche Befehle ab und prueft NUR die Entscheidung des Modells
(native Tool-Calls). Es wird NICHTS am Haus ausgefuehrt — der Test wertet die
Antwort des Modells bloss aus und druckt sie.

Aufruf:
  .venv/Scripts/python.exe tools/ollama_test.py [--model qwen3.5:9b] [--host http://localhost:11434]
"""
import argparse
import json
import time
import urllib.request

DATA = None  # wird geladen


def load_inventory(base):
    import pathlib
    inv = json.loads((base / "data" / "inventory.json").read_text(encoding="utf-8"))
    try:
        ov = json.loads((base / "data" / "overrides.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        ov = {"devices": {}, "rooms": {}}
    rooms = {r["id"]: r for r in inv["rooms"].values()}
    lines = []
    for d in inv["devices"].values():
        if d.get("driver_wrapper"):
            continue
        o = ov.get("devices", {}).get(str(d["id"]), {})
        if o.get("enabled") is False:
            continue
        if d["type"] not in ("light", "blind", "thermostat"):
            continue
        name = o.get("note") or d["name"]
        alias = ", ".join(o.get("aliases", []))
        lines.append(f"- {d['type']} \"{name}\" (Raum: {d['room']}, ID {d['id']})"
                     + (f", auch genannt: {alias}" if alias else ""))
    rooms_txt = ", ".join(sorted({r["name"] for r in rooms.values()}))
    return rooms_txt, "\n".join(lines)


SYSTEM_PROMPT = """Du bist der Sprachassistent eines Smart Homes (Control4-Haus, gesteuert ueber die c4bridge).
Du beantwortest kurze deutsche Saetze und fuehrst Haus-Aufgaben AUSNAHMSLOS ueber die angeboteten Funktionen (tools) aus.

Verfuegbare Raeume: {rooms}

Verfuegbare Geraete:
{devices}

Regeln:
1. Nenne Geraete/Raeume immer mit dem exakten Namen aus der Liste (Gross-/Kleinschreibung egal).
2. Ist unklar, welches Geraet gemeint ist, frage kurz nach (kein Werkzeugaufruf).
3. Gibt es kein passendes Geraet/Werkzeug (z. B. Tuerschloss, Alarmanlage), sag es kurz - erfinde nichts.
4. Ruecke niemals IDs aus, die nicht in der Liste stehen.
5. Antworte kurz auf Deutsch (ein Satz). Nach einer Aktion bestaetige sie knapp.
"""


TOOLS = [
    {"type": "function", "function": {
        "name": "light_set",
        "description": "Schaltet ein Licht ein oder aus; optional Helligkeit in Prozent (nur bei dimmbaren Lampen).",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "Exakter Geraetename aus der Liste"},
            "on": {"type": "boolean"},
            "level": {"type": "integer", "description": "Helligkeit 0-100, optional"}},
            "required": ["device", "on"]}}},
    {"type": "function", "function": {
        "name": "blind_action",
        "description": "Faehrt einen Rollladen/eine Jalousie: open (auf), close (zu) oder stop.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string"},
            "action": {"type": "string", "enum": ["open", "close", "stop"]}},
            "required": ["device", "action"]}}},
    {"type": "function", "function": {
        "name": "climate_setpoint",
        "description": "Setzt den Sollwert eines Thermostats in Grad Celsius (absolut).",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string"},
            "celsius": {"type": "number"}},
            "required": ["device", "celsius"]}}},
    {"type": "function", "function": {
        "name": "climate_setpoint_delta",
        "description": "Aendert den Thermostat-Sollwert um +x bzw. -x Grad Celsius.",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string"},
            "delta_c": {"type": "number"}},
            "required": ["device", "delta_c"]}}},
    {"type": "function", "function": {
        "name": "get_temperature",
        "description": "Liest die aktuelle Temperatur eines Thermostats (liefert Grad Celsius).",
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string"}},
            "required": ["device"]}}},
    {"type": "function", "function": {
        "name": "music_play",
        "description": "Sucht in der Musikbibliothek und spielt ein Album/Interpret in einem Raum ab.",
        "parameters": {"type": "object", "properties": {
            "room": {"type": "string"},
            "search": {"type": "string"}},
            "required": ["room", "search"]}}},
    {"type": "function", "function": {
        "name": "room_off",
        "description": "Schaltet alle Geraete/Quellen eines Raums aus.",
        "parameters": {"type": "object", "properties": {
            "room": {"type": "string"}},
            "required": ["room"]}}},
]

# Erwartung je Testfall: tool-Name + Schluesselwerte (Teilstring-Vergleich) oder "chat"/"refuse"
CASES = [
    ("Mach das Licht in Arbeit Alex an.", [("light_set", '"on":true')]),
    ("Rolladen Alex Westseite zu.", [("blind_action", '"action":"close"')]),
    ("Wie warm ist es im Esszimmer?", [("get_temperature", "")]),
    ("Dimme das Licht Schreibtisch im Arbeitszimmer auf 40 Prozent.", [("light_set", '"level":40')]),
    ("Stell das Thermostat in Arbeit Alex auf 21 Grad.", [("climate_setpoint", '"celsius":21')]),
    ("Mach es in Arbeit Alex waermer.", [("climate_setpoint_delta", '"delta_c":')]),
    ("Spiel Musik von den Beatles im Wohnzimmer.", [("music_play", "")]),
    ("Oeffne die Haustuer.", [("refuse", "")]),          # kein Schloss-Werkzeug
    ("Schalte das Licht im Flurblitz aus.", [("chat", "")]),  # unklarer Raum
    ("Mach das Licht im Arbeitszimmer an.", [("chat", "")]),  # Arbeit Kim ODER Alex -> nachfragen
    ("Schalte alles im Wohnzimmer aus.", [("room_off", "")]),
    ("Licht Schreibtisch aus.", [("light_set", '"on":false')]),
]


def chat(host, model, messages, tools, timeout):
    body = {"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.1}}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(host + "/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def judge(reply, expects):
    calls = reply.get("message", {}).get("tool_calls") or []
    text = reply.get("message", {}).get("content") or ""
    got = []
    for c in calls:
        fn = c.get("function", {})
        got.append(f"{fn.get('name')}({fn.get('arguments')})")
    flat = json.dumps(calls, ensure_ascii=False)
    for kind, needle in expects:
        if kind == "refuse":
            if not calls and any(w in text.lower() for w in ("kann ich nicht", "nicht verfügbar", "kein", "nicht möglich")):
                return True, got or [text[:80]]
            return False, got or [text[:120]]
        if kind == "chat":
            if not calls and text.strip():
                return True, [text[:80]]
            return False, got or [text[:120]]
        if any(fn.get("name") == kind and (not needle or needle.replace(" ", "") in flat.replace(" ", ""))
               for c in calls for fn in [c.get("function", {})]):
            return True, got
    return False, got or [text[:120]]


def main():
    import pathlib
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--case", type=int, default=None, help="nur Testfall Nr. ausfuehren")
    args = ap.parse_args()

    base = pathlib.Path(__file__).resolve().parent.parent
    rooms, devices = load_inventory(base)
    system = SYSTEM_PROMPT.format(rooms=rooms, devices=devices)
    print(f"Modell: {args.model} | Geraetekontext: {devices.count(chr(10)) + 1} Zeilen\n")

    ok = 0
    total = 0
    for i, (q, expects) in enumerate(CASES, 1):
        if args.case and i != args.case:
            continue
        total += 1
        t0 = time.time()
        try:
            r = chat(args.host, args.model,
                     [{"role": "system", "content": system}, {"role": "user", "content": q}],
                     TOOLS, args.timeout)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}] FEHLER: {e}")
            continue
        passed, got = judge(r, expects)
        ok += passed
        mark = "PASS" if passed else "FAIL"
        print(f"[{i}] {mark} ({time.time() - t0:.1f}s)  »{q}«")
        for g in got:
            print(f"      -> {g}")
    print(f"\nErgebnis: {ok}/{total} bestanden")


if __name__ == "__main__":
    main()
