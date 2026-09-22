'''PyMEOP swept acquisition, J.Maxwell 2021

Hardware-ramped sweeps: the DLC pro wide-scan module ramps the diode current on
its own clock while the SR860 fills its data capture buffer on its own clock.
Neither is paced by the host, so there is one fixed relationship between sample
index and laser current and no per-point network round trips.

Why this replaces the point-by-point loop
-----------------------------------------
An n-pole lock-in filter has a group delay of n*tau. Stepping the laser faster
than that delay does not produce independent points -- it produces a spectrum
that is shifted by (sweep speed * n * tau) and convolved with a one-sided
exponential tail. At the old settings (tau = 100 ms, 18 dB/oct, ~2 s sweep)
the shift was ~4.8 mA against a peak sigma of ~1.6 mA.

Here the analog filter is set short (a few ms) and the averaging is done
afterwards in software with a symmetric kernel, which has exactly zero group
delay. Statistical precision depends on total sweep time either way; what is
recovered is the systematic shift and the fit conditioning.
'''
import time
import datetime

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal


SIGNAL_SCALE = 1000.0       # lock-in volts -> mV, matching the old stepped path


class SweepPlan():
    '''Everything needed to turn a sample index into a laser current.'''

    def __init__(self, begin, end, duration, rate, settle_frac=0.0):
        self.begin = float(begin)
        self.end = float(end)
        self.duration = float(duration)
        self.rate = float(rate)
        self.settle_frac = float(settle_frac)

    @property
    def speed(self):
        '''Ramp rate in mA/s'''
        return abs(self.end - self.begin) / self.duration

    @property
    def descending(self):
        return self.end < self.begin

    def n_expected(self):
        return int(round(self.rate * self.duration))

    def currents(self, n_samples):
        '''Current at each captured sample.

        Mapped through time rather than through sample count so that
        over-capturing at the end of a ramp cannot rescale the axis.
        '''
        t = np.arange(n_samples) / self.rate
        frac = t / self.duration
        return self.begin + (self.end - self.begin) * frac, frac


def smooth_zero_phase(y, n_window):
    '''Symmetric moving average -- zero group delay by construction.

    A causal filter (which is what the lock-in's analog time constant is)
    shifts features downstream. A symmetric kernel cannot, which is the whole
    point of moving the averaging here.
    '''
    n_window = int(n_window)
    if n_window < 3 or n_window >= len(y):
        return np.asarray(y, dtype=float)
    if n_window % 2 == 0:
        n_window += 1

    kernel = np.hanning(n_window + 2)[1:-1]
    kernel = kernel / kernel.sum()

    pad = n_window // 2
    padded = np.concatenate([y[pad:0:-1], y, y[-2:-pad - 2:-1]])
    if len(padded) != len(y) + 2 * pad:          # short arrays, fall back to edge padding
        padded = np.concatenate([np.full(pad, y[0]), y, np.full(pad, y[-1])])
    return np.convolve(padded, kernel, mode='valid')


def bin_sweep(current, values, nbins):
    '''Bin a dense sweep onto a uniform current axis.

    Returns (centers, mean, err, count). err is the standard error within each
    bin, which is the error bar the old stepped acquisition could never provide.
    '''
    current = np.asarray(current, dtype=float)
    values = np.asarray(values, dtype=float)

    lo, hi = np.min(current), np.max(current)
    edges = np.linspace(lo, hi, int(nbins) + 1)
    idx = np.clip(np.digitize(current, edges) - 1, 0, int(nbins) - 1)

    count = np.bincount(idx, minlength=nbins).astype(float)
    total = np.bincount(idx, weights=values, minlength=nbins)
    sq = np.bincount(idx, weights=values ** 2, minlength=nbins)

    good = count > 0
    mean = np.zeros(nbins)
    err = np.zeros(nbins)
    mean[good] = total[good] / count[good]
    var = np.zeros(nbins)
    var[good] = np.maximum(sq[good] / count[good] - mean[good] ** 2, 0.0)
    err[good] = np.sqrt(var[good] / count[good])

    centers = 0.5 * (edges[:-1] + edges[1:])
    return centers[good], mean[good], err[good], count[good]


