"""Kommandozeile: python3 -m krempelair <befehl>."""

from __future__ import annotations

import argparse
import copy
import csv
import datetime
import getpass
import json
import logging
import os
import sys
import threading
import time

from . import __version__
from .config import load_config

log = logging.getLogger("krempelair")


def _setup_logging(verbose: bool, timestamps: bool = False) -> None:
    fmt = "%(levelname)s %(name)s: %(message)s"
    if timestamps:
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt)


def _plant(cfg):
    from .hardware import I2CBus, Plant

    if cfg.hardware.simulate:
        from .simulation import FakeSMBus, SimulatedPlant

        sim = SimulatedPlant()
        return Plant(I2CBus(cfg.hardware.i2c_bus, lambda n: FakeSMBus(sim)))
    return Plant(I2CBus(cfg.hardware.i2c_bus))


def _open_store_for_control(cfg):
    """Die Regelung startet auch ohne beschreibbare Datenbank: zuerst normal,
    dann nur lesend (z. B. SD-Karte schreibgeschützt), zuletzt im RAM mit
    Standardwerten. Regeln ist wichtiger als Verlauf."""
    from .store import Store

    for kwargs in ({}, {"readonly": True}):
        try:
            return Store(cfg.general.db_path, **kwargs)
        except Exception as exc:
            log.error("Datenbank %s nicht nutzbar (%s): %s", cfg.general.db_path,
                      "nur lesend" if kwargs else "normal", exc)
    log.error("Regle ohne Datenbank mit Standardwerten")
    return Store(":memory:")


def cmd_control(args, cfg) -> int:
    from .controller import ControllerService
    from .store import StateFile

    service = ControllerService(cfg, _plant(cfg), _open_store_for_control(cfg),
                                StateFile(cfg.general.state_path))
    service.run_forever()
    return 0


def cmd_bot(args, cfg) -> int:
    from .bot import Bot
    from .store import StateFile, Store
    from .telegram import TelegramAPI

    if not cfg.telegram.token:
        log.error("KREMPELAIR_TELEGRAM_TOKEN fehlt (/etc/krempelair/secrets.env)")
        return 2
    if not cfg.telegram.password_hash:
        log.warning("KREMPELAIR_BOT_PASSWORD_HASH fehlt: Anmelden ist nicht möglich")
    bot = Bot(cfg, TelegramAPI(cfg.telegram.token), Store(cfg.general.db_path),
              StateFile(cfg.general.state_path))
    bot.run_forever()
    return 0


def cmd_web(args, cfg) -> int:
    from .web import serve

    serve(cfg)
    return 0


def cmd_panel(args, cfg) -> int:
    from .panel import Panel
    from .web import serve

    if not cfg.telegram.password_hash:
        log.error("KREMPELAIR_BOT_PASSWORD_HASH fehlt: am Panel kann sich niemand anmelden")
    serve(cfg, Panel(cfg))
    return 0


def cmd_check_hardware(args, cfg) -> int:
    """Liest alle Karten einmal und gibt die Werte aus. Schreibt nichts.
    Läuft die Regelung, zeigt es deren Werte: nur sie darf auf den Bus."""
    from .hardware import (BETRIEB_BITS, SENSORS, STOERUNG_BITS, HardwareError,
                           bits_to_dict, level_from_outputs)
    from .store import STATE_STALE_SECONDS, StateFile

    ts, state = StateFile(cfg.general.state_path).read()
    if not args.force and ts is not None and time.time() - ts < STATE_STALE_SECONDS:
        print("Regelung läuft, Werte aus deren letztem Zyklus (--force liest selbst, "
              "nur bei gestoppter Regelung):")
        args.from_state = state
    if getattr(args, "from_state", None):
        s = args.from_state
        print("Ausgänge 0x20: 0x%02x (Lüftung Stufe %d)" % (s["outputs_raw"], s["fan_level"]))
        for title, value, names in (("Betrieb 0x21", s["betrieb_raw"], BETRIEB_BITS),
                                    ("Störung 0x22", s["stoerung_raw"], STOERUNG_BITS)):
            active = [n for n, on in bits_to_dict(value, names).items() if on]
            print("%s: 0x%02x %s" % (title, value, ", ".join(active) or "–"))
        for key, sensor in SENSORS.items():
            temp = s["temps"].get(key)
            print("%-12s %5s °C  (roh %4d)%s" % (
                sensor.label, "–" if temp is None else "%.1f" % temp, s["raw"][key],
                "  DEFEKT" if key in s.get("sensor_faults", ()) else ""))
        return 0

    plant = _plant(cfg)
    try:
        r = plant.read()
    except HardwareError as exc:
        print("I2C-Fehler: %s" % exc)
        return 1
    print("Ausgänge 0x20: 0x%02x (Lüftung Stufe %d)" % (r.outputs, level_from_outputs(r.outputs)))
    for title, value, names in (("Betrieb 0x21", r.betrieb, BETRIEB_BITS),
                                ("Störung 0x22", r.stoerung, STOERUNG_BITS)):
        active = [n for n, on in bits_to_dict(value, names).items() if on]
        print("%s: 0x%02x %s" % (title, value, ", ".join(active) or "–"))
    for key, sensor in SENSORS.items():
        print("%-12s %5.1f °C  (roh %4d)" % (sensor.label, r.temps[key], r.raw[key]))
    return 0


