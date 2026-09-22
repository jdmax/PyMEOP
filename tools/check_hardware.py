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


def check_lockin(settings):
    '''Socket, settings readback, and a short capture into the buffer.'''
    print(f"\nConnecting to lock-in at {settings['lockin_ip']}:"
          f"{settings.get('lockin_port', 23)}")
    lockin = LockIn(settings)
    if lockin.sock is None:
        print("  FAIL: no socket. Check the IP and that nothing else holds "
              "the connection.")
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
    lockin.capture_start(continuous=False, triggered=False)
    chunks, deadline = [], time.time() + 5.0
    while time.time() < deadline:
        new = lockin.capture_read_new()
        if new is not None and len(new):
            chunks.append(new)
            if sum(len(c) for c in chunks) >= rate:
                break
        else:
            time.sleep(0.02)
    lockin.capture_stop()

    if not chunks:
        print("  FAIL: no data came back. If CAPTUREBYTES? answered but "
              "CAPTUREGET? did not, the command syntax or units differ on "
              "your firmware -- see LockIn.capture_read_new.")
        return None

    data = np.concatenate(chunks)
    x, y = data[:, 0], data[:, 1]
    print(f"  got {len(data)} samples x {data.shape[1]} channels")
    print(f"  X  mean {x.mean():+.6e} V   rms {x.std():.3e}")
    print(f"  Y  mean {y.mean():+.6e} V   rms {y.std():.3e}")

    if not np.all(np.isfinite(data)):
        print("  FAIL: non-finite values -- byte order or block framing is wrong.")
        return None
    if np.abs(x).max() > 10:
        print("  FAIL: implausible magnitudes -- the floats are being "
              "misparsed. Suspect telnet IAC escaping; try a raw socket port "
              "in lockin_port.")
        return None

    print("  capture path OK")
    return lockin


def check_laser(settings):
    '''Wide-scan ramp actually moves the diode current.'''
    print(f"\nConnecting to probe laser at {settings['probe_ip']}")
    probe = ProbeLaser(settings)
    if not hasattr(probe, 'tn'):
        print("  FAIL: no connection.")
        return None

    start = probe._number(probe.read_current())
    print(f"  current-set reads {start} mA")

    begin = float(input("  ramp begin (mA), blank to skip: ") or 0) or None
    if begin is None:
        print("  skipped")
        return probe
    end = float(input("  ramp end   (mA): "))
    duration = float(settings.get('sweep_time', 2.0))

    print(f"\n  ramping {begin} -> {end} mA over {duration} s")
    probe.stop_scan()
    speed = probe.config_wide_scan('current', begin, end, duration)
    print(f"  speed {speed:.2f} mA/s")
    print(f"  wide-scan state before start: {probe.wide_scan_state()}")

    probe.start_scan()
    t0 = time.time()
    seen = []
    while time.time() - t0 < duration * 1.3:
        value = probe._number(probe.read_current())
        if value is not None:
            seen.append(value)
        time.sleep(duration / 12)
    probe.stop_scan()

    print(f"  sampled current during ramp: "
          + ", ".join(f"{v:.2f}" for v in seen[:12]))

    if len(seen) < 3 or (max(seen) - min(seen)) < 0.2 * abs(end - begin):
        print("  FAIL: the current did not move over the requested range. "
              "The wide-scan did not start -- check the output-channel enum "
              "(ProbeLaser.CHANNEL_CURRENT) and that (exec '...) is accepted.")
        return None

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
