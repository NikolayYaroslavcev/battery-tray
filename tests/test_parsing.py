"""Parsers checked against bytes recorded from real devices (Razer Barracuda X dongle, 2026-11-28).
Run: python -m unittest discover -s tests"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import battery_tray as b

BX = 0x0550


def report(hexstr):
    return list(bytes.fromhex(hexstr.replace(" ", ""))) + [0] * 30


# notes the dongle sends by itself; after the length in byte 1 comes a stale record from an older note
LEVEL_81 = report("02 0e 5049 08 de 917902 00 04 00 21 02 01 51  5049 08 c8 a3d601 00 04 00 2a 02 01 01")
PLUGGED = report("02 0e 5049 08 e0 1eb602 00 04 00 2a 02 01 01  5049 08 c8 a3d601 00 04 00 2a 02 01 01")
UNPLUGGED = report("02 0e 5049 08 e3 1df002 00 04 00 2a 02 01 00  5049 08 c8 a3d601 00 04 00 2a 02 01 01")
OFF = report("02 0e 5049 08 c7 ada634 00 04 00 20 02 01 00  4901 c061dc33 0003000e80 0001")
ON = report("02 1a 5049 0e c8 00000000 02 00 e3 01  5049 08 c9 bef734 00 04 00 20 02 01 01")
OTHER_OFF = report("02 0c 5049 0e c6 00000000 02 00 e3 00 1000 4901 c061dc33")
REPLY_90 = [0, 0] + list(b"PI") + [8, 1, 0, 0, 0, 0, 4, 0, 0x21, 1, 1, 90] + [0] * 40


class RazerNotes(unittest.TestCase):
    def setUp(self):
        b._radio[:] = [(BX, "headset", "Razer Barracuda X", (77, False))]

    def state(self):
        return b._radio[0][3]

    def test_level_and_charging(self):
        self.assertTrue(b.razer_note(BX, LEVEL_81))
        self.assertEqual(self.state(), (81, False))
        self.assertTrue(b.razer_note(BX, PLUGGED))
        self.assertEqual(self.state(), (81, True))
        self.assertTrue(b.razer_note(BX, UNPLUGGED))
        self.assertEqual(self.state(), (81, False))   # not the stale "charging" record after the length

    def test_off_and_on(self):
        self.assertTrue(b.razer_note(BX, OFF))
        self.assertEqual(self.state(), "offline")
        self.assertIs(b.razer_note(BX, ON), False)    # on again: ask for the level

    def test_ignored(self):
        for r in (REPLY_90, OTHER_OFF, [], [0] * 64):
            self.assertIsNone(b.razer_note(BX, r))
        self.assertEqual(self.state(), (77, False))

    def test_no_reading_yet(self):
        b._radio[:] = [(BX, "headset", "Razer Barracuda X", "offline")]
        self.assertIs(b.razer_note(BX, PLUGGED), False)

    def test_reply(self):
        self.assertEqual(b._pi_value(REPLY_90, 0x21), 90)
        self.assertIsNone(b._pi_value(REPLY_90, 0x2A))


class Logitech(unittest.TestCase):
    def test_voltage(self):
        self.assertEqual(b._volt_percent(4300), 100)
        self.assertEqual(b._volt_percent(4186), 100)
        self.assertEqual(b._volt_percent(3811), 50)
        self.assertEqual(b._volt_percent(3790), 44)
        self.assertEqual(b._volt_percent(3500), 0)
        self.assertEqual(b._volt_percent(3000), 0)


class Discovery(unittest.TestCase):
    def test_classify(self):
        d = lambda pid, name: {"product_id": pid, "product_string": name}
        self.assertEqual(b.classify(d(BX, "Razer Barracuda X 2.4")), "headset")
        self.assertEqual(b.classify(d(0x007B, "")), "mouse")
        self.assertEqual(b.classify(d(0x0999, "Razer BlackWidow V4")), None)        # wired
        self.assertEqual(b.classify(d(0x0999, "Razer BlackWidow V4 Pro Wireless")), "keyboard")
        self.assertEqual(b.nice_name(d(BX, "Razer Barracuda X 2.4")), "Razer Barracuda X")

    def test_versions(self):
        self.assertTrue(b.is_newer("2.1.10", "2.1.9"))
        self.assertFalse(b.is_newer("2.1.4", "2.1.4"))


if __name__ == "__main__":
    unittest.main()