def cmd_hash_password(args, cfg) -> int:
    from .bot import hash_password

    password = getpass.getpass("Bot-Passwort: ")
    if not password or password != getpass.getpass("Wiederholen: "):
        print("Passwörter leer oder ungleich", file=sys.stderr)
        return 1
    print("KREMPELAIR_BOT_PASSWORD_HASH=%s" % hash_password(password))
    return 0


def cmd_migrate(args, cfg) -> int:
    from .migrate import migrate
    from .store import Store

    for line in migrate(Store(cfg.general.db_path), args.legacy_db, args.legacy_auth,
                        args.telegrambot, args.secrets_out):
        print(line)
    return 0


def cmd_status(args, cfg) -> int:
    from .store import StateFile

    ts, state = StateFile(cfg.general.state_path).read()
    if ts is None:
        print("kein Zustand vorhanden (%s)" % cfg.general.state_path)
        return 1
    state["age_seconds"] = round(time.time() - ts, 1)
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def cmd_steptest(args, cfg) -> int:
    """Sprungversuch im Handbetrieb, Aufzeichnung als CSV."""
    from .hardware import from_pct
    from .store import STATE_STALE_SECONDS, StateFile, Store
    from .tuning import CSV_FIELDS

    store = Store(cfg.general.db_path)
    state_file = StateFile(cfg.general.state_path)
    ts, state = state_file.read()
    if ts is None or time.time() - ts > STATE_STALE_SECONDS:
        print("Die Regelung läuft nicht (kein aktueller Zustand).", file=sys.stderr)
        return 1
    if not state.get("running") and not args.allow_off:
        print("Die Lüftung läuft nicht. Sprungversuche sind nur bei laufender Anlage "
              "aussagekräftig (--allow-off übergeht das).", file=sys.stderr)
        return 1

    other = from_pct(args.other)
    plan = [(args.low, args.settle)]
    for _ in range(args.cycles):
        plan += [(args.high, args.hold), (args.low, args.hold)]

    def set_manual(level: float, until: float) -> None:
        values = {"wrg": other, "le": other}
        values[args.actuator] = from_pct(level)
        values["until"] = until
        store.set_setting("manual", values, "steptest")

    out = open(args.out, "w", newline="", encoding="utf-8")
    writer = csv.writer(out)
    writer.writerow(CSV_FIELDS)
    store.add_event("Sprungversuch %s gestartet" % args.actuator.upper(), "steptest")
    last_ts = None
    try:
        for level, duration in plan:
            end = time.time() + duration
            print("%s auf %.0f %% für %d s" % (args.actuator.upper(), level, duration))
            next_refresh = 0.0
            while time.time() < end:
                if time.time() >= next_refresh:
                    set_manual(level, time.time() + 120)
                    next_refresh = time.time() + 30
                ts, s = state_file.read()
                if ts and ts != last_ts and s.get("temps"):
                    last_ts = ts
                    t = s["temps"]
                    writer.writerow([round(ts, 1), t["t_zul"], t["t_aul"], t["t_abl"],
                                     s.get("wrg"), s.get("le"), s.get("fan_level"), s.get("mode")])
                    out.flush()
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("abgebrochen")
    finally:
        store.set_setting("manual", None, "steptest")
        store.add_event("Sprungversuch %s beendet" % args.actuator.upper(), "steptest")
        out.close()
    print("Aufzeichnung: %s" % args.out)
    return 0


