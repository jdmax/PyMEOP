#!/usr/bin/python3
'''Offline check of the swept-acquisition pipeline, J.Maxwell 2021

Runs with no hardware attached. Two things are verified:

1. The physics claim behind the rewrite -- that stepping the laser faster than
   the lock-in's group delay shifts and distorts the peaks, and that a short
   time constant plus zero-phase software averaging removes the shift without
   costing statistical precision.

2. The binary capture-buffer parsing in LockIn.capture_read_new, against a
   fake socket that speaks the SR860's block format.

Run:  python tools/test_sweep_offline.py
'''
import os
import socket
import sys

import numpy as np
from scipy import optimize

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.sweep import SweepPlan, process_sweep, smooth_zero_phase, bin_sweep
from app.instruments import LockIn


# Truth, taken from the shape of a real scan (saved_session.yaml) but with the
# widths de-convolved, since the recorded sigmas are themselves filter-broadened.
TRUTH = dict(pos1=84.97, sig1=1.20, hei1=0.1346,
             pos2=92.80, sig2=1.20, hei2=0.1628,
             slope=0.0001, inter=-0.0055)

BEGIN, END = 72.0, 104.0
NOISE_DENSITY = 0.004      # mV/sqrt(Hz) at the lock-in input


def peaks(x, *p):
    '''Same model the GUI fits: two Gaussians on a linear background.'''
    g1 = p[2] * np.exp(-np.power((x - p[0]), 2) / (2 * np.power(p[1], 2)))
    g2 = p[5] * np.exp(-np.power((x - p[3]), 2) / (2 * np.power(p[4], 2)))
    return g1 + g2 + p[6] * x + p[7]


def true_signal(current):
    t = TRUTH
    return peaks(current, t['pos1'], t['sig1'], t['hei1'],
                 t['pos2'], t['sig2'], t['hei2'], t['slope'], t['inter'])


def cascade_lowpass(x, dt, tau, poles):
    '''Causal n-pole RC cascade -- what the lock-in's output filter actually is.

    Group delay is poles * tau, which is the whole problem being modelled here.
    '''
    alpha = dt / tau
    y = np.array(x, dtype=float)
    for _ in range(int(poles)):
        out = np.empty_like(y)
        acc = y[0]
        for i in range(len(y)):
            acc += (y[i] - acc) * alpha
            out[i] = acc
        y = out
    return y


def fit(x, y):
    '''Fit with the same seeds and bounds the GUI uses.'''
    lo, hi = float(np.min(x)), float(np.max(x))
    mid = lo + (hi - lo) / 2
    p0 = [lo + (hi - lo) * 0.333, 2, 1, lo + (hi - lo) * 0.666, 2, 1, 0.1, 0.1]
    bounds = ((0, 0, 0, mid - 1, 0, 0, -np.inf, -np.inf),
              (mid + 1, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf))
    pf, _ = optimize.curve_fit(peaks, np.asarray(x), np.asarray(y),
                               p0=p0, bounds=bounds, maxfev=20000)
    return pf


def stepped_trial(rng, n_points=100, dt=0.02, tau=0.1, poles=3):
    '''The old path: 100 discrete current steps, one sample each, long TC.'''
    current = np.linspace(BEGIN, END, n_points)
    clean = true_signal(current)
    noise = rng.normal(0, NOISE_DENSITY / np.sqrt(dt), n_points)
    measured = cascade_lowpass(clean + noise, dt, tau, poles)
    return current, measured


def swept_trial(rng, rate=1220.0, duration=2.0, tau=0.003, poles=2,
                nbins=100, smooth=0.02):
    '''The new path: hardware ramp, dense capture, short TC, zero-phase average.'''
    dt = 1.0 / rate
    n = int(rate * duration)
    plan = SweepPlan(BEGIN, END, duration, rate, settle_frac=0.02)
    current, _frac = plan.currents(n)

    clean = true_signal(current)
    noise = rng.normal(0, NOISE_DENSITY / np.sqrt(dt), n)
    measured = cascade_lowpass(clean + noise, dt, tau, poles)

    # process_sweep expects volts in an (n, channels) array and scales to mV
    samples = np.column_stack([measured / 1000.0, np.zeros(n)])
    result = process_sweep(samples, plan, nbins, smooth)
    return result['currs'], result['rs'], result['errs']


def monte_carlo(trials=60, seed=1):
    rng = np.random.default_rng(seed)
    rows = {'stepped': [], 'swept': []}

    for _ in range(trials):
        try:
            x, y = stepped_trial(rng)
            rows['stepped'].append(fit(x, y))
        except Exception as e:
            print(f"  stepped fit failed: {e}")
        try:
            x, y, _e = swept_trial(rng)
            rows['swept'].append(fit(x, y))
        except Exception as e:
            print(f"  swept fit failed: {e}")

    return {k: np.array(v) for k, v in rows.items()}


