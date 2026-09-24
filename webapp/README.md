# PyMEOP data browser

A local web app for reading the event files the DAQ writes to `data/`. It is read
only: it opens the files, it never edits or moves them.

## Running it

```
python webapp/server.py
```

It serves on <http://localhost:8000> and opens a browser. Useful flags:

| Flag | Meaning |
|---|---|
| `--port 9000` | listen somewhere else |
| `--host 0.0.0.0` | let other machines on the network reach it (default is localhost only) |
| `--no-browser` | do not open a browser window |

No new dependencies: the server is Python standard library, and `dataset.py` uses
`numpy` and `PyYAML`, which the DAQ application already needs. The chart library
is vendored in `static/vendor/`, so the app works with no network connection.

The event directory comes from `event_dir` in `config.yaml`, so the browser always
looks where the DAQ writes.

## Watching a run live

With **Live** ticked in the top bar (the default) the page checks the data
directory every two seconds and redraws when a file changes, so a run the DAQ is
writing grows on screen scan by scan: the run chart gains points, the tiles and
the scan count update, and any zoom on the run chart is kept.

Sitting on the **last scan of the newest run** means following it: each new scan
opens as it arrives, and when the DAQ rolls over to a new event file (every 200
scans, or when a run is stopped and started) the page moves to the new file.
Anywhere else, the scan on screen stays put while the rest of the run updates
around it. The DAQ renames a file when it closes it (`current_<start>.txt` becomes
`<start>__<stop>.txt`); the page follows the rename.

The dot beside Live is green while watching, grey when paused, and red if the
server has stopped answering. Checks stop while the tab is hidden and catch up
when it is shown again. Untick Live to freeze the page, for instance while
comparing against a scan that would otherwise scroll away.

## What it shows

Pick a run in the left sidebar. Files with no scans in them are hidden by default
— of the files in `data/` most are empty, written when a run started and stopped
without recording anything.

**Over time** is polarization against time, across as many runs as you like. It
opens on the newest run. Tick runs in the file list to add them (shift-click ticks
a range), or use the buttons above the list: **Open run**, **24 h** and **7 days**
(each counted back from the end of the run that is open), or **All**. Clicking a
run's name opens it and, if it is not already plotted, plots just that run. Runs
are joined in time order with a break in the line between them, and across any
pause much longer than the scan cadence. **Against → Scan number** closes up the
gaps when the runs are hours apart. Any other fitted quantity can be plotted
instead: the two peak heights, their positions, their widths, the height ratio, or
R². The whiskers are ±1σ: stored with each fit for the peak parameters, and
propagated from the two peak heights for polarization.

Click a point to open that scan in the plots below, whichever run it is in; a
dashed rule marks whichever scan is open. Arrow keys and the slider step through
the open run. The address bar keeps the plotted runs, so a bookmark brings back
the same window.

**Plot CSV** saves one row per plotted scan: time, file, sweep direction, R², the
peak heights, the height ratio, and polarization with its uncertainty.

### Sweep direction

Each scan ramps the probe current either up or down, and the DAQ alternates
them. **Scans** chooses what to plot: low → high, high → low, or both. With both,
they are drawn as two series (blue and orange for a single quantity; for the two
peaks, the colour stays with the peak and a hollow marker means high → low).

With both on show, the line above the plot gives the difference, low → high minus
high → low, and how many standard errors it is from zero. It is worked out from
neighbouring pairs of opposite scans in the same run, taking every pair in either
order, so a steady rise or fall in the polarization cancels out and does not show
up as asymmetry. Neighbouring pairs share a scan, so the standard error allows for
the correlation between successive differences. With scans selected for a fit
(below), it covers only those scans.

### Build-up and relaxation times

Under the plot, **Drag to → Select for fit** switches dragging from zooming to
picking scans. Drag across the build-up or relaxation, then **Fit exponential**
fits

```
P(t) = P∞ + (P₀ − P∞) · exp(−(t − t₀)/τ)
```

