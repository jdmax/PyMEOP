# PyMEOP Refactor Plan

A prioritized checklist for restructuring PyMEOP. Each task is sized for one Claude session.
Work top to bottom; tasks in the same phase marked **‖** can run in parallel because they
touch different files. Check a box when its task is merged.

## Target layout

The goal is to separate three layers so the physics and the instrument I/O can be tested without Qt
or hardware:

```
main.py                      entry point: builds core objects, hands them to the GUI
config.yaml
app/
  core/          no Qt imports
    config.py        load + validate settings
    event.py         Event dataclass, JSON serialization
    fitting.py       two-gaussian + baseline fit (from gui.py Event.fit_scan & helpers)
    polarization.py  ratio -> M, sign convention, build-up / decay fits
    storage.py       EventWriter: rotating JSON-lines files, flush, close, rename
    scan.py          ScanRunner: steps currents, alternates direction, reads instruments
    relax.py         discharge-off relaxation state machine
  instruments/   no Qt imports
    transport.py     SocketTransport: locked query/write, flush, terminator, timeout, typed errors
    toptica.py  lockin.py  wavemeter.py  siggen.py
    sim.py           simulated instruments producing synthetic probe spectra
    manager.py       InstrumentManager: connect all, status, reconnect
  gui/           Qt only: widgets, plots, and thin QObject workers bridging core -> signals
    main_window.py  run_tab.py  find_tab.py  workers.py
tests/
```

**Rules for every session**
- `app/core` and `app/instruments` never import PyQt. Only `app/gui` touches Qt.
- Only a task's own files are edited. If another file needs to change, note it in the PR description.
- A "move" task keeps behaviour identical: fix nothing while moving, except what the task lists.
- Each task leaves `python main.py` launching (with simulated instruments once they exist) and `pytest` passing.
- Commit on a branch named after the task ID.

---

## Phase 0: Cleanup (no behaviour change)

- [x] **0.1 ‖ Untrack generated and personal files**
  `git rm --cached` for `app/__pycache__/`, `data/current_*`, `log/*`, `app/saved_session.yaml`,
  `3_9_2022_16_20.csv`, `.claude/settings.json` (move its contents to untracked `settings.local.json`).
  Add `data/.gitkeep`, `log/.gitkeep`, and ignore rules for `__pycache__/` everywhere.
  *Done when:* `git ls-files` shows only source, config, and requirements.
  **Keep a copy of a few `data/current_*.txt` files locally. Task 1.1 uses them as test fixtures.**

- [x] **0.2 ‖ Delete dead code**
  Remove `app/classes.py`, `app/gui_main_tab.py`, `app/magnet_control.py`, `discharge_script.py`,
  the `Keopsys` class, the commented-out LabJack code, `FindTab.dis_pushed`, `labjack_ip` in config, and unused
  imports. Remove `pytz` and `pyserial` from `requirements.txt`; add `pytest`.
  *Done when:* the app launches and `grep -r "LabJack\|Keopsys\|classes" app` returns nothing.

## Phase 1: Pure core with tests (no Qt, no hardware)

- [x] **1.1 Extract fitting into `app/core/fitting.py`**
  Move `fit_scan`, `try_fit`, `estimate_peaks`, `fit_bounds`, `check_fit`, `baseline_guess`, `peaks`,
  `r_squared`, and the constants from `app/gui.py` into plain functions returning a `FitResult` dataclass
  (`pf`, `pstd`, `pcov`, `ok`, `message`, `rsq`, `x_ref`, `fit_curve`). Add `tests/test_fitting.py`
  that uses synthetic spectra plus 2–3 saved real scans as fixtures (`tests/data/`).
  Write the tests against the current code *first* so they check that the move keeps results identical.
  *Done when:* `gui.py` calls `fitting.fit_scan(x, y, seed)` and the tests pass.

- [x] **1.2 ‖ `app/core/event.py`, `polarization.py`, `storage.py`** (after 1.1, parallel with 1.3)
  - `Event` dataclass: raw scan arrays, timestamps, scan direction, zero amplitudes, `FitResult`, polarization.
  - `polarization.py`: `ratio_to_pol(r, r0)`. The zero amplitudes are stored as floats, not parsed from GUI text.
  - `EventWriter`:
    - flushes after each line and rotates after N events;
    - fixes the rename bug (`self.config.settings`) and closes the file on shutdown;
    - writes `NaN` safely;
    - stops copying the whole `settings` dict and `pcov` into every line; it writes a run-header line instead.
  *Done when:* `gui.py` has no `Event` class and has `tests/test_storage.py` covering rotation, rename, and close.

- [x] **1.3 ‖ `app/core/config.py`**
  Load `config.yaml` into a validated settings object: required keys, types, directories created if missing,
  `scan_x_axis == 'wavelength'` rejected unless `scan_wave` is true. Session save/restore also lives here,
  with a missing or empty `saved_session.yaml` handled gracefully.
  *Done when:* `MainWindow` receives a settings object, and bad config gives one clear error at startup.

## Phase 2: Instrument layer

- [ ] **2.1 `app/instruments/transport.py` + drivers**
  Replace `telnetlib3` with a plain `socket` transport:
  - `query(cmd)` holds a lock, clears the input buffer, writes, reads to the terminator with a timeout, and
    raises `InstrumentError` or `InstrumentTimeout`;
  - `write(cmd)` for commands that don't reply;
  - the terminator is set per device, and connecting is explicit (`connect()`, `is_connected`), with no hidden
    try/except in `__init__`.

  Port `ProbeLaser` (check the `param-set!` return value; wide-scan uses `exec`), `LockIn` (parse `SNAPD?`, raise on
  malformed reply), `WavelengthMeter`, and `SigGen`. Add `tests/test_transport.py` against a local fake TCP server.
  *Done when:* no `telnetlib3` import remains and a timed-out reply cannot shift the replies that follow it.

