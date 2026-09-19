'''PyMEOP J.Maxwell 2021

Fit probe peak scans with two gaussians on a quadratic baseline. No Qt here.
'''

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy import optimize
from scipy.signal import find_peaks, peak_widths

N_PARS = 9  # two gaussians (position, sigma, height each) on a quadratic baseline
MIN_FIT_POINTS = 25  # below this the amplitude ratio is biased by several percent even
                     # when the fit converges and passes every other check
MIN_PEAK_SIGNIFICANCE = 3  # peak amplitude must be this many sigma above its own uncertainty
MIN_PEAK_POWER = 0.1  # peaks must cut this fraction of the residuals left by the bare baseline


@dataclass
class FitResult:
    '''Outcome of one scan fit.

    Attributes:
        pf: Fitted parameters, [pos1, sig1, hei1, pos2, sig2, hei2, quad, slope, offset];
            zeros when no fit was made
        pstd: Standard errors on pf, empty when no fit was made
        pcov: Covariance of pf, empty when no fit was made
        p0: Starting parameters of the fit kept, empty when no fit was made
        ok: True if the fit converged and passed every sanity check
        message: Why the fit was or wasn't accepted
        rsq: Coefficient of determination, nan when no fit was made
        x_ref: Middle of the scan, the reference the baseline is written about
        fit_curve: Model evaluated at the scan's x values in their original order,
            empty when no fit was made
    '''
    pf: list = field(default_factory=lambda: [0.0] * N_PARS)
    pstd: list = field(default_factory=list)
    pcov: list = field(default_factory=list)
    p0: list = field(default_factory=list)
    ok: bool = False
    message: str = ''
    rsq: float = np.nan
    x_ref: float = 0.0
    fit_curve: list = field(default_factory=list)

    @property
    def converged(self):
        '''True if a fit was made at all, good or not'''
        return len(self.pstd) > 0

    @property
    def peak1(self):
        return self.pf[2] if self.converged else np.nan

    @property
    def peak2(self):
        return self.pf[5] if self.converged else np.nan


def fit_scan(X, Y, seed=None):
    '''Fit scan data with two gaussians on a quadratic baseline.

    Fits from the previous converged fit and from starting parameters estimated
    from this scan's own data, then keeps whichever result comes out better. The
    previous fit is the better start while the peaks drift slowly, but it goes
    stale if they move, so it is never trusted on its own.

    Args:
        X: Scan x axis values, in scan order
        Y: Scan signal values
        seed: Parameter list from the last good fit, or None to only estimate
    Returns:
        FitResult
    '''

    X = np.asarray(X, dtype=float)
    Y = np.asarray(Y, dtype=float)

    good = np.isfinite(X) & np.isfinite(Y)
    if good.sum() < MIN_FIT_POINTS or not np.ptp(X[good]) > 0:
        return no_fit('Too few usable scan points to fit.')

    order = np.argsort(X[good])
    x, y = X[good][order], Y[good][order]
    span = x[-1] - x[0]
    dx = np.median(np.diff(x))
    if not dx > 0:
        dx = span / len(x)
    x_ref = float(0.5 * (x[0] + x[-1]))  # baseline reference, see peaks()

    est = estimate_peaks(x, y, span, dx, x_ref)
    starts = [est]
    if seed_usable(seed, x, span, dx):
        # take the peaks from the previous fit but always re-estimate the baseline:
        # it moves with laser power, independently of where the lines sit
        starts.insert(0, list(seed[:6]) + list(est[6:]))

    best = None
    for start in starts:
        trial = try_fit(x, y, start, span, dx, x_ref)
        if trial and (best is None or better_fit(trial, best)):
            best = trial
    if best is None:
        return no_fit('Fit did not converge from any starting point.', x_ref)

    result = FitResult(pf=best['pf'], pstd=best['pstd'], pcov=best['pcov'], p0=best['p0'],
                       ok=best['ok'], message=best['message'], x_ref=x_ref,
                       fit_curve=peaks(X, x_ref, *best['pf']),
                       rsq=r_squared(y, peaks(x, x_ref, *best['pf'])))
    if not result.ok:
        logging.warning(result.message)
    return result


def no_fit(message, x_ref=0.0):
    '''Placeholder result so a failed fit still plots and writes an event'''

    logging.warning(message)
    return FitResult(message=message, x_ref=x_ref)


