"""Read-only access to the PyMEOP event files under the data directory.

Event files are written by Event.print_event() as one JSON object per line, so a
file is a run and each line in it is one scan. Files are parsed lazily and cached
on modification time, since a long run is a few hundred scans of a hundred points
and reparsing it on every request is wasteful.
"""

import copy
import hashlib
import json
import os
import re
import sys
import threading

import numpy as np

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# current_2022-01-06_18-42-11.txt -> the run start, used to order the file list
NAME_STAMP = re.compile(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})')


EVENT_EXTS = ('.txt', '.json', '.jsonl')

# set by the server from its command line; None falls through to config.yaml
DEFAULT_DIR = None

# folders the browser may open must lie under this one; None allows any folder
FOLDER_LIMIT = None


def data_dir():
    '''Default event directory: the server's --data-dir, else event_dir from
    config.yaml, falling back to data/ beside the app'''

    if DEFAULT_DIR:
        return os.path.realpath(DEFAULT_DIR)
    try:
        with open(os.path.join(ROOT, 'config.yaml')) as f:
            settings = yaml.safe_load(f)['settings']
        return os.path.realpath(os.path.join(ROOT, settings.get('event_dir', 'data')))
    except Exception:
        return os.path.realpath(os.path.join(ROOT, 'data'))


def within(path, root):
    '''True if path is root or somewhere below it'''

    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives on Windows
        return False


def resolve_dir(requested=None):
    '''The folder a request asks for, checked, or the default one.

    Relative paths are taken from the repository root, as event_dir is.

    Args:
        requested: Folder from the request, or None/'' for the default
    Returns:
        Absolute real path of an existing directory
    Raises:
        ValueError if it is not a directory or lies outside FOLDER_LIMIT
    '''

    if not requested:
        return data_dir()
    path = os.path.realpath(os.path.join(ROOT, os.path.expanduser(requested)))
    if not os.path.isdir(path):
        raise ValueError('No such folder: %s' % requested)
    if FOLDER_LIMIT and not within(path, os.path.realpath(FOLDER_LIMIT)):
        raise ValueError('Folders outside %s are not available' % FOLDER_LIMIT)
    return path


def is_event_name(name):
    return name.lower().endswith(EVENT_EXTS)


def folders(requested=None):
    '''A folder and the folders inside it, for the folder picker.

    Args:
        requested: Folder to list, or None for the default
    Returns:
        dict with the path, its parent (None at the top or at FOLDER_LIMIT),
        the default folder, how many event files the folder holds, and each
        subfolder with its own event file count
    '''

    path = resolve_dir(requested)
    parent = os.path.dirname(path)
    if parent == path or (FOLDER_LIMIT and not within(parent, os.path.realpath(FOLDER_LIMIT))):
        parent = None

    def count(d):
        try:
            with os.scandir(d) as it:
                return sum(1 for e in it if is_event_name(e.name) and e.is_file())
        except OSError:
            return None  # no permission, or gone

    dirs = []
    try:
        with os.scandir(path) as it:
            entries = sorted((e for e in it if not e.name.startswith('.')),
                             key=lambda e: e.name.lower())
            for e in entries:
                try:
                    if e.is_dir():
                        dirs.append({'name': e.name, 'path': e.path, 'n_files': count(e.path)})
                except OSError:
                    continue
    except OSError as e:
        raise ValueError(str(e))
    return {'path': path, 'parent': parent, 'default': data_dir(),
            'n_files': count(path), 'dirs': dirs}


def safe_path(name, base):
    '''Resolve an event file name inside a data directory, or None.

    Only names that land directly in the directory are accepted, so a crafted
    request cannot walk out of it.

    Args:
        name: File name from the request path
        base: Directory from resolve_dir()
    Returns:
        Absolute path to an existing file, or None if the name is not one
    '''

    path = os.path.realpath(os.path.join(base, os.path.basename(name)))
    if os.path.dirname(path) != base or not os.path.isfile(path):
        return None
    return path


def name_stamp(name):
    '''Sort key from the timestamp in the file name, or 0 if it has none'''

    m = NAME_STAMP.search(name)
    if not m:
        return 0.0
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    return ((((y * 100 + mo) * 100 + d) * 100 + h) * 100 + mi) * 100 + s


# Peak line shapes, as app/scanfit.py names them. A Voigt fit has a Lorentzian
# width for each peak after the six gaussian parameters, then the baseline.
PROFILES = ('gauss', 'voigt')
N_GAUSS_PARS = 6  # two gaussians, each a position, a sigma and a height
N_VOIGT_PARS = 8