def cmd_backup(args, cfg) -> int:
    from .backup import create_backup, default_path, rotate

    out = args.out or default_path()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    members = create_backup(cfg, out)
    print("Sicherung %s (%d Dateien)" % (out, len(members)))
    if args.keep:
        rotate(os.path.dirname(os.path.abspath(out)), args.keep)
    return 0


def cmd_identify(args, cfg) -> int:
    from .tuning import identify, read_csv, recommend

    rows = []
    for path in args.csv:
        rows.extend(read_csv(path))
    rows.sort(key=lambda r: r["t"])
    results = identify(rows)
    if not results:
        print("Keine auswertbaren Sprünge gefunden.")
        return 1
    for r in results:
        print("%s-Sprung %+.0f %%: ΔT %+.2f K, K = %.1f K/100 %%, L = %.0f s, T = %.0f s "
              "(AUL %.1f, ABL %.1f)" % (r.actuator.upper(), r.du * 100, r.dy, r.gain,
                                        r.dead_time, r.time_constant, r.t_aul, r.t_abl))
    print()
    print("Vorschlag für /etc/krempelair/config.ini, Abschnitt [control]:")
    for key, value in recommend(results).items():
        if key == "heater_rise":
            print("heater_rise_stage%d = %s" % (args.fan_level, value))
        else:
            print("%s = %s" % (key, value))
    return 0


