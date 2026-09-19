'''Tests for app.core.storage'''

import datetime
import json
import os

import numpy as np
import pytest

from app.core.storage import FORMAT_VERSION, EventWriter, json_safe


class FakeClock:
    '''Returns a time that moves on by `step` seconds each call'''

    def __init__(self, step=1):
        self.t = datetime.datetime(2026, 9, 18, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.step = datetime.timedelta(seconds=step)

    def __call__(self):
        now = self.t
        self.t += self.step
        return now


def read_lines(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


def test_no_file_until_first_event(tmp_path):
    w = EventWriter(str(tmp_path), clock=FakeClock())
    w.close()
    assert os.listdir(tmp_path) == []


def test_header_then_events_flushed_as_written(tmp_path):
    w = EventWriter(str(tmp_path), header={'settings': {'scan_wait': 0.01}}, clock=FakeClock())
    w.write({'pol': 0.5})
    lines = read_lines(w.path)  # readable before close: flushed
    assert lines[0]['header']['format'] == FORMAT_VERSION
    assert lines[0]['header']['settings'] == {'scan_wait': 0.01}
    assert lines[1] == {'pol': 0.5}
    assert os.path.basename(w.path) == 'current_2026-09-18_12-00-00.txt'
    w.close()


def test_close_renames_with_start_and_stop(tmp_path):
    w = EventWriter(str(tmp_path), clock=FakeClock(step=90))
    w.write({'n': 1})
    w.close()
    w.close()  # closing twice is harmless
    assert os.listdir(tmp_path) == ['2026-09-18_12-00-00__2026-09-18_12-01-30.txt']


def test_rotation(tmp_path):
    w = EventWriter(str(tmp_path), max_events=3, clock=FakeClock())
    for n in range(7):
        w.write({'n': n})
    w.close()
    files = sorted(os.listdir(tmp_path))
    assert len(files) == 3 and not any(f.startswith('current_') for f in files)
    counts = [len(read_lines(tmp_path / f)) - 1 for f in files]
    assert counts == [3, 3, 1]
    events = [line['n'] for f in files for line in read_lines(tmp_path / f)[1:]]
    assert events == list(range(7))


def test_same_second_names_do_not_collide(tmp_path):
    clock = FakeClock(step=0)
    for _ in range(2):
        w = EventWriter(str(tmp_path), clock=clock)
        w.write({})
        w.close()
    assert len(os.listdir(tmp_path)) == 2


def test_nan_and_numpy_written_as_standard_json(tmp_path):
    w = EventWriter(str(tmp_path), clock=FakeClock())
    w.write({'pol': np.nan, 'r': np.float64(1.5), 'ok': np.bool_(True), 'n': np.int64(3),
             'pf': np.array([1.0, np.inf]), 'nested': {'x': [np.nan]}})
    w.close()
    (name,) = os.listdir(tmp_path)
    text = open(tmp_path / name).read()
    assert 'NaN' not in text and 'Infinity' not in text
    assert read_lines(tmp_path / name)[1] == {'pol': None, 'r': 1.5, 'ok': True, 'n': 3,
                                              'pf': [1.0, None], 'nested': {'x': [None]}}


def test_json_safe_datetime():
    t = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    assert json_safe({'t': t}) == {'t': str(t)}