- [ ] **2.2 `app/instruments/sim.py`** (after 2.1)
  Simulated drivers with the same interface: the lock-in returns two gaussian probe peaks on a sloped baseline plus
  noise, as a function of the simulated probe current, with polarization settable over time. Select with
  `simulate: true` in config or `python main.py --sim`.
  *Done when:* the full GUI runs scans and shows a fitted polarization with no hardware attached.

- [ ] **2.3 `app/instruments/manager.py`** (after 2.1)
  `InstrumentManager` connects every configured device, reports per-device status, allows reconnect, and is the
  *only* holder of instrument objects. Missing optional devices, such as no signal generator, are allowed; features
  that need them are disabled rather than failing.
  *Done when:* `MainWindow` no longer constructs drivers and a missing device shows in the status bar and doesn't
  block scanning.

## Phase 3: Acquisition logic out of the GUI

- [ ] **3.1 `app/core/scan.py` ScanRunner + `app/gui/workers.py`**
  Move the `RunThread` loop into a Qt-free `ScanRunner` that takes the manager, a current list, dwell, and a
  `should_stop()` callable, and yields points and completed scans. It:
  - records a lock-in failure as `NaN`, not 0;
  - tags each point with its scan direction;
  - checks for stop between points, not only between scans.

  `workers.py` wraps it in a `QObject` moved to a `QThread`, with signals `point`, `scan_done(Event)`,
  `error(str)`, `stopped`. It doesn't override `QThread.finished` and emits `stopped` in `finally`.
  Fitting runs in the same worker or a second one, and **the event travels in the signal** (fixes the
  `previous_event` race).
  *Done when:* no widget is read or written from a non-GUI thread in the Run path.

- [ ] **3.2 `app/core/relax.py` relaxation state machine** (after 3.1)
  Replace `RelaxThread` with explicit states (`DISCHARGE_ON_SCANNING` → `WAITING_SCAN_END` → `DISCHARGE_OFF` →
  `SETTLING` → …) driven by the worker. Stop takes effect within about 0.5 s in any state and leaves the discharge
  in a defined state. Add an optional settle scan to discard after the discharge turns back on.
  The GUI only receives `state_changed(str, seconds_left)`.
  *Done when:* stopping mid-way never starts a new scan and the buttons always return to a consistent state.

- [ ] **3.3 ‖ Find tab onto ScanRunner** (after 3.1, parallel with 3.2)
  Temperature and current searches reuse `ScanRunner` in a sweep mode and use `linspace` instead of `arange`.
  They choose X or R consistently with the Run tab and add a Stop button. The manager serializes them against a
  running Run scan, so two tabs can no longer send commands on the same socket.

## Phase 4: GUI becomes a thin view

- [ ] **4.1 Split and slim the GUI**
  Move the tabs into `app/gui/`. Remove `self.__dict__.update(parent.__dict__)`, and stop `MainWindow` replacing
  `width`/`height`/`left`/`top`. Tabs only build widgets, send user intents to the workers, and render results.
  Fix the `isRunning` calls missing their parentheses. Validate all numeric inputs before starting a scan.
  Install a `sys.excepthook` that logs errors and shows them in `QErrorMessage` instead of letting PyQt close the app.
  *Done when:* `app/gui` contains no fitting, file I/O, or instrument commands.

- [ ] **4.2 Logging cleanup**
  Replace the `print`s with `logging`, show instrument and fit errors in the status bar, and log each scan
  start and stop with its settings.

## Phase 5: Measurement features (on the clean base)

- [ ] **5.1 ‖ Lock-in settling:** read the time constant from the lock-in; warn or enforce `dwell ≥ 5τ`; compare
  forward and reverse scans, now that the direction is tagged, to measure lag bias.
- [ ] **5.2 ‖ Fit options:** a shared-sigma model (both lines have the same Doppler width), with an option to use the
  ratio of peak areas instead of heights; compare stability on the saved fixtures.
- [ ] **5.3 ‖ Run metadata and sign:** config and GUI fields for field (T), pumping line (f2±/f4±), pump power, and
  discharge amplitude/frequency, all written to the run header. The pumping line sets the polarization sign convention.
- [ ] **5.4 Build-up/relaxation analysis:** mark pump on/off times and fit M(t) = Ms(1 − e^(−t/Tb)) and the
  exponential decay Tr on the polarization history, shown on the polarization plot.
- [ ] **5.5 DC normalization:** read the photodiode DC level (lock-in aux input) and divide it out of the
  lock-in signal to remove the dependence on probe power versus current.
- [ ] **5.6 Hardware-timed scans:** Toptica wide-scan plus the lock-in capture buffer in place of point-by-point
  stepping, as another `ScanRunner` mode.

---

### Dependency summary
```
0.1, 0.2  ──►  1.1 ──► 1.2 ‖ 1.3
                          │
           2.1 ──► 2.2 ‖ 2.3
                          │
                3.1 ──► 3.2 ‖ 3.3 ──► 4.1 ──► 4.2 ──► 5.x
```
Phase 2 can start in parallel with Phase 1: it touches only `app/instruments/`, apart from the final
hookup into `gui.py`, which should be done after 1.2 merges.
