'''PyMEOP discharge power, J.Maxwell

RF power delivered to the discharge cell, from an SR844 lock-in and a PWR-8GHS
power sensor on a dual directional coupler between the Tomco amplifier and the
matching box. The forward coupled wave goes to the sensor and to the lock-in's
reference input, the reflected coupled wave to the lock-in's signal input.

  P_fwd    = P_sensor * K_fwd                    forward path calibration
  M        = (X + iY) / sqrt(P_carrier)          reflected amplitude over forward,
                                                 independent of drive level
  M        = E_d + E_r * Gamma                   one-port error model
  Gamma    = (M / g - E_d) / E_r                 g: lock-in gain on this range
                                                 relative to the calibration's
  P_net    = P_fwd * (1 - |Gamma|^2)
  P_plasma = P_net * (1 - Q_L / Q_U)

P_carrier is the sensor reading divided by 1 + m^2/2, the sidebands of sinusoidal
AM of depth m, since the lock-in sees only the carrier while the sensor reads the
true average. E_d (directivity leakage) is measured with a 50 ohm load, E_r with
an open, both at the end of the cable that normally goes to the matching box,
and checked with a 25 ohm standard.

Nothing here talks to an instrument or to Qt, so it can be tested on its own.
'''

import copy
import datetime
import math
import os
import warnings

import numpy as np
import yaml

Z0 = 50.0

# SR844 SENS and OFLT indices, as the IOC's mbbOut states
SENS_LABELS = ['100 nV', '300 nV', '1 uV', '3 uV', '10 uV', '30 uV', '100 uV', '300 uV',
               '1 mV', '3 mV', '10 mV', '30 mV', '100 mV', '300 mV', '1 V']
SENS_V = [m * 10.0**e for e in range(-7, 0) for m in (1, 3)] + [1.0]
TC_LABELS = ['100 us', '300 us', '1 ms', '3 ms', '10 ms', '30 ms', '100 ms', '300 ms',
             '1 s', '3 s', '10 s', '30 s', '100 s', '300 s', '1 ks', '3 ks']
TC_S = [m * 10.0**e for e in range(-4, 4) for m in (1, 3)]

SENSOR_MIN_DBM = -30.0    # PWR-8GHS specified range
SENSOR_MAX_DBM = 20.0
SENSOR_FLOOR_DBM = -98.0  # the sensor reports -99 dBm below its useable range


def dbm_to_w(dbm):
    return 1e-3 * 10.0**(dbm / 10.0)


def w_to_dbm(w):
    return 10.0 * math.log10(w / 1e-3) if w > 0 else float('-inf')


def am_factor(depth):
    '''Average power over carrier power for sinusoidal AM of depth 0 to 1'''
    return 1.0 + depth * depth / 2.0


def gamma_of_r(r, z0=Z0):
    '''Reflection coefficient of a resistance'''
    return (r - z0) / (r + z0)


def parallel(*rs):
    return 1.0 / sum(1.0 / r for r in rs)


def normalized(x, y, sensor_dbm, am_depth=0.0):
    '''M, the lock-in's reflected phasor over the forward carrier amplitude, V/sqrt(W)'''
    p_carrier = dbm_to_w(sensor_dbm) / am_factor(am_depth)
    return complex(x, y) / math.sqrt(p_carrier)


def pair(z):
    '''A complex number as [re, im], for YAML'''
    return [float(z.real), float(z.imag)]


def unpair(p):
    return complex(p[0], p[1])


def now_str():
    return str(datetime.datetime.now(tz=datetime.timezone.utc))


def mean_sem(values):
    '''Mean and standard error of the mean of real or complex values'''
    a = np.asarray(values)
    mean = a.mean()
    if len(a) < 2:
        return mean, (complex('nan') if np.iscomplexobj(a) else float('nan'))
    if np.iscomplexobj(a):
        sem = complex(a.real.std(ddof=1), a.imag.std(ddof=1)) / math.sqrt(len(a))
    else:
        sem = a.std(ddof=1) / math.sqrt(len(a))
    return mean, sem


