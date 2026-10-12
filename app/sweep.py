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

    def __init__(self, begin, end, duration, rate, settle_frac=0.0,
                 t_offset=0.0):
        self.begin = float(begin)
        self.end = float(end)
        self.duration = float(duration)
        self.rate = float(rate)
        self.settle_frac = float(settle_frac)
        # Seconds between the first captured sample and the ramp starting to
        # move. Starting the scan is a separate round trip and the controller
        # takes its own time to begin; measured here rather than assumed zero.
        self.t_offset = float(t_offset)

    @property
    def speed(self):
        '''Ramp rate in mA/s'''
        return abs(self.end - self.begin) / self.duration

    @property
    def descending(self):
        return self.end < self.begin

    def n_expected(self):
        '''Samples needed to cover the ramp, including the wait before it.'''
        return int(round(self.rate * (self.duration + self.t_offset)))

    def currents(self, n_samples):
        '''Current at each captured sample.

        Mapped through time rather than through sample count so that
        over-capturing at the end of a ramp cannot rescale the axis. Samples
        taken before the ramp moved come out with frac below zero and are
        dropped by the caller.
        '''
        t = np.arange(n_samples) / self.rate - self.t_offset
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

    # Keep only samples inside the ramp, trimmed at both ends. The start drops
    # the transient left by the previous ramp. The end matters just as much:
    # the measured start time carries tens of milliseconds of uncertainty, so
    # taking the ramp to run exactly to frac 1.0 sweeps up samples from after
    # it stopped. Those land in the last bin or two and arrive as a jump, not
    # as noise -- a bin whose internal scatter is twenty times its neighbours'
    # and whose value is wrong enough to drag the fit with it.
    keep = (frac >= plan.settle_frac) & (frac <= 1.0 - plan.settle_frac)
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
        try:
            self.wait()
        except RuntimeError:
            pass        # never constructed as a thread, nothing to wait on

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
            self.lockin.capture_stop(wait=False)
        except Exception:
            pass
        self.finished.emit()

    def _setup(self):
        '''Apply the lock-in settings this acquisition mode depends on.'''
        self._settle_temperature()

        self.lockin_config = self.lockin.configure(
            tc=self.settings.get('lockin_tc', 0.003),
            slope=self.settings.get('lockin_slope', 12),
            sync=self.settings.get('lockin_sync', False))

        self.triggered = self.settings.get('sweep_sync', 'software') == 'trigger'
        if self.triggered:
            channel = self.settings.get('trigger_channel')
            if not self.probe.set_scan_trigger(True, channel=channel):
                print("Falling back to software sweep sync")
                self.triggered = False
            elif self.settings.get('sweep_alternate', True):
                print("sweep_alternate is on with a hardware trigger. The "
                      "output asserts as the scan crosses the threshold, so "
                      "whether it does that on the edge the SR860 wants may "
                      "depend on the ramp direction. If only every other "
                      "sweep triggers, either set sweep_alternate to False or "
                      "invert the digital output.")

        # Capture geometry is the same for every sweep, so configure it once
        # rather than paying four round trips of dead time between ramps. The
        # buffer is sized past the end of the ramp; the overhang is discarded.
        # Sized for the ramp plus the wait before it starts, which is up to a
        # few hundred ms; the overhang is discarded when the samples are mapped.
        self.rate, self.nch, _kb = self.lockin.capture_config(
            self.settings.get('capture_rate', 1220), 'XY',
            seconds=self.duration * 1.2 + 1.0)

    def _settle_temperature(self):
        """Set the diode temperature and wait for the controller to settle.

        A DFB tunes on current and temperature together, so sweeping while the
        temperature is still moving puts the lines at a different current on
        every pass. That reads as peaks wandering through the scan and as
        noise when sweeps are compared, rather than as a temperature problem.
        """
        if self.temp is None:
            return

        try:
            now = self.probe.read_temp()
        except Exception:
            now = None

        if now is not None and abs(now - self.temp) > 0.01:
            print(f"Probe temperature {now:.3f} -> {self.temp:.3f} C. A DFB "
                  f"retunes in wavelength as it settles, so the peaks will "
                  f"move until it does.")
            self.probe.set_temp(self.temp)

        if not self.settings.get('temp_wait', True):
            return

        def report(actual, setpoint, waited):
            self.trace.emit({'message': f"waiting for {setpoint:.3f} C, "
                                        f"at {actual:.3f} C after {waited:.0f} s"})

        timeout = float(self.settings.get('temp_timeout', 300.0))
        if not self.probe.wait_for_temp(timeout, report):
            print(f"Temperature still not settled after {timeout:.0f} s "
                  f"({self.probe.temp_status()}). Sweeping anyway, but the "
                  f"peak positions will drift.")

    def _follow_ramp(self, t_capture, plan):
        """Watch the scanned value for the whole ramp, returning (time, fraction).

        Reading the value throughout rather than only at the start matters for
        two reasons. The fit then spans the whole ramp instead of its first
        third, so the rate is pinned by the data rather than extrapolated
        across the remaining two thirds. And the loop ends when the ramp does,
        so the capture is stopped within one poll of the real end instead of
        after a sample count computed from a model that might be wrong.

        The reads go to the laser, not the lock-in, so they cost the capture
        nothing.
        """
        span = plan.end - plan.begin
        track = []
        deadline = time.time() + plan.duration * 2.0 + 5.0

        while time.time() < deadline:
            if not self.is_running():
                return track, False
            value = self.probe.scan_value()
            now = time.time() - t_capture
            if value is None:
                return track, False         # cannot follow it; fall back
            fraction = (value - plan.begin) / span
            track.append((now, fraction))
            if fraction >= 0.995:           # the ramp has arrived
                return track, True
            self.emit_progress(max(0.0, fraction), 1.0)
        return track, False

    def _fit_ramp(self, track, plan):
        """Start time and duration of the ramp, from the whole of it.

        Returns (t_offset, duration), or (None, None) if the track is too
        thin to fit. The ends are left out: the controller takes a moment to
        reach speed, and the last stretch can be rounding onto the final
        value, so neither belongs in a straight line fit.
        """
        inner = [(t, f) for t, f in track if 0.05 <= f <= 0.95]
        if len(inner) < 8:
            return None, None

        times = np.array([t for t, _ in inner])
        fracs = np.array([f for _, f in inner])
        slope, intercept = np.polyfit(times, fracs, 1)
        if slope <= 0:
            return None, None

        duration = 1.0 / slope
        if abs(duration - plan.duration) > 0.2 * plan.duration:
            print(f"Ramp measured at {duration:.2f} s against the "
                  f"{plan.duration:.2f} s asked for, which is too far out to "
                  f"trust; using the requested time.")
            return None, None
        return max(0.0, -intercept / slope), duration

    def _one_sweep(self):
        '''Run a single ramp and return its processed spectrum.'''
        begin, end = self._limits()

        self.probe.config_wide_scan('current', begin, end, self.duration,
                                    continuous=False)

        if self.triggered:
            # The capture starts as the scan crosses the threshold, so sample
            # zero sits at a known current rather than at a guessed time. That
            # pins the axis against a late start and a wrong ramp rate alike.
            # Placed just inside the start so almost none of the ramp is lost.
            fraction = float(self.settings.get('trigger_threshold_frac', 0.02))
            threshold = begin + (end - begin) * fraction
            self.probe.set_scan_trigger(True, threshold=threshold)
            plan = SweepPlan(threshold, end,
                             self.duration * (1.0 - fraction), self.rate, 0.0)
        else:
            plan = SweepPlan(begin, end, self.duration, self.rate,
                             self.settings.get('sweep_settle_frac', 0.02))

        self.lockin.capture_start(continuous=False, triggered=self.triggered)
        t_capture = time.time()
        self.probe.start_scan()
        started = t_capture

        if self.triggered:
            arrived = self._wait_for_capture(plan, started)
            if arrived is None:
                return None
        else:
            track, finished = self._follow_ramp(t_capture, plan)
            if not self.is_running():
                self.lockin.capture_stop(wait=False)   # buffer is discarded
                return None
            if not finished:
                print(f"The ramp did not reach its end within "
                      f"{plan.duration * 2.0 + 5.0:.0f} s; using what was "
                      f"captured.")
            offset, duration = self._fit_ramp(track, plan)
            if offset is not None:
                plan.t_offset = offset
                plan.duration = duration

        # The lock-in's own output filter is causal, so a feature reaches the
        # capture that much after the laser passed it. It is small here, 6 ms
        # against a 2 s ramp, but it flips sign with the ramp direction and so
        # shows up as an alternating offset.
        plan.t_offset += float(self.lockin_config.get('group_delay', 0.0) or 0.0)

        # Waits for CAPTURESTAT? bit 0 to clear -- CAPTURESTOP only halts at
        # the next 2 kB boundary and CAPTUREGET? is refused until then. The
        # buffer cannot be read while it fills at all (SR860 manual p140),
        # which is why there is no live trace during a ramp.
        self.lockin.capture_stop()
        samples = self.lockin.capture_read_all()
        if samples is None or len(samples) < 10:
            raise IOError(f"Capture buffer came back empty after a "
                          f"{self.duration:.1f} s ramp")

        result = process_sweep(samples, plan,
                               self.settings.get('sweep_bins', 100),
                               self.settings.get('sweep_smooth', 0.02))
        result['stamp'] = datetime.datetime.now()
        result['lockin'] = getattr(self, 'lockin_config', {})
        result['t_start'] = started
        result['begin'] = begin
        result['end'] = end
        result['ramp_delay'] = plan.t_offset
        result['ramp_duration'] = plan.duration
        try:
            result['temp_act'] = self.probe.read_temp_actual()
            result['temp_set'] = self.probe.read_temp()
            result['temp_ready'] = self.probe.temp_ready()
        except Exception:
            pass        # a temperature read must never lose a good sweep
        return result

    def _wait_for_capture(self, plan, started):
        """Wait for a triggered capture to fill. Returns None if abandoned.

        Only the triggered path needs this: there the capture start is tied to
        the ramp by hardware, so the sample count is the thing to wait on.
        """
        target_bytes = plan.n_expected() * self.nch * 4
        timeout = self.duration * 2.0 + 5.0
        while True:
            if not self.is_running():
                self.lockin.capture_stop(wait=False)
                return None
            captured = self.lockin.capture_bytes()
            if captured >= target_bytes:
                return captured
            if time.time() - started > timeout:
                self.lockin.capture_stop(wait=False)
                if captured == 0:
                    raise TimeoutError(
                        f"The capture never triggered in {timeout:.1f} s. The "
                        f"SR860 waits on a falling edge at its rear Trig In; "
                        f"check the cable, the trigger output channel, and "
                        f"whether the edge needs inverting. "
                        f"sweep_sync: 'software' works without it.")
                raise TimeoutError(
                    f"Captured {captured}/{target_bytes} bytes in "
                    f"{timeout:.1f} s with the trigger armed.")
            self.emit_progress(captured, target_bytes)
            time.sleep(0.05)


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
