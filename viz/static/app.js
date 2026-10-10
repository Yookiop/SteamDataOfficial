/* =====================================================================
 * Steam-games visualisatie (viz/) - Backgrounds-stijl
 * ---------------------------------------------------------------------
 * - Leest data/games.csv (via fetch -> lokale server).
 * - TWEE GEANIMEERDE grafieken, elk met eigen Play/Restart/Duration en
 *   eigen MP4-export (`anims`, `makeAnim`, `bindAnimControls`):
 *   (1) Tijdlijn: "Amount of Steam games over time" - cumulatief aantal
 *       games (appids) tot elke releasedatum. Elke tick verschuift de
 *       tijdcursor en voegt de releases t/m die datum toe aan de lijn
 *       (tijdsverloop simulatie). De y-as schaalt DYNAMISCH mee met de
 *       teller in GROVE stappen (0..1000 → 10.000 → 50.000 → 150.000 →
 *       ...; `Y_STEPS`/`yCeil`) i.p.v. meteen 0..130.000.
 *   (2) Per jaar: "Games released per year" - één kolom per releasejaar
 *       met het totaal van dat jaar; de kolommen komen jaar na jaar
 *       tevoorschijn en de y-as zoomt VLOEIEND mee (`YEAR_Y_STEPS`/
 *       `yearYCeil` - fijnere stappen dan de tijdlijn, zodat de kolommen
 *       altijd een groot deel van de hoogte vullen). Het lopende jaar
 *       groeit mee met de cursor (aantal releases tot die datum) en de
 *       grote teller toont dat jaar + dat aantal.
 *   Bij een stapwissel ZOOMT de as ~1,2 s vloeiend (met crossfade van de
 *   oude/nieuwe gridlabels) i.p.v. een harde sprong. Aan het einde houdt
 *   elke animatie 10 s de eindstand vast (`END_PAUSE_MS`) voordat de
 *   cyclus opnieuw begint.
 * - De teller loopt VLOEIEND tussen twee releasedatums (`smoothCount`):
 *   in de vroege jaren is één release nog ~4% van de y-as, dus zonder die
 *   interpolatie "hapt" het begin van de animatie (zichtbare hops van
 *   ~20 px, terwijl er tussendoor niets beweegt). Op elke releasedatum is
 *   de waarde exact het cumulatieve aantal en op het eindframe het totaal.
 *   De per-jaar-as heeft daarnaast een vangnet: max(zoomwaarde, hoogste
 *   kolom * 1.03) — de as zoomt vertraagd, waardoor een kolom anders
 *   boven de plot uitsteekt en over de titel/kop heen getekend wordt.
 * - MP4-export: canvas.captureStream + MediaRecorder, download van een
 *   volledige cyclus (geen ffmpeg nodig, zoals Backgrounds).
 * - Filter: alleen releasedatums t/m vandaag (einddatum = vandaag).
 *   Placeholder-datums daarna (bv. 9998-01-01) vallen buiten BEIDE
 *   grafieken en de assen eindigen op vandaag.
 * ===================================================================== */

"use strict";

/* ---------- Kleuren ----------
 * Vast: donker thema. Per-grafiek kleuren (niet gedeeld):
 * chartColors.timeline (lijn/oppervlak/teller = line, stip = accent) en
 * chartColors.yearly (kolommen = color). Elke grafiek heeft eigen kleurkiezer(s). */
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
  yearly:   { color: "#66c0f4" },
};

/* Stijl van de x-/y-aswaarden (ticklabels) + y-as-label (bv. Amount).
 * Instelbaar op de pagina en GEDEELD: elke grafiek (ook toekomstige)
 * gebruikt deze waarden via axisFont()/axis.color/axis.yLabel.
 * Het y-as-label staat standaard UIT (vraag 2026-10-10): de grote teller
 * met zijn bijschrift ("Games released in <jaar>") zegt al genoeg. */
const axis = { size: 20, bold: false, color: C.text, yLabel: false };
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

const MONTHS_FULL = ["January", "February", "March", "April", "May",
                     "June", "July", "August", "September", "October",
                     "November", "December"];

const el = {
  timeline: document.getElementById("timelineCanvas"),
  yearly: document.getElementById("yearlyCanvas"),
  lineColor: document.getElementById("lineColor"),
  accentColor: document.getElementById("accentColor"),
  yearlyColor: document.getElementById("yearlyColor"),
  axisSize: document.getElementById("axisSize"),
  axisSizeVal: document.getElementById("axisSizeVal"),
  axisBold: document.getElementById("axisBold"),
  axisColor: document.getElementById("axisColor"),
  axisYLabel: document.getElementById("axisYLabel"),
  yrAxisSize: document.getElementById("yrAxisSize"),
  yrAxisSizeVal: document.getElementById("yrAxisSizeVal"),
  yrBold: document.getElementById("yrBold"),
  yrAxisColor: document.getElementById("yrAxisColor"),
  yrYLabel: document.getElementById("yrYLabel"),
  dataInfo: document.getElementById("dataInfo"),
  timelineFoot: document.getElementById("timelineFoot"),
  yearlyFoot: document.getElementById("yearlyFoot"),
};

