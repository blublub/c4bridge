"""Dialogzustand je Mikrofon: EIN Ort für „Was gehört zum aktuellen Gespräch?“ – statt Verlaufs-Regeln in jedem Werkzeug.

Vorher ging 30 s lang der ganze Chatverlauf ans Sprachmodell. Ein 8B-Modell bewertet dann keinen Themenwechsel, es setzt
das Gespräch fort (Handy-Test 18.10.: „Schreibtischlampe aus“ → 14 s später „Rollos zu“ → blind_action(device=
„Schreibtisch“)). Jetzt:

  1. Chatverlauf NUR bei offener Rückfrage („Welches Licht?“, „Meinst du …?“) – das ist der echte Dialog.
  2. Sonst steht die letzte Aktion als FAKT in der Sprecher-Notiz („zuletzt vor 14 s: Licht „Schreibtisch“ aus“) –
     aber nur, wenn der neue Satz nicht erkennbar ein anderes Thema hat (Gerätetyp-Wörter, nicht Gerätenamen) oder
     ausdrücklich zurückverweist („wieder“, „auch“, „nochmal“ …).
  3. Für die Prüfungen in execute_tool („kommt das Gerät / der Raum im Satz vor?“) zählt derselbe Ausschnitt: aktueller
     Satz + offene Rückfrage + ggf. die letzte Aktion. Ein Gerät aus einem abgeschlossenen anderen Thema fällt durch.
"""
from __future__ import annotations

import re
import time

QUESTION_S = 90.0       # offene Rückfrage / Angebote: auch „Hey Jarvis, ja“ eine Weile später gilt noch
CONTEXT_S = 45.0        # letzte Aktion als Notiz (ab Ende der Ansage)

# Gerätetyp-Wörter → Themenbereich. Licht und Schalter zusammen („Schreibtischlampe“ ist ein Schalter).
# Je Bereich: (Wortstämme, die auch IN zusammengesetzten Wörtern zählen – „Schreibtischlampe“, „Radiosender“,
#              Wörter, die nur als ganzes Wort zählen – „Rolle“ nicht in „Kontrolle“, „Band“ nicht in „Verband“)
_DOMAINS = {
    "licht": (r"lampe|licht|leuchte|beleuchtung|dimm|steckdose|pumpe|ventilator|schalter|relais|lamp|light",
              r"heller|dunkler|brighter|darker|switch|sockets?|fans?"),
    "rollladen": (r"rollo|rollladen|rolladen|rollläden|rolläden|jalousie|markise|raffstore|beschattung|verschattung|"
                  r"shutter|awning", r"rolle|rollen|blinds?"),
    "heizung": (r"heiz|thermostat|temperatur|klima|heating",
                r"grad|warm|kalt|wärmer|kälter|degrees?|warmer|colder|cold|heat"),
    "medien": (r"musik|radio|sender|playlist|hörspiel|lautstärke|music|volume",
               r"lied(er|es)?|songs?|titel|album|alben|interpret(en|in)?|band|folge|qobuz|tunein|lauter|leiser|pause|"
               r"pausier(e|en|t)?|spiel(e|en|t|st)?|hör(e|en|t|st)?|nächste[nrs]?|vorherige[nrs]?|"
               r"track|artist|episode|louder|quieter|play(ing)?|next|previous|stations?"),
    "tv": (r"fernseh|television", r"tv|filme?s?|serien?|staffel|episoden?|mediathek|movies?|series|season"),
    "wetter": (r"wetter|weather", r"regen|regnet|regnen|sonnig|vorhersage|schnee|rain|raining|sunny|forecast|snow|umbrella"),
}
# Stamm am Wortanfang („Lichtschalter“) oder als Grundwort hinter mindestens 3 Buchstaben („Steh|lampe“, „Heizungs|pumpe“,
# „Radio|sender“) – nicht in beliebigen Wörtern („Pf|licht“ ist kein Licht)
_DOMAIN_RX = {k: re.compile(r"\b(\w{3,})?(" + stems + r")\w*\b|\b(" + words + r")\b", re.I)
              for k, (stems, words) in _DOMAINS.items()}

# Sammelwörter je Bereich: nur dann darf ein vom Modell erfundenes Gerät durch „alle im Raum“ ersetzt werden
# („Mach die Rollos zu“, „Licht aus“, „alles zu“) – NICHT bei „Heizungspumpe“, „die Lampe“ (ein bestimmtes Gerät)
_GENERIC = {
    "licht": re.compile(r"\b(licht|lichter|lampen|beleuchtung|alles|alle|lights?|lamps|lighting|all|everything)\b", re.I),
    "rollladen": re.compile(r"\b(rollos?|rollläden|rollladen|rolladen|rolläden|jalousien?|rollen?|markisen?|"
                            r"beschattung|verschattung|alles|alle|blinds?|shutters?|shades?|all|everything)\b", re.I),
}

