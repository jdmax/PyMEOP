"""Read-only access to the PyMEOP event files under the data directory.

Event files are written by Event.print_event() as one JSON object per line, so a
file is a run and each line in it is one scan. Files are parsed lazily and cached
on modification time, since a long run is a few hundred scans of a hundred points
and reparsing it on every request is wasteful.
"""

import hashlib
import json
import os
import re
import threading

import numpy as np

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# current_2022-01-06_18-42-11.txt -> the run start, used to order the file list
NAME_STAMP = re.compile(r'(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})')


def data_dir():
    '''Event directory from config.yaml, falling back to data/ beside the app'''

    try:
        with open(os.path.join(ROOT, 'config.yaml')) as f:
            settings = yaml.safe_load(f)['settings']
        return os.path.join(ROOT, settings.get('event_dir', 'data'))
    except Exception:
        return os.path.join(ROOT, 'data')


def safe_path(name):
    '''Resolve an event file name inside the data directory, or None.

    Only names that land directly in the data directory are accepted, so a
    crafted request cannot walk out of it.

    Args:
        name: File name from the request path
    Returns:
        Absolute path to an existing file, or None if the name is not one
    '''

    base = os.path.realpath(data_dir())
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


N_GAUSS_PARS = 6  # two gaussians, each a position, a sigma and a height


def fit_parts(x, pf, x_ref, referenced):
    '''The two gaussians and the baseline of a fit, each evaluated on x.

    The parameters after the six gaussian ones are the baseline polynomial
    coefficients, highest power first, so one or two of them is a straight
    baseline or a quadratic without any further special casing.

    Args:
        x: Scan x axis values
        pf: Fit parameter list, gaussians first
        x_ref: Baseline reference, the mid scan x
        referenced: True if the baseline is about x_ref rather than raw x
    Returns:
        (g1, g2, base) arrays, the gaussians about zero and the baseline
    '''

    x = np.asarray(x, dtype=float)
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
        if self.base_deg is None and len(self.pf) > N_GAUSS_PARS:
            self.base_deg = len(self.pf) - N_GAUSS_PARS - 1

        self.fit = np.array(raw.get('fit') or [], dtype=float)
        self.g1 = self.g2 = self.base = np.array([])
        if len(self.pf) >= 8 and len(self.x):
            self.g1, self.g2, self.base = fit_parts(
                self.x, self.pf, self.x_ref, self.referenced)
            if len(self.fit) != len(self.x):
                self.fit = self.g1 + self.g2 + self.base

        self.rsq = r_squared(self.rs, self.fit) if len(self.fit) == len(self.rs) else None

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
            'peak1': self.peak(1),
            'peak2': self.peak(2),
        }

    def detail(self):
        '''Everything needed to draw the scan, its fit and the fit components'''

        d = self.summary()
        d.update({
            'x_ref': finite(self.x_ref),
            'baseline': {'degree': self.base_deg, 'referenced': self.referenced},
            'x': finite_list(self.x),
            'currs': finite_list(self.currs),
            'waves': finite_list(self.waves),
            'times': finite_list(self.times),
            'signal': finite_list(self.rs),
            'fit': finite_list(self.fit),
            # the gaussians are drawn sitting on the baseline, as the run tab draws
            # them, so the components stay in the range of the data
            'g1': finite_list(self.g1 + self.base) if len(self.g1) else [],
            'g2': finite_list(self.g2 + self.base) if len(self.g2) else [],
            'base': finite_list(self.base),
            'residual': finite_list(self.rs - self.fit) if len(self.fit) == len(self.rs) else [],
            'pcov': [finite_list(row) for row in (self.raw.get('pcov') or [])],
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

    def detail(self):
        '''The file list row plus a summary of every scan in the file'''

        d = self.info()
        d['settings'] = self.events[0].raw.get('settings') if self.events else {}
        d['events'] = [e.summary() for e in self.events]
        return d


class Library:
    '''The data directory, caching parsed files until they change on disk'''

    def __init__(self):
        self._runs = {}
        self._lock = threading.Lock()

    def names(self):
        '''Event file names, newest run first'''

        try:
            names = [n for n in os.listdir(data_dir())
                     if n.lower().endswith(('.txt', '.json', '.jsonl'))
                     and safe_path(n)]
        except OSError:
            return []
        return sorted(names, key=lambda n: (-name_stamp(n), n))

    def run(self, name):
        '''Parsed run for a file name, or None if the name is not an event file.

        The cache is keyed on size and modification time so a file still being
        written by a live run is picked up again once it grows.
        '''

        path = safe_path(name)
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

    def index(self):
        '''File list rows for the whole data directory'''

        rows = []
        for name in self.names():
            run = self.run(name)
            if run:
                rows.append(run.info())
        # the DAQ renames a file when it closes it, so drop cache entries for
        # paths that are gone rather than holding every old name forever
        live = {os.path.join(os.path.realpath(data_dir()), r['name']) for r in rows}
        with self._lock:
            for path in [p for p in self._runs if p not in live]:
                del self._runs[path]
        return rows

    def fingerprint(self):
        '''A short string that changes whenever an event file is added, removed or
        written. Only stats the directory, so a browser can poll it every few
        seconds and fetch the file list only when something has actually changed.
        '''

        h = hashlib.sha1()
        try:
            entries = sorted(os.scandir(data_dir()), key=lambda e: e.name)
        except OSError:
            return ''
        for e in entries:
            if not e.name.lower().endswith(('.txt', '.json', '.jsonl')):
                continue
            try:
                # not e.stat(): on Windows that is the directory entry, which keeps
                # the size and time from when the DAQ opened the file until it closes
                st = os.stat(e.path)
            except OSError:
                continue  # renamed between the listing and the stat
            h.update(('%s\0%d\0%d\n' % (e.name, st.st_size, st.st_mtime_ns)).encode('utf-8'))
        return h.hexdigest()[:16]