/* Bediening per animatie-grafiek; `el` hierboven bevat de gedeelde
 * stijlregelaars (kleuren van de grafieken + de as-instellingen). */
const animEl = {
  timeline: {
    playBtn: document.getElementById("playBtn"),
    restartBtn: document.getElementById("restartBtn"),
    durSlider: document.getElementById("durSlider"),
    durVal: document.getElementById("durVal"),
    exportBtn: document.getElementById("exportBtn"),
    exportStatus: document.getElementById("exportStatus"),
    recBadge: document.getElementById("recBadge"),
  },
  yearly: {
    playBtn: document.getElementById("yrPlayBtn"),
    restartBtn: document.getElementById("yrRestartBtn"),
    durSlider: document.getElementById("yrDurSlider"),
    durVal: document.getElementById("yrDurVal"),
    exportBtn: document.getElementById("yrExportBtn"),
    exportStatus: document.getElementById("yrExportStatus"),
    recBadge: document.getElementById("yrRecBadge"),
  },
};

const state = {
  ready: false,
  gamesTotal: 0,          // totaal aantal regels in games.csv
  withDate: 0,            // aantal met geldige releasedatum t/m vandaag
  futureDated: 0,         // aantal met releasedatum NA vandaag (placeholder)
  todayMs: 0,             // UTC-middernacht van vandaag (eindgrens = vandaag)
  relTimes: [],           // unieke releasedatums (ms, oplopend)
  relCounts: [],          // cumulatief aantal t/m elke relTime
  yTrans: [],             // vloeiende y-as-overgangen tijdlijn: {p, from, to}
  yearlyTrans: [],        // idem voor de per-jaar-grafiek
  firstYear: 0, lastYear: 0,
  tMin: 0, tMax: 0,       // as-bereik (ms) — tMax = vandaag (einddatum-filter)
  yearly: [],             // [{year, count, cumBefore}] per releasejaar (leemtes = 0)
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

/* Grove y-as-stappen voor de dynamische y-as (vraag 2026-09-27): na 1000
 * meteen naar 10.000, dan 50.000, 150.000, ... (de 100.000 wordt
 * overgeslagen) — bewust GROTE sprongen, zodat de y-labels niet steeds
 * verspringen (flikkeren). */
const Y_STEPS = [1000, 10000, 50000, 150000, 250000, 500000, 1000000];

/* Kleinste y-as-stap ≥ v (lijst hierboven; ver daarboven: verdubbelen). */
function yCeil(v) {
  for (const s of Y_STEPS) {
    if (v <= s + 1e-9) return s;
  }
  let s = Y_STEPS[Y_STEPS.length - 1];
  while (s < v) s *= 2;
  return s;
}

/* Duur van een vloeiende y-as-overgang (ms wall-time; onafhankelijk van de
 * Duration-slider). In dit venster zoomt de as soepel naar de volgende
 * stap i.p.v. een harde sprong. */
const Y_TRANS_MS = 1200;

/* Tickwaarden van een y-as met bovengrens ymax: 5 gelijke delen, zodat de
 * BOVENSTE tick altijd ymax zelf is (de gebruiker wil bv. 150.000 zien,
 * geen 140.000) en alle labels ronde getallen blijven (Y_STEPS zijn rond). */
function yTicks(ymax) {
  const step = ymax / 5;
  const out = [];
  for (let v = 0; v <= ymax + 1e-9; v += step) out.push(v);
  return out;
}

/* Fijnere y-as-stappen voor de per-jaar-grafiek. Daar liggen de waarden
 * (per jaar!) honderden keren lager dan de cumulatieve teller van de
 * tijdlijn, dus de grove Y_STEPS zouden de kolommen jarenlang in een
 * klein hoekje duwen (en aan het eind op 0..50.000 uitkomen). Deze
 * stappen houden de hoogste kolom steeds op ~60-95% van de as. */
const YEAR_Y_STEPS = [25, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000,
                      7500, 10000, 15000, 20000, 25000, 50000, 100000];

/* Kleinste per-jaar-stap ≥ v (lijst hierboven; ver daarboven: verdubbelen). */
function yearYCeil(v) {
  for (const s of YEAR_Y_STEPS) {
    if (v <= s + 1e-9) return s;
  }
  let s = YEAR_Y_STEPS[YEAR_Y_STEPS.length - 1];
  while (s < v) s *= 2;
  return s;
}

/* Stapwissels omzetten in een VLOEIENDE, aaneengesloten keten. Wissels die
 * dichter op elkaar liggen dan het zoomvenster worden samengevoegd (anders
 * zou de as aan het eind van het eerste venster alsnog een harde sprong
 * naar de volgende stap maken) en elke wissel begint bij de stap die op
 * dat moment echt geldt. */
function mergeYTrans(raw, transW) {
  const out = [];
  for (const tr of raw) {
    const last = out[out.length - 1];
    if (last && tr.p < last.p + transW) last.to = tr.to;
    else out.push({ p: tr.p, from: last ? last.to : tr.from, to: tr.to });
  }
  return out;
}

/* De y-as-overgang die op p geldt: de MEEST RECENTE stapwissel (de keten
 * hierboven is oplopend in p). */
function yTransitionAt(trans, p) {
  let tr = null;
  for (const t of trans) {
    if (t.p <= p) tr = t; else break;
  }
  return tr;
}

/* 'yyyy-mm-dd' -> ms sinds epoch (UTC-middernacht, geen TZ-shift). */
function dateToMs(s) {
  const p = s.split("-");
  return Date.UTC(+p[0], +p[1] - 1, +p[2]);
}
function fmtMonthYear(ms) {
  const d = new Date(ms);
  return `${MONTHS_FULL[d.getUTCMonth()]}-${d.getUTCFullYear()}`;
}
/* 'yyyy-mm-dd' (UTC) - voor footers/bijschriften. */
function fmtDateISO(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
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

/* Aantal games met een releasedatum t/m t (state.relTimes is oplopend).
 * Let op: de grafieken gebruiken de VLOEIENDE versie (smoothCount); deze
 * exacte telling blijft alleen staan als referentie/controle. */
function cumCount(t) {
  const i = upperBound(state.relTimes, t);
  return i > 0 ? state.relCounts[i - 1] : 0;
}

/* Vloeiende teller op tijdstip t: tussen twee opeenvolgende releasedatums
 * loopt het cumulatieve aantal gelijkmatig mee met de tijd i.p.v. per
 * release te springen. In de vroege jaren is één release ~4% van de y-as,
 * dus die sprongen zijn goed zichtbaar (het "happeren" van het begin);
 * later is één release minder dan een pixel. Op elke releasedatum is de
 * waarde exact het cumulatieve aantal, en na de laatste release blijft de
 * waarde staan — het eindframe toont dus exact het totaal. */
function smoothCount(t) {
  const ts = state.relTimes, cs = state.relCounts;
  const i = upperBound(ts, t);                 // # releases met tijd <= t
  if (i === 0) return 0;
  if (i >= ts.length) return cs[cs.length - 1];
  const t0 = ts[i - 1], t1 = ts[i];
  const f = t1 > t0 ? (t - t0) / (t1 - t0) : 1;
  return cs[i - 1] + (cs[i] - cs[i - 1]) * f;
}

/* Kolomwaarden van de per-jaar-grafiek op tijdstip t: afgeronde jaren hun
 * totaal, het LOPENDE jaar alleen wat er tot t is uitgekomen (zo groeit die
 * kolom mee met de cursor). `max` = de hoogste kolom op dat moment, voor de
 * dynamische y-as; `total` = het cumulatieve aantal t/m t (de grote teller
 * in de kop toont dat totaal, niet meer het jaar). De waarde van het lopende
 * jaar komt uit smoothCount, dus de kolom groeit vloeiend i.p.v. met één
 * sprong per release. */
function yearValuesAt(t) {
  const rows = state.yearly;
  const cum = smoothCount(t);
  const curYear = new Date(t).getUTCFullYear();
  const vals = new Array(rows.length);
  let max = 0;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    let v;
    if (r.year > curYear) v = 0;
    else if (r.year < curYear) v = r.count;
    else v = Math.max(0, Math.min(r.count, cum - r.cumBefore));
    vals[i] = v;
    if (v > max) max = v;
  }
  return { vals, max, curYear, total: cum };
}

/* =====================================================================
 * Data laden
 * ===================================================================== */
async function loadData() {
  try {
    const gamesCsv = await fetch("../data/games.csv").then((r) => {
      if (!r.ok) throw new Error(`games.csv: HTTP ${r.status}`);
      return r.text();
    });

    const gamesRows = parseCSV(gamesCsv);

    // Games met geldige releasedatum t/m vandaag; tel per datum en per jaar.
    // Releasedatums NA vandaag zijn placeholder-datums van nog niet
    // uitgebrachte games (bv. 9998-01-01) en blijven buiten de grafieken.
    const now = new Date();
    state.todayMs = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    state.gamesTotal = gamesRows.length;
    const byDate = new Map(); // dateStr -> aantal
    const byYear = new Map(); // jaar -> aantal
    let withDate = 0;
    for (const g of gamesRows) {
      const d = g.release_date_fmt;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(d || "")) continue;
      if (dateToMs(d) > state.todayMs) { state.futureDated++; continue; }
      withDate++;
      byDate.set(d, (byDate.get(d) || 0) + 1);
      const yr = +d.slice(0, 4);
      byYear.set(yr, (byYear.get(yr) || 0) + 1);
    }
    state.withDate = withDate;

    // Per-jaar-data: één rij per jaar (ook jaren zonder release = 0), zodat
    // de x-as van de kolomdiagram aaneengesloten blijft. `cumBefore` =
    // aantal releases vóór 1 januari van dat jaar (voor het lopende jaar).
    {
      const years = [...byYear.keys()];
      const y0 = Math.min(...years), y1 = Math.max(...years);
      state.yearly = [];
      for (let y = y0; y <= y1; y++) {
        state.yearly.push({ year: y, count: byYear.get(y) || 0, cumBefore: 0 });
      }
    }

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
    state.firstYear = new Date(times[0]).getUTCFullYear();
    state.lastYear = new Date(state.todayMs).getUTCFullYear();
    // De grafiek begint bij de Steam-lancering (september 2003), dus geen
    // lege voorloop in 2002: start op 1 september van het eerste jaar (of
    // op de eerste releasedatum zelf als die eerder zou liggen).
    state.tMin = Math.min(Date.UTC(state.firstYear, 8, 1),
                          state.relTimes[0]);
    state.tMax = state.todayMs;   // eindgrens = vandaag (geen padding meer)

    // Cumulatief vóór elk jaar (relTimes is oplopend gesorteerd).
    for (const row of state.yearly) {
      const i = upperBound(times, Date.UTC(row.year, 0, 1) - 1);
      row.cumBefore = i > 0 ? counts[i - 1] : 0;
    }

    // Vloeiende y-as: op welke p verspringt de y-as-stap en van/naar welke
    // bovengrens. De grafiek zoomt daar ~1,2 s soepel naartoe (Y_TRANS_MS)
    // i.p.v. een harde sprong. De tijdlijn gebruikt de cumulatieve teller,
    // de per-jaar-grafiek de hoogste kolom op dat moment.
    const pOf = (t) => (t - state.tMin) / (state.tMax - state.tMin);
    state.yTrans = [];
    {
      let prevY = yCeil(0);
      for (let i = 0; i < times.length; i++) {
        const y = yCeil(counts[i] * 1.05);
        if (y !== prevY) {
          state.yTrans.push({ p: pOf(times[i]), from: prevY, to: y });
          prevY = y;
        }
      }
    }
    state.yearlyTrans = [];
    {
      let prevY = yearYCeil(0);
      for (const t of times) {
        const y = yearYCeil(yearValuesAt(t).max * 1.08);
        if (y !== prevY) {
          state.yearlyTrans.push({ p: pOf(t), from: prevY, to: y });
          prevY = y;
        }
      }
    }

    // Footers / koptekst.
    el.dataInfo.textContent =
      `${fmtInt(state.gamesTotal)} games from games.csv · ` +
      `${fmtInt(state.withDate)} with a release date up to today`;
    el.timelineFoot.textContent =
      `${fmtInt(state.withDate)} games with a release date up to today ` +
      `(${fmtDateISO(state.todayMs)}) · ` +
      (state.futureDated
        ? `${fmtInt(state.futureDated)} games dated after today excluded · `
        : "") +
      `${state.relTimes.length} unique release dates · ` +
      `animation from ${state.firstYear} to ${state.lastYear}`;
    const yrs = state.yearly;
    const yearTotal = yrs.reduce((a, b) => a + b.count, 0);
    // Drukste jaar van de VOLLEDIGE jaren (het eerste en het lopende jaar
    // zijn onvolledig en zouden het beeld scheef trekken).
    const full = yrs.slice(1, -1);
    const best = full.reduce((a, b) => (b.count > a.count ? b : a), full[0]);
    el.yearlyFoot.textContent =
      `Based on ${fmtInt(yearTotal)} releases up to today (${fmtDateISO(state.todayMs)}) · ` +
      `${yrs.length} years${best
        ? ` · busiest full year: ${best.year} with ${fmtInt(best.count)} releases`
        : ""} · the first year (${yrs[0].year}) and the current year ` +
      `(${yrs[yrs.length - 1].year}) are partial, so their columns keep growing ` +
      `until the animation reaches the end`;

    state.ready = true;
    drawTimeline(0);
    drawYearly(0);
  } catch (err) {
    el.dataInfo.textContent = "❌ Failed to load";
    const box = document.createElement("div");
    box.className = "error-box";
    box.textContent =
      "Could not load data/games.csv (" + err.message + "). " +
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
  const curCount = smoothCount(tCur);          // vloeiend (geen hops per release)

  /* Dynamische y-as: schaalt mee met de huidige teller in GROVE stappen
   * (Y_STEPS: 1000 → 10.000 → 50.000 → ...) met ~5% kopruimte; minimum
   * 0..1000, want het duurt ~7 jaar voor 1000 games. Bij een stapwissel
   * ZOOMT de as ~1,2 s vloeiend naar de nieuwe bovengrens (smoothstep)
   * i.p.v. een harde sprong. */
  const transW = Y_TRANS_MS / anims.timeline.durMs;   // vensterbreedte in p-eenheden
  const ySteps = mergeYTrans(st.yTrans, transW);
  const trans = yTransitionAt(ySteps, p);
  const transE = trans ? Math.min(1, (p - trans.p) / transW) : 0;
  /* Vóór de eerste wissel geldt de eerste (vaste) stap uit de keten — niet
   * de stap die de teller op dat moment zou vragen: die ligt bij een
   * vloeiende teller soms al één stap voor, waardoor de as zou springen en
   * de wissel daarna weer terugzoomen (zichtbare flikkering). */
  const zoomed = trans
    ? trans.from + (trans.to - trans.from) * (transE * transE * (3 - 2 * transE))
    : (ySteps.length ? ySteps[0].from : yCeil(curCount * 1.05));
  /* Vangnet: de as zakt nooit onder de teller (de teller groeit sneller dan
   * de as zoomt). max() blijft continu bij een stapwissel, dus geen sprong. */
  const ymax = Math.max(zoomed, curCount * 1.03);
  const Y = (v) => y0 + ph - (v / ymax) * ph;

  /* Y-grid + labels — tijdens een overgang cross-faden de tickwaarden van
   * de oude (1-e) en nieuwe (e) as; waarden die in beide sets zitten
   * blijven vol zichtbaar. Alle posities volgen de geïnterpoleerde
   * schaal Y, zodat de as als één geheel rustig uitzoomt. */
  const ticks = new Map();               // tickwaarde -> alpha
  if (trans) {
    const e = transE * transE * (3 - 2 * transE);
    for (const v of yTicks(trans.from)) ticks.set(v, 1 - e);
    for (const v of yTicks(trans.to)) {
      ticks.set(v, Math.min(1, (ticks.get(v) || 0) + e));
    }
  } else {
    for (const v of yTicks(ymax)) ticks.set(v, 1);
  }
  ctx.font = axisFont();
  ctx.textAlign = "right";
  /* Knippen: gridlijnen EXACT op het plot, maar de labels in de
   * linkerkolom met een marge boven de plot — zo blijft de bovenste tick
   * (bv. 150.000, hij hangt met zijn bovenkant boven de rand) volledig
   * zichtbaar en schuiven inkomende labels tijdens een zoom netjes vanaf
   * de rand naar binnen i.p.v. boven de grafiek te zweven. */
  const labelPad = axis.size + 8;      // ruimte voor het bovenste ticklabel
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, y0, pw, ph);                             // plot: gridlijnen
  ctx.rect(0, y0 - labelPad, x0, ph + labelPad + 24);   // labels links
  ctx.clip();
  for (const [v, a] of ticks) {
    if (a < 0.02) continue;              // vrijwel onzichtbaar = overslaan
    ctx.globalAlpha = a;
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x0, Y(v));
    ctx.lineTo(x0 + pw, Y(v));
    ctx.stroke();
    ctx.fillStyle = axis.color;
    ctx.fillText(fmtInt(v), x0 - 14, Y(v) + 7);
    ctx.globalAlpha = 1;
  }
  ctx.restore();

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
  ctx.fillText(fmtInt(Math.round(curCount)), x0, 168);
  ctx.font = "500 35px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("Games released up to " + fmtMonthYear(tCur), x0, 212);
}

