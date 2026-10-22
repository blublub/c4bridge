# c4bridge – Installation guide

This guide is for setting up c4bridge on your own computer for the first time. It covers the manual way
(virtual environment + start script) and, optionally, a Windows service.

## 1. Requirements

| Part | Required? | Notes |
|---|---|---|
| Windows 10/11, Linux or macOS | yes | Developed and tested on Windows 11. Linux/macOS run through `run.sh` but are less tested |
| Python **3.11** | yes | 3.12/3.13 should work. Windows: `winget install Python.Python.3.11`, and tick "Add to PATH" |
| Control4 controller in your LAN, OS 3.x | yes | Plus a Control4 account (e-mail and password) with access to the project |
| Computer in the same LAN as the controller and the BluOS players | yes | BluOS discovery also needs multicast/mDNS (same VLAN) |
| MediaMTX | cameras only | The setup wizard downloads the right build |
| Ollama + a model | voice only | May run on another computer |
| Microphone device or browser microphone | voice only | The browser microphone needs HTTPS or `localhost` |

Disk space:
- About 200 MB for the bridge alone.
- About 2 GB with the voice packages.
- Plus the models, depending on what you choose.

## 2. Installation (Windows)

1. **Get the code.** Clone [blublub/c4bridge](https://github.com/blublub/c4bridge)
   (`git clone https://github.com/blublub/c4bridge.git C:\c4bridge`), or download it as a ZIP and unpack it.
   Do **not** copy the `.venv\` or `data\` folders from another installation. They contain machine-specific
   paths, users, tokens and project data.

2. **Create a virtual environment and install the packages.** Open a command prompt in the project folder:

   ```bat
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

   If you don't need the voice assistant, install only the first block of `requirements.txt` (down to
   `cryptography`). The voice packages download about 1 GB (CTranslate2, onnxruntime).

3. **Configuration file (optional).** It lives **outside** the project, so that credentials are not in the program folder:

   - Windows: `%USERPROFILE%\.config\c4bridge\config.env`
   - Linux/macOS: `~/.config/c4bridge/config.env`, then `chmod 600` it.

   `config.example.env` is the template. The file may stay **empty**: the setup wizard writes the Control4
   credentials for you. If you want to fill it in yourself:

   ```
   C4_USER=you@example.com
   C4_PASS=secret
   C4_IP=192.168.1.50       # controller IP (without it, the wizard finds the controller via your account)
   BRIDGE_HOST=127.0.0.1    # switch to 0.0.0.0 only after you have created a user (see section 5)
   BRIDGE_PORT=8080
   BRIDGE_SSL_PORT=8443     # optional: HTTPS in addition to HTTP (browser microphone on phones/tablets)
   ```

   Two environment variables change the default locations:
   - `C4BRIDGE_CONFIG`: another location for the config file.
   - `C4BRIDGE_DATA`: another data folder. The default is `data\` in the project.

4. **Start.** Double-click `run.bat`. Do not start `.venv\Scripts\uvicorn.exe` directly: those launchers contain
   a fixed Python path and break when the folder is moved.

5. **Open the browser at http://127.0.0.1:8080.** The setup wizard appears (section 4).

## 3. Installation (Linux / macOS)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x run.sh
./run.sh
```

On macOS you can get Python 3.11 through Homebrew. The setup wizard downloads MediaMTX for your system and CPU
(Linux amd64/arm64/armv7/armv6, macOS Intel/Apple) to `tools/mediamtx`.

## 4. First setup in the browser (setup wizard)

The wizard appears as long as no Control4 login is stored or no project has been imported.

![Setup wizard](docs/screenshots/setup.png)

1. **Language**: German or English. This applies to the UI, the admin console and the login page.
2. **Control4 account**: e-mail, password and, optionally, the controller IP.
   "Test login" does exactly **one** cloud login and shows the controller it found.
3. **Location for the weather** (optional): type a place name, click "Search" and pick a result.
   **Camera live stream** (optional): if MediaMTX is missing, the wizard offers to download it:
   - About 30 MB from GitHub.
   - Pinned version.
   - The SHA-256 is checked against the release.
4. **Save & import project**:
   - Writes `config.env`.
   - Imports the project (rooms, devices, drivers, bindings; 10–30 s).
   - Learns the endpoints and finds the BluOS players.
   - Loads the music library in the background.
   - Then the control UI opens.

The Director token is cached in `data/director_token.json`. Restarts do not need a new cloud login while the token
is valid for at least 30 more minutes. The bridge renews it by itself.

## 5. Users and LAN access

