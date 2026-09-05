/* =====================================================================
 * Steam-games visualisatie (viz/) - Backgrounds-stijl
 * ---------------------------------------------------------------------
 * - Leest data/games.csv + data/date.csv (via fetch -> lokale server).
 * - Tijdlijn: "Amount of Steam games over time" - cumulatief aantal
 *   games (appids) tot elke releasedatum, geanimeerd in de browser.
 *   Elke tick verschuift de tijdcursor en voegt de releases t/m die
 *   datum toe aan de lijn (tijdsverloop simulatie).
 * - MP4-export: canvas.captureStream + MediaRecorder, download van een
 *   volledige cyclus (geen ffmpeg nodig, zoals Backgrounds).
 * - Weekday-grafiek: aantal games per dag-van-week, primair uit
 *   date.csv (day_of_week_label, ISO maandag=1..zondag=7); releasedatums
 *   buiten date.csv (vóór 2003) direct uit de datum berekend.
 * ===================================================================== */

"use strict";

/* ---------- Kleuren ----------
 * Vast: donker thema. Per-grafiek kleuren (niet gedeeld):
 * chartColors.timeline (lijn/oppervlak/teller = line, stip = accent) en
 * chartColors.weekday (balken = color). Elke grafiek heeft eigen kleurkiezer(s). */
const C = {
  bg: "#0f1a26",                     // canvas-achtergrond
  grid: "rgba(199,213,224,0.08)",    // rasterlijnen
  axis: "rgba(199,213,224,0.30)",    // as-kader
  text: "#ffffff",                   // titels / labels (default WIT)
  muted: "#8ba2b6",                  // bijschriften (subtitel, datumlabel)
};

/* Kleuren per grafiek (NIET gedeeld): elke grafiek heeft eigen kleurkiezer(s). */
const chartColors = {
  timeline: { line: "#66c0f4", accent: "#e74c3c" },
  weekday:  { color: "#66c0f4" },
};

/* Stijl van de x-/y-aswaarden (ticklabels) + y-as-label (bv. Amount).
 * Instelbaar op de pagina en GEDEELD: elke grafiek (ook toekomstige)
 * gebruikt deze waarden via axisFont()/axis.color/axis.yLabel. */
const axis = { size: 20, bold: false, color: C.text, yLabel: true };
function axisFont() {
  return `${axis.bold ? 700 : 500} ${axis.size}px 'Segoe UI', Arial, sans-serif`;
}

/* '#rrggbb' + alpha -> 'rgba(r,g,b,a)' (voor het oppervlak onder de lijn). */
function withAlpha(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

const DAYS_ORDER = ["monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday"];
const DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday",
                    "Friday", "Saturday", "Sunday"];
const MONTHS_FULL = ["January", "February", "March", "April", "May",
                     "June", "July", "August", "September", "October",
                     "November", "December"];

const el = {
  timeline: document.getElementById("timelineCanvas"),
  weekday: document.getElementById("weekdayCanvas"),
  playBtn: document.getElementById("playBtn"),
  restartBtn: document.getElementById("restartBtn"),
  durSlider: document.getElementById("durSlider"),
  durVal: document.getElementById("durVal"),
  exportBtn: document.getElementById("exportBtn"),
  exportStatus: document.getElementById("exportStatus"),
  recBadge: document.getElementById("recBadge"),
  lineColor: document.getElementById("lineColor"),
  accentColor: document.getElementById("accentColor"),
  weekdayColor: document.getElementById("weekdayColor"),
  axisSize: document.getElementById("axisSize"),
  axisSizeVal: document.getElementById("axisSizeVal"),
  axisBold: document.getElementById("axisBold"),
  axisColor: document.getElementById("axisColor"),
  axisYLabel: document.getElementById("axisYLabel"),
  wdAxisSize: document.getElementById("wdAxisSize"),
  wdAxisSizeVal: document.getElementById("wdAxisSizeVal"),
  wdBold: document.getElementById("wdBold"),
  wdColor: document.getElementById("wdAxisColor"),
  wdYLabel: document.getElementById("wdYLabel"),
  dataInfo: document.getElementById("dataInfo"),
  timelineFoot: document.getElementById("timelineFoot"),
  weekdayFoot: document.getElementById("weekdayFoot"),
};

