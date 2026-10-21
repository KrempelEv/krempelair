import datetime
import json
import threading
import unittest
import urllib.error
import urllib.request

from krempelair.bot import hash_password
from krempelair.config import Config
from krempelair.hardware import level_from_outputs
from krempelair.panel import Panel
from krempelair.web import BoundedServer, Dashboard, make_handler

from helpers import Rig


class LanPanel(Panel):
    """Im Test kommt alles von 127.0.0.1; das zählt hier als LAN."""

    @staticmethod
    def is_lan(address, headers):
        return not headers.get("X-Forwarded-For")


class IsLanTest(unittest.TestCase):
    def test_addresses(self):
        no_proxy = {}
        self.assertTrue(Panel.is_lan("192.168.1.20", no_proxy))
        self.assertTrue(Panel.is_lan("10.0.0.5", no_proxy))
        self.assertTrue(Panel.is_lan("::ffff:192.168.1.20", no_proxy))
        self.assertFalse(Panel.is_lan("127.0.0.1", no_proxy))    # Rückkanal
        self.assertFalse(Panel.is_lan("::1", no_proxy))
        self.assertFalse(Panel.is_lan("8.8.8.8", no_proxy))
        self.assertFalse(Panel.is_lan("192.168.1.20", {"X-Forwarded-For": "1.2.3.4"}))


class PanelTest(unittest.TestCase):
    def setUp(self):
        cfg = Config()
        cfg.telegram.password_hash = hash_password("richtig", iterations=1000)
        self.rig = Rig(cfg, start=datetime.datetime(2026, 1, 15, 12, 0).timestamp())
        self.rig.service.start()
        self.rig.run(3)

        def sleep(seconds):
            self.rig.run(seconds, step=0.25)

        self.panel = LanPanel(cfg, clock=self.rig.time.time, sleep=sleep)
        dashboard = Dashboard(cfg, self.rig.state_file)
        self.server = BoundedServer(("127.0.0.1", 0), make_handler(dashboard, self.panel))
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cookie = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.rig.close()

    def post(self, path, body, headers=None, csrf=True):
        h = {"Content-Type": "application/json"}
        if csrf:
            h["X-Krempelair"] = "1"
        if self.cookie:
            h["Cookie"] = self.cookie
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                cookie = r.headers.get("Set-Cookie")
                if cookie:
                    self.cookie = cookie.split(";")[0]
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            data = json.loads(exc.read())
            exc.close()
            return exc.code, data

    def login(self):
        return self.post("/api/login", {"password": "richtig"})

    def test_session_endpoint(self):
        with urllib.request.urlopen(self.base + "/api/session") as r:
            self.assertEqual(json.loads(r.read()), {"panel": True, "lan": True, "authenticated": False})

    def test_login_wrong_and_right(self):
        self.assertEqual(self.post("/api/login", {"password": "falsch"})[0], 401)
        status, data = self.login()
        self.assertEqual(status, 200)
        self.assertTrue(self.cookie.startswith("krempelair_session="))

    def test_control_requires_login_and_header(self):
        self.assertEqual(self.post("/api/control", {"action": "fan", "level": 1})[0], 401)
        self.login()
        self.assertEqual(self.post("/api/control", {"action": "fan", "level": 1}, csrf=False)[0], 403)
        status, _ = self.post("/api/control", {"action": "fan", "level": 1},
                              headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_proxied_requests_refused(self):
        status, _ = self.post("/api/login", {"password": "richtig"}, headers={"X-Forwarded-For": "1.2.3.4"})
        self.assertEqual(status, 403)

    def test_fan_and_setpoint(self):
        self.login()
        status, data = self.post("/api/control", {"action": "fan", "level": 2})
        self.assertEqual(status, 200, data)
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 2)
        status, data = self.post("/api/control", {"action": "setpoint", "value": 22})
        self.assertEqual(self.rig.store.get_setting("temp_setpoint"), 22.0)
        self.assertEqual(self.post("/api/control", {"action": "setpoint", "value": 40})[0], 400)

    def test_manual_one_actuator_then_auto(self):
        self.login()
        self.post("/api/control", {"action": "fan", "level": 1})
        status, data = self.post("/api/control", {"action": "manual", "wrg": 25, "le": None, "hours": 2})
        self.assertEqual(status, 200, data)
        self.rig.run(10)
        ts, state = self.rig.state_file.read()
        self.assertEqual(state["mode"], "hand")
        self.assertEqual(state["wrg"], 256)                       # 25 % von Hand
        self.assertIsNone(state["manual"]["le"])                  # Heizventil automatisch
        self.assertEqual(self.post("/api/control", {"action": "manual", "wrg": 25, "hours": 9})[0], 400)
        self.post("/api/control", {"action": "auto"})
        self.rig.run(10)
        ts, state = self.rig.state_file.read()
        self.assertNotEqual(state["mode"], "hand")

    def test_manual_expires(self):
        self.login()
        self.post("/api/control", {"action": "manual", "wrg": 10, "le": 0, "hours": 1})
        self.rig.run(3700, step=2.0)
        ts, state = self.rig.state_file.read()
        self.assertNotEqual(state["mode"], "hand")

    def test_public_dashboard_rejects_post(self):
        dashboard = Dashboard(self.rig.cfg, self.rig.state_file)
        server = BoundedServer(("127.0.0.1", 0), make_handler(dashboard))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/api/control" % server.server_address[1],
                                         data=b"{}", headers={"X-Krempelair": "1"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 405)
            ctx.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen("http://127.0.0.1:%d/api/session" % server.server_address[1])
            self.assertEqual(ctx.exception.code, 404)
            ctx.exception.close()
        finally:
            server.shutdown()
            server.server_close()
