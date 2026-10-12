'''PyMEOP slow controls, J.Maxwell

EPICS process variables listed in the epics section of config.yaml, kept up to
date by Channel Access monitors, shown on the Slow Controls tab, and written to
the slow controls log. Some are controls the tab can set, within limits given in
the config.

The log is a second kind of data file, beside the event files. Each line is a
JSON record with a kind:

  - snapshot: every PV's last value, IOC timestamp and alarm severity, written
    every log_interval seconds
  - set: a value changed from this program, an EPICS control or one of the
    program's own (signal generator, zero), with the old and new value and what
    changed it
  - connect / disconnect: a PV came or went, so gaps in the snapshots are explained

Log files are named like event files, and a new one is started each UTC day.
'''

import datetime
import json
import logging
import math
import os
import shutil
import threading

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from app.datafiles import open_current, close_current

try:
    import epics
    EPICS_ERROR = None
except Exception as e:  # pyepics missing, or its Channel Access library failed to load
    epics = None
    EPICS_ERROR = f"pyepics unavailable: {e}"

DEFAULT_LOG_INTERVAL = 10.0  # seconds between snapshots
SEVERITIES = ('NO_ALARM', 'MINOR', 'MAJOR', 'INVALID')


def plain(value):
    '''A PV or program value as something json can write'''

    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, bytes):
        return value.decode(errors='replace')
    if isinstance(value, float) and not math.isfinite(value):
        return None  # json has no nan or inf
    return value


class PVSpec():
    '''One PV from the config: how to show it and whether it can be set'''

    def __init__(self, entry):
        self.pv = str(entry['pv'])
        self.label = str(entry.get('label', self.pv))
        self.units = str(entry.get('units', ''))
        self.format = str(entry.get('format', 'g'))
        self.group = str(entry.get('group', 'Process Variables'))
        self.readback = entry.get('readback')
        self.control = bool(entry.get('control', False))
        self.limits = None
        if self.control:
            try:
                lo, hi = (float(x) for x in entry['limits'])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"control {self.pv} needs limits: [low, high]")
            if not lo < hi:
                raise ValueError(f"control {self.pv} limits {entry['limits']} are not low, high")
            self.limits = (lo, hi)

    def channels(self):
        '''PV names this entry monitors'''

        return [self.pv, self.readback] if self.readback else [self.pv]

    def show(self, value):
        '''Value as text for the display'''

        if value is None:
            return ''
        try:
            return format(value, self.format)
        except (TypeError, ValueError):
            return str(value)


def load_specs(config_dict):
    '''PV entries and log interval from the epics section of the config.

    Entries that don't make sense are left out with a warning rather than
    stopping the program.

    Returns:
        (list of PVSpec, log interval in seconds, list of problems found)
    '''

    section = config_dict.get('epics') or {}
    problems = []
    try:
        interval = float(section.get('log_interval', DEFAULT_LOG_INTERVAL))
        if not interval > 0:
            raise ValueError
    except (TypeError, ValueError):
        problems.append(f"log_interval {section.get('log_interval')!r} is not a positive number, "
                        f"using {DEFAULT_LOG_INTERVAL}")
        interval = DEFAULT_LOG_INTERVAL

    specs = []
    seen = set()
    for entry in section.get('pvs') or []:
        try:
            spec = PVSpec(entry)
        except Exception as e:
            problems.append(f"Skipped PV entry {entry!r}: {e}")
            continue
        if seen & set(spec.channels()):
            problems.append(f"Skipped PV {spec.pv}: listed more than once")
            continue
        seen.update(spec.channels())
        specs.append(spec)
    for p in problems:
        logging.warning(p)
    return specs, interval, problems


class SlowLog():
    '''Slow controls log: JSON line records, a new file each UTC day.

    Records come from the GUI, from scan and Larmor threads, and from Channel
    Access callbacks, so writes are locked.
    '''

    def __init__(self, directory):
        self.directory = directory
        self.lock = threading.Lock()
        self.last_set = {}  # last value set of each of the program's own settings, for "old"
        self.file = None
        self.open()

    def open(self):
        self.file, self.path, self.start = open_current(self.directory)
        self.day = datetime.datetime.now(tz=datetime.timezone.utc).date()
        logging.info(f"Opened new slow controls log {self.path}")

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.file is None:
            return
        try:
            new = close_current(self.file, self.path, self.start, self.directory)
            logging.info(f"Closed slow controls log and moved to {new}.")
        except OSError as e:
            logging.info(f"Error closing slow controls log: {e}")
        self.file = None

    def write(self, kind, **fields):
        '''Write one record of the given kind'''

        now = datetime.datetime.now(tz=datetime.timezone.utc)
        record = {'kind': kind, 'time': str(now), 'stamp': now.timestamp()}
        record.update(fields)
        line = json.dumps(record, default=plain)
        with self.lock:
            if self.file is None:
                return  # closed on exit
            if now.date() != self.day:
                self._close()
                self.open()
            self.file.write(line + '\n')
            self.file.flush()  # so a crash loses nothing already logged

    def known(self, name, value):
        '''Note a setting's value as read back, so the next set of it logs the right old value'''

        self.last_set[name] = value

    def set(self, name, value, source, old=None, changed_only=False):
        '''Record a setting changed from this program.

        Args:
            name: PV name, or a name for one of the program's own settings
            value: value set
            source: what set it, e.g. 'user', 'larmor', 'relaxation'
            old: value before, or None to use the last one logged for name
            changed_only: skip it if the value is the same as the last one logged,
                for settings the program sends over and over, like discharge on
        '''

        if old is None:
            old = self.last_set.get(name)
        if changed_only and name in self.last_set and old == value:
            return
        self.last_set[name] = value
        self.write('set', name=name, value=plain(value), old=plain(old), source=source)