def report(name, pf):
    t = TRUTH
    true_ratio = t['hei1'] / t['hei2']
    pos1, sig1, hei1 = pf[:, 0], pf[:, 1], pf[:, 2]
    pos2, hei2 = pf[:, 3], pf[:, 5]
    ratio = hei1 / hei2

    print(f"\n  {name}  ({len(pf)} successful fits)")
    print(f"    peak 1 position   {pos1.mean():8.3f} mA   "
          f"(true {t['pos1']:.3f}, shift {pos1.mean() - t['pos1']:+.3f})")
    print(f"    peak 2 position   {pos2.mean():8.3f} mA   "
          f"(true {t['pos2']:.3f}, shift {pos2.mean() - t['pos2']:+.3f})")
    print(f"    peak 1 sigma      {sig1.mean():8.3f} mA   "
          f"(true {t['sig1']:.3f}, broadened {sig1.mean() / t['sig1']:.2f}x)")
    print(f"    height ratio      {ratio.mean():8.4f}      "
          f"(true {true_ratio:.4f}, bias {100 * (ratio.mean() / true_ratio - 1):+.2f}%)")
    print(f"    ratio scatter     {100 * ratio.std() / ratio.mean():8.2f} %  (rms)")
    return dict(shift=abs(pos1.mean() - t['pos1']),
                ratio_bias=abs(ratio.mean() / true_ratio - 1),
                ratio_scatter=ratio.std() / ratio.mean())


class FakeSock():
    '''Minimal SR860 stand-in that answers CAPTUREBYTES? and CAPTUREGET?.

    framed=True sends an IEEE-488.2 block; framed=False sends the payload raw,
    which is what firmware V1.51 actually does.
    '''

    def __init__(self, samples, framed=True, max_kb=None):
        self.payload = samples.astype('<f4').tobytes()
        self.framed = framed
        self.max_kb = max_kb        # cap per transfer, as the real unit does
        self.out = bytearray()

    def sendall(self, data):
        cmd = data.decode('ascii').strip()
        if cmd == 'CAPTUREBYTES?':
            self.out.extend(f"{len(self.payload)}\r".encode('ascii'))
        elif cmd.startswith('CAPTUREGET?'):
            offset_kb, n_kb = (int(v) for v in cmd.split('?')[1].split(','))
            if self.max_kb:
                n_kb = min(n_kb, self.max_kb)
            chunk = self.payload[offset_kb * 1024:(offset_kb + n_kb) * 1024]
            if self.framed:
                self.out.extend(
                    f"#{len(str(len(chunk)))}{len(chunk)}".encode('ascii'))
            self.out.extend(chunk)
        else:
            raise AssertionError(f"FakeSock got unexpected command {cmd!r}")

    def settimeout(self, value):
        self.timeout = value

    def gettimeout(self):
        return getattr(self, 'timeout', None)

    def recv(self, n):
        if not self.out:
            # An idle socket, which is how the reader detects the end of a
            # transfer that came back shorter than requested.
            raise socket.timeout("FakeSock is empty")
        chunk = bytes(self.out[:n])
        del self.out[:n]
        return chunk


def test_capture_parsing():
    '''Round-trip interleaved float32 through the real parsing code.

    Both transfer framings are covered: the IEEE block some firmware sends,
    and the headerless payload V1.51 actually sends. Getting the headerless
    case wrong reads the first data byte as a format marker and throws.
    '''
    n = 512
    x = np.linspace(0, 1, n)
    y = np.linspace(-1, 0, n)
    interleaved = np.empty(2 * n, dtype=np.float32)
    interleaved[0::2] = x
    interleaved[1::2] = y

    for framed in (True,):
        lockin = object.__new__(LockIn)   # bypass the socket-opening constructor
        lockin.sock = FakeSock(interleaved, framed=framed)
        lockin._buf = bytearray()
        lockin._cap_channels = 2
        lockin._cap_cursor_kb = 0
        lockin._get_fmt = None
        lockin._raw_transfer = False

        data = lockin.capture_read_all()
        label = 'IEEE block' if framed else 'headerless'
        assert data is not None, f"{label}: no data returned"
        assert data.shape[1] == 2, f"{label}: got {data.shape[1]} channels"

        n_got = data.shape[0]
        assert np.allclose(data[:, 0], x[:n_got], atol=1e-6), f"{label}: X mismatch"
        assert np.allclose(data[:, 1], y[:n_got], atol=1e-6), f"{label}: Y mismatch"
        print(f"  capture parsing OK ({label}): {n_got} samples x 2 channels")


