"""Bedienhandlungen, die Telegram-Bot und Web-Panel gemeinsam nutzen.

Schaltbefehle legt Remote in der Datenbank ab und wartet, bis die Regelung
sie ausgeführt hat; Sollwerte, Nachtauskühlung und Handbetrieb sind
Einstellungen, die die Regelung im nächsten Zyklus übernimmt.
"""

from __future__ import annotations

import datetime
import time
from typing import Optional, Tuple

from .config import Config
from .hardware import LEVEL_LABELS, from_pct
from .store import Store

SETPOINTS = tuple(range(17, 27))
NAK_TARGETS = tuple(range(18, 24))
MANUAL_HOURS = tuple(range(1, 9))


def fmt_until(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%d.%m. %H:%M")


class Remote:
    def __init__(self, cfg: Config, store: Store, clock=time.time, sleep=time.sleep):
        self.cfg = cfg
        self.store = store
        self.clock = clock
        self.sleep = sleep

    def run_command(self, kind: str, value: int, source: str) -> Tuple[bool, str]:
        """Schaltbefehl an die Regelung; (ausgeführt?, Meldung)."""
        if kind == "fan_level" and value not in LEVEL_LABELS:
            raise ValueError("Stufe muss 0, 1 oder 2 sein")
        if kind == "smoker" and value not in (0, 1):
            raise ValueError("Raucherraum 0 oder 1")
        if kind not in ("fan_level", "smoker"):
            raise ValueError("unbekannter Befehl")
        command_id = self.store.add_command(kind, value, source)
        deadline = self.clock() + self.cfg.telegram.command_timeout
        while self.clock() < deadline:
            self.sleep(0.3)
            row = self.store.get_command(command_id)
            if row is not None and row["status"] not in ("pending", "running"):
                if row["status"] == "done":
                    return True, row["result"]
                return False, "Nicht ausgeführt: %s" % row["result"]
        return False, "Die Regelung hat nicht geantwortet. Läuft krempelair-control?"

    def set_setpoint(self, value: int, source: str) -> str:
        if value not in SETPOINTS:
            raise ValueError("Solltemperatur %d–%d °C" % (SETPOINTS[0], SETPOINTS[-1]))
        self.store.set_setting("temp_setpoint", float(value), source)
        self.store.add_event("Solltemperatur Zuluft %d °C" % value, source)
        return "Solltemperatur Zuluft %d °C" % value

    def set_nak(self, on: bool, source: str) -> str:
        if on:
            until = self.clock() + self.cfg.nak.duration_hours * 3600
            self.store.set_setting("nak_until", until, source)
            text = "Nachtauskühlung aktiviert bis %s" % fmt_until(until)
        else:
            self.store.set_setting("nak_until", 0, source)
            text = "Nachtauskühlung beendet"
        self.store.add_event(text, source)
        return text

    def set_nak_target(self, value: int, source: str) -> str:
        if value not in NAK_TARGETS:
            raise ValueError("Ziel %d–%d °C" % (NAK_TARGETS[0], NAK_TARGETS[-1]))
        self.store.set_setting("nak_target", float(value), source)
        self.store.add_event("Ziel Nachtauskühlung %d °C" % value, source)
        return "Ziel Nachtauskühlung %d °C" % value

    def set_manual(self, wrg_pct: Optional[float], le_pct: Optional[float], hours: int,
                   source: str) -> str:
        """Stellglieder für 1–8 h von Hand; None = dieses bleibt automatisch."""
        if hours not in MANUAL_HOURS:
            raise ValueError("Dauer 1–8 h")
        if wrg_pct is None and le_pct is None:
            raise ValueError("mindestens ein Stellglied wählen")
        parts = []
        values = {"until": self.clock() + hours * 3600, "by": source}
        for key, pct, label in (("wrg", wrg_pct, "WRG-Klappe"), ("le", le_pct, "Heizventil")):
            if pct is None:
                values[key] = None
                continue
            if not 0 <= pct <= 100:
                raise ValueError("%s 0–100 %%" % label)
            values[key] = from_pct(pct)
            parts.append("%s %.0f %%" % (label, pct))
        self.store.set_setting("manual", values, source)
        text = "Handbetrieb bis %s: %s" % (fmt_until(values["until"]), ", ".join(parts))
        self.store.add_event(text, source)
        return text

    def clear_manual(self, source: str) -> str:
        self.store.set_setting("manual", None, source)
        self.store.add_event("Handbetrieb beendet, zurück auf Automatik", source)
        return "Zurück auf Automatik"
