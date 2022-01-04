'''PyMEOP, J.Maxwell 2020
'''
import datetime
import time
import math
from PyQt5.QtWidgets import QWidget, QLabel, QGroupBox, QHBoxLayout, QVBoxLayout, QGridLayout, QLineEdit, QSpacerItem, QSizePolicy, QComboBox, QPushButton, QProgressBar
from PyQt5.QtGui import QIntValidator, QDoubleValidator, QValidator
from PyQt5.QtCore import QThread, pyqtSignal, Qt
import pyqtgraph as pg
import numpy as np
 
   
class RunTab(QWidget):
    '''Creates run tab. Starts threads for run and to update plots'''
    def __init__(self, parent):
        super(QWidget,self).__init__(parent)
        self.__dict__.update(parent.__dict__)
        
        self.parent = parent
                              
        self.scan_currs = []
        self.scan_waves = []
        self.scan_rs = []
        self.scan_time = []
               
        # pyqtgrph styles        
        pg.setConfigOptions(antialias=True)
        self.run_pen = pg.mkPen(color=(250, 0, 0), width=1.5)
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
        
        
        self.scan_button = QPushButton("Run Scan",checkable=True)      
        self.controls_box.layout().addWidget(self.scan_button, 2, 2)
        self.scan_button.clicked.connect(self.scan_pushed)
        
        
        # Populate Analysis box
        self.anal_box = QGroupBox('Analysis Settings')
        self.anal_box.setLayout(QGridLayout())
        self.left.addWidget(self.anal_box)
        
        
        
        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)              
        
        self.pol_wid = pg.PlotWidget(title='Polarization')
        self.pol_wid.showGrid(True,True)
        self.pol_wid.addLegend(offset=(0.5, 0))
        self.right.addWidget(self.pol_wid)
        
        self.run_wid = pg.PlotWidget()
        self.time_axis = pg.DateAxisItem(orientation='bottom')
        self.run_wid = pg.PlotWidget(
            title='Running Scan', axisItems={'bottom': self.time_axis}
        )
        self.run_wid.showGrid(True,True)
        self.run_wid.addLegend(offset=(0.5, 0))
        self.run_plot = self.run_wid.plot([], [], pen=self.run_pen)         
        self.right.addWidget(self.run_wid)
        
        self.peak_wid = pg.PlotWidget(title='Probe Peaks')
        self.peak_wid.showGrid(True,True)
        self.peak_wid.addLegend(offset=(0.5, 0))
        self.right.addWidget(self.peak_wid)

    def run_pushed(self):
        '''Start main loop if conditions met'''
               
        if self.run_button.isChecked():        
            self.parent.status_bar.showMessage('Running sweeps...')
            #self.abort_button.setEnabled(True)
            self.lock_button.setEnabled(False)
            self.run_button.setText('Finish')
            self.start_thread()
            self.parent.run_toggle()
                   
        else:
            if self.run_thread.isRunning:
                self.run_button.setText('Finishing...')
                self.run_button.setEnabled(False)
        
    def scan_pushed(self):
        '''Start main loop if conditions met'''
               
        if self.scan_button.isChecked():        
            self.scan_button.setText('Stop')
            self.start_scan()
                   
        else:
            try:
                if self.scan_thread.isRunning:
                    self.scan_button.setText('Finishing...')
                    self.scan_button.setEnabled(False)
            except:
                pass
            
    def start_scan(self):
        
        start = float(self.curr_lo_edit.text())
        stop = float(self.curr_up_edit.text())
        step_size = (stop - start)/float(self.step_edit.text())
        curr_list = np.arange(start, stop, step_size)
        
        try:
            self.scan_thread = RunThread(self, curr_list, float(self.temp_edit.text()))
            self.scan_thread.finished.connect(self.finish_scans)
            self.scan_thread.reply.connect(self.build_scan)
            self.scan_thread.start()
        except Exception as e: 
            print('Exception starting run thread, lost connection: '+str(e))
        
    def build_scan(self, tup):
        '''Take emit from thread and add point to data        
        '''
        curr, wave, r, time = tup     
        if 'done' in curr: 
            pass   # do things to end event
        else:    
            self.scan_currs.append(float(curr))
            self.scan_waves.append(float(wave))
            self.scan_rs.append(float(r))
            self.scan_time.append(time.timestamp())
            if len(self.scan_currs) > 1000:
                self.scan_currs.pop(0)
                self.scan_waves.pop(0)
                self.scan_rs.pop(0)
                self.scan_time.pop(0)
            self.update_plot()        
        
    def update_plot(self):
        '''Update plots with new data
        '''
        #print(self.scan_waves, self.scan_rs)
        self.run_plot.setData(self.scan_time, self.scan_rs)

    def finish_scans(self):
        #self.scan_button.setEnabled(True)
        self.scan_button.setText("Run Scan")
        self.scan_button.toggle()

        
class RunThread(QThread):
    '''Thread class for running
    Args:
        templist: List of currents to scan through
        parent
    '''
    reply = pyqtSignal(tuple)     # reply signal
    finished = pyqtSignal()       # finished signal
    def __init__(self, parent, curr_list, temp):
        QThread.__init__(self)
        self.parent = parent  
        self.list = curr_list
        self.reverse_list = curr_list[::-1]
        self.temp = temp
        self.scans = 0   # number of scans that we've been through
                
    def __del__(self):
        self.wait()
        
    def run(self):
        '''Main scan loop
        '''         
        self.parent.parent.probe.set_temp(self.temp)
        start_time = datetime.datetime.now()
        list = self.list if (self.scans % 2 == 0) else self.reverse_list  # use reverse list on odd iterations
        
        while self.parent.run_button.isChecked():
            for curr in list:
                self.parent.parent.probe.set_current(curr)
                # if self.scans = 0:
                    # time.sleep(2)
                # else:    
                time.sleep(self.parent.settings['curr_scan_wait'])
                #wave = self.parent.parent.meter.read_wavelength(1)
                wave = 0
                x, y, r = self.parent.parent.lockin.read_all()
                self.reply.emit((curr, wave, r, datetime.datetime.now()))  
                
            self.scans += 1   
            self.reply.emit(("scan done", 0, 0, datetime.datetime.now()))    
                
        self.parent.parent.probe.set_current(self.list[0])
        self.finished.emit()
        