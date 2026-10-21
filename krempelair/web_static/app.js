"use strict";

/* Dashboard Lüftung Krempel – nur lesend.
   Schema und Werte aus /api/state, Verläufe aus /api/history mit Highcharts Stock. */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const MAX_EXTRA = 6;

/* Alle Kurven, die /api/history kennt. Farbe folgt der Grösse, nie der Reihenfolge. */
const SIGNALS = {
  t_zul: { label: "Zuluft", unit: "°C", color: "--c-zul" },
  t_soll: { label: "Soll Zuluft", unit: "°C", color: "--c-zul", dash: true },
  t_abl: { label: "Abluft", unit: "°C", color: "--c-abl" },
  t_aul: { label: "Aussenluft", unit: "°C", color: "--c-aul" },
  t_fol: { label: "Fortluft", unit: "°C", color: "--c-fol" },
  t_raum: { label: "Raum vorne", unit: "°C", color: "--c-raum" },
  t_witt: { label: "Witterung", unit: "°C", color: "--c-witt", dash: "Dot" },
  nak_target: { label: "Ziel Nachtauskühlung", unit: "°C", color: "--c-raum", dash: true },
  wrg: { label: "WRG-Klappe", unit: "%", color: "--c-wrg" },
  le: { label: "Heizventil", unit: "%", color: "--c-le" },
  demand: { label: "Regler: gewünschter Hub", unit: "K", color: "--ink-2" },
  fan_level: { label: "Lüftung Sollstufe", unit: "Stufe", color: "--ink-2", dash: true },
  fan_zul: { label: "Zuluftventilator", unit: "Stufe", color: "--c-zul" },
  fan_fol: { label: "Abluftventilator", unit: "Stufe", color: "--c-fol" },
  pump: { label: "Pumpe Heizregister", unit: "bool", color: "--c-le" },
  smoker: { label: "Raucherraum", unit: "bool", color: "--c-raum" },
  running: { label: "Anlage in Betrieb", unit: "bool", color: "--c-zul" },
  nak_active: { label: "Nachtauskühlung aktiv", unit: "bool", color: "--c-aul" },
  frost: { label: "Frost", unit: "bool", color: "--critical" },
  alarm: { label: "Sammelalarm", unit: "bool", color: "--critical" },
  stroem_zul: { label: "Strömungswächter Zuluft", unit: "bool", color: "--critical" },
  stroem_fol: { label: "Strömungswächter Fortluft", unit: "bool", color: "--critical" },
};
const LEVEL = { 0: "aus", 1: "Stufe 1", 2: "Stufe 2" };
/* Alles, was von der Einheit abhängt: Feldhöhe, Achse, Tooltip, Gruppe im Auswahlmenü */
const UNITS = {
  "°C": { group: "Temperaturen", weight: 3, suffix: " °C", decimals: 1 },
  "%": { group: "Stellwerte", weight: 2, min: 0, max: 100, suffix: " %", decimals: 0 },
  K: { group: "Regler", weight: 1.6, suffix: " K", decimals: 1 },
  Stufe: { group: "Ventilatoren", weight: 1.1, min: 0, max: 2, ticks: [0, 1, 2], step: true,
    axis: (v) => (v === 0 ? "aus" : String(v)), text: (v) => LEVEL[Math.round(v)] },
  bool: { group: "Zustände und Meldungen", weight: 0.55, min: 0, max: 1, ticks: [0, 1], step: true,
    single: true, text: (v) => (v ? "ein" : "aus") },
};

