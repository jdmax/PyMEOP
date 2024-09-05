'''PyMEOP, J.Maxwell 2020
'''
import datetime
import time
import math
from PyQt5.QtWidgets import QWidget, QLabel, QGroupBox, QHBoxLayout, QVBoxLayout, QGridLayout, QLineEdit, QSpacerItem, \
    QSizePolicy, QComboBox, QPushButton, QProgressBar, QCheckBox
from PyQt5.QtGui import QIntValidator, QDoubleValidator, QValidator
from PyQt5.QtCore import QThread, pyqtSignal, Qt
import pyqtgraph as pg
import numpy as np
 
   
class FindTab(QWidget):
    '''Creates main tab. Starts threads for run and to update plots'''
    def __init__(self, parent, statusbar):
        super(QWidget,self).__init__(parent)
        self.__dict__.update(parent.__dict__)
        
        self.parent = parent
        self.statusbar = statusbar
               
        # pyqtgrph styles        
        pg.setConfigOptions(antialias=True)
        self.temp_pen = pg.mkPen(color=(250, 0, 0), width=1.5)
        self.curr_pen = pg.mkPen(color=(0, 200, 0), width=1.5)
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        
        
        # Populate Run Tab
        self.main = QHBoxLayout()            # main layout
        self.setLayout(self.main)
        self.left = QVBoxLayout()     # left part of main layout
        self.main.addLayout(self.left)

        # Wide scan box
        self.scan_wide_box = QGroupBox('Wide Spectrum Scan')
        self.scan_wide_box.setLayout(QGridLayout())
        self.left.addWidget(self.scan_wide_box)


        self.wave_label = QLabel('Read Wavelength Meter?')
        self.scan_wide_box.layout().addWidget(self.wave_label, 3, 0)
        self.wave_check = QCheckBox()
        self.wave_check.setChecked(False)
        self.scan_wide_box.layout().addWidget(self.wave_check, 3, 1)
        #self.wave_check.currentIndexChanged.connect(self.wave_check_changed)

        self.temp_label = QLabel('Temperature Range ('+u"\u00b0"+'C):')
        self.scan_wide_box.layout().addWidget(self.temp_label, 0, 0)
        self.temp_edit1 =  QLineEdit()
        self.scan_wide_box.layout().addWidget(self.temp_edit1, 0, 1)
        self.temp_edit2 =  QLineEdit()
        self.scan_wide_box.layout().addWidget(self.temp_edit2, 0, 2)
        self.temp_step_label = QLabel('Scan Steps:')
        self.scan_wide_box.layout().addWidget(self.temp_step_label, 1, 0)
        self.wide_step_edit =  QLineEdit()
        self.scan_wide_box.layout().addWidget(self.wide_step_edit, 1, 1)
        self.stat_curr_label = QLabel('Static Current (mA):')
        self.scan_wide_box.layout().addWidget(self.stat_curr_label, 2, 0)
        self.stat_wide_edit =  QLineEdit()
        self.scan_wide_box.layout().addWidget(self.stat_wide_edit, 2, 1)
        
        self.start_wide_button = QPushButton("Run Wide Scan")
        self.scan_wide_box.layout().addWidget(self.start_wide_button, 2, 2)
        self.start_wide_button.clicked.connect(self.scan_temp_pushed)
        
        # Find current peaks box
        self.scan_fine_box = QGroupBox('Fine Peaks Scan')
        self.scan_fine_box.setLayout(QGridLayout())
        self.left.addWidget(self.scan_fine_box)

        #self.type_label = QLabel('Scan Type:')
        #self.scan_fine_box.layout().addWidget(self.type_label, 0, 0)
        #self.type_combo = QComboBox()
        #self.type_combo.addItems(['Temperature', 'Current'])
        #self.scan_fine_box.layout().addWidget(self.type_combo, 0, 1)
        #self.type_combo.currentIndexChanged.connect(self.type_combo_changed)
        #self.fine_scan_type = 'temp'
        
        self.scan_label = QLabel('Temperature Range (' + u"\u00b0" + 'C):')
        self.scan_fine_box.layout().addWidget(self.scan_label, 1, 0)
        self.scan_edit1 =  QLineEdit()
        self.scan_fine_box.layout().addWidget(self.scan_edit1, 1, 1)
        self.scan_edit2 =  QLineEdit()
        self.scan_fine_box.layout().addWidget(self.scan_edit2, 1, 2)
        self.fine_step_label = QLabel('Scan Time:')
        self.scan_fine_box.layout().addWidget(self.fine_step_label, 2, 0)
        self.step_fine_edit =  QLineEdit()
        self.scan_fine_box.layout().addWidget(self.step_fine_edit, 2, 1)
        self.stat_temp_label = QLabel('Static Current (mA):')
        self.scan_fine_box.layout().addWidget(self.stat_temp_label, 3, 0)
        self.stat_temp_edit =  QLineEdit()
        self.scan_fine_box.layout().addWidget(self.stat_temp_edit, 3, 1)
        
        self.start_fine_button = QPushButton("Run Fine Scan")
        self.scan_fine_box.layout().addWidget(self.start_fine_button, 3, 2)
        self.start_fine_button.clicked.connect(self.scan_fine_pushed)
        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)
        
        
        #self.scan_temp_wid = pg.PlotWidget(title='Grating Temperature Scan ('+u"\u00b0"+'C)')
        self.scan_wide_wid = pg.PlotWidget(title='Wide Scan')
        self.scan_wide_wid.showGrid(True, True)
        self.temp_plot = self.scan_wide_wid.plot([], [], pen=self.temp_pen)
        self.right.addWidget(self.scan_wide_wid)
        
        self.scan_fine_wid = pg.PlotWidget(title='Fine Scan')
        self.scan_fine_wid.showGrid(True, True)
        self.curr_plot = self.scan_fine_wid.plot([], [], pen=self.curr_pen)
        self.right.addWidget(self.scan_fine_wid)

    def type_combo_changed(self, i):
        if i == 1:
            self.fine_scan_type = 'current'
            self.scan_label.setText('Current Range (mA):')
            self.stat_temp_label.setText('Static Temperature ('+u"\u00b0"+'C):')
        else:
            self.fine_scan_type = 'temp'
            self.scan_label.setText('Temperature Range (' + u"\u00b0" + 'C):')
            self.stat_temp_label.setText('Static Current (mA):')


    def dis_pushed(self):
        '''Doc'''
        pass

    def scan_temp_pushed(self):
        self.start_wide_button.setEnabled(False)
        self.start_fine_button.setEnabled(False)

        self.scan_temps = []
        self.scan_waves = []
        self.scan_rs = []

        start = float(self.temp_edit1.text())
        stop = float(self.temp_edit2.text())
        curr = float(self.stat_wide_edit.text())
        step_size = (stop - start) / float(self.wide_step_edit.text())
        temp_list = np.arange(start, stop, step_size)

        try:
            self.scan_thread = ScanThread(self, 'temp', temp_list, curr, self.wave_check.isChecked())
            self.scan_thread.finished.connect(self.done_temp_scan)
            self.scan_thread.reply.connect(self.build_temp_scan)
            self.scan_thread.start()
        except Exception as e:
            print('Exception starting run thread, lost connection: ' + str(e))
        
    def build_temp_scan(self, tup):
        '''Take emit from thread and add point to data        
        '''
        temp, wave, r = tup
        self.scan_temps.append(float(temp))
        self.scan_waves.append(float(wave))
        self.scan_rs.append(float(r))
        self.update_temp_plot()
        
    def update_temp_plot(self):
        '''Update plots with new data
        '''
        #print(, self.scan_rs)
        if self.wave_check.isChecked():
            self.temp_plot.setData(self.scan_waves, self.scan_rs)
        else:
            self.temp_plot.setData(self.scan_temps, self.scan_rs)
        
    def done_temp_scan(self):
        self.start_fine_button.setEnabled(True)
        self.start_wide_button.setEnabled(True)
        
         
    def scan_fine_pushed(self):
        '''Doc'''
        self.start_fine_button.setEnabled(False)
        self.start_wide_button.setEnabled(False)

    
        start = float(self.scan_edit1.text())
        stop = float(self.scan_edit2.text())
        static = float(self.stat_temp_edit.text())
        scantime = float(self.step_fine_edit.text())
        
       # try:
        self.quick_thread = QuickScanThread(self, self.parent.probe, self.parent.lockin, start, stop,
                                            static, scantime)
        self.quick_thread.finished.connect(self.done_curr_scan)
        #self.quick_thread.reply.connect(self.build_curr_scan)
        self.quick_thread.start()
        #except Exception as e:
        #    print('Exception starting quick run thread, lost connection: '+str(e))
        
    def build_curr_scan(self, tup):
        '''Take emit from thread and add point to data        
        '''
        curr, wave, r = tup
        self.scan_currs.append(float(curr))
        self.scan_waves.append(float(wave))
        self.scan_rs.append(float(r))
        self.update_curr_plot()        
        
    def update_curr_plot(self):
        '''Update plots with new data
        '''
        #print(self.scan_waves, self.scan_rs)
        self.curr_plot.setData(self.scan_currs, self.scan_rs)
        #self.curr_plot.setData(self.scan_waves, self.scan_rs)

    def done_curr_scan(self, data):
        self.start_fine_button.setEnabled(True)
        self.start_wide_button.setEnabled(True)
        x, y, r, theta = data
        self.curr_plot.setData(r)
       
    
