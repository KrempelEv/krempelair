# Regelung

## Anlage

```
Aussenluft ──► [WRG-Kreuztauscher] ──► [Heizregister] ──► Zuluftventilator ──► Raum
   AUL             ▲   Bypassklappe        Ventil LE          Stufe 1/2           ZUL
                   │
Fortluft  ◄── Abluftventilator ◄──────────────────────────────────────────── Raum
   FOL                                                                          ABL
```

| Stellglied | Ausgang | 0 | 1023 |
|---|---|---|---|
| WRG-Klappe | 0x58 Kanal 0 | ganz Bypass, keine Rückgewinnung | ganz über den Tauscher |
| Heizventil (LE) | 0x58 Kanal 1 | zu | offen |
| Ventilatoren | 0x20 Bit 0/1 | aus | Stufe 1 / Stufe 2 |

## Betriebsfälle

Die Regelung entscheidet alle 2 s in dieser Reihenfolge:

| Fall | Bedingung | WRG | Heizventil |
|---|---|---|---|
| Frostschutz | Frostmeldung (0x22 Bit 2) | 1023 | 900 |
| Handbetrieb | Sprungversuch läuft (höchstens 60 min) | vorgegeben | vorgegeben |
| Nachtauskühlung | NAK aktiviert und Bedingungen erfüllt | 0 (Bypass) | 0 |
| Anlage aus | keine Betriebsmeldung Zuluft Stufe 1/2 | 1023 | 0, bei Kälte 900 |
| Sicherheitsstellung | 5 Zyklen (10 s) keine Messwerte | 1023 | 900 |
| Normalbetrieb | sonst | PID, siehe unten | PID |

Die Werte für «Frost» und die Sicherheitsstellung sind die des Altsystems
und stehen in `/etc/krempelair/config.ini`.

Bei stehender Anlage ist das Heizventil zu, damit keine Heizwärme ins
stehende Gerät geht. Ist es draussen kalt (Witterungsfühler unter
`off_cold_on` = 3 °C), öffnet es auf 900 wie im Altsystem, damit im
Register kein Wasser einfriert; zu geht es erst wieder über
`off_cold_off` = 5 °C. Fällt der Witterungsfühler aus, bleibt es offen
(der Aussenluftfühler im stehenden Gerät zeigt ein paar Kelvin zu warm). Seit dem
21.10.2026; vorher stand das Ventil bei «aus» immer auf 900.

Im Normalbetrieb ergibt sich die Betriebsart aus dem Ergebnis der Regelung:

| Anzeige | Bedeutung |
|---|---|
| Heizen | Heizventil offen, WRG voll |
| Wärmerückgewinnung | WRG teilweise oder voll, Abluft wärmer als Aussenluft |
| Kälterückgewinnung (KRG) | Aussenluft wärmer als Abluft: der Tauscher hält die Kälte der Innenluft im Gebäude |
| Freie Kühlung | Klappe auf Bypass, kühle Aussenluft kommt direkt herein |

KRG braucht keinen Schalter: Sie stellt sich ein, sobald es draussen wärmer
ist als drinnen und die Zuluft gekühlt werden soll. Ein Luftkühler ist nicht
vorhanden, gekühlt wird nur über den Tauscher.

## Zulufttemperatur: Vorsteuerung, PID, Aufteilung

Der Regler rechnet in Kelvin Temperaturhub gegenüber der Aussenluft:

```
Hub_soll = (Soll_ZUL − AUL)          Vorsteuerung: was die Anlage ungefähr leisten muss
         + PID(Soll_ZUL, ZUL)        Korrektur für alles, was das Modell nicht weiss
```

Die Aufteilung verteilt den Hub auf die Stellglieder, zuerst die kostenlose
WRG, dann das Heizregister:

```
WRG-Hub bei voller Klappe = η · (ABL − AUL)            η = hx_efficiency (gemessen 0,5)
Heiz-Hub bei offenem Ventil = heater_rise_stage1/2      (11 K / 8,6 K, siehe unten)
```

Weil der WRG-Hub mit dem Temperaturunterschied wächst, rechnet die
Aufteilung die Klappenstellung jedes Mal neu um. Der PID sieht dadurch eine
Strecke mit Verstärkung ≈ 1, egal ob es draussen −5 °C oder +25 °C hat. Das
alte Verfahren hatte eine feste Verstärkung für sehr unterschiedliche
Stellglieder und schwang je nach Wetter oder war träge.

Bausteine des PID (Åström/Hägglund):

| Baustein | Zweck |
|---|---|
| Sollwertgewichtung b | weniger Überschwingen bei Sollwertsprüngen (b = 1: aus) |
| D-Anteil auf den Messwert, gefiltert (Td/N) | kein Ausschlag bei Sollwertänderung; standardmässig aus (Td = 0), weil der Fühler nur 0,05 K auflöst |
| bedingte Integration (Anti-Windup) | liegt ein Stellglied am Anschlag, wächst der I-Anteil nicht weiter |
| Messwertfilter (6 s) | glättet das Rauschen des 10-Bit-Wandlers |
| Stellrate (5 %/s) | schont Klappen- und Ventilantrieb |
| Neustart bei der Vorsteuerung | nach «aus», Frost oder NAK beginnt der Regler ohne alten I-Anteil, also ohne Sprung |

