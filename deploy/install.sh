#!/bin/bash
# Installiert oder aktualisiert Krempelair auf dem Pi. Mehrfach ausführbar.
#
#   sudo bash deploy/install.sh
#
# Beim ersten Lauf werden Sollwert, freigegebene Telegram-Chats, Bot-Token
# und Bot-Passwort aus dem Altsystem (/opt/krempel) übernommen und das
# Altsystem abgeschaltet (nicht gelöscht). Zurück: sudo bash deploy/rollback.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/krempelair
ETC=/etc/krempelair
DATA=/var/lib/krempelair
LEGACY=/opt/krempel
TUNNEL_HOME=/var/lib/krempelair-tunnel
BACKUPS=/var/backups/krempelair

if [ "$(id -u)" != 0 ]; then
    echo "Bitte mit sudo ausführen." >&2
    exit 1
fi

# Bricht das Skript ab, nachdem das Altsystem gestoppt wurde, aber bevor die
# neue Regelung läuft, schaltet rollback.sh vollständig zurück (neue Dienste
# aus, Altsystem, Cron und nginx wieder an): nie zwei Regelungen gleichzeitig,
# nie gar keine.
LEGACY_STOPPED=0
on_error() {
    if [ "$LEGACY_STOPPED" = 1 ] && ! systemctl is-active --quiet krempelair-control; then
        echo "FEHLER: neue Regelung läuft nicht, schalte auf das Altsystem zurück." >&2
        bash "$SRC/deploy/rollback.sh" || true
    fi
}
trap on_error ERR

echo "0/7 Voraussetzungen"
for cmd in sqlite3 curl rsync i2cdetect; do
    command -v "$cmd" >/dev/null || NEED_APT=1
done
if [ -n "${NEED_APT:-}" ] || ! python3 -c "import smbus" 2>/dev/null; then
    apt-get install -y python3-smbus i2c-tools sqlite3 curl rsync
fi
python3 -c "import smbus, sqlite3, http.server, json"

echo "1/7 Benutzer"
# krempelair: Regelung und Bot; krempelair-web: Dashboard, nur lesend (Gruppe
# krempelair); krempelair-tunnel: nur der SSH-Rückkanal
if ! id krempelair >/dev/null 2>&1; then
    useradd --system --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin krempelair
fi
usermod -a -G i2c krempelair
if ! id krempelair-web >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin \
        --gid krempelair krempelair-web
fi
if ! id krempelair-tunnel >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$TUNNEL_HOME" --no-create-home \
        --shell /usr/sbin/nologin krempelair-tunnel
fi

echo "2/7 Programm nach $APP"
install -d -m 755 "$APP"
rm -rf "$APP/krempelair.new"
cp -r "$SRC/krempelair" "$APP/krempelair.new"
find "$APP/krempelair.new" -name __pycache__ -prune -exec rm -rf {} +
if [ -d "$APP/krempelair" ]; then
    rm -rf "$APP/krempelair.prev"
    mv "$APP/krempelair" "$APP/krempelair.prev"
fi
mv "$APP/krempelair.new" "$APP/krempelair"
chown -R root:root "$APP"
chmod -R u=rwX,go=rX "$APP"
install -m 755 "$SRC/deploy/krempelair-cli" /usr/local/bin/krempelair

echo "3/7 Konfiguration und Daten"
install -d -m 755 "$ETC"
if [ ! -f "$ETC/config.ini" ]; then
    install -m 644 "$SRC/deploy/config.ini" "$ETC/config.ini"
fi
install -d -o krempelair -g krempelair -m 750 "$DATA"

if [ ! -f "$DATA/krempelair.db" ] && [ -d "$LEGACY" ]; then
    echo "    Übernahme aus $LEGACY"
    /usr/local/bin/krempelair migrate-legacy --secrets-out "$ETC/secrets.env"