/* =====================================================================
 * Per-jaar-grafiek (geanimeerd): één kolom per releasejaar met het totaal
 * van dat jaar. De cursor loopt over dezelfde tijdsas als de tijdlijn; elk
 * jaar komt tevoorschijn zodra de cursor het bereikt en het LOPENDE jaar
 * groeit mee. De y-as zoomt vloeiend mee (YEAR_Y_STEPS, ~1,2 s smoothstep)
 * zodat de vroege jaren groot in beeld staan i.p.v. onzichtbare streepjes.
 * ===================================================================== */
function drawYearly(p) {
  const cv = el.yearly;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  const M = { l: 130, r: 70, t: 235, b: 105 };
  const x0 = M.l, y0 = M.t;
  const pw = W - M.l - M.r, ph = H - M.t - M.b;
  const st = state;
  const rows = st.yearly;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, W, H);

  /* Titel */
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = C.text;
  ctx.font = "700 40px 'Segoe UI', Arial, sans-serif";
  ctx.fillText("Games released per year", W / 2, 58);

  if (!rows.length) return;

  /* Kolomwaarden bij de cursor + de hoogste kolom (voor de y-as) */
  const tCur = st.tMin + p * (st.tMax - st.tMin);
  const { vals, max, total } = yearValuesAt(tCur);

  /* Dynamische y-as: kleinste per-jaar-stap ≥ hoogste kolom; bij een
   * stapwissel zoomt de as ~1,2 s vloeiend (smoothstep) i.p.v. een harde
   * sprong — zelfde aanpak als de tijdlijn, met eigen (fijnere) stappen
   * en 8% kopruimte zodat het getal boven de hoogste kolom vrij blijft
   * van de teller boven de grafiek. */
  const transW = Y_TRANS_MS / anims.yearly.durMs;
  const ySteps = mergeYTrans(st.yearlyTrans, transW);
  const trans = yTransitionAt(ySteps, p);
  const e = trans ? Math.min(1, (p - trans.p) / transW) : 0;
  /* Vóór de eerste wissel geldt de eerste (vaste) stap uit de keten — niet
   * de stap die de hoogste kolom op dat moment zou vragen: bij een vloeiende
   * kolom ligt die soms al één stap voor, waardoor de as zou springen en de
   * wissel daarna weer terugzoomen (zichtbare flikkering). */
  const zoomed = trans
    ? trans.from + (trans.to - trans.from) * (e * e * (3 - 2 * e))
    : (ySteps.length ? ySteps[0].from : yearYCeil(max * 1.08));
  /* Vangnet: tijdens een zoom loopt de as vertraagd mee (de kolommen groeien
   * sneller dan de as zoomt), waardoor een kolom anders boven de plot
   * uitsteekt en over de titel/kop heen getekend wordt. max() houdt de as
   * continu bij een stapwissel, dus geen sprong. */
  const ymax = Math.max(zoomed, max * 1.03);
  const Y = (v) => y0 + ph - (v / ymax) * ph;

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

  /* Y-grid + ticklabels (ronde stappen via niceStep; de as-bovengrens zelf
   * hoeft dus geen rond getal te zijn en het toplabel hoeft niet de
   * bovengrens te zijn). */
  const yStep = niceStep(ymax / 6);
  ctx.font = axisFont();
  ctx.textAlign = "right";
  ctx.textBaseline = "alphabetic";
  for (let v = 0; v <= ymax + 1e-9; v += yStep) {
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

  /* Kolommen (chartColors.yearly.color) + het aantal erboven. De kolommen
   * staan dicht op elkaar, dus het getal krimpt mee tot het binnen de
   * kolombreedte past (alle getallen even groot, dus één meting vooraf).
   * Nog niet bereikte jaren (waarde 0) krijgen geen kolom en geen getal. */
  const n = rows.length;
  const slot = pw / n;
  const barW = slot * 0.62;
  const nums = vals.map((v) => fmtInt(Math.round(v)));
  let numSize = 26;
  ctx.font = `600 ${numSize}px 'Segoe UI', Arial, sans-serif`;
  let numW = Math.max(...nums.map((t) => ctx.measureText(t).width));
  const numMax = slot - 6;
  if (numW > numMax) {
    numSize = Math.max(12, Math.floor(numSize * numMax / numW));
    ctx.font = `600 ${numSize}px 'Segoe UI', Arial, sans-serif`;
    numW = Math.max(...nums.map((t) => ctx.measureText(t).width));
  }
  ctx.textAlign = "center";
  ctx.fillStyle = chartColors.yearly.color;
  for (let i = 0; i < n; i++) {
    if (vals[i] <= 0) continue;
    const cx = x0 + slot * (i + 0.5);
    ctx.fillRect(cx - barW / 2, Y(vals[i]), barW, y0 + ph - Y(vals[i]));
  }
  ctx.fillStyle = C.text;
  for (let i = 0; i < n; i++) {
    if (vals[i] <= 0) continue;
    ctx.fillText(nums[i], x0 + slot * (i + 0.5), Y(vals[i]) - 14);
  }

  /* Jaarlabels onder de as: ALTIJD één label per jaar. Bij een grote
   * Axis-grootte passen 24 jaartallen niet naast elkaar, dus krimpt alleen
   * deze labelrij mee (net als de getallen boven de kolommen) zodat elk
   * jaar er toch staat. */
  ctx.fillStyle = axis.color;
  ctx.font = axisFont();
  ctx.textAlign = "center";
  const lastYearText = String(rows[n - 1].year);
  const yearMaxW = ctx.measureText(lastYearText).width;
  if (yearMaxW > slot - 6) {
    const yearSize = Math.max(10, Math.floor(axis.size * (slot - 6) / yearMaxW));
    ctx.font = `${axis.bold ? 700 : 500} ${yearSize}px 'Segoe UI', Arial, sans-serif`;
  }
  for (let i = 0; i < n; i++) {
    ctx.fillText(String(rows[i].year), x0 + slot * (i + 0.5), y0 + ph + 38);
  }

  /* Grote teller (in de kop boven de plot): het TOTALE aantal games t/m de
   * cursor, zonder jaartal (vraag 2026-10-10) — het getal loopt mee van 0 tot
   * het eindtotaal en is daarmee duidelijk zonder dat er een jaar bij hoeft;
   * de verdeling per jaar zit al in de kolommen. */
  ctx.textAlign = "left";
  ctx.fillStyle = chartColors.yearly.color;
  ctx.font = "800 88px 'Segoe UI', Arial, sans-serif";
  ctx.fillText(fmtInt(Math.round(total)), x0, 168);
  ctx.font = "500 35px 'Segoe UI', Arial, sans-serif";
  ctx.fillStyle = C.muted;
  ctx.fillText("Games released", x0, 212);
}

