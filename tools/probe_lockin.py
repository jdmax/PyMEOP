#!/usr/bin/python3
'''Wire-level probe of the SR860 socket interface, J.Maxwell 2021

check_hardware.py reported that *IDN? answers but the settings readback times
out. That means the socket is fine and something about the specific exchange is
not. This talks to the instrument directly, one command at a time, dumping raw
bytes, so the failure can be pinned to a terminator, a pacing problem, or a
single unsupported command.

Uses no project code beyond the config file, so a bug in LockIn cannot mask the
answer.

    python tools/probe_lockin.py
    python tools/probe_lockin.py --ip 129.57.37.101 --port 23
'''
import argparse
import socket
import sys
import time

try:
    import yaml
except ImportError:
    yaml = None


TERMINATORS = [('CR', b'\r'), ('LF', b'\n'), ('CRLF', b'\r\n')]

# The data capture command set is the least certain part of the driver, and the
# spelling has moved between SR860 firmware revisions. Each role lists the
# candidates to try, best guess first.
CAPTURE_CANDIDATES = {
    'max rate':   ['CAPTURERATEMAX?', 'CAPTUREMAXRATE?', 'CAPTRATEMAX?'],
    'rate index': ['CAPTURERATE?', 'CAPTRATE?'],
    'config':     ['CAPTURECFG?', 'CAPTURECONFIG?', 'CAPTCFG?'],
    'length':     ['CAPTURELEN?', 'CAPTURELENGTH?', 'CAPTLEN?'],
    'status':     ['CAPTURESTAT?', 'CAPTURESTATUS?', 'CAPTSTAT?'],
    'progress':   ['CAPTUREBYTES?', 'CAPTUREPROG?', 'CAPTBYTES?'],
}


class Wire():
    def __init__(self, ip, port, timeout=2.0, verbose=True):
        self.verbose = verbose
        self.sock = socket.create_connection((ip, port), timeout=timeout)
        self.sock.settimeout(timeout)
        banner = self.drain()
        if banner:
            print(f"  banner on connect: {banner!r}")

    def drain(self, wait=0.3):
        '''Swallow and return anything already waiting.'''
        self.sock.settimeout(wait)
        got = bytearray()
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                got.extend(chunk)
        except (socket.timeout, OSError):
            pass
        finally:
            self.sock.settimeout(2.0)
        return bytes(got)

    def send(self, cmd, term=b'\r'):
        self.sock.sendall(cmd.encode('ascii') + term)

    def read_raw(self, wait=3.0):
        '''Collect everything that arrives in the window, no terminator logic.

        Binary block data contains 0x0A and 0x0D bytes, so the line-oriented
        reader would truncate it at the first one.
        '''
        self.sock.settimeout(wait)
        got = bytearray()
        try:
            while True:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                got.extend(chunk)
        except socket.timeout:
            pass
        return bytes(got)

    def read(self, wait=2.0):
        '''Read whatever arrives within the window. Returns raw bytes.'''
        self.sock.settimeout(wait)
        got = bytearray()
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                got.extend(chunk)
                if got.endswith(b'\n') or got.endswith(b'\r'):
                    break
        except socket.timeout:
            pass
        return bytes(got)

    def exchange(self, cmd, term=b'\r', wait=2.0, label=None):
        self.send(cmd, term)
        reply = self.read(wait)
        ok = bool(reply)
        mark = 'ok  ' if ok else 'FAIL'
        print(f"    {mark} {label or cmd:<24} -> {reply!r}")
        return reply

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def load_ip(path='config.yaml'):
    if yaml is None:
        return None, None
    try:
        with open(path) as f:
            s = yaml.load(f, Loader=yaml.FullLoader)['settings']
        return s.get('lockin_ip'), int(s.get('lockin_port', 23))
    except Exception:
        return None, None


