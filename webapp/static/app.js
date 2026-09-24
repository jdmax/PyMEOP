/* PyMEOP data browser.

   Talks to the read only API in server.py and draws two levels: how the fitted
   quantities move over time across any number of runs, with exponential fits to
   the polarization, and one scan with its fit and the fit components. Scans are stored in whatever order the current ramped, so
   every trace is sorted on x before it reaches uPlot, which requires it. */

'use strict';

const state = {
  files: [],
  run: null,          // detail for the selected file
  event: null,        // detail for the selected scan
  eventIdx: 0,
  r0: 1,
  runCursorIdx: null,
  version: null,      // data directory fingerprint the page was last drawn from
  updatedAt: null,    // when a live update last changed what is on screen
  dir: null,          // folder of event files, or null for the server's default
  dirPath: '',        // that folder's full path, as the server resolved it
  loading: false,     // reading a folder's file list; live checks wait meanwhile
};

const charts = { run: null, scan: null, resid: null };

const el = (id) => document.getElementById(id);

/* An API address in the folder this tab is looking at */
function api(path) {
  if (!state.dir) return 'api/' + path;
  return 'api/' + path + (path.indexOf('?') < 0 ? '?' : '&') + 'dir=' + encodeURIComponent(state.dir);
}

/* ---------------- formatting ---------------- */

const DASH = '–';

function fmt(v, sig) {
  if (v === null || v === undefined || !isFinite(v)) return DASH;
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(3);
  return String(Number(v.toPrecision(sig || 5)));
}

function fmtAxis(v) {
  if (v === null || !isFinite(v)) return '';
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(1);
  return String(Number(v.toPrecision(4)));
}

function fmtDuration(s) {
  if (s === null || s === undefined || !isFinite(s)) return DASH;
  if (s < 90) return s.toFixed(1) + ' s';
  const m = Math.floor(s / 60);
  if (m < 60) return m + ' min ' + Math.round(s - m * 60) + ' s';
  return Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
}

function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + ' kB';
  return (b / 1048576).toFixed(1) + ' MB';
}

function fmtStamp(stamp) {
  if (!stamp) return DASH;
  return new Date(stamp * 1000).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

/* Run name carries the start time; fall back to it for files with no readable events. */
function labelFromName(name) {
  const m = name.match(/(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})/);
  if (!m) return name;
  return m[1] + '-' + m[2] + '-' + m[3] + '  ' + m[4] + ':' + m[5] + ':' + m[6];
}

/* The run start in a file name. The DAQ writes current_<start>.txt and renames
   it <start>__<stop>.txt when it closes it, so this is what stays the same. */
function runStamp(name) {
  const m = name.match(/\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}/);
  return m ? m[0] : name;
}

/* ---------------- palette ---------------- */

function palette() {
  const cs = getComputedStyle(document.documentElement);
  const get = (n) => cs.getPropertyValue(n).trim();
  return {
    s1: get('--series-1'), s2: get('--series-2'), s3: get('--series-3'),
    s4: get('--series-4'), base: get('--series-base'),
    grid: get('--grid'), axis: get('--axis'), ink: get('--ink-2'), muted: get('--muted'),
    surface: get('--surface'),
  };
}

/* ---------------- fit helpers ---------------- */

const GAUSS_PARAM_NAMES = [
  'Peak 1 position', 'Peak 1 σ', 'Peak 1 height',
  'Peak 2 position', 'Peak 2 σ', 'Peak 2 height',
];

/* Names for the baseline coefficients, which are the parameters after the six
   gaussian ones, highest power first. A straight baseline has two of them and a
   quadratic three, so the names are taken from the end. */
const BASELINE_PARAM_NAMES = ['Baseline curvature', 'Baseline slope', 'Baseline offset'];

function paramNames(pf) {
  const nBase = Math.max(0, (pf ? pf.length : 0) - GAUSS_PARAM_NAMES.length);
  return GAUSS_PARAM_NAMES.concat(BASELINE_PARAM_NAMES.slice(-nBase || undefined));
}

/* Polarization from the two fitted peak heights.

   r is the height ratio for this scan and r0 the same ratio with the target
   unpolarized, so r/r0 is what the polarization is actually read off. r0 is a
   property of the setup rather than of the file, so it is entered in the UI. */
function polarization(peak1, peak2, r0) {
  if (peak1 === null || peak2 === null || !peak2 || !r0 || !isFinite(r0)) return null;
  const ratio = (peak1 / peak2) / r0;
  if (!isFinite(ratio) || ratio === -1) return null;
  return (ratio - 1) / (ratio + 1);
}

function heightRatio(ev) {
  if (ev.peak1 === null || !ev.peak2) return null;
  const r = ev.peak1 / ev.peak2;
  return isFinite(r) ? r : null;
}

/* ---------------- sorting ---------------- */

/* uPlot needs x ascending; a scan may have been taken with the current ramping
   down, so sort once and reuse the order for every series in the scan. */
function sortedOrder(x) {
  const idx = x.map((v, i) => i);
  idx.sort((a, b) => {
    const va = x[a], vb = x[b];
    if (va === null) return 1;
    if (vb === null) return -1;
    return va - vb;
  });
  return idx;
}

function reorder(arr, order) {
  if (!arr || !arr.length) return null;
  return order.map((i) => (i < arr.length ? arr[i] : null));
}

/* ---------------- charts ---------------- */

function axisOpts(p, label, isX) {
  return {
    label: label,
    labelSize: 26,
    labelFont: '500 11px system-ui, sans-serif',
    font: '11px system-ui, sans-serif',
    stroke: p.muted,
    grid: { stroke: p.grid, width: 1 },
    ticks: { stroke: p.axis, width: 1, size: 4 },
    size: isX ? 42 : 60,
    values: (u, splits) => splits.map(fmtAxis),
  };
}

/* One sigma whiskers on the run chart. uPlot has no error bar series, so they
   are drawn straight onto the canvas under the line. */
function errorBars(sigmasFor) {
  return {
    hooks: {
      draw: (u) => {
        if (!el('showErr').checked) return;
        const ctx = u.ctx;
        ctx.save();
        // hand drawn marks are not clipped for us; a wide sigma would otherwise
        // run out over the axes and the legend
        ctx.beginPath();
        ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height);
        ctx.clip();
        ctx.lineCap = 'butt';
        for (let si = 1; si < u.series.length; si++) {
          const s = u.series[si];
          if (s.show === false) continue;
          const sig = sigmasFor(si);
          if (!sig) continue;
          const ys = u.data[si];
          ctx.strokeStyle = s.stroke();
          ctx.globalAlpha = 0.5;
          ctx.lineWidth = Math.max(1, Math.round(devicePixelRatio));
          const cap = 2.5 * devicePixelRatio;
          ctx.beginPath();
          for (let i = 0; i < ys.length; i++) {
            const y = ys[i], e = sig[i];
            if (y == null || e == null || !isFinite(e) || e <= 0) continue;
            const x = Math.round(u.valToPos(u.data[0][i], 'x', true));
            const lo = u.valToPos(y - e, s.scale, true);
            const hi = u.valToPos(y + e, s.scale, true);
            if (Math.abs(lo - hi) < 1.5) continue;   // nothing to see at this zoom
            ctx.moveTo(x, lo); ctx.lineTo(x, hi);
            ctx.moveTo(x - cap, lo); ctx.lineTo(x + cap, lo);
            ctx.moveTo(x - cap, hi); ctx.lineTo(x + cap, hi);
          }
          ctx.stroke();
        }
        ctx.restore();
      },
    },
  };
}

/* Direct labels at the apex of each fitted gaussian. Two of the scan colours sit
   below 3:1 on the light surface, and a visible label is the documented relief;
   the peak positions are worth reading straight off the plot in any case. */
function peakLabels(ev, p) {
  return {
    hooks: {
      draw: (u) => {
        if (!ev.pf || ev.pf.length < 6) return;
        const peaks = [
          { pos: ev.pf[0], height: ev.pf[2], label: 'Peak 1', colour: p.s3 },
          { pos: ev.pf[3], height: ev.pf[5], label: 'Peak 2', colour: p.s4 },
        ];
        const ctx = u.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height);
        ctx.clip();
        ctx.font = '600 ' + (11 * devicePixelRatio) + 'px system-ui, sans-serif';
        ctx.textAlign = 'center';
        peaks.forEach((pk) => {
          if (!isFinite(pk.pos) || !isFinite(pk.height)) return;
          // the component is drawn sitting on the baseline, so the apex is too
          const base = baselineAt(ev, pk.pos);
          const x = u.valToPos(pk.pos, 'x', true);
          const y = u.valToPos(pk.height + base, 'y', true);
          if (!isFinite(x) || !isFinite(y)) return;
          if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) return;
          const up = pk.height >= 0;
          ctx.textBaseline = up ? 'bottom' : 'top';
          const ty = y + (up ? -7 : 7) * devicePixelRatio;
          ctx.lineWidth = 3 * devicePixelRatio;
          ctx.strokeStyle = p.surface;
          ctx.strokeText(pk.label, x, ty);   // halo, so the label clears the trace
          ctx.fillStyle = pk.colour;
          ctx.fillText(pk.label, x, ty);
        });
        ctx.restore();
      },
    },
  };
}

/* Baseline of the stored fit evaluated at one x, for placing the apex labels. */
function baselineAt(ev, x) {
  const coef = ev.pf.slice(GAUSS_PARAM_NAMES.length);
  if (!coef.length) return 0;
  const referenced = ev.baseline ? ev.baseline.referenced : ev.pf.length >= 9;
  const xv = referenced ? x - (ev.x_ref || 0) : x;
  return coef.reduce((acc, c) => acc * xv + c, 0);   // Horner, highest power first
}

