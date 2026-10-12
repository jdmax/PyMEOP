#!/usr/bin/python3
'''Read the SR860 lock-in's current settings and print them, without changing anything.

Run from the repository root:
    python tools/sr860_check.py              # lock-in IP from config.yaml
    python tools/sr860_check.py 129.57.37.101

Use the printout to decide the nominal settings the DAQ should set at startup.
Every command sent is a query; nothing on the lock-in is changed.
'''
import sys
import time
from pathlib import Path

import yaml
from telnetlib3 import Telnet    # stdlib telnetlib removed in Python 3.13

PORT = 23
TIMEOUT = 2.0     # seconds to wait for each reply
PAUSE = 0.1       # seconds between queries; a burst of queries can go unanswered

# Index tables from the SR860 manual
TIME_CONSTANTS = [m * 10**e for e in range(-6, 5) for m in (1, 3)]          # 1 us .. 30 ks
SENS_VOLTS = [m * 10**e for e in range(0, -10, -1) for m in (1, 0.5, 0.2)][:28]   # 1 V .. 1 nV
SLOPES = ['6 dB/oct', '12 dB/oct', '18 dB/oct', '24 dB/oct']
OFF_ON = ['off', 'on']

# CDSP parameter indices, SR860 manual table for OUTR?/CDSP
PARAMS = ['X', 'Y', 'R', 'theta', 'Aux In 1', 'Aux In 2', 'Aux In 3', 'Aux In 4',
          'Xnoise', 'Ynoise', 'Aux Out 1', 'Aux Out 2', 'Ref phase', 'Sine amp', 'Sine offset',
          'Ref freq', 'Ext freq', 'Ext ref amp', 'Unused', 'Unused', 'Internal freq',
          'Data channel 1', 'Data channel 2', 'Data channel 3', 'Data channel 4']


def si(value, unit):
    '''Format a number with an SI prefix, e.g. 0.0002 V -> 200 uV'''
    if value == 0:
        return f"0 {unit}"
    for scale, prefix in ((1, ''), (1e-3, 'm'), (1e-6, 'u'), (1e-9, 'n'), (1e-12, 'p'), (1e-15, 'f')):
        if abs(value) >= scale * 0.999:
            return f"{value/scale:g} {prefix}{unit}"
    return f"{value:g} {unit}"


def from_list(table):
    '''Decoder that turns an integer index reply into its table entry'''
    def decode(reply):
        i = int(reply)
        return table[i] if 0 <= i < len(table) else f"unknown index {i}"
    return decode


def as_float(unit):
    return lambda reply: si(float(reply), unit)


def sensitivity(reply, current_mode=False):
    i = int(reply)
    if not 0 <= i < len(SENS_VOLTS):
        return f"unknown index {i}"
    # In current input mode the same index scale runs 1 uA .. 1 fA
    return si(SENS_VOLTS[i] * 1e-6, 'A') if current_mode else si(SENS_VOLTS[i], 'V')


# (label, query, decoder, config_key); config_key is the name a setting would take in
# config.yaml if the DAQ were to set it, chosen to match the stream branch's naming.
GROUPS = {
    'Reference': [
        ('Reference source',      'RSRC?',     from_list(['internal', 'external', 'dual', 'chopper']), None),
        ('External trigger',      'RTRG?',     from_list(['sine', 'positive TTL', 'negative TTL']), None),
        ('Ext ref input Z',       'REFZ?',     from_list(['50 Ohm', '1 MOhm']), None),
        ('Detected ref freq',     'FREQDET?',  as_float('Hz'), None),
        ('Reference freq',        'FREQ?',     as_float('Hz'), None),
        ('Harmonic',              'HARM?',     int, None),
        ('Phase',                 'PHAS?',     lambda r: f"{float(r):.3f} deg", 'lockin_phase'),
        ('Sine out amplitude',    'SLVL?',     as_float('V'), None),
        ('Sine out DC offset',    'SOFF?',     as_float('V'), None),
    ],
    'Signal input': [
        ('Input mode',            'IVMD?',     from_list(['voltage', 'current']), None),
        ('Voltage input',         'ISRC?',     from_list(['A', 'A-B']), None),
        ('Coupling',              'ICPL?',     from_list(['AC', 'DC']), None),
        ('Shield',                'IGND?',     from_list(['float', 'ground']), None),
        ('Voltage input range',   'IRNG?',     from_list(['1 V', '300 mV', '100 mV', '30 mV', '10 mV']), None),
        ('Current input gain',    'ICUR?',     from_list(['1 uA (1 MOhm)', '10 nA (100 MOhm)']), None),
        ('Sensitivity',           'SCAL?',     None, 'lockin_sens'),   # decoded after IVMD is known
        ('Signal strength (0-4)', 'ILVL?',     int, None),
    ],
    'Filters': [
        ('Time constant',         'OFLT?',     lambda r: si(TIME_CONSTANTS[int(r)], 's'), 'lockin_tc_stepped'),
        ('Filter slope',          'OFSL?',     from_list(SLOPES), 'lockin_slope_stepped'),
        ('Sync filter',           'SYNC?',     from_list(OFF_ON), 'lockin_sync'),
        ('Advanced filter',       'ADVFILT?',  from_list(OFF_ON), None),
        ('Equiv. noise BW',       'ENBW?',     as_float('Hz'), None),
    ],
    'Outputs and offsets': [
        ('CH1 output',            'COUT? 0',   from_list(['X', 'R']), None),
        ('CH2 output',            'COUT? 1',   from_list(['Y', 'theta']), None),
        ('X offset',              'COFA? 0',   from_list(OFF_ON), None),
        ('Y offset',              'COFA? 1',   from_list(OFF_ON), None),
        ('R offset',              'COFA? 2',   from_list(OFF_ON), None),
        ('X offset %',            'COFP? 0',   float, None),
        ('Y offset %',            'COFP? 1',   float, None),
        ('R offset %',            'COFP? 2',   float, None),
        ('X expand',              'CEXP? 0',   from_list(['x1', 'x10', 'x100']), None),
        ('Y expand',              'CEXP? 1',   from_list(['x1', 'x10', 'x100']), None),
        ('R expand',              'CEXP? 2',   from_list(['x1', 'x10', 'x100']), None),
        ('X ratio',               'CRAT? 0',   from_list(OFF_ON), None),
        ('Y ratio',               'CRAT? 1',   from_list(OFF_ON), None),
        ('R ratio',               'CRAT? 2',   from_list(OFF_ON), None),
    ],
    'Front-panel displays (what SNAPD? returns)': [
        ('Display 1',             'CDSP? 0',   from_list(PARAMS), None),
        ('Display 2',             'CDSP? 1',   from_list(PARAMS), None),
        ('Display 3',             'CDSP? 2',   from_list(PARAMS), None),
        ('Display 4',             'CDSP? 3',   from_list(PARAMS), None),
    ],
    'Status': [
        ('Lock-in status (LIAS?)', 'LIAS?',    int, None),
        ('Error status (ERRS?)',   'ERRS?',    int, None),
    ],
}

