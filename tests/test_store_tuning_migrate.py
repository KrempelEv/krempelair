import math
import os
import pickle
import shutil
import sqlite3
import tempfile
import unittest

from krempelair.bot import verify_password
from krempelair.migrate import migrate, read_auth_ids
from krempelair.store import StateFile, Store
from krempelair.tuning import identify, recommend, simc


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "k.db")
        self.now = 1_790_000_000.0
        self.store = Store(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir)

    def test_reopen_is_idempotent(self):
        self.store.set_setting("temp_setpoint", 22.5, "x")
        Store(self.path).close()
        self.assertEqual(self.store.get_setting("temp_setpoint"), 22.5)

    def test_set_default_does_not_overwrite(self):
        self.store.set_setting("nak_target", 21.0)
        self.store.set_default("nak_target", 20.0)
        self.assertEqual(self.store.get_setting("nak_target"), 21.0)

    def test_history_buckets(self):
        for i in range(600):
            self.store.add_sample(int(self.now) - 600 * 60 + i * 60,
                                  {"t_zul": 20.0 + (i % 2), "fan_level": i % 3})
        h = self.store.history(self.now - 600 * 60, self.now, max_points=100)
        self.assertLessEqual(len(h["t"]), 101)
        self.assertGreaterEqual(h["bucket"], 360)
        self.assertTrue(all(abs(v - 20.5) < 0.1 for v in h["t_zul"][1:-1]))
        self.assertEqual(max(h["fan_level"]), 2)

    def test_readonly_cannot_write(self):
        ro = Store(self.path, readonly=True)
        with self.assertRaises(sqlite3.OperationalError):
            ro.set_setting("x", 1)
        ro.close()

    def test_prune(self):
        self.store.add_sample(int(self.now) - 500 * 86400, {"t_zul": 1})
        self.store.add_sample(int(self.now), {"t_zul": 2})
        self.store.prune(400)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)

    def test_state_file_atomic(self):
        sf = StateFile(os.path.join(self.dir, "run", "state.json"))
        self.assertEqual(sf.read(), (None, {}))
        sf.write({"ts": 5, "ok": True})
        self.assertEqual(sf.read()[0], 5)
        self.assertEqual([n for n in os.listdir(os.path.dirname(sf.path)) if n.startswith(".")], [])


def fopdt_rows(k=20.0, tau=60.0, dead=15.0, du=0.5, actuator="le", t_aul=5.0, t_abl=21.0):
    rows = []
    y0 = 18.0
    for t in range(0, 900):
        step_on = t >= 300
        u = du * 1023 if step_on else 0
        dt = t - 300 - dead
        y = y0 + (k * du * (1 - math.exp(-dt / tau)) if dt > 0 else 0.0)
        rows.append({"t": float(t), "t_zul": round(y, 2), "t_aul": t_aul, "t_abl": t_abl,
                     "wrg": u if actuator == "wrg" else 0, "le": u if actuator == "le" else 0,
                     "fan_level": 1, "mode": "hand"})
    return rows