def try_fit(x, y, start, span, dx, x_ref):
    '''Run one fit from a given starting point and judge the result.

    Args:
        x: Sorted scan x axis values
        y: Scan signal values in the same order
        start: Nine starting parameters
        span: Width of the scan range
        dx: Typical step between scan points
        x_ref: Baseline reference point
    Returns:
        Dict of fit results, or None if the fit did not converge
    '''

    lower, upper = fit_bounds(start, x, span, dx)
    p0 = [float(np.clip(v, lo, hi)) for v, lo, hi in zip(start, lower, upper)]
    model = lambda xs, *p: peaks(xs, x_ref, *p)
    try:
        pf, pcov = optimize.curve_fit(  # x_scale='jac' since positions, widths and
            model, x, y, p0=p0, bounds=(lower, upper),  # amplitudes differ by
            x_scale='jac', max_nfev=2000)               # orders of magnitude
    except (RuntimeError, ValueError) as e:
        logging.info(f'Fit from {p0} did not converge: {e}')
        return None

    pstd = np.sqrt(np.diag(pcov))
    ssr = float(np.sum((y - model(x, *pf)) ** 2))
    ok, message = check_fit(pf, pstd, ssr, x, y, (lower, upper), x_ref)
    return {'p0': p0, 'pf': pf, 'pcov': pcov, 'pstd': pstd,
            'ssr': ssr, 'ok': ok, 'message': message}


def better_fit(trial, best):
    '''Prefer a fit that passes the sanity checks, then the one with smaller residuals'''

    if trial['ok'] != best['ok']:
        return trial['ok']
    return trial['ssr'] < best['ssr']


def seed_usable(seed, x, span, dx):
    '''True if the previous fit's peaks are worth trying as a start for this scan'''

    if seed is None or len(seed) < 6 or not np.all(np.isfinite(seed[:6])):
        return False
    pos1, sig1, hei1, pos2, sig2, hei2 = seed[:6]
    if not x[0] <= pos1 < pos2 <= x[-1]:  # both peaks must sit inside this scan range
        return False
    if not (dx <= sig1 <= span and dx <= sig2 <= span):
        return False
    return hei1 > 0 and hei2 > 0


