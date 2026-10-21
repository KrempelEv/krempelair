"""Telegram-Bot zur Fernbedienung.

Der Bot schreibt nie selbst auf die Hardware: Schaltbefehle legt er in der
Datenbank ab und wartet, bis die Regelung sie ausgeführt hat; Sollwerte
und die Nachtauskühlung sind Einstellungen, die die Regelung im nächsten
Zyklus übernimmt.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import html
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .hardware import LEVEL_LABELS, SENSORS
from .remote import NAK_TARGETS, SETPOINTS, Remote
from .store import STATE_STALE_SECONDS, StateFile, Store
from .telegram import TelegramAPI, TelegramError

log = logging.getLogger(__name__)

MAX_MESSAGE_AGE = 120
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

COMMANDS = [
    ("status", "Zustand der Lüftung"),
    ("lueftung", "Lüftung und Raucherraum schalten"),
    ("temperatur", "Solltemperatur Zuluft"),
    ("nak", "Nachtauskühlung für 48 h aktivieren"),
    ("login", "Anmelden: /login Passwort"),
    ("logout", "Diesen Chat abmelden"),
    ("hilfe", "Hilfe"),
]

HELP = (
    "<b>Krempelair</b>\n\n"
    "/status – Temperaturen, Stellwerte, Meldungen\n"
    "/lueftung – Lüftung Stufe 1/2/aus, Raucherraum\n"
    "/temperatur – Solltemperatur Zuluft\n"
    "/nak – Nachtauskühlung für 48 h aktivieren\n"
    "/login <i>Passwort</i> – diesen Chat freischalten\n"
    "/logout – diesen Chat abmelden"
)


# Passwort ----------------------------------------------------------------


def hash_password(password: str, iterations: int = 200000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256:%d:%s:%s" % (
        iterations, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt, digest = encoded.split(":")
        if scheme != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt),
                                    int(iterations))
        return hmac.compare_digest(check, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Darstellung -------------------------------------------------------------


def esc(text: Any) -> str:
    return html.escape(str(text), quote=False)


def fmt_temp(value: Optional[float]) -> str:
    return "–" if value is None else ("%.1f °C" % value)


def fmt_time(ts: float, now: float) -> str:
    dt = datetime.datetime.fromtimestamp(ts)
    if abs(ts - now) < 20 * 3600:
        return dt.strftime("%H:%M")
    return "%s %s" % (WEEKDAYS[dt.weekday()], dt.strftime("%d.%m. %H:%M"))


def fmt_age(seconds: float) -> str:
    if seconds < 90:
        return "%d s" % seconds
    if seconds < 5400:
        return "%d min" % (seconds // 60)
    return "%.1f h" % (seconds / 3600)


def render_status(state_ts: Optional[float], state: Dict[str, Any], now: float) -> str:
    if not state_ts:
        return "⚠️ Die Regelung liefert keine Daten. Läuft der Dienst krempelair-control?"
    age = now - state_ts
    lines = ["<b>Krempelair</b> · %s" % fmt_time(now, now)]
    if age > STATE_STALE_SECONDS:
        lines.append("⚠️ <b>Keine aktuellen Daten</b> – letzter Stand vor %s" % fmt_age(age))
    if not state.get("ok", True):
        lines.append("⚠️ <b>I2C-Fehler:</b> %s" % esc(state.get("hardware_error")))
    if not state.get("temps"):
        return "\n".join(lines)

    level = state.get("fan_level", 0)
    running = state.get("running")
    lines.append("")
    lines.append("Lüftung: <b>%s</b>%s" % (
        LEVEL_LABELS.get(level, level),
        "" if bool(running) == (level > 0) else
        (" – Anlage meldet Betrieb" if running else " – keine Betriebsmeldung")))
    if state.get("run_since"):
        hours = (now - state["run_since"]) / 3600
        limit = state.get("run_limit_at")
        lines.append("Läuft seit %.1f h%s" % (
            hours, ", schaltet spätestens %s aus" % fmt_time(limit, now) if limit else ""))
    if state.get("manual"):
        lines.append("✋ <b>Handbetrieb</b> bis %s" % fmt_time(state["manual"]["until"], now))
    lines.append("Raucherraum: %s" % ("ein" if state.get("smoker") else "aus"))
    lines.append("Betriebsart: <b>%s</b>" % esc(state.get("mode_label", "–")))
    lines.append("Nachtauskühlung: %s" % render_nak_short(state.get("nak") or {}, now))

    temps = state["temps"]
    lines.append("")
    lines.append("<b>Temperaturen</b>")
    lines.append("Zuluft %s (Soll %s)" % (fmt_temp(temps.get("t_zul")),
                                          fmt_temp(state.get("setpoint"))))
    for key in ("t_abl", "t_aul", "t_fol", "t_raum", "t_witt"):
        lines.append("%s %s" % (SENSORS[key].label, fmt_temp(temps.get(key))))

    lines.append("")
    lines.append("<b>Stellwerte</b>")
    lines.append("WRG-Klappe %s %%, Heizventil %s %%" % (
        _num(state.get("wrg_pct")), _num(state.get("le_pct"))))

    faults = state.get("faults") or []
    lines.append("")
    if faults:
        lines.append("⚠️ <b>Störungen:</b> %s" % esc(", ".join(faults)))
    else:
        lines.append("Störungen: keine")
    return "\n".join(lines)


def _num(value: Any) -> str:
    return "–" if value is None else ("%.0f" % value)


def render_nak_short(nak: Dict[str, Any], now: float) -> str:
    if not nak.get("enabled"):
        return "aus"
    until = fmt_time(nak.get("until", 0), now)
    if nak.get("active"):
        return "<b>läuft</b> (aktiviert bis %s)" % until
    return "bereit bis %s – %s" % (until, esc(nak.get("reason", "")))


def render_nak(state: Dict[str, Any], cfg: Config, now: float) -> str:
    nak = state.get("nak") or {}
    c = cfg.nak
    lines = ["<b>Nachtauskühlung</b>"]
    if nak.get("enabled"):
        lines.append("Aktiviert bis %s" % fmt_time(nak.get("until", 0), now))
        if nak.get("active"):
            lines.append("Läuft seit %s" % fmt_time(nak.get("since") or now, now))
        else:
            lines.append("Wartet: %s" % esc(nak.get("reason", "")))
    else:
        lines.append("Aus")
    lines.append("Ziel innen: %s" % fmt_temp(nak.get("target")))
    window = ""
    if c.window_start and c.window_end and c.window_start != c.window_end:
        window = ", nur %s–%s" % (c.window_start, c.window_end)
    lines.append("")
    lines.append(
        "Läuft, wenn %s über dem Ziel liegt und %s mindestens %.0f K kühler und "
        "mindestens %.0f °C warm ist%s. Die Lüftung wird dafür auf %s geschaltet, "
        "WRG auf Bypass, Heizventil zu." % (
            SENSORS[c.inside_sensor].label, SENSORS[c.outside_sensor].label, c.start_delta,
            c.min_outside, window, LEVEL_LABELS[c.fan_level]))
    return "\n".join(lines)


# Bot ---------------------------------------------------------------------


class Bot:
    def __init__(self, cfg: Config, api: TelegramAPI, store: Store, state_file: StateFile,
                 clock=time.time, sleep=time.sleep):
        self.cfg = cfg
        self.api = api
        self.store = store
        self.state_file = state_file
        self.clock = clock
        self.sleep = sleep
        self.failures: Dict[int, List[float]] = {}
        self.remote = Remote(cfg, store, clock=clock, sleep=sleep)

    # Ablauf ---------------------------------------------------------------

    def run_forever(self) -> None:
        try:
            self.api.set_commands([{"command": c, "description": d} for c, d in COMMANDS])
        except TelegramError as exc:
            log.warning("Befehlsliste nicht gesetzt: %s", exc)
        offset = self._skip_backlog()
        backoff = 1.0
        failing_since = None
        while True:
            try:
                updates = self.api.get_updates(offset, self.cfg.telegram.poll_timeout)
                backoff = 1.0
                if failing_since is not None and self.clock() - failing_since > MAX_MESSAGE_AGE:
                    # nach längerem Ausfall: liegengebliebene Tastendrücke verwerfen
                    # (Tasten tragen kein eigenes Datum, Text-Nachrichten schon)
                    failing_since = None
                    offset = self._skip_backlog()
                    continue
                failing_since = None
            except TelegramError as exc:
                if failing_since is None:
                    failing_since = self.clock()
                if exc.code == 401:
                    log.error("Telegram lehnt das Token ab; neuer Versuch in 5 min")
                    self.sleep(300)
                elif exc.code == 409:
                    log.error("Ein anderer Prozess fragt denselben Bot ab; neuer Versuch in 30 s")
                    self.sleep(30)
                elif exc.retry_after:
                    self.sleep(exc.retry_after)
                else:
                    log.warning("Telegram nicht erreichbar (%s); neuer Versuch in %.0f s",
                                exc, backoff)
                    self.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self.handle(update)
                except Exception:
                    log.exception("Fehler bei Update %s", update.get("update_id"))

    def _skip_backlog(self) -> Optional[int]:
        """Befehle, die liegen geblieben sind, solange der Bot nicht lief,
        werden verworfen: ein «Lüftung aus» von vor drei Stunden soll nicht
        plötzlich ausgeführt werden."""
        while True:
            try:
                last = self.api.call("getUpdates", offset=-1, timeout=0)
            except TelegramError as exc:
                log.warning("Telegram nicht erreichbar (%s)", exc)
                self.sleep(10)
                continue
            if last:
                log.info("Liegengebliebene Updates bis %s verworfen", last[-1]["update_id"])
                return last[-1]["update_id"] + 1
            return None

    # Verteilung -----------------------------------------------------------

    def handle(self, update: Dict[str, Any]) -> None:
        if "callback_query" in update:
            self.on_callback(update["callback_query"])
            return
        message = update.get("message")
        if not message or "text" not in message:
            return
        if self.clock() - message.get("date", 0) > MAX_MESSAGE_AGE:
            return
        text = message["text"].strip()
        if message["chat"].get("type") != "private":
            if text.startswith("/login"):
                self.on_login(message, "")
            else:
                self.reply(message, "Der Bot funktioniert nur im privaten Chat.")
            return
        if not text.startswith("/"):
            self.reply(message, "Befehle siehe /hilfe")
            return
        head, _, args = text.partition(" ")
        command = head[1:].split("@")[0].lower()
        chat_id = message["chat"]["id"]

        if command in ("start", "hilfe", "help"):
            self.reply(message, HELP)
            return
        if command == "login":
            self.on_login(message, args.strip())
            return
        if not self.store.is_chat_allowed(chat_id):
            self.reply(message, "Bitte zuerst anmelden: /login <i>Passwort</i>")
            return
        if command == "logout":
            self.store.remove_chat(chat_id)
            self.store.add_event("Chat abgemeldet", self.source(message))
            self.reply(message, "Abgemeldet.")
        elif command in ("status", "betrieb"):
            self.reply(message, self.status_text())
        elif command == "lueftung":
            self.reply(message, self.fan_text(), self.fan_keyboard())
        elif command == "temperatur":
            self.reply(message, self.setpoint_text(), self.setpoint_keyboard())
        elif command in ("nak", "nachtauskuehlung"):
            self.reply(message, self.nak_text(), self.nak_keyboard())
        else:
            self.reply(message, "Unbekannter Befehl. Siehe /hilfe")

    def reply(self, message: Dict[str, Any], text: str, keyboard=None) -> None:
        self.api.send_message(message["chat"]["id"], text, keyboard)

    @staticmethod
    def source(message: Dict[str, Any]) -> str:
        user = message.get("from") or {}
        name = user.get("username") or user.get("first_name") or str(message["chat"]["id"])
        return "Telegram %s" % name

    # Anmeldung ------------------------------------------------------------

    def on_login(self, message: Dict[str, Any], password: str) -> None:
        chat = message["chat"]
        chat_id = chat["id"]
        # Passwort nicht im Verlauf stehen lassen (in Gruppen klappt das nur,
        # wenn der Bot Admin ist; darum dort gar nicht prüfen)
        try:
            self.api.delete_message(chat_id, message["message_id"])
        except TelegramError:
            pass
        if chat.get("type") != "private":
            self.api.send_message(chat_id, "Anmelden bitte im privaten Chat mit dem Bot. "
                                           "Das Passwort hier sofort ändern lassen, falls es "
                                           "sichtbar war.")
            return
        now = self.clock()
        window = self.cfg.telegram.lockout_minutes * 60
        for key in list(self.failures):
            self.failures[key] = [t for t in self.failures[key] if now - t < window]
        recent = self.failures.setdefault(chat_id, [])
        total = sum(len(v) for v in self.failures.values())
        if (len(recent) >= self.cfg.telegram.max_login_failures
                or total >= 20 * self.cfg.telegram.max_login_failures):
            self.api.send_message(chat_id, "Zu viele Fehlversuche. Bitte später erneut versuchen.")
            return
        if not self.cfg.telegram.password_hash or not verify_password(
                password, self.cfg.telegram.password_hash):
            recent.append(now)
            self.store.add_event("Fehlgeschlagene Anmeldung", self.source(message), "warnung")
            self.api.send_message(chat_id, "Falsches Passwort.")
            return
        name = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or str(chat_id)
        self.store.allow_chat(chat_id, name)
        self.store.add_event("Chat angemeldet", self.source(message))
        self.api.send_message(chat_id, "Angemeldet. " + HELP)

    # Inhalte --------------------------------------------------------------

    def state(self) -> Tuple[Optional[float], Dict[str, Any]]:
        return self.state_file.read()

    def status_text(self) -> str:
        ts, state = self.state()
        return render_status(ts, state, self.clock())

    def fan_text(self) -> str:
        _, state = self.state()
        if not state.get("temps"):
            return "Lüftung schalten:"
        return "Lüftung schalten (jetzt: %s, Raucherraum %s):" % (
            LEVEL_LABELS.get(state.get("fan_level"), "–"), "ein" if state.get("smoker") else "aus")

    @staticmethod
    def fan_keyboard():
        return [
            [(LEVEL_LABELS[1], "fan:1"), (LEVEL_LABELS[2], "fan:2"), ("Aus", "fan:0")],
            [("Raucherraum ein", "smk:1"), ("Raucherraum aus", "smk:0")],
        ]

    def setpoint_text(self) -> str:
        current = self.store.get_setting("temp_setpoint", self.cfg.control.default_setpoint)
        return "Solltemperatur Zuluft (jetzt %s):" % fmt_temp(current)

    @staticmethod
    def setpoint_keyboard():
        return [[("%d°" % t, "sp:%d" % t) for t in SETPOINTS[:5]],
                [("%d°" % t, "sp:%d" % t) for t in SETPOINTS[5:]]]

    def nak_text(self) -> str:
        ts, state = self.state()
        if not state.get("nak"):
            until = float(self.store.get_setting("nak_until", 0) or 0)
            state = {"nak": {"enabled": until > self.clock(), "until": until,
                             "target": self.store.get_setting("nak_target")}}
        return render_nak(state, self.cfg, self.clock())

    def nak_keyboard(self):
        hours = self.cfg.nak.duration_hours
        return [
            [("%g h aktivieren" % hours, "nak:1"), ("Beenden", "nak:0")],
            [("Ziel %d°" % t, "nakt:%d" % t) for t in NAK_TARGETS[:3]],
            [("Ziel %d°" % t, "nakt:%d" % t) for t in NAK_TARGETS[3:]],
        ]

    # Tasten ---------------------------------------------------------------

    def on_callback(self, query: Dict[str, Any]) -> None:
        message = query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        data = query.get("data") or ""
        if (chat_id is None or message["chat"].get("type", "private") != "private"
                or not self.store.is_chat_allowed(chat_id)):
            self.api.answer_callback(query["id"], "Bitte zuerst im privaten Chat anmelden")
            return
        source = self.source({"from": query.get("from"), "chat": message["chat"]})
        kind, value = _parse_callback(data)
        try:
            self.api.answer_callback(query["id"])
        except TelegramError:
            pass
        if kind in ("fan", "smk"):
            text = self.run_command("fan_level" if kind == "fan" else "smoker", value, source)
            self.api.edit_message(chat_id, message["message_id"], text, self.fan_keyboard())
        elif kind == "sp" and value in SETPOINTS:
            self.remote.set_setpoint(value, source)
            self.api.edit_message(chat_id, message["message_id"],
                                  "Solltemperatur Zuluft: <b>%d °C</b>" % value)
        elif kind == "nak":
            self.remote.set_nak(bool(value), source)
            self._edit_nak(chat_id, message["message_id"])
        elif kind == "nakt" and value in NAK_TARGETS:
            self.remote.set_nak_target(value, source)
            self._edit_nak(chat_id, message["message_id"])

    def _edit_nak(self, chat_id: int, message_id: int) -> None:
        # Die Regelung übernimmt Einstellungen im nächsten Zyklus
        self.sleep(self.cfg.control.cycle_seconds + 0.5)
        self.api.edit_message(chat_id, message_id, self.nak_text(), self.nak_keyboard())

    def run_command(self, kind: str, value: int, source: str) -> str:
        ok, text = self.remote.run_command(kind, value, source)
        return ("✅ " if ok else "❌ ") + esc(text)


def _parse_callback(data: str) -> Tuple[str, int]:
    """Neue Tasten haben die Form art:wert. Ältere Nachrichten aus dem
    Altsystem tragen nur eine Zahl; die werden weiter verstanden."""
    if ":" in data:
        kind, _, value = data.partition(":")
        try:
            return kind, int(value)
        except ValueError:
            return "", 0
    try:
        number = int(data)
    except ValueError:
        return "", 0
    if number in (0, 1, 2):
        return "fan", number
    if number == 10:
        return "smk", 0
    if number == 11:
        return "smk", 1
    return "sp", number
