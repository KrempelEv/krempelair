"""Regeldienst: einziger Prozess mit Zugriff auf die Hardware.

Ein Takt (tick) alle 0.5 s nimmt Schaltbefehle entgegen, ein Regelzyklus
alle 2 s misst, rechnet und stellt. Kein Fehler in Messung, Datenbank oder
Rechnung beendet den Dienst; systemd startet ihn zusätzlich neu, falls er
doch hängt (Watchdog) oder stirbt.
"""

from __future__ import annotations

import datetime
import logging
import os
import signal
import socket
import time
from typing import Any, Dict, Optional

from . import __version__
from .config import Config
from .control import MODE_LABELS, NightCooling, Regulator
from .hardware import (
    AO_LE, AO_WRG, BETRIEB_BITS, LEVEL_LABELS, MASK_ALARM, MASK_FAULTS, MASK_FROST,
    MASK_RUNNING, SENSORS, STOERUNG_BITS, STOERUNG_LABELS, HardwareError, Plant,
    bits_to_dict, level_from_outputs, outputs_with_level, outputs_with_smoker,
    smoker_from_outputs, to_pct,
)
from .store import StateFile, Store

log = logging.getLogger(__name__)

WATCHDOG_INTERVAL = 5.0  # WatchdogSec=30 in der Unit; öfter melden bringt nichts

# Einstellungen aus der Datenbank: Typ und erlaubter Bereich. Ein kaputter
# Eintrag darf den Regelzyklus nie abbrechen, er fällt auf den Standard zurück.
SETTING_CHECKS = {
    "temp_setpoint": (float, lambda v: 5.0 <= v <= 35.0),
    "nak_target": (float, lambda v: 5.0 <= v <= 35.0),
    "nak_until": (float, lambda v: v >= 0),
}

_notify_socket = None


def sd_notify(message: str) -> None:
    """Meldung an systemd (READY, WATCHDOG, STATUS); ohne systemd wirkungslos."""
    global _notify_socket
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        if _notify_socket is None:
            _notify_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        _notify_socket.sendto(message.encode(), address)
    except OSError:
        pass


