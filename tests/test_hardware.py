import unittest

from krempelair.hardware import (
    ADDR_ANALOG_IN_1, ADDR_ANALOG_IN_2, SENSORS, HardwareError, I2CBus, Plant,
    level_from_outputs, outputs_with_level, outputs_with_smoker, scale,
)


class ScriptedBus:
    def __init__(self):
        self.bytes = {}
        self.blocks = {}
        self.written = []
        self.fail_next = 0
        self.closed = False

    def close(self):
        self.closed = True

    def _maybe_fail(self):
        if self.fail_next:
            self.fail_next -= 1
            raise OSError(121, "Remote I/O error")

    def read_byte(self, address):
        self._maybe_fail()
        return self.bytes[address]

    def write_byte(self, address, value):
        self._maybe_fail()
        self.written.append(("byte", address, value))
        self.bytes[address] = value

    def read_i2c_block_data(self, address, command, length):
        self._maybe_fail()
        return self.blocks[address][:length]

    def write_i2c_block_data(self, address, command, data):
        self._maybe_fail()
        self.written.append(("block", address, command, list(data)))


class BitHelperTest(unittest.TestCase):
    def test_level_roundtrip(self):
        for level in (0, 1, 2):
            self.assertEqual(level_from_outputs(outputs_with_level(0b11111000, level)), level)

    def test_level_keeps_other_bits(self):
        self.assertEqual(outputs_with_level(0b11110100, 2), 0b11110111)
        self.assertEqual(outputs_with_level(0b00000111, 0), 0b00000100)

    def test_invalid_level(self):
        with self.assertRaises(ValueError):
            outputs_with_level(0, 3)

    def test_smoker(self):
        self.assertEqual(outputs_with_smoker(0b11, True), 0b111)
        self.assertEqual(outputs_with_smoker(0b111, False), 0b11)

    def test_scale_matches_legacy_formulas(self):
        zul = SENSORS["t_zul"]
        aul = SENSORS["t_aul"]
        for raw in (0, 1, 512, 1000, 1023):
            self.assertEqual(scale(raw, zul), round((float(raw) / 1024) * 50, 1))
            self.assertEqual(scale(raw, aul), round(-30.0 + (float(raw) / 1024) * 100.0, 1))


class PlantTest(unittest.TestCase):
    def setUp(self):
        self.raw = ScriptedBus()
        self.calls = 0

        def factory(n):
            self.calls += 1
            return self.raw

        self.plant = Plant(I2CBus(1, factory))

    def test_digital_inverted(self):
        self.raw.bytes[0x21] = 0xFE
        self.assertEqual(self.plant.read_digital(0x21), 0x01)
        self.plant.write_outputs(0b101)
        self.assertEqual(self.raw.written[-1], ("byte", 0x20, 0xFA))

    def test_analog_board_layout_matches_legacy(self):
        # Altsystem: Kanal n = var[2n+2]*256 + var[2n+1]
        block = [0, 10, 1, 20, 2, 30, 3, 40, 0, 50, 1]
        self.raw.blocks[ADDR_ANALOG_IN_1] = block
        self.raw.blocks[ADDR_ANALOG_IN_2] = block
        values = self.plant.read_analog_board(ADDR_ANALOG_IN_1)
        self.assertEqual(values, [10 + 256, 20 + 512, 30 + 768, 40, 50 + 256])
        raw, temps = self.plant.read_temperatures()
        self.assertEqual(raw["t_zul"], 20 + 512)
        self.assertEqual(raw["t_aul"], 50 + 256)

    def test_analog_write_clamps_and_splits_bytes(self):
        self.assertEqual(self.plant.write_analog(1, 900), 900)
        self.assertEqual(self.raw.written[-1], ("block", 0x58, 1, [900 & 0xFF, 900 >> 8]))
        self.assertEqual(self.plant.write_analog(0, 5000), 1023)
        self.assertEqual(self.plant.write_analog(0, -3), 0)

    def test_bus_opened_once(self):
        self.raw.bytes[0x21] = 0xFF
        for _ in range(500):
            self.plant.read_digital(0x21)
        self.assertEqual(self.calls, 1)

    def test_bus_reopens_after_error(self):
        self.raw.bytes[0x21] = 0xFF
        self.plant.read_digital(0x21)
        self.raw.fail_next = 1
        with self.assertRaises(HardwareError):
            self.plant.read_digital(0x21)
        self.assertTrue(self.raw.closed)
        self.plant.read_digital(0x21)
        self.assertEqual(self.calls, 2)