class CalStore():
    '''Calibrations, kept in a YAML file so they last between runs.

    The file holds the Gamma calibration in use (E_d, E_r and the conditions it was
    taken in, with any 25 ohm checks and lock-in range gains measured on it), the
    forward calibration K_fwd, the tank Qs, and a history of every calibration
    saved, newest last, so an older one can be looked up after it is replaced.
    '''

    def __init__(self, path):
        self.path = path
        self.data = {'gamma': None, 'forward': None, 'tank': {}, 'history': []}
        self.error = None
        if os.path.exists(path):
            try:
                with open(path) as f:
                    loaded = yaml.safe_load(f) or {}
                self.data.update(loaded)
                self.data['tank'] = self.data.get('tank') or {}
                self.data['history'] = self.data.get('history') or []
            except Exception as e:
                self.error = f"Could not read calibrations from {path}: {e}"

    def save(self):
        '''Write the file whole, through a temporary file, so a crash cannot leave half of one'''
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            yaml.safe_dump(self.data, f, sort_keys=False)
        os.replace(tmp, self.path)

    @property
    def gamma(self):
        return self.data.get('gamma')

    @property
    def forward(self):
        return self.data.get('forward')

    @property
    def tank(self):
        return self.data['tank']

    def _remember(self, kind, entry):
        self.data['history'].append(copy.deepcopy({'kind': kind, **entry}))

    # Gamma calibration ------------------------------------------------------

    def set_gamma(self, load, open_):
        '''Make and save a Gamma calibration from 50 ohm load and open measurements.

        Args:
            load, open_: standard measurements from standard_record()

        Returns:
            the calibration dict

        Raises:
            ValueError: the two were taken on different lock-in ranges or frequencies
        '''
        if load['sens'] != open_['sens']:
            raise ValueError(f"load was measured on {SENS_LABELS[load['sens']]} and open on "
                             f"{SENS_LABELS[open_['sens']]}; measure both on one sensitivity")
        if not math.isclose(load['freq_hz'], open_['freq_hz'], rel_tol=0.005):
            raise ValueError(f"load was measured at {load['freq_hz']/1e6:.4f} MHz and open at "
                             f"{open_['freq_hz']/1e6:.4f} MHz")
        e_d = unpair(load['m'])
        e_r = unpair(open_['m']) - e_d
        if abs(e_r) == 0:
            raise ValueError("open and load read the same, so E_r is zero")
        cal = {
            'time': now_str(),
            'freq_hz': (load['freq_hz'] + open_['freq_hz']) / 2,
            'sens': load['sens'],
            'tc': load['tc'],
            'am_depth': load['am_depth'],
            'e_d': pair(e_d),
            'e_r': pair(e_r),
            'standards': {'load': load, 'open': open_},
            'checks': [],
            'range_gains': {},
        }
        self.data['gamma'] = cal
        self._remember('gamma', {k: v for k, v in cal.items() if k not in ('checks', 'range_gains')})
        self.save()
        return cal

    def add_r25(self, meas, r_ohms):
        '''Record a 25 ohm standard measured on the calibration in use.

        On the calibration's own range it checks the calibration: the Gamma read is
        compared with the one expected from the resistances. On another range it
        measures that range's gain relative to the calibration's, g = M / M_cal, from
        the latest 25 ohm reading on the calibration range, and stores it so Gamma
        on that range is corrected by it.

        Args:
            meas: standard measurement from standard_record()
            r_ohms: the DMM resistances of the terminators in parallel

        Returns:
            dict describing the check, with 'kind' 'check' or 'range_gain'

        Raises:
            ValueError: no calibration, or no 25 ohm reading on its range to compare to
        '''
        cal = self.gamma
        if cal is None:
            raise ValueError("save a Gamma calibration first")
        expected = gamma_of_r(parallel(*r_ohms)) if r_ohms else gamma_of_r(Z0 / 2)
        m = unpair(meas['m'])
        if meas['sens'] == cal['sens']:
            g = (m - unpair(cal['e_d'])) / unpair(cal['e_r'])
            entry = {'kind': 'check', 'time': now_str(), 'sens': meas['sens'], 'm': meas['m'],
                     'r_ohms': list(r_ohms), 'expected': expected, 'gamma': pair(g),
                     'error': abs(g - expected), 'freq_hz': meas['freq_hz']}
        else:
            refs = [c for c in cal['checks'] if c['kind'] == 'check']
            if not refs:
                raise ValueError(f"measure the 25 ohm standard on the calibration's range, "
                                 f"{SENS_LABELS[cal['sens']]}, before another range")
            gain = m / unpair(refs[-1]['m'])
            cal['range_gains'][int(meas['sens'])] = pair(gain)
            entry = {'kind': 'range_gain', 'time': now_str(), 'sens': meas['sens'], 'm': meas['m'],
                     'r_ohms': list(r_ohms), 'gain': pair(gain), 'freq_hz': meas['freq_hz']}
        cal['checks'].append(entry)
        self.save()
        return entry

    # Forward calibration ----------------------------------------------------

    def set_forward(self, k_fwd, freq_hz, **details):
        '''Save K_fwd, the forward power over the sensor reading'''
        if not (k_fwd > 0 and math.isfinite(k_fwd)):
            raise ValueError(f"K_fwd {k_fwd} must be a positive number")
        fwd = {'time': now_str(), 'k_fwd': float(k_fwd), 'k_fwd_db': 10 * math.log10(k_fwd),
               'freq_hz': freq_hz, **details}
        self.data['forward'] = fwd
        self._remember('forward', fwd)
        self.save()
        return fwd

    # Tank -------------------------------------------------------------------

    def set_tank(self, **values):
        '''Save tank parameters: q_u, q_l, im_slope (sign of dIm(Gamma)/df at resonance)'''
        for k, v in values.items():
            self.tank[k] = v
            self.tank[k + '_time'] = now_str()
        self._remember('tank', dict(values))
        self.save()


