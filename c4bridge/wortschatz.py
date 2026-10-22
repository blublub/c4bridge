"""WORTSCHATZ der Spracherkennung – die EINE Stelle für Wörter, die die Erkennung kennen soll, und für Hörfehler.

Ausführliche Anleitung (was wo, wie testen): WORTSCHATZ.md im Projektordner c4bridge/.

Zwei Werkzeuge:
  1. HINWEISTEXT (vor dem Erkennen) – nur Whisper (CPU und GPU) nutzt ihn: Wörter, die im Text vorkommen, schreibt Whisper
     bevorzugt so. Er wird bei jedem Satz aus sechs Teilen gebaut (build_hint):
       a) Wake-Phrase („Hey Jarvis“)                         → automatisch aus Admin › Wake-Word
       b) eigener Text aus Admin › Spracherkennung › Hinweistext (voice.json stt.hint) → für EURE Wörter, frei änderbar
       c) Raumnamen                                          → automatisch aus der Bridge (Admin › Räume)
       d) FESTE_WOERTER unten (Dienste, Gerätetypen)         → hier im Code
       e) Interpreten der Musiksammlung (zuletzt gespielte zuerst, bis ~220 Zeichen) → automatisch
       f) ZULETZT per Sprache gespielt: Interpreten + Titel, auch Qobuz/Radio (data/wortschatz_zuletzt.json, die
          letzten 30, bis ~160 Zeichen) → automatisch (merke() nach jedem erfolgreichen Abspielen). Steht am Ende, weil
          Whisper das Ende am stärksten beachtet: „Believer“ einmal gefunden → beim nächsten Mal nicht „beliebter“.
     Whisper behält höchstens die LETZTEN ~220 Wortbausteine – was vorne übersteht, fällt weg. Deshalb sind e) und f)
     begrenzt, damit Wake-Phrase und Räume immer drin bleiben.
     Canary und Parakeet kennen keinen Hinweistext – für sie helfen nur die Korrekturen (2).
  2. KORREKTUREN (nach dem Erkennen) – gelten für JEDE Erkennung: verhörte Wörter durch das richtige ersetzen.
       KORREKTUREN   – feste Schreibweisen (regulärer Ausdruck → Ersatz)
       KLANG         – Wörter, die wie ein Begriff KLINGEN (Kölner Phonetik), z. B. alle „Kobus/Quobus/Gobus“ → „Qobuz“
       WAKE_DEUTSCH  – wie Canary „Jarvis“ eindeutscht („Ja, das“, „Ich habe das“) → wird am Satzanfang abgeschnitten
     Raumnamen korrigiert voice.fix_rooms() automatisch (unscharf gegen die echten Räume, kein Pflegeaufwand).

Neues Wort? → meistens reicht Admin › Spracherkennung › Hinweistext (b). Kommt ein Begriff trotzdem regelmäßig falsch
an (im Log unter „transcript“ sichtbar): Schreibweise in KORREKTUREN eintragen oder – wenn er sehr unterschiedlich falsch
ankommt – in KLANG. Danach c4voice neu starten (Admin › Sprache › Neustart) und tools/t_wortschatz.py laufen lassen.
"""
from __future__ import annotations

import re

# d) Feste Wörter für den Hinweistext: Dienste und Gerätetypen, die Whisper sonst falsch schreibt
FESTE_WOERTER = "Dienste: Qobuz, TuneIn, Radio. Geräte: Rollladen, Rollos, Jalousie, Thermostat, Mediathek."

# Korrekturen: (regulärer Ausdruck, Ersatz). Groß-/Kleinschreibung egal, nur ganze Wörter (\b).
KORREKTUREN: list[tuple[str, str]] = [
    # Qobuz – Schreibweisen aus Handy-Tests (Whisper, Parakeet, Canary)
    (r"\b(kubos|cobus|kobus|qubos|kobuz|cobuz|cobos|kobos|qbus|q-bus|kuboss|cubus|kokuz|kuburz|koboz|kubuz|kobuss|"
     r"bobus|bobuz|coboz|quos)\b", "Qobuz"),
    # TuneIn
    (r"\b(tune ?in|tjun ?in|tjunin|tunin)\b", "TuneIn"),
]

# Klang-Korrekturen: (Muster für Kandidaten-Wörter, Kölner-Phonetik-Code, Ersatz). Nur Wörter, die zum Muster passen,
# werden geprüft – sonst würden zu viele gewöhnliche Wörter getroffen („Kapuze“ klingt auch wie Qobuz, endet aber auf -e).
KLANG: list[tuple[str, str, str]] = [
    (r"\b[KkQqCcGg][a-zäöü]{2,5}[sztß]\b", "418", "Qobuz"),     # Quobus, Kobus, Gobus, Koboz, Kobutz …
]

# Eingedeutschte Wake-Phrase am Satzanfang (Canary mit Sprache Deutsch schreibt „Jarvis“ so) – wird abgeschnitten
WAKE_DEUTSCH = r"^\s*(ja,? das|ich hab(e)? das|hey,? das|hi,? das)\b[.,!?]?\s*"


def merke(path, names, limit: int = 30) -> None:
    """f) Namen (Interpret, Titel, Sender) nach erfolgreichem Abspielen vorn in die Zuletzt-Liste."""
    import json
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        old = []
    new = []
    for n in list(names) + list(old):
        n = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", str(n or "")).strip(" „“\"")   # „(Live)“, „(Remastered)“ weg
        if len(n) >= 2 and not n.isdigit() and n.lower() not in {x.lower() for x in new}:
            new.append(n)
    try:
        path.write_text(json.dumps(new[:limit], ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def zuletzt(path) -> list:
    import json
    try:
        return list(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return []


def _bis(names, chars: int) -> list:
    out, n = [], 0
    for a in names:
        if n + len(a) > chars:
            break
        out.append(a)
        n += len(a) + 2
    return out


def build_hint(wake: str | None, own: str | None, rooms: list | None, artists: list | None,
               recent: list | None = None, artists_chars: int = 220, recent_chars: int = 160) -> str:
    """Hinweistext für Whisper (siehe oben: a–e)."""
    parts = []
    if wake:
        parts.append(f"{wake},")
    if own and own.strip():
        parts.append(own.strip())
    if rooms:
        parts.append("Räume: " + ", ".join(str(x) for x in rooms[:30]) + ".")
    parts.append(FESTE_WOERTER)
    if artists:
        parts.append("Musik: " + ", ".join(_bis(artists, artists_chars)) + ".")
    if recent:
        parts.append("Zuletzt: " + ", ".join(_bis(recent, recent_chars)) + ".")
    return " ".join(parts).strip()


def correct(text: str) -> str:
    """Korrekturen + Klang-Korrekturen auf einen erkannten Satz anwenden."""
    from .media import koelner
    for pat, rep in KORREKTUREN:
        text = re.sub(pat, rep, text, flags=re.I)
    for pat, code, rep in KLANG:
        text = re.sub(pat, lambda m: rep if koelner(m.group(0).lower()) == code else m.group(0), text)
    return text


def strip_wake_deutsch(raw: str) -> str | None:
    """Rest des Satzes ohne eingedeutschte Wake-Phrase – oder None, wenn keine am Anfang steht."""
    m = re.match(WAKE_DEUTSCH, raw or "", re.I)
    return raw[m.end():].strip() if m else None
