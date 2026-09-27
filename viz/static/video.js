/* =====================================================================
 * Steam video charts (viz/video.html) - vijf losse video-visuals op
 * dezelfde CSV's als de hoofdpagina (games.csv + game_genres.csv):
 *
 *   1) map     - genre-map: morphing treemap; elke tegel = een genre,
 *                oppervlak = aandeel van alle genre-tags; de kaart groeit
 *                en hervormt terwijl de tijd vordert (Indie -> kwart deel).
 *   2) spiral  - release-spiraal: 1 ring per jaar (januari bovenaan), elke
 *                stip = 1 dag; helderder/groter = meer releases. De sweep
 *                onthult de spiraal van binnen (2003) naar buiten (nu).
 *   3) f2p     - free-to-play vs paid: cirkeldiagram (paid vs free, samen
 *                100%) + klok met echte wijzerverhouding (minutenwijzer 12x
 *                zo snel als de uurwijzer) en maand-jaar eronder.
 *   4) indie   - "Indie takeover": thermometer; het kwik = het cumulatieve
 *                Indie-aandeel van alle releases, met mijlpalen 25/50/75%.
 *   5) players - player-funnel: hoeveel games bereiken welke spelerstand?
 *                (alle met data -> >=1 -> >=10 -> ... -> >=100k gemiddeld),
 *                als log-geschaalde staircase met exacte aantallen.
 *
 * Net als de hoofdpagina: canvas 1600x900, Play/Restart/Duur-slider,
 * MP4-export (MediaRecorder) en 10 s eind-hold voordat de cyclus opnieuw
 * begint.
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
  map:     { title: "Genre map",            dur: 60, slug: "genre_map" },
  spiral:  { title: "Release spiral",       dur: 60, slug: "release_spiral" },
  f2p:     { title: "Free-to-play vs paid", dur: 30, slug: "f2p_vs_paid" },
  indie:   { title: "Indie takeover",       dur: 30, slug: "indie_takeover" },
  players: { title: "Player funnel",        dur: 45, slug: "player_funnel" },
};

const HINTS = {
  map: "Each tile is a genre (the 9 biggest + an 'Other' tile); tile area = its share of all genre tags (a game can carry several). The map reshapes as Steam grows - watch Indie claim a quarter of it.",
  spiral: "One ring = one year (January at the top); every dot is one day, brighter and bigger when more games came out. The spiral unwinds from 2003 to today.",
  f2p: "Donut = cumulative paid vs free share of all releases (together 100%); the clock hands run like a real clock (minute hand 12x the hour hand) as the timeline advances, with the month-year below it.",
  indie: "The thermometer shows Indie's cumulative share of all Steam releases; the milestone lines mark 25% and 50% (and the 75% line it has not reached yet).",
  players: "How many measured games reach each audience size - from 'has any players' to 'averages over 100,000'. Bar lengths are log-scaled, the counts are exact.",
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
  years: [],                    // [{year, y0, y1, all:[t], free:[t], indie:[t]}] (f2p)
  map: null,                    // {genres:[{name,color,dark,times}], cps:[{t,rects}], tagsTotal}
  spiral: null,                 // {n, x, y, byLevel, size, color, cum, days, years, cx, cy}
  indie: null,                  // {all:[t], ind:[t], crossings:[t|null]}
  funnel: null,                 // {tiers:[{label,color,times}], top:{name,value}}
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

/* Afgeronde rechthoek als PAD (zonder begin/close, zodat je hem ook in een
 * groter pad kunt combineren - bv. buis + bol van de thermometer). */
