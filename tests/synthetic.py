'''Deterministic synthetic probe scans for the fitting tests'''

import numpy as np

TRUE_PARS = [65.0, 1.9, 0.33, 78.4, 1.75, 0.41, 2e-5, 1e-3, 0.02]  # like a real scan in mV


def spectrum(x, p):
    '''Two gaussians on a quadratic baseline referenced to the middle of x'''
    xr = x - 0.5 * (x.min() + x.max())
    g1 = p[2] * np.exp(-(x - p[0]) ** 2 / (2 * p[1] ** 2))
    g2 = p[5] * np.exp(-(x - p[3]) ** 2 / (2 * p[4] ** 2))
    return g1 + g2 + p[6] * xr ** 2 + p[7] * xr + p[8]


def make_case(name):
    '''Return (x, y, seed) for a named case. Every case uses its own fixed RNG seed.'''
    rng = np.random.default_rng(sum(map(ord, name)))  # str hash() varies per process
    x = np.linspace(55, 89.65, 100)
    seed = None
    p = list(TRUE_PARS)
    noise = 0.005

    if name == 'clean':
        noise = 0.0005
    elif name == 'noisy':
        noise = 0.03
    elif name == 'curved_baseline':
        p[6] = 4e-4
    elif name == 'reversed':
        x = x[::-1]
    elif name == 'seeded':
        seed = [65.3, 1.8, 0.3, 78.7, 1.7, 0.4, 0, 0, 0]
    elif name == 'stale_seed':
        seed = [60.0, 1.8, 0.3, 70.0, 1.7, 0.4, 0, 0, 0]
    elif name == 'peak_near_edge':
        p[3] = 88.0
    elif name == 'noise_only':
        p[2] = p[5] = 0.0
    elif name == 'with_nans':
        pass
    elif name == 'too_few_points':
        x = np.linspace(55, 89.65, 20)
    else:
        raise KeyError(name)

    y = spectrum(x, p) + rng.normal(0, noise, len(x))
    if name == 'with_nans':
        y[[5, 40, 77]] = np.nan
    return x, y, seed


CASES = ['clean', 'noisy', 'curved_baseline', 'reversed', 'seeded', 'stale_seed',
         'peak_near_edge', 'noise_only', 'with_nans', 'too_few_points']
