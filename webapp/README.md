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

**Across the run** opens on polarization against elapsed time, and will plot any
other fitted quantity for every scan in the file: the two peak heights, their
positions, their widths, the height ratio, or R². The whiskers are the ±1σ
uncertainties stored with each fit.

Click a point to open that scan in the plot below; a dashed rule marks whichever
scan is open, so the two plots stay tied together. Arrow keys and the slider move
the same selection.

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