- **Without a user account**, the UI is open to anyone who can reach it. That is only acceptable on `127.0.0.1`.
- Go to Admin → **Access & users** and create the first account with the role **admin**. After that, add more
  accounts as needed. The role "user" can operate the house and keep their own favourites.
- **Only then** set `BRIDGE_HOST=0.0.0.0` (in `config.env` or Admin → Setup → Control4) and restart.
  The bridge is now reachable at `http://<computer-ip>:8080` in your LAN. Windows may ask for a firewall rule
  (private network).
- Emergency login: `BRIDGE_WEB_USER` / `BRIDGE_WEB_PASS` in `config.env` only work while **no** accounts exist.
- Passwords are stored hashed (PBKDF2) in `data/users.json`. `data/secret.key` signs the session cookies.

## 6. What is detected automatically, and what you add yourself

**Automatic:**
- Rooms and floors, with the visibility set in Composer.
- Devices by Control4 proxy:
  - lights (`light_v2`, dimmers)
  - blinds (including groups)
  - thermostats (`thermostatV2`)
  - door/window contacts and motion sensors
  - cameras
- **Endpoints per room**, from the Composer bindings. They provide volume, mute, pause and now-playing through push events.
- **Multi-zone amplifiers**: which output belongs to which room.
- **BluOS players**:
  - Found through mDNS and the driver addresses.
  - Matched to the Control4 driver by MAC address, and to the room by the endpoint.
- Source icons from the controller, album covers and weather.

**Manual (admin console):**

| Where | What |
|---|---|
| Access & users | **Create the first admin account** (section 5) |
| Devices | A note per device (for example "ceiling light dining room") and alias names. The voice assistant needs these to pick the right lamp. You can also hide devices and fix their type (switch or dimmer) |
| UI & rooms | Show or hide rooms. The "Volume" column shows whether a room gets push updates or is **polling only** (orange) |
| Cameras | RTSP URL and credentials per camera, and the mode (snapshot or live) |
| Voice | Everything for c4voice (section 7) |

If the BluOS matching is wrong, you can assign players by hand in `data/bluos.json`:
`"overrides": {"<room-id>": "<ip>"}`.

![Admin console](docs/screenshots/admin.png)

## 7. Voice assistant c4voice (optional)

You talk to c4voice the way you talk at home:
- **Wake word and command in one breath**, no pause and no beep (*"Hey Jarvis, lights on"*).
- **Everyday phrases instead of device names**: *"a bit brighter"*, *"too dark"*, *"blinds down"*, *"louder"*.
  The microphone's room is the default.

**Recommended:** Whisper on the GPU (much better recognition than the CPU models) and Qwen3-TTS (much more natural
than Piper). Neither is set up by the admin. Both are described in the README under
"Recommended GPU components". The admin itself downloads:
- Piper voices
- the wake word models
- the CPU Whisper models

None of the components needs a Hugging Face login.