/* A rule through the scan that is open below, so the time plot says which point
   the lower plot is showing, when that scan is in the plot at all. */
function selectionMarker() {
  return {
    hooks: {
      draw: (u) => {
        const k = openRow();
        if (k < 0) return;
        const xv = tl.xs[k];
        if (xv === undefined || xv === null || !isFinite(xv)) return;
        const x = Math.round(u.valToPos(xv, 'x', true));
        if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) return;
        const ctx = u.ctx;
        ctx.save();
        ctx.strokeStyle = u.__accent;
        ctx.lineWidth = Math.max(1, Math.round(devicePixelRatio));
        ctx.setLineDash([4 * devicePixelRatio, 3 * devicePixelRatio]);
        ctx.beginPath();
        ctx.moveTo(x, u.bbox.top);
        ctx.lineTo(x, u.bbox.top + u.bbox.height);
        ctx.stroke();
        ctx.restore();
      },
    },
  };
}

/* A hairline at y = 0 so the residual chart has something to read against. */
function zeroLine(p) {
  return {
    hooks: {
      draw: (u) => {
        const y = u.valToPos(0, 'y', true);
        if (!isFinite(y)) return;
        const ctx = u.ctx;
        ctx.save();
        ctx.strokeStyle = p.axis;
        ctx.lineWidth = Math.max(1, Math.round(devicePixelRatio));
        ctx.beginPath();
        ctx.moveTo(u.bbox.left, y);
        ctx.lineTo(u.bbox.left + u.bbox.width, y);
        ctx.stroke();
        ctx.restore();
      },
    },
  };
}

/* uPlot needs the canvas size in pixels and renders its legend underneath, in
   flow. So the container is left to size itself to canvas plus legend, and the
   canvas height is read from the --plot-h custom property the stylesheet sets. */
function sizeFor(node) {
  const h = parseFloat(getComputedStyle(node).getPropertyValue('--plot-h'));
  return {
    width: Math.max(220, node.clientWidth),
    height: Math.max(90, isFinite(h) ? h : 200),
  };
}

function makePlot(node, opts, data) {
  if (node._plot) { node._plot.destroy(); node._plot = null; }
  node.innerHTML = '';
  const plot = new uPlot(Object.assign(sizeFor(node), opts), data, node);
  node._plot = plot;
  return plot;
}

/* ---------------- time plot ---------------- */

/* The top plot spans every run ticked in the file list, merged into one line
   of scans in time order, while the run bar and the scan plot below describe
   the one run that is open. Runs are kept by their start stamp, so a file the
   DAQ renames on closing stays in the plot.

   Every scan is swept one way, low to high current or high to low. The two
   directions are drawn as separate series so any difference between them
   shows, and the note above the plot puts a number on it. */

const tl = {
  stamps: new Set(),   // runs in the plot, by runStamp()
  cache: {},           // file name -> run detail, reused while size and mtime hold
  gen: 0,              // guards against an older load finishing after a newer one
  events: [],          // scan summaries of every run in the plot, oldest first
  rows: [],            // plot row -> event, or null for a row that breaks the line
  xs: [],              // plot row -> x value
  tx: [],              // [start stamp, x] of each real row, for placing fit curves
  selection: null,     // {t0, t1}: start stamps bounding the scans picked to fit
  fits: [],            // results of the last fit, one per group of scans fitted
  dragSelect: false,   // dragging on the plot selects for a fit instead of zooming
  lastCheck: null,     // last tick clicked in the file list, for shift-click ranges
};

const DIRS = {
  up: { label: 'Low → high', arrow: '↑' },
  down: { label: 'High → low', arrow: '↓' },
};

function shownDirs() {
  const v = el('direction').value;
  return v === 'both' ? ['up', 'down'] : [v];
}

/* One sigma on the polarization, from the uncertainties on the two peak
   heights, taken as independent. With q = (h1/h2)/r0 and P = (q-1)/(q+1),
   dP/dq = 2/(q+1)^2. */
function polarizationErr(e, r0) {
  const h1 = e.peak1, h2 = e.peak2;
  const s1 = e.pstd ? e.pstd[2] : null, s2 = e.pstd ? e.pstd[5] : null;
  if (h1 === null || h2 === null || !h1 || !h2 || !r0 || s1 == null || s2 == null) return null;
  const q = (h1 / h2) / r0;
  const sq = Math.abs(q) * Math.hypot(s1 / h1, s2 / h2);
  const v = Math.abs(2 / ((q + 1) * (q + 1)) * sq) * 100;
  return isFinite(v) ? v : null;
}

function polPercent(e) {
  const p = polarization(e.peak1, e.peak2, state.r0);
  return p === null ? null : p * 100;
}

const METRICS = {
  peaks: {
    axis: () => 'Peak height (lock-in R)',
    series: [
      { name: 'Peak 1 height', pick: (e) => e.peak1, err: (e) => e.pstd[2], color: 's3' },
      { name: 'Peak 2 height', pick: (e) => e.peak2, err: (e) => e.pstd[5], color: 's4' },
    ],
  },
  positions: {
    axis: (x_key) => 'Peak position' + (x_key === 'wavelength' ? '' : ' (A)'),
    series: [
      { name: 'Peak 1 position', pick: (e) => e.pf[0], err: (e) => e.pstd[0], color: 's3' },
      { name: 'Peak 2 position', pick: (e) => e.pf[3], err: (e) => e.pstd[3], color: 's4' },
    ],
  },
  widths: {
    axis: (x_key) => 'Peak width σ' + (x_key === 'wavelength' ? '' : ' (A)'),
    series: [
      { name: 'Peak 1 σ', pick: (e) => e.pf[1], err: (e) => e.pstd[1], color: 's3' },
      { name: 'Peak 2 σ', pick: (e) => e.pf[4], err: (e) => e.pstd[4], color: 's4' },
    ],
  },
  polarization: {
    axis: () => 'Polarization (%)',
    unit: ' %',
    series: [
      { name: 'P', pick: polPercent, err: (e) => polarizationErr(e, state.r0), color: 's1' },
    ],
  },
  ratio: {
    axis: () => 'Height ratio r',
    series: [{ name: 'r', pick: (e) => heightRatio(e), err: null, color: 's1' }],
  },
  rsq: {
    axis: () => 'R²',
    series: [{ name: 'R²', pick: (e) => e.rsq, err: null, color: 's1' }],
  },
};

function pick(fn, e) {
  try {
    const v = fn(e);
    return (v === undefined || v === null || !isFinite(v)) ? null : v;
  } catch (err) {
    return null;
  }
}

/* A value with its uncertainty, rounded to two significant figures of the
   uncertainty. */
function fmtPM(v, err) {
  if (v === null || v === undefined || !isFinite(v)) return DASH;
  if (err === null || err === undefined || !isFinite(err) || err <= 0) return fmt(v, 4);
  const d = Math.min(8, Math.max(0, 1 - Math.floor(Math.log10(err))));
  return v.toFixed(d) + ' ± ' + err.toFixed(d);
}

/* A time constant in whichever unit keeps it readable. */
function fmtTau(tau, err) {
  const [div, unit] = tau < 120 ? [1, 's'] : tau < 7200 ? [60, 'min'] : [3600, 'h'];
  return fmtPM(tau / div, err === null ? null : err / div) + ' ' + unit;
}

