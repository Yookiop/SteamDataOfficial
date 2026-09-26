/* =====================================================================
 * Steam video charts (viz/video.html) - vijf losse video-visuals op
 * dezelfde CSV's als de hoofdpagina (games.csv + game_genres.csv):
 *
 *   1) race  - bar chart race: genres racen om de meeste games (cumulatief).
 *   2) heat  - release-kalender 2003->nu (GitHub-stijl; elke dag een blokje,
 *              de sweep vult chronologisch; teller loopt mee).
 *   3) f2p   - free-to-play vs paid: cirkeldiagram (paid vs free, samen
 *              100%) + klok met echte wijzerverhouding (minutenwijzer 12x
 *              zo snel als de uurwijzer) en maand-jaar eronder.
 *   4) indie - "Indie takeover": 100%-ribbon met het Indie-aandeel per jaar.
 *   5) players - spelersverdeling: cirkeldiagram van 5 buckets (0-10 /
 *                10-100 / 100-1k / 1k-10k / >10k gemiddelde spelers) dat
 *                cumulatief t/m de datum groeit, met legenda rechts.
 *
 * Net als de hoofdpagina: canvas 1600x900, Play/Restart/Duur-slider,
 * MP4-export (MediaRecorder) en 10 s eind-hold voordat de cyclus opnieuw
 * begint. De race houdt de bar-posities bij in `state.racePos` (per-frame
 * easing; reset bij Restart/export/chart-wissel).
 * ===================================================================== */

"use strict";

/* ---------- Thema ---------- */
const C = {
  bg: "#0f1a26",
  grid: "rgba(199,213,224,0.08)",
  axis: "rgba(199,213,224,0.30)",
  text: "#e6eef5",
  muted: "#8ba2b6",
  accent: "#66c0f4",
};

const MONTHS_FULL = ["January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November", "December"];

/* Vaste kleuren per genre (race; "Indie" is ook de kleur van chart 4). */
const GENRE_COLORS = {
  "Indie": "#7ee081",
  "Casual": "#f4a259",
  "Action": "#e35d6a",
  "Adventure": "#b48cf2",
  "Simulation": "#5bc8d8",
  "Strategy": "#f2d75e",
  "RPG": "#e79ecb",
  "Free To Play": "#66c0f4",
  "Early Access": "#c98b5e",
  "Sports": "#8fd0c5",
  "Racing": "#c9cdd4",
  "Massively Multiplayer": "#a0a8ff",
};

const FREE_COLOR = "#49c5b6";
const PAID_COLOR = "#3d7ea6";
const INDIE_COLOR = "#7ee081";
const REST_COLOR = "#31465c";

const CHARTS = {
  race:  { title: "Genre race",           dur: 60, slug: "genre_race" },
  heat:  { title: "Release calendar",     dur: 45, slug: "release_calendar" },
  f2p:   { title: "Free-to-play vs paid", dur: 30, slug: "f2p_vs_paid" },
  indie: { title: "Indie takeover",       dur: 30, slug: "indie_takeover" },
  players: { title: "Player count",       dur: 45, slug: "player_count" },
};

const HINTS = {
  race: "Bar lengths are relative to the current #1; bars swap position as genres overtake each other (a game can carry several genres).",
  heat: "Every square is one day; the sweep fills the calendar chronologically and the counter follows. Brighter = more releases that day.",
  f2p: "Donut = cumulative paid vs free share of all releases (together 100%); the clock hands run like a real clock (minute hand 12x the hour hand) as the timeline advances, with the month-year below it.",
  indie: "Each column is one year; the green part is the Indie share of that year's releases (Steam's 'Indie' genre tag).",
  players: "Pie of average players per game (mean over all snapshots), growing up to the date; the legend has the exact counts. Tiny slices get a minimum line width so they stay visible.",
};

/* Pauze op het einde: 10 s de eindstand vasthouden voordat de cyclus opnieuw begint. */
const END_PAUSE_MS = 10000;

const el = {
  canvas: document.getElementById("canvas"),
  chips: Array.from(document.querySelectorAll("#chips .btn")),
  chartTitle: document.getElementById("chartTitle"),
  playBtn: document.getElementById("playBtn"),
  restartBtn: document.getElementById("restartBtn"),
  durSlider: document.getElementById("durSlider"),
  durVal: document.getElementById("durVal"),
  exportBtn: document.getElementById("exportBtn"),
  exportStatus: document.getElementById("exportStatus"),
  recBadge: document.getElementById("recBadge"),
  dataInfo: document.getElementById("dataInfo"),
  hint: document.getElementById("hint"),
};