1. **Install Ollama** from [ollama.com](https://ollama.com) and pull a model:

   ```bash
   ollama pull gemma4:12b
   ```

   Recommended environment variables for Ollama (restart Ollama afterwards):
   - `OLLAMA_FLASH_ATTENTION=1`
   - `OLLAMA_KV_CACHE_TYPE=q8_0`
   - `LLAMA_ARG_SWA_FULL=1`

   If Ollama runs on another computer, set `OLLAMA_HOST=0.0.0.0` there.
2. Admin → **Voice control**:
   - **LLM**:
     - The URL (`http://localhost:11434` or the other computer's URL), the model, and a context of 12288.
     - Prompts: the house prompt and the example sentences. `tools/prompts/de/` has the tested German template,
       `tools/prompts/en/` an English one.
   - **Speech recognition**:
     - **Recommended**: "GPU · Whisper turbo German (Vulkan)", or "multilingual" for English. It needs
       `bin/whisper-vulkan/whisper-server.exe` and a GGML model in `data/models/` (see the README).
     - Without a GPU: a CPU Whisper model. `base` is fast, `small` is more accurate. It downloads on first use to `data/models`.
     - The recognition language (`de`, `en`) is set here too.
   - **Wake word**: click "Load models" (about 60 MB, once). The default is `hey_jarvis`.
   - **Speech output**:
     - **Qwen3-TTS** (GPU, **recommended**, much more natural):
       - Needs the exported engine in `bin/tts/qwen3/` (see the README).
       - Pick a voice from `data/tts/qwen_ref_*.wav` and the model (0.6B recommended).
     - **Piper** (CPU): download a voice (for example `de_DE-thorsten-medium`, about 60 MB) and click "Listen".
       Piper is also the fallback when Qwen3 is not available.
   - Leave **dry_run** on until the commands look right. In dry-run mode nothing in the house is switched.
   - Enable the service. The bridge starts `python -m c4bridge.voice` (port 8090, localhost only, protected by a token) and watches it.
3. **Microphones**: you have two options.
   - Open the "Microphone test" page (`/voicetest`). Outside `localhost` this needs HTTPS.
   - Or use your own device that sends PCM16 / 16 kHz / mono over a WebSocket to `/api/voice/mic`.
     `tools/feed_voice.py` is an example.

   Every microphone that connects appears under "Microphones + rooms". Assign 1–3 rooms to each.
4. Weather location and web search (SearXNG or exa.ai) are under Voice control → Knowledge & weather.
5. **Language**: "Assistant language" (Speech recognition tab) switches between German and English. This covers
   recognition, spoken answers, short commands and the LLM instructions. See "Languages" in the README.

![Voice settings](docs/screenshots/voice.png)

**Hardware guide for the full voice stack (gemma4:12b + Whisper on the GPU + Qwen3-TTS):** at least **16 GB VRAM**.
- Measured:
  - Windows and open apps: about 4.8 GB
  - gemma: about 7.4 GB
  - Whisper: about 1.2 GB
  - Qwen3-TTS 0.6B: about 0.8 GB
- With less VRAM, Windows swaps to system memory and every answer takes several seconds longer.
- Smaller setups: a smaller LLM, Whisper on the CPU and Piper.
- Without a dedicated GPU, use one of these:
  - an Ollama server elsewhere in your network
  - an integrated GPU through Vulkan

## 8. Run as a Windows service (optional)

1. Download [WinSW v2.12.0](https://github.com/winsw/winsw/releases/tag/v2.12.0) (`WinSW-x64.exe`).
2. Save it as `service\c4bridge-dienst.exe`.
3. Right-click `service\install.ps1` → "Run with PowerShell". It asks for administrator rights, checks the SHA-256
   of WinSW, and creates the service from the template `c4bridge-dienst.vorlage.xml`.
4. To remove the service, run `service\uninstall.ps1`.

When the service stops, WinSW also stops c4voice and the Whisper server. On Linux, use a systemd unit that runs `run.sh`.

## 9. Operation

- **Restart**: close the `run.bat` window and start it again. Make sure the child processes (`c4bridge.voice`,
  `mediamtx`) are gone too. Otherwise ports 8080/8090 stay in use.
- **Updates**:
  1. Replace the program files (`c4bridge\`, `tools\`, `run.*`, `requirements.txt`).
  2. Keep `data\` and `config.env`.
  3. Run `pip install -r requirements.txt` again.
- **Logs**: Admin → Log (live), and `data/bridge.log`.
- **Push status**: Admin → Setup → Control4 → "WebSocket push". Without push, the bridge polls every 4 s.
- **Re-import**: after changes in Composer, go to Admin → Status → "Re-import devices". Otherwise the bridge re-imports
  automatically every `C4_SCAN_MIN` minutes (default 1440).

## 10. Troubleshooting

| Problem | Cause / fix |
|---|---|
| `.venv missing` on start | Step 2.2 was skipped, or the venv was copied from another folder. Create it again |
| `Address already in use` / port 8080 | An old bridge is still running. Look for `python.exe` running `uvicorn c4bridge` in Task Manager |
| Login in the wizard fails | The account has no access to the project, or there were too many cloud logins in a short time. Wait a few minutes |
| KODI shows red ("not reachable") | In KODI, go to Settings → Services → Control and enable "Allow remote control via HTTP" and "Allow remote control from applications on other systems" |
| No BluOS player found | The computer is in another VLAN/Wi-Fi without mDNS. The bridge also tries the IPs from the Control4 drivers. Otherwise use `overrides` in `data/bluos.json` |
| Room is "polling only" (orange) | The volume device sends no events (unknown proxy). Add a profile with `push` fields to `data/drivers.json`, or use the driver learning wizard |
| Browser microphone silent / "getUserMedia undefined" | Browsers allow the microphone only on `localhost` or over HTTPS. Set `BRIDGE_SSL_PORT=8443` and use `https://<bridge-ip>:8443` (accept the certificate once) |
| Browser shows "not secure" despite HTTPS | The certificate is self-signed. For no warning, use your own CA (for example `mkcert`) on all devices and point `BRIDGE_SSL_CERT` / `BRIDGE_SSL_KEY` to it |
| Camera live stream missing | MediaMTX is missing (the wizard downloads it), or the RTSP credentials are wrong. Use "Test" on the camera tab |
| Voice commands don't switch anything | `dry_run` is on (intended while testing). See Admin → Voice control |