/* Hilfen ------------------------------------------------------------------ */

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}
function pad(n) { return String(n).padStart(2, "0"); }
function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return pad(d.getHours()) + ":" + pad(d.getMinutes());
}
function fmtDateTime(ts) {
  const d = new Date(ts * 1000);
  return WD[d.getDay()] + " " + pad(d.getDate()) + "." + pad(d.getMonth() + 1) + ". " + fmtTime(ts);
}
function fmtNum(v, digits) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return Number(v).toFixed(digits).replace(".", ",").replace("-", "−");
}
function fmtTemp(v) { return v === null || v === undefined ? "–" : fmtNum(v, 1) + " °C"; }
function fmtPct(v) { return v === null || v === undefined ? "–" : fmtNum(v, 0) + " %"; }
function fmtAge(s) {
  if (s < 90) return Math.round(s) + " s";
  if (s < 5400) return Math.round(s / 60) + " min";
  return fmtNum(s / 3600, 1) + " h";
}
function setText(selector, text) { $$(selector).forEach((n) => { n.textContent = text; }); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

/* Anlagenschema ------------------------------------------------------------ */

function fanLevel(b, prefix) {
  return b[prefix + "_St2"] ? 2 : b[prefix + "_St1"] ? 1 : 0;
}
function isStale(data) {
  return data.stale !== undefined ? data.stale : data.age === null || data.age > 15 || !(data.state || {}).ok;
}
function nakText(nak) {
  if (!nak || !nak.enabled) return "aus";
  if (nak.active) return "läuft";
  return "bereit bis " + fmtDateTime(nak.until);
}

function updateScheme(data) {
  const s = data.state || {};
  const svg = $("#scheme");
  const stale = isStale(data);
  svg.classList.toggle("stale", stale);

  const t = s.temps || {};
  for (const key of ["t_aul", "t_fol", "t_abl", "t_zul", "t_raum", "t_witt"]) {
    setText('[data-t="' + key + '"]', fmtTemp(t[key]));
  }
  setText('[data-v="soll"]', "Soll " + fmtTemp(s.setpoint));
  const wrg = s.wrg_pct;
  setText('[data-v="wrg"]', fmtPct(wrg));
  setText('[data-v="bypass"]', wrg === undefined || wrg === null ? "" : "Bypass " + fmtPct(100 - wrg));
  setText('[data-v="le"]', fmtPct(s.le_pct));
  const manual = s.manual || {};
  $("#hand-wrg").classList.toggle("on", manual.wrg !== undefined && manual.wrg !== null);
  $("#hand-le").classList.toggle("on", manual.le !== undefined && manual.le !== null);

  const frac = wrg === undefined || wrg === null ? 1 : wrg / 100;
  // Bypassklappe: quer (zu) bei voller WRG, längs (offen) bei 0 %
  $("#damper .blade").setAttribute("transform", "rotate(" + ((1 - frac) * 90).toFixed(1) + ")");
  svg.style.setProperty("--through", frac.toFixed(3));
  svg.style.setProperty("--bypass", (1 - frac).toFixed(3));

  const heat = (s.le_pct || 0) / 100;
  const coil = $("#coil");
  coil.classList.toggle("heating", heat > 0.005);
  coil.style.setProperty("--heat", heat.toFixed(2));

  const b = s.betrieb || {};
  const st = s.stoerung || {};
  $("#pump").classList.toggle("on", !!b.LE_PU);
  $("#frost").classList.toggle("alarm", !!st.Frost);
  $("#fan-zul .flowsw").classList.toggle("alarm", !!st.Stroem_ZUL);
  $("#fan-fol .flowsw").classList.toggle("alarm", !!st.Stroem_FOL);

  const zul = fanLevel(b, "ZUL");
  const fol = fanLevel(b, "FOL");
  setText('[data-v="fan-zul"]', "Zuluft " + LEVEL[zul]);
  setText('[data-v="fan-fol"]', "Abluft " + LEVEL[fol]);
  for (const [id, level] of [["#fan-zul", zul], ["#fan-fol", fol]]) {
    $(id).classList.toggle("on", level > 0);
    $(id).classList.toggle("stage2", level === 2);
  }
  svg.classList.toggle("running", !!s.running && !stale);
  svg.classList.toggle("stage2", zul === 2);

  $("#smoker").classList.toggle("on", !!s.smoker);
  setText('[data-v="smoker"]', s.smoker ? "Raucher ein" : "Raucher aus");
  setText('[data-v="mode"]', s.mode_label || "–");
  setText('[data-v="level"]', s.fan_level === undefined ? "–" : LEVEL[s.fan_level]);
  setText('[data-v="nak"]', nakText(s.nak));
  setText('[data-v="faults"]', (s.faults || []).length ? s.faults.join(", ") : "keine");
  setText('[data-v="remaining"]', remainingText(s, data.server_time));
}

const ICON_ALERT = '<svg viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M8 1.5 15 14H1z"/><path fill="var(--surface)" d="M7.3 6h1.4l-.2 4.2H7.5zM7.3 11.2h1.4v1.4H7.3z"/></svg>';

function updateChips(data) {
  const s = data.state || {};
  const chips = [];
  if (s.mode_label) chips.push('<span class="chip on">' + escapeHtml(s.mode_label) + "</span>");
  if (s.nak && s.nak.enabled) chips.push('<span class="chip on">NAK ' + (s.nak.active ? "läuft" : "bereit") + "</span>");
  if (s.manual && s.mode !== "hand") chips.push('<span class="chip on">Handbetrieb</span>');
  if (s.manual) chips.push('<span class="chip on">bis ' + fmtTime(s.manual.until) + "</span>");
  for (const f of s.faults || []) chips.push('<span class="chip alarm">' + ICON_ALERT + escapeHtml(f) + "</span>");
  if (s.hardware_error) chips.push('<span class="chip alarm">' + ICON_ALERT + "I2C-Fehler</span>");
  $("#chips").innerHTML = chips.join("");
}

function updateConn(data, failed) {
  const c = $("#conn");
  c.classList.remove("ok", "stale", "down");
  let text;
  if (failed) {
    c.classList.add("down");
    text = "Dashboard nicht erreichbar";
  } else if (data.age === null) {
    c.classList.add("down");
    text = "Regelung liefert keine Daten";
  } else if (isStale(data)) {
    c.classList.add("stale");
    text = (data.state || {}).hardware_error ? "I2C-Fehler" : "Daten " + fmtAge(data.age) + " alt";
  } else {
    c.classList.add("ok");
    text = "Live · " + fmtTime(data.server_time);
  }
  $(".txt", c).textContent = text;
}

function updateValues(data) {
  const s = data.state || {};
  const t = s.temps || {};
  const meta = data.meta || {};
  const rows = [];
  const group = (title) => rows.push('<tr class="group"><th colspan="2">' + title + "</th></tr>");
  const row = (label, value, color, signals) => rows.push(
    "<tr" + (signals ? ' class="hit" tabindex="0" role="button" data-signals="' + signals + '" data-title="' + escapeHtml(label) + '"' : "") +
    "><th>" + (color ? '<span class="sw" style="background:var(' + color + ')"></span>' : "") +
    escapeHtml(label) + "</th><td>" + escapeHtml(value) + "</td></tr>");

  group("Temperaturen");
  for (const key of ["t_zul", "t_abl", "t_aul", "t_fol", "t_raum", "t_witt"]) {
    row(SIGNALS[key].label, fmtTemp(t[key]), SIGNALS[key].color, key);
  }
  row("Soll Zuluft", fmtTemp(s.setpoint), null, "t_soll,t_zul");
  group("Stellwerte");
  row("WRG-Klappe", fmtPct(s.wrg_pct), "--c-wrg", "wrg");
  row("Heizventil", fmtPct(s.le_pct), "--c-le", "le");
  group("Betrieb");
  row("Betriebsart", s.mode_label || "–", null, "running,wrg,le");
  row("Lüftung", s.fan_level === undefined ? "–" : LEVEL[s.fan_level], null, "fan_level,fan_zul,fan_fol");
  row("Raucherraum", s.smoker ? "ein" : "aus", null, "smoker");
  row("Nachtauskühlung", nakText(s.nak), null, "nak_active,t_raum,t_witt,nak_target");
  if (s.run_since) row("Laufzeit", runtimeText(s, data.server_time).replace("Läuft seit ", ""), null, "fan_level");
  row("Restlaufzeit", remainingText(s, data.server_time), null, "fan_level");
  if (s.manual) row("Handbetrieb", "bis " + fmtDateTime(s.manual.until), null, "wrg,le");
  if (s.nak && s.nak.enabled && !s.nak.active && s.nak.reason) row("NAK wartet", s.nak.reason);
  group("Meldungen");
  const bl = meta.betrieb_labels || {};
  const sl = meta.stoerung_labels || {};
  for (const k in s.betrieb || {}) if (s.betrieb[k]) row(bl[k] || k, "ein");
  const faults = Object.keys(s.stoerung || {}).filter((k) => s.stoerung[k]);
  if (!faults.length) row("Störungen", "keine", null, "alarm,frost,stroem_zul,stroem_fol");
  for (const k of faults) row(sl[k] || k, "▲ aktiv");
  $("#values tbody").innerHTML = rows.join("");
}

function updateFooter(data) {
  const s = data.state || {};
  const parts = ["Version " + ((data.meta || {}).version || "–")];
  if (s.started) parts.push("Regelung läuft seit " + fmtDateTime(s.started));
  if (s.algorithm) parts.push("Regler " + s.algorithm.toUpperCase() + ", Takt " + s.cycle_seconds + " s");
  parts.push("Charts: Highcharts Stock");
  $("#foot").textContent = parts.join(" · ");
}

function toSeries(src, k) {
  return src.t.map((t, i) => [t * 1000, src[k][i]]);
}

/* Highcharts Stock ---------------------------------------------------------- */

function setupHighcharts() {
  Highcharts.setOptions({
    lang: {
      locale: "de-CH",
      months: ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
        "September", "Oktober", "November", "Dezember"],
      shortMonths: ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],
      weekdays: ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"],
      shortWeekdays: WD,
      decimalPoint: ",",
      thousandsSep: "'",
      rangeSelectorZoom: "",
      rangeSelectorFrom: "Von",
      rangeSelectorTo: "Bis",
      loading: "Lade …",
      noData: "Keine Daten",
    },
    time: { timezone: "Europe/Zurich" },
  });
}

