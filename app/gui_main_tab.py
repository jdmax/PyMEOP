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
 
   
class MainTab(QWidget):
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
               
        # Populate Controls box
        self.controls_box = QGroupBox('Controls')
        self.controls_box.setLayout(QGridLayout())
        self.left.addWidget(self.controls_box)
        
        self.curr_label = QLabel('Current Range (mA):')
        self.controls_box.layout().addWidget(self.curr_label, 0, 0)
        self.curr_lo_edit =  QLineEdit()
        self.curr_lo_edit.setValidator(QDoubleValidator(3.0, 45.0, 3, notation=QDoubleValidator.StandardNotation))
        self.controls_box.layout().addWidget(self.curr_lo_edit, 0, 1)        
        self.curr_up_edit =  QLineEdit()
        self.curr_up_edit.setValidator(QDoubleValidator(3.0, 45.0, 3, notation=QDoubleValidator.StandardNotation))
        self.controls_box.layout().addWidget(self.curr_up_edit, 0, 2)
                
        self.step_label = QLabel('Number of Steps:')
        self.controls_box.layout().addWidget(self.step_label, 1, 0)
        self.step_edit =  QLineEdit()
        self.controls_box.layout().addWidget(self.step_edit, 1, 1)
        
        self.temp_label = QLabel('Temperature (C):')
        self.controls_box.layout().addWidget(self.temp_label, 2, 0)
        self.temp_edit =  QLineEdit()
        self.controls_box.layout().addWidget(self.temp_edit, 2, 1)
        
        
        self.scan_button = QPushButton("Run Current Scan")      
        self.controls_box.layout().addWidget(self.scan_button, 2, 2)
        self.scan_button.clicked.connect(self.scan_pushed)
        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)
              
        
        self.scan_wid = pg.PlotWidget(title='Scan')
        self.scan_wid.showGrid(True,True)
        self.abs_plot = self.scan_wid.plot([], [], pen=self.abs_pen) 
        self.right.addWidget(self.scan_wid)
        
    def scan_pushed(self):
        '''Doc'''
        self.scan_button.setEnabled(False)
    
        self.scan_currs = []
        self.scan_waves = []
        self.scan_rs = []
    
        start = float(self.curr_lo_edit.text())
        stop = float(self.curr_up_edit.text())
        step_size = (stop - start)/float(self.step_edit.text())
        curr_list = np.arange(start, stop, step_size)
        
        try:
            self.scan_thread = ScanThread(self, curr_list, float(self.temp_edit.text()))
            self.scan_thread.finished.connect(self.done_scan)
            self.scan_thread.reply.connect(self.build_scan)
            self.scan_thread.start()
        except Exception as e: 
            print('Exception starting run thread, lost connection: '+str(e))
        
    def build_scan(self, tup):
        '''Take emit from thread and add point to data        
        '''
        curr, wave, r = tup
        self.scan_currs.append(float(curr))
        self.scan_waves.append(float(wave))
        self.scan_rs.append(float(r))
        self.update_plot()        
        
    def update_plot(self):
        '''Update plots with new data
        '''
        #print(self.scan_waves, self.scan_rs)
        self.abs_plot.setData(self.scan_currs, self.scan_rs)

    def done_scan(self):
        self.scan_button.setEnabled(True)

        
class RunThread(QThread):
    '''Thread class for run
    Args:
    '''
    reply = pyqtSignal(tuple)     # reply signal
    finished = pyqtSignal()       # finished signal
    def __init__(self, parent, curr_list, temp):
        QThread.__init__(self)
        self.parent = parent  
        self.list = curr_list
        self.temp = temp
                
    def __del__(self):
        self.wait()
        
    def run(self):
        '''Main scan loop
        '''         
        first_time = True
        self.parent.parent.probe.set_temp(self.temp)
        start_time = datetime.datetime.now()
        for curr in self.list:
            self.parent.parent.probe.set_current(curr)
            if first_time:
                time.sleep(2)
                first_time = False
            else:    
                time.sleep(self.parent.settings['curr_scan_wait'])
            #wave = self.parent.parent.meter.read_wavelength(1)
            wave = 0
            x, y, r = self.parent.parent.lockin.read_all()
            self.reply.emit((curr, wave, r))  

            print(datetime.datetime.now() - start_time)
            
  
        self.finished.emit()
        