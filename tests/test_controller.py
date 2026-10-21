import datetime
import os
import sqlite3
import unittest

from krempelair.config import Config
from krempelair.hardware import HardwareError, level_from_outputs

from helpers import Rig


class ControllerTest(unittest.TestCase):
    def setUp(self):
        # 23:00 Ortszeit, damit ein Zeitfenster keine Rolle spielt
        start = datetime.datetime(2026, 7, 1, 23, 0).timestamp()
        self.rig = Rig(start=start)
        self.rig.service.start()

    def tearDown(self):
        self.rig.close()

    def test_publishes_state(self):
        self.rig.run(5)
        ts, state = self.rig.state_file.read()
        self.assertTrue(state["ok"])
        self.assertIn("t_zul", state["temps"])
        self.assertEqual(state["mode"], "aus")

    def test_bus_opened_once_over_many_cycles(self):
        # Das Altsystem ist nach ~340 Zyklen am Dateideskriptor-Limit gestorben
        self.rig.run(3600 * 3, step=1.0)
        self.assertEqual(self.rig.factory_calls, 1)

    def test_command_switches_fans(self):
        cid = self.rig.store.add_command("fan_level", 2, "test")
        self.rig.run(1)
        row = self.rig.store.get_command(cid)
        self.assertEqual(row["status"], "done")
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 2)

    def test_stale_command_expires(self):
        cid = self.rig.store.add_command("fan_level", 1, "test")
        self.rig.time.t += 60
        self.rig.run(1)
        self.assertEqual(self.rig.store.get_command(cid)["status"], "expired")
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 0)

    def test_hardware_failure_does_not_crash_and_recovers(self):
        self.rig.run(10)
        self.rig.sim.fail = True
        self.rig.run(30)
        ts, state = self.rig.state_file.read()
        self.assertFalse(state["ok"])
        self.assertEqual(state["mode"], "sensorfehler")
        self.rig.sim.fail = False
        self.rig.run(5)
        ts, state = self.rig.state_file.read()
        self.assertTrue(state["ok"])
        self.assertEqual((self.rig.sim.wrg, self.rig.sim.le), (1023, 0))  # Anlage aus, draussen warm

    def test_database_failure_does_not_stop_control(self):
        self.rig.run(5)
        self.rig.store.conn.close()
        self.rig.store.conn = sqlite3.connect(":memory:")  # ohne Tabellen
        self.rig.run(10)
        ts, state = self.rig.state_file.read()
        self.assertTrue(state["ok"])
        self.assertIsNotNone(state["db_error"])

    def test_samples_written(self):
        self.rig.run(300, step=1.0)
        count = self.rig.store.conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        self.assertGreaterEqual(count, 4)

    def test_nak_switches_fans_and_restores(self):
        self.rig.warm_night(target=22.0)
        self.rig.run(10)
        ts, state = self.rig.state_file.read()
        self.assertTrue(state["nak"]["active"])
        self.assertEqual(state["mode"], "nak")
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 2)
        self.assertEqual((self.rig.sim.wrg, self.rig.sim.le), (0, 0))
        # Raum kühlt ab, nach Mindestlaufzeit Stopp und Ventilatoren wieder aus
        self.rig.run(3600, step=2.0)
        ts, state = self.rig.state_file.read()
        self.assertLess(self.rig.sim.t_room, 22.5)
        self.assertFalse(state["nak"]["active"])
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 0)

    def test_nak_expires_after_activation_period(self):
        self.rig.warm_night(nak_hours=0.5, target=10.0)  # Ziel nie erreicht
        self.rig.run(600, step=2.0)
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 2)
        self.rig.run(1800, step=2.0)
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 0)

    def test_manual_fan_off_ends_nak(self):
        self.rig.warm_night()
        self.rig.run(10)
        self.rig.store.add_command("fan_level", 0, "test")
        self.rig.run(10)
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 0)
        self.assertEqual(self.rig.store.get_setting("nak_until"), 0)

    def test_nak_ownership_survives_restart(self):
        self.rig.warm_night(target=22.0)
        self.rig.run(10)
        rig = self.rig
        rig.service = rig.new_service()  # neuer Dienst, gleiche Datenbank (z. B. nach Absturz)
        rig.service.start()
        rig.run(3600, step=2.0)
        self.assertEqual(level_from_outputs(rig.sim.outputs), 0)

    def test_manual_mode_with_expiry(self):
        self.rig.store.set_setting("manual", {"wrg": 100, "le": 200,
                                              "until": self.rig.time.t + 60})
        self.rig.run(5)
        self.assertEqual((self.rig.sim.wrg, self.rig.sim.le), (100, 200))
        self.rig.run(70)
        self.assertEqual((self.rig.sim.wrg, self.rig.sim.le), (1023, 0))  # wieder «aus»


class ClosedLoopTest(unittest.TestCase):
    """PID gegen das Simulationsmodell: Sollwertsprung ohne Schwingen."""

    def test_setpoint_step(self):
        cfg = Config()
        rig = Rig(cfg, start=datetime.datetime(2026, 1, 15, 12, 0).timestamp())
        try:
            rig.sim.outside_mean = 2.0
            rig.sim.outside_swing = 0.0
            rig.sim.t_room = 21.0
            rig.service.start()
            rig.store.set_setting("temp_setpoint", 19.0)
            rig.store.add_command("fan_level", 1, "test")
            rig.run(1800, step=0.5)
            rig.store.set_setting("temp_setpoint", 22.0)
            errors = []
            for _ in range(int(1800 / 2)):
                rig.run(2, step=0.5)
                errors.append(22.0 - rig.sim.t_zul)
            overshoot = -min(errors)
            self.assertLess(overshoot, 1.0)
            self.assertLess(max(abs(e) for e in errors[-150:]), 0.4)  # letzte 5 min
        finally:
            rig.close()



