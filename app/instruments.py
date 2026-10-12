'''PyMEOP J.Maxwell 2021
'''
from PyQt5.QtCore import QThread, pyqtSignal, Qt
# from labjack import ljm
from telnetlib3 import Telnet    # stdlib telnetlib removed in Python 3.13
import time
import math

            
class ProbeLaser():      
    '''Access Probe laser over telnet
    '''
    
    
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
            type_code = 56 if 'temp' in type else 63  # 56 is temp, 63 or current
            self.tn.write(bytes(f"(param-set! 'laser1:wide-scan:output-channel {type_code})\n", 'ascii'))
            outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
            self.tn.write(bytes(f"(param-set! 'laser1:wide-scan:scan-begin {begin})\n", 'ascii'))
            outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
            self.tn.write(bytes(f"(param-set! 'laser1:wide-scan:scan-end {end})\n", 'ascii'))
            outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
            mode_str = "#t" if mode else "#f"
            self.tn.write(bytes(f"(param-set! 'laser1:wide-scan:continuous-mode {mode_str})\n", 'ascii'))
            outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
            self.tn.write(bytes(f"(param-set! 'laser1:wide-scan:shape {shape})\n", 'ascii'))
            outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
            self.tn.write(bytes(f"(param-set! 'laser1:wide-scan:speed {speed})\n", 'ascii'))
            outp = self.tn.read_until(bytes(">", 'ascii'),2).decode('ascii')
            return True
        except Exception as e:
            print(f"Scan config failed: {e}")
            return False


    def start_scan(self):
        """ Start wide scan
        """
        try:
            self.tn.write(bytes(f"(param-set! 'laser1:wide-scan:start)\n", 'ascii'))
        except Exception as e:
            print(f"Start scan failed: {e}")
            return False

    def stop_scan(self):
        """ Start wide scan
        """
        try:
            self.tn.write(bytes(f"(param-set! 'laser1:wide-scan:stop)\n", 'ascii'))
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
    '''Access SR860 lock-in amplifier'''

    TIME_CONSTANTS = [m * 10**e for e in range(-6, 5) for m in (1, 3)]   # OFLT index: 1 us .. 30 ks
    SLOPES = [6, 12, 18, 24]                                             # OFSL index, dB/oct
    SENSITIVITIES = [m * 10**e for e in range(0, -10, -1) for m in (1, 0.5, 0.2)][:28]   # SCAL index: 1 V .. 1 nV
    INPUT_RANGES = [1, 0.3, 0.1, 0.03, 0.01]                             # IRNG index, V

    # Settings that never change, in the order they are set: (command, value)
    FIXED = [
        ('RSRC', 1),      # external reference
        ('RTRG', 0),      # sine trigger
        ('REFZ', 0),      # 50 Ohm reference input
        ('HARM', 1),      # detect at the fundamental
        ('IVMD', 0),      # voltage input
        ('ISRC', 0),      # A, not A-B
        ('ICPL', 0),      # AC coupled
        ('IGND', 0),      # floating shield
        ('IRNG', 1),      # 300 mV input range
        ('SCAL', 2),      # 200 mV sensitivity
        ('SYNC', 0),      # sync filter off
        ('ADVFILT', 0),   # advanced filter off
        ('COFA 0,', 0), ('COFA 1,', 0), ('COFA 2,', 0),   # X, Y, R offsets off
        ('CEXP 0,', 0), ('CEXP 1,', 0), ('CEXP 2,', 0),   # X, Y, R expand x1
        ('CRAT 0,', 0), ('CRAT 1,', 0), ('CRAT 2,', 0),   # X, Y, R ratio off
    ]

    def __init__(self, settings):
        '''Start connection over telnet, then set the lock-in to its nominal settings'''
        self.ip = settings['lockin_ip']
        self.port = 23

        try:
            self.tn = Telnet(self.ip, port=self.port, timeout=5)
            outp = self.tn.read_until(bytes("\r", 'ascii'),2).decode('ascii')

        except Exception as e:
            print(f"Lock-in connection failed on {self.ip}: {e}")
            return

        try:
            self.configure(settings)
        except Exception as e:
            print(f"Lock-in configuration failed: {e}")

    def __del__(self):
        #self.tn.close()
        pass

    def query(self, cmd):
        '''Send a query and return the reply, retrying once if it goes unanswered'''
        for attempt in range(2):
            self.tn.read_very_eager()       # drop any late reply to an earlier query
            self.tn.write(bytes(f"{cmd}\r", 'ascii'))
            _, _, outp = self.tn.expect([rb"\r", rb"\n"], 2)
            outp = outp.decode('ascii').strip()
            if outp:
                return outp
        raise TimeoutError(f"no reply to {cmd}")

    def set(self, cmd, value, pause=0.05):
        '''Set one parameter and read it back. A command ending in a comma takes a channel
        argument, e.g. "COFA 0," sets with "COFA 0, value" and reads with "COFA? 0".
        Returns True if the read back matches.
        '''
        name, _, channel = cmd.partition(' ')
        self.tn.write(bytes(f"{cmd} {value}\r" if not channel else f"{cmd}{value}\r", 'ascii'))
        time.sleep(pause)
        readback = self.query(f"{name}? {channel.rstrip(',')}".strip())
        if int(float(readback)) != value:
            print(f"Lock-in {cmd.rstrip(',')} set to {value} but reads back {readback}")
            return False
        return True

    def configure(self, settings):
        '''Set the lock-in to the nominal settings for the stepped DAQ. Phase is left alone.'''
        ok = all([self.set(cmd, value) for cmd, value in self.FIXED])

        tc = settings['lockin_tc_stepped']
        tc_index = min(range(len(self.TIME_CONSTANTS)), key=lambda i: abs(math.log(self.TIME_CONSTANTS[i]/tc)))
        if not math.isclose(self.TIME_CONSTANTS[tc_index], tc, rel_tol=0.01):
            print(f"Lock-in time constant {tc} s is not an SR860 setting, using {self.TIME_CONSTANTS[tc_index]:g} s")
        ok &= self.set('OFLT', tc_index, pause=0.3)    # changing the time constant reconfigures the filters
        ok &= self.set('OFSL', self.SLOPES.index(settings['lockin_slope_stepped']))

        freq = float(self.query('FREQDET?'))
        if not math.isclose(freq, settings['lockin_ref_freq'], rel_tol=0.01):
            print(f"Lock-in reference at {freq} Hz, expected {settings['lockin_ref_freq']} Hz. Is the discharge modulation on?")
            ok = False
        if int(self.query('ILVL?')) >= 4:
            print("Lock-in input is overloaded (signal strength 4 of 4)")
            ok = False

        if ok:
            print(f"Lock-in configured: {self.TIME_CONSTANTS[tc_index]:g} s, {settings['lockin_slope_stepped']} dB/oct, reference {freq} Hz")
        return ok

    def read_settings(self):
        '''Read back the settings that shape the signal, to record with each event.
        Read rather than assumed, so a change made on the front panel is captured.

        Returns:
            dict of sens_V, tc_s, slope_dB_oct and range_V, or None if the read failed
        '''
        try:
            return {
                'sens_V': self.SENSITIVITIES[int(self.query('SCAL?'))],
                'tc_s': self.TIME_CONSTANTS[int(self.query('OFLT?'))],
                'slope_dB_oct': self.SLOPES[int(self.query('OFSL?'))],
                'range_V': self.INPUT_RANGES[int(self.query('IRNG?'))],
            }
        except Exception as e:
            print(f"Lock-in settings read failed: {e}")
            return None

    def read_all(self):
        '''Returns lock-in x, y, r as strings in V
        '''
        # SNAP? names its parameters, so a changed front-panel display can't change what is read
        self.tn.write(bytes(f"SNAP? 0,1,2\r", 'ascii'))
        try:
            x, y, r = self.tn.read_until(bytes("\r", 'ascii'),2).decode('ascii').split(',')
        except Exception as e:
            print(f"Lock read failed: {e}")
        return x, y, r
        
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

    def query(self, command):
        '''Send a query, return the reply as a string'''
        self.tn.read_very_eager()    # drop anything left unread
        self.tn.write(bytes(f"{command}\r", 'ascii'))
        return self.tn.read_until(bytes("\n", 'ascii'), 2).decode('ascii').strip()

    def read_freq(self):
        '''Read RF frequency in MHz'''
        return float(self.query("FREQ?")) / 1e6    # replies in Hz

    def read_amp(self):
        '''Read type N amplitude in volt peak to peak, into 50 ohm'''
        dbm = float(self.query("AMPR?"))    # replies in dBm
        vrms = math.sqrt(50 * 1e-3 * 10**(dbm / 10))
        return 2 * math.sqrt(2) * vrms

    def enable_n(self, on):
        """Turn on or off type N output"""
        b = 1 if on else 0
        self.tn.write(bytes(f"ENBR {b}\r", 'ascii'))

    def read_modulation(self):
        '''Read modulation settings, to put back with restore_modulation()'''
        return {cmd: self.query(f"{cmd}?") for cmd in ("TYPE", "MFNC", "RATE", "MODL")}

    def restore_modulation(self, saved):
        '''Put back modulation settings from read_modulation(); type first, since
        function and rate apply to the type set'''
        for cmd in ("TYPE", "MFNC", "RATE", "MODL"):
            self.tn.write(bytes(f"{cmd} {saved[cmd]}\r", 'ascii'))

    def set_fm(self, dev, rate, wave=1):
        '''Frequency modulate, sweeping the carrier by dev either side of it
        Arguments:
            dev: deviation in kHz
            rate: sweeps per second, Hz
            wave: modulation function, 1 ramp or 2 triangle
        Returns the deviation set in kHz, which the SG380 limits by carrier frequency
        '''
        self.tn.write(bytes(f"TYPE 1\r", 'ascii'))             # FM
        self.tn.write(bytes(f"MFNC {wave}\r", 'ascii'))        # 1 ramp, 2 triangle
        self.tn.write(bytes(f"FDEV {dev} kHz\r", 'ascii'))
        self.tn.write(bytes(f"RATE {rate} Hz\r", 'ascii'))
        self.tn.write(bytes(f"MODL 1\r", 'ascii'))             # modulation on
        return float(self.query("FDEV?")) / 1e3                # replies in Hz

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
            