function theme() {
  const ink = cssVar("--ink"), ink2 = cssVar("--ink-2"), muted = cssVar("--muted");
  const grid = cssVar("--grid"), axis = cssVar("--axis"), surface = cssVar("--surface");
  return { ink, ink2, muted, grid, axis, surface, wash: cssVar("--wash") };
}

function layout(keys) {
  /* Je Einheit ein Feld, Ein/Aus-Signale je ein schmales Feld; alle teilen die Zeitachse.
     Keine zweite y-Skala im selben Feld. */
  const panes = [];
  for (const unit in UNITS) {
    const members = keys.filter((k) => SIGNALS[k].unit === unit);
    if (UNITS[unit].single) {
      for (const k of members) panes.push(Object.assign({ unit, title: SIGNALS[k].label, keys: [k] }, UNITS[unit]));
    } else if (members.length) {
      panes.push(Object.assign({ unit, title: unit, keys: members }, UNITS[unit]));
    }
  }
  const gap = 4;
  const total = panes.reduce((a, p) => a + p.weight, 0);
  const usable = 100 - gap * (panes.length - 1);
  let top = 0;
  for (const p of panes) {
    p.top = top;
    p.height = usable * p.weight / total;
    top += p.height + gap;
  }
  const px = panes.reduce((a, p) => a + p.weight * (p.single ? 64 : 78), 0);
  return { panes, height: Math.round(px + 150 + panes.length * 8) };
}