/* =====================================================================
 * Afspelen (per grafiek een eigen animatie)
 * ===================================================================== */

/* Elke animatie-grafiek heeft eigen playback-state, zodat Play/Restart/
 * Duration/Export per grafiek werken (`anims.timeline`, `anims.yearly`).
 * Let op: `anims` wordt ook door de tekenfuncties gebruikt voor de duur
 * van een y-as-overgang, dus hij staat hierboven niet in `state`. */
const anims = {
  timeline: makeAnim("timeline", el.timeline, drawTimeline,
                     "steam_games_released_timeline", animEl.timeline),
  yearly: makeAnim("yearly", el.yearly, drawYearly,
                   "steam_games_released_per_year", animEl.yearly),
};

function makeAnim(name, canvas, draw, fileBase, ui) {
  return {
    name, canvas, draw, fileBase, ui,
    playing: false,
    elapsed: 0,          // ms "animatietijd" (1 cyclus = durMs + END_PAUSE_MS)
    durMs: 30000,
    p: 0,                // laatst getekende p (voor hertekenen bij stijlwijziging)
    lastTs: 0,
    recording: false,
    cancelExport: false,
    exportTimer: null,
    mediaRec: null,
    chunks: [],
  };
}

function anyRecording() {
  return Object.values(anims).some((a) => a.recording);
}