# what the browser can show: each scan's fit as the DAQ stored it, or every scan
# in one shape, refitting the ones stored in the other
SHAPES = ('recorded',) + PROFILES


def n_peak_pars(profile):
    return N_VOIGT_PARS if profile == 'voigt' else N_GAUSS_PARS


def scanfit():
    '''The DAQ's own fitting module, imported only when a Voigt or a refit is
    needed, since it brings in scipy and a browser of gaussian fits can do
    without it'''

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from app import scanfit as module
    return module


def fit_parts(x, pf, x_ref, referenced, profile='gauss'):
    '''The two peaks and the baseline of a fit, each evaluated on x.

    The parameters after the peak ones are the baseline polynomial
    coefficients, highest power first, so one or two of them is a straight
    baseline or a quadratic without any further special casing.

    Args:
        x: Scan x axis values
        pf: Fit parameter list, peaks first
        x_ref: Baseline reference, the mid scan x
        referenced: True if the baseline is about x_ref rather than raw x
        profile: 'gauss' or 'voigt'
    Returns:
        (g1, g2, base) arrays, the peaks about zero and the baseline
    '''

    x = np.asarray(x, dtype=float)
    if profile == 'voigt':
        return scanfit().parts(x, pf, x_ref if referenced else 0.0, 'voigt')
    g1 = pf[2] * np.exp(-np.power(x - pf[0], 2) / (2 * np.power(pf[1], 2)))
    g2 = pf[5] * np.exp(-np.power(x - pf[3], 2) / (2 * np.power(pf[4], 2)))
    base = np.polyval(pf[N_GAUSS_PARS:], x - x_ref if referenced else x)
    return g1, g2, base


def r_squared(y, fit):
    '''Coefficient of determination, or None where it is not defined'''

    y, fit = np.asarray(y, dtype=float), np.asarray(fit, dtype=float)
    good = np.isfinite(y) & np.isfinite(fit)
    if good.sum() < 2:
        return None
    y, fit = y[good], fit[good]
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if not ss_tot > 0:
        return None
    return finite(1 - float(np.sum((y - fit) ** 2)) / ss_tot)


def finite(v):
    '''A float that json can represent, or None for nan and inf'''

    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def finite_list(seq):
    '''A list of json safe floats, nan and inf becoming null'''

    return [finite(v) for v in (seq if seq is not None else [])]


def recorded_r0(raw):
    '''The zero polarization height ratio the DAQ used for a scan, or None.

    The DAQ writes its zero peak heights, p1_zero and p2_zero from the run tab,
    into every event and reads polarization off r/r0 with r0 their ratio. Older
    files predate this and carry neither.
    '''

    p1, p2 = finite(raw.get('p1_zero')), finite(raw.get('p2_zero'))
    if p1 is not None and p2:
        return finite(p1 / p2)
    r0 = finite(raw.get('r0'))
    return r0 if r0 else None


