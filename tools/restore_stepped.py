#!/usr/bin/python3
'''Put the instruments back for the stepped DAQ on main, J.Maxwell 2021

The swept acquisition leaves settings behind that the main branch neither
sets nor expects, because it uses whatever the instruments were left on. The
one that matters is the lock-in time constant: swept mode runs at a few
milliseconds and averages afterwards in software, while the stepped DAQ has
no post-averaging and needs the long one. Run the stepped DAQ on 3 ms and
every point is noise.

    python tools/restore_stepped.py            # show what would change
    python tools/restore_stepped.py --apply    # change it

Leaves the lock-in at lockin_tc_stepped and lockin_slope_stepped from the
config, stops any wide scan, and turns the trigger output back off.
'''
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from app.instruments import LockIn, ProbeLaser


def load_settings(path='config.yaml'):
    with open(path) as f:
        return yaml.load(f, Loader=yaml.FullLoader)['settings']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='actually change the instruments')
    ap.add_argument('--config', default='config.yaml')
    args = ap.parse_args()

    settings = load_settings(args.config)
    tc = float(settings.get('lockin_tc_stepped', 0.1))
    slope = float(settings.get('lockin_slope_stepped', 18))

    print(f"\nTarget for the stepped DAQ: TC {tc} s, slope {slope:.0f} dB/oct")
    if not args.apply:
        print("Showing only. Pass --apply to change anything.\n")

    lockin = LockIn(settings)
    if lockin.connected:
        before = lockin.get_config()
        print(f"\nlock-in now:  TC {before.get('tc')} s, "
              f"slope {before.get('slope_db')} dB/oct, "
              f"sync {before.get('sync')}")
        if args.apply:
            after = lockin.configure(tc=tc, slope=slope)
            print(f"lock-in set:  TC {after.get('tc')} s, "
                  f"slope {after.get('slope_db')} dB/oct")
        else:
            print(f"lock-in would become TC {tc} s, slope {slope:.0f} dB/oct")
    else:
        print("\nNo lock-in connection; its time constant is unchanged. This "
              "is the setting that matters most, so check it on the front "
              "panel before running the stepped DAQ.")

    probe = ProbeLaser(settings)
    if probe.connected:
        state = probe.wide_scan_state()
        trigger = probe.scan_trigger_config()
        print(f"\nlaser now:    wide-scan state {state} "
              f"({probe.wide_scan_state_text()}), trigger {trigger}")
        if args.apply:
            probe.stop_scan()
            probe.set_scan_trigger(False)
            print(f"laser set:    wide-scan stopped "
                  f"({probe.wide_scan_state_text()}), trigger output off")
        else:
            print("laser would have its wide scan stopped and its trigger "
                  "output turned off")
    else:
        print("\nNo laser connection; if a wide scan is running it will fight "
              "the stepped DAQ setting the current point by point.")

    print("\nThe capture buffer settings are left alone: the stepped DAQ never "
          "reads it, so they do no harm.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
