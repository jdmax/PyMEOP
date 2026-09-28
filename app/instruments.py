'''PyMEOP J.Maxwell 2021
'''
import re
import math
import sys
import time

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal, Qt
# from labjack import ljm
from telnetlib3 import Telnet    # stdlib telnetlib removed in Python 3.13

# python-vxi11, which srsinst.sr860 imports, still uses xdrlib. That left the
# standard library in 3.13; xdrlib3 is the same code under a new name, so it
# stands in -- the same shape of fix as telnetlib3 above. This has to happen
# before srsinst is imported.
if 'xdrlib' not in sys.modules:
    try:
        import xdrlib3
        sys.modules['xdrlib'] = xdrlib3
    except ImportError:
        pass        # a real xdrlib is still there on older Pythons

from srsinst.sr860 import SR860


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



TERMINATORS = {'CR': bytes([13]), 'LF': bytes([10]), 'CRLF': bytes([13, 10])}


def _term_bytes(name):
    """Terminator bytes for a config name: CR, LF or CRLF.

    Named rather than escaped because a backslash escape survives neither
    YAML quoting nor a trip through the shell reliably, and silently sending
    the wrong terminator looks like a dead instrument.
    """
    if isinstance(name, bytes):
        return name
    key = str(name).strip().upper()
    if key in TERMINATORS:
        return TERMINATORS[key]
    raise ValueError(f"lockin_term must be one of {sorted(TERMINATORS)}, "
                     f"got {name!r}")