def probe_command(ip, port, term, cmd, wait=1.5):
    '''Try one command on its own fresh connection.

    A command the firmware does not recognise can leave the parser unable to
    answer anything further, so reusing one connection would make every later
    candidate look broken too. Returns (reply, reason_it_failed).
    '''
    try:
        w = Wire(ip, port, verbose=False)
    except Exception as e:
        return None, f"could not connect ({e})"
    try:
        w.send('*IDN?', term)
        if not w.read(1.5):
            return None, "connection did not answer *IDN?"
        w.drain(0.1)

        w.send(cmd, term)
        reply = w.read(wait)
        if reply:
            return reply.decode('ascii', 'replace').strip(), None

        # Silence alone does not say whether the command was rejected or just
        # slow, so ask the instrument whether it logged an error.
        w.drain(0.1)
        w.send('ERRS?', term)
        err = w.read(1.0)
        detail = err.decode('ascii', 'replace').strip() if err else 'no answer'
        return None, f"no reply (ERRS? -> {detail})"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        w.close()


def describe(raw):
    '''Say what came back, in the terms that matter for a block transfer.'''
    if not raw:
        return "nothing"
    head = raw[:24]
    note = f"{len(raw)} bytes, starts {head!r}"
    if raw[:1] == b'#':
        try:
            ndigits = int(raw[1:2])
            declared = int(raw[2:2 + ndigits])
            payload = len(raw) - (2 + ndigits)
            note += f"  -> IEEE block, declares {declared} B, got {payload} B"
            if payload < declared:
                note += " (SHORT)"
        except ValueError:
            note += "  -> starts with # but the header will not parse"
    elif b'\xff' in raw:
        note += "  -> contains 0xFF; telnet IAC escaping may be corrupting it"
    return note


def probe_capture_transfer(ip, port, term):
    '''Find out how CAPTUREGET? wants to be asked, and whether it works at all.

    This is the one command the command-name probe cannot cover: it takes
    arguments and answers with binary, so it needs its own capture running and
    a reader that does not stop at the first newline.
    '''
    results = {}
    w = Wire(ip, port, timeout=5.0, verbose=False)
    try:
        w.send('*IDN?', term)
        if not w.read(2.0):
            print("    could not reach the instrument")
            return results
        w.drain(0.2)

        print("    arming a 1 s capture")
        for cmd in ('CAPTURECFG 1', 'CAPTURERATE 5', 'CAPTURELEN 16'):
            w.send(cmd, term)
            time.sleep(0.1)
        w.drain(0.2)

        w.send('CAPTURESTART 0,0', term)
        time.sleep(1.2)

        w.send('CAPTUREBYTES?', term)
        during = w.read(2.0)
        print(f"    CAPTUREBYTES? while running -> {during!r}")
        w.drain(0.2)

        # Does a transfer work at all while the capture is still going? The
        # live plot during a sweep depends on this being allowed.
        forms = ['CAPTUREGET? 0,1', 'CAPTUREGET?0,1', 'CAPTUREGET? 0, 1']
        print("    transfers WHILE running:")
        for form in forms:
            w.send(form, term)
            raw = w.read_raw(3.0)
            print(f"      {form:<22} {describe(raw)}")
            if raw:
                results['during'] = form
                break
            w.drain(0.2)

        w.send('CAPTURESTOP', term)
        time.sleep(0.2)
        w.drain(0.2)

        w.send('CAPTUREBYTES?', term)
        after = w.read(2.0)
        print(f"    CAPTUREBYTES? after stop -> {after!r}")
        w.drain(0.2)

        print("    transfers AFTER stop:")
        for form in forms:
            w.send(form, term)
            raw = w.read_raw(3.0)
            print(f"      {form:<22} {describe(raw)}")
            if raw:
                results['after'] = form
                break
            w.drain(0.2)
    finally:
        w.close()
    return results