function setPlayUI(anim) {
  anim.ui.playBtn.textContent = anim.playing ? "⏸ Pause" : "▶ Play";
}

/* Pauze op het einde van de animatie: 10 s de eindstand vasthouden voordat
 * de volgende cyclus begint (volledige cyclus = durMs + END_PAUSE_MS). */
const END_PAUSE_MS = 10000;

function loop(ts) {
  for (const anim of Object.values(anims)) {
    if (state.ready && anim.playing) {
      if (!anim.lastTs) anim.lastTs = ts;
      const dt = ts - anim.lastTs;
      anim.elapsed += dt;
      const cycle = anim.durMs + END_PAUSE_MS;
      const et = anim.elapsed % cycle;                   // tijd in de cyclus
      const p = et >= anim.durMs ? 1 : et / anim.durMs;  // laatste 10 s: eindstand
      anim.p = p;
      anim.draw(p);
    }
    anim.lastTs = ts;
  }
  requestAnimationFrame(loop);
}

function redrawCharts() {
  if (!state.ready) return;
  for (const anim of Object.values(anims)) {
    if (!anim.playing) anim.draw(anim.p);
  }
}

/* Koppel een as-config-regel (grootte/vet/kleur/y-label) aan de gedeelde
 * axis-instellingen. Zo werkt dezelfde config boven elke grafiek (en elke
 * toekomstige grafiek die hem toevoegt). */
