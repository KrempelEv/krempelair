# Betrieb und Wartung

## Dienste

| Dienst | Aufgabe | Neustart bei Absturz |
|---|---|---|
| `krempelair-control` | Regelung, einziger Zugriff auf I2C | nach 5 s; hängt der Takt 30 s, beendet systemd ihn und startet neu |
| `krempelair-bot` | Telegram-Bot | nach 10 s |
| `krempelair-web` | Dashboard auf Port 80, eigener Benutzer `krempelair-web`: liest die Datenbank, kann nichts schreiben | nach 5 s |
| `krempelair-panel` | Bedien-Panel auf Port 8080, nur LAN, Anmeldung mit dem Bot-Passwort | nach 5 s |
| `krempelair-tunnel` | Rückkanal zu maurizi.ch (SSH und Dashboard von aussen, docs/fernzugang.md) | nach 15 s |
| `krempelair-backup.timer` | Sicherung täglich 03:30, lokal und nach maurizi.ch | – |

Zusätzlich startet der Hardware-Watchdog den ganzen Pi neu, wenn das System
länger als 14 s nicht reagiert. Der tägliche Neustart und die Neustarts alle
vier Stunden aus dem Altsystem sind abgeschaltet; sie waren nur nötig, weil
die alte Regelung jede Stunde abstürzte.

```bash
systemctl status krempelair-control krempelair-bot krempelair-web
journalctl -u krempelair-control -n 100        # Meldungen der Regelung
journalctl -u krempelair-bot --since today
sudo systemctl restart krempelair-control
```

## Kommandozeile auf dem Pi

```bash
krempelair status                 # aktueller Zustand als JSON
krempelair check-hardware         # alle Karten einmal lesen (schreibt nichts)
krempelair hash-password          # neuen Passwort-Hash für den Bot erzeugen
sudo krempelair backup --out /tmp/k.tar.gz   # Sicherung von Hand
sudo -u krempelair krempelair steptest …   # siehe docs/regelung.md
```

## Dateien

| Pfad | Inhalt |
|---|---|
| `/opt/krempelair/krempelair/` | Programm (`…prev` = Stand vor dem letzten Update) |
| `/etc/krempelair/config.ini` | Konfiguration |
| `/etc/krempelair/secrets.env` | Bot-Token und Passwort-Hash (nur root) |
| `/etc/krempelair/legacy-crontab-pi.bak` | Crontab des Altsystems |
| `/var/lib/krempelair/krempelair.db` | Einstellungen, Verlauf, Ereignisse, freigegebene Chats |
| `/var/backups/krempelair/` | tägliche Sicherungen (14 Stück, nur root) |
| `/etc/krempelair/ssh/` | Backup-Schlüssel (nur root) |
| `/var/lib/krempelair-tunnel/.ssh/` | Tunnel-Schlüssel (Benutzer `krempelair-tunnel`) |
| `/etc/krempelair/tunnel.env` | Ziel des Rückkanals |
| `/opt/krempelair/deploy/` | Skripte (u. a. `rollback.sh`, `backup.sh`) |
| `/run/krempelair/state.json` | aktueller Zustand (im RAM) |

## Telegram

| Befehl | Wirkung |
|---|---|
| `/login Passwort` | Chat freischalten (Passwort-Nachricht wird gelöscht; 5 Fehlversuche sperren 15 min) |
| `/status` | Temperaturen, Stellwerte, Meldungen |
| `/lueftung` | Stufe 1, Stufe 2, Aus, Raucherraum ein/aus |
| `/temperatur` | Solltemperatur Zuluft 17–26 °C |
| `/nak` | Nachtauskühlung 48 h aktivieren / beenden, Ziel 18–23 °C |
| `/logout` | Chat abmelden |

