'''PyNMR, J.Maxwell 2020
'''
import datetime
import time
import math
from PyQt5.QtWidgets import QWidget, QLabel, QGroupBox, QHBoxLayout, QVBoxLayout, QGridLayout, QLineEdit, QSpacerItem, QSizePolicy, QComboBox, QPushButton, QProgressBar
from PyQt5.QtGui import QIntValidator, QDoubleValidator, QValidator
from PyQt5.QtCore import QThread, pyqtSignal, Qt
import pyqtgraph as pg
import numpy as np
 
   
class FindTab(QWidget):
    '''Creates main tab. Starts threads for run and to update plots'''
    def __init__(self, parent):
        super(QWidget,self).__init__(parent)
        self.__dict__.update(parent.__dict__)
        
        self.parent = parent
               
        # pyqtgrph styles        
        pg.setConfigOptions(antialias=True)
        self.abs_pen = pg.mkPen(color=(250, 0, 0), width=1.5)
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        
        
        # Populate Run Tab
        self.main = QHBoxLayout()            # main layout
        self.setLayout(self.main)
        self.left = QVBoxLayout()     # left part of main layout
        self.main.addLayout(self.left)
               
        # Find discharge box
        self.discharge_box = QGroupBox('Discharge')
        self.discharge_box.setLayout(QGridLayout())
        self.left.addWidget(self.discharge_box)
        
        self.freq_lo_label = QLabel('Frequency Range (MHz):')
        self.discharge_box.layout().addWidget(self.freq_lo_label, 0, 0)
        self.freq_lo_edit =  QLineEdit()
        self.freq_lo_edit.setValidator(QDoubleValidator(1, 100, 3, notation=QDoubleValidator.StandardNotation))
        self.discharge_box.layout().addWidget(self.freq_lo_edit, 0, 1)
        self.freq_up_edit =  QLineEdit()
        self.freq_up_edit.setValidator(QDoubleValidator(1, 100, 3, notation=QDoubleValidator.StandardNotation))
        self.discharge_box.layout().addWidget(self.freq_up_edit, 0, 2)
        
        self.volt_lo_label = QLabel('Voltage Range:')
        self.discharge_box.layout().addWidget(self.volt_lo_label, 1, 0)
        self.volt_lo_edit =  QLineEdit()
        self.volt_lo_edit.setValidator(QDoubleValidator(0, 1, 3, notation=QDoubleValidator.StandardNotation))
        self.discharge_box.layout().addWidget(self.volt_lo_edit, 1, 1)        
        self.volt_up_edit =  QLineEdit()
        self.volt_up_edit.setValidator(QDoubleValidator(0, 1, 3, notation=QDoubleValidator.StandardNotation))
        self.discharge_box.layout().addWidget(self.volt_up_edit, 1, 2)
        
        self.dis_button = QPushButton("Run Discharge Search")      
        self.discharge_box.layout().addWidget(self.dis_button, 2, 2)
        self.dis_button.clicked.connect(self.dis_pushed)
        
        
        
        # Find Temp peaks box
        self.scan_temp_box = QGroupBox('Scan Temperature Peaks')
        self.scan_temp_box.setLayout(QGridLayout())
        self.left.addWidget(self.scan_temp_box)
        
        self.temp_label = QLabel('Temperature Range:')
        self.scan_temp_box.layout().addWidget(self.temp_label, 0, 0)
        self.temp_edit1 =  QLineEdit()
        self.scan_temp_box.layout().addWidget(self.temp_edit1, 0, 1)
        self.temp_edit2 =  QLineEdit()
        self.scan_temp_box.layout().addWidget(self.temp_edit2, 0, 2)
        self.step_label = QLabel('Number of Steps:')
        self.scan_temp_box.layout().addWidget(self.step_label, 1, 0)
        self.step_edit =  QLineEdit()
        self.scan_temp_box.layout().addWidget(self.step_edit, 1, 1)
        
        self.start_button = QPushButton("Run Temperature Search")      
        self.scan_temp_box.layout().addWidget(self.start_button, 1, 2)
        self.start_button.clicked.connect(self.scan_temp_pushed)
        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)
        
        
        self.scan_wid = pg.PlotWidget(title='Scan')
        self.scan_wid.showGrid(True,True)
        self.abs_plot = self.scan_wid.plot([], [], pen=self.abs_pen) 
        self.right.addWidget(self.scan_wid)



    def dis_pushed(self):
        '''Doc'''
        pass

    def scan_temp_pushed(self):
    
        self.start_button.setEnabled(False)
    
        self.scan_temps = []
        self.scan_waves = []
        self.scan_rs = []
    
        start = float(self.temp_edit1.text())
        stop = float(self.temp_edit2.text())
        step_size = (stop - start)/float(self.step_edit.text())
        temp_list = np.arange(start, stop, step_size)
        
        try:
            self.scan_thread = TempScanThread(self, temp_list)
            self.scan_thread.finished.connect(self.done_scan)
            self.scan_thread.reply.connect(self.build_scan)
            self.scan_thread.start()
        except Exception as e: 
            print('Exception starting run thread, lost connection: '+str(e))
        
    def build_scan(self, tup):
        '''Take emit from thread and add point to data        
        '''
        temp, wave, r = tup
        self.scan_temps.append(float(temp))
        self.scan_waves.append(float(wave))
        self.scan_rs.append(float(r))
        self.update_plot()
        
    def update_plot(self):
        '''Update plots with new data
        '''
        #print(self.scan_waves, self.scan_rs)
        self.abs_plot.setData(self.scan_temps, self.scan_rs)
        
    def done_scan(self):
        self.start_button.setEnabled(True)
        
        
    
class TempScanThread(QThread):
    '''Thread class for temperature scan
    Args:
        templist: List of temperatures to scan through
        parent
    '''
    reply = pyqtSignal(tuple)     # reply signal
    finished = pyqtSignal()       # finished signal
    def __init__(self, parent, temp_list):
        QThread.__init__(self)
        self.parent = parent  
        self.list = temp_list
                
    def __del__(self):
        self.wait()
        
    def run(self):
        '''Main scan loop
        '''         
        first_time = True
        for temp in self.list:
            self.parent.parent.probe.set_temp(temp)
            if first_time:
                time.sleep(2)
                first_time = False
            else:    
                time.sleep(self.parent.settings['temp_scan_wait'])
            wave = self.parent.parent.meter.read_wavelength(1)
            x, y, r = self.parent.parent.lockin.read_all()
            self.reply.emit((temp, wave, r))      
  
        self.finished.emit()

        