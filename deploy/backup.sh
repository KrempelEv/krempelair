#!/bin/bash
# Tägliche Sicherung (krempelair-backup.timer): lokal 14 Tage behalten und
# nach maurizi.ch schieben. Dort darf der Pi nur schreiben, nicht lesen
# oder löschen (rrsync -wo).
set -euo pipefail

DIR=/var/backups/krempelair
KEY=/etc/krempelair/ssh/backup

install -d -m 700 "$DIR"
/usr/local/bin/krempelair backup --keep 14

# shellcheck source=/dev/null
. /etc/krempelair/tunnel.env
if [ -f "$KEY" ]; then
    rsync -a --ignore-existing \
        -e "ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=yes -o CheckHostIP=no -o UserKnownHostsFile=/etc/krempelair/ssh/known_hosts" \
        "$DIR/" "krempel-backup@${TUNNEL_HOST}:/"
    echo "nach ${TUNNEL_HOST} übertragen"
else
    echo "kein Backup-Schlüssel, nur lokal gesichert" >&2
fi