Schaltbefehle führt die Regelung aus; der Bot meldet erst «✅», wenn die
Ausgänge nachweislich umgeschaltet haben. Befehle, die während eines
Bot-Ausfalls liegen geblieben sind, werden verworfen und nicht nachträglich
ausgeführt. Tasten aus alten Nachrichten des Altsystems funktionieren weiter.

Bot-Passwort ändern:

```bash
krempelair hash-password              # Ausgabe in /etc/krempelair/secrets.env eintragen
sudo systemctl restart krempelair-bot
```

## Bedien-Panel (nur im LAN)

`http://192.168.1.100:8080/` – dasselbe Dashboard, dazu oben rechts
«Anmelden» (Passwort wie im Telegram-Bot) und die Karte **Bedienung**:

| Bereich | Wirkung |
|---|---|
| Lüftung | Aus, Stufe 1, Stufe 2 |
| Raucherraum | Ein, Aus |
| Solltemperatur Zuluft | 17–26 °C |
| Nachtauskühlung | 48 h aktivieren, beenden, Ziel innen 18–23 °C |
| Handbetrieb | WRG-Klappe und/oder Heizventil fest auf 0–100 %, für 1–8 h; danach automatisch zurück. «Automatik» beendet sofort. Frost hat immer Vorrang. Im Schema steht dann «HAND». |

Das Panel nimmt nur Anfragen aus privaten Netzen an und ist über
krempel.maurizi.ch nicht erreichbar (der Rückkanal leitet nur Port 80
weiter). Sitzung 8 h, Cookie `HttpOnly`/`SameSite=Strict`, 5 Fehlversuche
sperren 15 min. Jede Bedienung steht mit IP-Adresse im Ereignisprotokoll.

## Laufzeitbegrenzung

Läuft die Lüftung 36 h ohne Unterbruch, schaltet die Regelung sie aus
(«nach 36 h automatisch ausgeschaltet» im Protokoll). Die Zeit zählt auch
über Neustarts der Regelung hinweg; Bot, Dashboard und Panel zeigen «schaltet
spätestens … aus». Einstellbar mit `max_run_hours` unter `[control]`
(0 = aus).

## Dashboard

`http://192.168.1.100/` im LAN und https://krempel.maurizi.ch/ von überall –
ohne Anmeldung, nur lesend. Telegram-Namen erscheinen dort nicht.

- Anlagenschema mit Live-Werten in den Kennfarben nach DIN EN 16798-3.
  Jeder Fühler, jede Klappe, jeder Antrieb ist anklickbar und öffnet den
  Verlauf dazu; Direktlink z. B. `http://192.168.1.100/#t_zul`.
- Verläufe mit Highcharts Stock: Zeitraum wählen, mit dem Navigator
  verschieben, zoomen. In jedem Chart lassen sich bis zu 6 weitere Kurven
  dazuladen; andere Einheiten erscheinen als eigenes Feld darunter.
- Verlauf: ein Wert pro Minute, 400 Tage.

Highcharts ist für nicht-kommerzielle Nutzung (privat, Schule, gemeinnützige
Organisation) kostenlos; für kommerzielle Nutzung braucht es eine Lizenz
(highcharts.com/license).

## Update und Rückfall

```bash
deploy/deploy.sh pi@192.168.1.100     # vom Entwicklungsrechner: kopieren und installieren
sudo bash deploy/rollback.sh          # auf dem Pi: zurück zum Altsystem
```

`install.sh` ist mehrfach ausführbar; Konfiguration, Geheimnisse und
Datenbank bleiben erhalten.

## Entwicklung ohne Hardware

```bash
python3 -m krempelair simulate        # Regelung + Dashboard auf http://127.0.0.1:8080
PYTHONPATH=.:tests python3 -m unittest discover -s tests
```

Der Simulator erzeugt 48 h Verlauf und läuft dann in Echtzeit weiter. Der
Code läuft auf Python 3.7 (Pi) bis 3.14 und braucht ausser `smbus` nur die
Standardbibliothek.
