"""c4voice auf Englisch (voice.json → "lang": "en").

Die Bridge erzeugt ihre Rückmeldungen weiter auf Deutsch – voice.py wertet diese Texte an mehreren Stellen selbst aus
(„Licht „…“ an“, „Simulation“, Rückfrage-Erkennung). Übersetzt wird erst an der Ausgabe (to_en): gesprochener Text,
angezeigte Entscheidung, Verlauf für das Sprachmodell. Dazu: englischer Standard-Prompt, Kurzbefehle, Ja/Nein und die
Prüfregeln gegen erfundene Bestätigungen in englischer Fassung.

Neue Rückmeldung in voice.py → hier eine Regel ergänzen (tools/t_dialog_en.py zeigt, was noch deutsch durchrutscht).
"""
import re

# ---- Standard-Anweisungen (Admin › Sprachsteuerung › LLM; leer = diese Fassung) -------------------------------------
HOUSE_PROMPT = """You are the voice assistant of a smart home (Control4). You handle short English sentences and carry out house tasks WITHOUT EXCEPTION through the functions (tools).

Rooms (what each room has – nothing else):
{rooms}

Available devices:
{devices}

Rules:
1. Room: without a room name, the speaker's room ALWAYS applies – only its devices, leave room out. A named room (also in an answer to a follow-up question) goes into room, NEVER into device.
2. Names are often misheard: pick the most similar device or room. If SEVERAL fit about equally (just "office" with two offices), ask instead of guessing: ONE short question with a question mark, at most two options, no tool. Same for garbled or contradictory sentences.
3. If there is no matching device or tool (door lock, alarm system), say so briefly – invent nothing. Answer in one short sentence; after an action, confirm briefly.
4. Lights: "lights on/off" (also with a room) = light_set for ALL lights of the room, WITHOUT device; device only for one specific light. room_off only switches media (music/TV) off, never lights. Type switch (socket, relay, pump, fan – also "…lamp") → switch_set; light_set only for type light.
5. Music from the own collection → music_play. kind: artist (artist/band), album, song, genre (style from the list below), random ("play music" – NEW music), any (unclear). search = the spoken name EXACTLY as heard, even if it sounds wrong – the bridge corrects mishearings; leave nothing out. "Song by artist" → kind song, search = the whole phrase including "by …".
6. Follow-up "Do you mean X?": "Yes" = X; "No, Y" = ONLY Y, never reuse the old name.
7. Radio/stations ("play BBC Radio 2", "switch to Radio Bob", TuneIn = "radio") → stream_play service radio, kind station – always call it, also when switching. "On/from Qobuz" → stream_play service qobuz. Without a service name, music comes from the collection (music_play). Qobuz, radio and the collection are always audio (also audio plays, audiobooks, "episode") – NEVER video_play.
8. Movies/series from the media library → video_play (search = title, kind movie/series/any, season/episode only if named), only in rooms with "movies/series". Netflix/Prime/TV channels cannot be searched – say so briefly.
9. Playback: louder/quieter (+ "a bit"/"a lot") → volume_adjust; "volume to 30" → volume_set; pause/resume/next/previous song/mute/unmute → media_control; "shuffle on" → shuffle_on (not music_play), "shuffle off" → shuffle_off; "repeat this song" → repeat_one, "repeat all" → repeat_all, "repeat off" → repeat_off. "Music off"/"room off" → room_off. NEVER ask for the room here.
10. Questions never switch anything: "What is playing (where)?" → rooms_playing (without room = all rooms); questions about lights → lights_status; weather (today, tomorrow, weekday, "do I need an umbrella") → weather with day ("heute", "morgen", "übermorgen" or the weekday). Pass tool answers on unchanged.
11. Heating/temperature without a room ("two degrees warmer", "how warm is it") → climate_*/get_temperature without device and room – do not ask.
12. General knowledge/news questions → web_search with a short search query, never with device, room or personal data; without the tool, say there is no internet access.

Tool values stay as defined by the tools: strength "klein" (a bit), "mittel" (normal), "gross" (a lot).

Music styles in the collection: {genres}"""

