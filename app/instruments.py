'''PyMEOP J.Maxwell 2021
'''
import re
import math
import socket
import struct
import time

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal, Qt
# from labjack import ljm
from telnetlib3 import Telnet    # stdlib telnetlib removed in Python 3.13


class ProbeLaser():
    '''Access Probe laser over telnet
    '''

    # laser1:wide-scan:output-channel enum values
    CHANNEL_TEMP = 56
    CHANNEL_CURRENT = 63

    SHAPE_SAWTOOTH = 0
    SHAPE_TRIANGLE = 1

    def __init__(self, settings):
        '''Open connection to Toptica DLC controller
        '''
        self.ip = settings['probe_ip']
        self.port = 1998

        try:
            self.tn = Telnet(self.ip, port=self.port, timeout=2)

            outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
            self.tn.write(bytes("(param-disp 'laser1:dl:cc:current-set)\n", 'ascii'))
            outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')



        except Exception as e:
            print(f"Probe connection failed on {self.ip}: {e}")

    # def __del__(self):
        # self.tn.close()

    def _send(self, line):
        '''Write one DeCoF line and return the controller's reply text'''
        self.tn.write(bytes(f"{line}\n", 'ascii'))
        return self.tn.read_until(bytes(">", 'ascii'), 2).decode('ascii')

    def _set(self, param, value):
        '''Set a DeCoF parameter'''
        return self._send(f"(param-set! '{param} {value})")

    def _get(self, param):
        '''Read a DeCoF parameter, raw reply text'''
        return self._send(f"(param-disp '{param})")

    def _exec(self, param):
        '''Execute a DeCoF command.

        Note this is (exec '...), not (param-set! '...) -- an execute takes no
        value, so the old param-set! form was malformed and silently did nothing.
        '''
        return self._send(f"(exec '{param})")

    @staticmethod
    def _number(reply):
        '''Pull the first number out of a DeCoF reply, or None'''
        if reply is None:
            return None
        m = re.search(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', reply)
        return float(m.group()) if m else None

    def read_current(self):
        """
        """
        self.tn.write(bytes(f"(param-disp 'laser1:dl:cc:current-set)\n", 'ascii'))
        outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
        return outp

    def set_current(self, current):
        '''Arguments:
                curent: float
        '''
        self.tn.write(bytes(f"(param-set! 'laser1:dl:cc:current-set {current})\n", 'ascii'))
        outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
        return outp


    def read_temp(self, temp):
        '''
        '''
        self.tn.write(bytes(f"(param-disp 'laser1:dl:tc:temp-set)\n", 'ascii'))
        outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
        return outp

    def set_temp(self, temp):
        '''Arguments:
                temp: float
        '''
        self.tn.write(bytes(f"(param-set! 'laser1:dl:tc:temp-set {temp})\n", 'ascii'))
        outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
        return outp

    def config_scan(self, type, begin, end, mode, shape, speed):
        """ Configure parameters for a wide-scan
        Arguments:
            type: STR: current or temp (mA or C)
            begin: start value (mA or C)
            end: stop value
            mode: BOOL: true (#t) for continuous, false (#f) for one-shot
            shape: INT: 0 for sawtooth, 1 for triangle
            speed: rate in mA/s or K/s
        """
        try:
            type_code = self.CHANNEL_TEMP if 'temp' in type else self.CHANNEL_CURRENT
            self._set('laser1:wide-scan:output-channel', type_code)
            self._set('laser1:wide-scan:scan-begin', begin)
            self._set('laser1:wide-scan:scan-end', end)
            self._set('laser1:wide-scan:continuous-mode', "#t" if mode else "#f")
            self._set('laser1:wide-scan:shape', shape)
            self._set('laser1:wide-scan:speed', speed)
            return True
        except Exception as e:
            print(f"Scan config failed: {e}")
            return False

    def config_wide_scan(self, channel, begin, end, duration,
                         shape=SHAPE_SAWTOOTH, continuous=False):
        '''Configure a wide-scan by duration rather than by rate.

        Arguments:
            channel: 'current' or 'temp'
            begin, end: scan limits (mA or C)
            duration: seconds for one ramp
            shape: SHAPE_SAWTOOTH or SHAPE_TRIANGLE
            continuous: True to free-run, False for a single ramp
        Returns:
            The speed actually requested, in units/s.
        '''
        span = abs(end - begin)
        if duration <= 0 or span <= 0:
            raise ValueError(f"Bad wide-scan range {begin}->{end} over {duration} s")
        speed = span / float(duration)
        self.config_scan(channel, begin, end, continuous, shape, speed)
        return speed

    def wide_scan_state(self):
        '''Return the wide-scan state as a number, or None if unavailable.'''
        try:
            return self._number(self._get('laser1:wide-scan:state'))
        except Exception:
            return None

    def set_scan_trigger(self, enabled):
        '''Enable the wide-scan trigger output so the lock-in can hardware-sync.

        The exact DeCoF parameter for the trigger output differs between DLC pro
        firmware revisions -- this is the one spot to fix if 'trigger' sync does
        not work on the bench. Failure here is non-fatal: the caller falls back
        to software sync.
        '''
        try:
            self._set('laser1:wide-scan:trigger:output-enabled', "#t" if enabled else "#f")
            return True
        except Exception as e:
            print(f"Wide-scan trigger config failed: {e}")
            return False

    def start_scan(self):
        """ Start wide scan
        """
        try:
            self._exec('laser1:wide-scan:start')
            return True
        except Exception as e:
            print(f"Start scan failed: {e}")
            return False

    def stop_scan(self):
        """ Stop wide scan
        """
        try:
            self._exec('laser1:wide-scan:stop')
            return True
        except Exception as e:
            print(f"Stop scan failed: {e}")
            return False


class WavelengthMeter():
    '''Access Wavelength meter'''


    def __init__(self, settings):
        '''Start connection over telnet'''
        self.ip = settings['meter_ip']
        self.port = 5025

        try:
            self.tn = Telnet(self.ip, port=self.port, timeout=4)
            self.tn.write(bytes(f"MEAS:POW:WAV?\n", 'ascii'))
            outp = self.tn.read_some().decode('ascii')


        except Exception as e:
            print(f"Meter connection failed on {self.ip}: {e}")

    def __del__(self):
        #self.tn.close()
        pass

    def start_cont(self):
        '''Start continuous measurements'''
        self.tn.write(bytes(f"INIT:CONT 1\r", 'ascii'))

    def stop_cont(self):
        '''Stop continuous measurements'''
        self.tn.write(bytes(f"INIT:CONT 0\r", 'ascii'))

    def read_wavelength(self, channel):
        '''Arguments:
                channel: 1 or 2 for pump or probe
            Returns wavelenth in nm

        '''
        self.tn.write(bytes(f"FETC:POW:WAV?\r", 'ascii'))
        outp = self.tn.read_some().decode('ascii')
        return float(outp)*1e9


class LockIn():
    '''Access SR860 lock-in amplifier.

    Uses a plain socket rather than telnet: the data capture buffer is
    transferred as a binary IEEE-488.2 block, and a telnet connection escapes
    0xFF (IAC) bytes, which corrupts float data. Set 'lockin_port' in the config
    to a raw socket port if the default does not transfer binary cleanly.
    '''

    # OFLT index -> time constant in seconds
    TC_TABLE = [1e-6, 3e-6, 1e-5, 3e-5, 1e-4, 3e-4,
                1e-3, 3e-3, 1e-2, 3e-2, 1e-1, 3e-1,
                1.0, 3.0, 10.0, 30.0, 1e2, 3e2,
                1e3, 3e3, 1e4, 3e4]
    # OFSL index -> filter slope in dB/oct
    SLOPE_TABLE = [6, 12, 18, 24]
    # CAPTURECFG index -> (name, number of channels)
    CAPTURE_CFG = {'X': (0, 1), 'XY': (1, 2), 'RT': (2, 2), 'XYRT': (3, 4)}

    MAX_CAPTURE_KB = 65536          # 64 MB capture buffer
    GET_CHUNK_KB = 64               # kB per CAPTUREGET? transfer

    term = '\r'                     # command terminator, overridden per instance
    cmd_delay = 0.05                # seconds between writes
    settle = 0.3                    # pause after changing the filter config
    _get_fmt = None                 # CAPTUREGET? argument form that works

    # Data capture vocabulary. The spelling has moved between SR860 firmware
    # revisions, so these are overridable from config (lockin_capture_cmds)
    # once tools/probe_lockin.py has established what this unit answers to.
    CAPTURE_CMDS = {
        'ratemax': 'CAPTURERATEMAX?',
        'rate': 'CAPTURERATE',
        'cfg': 'CAPTURECFG',
        'len': 'CAPTURELEN',
        'start': 'CAPTURESTART',
        'stop': 'CAPTURESTOP',
        'bytes': 'CAPTUREBYTES?',
        'get': 'CAPTUREGET?',
        'rate_read': 'CAPTURERATE?',
        'len_read': 'CAPTURELEN?',
    }
    capture_cmds = CAPTURE_CMDS     # instance copy is made in __init__

    def __init__(self, settings):
        '''Open socket to the lock-in'''
        self.ip = settings['lockin_ip']
        self.port = int(settings.get('lockin_port', 23))
        self.sock = None
        self._buf = bytearray()
        self._cap_channels = 2
        self._cap_cursor_kb = 0
        self._get_fmt = None
        # Wire details that vary between firmware revisions. Defaults match
        # what the old telnet code used; tools/probe_lockin.py determines the
        # right values on the bench.
        self.term = settings.get('lockin_term', '\r').encode().decode(
            'unicode_escape')
        self.cmd_delay = float(settings.get('lockin_cmd_delay', 0.05))
        self.settle = float(settings.get('lockin_settle', 0.3))
        self.capture_cmds = dict(self.CAPTURE_CMDS)
        self.capture_cmds.update(settings.get('lockin_capture_cmds') or {})

        try:
            self.sock = socket.create_connection((self.ip, self.port), timeout=5)
            self.sock.settimeout(5)
            # Drain any banner the instrument sends on connect
            self._drain()
        except Exception as e:
            print(f"Lock-in connection failed on {self.ip}: {e}")

    def __del__(self):
        try:
            self.sock.close()
        except Exception:
            pass

    # -- transport ---------------------------------------------------------

    def _drain(self):
        '''Discard anything already waiting on the socket'''
        self.sock.settimeout(0.2)
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
        except (socket.timeout, OSError):
            pass
        finally:
            self.sock.settimeout(5)
        self._buf.clear()

    def _write(self, cmd):
        self.sock.sendall(bytes(f"{cmd}{self.term}", 'ascii'))

    def _read_exact(self, n):
        '''Read exactly n bytes'''
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise IOError("Lock-in closed the connection")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _read_line(self):
        '''Read up to a \\r or \\n terminator'''
        while True:
            for i, b in enumerate(self._buf):
                if b in (0x0A, 0x0D):
                    line = bytes(self._buf[:i])
                    del self._buf[:i + 1]
                    # A CRLF pair must be consumed whole, or the stray LF is
                    # read as an empty reply to the next query.
                    if self._buf[:1] in (b'\n', b'\r') and self._buf[:1] != bytes([b]):
                        del self._buf[:1]
                    return line.decode('ascii', 'replace').strip()
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError("Lock-in closed the connection")
            self._buf.extend(chunk)

    def command(self, cmd):
        '''Send a command that returns nothing.

        Consecutive sets can arrive coalesced in a single TCP segment, which
        some firmware parses badly. Queries need no such gap -- the blocking
        read that follows paces them, and a delay there would only slow the
        capture polling loop.
        '''
        self._write(cmd)
        if self.cmd_delay:
            time.sleep(self.cmd_delay)

    def query(self, cmd, retries=1):
        '''Send a query and return the reply as a string.

        Retries once on a timeout. The first query after a burst of set
        commands is intermittently dropped by this unit -- changing the time
        constant reconfigures the filter chain, and a query landing in that
        window sometimes goes unanswered. Draining first discards a late
        reply so it cannot be mistaken for the answer to the retry.
        '''
        for attempt in range(retries + 1):
            try:
                self._write(cmd)
                return self._read_line()
            except (socket.timeout, TimeoutError):
                if attempt >= retries:
                    raise
                self._drain()

    def query_float(self, cmd):
        return float(self.query(cmd))

    def query_int(self, cmd):
        return int(float(self.query(cmd)))

    def _read_block(self):
        '''Read an IEEE-488.2 definite length binary block: #<n><len><data>'''
        head = self._read_exact(1)
        if head != b'#':
            raise IOError(f"Expected binary block, got {head!r}")
        ndigits = int(self._read_exact(1))
        nbytes = int(self._read_exact(ndigits))
        return self._read_exact(nbytes)

    # -- configuration -----------------------------------------------------

    @classmethod
    def nearest_tc(cls, seconds):
        '''Return (index, actual_seconds) for the closest available TC'''
        idx = min(range(len(cls.TC_TABLE)),
                  key=lambda i: abs(math.log(cls.TC_TABLE[i] / float(seconds))))
        return idx, cls.TC_TABLE[idx]

    @classmethod
    def nearest_slope(cls, db_per_oct):
        '''Return (index, actual_db) for the closest available filter slope'''
        idx = min(range(len(cls.SLOPE_TABLE)),
                  key=lambda i: abs(cls.SLOPE_TABLE[i] - db_per_oct))
        return idx, cls.SLOPE_TABLE[idx]

    def configure(self, tc=None, slope=None, sync=None):
        '''Set time constant (s), filter slope (dB/oct) and sync filter.

        Returns the configuration actually applied, read back from the
        instrument rather than assumed.
        '''
        if tc is not None:
            idx, actual = self.nearest_tc(tc)
            self.command(f"OFLT {idx}")
            if abs(actual - tc) / float(tc) > 0.01:
                print(f"Lock-in TC {tc} s not available, using {actual} s")
        if slope is not None:
            idx, actual = self.nearest_slope(slope)
            self.command(f"OFSL {idx}")
            if actual != slope:
                print(f"Lock-in slope {slope} dB/oct not available, using {actual} dB/oct")
        if sync is not None:
            self.command(f"SYNC {1 if sync else 0}")

        # Changing the time constant reconfigures the output filter. Give it a
        # moment before reading anything back, rather than relying on the
        # retry in query() to paper over a query that lands mid-reconfigure.
        time.sleep(self.settle)
        return self.get_config()

    def get_config(self):
        '''Read back the settings that affect the shape of a swept spectrum.

        This goes into every event file so a scan can be interpreted later.
        '''
        config = {}
        # Queried one at a time so a single unsupported or slow command names
        # itself instead of taking the whole readback down with it.
        for key, cmd, convert in (
                ('tc', "OFLT?", lambda v: self.TC_TABLE[int(float(v))]),
                ('slope_db', "OFSL?", lambda v: self.SLOPE_TABLE[int(float(v))]),
                ('sync', "SYNC?", lambda v: bool(int(float(v)))),
                ('sensitivity', "SCAL?", float),
                ('ref_freq', "FREQ?", float)):
            try:
                config[key] = convert(self.query(cmd))
            except Exception as e:
                print(f"Lock-in {cmd} failed ({type(e).__name__}: {e})")

        if 'slope_db' in config and 'tc' in config:
            # group delay of an n-pole filter is n * tau
            poles = config['slope_db'] // 6
            config['poles'] = int(poles)
            config['group_delay'] = poles * config['tc']
        return config

    def read_all(self):
        '''Returns lock-in x, y, r.

        Uses SNAP? with explicit parameters rather than SNAPD?, which returns
        whatever the front panel display happens to be configured to show.
        '''
        try:
            reply = self.query("SNAP? 0,1,2")
            x, y, r = reply.split(',')[:3]
            return x, y, r
        except Exception as e:
            print(f"Lock read failed: {e}")
            return 0, 0, 0

    # -- data capture ------------------------------------------------------

    def capture_rate_max(self):
        '''Maximum capture rate in Hz for the current time constant'''
        return self.query_float(self.capture_cmds['ratemax'])

    def capture_config(self, target_rate, channels='XY', seconds=None):
        '''Configure the capture buffer.

        Arguments:
            target_rate: desired sample rate in Hz. The SR860 only supports
                rate_max / 2**n, so the nearest available rate is used.
            channels: 'X', 'XY', 'RT' or 'XYRT'
            seconds: length of acquisition to size the buffer for
        Returns:
            (actual_rate, n_channels, buffer_kb)
        '''
        cfg_idx, nch = self.CAPTURE_CFG[channels]
        self.command(f"{self.capture_cmds['cfg']} {cfg_idx}")
        self._cap_channels = nch

        rate_max = self.capture_rate_max()
        n = int(round(math.log2(rate_max / float(target_rate))))
        n = max(0, min(20, n))
        self.command(f"{self.capture_cmds['rate']} {n}")

        # CAPTURERATE? answers in Hz while CAPTURERATE is set with a divider
        # index, so the rate is read back rather than assumed. Every sample's
        # position on the current axis is derived from this number -- if it is
        # wrong the spectrum is stretched and the peak positions are nonsense.
        expected = rate_max / (2 ** n)
        actual_rate = expected
        try:
            reading = self.query_float(self.capture_cmds['rate_read'])
            if reading > 0:
                actual_rate = reading
                if abs(reading - expected) / expected > 0.01:
                    print(f"Capture rate set as index {n} (expected "
                          f"{expected:.1f} Hz) but reads back {reading:.1f} Hz. "
                          f"Using the readback. If this looks like the index "
                          f"was taken as a frequency, CAPTURERATE wants Hz on "
                          f"this firmware.")
        except Exception as e:
            print(f"Could not read capture rate back ({e}); "
                  f"assuming {expected:.1f} Hz")

        if seconds is None:
            seconds = 1.0
        nbytes = actual_rate * seconds * nch * 4
        kb = int(math.ceil(nbytes / 1024.0)) + 2       # margin for rounding
        kb = max(1, min(self.MAX_CAPTURE_KB, kb))
        self.command(f"{self.capture_cmds['len']} {kb}")

        try:
            got_kb = self.query_float(self.capture_cmds['len_read'])
            if got_kb and abs(got_kb - kb) > max(1, 0.1 * kb):
                print(f"Capture length asked for {kb} kB but reads back "
                      f"{got_kb:g}. CAPTURELEN may not be in kilobytes on this "
                      f"firmware; a short buffer truncates every sweep.")
                kb = got_kb
        except Exception:
            pass

        return actual_rate, nch, kb

    def capture_start(self, continuous=False, triggered=False):
        '''Start a capture. Resets the read cursor.'''
        self._cap_cursor_kb = 0
        self.command(f"{self.capture_cmds['start']} "
                     f"{1 if continuous else 0},{1 if triggered else 0}")

    def capture_stop(self):
        self.command(self.capture_cmds['stop'])

    def capture_bytes(self):
        '''Bytes captured so far'''
        return self.query_int(self.capture_cmds['bytes'])

    def _capture_get(self, offset_kb, n_kb):
        '''Fetch one chunk of the capture buffer as a binary block.

        The spacing of the arguments is not cosmetic. Firmware V1.51 answers
        "CAPTUREGET? 0, 1" but stays silent for "CAPTUREGET? 0,1", which is
        how this looked like a dead command for several rounds. Both forms are
        tried and the one that works is remembered, so the fallback costs at
        most one timeout per session rather than one per chunk.
        '''
        forms = [self._get_fmt] if self._get_fmt else ['{}, {}', '{},{}']
        failure = None
        for fmt in forms:
            try:
                self._write(f"{self.capture_cmds['get']} "
                            + fmt.format(offset_kb, n_kb))
                block = self._read_block()
                self._get_fmt = fmt
                return block
            except (socket.timeout, TimeoutError, IOError) as e:
                failure = e
                self._drain()
        raise failure

    def capture_read_new(self):
        '''Read whatever whole kB have arrived since the last call.

        Returns an (n_samples, n_channels) float array, or None if no new
        complete kilobyte is available yet.
        '''
        available_kb = self.capture_bytes() // 1024
        if available_kb <= self._cap_cursor_kb:
            return None

        blocks = []
        while self._cap_cursor_kb < available_kb:
            n_kb = min(self.GET_CHUNK_KB, available_kb - self._cap_cursor_kb)
            blocks.append(self._capture_get(self._cap_cursor_kb, n_kb))
            self._cap_cursor_kb += n_kb

        raw = b''.join(blocks)
        # SR860 capture data is little-endian float32, channels interleaved
        n_floats = len(raw) // 4
        data = np.array(struct.unpack(f'<{n_floats}f', raw[:n_floats * 4]))
        usable = (len(data) // self._cap_channels) * self._cap_channels
        return data[:usable].reshape(-1, self._cap_channels)


class SigGen():
    '''Access signal generator for discharge control'''


    def __init__(self, settings):
        '''Start connection over telnet'''
        self.ip = settings['siggen_ip']
        self.port = 5025

        try:
            self.tn = Telnet(self.ip, port=self.port, timeout=2)
            outp = self.tn.read_until(bytes("\r", 'ascii'),2).decode('ascii')

        except Exception as e:
            print(f"Signal generator connection failed on {self.ip}: {e}")

        self.init_settings()

    def init_settings(self):
        '''Set initial settings'''

        self.tn.write(bytes(f"ENBL 0\r", 'ascii'))            # BNC output off
        #self.tn.write(bytes(f"AMPR 0.1 Vpp\r", 'ascii'))      # N output to 0.1 Vpp
        #self.tn.write(bytes(f"FREQ 12 MHz\r", 'ascii'))       # Freq start at 12 MHz
        #self.tn.write(bytes(f"ENBR 1\r", 'ascii'))            # N output on
        #self.tn.write(bytes(f"TYPE 0\r", 'ascii'))            # AM modulation
        #self.tn.write(bytes(f"ADEP 50.0\r", 'ascii'))         # Modulation to 50% depth
        #self.tn.write(bytes(f"MFNC 0\r", 'ascii'))            # Modulation is sine wave
        #self.tn.write(bytes(f"RATE 1 kHz\r", 'ascii'))        # Modulation to 1 kHz
        #self.tn.write(bytes(f"MODL 1\r", 'ascii'))            # Modulation ON

    def __del__(self):
        #self.tn.close()
        pass

    def set_freq(self, freq):
        '''Set RF frequency in MHz'''
        self.tn.write(bytes(f"FREQ {freq} MHz\r", 'ascii'))

    def set_amp(self, amp):
        '''Set amplitude in volt peak to peak'''
        self.tn.write(bytes(f"AMPR {amp} Vpp\r", 'ascii'))

    def enable_n(self, on):
        """Turn on or off type N output"""
        b = 1 if on else 0
        self.tn.write(bytes(f"ENBR {b}\r", 'ascii'))

class Keopsys():
    '''Controls for Keopsys pump laser'''

    def __init__(self, settings):
        '''Start connection over telnet'''
        self.ip = settings['siggen_ip']
        self.port = 5025

        try:
            self.tn = Telnet(self.ip, port=self.port, timeout=2)
            outp = self.tn.read_until(bytes("\r", 'ascii'), 2).decode('ascii')

        except Exception as e:
            print(f"Keopsys laser connection failed on {self.ip}: {e}")



# class LabJack():
#     '''Access LabJack device
#     '''
#
#     def __init__(self, settings):
#         '''Open connection to LabJack
#         '''
#         ip = settings['labjack_ip']
#         try:
#             self.lj = ljm.openS("T4", "TCP", ip)
#         except Exception as e:
#             print(f"Connection to LabJack failed on {ip}: {e}")
#
#
#
#     def read_back(self):
#         '''Read voltage in
#         '''
#         aNames = ["AIN0","AIN1"]
#         return ljm.eReadNames(self.lj, len(aNames), aNames)
#
#     def __del__(self):
#         '''Close on delete'''
#         ljm.close(self.lj)