const state = {
  ready: false,
  chart: "race",
  gamesTotal: 0,
  t0: 0, t1: 0,                 // tijdsbereik (eerste release -> vandaag)
  years: [],                    // [{year, y0, y1, all:[t], free:[t], indie:[t]}]
  raceGenres: [],               // top 12: [{name, color, times:[t]}]
  racePos: null,                // Map(genre-naam -> y) tijdens de race
  era: null,                    // {days, counts, cum, cells, maxDay}
  heatBlocks: null,             // per jaar: {bx, by}
  players: null,                // {buckets:[{label,color,times}], snapT, excluded}
  // playback
  playing: false,
  elapsed: 0,
  durMs: 60000,
  pauseP: 0,
  lastTs: 0,
  // export
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

/* 'yyyy-mm-dd' -> ms sinds epoch (UTC-middernacht). */
function dateToMs(s) {
  const p = s.split("-");
  return Date.UTC(+p[0], +p[1] - 1, +p[2]);
}

function fmtMonthYear(ms) {
  const d = new Date(ms);
  return `${MONTHS_FULL[d.getUTCMonth()]}-${d.getUTCFullYear()}`;
}

/* Eenvoudige RFC4180-achtige CSV-parser (zoals de hoofdpagina). */
function parseCSV(text) {
  text = text.replace(/^\uFEFF/, "");
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

/* # elementen <= v in een oplopend gesorteerde array. */
function upperBound(arr, v) {
  let lo = 0, hi = arr.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (arr[m] <= v) lo = m + 1; else hi = m;
  }
  return lo;
}

/* Afgeronde rechthoek (boogjes i.p.v. ctx.roundRect, overal bruikbaar). */
function rrect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function hexRGB(hex) {
  return [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
}
function mixColor(a, b, t) {
  const A = hexRGB(a), B = hexRGB(b);
  const m = (i) => Math.round(A[i] + (B[i] - A[i]) * t);
  return `rgb(${m(0)},${m(1)},${m(2)})`;
}
function rampColor(stops, t) {
  t = Math.max(0, Math.min(1, t));
  const n = stops.length - 1;
  const f = t * n;
  const i = Math.min(Math.floor(f), n - 1);
  return mixColor(stops[i], stops[i + 1], f - i);
}

/* Kleur voor de kalender-heatmap: sqrt-schaal zodat kleine aantallen al
 * zichtbaar zijn (max = drukste dag). */
const HEAT_STOPS = ["#2b5a7d", "#3f8fc4", "#8fd0ff", "#ecf9ff"];
function heatColor(n) { return rampColor(HEAT_STOPS, Math.sqrt(n / state.era.maxDay)); }

/* =====================================================================
 * Data laden + voorbereiden
 * ===================================================================== */
async function loadData() {
  try {
    const [gamesCsv, genresCsv] = await Promise.all([
      fetch("../data/games.csv").then((r) => {
        if (!r.ok) throw new Error(`games.csv: HTTP ${r.status}`);
        return r.text();
      }),
      fetch("../data/game_genres.csv").then((r) => {
        if (!r.ok) throw new Error(`game_genres.csv: HTTP ${r.status}`);
        return r.text();
      }),
    ]);

    const gameRows = parseCSV(gamesCsv);
    const genreRows = parseCSV(genresCsv);

    // genres per appid (een game kan meerdere genres hebben)
    const genresOf = new Map();
    for (const r of genreRows) {
      if (!r.appid || !r.genres) continue;
      let list = genresOf.get(r.appid);
      if (!list) genresOf.set(r.appid, (list = []));
      list.push(r.genres);
    }

    const now = new Date();
    const todayMs = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    state.t1 = todayMs;

    // Jaar-buckets 2003..dit jaar
    const firstYear = 2003;
    const lastYear = now.getFullYear();
    const years = [];
    for (let y = firstYear; y <= lastYear; y++) {
      years.push({ year: y, y0: Date.UTC(y, 0, 1), y1: Date.UTC(y + 1, 0, 1), all: [], free: [], indie: [] });
    }
    const yearIndex = (t) => Math.min(years.length - 1, Math.max(0, new Date(t).getUTCFullYear() - firstYear));

    const raceMap = new Map();     // genre -> [t...]
    const dayMap = new Map();      // dag-ms -> aantal
    const snapT = [];              // releasedatums van games MET snapshots (%-noemer)
    const playerBuckets = [        // 5 buckets op gemiddelde spelers
      { label: "0–10", color: "#e35d6a", times: [] },
      { label: "10–100", color: "#f4a259", times: [] },
      { label: "100–1,000", color: "#f2d75e", times: [] },
      { label: "1,000–10,000", color: "#7ee081", times: [] },
      { label: ">10,000", color: "#66c0f4", times: [] },
    ];
    let t0 = Infinity;
    let gamesTotal = 0;

    for (const g of gameRows) {
      const d = g.release_date_fmt;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d || "")) continue;
      const t = dateToMs(d);
      if (t > todayMs) continue;               // einddatum-filter (zoals de hoofdpagina)
      gamesTotal++;
      const ap = parseFloat(g.average_players);
      if (!isNaN(ap)) {
        snapT.push(t);
        const bi = ap <= 10 ? 0 : ap <= 100 ? 1 : ap <= 1000 ? 2 : ap <= 10000 ? 3 : 4;
        playerBuckets[bi].times.push(t);
      }
      if (t < t0) t0 = t;
      const Y = years[yearIndex(t)];
      Y.all.push(t);
      if (g.is_free === "True") Y.free.push(t);
      const gs = genresOf.get(g.appid);
      if (gs) {
        for (const name of gs) {
          if (name === "Indie") Y.indie.push(t);
          let arr = raceMap.get(name);
          if (!arr) raceMap.set(name, (arr = []));
          arr.push(t);
        }
      }
      dayMap.set(t, (dayMap.get(t) || 0) + 1);
    }

    state.gamesTotal = gamesTotal;
    state.t0 = t0 === Infinity ? todayMs : t0;

    // Per-jaar arrays SORTEREN: de CSV staat op appid (niet op datum) maar
    // upperBound vereist oplopende arrays. Zonder sort klopten de
    // tussentijdse tellingen niet (share >100% → lijn schoot uit de grafiek);
    // alleen de eindstand was goed omdat cap dan alles dekt.
    for (const Y of years) {
      Y.all.sort((a, b) => a - b);
      Y.free.sort((a, b) => a - b);
      Y.indie.sort((a, b) => a - b);
    }

    // race: top 12 genres op totaal aantal games
    state.raceGenres = [...raceMap.entries()]
      .map(([name, times]) => ({ name, color: GENRE_COLORS[name] || "#8ba2b6", times: times.sort((a, b) => a - b) }))
      .sort((a, b) => b.times.length - a.times.length)
      .slice(0, 12);

    // heat: era-dagen (eerste release t/m vandaag) + pixelcellen per dag
    state.heatBlocks = heatLayout(firstYear, lastYear);
    const days = [], counts = [], cum = [], cells = [];
    let acc = 0;
    for (let ms = state.t0; ms <= todayMs; ms += 86400000) {
      const n = dayMap.get(ms) || 0;
      days.push(ms); counts.push(n); acc += n; cum.push(acc);
      const d = new Date(ms), y = d.getUTCFullYear();
      const blk = state.heatBlocks[y];
      const doy = Math.round((ms - Date.UTC(y, 0, 1)) / 86400000);
      const f = (new Date(Date.UTC(y, 0, 1)).getUTCDay() + 6) % 7;   // ma=0
      const idx = doy + f;
      cells.push([blk.bx + Math.floor(idx / 7) * 8, blk.by + (idx % 7) * 8]);
    }
    let maxDay = 1;
    for (const n of counts) if (n > maxDay) maxDay = n;
    state.era = { days, counts, cum, cells, maxDay };

    /* players: bucket-arrays sorteren (upperBound vereist oplopende arrays) */
    snapT.sort((a, b) => a - b);
    for (const bk of playerBuckets) bk.times.sort((a, b) => a - b);
    state.players = { buckets: playerBuckets, snapT, excluded: gamesTotal - snapT.length };

    state.years = years;
    el.dataInfo.textContent = `${fmtInt(gamesTotal)} games · top ${state.raceGenres.length} genres · ${days.length} days · ${fmtInt(snapT.length)} with player data`;

    state.ready = true;
    drawFrame(0, 0);
  } catch (err) {
    el.dataInfo.textContent = "❌ Failed to load";
    const box = document.createElement("div");
    box.className = "error-box";
    box.textContent =
      "Could not load data/games.csv or data/game_genres.csv (" + err.message + "). " +
      "Open this page through a local web server - fetch does not work from file://.";
    document.querySelector("main").prepend(box);
    console.error(err);
  }
}