const state = {
  ready: false,
  gamesTotal: 0,          // totaal aantal regels in games.csv
  withDate: 0,            // aantal met geldige releasedatum
  relTimes: [],           // unieke releasedatums (ms, oplopend)
  relCounts: [],          // cumulatief aantal t/m elke relTime
  firstYear: 0, lastYear: 0,
  tMin: 0, tMax: 0,       // gepadded as-bereik (ms)
  ymax: 0,
  weekdayCounts: [0, 0, 0, 0, 0, 0, 0],
  weekdayFallback: 0,     // releases buiten date.csv (weekday direct berekend)
  // playback
  playing: false,
  elapsed: 0,             // ms "animatietijd" (1 cyclus = durMs)
  durMs: 30000,
  pauseP: 0,
  recording: false,
  cancelExport: false,
  exportTimer: null,
  mediaRec: null,
  chunks: [],
};

/* =====================================================================
 * Helpers
 * ===================================================================== */
function fmtInt(n) { return n.toLocaleString("en-US"); }

function niceStep(raw) {
  if (raw <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  let step;
  if (norm <= 1) step = 1;
  else if (norm <= 2) step = 2;
  else if (norm <= 5) step = 5;
  else step = 10;
  return step * mag;
}

/* 'yyyy-mm-dd' -> ms sinds epoch (UTC-middernacht, geen TZ-shift). */
function dateToMs(s) {
  const p = s.split("-");
  return Date.UTC(+p[0], +p[1] - 1, +p[2]);
}
/* ISO-weekdag-index uit een 'yyyy-mm-dd' (maandag=0..zondag=6). */
function dateToDowIdx(s) {
  const p = s.split("-");
  const d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
  return (d.getUTCDay() + 6) % 7;
}
function fmtMonthYear(ms) {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${MONTHS_FULL[d.getUTCMonth()]}`;
}

/* Eenvoudige RFC4180-achtige CSV-parser (aanhalingstekens + , in veld). */
function parseCSV(text) {
  text = text.replace(/^\uFEFF/, ""); // BOM weg
  const rows = [];
  let row = [], field = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQ) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQ = false;
      } else field += ch;
    } else if (ch === '"') {
      inQ = true;
    } else if (ch === ",") {
      row.push(field); field = "";
    } else if (ch === "\n") {
      row.push(field); rows.push(row); row = []; field = "";
    } else if (ch !== "\r") {
      field += ch;
    }
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  if (!rows.length) return [];
  const header = rows[0].map((h) => h.trim());
  return rows.slice(1)
    .filter((r) => r.some((c) => c.trim() !== ""))
    .map((r) => {
      const o = {};
      header.forEach((h, i) => { o[h] = (r[i] ?? "").trim(); });
      return o;
    });
}

function upperBound(arr, v) {
  let lo = 0, hi = arr.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (arr[m] <= v) lo = m + 1; else hi = m;
  }
  return lo;
}

/* =====================================================================
 * Data laden
 * ===================================================================== */
async function loadData() {
  try {
    const [gamesCsv, dateCsv] = await Promise.all([
      fetch("../data/games.csv").then((r) => {
        if (!r.ok) throw new Error(`games.csv: HTTP ${r.status}`);
        return r.text();
      }),
      fetch("../data/date.csv").then((r) => {
        if (!r.ok) throw new Error(`date.csv: HTTP ${r.status}`);
        return r.text();
      }),
    ]);

    const gamesRows = parseCSV(gamesCsv);
    const dateRows = parseCSV(dateCsv);

    // Weekday-zoekmap uit date.csv: date_fmt -> dag-van-week-label.
    const dowMap = {};
    for (const r of dateRows) {
      if (r.date_fmt && r.day_of_week_label) {
        dowMap[r.date_fmt] = r.day_of_week_label.toLowerCase();
      }
    }

    // Games met geldige releasedatum; tel de dag-van-week.
    state.gamesTotal = gamesRows.length;
    const byDate = new Map(); // dateStr -> aantal
    let withDate = 0;
    for (const g of gamesRows) {
      const d = g.release_date_fmt;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d || "")) continue;
      withDate++;
      byDate.set(d, (byDate.get(d) || 0) + 1);
      const label = dowMap[d];
      const idx = label !== undefined ? DAYS_ORDER.indexOf(label)
                                      : dateToDowIdx(d);
      if (idx >= 0) {
        state.weekdayCounts[idx]++;
        if (label === undefined) state.weekdayFallback++;
      }
    }
    state.withDate = withDate;

    // Tijdlijn-data: unieke releasedatums + cumulatief.
    const times = [...byDate.keys()].map(dateToMs).sort((a, b) => a - b);
    const counts = [];
    {
      let acc = 0;
      const perMs = new Map();
      byDate.forEach((n, d) => perMs.set(dateToMs(d), n));
      for (const t of times) { acc += perMs.get(t); counts.push(acc); }
    }
    state.relTimes = times;
    state.relCounts = counts;
    const dFirst = new Date(times[0]);
    const dLast = new Date(times[times.length - 1]);
    state.firstYear = dFirst.getUTCFullYear();
    state.lastYear = dLast.getUTCFullYear();
    // De grafiek begint bij de Steam-lancering (september 2003), dus geen
    // lege voorloop in 2002: start op 1 september van het eerste jaar (of
    // op de eerste releasedatum zelf als die eerder zou liggen).
    state.tMin = Math.min(Date.UTC(state.firstYear, 8, 1),
                          state.relTimes[0]);
    state.tMax = Date.UTC(state.lastYear + 1, 5, 1);       // midden van jaar erna
    state.ymax = Math.ceil(counts[counts.length - 1] / 100) * 100;

    // Footers / koptekst.
    el.dataInfo.textContent =
      `${fmtInt(state.gamesTotal)} games from games.csv · ` +
      `${fmtInt(state.withDate)} with a release date`;
    el.timelineFoot.textContent =
      `${fmtInt(state.withDate)} games with a known release date ` +
      `(${fmtInt(state.gamesTotal)} in total in games.csv) · ` +
      `${state.relTimes.length} unique release dates · ` +
      `animation from ${state.firstYear} to ${state.lastYear}`;
    const dowTotal = state.weekdayCounts.reduce((a, b) => a + b, 0);
    el.weekdayFoot.textContent =
      `Based on ${fmtInt(dowTotal)} releases · weekday from date.csv ` +
      `(ISO, Monday=1)${state.weekdayFallback
        ? ` · ${state.weekdayFallback} releases before 2003 computed directly from the date`
        : ""}`;

    drawWeekday();
    state.pauseP = 0;
    drawTimeline(0);
    state.ready = true;
  } catch (err) {
    el.dataInfo.textContent = "❌ Failed to load";
    const box = document.createElement("div");
    box.className = "error-box";
    box.textContent =
      "Could not load data/games.csv or data/date.csv (" + err.message + "). " +
      "Open this page through a local web server - fetch does not work " +
      "from file://.";
    document.querySelector("main").prepend(box);
    console.error(err);
  }
}

/* =====================================================================
 * Tijdlijn-tekenen (per frame: p in [0,1) over de hele tijdsas)
 * ===================================================================== */
function drawTimeline(p) {
  const cv = el.timeline;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  const M = { l: 120, r: 70, t: 235, b: 90 };
  const x0 = M.l, y0 = M.t;
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const st = state;
  const X = (t) => x0 + ((t - st.tMin) / (st.tMax - st.tMin)) * pw;
  const Y = (v) => y0 + ph - (v / st.ymax) * ph;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, W, H);

  /* Titel */
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = C.text;
  ctx.font = "700 40px 'Segoe UI', Arial, sans-serif";
  ctx.fillText("Amount of Steam games over time", W / 2, 58);

  /* Tijdcursor */
  const tCur = st.tMin + p * (st.tMax - st.tMin);
  const idx = upperBound(st.relTimes, tCur);   // # releases met tijd <= cursor
  const curCount = idx > 0 ? st.relCounts[idx - 1] : 0;

  /* Y-grid + labels */
  const yStep = niceStep(st.ymax / 8);
  ctx.font = axisFont();
  ctx.textAlign = "right";
  for (let v = 0; v <= st.ymax; v += yStep) {
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x0, Y(v));
    ctx.lineTo(x0 + pw, Y(v));
    ctx.stroke();
    ctx.fillStyle = axis.color;
    ctx.fillText(fmtInt(v), x0 - 14, Y(v) + 7);
  }

  /* X-grid + jaarlabels (elke 2 jaar) */
  const yStart = new Date(st.tMin).getUTCFullYear();
  const yEnd = new Date(st.tMax).getUTCFullYear();
  ctx.font = axisFont();
  ctx.textAlign = "center";
  for (let yr = Math.ceil(yStart / 2) * 2; yr <= yEnd; yr += 2) {
    const x = X(Date.UTC(yr, 0, 1));
    ctx.strokeStyle = C.grid;
    ctx.beginPath();
    ctx.moveTo(x, y0);
    ctx.lineTo(x, y0 + ph);
    ctx.stroke();
    ctx.fillStyle = axis.color;
    ctx.fillText(String(yr), x, y0 + ph + 30);
  }

  /* As-kader */
  ctx.strokeStyle = C.axis;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x0, y0); ctx.lineTo(x0, y0 + ph);
  ctx.lineTo(x0 + pw, y0 + ph);
  ctx.stroke();

  /* Lijn + oppervlak t/m de cursor */
  if (idx > 0) {
    // oppervlak
    ctx.beginPath();
    ctx.moveTo(x0, Y(0));
    for (let i = 0; i < idx; i++) {
      ctx.lineTo(X(st.relTimes[i]), Y(st.relCounts[i]));
    }
    ctx.lineTo(X(tCur), Y(curCount));
    ctx.lineTo(X(tCur), Y(0));
    ctx.closePath();
    ctx.fillStyle = withAlpha(chartColors.timeline.line, 0.22);
    ctx.fill();

    // lijn
    ctx.beginPath();
    ctx.moveTo(x0, Y(0));
    for (let i = 0; i < idx; i++) {
      ctx.lineTo(X(st.relTimes[i]), Y(st.relCounts[i]));
    }
    ctx.lineTo(X(tCur), Y(curCount));
    ctx.strokeStyle = chartColors.timeline.line;
    ctx.lineWidth = 4;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
  }

  /* Verticale huidige-tijd-lijn */
  ctx.strokeStyle = "rgba(255,255,255,0.16)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(X(tCur), Y(0));
  ctx.lineTo(X(tCur), Y(0) - ph);
  ctx.stroke();

  /* Kop-stip */
  ctx.beginPath();
  ctx.arc(X(tCur), Y(curCount), 9, 0, Math.PI * 2);
  ctx.fillStyle = chartColors.timeline.accent;
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = C.bg;
  ctx.stroke();

  /* Grote teller + datum (in de kop boven de plot) */
  ctx.textAlign = "left";
  ctx.fillStyle = chartColors.timeline.line;
  ctx.font = "800 88px 'Segoe UI', Arial, sans-serif";
  ctx.fillText(fmtInt(curCount), x0, 168);
  ctx.font = "500 35px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("Games released up to " + fmtMonthYear(tCur), x0, 212);
}

/* =====================================================================
 * Weekday-grafiek (statisch)
 * ===================================================================== */
function drawWeekday() {
  const cv = el.weekday;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  const M = { l: 150, r: 60, t: 130, b: 105 };
  const x0 = M.l, y0 = M.t;
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const vals = state.weekdayCounts;
  const total = vals.reduce((a, b) => a + b, 0);
  const maxV = Math.max(...vals, 1);
  const yMax = maxV * 1.25;
  const Y = (v) => y0 + ph - (v / yMax) * ph;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, W, H);

  /* Titel */
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = C.text;
  ctx.font = "700 40px 'Segoe UI', Arial, sans-serif";
  ctx.fillText("Games released per weekday", W / 2, 66);

  /* Y-as-label "Amount" (geroteerd) - verbergbaar met de Y-label-toggle */
  if (axis.yLabel) {
    ctx.save();
    ctx.translate(52, y0 + ph / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = axis.color;
    ctx.font = "700 30px 'Segoe UI', Arial, sans-serif";
    ctx.fillText("Amount", 0, 0);
    ctx.restore();
  }

  /* Y-grid + ticklabels (de aantallen staan in de y-as) */
  const yStep = niceStep(yMax / 6);
  ctx.font = axisFont();
  ctx.textAlign = "right";
  ctx.textBaseline = "alphabetic";
  for (let v = 0; v <= yMax; v += yStep) {
    ctx.strokeStyle = C.grid;
    ctx.beginPath();
    ctx.moveTo(x0, Y(v));
    ctx.lineTo(x0 + pw, Y(v));
    ctx.stroke();
    ctx.fillStyle = axis.color;
    ctx.fillText(fmtInt(v), x0 - 14, Y(v) + 7);
  }

  /* As-kader */
  ctx.strokeStyle = C.axis;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x0, y0 + ph);
  ctx.lineTo(x0 + pw, y0 + ph);
  ctx.stroke();

  /* Bars (all the same color = chartColors.weekday.color) + only the
   * percentage above each bar */
  const slot = pw / 7;
  const barW = slot * 0.56;
  ctx.textAlign = "center";
  for (let i = 0; i < 7; i++) {
    const cx = x0 + slot * (i + 0.5);
    const v = vals[i];
    const pct = total ? (100 * v) / total : 0;
    // bar
    ctx.fillStyle = chartColors.weekday.color;
    ctx.fillRect(cx - barW / 2, Y(v), barW, y0 + ph - Y(v));
    // percentage boven de staaf
    ctx.fillStyle = C.text;
    ctx.font = "600 24px 'Segoe UI', Arial, sans-serif";
    ctx.fillText(pct.toFixed(1) + "%", cx, Y(v) - 14);
  }

  /* Weekday-labels onder de as */
  ctx.fillStyle = axis.color;
  ctx.font = axisFont();
  ctx.textAlign = "center";
  for (let i = 0; i < 7; i++) {
    ctx.fillText(DAY_LABELS[i], x0 + slot * (i + 0.5), y0 + ph + 38);
  }
}

/* =====================================================================
 * Afspelen
 * ===================================================================== */
function setPlayUI() {
  el.playBtn.textContent = state.playing ? "⏸ Pause" : "▶ Play";
}

function loop(ts) {
  if (state.ready && state.playing) {
    if (!state.lastTs) state.lastTs = ts;
    const dt = ts - state.lastTs;
    state.elapsed += dt;
    const p = (state.elapsed % state.durMs) / state.durMs;
    state.pauseP = p;
    drawTimeline(p);
  }
  state.lastTs = ts;
  requestAnimationFrame(loop);
}

function redrawCharts() {
  if (!state.ready) return;
  if (!state.playing) drawTimeline(state.pauseP);
  drawWeekday();
}

/* Koppel een as-config-regel (grootte/vet/kleur/y-label) aan de gedeelde
 * axis-instellingen. Zo werkt dezelfde config boven de tijdlijn én boven
 * de weekday-grafiek (en elke toekomstige grafiek die hem toevoegt). */
function bindAxisGroup(g) {
  g.size.addEventListener("input", () => {
    if (state.recording) return;
    axis.size = +g.size.value;
    syncAxisUI();
    redrawCharts();
  });
  g.bold.addEventListener("change", () => {
    if (state.recording) return;
    axis.bold = g.bold.checked;
    syncAxisUI();
    redrawCharts();
  });
  g.color.addEventListener("input", () => {
    if (state.recording) return;
    axis.color = g.color.value;
    syncAxisUI();
    redrawCharts();
  });
  g.ylabel.addEventListener("change", () => {
    if (state.recording) return;
    axis.yLabel = g.ylabel.checked;
    syncAxisUI();
    redrawCharts();
  });
}

/* Spiegel de gedeelde axis-instellingen naar alle regelaars op de pagina. */
function syncAxisUI() {
  el.axisSize.value = axis.size;
  el.wdAxisSize.value = axis.size;
  el.axisSizeVal.value = axis.size;
  el.wdAxisSizeVal.value = axis.size;
  el.axisBold.checked = axis.bold;
  el.wdBold.checked = axis.bold;
  el.axisColor.value = axis.color;
  el.wdColor.value = axis.color;
  el.axisYLabel.checked = axis.yLabel;
  el.wdYLabel.checked = axis.yLabel;
}

function bindControls() {
  el.playBtn.addEventListener("click", () => {
    if (state.recording || !state.ready) return;
    state.playing = !state.playing;
    state.lastTs = 0;
    setPlayUI();
  });
  el.restartBtn.addEventListener("click", () => {
    if (state.recording || !state.ready) return;
    state.elapsed = 0;
    state.pauseP = 0;
    if (!state.playing) drawTimeline(0);
    setPlayUI();
  });
  el.durSlider.addEventListener("input", () => {
    if (state.recording) return;
    state.durMs = +el.durSlider.value * 1000;
    el.durVal.value = el.durSlider.value;
    if (!state.playing) drawTimeline(state.pauseP);
  });
  el.lineColor.addEventListener("input", () => {
    if (state.recording) return;
    chartColors.timeline.line = el.lineColor.value;
    redrawCharts();
  });
  el.accentColor.addEventListener("input", () => {
    if (state.recording) return;
    chartColors.timeline.accent = el.accentColor.value;
    redrawCharts();
  });
  el.weekdayColor.addEventListener("input", () => {
    if (state.recording) return;
    chartColors.weekday.color = el.weekdayColor.value;
    drawWeekday();
  });
  bindAxisGroup({
    size: el.axisSize, sizeVal: el.axisSizeVal,
    bold: el.axisBold, color: el.axisColor, ylabel: el.axisYLabel,
  });
  bindAxisGroup({
    size: el.wdAxisSize, sizeVal: el.wdAxisSizeVal,
    bold: el.wdBold, color: el.wdColor, ylabel: el.wdYLabel,
  });
  syncAxisUI();
  el.exportBtn.addEventListener("click", () => {
    if (state.recording) stopExport(true); // knop = annuleren tijdens opname
    else startExport();
  });
}

function setControlsDisabled(disabled) {
  el.playBtn.disabled = disabled;
  el.restartBtn.disabled = disabled;
  el.durSlider.disabled = disabled;
  el.lineColor.disabled = disabled;
  el.accentColor.disabled = disabled;
  el.weekdayColor.disabled = disabled;
  el.axisSize.disabled = disabled;
  el.axisBold.disabled = disabled;
  el.axisColor.disabled = disabled;
  el.axisYLabel.disabled = disabled;
  el.wdAxisSize.disabled = disabled;
  el.wdBold.disabled = disabled;
  el.wdColor.disabled = disabled;
  el.wdYLabel.disabled = disabled;
}

/* =====================================================================
 * MP4-export (MediaRecorder; geen ffmpeg nodig)
 * ===================================================================== */
function pickMime() {
  if (!window.MediaRecorder || !window.MediaRecorder.isTypeSupported) return "";
  const candidates = [
    "video/mp4;codecs=avc1.42E01E",
    "video/mp4;codecs=vp9",
    "video/mp4",
    "video/webm;codecs=vp9",
    "video/webm",
  ];
  for (const m of candidates) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}

function startExport() {
  if (state.recording || !state.ready) return;
  const cv = el.timeline;
  let stream;
  try { stream = cv.captureStream(60); }
  catch (e) {
    el.exportStatus.textContent = "❌ captureStream is not supported in this browser.";
    el.exportStatus.hidden = false;
    return;
  }
  const mime = pickMime();
  if (!mime) {
    el.exportStatus.textContent = "❌ MediaRecorder is not supported in this browser.";
    el.exportStatus.hidden = false;
    return;
  }

  state.recording = true;
  state.cancelExport = false;
  state.playing = true;      // zorg dat de animatie draait tijdens opname
  state.elapsed = 0;
  state.lastTs = 0;
  state.chunks = [];
  setPlayUI();

  try {
    state.mediaRec = new MediaRecorder(stream, {
      mimeType: mime,
      videoBitsPerSecond: 12_000_000,
    });
  } catch (e) {
    state.mediaRec = new MediaRecorder(stream);
  }
  const rec = state.mediaRec;
  rec.ondataavailable = (ev) => {
    if (ev.data && ev.data.size) state.chunks.push(ev.data);
  };
  rec.onstop = finalizeExport;

  // UI: opname-modus
  el.recBadge.hidden = false;
  el.exportBtn.textContent = "■ Stop (cancel MP4)";
  el.exportBtn.classList.add("recording");
  el.exportStatus.hidden = false;
  el.exportStatus.textContent =
    `⏺ Recording… one full cycle (${el.durSlider.value} s) — keep this tab visible.`;
  setControlsDisabled(true);

  rec.start(250);
  state.exportTimer = setTimeout(() => stopExport(false), state.durMs + 500);
}

function stopExport(abort) {
  if (!state.recording) return;
  state.cancelExport = abort;
  clearTimeout(state.exportTimer);
  state.exportTimer = null;
  if (state.mediaRec && state.mediaRec.state !== "inactive") {
    try { state.mediaRec.stop(); } catch (e) { /* negeren */ }
  }
  // finalizeExport wordt via rec.onstop aangeroepen.
}

function finalizeExport() {
  const wasCancelled = state.cancelExport;
  const type = (state.mediaRec && state.mediaRec.mimeType) || "video/mp4";

  state.recording = false;
  state.cancelExport = false;
  state.playing = false;
  state.mediaRec = null;
  state.lastTs = 0;

  // UI terugzetten
  el.recBadge.hidden = true;
  el.exportBtn.textContent = "⬇ Export MP4";
  el.exportBtn.classList.remove("recording");
  setControlsDisabled(false);
  setPlayUI();

  if (wasCancelled) {
    el.exportStatus.textContent = "Export cancelled.";
    state.chunks = [];
    return;
  }

  const blob = new Blob(state.chunks, { type });
  state.chunks = [];
  const ext = type.indexOf("mp4") >= 0 ? "mp4" : "webm";
  const name = "steam_games_released_timeline." + ext;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  el.exportStatus.textContent = `✅ Downloaded: ${name} (${fmtInt(Math.round(blob.size / 1024))} kB).`;
}

/* =====================================================================
 * Start
 * ===================================================================== */
requestAnimationFrame(loop);
bindControls();
loadData();
