"""Rauchtest: alle lesenden GET-Endpunkte der Bridge (keine Streams, keine Schreibaktionen)."""
import json
import time
import urllib.request
import urllib.error

B = "http://127.0.0.1:8080"


def get(path, timeout=30):
    t = time.time()
    try:
        with urllib.request.urlopen(B + path, timeout=timeout) as r:
            body = r.read()
            return r.status, time.time() - t, body
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, time.time() - t, str(e).encode()


rooms = [r for r in json.loads(get("/api/rooms")[2]) if not r.get("hidden")]
kodi = json.loads(get("/api/kodi")[2])["players"]
did = str(kodi[0]["driver_id"]) if kodi else ""
plain = ["/api/me", "/api/users", "/api/status", "/api/log", "/api/ws/status", "/api/events", "/api/ws/discover",
         "/api/drivers", "/api/cameras/config", "/api/cameras", "/api/state/status", "/api/state/rooms", "/api/rooms",
         "/api/rooms/power", "/api/rooms/playing", "/api/devices", "/api/ai/context", "/api/tree",
         "/api/media/search?q=sting", "/api/media/browse?kind=genre", "/api/media/resolve?kind=artist&q=Sting",
         "/api/media/recent", "/api/media/random", "/api/kodi", "/api/bluos/players", "/api/ui/layout",
         "/api/voice/status", "/api/voice/config", "/api/voice/prompts/defaults", "/api/assist/status",
         "/api/assist/weather?day=0", "/api/weather/panel", "/api/voice/tts/voices", "/api/scenes", "/api/config",
         "/api/setup/mediamtx", "/api/blinds/state", "/api/switches/state", "/api/devices/state", "/api/contacts/state",
         "/api/learn/items", "/api/learn/schemas", "/api/learn/profiles", "/api/learned/state",
         "/api/learn/inspect/1792", "/api/learn/suggest/1920", "/api/learn/evaluate/1920", "/api/learn/capture/1920"]
if did:
    plain += [f"/api/kodi/{did}/library/movies?limit=5", f"/api/kodi/{did}/library/tvshows?limit=5",
              f"/api/kodi/{did}/library/channels?limit=5", f"/api/kodi/{did}/library/recordings?limit=5",
              f"/api/kodi/{did}/library/files"]
per_room = ["/api/rooms/{r}/state", "/api/rooms/{r}/backend", "/api/rooms/{r}/media", "/api/rooms/{r}/climate",
            "/api/rooms/{r}/scenes", "/api/rooms/{r}/buttons", "/api/rooms/{r}/queue", "/api/rooms/{r}/push",
            "/api/devices/state?room_id={r}", "/api/learned/state?room_id={r}"]
bad, n, slow = [], 0, []
for p in plain + [x.format(r=r["id"]) for r in rooms for x in per_room]:
    code, dt, body = get(p)
    n += 1
    if code >= 500 or code == 0:
        bad.append((p, code, body[:160]))
    elif code >= 400 and "/queue" not in p:
        bad.append((p, code, body[:160]))
    if dt > 5:
        slow.append((p, round(dt, 1)))
print(f"{n} Abrufe, {len(rooms)} Räume")
print("Fehler:", len(bad))
for b in bad:
    print("  ", b)
print("langsam (>5 s):", slow)