/* Kalender-layout: 3 kolommen x 8 jaar, per jaar een GitHub-raster
 * (54 weken x 7 dagen, cel 6px + 2px tussenruimte). */
function heatLayout(firstYear, lastYear) {
  const out = {};
  const x0 = 20, y0 = 210, labelW = 70, blockW = 54 * 8, blockH = 56, colGap = 24, rowGap = 12;
  for (let y = firstYear, i = 0; y <= lastYear; y++, i++) {
    const col = Math.floor(i / 8), row = i % 8;
    out[y] = { bx: x0 + col * (labelW + blockW + colGap) + labelW, by: y0 + row * (blockH + rowGap) };
  }
  return out;
}

/* =====================================================================
 * Tekenhulpjes
 * ===================================================================== */
function clearCanvas(ctx, W, H) {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, W, H);
}

function drawHeader(ctx, title, sub) {
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = C.text;
  ctx.font = "700 40px 'Segoe UI', Arial, sans-serif";
  ctx.fillText(title, 40, 56);
  ctx.font = "400 23px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText(sub, 40, 92);
}

function drawNote(ctx, text) {
  ctx.textAlign = "left";
  ctx.font = "400 19px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText(text, 40, 878);
}

function statBlock(ctx, x, label, value, color) {
  ctx.textAlign = "right";
  ctx.font = "600 20px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText(label, x, 76);
  ctx.font = "800 46px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = color;
  ctx.fillText(value, x, 126);
}