def standard_record(ms, sensor_dbms, freq_hz, sens, tc, am_depth):
    '''A standard's averaged measurement, as stored'''
    m, sem = mean_sem(ms)
    return {'time': now_str(), 'm': pair(m), 'sem': pair(sem), 'n': len(ms),
            'freq_hz': float(freq_hz), 'sens': int(sens), 'tc': int(tc), 'am_depth': float(am_depth),
            'sensor_dbm': float(np.mean(sensor_dbms))}


class Model():
    '''Turns one lock-in and sensor reading into Gamma and powers, from the calibrations'''

    def __init__(self, store):
        self.store = store

    def gamma(self, m, sens):
        '''Gamma from M, or None with no calibration. Also returns warnings about it.'''
        cal = self.store.gamma
        if cal is None:
            return None, ["no Γ calibration"]
        warnings = []
        gain = 1.0
        if sens != cal['sens']:
            g = cal['range_gains'].get(sens) or cal['range_gains'].get(str(sens))
            if g:
                gain = unpair(g)
            else:
                warnings.append(f"range {SENS_LABELS[sens]} differs from the calibration's "
                                f"{SENS_LABELS[cal['sens']]}, gain not measured")
        return (m / gain - unpair(cal['e_d'])) / unpair(cal['e_r']), warnings

    def powers(self, sensor_dbm, gamma):
        '''Forward, reflected, net and plasma power in W; None where it can't be had'''
        fwd = self.store.forward
        out = {'p_fwd': None, 'p_refl': None, 'p_net': None, 'p_plasma': None}
        if fwd is None:
            return out
        p_fwd = dbm_to_w(sensor_dbm) * fwd['k_fwd']
        out['p_fwd'] = p_fwd
        if gamma is None:
            return out
        g2 = abs(gamma)**2
        out['p_refl'] = p_fwd * g2
        out['p_net'] = p_fwd * (1 - g2)
        q_u, q_l = self.store.tank.get('q_u'), self.store.tank.get('q_l')
        if q_u and q_l and 0 < q_l <= q_u:
            out['p_plasma'] = out['p_net'] * (1 - q_l / q_u)
        return out


