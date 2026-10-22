"""Farblicht (Control4 light_v2): Zustand lesen, Presets, Farbnamen → SET_COLOR_TARGET-Parameter.

Der Director meldet Farbe nur bei Leuchten, die sie können (Variablen „Current Color …"); daran erkennen wir sie.
Modus 0 = volle Farbe (CIE xy), Modus 1 = Weißton (Kelvin).
"""
import colorsys
import re

STATE_VARS = ("Current Color Mode", "Current Color Chromaticity", "Current CCT", "Current Color Hue", "Current Color Saturation")
RATE_MS = 400

# Farbwörter (DE/EN) → ("k", Kelvin) oder ("hs", Farbton°, Sättigung %)
NAMES = {
    "warmweiß": ("k", 2700), "warmweiss": ("k", 2700), "warm white": ("k", 2700), "warm": ("k", 2700),
    "neutralweiß": ("k", 4000), "neutralweiss": ("k", 4000), "neutral white": ("k", 4000), "neutral": ("k", 4000),
    "kaltweiß": ("k", 6000), "kaltweiss": ("k", 6000), "cool white": ("k", 6000), "tageslicht": ("k", 6000),
    "daylight": ("k", 6000), "weiß": ("k", 4000), "weiss": ("k", 4000), "white": ("k", 4000),
    "rot": ("hs", 0, 100), "red": ("hs", 0, 100), "orange": ("hs", 28, 100),
    "gelb": ("hs", 52, 100), "yellow": ("hs", 52, 100), "grün": ("hs", 120, 100), "gruen": ("hs", 120, 100),
    "green": ("hs", 120, 100), "türkis": ("hs", 175, 100), "tuerkis": ("hs", 175, 100), "turquoise": ("hs", 175, 100),
    "cyan": ("hs", 185, 100), "blau": ("hs", 230, 100), "blue": ("hs", 230, 100),
    "lila": ("hs", 275, 100), "violett": ("hs", 275, 100), "purple": ("hs", 275, 100), "violet": ("hs", 275, 100),
    "magenta": ("hs", 300, 100), "pink": ("hs", 325, 70), "rosa": ("hs", 340, 45),
}


def hs_to_xy(hue: float, sat: float) -> tuple[float, float]:
    """Farbton (0–360°) + Sättigung (0–100 %) → CIE-xy (sRGB, D65)."""
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360, max(0.0, min(1.0, sat / 100)), 1.0)
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    X = 0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]
    Y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    Z = 0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]
    s = X + Y + Z or 1.0
    return round(X / s, 4), round(Y / s, 4)


def params_kelvin(k: int, lo: int = 2000, hi: int = 6500) -> dict:
    return {"LIGHT_COLOR_TARGET_COLOR_CORRELATED_TEMPERATURE": int(max(lo, min(hi, k))), "LIGHT_COLOR_TARGET_MODE": 1, "RATE": RATE_MS}


def params_xy(x: float, y: float) -> dict:
    return {"LIGHT_COLOR_TARGET_X": float(x), "LIGHT_COLOR_TARGET_Y": float(y), "LIGHT_COLOR_TARGET_MODE": 0, "RATE": RATE_MS}


def parse_state(vals: dict) -> dict | None:
    """vals = {varName: value} einer Leuchte → {mode, x, y, cct, hue, sat} oder None (keine Farbleuchte)."""
    if vals.get("Current Color Mode") is None:
        return None
    try:
        x, y = (float(p) for p in str(vals.get("Current Color Chromaticity") or "").split(",")[:2])
    except ValueError:
        x = y = None
    return {"mode": "cct" if str(vals.get("Current Color Mode")) == "1" else "color", "x": x, "y": y,
            "cct": vals.get("Current CCT"), "hue": vals.get("Current Color Hue"), "sat": vals.get("Current Color Saturation")}


def presets(vals: dict) -> tuple[list, int, int]:
    """Presets aus „Color Presets" (nur die benannten der App, Kategorie 2) + Kelvin-Bereich."""
    c = ((vals.get("Color Presets") or {}).get("colors") or {}) if isinstance(vals.get("Color Presets"), dict) else {}
    lst = c.get("color") or []
    if isinstance(lst, dict):
        lst = [lst]
    out = [{"id": p.get("id"), "name": p.get("name"), "mode": "cct" if p.get("color_mode") == 1 else "color",
            "x": p.get("color_x"), "y": p.get("color_y"), "cct": p.get("color_correlated_temperature"),
            "hue": p.get("color_hue"), "sat": p.get("color_saturation")}
           for p in lst if p.get("category") == 2 and p.get("name")]
    return out, int(c.get("color_correlated_temperature_min") or 2000), int(c.get("color_correlated_temperature_max") or 6500)


def preset_params(p: dict, lo: int, hi: int) -> dict:
    return params_kelvin(p["cct"], lo, hi) if p["mode"] == "cct" and (p.get("cct") or 0) > 0 else params_xy(p["x"], p["y"])


def resolve(text: str, plist: list, lo: int, hi: int) -> dict | None:
    """Freitext („warmweiß", „blau", „Sunset", „3000 K") → SET_COLOR_TARGET-Parameter oder None."""
    t = (text or "").strip().lower()
    m = re.fullmatch(r"(\d{4})\s*k?(elvin)?", t)
    if m:
        return params_kelvin(int(m.group(1)), lo, hi)
    for p in plist:
        if (p["name"] or "").lower() == t:
            return preset_params(p, lo, hi)
    n = NAMES.get(t)
    if n:
        return params_kelvin(n[1], lo, hi) if n[0] == "k" else params_xy(*hs_to_xy(n[1], n[2]))
    return None
