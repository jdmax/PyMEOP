'''PyMEOP J.Maxwell 2021
'''
from PyQt5.QtCore import QThread, pyqtSignal, Qt
# from labjack import ljm
import telnetlib
import time
from srsinst.sr860 import SR860
            
class ProbeLaser():      
    '''Access Probe laser over telnet
    '''
    
    
    def __init__(self, settings):
        '''Open connection to Toptica DLC controller
        '''  
        self.ip = settings['probe_ip']
        self.port = 1998
        
        try:
            self.tn = telnetlib.Telnet(self.ip, port=self.port, timeout=2)
            
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
            type_code = 56 if 'temp' in type else 63  # 56 is temp, 63 start(0,0)or current
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
            self.tn = telnetlib.Telnet(self.ip, port=self.port, timeout=4)
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
    '''Access lock-in meter'''

    def __init__(self, ip):
        '''Start connection to lockin with SRS driver'''
        self.ip = ip
        self.port = 23

        try:
            self.lockin = SR860('vxi11', self.ip)
        except Exception as e:
            print(f"Lock-in connection failed on {self.ip}: {e}")

    def read_all(self):
        '''Returns both all four lock-in parameters as x,y,r,theta
        '''
        x, y, r, theta = self.lockin.data.get_channel_values()
        return x, y, r

    def capture_start(self):
        """Configure and start SRS 860 capture mode. Configures for max buffer size, assuming it will be stopped before full
        """
        self.lockin.capture.buffer_size_in_kilobytes = 8
        self.lockin.capture.config = 'XYRT'
        self.lockin.capture.rate_divisor_exponent = 10  # 1 ms time constant give 78 kHz, this is divisor exponent
        self.lockin.capture.start(0,0)
        #time.sleep(0.5)
        print(self.lockin.capture.data_size_in_bytes)

    def capture_stop(self):
        """Configure and start SRS 860 capture mode. Configures for max buffer size, assuming it will be stopped before full
        """
        self.lockin.capture.stop()
        print(self.lockin.capture.data_size_in_kilobytes)
        print(self.lockin.capture.get_data(0))
        print(self.lockin.capture.get_data(1))
        print(self.lockin.capture.get_data(2))
        data = self.lockin.capture.get_all_data()
        # print(data[0])
        return data[2] # return all r



class LockInOld():
    '''Access lock-in meter'''

    def __init__(self, ip):
        '''Start connection over telnet'''
        self.ip = ip
        self.port = 23

        try:
            self.tn = telnetlib.Telnet(self.ip, port=self.port, timeout=5)
            outp = self.tn.read_until(bytes("\r", 'ascii'), 2).decode('ascii')

        except Exception as e:
            print(f"Lock-in connection failed on {self.ip}: {e}")

    def __del__(self):
        #self.tn.close()  
        pass
        
    def read_all(self):
        '''Returns both all four lock-in parameters as x,y,r,theta
        '''
        self.tn.write(bytes(f"SNAPD?\r", 'ascii'))
        x, y, r, th = self.tn.read_until(bytes("\r", 'ascii'),2).decode('ascii').split(',')
        return x, y, r

    def capture_start(self):
        """Configure and start SRS 860 capture mode. Configures for max buffer size, assuming it will be stopped before full
        """
        try:
            self.tn.write(bytes(f"CAPTURELEN 4096; CAPTURECFG 2; CAPTURERATE 0\r", 'ascii'))
            self.tn.write(bytes(f"CAPTURESTART 0, 0\r", 'ascii'))  # start one-shot immediately
            print("Started Lock-in capture")
            self.tn.write(bytes(f"CAPTUREBYTES?\r", 'ascii'))  # start one-shot immediately
            #outp = self.tn.read_until(bytes("\r", 'ascii'),1).decode('ascii')
            outp = self.tn.read_some().decode('ascii')
            print("out",outp)
        except Exception as e:
            print(f"Lock-in capture start failed: {e}")

    def capture_stop(self):
        """Stop capture and return data
        """
        data = [] # list of tups, tup is (X, Y, R, theta)
        buffer = b'\x00'
        try:
            self.tn.write(bytes(f"CAPTURESTOP\r", 'ascii'))
            self.tn.write(bytes(f"CAPTUREPROG?\r", 'ascii'))
            kb = int(self.tn.read_until(bytes("\r", 'ascii'),2).decode('ascii'))
            print("kB",kb)

            for i in range(0, kb, 2):
                self.tn.write(bytes(f"CAPTUREGET? {i}, 2\r", 'ascii'))   # get 32 kbytes of data at offset i
                block = self.tn.read_until(bytes("\r", 'ascii'),5)
                print("block", block)
                digits = block[1] -48
                print("digits",digits)
                offset = digits + 2
                data_size = (len(block) - offset)//4
                print("size",data_size)
                unpack_format = '>{}f'.format(data_size)
                vals = unpack_from(unpack_format, block, offset)
                print("vals",vals)

        except Exception as e:
            print(f"Lock-in capture start failed: {e}")

        
class SigGen():
    '''Access signal generator for discharge control'''
    

    def __init__(self, settings):
        '''Start connection over telnet'''        
        self.ip = settings['siggen_ip']
        self.port = 5025
 
        try:
            self.tn = telnetlib.Telnet(self.ip, port=self.port, timeout=2)
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


class Keopsys():
    '''Controls for Keopsys pump laser'''

    def __init__(self, settings):
        '''Start connection over telnet'''
        self.ip = settings['siggen_ip']
        self.port = 5025

        try:
            self.tn = telnetlib.Telnet(self.ip, port=self.port, timeout=2)
            outp = self.tn.read_until(bytes("\r", 'ascii'), 2).decode('ascii')

        except Exception as e:
            print(f"Signal generator connection failed on {self.ip}: {e}")

        self.init_settings()

        
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
    # def __del__(self):
        # '''Close on delete'''
        # ljm.close(self.lj) 
            