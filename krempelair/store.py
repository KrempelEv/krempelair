"""SQLite-Ablage, gemeinsam für Regelung, Bot und Dashboard.

Die Regelung ist der einzige Prozess, der die Hardware anfasst. Bot und
Dashboard reden über diese Datenbank mit ihr:

    settings   Sollwerte und Schalter (Bot schreibt, Regelung liest)
    commands   Schaltbefehle an die Ausgänge (Bot legt an, Regelung führt aus)
    samples    Verlauf für die Charts (Regelung schreibt)
    events     Ereignisprotokoll
    bot_chats  freigegebene Telegram-Chats

Der Live-Zustand liegt nicht hier, sondern als JSON-Datei im RAM
(siehe StateFile), damit die SD-Karte nicht alle paar Sekunden
beschrieben wird.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from .hardware import ANALOG_MAX, BETRIEB_BITS, SENSORS, STOERUNG_BITS

SCHEMA_VERSION = 1

# Ab diesem Alter gilt der Live-Zustand als veraltet (Regelzyklus 2 s)
STATE_STALE_SECONDS = 15

SAMPLE_COLUMNS = tuple(SENSORS) + (
    "t_soll", "nak_target", "wrg", "le", "signal",
    "mode", "fan_level", "smoker", "running", "frost", "alarm", "nak_active",
    "betrieb", "stoerung",
)


def _bit(names, name: str) -> int:
    return 1 << names.index(name)


def _level_sql(st1: str, st2: str) -> str:
    return "MAX(CASE WHEN betrieb & %d THEN 2 WHEN betrieb & %d THEN 1 ELSE 0 END)" % (
        _bit(BETRIEB_BITS, st2), _bit(BETRIEB_BITS, st1))


# Kurven für Charts: Name → SQL-Ausdruck über ein Zeitfenster (Messwerte
# gemittelt, Zustände als Maximum). Bits und Skala kommen aus hardware.py.
HISTORY_FIELDS = dict(
    [(k, "ROUND(AVG(%s), 2)" % k) for k in tuple(SENSORS) + ("t_soll", "nak_target")]
    + [
        ("wrg", "ROUND(AVG(wrg) / %r, 1)" % (ANALOG_MAX / 100.0)),
        ("le", "ROUND(AVG(le) / %r, 1)" % (ANALOG_MAX / 100.0)),
        ("demand", "ROUND(AVG(signal), 2)"),
        ("fan_level", "MAX(fan_level)"),
        ("fan_zul", _level_sql("ZUL_St1", "ZUL_St2")),
        ("fan_fol", _level_sql("FOL_St1", "FOL_St2")),
        ("pump", "MAX((betrieb & %d) > 0)" % _bit(BETRIEB_BITS, "LE_PU")),
        ("smoker", "MAX(smoker)"),
        ("running", "MAX(running)"),
        ("nak_active", "MAX(nak_active)"),
        ("frost", "MAX(frost)"),
        ("alarm", "MAX(alarm)"),
        ("stroem_zul", "MAX((stoerung & %d) > 0)" % _bit(STOERUNG_BITS, "Stroem_ZUL")),
        ("stroem_fol", "MAX((stoerung & %d) > 0)" % _bit(STOERUNG_BITS, "Stroem_FOL")),
    ]
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    value INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result TEXT NOT NULL DEFAULT '',
    done_at REAL
);
CREATE INDEX IF NOT EXISTS commands_pending ON commands(status, id);
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER PRIMARY KEY,
    t_zul REAL, t_abl REAL, t_fol REAL, t_aul REAL, t_raum REAL, t_witt REAL,
    t_soll REAL, nak_target REAL,
    wrg INTEGER, le INTEGER, signal REAL,
    mode TEXT, fan_level INTEGER, smoker INTEGER, running INTEGER,
    frost INTEGER, alarm INTEGER, nak_active INTEGER,
    betrieb INTEGER, stoerung INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS bot_chats (
    chat_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str, readonly: bool = False, clock=time.time):
        self.clock = clock
        if readonly:
            uri = "file:%s?mode=ro" % path
            self.conn = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None,
                                        check_same_thread=False)
        else:
            directory = os.path.dirname(os.path.abspath(path))
            os.makedirs(directory, exist_ok=True)
            self.conn = sqlite3.connect(path, timeout=10, isolation_level=None,
                                        check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self._migrate()
        self.conn.execute("PRAGMA busy_timeout=10000")
        if readonly:
            self.conn.execute("PRAGMA temp_store=MEMORY")  # GROUP BY nicht auf die SD-Karte
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        try:
            self.conn.executescript(
                "BEGIN IMMEDIATE;" + _SCHEMA + "PRAGMA user_version=%d; COMMIT;" % SCHEMA_VERSION
            )
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    # Einstellungen -------------------------------------------------------

    def get_settings(self) -> Dict[str, Any]:
        rows = self.conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any, by: str = "") -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (key, json.dumps(value), self.clock(), by),
        )

    def set_default(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at, updated_by) VALUES (?,?,?,?)",
            (key, json.dumps(value), self.clock(), "default"),
        )

    # Befehle ------------------------------------------------------------

    def add_command(self, kind: str, value: int, source: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO commands(ts, source, kind, value) VALUES (?,?,?,?)",
            (self.clock(), source, kind, int(value)),
        )
        return cur.lastrowid

    def pending_commands(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM commands WHERE status='pending' ORDER BY id"
        ).fetchall()

    def claim_command(self, command_id: int) -> bool:
        """Reserviert einen Befehl vor dem Ausführen. Scheitert danach das
        Zurückschreiben des Ergebnisses, bleibt er «running» und wird nie ein
        zweites Mal ausgeführt."""
        cur = self.conn.execute(
            "UPDATE commands SET status='running' WHERE id=? AND status='pending'", (command_id,))
        return cur.rowcount == 1

    def finish_command(self, command_id: int, status: str, result: str) -> None:
        self.conn.execute(
            "UPDATE commands SET status=?, result=?, done_at=? "
            "WHERE id=? AND status IN ('pending', 'running')",
            (status, result, self.clock(), command_id),
        )

    def get_command(self, command_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()

    def expire_commands(self, older_than: float) -> int:
        cur = self.conn.execute(
            "UPDATE commands SET status='expired', result='zu alt', done_at=? "
            "WHERE status='pending' AND ts < ?",
            (self.clock(), older_than),
        )
        return cur.rowcount

    # Verlauf ------------------------------------------------------------

    def add_sample(self, ts: int, values: Dict[str, Any]) -> None:
        cols = ["ts"] + list(SAMPLE_COLUMNS)
        self.conn.execute(
            "INSERT OR REPLACE INTO samples(%s) VALUES (%s)"
            % (",".join(cols), ",".join("?" * len(cols))),
            [int(ts)] + [values.get(c) for c in SAMPLE_COLUMNS],
        )

    def history(self, start: float, end: float, max_points: int = 1500,
                min_bucket: int = 60, keys=None) -> Dict[str, List[Any]]:
        """Verlauf in gleich grossen Zeitfenstern: Messwerte gemittelt,
        Zustände als Maximum (ein Fenster mit «ein» zählt als ein)."""
        keys = [k for k in (keys or HISTORY_FIELDS) if k in HISTORY_FIELDS]
        span = max(1.0, end - start)
        bucket = max(min_bucket, int(span // max_points) + 1)
        select = ", ".join(
            ["(ts / %d) * %d AS t" % (bucket, bucket)]
            + ["%s AS %s" % (HISTORY_FIELDS[k], k) for k in keys]
        )
        rows = self.conn.execute(
            "SELECT %s FROM samples WHERE ts >= ? AND ts <= ? GROUP BY 1 ORDER BY 1" % select,
            (int(start), int(end)),
        ).fetchall()
        out = {"bucket": bucket, "start": start, "end": end, "t": [r["t"] for r in rows]}
        for k in keys:
            out[k] = [r[k] for r in rows]
        return out

    def first_sample(self) -> Optional[int]:
        row = self.conn.execute("SELECT MIN(ts) FROM samples").fetchone()
        return row[0] if row else None

    # Ereignisse ---------------------------------------------------------

    def add_event(self, message: str, source: str = "regelung", level: str = "info") -> None:
        self.conn.execute(
            "INSERT INTO events(ts, level, source, message) VALUES (?,?,?,?)",
            (self.clock(), level, source, message),
        )

    def events(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT ts, level, source, message FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # Telegram-Chats -----------------------------------------------------

    def is_chat_allowed(self, chat_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM bot_chats WHERE chat_id=?", (chat_id,)
        ).fetchone() is not None

    def allow_chat(self, chat_id: int, name: str = "") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO bot_chats(chat_id, name, added_at) VALUES (?,?,?)",
            (chat_id, name, self.clock()),
        )

    def remove_chat(self, chat_id: int) -> None:
        self.conn.execute("DELETE FROM bot_chats WHERE chat_id=?", (chat_id,))

    # Aufräumen ----------------------------------------------------------

    def prune(self, retention_days: float) -> None:
        cutoff = self.clock() - retention_days * 86400
        self.conn.execute("DELETE FROM samples WHERE ts < ?", (int(cutoff),))
        self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        self.conn.execute("DELETE FROM commands WHERE ts < ?", (self.clock() - 30 * 86400,))


class StateFile:
    """Aktueller Zustand als JSON-Datei, atomar ersetzt."""

    def __init__(self, path: str):
        self.path = path

    def write(self, data: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def read(self) -> Tuple[Optional[float], Dict[str, Any]]:
        """(Zeitpunkt, Daten); (None, {}) wenn es keinen Zustand gibt."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None, {}
        return data.get("ts"), data