class ControllerService:
    def __init__(self, cfg: Config, plant: Plant, store: Store, state_file: StateFile,
                 clock=time.time, monotonic=time.monotonic, notify=sd_notify,
                 now_local=datetime.datetime.now):
        self.cfg = cfg
        self.plant = plant
        self.store = store
        self.state_file = state_file
        self.clock = clock
        self.monotonic = monotonic
        self.notify = notify
        self.now_local = now_local
        self.regulator = Regulator(cfg.control)
        self.nak = NightCooling(cfg.nak)
        self.stop_requested = False
        self.started = clock()
        self.settings: Dict[str, Any] = {}
        self.snapshot: Dict[str, Any] = {}
        self.fail_count = 0
        self.hw_error: Optional[str] = None
        self.db_error: Optional[str] = None
        self._bad_settings: set = set()
        self._run_since: Optional[float] = None   # Lüftung ununterbrochen ein seit
        self._status = ""
        self._prev: Dict[str, Any] = {}
        self._next_cycle = 0.0
        self._next_sample = 0.0
        self._next_prune = 0.0
        # owned: NAK hat die Lüftung eingeschaltet und hält sie auf ihrer Stufe
        # restore: NAK ist vorbei, die Lüftung muss noch auf prev_level zurück
        self._nak_fans: Dict[str, Any] = {"owned": False, "prev_level": 0, "restore": False}
        self._nak_warned = False

    # Hilfen ---------------------------------------------------------------

    def _db(self, fn, *args, **kwargs):
        """Datenbankzugriff, der die Regelung nie anhält."""
        try:
            result = fn(*args, **kwargs)
            if self.db_error:
                log.info("Datenbank wieder erreichbar")
                self.db_error = None
            return result
        except Exception as exc:
            if self.db_error != str(exc):
                log.error("Datenbankfehler: %s", exc)
                self.db_error = str(exc)
            return None

    def event(self, message: str, level: str = "info", source: str = "regelung") -> None:
        log.log(logging.WARNING if level in ("warnung", "alarm") else logging.INFO, message)
        self._db(self.store.add_event, message, source, level)

    def _defaults(self) -> Dict[str, Any]:
        return {
            "temp_setpoint": self.cfg.control.default_setpoint,
            "nak_until": 0,
            "nak_target": self.cfg.nak.default_target,
            "manual": None,
        }

    def load_settings(self) -> Dict[str, Any]:
        defaults = self._defaults()
        loaded = self._db(self.store.get_settings)
        if loaded is None:
            return self.settings or defaults  # Datenbank weg: letzter bekannter Stand
        settings = dict(defaults)
        settings.update(loaded)
        for key, (kind, valid) in SETTING_CHECKS.items():
            try:
                value = kind(settings[key])
                if not valid(value):
                    raise ValueError
            except (TypeError, ValueError):
                if key not in self._bad_settings:
                    self.event("Einstellung %s=%r ungültig, verwende %r"
                               % (key, settings[key], defaults[key]), "warnung")
                    self._bad_settings.add(key)
                value = defaults[key]
            else:
                self._bad_settings.discard(key)
            settings[key] = value
        self.settings = settings
        return settings

    # Ablauf ---------------------------------------------------------------

    def start(self) -> None:
        for key, value in self._defaults().items():
            if value is not None:
                self._db(self.store.set_default, key, value)
        self._db(self.store.expire_commands, self.clock() - self.cfg.telegram.command_timeout)
        self.load_settings()
        saved = self.settings.get("nak_fans")
        if isinstance(saved, dict):
            self._nak_fans.update(saved)
            if saved.get("owned"):
                # NAK hatte die Ventilatoren eingeschaltet, als der Dienst endete
                self.nak.state.active = True
                self.nak.state.since = self.clock()
        run_since = self.settings.get("run_since")
        if isinstance(run_since, (int, float)) and run_since > 0:
            self._run_since = float(run_since)  # Laufzeit zählt über Neustarts weiter
        self.event("Regelung gestartet (Version %s, %s)" % (__version__, self.cfg.control.algorithm))
        for problem in self.cfg.errors:
            self.event("Konfiguration: %s" % problem, "warnung")
        self.notify("READY=1")

    def run_forever(self) -> None:
        def _stop(signum, _frame):
            self.stop_requested = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        self.start()
        tick = self.cfg.control.tick_seconds
        next_tick = self.monotonic()
        last_watchdog = 0.0
        while not self.stop_requested:
            try:
                self.tick()
            except Exception:
                log.exception("Unerwarteter Fehler im Regeltakt")
            # nach dem Takt melden: hängt er, bleibt die Meldung aus
            if self.monotonic() - last_watchdog >= WATCHDOG_INTERVAL:
                self.notify("WATCHDOG=1")
                last_watchdog = self.monotonic()
            next_tick += tick
            delay = next_tick - self.monotonic()
            if delay < 0:
                next_tick = self.monotonic()
                delay = 0
            time.sleep(delay)
        self.event("Regelung beendet")
        self.notify("STOPPING=1")

    def tick(self) -> None:
        self.process_commands()
        now_mono = self.monotonic()
        if now_mono >= self._next_cycle:
            self._next_cycle = max(self._next_cycle + self.cfg.control.cycle_seconds, now_mono)
            self.cycle()
        now = self.clock()
        if self.snapshot.get("ok") and now >= self._next_sample:
            interval = self.cfg.general.history_seconds
            self._next_sample = (now // interval + 1) * interval
            self._db(self.store.add_sample, int(now), self.sample_values())
        if now >= self._next_prune:
            self._next_prune = now + 86400
            self._db(self.store.prune, self.cfg.general.retention_days)

    # Befehle --------------------------------------------------------------

    def process_commands(self) -> None:
        commands = self._db(self.store.pending_commands) or []
        for cmd in commands:
            if self.clock() - cmd["ts"] > self.cfg.telegram.command_timeout:
                self._db(self.store.finish_command, cmd["id"], "expired", "zu alt")
                continue
            if not self._db(self.store.claim_command, cmd["id"]):
                continue  # nicht reserviert: lieber nicht als doppelt ausführen
            try:
                result = self.execute(cmd["kind"], int(cmd["value"]), cmd["source"])
                self._db(self.store.finish_command, cmd["id"], "done", result)
            except (HardwareError, ValueError) as exc:
                self._db(self.store.finish_command, cmd["id"], "failed", str(exc))
                self.event("Befehl %s=%s fehlgeschlagen: %s" % (cmd["kind"], cmd["value"], exc),
                           "warnung", source=cmd["source"])

    def execute(self, kind: str, value: int, source: str) -> str:
        outputs = self.plant.read_outputs()
        if kind == "fan_level":
            new = outputs_with_level(outputs, value)
            text = "Lüftung %s" % LEVEL_LABELS[value]
            if self.nak.state.active and value == 0:
                self._db(self.store.set_setting, "nak_until", 0, source)
                self.settings["nak_until"] = 0
                self.event("Nachtauskühlung durch manuelles Ausschalten beendet", source=source)
            if self._nak_fans.get("owned") or self._nak_fans.get("restore"):
                self._set_nak_fans(False, 0, False)  # wer von Hand schaltet, übernimmt
        elif kind == "smoker":
            new = outputs_with_smoker(outputs, bool(value))
            text = "Raucherraum %s" % ("ein" if value else "aus")
        else:
            raise ValueError("unbekannter Befehl %s" % kind)
        self._write_outputs(new)
        self.event(text, source=source)
        return text

    def _write_outputs(self, value: int) -> int:
        self.plant.write_outputs(value)
        check = self.plant.read_outputs()
        if check != value:
            raise HardwareError("Ausgänge zeigen 0x%02x statt 0x%02x" % (check, value))
        return value

    def _set_nak_fans(self, owned: bool, prev_level: int, restore: bool) -> None:
        self._nak_fans = {"owned": owned, "prev_level": prev_level, "restore": restore}
        self._db(self.store.set_setting, "nak_fans", self._nak_fans, "regelung")

    # Regelzyklus ----------------------------------------------------------

    def cycle(self) -> None:
        cfg = self.cfg.control
        now = self.clock()
        settings = self.load_settings()
        nak_enabled = settings["nak_until"] > now
        nak_target = settings["nak_target"]
        try:
            reading = self.plant.read()
        except HardwareError as exc:
            # ohne Messwerte keine Nachtauskühlung; das Zurückschalten der
            # Lüftung holt der nächste erfolgreiche Zyklus nach
            if self.nak.state.active:
                self.nak.evaluate(now, self.now_local(), nak_enabled, nak_target, None, False)
                self._nak_transition(False, self.nak.state.reason, 0)
            self._hardware_failed(exc)
            return
        # Fühler am Wandleranschlag gelten als defekt: weder Regelung noch NAK
        # rechnen mit ihnen
        temps = {k: (None if k in reading.faulty else v) for k, v in reading.temps.items()}
        if set(reading.faulty) != set(self._prev.get("faulty", ())):
            if reading.faulty:
                self.event("Fühler defekt (Bruch/Kurzschluss): %s" % ", ".join(
                    SENSORS[k].label for k in reading.faulty), "alarm")
            elif self._prev.get("faulty"):
                self.event("Fühler wieder in Ordnung")
        frost = bool(reading.stoerung & MASK_FROST)
        alarm = bool(reading.stoerung & MASK_ALARM)
        running = bool(reading.betrieb & MASK_RUNNING)
        setpoint = settings["temp_setpoint"]

        was_active = self.nak.state.active
        nak = self.nak.evaluate(now, self.now_local(), nak_enabled, nak_target, temps, frost)
        if nak.active != was_active:
            self._nak_transition(nak.active, nak.reason, level_from_outputs(reading.outputs))
        outputs = self._reconcile_nak_fans(nak.active, reading.outputs)
        outputs = self._limit_runtime(now, outputs)
        fan_level = level_from_outputs(outputs)
        manual = self._manual(settings, now)
        decision = self.regulator.decide(self.monotonic(), temps, setpoint, fan_level,
                                         running, frost, nak.active, manual, reading.faulty)
        try:
            self._apply(decision)
        except HardwareError as exc:
            self._hardware_failed(exc)
            return
        # erst nach Lesen UND Schreiben gilt der Bus wieder als in Ordnung
        if self.hw_error:
            self.event("I2C wieder in Ordnung")
        self.hw_error = None
        self.fail_count = 0

        self.snapshot = self._decision_fields(decision)
        self.snapshot.update({
            "ts": now,
            "ok": True,
            "algorithm": cfg.algorithm,
            "cycle_seconds": cfg.cycle_seconds,
            "temps": temps,
            "raw": reading.raw,
            "sensor_faults": list(reading.faulty),
            "setpoint": setpoint,
            "control": decision.info,
            "fan_level": fan_level,
            "smoker": smoker_from_outputs(outputs),
            "running": running,
            "frost": frost,
            "alarm": alarm,
            "betrieb": bits_to_dict(reading.betrieb, BETRIEB_BITS),
            "stoerung": bits_to_dict(reading.stoerung, STOERUNG_BITS),
            "faults": [STOERUNG_LABELS[n] for i, n in enumerate(STOERUNG_BITS)
                       if reading.stoerung & MASK_FAULTS & (1 << i)],
            "betrieb_raw": reading.betrieb,
            "stoerung_raw": reading.stoerung,
            "outputs_raw": outputs,
            "nak": {
                "enabled": nak_enabled,
                "until": settings["nak_until"],
                "active": nak.active,
                "since": nak.since,
                "reason": nak.reason,
                "target": nak_target,
                "owns_fans": bool(self._nak_fans.get("owned")),
            },
            "run_since": self._run_since,
            "run_limit_at": (self._run_since + cfg.max_run_hours * 3600
                             if self._run_since and cfg.max_run_hours > 0 else None),
            "manual": manual,
            "hardware_error": None,
            "db_error": self.db_error,
        })
        self._publish()
        self._log_changes(reading)
        if self._status != decision.mode:
            self._status = decision.mode
            self.notify("STATUS=%s" % MODE_LABELS[decision.mode])

    def _apply(self, decision) -> None:
        self.plant.write_analog(AO_WRG, decision.wrg)
        self.plant.write_analog(AO_LE, decision.le)

    def _decision_fields(self, decision) -> Dict[str, Any]:
        return {
            "version": __version__,
            "started": self.started,
            "mode": decision.mode,
            "mode_label": MODE_LABELS[decision.mode],
            "wrg": decision.wrg,
            "le": decision.le,
            "wrg_pct": to_pct(decision.wrg),
            "le_pct": to_pct(decision.le),
        }

    def _manual(self, settings: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
        manual = settings.get("manual")
        if not isinstance(manual, dict):
            return None
        def actuator(key):
            value = manual.get(key)
            return None if value is None else max(0, min(1023, int(value)))

        try:
            until = float(manual.get("until") or 0)
            values = {"wrg": actuator("wrg"), "le": actuator("le"), "until": until,
                      "by": str(manual.get("by", ""))}
        except (TypeError, ValueError):
            return None
        if values["wrg"] is None and values["le"] is None:
            return None
        if until <= now or until > now + self.cfg.control.manual_max_minutes * 60:
            return None
        return values

    def _limit_runtime(self, now: float, outputs: int) -> int:
        """Läuft die Lüftung max_run_hours ohne Unterbruch, wird sie
        ausgeschaltet (vergessene Anlage). Gibt die Ausgänge danach zurück."""
        if level_from_outputs(outputs) == 0:
            if self._run_since is not None:
                self._run_since = None
                self._db(self.store.set_setting, "run_since", 0, "regelung")
            return outputs
        if self._run_since is None:
            self._run_since = now
            self._db(self.store.set_setting, "run_since", now, "regelung")
        limit = self.cfg.control.max_run_hours
        if limit <= 0 or now - self._run_since < limit * 3600:
            return outputs
        try:
            outputs = self._write_outputs(outputs_with_level(outputs, 0))
        except HardwareError as exc:
            if not self._nak_warned:
                self.event("Laufzeitbegrenzung konnte nicht ausschalten: %s" % exc, "warnung")
                self._nak_warned = True
            return outputs
        self.event("Lüftung nach %g h ununterbrochenem Betrieb automatisch ausgeschaltet" % limit,
                   "warnung")
        if self.nak.state.active:
            # sonst schaltet die Nachtauskühlung gleich wieder ein
            self._db(self.store.set_setting, "nak_until", 0, "regelung")
            self.settings["nak_until"] = 0
        self._set_nak_fans(False, 0, False)
        self._run_since = None
        self._db(self.store.set_setting, "run_since", 0, "regelung")
        return outputs

    def _nak_transition(self, active: bool, reason: str, level: int) -> None:
        """Merkt sich beim Start, ob NAK die Lüftung einschalten muss, und beim
        Ende, ob sie zurückgestellt werden muss. Geschaltet wird in
        _reconcile_nak_fans, jeden Zyklus erneut, bis es geklappt hat."""
        if active:
            self.event("Nachtauskühlung gestartet: %s" % reason)
            if self._nak_fans.get("restore"):
                # die Lüftung läuft noch von der letzten NAK: weiter besitzen,
                # ursprüngliche Stufe behalten
                self._set_nak_fans(True, int(self._nak_fans.get("prev_level", 0)), False)
            else:
                self._set_nak_fans(level < self.cfg.nak.fan_level, level, False)
        else:
            self.event("Nachtauskühlung beendet: %s" % reason)
            if self._nak_fans.get("owned"):
                self._set_nak_fans(False, int(self._nak_fans.get("prev_level", 0)), True)

    def _reconcile_nak_fans(self, active: bool, outputs: int) -> int:
        """Schaltet die Lüftung für NAK ein bzw. zurück; gibt die Ausgänge zurück,
        wie sie danach auf der Karte stehen."""
        nak_level = self.cfg.nak.fan_level
        level = level_from_outputs(outputs)
        fans = self._nak_fans
        try:
            if active and fans.get("owned") and level != nak_level:
                outputs = self._write_outputs(outputs_with_level(outputs, nak_level))
                self.event("Nachtauskühlung schaltet Lüftung auf %s" % LEVEL_LABELS[nak_level])
            elif not active and fans.get("restore"):
                prev = int(fans.get("prev_level", 0))
                if level == nak_level:
                    outputs = self._write_outputs(outputs_with_level(outputs, prev))
                    self.event("Nachtauskühlung stellt Lüftung zurück auf %s" % LEVEL_LABELS[prev])
                self._set_nak_fans(False, 0, False)
            self._nak_warned = False
        except HardwareError as exc:
            if not self._nak_warned:
                self.event("Nachtauskühlung konnte Ventilatoren nicht schalten: %s" % exc, "warnung")
                self._nak_warned = True
        return outputs

    def _hardware_failed(self, exc: Exception) -> None:
        self.fail_count += 1
        message = str(exc)
        if self.hw_error is None:
            self.event("I2C-Fehler: %s" % message, "warnung")
        self.hw_error = message
        safe = self.fail_count >= self.cfg.control.sensor_fail_cycles
        decision = None
        if safe:
            decision = self.regulator.safe()
            if self.fail_count == self.cfg.control.sensor_fail_cycles:
                self.event("Keine Messwerte seit %d Zyklen, Sicherheitsstellung" % self.fail_count,
                           "alarm")
            try:
                self._apply(decision)
            except HardwareError:
                pass
        snapshot = dict(self.snapshot)
        if decision is not None:
            snapshot.update(self._decision_fields(decision))
        snapshot.update({
            "ts": self.clock(),
            "ok": False,
            "version": __version__,
            "started": self.started,
            "hardware_error": message,
            "fail_count": self.fail_count,
        })
        self.snapshot = snapshot
        self._publish()

    def _publish(self) -> None:
        try:
            self.state_file.write(self.snapshot)
        except OSError as exc:
            log.error("Zustand nicht schreibbar: %s", exc)

    def _log_changes(self, reading) -> None:
        prev = self._prev
        snap = self.snapshot
        if prev.get("mode") != snap["mode"]:
            self.event("Betriebsart: %s" % snap["mode_label"])
        changed = (prev.get("stoerung", 0) ^ reading.stoerung) & MASK_FAULTS
        for i, name in enumerate(STOERUNG_BITS):
            if changed & (1 << i) and "mode" in prev:
                if reading.stoerung & (1 << i):
                    self.event("Störung: %s" % STOERUNG_LABELS[name], "alarm")
                else:
                    self.event("Störung behoben: %s" % STOERUNG_LABELS[name])
        if "running" in prev and prev["running"] != snap["running"]:
            self.event("Anlage %s" % ("läuft" if snap["running"] else "steht"))
        self._prev = {"mode": snap["mode"], "stoerung": reading.stoerung,
                      "running": snap["running"], "faulty": tuple(reading.faulty)}

    def sample_values(self) -> Dict[str, Any]:
        s = self.snapshot
        t = s.get("temps", {})
        values = {key: t.get(key) for key in SENSORS}
        values.update({
            "t_soll": s.get("setpoint"), "nak_target": s.get("nak", {}).get("target"),
            "wrg": s.get("wrg"), "le": s.get("le"),
            "signal": s.get("control", {}).get("demand"),
            "mode": s.get("mode"), "fan_level": s.get("fan_level"),
            "smoker": int(bool(s.get("smoker"))), "running": int(bool(s.get("running"))),
            "frost": int(bool(s.get("frost"))), "alarm": int(bool(s.get("alarm"))),
            "nak_active": int(bool(s.get("nak", {}).get("active"))),
            "betrieb": s.get("betrieb_raw"), "stoerung": s.get("stoerung_raw"),
        })
        return values
