'''Tests for app.core.config'''

import os
from pathlib import Path

import pytest
import yaml

from app.core.config import ConfigError, Settings, load_session, load_settings, save_session

REPO_CONFIG = Path(__file__).parent.parent / 'config.yaml'


def good_settings(tmp_path):
    return {
        'probe_ip': '10.0.0.1', 'meter_ip': '10.0.0.2', 'siggen_ip': '10.0.0.3', 'lockin_ip': '10.0.0.4',
        'scan_wait': 0.01, 'temp_scan_wait': 0.2, 'curr_scan_wait': 1,
        'event_dir': str(tmp_path / 'data'), 'log_dir': str(tmp_path / 'log'),
        'scan_x_axis': 'current', 'scan_wave': False,
    }


def write(tmp_path, settings, name='config.yaml'):
    path = tmp_path / name
    path.write_text(yaml.safe_dump({'settings': settings}))
    return path


def test_repo_config_is_valid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # its relative event and log dirs get created here
    settings = load_settings(REPO_CONFIG)
    assert isinstance(settings, Settings)


def test_loads_and_creates_directories(tmp_path):
    s = load_settings(write(tmp_path, good_settings(tmp_path)))
    assert s.curr_scan_wait == 1.0 and isinstance(s.curr_scan_wait, float)
    assert s.scan_wave is False
    assert os.path.isdir(s.event_dir) and os.path.isdir(s.log_dir)


def test_settings_are_read_only(tmp_path):
    s = load_settings(write(tmp_path, good_settings(tmp_path)))
    with pytest.raises(Exception):
        s.scan_wait = 5


def test_every_problem_reported_at_once(tmp_path):
    bad = good_settings(tmp_path)
    del bad['probe_ip']
    bad['scan_wiat'] = 0.1
    bad['scan_wait'] = -1
    bad['scan_wave'] = 'yes'
    bad['lockin_ip'] = ''
    with pytest.raises(ConfigError) as err:
        load_settings(write(tmp_path, bad))
    msg = str(err.value)
    for expect in ('missing: probe_ip', 'scan_wiat', 'scan_wait', 'scan_wave', 'lockin_ip'):
        assert expect in msg


def test_bool_is_not_a_time(tmp_path):
    bad = good_settings(tmp_path)
    bad['scan_wait'] = True
    with pytest.raises(ConfigError, match='scan_wait'):
        load_settings(write(tmp_path, bad))


@pytest.mark.parametrize('axis, wave, ok', [
    ('current', False, True), ('wavelength', True, True),
    ('wavelength', False, False), ('frequency', True, False),
])
def test_scan_x_axis(tmp_path, axis, wave, ok):
    s = good_settings(tmp_path)
    s['scan_x_axis'], s['scan_wave'] = axis, wave
    path = write(tmp_path, s)
    if ok:
        assert load_settings(path).scan_x_axis == axis
    else:
        with pytest.raises(ConfigError, match='scan_x_axis'):
            load_settings(path)


@pytest.mark.parametrize('text', ['', 'just a string', 'settings: [1, 2]', 'settings: {a: [unclosed'])
def test_malformed_files(tmp_path, text):
    path = tmp_path / 'config.yaml'
    path.write_text(text)
    with pytest.raises(ConfigError):
        load_settings(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match='Cannot read'):
        load_settings(tmp_path / 'nope.yaml')


def test_session_round_trip(tmp_path):
    path = tmp_path / 'session.yaml'
    session = {'run_tab': {'step_edit': '100', 'zero1_edit': '0.1598'}, 'find_tab': {}}
    save_session(path, session)
    assert load_session(path) == session


@pytest.mark.parametrize('text', [None, '', 'not: [valid', '- a list', 'run_tab: 5'])
def test_bad_or_missing_session_gives_blank(tmp_path, text):
    path = tmp_path / 'session.yaml'
    if text is not None:
        path.write_text(text)
    assert load_session(path) == {}
