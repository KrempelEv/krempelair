"""Bedien-Panel: das Dashboard mit Steuerung, nur im LAN.

Läuft als eigener Dienst (krempelair-panel, Port 8080). Der Rückkanal zu
maurizi.ch leitet nur Port 80 weiter, das Panel ist von aussen also gar nicht
erreichbar; zusätzlich nimmt es nur Anfragen aus privaten Netzen an.

Anmeldung mit dem Telegram-Passwort, Sitzung als Cookie (HttpOnly,
SameSite=Strict). Schreibende Anfragen brauchen ausserdem den Header
X-Krempelair und, falls der Browser einen Origin schickt, denselben Host.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

from .bot import verify_password
from .config import Config
from .remote import Remote
from .store import Store

log = logging.getLogger(__name__)

COOKIE = "krempelair_session"
MAX_BODY = 4096


class Panel:
    def __init__(self, cfg: Config, clock=time.time, sleep=time.sleep):
        self.cfg = cfg
        self.clock = clock
        self.sleep = sleep
        self.sessions: Dict[str, float] = {}
        self.failures: Dict[str, list] = {}
        self.lock = threading.Lock()

    # Zugang ---------------------------------------------------------------

    @staticmethod
    def is_lan(address: str, headers) -> bool:
        if headers.get("X-Forwarded-For") or headers.get("Forwarded"):
            return False  # über einen Proxy (z. B. maurizi.ch) gekommen
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return ip.is_private and not ip.is_loopback

    def session_valid(self, cookie_header: Optional[str]) -> bool:
        token = _cookie(cookie_header, COOKIE)
        if not token:
            return False
        now = self.clock()
        with self.lock:
            expires = self.sessions.get(token)
            if expires is None or expires < now:
                self.sessions.pop(token, None)
                return False
        return True

    def login(self, address: str, password: str) -> Tuple[int, Optional[str], str]:
        """(HTTP-Status, Sitzungs-Token oder None, Meldung)."""
        now = self.clock()
        window = self.cfg.telegram.lockout_minutes * 60
        limit = self.cfg.telegram.max_login_failures
        with self.lock:
            for key in list(self.failures):
                self.failures[key] = [t for t in self.failures[key] if now - t < window]
            recent = self.failures.setdefault(address, [])
            total = sum(len(v) for v in self.failures.values())
            if len(recent) >= limit or total >= 20 * limit:
                return 429, None, "Zu viele Fehlversuche, bitte später"
        if not self.cfg.telegram.password_hash or not verify_password(
                password, self.cfg.telegram.password_hash):
            with self.lock:
                self.failures.setdefault(address, []).append(now)
            self._event("Fehlgeschlagene Anmeldung am Panel", address, "warnung")
            return 401, None, "Falsches Passwort"
        token = os.urandom(32).hex()
        with self.lock:
            # abgelaufene Sitzungen bei der Gelegenheit wegräumen
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            self.sessions[token] = now + self.cfg.panel.session_hours * 3600
        self._event("Anmeldung am Panel", address)
        return 200, token, "Angemeldet"

    def logout(self, cookie_header: Optional[str]) -> None:
        token = _cookie(cookie_header, COOKIE)
        with self.lock:
            self.sessions.pop(token or "", None)

    # Bedienung ------------------------------------------------------------

    def _store(self) -> Store:
        return Store(self.cfg.general.db_path)

    def _event(self, message: str, address: str, level: str = "info") -> None:
        try:
            store = self._store()
            try:
                store.add_event(message, "Panel %s" % address, level)
            finally:
                store.close()
        except Exception as exc:
            log.error("Ereignis nicht gespeichert: %s", exc)

    def control(self, body: Dict[str, Any], address: str) -> Tuple[bool, str]:
        source = "Panel %s" % address
        action = body.get("action")
        store = self._store()
        try:
            remote = Remote(self.cfg, store, clock=self.clock, sleep=self.sleep)
            if action == "fan":
                return remote.run_command("fan_level", _int(body, "level"), source)
            if action == "smoker":
                return remote.run_command("smoker", 1 if body.get("on") else 0, source)
            if action == "setpoint":
                return True, remote.set_setpoint(_int(body, "value"), source)
            if action == "nak":
                return True, remote.set_nak(bool(body.get("on")), source)
            if action == "nak_target":
                return True, remote.set_nak_target(_int(body, "value"), source)
            if action == "manual":
                return True, remote.set_manual(_pct(body, "wrg"), _pct(body, "le"),
                                               _int(body, "hours"), source)
            if action == "auto":
                return True, remote.clear_manual(source)
            raise ValueError("unbekannte Aktion")
        finally:
            store.close()

    # HTTP -----------------------------------------------------------------

    def handle_get(self, handler, path: str) -> bool:
        if path != "/api/session":
            return False
        lan = self.is_lan(handler.client_address[0], handler.headers)
        handler._json(200, {"panel": True, "lan": lan,
                            "authenticated": lan and self.session_valid(handler.headers.get("Cookie"))})
        return True

    def handle_post(self, handler, path: str) -> None:
        address = handler.client_address[0]
        if not self.is_lan(address, handler.headers):
            handler._json(403, {"error": "Bedienung nur im LAN"})
            return
        if handler.headers.get("X-Krempelair") != "1":
            handler._json(403, {"error": "Header fehlt"})
            return
        origin = handler.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != handler.headers.get("Host", ""):
            handler._json(403, {"error": "fremde Herkunft"})
            return
        try:
            length = int(handler.headers.get("Content-Length") or 0)
            if length > MAX_BODY or "chunked" in handler.headers.get("Transfer-Encoding", ""):
                raise ValueError
            body = json.loads(handler.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            handler.close_connection = True
            handler._json(400, {"error": "ungültige Anfrage"})
            return

        if path == "/api/login":
            status, token, message = self.login(address, str(body.get("password", "")))
            cookie = None
            if token:
                cookie = "%s=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (
                    COOKIE, token, int(self.cfg.panel.session_hours * 3600))
            handler._json(status, {"authenticated": bool(token), "message": message},
                          cookie=cookie)
            return
        if path == "/api/logout":
            self.logout(handler.headers.get("Cookie"))
            handler._json(200, {"authenticated": False},
                          cookie="%s=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0" % COOKIE)
            return
        if not self.session_valid(handler.headers.get("Cookie")):
            handler._json(401, {"error": "nicht angemeldet"})
            return
        if path == "/api/control":
            try:
                ok, message = self.control(body, address)
            except ValueError as exc:
                handler._json(400, {"ok": False, "message": str(exc)})
                return
            handler._json(200 if ok else 409, {"ok": ok, "message": message})
            return
        handler._json(404, {"error": "nicht gefunden"})


def _cookie(header: Optional[str], name: str) -> Optional[str]:
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name and value:
            return value
    return None


def _int(body: Dict[str, Any], key: str) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError("%s: ganze Zahl erwartet" % key)
    return int(value)


def _pct(body: Dict[str, Any], key: str) -> Optional[float]:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s: Prozent erwartet" % key)
    return float(value)