class LockIn():
    """SR860 lock-in, driven through Stanford Research Systems' own package.

    Everything below the public methods is srsinst.sr860. The capture buffer
    protocol is fiddly -- the block framing, the argument spacing, when the
    buffer may be read -- and hand-rolling it cost a long run of bench
    failures for no benefit. SRS ship a tested implementation; this class
    exists only to keep the interface the rest of PyMEOP already uses.

    One thing worth recording from that episode: srsgui terminates commands
    with LF. This code previously sent CR, which the SR860 answers for text
    queries but evidently not for a binary CAPTUREGET?.
    """

    CHANNELS = {'X': 1, 'XY': 2, 'RT': 2, 'XYRT': 4}   # columns per config
    settle = 0.3        # filters need a moment after the time constant changes

    def __init__(self, settings):
        """Connect over the interface named by lockin_interface in the config.

        'tcpip' is the telnet port the rest of the lab uses; 'vxi11' is the
        transport in SRS's own Ethernet example, worth trying if the capture
        transfer misbehaves.
        """
        self.ip = settings['lockin_ip']
        self.port = int(settings.get('lockin_port', 23))
        self.interface = settings.get('lockin_interface', 'tcpip')
        self.lockin = None
        self._channels = 2

        try:
            self.lockin = SR860()
            if self.interface == 'vxi11':
                self.lockin.connect('vxi11', self.ip)
            else:
                self.lockin.connect('tcpip', self.ip, self.port)
                # srsgui terminates with LF, but the SR860's telnet terminator
                # is set on its front panel and this unit answers CR. A
                # mismatch is silent: the connection banner still appears and
                # then every command is ignored until the query times out.
                self.lockin.comm.set_term_char(_term_bytes(
                    settings.get('lockin_term', 'CR')))
        except Exception as e:
            print(f"Lock-in connection failed on {self.ip}: {e}")
            self.lockin = None

    def __del__(self):
        try:
            self.lockin.disconnect()
        except Exception:
            pass

    @property
    def connected(self):
        try:
            return bool(self.lockin) and self.lockin.is_connected()
        except Exception:
            return False

    def query(self, cmd):
        """Send a query and return the reply text, for diagnostics."""
        return self.lockin.query_text(cmd)

    def command(self, cmd):
        self.lockin.send(cmd)

    # -- configuration -----------------------------------------------------

    @staticmethod
    def _nearest(value, choices):
        """Closest available setting, compared logarithmically over decades."""
        value = float(value)
        if value <= 0:
            return min(choices, key=lambda c: abs(c - value))
        return min(choices, key=lambda c: abs(math.log(c / value)) if c > 0
                   else float('inf'))

    def configure(self, tc=None, slope=None, sync=None):
        """Set time constant (s), filter slope (dB/oct) and sync filter.

        Returns the configuration read back from the instrument rather than
        the values that were requested.
        """
        signal = self.lockin.signal
        # The settings are descriptors: reading one off the instance gives the
        # instrument's value, so the table of allowed values has to come from
        # the class.
        allowed = type(signal)
        if tc is not None:
            choice = self._nearest(tc, allowed.time_constant.set_dict)
            signal.time_constant = choice
            if abs(choice - tc) / float(tc) > 0.01:
                print(f"Lock-in TC {tc} s not available, using {choice} s")
        if slope is not None:
            choice = self._nearest(slope, allowed.filter_slope.set_dict)
            signal.filter_slope = choice
            if choice != slope:
                print(f"Lock-in slope {slope} dB/oct not available, "
                      f"using {choice} dB/oct")
        if sync is not None:
            signal.sync_filter = 'on' if sync else 'off'

        time.sleep(self.settle)
        return self.get_config()

    def get_config(self):
        """Read back the settings that shape a swept spectrum.

        This goes into every event file, so a scan stays interpretable after
        the settings have moved on. Read one at a time so an unsupported or
        slow query names itself rather than taking the whole readback down.
        """
        config = {}
        signal = self.lockin.signal
        for key, read in (
                ('tc', lambda: float(signal.time_constant)),
                ('slope_db', lambda: int(signal.filter_slope)),
                ('sync', lambda: str(signal.sync_filter) == 'on'),
                ('sensitivity', lambda: float(signal.voltage_sensitivity)),
                ('enbw', lambda: float(signal.equivalent_noise_bandwidth)),
                ('ref_freq', lambda: float(self.lockin.ref.detection_frequency))):
            try:
                config[key] = read()
            except Exception as e:
                print(f"Lock-in could not read {key} ({type(e).__name__}: {e})")

        if 'slope_db' in config and 'tc' in config:
            # group delay of an n-pole filter is n * tau
            poles = config['slope_db'] // 6
            config['poles'] = int(poles)
            config['group_delay'] = poles * config['tc']
        return config

    def read_all(self):
        """Return lock-in x, y, r as a single snapshot."""
        try:
            x, y, r = self.lockin.data.get_values(0, 1, 2)
            return x, y, r
        except Exception as e:
            print(f"Lock read failed: {e}")
            return 0, 0, 0

    # -- data capture ------------------------------------------------------

    @property
    def capture(self):
        return self.lockin.capture

    def capture_rate_max(self):
        """Maximum capture rate in Hz, which depends on the time constant."""
        return float(self.capture.max_rate)

    def capture_config(self, target_rate, channels='XY', seconds=None):
        """Configure the capture buffer.

        The rate is a power-of-two divisor of the maximum, so the nearest
        available one is set and then read back -- every sample's position on
        the current axis is derived from it.

        Returns (actual_rate, n_channels, buffer_kb).
        """
        nch = self.CHANNELS[channels]
        self.capture.config = channels

        rate_max = self.capture_rate_max()
        n = int(round(math.log2(rate_max / float(target_rate))))
        n = max(0, min(20, n))
        self.capture.rate_divisor_exponent = n
        actual_rate = float(self.capture.rate)

        nbytes = actual_rate * (seconds if seconds else 1.0) * nch * 4
        kb = int(math.ceil(nbytes / 1024.0)) + 2       # margin for rounding
        kb += kb % 2        # internal blocks are 2 kB, odd lengths round up
        kb = max(2, min(4096, kb))
        self.capture.buffer_size_in_kilobytes = kb

        self._channels = nch
        return actual_rate, nch, kb

    def capture_start(self, continuous=False, triggered=False):
        self.capture.start(1 if continuous else 0, 1 if triggered else 0)

    def capture_status(self):
        """Capture state as a bit field: 1 running, 2 triggered, 4 wrapped."""
        return int(self.capture.state)

    def capture_stop(self, wait=True, timeout=3.0):
        """Stop the capture, by default waiting until it has really stopped.

        CAPTURESTOP halts at the next 2 kB block boundary and the running bit
        stays set until that block has filled, so returning as soon as the
        command is sent can leave the buffer briefly unreadable.
        """
        self.capture.stop()
        if not wait:
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if not (self.capture_status() & 1):
                    return True
            except Exception as e:
                print(f"Could not read capture status ({e}); reading anyway")
                return False
            time.sleep(0.02)
        print(f"Capture still reports running {timeout:.1f} s after stopping")
        return False

    def capture_bytes(self):
        """Bytes captured so far. Live, so it can follow a sweep in progress."""
        return int(self.capture.data_size_in_bytes)

    def capture_read_all(self):
        """Read the whole capture buffer as an (n_samples, n_channels) array.

        srsinst returns one row per channel; the rest of PyMEOP works in
        samples by channels, so the result is transposed.
        """
        data = self.capture.get_all_data()
        if data is None or not len(data):
            return None
        return np.asarray(data).T


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