/* =====================================================================
 * 1) Genre race
 * ===================================================================== */
function drawRace(p, dt) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  clearCanvas(ctx, W, H);
  drawHeader(ctx, "Steam games per genre — cumulative",
    "each bar = released games carrying that genre tag (a game can carry several)");

  const t = state.t0 + p * (state.t1 - state.t0);
  const list = state.raceGenres;
  const vals = list.map((g) => upperBound(g.times, t));
  const order = list.map((g, i) => i)
    .sort((a, b) => vals[b] - vals[a] || list[a].name.localeCompare(list[b].name));
  const vmax = Math.max(1, vals[order[0]]);

  const TOP = 150, BOT = 800, rowH = (BOT - TOP) / list.length, barH = 40;
  const X0 = 350, MINBAR = 8, MAXLEN = 1080;

  /* soepele positie-wissels: per frame naar de doelpositie toe bewegen */
  if (!state.racePos) state.racePos = new Map();
  const k = dt > 0 ? 1 - Math.exp(-dt / 200) : 0;
  order.forEach((gi, rank) => {
    const g = list[gi], target = TOP + rank * rowH;
    if (!state.racePos.has(g.name)) state.racePos.set(g.name, target);
    else if (k > 0) state.racePos.set(g.name, state.racePos.get(g.name) + (target - state.racePos.get(g.name)) * k);
  });

  const items = list.map((g, i) => ({ g, v: vals[i], y: state.racePos.get(g.name) }))
    .sort((a, b) => a.y - b.y);

  for (const it of items) {
    const y = it.y + (rowH - barH) / 2;
    /* genre-naam (rechts uitgelijnd, vóór de balk) */
    ctx.textAlign = "right";
    ctx.textBaseline = "alphabetic";
    ctx.font = "700 24px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = "#dbe7f0";
    ctx.fillText(it.g.name, 330, y + 28);
    /* balk */
    const len = it.v > 0 ? MINBAR + (it.v / vmax) * (MAXLEN - MINBAR) : 0;
    if (len > 0.5) {
      rrect(ctx, X0, y, len, barH, 9);
      ctx.fillStyle = it.g.color;
      ctx.fill();
    }
    /* waarde */
    const label = fmtInt(it.v);
    ctx.font = "700 28px 'Segoe UI', Arial, sans-serif";
    const tw = ctx.measureText(label).width;
    let tx = X0 + len + 14, align = "left", color = "#e6eef5";
    if (tx + tw > 1556 && len > 80) { align = "right"; tx = X0 + len - 12; color = "#0d141d"; }
    ctx.textAlign = align;
    ctx.fillStyle = color;
    ctx.fillText(label, tx, y + 28);
  }

  /* watermerk rechtsboven: maand + jaar (was: alleen het jaar + een losse
     regel eronder; gebruikersfix 2026-09-27 — die losse regel overlapte het
     waarde-label van de bovenste balk; nu één label op de watermerk-plek).
     Maat streng gemeten: de breedste maand ('September-YYYY' = 1059px @130px)
     liep achter de ondertitel (eindigt op x=821) — 87px laat ~32px marge;
     alpha 0,13 zodat het watermerk zelf beter opvalt. Baseline 127: de
     descender ('p' van September; 20px diep @87px) hing op baseline 150 tot
     y=170 dwars door de bovenste balk (top y=157) — nu eindigt hij op y=147,
     10px boven de balk. */
  ctx.textAlign = "right";
  ctx.font = "800 87px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = "rgba(255,255,255,0.13)";
  ctx.fillText(fmtMonthYear(t), 1562, 127);

  drawNote(ctx, "cumulative · genre tags by Steam · released games only (up to today)");
}

/* =====================================================================
 * 2) Release-kalender (GitHub-stijl heatmap)
 * ===================================================================== */