EXAMPLES = """Without a room name, the speaker's room applies (leave room out).
LIGHTS
"Lights on", "Give me some light" → light_set(on=true); "All dark" → light_set(on=false); "Lights on in the living room" → light_set(on=true, room="Living Room")
"Floor lamp to 30 percent" → light_set(device="Floor lamp", on=true, level=30); "Full brightness" → light_set(on=true, level=100)
"Make the light blue", "Kitchen counter warm white" → light_color(color="blue") or light_color(device="Kitchen counter", color="warm white")
"A bit brighter" → light_adjust(direction="up", strength="klein"); "Too dark" → (up, "mittel"); "A bit darker" → (down, "klein"); "Too bright" → (down, "mittel"); "Make it cosy" → (down, "gross")
"Where are lights still on?" → lights_status (questions NEVER switch)
"Lights off in the bathroom and on in the hall" → two calls light_set with room
"Turn off the desk lump" (unknown word) → light_set(device="desk lump", on=false) – the bridge asks back; NEVER room_off
BLINDS
"Blinds down", "Close the shutters", "Close everything" → blind_action(device=<room name>, action="close") – ONLY blinds, no lights, no media; "Blinds up" → action="open"; "Stop the blinds" → "stop"
"Garden blind down" → blind_action(device="Garden", action="close")
"Darken the room", "The sun is blinding" → blinds down – NOT the lights; "Blinds to 50 percent" → no tool: positions are not supported by voice yet
MEDIA
"Play music" → music_play(kind="random"); "Quiet please", "TV off" → room_off()
"Play Coldplay Yellow" → music_play(search="Coldplay Yellow", kind="any") – without a service name, the collection
"Play Rise by Imagine Dragons" → music_play(search="Rise", artist="Imagine Dragons", kind="song")
Note "läuft: Qobuz – Imagine Dragons – …": "What's this song called?" → answer from the note, no tool; "More of this" → same service + artist; "And now Believer" → stream_play(service="qobuz", search="Believer", artist="Imagine Dragons", kind="song")
"Turn on the radio" (no station) → ask "Which station?"; "Off" (alone) → ask "Lights or music?"
"Play The Gruffalo audiobook on Qobuz" → stream_play(service="qobuz", search="The Gruffalo audiobook", kind="any"); "Play the audio play Paddington episode 3" → music_play(search="Paddington episode 3", kind="any")
"Which albums are there by Adele?" → music_albums(artist="Adele") – plays nothing; "Another version of this" → stream_play(service="qobuz", search="", version="andere")
MOVIES
"Start the movie Inception" → video_play(search="Inception", kind="movie"); "Breaking Bad season 2 episode 3" → video_play(search="Breaking Bad", kind="series", season=2, episode=3)"""

EXAMPLES_HEAD = "\n\nExamples (respond like this):\n"
PROMPT_TAIL = ("\n\nBefore each sentence, [square brackets] say which room the speaker is in (\"Sprecher im Raum\"), what "
               "is playing (\"läuft\") and, if any, the previous action (\"zuletzt vor … s\"). What a room has is listed in "
               "the room list above; what is not listed does not exist in that room (then say so briefly). Without a room "
               "name, the devices of THIS room are meant (leave room out); other devices only when another room is named "
               "explicitly. If the user names a device that is not listed, still call the tool with that name (without "
               "room) – the bridge looks it up and asks back itself; do not ask yourself. Use the previous action only when "
               "the sentence refers to it (\"on again\", \"and in the bathroom?\", \"more of this\"); otherwise it is a new "
               "command. A short follow-up without its own verb (\"And in the living room?\", \"And tomorrow?\") means: the "
               "SAME tool as the previous action, only with the new room or day. Relative brightness (\"brighter\", "
               "\"darker\", \"cosier\") → light_adjust. Always answer in English.")
KINDS = {"light": "light", "blind": "blind", "thermostat": "heating", "switch": "switch"}
MEDIA = {"Musik": "music", "Radio": "radio", "Qobuz": "Qobuz", "TV/Video": "TV/video", "Filme/Serien": "movies/series"}

SUMMARY_PROMPT = ("You are a voice assistant. Answer the question in at most two short English sentences, only from the "
                  "search results below. If they do not answer it, say so briefly. No lists, no links, no sources.")

NUDGE = ("ATTENTION: your last answer confirmed an action but did NOT call a tool – then nothing happens in the house. "
         "Call the matching tool now. If it is unclear what is meant, ask a short follow-up question.")

# ---- Eingaben (alles kleingeschrieben, Satzzeichen entfernt) ----------------------------------------------------------
YES = re.compile(r"(yes|yeah|yep|yup|sure|exactly|right|correct|okay|ok|please|do it|go ahead|that one|that s it|"
                 r"that s the one|play it|play that)( (yes|please|thanks|thank you|do it|that one|exactly|right))*")
NO = re.compile(r"(no|nope|nah|not)\b")
ACK = re.compile(r"(ok|okay|fine|good|great|perfect|cool|nice|thanks|thank you|thanks a lot|thank you very much|cheers)"
                 r"( thanks| thank you| you)?")
CANCEL = re.compile(r"(no|nope|nah|no thanks|no thank you|cancel|forget it|never mind|nevermind|leave it|stop|nothing|"
                    r"not now|don t)( thanks| thank you)?")
WRONG = re.compile(r"(no )?(that s|that is) (not it|wrong|not what i meant)|(no )?(i )?(didn t|did not) mean (that|this|it)|"
                   r"wrong|that s wrong")
