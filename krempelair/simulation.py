"""Simulierte I2C-Karten mit einfachem Anlagenmodell.

Für Tests und für den lokalen Betrieb ohne Hardware (`python3 -m krempelair
simulate`). Das Modell ist grob, reicht aber, um Regelung, Bot und
Dashboard mit plausiblen Werten laufen zu lassen.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Dict, List

from .hardware import (
    ADDR_ANALOG_IN_1, ADDR_ANALOG_IN_2, ADDR_ANALOG_OUT, ADDR_BETRIEB, ADDR_OUTPUTS,
    ADDR_STOERUNG, ANALOG_IN_SCALE, ANALOG_MAX, AO_LE, AO_WRG, BETRIEB_BITS, SENSORS,
    STOERUNG_BITS, level_from_outputs, smoker_from_outputs,
)


class SimulatedPlant:
    """Thermisches Modell: Zuluft folgt Aussenluft + WRG-Hub + Heizhub mit
    Verzögerung, der Raum folgt der Zuluft, solange die Lüftung läuft."""

    def __init__(self, clock=time.time, outside_mean: float = 12.0, outside_swing: float = 6.0,
                 efficiency: float = 0.62, heater_rise: float = 24.0, tau: float = 45.0):
        self.clock = clock
        self.outside_mean = outside_mean
        self.outside_swing = outside_swing
        self.efficiency = efficiency
        self.heater_rise = heater_rise
        self.tau = tau
        self.outputs = 0          # logisch, nicht invertiert
        self.wrg = 0
        self.le = 0
        self.t_room = 23.0
        self.t_zul = 18.0
        self.valve = 0.0          # Ventilstellung mit Laufzeit
        self.damper = 0.0         # Klappenstellung mit Laufzeit
        self.frost = False
        self.fail = False         # simulierter Busfehler
        self._last = clock()
        self._lock = threading.Lock()

    def outside(self, now: float) -> float:
        hour = (now % 86400) / 3600.0
        return self.outside_mean + self.outside_swing * math.sin((hour - 9) / 24 * 2 * math.pi)

    def advance(self) -> None:
        now = self.clock()
        dt = max(0.0, min(600.0, now - self._last))
        self._last = now
        if dt == 0:
            return
        flow = level_from_outputs(self.outputs)
        # Stellantriebe: 90 s für den ganzen Hub
        self.damper += max(-dt / 90, min(dt / 90, self.wrg / float(ANALOG_MAX) - self.damper))
        self.valve += max(-dt / 90, min(dt / 90, self.le / float(ANALOG_MAX) - self.valve))
        t_aul = self.outside(now)
        if flow:
            rise = self.heater_rise * (1.0 if flow == 1 else 0.75)
            target = t_aul + self.efficiency * self.damper * (self.t_room - t_aul) + rise * self.valve
            self.t_zul += (target - self.t_zul) * dt / (self.tau + dt)
            self.t_room += (self.t_zul - self.t_room) * dt / (1800.0 * (1.5 if flow == 1 else 1.0) + dt)
        else:
            self.t_zul += (self.t_room - 2 - self.t_zul) * dt / (300.0 + dt)
        # Gebäude: innere Lasten und Wärmeverlust nach aussen
        self.t_room += (0.4 + (t_aul - self.t_room) * 0.02) * dt / 3600.0

    def temps(self) -> Dict[str, float]:
        now = self.clock()
        t_aul = self.outside(now)
        running = level_from_outputs(self.outputs) > 0
        abl = self.t_room if running else self.t_room - 1.5
        return {
            "t_zul": self.t_zul,
            "t_abl": abl,
            "t_fol": abl - self.efficiency * self.damper * (abl - t_aul) if running else abl - 2,
            "t_aul": t_aul,
            "t_raum": self.t_room,
            "t_witt": t_aul - 0.5,
        }


class FakeSMBus:
    """Nachbau der smbus-Schnittstelle über SimulatedPlant."""

    def __init__(self, plant: SimulatedPlant):
        self.plant = plant
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def _check(self) -> None:
        if self.plant.fail:
            raise OSError(121, "Remote I/O error")

    def read_byte(self, address: int) -> int:
        self._check()
        p = self.plant
        with p._lock:
            p.advance()
            if address == ADDR_OUTPUTS:
                return 0xFF - p.outputs
            if address == ADDR_BETRIEB:
                level = level_from_outputs(p.outputs)
                on = []
                if level >= 1:
                    on += ["ZUL_St1", "FOL_St1"]
                if level == 2:
                    on += ["ZUL_St2", "FOL_St2"]
                if p.le > 0:
                    on.append("LE_PU")
                if smoker_from_outputs(p.outputs):
                    on.append("Raucherraum")
                logical = sum(1 << BETRIEB_BITS.index(name) for name in on)
                return 0xFF - logical
            if address == ADDR_STOERUNG:
                logical = (1 << STOERUNG_BITS.index("Frost")) if p.frost else 0
                return 0xFF - logical
        raise OSError(121, "Remote I/O error")

    def write_byte(self, address: int, value: int) -> None:
        self._check()
        if address != ADDR_OUTPUTS:
            raise OSError(121, "Remote I/O error")
        with self.plant._lock:
            self.plant.advance()
            self.plant.outputs = 0xFF - (value & 0xFF)

    def read_i2c_block_data(self, address: int, command: int, length: int) -> List[int]:
        self._check()
        if address not in (ADDR_ANALOG_IN_1, ADDR_ANALOG_IN_2):
            raise OSError(121, "Remote I/O error")
        with self.plant._lock:
            self.plant.advance()
            temps = self.plant.temps()
        data = [0] * 11
        for key, sensor in SENSORS.items():
            if sensor.address != address:
                continue
            raw = int(round((temps[key] - sensor.offset) / sensor.span * ANALOG_IN_SCALE))
            raw = max(0, min(ANALOG_MAX, raw))
            data[1 + 2 * sensor.channel] = raw & 0xFF
            data[2 + 2 * sensor.channel] = raw >> 8
        return data[:length]

    def write_i2c_block_data(self, address: int, command: int, data: List[int]) -> None:
        self._check()
        if address != ADDR_ANALOG_OUT:
            raise OSError(121, "Remote I/O error")
        value = data[0] + 256 * data[1]
        with self.plant._lock:
            self.plant.advance()
            if command == AO_WRG:
                self.plant.wrg = value
            elif command == AO_LE:
                self.plant.le = value