class ReviewFindingsTest(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(start=datetime.datetime(2026, 7, 1, 23, 0).timestamp())
        self.rig.service.start()

    def tearDown(self):
        self.rig.close()

    def test_nak_stops_on_bus_failure_and_restores_later(self):
        rig = self.rig
        rig.warm_night()
        rig.run(10)
        self.assertEqual(level_from_outputs(rig.sim.outputs), 2)
        rig.sim.fail = True
        rig.run(10)
        self.assertFalse(rig.service.nak.state.active)
        self.assertTrue(rig.service._nak_fans["restore"])
        rig.sim.fail = False
        rig.run(4)
        self.assertEqual(level_from_outputs(rig.sim.outputs), 0)
        self.assertFalse(rig.service._nak_fans["restore"])

    def test_command_never_runs_twice_when_result_cannot_be_saved(self):
        rig = self.rig
        cid = rig.store.add_command("fan_level", 2, "test")
        calls = []
        original = rig.service.execute

        def counting(*args):
            calls.append(args)
            return original(*args)

        rig.service.execute = counting
        rig.store.finish_command = lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("voll"))
        rig.run(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(rig.store.get_command(cid)["status"], "running")


class ConfigFallbackTest(unittest.TestCase):
    def test_invalid_values_keep_defaults(self):
        import tempfile
        from krempelair.config import load_config
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as fh:
            fh.write("[nak]\nwindow_start = 7\nfan_level = 3\ntarget = 20\n"
                     "[control]\nkp = abc\nalgorithm = fuzzy\nti = 120\n[unbekannt]\nx = 1\n")
        cfg = load_config(fh.name)
        os.unlink(fh.name)
        self.assertEqual(cfg.nak.window_start, "")
        self.assertEqual(cfg.nak.fan_level, 2)
        self.assertEqual(cfg.control.kp, 1.0)
        self.assertEqual(cfg.control.algorithm, "pid")
        self.assertEqual(cfg.control.ti, 120.0)
        self.assertEqual(len(cfg.errors), 6)


class CodeReviewFindingsTest(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(start=datetime.datetime(2026, 1, 15, 12, 0).timestamp())
        self.rig.service.start()

    def tearDown(self):
        self.rig.close()

    def test_broken_sensor_gives_safe_position_not_full_heating(self):
        rig = self.rig
        rig.store.add_command("fan_level", 1, "test")
        rig.run(60)
        original = rig.sim.temps
        rig.sim.temps = lambda: dict(original(), t_aul=-30.0)  # Rohwert 0: Bruch
        rig.run(10)
        ts, state = rig.state_file.read()
        self.assertEqual(state["mode"], "sensorfehler")
        self.assertEqual(state["sensor_faults"], ["t_aul"])
        self.assertIsNone(state["temps"]["t_aul"])
        self.assertEqual((rig.sim.wrg, rig.sim.le), (1023, 900))

    def test_failing_output_card_reaches_safe_state_without_event_flood(self):
        rig = self.rig
        rig.run(10)
        original = rig.plant.write_analog

        def broken(channel, value):
            raise HardwareError("0x58 antwortet nicht")

        rig.plant.write_analog = broken
        before = len(rig.store.events(500))
        rig.run(60)
        ts, state = rig.state_file.read()
        self.assertFalse(state["ok"])
        self.assertEqual(state["mode"], "sensorfehler")
        self.assertLess(len(rig.store.events(500)) - before, 5)
        rig.plant.write_analog = original

    def test_nak_restart_while_restore_pending_still_restores(self):
        rig = self.rig
        rig.time.t = datetime.datetime(2026, 7, 1, 23, 0).timestamp()
        rig.warm_night(target=22.0)
        rig.run(10)
        self.assertEqual(level_from_outputs(rig.sim.outputs), 2)
        rig.sim.fail = True
        rig.run(20 * 60, step=2.0)          # länger als die NAK-Pause
        rig.sim.fail = False
        rig.run(10)                          # NAK startet sofort wieder
        self.assertTrue(rig.service._nak_fans["owned"])
        self.assertEqual(rig.service._nak_fans["prev_level"], 0)
        rig.run(3600, step=2.0)
        self.assertEqual(level_from_outputs(rig.sim.outputs), 0)


class RuntimeLimitTest(unittest.TestCase):
    def test_fans_off_after_limit_and_counting_survives_restart(self):
        cfg = Config()
        cfg.control.max_run_hours = 2
        rig = Rig(cfg, start=datetime.datetime(2026, 1, 15, 12, 0).timestamp())
        try:
            rig.service.start()
            rig.store.add_command("fan_level", 1, "test")
            rig.run(3600, step=2.0)
            self.assertEqual(level_from_outputs(rig.sim.outputs), 1)
            rig.service = rig.new_service()        # Neustart nach 1 h
            rig.service.start()
            rig.run(3700, step=2.0)
            self.assertEqual(level_from_outputs(rig.sim.outputs), 0)
            self.assertTrue(any("ununterbrochenem Betrieb" in e["message"]
                                for e in rig.store.events(50)))
            ts, state = rig.state_file.read()
            self.assertIsNone(state["run_since"])
        finally:
            rig.close()

    def test_limit_zero_disables(self):
        cfg = Config()
        cfg.control.max_run_hours = 0
        rig = Rig(cfg)
        try:
            rig.service.start()
            rig.store.add_command("fan_level", 2, "test")
            rig.run(3 * 86400, step=10.0)
            self.assertEqual(level_from_outputs(rig.sim.outputs), 2)
        finally:
            rig.close()