const RANGE_BUTTONS = [
  { type: "hour", count: 6, text: "6 h" },
  { type: "day", count: 1, text: "24 h" },
  { type: "day", count: 3, text: "3 T" },
  { type: "week", count: 1, text: "7 T" },
  { type: "month", count: 1, text: "30 T" },
  { type: "year", count: 1, text: "1 J" },
  { type: "all", text: "Alles" },
];

class StockPanel {
  /* Ein Chart mit festen Grundkurven und bis zu 6 frei wählbaren Zusatzkurven. */
  constructor(root, opts) {
    this.root = root;
    this.base = opts.base.slice();
    this.extra = [];
    this.title = opts.title;
    this.chart = null;
    this.nav = null;        // ganzer Verlauf der ersten Kurve, grob, für den Navigator
    this.extremes = null;
    this.loadedAt = 0;
    this.bucket = 60;
    root.innerHTML = "";
    const head = document.createElement("div");
    head.className = "panel-head";
    if (opts.showTitle !== false) {
      const h = document.createElement("h3");
      h.textContent = opts.title;
      head.appendChild(h);
    }
    this.picker = document.createElement("div");
    this.picker.className = "picker";
    head.appendChild(this.picker);
    root.appendChild(head);
    this.box = document.createElement("div");
    this.box.className = "stock";
    root.appendChild(this.box);
    this.renderPicker();
    this.reload();
  }

  keys() { return this.base.concat(this.extra); }

  renderPicker() {
    const p = this.picker;
    p.innerHTML = "";
    for (const k of this.extra) {
      const chip = document.createElement("span");
      chip.className = "xchip";
      chip.innerHTML = '<span class="sw" style="background:var(' + SIGNALS[k].color + ')"></span>' +
        escapeHtml(SIGNALS[k].label);
      const rm = document.createElement("button");
      rm.type = "button";
      rm.setAttribute("aria-label", SIGNALS[k].label + " entfernen");
      rm.textContent = "×";
      rm.addEventListener("click", () => {
        this.extra = this.extra.filter((x) => x !== k);
        this.renderPicker();
        this.reload();
      });
      chip.appendChild(rm);
      p.appendChild(chip);
    }
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", "Kurve hinzufügen");
    const left = MAX_EXTRA - this.extra.length;
    sel.disabled = left <= 0;
    sel.innerHTML = '<option value="">' + (left > 0 ? "+ Kurve hinzufügen (" + left + " frei)"
      : "Maximal " + MAX_EXTRA + " Zusatzkurven") + "</option>";
    const used = new Set(this.keys());
    for (const unit in UNITS) {
      const og = document.createElement("optgroup");
      og.label = UNITS[unit].group;
      for (const k in SIGNALS) {
        if (SIGNALS[k].unit !== unit || used.has(k)) continue;
        const o = document.createElement("option");
        o.value = k;
        o.textContent = SIGNALS[k].label;
        og.appendChild(o);
      }
      if (og.children.length) sel.appendChild(og);
    }
    sel.addEventListener("change", () => {
      if (!sel.value || this.extra.length >= MAX_EXTRA) return;
      this.extra.push(sel.value);
      this.renderPicker();
      this.reload();
    });
    p.appendChild(sel);
  }

  detach(latest) {
    /* Zoom merken und Chart abbauen, damit build() ihn neu aufbaut */
    if (!this.chart) return;
    const ex = this.chart.xAxis[0].getExtremes();
    this.extremes = { min: ex.min, max: ex.max, latest: latest && ex.max >= ex.dataMax - 120000 };
    this.chart.destroy();
    this.chart = null;
  }

  async reload() {
    this.detach(true);
    if (!this.nav) {
      this.box.classList.add("loading");
      try {
        this.nav = await getJSON("/api/history?start=first&points=1000&keys=" + this.base[0]);
        this.navLoadedAt = Date.now();
      } catch (e) {
        this.box.textContent = "Verlauf nicht erreichbar.";
        return;
      }
      this.box.classList.remove("loading");
    }
    this.build();
    const ex = this.chart.xAxis[0].getExtremes();
    this.loadDetail(ex.min, ex.max);
  }

