#!/usr/bin/python3
'''Staged bench bring-up for the swept acquisition, J.Maxwell 2021

Exercises the new code paths one instrument at a time, outside the GUI, so a
failure points at a single thing instead of surfacing as a dead sweep inside a
worker thread. Run the stages in order; each one only depends on the ones
before it.

    python tools/check_hardware.py --lockin    # socket, TC/slope, capture buffer
    python tools/check_hardware.py --laser     # wide-scan ramp moves the diode
    python tools/check_hardware.py --sweep     # one coordinated sweep, end to end
    python tools/check_hardware.py --all

Nothing here writes an event file or touches the discharge. The laser and sweep
stages do ramp the diode current over the range in config.yaml, so make sure
that range is safe before running them.
'''
import argparse
import os
import re
import sys
import time
import types

import numpy as np
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.instruments import LockIn, ProbeLaser
from app.sweep import SweepThread, process_sweep, SweepPlan


def load_settings(path='config.yaml'):
    with open(path) as f:
        return yaml.load(f, Loader=yaml.FullLoader)['settings']


def ask_range(prompt):
    """Read a two-number range, however it is reasonably typed.

    Accepts '100-120', '100 120', '100,120', or a single number followed by
    a second prompt. Returns (begin, end), or None if the answer was blank.
    Re-asks rather than raising, since this runs at the bench.
    """
    while True:
        answer = input(prompt).strip()
        if not answer:
            return None
        # The lookbehind keeps '100-120' from reading as 100 and -120,
        # while a genuinely negative first value still parses.
        numbers = re.findall(r'(?<![\d.])-?\d+\.?\d*', answer)
        if len(numbers) >= 2:
            return float(numbers[0]), float(numbers[1])
        if len(numbers) == 1:
            second = input("  ramp end   (mA): ").strip()
            more = re.findall(r'(?<![\d.])-?\d+\.?\d*', second)
            if more:
                return float(numbers[0]), float(more[0])
        print(f"    could not read a range from {answer!r}, "
              f"try something like 100-120")


def check_lockin(settings):
    '''Socket, settings readback, and a short capture into the buffer.'''
    interface = settings.get('lockin_interface', 'vxi11')
    where = (settings['lockin_ip'] if interface == 'vxi11'
             else f"{settings['lockin_ip']}:{settings.get('lockin_port', 23)}")
    print(chr(10) + f"Connecting to lock-in over {interface} at {where}")
    lockin = LockIn(settings)
    if not lockin.connected:
        print("  FAIL: no connection. Check the IP, that nothing else holds "
              "the session, and lockin_interface in the config.")
        return None

    ident = lockin.query('*IDN?')
    print(f"  *IDN? -> {ident}")
    if '860' not in ident:
        print("  WARNING: that does not look like an SR860. The CAPTURE "
              "commands below are SR860-specific.")

    tc = settings.get('lockin_tc', 0.003)
    slope = settings.get('lockin_slope', 12)
    print(f"\nApplying TC={tc} s, slope={slope} dB/oct")
    config = lockin.configure(tc=tc, slope=slope,
                              sync=settings.get('lockin_sync', False))
    for key in sorted(config):
        print(f"  {key:<14} {config[key]}")
    if not config:
        print("  FAIL: could not read settings back.")
        return None
    if abs(config.get('tc', 0) - tc) / tc > 0.01:
        print(f"  WARNING: TC read back as {config.get('tc')}, not {tc}")

    print(f"\n  reference frequency {config.get('ref_freq')} Hz "
          f"(expect ~1000 for the discharge AM)")
    print(f"  group delay {1000 * config.get('group_delay', 0):.1f} ms")

    print("\nConfiguring capture buffer")
    target = settings.get('capture_rate', 1220)
    rate, nch, kb = lockin.capture_config(target, 'XY', seconds=1.0)
    print(f"  rate max      {lockin.capture_rate_max()} Hz")
    print(f"  requested     {target} Hz -> got {rate} Hz ({nch} channels, {kb} kB)")

    print("\nCapturing 1 s")
    target_bytes = int(rate) * nch * 4
    lockin.capture_start(continuous=False, triggered=False)

    # CAPTUREBYTES? is live, but CAPTUREGET? is not: the buffer can only be
    # fetched once the capture has stopped (manual p140).
    deadline = time.time() + 5.0
    captured = 0
    while time.time() < deadline:
        captured = lockin.capture_bytes()
        if captured >= target_bytes:
            break
        time.sleep(0.05)
    lockin.capture_stop()
    print(f"  captured {captured} bytes (wanted {target_bytes})")

    data = lockin.capture_read_all()
    if data is None or not len(data):
        print("  FAIL: buffer read back empty even though CAPTUREBYTES? "
              "reported data. See LockIn.capture_read_all.")
        return None

    x, y = data[:, 0], data[:, 1]
    print(f"  got {len(data)} samples x {data.shape[1]} channels")
    print(f"  X  mean {x.mean():+.6e} V   rms {x.std():.3e}")
    print(f"  Y  mean {y.mean():+.6e} V   rms {y.std():.3e}")

    # The Run tab fits X, so any signal sitting in Y is thrown away. Report
    # the phase here rather than leaving it to be discovered as poor SNR.
    r = float(np.hypot(x.mean(), y.mean()))
    theta = float(np.degrees(np.arctan2(y.mean(), x.mean())))
    in_x = abs(np.cos(np.radians(theta)))
    print(f"  R  mean {r:+.6e} V   theta {theta:+.1f} deg")
    print(f"  fraction of R landing in X: {in_x:.2f}")
    if in_x < 0.95:
        print(f"  NOTE: {100 * (1 - in_x):.0f}% of the signal amplitude is in "
              f"Y. Run auto-phase with the laser on a peak; the height ratio "
              f"survives a constant phase error but the signal to noise does "
              f"not.")

    if not np.all(np.isfinite(data)):
        print("  FAIL: non-finite values -- byte order or block framing is wrong.")
        return None
    if np.abs(x).max() > 10:
        print("  FAIL: implausible magnitudes -- the floats are being "
              "misparsed. Check byte order and channel interleaving.")
        return None

    print("  capture path OK")
    return lockin