Rückfall: `algorithm = legacy` in der Konfiguration rechnet wieder genau wie
das Altsystem (10-s-Takt, gleiche Formel, gleiche Werte; per Test gegen den
alten Code geprüft).

## Parameter bestimmen (Sprungversuch)

Die Werte `kp`, `ti`, `hx_efficiency` und `heater_rise_*` stammen aus dem
Sprungversuch vom 21.10.2026 (AUL ≈ 10 °C, ABL ≈ 21 °C):

| Sprung | Stufe | Hub | Totzeit L | Zeitkonstante T |
|---|---|---|---|---|
| Heizventil 0 → 100 % (WRG offen) | 1 | +12,6 K | 27 s | 63 s |
| Heizventil 100 → 0 % | 1 | −12,6 K | 42 s | 126 s |
| Heizventil 0 → 100 % | 2 | +9,7 K | 23 s | 51 s |
| Heizventil 100 → 0 % | 2 | −10,0 K | 30 s | 102 s |
| WRG 0 → 100 % (Ventil zu) | 1 | ≈ +3,3 K | ≈ 40 s | ≈ 170 s |

- Das Heizregister kühlt deutlich langsamer aus, als es aufheizt, und die
  Zuluft kriecht danach noch minutenlang nach (Wärme im Register). Vorlauf
  und Haltezeit darum mindestens 10 bzw. 15 Minuten (Standard von `steptest`).
- Das Ventil ist nicht linear: 66 % Hub geben etwa 53 % der Heizleistung.
  In der Konfiguration steht darum der Hub, der im Arbeitsbereich (60–70 %
  Ventil) passt: 11 K auf Stufe 1, 8,6 K auf Stufe 2 (gleiches Verhältnis
  wie 12,6 / 9,9 K bei vollem Hub). Mit 12,6 K startete die Regelung bei
  62 % statt der nötigen 68 % und sackte 0,8 K ab.
- WRG voll offen hebt die Zuluft um ≈ 0,5 · (ABL − AUL), Ventilatorwärme
  eingeschlossen; der Anteil der Klappe allein ist ≈ 0,3, der Rest kommt
  auch im Bypass (Leckage, Ventilator).
- `kp = 0.7`, `ti = 120` statt der SIMC-Werte (1,1 / 100–126 s): in der
  Simulation mit diesem Modell schwingt 1,1 wegen der Ventilkennlinie
  stärker über; 0,7/120 regelt beim Anfahren in gut einer Minute aus.

Wiederholen (Lüftung muss laufen, Stufe vorher per Telegram oder Panel):

```bash
sudo -u krempelair krempelair steptest --actuator le --other 100 --out /tmp/sprung-le.csv
sudo -u krempelair krempelair steptest --actuator wrg --other 0 --out /tmp/sprung-wrg.csv
krempelair identify /tmp/sprung-le.csv --fan-level 1
```

`steptest` setzt den Handbetrieb nur für jeweils 2 Minuten und verlängert
ihn laufend; bricht das Programm ab, ist die Regelung nach spätestens zwei
Minuten wieder automatisch. Frost hat immer Vorrang.

`identify` passt an jeden Sprung ein Modell erster Ordnung mit Totzeit an
(Zwei-Punkte-Methode nach Smith, 28,3 % und 63,2 %) und schlägt Parameter
nach SIMC (Skogestad 2003) vor:

```
Kp = T / (K · (τc + L)),   Ti = min(T, 4 · (τc + L)),   τc = L
```

Die Vorschläge in `/etc/krempelair/config.ini` unter `[control]` eintragen
und `sudo systemctl restart krempelair-control`.

## Nachtauskühlung (NAK)

Per Telegram (`/nak`) für 48 Stunden aktivierbar. In dieser Zeit schaltet die
Regelung die Lüftung selbst ein, wenn

- Raum vorne über dem NAK-Ziel + 1 K liegt,
- die Witterungstemperatur mindestens 3 K tiefer liegt und mindestens 10 °C hat,
- keine Frostmeldung anliegt (Sammelalarm und Strömungswächter kommen an
  dieser Anlage immer wieder und blockieren nichts; nur Frost hält die
  Anlage auf) und
- seit dem letzten Lauf 15 Minuten vergangen sind.

Während NAK: Lüftung Stufe 2, WRG-Klappe auf Bypass, Heizventil zu. Nach
frühestens 15 Minuten stoppt NAK, wenn das Ziel erreicht oder es draussen
nicht mehr kühler genug ist, sofort bei Ablauf der 48 h oder bei Frost.
Hat NAK die Lüftung eingeschaltet, stellt sie sie danach auf die vorherige
Stufe zurück. Wer während NAK von Hand schaltet, übernimmt: «Aus» beendet
NAK, Stufe 1/2 bleibt nach NAK bestehen. Das gilt auch über einen Neustart
der Regelung hinweg.

Alle Schwellen stehen unter `[nak]` in der Konfiguration; ein Zeitfenster
(z. B. 22:00–07:00) lässt sich dort zusätzlich setzen.