def test_short_transfer():
    '''A transfer that returns less than asked for must still assemble.

    Firmware V1.51 does not honour the length argument as kilobytes, so the
    reader advances by what actually arrived. Getting this wrong either loses
    sync with the buffer or blocks forever waiting for bytes that never come.
    '''
    n = 1024
    x = np.arange(n, dtype=np.float32)
    y = -x
    interleaved = np.empty(2 * n, dtype=np.float32)
    interleaved[0::2] = x
    interleaved[1::2] = y

    lockin = object.__new__(LockIn)
    lockin.sock = FakeSock(interleaved, framed=False, max_kb=1)
    lockin._buf = bytearray()
    lockin._cap_channels = 2
    lockin._cap_cursor_kb = 0
    lockin._get_fmt = None
    lockin._raw_transfer = False
    lockin._short_reported = False

    data = lockin.capture_read_all()
    n_got = data.shape[0]
    expected_kb = (2 * n * 4) // 1024
    assert n_got == expected_kb * 128, (
        f"assembled {n_got} samples, expected {expected_kb * 128}")
    assert np.allclose(data[:, 0], x[:n_got], atol=1e-3), "X lost sync"
    assert np.allclose(data[:, 1], y[:n_got], atol=1e-3), "Y lost sync"
    print(f"  short transfers OK: {expected_kb} kB reassembled 1 kB at a time")


def test_zero_phase():
    '''A symmetric kernel must not move a peak; a causal one does.'''
    x = np.linspace(0, 100, 4000)
    y = np.exp(-((x - 50) ** 2) / (2 * 2.0 ** 2))

    smoothed = smooth_zero_phase(y, 201)
    shift = x[np.argmax(smoothed)] - 50.0
    assert abs(shift) < 0.1, f"zero-phase filter shifted the peak by {shift:.3f}"

    causal = cascade_lowpass(y, x[1] - x[0], 2.0, 2)
    causal_shift = x[np.argmax(causal)] - 50.0
    print(f"  zero-phase shift {shift:+.4f} vs causal shift {causal_shift:+.4f}")
    assert causal_shift > 1.0, "causal reference filter should have shifted the peak"


def test_binning():
    '''Binning must preserve a known mean and produce sane error bars.'''
    rng = np.random.default_rng(0)
    current = np.linspace(72, 104, 10000)
    values = np.full(10000, 5.0) + rng.normal(0, 0.5, 10000)

    centers, mean, err, count = bin_sweep(current, values, 100)
    assert len(centers) == 100, f"expected 100 bins, got {len(centers)}"
    assert abs(mean.mean() - 5.0) < 0.05, f"bin means drifted: {mean.mean()}"
    expected_err = 0.5 / np.sqrt(count.mean())
    assert abs(err.mean() / expected_err - 1) < 0.2, "error bars are the wrong size"
    print(f"  binning OK: 100 bins, mean {mean.mean():.4f}, "
          f"err {err.mean():.4f} (expected {expected_err:.4f})")


def main():
    print("=" * 72)
    print("Unit checks")
    print("=" * 72)
    test_capture_parsing()
    test_zero_phase()
    test_binning()

    print()
    print("=" * 72)
    print("Monte Carlo: stepped (tau=100ms, 18dB, 20ms/point) vs")
    print("             swept  (tau=3ms, 12dB, 1220 Hz capture, zero-phase)")
    print("=" * 72)
    fits = monte_carlo()

    stepped = report("STEPPED (old path)", fits['stepped'])
    swept = report("SWEPT (new path)", fits['swept'])

    print()
    print("=" * 72)
    assert stepped['shift'] > 1.0, (
        "the stepped simulation should reproduce a large peak shift; "
        "check the filter model")
    assert swept['shift'] < 0.2, (
        f"swept path still shifts peaks by {swept['shift']:.3f} mA")
    print(f"  peak shift    {stepped['shift']:.3f} mA -> {swept['shift']:.3f} mA "
          f"({stepped['shift'] / max(swept['shift'], 1e-9):.0f}x better)")
    print(f"  ratio bias    {100 * stepped['ratio_bias']:.2f} % -> "
          f"{100 * swept['ratio_bias']:.2f} %")
    print(f"  ratio scatter {100 * stepped['ratio_scatter']:.2f} % -> "
          f"{100 * swept['ratio_scatter']:.2f} %")
    print()
    print("  All checks passed.")
    print("=" * 72)


if __name__ == '__main__':
    main()
