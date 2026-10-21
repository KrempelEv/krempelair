"""Übernahme aus dem Altsystem (/opt/krempel)."""

from __future__ import annotations

import os
import pickle
import re
import sqlite3
from typing import List, Optional, Tuple

from .bot import hash_password
from .store import Store


class _ListOfIntsUnpickler(pickle.Unpickler):
    """authIds enthält nur eine Liste von Zahlen; alles andere wird abgelehnt,
    damit beim Einlesen kein fremder Code laufen kann."""

    def find_class(self, module, name):
        raise pickle.UnpicklingError("unerwarteter Typ %s.%s" % (module, name))


def read_auth_ids(path: str) -> List[int]:
    with open(path, "rb") as fh:
        data = _ListOfIntsUnpickler(fh).load()
    if not isinstance(data, list) or not all(isinstance(x, int) for x in data):
        raise ValueError("authIds hat ein unerwartetes Format")
    return data


def read_setpoint(path: str) -> Optional[float]:
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        row = conn.execute("SELECT value FROM sollwerte WHERE key='tempSoll'").fetchone()
    finally:
        conn.close()
    return float(row[0]) if row else None


def read_bot_secrets(path: str) -> Tuple[Optional[str], Optional[str]]:
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    token = re.search(r'Updater\(\s*"([^"]+)"', source)
    password = re.search(r'pw\s*==\s*"([^"]+)"', source)
    return (token.group(1) if token else None, password.group(1) if password else None)


def write_secrets(path: str, token: str, password: str) -> None:
    lines = [
        "# Geheimnisse für krempelair-bot. Nur root und Gruppe krempelair lesen.",
        "KREMPELAIR_TELEGRAM_TOKEN=%s" % token,
        "KREMPELAIR_BOT_PASSWORD_HASH=%s" % hash_password(password),
        "",
    ]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def migrate(store: Store, legacy_db: Optional[str], legacy_auth: Optional[str],
            telegrambot: Optional[str], secrets_out: Optional[str]) -> List[str]:
    report = []
    if legacy_db and os.path.exists(legacy_db):
        setpoint = read_setpoint(legacy_db)
        if setpoint is not None:
            store.set_setting("temp_setpoint", setpoint, "migration")
            report.append("Solltemperatur %.1f °C übernommen" % setpoint)
    if legacy_auth and os.path.exists(legacy_auth):
        ids = set(read_auth_ids(legacy_auth))
        private = [c for c in ids if c > 0]   # negative IDs sind Gruppen: nicht übernehmen
        for chat_id in private:
            store.allow_chat(chat_id, "aus Altsystem")
        report.append("%d freigegebene Chats übernommen" % len(private))
        if len(private) != len(ids):
            report.append("%d Gruppenchats verworfen" % (len(ids) - len(private)))
    if telegrambot and secrets_out and os.path.exists(telegrambot):
        if os.path.exists(secrets_out):
            report.append("%s besteht schon, nicht überschrieben" % secrets_out)
        else:
            token, password = read_bot_secrets(telegrambot)
            if not token or not password:
                raise ValueError("Token oder Passwort im Altsystem nicht gefunden")
            write_secrets(secrets_out, token, password)
            report.append("Bot-Token und Passwort-Hash nach %s geschrieben" % secrets_out)
    if report:
        store.add_event("Übernahme aus Altsystem: " + "; ".join(report), "migration")
    return report
