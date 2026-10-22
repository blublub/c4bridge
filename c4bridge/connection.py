"""Long-lived Control4 connection with token caching.

Logging in through the Control4 cloud is the slow part. We do it once, keep
the director token in memory and renew it before it expires. Everything else
is a local HTTPS call to the Director.

The token is also stored in data/director_token.json (with its expiry), so a
bridge restart reuses it instead of logging in to the cloud again (the account
locks after too many logins in a row).
"""
import asyncio
import json
import ssl
import time
from typing import Any, Optional

import aiohttp
from pyControl4.account import C4Account
from pyControl4.director import C4Director
from pyControl4.error_handling import BadToken, Unauthorized

from . import logbus
from .config import Config, DATA_DIR

TOKEN_FILE = DATA_DIR / "director_token.json"
MIN_LEFT_S = 30 * 60   # gespeicherten Token nur nutzen, wenn er noch mindestens 30 min gilt

REFRESH_FRACTION = 0.8  # renew after 80 % of the token's lifetime


class Connection:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session: Optional[aiohttp.ClientSession] = None
        self.director: Optional[C4Director] = None
        self.controller_name = ""
        self.expires_at = 0.0
        self.valid_seconds = 0
        self.last_login = 0.0
        self.last_error = ""
        self.token_source = ""          # "cloud" | "cache"
        self._cache_tried = False
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self.director is not None and time.time() < self.expires_at

    def status(self) -> dict:
        return {
            "configured": self.cfg.c4_configured,
            "connected": self.connected,
            "controller": self.controller_name,
            "ip": self.cfg.c4_ip,
            "token_valid_seconds": self.valid_seconds,
            "token_expires_in": max(0, int(self.expires_at - time.time())) if self.director else 0,
            "last_login": self.last_login,
            "last_error": self.last_error,
            "token_source": self.token_source,
        }

    def _ensure_session(self) -> None:
        if self.session is None:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False  # Director uses a self-signed certificate
            ctx.verify_mode = ssl.CERT_NONE
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ctx))

    def _save_token(self, token: str, issued: float) -> None:
        try:
            from .auth import atomic_write
            atomic_write(TOKEN_FILE, json.dumps({
                "token": token, "issued_at": issued, "valid_seconds": self.valid_seconds,
                "controller": self.controller_name, "ip": self.cfg.c4_ip, "user": self.cfg.c4_user}))
        except Exception as e:  # noqa: BLE001
            logbus.warn("conn", f"Token nicht gespeichert: {type(e).__name__}: {e}")

    def _drop_token(self) -> None:
        try:
            TOKEN_FILE.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _load_token(self) -> bool:
        """Gespeicherten Director-Token übernehmen, wenn er zum selben Controller/Konto gehört und noch gilt."""
        try:
            t = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return False
        if t.get("ip") != self.cfg.c4_ip or t.get("user") != self.cfg.c4_user or not t.get("token"):
            return False
        valid = int(t.get("valid_seconds") or 0)
        expires = float(t.get("issued_at") or 0) + valid * REFRESH_FRACTION
        if expires - time.time() < MIN_LEFT_S:
            return False
        self._ensure_session()
        self.director = C4Director(self.cfg.c4_ip, t["token"], self.session)
        self.controller_name = t.get("controller", "")
        self.valid_seconds = valid
        self.last_login = float(t["issued_at"])
        self.expires_at = expires
        self.token_source = "cache"
        logbus.info("conn", f"Gespeicherten Director-Token übernommen (noch {int((expires - time.time()) / 3600)} h, "
                            f"kein Cloud-Login)")
        return True

    async def _login(self) -> None:
        started = time.time()
        logbus.info("conn", "Logging in to Control4 cloud ...")
        try:
            account = C4Account(self.cfg.c4_user, self.cfg.c4_password)
            await account.get_account_bearer_token()
            self.controller_name = (await account.get_account_controllers())["controllerCommonName"]
            tok = await account.get_director_bearer_token(self.controller_name)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            logbus.error("conn", f"Login failed: {self.last_error}")
            raise
        self._ensure_session()
        self.director = C4Director(self.cfg.c4_ip, tok["token"], self.session)
        self.valid_seconds = int(tok["validSeconds"])
        self.last_login = time.time()
        self.expires_at = self.last_login + self.valid_seconds * REFRESH_FRACTION
        self.last_error = ""
        self.token_source = "cloud"
        self._save_token(tok["token"], self.last_login)
        logbus.info("conn", f"Connected to {self.controller_name}; token valid {self.valid_seconds}s "
                            f"(login took {time.time() - started:.1f}s)")

    async def ensure(self) -> C4Director:
        if not self.cfg.c4_configured:
            raise RuntimeError("Control4 credentials not configured")
        if not self.connected:
            async with self._lock:
                if not self.connected:
                    if not self._cache_tried:
                        self._cache_tried = True          # nur beim ersten Mal (Start), danach normal erneuern
                        if self._load_token():
                            return self.director
                    await self._login()
        assert self.director is not None
        return self.director

    async def relogin(self) -> str:
        """Force a fresh cloud login (new director token); returns the token.

        Used by the websocket manager so the 24 h token never expires while
        the subscription is running.
        """
        async with self._lock:
            await self._login()
        assert self.director is not None
        return self.director.director_bearer_token

    async def _call(self, fn_name: str, *args) -> Any:
        for attempt in (1, 2):
            director = await self.ensure()
            try:
                return json.loads(await getattr(director, fn_name)(*args))
            except (Unauthorized, BadToken):
                if attempt == 2:
                    raise
                logbus.warn("conn", "Token rejected, logging in again")
                self.expires_at = 0
                self._drop_token()

    async def get(self, uri: str) -> Any:
        return await self._call("send_get_request", uri)

    async def post(self, uri: str, command: str, params: dict) -> Any:
        logbus.info("cmd", f"{command} {params} -> {uri}")
        return await self._call("send_post_request", uri, command, params)

    async def close(self) -> None:
        if self.session:
            await self.session.close()
