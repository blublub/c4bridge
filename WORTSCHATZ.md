# Wortschatz der Spracherkennung – wo was steht

Stand 18.10.2026. Kurz: **Eure eigenen Wörter → Admin, Hörfehler → `c4bridge/wortschatz.py`.**

## 1. Ein neues Wort soll erkannt werden (Name, Gerät, Interpret …)

**Admin › Sprache › Sprachdienst › Spracherkennung › „Hinweistext“** – einfach dazuschreiben, Komma-getrennt,
z. B. `Schreibtischlampe, Heizungspumpe, Avril Lavigne`. Speichern, wirkt ab dem nächsten Satz.

- Gespeichert in `data/voice.json` → `stt.hint`.
- Wirkt nur bei **Whisper** (alle Varianten: Vulkan mehrsprachig/deutsch, DirectML, CPU).
  Canary und Parakeet kennen keinen Hinweistext.
- Automatisch kommen dazu (nichts zu pflegen): die Wake-Phrase („Hey Jarvis“), alle Raumnamen, die Interpreten der
  Musiksammlung (zuletzt gespielte zuerst) und die festen Wörter aus `wortschatz.py` (`FESTE_WOERTER`: Qobuz, TuneIn,
  Radio, Rollladen …).
- **Neu:** Was per Sprache gefunden und gespielt wurde (Interpret + Titel, auch von Qobuz und Radio), merkt sich
  c4voice automatisch (`data/wortschatz_zuletzt.json`, die letzten 30) und hängt es als „Zuletzt: …“ ans Ende des
  Hinweistexts. Gemessen 18.10.: „Imagine Dragons“, „Avril Lavigne“, „Sk8er Boi“ kamen damit richtig an, ohne nicht.
  Die Datei darf man löschen (fängt dann leer an) oder von Hand ergänzen (JSON-Liste).
- Grenze: Whisper liest höchstens die LETZTEN ~220 Wortbausteine – was vorne übersteht, fällt weg. Reihenfolge:
  Wake-Phrase, euer Text, Räume, feste Wörter, Interpreten der Sammlung (~220 Zeichen), Zuletzt (~160 Zeichen). Die
  beiden letzten sind begrenzt, damit vorne nichts verloren geht.

## 2. Ein Wort kommt trotzdem immer wieder falsch an

Im Log (Admin › Log, oder `/api/voice/status`) steht bei jedem Satz `transcript` mit `text` (nach Korrektur) und `raw`
(so hat die Erkennung es gehört). Kommt ein Begriff regelmäßig gleich falsch an, in **`c4bridge/c4bridge/wortschatz.py`**:

| Liste | Wofür | Beispiel |
|---|---|---|
| `KORREKTUREN` | feste Schreibweise → richtig | `kubos`, `kobus`, `bobus` → `Qobuz` |
| `KLANG` | alles, was so **klingt** (Kölner Phonetik) | jedes Wort mit K/Q/C/G vorn, s/z/t hinten und Code `418` → `Qobuz` |
| `WAKE_DEUTSCH` | eingedeutschte Wake-Phrase am Satzanfang (wird abgeschnitten) | „Ja, das“, „Ich habe das“ |
| `FESTE_WOERTER` | feste Wörter für den Hinweistext | „Dienste: Qobuz, TuneIn, Radio …“ |

Danach: **Test** `.venv\Scripts\python.exe tools\t_wortschatz.py` (prüft auch, dass Wörter wie „Kapuze“ NICHT
verändert werden) und **c4voice neu starten** (Admin › Sprache › Neustart).

Raumnamen werden automatisch unscharf korrigiert (`voice.fix_rooms`, „S-Zimmer“ → Esszimmer) – dafür nichts eintragen,
höchstens im Admin bei Räumen einen Alias setzen.

## 3. Welche Erkennung läuft wo

| Admin-Auswahl | Wo | Tempo | Hinweistext | Bemerkung |
|---|---|---|---|---|
| **Whisper turbo mehrsprachig auf der Grafikkarte (Vulkan)** (live seit 19.10., Test) | GPU, eigener Dienst | ~0,3 s | ja | Original-Modell `data/models/whisper-turbo-ggml` (q8_0), Port 8094, Log `data/stt_vulkan_multi.log` – englische Namen/Titel besser, deutsche Sätze gleich gut (`stt-test/WhisperVulkan/ERGEBNIS.md`) |
| Whisper turbo deutsch auf der Grafikkarte (Vulkan) | GPU, eigener Dienst | ~0,3 s | ja | whisper.cpp: `bin/whisper-vulkan/whisper-server.exe`, Modell `data/models/whisper-turbo-german-ggml` (q8_0), Port 8092, Log `data/stt_vulkan.log` |
| Whisper turbo deutsch auf der Grafikkarte (DirectML) | GPU, eigener Dienst | ~0,4 s | ja | `c4bridge/stt_gpu.py`, venv `.venv-gpu`, Modell `data/models/whisper-turbo-german-onnx`, Port 8091, Log `data/stt_gpu.log` |
| Whisper large-v3-turbo / turbo deutsch | CPU | ~2,5 s | ja | faster-whisper, Beam/Threads einstellbar |
| NVIDIA Canary 1B v2 | CPU | ~0,1–0,4 s | nein | Sprache fest; übersetzt englische Wörter („Smile“ → „Lächeln“) |
| NVIDIA Parakeet v3 | CPU | ~0,1 s | nein | rät die Sprache, kippt bei kurzen Sätzen ins Englische |

**GPU-Dienste:** c4voice startet den gewählten selbst (nur lokal, 127.0.0.1) und beendet beim Start einen noch laufenden
Dienst der anderen Art (PIDs in `data/stt_gpu_pids.json`). Beide erkennen mit demselben Modell und demselben Hinweistext
denselben Text; Vulkan ist ~0,1 s schneller. Antwortet der Dienst nicht (startet noch, abgestürzt, Grafikkarte belegt),
erkennt c4voice den Satz mit **Canary** (Log `stt_fallback`) – es bleibt nie stumm.
- Vulkan: eigenständiges Programm (braucht nur den Grafiktreiber), selbst gebaut aus whisper.cpp – Version und
  Bauanleitung: `bin/whisper-vulkan/VERSION.txt` und `stt-test/WhisperVulkan/ERGEBNIS.md`.
- DirectML: nutzt interne Teile von onnx-asr → Version **0.12.0 fest** in `.venv-gpu` (nicht aktualisieren, ohne
  `tools/t_wortschatz.py` und einen Sprachtest).

## 4. Wake-Word

Admin › Sprache › Wake-Word: **Empfindlichkeit** (`wake.threshold`, aktuell 0,35; niedriger = hört eher, löst öfter
fälschlich aus) und **Gegenprüfung** (`wake.verify`, aktuell aus). Jedes Anschlagen steht mit Score im Log (`wake`).
