"""Sprungversuche auswerten und PID-Parameter nach SIMC vorschlagen.

Ablauf: `steptest` fährt WRG-Klappe oder Heizventil im Handbetrieb in
Sprüngen und zeichnet die Zulufttemperatur sekündlich auf. `identify`
passt daran ein Modell erster Ordnung mit Totzeit an (Zwei-Punkte-Methode
nach Smith: 28,3 % und 63,2 % der Sprungantwort) und rechnet daraus

    Kp = T / (K · (τc + L)),   Ti = min(T, 4 · (τc + L))      (SIMC, Skogestad 2003)

mit τc = L als Vorgabe für ein zügiges, aber gut gedämpftes Verhalten. Weil
der Regler über das Anlagenmodell in Kelvin rechnet, wird K zuerst in die
Modellgrössen (Tauscher-Wirkungsgrad, Heizhub) umgerechnet; die
Kreisverstärkung ist dann ≈ 1.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Dict, List, Optional

CSV_FIELDS = ("t", "t_zul", "t_aul", "t_abl", "wrg", "le", "fan_level", "mode")


@dataclass
class StepResult:
    actuator: str
    t_step: float
    du: float              # Sprunghöhe 0…1
    dy: float              # Änderung Zuluft in K
    gain: float            # K pro 100 %
    dead_time: float       # L in s
    time_constant: float   # T in s
    t_aul: float
    t_abl: float


def _smooth(values: List[float], window: int = 5) -> List[float]:
    out = []
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        out.append(acc / min(i + 1, window))
    return out


def read_csv(path: str) -> List[Dict[str, float]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append({k: (float(v) if k != "mode" and v not in ("", None) else v)
                         for k, v in row.items()})
    return rows


def find_steps(rows: List[Dict[str, float]], actuator: str, min_jump: float = 100) -> List[int]:
    idx = []
    for i in range(1, len(rows)):
        if abs(rows[i][actuator] - rows[i - 1][actuator]) >= min_jump:
            idx.append(i)
    return idx


def analyse_step(rows: List[Dict[str, float]], i: int, end: int, actuator: str,
                 before: float = 30.0, tail: float = 60.0) -> Optional[StepResult]:
    t0 = rows[i]["t"]
    pre = [r for r in rows[:i] if t0 - r["t"] <= before]
    seg = rows[i:end]
    if len(pre) < 5 or len(seg) < 30:
        return None
    y0 = sum(r["t_zul"] for r in pre) / len(pre)
    t_end = seg[-1]["t"]
    post = [r for r in seg if t_end - r["t"] <= tail]
    y1 = sum(r["t_zul"] for r in post) / len(post)
    dy = y1 - y0
    du = (rows[i][actuator] - rows[i - 1][actuator]) / 1023.0
    if abs(dy) < 0.3 or du == 0:
        return None
    ys = _smooth([r["t_zul"] for r in seg])
    times = [r["t"] - t0 for r in seg]

    def crossing(fraction: float) -> Optional[float]:
        level = y0 + fraction * dy
        for t, y in zip(times, ys):
            if (dy > 0 and y >= level) or (dy < 0 and y <= level):
                return t
        return None

    t28 = crossing(0.283)
    t63 = crossing(0.632)
    if t28 is None or t63 is None or t63 <= t28:
        return None
    tc = 1.5 * (t63 - t28)
    dead = max(0.0, t63 - tc)
    t_aul = sum(r["t_aul"] for r in seg) / len(seg)
    t_abl = sum(r["t_abl"] for r in seg) / len(seg)
    return StepResult(actuator, t0, du, dy, dy / du, dead, tc, t_aul, t_abl)


def identify(rows: List[Dict[str, float]]) -> List[StepResult]:
    steps = {a: find_steps(rows, a) for a in ("wrg", "le")}
    # ein Sprung gilt bis zum nächsten Sprung irgendeines Stellglieds, sonst
    # mischt sich die Antwort des anderen in die Auswertung
    every = sorted(set(steps["wrg"]) | set(steps["le"])) + [len(rows)]
    results = []
    for actuator in ("wrg", "le"):
        for i in steps[actuator]:
            end = next(j for j in every if j > i)
            result = analyse_step(rows, i, end, actuator)
            if result:
                results.append(result)
    return results


def simc(time_constant: float, dead_time: float, gain: float = 1.0,
         tau_c: Optional[float] = None) -> Dict[str, float]:
    dead = max(dead_time, 2.0)
    tau_c = dead if tau_c is None else tau_c
    kp = time_constant / (gain * (tau_c + dead))
    ti = min(time_constant, 4 * (tau_c + dead))
    return {"kp": round(kp, 2), "ti": round(ti, 0)}


def recommend(results: List[StepResult]) -> Dict[str, float]:
    """Modellgrössen und PID-Parameter aus allen gültigen Sprüngen."""
    out: Dict[str, float] = {}
    eff = [r.gain / (r.t_abl - r.t_aul) for r in results
           if r.actuator == "wrg" and abs(r.t_abl - r.t_aul) >= 3]
    rise = [r.gain for r in results if r.actuator == "le"]
    if eff:
        out["hx_efficiency"] = round(sum(eff) / len(eff), 2)
    if rise:
        out["heater_rise"] = round(sum(rise) / len(rise), 1)
    if results:
        # langsamster Sprung bestimmt die Einstellung (robust für beide Stufen)
        tuned = [simc(r.time_constant, r.dead_time) for r in results]
        out["kp"] = min(t["kp"] for t in tuned)
        out["ti"] = max(t["ti"] for t in tuned)
    return out
