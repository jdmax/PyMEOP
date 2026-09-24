'''Two peak fits to probe laser scans, shared by the DAQ and the data browser.

Each scan is fit with two peaks on a straight or quadratic baseline. The peaks
are either gaussians or Voigt profiles. At low pressure the lines are Doppler
broadened and a gaussian describes them; at pressures of order 100 mbar the
collisional (Lorentzian) broadening is comparable to the Doppler width, and a
gaussian leaves wings in the residuals and biases the heights.

Parameters are laid out so that the first six mean the same thing in either
shape, which keeps peak heights at indices 2 and 5 everywhere:

    gauss: pos1, sig1, hei1, pos2, sig2, hei2,             baseline...
    voigt: pos1, sig1, hei1, pos2, sig2, hei2, gam1, gam2, baseline...

sig is the gaussian sigma, gam the Lorentzian half width at half maximum, and
hei the height of the peak at its centre, so the height ratio reads the same
in both. The baseline coefficients come last, highest power first, about x_ref.

Nothing here touches Qt, so the data browser can refit scans with it.
'''

import logging
import numpy as np
from scipy import optimize
from scipy.signal import find_peaks, peak_widths
from scipy.special import voigt_profile

PROFILES = {'gauss': 'Gaussian', 'voigt': 'Voigt'}
DEFAULT_PROFILE = 'gauss'
N_GAUSS_PARS = 6  # two gaussians, each a position, a sigma and a height
N_VOIGT_PARS = 8  # the same, then a Lorentzian half width for each peak
BASELINE_DEGREES = (1, 2)  # straight or curved, set by baseline_degree in the config
DEFAULT_BASELINE_DEGREE = 2
MIN_FIT_POINTS = 25  # below this the amplitude ratio is biased by several percent even
                     # when the fit converges and passes every other check
MIN_PEAK_SIGNIFICANCE = 3  # peak amplitude must be this many sigma above its own uncertainty
MIN_PEAK_POWER = 0.1  # peaks must cut this fraction of the residuals left by the bare baseline

# A Voigt start splits the measured width between the two broadenings: 70% of the
# FWHM as the gaussian part and whatever Lorentzian makes up the rest, from the
# Olivero approximation below. Either end converges; this is just a middle start.
VOIGT_START_GAUSS = 0.7
VOIGT_START_LORENTZ = 0.493  # Lorentzian FWHM over total FWHM for that split
FWHM_PER_SIGMA = 2.3548


def n_peak_pars(profile):
    '''How many peak parameters come before the baseline for a line shape'''

    return N_VOIGT_PARS if profile == 'voigt' else N_GAUSS_PARS


def check_profile(profile):
    '''A known line shape name, or the default for anything else'''

    return profile if profile in PROFILES else DEFAULT_PROFILE


def voigt_fwhm(sig, gam):
    '''Voigt FWHM from its gaussian sigma and Lorentzian HWHM, Olivero and
    Longbothum (1977), good to 0.02%'''

    fg = FWHM_PER_SIGMA * sig
    fl = 2 * gam
    return 0.5346 * fl + np.sqrt(0.2166 * fl * fl + fg * fg)


def peak_shape(x, pos, sig, hei, gam=None):
    '''One peak with height hei at pos: a gaussian, or a Voigt when gam is given'''

    if gam is None:
        return hei * np.exp(-np.power((x - pos), 2) / (2 * np.power(sig, 2)))
    return hei * voigt_profile(x - pos, sig, gam) / voigt_profile(0.0, sig, gam)


def parts(x, p, x_ref, profile):
    '''The two peaks and the baseline separately, each about zero.

    The baseline parameters are the polynomial coefficients, highest power
    first, after the peak ones.

    They are referenced to x_ref, the middle of the scan, so the coefficients
    stay nearly independent of each other. Against raw current the x squared,
    x and constant terms are almost collinear and the covariance comes back
    singular.

    Args:
        x: x values to evaluate on
        p: Parameters, peaks first
        x_ref: Baseline reference; 0 for a baseline in raw x
        profile: 'gauss' or 'voigt'
    Returns:
        (g1, g2, base) arrays
    '''

    x = np.asarray(x, dtype=float)
    npk = n_peak_pars(profile)
    gam1, gam2 = (p[6], p[7]) if profile == 'voigt' else (None, None)
    g1 = peak_shape(x, p[0], p[1], p[2], gam1)
    g2 = peak_shape(x, p[3], p[4], p[5], gam2)
    base = np.polyval(p[npk:], x - x_ref)
    return g1, g2, base