def check_laser(settings):
    '''Wide-scan ramp actually moves the diode current.'''
    print(chr(10) + f"Connecting to probe laser at {settings['probe_ip']}")
    probe = ProbeLaser(settings)
    if not probe.connected:
        print("  FAIL: no connection. Check the IP and that nothing else "
              "holds the DLC pro command line.")
        return None

    print(f"  current setpoint {probe.read_current():.3f} mA")
    print(f"  current actual   {probe.read_current_actual():.3f} mA")
    print(f"  grating temp     {probe.read_temp():.3f} C")
    print(f"  wide-scan state  {probe.wide_scan_state()} "
          f"({probe.wide_scan_state_text()})")

    limits = ask_range("  ramp range in mA (e.g. 100-120), blank to skip: ")
    if limits is None:
        print("  skipped")
        return probe
    begin, end = limits
    duration = float(settings.get('sweep_time', 2.0))

    print(chr(10) + f"  ramping {begin} -> {end} mA over {duration} s")
    probe.stop_scan()
    speed = probe.config_wide_scan('current', begin, end, duration)
    print(f"  speed {speed:.3f} mA/s")

    probe.start_scan()
    t0 = time.time()
    seen = []
    # value_act follows the scanned channel itself, rather than the setpoint
    # the host last wrote, so it shows whether the ramp is really running.
    while time.time() - t0 < duration * 1.3:
        value = probe.scan_value()
        if value is not None:
            seen.append((time.time() - t0, value))
        time.sleep(duration / 15)
    state_during = probe.wide_scan_state_text()
    probe.stop_scan()

    if not seen:
        print("  FAIL: could not read the scan value at all.")
        return None

    print("  scan value during the ramp:")
    for t, v in seen[:10]:
        print(f"      +{t:5.2f}s  {v:8.3f}")
    values = [v for _, v in seen]
    covered = max(values) - min(values)
    print(f"  state while running: {state_during}")
    print(f"  covered {covered:.2f} mA of the {abs(end - begin):.2f} mA asked for")

    if covered < 0.2 * abs(end - begin):
        print("  FAIL: the scanned value did not move over the requested "
              "range, so the wide-scan never ran. Check the output-channel "
              "enum (ProbeLaser.CHANNEL_CURRENT) and whether probe_sdk_version "
              "matches the controller firmware.")
        return None
    if covered < 0.9 * abs(end - begin):
        print("  WARNING: the ramp covered less than the full range. It may "
              "still have been accelerating, or the duration was clipped.")

    print("  wide-scan OK")
    return probe