class PVManager(QObject):
    '''Channel Access monitors on the configured PVs, and the latest value of each.

    Monitor and connection callbacks run on a Channel Access thread. They only
    update the latest values and emit signals, which reach the GUI on its own thread.
    '''

    changed = pyqtSignal(str)            # PV name with a new value
    connection = pyqtSignal(str, bool)   # PV name, connected or not

    def __init__(self, specs, slowlog, ca_addr_list=None):
        super().__init__()
        self.specs = specs
        self.slowlog = slowlog
        self.error = None
        self.lock = threading.Lock()
        self.latest = {}   # name: {'value', 'ts', 'sev'}
        self.connected = {}
        self.channels = {}
        self.controls = {s.pv: s for s in specs if s.control}
        names = [name for s in specs for name in s.channels()]
        if not names:
            return
        if epics is None:
            self.error = EPICS_ERROR
            logging.warning(self.error)
            return

        if ca_addr_list:  # read when the Channel Access context is made, at the first PV
            os.environ['EPICS_CA_ADDR_LIST'] = str(ca_addr_list)
            os.environ['EPICS_CA_AUTO_ADDR_LIST'] = 'NO'
        if not shutil.which('caRepeater'):  # pyepics ships one beside its ca library, off the path
            try:
                os.environ['PATH'] += os.pathsep + os.path.dirname(epics.ca.find_libca())
            except Exception:
                pass
        try:
            for name in names:
                self.connected[name] = False
                self.channels[name] = epics.PV(name, form='time', auto_monitor=True,
                                               callback=self.on_value,
                                               connection_callback=self.on_connection)
        except Exception as e:
            self.error = f"Channel Access failed to start: {e}"
            logging.exception(self.error)

    def names(self):
        return list(self.connected)

    def on_value(self, pvname=None, value=None, timestamp=None, severity=None, **kw):
        with self.lock:
            self.latest[pvname] = {'value': plain(value), 'ts': timestamp, 'sev': severity}
        self.changed.emit(pvname)

    def on_connection(self, pvname=None, conn=None, **kw):
        conn = bool(conn)
        self.connected[pvname] = conn
        self.slowlog.write('connect' if conn else 'disconnect', name=pvname)
        self.connection.emit(pvname, conn)

    def value(self, name):
        '''Last value of a PV, or None if it has none or is disconnected'''

        if not self.connected.get(name):
            return None
        with self.lock:
            return self.latest.get(name, {}).get('value')

    def severity(self, name):
        with self.lock:
            return self.latest.get(name, {}).get('sev')

    def snapshot(self):
        '''Last value of every PV, None where disconnected, for an event'''

        return {name: self.value(name) for name in self.connected}

    def log_snapshot(self):
        '''Write every PV's last value, timestamp and severity to the log'''

        if not self.connected:
            return
        pvs = {}
        with self.lock:
            for name, conn in self.connected.items():
                last = self.latest.get(name)
                pvs[name] = dict(last) if conn and last else None
        self.slowlog.write('snapshot', pvs=pvs)

    def put(self, name, value, source='user'):
        '''Set a control PV, within its limits, and log the change.

        Raises:
            ValueError: name is not a control, the value is outside its limits,
                or the PV is not connected
        '''

        spec = self.controls.get(name)
        if spec is None:
            raise ValueError(f"{name} is not a control")
        value = float(value)
        lo, hi = spec.limits
        if not (math.isfinite(value) and lo <= value <= hi):
            raise ValueError(f"{spec.label} must be between {lo:g} and {hi:g} {spec.units}".rstrip())
        if not self.connected.get(name):
            raise ValueError(f"{spec.label} ({name}) is not connected")
        old = self.value(name)
        self.channels[name].put(value, wait=False)
        self.slowlog.set(name, value, source, old=old)
        logging.info(f"Set {name} to {value} from {old}")

    def disconnect(self):
        for pv in self.channels.values():
            try:
                pv.clear_callbacks()
                pv.disconnect()
            except Exception:
                pass
        self.channels = {}