ASKS = re.compile(r"\b(which|do you mean|tell me|what should)\b")
QUESTION = re.compile(r"^\W*(is|are|was|were|what|where|which|do we have|have we got|is there|are there|any)\b", re.I)
REQUEST = re.compile(r"\b(play|turn|switch|set|start|put on|make)\b", re.I)
FILLER = ("please", "now", "just", "can you", "could you")
_V = {"direction": "up", "strength": "mittel"}
QUICK = {
    "pause": ("media_control", {"action": "pause"}), "pause the music": ("media_control", {"action": "pause"}),
    "pause music": ("media_control", {"action": "pause"}), "stop": ("media_control", {"action": "stop"}),
    "stop the music": ("media_control", {"action": "stop"}), "resume": ("media_control", {"action": "play"}),
    "continue": ("media_control", {"action": "play"}), "play": ("media_control", {"action": "play"}),
    "next": ("media_control", {"action": "next"}), "next song": ("media_control", {"action": "next"}),
    "next track": ("media_control", {"action": "next"}), "skip": ("media_control", {"action": "next"}),
    "skip this": ("media_control", {"action": "next"}), "i don t like this": ("media_control", {"action": "next"}),
    "previous": ("media_control", {"action": "previous"}), "previous song": ("media_control", {"action": "previous"}),
    "last song": ("media_control", {"action": "previous"}), "mute": ("media_control", {"action": "mute"}),
    "unmute": ("media_control", {"action": "unmute"}), "sound on": ("media_control", {"action": "unmute"}),
    "louder": ("volume_adjust", dict(_V)), "volume up": ("volume_adjust", dict(_V)),
    "turn it up": ("volume_adjust", dict(_V)), "quieter": ("volume_adjust", {**_V, "direction": "down"}),
    "volume down": ("volume_adjust", {**_V, "direction": "down"}),
    "turn it down": ("volume_adjust", {**_V, "direction": "down"}),
    "blinds down": ("blind_action", {"action": "close"}), "close the blinds": ("blind_action", {"action": "close"}),
    "shutters down": ("blind_action", {"action": "close"}), "close the shutters": ("blind_action", {"action": "close"}),
    "blinds up": ("blind_action", {"action": "open"}), "open the blinds": ("blind_action", {"action": "open"}),
    "shutters up": ("blind_action", {"action": "open"}), "open the shutters": ("blind_action", {"action": "open"}),
    "what s playing": ("rooms_playing", {}), "what is playing": ("rooms_playing", {}),
    "where is music playing": ("rooms_playing", {}),
}
# Wie voice._NO_TOOL: Sätze ohne passendes Werkzeug → feste (deutsche) Antwort, an der Ausgabe übersetzt
NO_TOOL = [
    (re.compile(r"\b(blinds?|shutters?)\b.*\b(half|halfway|quarter|\d+\s*(percent|%))"),
     "Rollläden kann ich per Sprache nur ganz hoch, ganz runter oder anhalten – Positionen gehen noch nicht."),
    (re.compile(r"\b(tv|television|telly|projector)\b.*\b(on|start)\b|\b(turn|switch)\b.*\b(tv|television)\b.*\bon\b"),
     "Den Fernseher kann ich per Sprache noch nicht einschalten – bitte über die Control4-App oder die Fernbedienung."),
    (re.compile(r"\b(netflix|prime video|amazon prime|disney plus|disney\+|youtube|tv channel|news on tv)\b"),
     "Netflix, Prime und Fernsehsender kann ich per Sprache nicht durchsuchen – Filme und Serien aus der Mediathek schon."),
]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# ---- Prüfregeln gegen erfundene Bestätigungen (wie voice._CLAIMS_DONE usw.) --------------------------------------------
CLAIMS_DONE = re.compile(
    r"^(Radio |Qobuz: |Playing |Random: |Movie |Series |Lights? |\d+ lights? |Volume |Room .+ off|Blinds? |Thermostat .+ to |"
    r"[“\"][^”\"]+[”\"] (on|off)|[^:?]{2,40}: (paused|resumed|next song|previous song|stopped|muted|sound on|shuffle|repeat)|"
    r"[\w“”\" ./-]{2,40} (is |are )?(now )?(on|off|closed|open|opened|dimmed)[.!]?$)", re.I)
CLAIMS_STATE = re.compile(r"\b(is|are|s|re) (already|now|currently|still) (on|off|open|closed|dimmed|brighter|darker|louder|"
                          r"quieter|playing|switched on|switched off|turned on|turned off)\b|\b\d+([.,]\d+)?\s*(°\s*c?|degrees)\b",
                          re.I)
ANNOUNCES = re.compile(r"^((okay|ok|sure|alright|all right|one moment)[,.!]?\s+)?(i( ll| will| am going to| m going to)|let me)\s+"
                       r"(get|check|look|see|turn|switch|play|start|set|search|find|read|close|open|dim|measure)\b", re.I)
DONE_CLAIM = re.compile(r"\bi( ve| have)\b.{0,80}\b(turned|switched|started|set|played|opened|closed|dimmed|activated|"
                        r"deactivated|done|lowered|raised|paused|stopped)\b", re.I)
NEGATION = re.compile(r"\b(no|not|n t|unfortunately|sorry|can t|cannot)\b", re.I)


def plain(text: str) -> str:
    """Für die englischen Prüfregeln: Apostroph als Leerzeichen („I'll“ → „i ll“), wie bei den Eingaben."""
    return (text or "").replace("’", "'").replace("'", " ")


# ---- Ausgabe: deutsche Rückmeldung → Englisch ---------------------------------------------------------------------
SIM_DE, SIM_EN = " (Simulation – nichts ausgeführt)", " (simulation – nothing executed)"
_STATE = {"an": "on", "aus": "off", "heller": "brighter", "gedimmt": "dimmed"}
_THING = {"kein licht": "no light", "keinen schalter": "no switch", "keinen rollladen": "no blind",
          "kein thermostat": "no thermostat"}
