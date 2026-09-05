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
 * Vast: donker thema. Aanpasbaar via de kleurkiezers op de pagina:
 * colors.line (lijn / oppervlak / balken) en colors.accent (stip / drukste dag). */
const C = {
  bg: "#0f1a26",                     // canvas-achtergrond
  grid: "rgba(199,213,224,0.08)",    // rasterlijnen
  axis: "rgba(199,213,224,0.30)",    // as-kader
  text: "#e6eef5",                   // titels / hoofdkleur
  muted: "#8ba2b6",                  // bijschriften / ticklabels
};

let colors = { line: "#66c0f4", accent: "#e74c3c" };

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
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

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
function fmtDate(ms) {
  const d = new Date(ms);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  return `${dd} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
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
    state.tMin = Date.UTC(state.firstYear - 1, 6, 1);      // midden van jaar ervoor
    state.tMax = Date.UTC(state.lastYear + 1, 5, 1);       // midden van jaar erna
    state.ymax = Math.ceil(counts[counts.length - 1] / 100) * 100;

    // Footers / koptekst.
    el.dataInfo.textContent =
      `${fmtInt(state.gamesTotal)} games uit games.csv · ` +
      `${fmtInt(state.withDate)} met releasedatum`;
    el.timelineFoot.textContent =
      `${fmtInt(state.withDate)} games met bekende releasedatum ` +
      `(${fmtInt(state.gamesTotal)} totaal in games.csv) · ` +
      `${state.relTimes.length} unieke releasedatums · ` +
      `animatie van ${state.firstYear} tot ${state.lastYear}`;
    const dowTotal = state.weekdayCounts.reduce((a, b) => a + b, 0);
    el.weekdayFoot.textContent =
      `Gebaseerd op ${fmtInt(dowTotal)} releases · dag-van-week uit date.csv ` +
      `(ISO, maandag=1)${state.weekdayFallback
        ? ` · ${state.weekdayFallback} releases vóór 2003 direct uit de datum berekend`
        : ""}`;

    drawWeekday();
    state.pauseP = 0;
    drawTimeline(0);
    state.ready = true;
  } catch (err) {
    el.dataInfo.textContent = "❌ laden mislukt";
    const box = document.createElement("div");
    box.className = "error-box";
    box.textContent =
      "Kon data/games.csv of data/date.csv niet laden (" + err.message + "). " +
      "Open deze pagina via een lokale webserver (zie instructie onderaan de " +
      "pagina) - fetch werkt niet vanaf file://.";
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
  ctx.font = "500 21px 'Segoe UI', Arial, sans-serif";
  ctx.textAlign = "right";
  for (let v = 0; v <= st.ymax; v += yStep) {
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x0, Y(v));
    ctx.lineTo(x0 + pw, Y(v));
    ctx.stroke();
    ctx.fillStyle = C.muted;
    ctx.fillText(fmtInt(v), x0 - 14, Y(v) + 7);
  }

  /* X-grid + jaarlabels (elke 2 jaar) */
  const yStart = new Date(st.tMin).getUTCFullYear();
  const yEnd = new Date(st.tMax).getUTCFullYear();
  ctx.textAlign = "center";
  for (let yr = Math.ceil(yStart / 2) * 2; yr <= yEnd; yr += 2) {
    const x = X(Date.UTC(yr, 0, 1));
    ctx.strokeStyle = C.grid;
    ctx.beginPath();
    ctx.moveTo(x, y0);
    ctx.lineTo(x, y0 + ph);
    ctx.stroke();
    ctx.fillStyle = C.muted;
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
    ctx.fillStyle = withAlpha(colors.line, 0.22);
    ctx.fill();

    // lijn
    ctx.beginPath();
    ctx.moveTo(x0, Y(0));
    for (let i = 0; i < idx; i++) {
      ctx.lineTo(X(st.relTimes[i]), Y(st.relCounts[i]));
    }
    ctx.lineTo(X(tCur), Y(curCount));
    ctx.strokeStyle = colors.line;
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
  ctx.fillStyle = colors.accent;
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = C.bg;
  ctx.stroke();

  /* Grote teller + datum (in de kop boven de plot) */
  ctx.textAlign = "left";
  ctx.fillStyle = colors.line;
  ctx.font = "800 88px 'Segoe UI', Arial, sans-serif";
  ctx.fillText(fmtInt(curCount), x0, 168);
  ctx.font = "500 25px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("games released up to " + fmtDate(tCur), x0, 212);
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

  /* Y-as-label "Amount" (geroteerd) */
  ctx.save();
  ctx.translate(52, y0 + ph / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = C.muted;
  ctx.font = "700 30px 'Segoe UI', Arial, sans-serif";
  ctx.fillText("Amount", 0, 0);
  ctx.restore();

  /* Y-grid + ticklabels (de aantallen staan in de y-as) */
  const yStep = niceStep(yMax / 6);
  ctx.font = "500 20px 'Segoe UI', Arial, sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "alphabetic";
  for (let v = 0; v <= yMax; v += yStep) {
    ctx.strokeStyle = C.grid;
    ctx.beginPath();
    ctx.moveTo(x0, Y(v));
    ctx.lineTo(x0 + pw, Y(v));
    ctx.stroke();
    ctx.fillStyle = C.muted;
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

  /* Staafjes + alleen het percentage boven elke staaf */
  const slot = pw / 7;
  const barW = slot * 0.56;
  const maxIdx = vals.indexOf(maxV);
  ctx.textAlign = "center";
  for (let i = 0; i < 7; i++) {
    const cx = x0 + slot * (i + 0.5);
    const v = vals[i];
    const pct = total ? (100 * v) / total : 0;
    // staaf
    ctx.fillStyle = i === maxIdx ? colors.accent : colors.line;
    ctx.fillRect(cx - barW / 2, Y(v), barW, y0 + ph - Y(v));
    // percentage boven de staaf
    ctx.fillStyle = C.text;
    ctx.font = "600 24px 'Segoe UI', Arial, sans-serif";
    ctx.fillText(pct.toFixed(1) + "%", cx, Y(v) - 14);
  }

  /* Weekday-labels onder de as */
  ctx.fillStyle = C.muted;
  ctx.font = "500 20px 'Segoe UI', Arial, sans-serif";
  ctx.textAlign = "center";
  for (let i = 0; i < 7; i++) {
    ctx.fillText(DAY_LABELS[i], x0 + slot * (i + 0.5), y0 + ph + 38);
  }
}

/* =====================================================================
 * Afspelen
 * ===================================================================== */
function setPlayUI() {
  el.playBtn.textContent = state.playing ? "⏸ Pauze" : "▶ Afspelen";
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
    colors.line = el.lineColor.value;
    if (!state.playing) drawTimeline(state.pauseP);
    drawWeekday();
  });
  el.accentColor.addEventListener("input", () => {
    if (state.recording) return;
    colors.accent = el.accentColor.value;
    if (!state.playing) drawTimeline(state.pauseP);
    drawWeekday();
  });
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
    el.exportStatus.textContent = "❌ captureStream niet ondersteund in deze browser.";
    el.exportStatus.hidden = false;
    return;
  }
  const mime = pickMime();
  if (!mime) {
    el.exportStatus.textContent = "❌ MediaRecorder niet ondersteund in deze browser.";
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
  el.exportBtn.textContent = "■ Stop (annuleer MP4)";
  el.exportBtn.classList.add("recording");
  el.exportStatus.hidden = false;
  el.exportStatus.textContent =
    `⏺ Opnemen… één volledige cyclus (${el.durSlider.value} s) — houd dit tabblad zichtbaar.`;
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
  el.exportBtn.textContent = "⬇ Exporteer MP4";
  el.exportBtn.classList.remove("recording");
  setControlsDisabled(false);
  setPlayUI();

  if (wasCancelled) {
    el.exportStatus.textContent = "Export geannuleerd.";
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
  el.exportStatus.textContent = `✅ Gedownload: ${name} (${fmtInt(Math.round(blob.size / 1024))} kB).`;
}

/* =====================================================================
 * Start
 * ===================================================================== */
requestAnimationFrame(loop);
bindControls();
loadData();