class ScanThread(QThread):
    '''Thread class for temperature or current scan
    Args:
        list: List of temperatures or currents to scan through
        parent
    '''
    reply = pyqtSignal(tuple)     # reply signal
    finished = pyqtSignal()       # finished signal
    def __init__(self, parent, type, list, static, waves_inc):
        QThread.__init__(self)
        self.parent = parent
        self.list = list
        self.static = static  
        self.type = type # 'temp' or 'curr'
        self.wave_inc = waves_inc
                
    def __del__(self):
        self.wait()
        
    def run(self):
        '''Main scan loop
        '''         
        first_time = True
        if self.wave_inc:
            self.parent.parent.meter.start_cont()
        for v in self.list:
            if 'temp' in self.type:
                self.parent.parent.probe.set_current(self.static)
                self.parent.parent.probe.set_temp(v)

                if first_time:
                    time.sleep(2)
                    first_time = False
                    if self.wave_inc:
                        for i in range(10):
                            wave = self.parent.parent.meter.read_wavelength(1)
                            time.sleep(0.2)

                else:
                    time.sleep(self.parent.settings['temp_scan_wait'])

                x, y, r = self.parent.parent.lockin.read_all()
                if self.wave_inc:
                    wave = self.parent.parent.meter.read_wavelength(1)
                    self.reply.emit((v, wave, r))
                else:
                    self.reply.emit((v, 0, r))
            if 'curr' in self.type:
                self.parent.parent.probe.set_temp(self.static)
                self.parent.parent.probe.set_current(v)
                self.parent.parent.meter.read_wavelength(1)
                if first_time:
                    time.sleep(4)
                    first_time = False
                else:    
                    time.sleep(self.parent.settings['curr_scan_wait'])
                x, y, r = self.parent.parent.lockin.read_all()
                if self.wave_inc:
                    wave = self.parent.parent.meter.read_wavelength(1)
                    self.reply.emit((v, wave, r))
                else:
                    self.reply.emit((v, wave, r))
        if self.wave_inc:
            self.parent.parent.meter.stop_cont()
        self.finished.emit()