function drawHeat(p) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  clearCanvas(ctx, W, H);
  drawHeader(ctx, "Steam releases per day — 2003 → 2026",
    "every square is one day; brighter = more releases that day");

  const era = state.era, N = era.days.length;
  const done = Math.floor(p * N);
  const last = done > 0 ? Math.min(done - 1, N - 1) : -1;

  /* cellen: nog niet ge-sweept = donker, ge-sweept = gekleurd */
  for (let i = 0; i < N; i++) {
    const c = era.cells[i];
    let fill;
    if (i < done) fill = era.counts[i] > 0 ? heatColor(era.counts[i]) : "#1d2c3a";
    else fill = "#0e1722";
    ctx.fillStyle = fill;
    ctx.fillRect(c[0], c[1], 6, 6);
  }

  /* actieve cel (witte rand) */
  if (last >= 0) {
    const c = era.cells[last];
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(c[0] - 1.5, c[1] - 1.5, 9, 9);
  }

  /* jaar-labels + actief jaarblok */
  const curYear = last >= 0 ? new Date(era.days[last]).getUTCFullYear() : null;
  for (const key in state.heatBlocks) {
    const blk = state.heatBlocks[key];
    const active = +key === curYear;
    ctx.textAlign = "right";
    ctx.font = active ? "700 22px 'Segoe UI', Arial, sans-serif" : "500 20px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = active ? C.accent : C.muted;
    ctx.fillText(key, blk.bx - 16, blk.by + 36);
    if (active) {
      ctx.strokeStyle = "rgba(102,192,244,0.45)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(blk.bx - 4, blk.by - 4, 54 * 8 + 8, 7 * 8 + 8);
    }
  }

  /* kop rechts: aantal games + jaar-maand (geen dag-details) */
  const cum = last >= 0 ? era.cum[last] : 0;
  ctx.textAlign = "right";
  ctx.font = "800 76px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.accent;
  ctx.fillText(fmtInt(cum), 1562, 108);
  ctx.font = "400 26px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText(fmtMonthYear(last >= 0 ? era.days[last] : era.days[0]), 1562, 146);

  drawNote(ctx, "each square = one day · steam releases 2003 → today · counter follows the sweep");
}

/* =====================================================================
 * 3) Free-to-play vs paid (cirkeldiagram + klok met echte wijzerverhouding)
 * ===================================================================== */
function drawF2P(p) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  clearCanvas(ctx, W, H);
  drawHeader(ctx, "Free-to-play vs paid",
    "cumulative share of all Steam releases (paid vs free) · the clock hands run like a real clock (minute = 12x hour)");

  const t = state.t0 + p * (state.t1 - state.t0);

  /* cumulatieve tellingen t/m t */
  let paidCum = 0, freeCum = 0;
  for (const Y of state.years) {
    if (t < Y.y0) break;
    const cap = Math.min(t, Y.y1 - 1);
    const nAll = upperBound(Y.all, cap);
    const nFree = upperBound(Y.free, cap);
    paidCum += nAll - nFree;
    freeCum += nFree;
  }
  const total = paidCum + freeCum;
  const freeShare = total ? freeCum / total : 0;

  /* ----- donut: % free en % paid (samen 100%) ----- */
  const cx = 560, cy = 490, rOut = 250, rIn = 148;
  const a0 = -Math.PI / 2;                    // start bovenaan
  const gap = 0.022;                          // naad tussen de segmenten
  const freeA = freeShare * Math.PI * 2;

  /* paid-segment (de rest) */
  donutSeg(ctx, cx, cy, rOut, rIn, a0 + freeA + gap / 2, a0 + Math.PI * 2 - gap / 2);
  ctx.fillStyle = PAID_COLOR;
  ctx.fill();
  /* free-segment */
  if (freeA > gap * 1.2) {
    donutSeg(ctx, cx, cy, rOut, rIn, a0 + gap / 2, a0 + freeA - gap / 2);
    ctx.fillStyle = FREE_COLOR;
    ctx.fill();
  }

  /* labels bij de segmenten */
  donutCallout(ctx, cx, cy, rOut, a0 + freeA + (Math.PI * 2 - freeA) / 2,
    "PAID " + (100 * (1 - freeShare)).toFixed(1) + "%", "#9fc9e8");
  donutCallout(ctx, cx, cy, rOut, a0 + freeA / 2,
    "FREE " + (100 * freeShare).toFixed(1) + "%", FREE_COLOR);

  /* midden: totaal aantal games tot nu */
  ctx.textAlign = "center";
  ctx.font = "800 64px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = "#e6eef5";
  ctx.fillText(fmtInt(total), cx, cy + 8);
  ctx.font = "400 23px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("games so far", cx, cy + 46);

  /* ----- klok: wijzerplaat staat stil, de wijzers lopen als een echte klok ----- */
  const ccx = 1240, ccy = 400, cr = 105;
  /* 12 klokuren per cyclus: uurwijzer 1 slag, minutenwijzer 12 slagen
     (12x zo snel) - als op een echte klok: de uurwijzer 1 uur verder =
     de minutenwijzer 1x helemaal rond; einde = allebei recht (12:00). */
  const hourRot = p * Math.PI * 2;
  const minRot = p * Math.PI * 2 * 12;
  /* wijzerplaat + tikken (statisch) */
  ctx.strokeStyle = C.accent;
  ctx.lineWidth = 6;
  ctx.beginPath(); ctx.arc(ccx, ccy, cr, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = "rgba(230,238,245,0.5)";
  ctx.lineWidth = 4;
  for (let i = 0; i < 12; i++) {
    const a = i * Math.PI / 6;
    ctx.beginPath();
    ctx.moveTo(ccx + Math.cos(a) * (cr - 20), ccy + Math.sin(a) * (cr - 20));
    ctx.lineTo(ccx + Math.cos(a) * (cr - 7), ccy + Math.sin(a) * (cr - 7));
    ctx.stroke();
  }
  /* wijzers: elk met hun eigen rotatie rond het midden van de plaat */
  ctx.strokeStyle = "#e6eef5";
  ctx.lineCap = "round";
  /* uurwijzer (kort en dik) */
  ctx.save();
  ctx.translate(ccx, ccy);
  ctx.rotate(hourRot);
  ctx.lineWidth = 9;
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, -cr * 0.52); ctx.stroke();
  ctx.restore();
  /* minutenwijzer (lang en dun, gaat 12x zo snel) */
  ctx.save();
  ctx.translate(ccx, ccy);
  ctx.rotate(minRot);
  ctx.lineWidth = 5;
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, -cr * 0.80); ctx.stroke();
  ctx.restore();
  /* naaf (statisch) */
  ctx.fillStyle = C.accent;
  ctx.beginPath(); ctx.arc(ccx, ccy, 7, 0, Math.PI * 2); ctx.fill();

  /* maand-jaar onder de klok */
  ctx.textAlign = "center";
  ctx.font = "700 52px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.text;
  ctx.fillText(fmtMonthYear(t), ccx, ccy + cr + 92);

  /* kop-stats */
  statBlock(ctx, 1355, "PAID GAMES", fmtInt(paidCum), "#9fc9e8");
  statBlock(ctx, 1560, "FREE (F2P) GAMES", fmtInt(freeCum), FREE_COLOR);

  drawNote(ctx, "cumulative counts up to the clock's date · is_free from the Steam store (current status)");
}