# Resonance sweep -------------------------------------------------------------

def lorentzian(f, f0, q, a, c):
    '''Absorbed fraction 1 - |Gamma|^2 of a resonator coupled to the line'''
    return a / (1 + (2 * q * (f - f0) / f0)**2) + c


def fit_resonance(freqs, gammas):
    '''Fit the absorbed fraction 1 - |Gamma|^2 over a frequency sweep.

    Its full width at half maximum is f0 / Q_L. The Gamma locus is a circle; if it
    goes round the origin the tank is overcoupled (beta > 1), otherwise
    undercoupled, so from |Gamma| at resonance beta = (1 - |G0|)/(1 + |G0|), or its
    inverse, and the unloaded Q is Q_L (1 + beta). Only the unloaded Q of an unlit
    tank means anything here; a lit sweep gives the loaded Q.

    Args:
        freqs: Hz
        gammas: complex Gamma at each

    Returns:
        dict of f0, q_l, q_l_err, a, c, gamma0, beta, overcoupled, phase_swing_deg,
        q_u_est, im_slope (sign of dIm(Gamma)/df at f0), fit curve, or raises
        ValueError if it can't be fit
    '''
    from scipy.optimize import curve_fit, OptimizeWarning

    f = np.asarray(freqs, dtype=float)
    g = np.asarray(gammas, dtype=complex)
    if len(f) < 5:
        raise ValueError("need at least 5 points to fit a resonance")
    order = np.argsort(f)
    f, g = f[order], g[order]
    absorbed = 1 - np.abs(g)**2
    i = int(np.argmax(absorbed))
    f0 = f[i]
    c0 = float(np.min(absorbed))
    a0 = float(absorbed[i] - c0)
    half = absorbed > c0 + a0 / 2
    width = max(f[half].max() - f[half].min(), (f[-1] - f[0]) / len(f))
    q0 = f0 / width
    try:
        with warnings.catch_warnings():    # a perfect fit has no covariance; the error is then nan
            warnings.simplefilter('ignore', OptimizeWarning)
            popt, pcov = curve_fit(lorentzian, f, absorbed, p0=[f0, q0, a0, c0], maxfev=10000)
    except Exception as e:
        raise ValueError(f"resonance fit failed: {e}")
    f0, q, a, c = popt
    q = abs(q)
    perr = np.sqrt(np.diag(pcov)) if np.all(np.isfinite(pcov)) else [np.nan] * 4
    if not (f[0] <= f0 <= f[-1]):
        raise ValueError(f"fitted resonance {f0/1e6:.4f} MHz is outside the sweep")

    g0 = complex(np.interp(f0, f, g.real), np.interp(f0, f, g.imag))
    swing = float(np.degrees(np.ptp(np.unwrap(np.angle(g)))))
    overcoupled = swing > 180
    r = min(abs(g0), 0.999999)
    beta = (1 + r) / (1 - r) if overcoupled else (1 - r) / (1 + r)
    j = int(np.argmin(np.abs(f - f0)))
    lo, hi = max(j - 1, 0), min(j + 1, len(f) - 1)
    slope = np.sign((g[hi].imag - g[lo].imag) / (f[hi] - f[lo])) if hi > lo else 0.0
    return {'f0': float(f0), 'q_l': float(q), 'q_l_err': float(perr[1]), 'a': float(a), 'c': float(c),
            'gamma0': g0, 'beta': float(beta), 'overcoupled': overcoupled, 'phase_swing_deg': swing,
            'q_u_est': float(q * (1 + beta)), 'im_slope': float(slope),
            'fit_f': f, 'fit': lorentzian(f, *popt)}