def probe_port_for_binary(ip, port, term):
    '''Can this port actually carry a binary capture transfer?

    Port 23 on the SR860 is an ASCII console. It answers every text command
    happily and then returns a lone control byte for CAPTUREGET?, which is not
    a truncated payload -- it is the interface refusing to carry binary. The
    raw socket port is the one that can.
    '''
    out = {'port': port}
    try:
        w = Wire(ip, port, timeout=3.0, verbose=False)
    except Exception as e:
        out['error'] = f"cannot connect ({type(e).__name__})"
        return out
    try:
        w.send('*IDN?', term)
        idn = w.read(2.0)
        if not idn:
            out['error'] = "no answer to *IDN?"
            return out
        out['idn'] = idn.decode('ascii', 'replace').strip()
        w.drain(0.2)

        for cmd in ('CAPTURECFG 1', 'CAPTURERATE 5', 'CAPTURELEN 16'):
            w.send(cmd, term)
            time.sleep(0.1)
        w.drain(0.2)
        w.send('CAPTURESTART 0,0', term)
        time.sleep(0.8)

        w.send('CAPTUREBYTES?', term)
        nbytes = w.read(2.0)
        out['bytes'] = nbytes.decode('ascii', 'replace').strip()
        w.drain(0.2)

        w.send('CAPTUREGET? 0, 1', term)
        raw = w.read_raw(3.0)
        out['got'] = len(raw)
        out['head'] = raw[:16]
        out['usable'] = len(raw) >= 1024

        w.send('CAPTURESTOP', term)
    except Exception as e:
        out['error'] = f"{type(e).__name__}: {e}"
    finally:
        w.close()
    return out


