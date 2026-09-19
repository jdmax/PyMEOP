'''PyMEOP J.Maxwell 2021

Nuclear polarization from probe peak amplitudes. No Qt here.

The two probe lines address 2^3S sublevels that are not pumped, so their populations
follow the ground state spin temperature. Their amplitude ratio r, over the same ratio
r0 measured with the gas unpolarized, is (1 + M) / (1 - M), which inverts to
M = (r/r0 - 1) / (r/r0 + 1). The sign of M depends on which line is peak 1 (always
the lower x one, see fitting.estimate_peaks) and on the pumping line in use.
'''

import numpy as np


def peak_ratio(a, b):
    '''a / b, or nan when b is zero'''
    return a / b if b else np.nan


def ratio_to_pol(r, r0):
    '''Polarization, as a fraction, from peak ratio r and zero-polarization ratio r0'''

    ratio = r / r0 if r0 else np.nan
    return (ratio - 1) / (ratio + 1) if np.isfinite(ratio) and ratio != -1 else np.nan


def polarization(peak1, peak2, zero1, zero2):
    '''Peak ratio, zero ratio and polarization from fitted and zero amplitudes.

    Args:
        peak1, peak2: Fitted amplitudes of the lower and higher x probe peaks
        zero1, zero2: The same amplitudes measured at zero polarization
    Returns:
        Tuple of (r, r0, pol), each nan if it cannot be formed
    '''

    r = peak_ratio(peak1, peak2)
    r0 = peak_ratio(zero1, zero2)
    return r, r0, ratio_to_pol(r, r0)
