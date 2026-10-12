"""SQLite cache of the event files and of the fits made from them.

The event files stay the record; this is a cache the server builds from them and
can throw away. It holds three things:

  - where each scan's line sits in its file, and the summary of the scan as the
    DAQ fit it, so a run's scan list or the time plot over many runs comes
    straight from the database without parsing any files;
  - for each file path, the size and time it had when last read, so only files
    that changed are looked at again;
  - every refit the browser has asked for, so a view in another shape, baseline
    or fit range is fit once and kept, across server restarts.

Runs are known by the timestamp in their name and a hash of their first line,
not by their path. The DAQ renames a file when it closes a run, and a run may be
copied into a folder of hand-picked runs; neither reads or fits it again. The
DAQ only ever appends to a file, so a file that grew is read from where the last
read stopped. A line with no newline after it yet is a scan still being written,
and is left for the next read.

Delete the database file to rebuild it from scratch. CACHE_VERSION is bumped
whenever what is stored changes, which also rebuilds it. Refits are kept per
version of app/scanfit.py, so changing the fitting code refits on next view.
"""

import concurrent.futures
import hashlib
import json
import multiprocessing
import os
import sqlite3
import threading

import numpy as np

from webapp import dataset

# bump when the stored summaries or refit results change shape or meaning
CACHE_VERSION = 1

# scans sent to a fitting process at a time; the fits within a run go in order,
# each seeded from the last, so a run is fit chunk by chunk, saved as it goes
FIT_CHUNK = 10

SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
    run_key TEXT PRIMARY KEY,
    read_to INTEGER NOT NULL,     -- bytes of the file read into lines so far
    settings TEXT,                -- DAQ settings from the first scan, json
    x_key TEXT);
CREATE TABLE IF NOT EXISTS lines (
    run_key TEXT NOT NULL,
    line INTEGER NOT NULL,        -- line number among the non-blank lines
    offset INTEGER NOT NULL,
    length INTEGER NOT NULL,
    idx INTEGER,                  -- scan number, NULL for a line that is not a scan
    start_stamp REAL,
    stop_stamp REAL,
    summary TEXT,                 -- Event.summary() as stored by the DAQ, json
    PRIMARY KEY (run_key, line));
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    run_key TEXT,                 -- NULL until the first scan is written
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS refits (
    run_key TEXT NOT NULL,
    view TEXT NOT NULL,           -- shape, baseline, range and fitter version
    idx INTEGER NOT NULL,
    start_stamp REAL,
    result TEXT,                  -- the refit, json; NULL keeps the stored fit
    summary TEXT,                 -- Event.summary() under the refit, json
    seed TEXT,                    -- seed for the next scan's fit, json
    PRIMARY KEY (run_key, view, idx));
'''


def fit_version():
    '''Short hash of the DAQ's fitting code, so refits from older code are not reused'''

    path = os.path.join(dataset.ROOT, 'app', 'scanfit.py')
    try:
        with open(path, 'rb') as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        return 'none'


def plain(a):
    '''A fit array as nested lists of floats, for json; None stays None'''

    return None if a is None else np.asarray(a, dtype=float).tolist()


def fit_scans(tasks, seed, time_limit):
    '''Fit a stretch of a run's scans in order, in a worker process.

    Args:
        tasks: Per scan, ('keep', seed or None) for a scan whose stored fit is
            kept, with the seed it gives the next fit if its fit was good, or
            ('fit', x, y, profile, base_deg) for one to refit
        seed: Parameters of the last good fit before the stretch, or None
        time_limit: Seconds one fit may take before it is given up
    Returns:
        Per scan, (result or None, seed after it)
    '''

    from app import scanfit
    out = []
    for task in tasks:
        if task[0] == 'keep':
            if task[1] is not None:
                seed = task[1]
            out.append((None, seed))
            continue
        _, x, y, profile, base_deg = task
        b = scanfit.ScanFitter(base_deg, profile, time_limit).fit(x, y, seed)
        res = {'pf': plain(b['pf']), 'pstd': plain(b.get('pstd')), 'pcov': plain(b.get('pcov')),
               'ok': bool(b['ok']), 'message': b['message'], 'x_ref': float(b['x_ref']),
               'base_deg': base_deg, 'profile': profile}
        if res['ok']:
            seed = res['pf']
        out.append((res, seed))
    return out