  build() {
    const th = theme();
    const keys = this.keys();
    const { panes, height } = layout(keys);
    const nav = keys[0];
    const yAxis = panes.map((p, i) => ({
      top: p.top + "%",
      height: p.height + "%",
      offset: 0,
      opposite: false,
      min: p.min, max: p.max,
      tickPositions: p.ticks,
      softMin: p.unit === "°C" ? undefined : p.min,
      gridLineColor: th.grid,
      lineColor: th.axis,
      lineWidth: 0,
      labels: {
        enabled: !p.single,
        align: "right", x: -6, style: { color: th.muted, fontSize: "11px" },
        formatter: p.axis ? function () { return p.axis(this.value); } : undefined,
      },
      title: {
        text: p.title, align: "high", rotation: 0, textAlign: "left", reserveSpace: false,
        offset: 0, x: 6, y: p.single ? 12 : -2,
        style: { color: th.ink2, fontSize: "11.5px", fontWeight: "600", whiteSpace: "nowrap" },
      },
      resize: { enabled: i < panes.length - 1 && panes.length > 1, lineColor: th.axis, lineWidth: 1 },
    }));
    const series = keys.map((k) => {
      const sig = SIGNALS[k];
      const u = UNITS[sig.unit];
      const paneIndex = panes.findIndex((p) => p.keys.indexOf(k) >= 0);
      const color = cssVar(sig.color);
      const bool = sig.unit === "bool";
      return {
        id: k,
        name: sig.label,
        type: bool ? "area" : "line",
        yAxis: paneIndex,
        // erst die Navigator-Daten, die Details lädt reload() gleich danach
        data: k === nav ? toSeries(this.nav, k) : [],
        color: color,
        dashStyle: typeof sig.dash === "string" ? sig.dash : sig.dash ? "ShortDash" : "Solid",
        lineWidth: bool ? 1.5 : 2,
        fillOpacity: bool ? 0.35 : undefined,
        // Ein/Aus: bei «aus» keine farbige Linie, damit Rot nur bei aktivem Alarm erscheint
        zones: bool ? [{ value: 0.5, color: "transparent" }, { color: color }] : undefined,
        step: u.step ? "left" : undefined,
        tooltip: {
          valueSuffix: u.suffix,
          valueDecimals: u.decimals,
          pointFormatter: u.text ? function () {
            return '<span style="color:' + this.color + '">●</span> ' + this.series.name +
              ": <b>" + u.text(this.y) + "</b><br/>";
          } : undefined,
        },
        dataGrouping: {
          approximation: u.step ? "high" : "average",
          groupPixelWidth: 2,
          units: [["minute", [1, 5, 10, 15, 30]], ["hour", [1, 2, 3, 6, 12]], ["day", [1]], ["week", [1]]],
        },
        showInNavigator: false,
      };
    });
    const self = this;
    const narrow = this.root.clientWidth < 620;
    const fontFamily = 'system-ui, -apple-system, "Segoe UI", sans-serif';
    this.chart = Highcharts.stockChart(this.box, {
      chart: {
        height: height,
        backgroundColor: "transparent",
        style: { fontFamily: fontFamily },
        spacing: [8, 4, 8, 4],
        zooming: { type: "x" },
        animation: false,
      },
      accessibility: { description: this.title },
      credits: { style: { color: th.muted } },
      title: { text: undefined },
      legend: {
        enabled: keys.length > 1,
        align: "left",
        itemStyle: { color: th.ink2, fontWeight: "400", fontSize: "12.5px" },
        itemHoverStyle: { color: th.ink },
        itemHiddenStyle: { color: th.muted },
      },
      rangeSelector: {
        buttons: RANGE_BUTTONS,
        selected: this.extremes ? undefined : 1,
        inputEnabled: !narrow,
        inputDateFormat: "%d.%m.%Y %H:%M",
        inputEditDateFormat: "%d.%m.%Y %H:%M",
        inputStyle: { color: th.ink },
        labelStyle: { color: th.muted },
        buttonTheme: {
          fill: "none", stroke: "none", r: 7, height: 26, width: narrow ? 34 : 42,
          style: { color: th.ink2, fontWeight: "400" },
          states: {
            hover: { fill: th.wash, style: { color: th.ink } },
            select: { fill: th.wash, style: { color: th.ink, fontWeight: "600" } },
            disabled: { style: { color: th.axis } },
          },
        },
      },
      navigator: {
        adaptToUpdatedData: false,
        height: 32,
        maskFill: th.wash,
        outlineColor: th.axis,
        handles: { backgroundColor: th.surface, borderColor: th.ink2 },
        xAxis: { gridLineColor: th.grid, labels: { style: { color: th.muted, textOutline: "none", fontSize: "10.5px" } } },
        series: { data: toSeries(this.nav, nav), color: cssVar(SIGNALS[nav].color), type: "line", lineWidth: 1 },
      },
      scrollbar: { enabled: false },
      xAxis: {
        ordinal: false,
        lineColor: th.axis,
        tickColor: th.axis,
        gridLineWidth: 0,
        crosshair: { color: th.ink2, dashStyle: "ShortDash", width: 1 },
        labels: { style: { color: th.muted, fontSize: "11px" } },
        events: { afterSetExtremes: function (e) { if (e.trigger !== "refresh") self.loadDetail(e.min, e.max); } },
        minRange: 30 * 60 * 1000,
      },
      yAxis: yAxis,
      tooltip: {
        split: false,
        shared: true,
        backgroundColor: th.surface,
        borderColor: th.axis,
        borderRadius: 10,
        shadow: true,
        style: { color: th.ink, fontSize: "12.5px" },
        xDateFormat: "%a %d.%m.%Y %H:%M",
      },
      plotOptions: { series: { animation: false, states: { hover: { lineWidthPlus: 0 } }, marker: { radius: 3 } } },
      series: series,
    });
    if (this.extremes) {
      const now = Date.now();
      const width = this.extremes.max - this.extremes.min;
      const max = this.extremes.latest ? now : this.extremes.max;
      this.chart.xAxis[0].setExtremes(max - width, max);
    }
  }

