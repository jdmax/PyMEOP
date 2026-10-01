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
| `--data-dir DIR` | open this folder by default instead of `event_dir` from `config.yaml` |
| `--db FILE` | cache database, default `webapp/cache/pymeop.sqlite` (see [Loading](#loading)) |
| `--fit-workers N` | processes refitting runs side by side, default 2; 0 fits in the server process |

No new dependencies: the server is Python standard library, and `dataset.py` uses
`numpy`, `PyYAML` and `scipy`, which the DAQ application already needs. The chart library
is vendored in `static/vendor/`, so the app works with no network connection.

The default event directory comes from `event_dir` in `config.yaml`, so the
browser looks where the DAQ writes unless told otherwise.

## Running it on a server

The browser can also run all the time on another machine, reading a copy of the
event files that the DAQ machine mirrors to it. Run locally as above, it is
unchanged.

**Mirroring the data.** `deploy/mirror_data.sh` copies the DAQ's `data/` to any
number of places with rsync, and is meant for cron on the DAQ machine:

```
* * * * * /home/daq/PyMEOP/deploy/mirror_data.sh /home/daq/PyMEOP/data epics:/srv/pymeop/data group:/group/xxx/pymeop/data >> /home/daq/PyMEOP/log/mirror.log 2>&1
```

Each destination is `user@host:/path` over ssh, or a local or mounted path. They
are tried independently, so one being down does not hold up the rest, and the DAQ
never waits on any of them. Copies at the destinations are never deleted, except
the `current_` file of a run the DAQ has since closed and renamed, so a
destination keeps everything even if the DAQ's own folder is cleared. The script
prints only on failure.

ssh must work without a password for the user cron runs as: make a key with
`ssh-keygen -t ed25519`, add it to each destination with `ssh-copy-id`, and ssh
to each host once by hand to accept its host key. Short names like `epics` above
can be set up in `~/.ssh/config`.

**Serving it.** On the server, a copy of the repository and a virtual environment
with `numpy`, `scipy` and `PyYAML` is all it needs; the Qt and instrument
packages in `requirements.txt` are for the DAQ only.

```
python3 -m venv .venv
.venv/bin/pip install numpy scipy pyyaml
```

```
deploy/start_server.sh /srv/pymeop/data [PORT]    # starts it in screen session pymeop-web
deploy/stop_server.sh                             # stops it
```

The server runs in a detached `screen` session, on port 8000 unless another is
given, and is started again if it stops on an error. `screen -r pymeop-web`
looks in on it (Ctrl-a d to leave it running); its output is also kept in
`log/webapp.screen.log`. Live updates work as they do locally, a minute or so
behind the DAQ, as often as cron mirrors.

**Lab subnet only.** The server has no login, so let only the lab subnet reach
its port with the firewall:

```
sudo ufw allow from 129.57.36.0/23 to any port 8000 proto tcp
sudo ufw enable
```

with the subnet changed to the lab's. Check that ssh is still allowed
(`sudo ufw allow ssh`) before enabling ufw on a remote machine.

## Choosing a folder

The folder path in the top bar is a button. It opens a picker that walks the
folders on the machine running the server: click a folder to look inside it (each
one shows how many event files it holds), type or paste a path and press Enter,
or go **Up**. **Open this folder** switches the page to it, and **Default folder**
goes back to `event_dir`. This makes it easy to keep smaller folders of hand-picked
runs, for one study each, and open them without reading the whole archive.

A folder other than the default is outlined in blue in the top bar. The folder
goes into the address bar (`#dir=...`), so a bookmark reopens it, and the browser
remembers the last one it used. Each tab can look at its own folder. Live updates
watch whichever folder is open.

When the server is started with `--host` set to something other than localhost,
anyone who can reach it could use the picker, so it is then limited to folders
inside the default data directory.

## Loading

The server keeps what it reads from the event files in a cache database
(`store.py`, SQLite, `webapp/cache/pymeop.sqlite` unless `--db` says otherwise):
for each scan, where its line is in its file and its summary as the DAQ fit it,
and every refit the browser has asked for. The file list, a run's scans and the
time plot across many runs come from the database without parsing any files; a
scan's points are read from its line in the file when it is opened.

The first time the server sees a folder it reads every file in it, about 15 s
for 400 MB, and the page shows a progress bar with the files and megabytes read
so far. After that only files that have changed are read, and only their new
lines, so a live run costs only its new scans. The server reads the default
folder as soon as it starts and again every 10 seconds, so new scans are in the
cache before anyone asks for them.

The event files stay the record; the database can be deleted at any time, with
the server stopped, and is rebuilt from them. Runs are known by the timestamp in
their name and their first line rather than their path, so the DAQ renaming a
file it closed, or a run copied into a folder of hand-picked runs, is not read
or fit again. If a file is found to differ from what was read in, its run is
dropped from the cache and the page asks for a reload.

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

Pick a run in the left sidebar; nothing opens until you do, unless the address
names one (a reload or a bookmark reopens the run it was on). Files with no scans in them are hidden by default
— of the files in `data/` most are empty, written when a run started and stopped
without recording anything.

**Over time** is polarization against time, across as many runs as you like. It
starts on the run you open. Tick runs in the file list to add them (shift-click ticks
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

**Scan** plots one scan: the measured lock-in R against probe current, the fit,
the two peak components drawn sitting on the baseline, and the baseline itself. Underneath is the residual, sharing the cursor with the plot above. The
fit panel lists every parameter with its ±1σ and flags R² in red when it drops
below 0.99. Arrow keys step through the scans.

Drag across a plot to zoom, double-click to reset, and click a name in the legend
to hide that trace — useful when a failed fit has a component running far off the
scale of the data.

## Gaussian or Voigt

The DAQ fits each scan with two gaussians or two Voigt profiles, whichever is set
on its run tab, and records which in the scan (`profile`). Gaussians suit low
pressure, where the lines are Doppler broadened; at around 100 mbar the pressure
broadening is comparable and a gaussian biases the heights, and so the
polarization.

**Fit** in the top bar picks what the browser shows:

- **As recorded**, the default: each scan's fit as the DAQ stored it.
- **Gaussian** or **Voigt**: every scan in that shape. Scans the DAQ already fit
  that way keep their stored fit; the rest are refit on the server with the DAQ's
  own fitting code (`app/scanfit.py`), in order, each seeded from the last good
  fit as the DAQ does. A Voigt refit takes about 70 ms a scan, so the first look
  at a long run in the other shape takes ten seconds or so. Meanwhile a bar under
  the top bar shows the run being refit and how many of its scans are done.
  Refits are saved in the cache database as they are made, so each run is fit
  only once in each view, even across server restarts, and a live run only
  refits its new scans. Fits within a run go in order, but separate runs are
  fit side by side, one per `--fit-workers` process. A change to
  `app/scanfit.py` makes the cached refits stale, and runs are refit when next
  viewed.

The fit panel says when a scan's fit is a refit, and names the checks a fit
failed. **Show → Peak widths γ** plots the Lorentzian widths of Voigt fits. The
choice rides along in the address bar (`&shape=voigt`) and is remembered by the
browser.

**Baseline** beside it does the same for the polynomial under the peaks:

- **As recorded**, the default: each scan's baseline as the DAQ fit it.
- **Straight** or **Quadratic**: every scan on that baseline, refitting the scans
  the DAQ fit on the other in the same way. It combines with **Fit**, so
  *Voigt* on *Quadratic* refits any scan not already stored exactly that way.
  It rides along as `&base=1` or `&base=2`.

**Fit range** limits the fit to part of each scan. Below the scan plot, set
**Drag to** to **Set fit range** and drag across the scan: every scan is refit
using only the points with x in that range, in the chosen shape and baseline. The
parts left out are greyed on the scan and residual plots, the fit curves and
residuals stop at the range, and R² counts only the points fitted. The range is
in x, so it holds from scan to scan and across every run in the time plot; a run
swept over other currents may have too few points inside it to fit. A scan lying
entirely inside the range keeps its stored fit. **Fit whole scan** goes back to
every point. The range rides along in the address bar (`&range=114.8,126`) but,
unlike the other choices, is not remembered by the browser, so a range from
another day cannot quietly change every fit.

A single refit usually takes 20 to 150 ms. One still going after 2 s is given up
and the scan marked as a failed fit ("Fit gave up after 2 s."), so one bad scan
cannot hold up a run. The limit is `REFIT_TIME_LIMIT` in `dataset.py`; the DAQ's
own fits run without one.

Refitting changes the peak heights but not the r₀ recorded with each scan, which
came from heights under the DAQ's own fit. To read polarization off a refit, type
an r₀ measured with the same shape, baseline and range.

## Polarization and r₀

Polarization is computed from the two fitted peak heights as

```
P = (r/r₀ − 1) / (r/r₀ + 1),   r = peak 1 height / peak 2 height
```

This is the same formula the DAQ uses. r₀ is the same height ratio measured with the
target unpolarized. The DAQ writes its zero peak heights (`p1_zero`, `p2_zero`,
from **Set Current as Zero** on the run tab) into every scan, and the browser takes
r₀ = `p1_zero / p2_zero` from each scan, so its polarization matches what the DAQ
showed at the time. The P tiles say which r₀ they used.

The **r₀** box in the top bar is an override. Left empty, as it starts, each scan
uses its recorded r₀. A value typed there is used for every scan instead, which is
how to apply a zero measured later, or to give one to the older files that predate
the recorded zero and otherwise fall back to r₀ = 1. When the box is empty it shows
the run's recorded r₀ in grey. Clear the box, or click **Recorded r₀**, to go back
to the recorded values.

A relaxation that settles at a P∞ other than zero means the recorded r₀ is off.
Each fit with P∞ free has an **r₀ for P∞ = 0** column: the r₀ that would read
the fitted asymptote as zero, r₀ (1 + P∞)/(1 − P∞), with its 1σ from P∞ in the
tooltip. Click it to use that r₀ for every scan. The fits are then redone on the
same selection, and since P is not linear in r₀, the step is repeated until P∞
is zero to within 0.001 % (two or three steps). The P tiles and the fit note then
say r₀ was set for P∞ = 0. The column is empty when P∞ is held at zero, or when
the selected scans were read off different recorded r₀ values.

The earliest files with zero readings (before about 16:53 on 7 Jan 2022) have a
stored `pol` of 1.0 for every scan, from a bug in the DAQ at the time. The browser
recomputes P from the peak heights rather than reading `pol`, so those scans show
the right value.

## Baseline conventions

The DAQ fits two peaks on a polynomial baseline, and `baseline_degree` in
`config.yaml` chooses the baseline: `1` for a straight one, `2` for a quadratic.
The peak parameters come first: position, σ and height for peak 1, then peak 2,
and for a Voigt fit each peak's Lorentzian half width γ after those six. The
baseline polynomial's coefficients follow, highest power first, so a gaussian fit
records eight or nine parameters and a Voigt fit ten or eleven. The height is the
peak's height at its centre in either shape.

Three forms are therefore readable, and the browser tells them apart by what the
event file records rather than by counting parameters:

| Written | Marker in the file | Baseline |
|---|---|---|
| current | `base_deg` | that degree, referenced to mid scan |
| after mid-scan referencing, before the flag | `x_ref`, no `base_deg` | degree from the coefficient count, referenced to mid scan |
| the original archive | neither | straight, in raw current |

The distinction matters: a new eight parameter gaussian fit and an old one have
the same shape but different baselines, one about mid scan and one about zero. The fit
panel names the convention for whichever scan is open. Everything in `data/`
today is the original archive form.

## Getting the numbers out

- **Scan CSV** — the plotted columns for the scan on screen.
- **Run CSV** — one row per scan: timestamps, R², height ratio, polarization, and
  every fit parameter with its uncertainty.
- **Raw file** — the original event file, untouched.

The address bar carries the selection (`#dir=...&file=...&scan=...`, with `dir`
only for a folder other than the default), so a particular scan can be bookmarked
or sent to someone looking at the same data directory.

## Layout

```
webapp/
  server.py            HTTP server and JSON API
  dataset.py           parses scans, rebuilds fit components
  store.py             cache database of the files' scans and of refits
  fitting.py           exponential fits for build-up and relaxation times
../app/scanfit.py      the DAQ's peak fits, used here to refit scans
  static/
    index.html         the page
    app.js             charts, panels, CSV export
    style.css          theme tokens, light and dark
    vendor/            uPlot, vendored for offline use
```

The API, should anything else want it:

Every `GET` route but `/api/folders` takes `?dir=<folder>` to work in a folder other
than the default. The two scan routes also take `?shape=gauss` or `?shape=voigt` to
return every scan's fit in that shape, `?base=1` or `?base=2` for every scan on a
straight or quadratic baseline, and `?range=lo,hi` for fits over only the points
with x in that range, refitting as needed; left off, or `recorded`, they return
the fits as stored.

| Route | Returns |
|---|---|
| `GET /api/files` | every event file with scan count, duration and size |
| `GET /api/progress` | how far the server has got reading the folder: `loading`, `done`/`total` files and `bytes_done`/`bytes_total` |
| `GET /api/refitting` | the refits under way, each with its file `name`, `profile`, `base`, `range` and `done`/`total` scans |
| `GET /api/folders?path=<folder>` | a folder's subfolders, each with its event file count, for the picker |
| `GET /api/version` | a fingerprint of the data directory that changes on any write; cheap enough to poll |
| `GET /api/file/<name>` | the file plus a summary of each scan |
| `GET /api/file/<name>/event/<i>` | one scan: points, fit, components, residual |
| `GET /api/file/<name>/raw` | the original file |
| `POST /api/fit/exp` | exponential fit; body `{"t": [...], "y": [...], "sigma": [...] or null, "asymptote": null or 0}` |
