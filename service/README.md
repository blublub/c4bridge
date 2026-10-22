# c4bridge als Windows-Dienst

**Einrichten:** Rechtsklick auf `install.ps1` → „Mit PowerShell ausführen“ → UAC bestätigen. Läuft die Bridge noch
von Hand (Port 8080), fragt das Skript, ob es sie beenden darf. Danach startet die Bridge mit Windows (verzögert),
auch ohne Anmeldung, und nach einem Absturz von selbst neu (10 s / 30 s / 60 s).

**Entfernen:** `uninstall.ps1` (gleich wie oben). Programm, Daten und Einstellungen bleiben; von Hand: `run.bat`.

**Bedienen (ohne Admin – das Installationsskript gibt deinem Benutzer das Recht):**

```
sc query c4bridge
sc stop c4bridge
sc start c4bridge
```

c4voice allein neu starten geht wie bisher über Admin › Sprache › Neustart (ohne den Dienst anzufassen).

**Wie er läuft:**
- Konto **SYSTEM** – kein Passwort nötig. Einstellungen und HTTPS-Zertifikat kommen trotzdem aus deinem
  Benutzerordner (`C4BRIDGE_CONFIG`, `BRIDGE_SSL_CERT/KEY` in `c4bridge-dienst.xml`) – die Handys behalten ihr
  bestätigtes Zertifikat.
- Startbefehl wie `run.bat`, aber `--host 0.0.0.0` (LAN) und HTTPS 8443.
- Logs: `service\logs\c4bridge-dienst.out.log` / `.err.log` (rollierend, 5 × 5 MB) und `.wrapper.log`.
- Beim Stoppen beendet WinSW auch c4voice und den Whisper-Dienst.

**Zu wissen:**
- **Ollama** ist ein normales Programm und startet erst mit deiner Anmeldung. Bis dahin läuft die Bridge, der
  Sprachdienst entscheidet aber nichts (Admin zeigt „Ollama nicht erreichbar“).
- Die Spracherkennung auf der Grafikkarte läuft im Dienst in „Sitzung 0“. Sollte Vulkan dort nicht gehen, erkennt
  c4voice automatisch mit Canary auf der CPU weiter – im Admin-Status sichtbar („Dienst startet …“ bleibt stehen).
  Nach dem Einrichten einmal prüfen.

**Dateien:** `c4bridge-dienst.exe` = WinSW v2.12.0 (github.com/winsw/winsw, MIT-Lizenz, unsigniert),
SHA256 `05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da` – `install.ps1` prüft sie.
`c4bridge-dienst.vorlage.xml` = Vorlage; `c4bridge-dienst.xml` erzeugt `install.ps1`.