/* Ring-segment van de donut (buitenboog heen, binnenboog terug). */
function donutSeg(ctx, cx, cy, rOut, rIn, a0, a1) {
  ctx.beginPath();
  ctx.arc(cx, cy, rOut, a0, a1);
  ctx.arc(cx, cy, rIn, a1, a0, true);
  ctx.closePath();
}

/* Label met verbindingslijntje buiten de donut. */
function donutCallout(ctx, cx, cy, r, a, text, color) {
  const x1 = cx + Math.cos(a) * (r + 10);
  const y1 = cy + Math.sin(a) * (r + 10);
  const x2 = cx + Math.cos(a) * (r + 48);
  const y2 = cy + Math.sin(a) * (r + 48);
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  const left = Math.cos(a) >= 0;
  ctx.textAlign = left ? "left" : "right";
  ctx.fillStyle = color;
  ctx.font = "800 34px 'Segoe UI', Arial, sans-serif";
  ctx.fillText(text, x2 + (left ? 12 : -12), y2 + 12);
}

/* =====================================================================
 * 4) Indie takeover (100%-ribbon per jaar)
 * ===================================================================== */
function drawIndie(p) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  clearCanvas(ctx, W, H);
  drawHeader(ctx, "How Indie took over Steam",
    "Indie share of new releases per year (Steam 'Indie' genre tag)");

  const t = state.t0 + p * (state.t1 - state.t0);
  const years = state.years;
  const x0 = 120, x1 = 1560, yTop = 210, yBot = 780;
  const colH = yBot - yTop;
  const slot = (x1 - x0) / years.length;
  const barW = 42;
  let sIndie = 0, sAll = 0;
  let crossCx = null, crossOn = false;

  for (let i = 0; i < years.length; i++) {
    const Y = years[i];
    const cx = x0 + slot * (i + 0.5);
    const bx = cx - barW / 2;
    if (t < Y.y0) {
      ctx.fillStyle = "#131d29";
      ctx.fillRect(bx, yTop, barW, colH);
      continue;
    }
    const cap = Math.min(t, Y.y1 - 1);
    const nAll = upperBound(Y.all, cap);
    const nInd = upperBound(Y.indie, cap);
    sAll += nAll;
    sIndie += nInd;
    if (nAll === 0) {
      ctx.fillStyle = "#131d29";
      ctx.fillRect(bx, yTop, barW, colH);
      continue;
    }
    const share = nInd / nAll;
    const split = yBot - share * colH;
    ctx.fillStyle = REST_COLOR;
    ctx.fillRect(bx, yTop, barW, split - yTop);
    ctx.fillStyle = INDIE_COLOR;
    ctx.fillRect(bx, split, barW, yBot - split);
    /* percentage boven de kolom */
    ctx.textAlign = "center";
    ctx.font = share > 0.5 ? "700 22px 'Segoe UI', Arial, sans-serif" : "600 22px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = share > 0.5 ? INDIE_COLOR : "#c7d5e0";
    ctx.fillText(Math.round(share * 100) + "%", cx, yTop - 12);
    if (Y.year === 2012 && share > 0.5 && crossCx === null) { crossCx = cx; crossOn = true; }
  }

  /* mijlpaal: het eerste jaar met een Indie-meerderheid */
  if (crossOn) {
    ctx.strokeStyle = "rgba(126,224,129,0.55)";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 6]);
    ctx.beginPath(); ctx.moveTo(crossCx, yTop - 26); ctx.lineTo(crossCx, yBot + 12); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = INDIE_COLOR;
    ctx.beginPath(); ctx.arc(crossCx, yTop - 30, 5, 0, Math.PI * 2); ctx.fill();
  }

  /* jaar-labels (2004..2026, stap 2) */
  ctx.textAlign = "center";
  ctx.font = "500 20px 'Segoe UI', Arial, sans-serif";
  for (let i = 1; i < years.length; i += 2) {
    ctx.fillStyle = years[i].year === 2012 && crossOn ? INDIE_COLOR : C.muted;
    ctx.fillText(String(years[i].year), x0 + slot * (i + 0.5), yBot + 34);
  }

  /* kop-stats: cumulatief Indie-aandeel */
  const share = sAll ? sIndie / sAll : 0;
  ctx.textAlign = "right";
  ctx.font = "800 76px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = INDIE_COLOR;
  ctx.fillText((share * 100).toFixed(1) + "%", 1562, 108);
  ctx.font = "400 25px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("of all released Steam games are Indie", 1562, 146);
  ctx.font = "500 23px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = "#c7d5e0";
  ctx.fillText(`${fmtInt(sIndie)} Indie · ${fmtInt(sAll)} total`, 1562, 180);

  drawNote(ctx, "Indie = games carrying Steam's 'Indie' genre tag · releases up to today");
}