class Store:
    '''The cache database, safe to use from the server's request threads'''

    def __init__(self, path, fit_workers=2):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._local = threading.local()
        self._write = threading.Lock()   # one writer at a time; readers go on under WAL
        self._guard = threading.Lock()
        self._key_locks = {}             # run key or view -> lock, so one thread does each read or fit
        self._progress = {}              # refits under way
        self.fit_version = fit_version()
        self.fit_workers = fit_workers
        self._pool = None

        db = self.db()
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        row = db.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        if row is None or row[0] != str(CACHE_VERSION):
            with self._write, db:
                for table in ('runs', 'lines', 'files', 'refits'):
                    db.execute('DELETE FROM %s' % table)
                db.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)", (str(CACHE_VERSION),))

    def db(self):
        '''This thread's connection'''

        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.execute('PRAGMA synchronous=NORMAL')
            self._local.conn = conn
        return conn

    def lock(self, key):
        with self._guard:
            return self._key_locks.setdefault(key, threading.Lock())

    # ---------------- reading files in ----------------

    def ingest(self, path):
        '''Bring the cache up to date with one event file.

        Args:
            path: Absolute path to the file
        Returns:
            The run key, or None while the file has no complete scan line
        Raises:
            OSError if the file cannot be read
        '''

        st = os.stat(path)
        db = self.db()
        known = db.execute('SELECT run_key, size, mtime_ns FROM files WHERE path=?', (path,)).fetchone()
        if known and known[1] == st.st_size and known[2] == st.st_mtime_ns:
            return known[0]

        with open(path, 'rb') as f:
            first = f.readline()
            key = None
            if first.endswith(b'\n'):
                name = os.path.basename(path)
                stamp = dataset.NAME_STAMP.search(name)
                key = '%s:%s' % (stamp.group(0) if stamp else 'nostamp',
                                 hashlib.sha1(first).hexdigest()[:16])
                with self.lock(key):
                    self._read_lines(db, f, key)
        with self._write, db:
            db.execute('INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?)',
                       (path, key, st.st_size, st.st_mtime_ns))
        return key

    def _read_lines(self, db, f, key):
        '''Add the complete lines past what has been read of a run'''

        row = db.execute('SELECT read_to FROM runs WHERE run_key=?', (key,)).fetchone()
        read_to = row[0] if row else 0
        f.seek(0, os.SEEK_END)
        if f.tell() <= read_to:
            return
        f.seek(read_to)
        data = f.read()
        end = data.rfind(b'\n') + 1   # past the last complete line
        if end == 0:
            return
        line, idx = db.execute('SELECT COUNT(*), COUNT(idx) FROM lines WHERE run_key=?', (key,)).fetchone()
        rows, first_scan = [], None
        pos = 0
        for chunk in data[:end].split(b'\n')[:-1]:
            offset, length = read_to + pos, len(chunk)
            pos += length + 1
            if not chunk.strip():
                continue
            raw = dataset._scan_line(chunk)
            if raw is None:
                rows.append((key, line, offset, length, None, None, None, None))
            else:
                ev = dataset.Event(idx, raw)
                if first_scan is None and idx == 0:
                    first_scan = ev
                rows.append((key, line, offset, length, idx, dataset.finite(ev.start_stamp),
                             dataset.finite(ev.stop_stamp), json.dumps(ev.summary())))
                idx += 1
            line += 1
        with self._write, db:
            db.executemany('INSERT INTO lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)
            if row is None:
                db.execute('INSERT INTO runs VALUES (?, ?, NULL, NULL)', (key, read_to + end))
            else:
                db.execute('UPDATE runs SET read_to=? WHERE run_key=?', (read_to + end, key))
            if first_scan is not None:
                db.execute('UPDATE runs SET settings=?, x_key=? WHERE run_key=?',
                           (json.dumps(first_scan.raw.get('settings') or {}), first_scan.x_key, key))

    def forget(self, key):
        '''Drop everything known about a run, to read it again from scratch'''

        db = self.db()
        with self._write, db:
            for table in ('lines', 'runs', 'refits', 'files'):
                db.execute('DELETE FROM %s WHERE run_key=?' % table, (key,))

    def prune(self, base, paths):
        '''Forget the file paths in a folder that are no longer there; the runs
        they held are kept, as a renamed or moved file is the same run'''

        db = self.db()
        known = [p for (p,) in db.execute('SELECT path FROM files')
                 if os.path.dirname(p) == base and p not in paths]
        if known:
            with self._write, db:
                db.executemany('DELETE FROM files WHERE path=?', [(p,) for p in known])

    # ---------------- what the API reads ----------------

    def scans(self, key, size):
        '''A run's scan lines within the first size bytes of its file:
        (idx, offset, length, start_stamp, summary json), in order'''

        return self.db().execute(
            'SELECT idx, offset, length, start_stamp, summary FROM lines '
            'WHERE run_key=? AND idx IS NOT NULL AND offset + length <= ? ORDER BY line',
            (key, size)).fetchall()

    def info(self, key, size):
        '''Scan count, unreadable lines, first start, last stop, x axis and settings'''

        db = self.db()
        n, bad, start, stop = db.execute(
            'SELECT COUNT(idx), COUNT(*) - COUNT(idx), MIN(start_stamp), MAX(stop_stamp) '
            'FROM lines WHERE run_key=? AND offset + length <= ?', (key, size)).fetchone()
        run = db.execute('SELECT x_key, settings FROM runs WHERE run_key=?', (key,)).fetchone()
        x_key, settings = run if run else (None, None)
        return {'n_events': n, 'bad_lines': bad, 'start_stamp': start, 'stop_stamp': stop,
                'x_key': x_key or 'current', 'settings': json.loads(settings) if settings else {}}

    # ---------------- refits ----------------

    def view_key(self, profile, base, xrange):
        return '%s|%s|%s|%s' % (profile or '-', base or '-',
                                '%r,%r' % xrange if xrange else '-', self.fit_version)

    def refits(self, run, profile, base, xrange):
        '''Each scan's refit for a view, fitting any not yet in the cache.

        Follows the DAQ: scans in order, each seeded from the last good fit
        before it. A scan already stored the wanted way keeps its fit, and seeds
        the next. A seed of another shape or baseline goes unused, and the
        fitter estimates that scan from scratch.

        Args:
            run: The StoredRun
            profile: 'gauss' or 'voigt', or None to keep each scan's own
            base: Baseline degree 1 or 2, or None to keep each scan's own
            xrange: (lo, hi) to fit only the points with x in that range, or None
        Returns:
            Per scan, (result or None to keep the stored fit, summary json or None)
        '''

        view = self.view_key(profile, base, xrange)
        scans = run.scans()
        db = self.db()
        with self.lock((run.key, view)):
            done = db.execute('SELECT idx, start_stamp, result, summary, seed FROM refits '
                              'WHERE run_key=? AND view=? ORDER BY idx', (run.key, view)).fetchall()
            n = 0   # scans already fit, as long as they are the run's first ones
            while n < len(done) and n < len(scans) and done[n][0] == scans[n][0] \
                    and done[n][1] == scans[n][3]:
                n += 1
            if n < len(done):
                with self._write, db:
                    db.execute('DELETE FROM refits WHERE run_key=? AND view=? AND idx>=?',
                               (run.key, view, n))
            out = [(None if r is None else json.loads(r), s) for _, _, r, s, _ in done[:n]]
            seed = json.loads(done[n - 1][4]) if n and done[n - 1][4] else None
            if n == len(scans):
                return out

            prog = {'name': run.name, 'profile': profile, 'base': base,
                    'range': list(xrange) if xrange else None, 'done': 0, 'total': len(scans) - n}
            with self._guard:
                self._progress[(run.key, view)] = prog
            try:
                for i in range(n, len(scans), FIT_CHUNK):
                    events = [run.load(s) for s in scans[i:i + FIT_CHUNK]]
                    fits = self._fit(self._tasks(events, profile, base, xrange), seed)
                    rows = []
                    for ev, (res, seed) in zip(events, fits):
                        summary = None
                        if res is not None:
                            res['range'] = list(xrange) if xrange else None
                            summary = json.dumps(ev.with_fit(res).summary())
                        out.append((res, summary))
                        rows.append((run.key, view, ev.index, dataset.finite(ev.start_stamp),
                                     None if res is None else json.dumps(res), summary,
                                     None if seed is None else json.dumps(seed)))
                    with self._write, db:
                        db.executemany('INSERT OR REPLACE INTO refits VALUES (?, ?, ?, ?, ?, ?, ?)', rows)
                    prog['done'] += len(events)
            finally:
                with self._guard:
                    self._progress.pop((run.key, view), None)
            return out

    @staticmethod
    def _tasks(events, profile, base, xrange):
        '''What fit_scans() is to do with each scan'''

        fitting = dataset.scanfit()
        tasks = []
        for ev in events:
            inside = ev.in_range(xrange) if xrange else None
            if ((profile is None or ev.profile == profile)
                    and (base is None or ev.base_deg == base)
                    and (inside is None or inside.all())
                    and len(ev.pf) >= ev.n_peak + 2):
                tasks.append(('keep', [float(p) for p in ev.pf] if ev.fit_ok is not False else None))
            else:
                shape = profile or ev.profile
                base_deg = base or (ev.base_deg if ev.base_deg in fitting.BASELINE_DEGREES
                                    else fitting.DEFAULT_BASELINE_DEGREE)
                x, y = (ev.x, ev.rs) if inside is None else (ev.x[inside], ev.rs[inside])
                tasks.append(('fit', x, y, shape, base_deg))
        return tasks

    def _fit(self, tasks, seed):
        '''Run fit_scans() in the worker processes, or here with none'''

        if not self.fit_workers:
            return fit_scans(tasks, seed, dataset.REFIT_TIME_LIMIT)
        for attempt in (1, 2):
            with self._guard:
                if self._pool is None:
                    # spawn, not fork: the server has threads and open databases
                    self._pool = concurrent.futures.ProcessPoolExecutor(
                        self.fit_workers, mp_context=multiprocessing.get_context('spawn'))
                pool = self._pool
            try:
                return pool.submit(fit_scans, tasks, seed, dataset.REFIT_TIME_LIMIT).result()
            except concurrent.futures.process.BrokenProcessPool:
                with self._guard:   # a worker died; start a fresh pool and try once more
                    if self._pool is pool:
                        self._pool = None
                if attempt == 2:
                    raise

    def refit_progress(self):
        with self._guard:
            return [dict(p) for p in self._progress.values()]

    def close(self):
        if self._pool is not None:
            self._pool.shutdown(cancel_futures=True)


class StoredRun:
    '''One event file as the cache knows it'''

    def __init__(self, store, path, key, stat):
        self.store = store
        self.path = path
        self.name = os.path.basename(path)
        self.key = key
        self.size = stat.st_size
        self.mtime = stat.st_mtime

    def scans(self):
        if self.key is None:
            return []
        return self.store.scans(self.key, self.size)

    def load(self, scan):
        '''Parse one scan from the file, given its row from scans()'''

        idx, offset, length, start_stamp, _ = scan
        with open(self.path, 'rb') as f:
            f.seek(offset)
            raw = dataset._scan_line(f.read(length))
        if raw is None or dataset.finite(raw.get('start_stamp')) != start_stamp:
            # the file is not the one that was read in; start over on it
            self.store.forget(self.key)
            raise ValueError('%s changed on disk; reload the page' % self.name)
        return dataset.Event(idx, raw)

    def info(self, error=None):
        '''One row for the file list'''

        d = {'name': self.name, 'size': self.size, 'mtime': self.mtime, 'n_events': 0,
             'bad_lines': 0, 'error': error, 'start_stamp': None, 'stop_stamp': None,
             'duration': None, 'x_key': 'current'}
        if self.key is not None:
            i = self.store.info(self.key, self.size)
            start, stop = dataset.finite(i['start_stamp']), dataset.finite(i['stop_stamp'])
            d.update({'n_events': i['n_events'], 'bad_lines': i['bad_lines'], 'x_key': i['x_key'],
                      'start_stamp': start, 'stop_stamp': stop,
                      'duration': dataset.finite(stop - start) if start and stop else None})
        return d

    def view(self, shape, base, xrange):
        '''Per scan, (refit result or None, summary json) for a view'''

        if shape not in dataset.SHAPES:
            raise ValueError('Unknown line shape %r, expected one of %s'
                             % (shape, ', '.join(dataset.SHAPES)))
        base = dataset.baseline_choice(base)
        xrange = dataset.range_choice(xrange)
        scans = self.scans()
        if shape == 'recorded' and base is None and xrange is None:
            return [(None, s[4]) for s in scans]
        fits = self.store.refits(self, None if shape == 'recorded' else shape, base, xrange)
        return [(res, s[4] if summary is None else summary) for s, (res, summary) in zip(scans, fits)]

    def detail(self, shape='recorded', base=None, xrange=None):
        '''The file list row plus a summary of every scan in the file'''

        d = self.info()
        d['settings'] = self.store.info(self.key, self.size)['settings'] if self.key else {}
        d['shape'] = shape
        d['base'] = dataset.baseline_choice(base)
        r = dataset.range_choice(xrange)
        d['range'] = list(r) if r else None
        d['events'] = [json.loads(s) for _, s in self.view(shape, base, xrange)]
        return d

    def event(self, i, shape='recorded', base=None, xrange=None):
        '''Everything needed to draw one scan, or None if there is no such scan'''

        view = self.view(shape, base, xrange)
        scans = self.scans()
        if not 0 <= i < len(view):
            return None
        ev = self.load(scans[i])
        res = view[i][0]
        return (ev if res is None else ev.with_fit(res)).detail()


class Library:
    '''The data directories, read through the cache.

    Every method takes the directory to work in, from dataset.resolve_dir(), so
    each browser tab can look at its own folder.
    '''

    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._index_locks = {}   # directory -> lock, one index pass per folder at a time
        self._progress = {}      # directory -> progress of the index pass under way

    def names(self, base):
        '''Event file names in a directory, newest run first'''

        try:
            names = [n for n in os.listdir(base)
                     if dataset.is_event_name(n) and dataset.safe_path(n, base)]
        except OSError:
            return []
        return sorted(names, key=lambda n: (-dataset.name_stamp(n), n))

    def run(self, name, base):
        '''The StoredRun for a file name, or None if it is not an event file'''

        path = dataset.safe_path(name, base)
        if not path:
            return None
        key = self.store.ingest(path)
        return StoredRun(self.store, path, key, os.stat(path))

    def row(self, name, base):
        '''File list row for a file name, or None if it is not an event file'''

        path = dataset.safe_path(name, base)
        if not path:
            return None
        try:
            key = self.store.ingest(path)
        except OSError as e:
            return StoredRun(self.store, path, None, os.stat(path)).info(str(e))
        return StoredRun(self.store, path, key, os.stat(path)).info()

    def index(self, base):
        '''File list rows for a whole data directory.

        The first pass over a folder reads every file in it into the cache, which
        takes a while, so its progress is kept for progress() to report. After
        that only changed files are read, and only their new lines.
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
                    try:
                        row = self.row(name, base)
                    except OSError:
                        row = None  # renamed by the DAQ since the listing
                    if row:
                        rows.append(row)
                    prog['done'] += 1
                    prog['bytes_done'] += size
            finally:
                with self._lock:
                    self._progress.pop(base, None)
            self.store.prune(base, {os.path.join(base, r['name']) for r in rows})
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
            if not dataset.is_event_name(e.name):
                continue
            try:
                # not e.stat(): on Windows that is the directory entry, which keeps
                # the size and time from when the DAQ opened the file until it closes
                st = os.stat(e.path)
            except OSError:
                continue  # renamed between the listing and the stat
            h.update(('%s\0%d\0%d\n' % (e.name, st.st_size, st.st_mtime_ns)).encode('utf-8'))
        return h.hexdigest()[:16]