  isLive() {
    /* Fenster reicht bis ans Ende der geladenen Daten: dann ist «jetzt» gemeint */
    const ex = this.chart.xAxis[0].getExtremes();
    return ex.max >= ex.dataMax - 3 * 60 * 1000;
  }

  async loadDetail(min, max) {
    if (!this.chart || min === undefined || max === undefined) return;
    // Highcharts begrenzt die Extremes auf die bekannten Daten; am Live-Ende
    // darum bis jetzt laden, sonst kommen neue Punkte nie dazu
    if (this.isLive()) max = Math.max(max, Date.now());
    const token = (this._token = (this._token || 0) + 1);
    // etwa ein Punkt pro Pixel: die Datengruppierung fasst ohnehin 2 px zusammen
    const points = Math.min(4000, Math.max(300, Math.round(this.chart.plotWidth)));
    const url = "/api/history?start=" + Math.floor(min / 1000) + "&end=" + Math.ceil(max / 1000) +
      "&points=" + points + "&keys=" + this.keys().join(",");
    let data;
    try { data = await getJSON(url); } catch (e) { return; }
    if (token !== this._token || !this.chart) return;
    this.keys().forEach((k) => {
      const s = this.chart.get(k);
      if (s) s.setData(toSeries(data, k), false, false, false);
    });
    this.chart.redraw(false);
    this.loadedAt = Date.now();
    this.bucket = data.bucket || 60;
  }

  async refresh() {
    /* Steht das Fenster am Live-Ende, nachziehen – aber erst, wenn ein neuer
       Punkt fällig ist (bei «1 Jahr» also nur alle paar Stunden) */
    if (!this.chart || Date.now() - this.loadedAt < this.bucket * 1000 || !this.isLive()) return;
    const ex = this.chart.xAxis[0].getExtremes();
    const width = ex.max - ex.min;
    if (Date.now() - (this.navLoadedAt || 0) > 10 * 60 * 1000) {
      // Navigator mitwachsen lassen, sonst bleibt die Zeitachse beim Ladezeitpunkt stehen
      try {
        this.nav = await getJSON("/api/history?start=first&points=1000&keys=" + this.base[0]);
        this.navLoadedAt = Date.now();
        const navSeries = this.chart.navigator && this.chart.navigator.series && this.chart.navigator.series[0];
        if (navSeries) navSeries.setData(toSeries(this.nav, this.base[0]), false);
      } catch (e) { /* nächstes Mal */ }
    }
    await this.loadDetail(Date.now() - width, Date.now());
    this.chart.xAxis[0].setExtremes(Date.now() - width, Date.now(), true, false, { trigger: "refresh" });
  }

  rebuild() {
    if (!this.chart) return;
    this.detach(false);
    this.build();
    const ex = this.chart.xAxis[0].getExtremes();
    this.loadDetail(ex.min, ex.max);
  }
}

/* Detailansicht (Klick im Schema) ------------------------------------------- */

let detailPanel = null;

function openDetail(node) {
  const keys = (node.dataset.signals || "").split(",").filter((k) => SIGNALS[k]);
  if (!keys.length) return;
  const dlg = $("#detail");
  $("#detail-title").textContent = node.dataset.title || SIGNALS[keys[0]].label;
  if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  detailPanel = new StockPanel($("#detail-panel"), { title: node.dataset.title || "", base: keys, showTitle: false });
}

function openFromHash() {
  /* Direktlink, z. B. /#t_zul öffnet den Verlauf der Zulufttemperatur */
  const key = decodeURIComponent(location.hash.slice(1));
  if (!key) return;
  const node = $$("#scheme .hit").find((n) => (n.dataset.signals || "").split(",")[0] === key);
  if (node && !$("#detail").open) openDetail(node);
}

function closeDetail() {
  const dlg = $("#detail");
  if (detailPanel && detailPanel.chart) detailPanel.chart.destroy();
  detailPanel = null;
  $("#detail-panel").innerHTML = "";
  if (dlg.open) dlg.close();
  if (location.hash) history.replaceState(null, "", location.pathname);
}

function setupHits() {
  for (const g of $$("#scheme .hit")) {
    g.setAttribute("tabindex", "0");
    g.setAttribute("role", "button");
    g.setAttribute("aria-label", (g.dataset.title || "") + ": Verlauf anzeigen");
    // unsichtbare Trefferfläche, damit auch Linien und Text gut klickbar sind
    const bb = g.getBBox();
    const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    r.setAttribute("x", bb.x - 6); r.setAttribute("y", bb.y - 6);
    r.setAttribute("width", bb.width + 12); r.setAttribute("height", bb.height + 12);
    r.setAttribute("rx", 8);
    r.setAttribute("class", "hitbox");
    g.insertBefore(r, g.firstChild);
  }
  document.addEventListener("click", (e) => {
    const hit = e.target.closest && e.target.closest(".hit");
    if (hit) openDetail(hit);
  });
  document.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("hit")) {
      e.preventDefault();
      openDetail(e.target);
    }
  });
  $("#detail-close").addEventListener("click", closeDetail);
  $("#detail").addEventListener("close", closeDetail);
  $("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") closeDetail(); });
}

