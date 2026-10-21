"""Dashboard: Anlagenschema und Verläufe, nur lesend, ohne Anmeldung."""

from __future__ import annotations

import gzip
import json
import sqlite3
import logging
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Config
from .hardware import BETRIEB_LABELS, STOERUNG_LABELS
from .store import HISTORY_FIELDS, STATE_STALE_SECONDS, StateFile, Store

log = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_static")
MAX_HOURS = 24 * 3660  # zehn Jahre; die Daten selbst begrenzt retention_days
POINT_STEPS = (250, 500, 1000, 2000, 4000)
HISTORY_SLOTS = threading.BoundedSemaphore(2)   # gleichzeitige Verlaufsabfragen
QUERY_SECONDS = 5.0
MAX_THREADS = 32


class Busy(Exception):
    """Zu viele oder zu teure Abfragen: 503 statt den Pi auszulasten."""

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    ),
}


class Dashboard:
    def __init__(self, cfg: Config, state_file: StateFile):
        self.cfg = cfg
        self.state_file = state_file
        self._cache: Dict[Tuple[Tuple[str, ...], int], Tuple[float, Dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

    def _store(self) -> Store:
        return Store(self.cfg.general.db_path, readonly=True)

    def state(self) -> Dict[str, Any]:
        ts, state = self.state_file.read()
        now = time.time()
        age = None if ts is None else round(now - ts, 1)
        return {
            "server_time": now,
            "age": age,
            "stale": age is None or age > STATE_STALE_SECONDS or not state.get("ok"),
            "state": state,
            "meta": {
                "version": __version__,
                "betrieb_labels": BETRIEB_LABELS,
                "stoerung_labels": STOERUNG_LABELS,
            },
        }

    def history(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """?start=&end= (Unixzeit) oder ?hours=, dazu points= und keys=a,b,c.
        start=first heisst: ab dem ältesten Messwert. Zu lange Zeiträume werden
        auf MAX_HOURS gekappt, nicht abgelehnt."""
        now = time.time()
        end = min(now, float(_first(query, "end", str(now))))
        wanted = int(_first(query, "points", "1200"))
        points = next((p for p in POINT_STEPS if p >= wanted), POINT_STEPS[-1])
        keys = list(dict.fromkeys(k for k in _first(query, "keys", "").split(",") if k))
        unknown = [k for k in keys if k not in HISTORY_FIELDS]
        if unknown:
            raise ValueError("unbekannte Kurve %s" % ",".join(unknown))
        start_arg = _first(query, "start", "")
        if start_arg == "first":
            # Navigator: ganzer Verlauf, grob; ändert sich kaum, darum 10 min im Speicher
            cache_key = (tuple(keys), points)
            with self._cache_lock:
                hit = self._cache.get(cache_key)
            if hit and hit[0] > now:
                return hit[1]
        if not os.path.exists(self.cfg.general.db_path):
            return {"bucket": 0, "t": [], "first": None}
        if not HISTORY_SLOTS.acquire(timeout=2):
            raise Busy()
        store = self._store()
        deadline = time.monotonic() + QUERY_SECONDS
        store.conn.set_progress_handler(lambda: time.monotonic() > deadline, 20000)
        try:
            first = store.first_sample()
            if start_arg == "first":
                start = float(first) if first is not None else end - 3600
            elif start_arg:
                start = float(start_arg)
            else:
                hours = min(MAX_HOURS, max(0.1, float(_first(query, "hours", "24"))))
                start = end - hours * 3600
            start = max(start, end - MAX_HOURS * 3600)
            if not start < end:
                raise ValueError("ungültiger Zeitraum")
            try:
                data = store.history(start, end, points, self.cfg.general.history_seconds,
                                     keys or None)
            except sqlite3.OperationalError as exc:
                if "interrupt" in str(exc):
                    raise Busy()
                raise
            data["first"] = first
        finally:
            store.close()
            HISTORY_SLOTS.release()
        if start_arg == "first":
            with self._cache_lock:
                if len(self._cache) > 50:  # öffentlich erreichbar: Speicher begrenzen
                    self._cache.clear()
                self._cache[cache_key] = (now + 600, data)
        return data

    def events(self, query: Dict[str, Any]) -> Any:
        limit = min(500, max(1, int(_first(query, "limit", "50"))))
        if not os.path.exists(self.cfg.general.db_path):
            return []
        store = self._store()
        try:
            events = store.events(limit)
        finally:
            store.close()
        # Das Dashboard ist öffentlich: keine Telegram-Namen herausgeben
        for ev in events:
            if ev["source"].startswith("Telegram"):
                ev["source"] = "Telegram"
        return events

    def health(self) -> Tuple[int, Dict[str, Any]]:
        ts, state = self.state_file.read()
        age = None if ts is None else time.time() - ts
        ok = age is not None and age < STATE_STALE_SECONDS and bool(state.get("ok"))
        return (200 if ok else 503), {"ok": ok, "age": age}


_static_cache: Dict[Tuple[str, float, bool], bytes] = {}
_static_lock = threading.Lock()


def _read_static(path: str, gzipped: bool) -> bytes:
    """Statische Dateien, bei Bedarf einmal komprimiert und im Speicher gehalten
    (Highcharts schrumpft so von 380 auf rund 130 kB)."""
    key = (path, os.path.getmtime(path), gzipped)
    with _static_lock:
        body = _static_cache.get(key)
    if body is None:
        with open(path, "rb") as fh:
            body = fh.read()
        if gzipped:
            body = gzip.compress(body, 6)
        with _static_lock:
            _static_cache[key] = body
    return body


def _first(query: Dict[str, Any], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default


def make_handler(dashboard: Dashboard, panel=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "krempelair/" + __version__
        protocol_version = "HTTP/1.1"
        timeout = 20  # hängende Verbindungen nicht ewig halten

        def log_message(self, fmt, *args):  # Zugriffe nicht ins Journal
            log.debug("%s %s", self.address_string(), fmt % args)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            # GET/HEAD mit Body: nicht erlaubt (sonst Request-Smuggling hinter Apache)
            if self.headers.get("Transfer-Encoding") or int(self.headers.get("Content-Length") or 0):
                self.close_connection = True
                self._json(400, {"error": "Body nicht erlaubt"})
                return
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if panel is not None and panel.handle_get(self, url.path):
                    return
                if url.path == "/api/state":
                    self._json(200, dashboard.state())
                elif url.path == "/api/history":
                    self._json(200, dashboard.history(query))
                elif url.path == "/api/events":
                    self._json(200, dashboard.events(query))
                elif url.path == "/healthz":
                    self._json(*dashboard.health())
                elif url.path in ("/", "/index.html"):
                    self._static("index.html")
                elif url.path.startswith("/static/"):
                    self._static(url.path[len("/static/"):])
                else:
                    self._json(404, {"error": "nicht gefunden"})
            except Busy:
                self._json(503, {"error": "beschäftigt, bitte gleich nochmals"})
            except ValueError:
                self._json(400, {"error": "ungültige Anfrage"})
            except Exception:
                log.exception("Fehler bei %s", self.path)
                self._json(500, {"error": "interner Fehler"})

        def _accepts_gzip(self) -> bool:
            return "gzip" in self.headers.get("Accept-Encoding", "")

        def _send(self, status: int, ctype: str, body: bytes, cache: str,
                  gzipped: bool = False, cookie: Optional[str] = None) -> None:
            self.send_response(status)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Vary", "Accept-Encoding")
            if gzipped:
                self.send_header("Content-Encoding", "gzip")
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: Any, cookie: Optional[str] = None) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            # Verläufe sind bis 100 kB gross und gehen auch durch den Tunnel
            gzipped = len(body) > 1024 and self._accepts_gzip()
            if gzipped:
                body = gzip.compress(body, 5)
            self._send(status, "application/json; charset=utf-8", body, "no-store", gzipped, cookie)

        def do_POST(self):
            if panel is None:  # öffentliches Dashboard: nur lesen
                self.close_connection = True
                self._json(405, {"error": "nur lesend"})
                return
            try:
                panel.handle_post(self, urlparse(self.path).path)
            except Exception:
                log.exception("Fehler bei POST %s", self.path)
                self._json(500, {"error": "interner Fehler"})

        def _static(self, name: str) -> None:
            path = os.path.normpath(os.path.join(STATIC_DIR, name))
            if not path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(path):
                self._json(404, {"error": "nicht gefunden"})
                return
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            textual = ctype.startswith("text/") or ctype == "application/javascript"
            if textual:
                ctype += "; charset=utf-8"
            gzipped = textual and self._accepts_gzip()
            body = _read_static(path, gzipped)
            cache = "max-age=604800, immutable" if "/vendor/" in path else "no-cache"
            self._send(200, ctype, body, cache, gzipped)

    return Handler


class BoundedServer(ThreadingHTTPServer):
    """Höchstens MAX_THREADS Anfragen gleichzeitig; weitere Verbindungen werden
    sofort geschlossen statt Threads anzuhäufen."""

    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(MAX_THREADS)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def serve(cfg: Config, panel=None) -> None:
    """Öffentliches Dashboard (panel=None) oder Bedien-Panel im LAN."""
    mimetypes.add_type("application/javascript", ".js")
    threading.stack_size(512 * 1024)  # 32-Bit-Pi: 8 MB je Thread wären zu viel
    dashboard = Dashboard(cfg, StateFile(cfg.general.state_path))
    host, port = (cfg.panel.host, cfg.panel.port) if panel else (cfg.web.host, cfg.web.port)
    server = BoundedServer((host, port), make_handler(dashboard, panel))
    log.info("%s auf http://%s:%d/", "Panel" if panel else "Dashboard", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