function bindAxisGroup(g) {
  g.size.addEventListener("input", () => {
    if (anyRecording()) return;
    axis.size = +g.size.value;
    syncAxisUI();
    redrawCharts();
  });
  g.bold.addEventListener("change", () => {
    if (anyRecording()) return;
    axis.bold = g.bold.checked;
    syncAxisUI();
    redrawCharts();
  });
  g.color.addEventListener("input", () => {
    if (anyRecording()) return;
    axis.color = g.color.value;
    syncAxisUI();
    redrawCharts();
  });
  g.ylabel.addEventListener("change", () => {
    if (anyRecording()) return;
    axis.yLabel = g.ylabel.checked;
    syncAxisUI();
    redrawCharts();
  });
}

/* Spiegel de gedeelde axis-instellingen naar alle regelaars op de pagina. */
function syncAxisUI() {
  el.axisSize.value = axis.size;
  el.yrAxisSize.value = axis.size;
  el.axisSizeVal.value = axis.size;
  el.yrAxisSizeVal.value = axis.size;
  el.axisBold.checked = axis.bold;
  el.yrBold.checked = axis.bold;
  el.axisColor.value = axis.color;
  el.yrAxisColor.value = axis.color;
  el.axisYLabel.checked = axis.yLabel;
  el.yrYLabel.checked = axis.yLabel;
}

