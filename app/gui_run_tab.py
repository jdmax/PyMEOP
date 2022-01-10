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
        
        # pyqtgrph styles        
        pg.setConfigOptions(antialias=True)
        self.run_pen = pg.mkPen(color=(250, 0, 0), width=1.5)
        self.peak_pen = pg.mkPen(color=(0, 250, 0), width=3)
        self.fit_pen = pg.mkPen(color=(0, 0, 250), width=1.5)
        self.pol_pen = pg.mkPen(color=(250, 0, 0), width=1.5)
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        
        self.scan_currs = []
        self.scan_waves = []
        self.scan_rs = []
        self.scan_times = []
        
        self.currs = []
        self.waves = []
        self.rs = []
        self.times = []
        
        self.pol_hist = {}    # polarization history keyed on stop timestamp
        
        
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
        self.anal_box = QGroupBox('Fit Parameters')
        self.anal_box.setLayout(QGridLayout())
        self.left.addWidget(self.anal_box)
        
        # self.pos_label = QLabel("Position:")
        # self.anal_box.layout().addWidget(self.pos_label, 4, 1)
        # self.sig_label = QLabel("Sigma:")
        # self.anal_box.layout().addWidget(self.sig_label, 4, 2)
        # self.hei_label = QLabel("Height:")
        # self.anal_box.layout().addWidget(self.hei_label, 4, 3)
        
        
        self.g1_label = QLabel("Gaussian 1:")
        self.anal_box.layout().addWidget(self.g1_label, 1, 0)
        self.g1_pos_edit =  QLineEdit()
        self.g1_pos_edit.setPlaceholderText("Position")
        self.anal_box.layout().addWidget(self.g1_pos_edit, 1, 1)
        self.g1_sig_edit =  QLineEdit()
        self.g1_sig_edit.setPlaceholderText("Sigma")
        self.anal_box.layout().addWidget(self.g1_sig_edit, 1, 2)
        self.g1_hei_edit =  QLineEdit()
        self.g1_hei_edit.setPlaceholderText("Height")
        self.anal_box.layout().addWidget(self.g1_hei_edit, 1, 3)        
        
        self.g2_label = QLabel("Gaussian 2:")
        self.anal_box.layout().addWidget(self.g2_label, 2, 0)
        self.g2_pos_edit =  QLineEdit()
        self.g2_pos_edit.setPlaceholderText("Position")
        self.anal_box.layout().addWidget(self.g2_pos_edit, 2, 1)
        self.g2_sig_edit =  QLineEdit()
        self.g2_sig_edit.setPlaceholderText("Sigma")
        self.anal_box.layout().addWidget(self.g2_sig_edit, 2, 2)
        self.g2_hei_edit =  QLineEdit()
        self.g2_hei_edit.setPlaceholderText("Height")
        self.anal_box.layout().addWidget(self.g2_hei_edit, 2, 3)        
        
        self.slope_label = QLabel("Linear:")
        self.anal_box.layout().addWidget(self.slope_label, 3, 0)
        self.slope_edit =  QLineEdit()
        self.slope_edit.setPlaceholderText("Slope")
        self.anal_box.layout().addWidget(self.slope_edit, 3, 1)
        self.int_edit =  QLineEdit()
        self.int_edit.setPlaceholderText("Intercept")
        self.anal_box.layout().addWidget(self.int_edit, 3, 2)       


        # Populate Results box
        self.res_box = QGroupBox('Results')
        self.res_box.setLayout(QVBoxLayout())
        self.left.addWidget(self.res_box)     
        
        self.peaks_layout = QGridLayout()
        self.res_box.layout().addLayout(self.peaks_layout)
        self.peaks_label = QLabel("Peak Amplitudes:")
        self.peaks_layout.addWidget(self.peaks_label, 0, 0)
        self.peak1_edit = QLineEdit()
        self.peak1_edit.setEnabled(False)
        self.peaks_layout.addWidget(self.peak1_edit, 0, 1)
        self.peak2_edit = QLineEdit()
        self.peak2_edit.setEnabled(False)
        self.peaks_layout.addWidget(self.peak2_edit, 0, 2)
        
        
        self.res_box.layout().addWidget(self.parent.divider())
        
        self.zero_layout = QGridLayout()
        self.res_box.layout().addLayout(self.zero_layout)
        self.zero_label = QLabel("Zero Amplitudes:")
        self.zero_layout.addWidget(self.zero_label, 0, 0)
        self.zero1_edit = QLineEdit()
        self.zero1_edit.setEnabled(False)
        self.zero_layout.addWidget(self.zero1_edit, 0, 1)
        self.zero2_edit = QLineEdit()
        self.zero2_edit.setEnabled(False)
        self.zero_layout.addWidget(self.zero2_edit, 0, 2)
        
        self.zero_button = QPushButton("Set Current as Zero")      
        self.zero_layout.addWidget(self.zero_button, 1, 2)
        self.zero_button.clicked.connect(self.zero_pushed)
        
        self.res_box.layout().addWidget(self.parent.divider())
        
        self.pol_layout = QGridLayout()
        self.res_box.layout().addLayout(self.pol_layout)
        self.pol_label = QLabel("Polarization:")
        self.pol_layout.addWidget(self.pol_label, 0, 0)
        self.pol_value = QLabel()
        self.pol_value.setStyleSheet("font:30pt")
        self.pol_layout.addWidget(self.pol_value, 0, 1)
        

        # self.params_label = QLabel('Result Parameters:')
        # self.res_box.layout().addWidget(self.params_label , 0, 0)
        # self.results_labels = []
        # for i in range(8):
            # self.results_labels.append(QLabel(''))
            # print(int((i+1)/3), i - 3*int((i+1)/3) + 1)
            # self.res_box.layout().addWidget(self.results_labels[i] , int((i+1)/3), i - 3*int((i+1)/3) + 1)
            
        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)             
        
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
        self.peak_plot = self.peak_wid.plot([], [], pen=self.peak_pen)   
        self.fit_plot = self.peak_wid.plot([], [], pen=self.fit_pen)   
        self.right.addWidget(self.peak_wid) 
        
        self.pol_wid = pg.PlotWidget()
        self.time2_axis = pg.DateAxisItem(orientation='bottom')
        self.pol_wid = pg.PlotWidget(
            title='Polarization (%)', axisItems={'bottom': self.time2_axis}
        )
        self.pol_wid.showGrid(True,True)
        self.pol_wid.addLegend(offset=(0.5, 0))
        self.pol_plot = self.pol_wid.plot([], [], pen=self.peak_pen)   
        self.right.addWidget(self.pol_wid)

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
        curr, wave, r, time, status = tup     
        if 'done' in status:     # got last part of scan, rest and send to event
        
            try:
                params =  [float(self.g1_pos_edit.text()),
                    float(self.g1_sig_edit.text()),
                    float(self.g1_hei_edit.text()),
                    float(self.g2_pos_edit.text()),
                    float(self.g2_sig_edit.text()),
                    float(self.g2_hei_edit.text()),
                    float(self.slope_edit.text()),
                    float(self.int_edit.text())]     
            except ValueError:
                params = [0, 0, 0, 0, 0, 0, 0, 0]
            self.parent.end_event(self.scan_currs, self.scan_waves, self.scan_rs, self.scan_times, params)
            self.scan_currs = []
            self.scan_waves = []
            self.scan_rs = []
            self.scan_times = []
        else:             
            self.currs.append(float(curr))
            self.waves.append(float(wave))
            self.rs.append(float(r))
            self.times.append(time.timestamp())
            self.scan_currs.append(float(curr))
            self.scan_waves.append(float(wave))
            self.scan_rs.append(float(r))
            self.scan_times.append(time.timestamp())
            if len(self.currs) > 600:
                self.currs.pop(0)
                self.waves.pop(0)
                self.rs.pop(0)
                self.times.pop(0)
            self.update_run_plot()        
        
    def update_run_plot(self):
        '''Update plots with new data
        '''
        #print(self.scan_waves, self.scan_rs)
        self.run_plot.setData(self.times, self.rs)
        
    def update_scan_plot(self):
        '''Update tab with new data
        '''
        self.pol_hist[self.parent.previous_event.stop_stamp] = self.parent.previous_event.pol*100
        time_list = list(self.pol_hist.keys())
        pol_list = [self.pol_hist[k] for k in self.pol_hist.keys()]
        
        self.peak_plot.setData(self.parent.previous_event.currs, self.parent.previous_event.rs)
        self.fit_plot.setData(self.parent.previous_event.currs, self.parent.previous_event.fit)
        self.pol_plot.setData(time_list, pol_list)
                
        self.g1_pos_edit.setText(f"{self.parent.previous_event.pf[0]:.4f}")
        self.g1_sig_edit.setText(f"{self.parent.previous_event.pf[1]:.4f}")
        self.g1_hei_edit.setText(f"{self.parent.previous_event.pf[2]:.4f}")
        self.g2_pos_edit.setText(f"{self.parent.previous_event.pf[3]:.4f}")
        self.g2_sig_edit.setText(f"{self.parent.previous_event.pf[4]:.4f}")
        self.g2_hei_edit.setText(f"{self.parent.previous_event.pf[5]:.4f}")
        self.slope_edit.setText(f"{self.parent.previous_event.pf[6]:.4f}")
        self.int_edit.setText(f"{self.parent.previous_event.pf[7]:.4f}")
        
        self.peak1_edit.setText(f"{self.parent.previous_event.pf[2]:.4f}")
        self.peak2_edit.setText(f"{self.parent.previous_event.pf[5]:.4f}")
        
        self.pol_value.setText(f"{self.parent.previous_event.pol*100:.2f}%")

    def finish_scans(self):
        #self.scan_button.setEnabled(True)
        self.scan_button.setText("Run Scan")
        self.scan_button.setEnabled(True)
        
    def zero_pushed(self):
        '''Set current peak amplitudes as zero'''
        self.parent.event.p1_zero = float(self.peak1_edit.text())
        self.parent.event.p2_zero = float(self.peak2_edit.text())   
        self.zero1_edit.setText(self.peak1_edit.text())
        self.zero2_edit.setText(self.peak2_edit.text())
               
               
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
        while self.parent.scan_button.isChecked():
            list = self.list if (self.scans % 2 == 0) else self.reverse_list  # use reverse list on odd iterations
            for curr in list:
                self.parent.parent.probe.set_current(curr)
                # if self.scans = 0:
                    # time.sleep(2)
                # else:    
                time.sleep(self.parent.settings['scan_wait'])
                #wave = self.parent.parent.meter.read_wavelength(1)
                wave = 0
                x, y, r = self.parent.parent.lockin.read_all() 
                self.reply.emit((curr, wave, float(r)*1000, datetime.datetime.now(), 'running'))    # turning lock-in V to mV
                
            self.scans += 1   
            self.reply.emit((0, 0, 0, datetime.datetime.now(), 'done'))    
                
        self.parent.parent.probe.set_current(self.list[0])
        self.finished.emit()
        