function fmtShort(stamp) {
  if (!stamp) return DASH;
  return new Date(stamp * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/* ---- loading ---- */

/* Fetch whatever the plot needs that is not already cached, and merge it.
   Returns false if a newer load started meanwhile, so the caller skips drawing. */
async function loadTimeline() {
  const gen = ++tl.gen;
  const rows = state.files.filter((f) => f.n_events > 0 && tl.stamps.has(runStamp(f.name)));
  const need = rows.filter((f) => {
    const c = tl.cache[f.name];
    return !c || c.size !== f.size || c.mtime !== f.mtime;
  });
  // a few at a time, so ticking every run does not fire a hundred requests at once
  for (let i = 0; i < need.length; i += 6) {
    const got = await Promise.all(need.slice(i, i + 6).map((f) =>
      getJSON(api('file/' + encodeURIComponent(f.name)))));
    got.forEach((r) => { tl.cache[r.name] = r; });
  }
  if (gen !== tl.gen) return false;

  const keep = new Set(rows.map((f) => f.name));
  Object.keys(tl.cache).forEach((n) => { if (!keep.has(n)) delete tl.cache[n]; });
  const events = [];
  rows.forEach((f) => {
    const r = tl.cache[f.name];
    if (!r) return;
    r.events.forEach((e, i) => {
      if (e.start_stamp) events.push(Object.assign({ file: r.name, idx: i }, e));
    });
  });
  events.sort((a, b) => a.start_stamp - b.start_stamp);
  tl.events = events;
  return true;
}

async function setTimeline(stamps) {
  tl.stamps = stamps;
  tl.fits = [];
  renderFileList();
  if (await loadTimeline()) drawTimeline();
  renderFits();
  writeHash();
}

/* The quick picks above the file list. The windows run back from the end of
   the open run, so on a live run they are the last day or week of data. */
function applyPreset(kind) {
  const withScans = state.files.filter((f) => f.n_events > 0);
  let chosen;
  if (kind === 'all') {
    chosen = withScans;
  } else if (kind === 'open') {
    chosen = state.run ? withScans.filter((f) => runStamp(f.name) === runStamp(state.run.name)) : [];
  } else {
    const ends = withScans.map((f) => f.stop_stamp || f.start_stamp || 0);
    const end = (state.run && state.run.stop_stamp) || Math.max.apply(null, ends.concat([0]));
    const span = kind === 'day' ? 86400 : 7 * 86400;
    chosen = withScans.filter((f) =>
      (f.stop_stamp || f.start_stamp || 0) >= end - span && (f.start_stamp || 0) <= end);
  }
  setTimeline(new Set(chosen.map((f) => runStamp(f.name)))).catch(showError);
}

/* ---- rows ---- */

/* Plot rows in time order, with a null row between runs and across any pause
   much longer than the scan cadence, so no line is drawn across hours with no
   data. Everywhere else a direction's series simply skips the other
   direction's rows, which uPlot draws straight across. */
function buildRows(events, byTime) {
  const dts = [];
  for (let i = 1; i < events.length; i++) {
    if (events[i].file === events[i - 1].file) dts.push(events[i].start_stamp - events[i - 1].start_stamp);
  }
  const cadence = median(dts) || 0;
  const rows = [], xs = [];
  events.forEach((e, i) => {
    if (i > 0) {
      const prev = events[i - 1];
      const dt = e.start_stamp - prev.start_stamp;
      if (runStamp(e.file) !== runStamp(prev.file) || (cadence > 0 && dt > 10 * cadence)) {
        rows.push(null);
        xs.push(byTime ? (prev.start_stamp + e.start_stamp) / 2 : i + 0.5);
      }
    }
    rows.push(e);
    xs.push(byTime ? e.start_stamp : i + 1);
  });
  return { rows, xs, cadence };
}

/* Row of the scan open below, or -1 if that scan is not in the plot. */
function openRow() {
  if (!state.run) return -1;
  const st = runStamp(state.run.name);
  return tl.rows.findIndex((e) => e && e.idx === state.eventIdx && runStamp(e.file) === st);
}

function inSelection(e) {
  return !!tl.selection && e.start_stamp >= tl.selection.t0 && e.start_stamp <= tl.selection.t1;
}

function selectedEvents(dir) {
  return tl.events.filter((e) => inSelection(e) && (dir ? e.direction === dir : true));
}

/* x on the plot for a time, which in scan number mode means interpolating
   between the scans either side of it. */
function timeToX(t) {
  if (el('runX').value === 'time') return t;
  const tx = tl.tx;
  if (!tx.length) return null;
  if (t <= tx[0][0]) return tx[0][1];
  for (let k = 1; k < tx.length; k++) {
    if (t <= tx[k][0]) {
      const [ta, xa] = tx[k - 1], [tb, xb] = tx[k];
      return tb > ta ? xa + (xb - xa) * (t - ta) / (tb - ta) : xb;
    }
  }
  return tx[tx.length - 1][1];
}

/* ---- plugins ---- */

/* The scans picked for a fit, shaded behind the data. Drawn here rather than
   left to uPlot's drag box so it survives redraws and live updates. */
function selectionBand() {
  return {
    hooks: {
      drawClear: (u) => {
        if (!tl.selection) return;
        let lo = Infinity, hi = -Infinity;
        tl.rows.forEach((e, k) => {
          if (e && inSelection(e)) { lo = Math.min(lo, tl.xs[k]); hi = Math.max(hi, tl.xs[k]); }
        });
        if (!(hi >= lo)) return;
        const pad = 5 * devicePixelRatio;
        const x0 = Math.max(u.bbox.left, u.valToPos(lo, 'x', true) - pad);
        const x1 = Math.min(u.bbox.left + u.bbox.width, u.valToPos(hi, 'x', true) + pad);
        if (x1 <= x0) return;
        const ctx = u.ctx;
        ctx.save();
        ctx.globalAlpha = 0.12;
        ctx.fillStyle = palette().s1;
        ctx.fillRect(x0, u.bbox.top, x1 - x0, u.bbox.height);
        ctx.restore();
      },
    },
  };
}

function fitColour(key, p) {
  return key === 'up' ? p.s1 : key === 'down' ? p.s2 : p.ink;
}

/* The fitted exponentials, over the span of the scans each was fitted to. */
function fitCurves() {
  return {
    hooks: {
      draw: (u) => {
        if (!tl.fits.length || el('metric').value !== 'polarization') return;
        const p = palette();
        const ctx = u.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height);
        ctx.clip();
        ctx.lineJoin = 'round';
        // per direction first, the combined fit last so it stays on top
        tl.fits.slice().sort((a, b) => (a.key === 'all') - (b.key === 'all')).forEach((f) => {
          if (f.error) return;
          ctx.beginPath();
          let pen = false;
          for (let k = 0; k <= 240; k++) {
            const t = f.t0 + (f.t1 - f.t0) * k / 240;
            const y = f.p_inf + (f.p_0 - f.p_inf) * Math.exp(-(t - f.t0) / f.tau);
            const xv = timeToX(t);
            if (xv === null) continue;
            const px = u.valToPos(xv, 'x', true), py = u.valToPos(y, 'y', true);
            if (!isFinite(px) || !isFinite(py)) continue;
            if (pen) ctx.lineTo(px, py); else ctx.moveTo(px, py);
            pen = true;
          }
          if (f.key === 'all') {
            // the combined fit sits between the two directions: a fine dashed
            // line, so it does not bury them
            ctx.setLineDash([5 * devicePixelRatio, 4 * devicePixelRatio]);
            ctx.strokeStyle = fitColour(f.key, p);
            ctx.lineWidth = 1.5 * devicePixelRatio;
            ctx.stroke();
            ctx.setLineDash([]);
            return;
          }
          // a thin halo in the surface colour separates the curve from the
          // points of its own colour without hiding them
          ctx.strokeStyle = p.surface;
          ctx.lineWidth = 4 * devicePixelRatio;
          ctx.stroke();
          ctx.strokeStyle = fitColour(f.key, p);
          ctx.lineWidth = 2 * devicePixelRatio;
          ctx.stroke();
        });
        ctx.restore();
      },
    },
  };
}

/* ---- drawing ---- */

/* The x range the user has zoomed the time plot to, or null if it shows
   everything. Only kept while the plot shows the same view, so a live redraw
   keeps the zoom but choosing another metric still resets it. */
function runZoom(node, viewKey) {
  const u = node._plot;
  if (!u || node._viewKey !== viewKey) return null;
  const xs = u.data[0].filter((v) => v !== null && isFinite(v));
  if (!xs.length) return null;
  const { min, max } = u.scales.x;
  const lo = Math.min.apply(null, xs), hi = Math.max.apply(null, xs);
  const eps = 1e-9 * Math.max(1, Math.abs(hi - lo));
  return (min > lo + eps || max < hi - eps) ? { min, max } : null;
}

function renderTimelineTitle() {
  const ev = tl.events;
  if (!ev.length) {
    el('tlWhen').textContent = tl.stamps.size ? 'The ticked runs hold no scans.' : 'No runs ticked.';
    return;
  }
  const nRuns = new Set(ev.map((e) => runStamp(e.file))).size;
  const up = ev.filter((e) => e.direction === 'up').length;
  const down = ev.filter((e) => e.direction === 'down').length;
  const last = ev[ev.length - 1];
  el('tlWhen').textContent = nRuns + (nRuns === 1 ? ' run' : ' runs') + '  ·  ' +
    fmtStamp(ev[0].start_stamp) + '  →  ' + fmtStamp(last.stop_stamp || last.start_stamp) +
    '  ·  ' + ev.length + ' scans, ' + up + ' low → high and ' + down + ' high → low';
}

function drawTimeline(opts) {
  const node = el('runChart');
  renderTimelineTitle();
  if (!tl.events.length) {
    if (node._plot) { node._plot.destroy(); node._plot = null; }
    charts.run = null;
    node.innerHTML = '<p class="hint chart-empty">Tick one or more runs in the list to plot them here.</p>';
    el('runNote').textContent = '';
    renderAsymmetry();
    updateFitBar();
    return;
  }

  const p = palette();
  const spec = METRICS[el('metric').value];
  const byTime = el('runX').value === 'time';
  const dirs = shownDirs();
  const viewKey = [el('metric').value, el('runX').value, el('direction').value].join('|');
  const zoom = opts && opts.keepZoom ? runZoom(node, viewKey) : null;

  const { rows, xs } = buildRows(tl.events, byTime);
  tl.rows = rows;
  tl.xs = xs;
  tl.tx = [];
  rows.forEach((e, k) => { if (e) tl.tx.push([e.start_stamp, xs[k]]); });

  const cols = [xs];
  const sigmas = [null];
  const series = [{
    label: byTime ? 'Time' : 'Scan',
    value: (u, v) => (v === null || v === undefined) ? DASH : (byTime ? fmtStamp(v) : '#' + Math.round(v)),
  }];

  // one colour per direction when there is one quantity; with two (the two
  // peaks) colour stays with the peak and a hollow marker means high to low
  const single = spec.series.length === 1;
  spec.series.forEach((s) => {
    dirs.forEach((d) => {
      const colour = single ? (d === 'up' ? p.s1 : p.s2) : p[s.color];
      const hollow = !single && d === 'down';
      cols.push(rows.map((e) => {
        if (e === null) return null;                 // break the line
        if (e.direction !== d) return undefined;     // the other direction: span it
        const v = pick(s.pick, e);
        return v === null ? undefined : v;
      }));
      sigmas.push(s.err ? rows.map((e) => (e && e.direction === d ? pick(s.err, e) : null)) : null);
      series.push({
        label: s.name + ' ' + DIRS[d].arrow,
        stroke: colour,
        width: 1.5,
        points: { show: true, size: 5, width: hollow ? 1.5 : 1, stroke: colour,
                  fill: hollow ? p.surface : colour },
        value: (u, v) => fmt(v, 5),
      });
    });
  });

  // one scale, always: two measures of different size get their own view
  // rather than a second y axis
  const every = [];
  for (let i = 1; i < cols.length; i++) every.push.apply(every, cols[i]);
  const range = robustRange(every);
  const offScale = range
    ? every.filter((v) => v != null && isFinite(v) && (v < range[0] || v > range[1])).length
    : 0;
  el('runNote').textContent = offScale
    ? offScale + (offScale === 1 ? ' point runs' : ' points run') +
      ' off this scale — drag to zoom, or switch to Fit R² to find the bad fits.'
    : '';

  const scales = { x: { time: byTime } };
  if (range) scales.y = { range: () => range };
  // a single scan is one point, which uPlot's auto range spreads across an axis
  // of nonsense values; a live run shows exactly that after each rollover
  if (tl.events.length === 1) {
    const w = byTime ? 60 : 1;
    scales.x.range = () => [xs[0] - w, xs[0] + w];
  }

  const xAxis = axisOpts(p, byTime ? 'Time' : 'Scan number', true);
  if (byTime) delete xAxis.values;   // let uPlot write dates and clock times

  const x_key = tl.events[0].x_key;
  charts.run = makePlot(node, {
    scales: scales,
    cursor: {
      drag: { x: true, y: false, setScale: !tl.dragSelect },
      points: { size: 8 },
      focus: { prox: 20 },
    },
    legend: { live: true },
    axes: [xAxis, axisOpts(p, spec.axis(x_key), false)],
    series: series,
    hooks: { setSelect: [onPlotSelect] },
    plugins: [selectionBand(), errorBars((si) => sigmas[si]), fitCurves(), selectionMarker()],
  }, cols);

  charts.run.__accent = p.s1;
  node._viewKey = viewKey;
  if (zoom) charts.run.setScale('x', zoom);
  pickOnClick(charts.run, node);
  renderAsymmetry();
  updateFitBar();
}

/* ---- picking scans ---- */

/* Click a point on the time plot to open that scan below.

   uPlot's cursor.bind indirection did not fire here, so the listener goes
   straight on the plot surface. The index is taken at mousedown, before a drag
   can move the cursor, and a press that travels more than a few pixels is a
   drag rather than a pick. */
function pickOnClick(plot, node) {
  const over = node.querySelector('.u-over');
  if (!over) return;
  let downX = null, downIdx = null;
  over.addEventListener('mousedown', (e) => {
    downX = e.clientX;
    downIdx = plot.cursor.idx;
  });
  over.addEventListener('mouseup', (e) => {
    const x = downX, idx = downIdx;
    downX = downIdx = null;
    if (x === null || Math.abs(e.clientX - x) > 4) return;   // a drag, not a pick
    if (idx === null || idx === undefined) return;
    const ev = tl.rows[idx];
    if (ev) openScan(ev);
  });
}

function openScan(ev) {
  if (state.run && runStamp(state.run.name) === runStamp(ev.file)) {
    selectEvent(ev.idx).catch(showError);
  } else {
    selectFile(ev.file, ev.idx, 'keep').catch(showError);
  }
}

/* A drag in select mode picks the scans to fit, kept as a span of time so it
   holds through live updates and a switch between time and scan number. */
function onPlotSelect(u) {
  if (!tl.dragSelect || u.select.width < 3) return;
  // the first and last scans sit right on the edges of the plot, so a drag that
  // reaches within a few pixels of an edge takes everything beyond it too
  const edge = 4, right = u.select.left + u.select.width;
  const x0 = u.select.left <= edge ? -Infinity : u.posToVal(u.select.left, 'x');
  const x1 = right >= u.over.clientWidth - edge ? Infinity : u.posToVal(right, 'x');
  const picked = tl.rows.filter((e, k) => e && tl.xs[k] >= x0 && tl.xs[k] <= x1);
  tl.selection = picked.length ? {
    t0: Math.min.apply(null, picked.map((e) => e.start_stamp)),
    t1: Math.max.apply(null, picked.map((e) => e.start_stamp)),
  } : null;
  tl.fits = [];
  u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);   // the band takes over
  renderFits();
  renderAsymmetry();
  updateFitBar();
  u.redraw();
}