_WHAT = {"die Serie": "the series", "den Film": "the movie"}
_MEDIA_WORD = {"pausiert": "paused", "läuft weiter": "resumed", "nächstes Lied": "next song",
               "vorheriges Lied": "previous song", "gestoppt": "stopped", "stumm": "muted", "Ton wieder an": "sound on again",
               "Zufallswiedergabe an": "shuffle on", "Zufallswiedergabe aus": "shuffle off",
               "Lied wird wiederholt": "repeating this song", "alles wird wiederholt": "repeating everything",
               "Wiederholen aus": "repeat off"}
_PLAYSTATE = {"läuft": "playing", "pausiert": "paused", "gestoppt": "stopped", "an": "on"}
_BLIND = {"fährt auf": "opening", "fährt zu": "closing", "fahren auf": "opening", "fahren zu": "closing",
          "gestoppt": "stopped"}
WMO = {"klar": "clear", "überwiegend klar": "mostly clear", "teils bewölkt": "partly cloudy", "bedeckt": "overcast",
       "Nebel": "fog", "Nebel mit Reif": "freezing fog", "leichter Nieselregen": "light drizzle", "Nieselregen": "drizzle",
       "starker Nieselregen": "heavy drizzle", "gefrierender Nieselregen": "freezing drizzle", "leichter Regen": "light rain",
       "Regen": "rain", "starker Regen": "heavy rain", "gefrierender Regen": "freezing rain", "leichter Schneefall": "light snow",
       "Schneefall": "snow", "starker Schneefall": "heavy snow", "Schneegriesel": "snow grains", "Regenschauer": "rain showers",
       "kräftige Regenschauer": "heavy rain showers", "heftige Regenschauer": "violent rain showers",
       "Schneeschauer": "snow showers", "kräftige Schneeschauer": "heavy snow showers", "Gewitter": "thunderstorm",
       "Gewitter mit Hagel": "thunderstorm with hail", "wechselhaft": "changeable"}
_DAY = {"heute": "Today", "morgen": "Tomorrow", "übermorgen": "The day after tomorrow", "montag": "Monday",
        "dienstag": "Tuesday", "mittwoch": "Wednesday", "donnerstag": "Thursday", "freitag": "Friday",
        "samstag": "Saturday", "sonntag": "Sunday"}


def _list(s: str, word: str = "and") -> str:
    """Aufzählung „A, B und C“ → „A, B and C“ (nur der letzte Trenner – Namen mit „und“ bleiben sonst heil)."""
    sep = " und " if word == "and" else " oder "
    return f" {word} ".join(s.rsplit(sep, 1)) if sep in s else s


def _inner(s: str) -> str:
    """Bruchstücke in Platzhaltern: „X“ von Y, Zustände in Gerätelisten."""
    s = re.sub(r"(„[^“]+“) von ", r"\1 by ", s)
    s = re.sub(r"(„[^“]+“) (an|aus)\b", lambda m: f"{m[1]} {_STATE[m[2]]}", s)
    s = re.sub(r"\((schon voll an)\)", "(already fully on)", s)
    s = re.sub(r"\((schon ganz dunkel)\)", "(already as dark as it gets)", s)
    s = re.sub(r"\(aus\)", "(off)", s)
    return s.replace("auf Qobuz suchen", "search Qobuz")


def _num(s: str) -> str:
    return s.replace(",", ".")


def _weather(m) -> str:
    """Satz aus assist.weather (deutsch) → englisch, Teil für Teil."""
    parts = [p.strip() for p in m[0].rstrip(".").split(", ")]
    out, i = [], 0
    while i < len(parts):
        p = parts[i]
        g = re.fullmatch(r"Gerade( in (.+?))? (-?[\d,]+) Grad und (.+?)\. Heute (.+)", p)
        h = re.fullmatch(r"(\w+)( in (.+?))?: (.+)", p)
        if g:
            out.append(f"Right now{' in ' + g[2] if g[2] else ''} it is {_num(g[3])} degrees and {WMO.get(g[4], g[4])}. "
                       f"Today {WMO.get(g[5], g[5])}")
        elif h and h[1].lower() in _DAY:
            out.append(f"{_DAY[h[1].lower()]}{' in ' + h[3] if h[3] else ''}: {WMO.get(h[4], h[4])}")
        elif re.fullmatch(r"(-?[\d,]+) bis (-?[\d,]+) Grad", p):
            a, b = re.fullmatch(r"(-?[\d,]+) bis (-?[\d,]+) Grad", p).groups()
            out.append(f"{_num(a)} to {_num(b)} degrees")
        elif re.fullmatch(r"Regenrisiko (\d+) Prozent", p):
            out.append(p.split()[1] + " percent chance of rain")
        elif re.fullmatch(r"ab etwa (\d+) Uhr", p):
            out.append("from about " + p.split()[2] + " o'clock")
        elif p == "trocken":
            out.append("dry")
        elif p == "windig" and i + 1 < len(parts) and parts[i + 1].startswith("Böen bis"):
            v = re.search(r"([\d,]+)", parts[i + 1])
            out.append(f"windy, gusts up to {_num(v[1]) if v else '?'} kilometres per hour")
            i += 1
        else:
            out.append(WMO.get(p, p))
        i += 1
    return ", ".join(out) + "."