/* =====================================================================
 * 5) Spelersverdeling (5 buckets op gemiddelde spelers; cirkeldiagram)
 * ===================================================================== */
function drawPlayers(p) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  const t = state.t0 + p * (state.t1 - state.t0);
  const P = state.players;
  const counts = P.buckets.map((b) => upperBound(b.times, t));
  const measured = upperBound(P.snapT, t);
  const total = counts.reduce((a, b) => a + b, 0);

  clearCanvas(ctx, W, H);
  drawHeader(ctx, "Steam games by average player count",
    "every game with player snapshots, bucketed by its average player count · the pie grows up to the date");

  /* cirkeldiagram: de 4 kleine punten liggen onderaan gecentreerd (a0 schuift
     mee met hun totale hoek), zodat hun dunne lijntjes nergens botsen */
  const cx = 520, cy = 470, r = 270;
  const angs = counts.map((n) => (total > 0 ? (Math.PI * 2 * n) / total : 0));
  const smallAng = angs.slice(1).reduce((a, b) => a + b, 0);
  const a0 = Math.PI / 2 + smallAng / 2;

  if (total === 0) {
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = C.axis; ctx.lineWidth = 2; ctx.stroke();
  } else {
    let a = a0;
    for (let i = 0; i < P.buckets.length; i++) {
      const b = P.buckets[i], ang = angs[i];
      if (ang > 0) {
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r, a, a + ang);
        ctx.closePath();
        ctx.fillStyle = b.color;
        ctx.fill();
        /* hairline-punten (< ~3°) als zichtbaar kleurlijntje op de rand */
        if (ang < 0.05) {
          ctx.beginPath();
          ctx.arc(cx, cy, r - 3, a, a + ang);
          ctx.strokeStyle = b.color;
          ctx.lineWidth = 5;
          ctx.stroke();
        }
      }
      a += ang;
    }
    /* groot %-label IN het dominante stuk (donkere tekst = leesbaar op rood) */
    if (angs[0] >= 1.0) {
      const mid = a0 + angs[0] / 2;
      const lx = cx + Math.cos(mid) * r * 0.56;
      const ly = cy + Math.sin(mid) * r * 0.56;
      ctx.textAlign = "center";
      ctx.font = "800 84px 'Segoe UI', Arial, sans-serif";
      ctx.fillStyle = "#0f1a26";
      ctx.fillText((100 * counts[0] / total).toFixed(1) + "%", lx, ly + 12);
      ctx.font = "600 26px 'Segoe UI', Arial, sans-serif";
      ctx.fillStyle = "rgba(15,26,38,0.85)";
      ctx.fillText(fmtInt(counts[0]) + " games", lx, ly + 54);
    }
  }

  /* legenda rechts: kleur + bucket + % + aantal (live meegeteld) */
  for (let i = 0; i < P.buckets.length; i++) {
    const b = P.buckets[i], y = 250 + i * 90;
    const pct = total > 0 ? (100 * counts[i] / total) : 0;
    rrect(ctx, 950, y - 22, 26, 26, 6);
    ctx.fillStyle = b.color;
    ctx.fill();
    ctx.textAlign = "left";
    ctx.font = "600 28px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = "#dbe7f0";
    ctx.fillText(b.label, 992, y);
    ctx.textAlign = "right";
    ctx.font = "800 30px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = b.color;
    ctx.fillText(pct.toFixed(1) + "%", 1330, y);
    ctx.font = "600 26px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = "#e6eef5";
    ctx.fillText(fmtInt(counts[i]), 1560, y);
  }

  /* kop-stats rechtsboven */
  ctx.textAlign = "right";
  ctx.font = "600 20px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("GAMES MEASURED", 1560, 64);
  ctx.font = "800 46px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = "#e6eef5";
  ctx.fillText(fmtInt(measured), 1560, 108);
  ctx.font = "400 26px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText(fmtMonthYear(t), 1560, 146);

  drawNote(ctx, "average players = mean of all collected snapshots · " +
    fmtInt(P.excluded) + " games without snapshots are excluded · tiny slices get a minimum line width");
}