def estimate_peaks(x, y, span, dx, x_ref):
    '''Estimate baseline and two gaussian parameters straight from sorted scan data.

    The peaks are located against a straight line, not against the quadratic the
    model will use. A quadratic guess that comes out too curved arcs away from the
    data at the ends of the scan and invents a peak there; a line cannot.

    Args:
        x: Sorted scan x axis values
        y: Scan signal values in the same order
        span: Width of the scan range
        dx: Typical step between scan points
        x_ref: Baseline reference point
    Returns:
        List of nine starting parameters
    '''

    xr = x - x_ref
    resid = y - np.polyval(baseline_guess(x, y, 1, x_ref), xr)

    amp = resid.max()
    if not amp > 0:  # nothing above baseline, fall back to thirds of the range
        flat = abs(np.ptp(y)) or 1.0
        quad, slope, inter = baseline_guess(x, y, 2, x_ref)
        return [x[0] + span / 3, span / 10, flat,
                x[0] + 2 * span / 3, span / 10, flat, quad, slope, inter]

    idx, props = find_peaks(resid, prominence=0.2 * amp, distance=max(3, len(x) // 20))
    found = []
    if len(idx):
        widths = peak_widths(resid, idx, rel_height=0.5)[0]
        pick = np.argsort(props['prominences'])[::-1][:2]  # the two most prominent peaks
        found = [(x[i], width_to_sigma(widths[p], dx, span), resid[i])
                 for p, i in zip(pick, idx[pick])]

    if len(found) == 1:  # look for the second peak away from the one we have
        pos, sig, hei = found[0]
        away = np.abs(x - pos) > 3 * sig
        if away.any():
            j = int(np.argmax(np.where(away, resid, -np.inf)))
            found.append((x[j], sig, max(resid[j], 0.05 * amp)))
        else:
            found.append((pos + 4 * sig, sig, 0.05 * amp))
    elif not found:
        found = [(x[0] + span / 3, span / 10, amp), (x[0] + 2 * span / 3, span / 10, amp)]

    found.sort(key=lambda p: p[0])  # peak 1 is always the lower x peak, polarization sign depends on it
    (pos1, sig1, hei1), (pos2, sig2, hei2) = found
    if pos2 - pos1 < dx:  # keep the two gaussians distinguishable
        pos1, pos2 = pos1 - dx, pos2 + dx

    # now the peaks are placed, fit the quadratic to everything that isn't one
    off = (np.abs(x - pos1) > 3 * sig1) & (np.abs(x - pos2) > 3 * sig2)
    quad, slope, inter = baseline_guess(x, y, 2, x_ref, off)
    base = np.polyval([quad, slope, inter], xr)  # heights measured off that baseline
    hei1 = max(np.interp(pos1, x, y - base), 0.05 * amp)
    hei2 = max(np.interp(pos2, x, y - base), 0.05 * amp)
    return [pos1, sig1, hei1, pos2, sig2, hei2, quad, slope, inter]


def width_to_sigma(width, dx, span):
    '''Convert a peak width in samples to a gaussian sigma in x units'''

    return float(np.clip(width * dx / 2.3548, dx, span / 2))


def fit_bounds(p0, x, span, dx):
    '''Bounds built around the starting guess, so the peaks stay separate and in range.

    Args:
        p0: Starting parameters
        x: Sorted scan x axis values
        span: Width of the scan range
        dx: Typical step between scan points
    Returns:
        Tuple of (lower, upper) bound lists
    '''

    margin = max(0.1 * span, 2 * dx)  # let a clipped peak sit just outside the window
    split = float(np.clip(0.5 * (p0[0] + p0[3]),  # peaks stay either side of their own midpoint
                          x[0] - margin + dx, x[-1] + margin - dx))
    lower = [x[0] - margin, dx, 0, split, dx, 0, -np.inf, -np.inf, -np.inf]
    upper = [split, span, np.inf, x[-1] + margin, span, np.inf, np.inf, np.inf, np.inf]
    return lower, upper


def check_fit(pf, pstd, ssr, x, y, bounds, x_ref):
    '''Sanity check a converged fit before its result is used or seeded forward.

    Args:
        pf: Fitted parameters
        pstd: Standard errors on the fitted parameters
        ssr: Sum of squared residuals of the fit
        x: Sorted scan x axis values the fit was made against
        y: Scan signal values in the same order
        bounds: The (lower, upper) bounds the fit ran under
        x_ref: Baseline reference point
    Returns:
        Tuple of (ok, message)
    '''

    if not np.all(np.isfinite(pf)):
        return False, 'Fit returned non-finite parameters.'
    pos1, sig1, hei1, pos2, sig2, hei2 = pf[:6]
    if hei1 <= 0 or hei2 <= 0:
        return False, 'Fit found a peak with no amplitude.'
    lower, upper = bounds
    edge = 0.002 * (upper[3] - lower[0])  # a parameter sitting on its own bound means
    if pos1 <= lower[0] + edge or pos2 >= upper[3] - edge:  # the fit wanted to go further
        return False, 'Fit pushed a peak off the end of the scan.'
    if sig1 >= upper[1] - 0.002 * upper[1] or sig2 >= upper[4] - 0.002 * upper[4]:
        return False, 'Fit widened a peak into the baseline.'
    if min(hei1, hei2) < 0.005 * max(hei1, hei2):
        return False, 'One peak has no amplitude next to the other.'
    if abs(pos2 - pos1) < 0.5 * (sig1 + sig2):
        return False, 'Fit collapsed both peaks onto one feature.'
    if not np.all(np.isfinite(pstd[:6])):
        return False, 'Fit uncertainties are undefined.'
    if pstd[2] * MIN_PEAK_SIGNIFICANCE > hei1 or pstd[5] * MIN_PEAK_SIGNIFICANCE > hei2:
        return False, 'Peak amplitudes are not significant above the noise.'
    xr = x - x_ref  # the null model is now the bare baseline, with no gaussians at all
    base_ssr = float(np.sum((y - np.polyval(np.polyfit(xr, y, 2), xr)) ** 2))
    if not ssr < (1 - MIN_PEAK_POWER) * base_ssr:
        return False, 'Peaks do not stand out from the curved baseline.'
    return True, 'Fit good.'


def r_squared(y, fit):
    '''Coefficient of determination, as a quick number for fit quality'''

    ss_tot = np.sum((y - y.mean()) ** 2)
    if not ss_tot > 0:
        return np.nan
    return float(1 - np.sum((y - fit) ** 2) / ss_tot)


def peaks(x, x_ref, *p):
    '''Two gaussians on a quadratic baseline.

    The baseline is referenced to x_ref, the middle of the scan, so its three
    coefficients stay nearly independent of each other. Against raw current the
    x squared, x and constant terms are almost collinear and the covariance
    comes back singular.
    '''
    xr = x - x_ref
    g1 = p[2] * np.exp(-np.power((x - p[0]), 2) / (2 * np.power(p[1], 2)))
    g2 = p[5] * np.exp(-np.power((x - p[3]), 2) / (2 * np.power(p[4], 2)))
    base = p[6] * np.power(xr, 2) + p[7] * xr + p[8]
    return g1 + g2 + base


def baseline_guess(x, y, deg, x_ref, within=None):
    '''Robust polynomial baseline: fit, drop whatever stands above it, refit.

    The cut is at three robust sigma above the curve rather than at a fixed
    quota, so once the peaks are excluded it stops eating real baseline points
    at the ends of the scan, which would otherwise bend the curve.

    Args:
        x: Sorted scan x axis values
        y: Scan signal values in the same order
        deg: Polynomial degree
        x_ref: Baseline reference point
        within: Optional mask of points allowed to take part
    Returns:
        Polynomial coefficients about x_ref, highest power first
    '''

    xr = x - x_ref
    ok = np.ones(len(y), dtype=bool) if within is None else np.asarray(within)
    if ok.sum() < deg + 2:
        ok = np.ones(len(y), dtype=bool)
    coef = np.polyfit(xr[ok], y[ok], deg)
    for _ in range(4):
        resid = y - np.polyval(coef, xr)
        med = np.median(resid[ok])
        mad = np.median(np.abs(resid[ok] - med))
        if not mad > 0:
            break
        keep = ok & (resid < med + 3 * 1.4826 * mad)  # peaks are positive going, cut high only
        if keep.sum() < deg + 3:
            break
        coef = np.polyfit(xr[keep], y[keep], deg)
    return coef