EXACT = {
    "Dazu finde ich kein passendes Gerät – wie heißt es genau?": "I can't find a matching device – what exactly is it called?",
    "Den Lichtzustand kann ich gerade nicht lesen.": "I can't read the light status right now.",
    "Ja, da ist noch Licht an.": "Yes, a light is still on there.",
    "Das klang nach einer Frage – soll ich etwas spielen?": "That sounded like a question – shall I play something?",
    "Das klang nach einer Frage – ich habe nichts geschaltet.": "That sounded like a question – I haven't switched anything.",
    "Ohne Raumzuordnung des Mikrofons und ohne Raumnennung weiß ich nicht, welches Licht gemeint ist.":
        "The microphone has no room and you didn't name one, so I don't know which light you mean.",
    "Ohne Raumzuordnung des Mikrofons und ohne Raumnennung weiß ich nicht, welche Lichter gemeint sind.":
        "The microphone has no room and you didn't name one, so I don't know which lights you mean.",
    "Schon ganz dunkel.": "Already as dark as it gets.", "Schon voll hell.": "Already at full brightness.",
    "Da ist nichts zu ändern.": "Nothing to change there.",
    "In welchem Raum? (Das Mikrofon ist keinem Raum zugeordnet.)": "Which room? (The microphone has no room assigned.)",
    "In unserer Sammlung kann ich nicht nach anderen Versionen suchen – das geht nur über Qobuz.":
        "I can't search for other versions in our collection – that only works on Qobuz.",
    "Von welchem Interpreten?": "By which artist?",
    "Gerade läuft kein Titel – von welchem Lied soll ich eine andere Version suchen?":
        "Nothing is playing right now – which song should I find another version of?",
    "Welchen Sender?": "Which station?", "Was soll ich auf Qobuz spielen?": "What shall I play on Qobuz?",
    "Welchen Film oder welche Serie?": "Which movie or series?",
    "Die Mediathek ist gerade nicht erreichbar.": "The media library is not reachable right now.",
    "Welche Lautstärke (0–100)?": "Which volume (0–100)?",
    "Für das Wetter ist noch kein Ort hinterlegt.": "No location is set for the weather yet.",
    "Der Wetterdienst ist gerade nicht erreichbar.": "The weather service is not reachable right now.",
    "Keine Wetterdaten.": "No weather data.", "Wonach soll ich suchen?": "What should I search for?",
    "Die Websuche ist nicht eingerichtet.": "Web search is not set up.",
    "Die Websuche ist gerade nicht erreichbar.": "Web search is not reachable right now.",
    "Dazu konnte ich nichts Sicheres finden.": "I couldn't find anything reliable on that.",
    "Den Wiedergabe-Status kann ich gerade nicht lesen.": "I can't read the playback status right now.",
    "Gerade läuft in keinem Raum etwas.": "Nothing is playing in any room right now.",
    "Was soll ich ausschalten – das Licht, die Musik oder ein bestimmtes Gerät?":
        "What should I turn off – the lights, the music or a specific device?",
    "Welcher Raum soll ausgeschaltet werden? (Das Mikrofon ist keinem Raum zugeordnet.)":
        "Which room should be turned off? (The microphone has no room assigned.)",
    "Rollläden kann ich per Sprache nur ganz hoch, ganz runter oder anhalten – Positionen gehen noch nicht.":
        "By voice I can only move the blinds fully up, fully down or stop them – positions are not supported yet.",
    "Den Fernseher kann ich per Sprache noch nicht einschalten – bitte über die Control4-App oder die Fernbedienung.":
        "I can't turn the TV on by voice yet – please use the Control4 app or the remote.",
    "Netflix, Prime und Fernsehsender kann ich per Sprache nicht durchsuchen – Filme und Serien aus der Mediathek schon.":
        "I can't search Netflix, Prime or TV channels by voice – movies and series from the media library work.",
    "Okay, dann nicht.": "Okay, never mind.", "Okay – wie heißt es genau?": "Okay – what exactly is it called?",
    "Das habe ich nicht verstanden – sag es bitte noch einmal.": "I didn't understand that – please say it again.",
    "Das habe ich nicht ausgeführt – sag es bitte noch einmal.": "I didn't carry that out – please say it again.",
    "Ich habe nichts verstanden.": "I didn't catch anything.", "Moment, ich schaue nach.": "One moment, I'll look it up.",
    "Gern.": "You're welcome.", "Was soll ich tun?": "What should I do?", "Verstanden.": "Understood.",
    "Aus": "Off", "Licht an": "Light on", "Licht aus": "Light off",
}

