#!/bin/bash
# Zurück zum Altsystem (/opt/krempel). Die neue Installation bleibt liegen
# und kann mit install.sh wieder eingeschaltet werden.
#
#   sudo bash deploy/rollback.sh
set -euo pipefail

ETC=/etc/krempelair

if [ "$(id -u)" != 0 ]; then
    echo "Bitte mit sudo ausführen." >&2
    exit 1
fi

systemctl disable --now krempelair-bot krempelair-web krempelair-control >/dev/null 2>&1 || true

for svc in regulation.sh telegram.sh uwsgi; do
    if [ -x "/etc/init.d/$svc" ]; then
        update-rc.d "$svc" enable >/dev/null 2>&1 || true
    fi
done
systemctl enable nginx >/dev/null 2>&1 || true
systemctl daemon-reload

if [ -f "$ETC/legacy-crontab-pi.bak" ]; then
    crontab -u pi "$ETC/legacy-crontab-pi.bak"
fi

# aktuellen Sollwert ins Altsystem übernehmen, sonst regelt es auf den Stand der Übernahme
if [ -f /var/lib/krempelair/krempelair.db ] && [ -f /opt/krempel/share/data.db ]; then
    setpoint=$(sqlite3 /var/lib/krempelair/krempelair.db \
        "SELECT value FROM settings WHERE key='temp_setpoint'" 2>/dev/null || true)
    if echo "$setpoint" | grep -Eq '^[0-9]+(\.[0-9]+)?$'; then
        sqlite3 /opt/krempel/share/data.db \
            "UPDATE sollwerte SET value=$setpoint WHERE key='tempSoll'"
        echo "Sollwert $setpoint °C ins Altsystem übernommen."
    fi
fi

/etc/init.d/regulation.sh start
/etc/init.d/telegram.sh start || echo "WARNUNG: alter Telegram-Bot startet nicht." >&2
systemctl start nginx || true
sleep 3
if ! pgrep -f /opt/krempel/regulation/regulation.py >/dev/null; then
    echo "FEHLER: alte Regelung läuft nicht. Anlage ohne Regelung!" >&2
    exit 1
fi
echo "Altsystem wieder aktiv (Regelung, Telegram-Bot, Cron-Neustarts)."
