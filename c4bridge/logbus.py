"""In-memory log ring buffer with live subscribers (feeds the web live log)."""
import asyncio
import json
import time
from collections import deque

_buffer: deque = deque(maxlen=1000)
_subscribers: set = set()


def log(level: str, source: str, message: str, **extra) -> dict:
    entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "level": level,
             "source": source, "message": message, **extra}
    _buffer.append(entry)
    print(f"{entry['ts']} {level:5} {source}: {message}", flush=True)
    for q in list(_subscribers):
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            pass
    return entry


def info(source, message, **extra):
    return log("INFO", source, message, **extra)


def warn(source, message, **extra):
    return log("WARN", source, message, **extra)


def error(source, message, **extra):
    return log("ERROR", source, message, **extra)


def recent(n: int = 200) -> list:
    return list(_buffer)[-n:]


async def stream():
    """Async generator of server-sent-event lines: backlog first, then live."""
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    try:
        for entry in recent(100):
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
        while True:
            try:
                entry = await asyncio.wait_for(q.get(), timeout=15)
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        _subscribers.discard(q)
