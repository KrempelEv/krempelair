import datetime
import random
import unittest

from krempelair.config import ControlConfig, NakConfig
from krempelair.control import (
    MODE_AUS, MODE_FREIE_KUEHLUNG, MODE_FROST, MODE_HAND, MODE_HEIZEN, MODE_KRG, MODE_NAK,
    MODE_WRG, PID, LegacySequence, NightCooling, Regulator, allocate, in_window, parse_hhmm,
)


def legacy_reference(stellsignal, error_sum, soll, ist):
    """Wörtlich aus /opt/krempel/regulation/regulation.py (Heizbetrieb)."""
    error = soll - ist
    error_sum = error_sum + error
    y = 1 * error + 0.1 * error_sum
    stellsignal = stellsignal + y
    if stellsignal > 2046:
        stellsignal = 2046
        error_sum = 0
    if stellsignal < 0:
        stellsignal = 0
    wrg = stellsignal
    if wrg > 1023:
        wrg = 1023
    le = stellsignal - 1023
    if le < 0:
        le = 0
    if error < 1 and error > -1:
        error_sum = 0
    return stellsignal, error_sum, int(wrg), int(le)


def temps(zul=20.0, abl=22.0, aul=5.0, witt=5.0, raum=22.0, fol=10.0):
    return {"t_zul": zul, "t_abl": abl, "t_aul": aul, "t_witt": witt, "t_raum": raum,
            "t_fol": fol}


class LegacyTest(unittest.TestCase):
    def test_identical_to_old_code(self):
        rnd = random.Random(4)
        seq = LegacySequence()
        signal, error_sum = 1800, 0
        for _ in range(2000):
            soll = rnd.choice([17, 20, 23, 26])
            ist = round(rnd.uniform(5, 35), 1)
            signal, error_sum, wrg, le = legacy_reference(signal, error_sum, soll, ist)
            got_wrg, got_le, _ = seq.step(soll, ist)
            self.assertEqual((got_wrg, got_le), (wrg, le))
            self.assertAlmostEqual(seq.signal, signal)


class PIDTest(unittest.TestCase):
    def test_proportional_and_integral(self):
        pid = PID(kp=2.0, ti=10.0)
        out = pid.update(20.0, 19.0, dt=1.0)
        self.assertAlmostEqual(out, 2.0 + 0.2)

    def test_conditional_integration(self):
        pid = PID(kp=1.0, ti=10.0)
        pid.update(20.0, 10.0, 1.0, hold_up=True)
        self.assertEqual(pid.i, 0.0)
        pid.update(20.0, 30.0, 1.0, hold_up=True)  # nach unten darf integriert werden
        self.assertLess(pid.i, 0.0)

    def test_output_limits(self):
        pid = PID(kp=10.0, ti=1.0, out_min=-5, out_max=5)
        for _ in range(100):
            out = pid.update(100.0, 0.0, 1.0)
        self.assertEqual(out, 5)
        self.assertEqual(pid.i, 5)

    def test_derivative_on_measurement_ignores_setpoint_jump(self):
        pid = PID(kp=1.0, ti=0.0, td=10.0)
        pid.update(20.0, 20.0, 1.0)
        pid.update(25.0, 20.0, 1.0)
        self.assertEqual(pid.d, 0.0)
        pid.update(25.0, 21.0, 1.0)
        self.assertLess(pid.d, 0.0)


class AllocateTest(unittest.TestCase):
    def test_wrg_first_then_heater(self):
        a = allocate(5.0, t_aul=0.0, t_abl=20.0, efficiency=0.5, heater_rise=20.0)
        self.assertAlmostEqual(a.wrg, 0.5)
        self.assertEqual(a.le, 0.0)
        a = allocate(15.0, 0.0, 20.0, 0.5, 20.0)
        self.assertEqual(a.wrg, 1.0)
        self.assertAlmostEqual(a.le, 0.25)
        self.assertAlmostEqual(a.achieved, 15.0)

    def test_heater_saturates(self):
        a = allocate(50.0, 0.0, 20.0, 0.5, 20.0)
        self.assertEqual((a.wrg, a.le), (1.0, 1.0))
        self.assertAlmostEqual(a.achieved, 30.0)

    def test_krg_when_outside_warmer(self):
        # aussen 30, Abluft 24: Tauscher hält die Kälte der Innenluft
        a = allocate(-2.0, t_aul=30.0, t_abl=24.0, efficiency=0.5, heater_rise=20.0)
        self.assertAlmostEqual(a.wrg, 2.0 / 3.0)
        self.assertEqual(a.le, 0.0)

    def test_bypass_when_outside_cooler_and_cooling_needed(self):
        a = allocate(-2.0, t_aul=15.0, t_abl=26.0, efficiency=0.5, heater_rise=20.0)
        self.assertEqual((a.wrg, a.le), (0.0, 0.0))

    def test_no_wrg_when_it_cannot_heat(self):
        a = allocate(4.0, t_aul=25.0, t_abl=22.0, efficiency=0.5, heater_rise=20.0)
        self.assertEqual(a.wrg, 0.0)
        self.assertAlmostEqual(a.le, 0.2)