/* Play/Restart/Duration/Export van één grafiek. */
function bindAnimControls(anim) {
  const ui = anim.ui;
  ui.playBtn.addEventListener("click", () => {
    if (anyRecording() || !state.ready) return;
    anim.playing = !anim.playing;
    anim.lastTs = 0;
    setPlayUI(anim);
  });
  ui.restartBtn.addEventListener("click", () => {
    if (anyRecording() || !state.ready) return;
    anim.elapsed = 0;
    anim.p = 0;
    if (!anim.playing) anim.draw(0);
    setPlayUI(anim);
  });
  ui.durSlider.addEventListener("input", () => {
    if (anyRecording()) return;
    anim.durMs = +ui.durSlider.value * 1000;
    ui.durVal.value = ui.durSlider.value;
    if (!anim.playing) anim.draw(anim.p);
  });
  ui.exportBtn.addEventListener("click", () => {
    if (anim.recording) stopExport(anim, true);  // knop = annuleren tijdens opname
    else startExport(anim);
  });
}

function bindControls() {
  el.lineColor.addEventListener("input", () => {
    if (anyRecording()) return;
    chartColors.timeline.line = el.lineColor.value;
    redrawCharts();
  });
  el.accentColor.addEventListener("input", () => {
    if (anyRecording()) return;
    chartColors.timeline.accent = el.accentColor.value;
    redrawCharts();
  });
  el.yearlyColor.addEventListener("input", () => {
    if (anyRecording()) return;
    chartColors.yearly.color = el.yearlyColor.value;
    anims.yearly.draw(anims.yearly.p);
  });
  bindAxisGroup({
    size: el.axisSize, sizeVal: el.axisSizeVal,
    bold: el.axisBold, color: el.axisColor, ylabel: el.axisYLabel,
  });
  bindAxisGroup({
    size: el.yrAxisSize, sizeVal: el.yrAxisSizeVal,
    bold: el.yrBold, color: el.yrAxisColor, ylabel: el.yrYLabel,
  });
  syncAxisUI();
  for (const anim of Object.values(anims)) bindAnimControls(anim);
}

