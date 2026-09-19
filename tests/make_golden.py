'''Regenerate tests/data/golden_fits.json from a fit implementation.

The committed golden file was produced by the pre-refactor Event.fit_scan in app/gui.py
(commit a56b86c), so test_fitting checks the extracted fitting module against it.
Only rerun this (python -m tests.make_golden) when a fit change is intended, and say
why in the commit.
'''

import json
from pathlib import Path

import numpy as np

from tests.synthetic import CASES, make_case

DATA = Path(__file__).parent / 'data'


def all_cases():
    '''Yield (name, x, y, seed) for every synthetic and recorded case'''
    for name in CASES:
        x, y, seed = make_case(name)
        yield name, np.asarray(x, float), np.asarray(y, float), seed
    with open(DATA / 'scans.json') as f:
        for name, d in json.load(f).items():
            yield name, np.asarray(d['currs'], float), np.asarray(d['rs'], float), d['seed']


def record(fit_fn):
    '''fit_fn(x, y, seed) -> dict with pf, ok, message, rsq, x_ref, fit_curve'''
    out = {}
    for name, x, y, seed in all_cases():
        r = fit_fn(x, y, seed)
        out[name] = {k: (np.asarray(v, float).tolist() if k in ('pf', 'fit_curve') else v)
                     for k, v in r.items()}
    return out


def fitting_result(x, y, seed):
    '''Adapter from app.core.fitting to the golden record format'''
    from app.core import fitting
    r = fitting.fit_scan(x, y, seed)
    return {'pf': r.pf, 'ok': bool(r.ok), 'message': r.message,
            'rsq': None if np.isnan(r.rsq) else float(r.rsq), 'x_ref': r.x_ref, 'fit_curve': r.fit_curve}


if __name__ == '__main__':  # python -m tests.make_golden, from the repo root
    with open(DATA / 'golden_fits.json', 'w') as f:
        json.dump(record(fitting_result), f, indent=1)
    print(f'Wrote {DATA / "golden_fits.json"}')