def process_sweep(samples, plan, nbins, smooth_seconds):
    '''Turn raw capture samples into a binned spectrum.

    Arguments:
        samples: (n, nch) array from LockIn.capture_read_all, X in column 0
        plan: SweepPlan used for the ramp
    Returns a dict ready to hand to the fitter.
    '''
    x = np.asarray(samples)[:, 0] * SIGNAL_SCALE
    y = (np.asarray(samples)[:, 1] * SIGNAL_SCALE
         if samples.shape[1] > 1 else np.zeros_like(x))

    current, frac = plan.currents(len(x))

    # Keep only samples inside the ramp, dropping the settling transient at the
    # start where the filter is still recovering from the previous ramp.
    keep = (frac >= plan.settle_frac) & (frac <= 1.0)
    if keep.sum() < 10:
        raise ValueError(f"Sweep produced only {keep.sum()} usable samples")

    current, x, y = current[keep], x[keep], y[keep]

    if smooth_seconds and smooth_seconds > 0:
        x = smooth_zero_phase(x, round(smooth_seconds * plan.rate))
        y = smooth_zero_phase(y, round(smooth_seconds * plan.rate))

    r = np.hypot(x, y)

    centers, mean, err, count = bin_sweep(current, x, nbins)
    y_mean = bin_sweep(current, y, nbins)[1]
    r_mean = bin_sweep(current, r, nbins)[1]

    # Ascending current order so the fitter always sees the same orientation
    if plan.descending:
        centers, mean, err, count, y_mean, r_mean = (
            centers[::-1], mean[::-1], err[::-1], count[::-1],
            y_mean[::-1], r_mean[::-1])

    return {
        'currs': centers,
        'rs': mean,
        'errs': err,
        'counts': count,
        'ys': y_mean,
        'r_mag': r_mean,
        'direction': 'down' if plan.descending else 'up',
        'n_raw': int(len(x)),
        'rate': plan.rate,
        'duration': plan.duration,
        'speed': plan.speed,
    }


class SweepThread(QThread):
    '''Repeatedly ramp the laser in hardware and read the capture buffer.

    Emits:
        trace: ramp progress while a sweep runs (no data -- the capture
            buffer is unreadable until the sweep stops)
        sweep: completed binned sweep dict
        error: message string on a failure that stops the run
    '''
    trace = pyqtSignal(object)
    sweep = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, parent, begin, end, temp, is_running, settings,
                 duration=None, once=False):
        QThread.__init__(self)
        self.parent = parent
        self.begin = float(begin)
        self.end = float(end)
        self.temp = temp
        self.is_running = is_running
        self.settings = settings
        self.once = once
        self.duration = float(duration if duration is not None
                              else settings.get('sweep_time', 2.0))
        self.sweeps = 0

    def __del__(self):
        self.wait()

    # -- helpers -----------------------------------------------------------

    @property
    def probe(self):
        return self.parent.parent.probe

    @property
    def lockin(self):
        return self.parent.parent.lockin

    def _limits(self):
        '''Alternate ramp direction so there is no flyback discontinuity.'''
        if self.settings.get('sweep_alternate', True) and self.sweeps % 2:
            return self.end, self.begin
        return self.begin, self.end

    # -- main loop ---------------------------------------------------------

    def run(self):
        try:
            self._setup()
        except Exception as e:
            self.error.emit(f"Sweep setup failed: {e}")
            self.finished.emit()
            return

        while self.is_running():
            try:
                result = self._one_sweep()
            except Exception as e:
                self.error.emit(f"Sweep failed: {e}")
                break

            if result is None:
                break

            self.sweeps += 1
            self.sweep.emit(result)

            if self.once:
                break

        try:
            self.probe.stop_scan()
            self.lockin.capture_stop()
        except Exception:
            pass
        self.finished.emit()

    def _setup(self):
        '''Apply the lock-in settings this acquisition mode depends on.'''
        if self.temp is not None:
            self.probe.set_temp(self.temp)

        self.lockin_config = self.lockin.configure(
            tc=self.settings.get('lockin_tc', 0.003),
            slope=self.settings.get('lockin_slope', 12),
            sync=self.settings.get('lockin_sync', False))

        self.triggered = self.settings.get('sweep_sync', 'software') == 'trigger'
        if self.triggered:
            if not self.probe.set_scan_trigger(True):
                print("Falling back to software sweep sync")
                self.triggered = False

        # Capture geometry is the same for every sweep, so configure it once
        # rather than paying four round trips of dead time between ramps. The
        # buffer is sized past the end of the ramp; the overhang is discarded.
        self.rate, self.nch, _kb = self.lockin.capture_config(
            self.settings.get('capture_rate', 1220), 'XY',
            seconds=self.duration * 1.2)

    def _one_sweep(self):
        '''Run a single ramp and return its processed spectrum.'''
        begin, end = self._limits()

        plan = SweepPlan(begin, end, self.duration, self.rate,
                         self.settings.get('sweep_settle_frac', 0.02))

        self.probe.stop_scan()      # make sure no previous ramp is still armed
        self.probe.config_wide_scan('current', begin, end, self.duration,
                                    continuous=False)

        self.lockin.capture_start(continuous=False, triggered=self.triggered)
        self.probe.start_scan()
        started = time.time()

        n_target = plan.n_expected()
        target_bytes = n_target * self.nch * 4
        timeout = self.duration * 2.0 + 5.0

        # The buffer cannot be read while it is filling: CAPTUREGET? raises a
        # range error until the capture is stopped (SR860 manual p140). So the
        # ramp is watched through CAPTUREBYTES?, which is live, and the data is
        # fetched once at the end. That is why the plot updates per sweep
        # rather than continuously during one.
        while True:
            if not self.is_running():
                self.lockin.capture_stop()
                return None

            captured = self.lockin.capture_bytes()
            if captured >= target_bytes:
                break
            if time.time() - started > timeout:
                self.lockin.capture_stop()
                raise TimeoutError(
                    f"Captured {captured}/{target_bytes} bytes in "
                    f"{timeout:.1f} s. Check that the wide-scan actually "
                    f"started" + (" and that the trigger is wired."
                                  if self.triggered else "."))
            self.emit_progress(captured, target_bytes)
            time.sleep(0.05)

        self.lockin.capture_stop()
        samples = self.lockin.capture_read_all()
        if samples is None or len(samples) < 10:
            raise IOError(f"Capture buffer came back empty after a "
                          f"{self.duration:.1f} s ramp")
        samples = samples[:n_target]

        result = process_sweep(samples, plan,
                               self.settings.get('sweep_bins', 100),
                               self.settings.get('sweep_smooth', 0.02))
        result['stamp'] = datetime.datetime.now()
        result['lockin'] = getattr(self, 'lockin_config', {})
        result['t_start'] = started
        result['begin'] = begin
        result['end'] = end
        return result

    def emit_progress(self, captured, target):
        '''Report how far through the ramp we are.

        The capture buffer is unreadable until the sweep ends, so this carries
        a fraction rather than data. The trace signal still fires once per
        completed sweep.
        '''
        try:
            self.trace.emit({'progress': min(1.0, captured / float(target))})
        except Exception:
            pass        # a dropped progress frame must never kill acquisition