def model(x, p, x_ref, profile):
    '''Two peaks on a straight or quadratic baseline: the model that gets fit'''

    g1, g2, base = parts(x, p, x_ref, profile)
    return g1 + g2 + base


def r_squared(y, fit):
    '''Coefficient of determination, as a quick number for fit quality'''

    ss_tot = np.sum((y - y.mean()) ** 2)
    if not ss_tot > 0:
        return np.nan
    return float(1 - np.sum((y - fit) ** 2) / ss_tot)


class ScanFitter():
    '''Fits one scan at a time with a fixed baseline degree and line shape.

    Args:
        base_deg: Baseline polynomial degree, 1 or 2
        profile: 'gauss' or 'voigt'
    '''

    def __init__(self, base_deg=DEFAULT_BASELINE_DEGREE, profile=DEFAULT_PROFILE):
        self.base_deg = base_deg
        self.profile = check_profile(profile)
        self.voigt = self.profile == 'voigt'
        self.n_peak = n_peak_pars(self.profile)
        self.n_pars = self.n_peak + base_deg + 1
        self.x_ref = 0.0

    def fit(self, X, Y, seed=None):
        '''Fit scan data with two peaks on a straight or quadratic baseline.

        Fits from the previous converged fit and from starting parameters estimated
        from this scan's own data, then keeps whichever result comes out better. The
        previous fit is the better start while the peaks drift slowly, but it goes
        stale if they move, so it is never trusted on its own.

        Args:
            X: Scan x axis values, in any order
            Y: Scan signal values
            seed: Parameter list from the last good fit, or None to only estimate
        Returns:
            Dict with x_ref, ok and message, and when a fit converged p0, pf,
            pcov, pstd, ssr and rsq; pf is None when nothing converged
        '''

        X = np.asarray(X, dtype=float)
        Y = np.asarray(Y, dtype=float)
        good = np.isfinite(X) & np.isfinite(Y)
        if good.sum() < MIN_FIT_POINTS or not np.ptp(X[good]) > 0:
            return {'x_ref': self.x_ref, 'pf': None, 'ok': False,
                    'message': 'Too few usable scan points to fit.'}

        order = np.argsort(X[good])
        x, y = X[good][order], Y[good][order]
        span = x[-1] - x[0]
        dx = np.median(np.diff(x))
        if not dx > 0:
            dx = span / len(x)
        self.dx = dx
        self.x_ref = float(0.5 * (x[0] + x[-1]))  # baseline reference, see parts()

        est = self.estimate_peaks(x, y, span, dx)
        starts = [est]
        if self.seed_usable(seed, x, span, dx):
            # take the peaks from the previous fit but always re-estimate the baseline:
            # it moves with laser power, independently of where the lines sit
            starts.insert(0, list(seed[:self.n_peak]) + list(est[self.n_peak:]))

        best = None
        for start in starts:
            trial = self.try_fit(x, y, start, span, dx)
            if trial and (best is None or self.better_fit(trial, best)):
                best = trial
        if best is None:
            return {'x_ref': self.x_ref, 'pf': None, 'ok': False,
                    'message': 'Fit did not converge from any starting point.'}

        best['x_ref'] = self.x_ref
        best['rsq'] = r_squared(y, self.peaks(x, *best['pf']))
        return best

    def try_fit(self, x, y, start, span, dx):
        '''Run one fit from a given starting point and judge the result.

        Args:
            x: Sorted scan x axis values
            y: Scan signal values in the same order
            start: Starting parameters, the baseline coefficients last
            span: Width of the scan range
            dx: Typical step between scan points
        Returns:
            Dict of fit results, or None if the fit did not converge
        '''

        lower, upper = self.fit_bounds(start, x, span, dx)
        p0 = [float(np.clip(v, lo, hi)) for v, lo, hi in zip(start, lower, upper)]
        try:
            pf, pcov = optimize.curve_fit(  # x_scale='jac' since positions, widths and
                self.peaks, x, y, p0=p0, bounds=(lower, upper),  # amplitudes differ by
                x_scale='jac', max_nfev=2000)                    # orders of magnitude
        except (RuntimeError, ValueError) as e:
            logging.info(f'Fit from {p0} did not converge: {e}')
            return None

        pstd = np.sqrt(np.diag(pcov))
        ssr = float(np.sum((y - self.peaks(x, *pf)) ** 2))
        ok, message = self.check_fit(pf, pstd, ssr, x, y, (lower, upper))
        return {'p0': p0, 'pf': pf, 'pcov': pcov, 'pstd': pstd,
                'ssr': ssr, 'ok': ok, 'message': message}

    def better_fit(self, trial, best):
        '''Prefer a fit that passes the sanity checks, then the one with smaller residuals'''

        if trial['ok'] != best['ok']:
            return trial['ok']
        return trial['ssr'] < best['ssr']

    def widths(self, p):
        '''Each peak's width as a gaussian-equivalent sigma, FWHM / 2.3548.

        For gaussians this is sigma itself, so the width checks read the same
        in either shape.
        '''

        if not self.voigt:
            return p[1], p[4]
        return (voigt_fwhm(p[1], p[6]) / FWHM_PER_SIGMA,
                voigt_fwhm(p[4], p[7]) / FWHM_PER_SIGMA)

    def seed_usable(self, seed, x, span, dx):
        '''True if the previous fit's peaks are worth trying as a start for this scan'''

        if seed is None or len(seed) != self.n_pars:  # a fit of another shape or baseline
            return False
        if not np.all(np.isfinite(seed[:self.n_peak])):
            return False
        pos1, sig1, hei1, pos2, sig2, hei2 = seed[:6]
        if not x[0] <= pos1 < pos2 <= x[-1]:  # both peaks must sit inside this scan range
            return False
        w1, w2 = self.widths(seed)
        if not (dx <= w1 <= span and dx <= w2 <= span):
            return False
        if self.voigt and not (seed[6] >= 0 and seed[7] >= 0):
            return False
        return hei1 > 0 and hei2 > 0

    def estimate_peaks(self, x, y, span, dx):
        '''Estimate baseline and two peaks' parameters straight from sorted scan data.

        The peaks are always located against a straight line, whatever degree the
        model itself uses. A quadratic guess that comes out too curved arcs away
        from the data at the ends of the scan and invents a peak there; a line
        cannot.

        Args:
            x: Sorted scan x axis values
            y: Scan signal values in the same order
            span: Width of the scan range
            dx: Typical step between scan points
        Returns:
            List of n_pars starting parameters, the baseline coefficients last
        '''

        xr = x - self.x_ref
        resid = y - np.polyval(self.baseline_guess(x, y, 1), xr)

        amp = resid.max()
        if not amp > 0:  # nothing above baseline, fall back to thirds of the range
            flat = abs(np.ptp(y)) or 1.0
            coef = self.baseline_guess(x, y, self.base_deg)
            return self.peak_start(x[0] + span / 3, span / 10, flat,
                                   x[0] + 2 * span / 3, span / 10, flat) + list(coef)

        idx, props = find_peaks(resid, prominence=0.2 * amp, distance=max(3, len(x) // 20))
        found = []
        if len(idx):
            widths = peak_widths(resid, idx, rel_height=0.5)[0]
            pick = np.argsort(props['prominences'])[::-1][:2]  # the two most prominent peaks
            found = [(x[i], self.width_to_sigma(widths[p], dx, span), resid[i])
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
        if pos2 - pos1 < dx:  # keep the two peaks distinguishable
            pos1, pos2 = pos1 - dx, pos2 + dx

        # now the peaks are placed, fit the baseline to everything that isn't one
        off = (np.abs(x - pos1) > 3 * sig1) & (np.abs(x - pos2) > 3 * sig2)
        coef = self.baseline_guess(x, y, self.base_deg, off)
        base = np.polyval(coef, xr)  # heights measured off that baseline
        hei1 = max(np.interp(pos1, x, y - base), 0.05 * amp)
        hei2 = max(np.interp(pos2, x, y - base), 0.05 * amp)
        return self.peak_start(pos1, sig1, hei1, pos2, sig2, hei2) + list(coef)

    def peak_start(self, pos1, sig1, hei1, pos2, sig2, hei2):
        '''Peak starting parameters from gaussian-equivalent widths.

        A Voigt start shares each measured width between the gaussian and
        Lorentzian parts, keeping the same FWHM.
        '''

        if not self.voigt:
            return [pos1, sig1, hei1, pos2, sig2, hei2]
        g = VOIGT_START_GAUSS
        l = 0.5 * VOIGT_START_LORENTZ * FWHM_PER_SIGMA  # HWHM per unit equivalent sigma
        return [pos1, g * sig1, hei1, pos2, g * sig2, hei2, l * sig1, l * sig2]

    def width_to_sigma(self, width, dx, span):
        '''Convert a peak width in samples to a gaussian sigma in x units'''

        return float(np.clip(width * dx / FWHM_PER_SIGMA, dx, span / 2))

    def fit_bounds(self, p0, x, span, dx):
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
        free = self.base_deg + 1  # the baseline coefficients are left unbounded
        if not self.voigt:
            lower = [x[0] - margin, dx, 0, split, dx, 0]
            upper = [split, span, np.inf, x[-1] + margin, span, np.inf]
        else:
            # either broadening may be the smaller one, so each part alone may be
            # narrower than a scan step; the total width is checked after the fit
            lower = [x[0] - margin, 0.1 * dx, 0, split, 0.1 * dx, 0, 0, 0]
            upper = [split, span, np.inf, x[-1] + margin, span, np.inf, span, span]
        return lower + [-np.inf] * free, upper + [np.inf] * free

    def check_fit(self, pf, pstd, ssr, x, y, bounds):
        '''Sanity check a converged fit before its result is used or seeded forward.

        Args:
            pf: Fitted parameters
            pstd: Standard errors on the fitted parameters
            ssr: Sum of squared residuals of the fit
            x: Sorted scan x axis values the fit was made against
            y: Scan signal values in the same order
            bounds: The (lower, upper) bounds the fit ran under
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
        if self.voigt and (pf[6] >= upper[6] - 0.002 * upper[6] or
                           pf[7] >= upper[7] - 0.002 * upper[7]):
            return False, 'Fit widened a peak into the baseline.'
        w1, w2 = self.widths(pf)
        if self.voigt and (w1 < self.dx or w2 < self.dx):
            return False, 'Fit narrowed a peak below the scan step.'
        if min(hei1, hei2) < 0.005 * max(hei1, hei2):
            return False, 'One peak has no amplitude next to the other.'
        if abs(pos2 - pos1) < 0.5 * (w1 + w2):
            return False, 'Fit collapsed both peaks onto one feature.'
        if not np.all(np.isfinite(pstd[:6])):
            return False, 'Fit uncertainties are undefined.'
        if pstd[2] * MIN_PEAK_SIGNIFICANCE > hei1 or pstd[5] * MIN_PEAK_SIGNIFICANCE > hei2:
            return False, 'Peak amplitudes are not significant above the noise.'
        xr = x - self.x_ref  # the null model is the bare baseline, with no peaks at all
        base_ssr = float(np.sum((y - np.polyval(np.polyfit(xr, y, self.base_deg), xr)) ** 2))
        if not ssr < (1 - MIN_PEAK_POWER) * base_ssr:
            shape = 'curved' if self.base_deg > 1 else 'straight'
            return False, f'Peaks do not stand out from the {shape} baseline.'
        return True, 'Fit good.'

    def parts(self, x, *p):
        '''The two peaks and the baseline separately, about this scan's x_ref'''

        return parts(x, p, self.x_ref, self.profile)

    def peaks(self, x, *p):
        '''The model that gets fit, in the form curve_fit calls it'''

        return model(x, p, self.x_ref, self.profile)

    def baseline_guess(self, x, y, deg, within=None):
        '''Robust polynomial baseline: fit, drop whatever stands above it, refit.

        The cut is at three robust sigma above the curve rather than at a fixed
        quota, so once the peaks are excluded it stops eating real baseline points
        at the ends of the scan, which would otherwise bend the curve.

        Args:
            x: Sorted scan x axis values
            y: Scan signal values in the same order
            deg: Polynomial degree
            within: Optional mask of points allowed to take part
        Returns:
            Polynomial coefficients about x_ref, highest power first
        '''

        xr = x - self.x_ref
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
