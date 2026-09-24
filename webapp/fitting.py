"""Exponential fits to polarization against time, for build-up and relaxation times.

The model is

    P(t) = P_inf + (P_0 - P_inf) * exp(-(t - t0) / tau)

with t0 the first point in the fit, so P_0 is the fitted polarization there and
P_inf where it is heading. For any fixed tau the model is linear in P_inf and
P_0, so the fit searches over tau alone and solves the other two exactly at
each step (variable projection). That needs no starting guess and cannot stall
in a local minimum of the linear parameters, which a general least squares
routine started from a poor guess can.

Only numpy is needed, like the rest of the data browser.
"""

import numpy as np

# tau is searched from this fraction of the span of the data to this multiple of
# it. A build-up seen only in its first stretch has a tau well beyond the span;
# the upper bound is where the curve is indistinguishable from a straight line.
TAU_LO, TAU_HI = 1e-3, 1e3
GRID = 400


def _solve(t, y, w, tau, asymptote):
    '''Best P_inf and P_0 for one tau, and the weighted sum of squares.

    Returns (p_inf, p_0, chi2), with p_inf fixed at `asymptote` when that is set.
    '''

    e = np.exp(-t / tau)
    if asymptote is None:
        # y = p_inf * (1 - e) + p_0 * e
        a = np.column_stack((1 - e, e))
        sw = np.sqrt(w)
        coef, *_ = np.linalg.lstsq(a * sw[:, None], y * sw, rcond=None)
        p_inf, p_0 = coef
    else:
        p_inf = asymptote
        # y - p_inf = (p_0 - p_inf) * e
        den = np.sum(w * e * e)
        amp = np.sum(w * e * (y - p_inf)) / den if den > 0 else 0.0
        p_0 = p_inf + amp
    r = y - (p_inf + (p_0 - p_inf) * e)
    return p_inf, p_0, float(np.sum(w * r * r))


def exp_fit(t, y, sigma=None, asymptote=None):
    '''Fit P(t) = P_inf + (P_0 - P_inf) exp(-(t - t0)/tau).

    Args:
        t: Times in seconds, any origin
        y: Values to fit, the polarization in percent from the browser
        sigma: One sigma uncertainty on each y, or None for an unweighted fit.
            Points with a missing or non-positive sigma make the fit unweighted.
        asymptote: Fix P_inf at this value (0 for relaxation to zero), or None
    Returns:
        dict of the fitted parameters with their one sigma uncertainties,
        chi2 and its degrees of freedom, and flags for a poorly determined tau
    Raises:
        ValueError: Too few usable points to fit
    '''

    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    good = np.isfinite(t) & np.isfinite(y)
    weighted = sigma is not None
    if weighted:
        s = np.asarray([np.nan if v is None else v for v in sigma], dtype=float)
        weighted = bool(np.all(np.isfinite(s[good]) & (s[good] > 0)))
    t, y = t[good], y[good]
    s = s[good] if weighted else np.ones_like(y)

    order = np.argsort(t)
    t, y, s = t[order], y[order], s[order]
    n_par = 2 if asymptote is None else 1
    n_par += 1  # tau
    if len(t) < n_par + 1:
        raise ValueError('Need at least %d points to fit, have %d' % (n_par + 1, len(t)))

    t0 = t[0]
    t = t - t0
    span = t[-1]
    if not span > 0:
        raise ValueError('The points in the fit are all at the same time')
    w = 1.0 / (s * s)

    # coarse grid in log tau, then a golden section refinement around the best
    logs = np.linspace(np.log(TAU_LO * span), np.log(TAU_HI * span), GRID)
    costs = np.array([_solve(t, y, w, np.exp(lt), asymptote)[2] for lt in logs])
    k = int(np.argmin(costs))
    lo, hi = logs[max(k - 1, 0)], logs[min(k + 1, GRID - 1)]
    g = (np.sqrt(5) - 1) / 2
    c, d = hi - g * (hi - lo), lo + g * (hi - lo)
    fc = _solve(t, y, w, np.exp(c), asymptote)[2]
    fd = _solve(t, y, w, np.exp(d), asymptote)[2]
    for _ in range(80):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - g * (hi - lo)
            fc = _solve(t, y, w, np.exp(c), asymptote)[2]
        else:
            lo, c, fc = c, d, fd
            d = lo + g * (hi - lo)
            fd = _solve(t, y, w, np.exp(d), asymptote)[2]
    tau = float(np.exp(0.5 * (lo + hi)))
    p_inf, p_0, chi2 = _solve(t, y, w, tau, asymptote)
    at_edge = k in (0, GRID - 1)

    # covariance from the jacobian at the minimum, in (p_inf, p_0, tau) order
    e = np.exp(-t / tau)
    cols = [] if asymptote is not None else [1 - e]
    cols += [e, (p_0 - p_inf) * e * t / (tau * tau)]
    jac = np.column_stack(cols)
    dof = len(t) - jac.shape[1]
    redchi2 = chi2 / dof if dof > 0 else float('nan')
    try:
        cov = np.linalg.inv(jac.T @ (jac * w[:, None]))
        # scale by the scatter actually seen, as scipy's curve_fit does by
        # default: the sigmas of the peak fits say how the points are weighted
        # against each other, not how far they really scatter
        cov = cov * redchi2 if dof > 0 else cov * np.nan
        errs = np.sqrt(np.clip(np.diag(cov), 0, None))
    except np.linalg.LinAlgError:
        errs = np.full(jac.shape[1], np.nan)
    if asymptote is None:
        p_inf_err, p_0_err, tau_err = errs
    else:
        p_inf_err, (p_0_err, tau_err) = 0.0, errs

    return {
        'tau': tau, 'tau_err': _f(tau_err),
        'p_inf': float(p_inf), 'p_inf_err': _f(p_inf_err),
        'p_0': float(p_0), 'p_0_err': _f(p_0_err),
        't0': float(t0), 't1': float(t0 + span),
        'n': int(len(t)), 'chi2': float(chi2), 'dof': int(dof), 'redchi2': _f(redchi2),
        'weighted': weighted, 'asymptote_fixed': asymptote is not None,
        # a tau pinned to the edge of the search, or one far longer than the data,
        # is not measured by this range: the curve there is close to a line
        'tau_unbounded': bool(at_edge or tau > 20 * span),
        'kind': 'build-up' if abs(p_inf) > abs(p_0) else 'relaxation',
    }


def _f(v):
    v = float(v)
    return v if np.isfinite(v) else None