function rrectPath(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/* Afgeronde rechthoek (boogjes i.p.v. ctx.roundRect, overal bruikbaar). */
function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  rrectPath(ctx, x, y, w, h, r);
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

/* Kleurverloop voor de spiraal: sqrt-schaal zodat kleine aantallen al
 * zichtbaar zijn (max = drukste dag). */
const HEAT_STOPS = ["#2b5a7d", "#3f8fc4", "#8fd0ff", "#ecf9ff"];

/* Donkere of lichte tekst op een tegelkleur? */
function isDarkText(hex) {
  const [r, g, b] = hexRGB(hex);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
}

/* Player-funnel: drempels op `average_players` (gemiddelde van alle
 * momentopnames per game). De eerste rij = alle games met data. */
const FUNNEL_TIERS = [
  { label: "ALL MEASURED", color: "#c9d6e2", min: -1 },
  { label: "\u2265 1", color: "#e35d6a", min: 1 },
  { label: "\u2265 10", color: "#f4a259", min: 10 },
  { label: "\u2265 100", color: "#f2d75e", min: 100 },
  { label: "\u2265 1,000", color: "#7ee081", min: 1000 },
  { label: "\u2265 10,000", color: "#66c0f4", min: 10000 },
  { label: "\u2265 100,000", color: "#c9f0ff", min: 100000 },
];

/* Indie-thermometer: mijlpalen in het cumulatieve aandeel. */
const INDIE_MILESTONES = [0.25, 0.5, 0.75];

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

    const genreMap = new Map();    // genre -> [t...] (voor de genre-map)
    const dayMap = new Map();      // dag-ms -> aantal (voor de spiraal)
    const tierTimes = FUNNEL_TIERS.map(() => []);   // per drempel: [t...]
    let t0 = Infinity;
    let gamesTotal = 0;
    let topGame = null;            // recordhouder op average_players

    for (const g of gameRows) {
      const d = g.release_date_fmt;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d || "")) continue;
      const t = dateToMs(d);
      if (t > todayMs) continue;               // einddatum-filter (zoals de hoofdpagina)
      gamesTotal++;
      const ap = parseFloat(g.average_players);
      if (!isNaN(ap)) {
        if (!topGame || ap > topGame.value) topGame = { name: g.name, value: ap };
        for (let k = 0; k < FUNNEL_TIERS.length; k++) {
          if (ap >= FUNNEL_TIERS[k].min) tierTimes[k].push(t);
        }
      }
      if (t < t0) t0 = t;
      const Y = years[yearIndex(t)];
      Y.all.push(t);
      if (g.is_free === "True") Y.free.push(t);
      const gs = genresOf.get(g.appid);
      if (gs) {
        for (const name of gs) {
          if (name === "Indie") Y.indie.push(t);
          let arr = genreMap.get(name);
          if (!arr) genreMap.set(name, (arr = []));
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

    /* --- genre-map: top 9 genres + "Other" (alle overige tags samen) --- */
    const genreList = [...genreMap.entries()]
      .map(([name, times]) => ({ name, times: times.sort((a, b) => a - b) }))
      .sort((a, b) => b.times.length - a.times.length);
    const mapGenres = genreList.slice(0, 9).map((g) => ({
      name: g.name,
      color: GENRE_COLORS[g.name] || "#8ba2b6",
      dark: isDarkText(GENRE_COLORS[g.name] || "#8ba2b6"),
      times: g.times,
    }));
    const otherTimes = [];
    for (const g of genreList.slice(9)) for (const v of g.times) otherTimes.push(v);
    otherTimes.sort((a, b) => a - b);
    mapGenres.push({ name: "Other", color: REST_COLOR, dark: false, times: otherTimes });
    const tagsTotal = mapGenres.reduce((a, g) => a + g.times.length, 0);

    /* Checkpoints (per maand) met per checkpoint een squarified-treemap-
       layout, zodat de kaart tussen maanden soepel kan morphen. */
    const cpTimes = [state.t0];
    for (let tt = Date.UTC(firstYear, new Date(state.t0).getUTCMonth() + 1, 1); tt < state.t1; tt = nextMonth(tt)) {
      cpTimes.push(tt);
    }
    if (cpTimes[cpTimes.length - 1] !== state.t1) cpTimes.push(state.t1);
    const cps = cpTimes.map((tt) => ({
      t: tt,
      rects: squarify(mapGenres.map((g) => upperBound(g.times, tt)), MAP_AREA.x, MAP_AREA.y, MAP_AREA.w, MAP_AREA.h),
    }));
    state.map = { genres: mapGenres, cps, tagsTotal };

    /* --- release-spiraal: 1 ring per jaar, per dag een stip (vast punt) --- */
    const SP = { cx: 850, cy: 488, r0: 75, perYear: 13.6, levels: 8 };
    const byLevel = Array.from({ length: SP.levels + 1 }, () => []);
    const spSize = new Array(SP.levels + 1).fill(0);
    const spColor = new Array(SP.levels + 1).fill("#000");
    spSize[0] = 1.6;
    spColor[0] = "#243a4d";
    for (let l = 1; l <= SP.levels; l++) {
      const q = (l - 1) / (SP.levels - 1);
      spSize[l] = 2.0 + 3.8 * q;
      spColor[l] = rampColor(HEAT_STOPS, q);
    }
    const days = [], counts = [], cum = [], spX = [], spY = [];
    let acc = 0, maxDay = 1;
    for (let ms = state.t0; ms <= todayMs; ms += 86400000) {
      const n = dayMap.get(ms) || 0;
      if (n > maxDay) maxDay = n;
      days.push(ms); counts.push(n); acc += n; cum.push(acc);
    }
    for (let i = 0; i < days.length; i++) {
      const ms = days[i];
      const d = new Date(ms), y = d.getUTCFullYear();
      const doy = Math.round((ms - Date.UTC(y, 0, 1)) / 86400000);
      const diy = (Date.UTC(y + 1, 0, 1) - Date.UTC(y, 0, 1)) / 86400000;
      const a = (doy / diy) * Math.PI * 2 - Math.PI / 2;
      const r = SP.r0 + ((y - firstYear) + doy / diy) * SP.perYear;
      spX.push(Math.round(SP.cx + Math.cos(a) * r));
      spY.push(Math.round(SP.cy + Math.sin(a) * r));
      const n = counts[i];
      const lv = n > 0 ? 1 + Math.min(SP.levels - 1, Math.floor(Math.sqrt(n / maxDay) * SP.levels)) : 0;
      byLevel[lv].push(i);
    }
    const spiralYears = [];
    for (let y = firstYear + 1; y <= lastYear; y++) spiralYears.push({ year: y, rad: SP.r0 + (y - firstYear) * SP.perYear });
    state.spiral = { n: days.length, x: spX, y: spY, byLevel, size: spSize, color: spColor,
                     days, cum, maxDay, years: spiralYears, cx: SP.cx, cy: SP.cy };

    /* --- indie: cumulatieve share (alle releases vs 'Indie'-tag) + mijlpalen ---
     * Mijlpalen = BLIJVENDE kruisingen: de eerste datum waarna het aandeel
     * niet meer onder de drempel zakt (het aandeel begint op ~33%, zakt in de
     * AAA-jaren naar ~15% en stijgt daarna door naar ~72%). */
    const indAll = [], indInd = [];
    for (const Y of years) {
      for (const v of Y.all) indAll.push(v);
      for (const v of Y.indie) indInd.push(v);
    }
    const crossings = INDIE_MILESTONES.map(() => null);
    const lastBelow = INDIE_MILESTONES.map(() => null);
    let ci = 0;
    while (ci < indAll.length) {
      const d = indAll[ci];
      let cj = ci;
      while (cj < indAll.length && indAll[cj] === d) cj++;
      const share = upperBound(indInd, d) / cj;
      for (let k = 0; k < INDIE_MILESTONES.length; k++) {
        if (share < INDIE_MILESTONES[k]) lastBelow[k] = d;
      }
      ci = cj;
    }
    for (let k = 0; k < INDIE_MILESTONES.length; k++) {
      if (lastBelow[k] === null) crossings[k] = indAll[0];          // nooit meer onder geweest
      else {
        const idx = upperBound(indAll, lastBelow[k]);               // eerste datum er na
        crossings[k] = idx < indAll.length ? indAll[idx] : null;
      }
    }
    state.indie = { all: indAll, ind: indInd, crossings };

    /* --- funnel: per drempel de releasedatums (sorteren: upperBound) --- */
    for (const arr of tierTimes) arr.sort((a, b) => a - b);
    state.funnel = {
      tiers: FUNNEL_TIERS.map((tier, i) => ({ label: tier.label, color: tier.color, times: tierTimes[i] })),
      top: topGame,
    };

    state.years = years;
    el.dataInfo.textContent = `${fmtInt(gamesTotal)} games · ${state.map.genres.length} genre tiles · ${days.length} days · ${fmtInt(state.funnel.tiers[0].times.length)} with player data`;

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

/* Gebied van de genre-map (treemap). */
const MAP_AREA = { x: 60, y: 200, w: 1480, h: 590 };

function nextMonth(ms) {
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
}

/* Squarified treemap (Bruls e.a.): rechthoeken met een zo vierkant
 * mogelijke beeldverhouding, in dalende volgorde van waarde. Kleine genres
 * krijgen een kleine minimum-oppervlakte (anders worden het onleesbare
 * streepjes); de grote tegels krimpen daardoor ~3% - onmerkbaar. Een te
 * dunne LAATSTE rij (één klein genre als 1480x6-streep) wordt bij de vorige
 * rij getrokken - zie MIN_ROW_PX. */
const MIN_ROW_PX = 55;
function squarify(vals, x, y, w, h) {
  const total0 = vals.reduce((a, b) => a + b, 0);
  const eps = total0 > 0 ? total0 * 0.0035 : 0;
  const v2 = vals.map((v) => (v > 0 ? v + eps : 0));
  const total = v2.reduce((a, b) => a + b, 0) || 1;
  const order = v2.map((v, i) => i).sort((a, b) => v2[b] - v2[a]);
  const scale = (w * h) / total;
  const items = order.map((i) => ({ i, a: v2[i] * scale })).filter((it) => it.a > 0);

  /* rijen bepalen (nog niet plaatsen) */
  const rows = [];
  let rw = w, rh = h, k = 0;
  while (k < items.length && rw > 0.01 && rh > 0.01) {
    const horizontal = rw >= rh;
    const side = horizontal ? rw : rh;
    const row = [items[k]];
    let sum = items[k].a;
    let best = worstAspect(row, sum, side);
    while (k + 1 < items.length) {
      const sum2 = sum + items[k + 1].a;
      const w2 = worstAspect(row.concat([items[k + 1]]), sum2, side);
      if (w2 <= best) { row.push(items[k + 1]); sum = sum2; best = w2; k++; }
      else break;
    }
    k++;
    rows.push({ row, sum, side, horizontal });
    if (horizontal) rh -= sum / side;
    else rw -= sum / side;
  }
  /* flinterdunne laatste rij? samenvoegen met de rij ervoor */
  while (rows.length > 1 && rows[rows.length - 1].sum / rows[rows.length - 1].side < MIN_ROW_PX) {
    const last = rows.pop();
    const prev = rows[rows.length - 1];
    if (prev.horizontal !== last.horizontal) break;
    for (const it of last.row) { prev.row.push(it); prev.sum += it.a; }
  }

  /* rijen plaatsen */
  const rects = new Array(vals.length).fill(null);
  let rx = x, ry = y, rw2 = w, rh2 = h;
  for (const r of rows) {
    const thick = r.sum / r.side;
    let off = 0;
    for (const it of r.row) {
      const len = it.a / thick;
      rects[it.i] = r.horizontal
        ? { x: rx + off, y: ry, w: len, h: thick }
        : { x: rx, y: ry + off, w: thick, h: len };
      off += len;
    }
    if (r.horizontal) { ry += thick; rh2 -= thick; }
    else { rx += thick; rw2 -= thick; }
  }
  return rects;
}
function worstAspect(row, sum, side) {
  const thick = sum / side;
  let lo = Infinity, hi = 0;
  for (const it of row) {
    const len = it.a / thick;
    if (len < lo) lo = len;
    if (len > hi) hi = len;
  }
  return Math.max(thick / lo, hi / thick);
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
 * 1) Genre-map (morphing treemap: aandeel van alle genre-tags)
 * ===================================================================== */
function drawMap(p) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  clearCanvas(ctx, W, H);
  drawHeader(ctx, "The map of Steam by genre",
    "each tile = a genre (the 9 biggest + an 'Other' tile); tile area = its share of all genre tags");

  const M = state.map, t = state.t0 + p * (state.t1 - state.t0);

  /* morph tussen de maand-checkpoints heen */
  const seg = p * (M.cps.length - 1);
  const ci = Math.min(M.cps.length - 2, Math.floor(seg));
  const f = seg - ci;
  const A = M.cps[ci].rects, B = M.cps[ci + 1].rects;

  const tiles = [];
  for (let i = 0; i < M.genres.length; i++) {
    const a = A[i], b = B[i];
    const r = a && b ? {
      x: a.x + (b.x - a.x) * f,
      y: a.y + (b.y - a.y) * f,
      w: a.w + (b.w - a.w) * f,
      h: a.h + (b.h - a.h) * f,
    } : (b || a);
    if (!r) continue;
    tiles.push({ g: M.genres[i], r, cnt: upperBound(M.genres[i].times, t) });
  }
  /* grootste tegels eerst, kleine erbovenop */
  tiles.sort((u, v) => v.r.w * v.r.h - u.r.w * u.r.h);

  for (const tl of tiles) {
    const r = tl.r, g = tl.g, cnt = tl.cnt;
    if (r.w < 3 || r.h < 3) continue;
    rrect(ctx, r.x + 2, r.y + 2, r.w - 4, r.h - 4, 8);
    ctx.fillStyle = cnt > 0 ? g.color : "#1a2735";
    ctx.fill();
    if (cnt === 0) continue;      // genre bestaat op deze datum nog niet

    /* labels zo groot als de tegel toelaat */
    const fs = Math.max(14, Math.min(34, Math.floor(Math.min(r.w * 0.11, r.h * 0.22))));
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = g.dark ? "#10202e" : "#eef7ff";
    ctx.font = `700 ${fs}px 'Segoe UI', Arial, sans-serif`;
    if (r.w > 80 && r.h > fs + 26 && ctx.measureText(g.name).width <= r.w - 26) {
      ctx.fillText(g.name, r.x + 14, r.y + 12 + fs);
    }
    if (r.h > fs + 62 && r.w > 110) {
      ctx.font = `600 ${Math.round(fs * 0.72)}px 'Segoe UI', Arial, sans-serif`;
      const txt = (cnt >= 1000 ? (cnt / 1000).toFixed(1) + "k" : String(cnt)) + (cnt === 1 ? " game" : " games");
      ctx.fillText(txt, r.x + 14, r.y + 12 + fs + Math.round(fs * 0.95));
    }
  }

  /* kop rechtsboven: het grootste genre op dit moment */
  let lead = null;
  for (const tl of tiles) if (tl.cnt > 0 && (!lead || tl.cnt > lead.cnt)) lead = tl;
  ctx.textAlign = "right";
  ctx.font = "600 20px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("LEADING GENRE", 1562, 64);
  ctx.font = "800 46px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = lead ? lead.g.color : C.accent;
  ctx.fillText(lead ? lead.g.name : "-", 1562, 108);
  ctx.font = "600 24px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = "#e6eef5";
  ctx.fillText(lead ? fmtInt(lead.cnt) + " games" : "", 1562, 142);
  ctx.font = "400 24px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText(fmtMonthYear(t), 1562, 176);

  drawNote(ctx, "tile area = share of " + fmtInt(M.tagsTotal) + " genre tags · a game can carry several tags · 'Other' = all smaller tags · releases up to today");
}

/* =====================================================================
 * 2) Release-spiraal (1 ring per jaar; elke dag een stip)
 * ===================================================================== */
function drawSpiral(p) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  clearCanvas(ctx, W, H);
  drawHeader(ctx, "Steam releases — one ring per year",
    "every dot is one day; January sits at the top");

  const S = state.spiral;
  const iNow = Math.min(S.n - 1, Math.floor(p * (S.n - 1)));

  /* nog niet onthuld: zwakke stippen, zodat de vorm al zichtbaar is */
  ctx.fillStyle = "#16202c";
  for (let i = iNow + 1; i < S.n; i++) ctx.fillRect(S.x[i] - 1, S.y[i] - 1, 2, 2);

  /* jaar-spoke + tikken (jan-1 van elk jaar ligt op de verticale lijn) */
  ctx.strokeStyle = "rgba(199,213,224,0.12)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(S.cx, S.cy);
  ctx.lineTo(S.cx, S.cy - (S.years[S.years.length - 1].rad + 20));
  ctx.stroke();
  const activeYear = new Date(S.days[iNow]).getUTCFullYear();
  for (const yr of S.years) {
    const y = S.cy - yr.rad;
    const active = yr.year === activeYear;
    ctx.strokeStyle = active ? C.accent : (yr.year % 2 === 0 ? "rgba(199,213,224,0.5)" : "rgba(199,213,224,0.22)");
    ctx.lineWidth = active ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(S.cx - 14, y); ctx.lineTo(S.cx - 4, y); ctx.stroke();
  }

  /* onthulde dagen, gebatcht per helderheidsniveau */
  for (let l = 0; l < S.size.length; l++) {
    const list = S.byLevel[l];
    if (!list.length) continue;
    const s = S.size[l];
    ctx.fillStyle = S.color[l];
    for (const i of list) {
      if (i > iNow) break;
      ctx.fillRect(S.x[i] - s / 2, S.y[i] - s / 2, s, s);
    }
  }

  /* actieve dag: witte ring + stip */
  const ax = S.x[iNow], ay = S.y[iNow];
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(ax, ay, 8, 0, Math.PI * 2); ctx.stroke();
  ctx.strokeStyle = "rgba(255,255,255,0.3)";
  ctx.beginPath(); ctx.arc(ax, ay, 14, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = "#ffffff";
  ctx.beginPath(); ctx.arc(ax, ay, 3, 0, Math.PI * 2); ctx.fill();

  /* jaar-labels met halo (boven de stippen, anders prikken ze erdoorheen) */
  ctx.textAlign = "right";
  ctx.lineJoin = "round";
  for (const yr of S.years) {
    if (yr.year % 2 !== 0) continue;
    const y = S.cy - yr.rad + 6;
    const active = yr.year === activeYear;
    ctx.font = (active ? "700 18px" : "500 17px") + " 'Segoe UI', Arial, sans-serif";
    ctx.lineWidth = 5;
    ctx.strokeStyle = C.bg;
    ctx.strokeText(String(yr.year), S.cx - 22, y);
    ctx.fillStyle = active ? C.accent : C.muted;
    ctx.fillText(String(yr.year), S.cx - 22, y);
  }

  /* kop-stats rechtsboven */
  ctx.textAlign = "right";
  ctx.font = "600 20px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("RELEASES SO FAR", 1562, 64);
  ctx.font = "800 76px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.accent;
  ctx.fillText(fmtInt(S.cum[iNow]), 1562, 140);
  ctx.font = "600 26px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = "#e6eef5";
  ctx.fillText(fmtMonthYear(S.days[iNow]), 1562, 178);

  drawNote(ctx, "each dot = one day · each ring = one year · brighter = more releases that day");
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
 * 4) Indie takeover (thermometer met het cumulatieve Indie-aandeel)
 * ===================================================================== */
function drawIndie(p) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  clearCanvas(ctx, W, H);
  drawHeader(ctx, "How Indie took over Steam",
    "Indie's cumulative share of all Steam releases — the thin line traces the history up to the shown date");

  const t = state.t0 + p * (state.t1 - state.t0);
  const D = state.indie;
  const sAll = upperBound(D.all, t), sInd = upperBound(D.ind, t);
  const share = sAll ? sInd / sAll : 0;

  /* thermometer: buis + bol als één pad (clip + rand) */
  const tubeX = 560, tubeW = 200, tubeTop = 150, tubeBot = 700;
  const colH = tubeBot - tubeTop;
  const bulbCx = tubeX + tubeW / 2, bulbCy = 760, bulbR = 95;
  const levelY = tubeBot - share * colH;
  const bulbBottom = bulbCy + bulbR;

  function thermoPath() {
    rrectPath(ctx, tubeX, tubeTop, tubeW, tubeBot - tubeTop, tubeW / 2);
    ctx.moveTo(bulbCx + bulbR, bulbCy);
    ctx.arc(bulbCx, bulbCy, bulbR, 0, Math.PI * 2);
  }

  /* donkere basis */
  ctx.beginPath();
  thermoPath();
  ctx.fillStyle = "#141f2b";
  ctx.fill();

  /* kwik binnen de clip: bol gevuld + kolom met golvend oppervlak */
  ctx.save();
  ctx.beginPath();
  thermoPath();
  ctx.clip();

  ctx.fillStyle = INDIE_COLOR;
  ctx.beginPath();
  ctx.arc(bulbCx, bulbCy, bulbR - 4, 0, Math.PI * 2);
  ctx.fill();

  const wave = (xi) => levelY
    + Math.sin(xi / 34 + p * Math.PI * 2 * 5) * 3.5
    + Math.sin(xi / 17 - p * Math.PI * 2 * 3) * 1.8;
  const wx0 = tubeX - 6, wx1 = tubeX + tubeW + 6;
  ctx.beginPath();
  ctx.moveTo(wx0, wave(wx0));
  for (let xi = wx0; xi <= wx1; xi += 4) ctx.lineTo(xi, wave(xi));
  ctx.lineTo(wx1, bulbBottom + 10);
  ctx.lineTo(wx0, bulbBottom + 10);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, levelY, 0, bulbBottom);
  grad.addColorStop(0, "#9aefa0");
  grad.addColorStop(1, "#57bb63");
  ctx.fillStyle = grad;
  ctx.fill();

  /* oppervlakte-lijn */
  ctx.beginPath();
  ctx.moveTo(wx0, wave(wx0));
  for (let xi = wx0; xi <= wx1; xi += 4) ctx.lineTo(xi, wave(xi));
  ctx.strokeStyle = "rgba(255,255,255,0.45)";
  ctx.lineWidth = 2;
  ctx.stroke();

  /* historie-sparkline: het cumulatieve aandeel als dun lijntje (de dip
     in de vroege AAA-jaren + de klim naar vandaag); groeit mee met de tijd */
  ctx.beginPath();
  const nSteps = 220;
  for (let i = 0; i <= nSteps; i++) {
    const f = (i / nSteps) * p;                     // alleen het verstreken deel
    const tt = state.t0 + f * (state.t1 - state.t0);
    const a = upperBound(D.all, tt);
    const sh = a ? upperBound(D.ind, tt) / a : 0;
    const xx = tubeX + 14 + f * (tubeW - 28);
    const yy = tubeBot - sh * colH;
    if (i === 0) ctx.moveTo(xx, yy); else ctx.lineTo(xx, yy);
  }
  ctx.strokeStyle = "rgba(255,255,255,0.45)";
  ctx.lineWidth = 1.6;
  ctx.stroke();

  /* bubbels in het kwik */
  ctx.fillStyle = "rgba(255,255,255,0.28)";
  for (let i = 0; i < 9; i++) {
    const prog = (p * (2.2 + i * 0.35) + i * 0.137) % 1;
    const bx = tubeX + 26 + ((i * 71) % (tubeW - 52)) + Math.sin(p * Math.PI * 2 * 2 + i) * 5;
    const by = bulbBottom - 20 - prog * (bulbBottom - 20 - levelY);
    if (by < levelY + 8) continue;
    ctx.beginPath();
    ctx.arc(bx, by, 2.5 + (i % 3), 0, Math.PI * 2);
    ctx.fill();
  }

  /* mijlpaal-lijnen binnen de buis */
  for (const m of INDIE_MILESTONES) {
    const y = tubeBot - m * colH;
    ctx.strokeStyle = "rgba(255,255,255,0.30)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([7, 6]);
    ctx.beginPath(); ctx.moveTo(tubeX, y); ctx.lineTo(tubeX + tubeW, y); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.restore();

  /* buis-rand */
  ctx.beginPath();
  thermoPath();
  ctx.strokeStyle = "rgba(255,255,255,0.25)";
  ctx.lineWidth = 2.5;
  ctx.stroke();

  /* mijlpaal-labels links + tikje naar de buis */
  for (let k = 0; k < INDIE_MILESTONES.length; k++) {
    const m = INDIE_MILESTONES[k], y = tubeBot - m * colH;
    const reached = D.crossings[k] !== null && t >= D.crossings[k];
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(530, y); ctx.lineTo(tubeX, y); ctx.stroke();
    ctx.textAlign = "right";
    ctx.font = "700 24px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = reached ? INDIE_COLOR : C.muted;
    ctx.fillText(Math.round(m * 100) + "%", 516, y + 4);
    ctx.font = "500 17px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = reached ? "#9aefa0" : C.muted;
    const sub = D.crossings[k] === null ? "not reached yet" : (reached ? fmtMonthYear(D.crossings[k]) : "not yet");
    ctx.fillText(sub, 516, y + 26);
  }

  /* groot percentage dat met het kwik mee omhoog kruipt */
  const px = tubeX + tubeW + 62;
  ctx.textAlign = "left";
  const pctTxt = (100 * share).toFixed(1);
  ctx.font = "800 130px 'Segoe UI', Arial, sans-serif";
  const numW = ctx.measureText(pctTxt).width;
  ctx.fillStyle = "#e6eef5";
  ctx.fillText(pctTxt, px, levelY + 44);
  ctx.fillStyle = INDIE_COLOR;
  ctx.fillText("%", px + numW + 6, levelY + 44);
  ctx.font = "600 24px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = "#c7d5e0";
  ctx.fillText(`${fmtInt(sInd)} Indie of ${fmtInt(sAll)} games`, px, levelY + 88);

  /* maand-jaar rechtsboven */
  ctx.textAlign = "right";
  ctx.font = "600 26px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText(fmtMonthYear(t), 1562, 64);

  drawNote(ctx, "Indie = Steam's 'Indie' genre tag · cumulative share of all releases");
}

