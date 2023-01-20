import serial
from serial.tools.list_ports import comports
import time
import re


class MagnetControl():
    '''Talks to Magnet PS via serial, contains magnet state attributes'''
    
    def __init__(self):
    
        self.status = {}  # all magnet parameters, their query strings and description
        
        self.status.update({ 'current' :    { 'value' : '0', 'query' : "IMAG?",     'text' : 'Magnet Current (A)'}})
        self.status.update({ 'ps_current' : { 'value' : '0', 'query' : "IOUT?",     'text' : 'Power Supply Current (A)'}})
        self.status.update({ 'v_mag' :      { 'value' : '0', 'query' : "VMAG?",     'text' : 'Voltage (V)'}})
        self.status.update({ 'up_lim' :     { 'value' : '0', 'query' : "ULIM?",     'text' : 'Upper Limit (A)'}})
        self.status.update({ 'low_lim' :    { 'value' : '0', 'query' : "LLIM?",     'text' : 'Lower Limit (A)'}})
        self.status.update({ 'sweep' :      { 'value' : '0', 'query' : "SWEEP?",    'text' : 'Sweep Status'}})
        self.status.update({ 'switch' :     { 'value' : '0', 'query' : "PSHTR?",    'text' : 'Switch Heater Status'}})
       # self.status.update({ 'id' :         { 'value' : '0', 'query' : "*IDN?",     'text' : 'Device ID'}})


        self.commands = {       # command strings 
            'low_lim' : "LLIM ",
            'up_lim' : "ULIM ",
            'ps_on' : "PSHTR ON",
            'ps_off' : "PSHTR OFF",
            'sw_up' : "SWEEP UP",
            'sw_down' : "SWEEP DOWN",
            'sw_pause' : "SWEEP PAUSE",
            'sw_zero' : "SWEEP ZERO",
            'complete' : "OPC?"
            }
        self.fast_mode = ''    
        self.s = serial.Serial()
        self.open_port()
        self.current_regex = re.compile('(\d+.\d+)')   


    def open_port(self):
        '''Open serial connection'''
        #port = "/dev/ttyUSB1"
        self.port = 'COM11'
        self.s=serial.Serial(self.port, baudrate=9600, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=0.1) 

    def read_port(self):
        '''Read from serial port until encounter space'''
        message = ""
        while True:
            message1 = self.s.readline().decode("utf-8") 
            message2 = self.s.readline().decode("utf-8")    
            return message2

    def write_port(self, string):
        '''Write to serial'''
        self.s.flushInput()
        self.s.flushOutput()
        self.s.write((string+"\n").encode())
        
    def read_current_mag(self):
        self.write_port('IMAG?')
        data = self.read_port()
        m = self.current_regex.search(data)
        return float(m.group())
        
    def read_current_pow(self):
        self.write_port('IOUT?')
        data = self.read_port()
        m = self.current_regex.search(data)
        return float(m.group())
        
    def set_llim(self, value):
        self.write_port(f"LLIM {value}")
        self.write_port(f"LLIM?")
        data = self.read_port()
        m = self.current_regex.search(data)
        return float(m.group())
    
    def set_ulim(self, value):
        self.write_port(f"ULIM {value}")
    
    
    
    
    def fast(self, bool):
        '''Select fast for sweep mode'''
        if bool:
            self.fast_mode = ' FAST'
        else:
            self.fast_mode = ''

    def toggle(self):
        '''Toggle switch heater'''
        if '0' in self.status['switch']:
            self.write_port(self.commands['ps_on'])
        else:
            self.write_port(self.commands['ps_off'])
            
        self.read_all()    
            
     
    def get_ports(self):
        '''Return available serial ports. Port is tuple: (port, desc, hwid)'''
        return sorted(comports())
        
    def set_port(self, port):
        self.port = port
    
    def close_port(self):
        '''Close serial connection'''
        self.s.close()
        
   
        if self.s.is_open:
            self.read_all()
            self.write_port('REMOTE')
        
    def close_port(self):
        if self.s.is_open:
            self.write_port('LOCAL')
            self.s.close()
        
                  
    def read_all(self):
        '''Read all magnet parameters and write to instance state attribute'''
        keys = sorted(self.status.keys())
        command_string = ';'.join([self.status[x]['query'] for x in keys])
        self.write_port(command_string)         
        time.sleep(0.1)
        replies = self.read_port().split(';')
        for key, value in zip(keys,replies):
            self.status[key]['value'] = value 
      
        self.s.flushInput()
        self.s.flushOutput()  
        
        
       
        

        
                