def stage(title):
    print(f"\n{title}")
    print("  " + "-" * 66)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ip')
    ap.add_argument('--port', type=int)
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--ports', action='store_true',
                    help='only test which port can carry a binary transfer')
    args = ap.parse_args()

    ip, port = load_ip(args.config)
    ip = args.ip or ip
    port = args.port or port or 23
    if not ip:
        print("No IP. Pass --ip or point --config at a readable config.yaml")
        return 1

    print(f"Probing SR860 at {ip}:{port}")
    findings = {}

    if args.ports:
        stage("Which port can carry a binary capture transfer?")
        best = None
        for candidate in (23, 50000, 5025, 1861):
            r = probe_port_for_binary(ip, candidate, b'\r')
            if r.get('error'):
                print(f"    port {candidate:<6} {r['error']}")
                continue
            verdict = 'USABLE' if r.get('usable') else 'text only'
            print(f"    port {candidate:<6} {verdict:<10} "
                  f"CAPTUREBYTES?={r.get('bytes'):<8} "
                  f"CAPTUREGET? -> {r.get('got')} bytes {r.get('head')!r}")
            if r.get('usable') and best is None:
                best = candidate
        print()
        if best:
            print(f"  Use port {best}. Set in config.yaml:")
            print(f"    lockin_port: {best}")
        else:
            print("  No port returned a real payload. The capture buffer may")
            print("  need to be read over VXI-11/USB/GPIB on this unit, or the")
            print("  transfer needs a command this probe has not tried.")
        return 0

    # 1: which terminator gets an answer at all, on a fresh connection each time
    stage("1. Command terminator")
    for name, term in TERMINATORS:
        w = Wire(ip, port)
        reply = w.exchange('*IDN?', term, label=f"*IDN? [{name}]")
        if reply:
            findings.setdefault('term', (name, term))
            findings.setdefault('reply_end', reply[-2:])
        w.close()
        time.sleep(0.2)

    if 'term' not in findings:
        print("\n  Nothing answered on any terminator. Check the port.")
        return 1
    name, term = findings['term']
    print(f"\n  -> using {name} for the rest")
    print(f"  -> replies end with {findings['reply_end']!r}")

    # 2: a bare query with no preceding set
    stage("2. Bare queries, no sets first")
    w = Wire(ip, port)
    w.exchange('*IDN?', term)
    for cmd in ('OFLT?', 'OFSL?', 'SYNC?', 'SCAL?', 'FREQ?'):
        reply = w.exchange(cmd, term)
        findings[f'bare_{cmd}'] = bool(reply)
        w.drain(0.1)
    w.close()

    dead = [c for c in ('OFLT?', 'OFSL?', 'SYNC?', 'SCAL?', 'FREQ?')
            if not findings.get(f'bare_{c}')]
    if dead:
        print(f"\n  These do not answer even on their own: {', '.join(dead)}")
        print("  That is an unsupported command, not a pacing problem.")

    # 3: does a set poison the following query?
    stage("3. Set then query, no delay")
    w = Wire(ip, port)
    w.exchange('*IDN?', term)
    w.send('OFLT 7', term)
    reply = w.exchange('OFLT?', term, label='OFLT? after OFLT 7')
    findings['set_then_query'] = bool(reply)
    w.close()

    stage("4. Set then query, 100 ms gap")
    w = Wire(ip, port)
    w.exchange('*IDN?', term)
    w.send('OFLT 7', term)
    time.sleep(0.1)
    reply = w.exchange('OFLT?', term, label='OFLT? after 100 ms')
    findings['set_then_query_delay'] = bool(reply)
    w.close()

    # 5: the exact sequence configure() sends
    stage("5. The sequence that failed, with pacing")
    w = Wire(ip, port)
    w.exchange('*IDN?', term)
    for cmd in ('OFLT 7', 'OFSL 1', 'SYNC 0'):
        w.send(cmd, term)
        time.sleep(0.05)
        leftover = w.drain(0.1)
        if leftover:
            print(f"    note: {cmd!r} produced unsolicited {leftover!r}")
    for cmd in ('OFLT?', 'OFSL?', 'SYNC?'):
        findings[f'seq_{cmd}'] = bool(w.exchange(cmd, term))
    w.close()

    # 6: discover the capture command spellings
    stage("6. Capture buffer commands (trying candidate spellings)")
    capture = {}
    for role, candidates in CAPTURE_CANDIDATES.items():
        print(f"    {role}:")
        for cmd in candidates:
            reply, why = probe_command(ip, port, term, cmd)
            if reply:
                print(f"      ok   {cmd:<22} -> {reply!r}")
                capture[role] = (cmd, reply)
                break
            print(f"      --   {cmd:<22} {why}")
        if role not in capture:
            print(f"      none of the candidates answered")
    findings['capture'] = capture

    # 7: the binary transfer itself
    stage("7. CAPTUREGET? binary transfer")
    transfer = probe_capture_transfer(ip, port, term)
    findings['transfer'] = transfer

    # verdict
    stage("Verdict")
    print(f"  terminator to use:        {name}")
    if dead:
        print(f"  unsupported commands:     {', '.join(dead)}")
        print( "                            remove these from get_config()")
    if findings.get('set_then_query') is False and \
            findings.get('set_then_query_delay') is True:
        print("  a set DOES stall the next query unless paced")
        print("  -> set lockin_cmd_delay: 0.1 in config.yaml")
    elif findings.get('set_then_query'):
        print("  pacing is not the problem; sets do not stall queries")

    capture = findings.get('capture', {})
    missing = [r for r in CAPTURE_CANDIDATES if r not in capture]
    if capture:
        print("\n  capture commands that answered:")
        for role, (cmd, reply) in capture.items():
            print(f"    {role:<12} {cmd:<22} -> {reply}")
    if missing:
        print(f"\n  no spelling found for: {', '.join(missing)}")
        print( "  Send this output back -- the driver needs the real names,")
        print( "  which are in the Data Capture section of the SR860 manual.")
    elif capture:
        print("\n  all capture roles resolved")

    transfer = findings.get('transfer', {})
    if transfer.get('during'):
        print(f"\n  CAPTUREGET? works while capturing, as {transfer['during']!r}")
    elif transfer.get('after'):
        print(f"\n  CAPTUREGET? only works AFTER CAPTURESTOP, as "
              f"{transfer['after']!r}")
        print( "  -> the sweep must read its buffer at the end rather than")
        print( "     streaming it, so there is no live trace during a ramp")
    else:
        print("\n  CAPTUREGET? returned nothing in any form, running or stopped.")
        print( "  Check the Data Capture section of the manual for the argument")
        print( "  units, and whether binary transfers need a different port.")

    escape = {'CR': r'\r', 'LF': r'\n', 'CRLF': r'\r\n'}[name]
    print(f"\n  Add to config.yaml (single quotes -- the escape is resolved")
    print(f"  by the loader, not by YAML):")
    print(f"    lockin_term: '{escape}'")
    if findings.get('set_then_query'):
        print(f"    lockin_cmd_delay: 0      # sets do not stall queries here")
    return 0


if __name__ == '__main__':
    sys.exit(main())