class RegulatorTest(unittest.TestCase):
    def setUp(self):
        self.cfg = ControlConfig()
        self.reg = Regulator(self.cfg)

    def test_frost_has_priority(self):
        d = self.reg.decide(0, temps(), 20, 1, True, True, True, {"wrg": 0, "le": 0})
        self.assertEqual((d.mode, d.wrg, d.le), (MODE_FROST, 1023, 900))

    def test_manual_before_nak_and_off(self):
        d = self.reg.decide(0, temps(), 20, 0, False, False, True, {"wrg": 100, "le": 200})
        self.assertEqual((d.mode, d.wrg, d.le), (MODE_HAND, 100, 200))

    def test_nak_bypass_and_valve_closed(self):
        d = self.reg.decide(0, temps(), 20, 2, True, False, True)
        self.assertEqual((d.mode, d.wrg, d.le), (MODE_NAK, 0, 0))

    def test_off_state_like_legacy(self):
        d = self.reg.decide(0, temps(), 20, 0, False, False, False)
        self.assertEqual((d.mode, d.wrg, d.le), (MODE_AUS, 1023, 900))

    def test_modes_in_operation(self):
        d = self.reg.decide(0, temps(zul=10, aul=0, abl=20), 20, 1, True, False, False)
        self.assertEqual(d.mode, MODE_HEIZEN)
        reg = Regulator(self.cfg)
        d = reg.decide(0, temps(zul=20, aul=15, abl=22), 18, 1, True, False, False)
        self.assertEqual(d.mode, MODE_WRG)
        reg = Regulator(self.cfg)
        d = reg.decide(0, temps(zul=29, aul=30, abl=24), 20, 1, True, False, False)
        self.assertEqual(d.mode, MODE_KRG)
        self.assertEqual(d.le, 0)
        reg = Regulator(self.cfg)
        d = reg.decide(0, temps(zul=22, aul=18, abl=26), 16, 1, True, False, False)
        self.assertEqual((d.mode, d.wrg, d.le), (MODE_FREIE_KUEHLUNG, 0, 0))

    def test_rate_limit(self):
        cfg = ControlConfig(max_rate_per_s=5.0)
        reg = Regulator(cfg)
        reg.decide(0, temps(zul=20, aul=0, abl=20), 20, 1, True, False, False)
        first = reg._out
        d = reg.decide(2, temps(zul=5, aul=0, abl=20), 40, 1, True, False, False)
        self.assertLessEqual(abs(d.le - first[1]), 0.05 * 1023 * 2 + 1)

    def test_legacy_algorithm_option(self):
        reg = Regulator(ControlConfig(algorithm="legacy"))
        d = reg.decide(0, temps(zul=18, witt=5), 20, 1, True, False, False)
        self.assertEqual(d.mode, MODE_HEIZEN)
        d = reg.decide(1, temps(zul=18, witt=25), 20, 1, True, False, False)
        self.assertEqual(d.mode, MODE_HEIZEN)  # erst nach 10 s neu gerechnet
        d = reg.decide(11, temps(zul=18, witt=25), 20, 1, True, False, False)
        self.assertEqual((d.mode, d.wrg, d.le), (MODE_KRG, 1023, 0))


class WindowTest(unittest.TestCase):
    def test_over_midnight(self):
        start, end = parse_hhmm("22:00"), parse_hhmm("07:00")
        at = lambda h, m=0: datetime.datetime(2026, 7, 1, h, m)
        self.assertTrue(in_window(at(23), start, end))
        self.assertTrue(in_window(at(3), start, end))
        self.assertFalse(in_window(at(7), start, end))
        self.assertFalse(in_window(at(12), start, end))

    def test_empty_means_always(self):
        self.assertTrue(in_window(datetime.datetime(2026, 7, 1, 12), parse_hhmm(""),
                                  parse_hhmm("")))


class NightCoolingTest(unittest.TestCase):
    def setUp(self):
        self.cfg = NakConfig()
        self.nak = NightCooling(self.cfg)
        self.now = datetime.datetime(2026, 7, 1, 23, 0)

    def ev(self, ts, inside, outside, enabled=True, frost=False, target=20.0):
        return self.nak.evaluate(ts, self.now, enabled, target,
                                 {"t_raum": inside, "t_witt": outside}, frost)

    def test_starts_and_respects_min_run(self):
        self.assertTrue(self.ev(0, 25, 18).active)
        self.assertTrue(self.ev(60, 19.5, 18).active)       # Mindestlaufzeit
        self.assertFalse(self.ev(16 * 60, 19.5, 18).active)  # Ziel erreicht
        self.assertEqual(self.nak.state.reason, "Ziel erreicht")

    def test_pause_after_stop(self):
        self.ev(0, 25, 18)
        self.ev(16 * 60, 19.5, 18)
        self.assertFalse(self.ev(20 * 60, 25, 18).active)
        self.assertTrue(self.ev(32 * 60, 25, 18).active)

    def test_blockers(self):
        self.assertFalse(self.ev(0, 25, 18, enabled=False).active)
        self.assertFalse(self.ev(0, 25, 18, frost=True).active)
        self.assertFalse(self.ev(0, 25, 23.5).active)        # nicht 3 K kühler
        self.assertFalse(self.ev(0, 25, 8).active)           # draussen zu kalt
        self.assertFalse(self.ev(0, 20.8, 10).active)        # innen kaum über Ziel

    def test_stops_immediately_when_disabled(self):
        self.ev(0, 25, 18)
        self.assertFalse(self.ev(60, 25, 18, enabled=False).active)