R = re.compile
RULES = [
    # Geräte suchen (pick)
    (R(r"(Kein Licht|Keinen Schalter|Keinen Rollladen|Kein Thermostat) namens „(.+?)“ gefunden\."),
     lambda m: f"{_THING[m[1].lower()].capitalize()} called “{m[2]}” found."),
    (R(r"In (.+?) gibt es (kein Licht|keinen Schalter|keinen Rollladen|kein Thermostat) „(.+?)“ – meinst du „(.+?)“ in (.+?)\?"),
     lambda m: f"There is {_THING[m[2].lower()]} “{m[3]}” in {m[1]} – do you mean “{m[4]}” in {m[5]}?"),
    (R(r"In (.+?) gibt es (kein Licht|keinen Schalter|keinen Rollladen|kein Thermostat) „(.+?)“ – wie heißt es genau\?"),
     lambda m: f"There is {_THING[m[2].lower()]} “{m[3]}” in {m[1]} – what exactly is it called?"),
    (R(r"Welches – (.+)\?"), lambda m: f"Which one – {_list(m[1], 'or')}?"),
    (R(r"Welches Thermostat – (.+)\?"), lambda m: f"Which thermostat – {_list(m[1], 'or')}?"),
    (R(r"Welchen Rollladen – alle oder (.+)\?"), lambda m: f"Which blind – all of them or {_list(m[1], 'or')}?"),
    # Licht
    (R(r"Nein, (in (.+)|im Haus) ist alles aus\."),
     lambda m: f"No, everything is off {'in ' + m[2] if m[2] else 'in the house'}."),
    (R(r"Ja, noch in (.+)\."), lambda m: f"Yes, still in {_list(m[1])}."),
    (R(r"(In (.+)|Im Haus) ist alles aus\."), lambda m: f"Everything is off {'in ' + m[2] if m[2] else 'in the house'}."),
    (R(r"In (.+?) brennt: (.+)\."), lambda m: f"On in {m[1]}: {_list(m[2])}."),
    (R(r"Soll ich „(.+)“ spielen\?"), r"Shall I play “\1”?"),
    (R(r"In (.+?) gibt es keine Lichter\."), r"There are no lights in \1."),
    (R(r"Den Lichtzustand in (.+?) kann ich gerade nicht lesen\."), r"I can't read the light status in \1 right now."),
    (R(r"Die Lichter in (.+?) sind schon aus\."), r"The lights in \1 are already off."),
    (R(r"Licht („.+?“) auf (.+)"), r"Light \1 set to \2"),
    (R(r"(\d+) Lichter in (.+?) auf (.+)"), r"\1 lights in \2 set to \3"),
    (R(r"In (.+?) gibt es kein Licht, das Farbe kann\."), r"There is no colour light in \1."),
    (R(r"Die Farbe „(.+?)“ kenne ich nicht\."), r"I don't know the colour “\1”."),
    (R(r"Licht („.+?“) (an|aus)( \(\d+ %\))?"), lambda m: f"Light {m[1]} {_STATE[m[2]]}{m[3] or ''}"),
    (R(r"Licht( in (.+?))? (an|auf (\d+ %))"),
     lambda m: f"Light{' in ' + m[2] if m[2] else ''} {'on' if m[3] == 'an' else 'to ' + m[4]}"),
    (R(r"(\d+) (Licht|Lichter)( in (.+?))? (an|aus|heller|gedimmt)( \(([^;]+)\))?(; übersprungen: (.+))?"),
     lambda m: (f"{m[1]} {'light' if m[2] == 'Licht' else 'lights'}{' in ' + m[4] if m[4] else ''} {_STATE[m[5]]}"
                + (f" ({_inner(m[7])})" if m[7] else "") + (f"; skipped: {_inner(m[9])}" if m[9] else ""))),
    # Rollläden
    (R(r"In (.+?) gibt es keine Rollläden\."), r"There are no blinds in \1."),
    (R(r"Rollladen („.+?“) (fährt auf|fährt zu|gestoppt)"), lambda m: f"Blind {m[1]} {_BLIND[m[2]]}"),
    (R(r"Rollläden( in (.+?))? (fahren auf|fahren zu|gestoppt)( \((.+)\))?"),
     lambda m: f"Blinds{' in ' + m[2] if m[2] else ''} {_BLIND[m[3]]}{' (' + m[5] + ')' if m[5] else ''}"),
    # Heizung
    (R(r"In (.+?) gibt es kein Thermostat\."), r"There is no thermostat in \1."),
    (R(r"Im Raum (.+?) sind es (.+?) °C\."), lambda m: f"It is {_num(m[2])} °C in {m[1]}."),
    (R(r"Thermostat („.+?“) liefert gerade keinen Messwert\."), r"Thermostat \1 is not reporting a value right now."),
    (R(r"Den aktuellen Sollwert von („.+?“) kenne ich gerade nicht\."), r"I don't know the current setpoint of \1 right now."),
    (R(r"Thermostat („.+?“) auf (.+?) °C"), lambda m: f"Thermostat {m[1]} set to {_num(m[2])} °C"),
    # Räume
    (R(r"Kein Raum namens („.+?“) gefunden\."), r"No room called \1 found."),
    (R(r"Raum (.+) aus"), r"Room \1 off"),
    # Musik
    (R(r"In (.+?) gibt es keine (.+?) – dort ist keine passende Quelle eingerichtet\."),
     lambda m: f"There is no {'music' if m[2] == 'Musik' else m[2]} in {m[1]} – no matching source is set up there."),
    (R(r"Zu („.+?“) finde ich in unserer Sammlung nichts Passendes – soll ich auf Qobuz suchen\?"),
     r"I can't find anything matching \1 in our collection – shall I search Qobuz?"),
    (R(r"Zu („.+?“) finde ich keine Musik – meinst du (die Serie|den Film) („.+?“)\?"),
     lambda m: f"I can't find any music for {m[1]} – do you mean {_WHAT[m[2]]} {m[3]}?"),
    (R(r"Zu („.+?“) finde ich nichts – meinst du (.+)\?"),
     lambda m: f"I can't find anything for {m[1]} – do you mean {_list(_inner(m[2]), 'or')}?"),
    (R(r"Zu („.+?“) finde ich nichts in der Musiksammlung – soll ich auf Qobuz suchen\?"),
     r"I can't find anything for \1 in the music collection – shall I search Qobuz?"),
    (R(r"Von (.+?) habe ich nichts in unserer Sammlung – soll ich („.+?“) auf Qobuz suchen\?"),
     r"I have nothing by \1 in our collection – shall I search Qobuz for \2?"),
    (R(r"(„.+?“) von (.+?) habe ich nicht in der Sammlung – meinst du („.+?“) von (.+?), oder soll ich auf Qobuz suchen\?"),
     r"\1 by \2 isn't in the collection – do you mean \3 by \4, or shall I search Qobuz?"),
    (R(r"Meinst du (die Serie|den Film) („.+?“)( oder („.+?“))?( \(nach („.+?“)\))?\?"),
     lambda m: (f"Do you mean {_WHAT[m[1]]} {m[2]}{' or ' + m[4] if m[4] else ''}"
                + (f" (searching for {m[6]})" if m[6] else "") + "?")),
    (R(r"Meinst du (.+?)( \(nach („.+?“)\))?\?"),
     lambda m: f"Do you mean {_list(_inner(m[1]), 'or')}{' (searching for ' + m[3] + ')' if m[3] else ''}?"),
    (R(r"(„.+?“) finde ich auf dem Player in (.+?) nicht\."), r"I can't find \1 on the player in \2."),
    (R(r"In (.+?) gibt es keine Musikquelle, die ich abspielen kann\."), r"There is no music source in \1 that I can play."),
    (R(r"Der Player in (.+?) ist gerade nicht erreichbar\."), r"The player in \1 is not reachable right now."),
    (R(r"(„.+?“) ist auf („.+?“) \((.*?)\) – ich spiele das Album in (.+)"), r"\1 is on \2 (\3) – playing the album in \4"),
    (R(r"Spiele (.+) in (.+?)"), r"Playing \1 in \2"),
    (R(r"Zufällig: (.+) in (.+?)"), r"Random: \1 in \2"),
    (R(r"Von (.+?) haben wir 1 Album: (.+)\."), r"We have 1 album by \1: \2."),
    (R(r"Von (.+?) haben wir (\d+) Alben: (.+)\."), lambda m: f"We have {m[2]} albums by {m[1]}: {_list(m[3])}."),
    (R(r"Von (.+?) habe ich in unserer Sammlung nichts gefunden\."), r"I found nothing by \1 in our collection."),
    (R(r"Von (.+?) habe ich in der Sammlung nichts, und Qobuz kann ich in (.+?) nicht fragen\."),
     r"I have nothing by \1 in the collection, and I can't ask Qobuz in \2."),
    (R(r"Von (.+?) habe ich weder in der Sammlung noch auf Qobuz Alben gefunden\."),
     r"I found no albums by \1, neither in the collection nor on Qobuz."),
    (R(r"Auf Qobuz von (.+?): (.+)\."), lambda m: f"On Qobuz by {m[1]}: {_list(m[2])}."),
    (R(r"Auf Qobuz finde ich zu („.+?“) nichts( Passendes)? – soll ich in unserer Musik suchen\?"),
     lambda m: f"I can't find anything{' matching' if m[2] else ''} for {m[1]} on Qobuz – shall I search our music?"),
    (R(r"Keinen Sender zu („.+?“) gefunden\."), r"No station found for \1."),
    (R(r"Auf Qobuz nichts zu („.+?“) gefunden\."), r"Nothing found on Qobuz for \1."),
    (R(r"(Radio|Qobuz) per Sprache geht in (.+?) noch nicht \(nur in Räumen mit BluOS-Player\)\."),
     r"\1 by voice doesn't work in \2 yet (only in rooms with a BluOS player)."),
    (R(r"(„.+?“) lässt sich gerade nicht abspielen – der Dienst liefert keinen Stream\."),
     r"\1 can't be played right now – the service delivers no stream."),
    # Filme
    (R(r"In (.+?) gibt es keine Mediathek – Filme und Serien gehen nur im Raum mit dem Mediacenter\."),
     r"There is no media library in \1 – movies and series only work in the room with the media center."),
    (R(r"Zu („.+?“) finde ich keinen Film und keine Serie – meinst du (.+)\?"),
     lambda m: f"I can't find a movie or series for {m[1]} – do you mean {_list(_inner(m[2]), 'or')}?"),
    (R(r"Zu („.+?“) finde ich in der Mediathek nichts\."), r"I can't find anything for \1 in the media library."),
    (R(r"In (.+?) gibt es keine Mediathek\."), r"There is no media library in \1."),
    (R(r"Zu („.+?“) gibt es keine passende Folge\."), r"There is no matching episode for \1."),
    (R(r"(„.+?“) ist in (.+?) keine Quelle\."), r"\1 is not a source in \2."),
    (R(r"(„.+?“) lässt sich gerade nicht starten – KODI antwortet nicht\."), r"\1 can't be started right now – KODI is not responding."),
    (R(r"Serie („.+?“) (.*?) ?in (.+)"),
     lambda m: "Series " + " ".join(x for x in (m[1], re.sub(r"Staffel (\d+)", r"season \1",
                                                               re.sub(r"Folge (\d+)", r"episode \1", m[2] or ""))) if x)
     + f" in {m[3]}"),
    (R(r"Film („.+?“) in (.+)"), r"Movie \1 in \2"),
    # Lautstärke, Schalter, Wiedergabe
    (R(r"Die aktuelle Lautstärke in (.+?) kenne ich gerade nicht\."), r"I don't know the current volume in \1 right now."),
    (R(r"Lautstärke in (.+?) auf (\d+)( \(Sprach-Obergrenze (\d+)\))?"),
     lambda m: f"Volume in {m[1]} set to {m[2]}{' (voice limit ' + m[4] + ')' if m[4] else ''}"),
    (R(r"Lautstärke (\d+)( \(Sprach-Obergrenze (\d+)\))?"),
     lambda m: f"Volume {m[1]}{' (voice limit ' + m[3] + ')' if m[3] else ''}"),
    (R(r"(„.+?“) (an|aus)"), lambda m: f"{m[1]} {_STATE[m[2]]}"),
    (R(r"Zufall/Wiederholen geht in (.+?) mit dieser Quelle nicht\."), r"Shuffle/repeat doesn't work in \1 with this source."),
    (R(r"Unbekannte Wiedergabe-Aktion („.+?“)\."), r"Unknown playback action \1."),
    (R(r"(.+?): (" + "|".join(map(re.escape, _MEDIA_WORD)) + ")"), lambda m: f"{m[1]}: {_MEDIA_WORD[m[2]]}"),
    (R("(" + "|".join(map(re.escape, _MEDIA_WORD)) + ")", re.I),
     lambda m: next(v for k, v in _MEDIA_WORD.items() if k.lower() == m[1].lower()).capitalize()),
    # Auskünfte
    (R(r"(Heute|Morgen|Übermorgen|Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag|Gerade)\b.*\bGrad\b.*"), _weather),
    (R(r"Zu („.+?“) habe ich nichts gefunden\."), r"I found nothing for \1."),
    (R(r"In (.+?) läuft gerade nichts\."), r"Nothing is playing in \1 right now."),
    (R(r"Unbekannte Funktion (.+)\."), r"Unknown function \1."),
    (R(r"Fehler bei (\w+): (.*)"), r"Error in \1: \2"),
]