fi
chown -R krempelair:krempelair "$DATA"
find "$DATA" -maxdepth 1 -type f -name 'krempelair.db*' -exec chmod 640 {} +
# Sicherungen enthalten secrets.env: nur root (früher unter $DATA/backups)
install -d -o root -g root -m 700 "$BACKUPS"
if [ -d "$DATA/backups" ]; then
    find "$DATA/backups" -maxdepth 1 -name 'krempelair-*.tar.gz' -exec mv {} "$BACKUPS/" \;
    rm -rf "$DATA/backups"
fi
chown -R root:root "$BACKUPS"
if [ -f "$ETC/secrets.env" ]; then
    chown root:root "$ETC/secrets.env"
    chmod 600 "$ETC/secrets.env"
else
    echo "    WARNUNG: $ETC/secrets.env fehlt, der Bot startet nicht." >&2
fi

echo "4/7 Altsystem abschalten (Dateien bleiben)"
# zuerst Cron, damit kein Neustart-Job das Altsystem neben der neuen Regelung wiederbelebt
if crontab -u pi -l >/dev/null 2>&1; then
    if [ ! -f "$ETC/legacy-crontab-pi.bak" ]; then
        crontab -u pi -l > "$ETC/legacy-crontab-pi.bak"
    fi
    crontab -u pi -l \
        | sed -E '/^[^#].*(reboot\.sh|restartRegulation\.sh|restartTelegram\.sh)/s/^/#krempelair-alt# /' \
        | crontab -u pi -
fi
if [ -d "$LEGACY" ]; then
    LEGACY_STOPPED=1
fi
for svc in regulation.sh telegram.sh; do
    if [ -x "/etc/init.d/$svc" ]; then
        "/etc/init.d/$svc" stop >/dev/null 2>&1 || true
        update-rc.d "$svc" disable >/dev/null 2>&1 || true
    fi
done
pkill -f '/opt/krempel/regulation/(regulation|telegrambot)\.py' || true
# nginx und uWSGI gehörten zur stillgelegten Web-Version; Port 80 übernimmt das Dashboard
systemctl disable --now nginx >/dev/null 2>&1 || true
if [ -x /etc/init.d/uwsgi ]; then
    /etc/init.d/uwsgi stop >/dev/null 2>&1 || true
    update-rc.d uwsgi disable >/dev/null 2>&1 || true
fi

echo "5/7 systemd: Dienste, Fernzugang, Hardware-Watchdog, Journal"
install -m 644 "$SRC"/deploy/systemd/krempelair-*.service "$SRC"/deploy/systemd/krempelair-*.timer /etc/systemd/system/
# Fernzugang über maurizi.ch: Schlüssel bleibt bei Updates erhalten; ein neuer
# Schlüssel (neue SD-Karte ohne Sicherung) muss auf maurizi.ch eingetragen werden
if [ ! -f "$ETC/tunnel.env" ]; then
    install -m 644 "$SRC/deploy/tunnel/tunnel.env" "$ETC/tunnel.env"
fi
# Tunnel-Schlüssel gehört krempelair-tunnel, Backup-Schlüssel root. Ältere
# Installationen hatten beide unter $DATA/.ssh: dorthin nie wieder.
install -d -o krempelair-tunnel -g krempelair-tunnel -m 700 "$TUNNEL_HOME" "$TUNNEL_HOME/.ssh"
install -d -o root -g root -m 700 "$ETC/ssh"
for f in tunnel tunnel.pub; do
    if [ -f "$DATA/.ssh/$f" ] && [ ! -f "$TUNNEL_HOME/.ssh/$f" ]; then mv "$DATA/.ssh/$f" "$TUNNEL_HOME/.ssh/"; fi
done
for f in backup backup.pub; do
    if [ -f "$DATA/.ssh/$f" ] && [ ! -f "$ETC/ssh/$f" ]; then mv "$DATA/.ssh/$f" "$ETC/ssh/"; fi
