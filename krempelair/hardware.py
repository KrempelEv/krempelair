"""Zugriff auf die I2C-Karten der Lüftung.

Belegung (aus dem Altsystem übernommen):

    0x20  Digitalausgang   Bit 0 Ventilatoren ein / Stufe 1, Bit 1 Stufe 2,
                           Bit 2 Raucherraum
    0x21  Digitaleingang   Betriebsmeldungen
    0x22  Digitaleingang   Störmeldungen
    0x08  Analogeingang    K1 Zuluft, K2 Abluft, K3 Fortluft, K4 Aussenluft
    0x09  Analogeingang    K1 Raum vorne, K2 Witterung
    0x58  Analogausgang    K0 WRG-Bypassklappe, K1 Heizventil (LE)

Die Digitalkarten sind aktiv-low, darum wird beim Lesen und Schreiben
invertiert. Der Bus wird genau einmal geöffnet und bei einem Fehler
geschlossen und beim nächsten Zugriff neu geöffnet. Das Altsystem hat pro
Zugriff einen neuen Dateideskriptor geöffnet und nie geschlossen; daran ist
die Regelung jede Stunde gestorben.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

log = logging.getLogger(__name__)

ADDR_OUTPUTS = 0x20
ADDR_BETRIEB = 0x21
ADDR_STOERUNG = 0x22
ADDR_ANALOG_IN_1 = 0x08
ADDR_ANALOG_IN_2 = 0x09
ADDR_ANALOG_OUT = 0x58

AO_WRG = 0
AO_LE = 1
ANALOG_MAX = 1023           # Stellausgänge 0…1023
ANALOG_IN_SCALE = 1024.0    # Eingänge: Rohwert / 1024 × Messbereich (wie im Altsystem)

BIT_FAN_ON = 0
BIT_FAN_STAGE2 = 1
BIT_SMOKER = 2

LEVEL_LABELS = {0: "aus", 1: "Stufe 1", 2: "Stufe 2"}

BETRIEB_BITS = ("ZUL_St1", "ZUL_St2", "FOL_St1", "FOL_St2", "LE_PU", "Raucherraum", "E6", "E7")
STOERUNG_BITS = ("Quit", "Sammelalarm", "Frost", "Stroem_ZUL", "Stroem_FOL", "AL5", "AL6", "AL7")

BETRIEB_LABELS = {
    "ZUL_St1": "Zuluft Stufe 1",
    "ZUL_St2": "Zuluft Stufe 2",
    "FOL_St1": "Fortluft Stufe 1",
    "FOL_St2": "Fortluft Stufe 2",
    "LE_PU": "Pumpe Heizregister",
    "Raucherraum": "Raucherraum",
    "E6": "Eingang 6",
    "E7": "Eingang 7",
}
STOERUNG_LABELS = {
    "Quit": "Quittierung",
    "Sammelalarm": "Sammelalarm",
    "Frost": "Frost",
    "Stroem_ZUL": "Strömungswächter Zuluft",
    "Stroem_FOL": "Strömungswächter Fortluft",
    "AL5": "Alarm 5",
    "AL6": "Alarm 6",
    "AL7": "Alarm 7",
}

MASK_RUNNING = (1 << BETRIEB_BITS.index("ZUL_St1")) | (1 << BETRIEB_BITS.index("ZUL_St2"))
MASK_FROST = 1 << STOERUNG_BITS.index("Frost")
MASK_ALARM = 1 << STOERUNG_BITS.index("Sammelalarm")
# Meldungen, die als Störung gelten (Quittierung ist keine)
MASK_FAULTS = 0xFF & ~(1 << STOERUNG_BITS.index("Quit"))


@dataclass(frozen=True)
class Sensor:
    key: str
    label: str
    address: int
    channel: int
    offset: float
    span: float


SENSORS = OrderedDict(
    (s.key, s)
    for s in (
        Sensor("t_zul", "Zuluft", ADDR_ANALOG_IN_1, 1, 0.0, 50.0),
        Sensor("t_abl", "Abluft", ADDR_ANALOG_IN_1, 2, 0.0, 50.0),
        Sensor("t_fol", "Fortluft", ADDR_ANALOG_IN_1, 3, 0.0, 50.0),
        Sensor("t_aul", "Aussenluft", ADDR_ANALOG_IN_1, 4, -30.0, 100.0),
        Sensor("t_raum", "Raum vorne", ADDR_ANALOG_IN_2, 1, 0.0, 50.0),
        Sensor("t_witt", "Witterung", ADDR_ANALOG_IN_2, 2, -30.0, 100.0),
    )
)


class HardwareError(Exception):
    pass


def default_smbus_factory(bus_number: int):
    import smbus  # erst hier, damit Tests und Simulator ohne smbus laufen

    return smbus.SMBus(bus_number)


class I2CBus:
    """Ein einziger, wiederverwendeter Buszugang mit Wiederverbindung."""

    def __init__(self, bus_number: int = 1, factory: Callable = default_smbus_factory):
        self._bus_number = bus_number
        self._factory = factory
        self._bus = None
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:  # Schliessen darf nie selbst scheitern
                pass
            self._bus = None

    def _call(self, name: str, *args):
        with self._lock:
            try:
                if self._bus is None:
                    self._bus = self._factory(self._bus_number)
                return getattr(self._bus, name)(*args)
            except Exception as exc:  # auch ImportError, wenn smbus fehlt
                self._close_locked()
                raise HardwareError("%s%s: %s" % (name, _fmt_args(args), exc))

    def read_byte(self, address: int) -> int:
        return self._call("read_byte", address)

    def write_byte(self, address: int, value: int) -> None:
        self._call("write_byte", address, value)

    def read_block(self, address: int, command: int, length: int) -> List[int]:
        return list(self._call("read_i2c_block_data", address, command, length))

    def write_block(self, address: int, command: int, data: List[int]) -> None:
        self._call("write_i2c_block_data", address, command, data)


def _fmt_args(args) -> str:
    return "(" + ", ".join("0x%02x" % a if isinstance(a, int) else repr(a) for a in args) + ")"


def scale(raw: int, sensor: Sensor) -> float:
    return round(sensor.offset + raw / ANALOG_IN_SCALE * sensor.span, 1)


def to_pct(value):
    """Stellwert 0…1023 → Prozent (eine Nachkommastelle)."""
    return None if value is None else round(value * 100.0 / ANALOG_MAX, 1)


def from_pct(percent: float) -> int:
    return int(round(max(0.0, min(100.0, percent)) * ANALOG_MAX / 100.0))


def level_from_outputs(outputs: int) -> int:
    if outputs & (1 << BIT_FAN_ON):
        return 2 if outputs & (1 << BIT_FAN_STAGE2) else 1
    return 0


def outputs_with_level(outputs: int, level: int) -> int:
    if level not in (0, 1, 2):
        raise ValueError("Stufe muss 0, 1 oder 2 sein")
    outputs &= ~((1 << BIT_FAN_ON) | (1 << BIT_FAN_STAGE2))
    if level >= 1:
        outputs |= 1 << BIT_FAN_ON
    if level == 2:
        outputs |= 1 << BIT_FAN_STAGE2
    return outputs


def smoker_from_outputs(outputs: int) -> bool:
    return bool(outputs & (1 << BIT_SMOKER))


def outputs_with_smoker(outputs: int, on: bool) -> int:
    if on:
        return outputs | (1 << BIT_SMOKER)
    return outputs & ~(1 << BIT_SMOKER)


def bits_to_dict(value: int, names) -> Dict[str, bool]:
    return OrderedDict((name, bool(value & (1 << i))) for i, name in enumerate(names))


@dataclass
class Reading:
    betrieb: int
    stoerung: int
    outputs: int
    raw: Dict[str, int] = field(default_factory=dict)
    temps: Dict[str, float] = field(default_factory=dict)
    # Fühler am Anschlag des Wandlers (0 oder 1023): Bruch oder Kurzschluss
    faulty: Tuple[str, ...] = ()


class Plant:
    """Anlagensicht auf die I2C-Karten."""

    def __init__(self, bus: I2CBus):
        self.bus = bus

    def read_digital(self, address: int) -> int:
        return 0xFF - (self.bus.read_byte(address) & 0xFF)

    def read_outputs(self) -> int:
        return self.read_digital(ADDR_OUTPUTS)

    def write_outputs(self, value: int) -> None:
        self.bus.write_byte(ADDR_OUTPUTS, 0xFF - (value & 0xFF))

    def read_analog_board(self, address: int) -> List[int]:
        """Fünf 10-Bit-Kanäle; Byte 0 ist der Zeiger, dann je Kanal Low/High."""
        data = self.bus.read_block(address, 0x00, 11)
        if len(data) < 11:
            raise HardwareError("Analogkarte 0x%02x: nur %d Bytes" % (address, len(data)))
        return [data[1 + 2 * ch] + 256 * data[2 + 2 * ch] for ch in range(5)]

    def read_temperatures(self):
        boards = {}
        raw = OrderedDict()
        temps = OrderedDict()
        for key, sensor in SENSORS.items():
            if sensor.address not in boards:
                boards[sensor.address] = self.read_analog_board(sensor.address)
            raw[key] = boards[sensor.address][sensor.channel]
            temps[key] = scale(raw[key], sensor)
        return raw, temps

    def write_analog(self, channel: int, value: int) -> int:
        value = max(0, min(ANALOG_MAX, int(value)))
        self.bus.write_block(ADDR_ANALOG_OUT, channel, [value & 0xFF, value >> 8])
        return value

    def read(self) -> Reading:
        betrieb = self.read_digital(ADDR_BETRIEB)
        stoerung = self.read_digital(ADDR_STOERUNG)
        outputs = self.read_outputs()
        raw, temps = self.read_temperatures()
        faulty = tuple(k for k, v in raw.items() if v <= 0 or v >= ANALOG_MAX)
        return Reading(betrieb, stoerung, outputs, raw, temps, faulty)
