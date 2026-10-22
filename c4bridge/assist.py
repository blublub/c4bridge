"""Wissen für die Sprachsteuerung: Wetter (Open-Meteo) und Websuche (SearXNG oder Exa).

Die Bridge macht alle Internet-Abfragen – c4voice fragt nur die Bridge (Service-Token).
So liegen Schlüssel (EXA_API_KEY in config.env) und Internetzugriff an EINER Stelle,
und Hausdaten (Geräte, Räume) gehen nie in eine Suchanfrage: gesucht wird nur der Text,
den das Sprachmodell für das Werkzeug web_search formuliert.
"""
import datetime as dt
from typing import Optional

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=8)
UA = {"User-Agent": "c4bridge/1.0 (Smart-Home-Sprachassistent)"}

# WMO-Wettercodes → deutscher Text (Open-Meteo „weather_code")
WMO = {
    0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt", 45: "Nebel", 48: "Nebel mit Reif",
    51: "leichter Nieselregen", 53: "Nieselregen", 55: "starker Nieselregen",
    56: "gefrierender Nieselregen", 57: "gefrierender Nieselregen",
    61: "leichter Regen", 63: "Regen", 65: "starker Regen", 66: "gefrierender Regen", 67: "gefrierender Regen",
    71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall", 77: "Schneegriesel",
    80: "Regenschauer", 81: "kräftige Regenschauer", 82: "heftige Regenschauer",
    85: "Schneeschauer", 86: "kräftige Schneeschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel",
}
DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _fmt(v) -> str:
    """17.4 → „17", -0.4 → „0" (gesprochen klingen ganze Grad natürlicher)."""
    r = round(float(v))
    return str(0 if r == 0 else r)


async def geocode(name: str) -> list:
    """Ortsname → Kandidaten [{name, admin, country, lat, lon}] (Open-Meteo Geocoding, ohne Schlüssel)."""
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=UA) as s:
        async with s.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 5, "language": "de", "format": "json"}) as r:
            r.raise_for_status()
            data = await r.json()
    return [{"name": x.get("name"), "admin": x.get("admin1") or "", "country": x.get("country") or "",
             "lat": round(x["latitude"], 4), "lon": round(x["longitude"], 4)} for x in data.get("results") or []]


async def weather(lat: float, lon: float, day: int = 0, place: str = "") -> dict:
    """Vorhersage für einen Tag (0 = heute … 6) + bei heute die aktuelle Lage.
    Liefert Zahlen UND einen fertigen deutschen Satz (text) für die Sprachausgabe."""
    day = max(0, min(6, int(day)))
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,wind_speed_10m_max,sunrise,sunset",
        "hourly": "precipitation_probability,precipitation",
    }
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=UA) as s:
        async with s.get("https://api.open-meteo.com/v1/forecast", params=params) as r:
            r.raise_for_status()
            w = await r.json()
    d, h = w["daily"], w.get("hourly") or {}
    date = d["time"][day]
    out = {
        "place": place, "date": date, "day": day,
        "label": "heute" if day == 0 else "morgen" if day == 1 else "übermorgen" if day == 2
        else DAYS[dt.date.fromisoformat(date).weekday()],
        "code": d["weather_code"][day], "condition": WMO.get(d["weather_code"][day], "wechselhaft"),
        "t_max": d["temperature_2m_max"][day], "t_min": d["temperature_2m_min"][day],
        "rain_prob": d["precipitation_probability_max"][day], "rain_mm": d["precipitation_sum"][day],
        "wind_max": d["wind_speed_10m_max"][day],
        "sunrise": d["sunrise"][day][-5:], "sunset": d["sunset"][day][-5:],
    }
    # ab wann regnet es (erste Stunde des Tages mit ≥ 50 % Regenwahrscheinlichkeit, heute erst ab jetzt)
    now_h = (w.get("current") or {}).get("time", "")[:13]
    rain_from = None
    for t, p in zip(h.get("time") or [], h.get("precipitation_probability") or []):
        if t.startswith(date) and (day or t[:13] >= now_h) and (p or 0) >= 50:
            rain_from = t[11:16]
            break
    out["rain_from"] = rain_from
    if day == 0 and w.get("current"):
        c = w["current"]
        out["now"] = {"temp": c.get("temperature_2m"), "feels": c.get("apparent_temperature"),
                      "condition": WMO.get(c.get("weather_code"), ""), "wind": c.get("wind_speed_10m")}
    # gesprochener Satz
    where = f" in {place}" if place else ""
    parts = [f"{out['label'].capitalize()}{where}: {out['condition']}, {_fmt(out['t_min'])} bis {_fmt(out['t_max'])} Grad"]
    if day == 0 and out.get("now") and out["now"]["temp"] is not None:
        parts[0] = (f"Gerade{where} {_fmt(out['now']['temp'])} Grad und {out['now']['condition'] or out['condition']}. "
                    f"Heute {out['condition']}, {_fmt(out['t_min'])} bis {_fmt(out['t_max'])} Grad")
    rp = out["rain_prob"] or 0
    if rp >= 50:
        parts.append(f"Regenrisiko {rp} Prozent" + (f", ab etwa {int(rain_from[:2])} Uhr" if rain_from else ""))
    elif rp >= 20:
        parts.append(f"Regenrisiko {rp} Prozent")
    else:
        parts.append("trocken")
    if (out["wind_max"] or 0) >= 40:
        parts.append(f"windig, Böen bis {_fmt(out['wind_max'])} Kilometer pro Stunde")
    out["text"] = ", ".join(parts) + "."
    return out