class TuningTest(unittest.TestCase):
    def test_identifies_fopdt(self):
        results = identify(fopdt_rows())
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertAlmostEqual(r.gain, 20.0, delta=1.0)
        self.assertAlmostEqual(r.time_constant, 60.0, delta=8.0)
        self.assertAlmostEqual(r.dead_time, 15.0, delta=5.0)

    def test_step_ends_at_next_step_of_other_actuator(self):
        rows = fopdt_rows()
        for r in rows[600:]:
            r["wrg"] = 1023
            r["t_zul"] += 5.0  # Antwort auf den WRG-Sprung
        le = [r for r in identify(rows) if r.actuator == "le"]
        self.assertEqual(len(le), 1)
        self.assertAlmostEqual(le[0].gain, 20.0, delta=1.0)

    def test_recommendation(self):
        rows = fopdt_rows(k=9.6, actuator="wrg")  # η = 9.6 / (21 − 5) = 0.6
        rec = recommend(identify(rows))
        self.assertAlmostEqual(rec["hx_efficiency"], 0.6, delta=0.05)
        self.assertIn("kp", rec)

    def test_simc(self):
        self.assertEqual(simc(60, 15), {"kp": 2.0, "ti": 60.0})


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_full_migration(self):
        legacy_db = os.path.join(self.dir, "data.db")
        conn = sqlite3.connect(legacy_db)
        conn.execute("CREATE TABLE sollwerte(key text, value real)")
        conn.execute("INSERT INTO sollwerte VALUES ('tempSoll', 23.0)")
        conn.commit()
        conn.close()
        auth = os.path.join(self.dir, "authIds")
        with open(auth, "wb") as fh:
            pickle.dump([111, 222, 111], fh)
        bot = os.path.join(self.dir, "telegrambot.py")
        with open(bot, "w") as fh:
            fh.write('    if pw == "geheim":\n    updater = Updater("123:ABC")\n')
        secrets = os.path.join(self.dir, "secrets.env")
        store = Store(os.path.join(self.dir, "k.db"))
        migrate(store, legacy_db, auth, bot, secrets)
        self.assertEqual(store.get_setting("temp_setpoint"), 23.0)
        self.assertTrue(store.is_chat_allowed(111) and store.is_chat_allowed(222))
        with open(secrets) as fh:
            env = dict(line.split("=", 1) for line in fh.read().splitlines()
                       if "=" in line and not line.startswith("#"))
        self.assertEqual(env["KREMPELAIR_TELEGRAM_TOKEN"], "123:ABC")
        self.assertTrue(verify_password("geheim", env["KREMPELAIR_BOT_PASSWORD_HASH"]))
        self.assertEqual(os.stat(secrets).st_mode & 0o777, 0o640)
        store.close()

    def test_rejects_malicious_pickle(self):
        path = os.path.join(self.dir, "evil")

        class Evil:
            def __reduce__(self):
                return (os.system, ("echo pwned",))

        with open(path, "wb") as fh:
            pickle.dump([Evil()], fh)
        with self.assertRaises(pickle.UnpicklingError):
            read_auth_ids(path)



class BackupTest(unittest.TestCase):
    def test_backup_contains_config_secrets_keys_and_db(self):
        import tarfile
        from krempelair.backup import create_backup, rotate
        from krempelair.config import Config
        d = tempfile.mkdtemp()
        try:
            etc = os.path.join(d, "etc")
            data = os.path.join(d, "data")
            tunnel = os.path.join(d, "tunnel", ".ssh")
            os.makedirs(os.path.join(etc, "ssh"))
            os.makedirs(data)
            os.makedirs(tunnel)
            for path in [os.path.join(etc, n) for n in ("config.ini", "secrets.env", "ssh/backup")] \
                    + [os.path.join(tunnel, "tunnel")]:
                with open(path, "w") as fh:
                    fh.write("x")
            cfg = Config()
            cfg.general.db_path = os.path.join(data, "k.db")
            store = Store(cfg.general.db_path)
            store.set_setting("temp_setpoint", 22.0)
            out = os.path.join(d, "krempelair-1.tar.gz")
            create_backup(cfg, out, etc_dir=etc, tunnel_dir=tunnel)
            store.close()
            with tarfile.open(out) as tar:
                names = tar.getnames()
                tar.extract(cfg.general.db_path.lstrip("/"), os.path.join(d, "x"))
            self.assertIn("krempelair-backup.json", names)
            self.assertIn(os.path.join(etc, "secrets.env").lstrip("/"), names)
            self.assertIn(os.path.join(tunnel, "tunnel").lstrip("/"), names)
            self.assertIn(os.path.join(etc, "ssh", "backup").lstrip("/"), names)
            restored = Store(os.path.join(d, "x", cfg.general.db_path.lstrip("/")))
            self.assertEqual(restored.get_setting("temp_setpoint"), 22.0)
            restored.close()
            self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)
            for i in range(2, 6):
                open(os.path.join(d, "krempelair-%d.tar.gz" % i), "w").close()
            rotate(d, 3)
            self.assertEqual(sorted(f for f in os.listdir(d) if f.endswith(".tar.gz")),
                             ["krempelair-3.tar.gz", "krempelair-4.tar.gz", "krempelair-5.tar.gz"])
        finally:
            shutil.rmtree(d)
