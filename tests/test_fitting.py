'''Tests for app.core.fitting.

The golden comparison pins the extracted module to the results of the pre-refactor
fit code; see tests/make_golden.py.
'''

import json
import math

import numpy as np
import pytest

from app.core import fitting
from tests.make_golden import DATA, all_cases
from tests.synthetic import TRUE_PARS, make_case

with open(DATA / 'golden_fits.json') as f:
    GOLDEN = json.load(f)

CASES = {name: (x, y, seed) for name, x, y, seed in all_cases()}


def test_every_case_has_a_golden_result():
    assert set(CASES) == set(GOLDEN)


@pytest.mark.parametrize('name', sorted(CASES))
def test_matches_golden(name):
    x, y, seed = CASES[name]
    want = GOLDEN[name]
    got = fitting.fit_scan(x, y, seed)

    assert got.ok == want['ok']
    assert got.message == want['message']
    assert got.x_ref == pytest.approx(want['x_ref'], rel=1e-12)
    np.testing.assert_allclose(got.pf, want['pf'], rtol=1e-9, atol=1e-15)
    if want['rsq'] is None:
        assert math.isnan(got.rsq)
    else:
        assert got.rsq == pytest.approx(want['rsq'], rel=1e-9)
    np.testing.assert_allclose(np.asarray(got.fit_curve, float), want['fit_curve'], rtol=1e-9, atol=1e-15)


def test_recovers_true_heights_on_clean_scan():
    x, y, _ = make_case('clean')
    r = fitting.fit_scan(x, y)
    assert r.ok
    assert r.peak1 == pytest.approx(TRUE_PARS[2], rel=0.01)
    assert r.peak2 == pytest.approx(TRUE_PARS[5], rel=0.01)
    assert r.pf[0] < r.pf[3]  # peak 1 is always the lower x peak


def test_scan_direction_does_not_change_result():
    x, y, _ = make_case('clean')
    fwd = fitting.fit_scan(x, y)
    rev = fitting.fit_scan(x[::-1], y[::-1])
    np.testing.assert_allclose(fwd.pf, rev.pf, rtol=1e-6)
    np.testing.assert_allclose(rev.fit_curve, np.asarray(fwd.fit_curve)[::-1], rtol=1e-6)


def test_failed_fit_has_placeholder_results():
    x, y, _ = make_case('too_few_points')
    r = fitting.fit_scan(x, y)
    assert not r.ok and not r.converged
    assert list(r.pf) == [0.0] * fitting.N_PARS
    assert len(r.fit_curve) == 0
    assert math.isnan(r.peak1) and math.isnan(r.peak2)


def test_noise_only_is_rejected():
    x, y, _ = make_case('noise_only')
    assert not fitting.fit_scan(x, y).ok
