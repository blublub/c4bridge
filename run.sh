#!/usr/bin/env bash
# Start c4bridge (Linux/macOS). Settings: ~/.config/c4bridge/config.env (see config.example.env)
# Kamera-Livestream: tools/mediamtx (ohne .exe) – lädt der Einrichtungsassistent passend zu OS/CPU.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo ".venv fehlt. Einmalig einrichten:"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/python -m pip install -r requirements.txt"
    exit 1
fi
exec .venv/bin/python -m uvicorn c4bridge.app:app --host "${BRIDGE_HOST:-127.0.0.1}" --port "${BRIDGE_PORT:-8080}" --no-access-log