/* ---- asymmetry ---- */

/* The difference between the two sweep directions, from neighbouring scans
   swept opposite ways in the same run. Every such pair counts, in either
   order, so a steady drift in the polarization cancels rather than showing up
   as asymmetry. Neighbouring pairs share a scan, so the error on the mean is
   widened by the correlation between successive differences. */
function renderAsymmetry() {
  const note = el('asymNote');
  if (el('direction').value !== 'both' || !tl.events.length) { note.innerHTML = ''; return; }
  const spec = METRICS[el('metric').value];
  const evs = tl.selection ? selectedEvents() : tl.events;
  const dts = [];
  for (let i = 1; i < evs.length; i++) {
    if (evs[i].file === evs[i - 1].file) dts.push(evs[i].start_stamp - evs[i - 1].start_stamp);
  }
  const cadence = median(dts) || Infinity;

  let nPairs = 0;
  const parts = [];
  spec.series.forEach((s) => {
    const d = [];
    for (let i = 1; i < evs.length; i++) {
      const a = evs[i - 1], b = evs[i];
      if (runStamp(a.file) !== runStamp(b.file) || !a.direction || !b.direction ||
          a.direction === b.direction || b.start_stamp - a.start_stamp > 3 * cadence) continue;
      const va = pick(s.pick, a), vb = pick(s.pick, b);
      if (va === null || vb === null) continue;
      d.push(a.direction === 'up' ? va - vb : vb - va);
    }
    if (d.length < 3) return;
    nPairs = Math.max(nPairs, d.length);
    const n = d.length;
    const mean = d.reduce((acc, v) => acc + v, 0) / n;
    const vr = d.reduce((acc, v) => acc + (v - mean) * (v - mean), 0) / (n - 1);
    let c1 = 0;
    for (let i = 1; i < n; i++) c1 += (d[i] - mean) * (d[i - 1] - mean);
    const rho = vr > 0 ? c1 / ((n - 1) * vr) : 0;
    const sem = Math.sqrt(vr / n * Math.max(1, 1 + 2 * rho));
    const z = sem > 0 ? Math.abs(mean / sem) : 0;
    parts.push('<b>' + s.name + '</b> ' + (mean >= 0 ? '+' : '') + fmtPM(mean, sem) +
      (spec.unit || '') + ' <span class="muted">(' + z.toFixed(1) + 'σ)</span>');
  });
  if (!parts.length) {
    note.textContent = 'Too few neighbouring pairs of opposite scans to compare the two directions.';
    return;
  }
  note.innerHTML = 'Low → high minus high → low' + (tl.selection ? ', selected scans' : '') +
    ', ' + nPairs + ' neighbouring pairs: ' + parts.join('  ·  ');
}

/* ---- exponential fits ---- */