function setControlsDisabled(disabled) {
  el.lineColor.disabled = disabled;
  el.accentColor.disabled = disabled;
  el.yearlyColor.disabled = disabled;
  el.axisSize.disabled = disabled;
  el.axisBold.disabled = disabled;
  el.axisColor.disabled = disabled;
  el.axisYLabel.disabled = disabled;
  el.yrAxisSize.disabled = disabled;
  el.yrBold.disabled = disabled;
  el.yrAxisColor.disabled = disabled;
  el.yrYLabel.disabled = disabled;
  for (const anim of Object.values(anims)) {
    const ui = anim.ui;
    ui.durSlider.disabled = disabled;
    ui.exportBtn.disabled = disabled;
    // De Stop-knop van de opnemende grafiek moet juist WEL klikbaar blijven.
    ui.playBtn.disabled = disabled;
    ui.restartBtn.disabled = disabled;
  }
  if (disabled) {
    for (const anim of Object.values(anims)) {
      if (anim.recording) anim.ui.exportBtn.disabled = false;
    }
  }
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

function startExport(anim) {
  if (anyRecording() || !state.ready) return;
  const ui = anim.ui;
  let stream;
  try { stream = anim.canvas.captureStream(60); }
  catch (e) {
    ui.exportStatus.textContent = "❌ captureStream is not supported in this browser.";
    ui.exportStatus.hidden = false;
    return;
  }
  const mime = pickMime();
  if (!mime) {
    ui.exportStatus.textContent = "❌ MediaRecorder is not supported in this browser.";
    ui.exportStatus.hidden = false;
    return;
  }

  anim.recording = true;
  anim.cancelExport = false;
  anim.playing = true;      // zorg dat de animatie draait tijdens opname
  anim.elapsed = 0;
  anim.lastTs = 0;
  anim.chunks = [];
  setPlayUI(anim);

  try {
    anim.mediaRec = new MediaRecorder(stream, {
      mimeType: mime,
      videoBitsPerSecond: 12_000_000,
    });
  } catch (e) {
    anim.mediaRec = new MediaRecorder(stream);
  }
  const rec = anim.mediaRec;
  rec.ondataavailable = (ev) => {
    if (ev.data && ev.data.size) anim.chunks.push(ev.data);
  };
  rec.onstop = () => finalizeExport(anim);

  // UI: opname-modus (alle andere bediening op de pagina op slot)
  ui.recBadge.hidden = false;
  ui.exportBtn.textContent = "■ Stop (cancel MP4)";
  ui.exportBtn.classList.add("recording");
  ui.exportStatus.hidden = false;
  ui.exportStatus.textContent =
    `⏺ Recording… one full cycle (${ui.durSlider.value} s) — keep this tab visible.`;
  setControlsDisabled(true);

  rec.start(250);
  anim.exportTimer = setTimeout(() => stopExport(anim, false), anim.durMs + 500);
}

function stopExport(anim, abort) {
  if (!anim.recording) return;
  anim.cancelExport = abort;
  clearTimeout(anim.exportTimer);
  anim.exportTimer = null;
  if (anim.mediaRec && anim.mediaRec.state !== "inactive") {
    try { anim.mediaRec.stop(); } catch (e) { /* negeren */ }
  }
  // finalizeExport wordt via rec.onstop aangeroepen.
}

function finalizeExport(anim) {
  const ui = anim.ui;
  const wasCancelled = anim.cancelExport;
  const type = (anim.mediaRec && anim.mediaRec.mimeType) || "video/mp4";

  anim.recording = false;
  anim.cancelExport = false;
  anim.playing = false;
  anim.mediaRec = null;
  anim.lastTs = 0;

  // UI terugzetten
  ui.recBadge.hidden = true;
  ui.exportBtn.textContent = "⬇ Export MP4";
  ui.exportBtn.classList.remove("recording");
  setControlsDisabled(false);
  setPlayUI(anim);

  if (wasCancelled) {
    ui.exportStatus.textContent = "Export cancelled.";
    anim.chunks = [];
    return;
  }

  const blob = new Blob(anim.chunks, { type });
  anim.chunks = [];
  const ext = type.indexOf("mp4") >= 0 ? "mp4" : "webm";
  const name = anim.fileBase + "." + ext;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  ui.exportStatus.textContent = `✅ Downloaded: ${name} (${fmtInt(Math.round(blob.size / 1024))} kB).`;
}

/* =====================================================================
 * Start
 * ===================================================================== */
requestAnimationFrame(loop);
bindControls();
loadData();