def cmd_simulate(args, cfg) -> int:
    """Regelung und Dashboard mit simulierter Anlage, für die Entwicklung."""
    from .controller import ControllerService
    from .hardware import I2CBus, Plant, level_from_outputs
    from .simulation import FakeSMBus, SimulatedPlant
    from .store import StateFile, Store
    from .web import serve

    directory = os.path.abspath(args.dir)
    os.makedirs(directory, exist_ok=True)
    cfg.general.db_path = os.path.join(directory, "krempelair.db")
    cfg.general.state_path = os.path.join(directory, "state.json")
    cfg.web.host = args.host
    cfg.web.port = args.port

    fake = {"t": time.time() - args.seed_hours * 3600}
    sim = SimulatedPlant(clock=lambda: fake["t"])
    plant = Plant(I2CBus(1, lambda n: FakeSMBus(sim)))

    if args.seed_hours > 0 and not os.path.exists(cfg.general.db_path):
        print("Erzeuge %g h Verlauf …" % args.seed_hours)
        store = Store(cfg.general.db_path, clock=lambda: fake["t"])
        service = ControllerService(
            cfg, plant, store, StateFile(os.path.join(directory, ".seed.json")),
            clock=lambda: fake["t"], monotonic=lambda: fake["t"], notify=lambda m: None,
            now_local=lambda: datetime.datetime.fromtimestamp(fake["t"]))
        service.start()
        store.set_setting("nak_until", time.time() + 86400 * 2, "simulation")
        store.set_setting("temp_setpoint", 21.0, "simulation")
        while fake["t"] < time.time():
            hour = datetime.datetime.fromtimestamp(fake["t"]).hour
            wanted = 1 if 8 <= hour < 22 else None
            level = level_from_outputs(sim.outputs)
            if wanted and level == 0:
                service.execute("fan_level", 1, "simulation")
            elif hour == 22 and level and not service.nak.state.active:
                service.execute("fan_level", 0, "simulation")
            service.tick()
            fake["t"] += cfg.control.cycle_seconds
        store.close()

    sim.clock = time.time
    sim._last = time.time()

    def run_control():
        service = ControllerService(cfg, plant, Store(cfg.general.db_path),
                                    StateFile(cfg.general.state_path))
        service.start()
        while True:
            try:
                service.tick()
            except Exception:
                log.exception("Fehler im Regeltakt")
            time.sleep(cfg.control.tick_seconds)

    threading.Thread(target=run_control, daemon=True).start()
    if args.panel_port:
        from .panel import Panel

        class LocalPanel(Panel):
            """Simulator: Zugriffe vom eigenen Rechner gelten als LAN."""

            @staticmethod
            def is_lan(address, headers):
                return not headers.get("X-Forwarded-For")

        panel_cfg = copy.deepcopy(cfg)
        panel_cfg.panel.host = args.host
        panel_cfg.panel.port = args.panel_port
        if not panel_cfg.telegram.password_hash:
            from .bot import hash_password
            panel_cfg.telegram.password_hash = hash_password("simulation", iterations=1000)
            print("Panel-Passwort im Simulator: simulation")
        threading.Thread(target=serve, args=(panel_cfg, LocalPanel(panel_cfg)), daemon=True).start()
        print("Panel: http://%s:%d/" % (args.host, args.panel_port))
    print("Dashboard: http://%s:%d/" % (args.host, args.port))
    serve(cfg)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="krempelair", description="Lüftung Krempel")
    parser.add_argument("--config", help="Pfad zur config.ini")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    sub.add_parser("control", help="Regeldienst").set_defaults(func=cmd_control)
    sub.add_parser("bot", help="Telegram-Bot").set_defaults(func=cmd_bot)
    sub.add_parser("web", help="Dashboard").set_defaults(func=cmd_web)
    sub.add_parser("panel", help="Bedien-Panel (nur LAN)").set_defaults(func=cmd_panel)
    p = sub.add_parser("check-hardware", help="alle Karten einmal lesen")
    p.add_argument("--force", action="store_true", help="selbst lesen (nur bei gestoppter Regelung)")
    p.set_defaults(func=cmd_check_hardware)
    sub.add_parser("hash-password", help="Passwort-Hash für den Bot erzeugen").set_defaults(func=cmd_hash_password)
    sub.add_parser("status", help="aktuellen Zustand ausgeben").set_defaults(func=cmd_status)

    p = sub.add_parser("migrate-legacy", help="Daten aus /opt/krempel übernehmen")
    p.set_defaults(func=cmd_migrate)
    p.add_argument("--legacy-db", default="/opt/krempel/share/data.db")
    p.add_argument("--legacy-auth", default="/opt/krempel/share/authIds")
    p.add_argument("--telegrambot", default="/opt/krempel/regulation/telegrambot.py")
    p.add_argument("--secrets-out", default=None)

    p = sub.add_parser("steptest", help="Sprungversuch im Handbetrieb")
    p.set_defaults(func=cmd_steptest)
    p.add_argument("--actuator", choices=("wrg", "le"), required=True)
    p.add_argument("--low", type=float, default=0.0, help="unterer Wert in %%")
    p.add_argument("--high", type=float, default=100.0, help="oberer Wert in %%")
    p.add_argument("--other", type=float, default=0.0, help="anderes Stellglied in %%")
    # das Heizregister kühlt langsam aus (T ≈ 130 s plus Nachlauf, Versuch 2026-10-21)
    p.add_argument("--settle", type=int, default=600, help="Vorlauf in s")
    p.add_argument("--hold", type=int, default=900, help="Haltezeit je Sprung in s")
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--allow-off", action="store_true")
    p.add_argument("--out", required=True)

    p = sub.add_parser("backup", help="Sicherung für den Ersatz der SD-Karte (als root)")
    p.set_defaults(func=cmd_backup)
    p.add_argument("--out", help="Zieldatei (Standard: /var/lib/krempelair/backups/…)")
    p.add_argument("--keep", type=int, default=0, help="nur die neusten N Sicherungen behalten")

    p = sub.add_parser("identify", help="Sprungversuche auswerten")
    p.set_defaults(func=cmd_identify)
    p.add_argument("csv", nargs="+")
    p.add_argument("--fan-level", type=int, default=1)

    p = sub.add_parser("simulate", help="Regelung + Dashboard mit simulierter Anlage")
    p.set_defaults(func=cmd_simulate)
    p.add_argument("--dir", default=".sim")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--seed-hours", type=float, default=48.0)
    p.add_argument("--panel-port", type=int, default=0, help="zusätzlich das Bedien-Panel")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose, timestamps=args.command == "simulate")
    cfg = load_config(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
