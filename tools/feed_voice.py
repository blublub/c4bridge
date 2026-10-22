"""Speist eine WAV-Datei (16 kHz, mono, 16 bit) als Mikrofon über die Bridge in c4voice ein.

Zum Testen der Kette ohne echtes Mikrofon (c4voice ist nur noch über die Bridge erreichbar):
  .venv/Scripts/python.exe tools/feed_voice.py data/test_speech.wav [--bridge http://127.0.0.1:8080]
        [--user NAME --password PW]      # nur nötig, wenn Benutzer angelegt sind
ACHTUNG: Ist der Trockenlauf aus, schaltet der Befehl ECHT.
"""
import argparse
import asyncio
import json
import wave

import aiohttp


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--bridge", default="http://127.0.0.1:8080")
    ap.add_argument("--user")
    ap.add_argument("--password", default="")
    ap.add_argument("--wake", action="store_true", help="Wake-Modus: Dauer-Stream ohne start/stop")
    ap.add_argument("--simulate", action="store_true",
                    help="nur simulieren (erzwingt Trockenlauf für diese Verbindung, egal wie voice.json steht)")
    ap.add_argument("--client", default="feed_voice", help="Mikrofon-Name (Raumzuordnung im Admin)")
    ap.add_argument("--tts-out", help="gesprochene Antwort (WAV) hierhin speichern")
    ap.add_argument("--answer", help="zweite WAV: Antwort auf eine Rückfrage (ohne Wake-Word, nach 'question')")
    args = ap.parse_args()

    w = wave.open(args.wav, "rb")
    assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, \
        "benoetigt 16 kHz / mono / 16 bit"
    frames = w.readframes(w.getnframes())
    w.close()
    if args.wake:                       # Wake-Modus: Raumruhe davor/danach, damit Ende erkannt wird
        frames = b"\0" * 32000 + frames + b"\0" * 64000
    chunk = 960 * 4                     # ~120 ms
    parts = [frames[i:i + chunk] for i in range(0, len(frames), chunk)]
    print(f"{len(frames) / 32000:.1f}s Audio in {len(parts)} Chunks" + (" · nur Simulation" if args.simulate else ""))

    hdr = {}
    if args.user:
        import base64
        hdr["Authorization"] = "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
    async with aiohttp.ClientSession(headers=hdr) as s:
        async with s.ws_connect(args.bridge.rstrip("/") + "/api/voice/mic") as ws:
            await ws.send_json({"type": "hello", "client": args.client, "push_to_talk": not args.wake,
                                "simulate": args.simulate, "tts": bool(args.tts_out)})
            done = asyncio.Event()
            asked = asyncio.Event()
            n_tts = 0

            async def reader():
                nonlocal n_tts
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        j = json.loads(msg.data)
                        print("<<", json.dumps(j, ensure_ascii=False)[:400])
                        if j.get("type") in ("decision", "error"):
                            done.set()
                        if j.get("type") == "question":
                            asked.set()
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        n_tts += 1
                        out = args.tts_out if n_tts == 1 else args.tts_out.replace(".wav", f"_{n_tts}.wav")
                        with open(out, "wb") as f:
                            f.write(msg.data)
                        print(f"<< [Sprachausgabe {len(msg.data)} Bytes → {out}]")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                done.set()

            async def send(chunks):
                for p in chunks:
                    await ws.send_bytes(p)
                    await asyncio.sleep(0.12)   # echtzeitnah

            rd = asyncio.create_task(reader())
            if not args.wake:
                await ws.send_json({"type": "start"})
            await send(parts)
            if not args.wake:
                await ws.send_json({"type": "stop"})
            try:
                await asyncio.wait_for(done.wait(), 90)
            except asyncio.TimeoutError:
                print("(keine Entscheidung innerhalb 90 s)")
            try:                                # Sprachausgabe/Rückfrage folgt nach der Entscheidung
                await asyncio.wait_for(asked.wait(), 6)
            except asyncio.TimeoutError:
                pass
            if args.answer and asked.is_set():
                w2 = wave.open(args.answer, "rb")
                f2 = w2.readframes(w2.getnframes())
                w2.close()
                await asyncio.sleep(4)          # Ansage ausklingen lassen (Dienst hört währenddessen nicht zu)
                done.clear()
                print("-- Antwort auf die Rückfrage --")
                if not args.wake:
                    await ws.send_json({"type": "start"})
                await send([f2[i:i + chunk] for i in range(0, len(f2), chunk)]
                           + ([b"\0" * chunk] * 12 if args.wake else []))
                if not args.wake:
                    await ws.send_json({"type": "stop"})
                try:
                    await asyncio.wait_for(done.wait(), 90)
                except asyncio.TimeoutError:
                    print("(keine Entscheidung innerhalb 90 s)")
                await asyncio.sleep(1.5)
            rd.cancel()
            await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