/* Bedienung: nur im Panel (Port 8080, LAN) ------------------------------------- */

let panelMode = false;
let authenticated = false;
let lastManualKey = null;

function setMsg(text) { $("#control-msg").textContent = text || ""; }

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Krempelair": "1" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { /* leer */ }
  if (r.status === 401 && path !== "/api/login") { authenticated = false; renderAuth(); }
  return { status: r.status, data: data };
}

async function control(body) {
  setMsg("Wird ausgeführt …");
  $$("#control-card button").forEach((b) => { b.disabled = true; });
  try {
    const res = await post("/api/control", body);
    setMsg((res.data.ok ? "✓ " : "✗ ") + (res.data.message || res.data.error || "Fehler " + res.status));
  } catch (e) {
    setMsg("✗ Panel nicht erreichbar");
  } finally {
    $$("#control-card button").forEach((b) => { b.disabled = false; });
    loadState();
  }
}

function renderAuth() {
  const btn = $("#auth-btn");
  btn.hidden = !panelMode;
  btn.textContent = authenticated ? "Abmelden" : "Anmelden";
  btn.classList.toggle("ghost", authenticated);
  $("#control-card").hidden = !authenticated;
}

function fillSelect(sel, values, suffix) {
  sel.innerHTML = values.map((v) => '<option value="' + v + '">' + v + suffix + "</option>").join("");
}

function setupControls() {
  fillSelect($("#ctl-setpoint"), [17, 18, 19, 20, 21, 22, 23, 24, 25, 26], " °C");
  fillSelect($("#ctl-nak-target"), [18, 19, 20, 21, 22, 23], " °C");
  fillSelect($("#man-hours"), [1, 2, 3, 4, 5, 6, 7, 8], " h");
  $$("#ctl-fan button").forEach((b) => b.addEventListener("click",
    () => control({ action: "fan", level: Number(b.dataset.level) })));
  $$("#ctl-smoker button").forEach((b) => b.addEventListener("click",
    () => control({ action: "smoker", on: b.dataset.on === "1" })));
  $("#ctl-setpoint-set").addEventListener("click",
    () => control({ action: "setpoint", value: Number($("#ctl-setpoint").value) }));
  $("#ctl-nak-on").addEventListener("click", () => control({ action: "nak", on: true }));
  $("#ctl-nak-off").addEventListener("click", () => control({ action: "nak", on: false }));
  $("#ctl-nak-target-set").addEventListener("click",
    () => control({ action: "nak_target", value: Number($("#ctl-nak-target").value) }));
  for (const k of ["wrg", "le"]) {
    const on = $("#man-" + k + "-on"), range = $("#man-" + k), out = $("#man-" + k + "-out");
    const sync = () => { range.disabled = !on.checked; out.textContent = range.value + " %"; };
    on.addEventListener("change", sync);
    range.addEventListener("input", sync);
    sync();
  }
  $("#man-set").addEventListener("click", () => control({
    action: "manual",
    wrg: $("#man-wrg-on").checked ? Number($("#man-wrg").value) : null,
    le: $("#man-le-on").checked ? Number($("#man-le").value) : null,
    hours: Number($("#man-hours").value),
  }));
  $("#man-auto").addEventListener("click", () => control({ action: "auto" }));

  $("#auth-btn").addEventListener("click", async () => {
    if (authenticated) {
      await post("/api/logout", {});
      authenticated = false;
      renderAuth();
      return;
    }
    $("#login-msg").textContent = "";
    $("#login-pw").value = "";
    $("#login").showModal();
  });
  $("#login-cancel").addEventListener("click", () => $("#login").close());
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await post("/api/login", { password: $("#login-pw").value });
    if (res.status === 200) {
      authenticated = true;
      $("#login").close();
      renderAuth();
      loadState();
    } else {
      $("#login-msg").textContent = res.data.message || res.data.error || "Anmeldung fehlgeschlagen";
    }
  });
}