class Event:
    '''One scan: its raw points, its fit, and the numbers derived from the fit'''

    def __init__(self, index, raw):
        self.index = index
        self.raw = raw
        self.currs = np.array(raw.get('currs') or [], dtype=float)
        self.waves = np.array(raw.get('waves') or [], dtype=float)
        self.rs = np.array(raw.get('rs') or [], dtype=float)
        self.times = np.array(raw.get('times') or [], dtype=float)
        self.pf = list(raw.get('pf') or [])
        self.pstd = list(raw.get('pstd') or [])
        self.pcov = raw.get('pcov') or []
        self.profile = raw.get('profile') if raw.get('profile') in PROFILES else 'gauss'
        self.n_peak = n_peak_pars(self.profile)
        self.refit = False  # True once the fit is the browser's own rather than the DAQ's
        self.fit_ok = raw.get('fit_good')  # not in the oldest files
        self.fit_message = raw.get('fit_message')
        self.start_stamp = raw.get('start_stamp')
        self.stop_stamp = raw.get('stop_stamp')

        # wavelength is only the x axis when the wavemeter was actually read;
        # in current scans the column sits at zero and current is the axis
        self.x_key = 'wavelength' if np.any(self.waves != 0) else 'current'
        self.x = self.waves if self.x_key == 'wavelength' else self.currs

        # which way the sweep ran, from where it ended against where it started
        self.direction = None
        if len(self.x) > 1 and np.isfinite(self.x[0]) and np.isfinite(self.x[-1]):
            if self.x[-1] > self.x[0]:
                self.direction = 'up'
            elif self.x[-1] < self.x[0]:
                self.direction = 'down'

        # Which baseline convention this scan was written under. Files from before
        # the baseline was referenced to mid scan carry neither base_deg nor x_ref,
        # and their straight baseline is in raw x. Anything with x_ref is referenced,
        # and the coefficient count gives the degree when base_deg is not recorded.
        self.referenced = 'base_deg' in raw or 'x_ref' in raw
        if self.referenced and raw.get('x_ref') is not None:
            self.x_ref = float(raw['x_ref'])
        elif len(self.x):
            self.x_ref = float(0.5 * (np.min(self.x) + np.max(self.x)))
        else:
            self.x_ref = 0.0

        self.base_deg = raw.get('base_deg')
        if self.base_deg is None and len(self.pf) > self.n_peak:
            self.base_deg = len(self.pf) - self.n_peak - 1

        self.components(np.array(raw.get('fit') or [], dtype=float))
        self.r0 = recorded_r0(raw)

    def components(self, fit):
        '''Rebuild the fit curve, its parts and R² from pf.

        Args:
            fit: The fit curve as stored, used when it matches the scan
        '''

        self.fit = fit
        self.g1 = self.g2 = self.base = np.array([])
        if len(self.pf) >= self.n_peak + 2 and len(self.x):
            try:
                self.g1, self.g2, self.base = fit_parts(
                    self.x, self.pf, self.x_ref, self.referenced, self.profile)
            except ImportError:  # a Voigt fit to draw, and no scipy to draw it with
                pass
            else:
                if len(self.fit) != len(self.x):
                    self.fit = self.g1 + self.g2 + self.base
        self.rsq = r_squared(self.rs, self.fit) if len(self.fit) == len(self.rs) else None

    def with_fit(self, res, profile):
        '''A copy of this scan carrying a refit in place of the stored fit.

        Args:
            res: One scan's entry from refit_run()
            profile: The shape it was fit with
        '''

        e = copy.copy(self)
        e.profile, e.n_peak, e.refit = profile, n_peak_pars(profile), True
        e.fit_ok, e.fit_message = res['ok'], res['message']
        e.base_deg = res['base_deg']
        e.referenced, e.x_ref = True, res['x_ref']
        e.pf = [] if res['pf'] is None else list(res['pf'])
        e.pstd = [] if res['pstd'] is None else list(res['pstd'])
        e.pcov = [] if res['pcov'] is None else res['pcov']
        e.components(np.array([]))
        return e

    def peak(self, i):
        '''Fitted height of peak 1 or 2, or None if the fit is missing'''

        idx = 2 if i == 1 else 5
        return finite(self.pf[idx]) if len(self.pf) > idx else None

    def summary(self):
        '''Per scan numbers for the run views, small enough to send for a whole file'''

        return {
            'index': self.index,
            'start_stamp': finite(self.start_stamp),
            'stop_stamp': finite(self.stop_stamp),
            'start_time': self.raw.get('start_time'),
            'duration': finite((self.stop_stamp or 0) - (self.start_stamp or 0)),
            'n_points': int(len(self.rs)),
            'x_key': self.x_key,
            'direction': self.direction,
            'x_min': finite(np.min(self.x)) if len(self.x) else None,
            'x_max': finite(np.max(self.x)) if len(self.x) else None,
            'signal_min': finite(np.min(self.rs)) if len(self.rs) else None,
            'signal_max': finite(np.max(self.rs)) if len(self.rs) else None,
            'pf': finite_list(self.pf),
            'pstd': finite_list(self.pstd),
            'rsq': self.rsq,
            'profile': self.profile,
            'refit': self.refit,
            'fit_ok': self.fit_ok,
            'fit_message': self.fit_message,
            'peak1': self.peak(1),
            'peak2': self.peak(2),
            'r0': self.r0,
        }

    def detail(self):
        '''Everything needed to draw the scan, its fit and the fit components'''

        d = self.summary()
        d.update({
            'x_ref': finite(self.x_ref),
            'baseline': {'degree': self.base_deg, 'referenced': self.referenced},
            'n_peak': self.n_peak,
            'x': finite_list(self.x),
            'currs': finite_list(self.currs),
            'waves': finite_list(self.waves),
            'times': finite_list(self.times),
            'signal': finite_list(self.rs),
            'fit': finite_list(self.fit),
            # the peaks are drawn sitting on the baseline, as the run tab draws
            # them, so the components stay in the range of the data
            'g1': finite_list(self.g1 + self.base) if len(self.g1) else [],
            'g2': finite_list(self.g2 + self.base) if len(self.g2) else [],
            'base': finite_list(self.base),
            'residual': finite_list(self.rs - self.fit) if len(self.fit) == len(self.rs) else [],
            'pcov': [finite_list(row) for row in self.pcov],
            'settings': self.raw.get('settings') or {},
        })
        return d