function updateFitBar() {
  el('dragZoom').setAttribute('aria-pressed', String(!tl.dragSelect));
  el('dragSelect').setAttribute('aria-pressed', String(tl.dragSelect));
  const dirs = shownDirs();
  const sel = selectedEvents().filter((e) => dirs.indexOf(e.direction) >= 0);
  const isPol = el('metric').value === 'polarization';
  if (tl.selection && sel.length) {
    el('selInfo').textContent = sel.length + ' scans selected  ·  ' +
      fmtShort(tl.selection.t0) + ' → ' + fmtShort(tl.selection.t1) +
      '  (' + fmtDuration(tl.selection.t1 - tl.selection.t0) + ')';
  } else if (tl.selection) {
    el('selInfo').textContent = 'No scans shown in the selected span.';
  } else {
    el('selInfo').textContent = tl.dragSelect ? 'Drag across the plot to pick the scans to fit.' : '';
  }
  const fitBtn = el('fitBtn');
  fitBtn.disabled = !(isPol && sel.length >= 4);
  fitBtn.title = !isPol ? 'Fits are to the polarization: set Show to Polarization'
    : sel.length < 4 ? 'Select at least four scans on the plot first' : '';
  el('clearSel').disabled = !tl.selection && !tl.fits.length;
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

/* Fit each direction on show separately, and with both on show the two
   together as well, so a difference in time constant between them is plain. */
async function doFit() {
  const dirs = shownDirs();
  const groups = dirs.map((d) => ({ key: d, label: DIRS[d].label, events: selectedEvents(d) }));
  if (dirs.length > 1) {
    groups.push({ key: 'all', label: 'Both together',
                  events: selectedEvents().filter((e) => dirs.indexOf(e.direction) >= 0) });
  }
  const asymptote = el('asymptote').value === 'zero' ? 0 : null;
  const fitBtn = el('fitBtn');
  fitBtn.disabled = true;
  fitBtn.textContent = 'Fitting…';
  const results = [];
  try {
    for (const g of groups) {
      const pts = g.events
        .map((e) => ({ t: e.start_stamp, y: polPercent(e), s: polarizationErr(e, state.r0) }))
        .filter((q) => q.y !== null);
      const base = { key: g.key, label: g.label, n: pts.length, r0: state.r0 };
      try {
        const sig = pts.every((q) => q.s !== null && q.s > 0) ? pts.map((q) => q.s) : null;
        const r = await postJSON('api/fit/exp', {
          t: pts.map((q) => q.t), y: pts.map((q) => q.y), sigma: sig, asymptote: asymptote,
        });
        results.push(Object.assign(base, r));
      } catch (err) {
        results.push(Object.assign(base, { error: String(err.message || err) }));
      }
    }
  } finally {
    fitBtn.textContent = 'Fit exponential';
  }
  tl.fits = results;
  renderFits();
  updateFitBar();
  if (charts.run) charts.run.redraw();
}

function renderFits() {
  const host = el('fitResults');
  if (!tl.fits.length) { host.innerHTML = ''; return; }
  const p = palette();
  const swatch = (c) => '<span class="swatch" style="background:' + c + '"></span>';
  const loose = (f) => f.tau_unbounded || (f.tau_err !== null && f.tau_err > 0.5 * f.tau);
  const rows = tl.fits.map((f) => {
    const head = '<td>' + swatch(fitColour(f.key, p)) + f.label + '</td>';
    if (f.error) return '<tr>' + head + '<td colspan="6" class="warn">' + f.error + '</td></tr>';
    return '<tr>' + head +
      '<td>' + f.n + '</td>' +
      '<td>' + f.kind + '</td>' +
      '<td' + (loose(f) ? ' class="warn" title="The selected span does not pin τ down"' : '') + '>' +
        fmtTau(f.tau, f.tau_err) + '</td>' +
      '<td>' + fmtPM(f.p_0, f.p_0_err) + '</td>' +
      '<td>' + (f.asymptote_fixed ? '0 (fixed)' : fmtPM(f.p_inf, f.p_inf_err)) + '</td>' +
      '<td>' + fmt(f.redchi2, 3) + '</td></tr>';
  }).join('');

  const ok = tl.fits.filter((f) => !f.error);
  const notes = [];
  if (ok.some(loose)) {
    notes.push('<p class="fitnote warn">A time constant in red is not pinned down by the ' +
      'selected span: the curve there is close to a straight line. Select a longer stretch ' +
      'of the build-up or relaxation.</p>');
  }
  if (ok.length) {
    const weighted = ok.every((f) => f.weighted);
    notes.push('<p class="fitnote">P(t) = P<sub>∞</sub> + (P<sub>0</sub> − P<sub>∞</sub>) ' +
      'exp(−(t − t<sub>0</sub>)/τ), with t<sub>0</sub> the first scan selected. ' +
      (weighted ? 'Weighted by each scan\'s ±1σ from its peak fit; '
                : 'Unweighted, as some scans have no stored uncertainty; ') +
      'uncertainties scaled by √χ²<sub>ν</sub>. Fitted at r<sub>0</sub> = ' + ok[0].r0 + '.</p>');
  }
  host.innerHTML = '<table class="params fittable">' +
    '<caption class="sr-only">Exponential fits to the selected polarization</caption>' +
    '<thead><tr><th scope="col">Scans</th><th scope="col">N</th><th scope="col">Type</th>' +
    '<th scope="col">τ</th><th scope="col">P<sub>0</sub> (%)</th>' +
    '<th scope="col">P<sub>∞</sub> (%)</th><th scope="col">χ²<sub>ν</sub></th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table>' + notes.join('');
}

function clearSelection() {
  tl.selection = null;
  tl.fits = [];
  renderFits();
  renderAsymmetry();
  updateFitBar();
  if (charts.run) charts.run.redraw();
}

/* ---- export ---- */

function exportTimeline() {
  if (!tl.events.length) return;
  const head = ['start_stamp', 'start_time', 'file', 'scan', 'direction', 'n_points', 'rsq',
                'peak1_height', 'peak2_height', 'height_ratio', 'polarization_pct',
                'polarization_err_pct'];
  const lines = [head.join(',')];
  tl.events.forEach((e) => {
    lines.push([e.start_stamp, JSON.stringify(e.start_time || ''), JSON.stringify(e.file),
                e.idx + 1, e.direction || '', e.n_points, e.rsq, e.peak1, e.peak2,
                heightRatio(e), polPercent(e), polarizationErr(e, state.r0)]
      .map((v) => (typeof v === 'string' ? v : csvCell(v))).join(','));
  });
  const first = runStamp(tl.events[0].file), last = runStamp(tl.events[tl.events.length - 1].file);
  download('plot_' + first + (last !== first ? '_to_' + last : '') + '.csv', lines.join('\n'));
}

/* ---------------- scan chart ---------------- */

function drawScanChart() {
  const ev = state.event;
  const node = el('scanChart');
  const rnode = el('residChart');
  if (!ev) { node.innerHTML = ''; rnode.innerHTML = ''; return; }

  const p = palette();
  const order = sortedOrder(ev.x);
  const x = reorder(ev.x, order) || [];
  const xLabel = ev.x_key === 'wavelength' ? 'Wavelength' : 'Probe current (A)';

  const hasFit = ev.fit && ev.fit.length === ev.x.length;

  // Draw order is series order. The baseline and the total fit go down solid,
  // then the two components over them with open dashes so the fit still shows
  // through where a component sits right on top of it, then the measured points
  // last. The dash pattern is a second channel besides hue, for a colourblind
  // reader and for print.
  const series = [{ label: xLabel, value: (u, v) => fmt(v, 6) }];
  const cols = [x];

  if (hasFit) {
    series.push({ label: 'Baseline', stroke: p.base, width: 1.25, dash: [1, 4],
                  points: { show: false }, value: (u, v) => fmt(v, 5) });
    cols.push(reorder(ev.base, order));
    series.push({ label: 'Fit', stroke: p.s2, width: 2,
                  points: { show: false }, value: (u, v) => fmt(v, 5) });
    cols.push(reorder(ev.fit, order));
    series.push({ label: 'Peak 1', stroke: p.s3, width: 1.5, dash: [3, 5],
                  points: { show: false }, value: (u, v) => fmt(v, 5) });
    cols.push(reorder(ev.g1, order));
    series.push({ label: 'Peak 2', stroke: p.s4, width: 1.5, dash: [7, 5],
                  points: { show: false }, value: (u, v) => fmt(v, 5) });
    cols.push(reorder(ev.g2, order));
  }

  series.push({
    label: 'Signal', stroke: p.s1, width: 1.25,
    points: { show: true, size: 5, stroke: p.s1, fill: p.s1 },
    value: (u, v) => fmt(v, 5),
  });
  cols.push(reorder(ev.signal, order));

  const sync = uPlot.sync('scan');

  charts.scan = makePlot(node, {
    cursor: { drag: { x: true, y: false }, points: { size: 8 }, sync: { key: sync.key } },
    legend: { live: true },
    axes: [axisOpts(p, xLabel, true), axisOpts(p, 'Lock-in R', false)],
    series: series,
    plugins: hasFit ? [peakLabels(ev, p)] : [],
  }, cols);

  if (ev.residual && ev.residual.length) {
    charts.resid = makePlot(rnode, {
      cursor: { drag: { x: true, y: false }, points: { size: 8 }, sync: { key: sync.key } },
      legend: { show: false },   // one series, already named by the axis
      axes: [axisOpts(p, '', true), axisOpts(p, 'Signal − fit', false)],
      series: [
        { label: xLabel, value: (u, v) => fmt(v, 6) },
        {
          label: 'Signal − fit', stroke: p.s1, width: 1,
          points: { show: true, size: 4, stroke: p.s1, fill: p.s1 },
          value: (u, v) => fmt(v, 5),
        },
      ],
      plugins: [zeroLine(p)],
    }, [x, reorder(ev.residual, order)]);
  } else {
    rnode.innerHTML = '';
    charts.resid = null;
  }
}

/* ---------------- panels ---------------- */

function tile(label, value, sub, cls) {
  return '<div class="tile' + (cls ? ' ' + cls : '') + '">' +
    '<div class="tlabel">' + label + '</div>' +
    '<div class="tvalue">' + value + '</div>' +
    (sub ? '<div class="tsub">' + sub + '</div>' : '') + '</div>';
}

/* A y range set by the bulk of the scans rather than by the extremes.

   One failed fit can put the peak height ratio orders of magnitude out, which
   flattens a whole run into a line at zero. When that happens the axis is set
   from the 2nd to 98th percentile and the outliers are left to run off the
   plot, where the caller counts them and says so. A run without wild points is
   left on uPlot's own auto range. */
function robustRange(values) {
  const v = values.filter((x) => x !== null && isFinite(x)).sort((a, b) => a - b);
  if (v.length < 8) return null;
  const at = (q) => v[Math.min(v.length - 1, Math.max(0, Math.round(q * (v.length - 1))))];
  const lo = at(0.02), hi = at(0.98);
  const full = v[v.length - 1] - v[0];
  const inner = hi - lo;
  if (!(inner > 0) || !(full > 4 * inner)) return null;   // nothing wild, leave it alone
  const pad = inner * 0.15;
  return [lo - pad, hi + pad];
}

function median(values) {
  const v = values.filter((x) => x !== null && isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

function renderRunBar() {
  const run = state.run;
  el('runName').textContent = run.name;
  el('runWhen').textContent = fmtStamp(run.start_stamp) + '  →  ' +
    fmtStamp(run.stop_stamp) + '   ·   ' + fmtSize(run.size);

  const rsqs = run.events.map((e) => e.rsq);
  const pols = run.events.map((e) => {
    const v = polarization(e.peak1, e.peak2, state.r0);
    return v === null ? null : v * 100;
  });
  const medRsq = median(rsqs);
  const medPol = median(pols);
  const xmin = median(run.events.map((e) => e.x_min));
  const xmax = median(run.events.map((e) => e.x_max));

  el('runTiles').innerHTML =
    tile('Scans', run.events.length, fmtDuration(run.duration) + ' total') +
    tile('Median R²', fmt(medRsq, 4),
         medRsq !== null && medRsq > 0.99 ? 'fits converged' : 'check the fits',
         medRsq === null ? '' : (medRsq > 0.99 ? 'good' : 'warn')) +
    tile('Median P', medPol === null ? DASH : medPol.toFixed(2) + ' %',
         'at r₀ = ' + state.r0) +
    tile('Scan range',
         fmt(xmin, 4) + ' – ' + fmt(xmax, 4),
         run.x_key === 'wavelength' ? 'wavelength' : 'probe current (A)') +
    (run.bad_lines ? tile('Unreadable lines', run.bad_lines, 'skipped', 'warn') : '');

  el('rawLink').href = api('file/' + encodeURIComponent(run.name) + '/raw');
  el('rawLink').setAttribute('download', run.name);
}

function renderFitPanel() {
  const ev = state.event;
  const names = paramNames(ev.pf);
  const r = heightRatio(ev);
  const pol = polarization(ev.peak1, ev.peak2, state.r0);

  el('fitTiles').innerHTML =
    tile('R²', fmt(ev.rsq, 5), ev.n_points + ' points',
         ev.rsq === null ? '' : (ev.rsq > 0.99 ? 'good' : 'warn')) +
    tile('Height ratio r', fmt(r, 5), 'peak 1 / peak 2') +
    tile('Polarization', pol === null ? DASH : (pol * 100).toFixed(2) + ' %',
         'r₀ = ' + state.r0);

  const swatch = (c) => '<span class="swatch" style="background:' + c + '"></span>';
  const p = palette();
  const rows = names.map((n, i) => {
    const colour = i < 3 ? p.s3 : (i < 6 ? p.s4 : p.base);
    const sep = (i === 3 || i === 6) ? ' class="sep"' : '';
    return '<tr' + sep + '><td>' + (i % 3 === 0 || i >= 6 ? swatch(colour) : '') +
      n + '</td><td>' + fmt(ev.pf[i], 6) + '</td><td>' +
      (ev.pstd && ev.pstd[i] !== undefined ? fmt(ev.pstd[i], 3) : DASH) + '</td></tr>';
  }).join('');
  el('paramTable').querySelector('tbody').innerHTML = rows ||
    '<tr><td colspan="3">No fit stored for this scan.</td></tr>';

  const note = el('fitNote');
  const nBase = ev.pf.length - GAUSS_PARAM_NAMES.length;
  const referenced = ev.baseline ? ev.baseline.referenced : ev.pf.length >= 9;
  if (!ev.pf.length) {
    note.textContent = 'This scan has no stored fit.';
    note.className = 'note warn';
  } else {
    const shape = nBase > 2 ? 'a quadratic baseline' : 'a straight baseline';
    note.textContent = 'Two gaussians on ' + shape + (referenced
      ? ' referenced to mid scan.'
      : ' in raw current (written before the baseline was referenced to mid scan).');
    note.className = 'note';
  }
}

function renderPointTable() {
  const ev = state.event;
  el('thX').textContent = ev.x_key === 'wavelength' ? 'Wavelength' : 'Current';
  const n = ev.x.length;
  const cell = (arr, i) => '<td>' + (arr && arr.length > i ? fmt(arr[i], 6) : DASH) + '</td>';
  let html = '';
  for (let i = 0; i < n; i++) {
    html += '<tr><td>' + (i + 1) + '</td>' +
      cell(ev.x, i) + cell(ev.signal, i) + cell(ev.fit, i) +
      cell(ev.g1, i) + cell(ev.g2, i) + cell(ev.base, i) + cell(ev.residual, i) + '</tr>';
  }
  el('pointTable').querySelector('tbody').innerHTML = html;
}

/* The scan title, slider and arrows, which change when the run grows even
   though the scan on screen does not. */
function renderScanNav() {
  const ev = state.event;
  const total = state.run.events.length;
  el('scanTitle').textContent = 'Scan ' + (state.eventIdx + 1) + ' of ' + total +
    '   ·   ' + fmtStamp(ev.start_stamp) + '   ·   ' + fmtDuration(ev.duration) +
    (DIRS[ev.direction] ? '   ·   ' + DIRS[ev.direction].label.toLowerCase() : '');
  el('prevScan').disabled = state.eventIdx <= 0;
  el('nextScan').disabled = state.eventIdx >= total - 1;
  const slider = el('scanSlider');
  slider.max = String(Math.max(0, total - 1));
  slider.value = String(state.eventIdx);
}

function renderScan() {
  renderScanNav();
  renderFitPanel();
  drawScanChart();
  // move the open-scan rule. Not straight away: a plot uPlot has only just built
  // sets its scales up in a microtask, and a redraw before then leaves the x
  // scale empty and the plot blank, which is what a theme switch used to do
  requestAnimationFrame(() => { if (charts.run) charts.run.redraw(); });
  if (el('pointTable').closest('details').open) renderPointTable();
}

/* ---------------- file list ---------------- */

function renderFileList() {
  const q = el('fileSearch').value.trim().toLowerCase();
  const hideEmpty = el('hideEmpty').checked;
  const shown = state.files.filter((f) =>
    (!hideEmpty || f.n_events > 0) &&
    (!q || f.name.toLowerCase().indexOf(q) >= 0 || labelFromName(f.name).toLowerCase().indexOf(q) >= 0));

  const withData = state.files.filter((f) => f.n_events > 0).length;
  el('fileCount').textContent = shown.length + ' of ' + state.files.length +
    ' files shown · ' + withData + ' hold scans';

  el('fileList').innerHTML = shown.map((f, i) => {
    const empty = f.n_events === 0;
    const stamp = runStamp(f.name);
    const meta = empty
      ? '<span>empty</span><span>' + fmtSize(f.size) + '</span>'
      : '<span>' + f.n_events + ' scan' + (f.n_events === 1 ? '' : 's') + '</span>' +
        '<span>' + fmtDuration(f.duration) + '</span>' +
        '<span>' + fmtSize(f.size) + '</span>';
    return '<li><input type="checkbox" class="tlcheck" data-stamp="' + stamp + '" data-i="' + i + '"' +
      (!empty && tl.stamps.has(stamp) ? ' checked' : '') + (empty ? ' disabled' : '') +
      ' aria-label="Plot ' + labelFromName(f.name) + ' in the time plot"' +
      ' title="Include in the time plot (shift-click for a range)">' +
      '<button class="filerow' + (empty ? ' is-empty' : '') +
      '" type="button" data-name="' + f.name + '"' +
      (state.run && state.run.name === f.name ? ' aria-current="true"' : '') + '>' +
      '<span class="fdate">' + labelFromName(f.name) + '</span>' +
      '<span class="fmeta">' + meta + '</span></button></li>';
  }).join('') || '<li class="side-count" style="padding:10px">No files match.</li>';
}

/* Tick or untick a run for the time plot; shift-click sets every run between
   this tick and the last one clicked to match. */
function onTick(box, shift) {
  const boxes = Array.from(el('fileList').querySelectorAll('.tlcheck'));
  const i = boxes.indexOf(box);
  if (shift && tl.lastCheck !== null && tl.lastCheck < boxes.length) {
    const a = Math.min(i, tl.lastCheck), b = Math.max(i, tl.lastCheck);
    for (let k = a; k <= b; k++) if (!boxes[k].disabled) boxes[k].checked = box.checked;
  }
  tl.lastCheck = i;
  const stamps = new Set(tl.stamps);
  boxes.forEach((b) => {
    if (b.checked) stamps.add(b.dataset.stamp); else stamps.delete(b.dataset.stamp);
  });
  setTimeline(stamps).catch(showError);
}

/* ---------------- deep links ---------------- */

/* The address bar carries the selection, so a particular scan can be bookmarked
   or pasted to someone else looking at the same data directory. A folder other
   than the default one rides along too. */
function readHash() {
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  const file = h.get('file');
  const scan = parseInt(h.get('scan'), 10);
  const plot = (h.get('plot') || '').split(',').filter((v) => v);
  return { dir: h.get('dir'), file: file || null, scan: isFinite(scan) ? scan - 1 : 0, plot: plot };
}

function dirHash() {
  return state.dir ? 'dir=' + encodeURIComponent(state.dir) : '';
}

function writeHash() {
  if (!state.run) return;
  let h = (state.dir ? dirHash() + '&' : '') +
    'file=' + encodeURIComponent(state.run.name) + '&scan=' + (state.eventIdx + 1);
  if (tl.stamps.size > 1) h += '&plot=' + Array.from(tl.stamps).sort().join(',');
  if (location.hash.replace(/^#/, '') !== h) {
    history.replaceState(null, '', '#' + h);
  }
}

/* ---------------- loading ---------------- */

async function getJSON(url) {
  const res = await fetch(url);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

const WELCOME = el('welcome').innerHTML;
const LOGO = el('welcome').querySelector('.welcome-logo').outerHTML;

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
}

/* The first read of a folder parses every event file in it, which can take a
   while for a big one, so say how far it has got. Progress is by bytes, since
   the files range from empty to a few megabytes. The splash is built once and
   then only the bar and the text change, so the logo does not flicker. */
function showLoading(p) {
  el('workspace').hidden = true;
  el('welcome').hidden = false;
  let box = el('welcome').querySelector('.loading');
  if (!box) {
    el('welcome').innerHTML = LOGO + '<div class="loading"><h2>Reading event files</h2>' +
      '<p class="dirline"></p>' +
      '<progress max="1" aria-label="Reading event files"></progress>' +
      '<p class="progress-text"></p>' +
      '<p class="muted">Each file is read once; after that the list comes from memory.</p></div>';
    box = el('welcome').querySelector('.loading');
  }
  box.querySelector('.dirline').textContent = state.dir || state.dirPath;
  const bar = box.querySelector('progress');
  let text = 'Listing the folder' + '…';
  if (p && p.loading && p.total) {
    const frac = p.bytes_total ? p.bytes_done / p.bytes_total : p.done / p.total;
    bar.value = frac;
    text = p.done + ' of ' + p.total + ' files · ' + fmtSize(p.bytes_done) + ' of ' +
      fmtSize(p.bytes_total) + ' · ' + Math.round(100 * frac) + '%';
  } else {
    bar.removeAttribute('value');   // indeterminate until the server reports a count
  }
  box.querySelector('.progress-text').textContent = text;
  el('fileCount').textContent = p && p.loading && p.total
    ? 'Reading ' + p.done + ' of ' + p.total + ' files' + '…' : 'Reading files' + '…';
}

/* The file list for the folder, with progress shown while the server reads it */
async function fetchIndex() {
  let live = true;
  showLoading(null);
  const timer = setInterval(async () => {
    try {
      const p = await getJSON(api('progress'));
      if (!live) return;
      if (!state.dirPath) setDirLabel(p.path);
      showLoading(p);
    } catch (e) { /* the list request will report it */ }
  }, 250);
  try {
    return await getJSON(api('files'));
  } finally {
    live = false;
    clearInterval(timer);
  }
}

function setDirLabel(path) {
  state.dirPath = path;
  el('dataDir').hidden = !path;
  // marked left to right, so the rtl box that trims the start keeps the path in order
  el('dataDirPath').textContent = '\u200E' + path + '\u200E';
  el('dataDir').title = path + (state.dir ? '' : ' (default)') + ': click to open another folder';
  el('dataDir').classList.toggle('is-other', !!state.dir);
}

function saveDir() {
  try {
    if (state.dir) localStorage.setItem('pymeop-dir', state.dir);
    else localStorage.removeItem('pymeop-dir');
  } catch (e) { /* private mode */ }
}

async function loadFiles() {
  state.loading = true;
  let data;
  try {
    data = await fetchIndex();
  } finally {
    state.loading = false;
  }
  // the default folder picked by its path is still the default
  if (data.data_dir === data.default_dir) state.dir = null;
  saveDir();
  state.files = data.files;
  state.version = data.version;
  setDirLabel(data.data_dir);
  el('welcome').innerHTML = WELCOME;
  renderFileList();
  const want = readHash();
  const known = want.file && state.files.some((f) => f.name === want.file);
  const first = state.files.find((f) => f.n_events > 0);
  if (want.plot.length) tl.stamps = new Set(want.plot);
  if (known) {
    await selectFile(want.file, want.scan);
  } else if (first) {
    await selectFile(first.name);
  } else {
    el('welcome').innerHTML = '<h2>No scans here</h2><p>' +
      (state.files.length
        ? 'None of the ' + state.files.length + ' event files in this folder hold any scans.'
        : 'This folder has no event files in it.') +
      ' Click the folder name in the top bar to open another.</p>';
    history.replaceState(null, '', '#' + dirHash());
  }
}

/* Start over in another folder: nothing from the old one carries across */
async function switchFolder(dir) {
  state.dir = dir || null;
  state.run = null;
  state.event = null;
  state.eventIdx = 0;
  state.files = [];
  state.version = null;
  state.updatedAt = null;
  setDirLabel(dir || '');
  tl.gen++;
  tl.stamps = new Set();
  tl.cache = {};
  tl.events = [];
  tl.selection = null;
  tl.fits = [];
  tl.lastCheck = null;
  renderFits();
  history.replaceState(null, '', '#' + dirHash());
  await loadFiles();
}

/* Open a run in the run bar and scan plot. What happens to the time plot
   depends on how the run was reached:
     'auto'    clicked in the list: plot just this run, unless it is plotted already
     'keep'    clicked on the time plot: leave the plot as it is
     'extend'  followed onto a new file by a live update: add it to the plot */
async function selectFile(name, startScan, mode) {
  const run = await getJSON(api('file/' + encodeURIComponent(name)));
  state.run = run;
  state.eventIdx = 0;
  tl.cache[run.name] = run;
  const stamp = runStamp(run.name);
  let changed = false;
  if (run.events.length && !tl.stamps.has(stamp) && mode !== 'keep') {
    if (mode === 'extend') {
      tl.stamps.add(stamp);
    } else {
      tl.stamps = new Set([stamp]);
      tl.selection = null;
    }
    tl.fits = [];
    changed = true;
  }
  renderFileList();
  if (!run.events.length) {
    el('workspace').hidden = true;
    el('welcome').hidden = false;
    el('welcome').innerHTML = '<h2>' + labelFromName(run.name) + '</h2>' +
      '<p>This event file holds no scans. It was created when the run started ' +
      'but nothing was written to it.</p>';
    return;
  }
  el('welcome').hidden = true;
  el('workspace').hidden = false;
  renderRunBar();
  if (changed || !charts.run || tl.events.every((e) => e.file !== run.name)) {
    if (await loadTimeline()) drawTimeline({ keepZoom: !changed || mode === 'extend' });
  }
  renderFits();
  await selectEvent(startScan || 0);
}

async function selectEvent(idx) {
  if (!state.run || !state.run.events.length) return;
  idx = Math.max(0, Math.min(state.run.events.length - 1, idx));
  state.eventIdx = idx;
  state.event = await getJSON(
    api('file/' + encodeURIComponent(state.run.name) + '/event/' + idx));
  renderScan();
  writeHash();
}

/* ---------------- live updates ---------------- */

/* The page asks the server every few seconds whether anything in the data
   directory has changed, which costs the server a directory listing and
   nothing more, and only fetches the files again when it has. A run the DAQ
   is writing then grows on screen scan by scan.

   Sitting on the last scan of the newest run means following it, like tail -f:
   each new scan opens as it arrives, and when the DAQ rolls over to a new file
   the page moves to it. Anywhere else the scan on screen stays put and only the
   run chart and the scan count grow. */

const POLL_MS = 2000;
let polling = false;

function newestWithScans(files) {
  const f = files.find((r) => r.n_events > 0);
  return f ? f.name : null;
}

function setLive(cls, text) {
  const label = el('liveToggle').closest('.live');
  label.classList.toggle('is-on', cls === 'on');
  label.classList.toggle('is-down', cls === 'down');
  el('liveStatus').textContent = text || '';
}

async function refresh() {
  const before = state.files;
  const dir = state.dir;
  const data = await getJSON(api('files'));
  if (state.dir !== dir || state.loading) return;   // the folder changed meanwhile
  state.files = data.files;
  state.version = data.version;

  const run = state.run;
  if (!run) {
    renderFileList();
    const first = newestWithScans(state.files);
    if (first) await selectFile(first);
    return;
  }

  const tailing = state.eventIdx >= run.events.length - 1 &&
    runStamp(newestWithScans(before) || '') === runStamp(run.name);
  const newest = newestWithScans(state.files);
  if (tailing && newest && runStamp(newest) !== runStamp(run.name)) {
    const row = state.files.find((f) => f.name === newest);
    await selectFile(newest, row.n_events - 1, 'extend');
    return;
  }

  // the open file, possibly under the name the DAQ gave it on closing
  const row = state.files.find((f) => f.name === run.name) ||
    state.files.find((f) => runStamp(f.name) === runStamp(run.name));
  if (!row || (row.name === run.name && row.size === run.size && row.mtime === run.mtime)) {
    renderFileList();   // nothing new in the open run, or it has gone
    await refreshTimeline();   // but another plotted run may have changed
    return;
  }

  if (!run.events.length) {   // an empty file that has had its first scan
    await selectFile(row.name, tailing ? row.n_events - 1 : 0, 'keep');
    return;
  }

  const fresh = await getJSON(api('file/' + encodeURIComponent(row.name)));
  if (state.run !== run) return;   // another run was picked while this loaded
  const prevIdx = state.eventIdx;
  state.run = fresh;
  tl.cache[fresh.name] = fresh;
  renderFileList();
  if (!fresh.events.length) return;
  renderRunBar();
  await refreshTimeline();
  const idx = tailing ? fresh.events.length - 1 : Math.min(prevIdx, fresh.events.length - 1);
  if (idx !== prevIdx || !state.event) {
    await selectEvent(idx);
  } else {
    renderScanNav();
    writeHash();
  }
}

async function refreshTimeline() {
  if (tl.stamps.size && await loadTimeline()) drawTimeline({ keepZoom: true });
}

async function poll() {
  if (polling || state.loading || document.hidden || !el('liveToggle').checked) return;
  polling = true;
  try {
    const dir = state.dir;
    const { version } = await getJSON(api('version'));
    if (version !== state.version && state.dir === dir && !state.loading) {
      await refresh();
      state.updatedAt = new Date().toLocaleTimeString();
    }
    setLive('on', state.updatedAt ? 'updated ' + state.updatedAt : '');
  } catch (err) {
    setLive('down', 'server not responding');
  } finally {
    polling = false;
  }
}

/* ---------------- folder picker ---------------- */

/* Walks the server's folders rather than using the browser's own file dialog,
   which would hand over the files but never the path the server needs. */
const picker = { path: null, parent: null };

function plural(n, word) {
  return n + ' ' + word + (n === 1 ? '' : 's');
}

async function browseFolder(path) {
  el('folderInfo').classList.remove('offscale');
  el('folderInfo').textContent = 'Looking' + '…';
  let d;
  try {
    d = await getJSON('api/folders' + (path ? '?path=' + encodeURIComponent(path) : ''));
  } catch (err) {
    el('folderInfo').classList.add('offscale');
    el('folderInfo').textContent = String(err.message || err);
    return;
  }
  picker.path = d.path;
  picker.parent = d.parent;
  el('folderPath').value = d.path;
  el('folderUp').disabled = !d.parent;
  el('folderOpen').disabled = false;
  el('folderInfo').textContent = plural(d.n_files || 0, 'event file') + ' here' +
    (d.path === d.default ? ' · the default folder' : '') +
    (d.dirs.length ? ' · ' + plural(d.dirs.length, 'folder') + ' inside' : '');
  el('folderList').innerHTML = d.dirs.map((f) =>
    '<li><button class="folderrow" type="button" data-path="' + esc(f.path) + '">' +
    '<span class="fname">' + esc(f.name) + '</span>' +
    '<span class="fmeta">' + (f.n_files === null ? 'no access' : f.n_files ? plural(f.n_files, 'event file') : '') +
    '</span></button></li>').join('') ||
    '<li class="side-count folder-none">No folders inside this one.</li>';
}

function openPicker() {
  el('folderOpen').disabled = true;
  el('folderList').innerHTML = '';
  el('folderDlg').showModal();
  browseFolder(state.dir || state.dirPath || null);
}

function choosePicked(path) {
  el('folderDlg').close();
  switchFolder(path).catch(showError);
}

/* ---------------- CSV ---------------- */

function download(name, text) {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function csvCell(v) {
  return (v === null || v === undefined || !isFinite(v)) ? '' : String(v);
}

function exportScan() {
  const ev = state.event;
  if (!ev) return;
  const head = [ev.x_key, 'time', 'signal', 'fit', 'peak1_on_base', 'peak2_on_base',
                'baseline', 'residual'];
  const lines = [head.join(',')];
  for (let i = 0; i < ev.x.length; i++) {
    lines.push([ev.x[i], ev.times[i], ev.signal[i], ev.fit[i], ev.g1[i], ev.g2[i],
                ev.base[i], ev.residual[i]].map(csvCell).join(','));
  }
  download(state.run.name.replace(/\.\w+$/, '') + '_scan' + (state.eventIdx + 1) + '.csv',
           lines.join('\n'));
}

function exportRun() {
  const run = state.run;
  if (!run) return;
  const names = paramNames(run.events[0].pf);
  const head = ['scan', 'start_stamp', 'start_time', 'elapsed_s', 'n_points', 'rsq',
                'height_ratio', 'polarization_pct']
    .concat(names.map((n) => n.toLowerCase().replace(/[^a-z0-9]+/g, '_')))
    .concat(names.map((n) => n.toLowerCase().replace(/[^a-z0-9]+/g, '_') + '_std'));
  const t0 = run.start_stamp || 0;
  const lines = [head.join(',')];
  run.events.forEach((e, i) => {
    const pol = polarization(e.peak1, e.peak2, state.r0);
    const row = [i + 1, e.start_stamp, JSON.stringify(e.start_time || ''),
                 (e.start_stamp || 0) - t0, e.n_points, e.rsq,
                 heightRatio(e), pol === null ? null : pol * 100]
      .map((v) => (typeof v === 'string' ? v : csvCell(v)))
      .concat(names.map((n, j) => csvCell(e.pf[j])))
      .concat(names.map((n, j) => csvCell(e.pstd[j])));
    lines.push(row.join(','));
  });
  download(run.name.replace(/\.\w+$/, '') + '_run.csv', lines.join('\n'));
}

/* ---------------- wiring ---------------- */

function redrawAll() {
  if (!state.run) return;
  renderRunBar();
  drawTimeline({ keepZoom: true });
  renderFits();
  if (state.event) renderScan();
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('pymeop-theme', theme); } catch (e) { /* private mode */ }
  redrawAll();
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem('pymeop-theme'); } catch (e) { /* private mode */ }
  if (saved) document.documentElement.setAttribute('data-theme', saved);
}

function currentTheme() {
  const set = document.documentElement.getAttribute('data-theme');
  if (set) return set;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function wire() {
  el('fileList').addEventListener('click', (e) => {
    const btn = e.target.closest('.filerow');
    if (btn) selectFile(btn.dataset.name).catch(showError);
    const box = e.target.closest('.tlcheck');
    if (box) onTick(box, e.shiftKey);
  });
  document.querySelector('.presets').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-preset]');
    if (chip) applyPreset(chip.dataset.preset);
  });

  el('fileSearch').addEventListener('input', renderFileList);
  el('hideEmpty').addEventListener('change', renderFileList);

  // the fits are to one metric, one choice of directions and one r0; a change
  // to any of them leaves the fits describing something no longer on show
  const restyle = (clearFits) => () => {
    if (clearFits) { tl.fits = []; renderFits(); }
    drawTimeline();
  };
  el('metric').addEventListener('change', restyle(true));
  el('direction').addEventListener('change', restyle(true));
  el('runX').addEventListener('change', restyle(false));
  el('dragZoom').addEventListener('click', () => { tl.dragSelect = false; drawTimeline({ keepZoom: true }); });
  el('dragSelect').addEventListener('click', () => { tl.dragSelect = true; drawTimeline({ keepZoom: true }); });
  el('fitBtn').addEventListener('click', () => doFit().catch(showError));
  el('clearSel').addEventListener('click', clearSelection);
  el('asymptote').addEventListener('change', () => {
    tl.fits = []; renderFits(); updateFitBar();
    if (charts.run) charts.run.redraw();
  });
  el('csvTimeline').addEventListener('click', exportTimeline);
  el('showErr').addEventListener('change', () => charts.run && charts.run.redraw());

  el('prevScan').addEventListener('click', () => selectEvent(state.eventIdx - 1));
  el('nextScan').addEventListener('click', () => selectEvent(state.eventIdx + 1));
  el('scanSlider').addEventListener('input', (e) => selectEvent(Number(e.target.value)));

  el('csvScan').addEventListener('click', exportScan);
  el('csvRun').addEventListener('click', exportRun);

  el('r0').addEventListener('input', () => {
    const v = parseFloat(el('r0').value);
    state.r0 = isFinite(v) && v !== 0 ? v : 1;
    if (!state.run) return;
    renderRunBar();
    tl.fits = [];
    renderFits();
    if (el('metric').value === 'polarization') drawTimeline({ keepZoom: true });
    if (state.event) renderFitPanel();
  });

  el('dataDir').addEventListener('click', openPicker);
  el('folderList').addEventListener('click', (e) => {
    const row = e.target.closest('.folderrow');
    if (row) browseFolder(row.dataset.path);
  });
  el('folderUp').addEventListener('click', () => { if (picker.parent) browseFolder(picker.parent); });
  el('folderGo').addEventListener('click', () => browseFolder(el('folderPath').value.trim()));
  el('folderPath').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); browseFolder(el('folderPath').value.trim()); }
  });
  el('folderCancel').addEventListener('click', () => el('folderDlg').close());
  el('folderOpen').addEventListener('click', () => { if (picker.path) choosePicked(picker.path); });
  el('folderDefault').addEventListener('click', () => choosePicked(null));

  el('themeBtn').addEventListener('click', () =>
    applyTheme(currentTheme() === 'dark' ? 'light' : 'dark'));

  el('pointTable').closest('details').addEventListener('toggle', (e) => {
    if (e.target.open && state.event) renderPointTable();
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, select, textarea')) return;
    if (e.key === 'ArrowLeft') { selectEvent(state.eventIdx - 1); e.preventDefault(); }
    if (e.key === 'ArrowRight') { selectEvent(state.eventIdx + 1); e.preventDefault(); }
  });

  let pending = null;
  addEventListener('resize', () => {
    clearTimeout(pending);
    pending = setTimeout(() => {
      ['runChart', 'scanChart', 'residChart'].forEach((id) => {
        const node = el(id);
        if (node && node._plot) node._plot.setSize(sizeFor(node));
      });
    }, 120);
  });

  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!document.documentElement.getAttribute('data-theme')) redrawAll();
  });

  el('liveToggle').addEventListener('change', (e) => {
    try { localStorage.setItem('pymeop-live', e.target.checked ? '1' : '0'); } catch (err) { /* private mode */ }
    if (e.target.checked) poll(); else setLive('off', 'paused');
  });
  // a hidden tab stops asking; catch up as soon as it is looked at again
  document.addEventListener('visibilitychange', poll);
}

function initLive() {
  let saved = null;
  try { saved = localStorage.getItem('pymeop-live'); } catch (e) { /* private mode */ }
  el('liveToggle').checked = saved !== '0';
  setLive(el('liveToggle').checked ? 'on' : 'off', el('liveToggle').checked ? '' : 'paused');
  setInterval(poll, POLL_MS);
}

function showError(err) {
  el('welcome').hidden = false;
  el('workspace').hidden = true;
  el('welcome').innerHTML = '<h2>Could not read that</h2><p>' +
    String(err.message || err) + '</p>';
}

/* The folder comes from the address bar if it names one, else the one this
   browser used last. A remembered folder that has since gone falls back to the
   default rather than leaving the page on an error. */
async function start() {
  const fromHash = readHash().dir;
  let saved = null;
  try { saved = localStorage.getItem('pymeop-dir'); } catch (e) { /* private mode */ }
  state.dir = fromHash || saved || null;
  try {
    await loadFiles();
  } catch (err) {
    if (fromHash || !saved) throw err;
    state.dir = null;
    await loadFiles();
  }
}

initTheme();
wire();
start().catch(showError).then(initLive);