def _rooms_playing(s: str) -> str | None:
    """„Raum: Quelle, Titel (läuft); Raum2: …“ (rooms_playing) – Zustand am Ende jedes Teils übersetzen."""
    if not re.search(r"\((läuft|pausiert|gestoppt|an)\)(;|$)", s):
        return None
    return re.sub(r"\((läuft|pausiert|gestoppt|an)\)(?=;|$)", lambda m: f"({_PLAYSTATE[m[1]]})",
                  re.sub(r"\bQuelle (\d+)", r"source \1", s))


def _seg(s: str) -> str:
    sim = ""
    if s.endswith(SIM_DE):
        s, sim = s[:-len(SIM_DE)], SIM_EN
    key = s.strip()
    if key in EXACT:
        return EXACT[key] + sim
    for rx, rep in RULES:
        m = rx.fullmatch(key)
        if m:
            return (rep(m) if callable(rep) else m.expand(rep)) + sim
    return (_rooms_playing(key) or _inner(key)) + sim


def to_en(text: str) -> str:
    """Deutsche Rückmeldung (auch mehrere, mit „ · “ verbunden) → Englisch; Unbekanntes bleibt stehen."""
    if not text:
        return text
    out = " · ".join(_seg(p) for p in str(text).split(" · "))
    return re.sub(r"„([^“„]*)“", lambda m: f"“{m[1]}”", out).replace("„", "“")


def speakable(text: str) -> str:
    """Wie voice.speakable, für englische Ansagen."""
    t = text.replace(SIM_EN, ", simulated only")
    t = t.replace(" · ", ". ").replace("“", "").replace("”", "").replace("„", "").replace('"', "")
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"(\d)\s*°C", r"\1 degrees", t)
    t = re.sub(r"\(e\.g\.[^)]*\)", "", t)
    t = t.replace("–", ",").replace("→", "to").replace("✓", "")
    t = re.sub(r"\s+([.,!?])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()