async def search_searxng(url: str, query: str, n: int = 5) -> list:
    """SearXNG (selbst betrieben): JSON-Ausgabe muss in settings.yml erlaubt sein (search.formats: json)."""
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=UA) as s:
        async with s.get(url.rstrip("/") + "/search",
                         params={"q": query, "format": "json", "language": "de", "safesearch": 1}) as r:
            if r.status == 403:
                raise RuntimeError("SearXNG verweigert JSON – in settings.yml unter search.formats „json“ ergänzen")
            r.raise_for_status()
            data = await r.json(content_type=None)
    out = []
    for x in data.get("answers") or []:            # Sofortantworten (z. B. Rechner, Wikipedia-Box)
        txt = x if isinstance(x, str) else (x.get("answer") or "")
        if txt:
            out.append({"title": "Sofortantwort", "snippet": str(txt)[:600], "url": ""})
    for x in data.get("results") or []:
        out.append({"title": x.get("title") or "", "snippet": (x.get("content") or "")[:600], "url": x.get("url") or ""})
    return out[:n]


async def search_exa(key: str, query: str, n: int = 5) -> list:
    """Exa (exa.ai): liefert passende Textausschnitte direkt mit (kostenpflichtig, API-Schlüssel)."""
    body = {"query": query, "numResults": n, "type": "auto",
            "contents": {"text": {"maxCharacters": 700}}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12), headers=UA) as s:
        async with s.post("https://api.exa.ai/search", json=body,
                          headers={"x-api-key": key, "Content-Type": "application/json"}) as r:
            if r.status in (401, 403):
                raise RuntimeError("Exa: API-Schlüssel ungültig")
            r.raise_for_status()
            data = await r.json()
    return [{"title": x.get("title") or "", "snippet": (x.get("text") or "")[:700], "url": x.get("url") or ""}
            for x in data.get("results") or []]


async def search(provider: str, query: str, n: int, searxng_url: str = "", exa_key: str = "") -> list:
    if provider == "exa":
        if not exa_key:
            raise RuntimeError("Exa: kein API-Schlüssel hinterlegt (Admin → Sprache → Wissen & Wetter)")
        return await search_exa(exa_key, query, n)
    if not searxng_url:
        raise RuntimeError("SearXNG: keine URL hinterlegt (Admin → Sprache → Wissen & Wetter)")
    return await search_searxng(searxng_url, query, n)


def provider_ready(acfg: dict, exa_key: str) -> Optional[str]:
    """None = Websuche nutzbar, sonst Grund."""
    web = (acfg or {}).get("web") or {}
    if not web.get("enabled"):
        return "aus"
    if web.get("provider") == "exa":
        return None if exa_key else "kein Exa-Schlüssel"
    return None if web.get("searxng_url") else "keine SearXNG-URL"


async def panel(lat: float, lon: float, place: str = "", hours: int = 6) -> dict:
    """Kompaktes Wetter für die Oberfläche: jetzt, die nächsten Stunden, morgen (Open-Meteo, ohne Schlüssel)."""
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day",
        "hourly": "temperature_2m,weather_code,precipitation_probability,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers=UA) as s:
        async with s.get("https://api.open-meteo.com/v1/forecast", params=params) as r:
            r.raise_for_status()
            w = await r.json()
    c, h, d = w.get("current") or {}, w.get("hourly") or {}, w.get("daily") or {}
    now_h = (c.get("time") or "")[:13]
    nxt = []
    for i, t in enumerate(h.get("time") or []):
        if t[:13] > now_h and len(nxt) < hours:
            nxt.append({"time": t[11:16], "temp": h["temperature_2m"][i], "code": h["weather_code"][i],
                        "rain": h["precipitation_probability"][i], "day": h["is_day"][i]})
    tm = 1 if len(d.get("time") or []) > 1 else 0
    return {
        "place": place,
        "now": {"temp": c.get("temperature_2m"), "feels": c.get("apparent_temperature"), "code": c.get("weather_code"),
                "condition": WMO.get(c.get("weather_code"), ""), "wind": c.get("wind_speed_10m"), "day": c.get("is_day", 1),
                "t_min": (d.get("temperature_2m_min") or [None])[0], "t_max": (d.get("temperature_2m_max") or [None])[0]},
        "hours": nxt,
        "tomorrow": {"code": d["weather_code"][tm], "condition": WMO.get(d["weather_code"][tm], ""),
                     "t_min": d["temperature_2m_min"][tm], "t_max": d["temperature_2m_max"][tm],
                     "rain": d["precipitation_probability_max"][tm]} if d.get("time") else None,
    }