_TOOL_DOMAIN = {
    "light_set": "licht", "light_adjust": "licht", "switch_set": "licht",
    "blind_action": "rollladen",
    "climate_setpoint": "heizung", "climate_setpoint_delta": "heizung", "get_temperature": "heizung",
    "lights_status": "licht",
    "music_play": "medien", "stream_play": "medien", "volume_set": "medien", "volume_adjust": "medien",
    "media_control": "medien", "rooms_playing": "medien", "room_off": "medien",
    "music_albums": "medien",
    "video_play": "tv",
    "weather": "wetter",
}

# Rückverweis auf das eben Gesagte/Getane – dann gilt die letzte Aktion auch bei anderem Gerätetyp-Wort
_REFERS = re.compile(r"\b(wieder|auch|ebenfalls|genauso|nochmal|noch mal|noch einmal|zurück|dort|da|dasselbe|"
                     r"das gleiche|den gleichen|die gleiche|es|ihn|sie|again|also|too|as well|there|the same|it|them)\b",
                     re.I)


# Für repeat_for_room: Werkzeuge, die raumweit sinnvoll wiederholbar sind (switch_set braucht immer einen Namen)
_ROOM_TOOLS = {"light_set", "light_adjust", "blind_action", "climate_setpoint", "climate_setpoint_delta",
               "get_temperature", "lights_status", "music_play", "stream_play", "video_play", "volume_set", "volume_adjust",
               "media_control", "rooms_playing", "room_off"}
_FILLER = ("und", "im", "in", "in der", "der", "die", "das", "dem", "den", "beim", "bei", "auf", "auch", "da", "dort",
           "mal", "bitte", "wie", "ist", "es", "was", "mit", "jetzt", "noch", "ebenfalls", "genauso",
           "and", "in the", "the", "please", "also", "too", "there", "now", "what about", "how about")


def _without_rooms(text: str, rooms: list[str] | None) -> str:
    """Raumnamen raus („Spielzimmer“, „Heizungsraum“ sind Räume, keine Gerätetypen)."""
    t = text or ""
    for r in rooms or []:
        for part in [r, *re.split(r"\s*/\s*", r)]:
            if part.strip():
                t = re.sub(r"\b" + re.escape(part.strip()) + r"\b", " ", t, flags=re.I)
    return t


def domains_of(text: str, rooms: list[str] | None = None) -> set[str]:
    """Themenbereiche, die der Satz über Gerätetyp-Wörter erkennbar nennt (leer = keiner erkennbar)."""
    t = _without_rooms(text, rooms)
    return {k for k, rx in _DOMAIN_RX.items() if rx.search(t)}


def generic_named(domain: str, text: str, rooms: list[str] | None = None) -> bool:
    """Nennt der Satz den Gerätetyp als Sammelwort („die Rollos“, „das Licht“, „alles“)?"""
    rx = _GENERIC.get(domain)
    return bool(rx and rx.search(_without_rooms(text, rooms)))


def refers_back(text: str) -> bool:
    return bool(_REFERS.search(text or ""))


