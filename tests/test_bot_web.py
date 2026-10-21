import datetime
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from krempelair.bot import Bot, _parse_callback, hash_password, render_status
from krempelair.config import Config
from krempelair.hardware import level_from_outputs
from krempelair.web import Dashboard, make_handler

from helpers import Rig


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.deleted = []
        self.answered = []

    def send_message(self, chat_id, text, keyboard=None):
        self.sent.append((chat_id, text, keyboard))

    def edit_message(self, chat_id, message_id, text, keyboard=None):
        self.edited.append((chat_id, message_id, text))

    def answer_callback(self, callback_id, text=None):
        self.answered.append((callback_id, text))

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class BotTest(unittest.TestCase):
    def setUp(self):
        cfg = Config()
        cfg.telegram.password_hash = hash_password("richtig", iterations=1000)
        self.rig = Rig(cfg, start=datetime.datetime(2026, 7, 1, 23, 0).timestamp())
        self.rig.service.start()
        self.rig.run(3)
        self.api = FakeAPI()

        def sleep(seconds):
            # Während der Bot wartet, arbeitet die Regelung weiter
            self.rig.run(seconds, step=0.25)

        self.bot = Bot(cfg, self.api, self.rig.store, self.rig.state_file,
                       clock=self.rig.time.time, sleep=sleep)

    def tearDown(self):
        self.rig.close()

    def msg(self, text, chat_id=42):
        return {"message": {"message_id": 7, "date": self.rig.time.t, "text": text,
                            "chat": {"id": chat_id, "type": "private", "first_name": "Aang"},
                            "from": {"id": chat_id, "first_name": "Aang"}}}

    def callback(self, data, chat_id=42):
        return {"callback_query": {"id": "cb1", "data": data, "from": {"username": "aang"},
                                   "message": {"message_id": 9, "chat": {"id": chat_id}}}}

    def last_text(self):
        return self.api.sent[-1][1]

    def test_requires_login(self):
        self.bot.handle(self.msg("/status"))
        self.assertIn("anmelden", self.last_text())

    def test_login_deletes_password_and_allows_chat(self):
        self.bot.handle(self.msg("/login richtig"))
        self.assertEqual(self.api.deleted, [(42, 7)])
        self.assertTrue(self.rig.store.is_chat_allowed(42))
        self.bot.handle(self.msg("/status"))
        self.assertIn("Temperaturen", self.last_text())

    def test_login_lockout(self):
        for _ in range(5):
            self.bot.handle(self.msg("/login falsch"))
        self.bot.handle(self.msg("/login richtig"))
        self.assertIn("Zu viele", self.last_text())
        self.assertFalse(self.rig.store.is_chat_allowed(42))

    def test_callback_unauthorized(self):
        self.bot.handle(self.callback("fan:1"))
        self.assertEqual(self.api.answered[-1][1], "Bitte zuerst im privaten Chat anmelden")

    def test_fan_callback_waits_for_controller(self):
        self.rig.store.allow_chat(42)
        self.bot.handle(self.callback("fan:2"))
        self.assertIn("✅", self.api.edited[-1][2])
        self.assertEqual(level_from_outputs(self.rig.sim.outputs), 2)

    def test_setpoint_and_nak(self):
        self.rig.store.allow_chat(42)
        self.bot.handle(self.callback("sp:22"))
        self.assertEqual(self.rig.store.get_setting("temp_setpoint"), 22.0)
        self.bot.handle(self.callback("nak:1"))
        until = self.rig.store.get_setting("nak_until")
        self.assertAlmostEqual(until - self.rig.time.t, 48 * 3600, delta=10)
        self.assertIn("Aktiviert bis", self.api.edited[-1][2])
        self.bot.handle(self.callback("nak:0"))
        self.assertEqual(self.rig.store.get_setting("nak_until"), 0)

    def test_login_refused_in_groups(self):
        update = self.msg("/login richtig", chat_id=-100)
        update["message"]["chat"]["type"] = "group"
        self.bot.handle(update)
        self.assertFalse(self.rig.store.is_chat_allowed(-100))
        self.assertIn("privaten Chat", self.last_text())

    def test_global_login_limit(self):
        for chat in range(1, 110):  # globale Schwelle: 20 × 5 Fehlversuche
            self.bot.handle(self.msg("/login falsch", chat_id=chat))
        self.bot.handle(self.msg("/login richtig", chat_id=999))
        self.assertFalse(self.rig.store.is_chat_allowed(999))

    def test_old_messages_ignored(self):
        update = self.msg("/login richtig")
        update["message"]["date"] -= 3600
        self.bot.handle(update)
        self.assertEqual(self.api.sent, [])

    def test_legacy_callbacks(self):
        self.assertEqual(_parse_callback("1"), ("fan", 1))
        self.assertEqual(_parse_callback("11"), ("smk", 1))
        self.assertEqual(_parse_callback("10"), ("smk", 0))
        self.assertEqual(_parse_callback("23"), ("sp", 23))
        self.assertEqual(_parse_callback("nakt:20"), ("nakt", 20))

    def test_render_status_stale(self):
        ts, state = self.rig.state_file.read()
        text = render_status(ts, state, ts + 600)
        self.assertIn("Keine aktuellen Daten", text)
        self.assertIn("Zuluft", text)


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rig = Rig()
        cls.rig.service.start()
        cls.rig.run(120, step=1.0)
        dashboard = Dashboard(cls.rig.cfg, cls.rig.state_file)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(dashboard))
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.rig.close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers, r.read()

    def test_state(self):
        status, headers, body = self.get("/api/state")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("t_zul", data["state"]["temps"])
        self.assertIn("stale", data)  # Rig läuft mit Kunstzeit, darum hier immer «alt»
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_history_and_events(self):
        status, _, body = self.get("/api/history?hours=1")
        self.assertIn("t_zul", json.loads(body))
        status, _, body = self.get("/api/events?limit=5")
        self.assertTrue(json.loads(body))

    def test_index_served(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])

    def test_traversal_blocked(self):
        for path in ("/static/../web.py", "/static/%2e%2e/web.py", "/static/../../etc/passwd"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code, 404)
            ctx.exception.close()

    def test_long_range_is_clamped(self):
        status, _, body = self.get("/api/history?start=0&keys=t_zul")
        self.assertEqual(status, 200)

    def test_bad_query(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/history?hours=abc")
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()


    def test_keys_and_first(self):
        status, _, body = self.get("/api/history?start=first&points=100&keys=t_zul,wrg,pump")
        data = json.loads(body)
        self.assertEqual(sorted(k for k in data if k not in ("t", "bucket", "start", "end", "first")),
                         ["pump", "t_zul", "wrg"])
        self.assertIsNotNone(data["first"])

    def test_unknown_key_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/history?keys=t_zul,drop")
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_vendor_gzip(self):
        req = urllib.request.Request(self.base + "/static/vendor/highstock.js",
                                     headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.headers["Content-Encoding"], "gzip")
            self.assertLess(int(r.headers["Content-Length"]), 200000)
