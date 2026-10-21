"""Regellogik ohne Hardware- und Datenbankzugriff.

Alles hier ist rein rechnerisch und darum direkt testbar.

Stellsignale 0…1023:
    WRG  Bypassklappe am Kreuz-Wärmetauscher, 0 = ganz Bypass, 1023 = ganz
         über den Tauscher (volle Rückgewinnung)
    LE   Ventil Heizregister, 0 = zu, 1023 = offen

Die Zulufttemperatur regelt ein PID-Regler mit Vorsteuerung. Gerechnet wird
in Kelvin Temperaturhub gegenüber der Aussenluft: der Regler bestimmt, um
wie viel die Luft angehoben (oder abgesenkt) werden soll, die Aufteilung
rechnet das über ein einfaches Anlagenmodell in Klappen- und
Ventilstellung um. Weil der Hub der WRG mit der Temperaturdifferenz
Abluft − Aussenluft wächst, bleibt die Kreisverstärkung damit über das
ganze Jahr etwa gleich. Details und Herleitung: docs/regelung.md.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .config import ControlConfig, NakConfig
from .hardware import ANALOG_MAX

MODE_HEIZEN = "heizen"
MODE_WRG = "wrg"
MODE_KRG = "krg"
MODE_FREIE_KUEHLUNG = "freie_kuehlung"
MODE_NAK = "nak"
MODE_AUS = "aus"
MODE_FROST = "frost"
MODE_SENSORFEHLER = "sensorfehler"
MODE_HAND = "hand"

MODE_LABELS = {
    MODE_HEIZEN: "Heizen",
    MODE_WRG: "Wärmerückgewinnung",
    MODE_KRG: "Kälterückgewinnung",
    MODE_FREIE_KUEHLUNG: "Freie Kühlung",
    MODE_NAK: "Nachtauskühlung",
    MODE_AUS: "Anlage aus",
    MODE_FROST: "Frostschutz",
    MODE_SENSORFEHLER: "Sicherheitsstellung",
    MODE_HAND: "Handbetrieb",
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# PID ---------------------------------------------------------------------


class PID:
    """PID in Parallelform nach Åström/Hägglund.

    - P mit Sollwertgewichtung b
    - D auf den Messwert (kein Sprung bei Sollwertänderung), Filter Td/N
    - I mit bedingter Integration als Anti-Windup: liegt das Stellglied am
      Anschlag, wird nicht weiter in diese Richtung integriert
    - Ausgang und Integral begrenzt
    """

    def __init__(self, kp: float, ti: float, td: float = 0.0, n: float = 10.0,
                 b: float = 1.0, out_min: float = -math.inf, out_max: float = math.inf):
        self.kp = kp
        self.ti = ti
        self.td = td
        self.n = n
        self.b = b
        self.out_min = out_min
        self.out_max = out_max
        self.reset()

    def reset(self) -> None:
        self.i = 0.0
        self.d = 0.0
        self.prev_y: Optional[float] = None
        self.p = 0.0

    def update(self, setpoint: float, y: float, dt: float,
               hold_up: bool = False, hold_down: bool = False) -> float:
        error = setpoint - y
        self.p = self.kp * (self.b * setpoint - y)
        if self.td > 0 and self.prev_y is not None and dt > 0:
            tf = self.td / self.n
            self.d = (tf / (tf + dt)) * self.d - self.kp * self.td / (tf + dt) * (y - self.prev_y)
        self.prev_y = y
        if self.ti > 0 and dt > 0:
            di = self.kp * dt / self.ti * error
            if not ((di > 0 and hold_up) or (di < 0 and hold_down)):
                self.i = clamp(self.i + di, self.out_min, self.out_max)
        return clamp(self.p + self.i + self.d, self.out_min, self.out_max)


class LowPass:
    """Tiefpass 1. Ordnung gegen das Rauschen der 10-Bit-Messung."""

    def __init__(self, tau: float):
        self.tau = tau
        self.value: Optional[float] = None

    def reset(self) -> None:
        self.value = None

    def update(self, x: float, dt: float) -> float:
        if self.value is None or self.tau <= 0:
            self.value = x
        else:
            self.value += (x - self.value) * dt / (self.tau + dt)
        return self.value


@dataclass
class Allocation:
    wrg: float          # 0…1
    le: float           # 0…1
    achieved: float     # erreichbarer Hub in K
    wrg_gain: float     # Hub bei voller WRG in K (negativ = kühlt)


def allocate(demand: float, t_aul: float, t_abl: float, efficiency: float,
             heater_rise: float) -> Allocation:
    """Verteilt einen gewünschten Temperaturhub auf WRG-Klappe und Heizventil.

    Zuerst die WRG (kostenlos), dann das Heizregister. Die WRG hebt um
    η·(Abluft − Aussenluft): ist die Abluft wärmer, heizt sie, ist sie
    kälter, kühlt sie (Kälterückgewinnung). Kann die WRG in die gewünschte
    Richtung nichts beitragen, geht die Klappe auf Bypass.
    """
    wrg_gain = efficiency * (t_abl - t_aul)
    x = 0.0
    v = 0.0
    if demand > 0:
        if wrg_gain > 0:
            x = min(1.0, demand / wrg_gain)
        rest = demand - x * max(wrg_gain, 0.0)
        if rest > 0 and heater_rise > 0:
            v = min(1.0, rest / heater_rise)
    elif demand < 0 and wrg_gain < 0:
        x = min(1.0, demand / wrg_gain)
    achieved = x * wrg_gain + v * heater_rise
    return Allocation(x, v, achieved, wrg_gain)


class SupplyAirController:
    """Zulufttemperatur: Vorsteuerung + PID-Korrektur + Aufteilung."""

    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        self.pid = PID(cfg.kp, cfg.ti, cfg.td, cfg.derivative_filter, cfg.setpoint_weight,
                       -cfg.trim_limit, cfg.trim_limit)
        self.filter = LowPass(cfg.measurement_filter)
        self.sat_high = False
        self.sat_low = False

    def reset(self) -> None:
        self.pid.reset()
        self.filter.reset()
        self.sat_high = self.sat_low = False

    def update(self, setpoint: float, temps: Dict[str, float], fan_level: int,
               dt: float) -> Tuple[Allocation, Dict[str, float]]:
        cfg = self.cfg
        y = self.filter.update(temps["t_zul"], dt)
        feedforward = setpoint - temps["t_aul"]
        trim = self.pid.update(setpoint, y, dt, self.sat_high, self.sat_low)
        demand = feedforward + trim
        heater_rise = cfg.heater_rise_stage2 if fan_level >= 2 else cfg.heater_rise_stage1
        alloc = allocate(demand, temps["t_aul"], temps["t_abl"], cfg.hx_efficiency, heater_rise)
        self.sat_high = demand > alloc.achieved + 0.05
        self.sat_low = demand < alloc.achieved - 0.05
        info = {
            "y": round(y, 2),
            "error": round(setpoint - y, 2),
            "feedforward": round(feedforward, 2),
            "trim": round(trim, 2),
            "p": round(self.pid.p, 2),
            "i": round(self.pid.i, 2),
            "d": round(self.pid.d, 2),
            "demand": round(demand, 2),
            "achieved": round(alloc.achieved, 2),
        }
        return alloc, info


class LegacySequence:
    """Rechenweg des Altsystems, unverändert, als Rückfallebene.

    Gesamtsignal 0…2046: untere Hälfte WRG, obere Hälfte Heizventil.
    Signal += kp·e + ki·Σe; Σe = 0 bei |e| < 1 K oder am oberen Anschlag.
    """

    def __init__(self, start_signal: float = 1800.0, kp: float = 1.0, ki: float = 0.1):
        self.kp = kp
        self.ki = ki
        self.signal = float(start_signal)
        self.error_sum = 0.0

    def step(self, setpoint: float, actual: float) -> Tuple[int, int, float]:
        error = setpoint - actual
        self.error_sum += error
        self.signal += self.kp * error + self.ki * self.error_sum
        if self.signal > 2 * ANALOG_MAX:
            self.signal = float(2 * ANALOG_MAX)
            self.error_sum = 0.0
        if self.signal < 0:
            self.signal = 0.0
        if -1 < error < 1:
            self.error_sum = 0.0
        wrg = int(min(self.signal, ANALOG_MAX))
        le = int(max(self.signal - ANALOG_MAX, 0))
        return wrg, le, error


# Nachtauskühlung ---------------------------------------------------------


def parse_hhmm(text: str) -> Optional[datetime.time]:
    text = text.strip()
    if not text:
        return None
    hours, minutes = text.split(":")
    return datetime.time(int(hours), int(minutes))


def in_window(now: datetime.datetime, start: Optional[datetime.time],
              end: Optional[datetime.time]) -> bool:
    """Ohne Fenster (oder Start = Ende) gilt der ganze Tag."""
    if start is None or end is None or start == end:
        return True
    t = now.time()
    if start < end:
        return start <= t < end
    return t >= start or t < end


@dataclass
class NakState:
    active: bool = False
    since: Optional[float] = None
    last_stop: Optional[float] = None
    reason: str = ""


class NightCooling:
    """Nachtauskühlung (NAK): kühle Aussenluft ohne Wärmerückgewinnung
    einblasen, solange es drinnen wärmer ist als das Ziel und draussen
    deutlich kühler als drinnen. Wird per Telegram für 48 h aktiviert; die
    Regelung schaltet die Ventilatoren dann selbst ein und wieder aus.

    Start, wenn alles zutrifft:
      - aktiviert, im Zeitfenster (falls eines gesetzt), kein Frost
        (Sammelalarm und Strömungswächter kommen an dieser Anlage immer
        wieder und halten sie nicht auf; nur Frost)
      - innen > Ziel + start_margin
      - innen − aussen ≥ start_delta
      - aussen ≥ min_outside
      - seit dem letzten Stopp mindestens min_pause vergangen
    Stopp sofort bei Schalter aus, Fenster zu Ende, Frost oder
    fehlenden Messwerten; nach min_run zusätzlich, wenn innen ≤ Ziel,
    innen − aussen < stop_delta oder aussen < min_outside − 1 K.
    """

    def __init__(self, cfg: NakConfig):
        self.cfg = cfg
        self.window_start = parse_hhmm(cfg.window_start)
        self.window_end = parse_hhmm(cfg.window_end)
        self.state = NakState()

    def in_window(self, now: datetime.datetime) -> bool:
        return in_window(now, self.window_start, self.window_end)

    def evaluate(self, now_ts: float, now: datetime.datetime, enabled: bool, target: float,
                 temps: Optional[Dict[str, float]], frost: bool) -> NakState:
        st = self.state
        cfg = self.cfg
        inside = temps.get(cfg.inside_sensor) if temps else None
        outside = temps.get(cfg.outside_sensor) if temps else None

        if st.active:
            reason = None
            if not enabled:
                reason = "nicht mehr aktiviert"
            elif not self.in_window(now):
                reason = "Zeitfenster zu Ende"
            elif frost:
                reason = "Frostmeldung"
            elif inside is None or outside is None:
                reason = "keine Messwerte"
            elif now_ts - (st.since if st.since is not None else now_ts) >= cfg.min_run_minutes * 60:
                if inside <= target:
                    reason = "Ziel erreicht"
                elif inside - outside < cfg.stop_delta:
                    reason = "draussen nicht mehr kühler genug"
                elif outside < cfg.min_outside - 1.0:
                    reason = "draussen zu kalt"
            if reason:
                st.active = False
                st.since = None
                st.last_stop = now_ts
                st.reason = reason
            return st

        st.reason = self._blocker(now_ts, now, enabled, target, inside, outside, frost)
        if not st.reason:
            st.active = True
            st.since = now_ts
            st.reason = "innen %.1f °C, aussen %.1f °C" % (inside, outside)
        return st

    def _blocker(self, now_ts, now, enabled, target, inside, outside, frost) -> str:
        cfg = self.cfg
        if not enabled:
            return "nicht aktiviert"
        if not self.in_window(now):
            return "wartet auf %s–%s" % (cfg.window_start, cfg.window_end)
        if frost:
            return "Frostmeldung"
        if inside is None or outside is None:
            return "keine Messwerte"
        if outside < cfg.min_outside:
            return "draussen unter %.0f °C" % cfg.min_outside
        if inside <= target + cfg.start_margin:
            return "innen nicht über Ziel"
        if inside - outside < cfg.start_delta:
            return "draussen nicht %.0f K kühler" % cfg.start_delta
        if self.state.last_stop is not None and now_ts - self.state.last_stop < cfg.min_pause_minutes * 60:
            return "Pause nach letztem Lauf"
        return ""


# Betriebsfall wählen -----------------------------------------------------


@dataclass
class Decision:
    mode: str
    wrg: int
    le: int
    info: Dict[str, float] = field(default_factory=dict)


class Regulator:
    """Wählt den Betriebsfall und berechnet die Stellwerte."""

    def __init__(self, cfg: ControlConfig):
        self.cfg = cfg
        self.supply = SupplyAirController(cfg)
        self.legacy = LegacySequence(cfg.legacy_start_signal, cfg.legacy_kp, cfg.legacy_ki)
        self._last_pid_time: Optional[float] = None
        self._last_legacy_time: Optional[float] = None
        self._legacy_out: Tuple[str, int, int, float] = (MODE_HEIZEN, 0, 0, 0.0)
        self._out: Tuple[Optional[int], Optional[int]] = (None, None)
        # Stillstand: Ventil offen gegen Frost; bis zur ersten Messung über
        # off_cold_off gilt «kalt», das ist die sichere Seite
        self._off_cold = True

    def _off_le(self, temps: Dict[str, float], faulty) -> int:
        cfg = self.cfg
        # nur der Witterungsfühler: die Aussenluft im stehenden Gerät zeigt
        # ein paar Kelvin zu warm; fällt er aus, bleibt das Ventil offen
        outside = None if "t_witt" in faulty else temps.get("t_witt")
        if outside is None or outside < cfg.off_cold_on:
            self._off_cold = True
        elif outside > cfg.off_cold_off:
            self._off_cold = False
        return cfg.off_le_cold if self._off_cold else cfg.off_le

    def decide(self, now_mono: float, temps: Dict[str, float], setpoint: float,
               fan_level: int, running: bool, frost: bool, nak_active: bool,
               manual: Optional[Dict[str, int]] = None, faulty=()) -> Decision:
        cfg = self.cfg
        needed = ("t_zul", "t_witt") if cfg.algorithm == "legacy" else ("t_zul", "t_aul", "t_abl")
        if frost:
            decision = Decision(MODE_FROST, cfg.frost_wrg, cfg.frost_le)
        elif manual is not None and manual.get("wrg") is not None and manual.get("le") is not None:
            decision = Decision(MODE_HAND, int(manual["wrg"]), int(manual["le"]))
        elif nak_active:
            # kühle Aussenluft direkt durch den Bypass, Heizventil zu
            decision = Decision(MODE_NAK, 0, 0)
        elif not running:
            decision = Decision(MODE_AUS, cfg.off_wrg, self._off_le(temps, faulty))
        elif any(k in faulty for k in needed):
            # mit einem defekten Fühler würde der Regler auf Unsinn regeln
            decision = Decision(MODE_SENSORFEHLER, cfg.safe_wrg, cfg.safe_le)
        elif cfg.algorithm == "legacy":
            decision = self._legacy(now_mono, temps, setpoint)
        else:
            decision = self._pid(now_mono, temps, setpoint, fan_level)
        if manual is not None and decision.mode not in (MODE_FROST, MODE_HAND):
            # nur ein Stellglied von Hand: das andere regelt weiter automatisch
            if manual.get("wrg") is not None:
                decision.wrg = int(manual["wrg"])
            if manual.get("le") is not None:
                decision.le = int(manual["le"])
            decision.mode = MODE_HAND
        if decision.mode not in (MODE_HEIZEN, MODE_WRG, MODE_KRG, MODE_FREIE_KUEHLUNG):
            # nach Hand, Frost, NAK usw. beginnt der PID neu bei der Vorsteuerung
            self._last_pid_time = None
        decision.wrg = int(clamp(decision.wrg, 0, ANALOG_MAX))
        decision.le = int(clamp(decision.le, 0, ANALOG_MAX))
        self._out = (decision.wrg, decision.le)
        return decision

    def safe(self) -> Decision:
        self._last_pid_time = None
        return Decision(MODE_SENSORFEHLER, self.cfg.safe_wrg, self.cfg.safe_le)

    def _pid(self, now_mono: float, temps: Dict[str, float], setpoint: float,
             fan_level: int) -> Decision:
        cfg = self.cfg
        last = self._last_pid_time
        if last is None or now_mono - last > 5 * cfg.cycle_seconds:
            # (Wieder-)Einstieg: bei der Vorsteuerung beginnen, ohne alte Korrektur
            self.supply.reset()
            dt = cfg.cycle_seconds
            rate_from = None
        else:
            dt = max(0.1, now_mono - last)
            rate_from = self._out
        self._last_pid_time = now_mono

        alloc, info = self.supply.update(setpoint, temps, fan_level, dt)
        wrg = alloc.wrg * ANALOG_MAX
        le = alloc.le * ANALOG_MAX
        if rate_from is not None and cfg.max_rate_per_s > 0:
            prev_wrg, prev_le = rate_from
            step = cfg.max_rate_per_s / 100.0 * ANALOG_MAX * dt
            if prev_wrg is not None:
                wrg = clamp(wrg, prev_wrg - step, prev_wrg + step)
            if prev_le is not None:
                le = clamp(le, prev_le - step, prev_le + step)

        if le >= 1:
            mode = MODE_HEIZEN
        elif wrg >= 1:
            mode = MODE_WRG if alloc.wrg_gain >= 0 else MODE_KRG
        else:
            mode = MODE_FREIE_KUEHLUNG
        return Decision(mode, int(round(wrg)), int(round(le)), info)

    def _legacy(self, now_mono: float, temps: Dict[str, float], setpoint: float) -> Decision:
        last = self._last_legacy_time
        if last is None or now_mono - last >= self.cfg.legacy_cycle_seconds:
            self._last_legacy_time = now_mono
            if temps["t_witt"] < setpoint:
                wrg, le, error = self.legacy.step(setpoint, temps["t_zul"])
                self._legacy_out = (MODE_HEIZEN, wrg, le, error)
            else:
                self._legacy_out = (MODE_KRG, ANALOG_MAX, 0, setpoint - temps["t_zul"])
        mode, wrg, le, error = self._legacy_out
        return Decision(mode, wrg, le, {"error": round(error, 2), "signal": self.legacy.signal})
