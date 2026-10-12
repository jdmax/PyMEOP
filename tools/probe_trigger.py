#!/usr/bin/python3
'''Find the DLC pro wide-scan trigger output channel, J.Maxwell 2021

laser1:wide-scan:trigger:output-channel is a bare integer, as the scan output
channel was, so this asks the controller which numbers it accepts and watches
the four digital outputs to see which one each number drives.

Nothing is scanned. Only configuration parameters are written, and the
original trigger settings are put back before exiting.

    python tools/probe_trigger.py
    python tools/probe_trigger.py --max 16
'''
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from app.instruments import ProbeLaser


def load_settings(path='config.yaml'):
    with open(path) as f:
        return yaml.load(f, Loader=yaml.FullLoader)['settings']


def ask_range(prompt):
    """Read a two-number range, however it is reasonably typed."""
    while True:
        answer = input(prompt).strip()
        if not answer:
            return None
        # the lookbehind keeps '100-120' from reading as 100 and -120
        numbers = re.findall(r'(?<![\d.])-?\d+\.?\d*', answer)
        if len(numbers) >= 2:
            return float(numbers[0]), float(numbers[1])
        print("    try something like 100-120")


def watch_outputs(probe, channels, begin, end, duration=2.0):
    """Run a scan per candidate channel and see which output it drives.

    The threshold is put mid-ramp on purpose. The output then holds one state
    for the first half of the scan and the other for the second, which a
    polled read will catch; a brief pulse at the very start would fall between
    polls, since reading all four outputs costs four round trips.
    """
    threshold = begin + (end - begin) * 0.5
    found = {}

    for channel in channels:
        probe.stop_scan()
        if not probe.set_scan_trigger(True, channel=channel,
                                      threshold=threshold):
            continue
        probe.config_wide_scan('current', begin, end, duration)

        before = {i: s.get('value_act')
                  for i, s in probe.digital_outputs().items()}
        probe.start_scan()
        seen = {i: {before[i]} for i in before}
        deadline = time.time() + duration * 1.3
        while time.time() < deadline:
            for i, state in probe.digital_outputs().items():
                seen.setdefault(i, set()).add(state.get('value_act'))
        probe.stop_scan()

        changed = [i for i, values in seen.items() if len(values) > 1]
        found[channel] = changed
        note = (f"digital-out{changed[0]}" if len(changed) == 1
                else (f"several: {changed}" if changed else "nothing toggled"))
        print(f"  channel {channel:>3}  ->  {note}")

    return found

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ip')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--max', type=int, default=16,
                    help='highest channel number to try (default 16)')
    ap.add_argument('--watch', action='store_true',
                    help='run a scan per candidate and see which output it drives')
    args = ap.parse_args()

    settings = load_settings(args.config)
    if args.ip:
        settings = dict(settings, probe_ip=args.ip)

    probe = ProbeLaser(settings)
    if not probe.connected:
        print("No connection to the DLC pro.")
        return 1

    original = probe.scan_trigger_config()
    print(f"\ncurrent trigger settings: {original}")

    print("\ndigital outputs as they stand")
    print(f"  {'out':>4}  {'mode':<22} {'level':>6}  {'inverted':>8}")
    print("  " + "-" * 46)
    for index, state in probe.digital_outputs().items():
        print(f"  {index:>4}  {str(state.get('mode')):<22} "
              f"{str(state.get('value_act')):>6}  {str(state.get('invert')):>8}")

    print(chr(10) + "analog outputs: these carry voltages rather than logic,"
          " so they are not what drives the trigger")
    for name, state in probe.analog_outputs().items():
        print(f"  {name}: {state}")

    print(f"\ntrying trigger output-channel 0..{args.max}")
    print(f"  {'ch':>4}  digital output modes after setting it")
    print("  " + "-" * 56)

    accepted = []
    try:
        for channel in range(args.max + 1):
            if not probe.set_scan_trigger(True, channel=channel):
                continue
            time.sleep(0.05)
            modes = {i: s.get('mode') for i, s in probe.digital_outputs().items()}
            accepted.append((channel, modes))
            print(f"  {channel:>4}  {modes}")
        if args.watch and accepted:
            limits = ask_range(chr(10) +
                "  ramp range in mA to test with (e.g. 100-120): ")
            if limits:
                print(chr(10) + "watching the digital outputs during a scan")
                watch_outputs(probe, [c for c, _ in accepted], *limits)
    finally:
        try:
            probe.set_scan_trigger(
                original.get('enabled', False),
                channel=original.get('channel'),
                threshold=original.get('threshold'))
            print(f"\nrestored trigger settings to {original}")
        except Exception as e:
            print(f"\nCOULD NOT RESTORE trigger settings {original}: {e}")

    if not accepted:
        print("\nNo channel number was accepted. Check that the wide-scan is "
              "not running and that the firmware has a trigger output.")
        return 1

    print(f"\n{len(accepted)} channel(s) accepted. The one whose digital "
          f"output changes mode when it is selected is the one that carries "
          f"the trigger; wire that connector to the SR860's rear Trig In and "
          f"put its number in trigger_channel.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
