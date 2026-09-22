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


def stage(title):
    print(f"\n{title}")
    print("  " + "-" * 66)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ip')
    ap.add_argument('--port', type=int)
    ap.add_argument('--config', default='config.yaml')
    args = ap.parse_args()

    ip, port = load_ip(args.config)
    ip = args.ip or ip
    port = args.port or port or 23
    if not ip:
        print("No IP. Pass --ip or point --config at a readable config.yaml")
        return 1

    print(f"Probing SR860 at {ip}:{port}")
    findings = {}

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

    print(f"\n  Add to config.yaml:")
    print(f"    lockin_term: '{'CRLF' if name == 'CRLF' else name}'"
          f"   # as an escape: "
          f"{repr(term.decode('ascii'))}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