class WavemeterThread(QThread):
    '''Poll the wavelength meter on its own clock during a wide sweep.

    The meter updates at a few Hz, far slower than the capture buffer. Polling
    it inline would pace the whole sweep, so it runs here instead and the
    readings are interpolated onto the dense current axis afterwards.
    '''
    finished = pyqtSignal()

    def __init__(self, meter, is_running, interval=0.2, channel=1):
        QThread.__init__(self)
        self.meter = meter
        self.is_running = is_running
        self.interval = interval
        self.channel = channel
        self.stamps = []
        self.waves = []

    def __del__(self):
        self.wait()

    def run(self):
        try:
            self.meter.start_cont()
        except Exception as e:
            print(f"Wavemeter start failed: {e}")

        while self.is_running():
            try:
                wave = self.meter.read_wavelength(self.channel)
                self.stamps.append(time.time())
                self.waves.append(wave)
            except Exception as e:
                print(f"Wavemeter read failed: {e}")
            time.sleep(self.interval)

        try:
            self.meter.stop_cont()
        except Exception:
            pass
        self.finished.emit()

    def interpolate(self, t_start, duration, currents, plan_currents):
        '''Map logged wavelengths onto a sweep's current axis.

        Returns an array the same length as currents, or zeros if too few
        wavelength samples were collected to be meaningful.
        '''
        if len(self.waves) < 2:
            return np.zeros(len(currents))

        stamps = np.array(self.stamps)
        waves = np.array(self.waves)

        inside = (stamps >= t_start) & (stamps <= t_start + duration)
        if inside.sum() < 2:
            return np.zeros(len(currents))

        # Time of each logged wavelength -> the current the ramp was at then
        frac = (stamps[inside] - t_start) / duration
        curr_at_wave = plan_currents[0] + (plan_currents[-1] - plan_currents[0]) * frac

        order = np.argsort(curr_at_wave)
        return np.interp(currents, curr_at_wave[order], waves[inside][order])
