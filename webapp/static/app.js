/* PyMEOP data browser.

   Talks to the read only API in server.py and draws two levels of the same run:
   how the fitted quantities move across the run, and one scan with its fit and
   the fit components. Scans are stored in whatever order the current ramped, so
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
};

const charts = { run: null, scan: null, resid: null };

const el = (id) => document.getElementById(id);

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
            if (y === null || e === null || !isFinite(e) || e <= 0) continue;
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

/* A rule through the scan that is open below, so the run chart says which point
   the lower plot is showing. xs is the run chart's own x column. */
function selectionMarker(xs) {
  return {
    hooks: {
      draw: (u) => {
        const xv = xs[state.eventIdx];
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

/* ---------------- run chart ---------------- */

const METRICS = {
  peaks: {
    axis: () => 'Peak height (lock-in R)',
    series: [
      { name: 'Peak 1 height', pick: (e) => e.peak1, err: (e) => e.pstd[2], color: 's3' },
      { name: 'Peak 2 height', pick: (e) => e.peak2, err: (e) => e.pstd[5], color: 's4' },
    ],
  },
  positions: {
    axis: (run) => 'Peak position' + (run.x_key === 'wavelength' ? '' : ' (A)'),
    series: [
      { name: 'Peak 1 position', pick: (e) => e.pf[0], err: (e) => e.pstd[0], color: 's3' },
      { name: 'Peak 2 position', pick: (e) => e.pf[3], err: (e) => e.pstd[3], color: 's4' },
    ],
  },
  widths: {
    axis: (run) => 'Peak width σ' + (run.x_key === 'wavelength' ? '' : ' (A)'),
    series: [
      { name: 'Peak 1 σ', pick: (e) => e.pf[1], err: (e) => e.pstd[1], color: 's3' },
      { name: 'Peak 2 σ', pick: (e) => e.pf[4], err: (e) => e.pstd[4], color: 's4' },
    ],
  },
  polarization: {
    axis: () => 'Polarization (%)',
    series: [
      { name: 'P', pick: (e) => {
          const p = polarization(e.peak1, e.peak2, state.r0);
          return p === null ? null : p * 100;
        }, err: null, color: 's1' },
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

/* The x range the user has zoomed the run chart to, or null if it shows the
   whole run. Only kept while the chart shows the same view of the same run, so
   a live redraw keeps the zoom but choosing another metric still resets it. */
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

function drawRunChart(opts) {
  const node = el('runChart');
  const run = state.run;
  if (!run || !run.events.length) { node.innerHTML = ''; return; }
  const viewKey = el('metric').value + '|' + el('runX').value + '|' + runStamp(run.name);
  const zoom = opts && opts.keepZoom ? runZoom(node, viewKey) : null;

  const p = palette();
  const spec = METRICS[el('metric').value];
  const byTime = el('runX').value === 'time';
  const t0 = run.start_stamp || 0;

  const xs = run.events.map((e, i) =>
    byTime ? ((e.start_stamp || 0) - t0) : (i + 1));

  const cols = [xs];
  const sigmas = [null];
  const series = [{ label: byTime ? 'Elapsed' : 'Scan', value: (u, v) =>
    v === null ? DASH : (byTime ? fmtDuration(v) : '#' + v) }];

  spec.series.forEach((s) => {
    cols.push(run.events.map((e) => pick(s.pick, e)));
    sigmas.push(s.err ? run.events.map((e) => pick(s.err, e)) : null);
    series.push({
      label: s.name,
      stroke: p[s.color],
      width: 2,
      points: { show: true, size: 5, stroke: p[s.color], fill: p[s.color] },
      value: (u, v) => fmt(v, 5),
    });
  });

  // one scale, always: two measures of different size get their own view
  // rather than a second y axis
  const every = [];
  for (let i = 1; i < cols.length; i++) every.push.apply(every, cols[i]);
  const range = robustRange(every);
  const offScale = range
    ? every.filter((v) => v !== null && isFinite(v) && (v < range[0] || v > range[1])).length
    : 0;
  el('runNote').textContent = offScale
    ? offScale + (offScale === 1 ? ' point runs' : ' points run') +
      ' off this scale — drag to zoom, or switch to Fit R² to find the bad fits.'
    : '';

  const scales = range ? { y: { range: () => range } } : {};
  // a run's first scan is one point, which uPlot's auto range spreads across
  // an axis of nonsense values; a live run shows exactly that after each rollover
  if (xs.length === 1) scales.x = { time: false, range: () => [xs[0] - 1, xs[0] + 1] };

  charts.run = makePlot(node, {
    scales: scales,
    cursor: {
      drag: { x: true, y: false },
      points: { size: 8 },
      focus: { prox: 20 },
    },
    legend: { live: true },
    axes: [
      axisOpts(p, byTime ? 'Elapsed time (s)' : 'Scan number', true),
      axisOpts(p, spec.axis(run), false),
    ],
    series: series,
    plugins: [errorBars((si) => sigmas[si]), selectionMarker(xs)],
  }, cols);

  charts.run.__accent = palette().s1;
  node._viewKey = viewKey;
  if (zoom) charts.run.setScale('x', zoom);
  pickOnClick(charts.run, node);
}

/* Click a point on the run chart to open that scan below.

   uPlot's cursor.bind indirection did not fire here, so the listener goes
   straight on the plot surface. The index is taken at mousedown, before a drag
   can move the cursor, and a press that travels more than a few pixels is a
   zoom drag rather than a pick. */
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
    selectEvent(idx);
  });
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

  el('rawLink').href = 'api/file/' + encodeURIComponent(run.name) + '/raw';
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
    '   ·   ' + fmtStamp(ev.start_stamp) + '   ·   ' + fmtDuration(ev.duration);
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
  if (charts.run) charts.run.redraw();   // move the selection marker
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

  el('fileList').innerHTML = shown.map((f) => {
    const empty = f.n_events === 0;
    const meta = empty
      ? '<span>empty</span><span>' + fmtSize(f.size) + '</span>'
      : '<span>' + f.n_events + ' scan' + (f.n_events === 1 ? '' : 's') + '</span>' +
        '<span>' + fmtDuration(f.duration) + '</span>' +
        '<span>' + fmtSize(f.size) + '</span>';
    return '<li><button class="filerow' + (empty ? ' is-empty' : '') +
      '" type="button" data-name="' + f.name + '"' +
      (state.run && state.run.name === f.name ? ' aria-current="true"' : '') + '>' +
      '<span class="fdate">' + labelFromName(f.name) + '</span>' +
      '<span class="fmeta">' + meta + '</span></button></li>';
  }).join('') || '<li class="side-count" style="padding:10px">No files match.</li>';
}

/* ---------------- deep links ---------------- */

/* The address bar carries the selection, so a particular scan can be bookmarked
   or pasted to someone else looking at the same data directory. */
function readHash() {
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  const file = h.get('file');
  const scan = parseInt(h.get('scan'), 10);
  return { file: file || null, scan: isFinite(scan) ? scan - 1 : 0 };
}

function writeHash() {
  if (!state.run) return;
  const h = 'file=' + encodeURIComponent(state.run.name) + '&scan=' + (state.eventIdx + 1);
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

async function loadFiles() {
  const data = await getJSON('api/files');
  state.files = data.files;
  state.version = data.version;
  el('dataDir').textContent = data.data_dir;
  renderFileList();
  const want = readHash();
  const known = want.file && state.files.some((f) => f.name === want.file);
  const first = state.files.find((f) => f.n_events > 0);
  if (known) {
    await selectFile(want.file, want.scan);
  } else if (first) {
    await selectFile(first.name);
  }
}

async function selectFile(name, startScan) {
  const run = await getJSON('api/file/' + encodeURIComponent(name));
  state.run = run;
  state.eventIdx = 0;
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
  drawRunChart();
  await selectEvent(startScan || 0);
}

async function selectEvent(idx) {
  if (!state.run || !state.run.events.length) return;
  idx = Math.max(0, Math.min(state.run.events.length - 1, idx));
  state.eventIdx = idx;
  state.event = await getJSON(
    'api/file/' + encodeURIComponent(state.run.name) + '/event/' + idx);
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
  const data = await getJSON('api/files');
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
    await selectFile(newest, row.n_events - 1);
    return;
  }

  // the open file, possibly under the name the DAQ gave it on closing
  const row = state.files.find((f) => f.name === run.name) ||
    state.files.find((f) => runStamp(f.name) === runStamp(run.name));
  if (!row || (row.name === run.name && row.size === run.size && row.mtime === run.mtime)) {
    renderFileList();   // nothing new in the open run, or it has gone
    return;
  }

  if (!run.events.length) {   // an empty file that has had its first scan
    await selectFile(row.name, tailing ? row.n_events - 1 : 0);
    return;
  }

  const fresh = await getJSON('api/file/' + encodeURIComponent(row.name));
  if (state.run !== run) return;   // another run was picked while this loaded
  const prevIdx = state.eventIdx;
  state.run = fresh;
  renderFileList();
  if (!fresh.events.length) return;
  renderRunBar();
  drawRunChart({ keepZoom: true });
  const idx = tailing ? fresh.events.length - 1 : Math.min(prevIdx, fresh.events.length - 1);
  if (idx !== prevIdx || !state.event) {
    await selectEvent(idx);
  } else {
    renderScanNav();
    writeHash();
  }
}

async function poll() {
  if (polling || document.hidden || !el('liveToggle').checked) return;
  polling = true;
  try {
    const { version } = await getJSON('api/version');
    if (version !== state.version) {
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
  drawRunChart();
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
  });

  el('fileSearch').addEventListener('input', renderFileList);
  el('hideEmpty').addEventListener('change', renderFileList);

  el('metric').addEventListener('change', drawRunChart);
  el('runX').addEventListener('change', drawRunChart);
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
    if (el('metric').value === 'polarization') drawRunChart();
    if (state.event) renderFitPanel();
  });

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

initTheme();
wire();
loadFiles().catch(showError).then(initLive);
