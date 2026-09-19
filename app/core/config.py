'''PyMEOP J.Maxwell 2021

Load and validate config.yaml, and keep the GUI session file. No Qt here.
'''

import logging
import os
from dataclasses import dataclass, fields

import yaml

X_AXES = ('current', 'wavelength')


class ConfigError(Exception):
    '''Config file missing, unreadable or invalid. The message lists every problem found.'''


@dataclass(frozen=True)
class Settings:
    '''Validated contents of the settings block of config.yaml'''

    probe_ip: str
    meter_ip: str
    siggen_ip: str
    lockin_ip: str
    scan_wait: float  # seconds between running scan current change and read
    temp_scan_wait: float  # seconds between temp change and read
    curr_scan_wait: float  # seconds between curr change and read
    event_dir: str
    log_dir: str
    scan_x_axis: str  # X axis to fit scans against, "current" or "wavelength"
    scan_wave: bool  # read the wavelength meter during running scans?


def load_settings(path='config.yaml'):
    '''Read, validate and return settings, creating the event and log directories.

    Args:
        path: YAML config file with a top level "settings" mapping
    Returns:
        Settings
    Raises:
        ConfigError: listing every problem found, so they can all be fixed at once
    '''

    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except OSError as e:
        raise ConfigError(f'Cannot read config file {path}: {e}') from e
    except yaml.YAMLError as e:
        raise ConfigError(f'Config file {path} is not valid YAML: {e}') from e

    raw = doc.get('settings') if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        raise ConfigError(f'Config file {path} needs a "settings:" mapping at the top level.')

    problems = []
    known = {f.name: f.type for f in fields(Settings)}
    missing = [k for k in known if k not in raw]
    if missing:
        problems.append(f'missing: {", ".join(missing)}')
    unknown = [k for k in raw if k not in known]
    if unknown:
        problems.append(f'not recognised (typo?): {", ".join(unknown)}')

    values = {}
    for name, kind in known.items():
        if name not in raw:
            continue
        value = raw[name]
        if kind is float:  # ints are fine for times, bools are not
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                problems.append(f'{name} must be a number of seconds >= 0, got {value!r}')
                continue
            value = float(value)
        elif kind is bool:
            if not isinstance(value, bool):
                problems.append(f'{name} must be True or False, got {value!r}')
                continue
        elif not isinstance(value, str) or not value.strip():
            problems.append(f'{name} must be a non-empty string, got {value!r}')
            continue
        values[name] = value

    axis = values.get('scan_x_axis')
    if axis is not None and axis not in X_AXES:
        problems.append(f'scan_x_axis must be one of {", ".join(X_AXES)}, got {axis!r}')
    elif axis == 'wavelength' and values.get('scan_wave') is False:
        problems.append('scan_x_axis: wavelength needs scan_wave: True, '
                        'or every scan point has wavelength 0')

    if problems:
        raise ConfigError(f'Problems in config file {path}:\n  - ' + '\n  - '.join(problems))

    settings = Settings(**values)
    for d in (settings.event_dir, settings.log_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            raise ConfigError(f'Cannot create directory {d!r} from {path}: {e}') from e
    return settings


def load_session(path):
    '''Return the saved GUI session as {tab name: {edit name: text}}, or {} if there is none.

    A missing, empty or damaged session file only costs the remembered field values,
    so it is logged rather than raised.
    '''

    try:
        with open(path) as f:
            session = yaml.safe_load(f)
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as e:
        logging.warning(f'Could not read session file {path}, starting blank: {e}')
        return {}
    if session is None:
        return {}
    if not isinstance(session, dict) or not all(isinstance(v, dict) for v in session.values()):
        logging.warning(f'Session file {path} is not in the expected form, starting blank.')
        return {}
    return session


def save_session(path, session):
    '''Write the GUI session, {tab name: {edit name: text}}, for recall on restart'''

    with open(path, 'w') as f:
        yaml.safe_dump(session, f)
