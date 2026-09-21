#!/usr/bin/python3
'''Measure the lock-in lag from sweep direction, J.Maxwell 2021

The Run tab alternates sweep direction on every scan. If the acquisition is
lagging behind the lock-in's filter, the fitted peak positions are dragged in
whichever direction the ramp is moving, so forward and reverse scans disagree
by twice the lag. That difference is a direct measurement of the systematic.

Works on any event file: the recorded 'currs' array is in scan order, so the
direction of a historical scan is recoverable even though it was never stored.

Run:  python tools/check_sweep_direction.py [data_dir]
'''
import glob
import json
import os
import sys

import numpy as np


def load_events(path):
    '''Yield (direction, pf) for every event with a usable fit.'''
    for name in sorted(glob.glob(os.path.join(path, '*.txt'))):
        events = []
        for line in open(name):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            currs, pf = d.get('currs'), d.get('pf')
            if not currs or not pf or len(currs) < 3:
                continue

            lo, hi = min(currs), max(currs)
            # A fit that landed on a bound or outside the scan is not a
            # measurement of anything; drop it rather than average it in.
            if not (lo < pf[0] < hi and lo < pf[3] < hi):
                continue
            if pf[2] <= 0 or pf[5] <= 0:
                continue

            # Swept events record the direction; stepped events do not, but
            # their currs array is still in scan order so it can be recovered.
            direction = d.get('direction')
            if direction not in ('up', 'down'):
                direction = 'up' if currs[-1] > currs[0] else 'down'

            events.append((direction, pf, d))
        if events:
            yield name, events


def summarise(events):
    up = np.array([pf for d, pf, _ in events if d == 'up'])
    down = np.array([pf for d, pf, _ in events if d == 'down'])
    if len(up) < 2 or len(down) < 2:
        return None

    out = {'n_up': len(up), 'n_down': len(down)}
    for label, col in (('peak1', 0), ('peak2', 3)):
        u, dn = up[:, col], down[:, col]
        # Reverse scans lag toward lower current, forward toward higher, so
        # the separation is twice the lag.
        out[label] = {
            'up': u.mean(), 'down': dn.mean(),
            'diff': u.mean() - dn.mean(),
            'lag': (u.mean() - dn.mean()) / 2,
            'err': np.hypot(u.std() / np.sqrt(len(u)), dn.std() / np.sqrt(len(dn))),
        }
    ratio_up = (up[:, 2] / up[:, 5]).mean()
    ratio_down = (down[:, 2] / down[:, 5]).mean()
    out['ratio'] = {'up': ratio_up, 'down': ratio_down,
                    'diff_pct': 100 * (ratio_up / ratio_down - 1)}
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'data'

    all_events = []
    print(f"{'file':<44} {'up':>4} {'dn':>4} {'peak1 lag':>11} {'peak2 lag':>11}")
    print("-" * 78)

    for name, events in load_events(path):
        all_events.extend(events)
        s = summarise(events)
        if s is None:
            continue
        print(f"{os.path.basename(name):<44} {s['n_up']:>4} {s['n_down']:>4} "
              f"{s['peak1']['lag']:>8.3f} mA {s['peak2']['lag']:>8.3f} mA")

    print("-" * 78)
    s = summarise(all_events)
    if s is None:
        print("Not enough events in both directions to compare.")
        return

    print(f"\nPooled over {s['n_up']} forward and {s['n_down']} reverse scans\n")
    for label in ('peak1', 'peak2'):
        p = s[label]
        print(f"  {label}:  forward {p['up']:8.3f} mA   reverse {p['down']:8.3f} mA")
        print(f"          separation {p['diff']:+.3f} +/- {p['err']:.3f} mA"
              f"   ->  lag {p['lag']:+.3f} mA")

    print(f"\n  height ratio:  forward {s['ratio']['up']:.4f}   "
          f"reverse {s['ratio']['down']:.4f}   "
          f"({s['ratio']['diff_pct']:+.2f} % apart)")

    lag = abs(s['peak1']['lag'])
    print()
    if lag > 3 * s['peak1']['err'] and lag > 0.1:
        print(f"  Forward and reverse disagree by {abs(s['peak1']['diff']):.3f} mA, "
              f"well beyond the {s['peak1']['err']:.3f} mA scatter.")
        print("  That is the filter group delay dragging the peaks along the ramp.")
    else:
        print("  No significant direction dependence in this data.")


if __name__ == '__main__':
    main()
