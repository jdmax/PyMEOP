'''PyMEOP J.Maxwell 2021

One probe scan and its analysis. No Qt here.
'''

import datetime
from dataclasses import dataclass, field

import numpy as np

from app.core import fitting
from app.core.polarization import polarization


def utc_now():
    return datetime.datetime.now(tz=datetime.timezone.utc)


@dataclass
class Event:
    '''A single probe scan, its fit and the polarization worked out from it.

    Attributes:
        start_time: When the scan started accumulating, UTC
        stop_time: When the last point arrived, UTC
        currs: Probe laser current setpoint of each point (mA)
        waves: Wavelength of each point (nm), zero when not read
        rs: Lock-in signal of each point (mV)
        times: Timestamp of each point
        x_axis_name: "current" or "wavelength", what the scan is fitted against
        p1_zero, p2_zero: Peak amplitudes at zero polarization, used for r0
        fit: FitResult, None until analyze() or fail() has run
        r: Peak 1 over peak 2 amplitude ratio
        r0: The same ratio at zero polarization
        pol: Polarization as a fraction
    '''

    start_time: datetime.datetime = field(default_factory=utc_now)
    stop_time: datetime.datetime = None
    currs: list = field(default_factory=list)
    waves: list = field(default_factory=list)
    rs: list = field(default_factory=list)
    times: list = field(default_factory=list)
    x_axis_name: str = 'current'
    p1_zero: float = np.nan
    p2_zero: float = np.nan
    fit: fitting.FitResult = None
    r: float = np.nan
    r0: float = np.nan
    pol: float = np.nan

    @property
    def direction(self):
        '''"up" or "down" for the direction the current was stepped, None if unknown'''
        if len(self.currs) < 2 or self.currs[0] == self.currs[-1]:
            return None
        return 'up' if self.currs[-1] > self.currs[0] else 'down'

    def x_data(self):
        '''The x axis to fit and plot against, per x_axis_name'''
        return np.array(self.waves if self.x_axis_name == 'wavelength' else self.currs, dtype=float)

    def analyze(self, seed=None):
        '''Fit the scan and work out polarization from the peak amplitudes.

        Args:
            seed: Parameter list from the last good fit, or None to only estimate
        '''
        self.fit = fitting.fit_scan(self.x_data(), self.rs, seed)
        self.r, self.r0, self.pol = polarization(self.fit.peak1, self.fit.peak2,
                                                 self.p1_zero, self.p2_zero)

    def fail(self, message):
        '''Record that the scan could not be analyzed, so it still plots and is written out'''
        self.fit = fitting.no_fit(message)
        self.r, self.r0, self.pol = polarization(np.nan, np.nan, self.p1_zero, self.p2_zero)

    def to_record(self):
        '''Dict for one eventfile line.

        Keys match the eventfile lines written before the refactor, less the settings
        (now in the file header) and pcov, plus direction.
        '''
        fit = self.fit or fitting.FitResult()
        return {
            'start_time': str(self.start_time),
            'start_stamp': self.start_time.timestamp(),
            'stop_time': str(self.stop_time) if self.stop_time else None,
            'stop_stamp': self.stop_time.timestamp() if self.stop_time else None,
            'direction': self.direction,
            'currs': self.currs,
            'waves': self.waves,
            'rs': self.rs,
            'times': self.times,
            'x_axis': self.x_data(),
            'x_ref': fit.x_ref,
            'p1_zero': self.p1_zero,
            'p2_zero': self.p2_zero,
            'fit_good': fit.ok,
            'fit_message': fit.message,
            'p0': fit.p0,
            'pf': fit.pf,
            'pstd': fit.pstd,
            'fit': fit.fit_curve,
            'rsq': fit.rsq,
            'peak1': fit.peak1,
            'peak2': fit.peak2,
            'r': self.r,
            'r0': self.r0,
            'pol': self.pol,
        }