class QuickScanThread(QThread):
    '''Thread class for fast temperature or current scan
    '''
    reply = pyqtSignal(tuple)  # reply signal
    finished = pyqtSignal(np.ndarray)  # finished signal

    def __init__(self, parent, probe, lockin, start, stop, static, scantime):
        QThread.__init__(self)
        self.parent = parent
        self.list = list
        self.probe = probe # probe laser interface instance
        self.lockin = lockin # lock in interface instance
        self.static = static
        self.type = type  # 'temp' or 'curr'
        self.stop = stop
        self.begin = start
        self.time = scantime

    def __del__(self):
        self.wait()

    def run(self):
        '''Main quick scan loop.
        Bring laser to correct temperature and voltage, then wait a sec, then start scan
        '''


        if self.type == 1:  # current scan
            self.probe.set_temp(self.static)
            self.probe.set_current(self.begin)
            type = 'curr'
        else: # current scan
            self.probe.set_current(self.static)
            self.probe.set_temp(self.begin)
            type = 'temp'

        rate = (self.stop - self.begin)/self.time
        self.probe.config_scan(type, self.begin, self.stop, False, 0, rate)

        time.sleep(2)
        #while not self.probe.check_ready():
        #    time.sleep(0.5)
        #    print('Waiting to settle, first time')

        # one shot, sawtooth scan
        print('starting')
        success = self.probe.start_scan()
        if not success:
            self.parent.status_bar.showMessage(f"Scan failed.")
            self.finished.emit([0,0,0,0])



        while True:
            state = self.probe.wide_scan_state()
            if state == 2:
                print('Running')
                self.parent.status_bar.showMessage(f"Running scan.")
                break
            elif state == 1:
                print('Waiting for start condition')
                self.parent.status_bar.showMessage(f"Waiting for scan start.")

            else:
                break
            time.sleep(0.01)
        self.lockin.capture_start()
        time.sleep(self.time)
        print('Should be done with sweep. Stopping capture.')
        data = self.lockin.capture_stop()
        print('Capture returned.')
        self.parent.status_bar.showMessage(f"Scan complete.")

        self.finished.emit(data)