done
rm -rf "$DATA/.ssh"
install -o krempelair-tunnel -g krempelair-tunnel -m 644 "$SRC/deploy/tunnel/known_hosts" "$TUNNEL_HOME/.ssh/known_hosts"
install -o root -g root -m 644 "$SRC/deploy/tunnel/known_hosts" "$ETC/ssh/known_hosts"
if [ ! -f "$TUNNEL_HOME/.ssh/tunnel" ]; then
    runuser -u krempelair-tunnel -- ssh-keygen -q -t ed25519 -N '' -C krempelair-pi-tunnel -f "$TUNNEL_HOME/.ssh/tunnel"
    echo "    NEUER Tunnel-Schlüssel, auf maurizi.ch eintragen (docs/fernzugang.md):"
    sed 's/^/    /' "$TUNNEL_HOME/.ssh/tunnel.pub"
fi
chown -R krempelair-tunnel:krempelair-tunnel "$TUNNEL_HOME/.ssh"
chmod 600 "$TUNNEL_HOME/.ssh/tunnel"
if [ ! -f "$ETC/ssh/backup" ]; then
    ssh-keygen -q -t ed25519 -N '' -C krempelair-pi-backup -f "$ETC/ssh/backup"
    echo "    NEUER Backup-Schlüssel, auf maurizi.ch eintragen (docs/fernzugang.md):"
    sed 's/^/    /' "$ETC/ssh/backup.pub"
fi
chown root:root "$ETC/ssh/backup" "$ETC/ssh/backup.pub"
chmod 600 "$ETC/ssh/backup"
install -d -m 755 "$APP/deploy"
cp -r "$SRC/deploy/." "$APP/deploy/"
install -d -m 755 /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
install -m 644 "$SRC/deploy/watchdog.conf" /etc/systemd/system.conf.d/krempelair-watchdog.conf
install -m 644 "$SRC/deploy/journald.conf" /etc/systemd/journald.conf.d/krempelair.conf
if [ ! -d /var/log/journal ]; then
    install -d -m 2755 -g systemd-journal /var/log/journal
    systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
    systemctl restart systemd-journald
fi
systemctl daemon-reload

echo "6/7 Dienste starten"
systemctl enable krempelair-control krempelair-web >/dev/null
systemctl restart krempelair-control    # Type=notify: wartet auf READY
systemctl restart krempelair-web
if [ -f "$ETC/secrets.env" ]; then
    systemctl enable krempelair-bot krempelair-panel >/dev/null
    systemctl restart krempelair-bot krempelair-panel
fi
systemctl enable krempelair-tunnel >/dev/null
# läuft der Tunnel schon, trägt er womöglich diese Sitzung: Neustart erst am Schluss
systemctl is-active --quiet krempelair-tunnel || systemctl start krempelair-tunnel
systemctl enable --now krempelair-backup.timer >/dev/null

echo "7/7 Prüfung"
for _ in $(seq 20); do
    curl -fsS -o /dev/null http://127.0.0.1/healthz && break
    sleep 1
done
# falls doch noch ein Prozess des Altsystems lief: jetzt endgültig beenden
pkill -f '/opt/krempel/regulation/(regulation|telegrambot)\.py' || true
status=0
for unit in krempelair-control krempelair-web krempelair-bot krempelair-panel krempelair-tunnel; do
    state=$(systemctl is-active "$unit" || true)
    printf '    %-20s %s\n' "$unit" "$state"
    if [ "$state" != active ] && { [ "$unit" = krempelair-control ] || [ "$unit" = krempelair-web ]; }; then
        status=1
    fi
done
if curl -fsS -o /dev/null http://127.0.0.1/healthz; then
    echo "    Dashboard        http://$(hostname -I | awk '{print $1}')/"
else
    echo "    Dashboard meldet keinen frischen Zustand (healthz)" >&2
    status=1
fi
if ! systemctl is-active --quiet krempelair-control; then
    false  # löst on_error aus: Altsystem zurück
fi
trap - ERR
# Tunnel 5 s nach dem Ende neu starten (neue Unit, neuer Schlüssel), damit eine
# Sitzung über den Rückkanal Prüfung und Rückgabewert noch mitbekommt
if ! systemd-run --quiet --on-active=5 /bin/systemctl restart krempelair-tunnel; then
    systemctl restart krempelair-tunnel
fi
echo "    Tunnel startet in 5 s neu"
exit $status