/* =====================================================================
 * 5) Player-funnel (hoeveel games bereiken welke spelerstand?)
 * ===================================================================== */
function drawFunnel(p) {
  const ctx = el.canvas.getContext("2d");
  const W = el.canvas.width, H = el.canvas.height;
  clearCanvas(ctx, W, H);

  const F = state.funnel;
  const nm = F.top ? (F.top.name.length > 26 ? F.top.name.slice(0, 25) + "\u2026" : F.top.name) : null;
  const rec = F.top ? ` - the biggest is ${nm} with ${fmtInt(Math.round(F.top.value))} average players` : "";
  drawHeader(ctx, "How many games reach how many players",
    "average concurrent players per game (mean of all collected snapshots)" + rec);

  const t = state.t0 + p * (state.t1 - state.t0);
  const counts = F.tiers.map((ti) => upperBound(ti.times, t));
  const measured = Math.max(1, counts[0]);
  const logMax = Math.log10(measured + 1);

  const X0 = 330, X1 = 1330, rowTop = 190, rowH = 96, barH = 56;
  const maxW = X1 - X0;

  ctx.textAlign = "right";
  ctx.font = "600 18px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("AVG PLAYERS", 300, rowTop - 18);

  for (let i = 0; i < F.tiers.length; i++) {
    const tier = F.tiers[i], cnt = counts[i];
    const yc = rowTop + i * rowH + barH / 2;
    /* log-schaal: 100.000 vs 7 games blijft in één beeld zichtbaar */
    const frac = Math.log10(cnt + 1) / logMax;
    const len = 70 + frac * (maxW - 70);
    rrect(ctx, X0, yc - barH / 2, maxW, barH, 12);
    ctx.fillStyle = "#16212d";
    ctx.fill();
    rrect(ctx, X0, yc - barH / 2, len, barH, 12);
    ctx.fillStyle = tier.color;
    ctx.fill();
    /* linkerkolom: drempel */
    ctx.textAlign = "right";
    ctx.font = i === 0 ? "700 26px 'Segoe UI', Arial, sans-serif" : "700 30px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = tier.color;
    ctx.fillText(tier.label, 300, yc + 10);
    /* rechts: aantal + percentage */
    ctx.font = "800 44px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = tier.color;
    ctx.fillText(fmtInt(cnt), 1560, yc + 8);
    ctx.font = "500 22px 'Segoe UI', Arial, sans-serif";
    ctx.fillStyle = C.muted;
    const pct = cnt / measured;
    const pctTxt = i === 0 ? "measured games"
      : (pct < 0.0005 ? "<0.1%" : (100 * pct).toFixed(1) + "%") + " of measured";
    ctx.fillText(pctTxt, 1560, yc + 38);
  }

  /* maand-jaar rechtsboven */
  ctx.textAlign = "right";
  ctx.font = "600 26px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText(fmtMonthYear(t), 1562, 64);

  drawNote(ctx, "average players = mean of all collected snapshots · bar length is log-scaled · games without player data are excluded");
}

/* =====================================================================
 * Afspelen
 * ===================================================================== */
function drawFrame(p, dt) {
  if (!state.ready) return;
  switch (state.chart) {
    case "map": drawMap(p); break;
    case "spiral": drawSpiral(p); break;
    case "f2p": drawF2P(p); break;
    case "indie": drawIndie(p); break;
    case "players": drawFunnel(p); break;
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
activateChart("map");
loadData();