function updateControls(s) {
  if (!authenticated || !s) return;
  $$("#ctl-fan button").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.level) === s.fan_level)));
  $$("#ctl-smoker button").forEach((b) => b.setAttribute("aria-pressed", String((b.dataset.on === "1") === !!s.smoker)));
  if (document.activeElement !== $("#ctl-setpoint") && s.setpoint) $("#ctl-setpoint").value = String(Math.round(s.setpoint));
  const nak = s.nak || {};
  if (document.activeElement !== $("#ctl-nak-target") && nak.target) $("#ctl-nak-target").value = String(Math.round(nak.target));
  $("#ctl-nak-state").textContent = nak.enabled ? (nak.active ? "läuft" : "bereit bis " + fmtDateTime(nak.until) + " – " + (nak.reason || "")) : "aus";
  $("#ctl-runtime").textContent = runtimeText(s);
  const m = s.manual;
  const key = m ? m.until + ":" + m.wrg + ":" + m.le : "auto";
  if (key !== lastManualKey) {
    // aktiven Handbetrieb in die Regler übernehmen (nur bei Änderung, nicht beim Schieben)
    lastManualKey = key;
    for (const k of ["wrg", "le"]) {
      const on = !!m && m[k] !== null;
      $("#man-" + k + "-on").checked = on;
      if (on) $("#man-" + k).value = String(Math.round(m[k] / 10.23 / 5) * 5);
      $("#man-" + k).dispatchEvent(new Event("input"));
    }
  }
  $("#man-state").textContent = m
    ? "Hand bis " + fmtDateTime(m.until) + ": " + [m.wrg !== null ? "WRG " + fmtPct(m.wrg / 10.23) : null,
      m.le !== null ? "Heizventil " + fmtPct(m.le / 10.23) : null].filter(Boolean).join(", ")
    : "Automatik";
}

// now: Serverzeit, damit eine falsch gehende Uhr im Browser nichts verschiebt
function runtimeText(s, now) {
  if (!s.run_since) return "";
  const hours = ((now || Date.now() / 1000) - s.run_since) / 3600;
  return "Läuft seit " + fmtNum(hours, 1) + " h" +
    (s.run_limit_at ? ", schaltet spätestens " + fmtDateTime(s.run_limit_at) + " aus" : "");
}

// bis die Laufzeitbegrenzung (max_run_hours) die Lüftung ausschaltet
function remainingText(s, now) {
  if (!s.running) return "–";
  if (!s.run_limit_at) return "unbegrenzt";
  const left = Math.max(0, s.run_limit_at - (now || Date.now() / 1000));
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const d = new Date(s.run_limit_at * 1000);
  return h + " h " + pad(m) + " min · " + WD[d.getDay()] + " " + fmtTime(s.run_limit_at);
}

async function initPanel() {
  let r;
  try { r = await fetch("/api/session", { cache: "no-store", credentials: "same-origin" }); } catch (e) { return; }
  if (r.status !== 200) {
    // öffentliches Dashboard: im LAN auf das Panel hinweisen
    if (/^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)|\.local$|^krempelair$/.test(location.hostname)) {
      const a = document.createElement("a");
      a.href = "http://" + location.hostname + ":8080/";
      a.textContent = "Bedienen (nur im LAN)";
      $(".foot").appendChild(document.createTextNode(" · "));
      $(".foot").appendChild(a);
    }
    return;
  }
  const s = await r.json();
  panelMode = true;
  authenticated = !!s.authenticated;
  setupControls();
  renderAuth();
}

/* Laden -------------------------------------------------------------------- */

const panels = [];

async function loadState() {
  try {
    const data = await getJSON("/api/state");
    updateConn(data, false);
    updateScheme(data);
    updateChips(data);
    updateValues(data);
    updateFooter(data);
    updateControls(data.state);
  } catch (err) {
    updateConn({}, true);
  }
}

async function loadEvents() {
  try {
    const list = await getJSON("/api/events?limit=60");
    $("#events").innerHTML = list.map((ev) =>
      '<li class="' + escapeHtml(ev.level) + '"><time>' + fmtDateTime(ev.ts).slice(3) + "</time><span><span>" +
      escapeHtml(ev.message) + '</span><br><span class="src">' + escapeHtml(ev.source) + "</span></span></li>"
    ).join("") || "<li><span></span><span>Keine Ereignisse</span></li>";
  } catch (err) { /* später erneut */ }
}

function init() {
  setupHighcharts();
  setupHits();
  panels.push(new StockPanel($("#panel-temp"), {
    title: "Temperaturen", base: ["t_zul", "t_soll", "t_abl", "t_aul", "t_fol", "t_raum", "t_witt"] }));
  panels.push(new StockPanel($("#panel-out"), { title: "Stellwerte", base: ["wrg", "le"] }));
  panels.push(new StockPanel($("#panel-state"), {
    title: "Betrieb", base: ["fan_level", "nak_active", "smoker", "alarm", "frost"] }));

  openFromHash();
  window.addEventListener("hashchange", openFromHash);
  initPanel();

  const allPanels = () => panels.concat(detailPanel ? [detailPanel] : []);
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const retheme = () => allPanels().forEach((p) => p.rebuild());
  if (mq.addEventListener) mq.addEventListener("change", retheme);

  loadState();
  loadEvents();
  // versteckte Tabs fragen nicht nach; beim Zurückkommen sofort aktualisieren
  const visible = (fn) => () => { if (!document.hidden) fn(); };
  const refreshCharts = () => allPanels().forEach((p) => p.refresh());
  setInterval(visible(loadState), 3000);
  setInterval(visible(loadEvents), 20000);
  setInterval(visible(refreshCharts), 60000);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    loadState();
    loadEvents();
    refreshCharts();
  });
}

document.addEventListener("DOMContentLoaded", init);