class Dialog:
    """Zustand eines Mikrofons. begin() vor jedem Satz, end() danach."""

    def __init__(self) -> None:
        self.turns: list[dict] = []        # Chatverlauf der OFFENEN Rückfrage (user/assistant)
        self.offers: list[dict] = []       # feste Angebote der offenen Rückfrage
        self.question_until = 0.0
        self.last: dict | None = None      # {text, answer, domains, at, calls}
        self.last_until = 0.0
        self.rooms: list[str] = []         # Raumnamen (von process_utterance gesetzt) – zählen nicht als Gerätetyp

    # ---- vor dem Satz -------------------------------------------------------------------------------------------
    def open_question(self, now: float | None = None) -> bool:
        return bool(self.turns) and (now or time.time()) < self.question_until

    def history(self, now: float | None = None) -> list[dict]:
        return list(self.turns) if self.open_question(now) else []

    def pending(self, now: float | None = None) -> list[dict]:
        return list(self.offers) if self.open_question(now) else []

    def begin(self, text: str, now: float | None = None) -> bool:
        """Vor dem Satz: Nennt die Antwort auf eine offene Rückfrage erkennbar einen ANDEREN Gerätetyp als die Frage
        („Möchtest du die Schreibtischlampe ausschalten?“ → „Mach die Rollos zu“), ist es ein neuer Befehl – Rückfrage
        und Angebote verfallen. „Ja“, „Kim“, „Folge 104“ nennen keinen Typ bzw. denselben → die Frage bleibt offen.
        Rückgabe: True = Thema gewechselt."""
        if not self.open_question(now):
            return False
        said = domains_of(text, self.rooms)
        asked = domains_of(" ".join(h.get("content", "") for h in self.turns), self.rooms)
        if said and asked and not (said & asked):
            self.turns, self.offers, self.question_until = [], [], 0.0
            return True
        return False

    def recent(self, text: str, now: float | None = None, strict: bool = False) -> dict | None:
        """Letzte Aktion, wenn sie zum neuen Satz passen kann: noch frisch UND (kein erkennbar anderer Gerätetyp ODER
        ausdrücklicher Rückverweis). Bei offener Rückfrage nicht – dann trägt der Chatverlauf.
        strict (für die Geräteprüfung): ein Rückverweis allein reicht nicht – „Und die Rollos auch“ nach
        „Schreibtischlampe aus“ darf die Aktion als Notiz sehen, aber kein Licht-Gerät auf die Rollläden übertragen."""
        now = now or time.time()
        if not self.last or now >= self.last_until or self.open_question(now):
            return None
        said = domains_of(text, self.rooms)
        if said and self.last["domains"] and not (said & self.last["domains"]) and (strict or not refers_back(text)):
            return None                                # Themenwechsel („Rollos zu“ nach „Schreibtischlampe aus“)
        return self.last

    def note(self, text: str, now: float | None = None) -> str:
        """Zusatz für die Sprecher-Notiz: „zuletzt vor 14 s: …“ (oder leer)."""
        now = now or time.time()
        r = self.recent(text, now)
        if not r:
            return ""
        ago = max(1, round(now - r["at"]))
        return f"zuletzt vor {ago} s: „{r['text']}“ → {r['answer'][:160]}"

    def said(self, text: str, now: float | None = None) -> str:
        """Text für die Prüfungen in execute_tool (Raum/Gerät im Satz?): aktueller Satz + offene Rückfrage + ggf. die
        passende letzte Aktion („Mach sie wieder an“ → Gerät von eben gilt)."""
        parts = [h.get("content", "") for h in self.history(now) if h.get("role") == "user"]
        r = self.recent(text, now, strict=True)
        if r:
            parts += [r["text"], r["answer"]]
        return " ".join(parts + [text])

    def repeat_for_room(self, text: str, rooms: list[dict], now: float | None = None) -> list[dict] | None:
        """„Und im Wohnzimmer?“ / „Im Bad auch“: Satz besteht nur aus Füllwörtern + EINEM Raum → letzte Aktion dort
        wiederholen (gleiches Werkzeug, neuer Raum, ohne Gerätenamen). Das Modell griff sonst zu „Was läuft“.
        Nur Werkzeuge mit Raum-Parameter; Schalter nicht (die gibt es nur einzeln mit Namen)."""
        r = self.recent(text, now)
        if not r or not r.get("calls"):
            return None
        t = " " + " ".join(re.sub(r"[^\wäöüß ]+", " ", (text or "").lower()).split()) + " "
        for w in _FILLER:
            t = t.replace(f" {w} ", " ").replace(f" {w} ", " ")
        t = t.strip()
        if not t:
            return None
        def norm(n: str) -> str:
            return " ".join(re.sub(r"[^\wäöüß ]+", " ", n.lower()).split())
        # erst ganzer Name/Alias („Küche“ = Raum Küche), dann Namensteile („Esszimmer“ aus „Esszimmer / Küche“)
        full = [x for x in rooms if any(t == norm(n) for n in [x["name"], *(x.get("aliases") or [])] if n)]
        part = [x for x in rooms if any(t == norm(n) for n in re.split(r"\s*/\s*", x["name"]) if n)]
        cand = full or part
        if len(cand) != 1:
            return None                                # keiner oder mehrdeutig → dem Modell überlassen
        hit = cand[0]
        out = []
        for c in r["calls"]:
            if c.get("name") not in _ROOM_TOOLS:
                continue
            args = {k: v for k, v in (c.get("arguments") or {}).items() if k not in ("room", "device")}
            out.append({"name": c["name"], "arguments": {**args, "room": hit["name"]}})
        return out or None

    # ---- nach dem Satz ------------------------------------------------------------------------------------------
    def end(self, text: str, answer: str, tools: list[str], asks: bool, offers: list | None,
            speak_end: float | None = None, calls: list | None = None, ok: bool = True) -> None:
        """Ergebnis eines Satzes eintragen. asks = Antwort ist eine Rückfrage (Mikrofon hört ohne Wake-Word zu).
        ok = alle Werkzeuge erfolgreich; eine fehlgeschlagene Aktion wird nicht per „Und im Wohnzimmer?“ wiederholt."""
        t_end = speak_end or time.time()
        if asks:
            base = self.turns if self.open_question() else []
            self.turns = [*base, {"role": "user", "content": text}, {"role": "assistant", "content": answer}][-6:]
            self.offers = list(offers or [])
            self.question_until = t_end + QUESTION_S
        else:
            dialog_text = " ".join(h["content"] for h in self.turns if h.get("role") == "user")
            self.turns, self.offers, self.question_until = [], [], 0.0
            doms = {_TOOL_DOMAIN[t] for t in tools if t in _TOOL_DOMAIN} or domains_of(f"{dialog_text} {text}", self.rooms)
            self.last = {"text": text, "answer": answer, "domains": doms, "at": time.time(),
                         "calls": list(calls or []) if ok else []}
            self.last_until = t_end + CONTEXT_S

    def reset(self) -> None:
        self.__init__()