class Run:
    '''One event file, holding the scans it contains'''

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        stat = os.stat(path)
        self.size = stat.st_size
        self.mtime = stat.st_mtime
        self.events = []
        self.bad_lines = 0
        self.error = None
        self._views = {}
        self._parse()

    def _parse(self):
        # Read in one go and parse after closing: on Windows the DAQ cannot rename
        # a finished file while it is open here, so keep that window short.
        try:
            with open(self.path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError as e:
            self.error = str(e)
            return
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                self.events.append(Event(len(self.events), json.loads(line)))
            except (ValueError, TypeError):
                # a last line with no newline is a scan still being written, not a
                # bad one; it is read in full once the rest of it lands
                if i < len(lines) - 1:
                    self.bad_lines += 1

    def info(self):
        '''One row for the file list'''

        stamps = [e.start_stamp for e in self.events if e.start_stamp]
        stops = [e.stop_stamp for e in self.events if e.stop_stamp]
        return {
            'name': self.name,
            'size': self.size,
            'mtime': self.mtime,
            'n_events': len(self.events),
            'bad_lines': self.bad_lines,
            'error': self.error,
            'start_stamp': finite(min(stamps)) if stamps else None,
            'stop_stamp': finite(max(stops)) if stops else None,
            'duration': finite(max(stops) - min(stamps)) if stamps and stops else None,
            'x_key': self.events[0].x_key if self.events else 'current',
        }

    def view(self, shape='recorded'):
        '''The scans with their fits in one shape.

        Args:
            shape: 'recorded' for each scan's fit as the DAQ stored it, or
                'gauss' or 'voigt' for every scan in that shape, refitting any
                stored in the other
        Returns:
            List of Event, refit ones being copies of the stored ones
        Raises:
            ValueError for an unknown shape
        '''

        if shape not in SHAPES:
            raise ValueError('Unknown line shape %r, expected one of %s' % (shape, ', '.join(SHAPES)))
        if shape == 'recorded':
            return self.events
        if shape not in self._views:
            results = refit_run(self, shape)
            self._views[shape] = [e if r is None else e.with_fit(r, shape)
                                  for e, r in zip(self.events, results)]
        return self._views[shape]

    def detail(self, shape='recorded'):
        '''The file list row plus a summary of every scan in the file'''

        d = self.info()
        d['settings'] = self.events[0].raw.get('settings') if self.events else {}
        d['shape'] = shape
        d['events'] = [e.summary() for e in self.view(shape)]
        return d


# Refits, kept apart from the parsed runs: a live run is parsed again each time
# it grows, and the scans already refit should not be fit again with it.
_refits = {}        # (run start, shape) -> [(scan start stamp, result or None, seed after)]
_refit_locks = {}   # the same keys, so two requests for one run fit it only once
_refit_guard = threading.Lock()


def refit_run(run, profile):
    '''Fit every scan of a run whose stored fit is not in the given shape.

    The scans are fit in order, each seeded from the last good fit before it as
    the DAQ does. Scans stored in the wanted shape keep their fit and seed the
    next one.

    Args:
        run: The Run
        profile: 'gauss' or 'voigt'
    Returns:
        One entry per scan: None to keep the stored fit, or a dict of pf, pstd,
        pcov, ok, message, x_ref and base_deg (pf None if no fit converged)
    '''

    fitting = scanfit()
    stamp = NAME_STAMP.search(run.name)
    key = (stamp.group(0) if stamp else run.path, profile)
    with _refit_guard:
        lock = _refit_locks.setdefault(key, threading.Lock())
    with lock:
        done = _refits.get(key, [])
        n = 0  # scans already fit, as long as the file still starts with them
        while (n < len(done) and n < len(run.events)
               and done[n][0] == run.events[n].start_stamp):
            n += 1
        out = done[:n]
        seed = out[-1][2] if out else None
        for ev in run.events[n:]:
            res = None
            if ev.profile == profile and len(ev.pf) >= ev.n_peak + 2:
                if ev.fit_ok is not False:
                    seed = list(ev.pf)
            else:
                base_deg = (ev.base_deg if ev.base_deg in fitting.BASELINE_DEGREES
                            else fitting.DEFAULT_BASELINE_DEGREE)
                b = fitting.ScanFitter(base_deg, profile).fit(ev.x, ev.rs, seed)
                res = {'pf': b['pf'], 'pstd': b.get('pstd'), 'pcov': b.get('pcov'),
                       'ok': b['ok'], 'message': b['message'], 'x_ref': b['x_ref'],
                       'base_deg': base_deg}
                if b['ok']:
                    seed = list(b['pf'])
            out.append((ev.start_stamp, res, seed))
        _refits[key] = out
        return [r for _, r, _ in out]


class Library:
    '''The data directories, caching parsed files until they change on disk.

    Every method takes the directory to work in, from resolve_dir(), so each
    browser tab can look at its own folder. Parsed runs are cached by path, so
    moving between folders and back does not read the files again.
    '''

    def __init__(self):
        self._runs = {}
        self._lock = threading.Lock()
        self._index_locks = {}   # directory -> lock, one index pass per folder at a time
        self._progress = {}      # directory -> progress of the index pass under way

    def names(self, base):
        '''Event file names in a directory, newest run first'''

        try:
            names = [n for n in os.listdir(base) if is_event_name(n) and safe_path(n, base)]
        except OSError:
            return []
        return sorted(names, key=lambda n: (-name_stamp(n), n))

    def run(self, name, base):
        '''Parsed run for a file name, or None if the name is not an event file.

        The cache is keyed on size and modification time so a file still being
        written by a live run is picked up again once it grows.
        '''

        path = safe_path(name, base)
        if not path:
            return None
        stat = os.stat(path)
        key = (path, stat.st_size, stat.st_mtime)
        with self._lock:
            hit = self._runs.get(path)
            if hit and hit[0] == key:
                return hit[1]
        run = Run(path)
        with self._lock:
            self._runs[path] = (key, run)
        return run

    def index(self, base):
        '''File list rows for a whole data directory.

        The first pass over a large folder parses every file in it, which takes a
        while, so its progress is kept for progress() to report. Only one pass
        runs per folder at a time: a second request waits for the first and then
        finds everything cached.
        '''

        with self._lock:
            lock = self._index_locks.setdefault(base, threading.Lock())
        with lock:
            names = self.names(base)
            sizes = []
            for name in names:
                try:
                    sizes.append(os.stat(os.path.join(base, name)).st_size)
                except OSError:
                    sizes.append(0)
            prog = {'loading': True, 'done': 0, 'total': len(names),
                    'bytes_done': 0, 'bytes_total': sum(sizes)}
            with self._lock:
                self._progress[base] = prog
            rows = []
            try:
                for name, size in zip(names, sizes):
                    run = self.run(name, base)
                    if run:
                        rows.append(run.info())
                    prog['done'] += 1
                    prog['bytes_done'] += size
            finally:
                with self._lock:
                    self._progress.pop(base, None)
            # the DAQ renames a file when it closes it, so drop cache entries for
            # paths in this folder that are gone rather than holding every old name
            live = {os.path.join(base, r['name']) for r in rows}
            with self._lock:
                for path in [p for p in self._runs
                             if os.path.dirname(p) == base and p not in live]:
                    del self._runs[path]
            return rows

    def progress(self, base):
        '''How far the index pass over a folder has got, or loading False if
        none is running'''

        with self._lock:
            prog = self._progress.get(base)
            return dict(prog) if prog else {'loading': False}

    def fingerprint(self, base):
        '''A short string that changes whenever an event file is added, removed or
        written. Only stats the directory, so a browser can poll it every few
        seconds and fetch the file list only when something has actually changed.
        '''

        h = hashlib.sha1()
        try:
            entries = sorted(os.scandir(base), key=lambda e: e.name)
        except OSError:
            return ''
        for e in entries:
            if not is_event_name(e.name):
                continue
            try:
                # not e.stat(): on Windows that is the directory entry, which keeps
                # the size and time from when the DAQ opened the file until it closes
                st = os.stat(e.path)
            except OSError:
                continue  # renamed between the listing and the stat
            h.update(('%s\0%d\0%d\n' % (e.name, st.st_size, st.st_mtime_ns)).encode('utf-8'))
        return h.hexdigest()[:16]
