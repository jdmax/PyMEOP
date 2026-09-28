#!/usr/bin/python3
'''Find the DLC pro wide-scan output channel numbers, J.Maxwell 2021

laser1:wide-scan:output-channel is a bare integer in the DeCoF tree, with no
enum in the SDK, and the values inherited from the old code (56 for
temperature, 63 for current) are rejected by this controller. Rather than
guess again, this asks the instrument: it sets each candidate number and,
where the controller accepts it, reads back value-unit and value-act, which
name and value the selected channel.

Nothing is scanned. Only the configuration parameter is written, and the
original value is put back before exiting.

    python tools/probe_wide_scan.py
    python tools/probe_wide_scan.py --ip 129.57.36.81 --max 90
'''
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from app.instruments import ProbeLaser


def load_settings(path='config.yaml'):
    with open(path) as f:
        return yaml.load(f, Loader=yaml.FullLoader)['settings']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ip')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--max', type=int, default=90,
                    help='highest channel number to try (default 90)')
    args = ap.parse_args()

    settings = load_settings(args.config)
    if args.ip:
        settings = dict(settings, probe_ip=args.ip)

    probe = ProbeLaser(settings)
    if not probe.connected:
        print("No connection to the DLC pro.")
        return 1

    ws = probe.wide_scan
    original = ws.output_channel.get()
    print(f"\ncurrent wide-scan output-channel: {original}")
    print(f"  value unit {ws.value_unit.get()!r}  "
          f"value act {ws.value_act.get()}")

    # Other places in the tree that name an output channel, for comparison
    for label, path in (('laser1:scan', lambda: probe.laser.scan.output_channel.get()),):
        try:
            print(f"  {label}:output-channel = {path()}")
        except Exception as e:
            print(f"  {label}: unreadable ({type(e).__name__})")

    print(f"\ntrying channel numbers 0..{args.max}")
    print(f"  {'ch':>4}  {'unit':<8} {'value':>12}")
    print("  " + "-" * 30)

    found = []
    try:
        for channel in range(args.max + 1):
            try:
                ws.output_channel.set(channel)
            except Exception:
                continue            # controller rejected it, not a real channel
            try:
                unit = ws.value_unit.get()
                value = ws.value_act.get()
            except Exception as e:
                unit, value = f'({type(e).__name__})', float('nan')
            found.append((channel, unit, value))
            print(f"  {channel:>4}  {str(unit):<8} {value:>12.4f}")
    finally:
        try:
            ws.output_channel.set(original)
            print(f"\nrestored output-channel to {original}")
        except Exception as e:
            print(f"\nCOULD NOT RESTORE output-channel to {original}: {e}")

    if not found:
        print("\nNo channel number was accepted. Check that the wide-scan is "
              "not running and that probe_sdk_version matches the firmware.")
        return 1

    print(f"\n{len(found)} channel(s) accepted. Matching them up:")
    for channel, unit, value in found:
        unit_text = str(unit).lower()
        if 'a' in unit_text and 'k' not in unit_text:
            guess = 'diode current -> CHANNEL_CURRENT'
        elif 'k' in unit_text or 'c' in unit_text or 'deg' in unit_text:
            guess = 'temperature -> CHANNEL_TEMP'
        elif 'v' in unit_text:
            guess = 'a voltage output, piezo or aux'
        else:
            guess = ''
        print(f"  {channel:>4}  unit {str(unit):<8} {guess}")
    print("\nPut the current and temperature numbers into "
          "ProbeLaser.CHANNEL_CURRENT and CHANNEL_TEMP.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
