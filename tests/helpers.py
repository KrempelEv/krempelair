from __future__ import annotations

import datetime
import os
import shutil
import tempfile

from krempelair.config import Config
from krempelair.controller import ControllerService
from krempelair.hardware import I2CBus, Plant
from krempelair.simulation import FakeSMBus, SimulatedPlant
from krempelair.store import StateFile, Store


class FakeTime:
    def __init__(self, start: float = 1_790_000_000.0):
        self.t = start

    def time(self) -> float:
        return self.t

    def local(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(self.t)


class Rig:
    """Regeldienst mit simulierter Anlage, Testdatenbank und Kunstzeit."""

    def __init__(self, cfg: Config = None, start: float = 1_790_000_000.0):
        self.dir = tempfile.mkdtemp(prefix="krempelair-test-")
        self.cfg = cfg or Config()
        self.cfg.general.db_path = os.path.join(self.dir, "k.db")
        self.cfg.general.state_path = os.path.join(self.dir, "state.json")
        self.time = FakeTime(start)
        self.sim = SimulatedPlant(clock=self.time.time)
        self.factory_calls = 0

        def factory(n):
            self.factory_calls += 1
            return FakeSMBus(self.sim)

        self.bus = I2CBus(1, factory)
        self.plant = Plant(self.bus)
        self.store = Store(self.cfg.general.db_path, clock=self.time.time)
        self.state_file = StateFile(self.cfg.general.state_path)
        self.service = self.new_service()

    def new_service(self) -> ControllerService:
        """Neuer Regeldienst auf derselben Anlage und Datenbank (wie nach einem Neustart)."""
        return ControllerService(
            self.cfg, self.plant, self.store, self.state_file,
            clock=self.time.time, monotonic=self.time.time, notify=lambda m: None,
            now_local=self.time.local)

    def warm_night(self, nak_hours: float = 48, target: float = None) -> None:
        """Warmer Raum, kühle konstante Aussenluft, NAK aktiviert."""
        self.sim.t_room = 27.0
        self.sim.outside_mean = 14.0
        self.sim.outside_swing = 0.0
        self.store.set_setting("nak_until", self.time.t + nak_hours * 3600)
        if target is not None:
            self.store.set_setting("nak_target", target)

    def run(self, seconds: float, step: float = None) -> None:
        step = step or self.cfg.control.tick_seconds
        end = self.time.t + seconds
        while self.time.t < end:
            self.service.tick()
            self.time.t += step

    def close(self) -> None:
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)
