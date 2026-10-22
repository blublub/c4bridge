/* Englisch für Admin- und Login-Seite (die Bedienoberfläche übersetzt selbst über core.js).
   Statt jede Vorlage umzubauen, ersetzt dieses Skript beim Anzeigen bekannte deutsche Texte (Textknoten,
   Platzhalter, Tooltips, alert/confirm/prompt). Sprache = dieselbe Wahl wie in der Bedienoberfläche (c4lang).
   Neue deutsche Texte im Admin → hier ergänzen (Form: ['deutsch', 'english']). */
(() => {
  let lang = 'de';
  try { lang = localStorage.getItem('c4lang') === 'en' ? 'en' : 'de'; } catch { /* privat */ }
  window.C4LANG = lang;
  window.c4SetLang = l => {
    try { localStorage.setItem('c4lang', l); } catch { /* privat */ }
    fetch('/api/me/prefs', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lang: l }) })
      .catch(() => { }).finally(() => location.reload());
  };
  window.c4tr = s => s;                      // Deutsch: unverändert
  if (lang !== 'en') return;
  document.documentElement.lang = 'en';

  // ---- ganze Texte (exakt, nach Zusammenfassen der Leerzeichen) ------------------------------------------
  const EXACT = [
    // Sprachausgabe: Qwen3-TTS
    ['Piper-Stimme', 'Piper voice'], ['Piper-Stimmen (Rückfall, falls Pocket/Qwen3 ausfällt)', 'Piper voices (fallback if Pocket/Qwen3 fails)'],
    ['spricht nur, wenn Piper gewählt ist oder Pocket/Qwen3 ausfällt; Tempo und Lautstärke gelten nur für Piper', 'only speaks when Piper is selected or Pocket/Qwen3 fails; speed and volume apply to Piper only'],
    ['Spracherkennung', 'Speech recognition'], ['LLM · Ollama', 'LLM · Ollama'], ['Spracherkennung · Whisper', 'Speech recognition · Whisper'],
    ['Hinweistext für die Erkennung', 'Hint text for recognition'], ['GPU (eigener Dienst, ~1 GB Grafikspeicher)', 'GPU (own service, ~1 GB VRAM)'], ['CPU (Standard, ohne Grafikkarte)', 'CPU (default, no GPU)'],
    ['Sprachausgabe (lokal)', 'Speech output (local)'], ['Sprachmodell', 'Speech model'], ['Piper (CPU, sofort)', 'Piper (CPU, instant)'],
    ['Qwen3-TTS (Grafikkarte, natürlicher)', 'Qwen3-TTS (GPU, more natural)'], ['Qwen3-Ordner', 'Qwen3 folder'],
    ['Pocket-Stimme', 'Pocket voice'], ['fällt das Modell aus, spricht Piper', 'if the model fails, Piper speaks'], ['Stimmproben in bin/tts/pocket/voices', 'Voice samples in bin/tts/pocket/voices'],
    ['Stimmvorlage', 'Voice sample'], ['Text der Vorlage', 'Sample transcript'],
    ['Qwen3-TTS läuft als eigener Dienst (~1,5 GB Grafikspeicher); fällt er aus, spricht Piper', 'Qwen3-TTS runs as its own service (~1.5 GB VRAM); if it fails, Piper speaks'],
    ['Qwen3-Modell', 'Qwen3 model'],
    ['1.7B (beste Qualität, ~1,4 GB Grafikspeicher)', '1.7B (best quality, ~1.4 GB VRAM)'],
    ['0.6B (schneller, weniger Grafikspeicher)', '0.6B (faster, less VRAM)'],
    ['Wechsel lädt den Dienst beim nächsten Satz neu (~7 s)', 'Switching reloads the service on the next sentence (~7 s)'],
    ['data/tts/qwen_ref_<name>.wav + .txt (5–10 s klare Sprache – längere Vorlagen verschlucken kurze Sätze; echte Personen nur mit Einverständnis)', 'data/tts/qwen_ref_<name>.wav + .txt (5–10 s of clear speech – longer samples swallow short sentences; real people only with their consent)'],
    // Mehrzonen / Push-Art je Raum
    ['direkt', 'direct'], ['nachlesen', 're-read'], ['nur Polling', 'polling only'], ['geprüft', 'verified'],
    ['ungeprüft', 'unverified'], ['falsch', 'mismatch'], ['direkt aus Ereignis', 'direct from event'],
    ['Raum nachlesen', 'Re-read room'], ['Push aus', 'Push off'], ['Gerät meldet keine Lautstärke', 'Device reports no volume'],
    ['Gerät unbekannt', 'Device unknown'], ['Ausgang mit anderem Raum geteilt', 'Output shared with another room'],
    ['= das Lautstärke-Gerät meldet nichts → die Oberfläche sieht Änderungen erst beim nächsten Abfragen (5–15 s). „nachlesen" = Ereignis kommt, der Raum wird dann gelesen.',
     '= the volume device reports nothing → the UI sees changes only at the next poll (5–15 s). “re-read” = an event arrives, then the room is read.'],
    // BluOS-Selbstkonfiguration (Status-Reiter)
    ['Einrichtung', 'Setup'], ['Module', 'Modules'], ['Geräte', 'Devices'], ['nach Raum', 'by room'], ['nach Treiber', 'by driver'], ['nach Typ', 'by type'], ['alle auf', 'expand all'], ['alle zu', 'collapse all'], ['nur aktive', 'active only'], ['🧪 Gerät anlernen (experimentell)', '🧪 Teach device (experimental)'], ['Weitere Control4-Items', 'Other Control4 items'], ['über Treiber', 'via driver'], ['Gerät (ID)', 'Device (ID)'], ['Bridge-Typ', 'Bridge type'], ['Control4-Proxy / Treiber', 'Control4 proxy / driver'], ['Funktion', 'Function'], ['Anmerkung (für die KI)', 'Note (for the AI)'], ['Weitere Namen', 'Other names'], ['Zuordnen', 'Map'], ['Mitschnitt', 'Capture'], ['Variablen', 'Variables'], ['Befehle', 'Commands'], ['Profil speichern', 'Save profile'], ['Prüfen', 'Check'], ['Testen …', 'Test …'], ['Mitschnitt starten', 'Start capture'], ['stoppen', 'stop'], ['neu starten', 'restart'], ['anderes Gerät', 'other device'], ['schließen', 'close'], ['Gerätetyp', 'Device type'], ['Vorschlag neu übernehmen', 'Apply suggestion again'], ['Was die Bridge braucht – Werte', 'What the bridge needs – values'], ['Aktionen', 'Actions'], ['Profil-Schlüssel', 'Profile key'], ['Gespeicherte Profile', 'Saved profiles'], ['ID laden', 'Load ID'], ['wählen', 'select'], ['Gerät nicht dabei? Projekt neu einlesen', 'Device missing? Re-import project'], ['Untersuchen', 'Inspect'], ['Anlernen', 'Teach'], ['– nicht belegt –', '– not mapped –'], ['Bindungen des Treibers', 'Driver bindings'], ['Prüfung – so sieht die Bridge das Gerät jetzt', 'Check – how the bridge sees the device now'], ['geschätzt', 'guessed'], ['unbekannter Wert', 'unknown value'], ['Nutzung durch die Bridge', 'Use by the bridge'], ['Item (ID)', 'Item (ID)'], ['Profil', 'Profile'],
    ['KODI (automatisch erkannt)', 'KODI (auto-detected)'], ['Adresse', 'Address'], ['Bereiche (Control4-Quelle)', 'Sections (Control4 source)'],
    ['nur Control4', 'Control4 only'], ['Wiedergabe', 'Playback'], ['gestoppt', 'stopped'],
    ['Nur lesend: Adresse aus den Eigenschaften des Control4-Treibers, Bereiche über dessen Bindungen; Status per KODI-WebSocket (Port 9090) und Control4-Push, Mediathek über KODI JSON-RPC.',
      'Read-only: address from the Control4 driver properties, sections via its bindings; status via KODI WebSocket (port 9090) and Control4 push, library via KODI JSON-RPC.'],
    ['BluOS-Player (automatisch erkannt)', 'BluOS players (auto-detected)'], ['Neu erkennen', 'Detect again'],
    ['erkenne …', 'detecting …'], ['Hinweise', 'Notes'], ['Player', 'Player'], ['Zuordnung', 'Assignment'],
    ['Lautstärke', 'Volume'], ['Control4-Quellen → BluOS-Dienst', 'Control4 sources → BluOS service'],
    ['fest (Verstärker)', 'fixed (amplifier)'], ['am Player', 'on the player'], ['Modell', 'Model'],
    ['IP / MAC', 'IP / MAC'], ['Dienste und Eingänge am Player', 'Services and inputs on the player'],
    ['Control4-Treiber', 'Control4 driver'], ['feste Lautstärke', 'fixed volume'], ['nicht erreichbar', 'unreachable'],
    ['Raum des Treibers', 'driver room'], ['Hand', 'manual'], ['Bibliothek', 'Library'],
    ['Nur lesend: Player per mDNS und Control4-Adressen, Zuordnung über MAC; Räume über die Audio-Endpunkte in Composer.',
     'Read-only: players via mDNS and Control4 addresses, matched by MAC; rooms via the audio endpoints in Composer.'],
    // Kopf, Reiter, allgemein
    ['c4bridge · Admin', 'c4bridge · Admin'], ['c4bridge · Anmelden', 'c4bridge · Sign in'], ['⌂ Start', '⌂ Home'],
    ['Zur Bedienoberfläche', 'To the control UI'], ['Status', 'Status'], ['Config', 'Config'], ['Geräte', 'Devices'],
    ['Projektdaten', 'Project data'], ['Log', 'Log'], ['Abmelden', 'Sign out'], ['Speichern', 'Save'], ['Löschen', 'Delete'],
    ['Entfernen', 'Remove'], ['Anlegen', 'Create'], ['Testen', 'Test'], ['Schließen', 'Close'], ['Öffnen', 'Open'],
    ['Suchen', 'Search'], ['Zurücksetzen', 'Reset'], ['Neustart', 'Restart'], ['Start', 'Start'], ['Stopp', 'Stop'],
    ['Herunterladen', 'Download'], ['Probe', 'Try'], ['aktiv', 'active'], ['Aktiv', 'Active'], ['alle', 'all'],
    ['keine', 'none'], ['keiner', 'none'], ['– keiner –', '– none –'], ['ein', 'on'], ['aus', 'off'], ['an', 'on'],
    ['ja', 'yes'], ['nein', 'no'], ['normal', 'normal'], ['sichtbar', 'visible'], ['angelegt', 'created'],
    ['ausgewertet', 'evaluated'], ['eingebaut', 'built-in'], ['verbunden', 'connected'], ['getrennt', 'disconnected'],
    ['speichert sofort', 'saves immediately'], ['Gespeichert', 'Saved'], ['Gespeichert ✓', 'Saved ✓'], ['gespeichert ✓', 'saved ✓'],
    ['angelegt ✓', 'created ✓'], ['Abgemeldet ✓', 'Signed out ✓'], ['Passwort gesetzt ✓', 'Password set ✓'],
    ['✗ Fehler', '✗ Error'], ['Fehler:', 'Error:'], ['lade …', 'loading …'], ['lädt …', 'loading …'], ['lädt … (~60 MB)', 'loading … (~60 MB)'],
    ['frage ab …', 'querying …'], ['erzeuge …', 'generating …'], ['scannt …', 'scanning …'], ['suche …', 'searching …'],
    ['teste …', 'testing …'], ['lese Raum …', 'reading room …'], ['erst einen Ort wählen', 'choose a place first'],
    ['nichts gefunden', 'nothing found'], ['keine Treffer', 'no results'], ['nicht verfügbar', 'not available'],
    ['nicht angemeldet', 'not signed in'], ['nicht konfiguriert', 'not configured'], ['noch nicht gesetzt', 'not set yet'],
    ['Status nicht verfügbar', 'Status not available'], ['▸ anzeigen', '▸ show'], ['▾ ausblenden', '▾ hide'],
    ['↻ neu laden', '↻ reload'], ['▶ Anhören', '▶ Listen'], ['Diagnose:', 'Diagnostics:'], ['Push-first:', 'Push-first:'],
    // Config-Unterreiter
    ['Control4', 'Control4'], ['Zugang & Benutzer', 'Access & users'], ['Oberfläche & Räume', 'UI & rooms'], ['Kameras', 'Cameras'],
    ['Sprache', 'Language'], ['Design', 'Design'], ['Theme', 'Theme'],
    // Control4
    ['Control4-Zugang', 'Control4 access'], ['E-Mail', 'E-mail'], ['Passwort', 'Password'], ['Controller-IP', 'Controller IP'],
    ['Controller-Scan', 'Controller scan'], ['Jetzt scannen', 'Scan now'], ['Projekt abrufen', 'Fetch project'],
    ['Geräte neu importieren', 'Re-import devices'], ['Musikbibliothek neu laden', 'Reload music library'],
    ['WebSocket-Push', 'WebSocket push'], ['Neu verbinden', 'Reconnect'], ['Neu abonnieren', 'Resubscribe'],
    ['Absender anzeigen', 'Show senders'], ['Token läuft ab in', 'Token expires in'], ['Inventar (Bridge)', 'Inventory (bridge)'],
    ['Director-Ereignisse mitschneiden (Standard aus)', 'record Director events (default off)'],
    ['nicht verfügbar (pyControl4-Version)', 'not available (pyControl4 version)'],
    ['Absender', 'Sender'], ['Anzahl', 'Count'], ['zuletzt vor', 'last'], ['Felder', 'Fields'],
    ['Minuten (0 = aus, Standard 1440 = einmal am Tag) – sucht automatisch nach neuen Geräten und Räumen.',
      'minutes (0 = off, default 1440 = once a day) – looks for new devices and rooms automatically.'],
    ['Zugangsdaten wirken sofort (ohne Neustart). Datei:', 'Credentials apply immediately (no restart). File:'],
    ['gesetzt – leer lassen = unverändert', 'set – leave empty = unchanged'],
    ['liegt in config.env (außerhalb des Projekts), leer lassen = unverändert', 'stored in config.env (outside the project), leave empty = unchanged'],
    // Zugang & Benutzer
    ['Benutzerverwaltung', 'User management'], ['Benutzer', 'User'], ['Benutzername', 'User name'], ['Rolle', 'Role'],
    ['Aktionen', 'Actions'], ['Aktion', 'Action'], ['Passwort setzen', 'Set password'], ['Geräte abmelden', 'Sign out devices'],
    ['Admin-Konto anlegen', 'Create admin account'], ['Als Benutzerkonto übernehmen', 'Adopt as user account'],
    ['Passwort (min. 6 Zeichen)', 'Password (min. 6 characters)'], ['Login-Benutzer', 'Login user'], ['Login-Passwort', 'Login password'],
    ['Host / Port', 'Host / port'], ['Notfall-Login (Config-Datei) · Host / Port', 'Emergency login (config file) · host / port'],
    ['Notfall-Login – inaktiv, solange Benutzerkonten existieren', 'Emergency login – inactive while user accounts exist'],
    ['Nur relevant, solange oben KEINE Benutzer angelegt sind: dann gilt dieser einzelne Login (als Admin).',
      'Only relevant while NO users exist above: then this single login applies (as admin).'],
    ['Login wirkt sofort · Host/Port brauchen einen Bridge-Neustart (run.bat)', 'Login applies immediately · host/port need a bridge restart (run.bat)'],
    ['leer = kein Login (nur localhost!)', 'empty = no login (localhost only!)'],
    ['Alle Geräte dieses Kontos abmelden (z. B. verlorenes Tablet)', 'Sign out all devices of this account (e.g. lost tablet)'],
    ['⚠ kein Login eingerichtet (offen im Netz)', '⚠ no login set up (open on the network)'],
    ['⚠ Noch kein Benutzer und kein Notfall-Login – jeder im Netz kann bedienen und hat Admin-Rechte. Unten das erste Konto anlegen: es wird automatisch',
      '⚠ No user and no emergency login yet – anyone on the network can operate and has admin rights. Create the first account below: it automatically becomes'],
    [', danach gilt Login-Pflicht.', ', after that login is required.'],
    ['Neues Passwort für den Notfall-Login (min. 6 Zeichen):', 'New password for the emergency login (min. 6 characters):'],
    ['Erstes Konto angelegt (admin). Ab jetzt gilt Login-Pflicht – bitte anmelden.', 'First account created (admin). Login is now required – please sign in.'],
    ['Passwort gesetzt ✓ (bestehende Anmeldungen dieses Kontos sind damit beendet)', 'Password set ✓ (existing sessions of this account were ended)'],
    ['Den Notfall-Login als erstes Admin-Konto übernehmen? Name und Passwort bleiben gleich; ab dann gilt die Benutzerverwaltung.',
      'Adopt the emergency login as the first admin account? Name and password stay the same; user management applies from then on.'],
    ['Gleicher Name und gleiches Passwort, danach mit Rollen, Favoriten und „Geräte abmelden“', 'Same name and password, then with roles, favourites and “sign out devices”'],
    [': alles inkl. Admin-Oberfläche ·', ': everything incl. admin UI ·'],
    [': bedienen, eigene Favoriten, eigenes Theme/Sprache. Jeder Benutzer hat sein eigenes Startseiten-Layout; ohne eigenes gilt der Hausstandard.',
      ': operate, own favourites, own theme/language. Each user has their own home layout; without one the house default applies.'],
    // Oberfläche & Räume
    ['OS X4 (Standard)', 'OS X4 (default)'], ['OS 3 – veraltet, wird nicht mehr weiterentwickelt', 'OS 3 – deprecated, no longer developed'],
    ['Persönliche Einstellung des angemeldeten Benutzers (auch im ⋮-Menü der Bedienoberfläche umschaltbar), wirkt sofort.',
      'Personal setting of the signed-in user (also switchable in the ⋮ menu of the control UI), applies immediately.'],
    ['Theme gespeichert ✓ (gilt beim nächsten Öffnen der Bedienoberfläche)', 'Theme saved ✓ (applies the next time the control UI opens)'],
    ['Räume – Sichtbarkeit', 'Rooms – visibility'], ['Raum', 'Room'], ['Ebene', 'Floor'], ['Push-Quellen', 'Push sources'],
    ['Composer: versteckt', 'Composer: hidden'], ['in Composer als versteckt markiert', 'marked hidden in Composer'],
    ['Vorauswahl = Composer-Einstellung („roomHidden/tempHidden“). Die Schalter speichern sofort. Lieblingsräume legt jeder Benutzer selbst fest (Stern in der Raumliste der Bedienoberfläche).',
      'Preselection = Composer setting (“roomHidden/tempHidden”). Switches save immediately. Each user sets their favourite rooms (star in the room list of the control UI).'],
    ['Laut Composer', 'Per Composer'], ['Quelle', 'Source'], ['Lautstärke / Stumm', 'Volume / mute'], ['Audio-Pfad', 'Audio path'],
    ['Video-Pfad', 'Video path'], ['Ton beim Sehen', 'Sound when watching'], ['Letzte Ereignisse', 'Recent events'],
    ['UI-Agent noch nicht gesehen', 'UI agent not seen yet'], ['seit dem Start noch keine', 'none since start'],
    ['noch kein Ereignis', 'no event yet'], ['keine Endpunkt-Bindungen', 'no endpoint bindings'],
    ['kein Lautstärke-Gerät bekannt (Raum noch nie an gesehen)', 'no volume device known (room never seen on)'],
    ['direkt aus Ereignis', 'directly from event'], ['Raum nachlesen', 're-read room'],
    ['– Titel/Play-Pause per Ereignis dieses Geräts, Quellwechsel per Raum-Ereignis bzw. alle 5 s',
      '– title/play-pause via this device’s events, source change via room event or every 5 s'],
    // Kameras
    ['Kamera anlegen', 'Add camera'], ['Name (z. B. Hofkamera)', 'Name (e.g. yard camera)'], ['RTSP-Livestream', 'RTSP live stream'],
    ['Snapshot (Standbild)', 'Snapshot (still image)'], ['Kamera entfernen?', 'Remove camera?'],
    ['Zugangsdaten liegen lokal in data/cameras.json und werden nie an den Browser zurückgegeben. RTSP-Modus nutzt den Livestream direkt von der Kamera („Testen“ prüft die Verbindung).',
      'Credentials are stored locally in data/cameras.json and never sent back to the browser. RTSP mode uses the camera’s live stream directly (“Test” checks the connection).'],
    // Geräte / Projektdaten
    ['Name Control4 (ID)', 'Control4 name (ID)'], ['Typ', 'Type'], ['Funktion', 'Function'], ['Anmerkung (für die KI)', 'Note (for the AI)'],
    ['Weitere Namen', 'Other names'], ['z. B. Deckenleuchte Esszimmer', 'e.g. dining room ceiling light'], ['Deckenlicht, Lampe oben', 'ceiling light, top lamp'],
    ['Gerät, ID, Raum oder Anmerkung suchen …', 'Search device, ID, room or note …'], ['alle Räume', 'all rooms'], ['alle Typen', 'all types'],
    ['nur aktive', 'active only'], ['Gerät / Raum / Ebene suchen …', 'Search device / room / floor …'],
    ['Links ein Gerät oder Raum auswählen, um alle Properties live vom Director zu sehen.', 'Select a device or room on the left to see all its properties live from the Director.'],
    ['Variablen (live)', 'Variables (live)'], ['Director-Item-Info', 'Director item info'], ['Treiber', 'Driver'], ['Proxy', 'Proxy'],
    ['Räume / Geräte', 'Rooms / devices'],
    // Musik-Diagnose
    ['Musikbibliothek – Diagnose (Suche wie die Sprachsteuerung)', 'Music library – diagnostics (search like voice control)'],
    ['Interpret, Album oder Titel suchen …', 'Search artist, album or title …'], ['Probe-Suche', 'Test search'], ['Treffer', 'Results'],
    ['Interpret', 'Artist'], ['Album', 'Album'], ['Titel', 'Title'], ['Alben', 'Albums'],
    // Sprache (c4voice)
    ['Sprachdienst c4voice', 'Voice service c4voice'], ['Sprachdienst', 'Voice service'], ['Mikrofone + Räume', 'Microphones + rooms'],
    ['Mikrofone & Räume', 'Microphones & rooms'], ['Sprachausgabe', 'Speech output'], ['Wissen & Wetter', 'Knowledge & weather'],
    ['Licht', 'Light'], ['Lautstärke', 'Volume'], ['Sprachdienst c4voice läuft', 'Voice service c4voice running'],
    ['Autostart & Watchdog', 'Autostart & watchdog'], ['c4voice mit der Bridge starten und bei Absturz automatisch neu starten',
      'start c4voice with the bridge and restart it automatically after a crash'],
    ['Bausteine im Dienst (Start/Neustart/Stopp gelten für alle):', 'Building blocks in the service (start/restart/stop apply to all):'],
    ['Wake-Word · openWakeWord', 'Wake word · openWakeWord'], ['Wake-Word · openWakeWord (CPU)', 'Wake word · openWakeWord (CPU)'],
    ['Spracherkennung · Whisper', 'Speech recognition · Whisper'], ['Spracherkennung · faster-whisper (CPU)', 'Speech recognition · faster-whisper (CPU)'],
    ['Entscheidung · Ollama (extern)', 'Decision · Ollama (external)'], ['KI · Ollama', 'AI · Ollama'], ['Modell', 'Model'],
    ['Ollama-URL', 'Ollama URL'], ['Kontext', 'Context'], ['Im Speicher halten', 'Keep in memory'], ['Bridge-URL', 'Bridge URL'],
    ['Echt schalten', 'Switch for real'], ['Nur simulieren', 'Simulate only'], ['Jeder erkannte Befehl wird im Haus ausgeführt.', 'Every recognised command is executed in the house.'],
    ['Befehle werden entschieden und angezeigt, aber nicht ans Haus gesendet.', 'Commands are decided and shown, but not sent to the house.'],
    ['Trockenlauf AUS – Sprachbefehle schalten ECHT', 'Dry run OFF – voice commands switch FOR REAL'], ['Trockenlauf ausschalten?', 'Turn off dry run?'],
    ['Fehlende Modelle herunterladen', 'Download missing models'], ['Mikrofon-Test öffnen', 'Open microphone test'],
    ['Mikrofon im Browser testen (kleines, verschiebbares Fenster)', 'Test the microphone in the browser (small movable window)'],
    ['Mikrofon muss erst das Wake-Word sagen', 'Microphone must say the wake word first'], ['Rückfragen', 'Follow-up questions'],
    ['Nach einer Rückfrage („Welches Arbeitszimmer?“) 12 s ohne Wake-Word auf die Antwort hören; die Frage bleibt 90 s offen (auch „Hey Jarvis, ja“ geht dann noch)',
      'After a follow-up question (“Which study?”) listen 12 s for the answer without wake word; the question stays open for 90 s (“Hey Jarvis, yes” still works then)'],
    ['Noch keine Mikrofone gesehen – einmal den Mikrofon-Test verbinden, dann erscheinen sie hier.', 'No microphones seen yet – connect the microphone test once, then they appear here.'],
    ['Jedem Mikrofon 1–3 Räume zuweisen (z. B. Esszimmer + Küche als Einheit): „Mach das Licht an" ohne Raumnennung gilt dann für diese Räume – die anderen Geräte nutzt die KI nur bei ausdrücklicher Nennung.',
      'Assign 1–3 rooms to each microphone (e.g. dining room + kitchen as one unit): “turn on the light” without naming a room then applies to these rooms – the AI only uses other devices when explicitly named.'],
    ['c4voice lauscht nur auf 127.0.0.1 und nimmt nur Aufträge der Bridge an (Token). Mikrofone verbinden sich über die Bridge.',
      'c4voice listens only on 127.0.0.1 and accepts jobs only from the bridge (token). Microphones connect via the bridge.'],
    ['Eigene Geräte (ESP32/Raspberry) streamen PCM16/16 kHz/mono an', 'Own devices (ESP32/Raspberry) stream PCM16/16 kHz/mono to'],
    [', dann Binärframes.', ', then binary frames.'], ['(Pflicht),', '(required),'],
    ['Fachbegriffe, die oft falsch erkannt werden', 'Terms that are often misrecognised'], ['Hinweistext', 'Hint text'],
    ['tiny – am schnellsten, schwächer', 'tiny – fastest, weaker'], ['base – Kompromiss', 'base – compromise'],
    ['small – deutlich genauer', 'small – much more accurate'], ['medium – am genauesten, langsam', 'medium – most accurate, slow'],
    ['größeres Modell = bessere Erkennung, mehr CPU/Ladezeit beim ersten Start', 'larger model = better recognition, more CPU/load time on first start'],
    ['darf auf anderem PC laufen (dort Ollama mit OLLAMA_HOST=0.0.0.0 starten)', 'may run on another PC (start Ollama there with OLLAMA_HOST=0.0.0.0)'],
    ['Minuten (−1 = immer, 0 = sofort entladen)', 'minutes (−1 = always, 0 = unload immediately)'],
    ['Minuten nach dem letzten Befehl (−1 = immer, 0 = sofort entladen). Lang = keine Wartezeit beim ersten Befehl, belegt aber dauerhaft (Grafik-)Speicher.',
      'minutes after the last command (−1 = always, 0 = unload immediately). Long = no wait on the first command, but permanently uses (graphics) memory.'],
    ['Tokens (0 = Ollama-Standard). Für Befehle meist egal; nur erhöhen, wenn die Geräteliste sehr lang ist.', 'tokens (0 = Ollama default). Usually irrelevant for commands; only increase if the device list is very long.'],
    ['Tokens (0 = Standard). Suchtreffer sind länger – hier ggf. 8192 oder mehr.', 'tokens (0 = default). Search results are longer – maybe 8192 or more here.'],
    ['Erweitert: Anweisungen an die KI (Prompts)', 'Advanced: instructions for the AI (prompts)'],
    ['Leer bzw. unverändert = eingebaute Anweisungen (bekommen künftige Verbesserungen automatisch). Erst nach „Speichern" aktiv, wirkt ab dem nächsten Befehl.',
      'Empty or unchanged = built-in instructions (get future improvements automatically). Active only after “Save”, applies from the next command.'],
    ['Haussteuerung', 'Home control'], ['Allgemeine Fragen', 'General questions'], ['Allgemeine Fragen · Websuche', 'General questions · web search'],
    ['Standard wiederherstellen', 'Restore default'], ['– Platzhalter', '– placeholders'], ['Platzhalter', 'Placeholders'],
    ['Sprachausgabe · Piper (lokal, CPU)', 'Speech output · Piper (local, CPU)'], ['Stimme', 'Voice'], ['Stimmen', 'Voices'], ['Tempo', 'Speed'],
    ['Was sprechen', 'What to speak'], ['kurz – nur „Okay“ bei Erfolg', 'short – just “okay” on success'],
    ['ganze Antwort („Licht Schreibtisch an, 40 Prozent“)', 'full answer (“desk light on, 40 percent”)'],
    ['Rückfragen, Fehler und Auskünfte (Temperatur) immer vollständig', 'follow-up questions, errors and information (temperature) always in full'],
    ['Antworten sprechen (Mikrofon-Gerät bzw. Browser spielt sie ab)', 'Speak answers (microphone device or browser plays them)'],
    ['Hört mit den Reglern oben (auch ungespeichert). c4voice muss laufen.', 'Listens with the sliders above (even unsaved). c4voice must be running.'],
    ['⚠ Piper nicht installiert (pip install piper-tts)', '⚠ Piper not installed (pip install piper-tts)'], ['keine Stimme installiert', 'no voice installed'],
    ['Quelle: HuggingFace rhasspy/piper-voices (ca. 60–110 MB je Stimme, Ablage', 'Source: HuggingFace rhasspy/piper-voices (about 60–110 MB per voice, stored in'],
    ['Wetter · Open-Meteo (ohne Schlüssel)', 'Weather · Open-Meteo (no key)'], ['Ort', 'Place'], ['Koordinaten', 'Coordinates'],
    ['Ort eintippen, z. B. Stadt oder Ortsteil', 'Type a place, e.g. town or district'], ['Heute', 'Today'], ['Morgen', 'Tomorrow'],
    ['„Hey Jarvis, wie wird das Wetter heute / morgen / am Samstag?“', '“Hey Jarvis, what’s the weather today / tomorrow / on Saturday?”'],
    ['werden beim Ort-Suchen gesetzt; bleiben in data/voice.json (nur lokal)', 'set when searching a place; stay in data/voice.json (local only)'],
    ['Fragen wie „Wer hat …?“, „Wann ist …?“ im Internet nachschlagen (die Frage verlässt dann das Haus, Hausdaten nie)',
      'Look up questions like “Who did …?”, “When is …?” on the internet (the question then leaves the house, house data never)'],
    ['Anbieter', 'Provider'], ['SearXNG (selbst betrieben)', 'SearXNG (self-hosted)'], ['SearXNG-URL', 'SearXNG URL'],
    ['Exa (exa.ai, API-Schlüssel)', 'Exa (exa.ai, API key)'], ['Exa-Schlüssel', 'Exa key'], ['in settings.yml muss', 'settings.yml must'],
    ['„json“ enthalten', 'contain “json”'], ['Suchtreffer, aus denen die Antwort entsteht', 'search results the answer is built from'],
    ['Antwort-Modell (optional, zweite Ollama-Instanz)', 'Answer model (optional, second Ollama instance)'], ['leer = wie Haupt-KI', 'empty = same as main AI'],
    ['– fasst Suchtreffer zur gesprochenen Antwort zusammen (Antwort-Modell)', '– summarises search results into the spoken answer (answer model)'],
    ['fasst die Suchtreffer zu 1–2 gesprochenen Sätzen zusammen; Haussteuerung bleibt beim schnellen Hauptmodell',
      'summarises the search results into 1–2 spoken sentences; home control stays with the fast main model'],
    ['hilft dem Modell mit Haus-Vokabular (wird als Kontext mitgegeben)', 'helps the model with house vocabulary (passed as context)'],
    ['Helligkeitssteuerung · relative Stufen („ein bisschen / normal / richtig“)', 'Brightness control · relative steps (“a little / normal / a lot”)'],
    ['„ein bisschen“', '“a little”'], ['„viel“', '“a lot”'], ['„richtig hell / gemütlich“', '“really bright / cosy”'], ['Stufen', 'Steps'],
    ['Untergrenze', 'Lower limit'], ['Obergrenze', 'Upper limit'], ['Ausnahme „gemütlich“', 'Exception “cosy”'], ['heller +', 'brighter +'],
    ['% des Restwegs · dunkler −', '% of the remaining range · darker −'], ['% des Restwegs (100 = voll) · dunkler −', '% of the remaining range (100 = full) · darker −'],
    ['% vom aktuellen Wert', '% of the current value'], ['schon ≤', 'already ≤'], ['% → nur noch −', '% → only −'],
    ['% (damit „gemütlich“ ein schon dunkles Licht nicht fast ausschaltet)', '% (so “cosy” doesn’t almost switch off an already dim light)'],
    ['% — „dunkler“ endet nie darunter; ganz aus nur mit „Licht aus“', '% — “darker” never goes below; fully off only with “light off”'],
    ['Beispiel (Standard): 100 % + „ein bisschen dunkler“ → 70 % · 100 % + „gemütlich“ → 40 % · bei 30 % + „gemütlich“ → 23 %. Ausgeschaltete Lichter: „ein bisschen heller“ → 30 %, „richtig hell“ → 100 %.',
      'Example (default): 100 % + “a little darker” → 70 % · 100 % + “cosy” → 40 % · at 30 % + “cosy” → 23 %. Lights that are off: “a little brighter” → 30 %, “really bright” → 100 %.'],
    ['Lautstärke per Sprache („lauter / ein bisschen leiser / viel lauter“)', 'Volume by voice (“louder / a little quieter / much louder”)'],
    ['Punkte auf der Skala 0–100 (feste Schritte, keine Prozent vom aktuellen Wert)', 'points on the 0–100 scale (fixed steps, not percent of the current value)'],
    ['Sprachbefehle stellen nie lauter als diesen Wert („volle Pulle“-Schutz); die Oberfläche ist nicht begrenzt',
      'voice commands never set louder than this (“full blast” protection); the UI is not limited'],
    ['Textfelder/Auswahl → Speichern (gilt für alle Unterreiter) · Schalter speichern sofort', 'text fields/selections → Save (applies to all sub-tabs) · switches save immediately'],
    ['Gespeichert ✓ (wirkt sofort; Modell-/Wake-Änderungen brauchen „Neustart“)', 'Saved ✓ (applies immediately; model/wake changes need “Restart”)'],
    ['Neustart des Sprachdienstes nötig', 'Voice service restart required'], ['… lädt beim ersten Audio', '… loads on first audio'],
    ['Pflicht', 'required'], ['min', 'min'], ['Abzug', 'Deduction'], ['Breite', 'Width'], ['Länge', 'Length'],
    ['). Wiedergabe: Der Browser-Mikrofon-Test spielt die Antworten ab; eigene Mikrofon-Geräte bekommen nach',
      '). Playback: the browser microphone test plays the answers; own microphone devices receive after'],
    ['die WAV-Datei als Binärnachricht (hello', 'the WAV file as a binary message (hello'], ['= nur Text).', '= text only).'],
    ['– mit Login (HTTP-Basic eines Benutzers, am besten ein eigener Benutzer „mikrofon-küche“ mit Rolle user), hello',
      '– with login (HTTP Basic of a user, ideally a dedicated user “mic-kitchen” with role user), hello'],
    ['Ab dem ersten Benutzer gilt Login-Pflicht. Eine Anmeldung gilt 1 Jahr und verlängert sich bei jeder Benutzung (Wandtablets bleiben angemeldet); „Geräte abmelden" beendet sie sofort. Das erste Konto wird immer',
      'From the first user on, login is required. A sign-in lasts 1 year and is extended on every use (wall tablets stay signed in); “Sign out devices” ends it immediately. The first account is always'],
    ['– aus', '– off'],
    // Login-Seite
    ['Bitte anmelden', 'Please sign in'], ['Anmelden', 'Sign in'], ['Anmeldung fehlgeschlagen', 'Sign-in failed'],
    ['Bridge nicht erreichbar', 'Bridge not reachable'],
    // Admin › Einrichtung (Nachträge 22.10.)
    ['‹ Zurück', '‹ Back'], ['Zurück', 'Back'], ['Sprachsteuerung', 'Voice control'], ['Gegenprüfung', 'Cross-check'],
    ['BluOS, KODI → Einrichtung › Module · Geräte → Einrichtung › Geräte', 'BluOS, KODI → Setup › Modules · Devices → Setup › Devices'],
    ['von der Bridge nicht als Gerät geführt (Module, Agenten, Quellen, Bedienteile, Kandidaten)',
      'not handled as devices by the bridge (modules, agents, sources, keypads, candidates)'],
    ['Gerät, ID, Raum, Proxy, Treiber suchen …', 'Search device, ID, room, proxy, driver …'],
    ['Allerwelts-Proxy – Typ über den Treiber-Dateinamen', 'Generic proxy – type taken from the driver file name'],
    ['per Sprache ansprechbar – speichert sofort', 'controllable by voice – saves immediately'], ['per Sprache ansprechbar', 'controllable by voice'],
    ['Name anklicken = Gerät untersuchen (Variablen, Befehle, Push-Mitschnitt). Erkannt wird über den Control4-Proxy; „über Treiber“ = Allerwelts-Proxy, Typ kommt aus dem Treiber-Dateinamen.',
      'Click a name to inspect the device (variables, commands, push capture). Detection uses the Control4 proxy; “via driver” = generic proxy, the type comes from the driver file name.'],
    ['Module sprechen die eigene Schnittstelle eines Geräts (BluOS, KODI) – zusätzlich zu dem, was der Control4-Treiber liefert. Neue Treiber ohne eigenes Modul: Einrichtung › Geräte › „Gerät anlernen“.',
      'Modules talk to a device’s own interface (BluOS, KODI) – in addition to what the Control4 driver provides. New drivers without their own module: Setup › Devices › “Teach device”.'],
    ['(oder', '(or'],
    ['1 = schnell, legt jedes Wort sofort fest · 5 = wägt fünf Lesarten ab, besser bei Namen', '1 = fast, fixes each word immediately · 5 = weighs five readings, better for names'],
    ['Beispielsätze für die KI (Licht, Rollläden, Medien)', 'Example sentences for the AI (lights, blinds, media)'],
    ['Eure eigenen Wörter, die oft falsch erkannt werden (Namen, Geräte, Interpreten). Raum- und Gerätenamen kommen automatisch dazu – alles zum Wortschatz: c4bridge/WORTSCHATZ.md',
      'Your own words that are often misrecognised (names, devices, artists). Room and device names are added automatically – more on the vocabulary: c4bridge/WORTSCHATZ.md'],
    ['Nur ausführen, wenn die Anrede („Jarvis“) auch im erkannten Text steht – sonst Fehlalarm verwerfen',
      'Only execute if the wake name (“Jarvis”) also appears in the recognised text – otherwise discard as a false alarm'],
    ['Schwelle 0,1–0,9 – niedriger = hört dich eher, löst aber öfter fälschlich aus (Standard 0,5)',
      'Threshold 0.1–0.9 – lower = hears you more easily, but triggers falsely more often (default 0.5)'],
    ['So soll die KI auf typische Sätze reagieren – je Zeile „Satz → Werkzeug(…)“. Eigene Formulierungen einfach ergänzen (z. B. wie ihr zu Hause sprecht). Stehen im zwischengespeicherten Teil der Anweisungen und kosten daher kaum Zeit. Erst nach „Speichern“ aktiv, wirkt ab dem nächsten Befehl.',
      'How the AI should respond to typical sentences – one per line, “sentence → tool(…)”. Simply add your own phrasings (e.g. how you talk at home). They sit in the cached part of the instructions and therefore cost hardly any time. Active only after “Save”, applies from the next command.'],
    ['Threads für die Erkennung auf der CPU (Standard 4; 8–12 deutlich schneller, darüber kaum noch)',
      'Threads for recognition on the CPU (default 4; 8–12 much faster, little gain beyond that)'],
    ['größeres Modell = bessere Erkennung · Wechsel wirkt nach „Speichern“ ab dem nächsten Satz',
      'larger model = better recognition · a change applies after “Save” from the next sentence'],
    ['leer = Standard bin/tts/qwen3 (mit .venv und model-base)', 'empty = default bin/tts/qwen3 (with .venv and model-base)'],
    ['was in der Vorlage gesagt wird', 'what is said in the reference clip'],
    ['GPU · Qwen3-TTS (eigener Dienst, ~1,4 GB)', 'GPU · Qwen3-TTS (separate service, ~1.4 GB)'],
    ['CPU · Piper (sofort)', 'CPU · Piper (instant)'], ['GPU · Pocket TTS (viele Stimmen, ~0,3 s je Satz)', 'GPU · Pocket TTS (many voices, ~0.3 s per sentence)'],
    ['CPU · Whisper large-v3-turbo deutsch (primeline) – ~2,5 s je Satz', 'CPU · Whisper large-v3-turbo German (primeline) – ~2.5 s per sentence'],
    ['CPU · Whisper large-v3-turbo – genau, ~2,5 s je Satz', 'CPU · Whisper large-v3-turbo – accurate, ~2.5 s per sentence'],
    ['CPU · Whisper medium – genauer, langsam', 'CPU · Whisper medium – more accurate, slow'],
    ['GPU · Whisper turbo deutsch (DirectML) – ~0,4 s je Satz', 'GPU · Whisper turbo German (DirectML) – ~0.4 s per sentence'],
    ['GPU · Whisper turbo deutsch (Vulkan) – ~0,3 s je Satz', 'GPU · Whisper turbo German (Vulkan) – ~0.3 s per sentence'],
    ['GPU · Whisper turbo mehrsprachig (Vulkan) – ~0,3 s, englische Namen/Titel besser', 'GPU · Whisper turbo multilingual (Vulkan) – ~0.3 s, better for English names/titles'],
    ['Interner Port', 'Internal port'], ['Varianten (Beam)', 'Variants (beam)'], ['eigene Anweisungen', 'custom instructions'],
    ['konto@example.de', 'account@example.com'],
    ['Sprache des Assistenten', 'Assistant language'], ['Empfindlichkeit', 'Sensitivity'],
    ['Erkennung, Antworten, Kurzbefehle und Anweisungen an die KI – die Anweisungen sind je Sprache getrennt gespeichert · speichert sofort',
      'Recognition, answers, short commands and instructions for the AI – instructions are stored separately per language · saves immediately'],
    // Wetter (WMO-Texte aus assist.py – der Server liefert Deutsch, weil die Sprachausgabe sie vorliest)
    ['klar', 'clear'], ['überwiegend klar', 'mostly clear'], ['teils bewölkt', 'partly cloudy'], ['bedeckt', 'overcast'],
    ['Nebel', 'fog'], ['Nebel mit Reif', 'freezing fog'], ['leichter Nieselregen', 'light drizzle'], ['Nieselregen', 'drizzle'],
    ['starker Nieselregen', 'heavy drizzle'], ['gefrierender Nieselregen', 'freezing drizzle'], ['leichter Regen', 'light rain'],
    ['Regen', 'rain'], ['starker Regen', 'heavy rain'], ['gefrierender Regen', 'freezing rain'], ['leichter Schneefall', 'light snow'],
    ['Schneefall', 'snow'], ['starker Schneefall', 'heavy snow'], ['Schneegriesel', 'snow grains'], ['Regenschauer', 'rain showers'],
    ['kräftige Regenschauer', 'heavy rain showers'], ['heftige Regenschauer', 'violent rain showers'], ['Schneeschauer', 'snow showers'],
    ['kräftige Schneeschauer', 'heavy snow showers'], ['Gewitter', 'thunderstorm'], ['Gewitter mit Hagel', 'thunderstorm with hail'],
    // Mikrofon-Test (voicetest.html)
    ['c4voice · Mikrofon-Test', 'c4voice · Microphone test'],
    ['Verbinde mit dem Sprachdienst und rede – Wake-Word: „hey jarvis“. Der Trockenlauf entscheidet nur, das Haus wird nicht geschaltet.',
      'Connect to the voice service and talk – wake word: “hey jarvis”. Dry run only decides; nothing in the house is switched.'],
    ['Wake-Word (dauerhaft)', 'Wake word (always on)'], ['nicht verbunden', 'not connected'], ['Name dieses Mikrofons', 'Name of this microphone'],
    ['– eindeutig je Gerät (z. B. „Handy Alex“, „Tablet Küche“); die Raumzuordnung im Admin hängt daran',
      '– unique per device (e.g. “Phone Alex”, “Tablet kitchen”); the room assignment in the admin depends on it'],
    ['Mikrofon starten/stoppen', 'Start/stop microphone'], ['Bereit', 'Ready'], ['Mikrofon anklicken zum Verbinden', 'Click the microphone to connect'],
    ['Verstärkung', 'Gain'], ['– Handy-Mikrofone sind leise; so weit aufdrehen, dass der Balken beim Sprechen gut ausschlägt',
      '– phone microphones are quiet; turn it up until the bar moves clearly while you speak'],
    ['Mikrofon an, „hey jarvis“ sagen, dann den Befehl. Im Trockenlauf wird nur entschieden, nicht geschaltet.',
      'Microphone on, say “hey jarvis”, then the command. Dry run only decides, nothing is switched.'],
    ['Mikrofon an, Button drücken, Befehl sagen, Button wieder drücken.', 'Microphone on, press the button, say the command, press the button again.'],
    ['Dieser Browser hat keinen Mikrofon-Zugriff (getUserMedia fehlt) – Chrome/Edge/Firefox verwenden.',
      'This browser has no microphone access (getUserMedia missing) – use Chrome/Edge/Firefox.'],
    ['WebSocket-Fehler – angemeldet? läuft c4voice? (Admin → Config → Sprache)', 'WebSocket error – signed in? is c4voice running? (Admin → Setup → Voice control)'],
    ['Trockenlauf AN – Befehle werden nur simuliert', 'Dry run ON – commands are only simulated'],
    ['Trockenlauf AUS – Befehle schalten ECHT im Haus', 'Dry run OFF – commands REALLY switch things in the house'],
    ['⚠ kein Raum zugeordnet – Admin → Config → Sprache → Mikrofone & Räume (Befehle ohne Raumnennung gehen sonst nicht)',
      '⚠ no room assigned – Admin → Setup → Voice control → Microphones + rooms (otherwise commands without a room name do not work)'],
    ['Ich höre zu …', 'Listening …'], ['transkribiere …', 'transcribing …'], ['denkt nach …', 'thinking …'], ['führt aus …', 'executing …'],
    ['aufnahme …', 'recording …'], ['verbinde …', 'connecting …'], ['Erkannt', 'Recognised'], ['(Stille)', '(silence)'],
    ['nur das Aktivierungswort gehört – jetzt den Befehl sprechen (6 s)', 'only the wake word was heard – say the command now (6 s)'],
    ['Entscheidung (Trockenlauf)', 'Decision (dry run)'], ['Ausgeführt', 'Executed'], ['Sprachausgabe', 'Speech output'], ['Modus', 'Mode'],
    ['Verbindung', 'Connection'], ['Mikrofon', 'Microphone'],
    ['Rückfrage – einfach antworten (ohne Wake-Word, 8 s)', 'Follow-up question – just answer (no wake word, 8 s)'],
    ['Höre auf „hey jarvis“ …', 'Listening for “hey jarvis” …'], ['Nimm auf – beim Sprechen roten Button drücken zum Senden', 'Recording – press the red button to send'],
  ];

  // ---- ganze Meldungen mit Platzhaltern (BluOS-Hinweise vom Server) – vor den Teiltexten angewendet ------------
  const PRE = [
    [/^„(.+)“: Control4 kennt (.+?), der Player ist jetzt unter (\S+) erreichbar \(DHCP\?\) – Bridge nutzt (\S+)\.$/,
      '“$1”: Control4 knows $2, but the player is now reachable at $3 (DHCP?) – the bridge uses $4.'],
    [/^„(.+)“: MAC laut Control4 \((.+?)\) passt nicht, Player „(.+)“ über den Namen gefunden\.$/,
      '“$1”: the MAC known to Control4 ($2) does not match; player “$3” found by name.'],
    [/^Player „(.+)“ \(Treiber #(\d+), (.+?), MAC (.+?)\) ist nicht erreichbar\.$/, 'Player “$1” (driver #$2, $3, MAC $4) is not reachable.'],
    [/^Endpunkt „(.+)“ \(#(\d+)\) ist in Composer keinem Raum als Audio-Endpunkt zugeordnet\.$/,
      'Endpoint “$1” (#$2) is not assigned to any room as an audio endpoint in Composer.'],
    [/^Control4-Quelle „(.+)“ \((.+)\) hat keinen passenden BluOS-Dienst\.$/, 'Control4 source “$1” ($2) has no matching BluOS service.'],
    [/^Control4-Quelle „(.+)“: Dienst (.+) ist auf keinem Player eingerichtet\.$/, 'Control4 source “$1”: service $2 is not set up on any player.'],
    [/^Raum „(.+)“: Player „(.+)“ \((.+)\) nur über den Namen zugeordnet – in Control4 gehört kein erreichbarer Player-Treiber zu diesem Raum\.$/,
      'Room “$1”: player “$2” ($3) matched by name only – no reachable player driver belongs to this room in Control4.'],
    [/^Player „(.+)“ \((.+)\) gehört zu keinem Raum\.$/, 'Player “$1” ($2) does not belong to any room.'],
    // Mikrofon-Test
    [/^erkannt \((.+)\) – jetzt sprechen$/, 'detected ($1) – speak now'], [/^Gesprochen \((.+) s\)$/, 'Spoken ($1 s)'],
    [/^✓ Mikrofon zugeordnet: (.+)$/, '✓ Microphone assigned: $1'], [/^(\d+) Audio-Pakete gesendet$/, '$1 audio packets sent'],
    [/^Mikrofon heißt jetzt „(.+)“ – im Admin \(Sprache › Mikrofone & Räume\) einem Raum zuordnen; gilt ab dem nächsten Verbinden\.$/,
      'Microphone is now called “$1” – assign it to a room in the admin (Voice control › Microphones + rooms); applies from the next connection.'],
    [/^Abspielen blockiert: (.+)$/, 'Playback blocked: $1'],
    [/^Zugriff verweigert\/fehlgeschlagen: (.+) — ggf\. in einem normalen Browser-Fenster \(Chrome\/Edge\) öffnen\.$/,
      'Access denied/failed: $1 — try opening it in a normal browser window (Chrome/Edge).'],
    [/^Der Browser gibt das Mikrofon über (\S+) nicht frei \(nur unter https:\/\/ oder localhost\)\. Abhilfe: Bridge per HTTPS erreichen oder in Chrome (\S+) mit (\S+) freischalten\.$/,
      'The browser does not allow the microphone over $1 (only on https:// or localhost). Fix: reach the bridge over HTTPS, or allow $3 in Chrome at $2.'],
    [/^(.+) · Sprachausgabe an$/, (m, a) => tr(a) + ' · speech output on'],
  ];

  // ---- Teiltexte in längeren/zusammengesetzten Texten (z. B. „#260 · 14 Geräte", Statuszeilen) ----------------
  const PART = [
    ['Oberfläche(n) verbunden', 'UI(s) connected'], ['Director-Abrufe', 'Director requests'], ['aus Zwischenspeicher', 'from cache'],
    ['Ereignisse ausgewertet', 'events evaluated'], ['ignoriert', 'ignored'], ['Items abonniert', 'items subscribed'],
    ['Token-Erneuerung alle', 'token renewal every'], ['Push-first:', 'Push-first:'], ['aus – Abfrage alle', 'off – polling every'],
    ['ausgeblendet', 'hidden'], ['Geräte', 'devices'], ['Titel', 'titles'], ['Alben', 'albums'], ['Autostart an', 'autostart on'],
    ['Stand ', 'As of '], ['Import ', 'Import '], ['seit', 'since'], ['direkt aus Ereignis', 'directly from event'], ['Raum nachlesen', 're-read room'],
    ['(auch Räume', '(also rooms'], ['Raum an/aus über', 'room on/off via'], ['Raum an', 'room on'], ['Raum aus', 'room off'],
    ['Lautstärke', 'volume'], ['stumm', 'muted'], ['ein ', 'in '], [' aus ', ' out '], ['Status', 'Status'],
    ['Push ✓', 'push ✓'], ['Audio-Endpunkt', 'Audio endpoint'], ['Video-Endpunkt', 'Video endpoint'], ['Lautstärke Sehen', 'Volume watching'],
    ['läuft', 'running'], ['verbunden', 'connected'], ['getrennt', 'disconnected'], ['Räume', 'rooms'], ['neue', 'new'],
    ['männlich', 'male'], ['weiblich', 'female'], ['sehr klein', 'very small'], ['beste Qualität, etwas langsamer', 'best quality, a bit slower'],
    ['klar (Empfehlung)', 'clear (recommended)'], ['lebhafter', 'livelier'], ['mehrere Sprecher', 'several speakers'],
    ['lädt beim ersten Sprechen', 'loads on first speech'], ['lädt beim ersten Audio', 'loads on first audio'],
    ['Dienst läuft', 'service running'], ['Dienst startet …', 'service starting …'], ['geladen', 'loaded'],
    ['wechselt beim nächsten Satz', 'switches with the next sentence'], ['noch aktiv', 'still active'],
    ['auf der GPU', 'on the GPU'], ['nicht geladen (lädt beim ersten Satz)', 'not loaded (loads on first sentence)'],
    ['Ollama nicht erreichbar', 'Ollama not reachable'],
    ['bis zum Ton', 'until sound'], ['Fehler', 'Error'], ['Gespeichert', 'Saved'], ['Ort', 'Place'],
    ['aktiv', 'active'], [', wenn HTTPS eingerichtet ist)', ', if HTTPS is set up)'], ['Englisch', 'English'],
  ];
  const REGEX = [
    [/Ausgang (\d+) passte nicht zum Raum/g, 'Output $1 did not match the room'], [/Ausgang (\d+)/g, 'output $1'],
    [/^über (MAC|IP|Name)$/, 'via $1'],
    [/\bvor (\d+) s\b/g, '$1 s ago'], [/\bzuletzt\b/g, 'last'], [/\b(\d+) Räume, (\d+) Geräte/g, '$1 rooms, $2 devices'],
  ];

  const norm = s => s.replace(/\s+/g, ' ').trim();
  const MAP = new Map(EXACT.map(([d, e]) => [norm(d), e]));
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Teiltexte: nur als ganze Wörter (keine Buchstaben direkt davor/danach), längste zuerst
  const PARTS = [...EXACT.filter(([d]) => d.length >= 12), ...PART].sort((a, b) => b[0].length - a[0].length)
    .map(([d, e]) => [new RegExp('(?<![\\p{L}])' + esc(d) + '(?![\\p{L}])', 'gu'), e]);

  function tr(s) {
    if (!s || !/[A-Za-zÄÖÜäöüß]/.test(s)) return s;
    const n = norm(s);
    const hit = MAP.get(n);
    if (hit != null) return s.replace(n, hit) === s ? hit : s.replace(n, hit);
    for (const [re, e] of PRE) if (re.test(n)) return n.replace(re, e);
    let out = s;
    for (const [re, e] of PARTS) if (re.test(out)) { re.lastIndex = 0; out = out.replace(re, e); }
    for (const [re, e] of REGEX) out = out.replace(re, e);
    return out;
  }
  window.c4tr = tr;

  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'PRE', 'CODE']);
  function walk(root) {
    if (root.nodeType === 3) { fixText(root); return; }
    if (root.nodeType !== 1 || root.closest('[data-notr], #t-log, #logbox')) return;
    for (const a of ['placeholder', 'title', 'aria-label']) {   // auch bei Textfeldern (Platzhalter), deren Inhalt bleibt
      const v = root.getAttribute && root.getAttribute(a);
      if (v) { const t = tr(v); if (t !== v) root.setAttribute(a, t); }
    }
    if (SKIP.has(root.tagName)) return;
    for (const c of root.childNodes) walk(c);
  }
  function fixText(n) {
    const p = n.parentElement;
    if (!p || SKIP.has(p.tagName) || p.closest('[data-notr], #t-log, #logbox')) return;
    const t = tr(n.nodeValue);
    if (t !== n.nodeValue) n.nodeValue = t;
  }
  let busy = false;
  const mo = new MutationObserver(list => {
    if (busy) return;
    busy = true;
    try {
      for (const m of list) {
        if (m.type === 'characterData') fixText(m.target);
        else if (m.type === 'attributes') walk(m.target);
        else m.addedNodes.forEach(walk);
      }
    } finally { busy = false; }
  });
  const start = () => {
    document.title = tr(document.title);
    walk(document.body);
    mo.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true,
      attributeFilter: ['placeholder', 'title', 'aria-label'] });
  };
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
  for (const f of ['alert', 'confirm', 'prompt']) {
    const orig = window[f].bind(window);
    window[f] = (msg, ...rest) => orig(typeof msg === 'string' ? tr(msg) : msg, ...rest);
  }
})();
