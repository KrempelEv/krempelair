"""Konfiguration aus INI-Datei und Umgebungsvariablen.

Jeder Abschnitt der INI-Datei entspricht einer Dataclass. Fehlende Werte
nehmen den Standard aus der Dataclass; der Typ des Standards bestimmt die
Umwandlung. Geheimnisse (Bot-Token, Passwort-Hash) kommen nur aus der
Umgebung, damit sie nicht in der Konfigurationsdatei stehen.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field, fields

from .hardware import SENSORS

DEFAULT_CONFIG_PATH = "/etc/krempelair/config.ini"


@dataclass
class GeneralConfig:
    db_path: str = "/var/lib/krempelair/krempelair.db"
    state_path: str = "/run/krempelair/state.json"
    history_seconds: int = 60
    retention_days: int = 400


@dataclass
class HardwareConfig:
    i2c_bus: int = 1
    simulate: bool = False


@dataclass
class ControlConfig:
    # "pid" (Standard) oder "legacy" (Rechenweg des Altsystems, 10-s-Takt)
    algorithm: str = "pid"
    cycle_seconds: float = 2.0
    tick_seconds: float = 0.5
    # PID auf die Zulufttemperatur; Ausgang ist eine Korrektur in Kelvin
    # zur Vorsteuerung (Soll − Aussenluft). Siehe docs/regelung.md.
    kp: float = 1.0
    ti: float = 90.0
    td: float = 0.0
    derivative_filter: float = 10.0
    setpoint_weight: float = 1.0
    trim_limit: float = 12.0
    measurement_filter: float = 6.0
    # Anlagenmodell für die Aufteilung auf WRG und Heizregister
    hx_efficiency: float = 0.6
    heater_rise_stage1: float = 25.0
    heater_rise_stage2: float = 18.0
    max_rate_per_s: float = 5.0
    # Rechenweg des Altsystems
    legacy_start_signal: float = 1800.0
    legacy_kp: float = 1.0
    legacy_ki: float = 0.1
    legacy_cycle_seconds: float = 10.0
    # Stellwerte 0…1023 je Zustand
    off_wrg: int = 1023
    # Heizventil bei stehender Anlage: zu; ist es draussen kalt (Witterung
    # unter off_cold_on °C bis über off_cold_off °C, Fühlerausfall) offen
    # gegen Frost im stehenden Register. off_le = 900 verhält sich wie das Altsystem.
    off_le: int = 0
    off_le_cold: int = 900
    off_cold_on: float = 3.0
    off_cold_off: float = 5.0
    frost_wrg: int = 1023
    frost_le: int = 900
    safe_wrg: int = 1023
    safe_le: int = 900
    # so viele Zyklen in Folge ohne Messwerte, dann Sicherheitsstellung
    sensor_fail_cycles: int = 5
    default_setpoint: float = 20.0
    # Handbetrieb (Panel, Sprungversuche) längstens so lange
    manual_max_minutes: float = 480.0
    # Lüftung nach so vielen Stunden ununterbrochenem Lauf ausschalten (0 = nie)
    max_run_hours: float = 36.0


@dataclass
class NakConfig:
    """Nachtauskühlung, per Telegram jeweils für duration_hours aktiviert."""

    duration_hours: float = 48.0
    # optionales Zeitfenster "HH:MM"; leer = nur nach Temperaturen
    window_start: str = ""
    window_end: str = ""
    inside_sensor: str = "t_raum"
    outside_sensor: str = "t_witt"
    start_margin: float = 1.0
    start_delta: float = 3.0
    stop_delta: float = 1.0
    min_outside: float = 10.0
    fan_level: int = 2
    min_run_minutes: float = 15.0
    min_pause_minutes: float = 15.0
    default_target: float = 20.0


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 80


@dataclass
class PanelConfig:
    """Bedien-Panel, nur im LAN (nie über den Rückkanal weiterleiten)."""

    host: str = "0.0.0.0"
    port: int = 8080
    session_hours: float = 8.0


@dataclass
class TelegramConfig:
    poll_timeout: int = 50
    command_timeout: float = 20.0
    max_login_failures: int = 5
    lockout_minutes: float = 15.0
    # aus der Umgebung
    token: str = ""
    password_hash: str = ""


@dataclass
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    nak: NakConfig = field(default_factory=NakConfig)
    web: WebConfig = field(default_factory=WebConfig)
    panel: PanelConfig = field(default_factory=PanelConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    # Fehler beim Einlesen; die Dienste melden sie, laufen aber mit Standardwerten weiter
    errors: list = field(default_factory=list)


def _convert(raw: str, default):
    if isinstance(default, bool):
        value = raw.strip().lower()
        if value in ("1", "true", "yes", "ja", "on"):
            return True
        if value in ("0", "false", "no", "nein", "off"):
            return False
        raise ValueError("kein Wahrheitswert: %r" % raw)
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw.strip()


def _hhmm_or_empty(value: str) -> bool:
    if value == "":
        return True
    parts = value.split(":")
    return (len(parts) == 2 and all(p.isdigit() for p in parts)
            and 0 <= int(parts[0]) < 24 and 0 <= int(parts[1]) < 60)


SENSOR_KEYS = tuple(SENSORS)

# Prüfungen über den Typ hinaus; ein ungültiger Wert behält den Standard
VALIDATORS = {
    ("control", "algorithm"): lambda v: v in ("pid", "legacy"),
    ("control", "cycle_seconds"): lambda v: 0.5 <= v <= 60,
    ("control", "tick_seconds"): lambda v: 0.1 <= v <= 5,
    ("control", "ti"): lambda v: v >= 0,
    ("control", "kp"): lambda v: v >= 0,
    ("control", "hx_efficiency"): lambda v: 0 < v <= 1,
    ("nak", "window_start"): _hhmm_or_empty,
    ("nak", "window_end"): _hhmm_or_empty,
    ("nak", "fan_level"): lambda v: v in (1, 2),
    ("nak", "inside_sensor"): lambda v: v in SENSOR_KEYS,
    ("nak", "outside_sensor"): lambda v: v in SENSOR_KEYS,
    ("nak", "duration_hours"): lambda v: 0 < v <= 24 * 14,
    ("web", "port"): lambda v: 0 < v < 65536,
    ("panel", "port"): lambda v: 0 < v < 65536,
    ("panel", "session_hours"): lambda v: 0 < v <= 24,
    ("control", "max_run_hours"): lambda v: v >= 0,
    ("control", "off_le"): lambda v: 0 <= v <= 1023,
    ("control", "off_le_cold"): lambda v: 0 <= v <= 1023,
    ("control", "off_cold_on"): lambda v: -20 <= v <= 20,
    ("control", "off_cold_off"): lambda v: -20 <= v <= 25,
    ("general", "history_seconds"): lambda v: 10 <= v <= 3600,
    ("general", "retention_days"): lambda v: v >= 7,
    ("control", "sensor_fail_cycles"): lambda v: v >= 1,
    ("control", "manual_max_minutes"): lambda v: 0 < v <= 24 * 60,
    ("control", "measurement_filter"): lambda v: v >= 0,
    ("control", "max_rate_per_s"): lambda v: v >= 0,
    ("nak", "min_run_minutes"): lambda v: v >= 0,
    ("nak", "min_pause_minutes"): lambda v: v >= 0,
    ("telegram", "command_timeout"): lambda v: 3 <= v <= 120,
    ("telegram", "poll_timeout"): lambda v: 1 <= v <= 90,
}


def _fill(section_obj, parser: configparser.ConfigParser, section: str,
          errors: list) -> None:
    if not parser.has_section(section):
        return
    known = {f.name for f in fields(section_obj)}
    for key, raw in parser.items(section):
        if key not in known:
            errors.append("unbekannter Schlüssel [%s] %s, ignoriert" % (section, key))
            continue
        default = getattr(section_obj, key)
        try:
            value = _convert(raw, default)
            check = VALIDATORS.get((section, key))
            if check and not check(value):
                raise ValueError("Wert %r nicht erlaubt" % raw)
        except ValueError as exc:
            errors.append("[%s] %s: %s, Standard %r bleibt" % (section, key, exc, default))
            continue
        setattr(section_obj, key, value)


def load_config(path: str = None) -> Config:
    """Lädt die Konfiguration. Eine fehlende Datei ergibt die Standardwerte.
    Ungültige Einträge landen in cfg.errors statt den Start zu verhindern:
    eine Regelung mit Standardwerten ist besser als gar keine."""
    path = path or os.environ.get("KREMPELAIR_CONFIG", DEFAULT_CONFIG_PATH)
    cfg = Config()
    parser = configparser.ConfigParser(interpolation=None)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            parser.read_file(fh)
    for section in parser.sections():
        if not hasattr(cfg, section):
            cfg.errors.append("unbekannter Abschnitt [%s], ignoriert" % section)
    for f in fields(cfg):
        if f.name != "errors":
            _fill(getattr(cfg, f.name), parser, f.name, cfg.errors)

    cfg.telegram.token = os.environ.get("KREMPELAIR_TELEGRAM_TOKEN", cfg.telegram.token)
    cfg.telegram.password_hash = os.environ.get(
        "KREMPELAIR_BOT_PASSWORD_HASH", cfg.telegram.password_hash
    )
    if os.environ.get("KREMPELAIR_DB"):
        cfg.general.db_path = os.environ["KREMPELAIR_DB"]
    return cfg