with t₀ the first selected scan. P∞ is fitted too, or held at zero with
**P∞ → Fixed at 0**. Each direction on show is fitted separately, and with both
on show, the two together as well. The table gives τ, P₀ and P∞ with ±1σ, the
number of scans, whether it is a build-up or a relaxation, and χ²ν. The fitted
curves are drawn over the data. Each scan is weighted by its polarization
uncertainty, and the parameter errors are scaled by √χ²ν as scipy's `curve_fit`
does by default. A τ shown in red is not measured by the selected span: the curve
over it is nearly straight, so select a longer stretch. A drag that ends within a
few pixels of either edge of the plot takes every scan beyond it.

The fit searches τ from a thousandth to a thousand times the selected span,
solving for P₀ and P∞ exactly at each τ, so it needs no starting guess. It is in
`fitting.py`. Changing r₀, the quantity shown, the directions shown or the P∞
setting clears the fits, since they would no longer describe what is on the plot.

A single failed fit can throw the height ratio out by orders of magnitude and
flatten a whole run into a line at zero. When that happens the axis is set from
the 2nd to 98th percentile of the scans instead, the outliers run off the plot,
and a note in red says how many did. Switching to Fit R² is usually the quickest
way to find them; the off-scale points stay clickable.

**Scan** plots one scan: the measured lock-in R against probe current, the stored
fit, the two gaussian components drawn sitting on the baseline, and the baseline
itself. Underneath is the residual, sharing the cursor with the plot above. The
fit panel lists every parameter with its ±1σ and flags R² in red when it drops
below 0.99. Arrow keys step through the scans.

Drag across a plot to zoom, double-click to reset, and click a name in the legend
to hide that trace — useful when a failed fit has a component running far off the
scale of the data.

## Polarization and r₀

Polarization is computed from the two fitted peak heights as

```
P = (r/r₀ − 1) / (r/r₀ + 1),   r = peak 1 height / peak 2 height
```

r₀ is the same height ratio measured with the target unpolarized. It is a property
of the setup rather than of the file — the archived event files do not record the
zero readings — so it is entered in the top bar and defaults to 1. Change it and
every polarization number on the page updates. Treat P as meaningful only once r₀
is set to the zero measurement that belongs with the run.

## Baseline conventions

The DAQ fits two gaussians on a polynomial baseline, and `baseline_degree` in
`config.yaml` chooses the baseline: `1` for a straight one, `2` for a quadratic.
The parameters after the six gaussian ones are that polynomial's coefficients,
highest power first, so a straight baseline records eight parameters in total and
a quadratic nine.

Three forms are therefore readable, and the browser tells them apart by what the
event file records rather than by counting parameters:

| Written | Marker in the file | Baseline |
|---|---|---|
| current | `base_deg` | that degree, referenced to mid scan |
| after mid-scan referencing, before the flag | `x_ref`, no `base_deg` | degree from the coefficient count, referenced to mid scan |
| the original archive | neither | straight, in raw current |

The distinction matters: a new eight parameter fit and an old one have the same
shape but different baselines, one about mid scan and one about zero. The fit
panel names the convention for whichever scan is open. Everything in `data/`
today is the original archive form.

## Getting the numbers out

- **Scan CSV** — the plotted columns for the scan on screen.
- **Run CSV** — one row per scan: timestamps, R², height ratio, polarization, and
  every fit parameter with its uncertainty.
- **Raw file** — the original event file, untouched.

The address bar carries the selection (`#file=...&scan=...`), so a particular scan
can be bookmarked or sent to someone looking at the same data directory.

## Layout

```
webapp/
  server.py            HTTP server and JSON API
  dataset.py           reads and caches the event files, rebuilds fit components
  fitting.py           exponential fits for build-up and relaxation times
  static/
    index.html         the page
    app.js             charts, panels, CSV export
    style.css          theme tokens, light and dark
    vendor/            uPlot, vendored for offline use
```

The API, should anything else want it:

| Route | Returns |
|---|---|
| `GET /api/files` | every event file with scan count, duration and size |
| `GET /api/version` | a fingerprint of the data directory that changes on any write; cheap enough to poll |
| `GET /api/file/<name>` | the file plus a summary of each scan |
| `GET /api/file/<name>/event/<i>` | one scan: points, fit, components, residual |
| `GET /api/file/<name>/raw` | the original file |
| `POST /api/fit/exp` | exponential fit; body `{"t": [...], "y": [...], "sigma": [...] or null, "asymptote": null or 0}` |