def check_sweep(settings, probe=None, lockin=None):
    '''One full coordinated sweep through the real SweepThread logic.'''
    probe = probe or ProbeLaser(settings)
    lockin = lockin or LockIn(settings)

    begin = float(input("\n  sweep begin (mA): "))
    end = float(input("  sweep end   (mA): "))

    # SweepThread reaches its instruments through parent.parent, so stand in
    # for the GUI with a shim rather than duplicating the sweep logic here.
    shim = types.SimpleNamespace(
        parent=types.SimpleNamespace(probe=probe, lockin=lockin),
        settings=settings)

    thread = SweepThread(shim, begin, end, None, lambda: True, settings,
                         once=True)
    print("\n  running one sweep")
    thread._setup()
    result = thread._one_sweep()

    if result is None:
        print("  FAIL: sweep returned nothing.")
        return

    currs, rs = np.asarray(result['currs']), np.asarray(result['rs'])
    print(f"  direction      {result['direction']}")
    print(f"  raw samples    {result['n_raw']}")
    print(f"  binned points  {len(currs)}")
    print(f"  current span   {currs.min():.2f} -> {currs.max():.2f} mA")
    print(f"  signal range   {rs.min():+.4f} -> {rs.max():+.4f} mV")
    print(f"  median err     {np.median(result['errs']):.4f} mV")
    print(f"  speed          {result['speed']:.2f} mA/s")

    expected = abs(end - begin)
    if abs((currs.max() - currs.min()) - expected) > 0.1 * expected:
        print(f"  WARNING: covered {currs.max() - currs.min():.2f} mA but "
              f"asked for {expected:.2f} mA -- ramp and capture may be "
              f"out of step.")

    out = os.path.join('data', 'bringup_sweep.csv')
    np.savetxt(out, np.column_stack([currs, rs, result['errs']]),
               delimiter=',', header='current_mA,signal_mV,err_mV')
    print(f"\n  wrote {out} -- plot it and confirm both peaks are there.")
    print("  sweep path OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--lockin', action='store_true')
    ap.add_argument('--laser', action='store_true')
    ap.add_argument('--sweep', action='store_true')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--config', default='config.yaml')
    args = ap.parse_args()

    if not any([args.lockin, args.laser, args.sweep, args.all]):
        ap.print_help()
        return

    settings = load_settings(args.config)
    print(f"sweep_mode={settings.get('sweep_mode')} "
          f"sweep_time={settings.get('sweep_time')} s "
          f"capture_rate={settings.get('capture_rate')} Hz")

    # Missing keys fall back to code defaults, which quietly hides the fact
    # that the config being read is not the one in the repository.
    expected = ('sweep_mode', 'sweep_time', 'capture_rate', 'lockin_tc',
                'lockin_slope', 'sweep_bins')
    absent = [k for k in expected if settings.get(k) is None]
    if absent:
        print(f"\n  !! {os.path.abspath(args.config)} is missing: "
              f"{', '.join(absent)}")
        print( "  !! Code defaults are being used instead. This config predates")
        print( "  !! the swept acquisition -- the checks below may pass while")
        print( "  !! the GUI still runs with the wrong settings. Check:")
        print( "  !!     git status --short config.yaml")
        print( "  !!     git log --oneline -1")
        print()

    lockin = probe = None
    if args.lockin or args.all:
        lockin = check_lockin(settings)
        if lockin is None and args.all:
            print("\nStopping: fix the lock-in stage first.")
            return
    if args.laser or args.all:
        probe = check_laser(settings)
        if probe is None and args.all:
            print("\nStopping: fix the wide-scan stage first.")
            return
    if args.sweep or args.all:
        check_sweep(settings, probe, lockin)


if __name__ == '__main__':
    main()
