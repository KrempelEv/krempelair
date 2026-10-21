"""Sicherung für den 1:1-Ersatz der SD-Karte.

Das Archiv enthält alles, was nicht im Repository steht: Konfiguration,
Bot-Geheimnisse, Fernzugangs-Schlüssel und die Datenbank (Einstellungen,
Verlauf, Ereignisse, freigegebene Chats). Die Pfade im Archiv sind die
Zielpfade auf dem Pi, `bootstrap.sh --restore` packt es nach / aus.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tarfile
import tempfile
import time
from typing import List

from . import __version__
from .config import Config

PREFIX = "krempelair-"
ETC_DIR = "/etc/krempelair"
# Backup-Schlüssel gehört root, Tunnel-Schlüssel dem Benutzer krempelair-tunnel
ETC_FILES = ("config.ini", "secrets.env", "tunnel.env",
             "ssh/backup", "ssh/backup.pub", "ssh/known_hosts")
TUNNEL_SSH_DIR = "/var/lib/krempelair-tunnel/.ssh"
TUNNEL_FILES = ("tunnel", "tunnel.pub", "known_hosts")
BACKUP_DIR = "/var/backups/krempelair"


def default_path() -> str:
    return os.path.join(BACKUP_DIR, time.strftime(PREFIX + "%Y%m%d-%H%M.tar.gz"))


def _sqlite_snapshot(db_path: str, target: str) -> None:
    """Konsistente Kopie im laufenden Betrieb (SQLite Online-Backup)."""
    source = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def create_backup(cfg: Config, out_path: str, etc_dir: str = ETC_DIR,
                  tunnel_dir: str = TUNNEL_SSH_DIR) -> List[str]:
    candidates = ([os.path.join(etc_dir, n) for n in ETC_FILES]
                  + [os.path.join(tunnel_dir, n) for n in TUNNEL_FILES])
    members = []
    tmp_out = out_path + ".part"
    if os.path.lexists(tmp_out):
        os.unlink(tmp_out)  # Rest eines abgebrochenen Laufs (oder ein Symlink): weg
    # neu anlegen, nie einem Symlink folgen, von Anfang an nur für root lesbar
    fd = os.open(tmp_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    # Datenbank-Kopie im RAM, nicht auf der SD-Karte
    scratch = "/dev/shm" if os.path.isdir("/dev/shm") else None
    try:
        with tempfile.TemporaryDirectory(prefix="krempelair-backup-", dir=scratch) as tmp_dir, \
                os.fdopen(fd, "wb") as raw, \
                tarfile.open(fileobj=raw, mode="w:gz", compresslevel=6) as tar:
            for path in candidates:
                if os.path.exists(path):
                    tar.add(path, arcname=path.lstrip("/"))
                    members.append(path)
            if os.path.exists(cfg.general.db_path):
                snapshot = os.path.join(tmp_dir, "krempelair.db")
                _sqlite_snapshot(cfg.general.db_path, snapshot)
                tar.add(snapshot, arcname=cfg.general.db_path.lstrip("/"))
                members.append(cfg.general.db_path)
            info = json.dumps({"version": __version__, "created": time.time(),
                               "files": members}, indent=2).encode()
            entry = tarfile.TarInfo("krempelair-backup.json")
            entry.size = len(info)
            entry.mtime = int(time.time())
            tar.addfile(entry, io.BytesIO(info))
        os.replace(tmp_out, out_path)
    finally:
        if os.path.exists(tmp_out):
            os.unlink(tmp_out)
    return members


def rotate(directory: str, keep: int) -> None:
    files = sorted(f for f in os.listdir(directory)
                   if f.startswith(PREFIX) and f.endswith(".tar.gz"))
    for name in files[:-keep] if keep > 0 else []:
        os.unlink(os.path.join(directory, name))
