#!/bin/bash
# Neue SD-Karte als 1:1-Ersatz einrichten. Anleitung: docs/neue-sd-karte.md
#
# Auf frischem Raspberry Pi OS Lite (Benutzer pi, WLAN und SSH im Imager
# gesetzt), aus dem entpackten Projektordner:
#
#   sudo bash deploy/bootstrap.sh --restore /home/pi/krempelair-JJJJMMTT-HHMM.tar.gz
#
# Ohne --restore entsteht eine leere Anlage (Sollwert 20 °C, keine
# freigegebenen Chats, neue Schlüssel, die auf maurizi.ch einzutragen sind).
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
RESTORE=""
IP_ADDRESS="192.168.1.100/24"
GATEWAY="192.168.1.1"
DNS="8.8.8.8 192.168.1.1"

while [ $# -gt 0 ]; do
    case "$1" in
        --restore) RESTORE="$2"; shift 2 ;;
        --ip) IP_ADDRESS="$2"; shift 2 ;;
        --gateway) GATEWAY="$2"; shift 2 ;;
        *) echo "unbekannte Option $1" >&2; exit 1 ;;
    esac
done

if [ "$(id -u)" != 0 ]; then
    echo "Bitte mit sudo ausführen." >&2
    exit 1
fi
if [ -n "$RESTORE" ] && [ ! -f "$RESTORE" ]; then
    echo "Sicherung $RESTORE nicht gefunden." >&2
    exit 1
fi

echo "1/6 Name, Zeitzone"
hostnamectl set-hostname krempelair
sed -i 's/^127\.0\.1\.1.*/127.0.1.1\tkrempelair/' /etc/hosts
timedatectl set-timezone Europe/Zurich

echo "2/6 I2C einschalten"
if command -v raspi-config >/dev/null; then
    raspi-config nonint do_i2c 0
else
    CONFIG=/boot/firmware/config.txt
    [ -f "$CONFIG" ] || CONFIG=/boot/config.txt
    grep -q '^dtparam=i2c_arm=on' "$CONFIG" || echo 'dtparam=i2c_arm=on' >> "$CONFIG"
    grep -q '^i2c-dev' /etc/modules || echo 'i2c-dev' >> /etc/modules
fi
modprobe i2c-dev || true

echo "3/6 Pakete"
apt-get update
apt-get install -y python3 python3-smbus i2c-tools sqlite3 rsync curl
DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confold upgrade

echo "4/6 Feste IP $IP_ADDRESS (gilt nach dem Neustart)"
if systemctl is-active --quiet NetworkManager; then
    CONN=$(nmcli -t -f NAME,TYPE connection show | awk -F: '$2 ~ /wireless/ {print $1; exit}')
    if [ -n "$CONN" ]; then
        nmcli connection modify "$CONN" ipv4.method manual ipv4.addresses "$IP_ADDRESS" \
            ipv4.gateway "$GATEWAY" ipv4.dns "$DNS"
    else
        echo "    WARNUNG: keine WLAN-Verbindung in NetworkManager gefunden." >&2
    fi
elif [ -f /etc/dhcpcd.conf ]; then
    if ! grep -q '# krempelair' /etc/dhcpcd.conf; then
        cat >> /etc/dhcpcd.conf <<EOF

# krempelair
interface wlan0
static ip_address=$IP_ADDRESS
static routers=$GATEWAY
static domain_name_servers=$DNS
EOF
    fi
fi

echo "5/6 Admin-Schlüssel für pi"
install -d -o pi -g pi -m 700 /home/pi/.ssh
touch /home/pi/.ssh/authorized_keys
while read -r line; do
    grep -qF "$line" /home/pi/.ssh/authorized_keys || echo "$line" >> /home/pi/.ssh/authorized_keys
done < "$SRC/deploy/admin_authorized_keys"
chown pi:pi /home/pi/.ssh/authorized_keys
chmod 600 /home/pi/.ssh/authorized_keys

echo "6/6 Sicherung zurückspielen und installieren"
if [ -n "$RESTORE" ]; then
    # Archiv enthält nur /etc/krempelair, /var/lib/krempelair(-tunnel); ältere
    # Sicherungen mit Schlüsseln unter /var/lib/krempelair/.ssh ordnet install.sh um
    tar xzf "$RESTORE" -C / --exclude=krempelair-backup.json
    echo "    $(tar xzOf "$RESTORE" krempelair-backup.json | grep -m1 created || true)"
fi
bash "$SRC/deploy/install.sh"

echo
echo "Fertig. Jetzt neu starten: sudo reboot"
echo "Danach: http://${IP_ADDRESS%/*}/ und https://krempel.maurizi.ch/"
