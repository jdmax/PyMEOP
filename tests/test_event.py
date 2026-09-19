'''Tests for app.core.event and app.core.polarization'''

import json
import math

import numpy as np
import pytest

from app.core import fitting
from app.core.event import Event
from app.core.polarization import polarization, ratio_to_pol
from app.core.storage import json_safe
from tests.synthetic import make_case


@pytest.mark.parametrize('M', [-0.86, -0.3, 0.0, 0.05, 0.5, 0.86])
def test_ratio_to_pol_inverts_spin_temperature_ratio(M):
    r0 = 0.8
    r = r0 * (1 + M) / (1 - M)
    assert ratio_to_pol(r, r0) == pytest.approx(M)


@pytest.mark.parametrize('peaks, zeros', [
    ((1.0, 0.0), (1.0, 1.0)),  # no peak 2
    ((1.0, 1.0), (1.0, 0.0)),  # no zero
    ((np.nan, 1.0), (1.0, 1.0)),
    ((-1.0, 1.0), (1.0, 1.0)),  # ratio of -1
])
def test_polarization_undefined_gives_nan(peaks, zeros):
    assert math.isnan(polarization(*peaks, *zeros)[2])


def scan_event(case='clean', **kw):
    x, y, _ = make_case(case)
    return Event(currs=list(x), waves=[0.0] * len(x), rs=list(y), times=list(range(len(x))), **kw)


def test_analyze_matches_fitting_and_polarization():
    e = scan_event(p1_zero=0.33, p2_zero=0.41)
    e.analyze()
    want = fitting.fit_scan(e.x_data(), e.rs)
    np.testing.assert_array_equal(e.fit.pf, want.pf)
    assert e.r == pytest.approx(want.peak1 / want.peak2)
    assert e.r0 == pytest.approx(0.33 / 0.41)
    assert e.pol == pytest.approx(ratio_to_pol(e.r, e.r0))
    assert abs(e.pol) < 0.02  # the synthetic peaks sit at about the zero ratio


def test_zeros_are_used_at_full_precision():
    e = scan_event(p1_zero=0.123456789, p2_zero=0.2)
    e.analyze()
    assert e.r0 == 0.123456789 / 0.2


def test_fail_keeps_scan_but_no_polarization():
    e = scan_event(p1_zero=0.3, p2_zero=0.4)
    e.fail('Fit failed: boom')
    assert not e.fit.ok and e.fit.message == 'Fit failed: boom'
    assert math.isnan(e.pol) and e.r0 == pytest.approx(0.75)


@pytest.mark.parametrize('currs, want', [([1, 2, 3], 'up'), ([3, 2, 1], 'down'), ([1], None), ([], None)])
def test_direction(currs, want):
    assert Event(currs=currs).direction == want


def test_wavelength_axis():
    e = Event(currs=[1, 2], waves=[1083.0, 1083.1], x_axis_name='wavelength')
    np.testing.assert_array_equal(e.x_data(), [1083.0, 1083.1])


def test_record_is_strict_json_and_keeps_old_keys():
    e = scan_event('too_few_points')
    e.analyze()
    rec = e.to_record()
    line = json.dumps(json_safe(rec), allow_nan=False)  # raises on NaN
    back = json.loads(line)
    for key in ('currs', 'waves', 'rs', 'times', 'x_axis', 'fit', 'pf', 'pstd', 'p0', 'fit_good',
                'fit_message', 'rsq', 'peak1', 'peak2', 'r', 'r0', 'pol', 'p1_zero', 'p2_zero',
                'start_time', 'start_stamp', 'stop_time', 'stop_stamp', 'x_ref', 'direction'):
        assert key in back
    assert 'settings' not in back and 'pcov' not in back
    assert back['pol'] is None and back['rsq'] is None
