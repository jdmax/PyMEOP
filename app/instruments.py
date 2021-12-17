'''J.Maxwell 2021
'''
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from toptica.lasersdk.dlcpro.v2_0_3 import DLCpro, NetworkConnection, DeviceNotFoundError, DecopError, UserLevel
from labjack import ljm
import telnetlib

            
class ProbeLaser():      
    '''Access Toptica Laser SDK to control probe laser    
    '''
    
    
    def __init__(self, settings):
        '''Open connection to Toptica DLC controller
        '''  
        self.ip = settings['probe_ip']
                
        with DLCpro(NetworkConnection(self.ip)) as self.dlc:
            print("This is a {} with serial number {}.\n".format(
                self.dlc.laser1.dl.cc.current_set.get(), self.dlc.uptime.get()))
        
        
        
    def set_current(self, current):
    
        with DLCpro(NetworkConnection(self.ip)) as self.dlc:
            self.dlc.laser1.dl.cc.current_set.set(float(current))
            return self.dlc.laser1.dl.cc.current_set.get()
        
        
    def set_temp(self, temp):
        with DLCpro(NetworkConnection(self.ip)) as self.dlc:
            self.dlc.laser1.dl.tc.temp_set.set(float(temp))
            return self.dlc.laser1.dl.tc.temp_set.get()
        return value

	
        # with Client(NetworkConnection(ip)) as client:
            # print("This is a {} with serial number {}.\n".format(
                # client.get('system-type'), client.get('serial-number')))
                
            # print(f"Current: {client.get('laser1:dl:cc:current-set')}")            
            # client.set('laser1:dl:cc:current-set', 135)            
            # print(f"Current: {client.get('laser1:dl:cc:current-set')}")  
            
            
            # print(f"Temp: {client.get('laser1:dl:tc:temp-set')}") 
            # client.set('laser1:dl:tc:temp-set', 20)
            # print(f"Temp: {client.get('laser1:dl:tc:temp-set')}") 
		
        
        
class WavelengthMeter():
    '''Access Wavelength meter'''
    

    def __init__(self, settings):
        '''Start connection over telnet'''        
        self.ip = settings['meter_ip']
        self.port = 5025
 
        try:
            tn = telnetlib.Telnet(self.ip, port=self.port, timeout=2)
                        
            #tn.write(bytes(f"FREQ {config.channel['cent_freq']*1000000}\n", 'ascii'))
            tn.write(bytes("*idn?\n", 'ascii'))
            outp = tn.read_some().decode('ascii')
            print(outp)
            
            tn.close()
            
        except Exception as e:
            print(f"Meter connection failed on {self.host}: {e}")
        
        
        
class LabJack():      
    '''Access LabJack device 
    '''
    
    def __init__(self, settings):
        '''Open connection to LabJack
        '''  
        ip = settings['labjack_ip']
        try:
            self.lj = ljm.openS("T4", "TCP", ip) 
        except Exception as e:
            print(f"Connection to LabJack failed on {ip}: {e}")

        
    
    def read_back(self):
        '''Read voltage in
        '''
        aNames = ["AIN0","AIN1"]
        return ljm.eReadNames(self.lj, len(aNames), aNames)
        
    # def __del__(self):
        # '''Close on delete'''
        # ljm.close(self.lj) 
            