class SR860:
    '''Bare query-only connection, in the same style as app.instruments.LockIn'''

    def __init__(self, ip):
        self.tn = Telnet(ip, port=PORT, timeout=5)
        self.tn.read_until(b"\r", 2)        # discard the connection banner

    def query(self, cmd):
        '''Send one query and return the reply stripped, retrying once on timeout'''
        for attempt in range(2):
            self.tn.read_very_eager()       # drop anything left over from a late reply
            self.tn.write(bytes(f"{cmd}\r", 'ascii'))
            _, _, reply = self.tn.expect([rb"\r", rb"\n"], TIMEOUT)
            reply = reply.decode('ascii', errors='replace').strip()
            time.sleep(PAUSE)
            if reply:
                return reply
        return None

    def close(self):
        self.tn.close()


def main():
    root = Path(__file__).resolve().parent.parent
    if len(sys.argv) > 1:
        ip = sys.argv[1]
    else:
        with open(root / 'config.yaml') as f:
            ip = yaml.load(f, Loader=yaml.FullLoader)['settings']['lockin_ip']

    print(f"Connecting to SR860 at {ip}:{PORT} ...")
    try:
        li = SR860(ip)
    except Exception as e:
        sys.exit(f"Lock-in connection failed on {ip}: {e}")

    idn = li.query('*IDN?')
    print(f"Identity: {idn or 'no reply'}\n")

    raw = {}           # command -> raw reply, for the YAML summary
    current_mode = False
    for group, items in GROUPS.items():
        print(group)
        for label, cmd, decode, key in items:
            reply = li.query(cmd)
            raw[cmd] = reply
            if reply is None:
                shown = 'no reply'
            else:
                try:
                    if cmd == 'IVMD?':
                        current_mode = int(reply) == 1
                    if cmd == 'SCAL?':
                        shown = sensitivity(reply, current_mode)
                    else:
                        shown = decode(reply)
                except (ValueError, IndexError):
                    shown = '?'
                shown = f"{shown}   [{reply}]"
            print(f"    {label:24s} {shown}")
        print()

    # LIAS? bits latch until read, so the first read holds whatever has happened since the
    # last read, possibly days ago. A second read a moment later shows what is still occurring.
    def set_bits(reply):
        try:
            value = int(reply)
        except (TypeError, ValueError):
            return 'no reply'
        return ', '.join(str(b) for b in range(16) if value & (1 << b)) or 'none'
    time.sleep(1)
    print(f"LIAS? bits set, latched since last read: {set_bits(raw['LIAS?'])}")
    print(f"LIAS? bits set in the last second:       {set_bits(li.query('LIAS?'))}")
    print("    (check bit meanings against the LIA status register table in the SR860 manual)")

    xyr = li.query('SNAP? 0,1,2')
    if xyr:
        x, y, r = (float(v) for v in xyr.split(','))
        print(f"\nSNAP? 0,1,2   X = {si(x, 'V')}   Y = {si(y, 'V')}   R = {si(r, 'V')}")
        if r:
            print(f"              X/R = {x/r:.2f}  (1.00 when the phase is set to put the signal in X)")

    # Candidate config.yaml entries holding the values just read
    print("\nCurrent values as candidate config.yaml settings (raw indices as the SR860 takes them):")
    for group, items in GROUPS.items():
        for label, cmd, _, key in items:
            if key and raw.get(cmd) is not None:
                print(f"    {key}: {raw[cmd]:<12s} # {cmd[:-1]} {label}")

    li.close()


if __name__ == '__main__':
    main()
