'''PyMEOP J.Maxwell 2021

Write events to rotating JSON-lines eventfiles. No Qt here.

A file is named current_<start>.txt while it is being written, and renamed to
<start>__<stop>.txt when it is closed or rotated. Its first line is a header,
{"header": {...}}, holding the run settings; every following line is one event.
Non-finite numbers are written as null so every line is standard JSON.
'''

import datetime
import json
import logging
import math
import os

import numpy as np

FORMAT_VERSION = 2  # 1 was the pre-refactor layout, with settings copied into every event
STAMP = '%Y-%m-%d_%H-%M-%S'


def utc_now():
    return datetime.datetime.now(tz=datetime.timezone.utc)


def json_safe(value):
    '''Convert numpy types and containers to plain JSON types, with non-finite numbers as None'''

    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [json_safe(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (datetime.datetime, datetime.date)):
        return str(value)
    return value


class EventWriter:
    '''Append event records to eventfiles in a directory, rotating every max_events.

    The file is opened on the first write, so starting and stopping the app without
    taking data leaves no empty files behind.

    Args:
        directory: Where to write eventfiles
        header: Dict written as the first line of every file, e.g. the run settings
        max_events: Events per file before rotating to a new one
        clock: Callable returning the current UTC datetime, replaceable for tests
    '''

    def __init__(self, directory, header=None, max_events=200, clock=utc_now):
        self.directory = directory
        self.header = header or {}
        self.max_events = max_events
        self.clock = clock
        self.file = None
        self.path = None
        self.start = None
        self.events = 0  # events in the open file

    def write(self, record):
        '''Write one event record as a line, rotating first if the open file is full'''

        if self.file is not None and self.events >= self.max_events:
            self.close()
        if self.file is None:
            self.open()
        self.file.write(json.dumps(json_safe(record), allow_nan=False) + '\n')
        self.file.flush()  # a crash loses at most the event being written
        self.events += 1

    def open(self):
        '''Start a new eventfile and write its header'''

        self.start = self.clock()
        self.path = os.path.join(self.directory, f'current_{self.start.strftime(STAMP)}.txt')
        self.file = open(self.path, 'w')
        self.events = 0
        header = {'format': FORMAT_VERSION, 'opened': self.start, **self.header}
        self.file.write(json.dumps({'header': json_safe(header)}, allow_nan=False) + '\n')
        self.file.flush()
        logging.info(f'Opened new eventfile {self.path}')

    def close(self):
        '''Close the open eventfile, if any, and rename it for its start and stop times'''

        if self.file is None:
            return
        self.file.close()
        self.file = None
        base = f'{self.start.strftime(STAMP)}__{self.clock().strftime(STAMP)}'
        final = os.path.join(self.directory, f'{base}.txt')
        n = 1
        while os.path.exists(final):  # two files closed within the same second
            final = os.path.join(self.directory, f'{base}_{n}.txt')
            n += 1
        try:
            os.rename(self.path, final)
            logging.info(f'Closed eventfile and moved to {final}.')
        except OSError as e:
            logging.error(f'Closed eventfile {self.path} but could not rename it: {e}')