/* =====================================================================
 * Afspelen
 * ===================================================================== */
function drawFrame(p, dt) {
  if (!state.ready && state.chart !== "race") return;
  switch (state.chart) {
    case "race": drawRace(p, dt || 0); break;
    case "heat": drawHeat(p); break;
    case "f2p": drawF2P(p); break;
    case "indie": drawIndie(p); break;
    case "players": drawPlayers(p); break;
  }
}

function setPlayUI() {
  el.playBtn.textContent = state.playing ? "⏸ Pause" : "▶ Play";
}

function loop(ts) {
  if (state.ready && state.playing) {
    if (!state.lastTs) state.lastTs = ts;
    const dt = ts - state.lastTs;
    state.elapsed += dt;
    const cycle = state.durMs + END_PAUSE_MS;
    const et = state.elapsed % cycle;
    const p = et >= state.durMs ? 1 : et / state.durMs;
    state.pauseP = p;
    drawFrame(p, dt);
  }
  state.lastTs = ts;
  requestAnimationFrame(loop);
}

function activateChart(key) {
  state.chart = key;
  for (const b of el.chips) b.classList.toggle("active", b.dataset.chart === key);
  el.chartTitle.textContent = CHARTS[key].title;
  el.hint.textContent = HINTS[key];
  el.durSlider.value = CHARTS[key].dur;
  state.durMs = CHARTS[key].dur * 1000;
  el.durVal.value = CHARTS[key].dur;
  state.elapsed = 0;
  state.pauseP = 0;
  state.racePos = null;
  if (state.ready) drawFrame(0, 0);
}

function bindControls() {
  for (const b of el.chips) {
    b.addEventListener("click", () => {
      if (state.recording || !state.ready) return;
      activateChart(b.dataset.chart);
    });
  }
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
    state.racePos = null;
    drawFrame(0, 0);
  });
  el.durSlider.addEventListener("input", () => {
    if (state.recording) return;
    state.durMs = +el.durSlider.value * 1000;
    el.durVal.value = el.durSlider.value;
    if (!state.playing) drawFrame(state.pauseP, 0);
  });
  el.exportBtn.addEventListener("click", () => {
    if (state.recording) stopExport(true);   // knop = annuleren tijdens opname
    else startExport();
  });
}

function setControlsDisabled(disabled) {
  el.playBtn.disabled = disabled;
  el.restartBtn.disabled = disabled;
  el.durSlider.disabled = disabled;
  for (const b of el.chips) b.disabled = disabled;
}

/* =====================================================================
 * MP4-export (MediaRecorder; zelfde aanpak als de hoofdpagina)
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
  const cv = el.canvas;
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
  state.playing = true;           // animatie draait tijdens opname
  state.elapsed = 0;
  state.lastTs = 0;
  state.racePos = null;           // race begint netjes vooraan
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

  el.recBadge.hidden = false;
  el.exportBtn.textContent = "■ Stop (cancel MP4)";
  el.exportBtn.classList.add("recording");
  el.exportStatus.hidden = false;
  el.exportStatus.textContent =
    `⏺ Recording… one full cycle (${el.durSlider.value} s) + end hold — keep this tab visible.`;
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
  const name = `steam_${CHARTS[state.chart].slug}.${ext}`;
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
activateChart("race");
loadData();
