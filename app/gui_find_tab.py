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

from app.sweep import SweepThread, WavemeterThread


class FindTab(QWidget):
    '''Creates main tab. Starts threads for run and to update plots'''
    def __init__(self, parent):
        super(QWidget,self).__init__(parent)
        self.__dict__.update(parent.__dict__)
        
        self.parent = parent
               
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

        # Find Temp peaks box
        self.scan_temp_box = QGroupBox('Scan Temperature Peaks')
        self.scan_temp_box.setLayout(QGridLayout())
        self.left.addWidget(self.scan_temp_box)
        
        self.temp_label = QLabel('Temperature Range ('+u"\u00b0"+'C):')
        self.scan_temp_box.layout().addWidget(self.temp_label, 0, 0)
        self.temp_edit1 =  QLineEdit()
        self.scan_temp_box.layout().addWidget(self.temp_edit1, 0, 1)
        self.temp_edit2 =  QLineEdit()
        self.scan_temp_box.layout().addWidget(self.temp_edit2, 0, 2)
        self.temp_step_label = QLabel('Number of Steps:')
        self.scan_temp_box.layout().addWidget(self.temp_step_label, 1, 0)
        self.step_temp_edit =  QLineEdit()
        self.scan_temp_box.layout().addWidget(self.step_temp_edit, 1, 1)
        self.stat_curr_label = QLabel('Static Current (mA):')
        self.scan_temp_box.layout().addWidget(self.stat_curr_label, 2, 0)
        self.stat_curr_edit =  QLineEdit()
        self.scan_temp_box.layout().addWidget(self.stat_curr_edit, 2, 1)
        
        self.start_temp_button = QPushButton("Run Temperature Search")      
        self.scan_temp_box.layout().addWidget(self.start_temp_button, 2, 2)
        self.start_temp_button.clicked.connect(self.scan_temp_pushed)
        
        # Find current peaks box
        self.scan_curr_box = QGroupBox('Scan Current Peaks')
        self.scan_curr_box.setLayout(QGridLayout())
        self.left.addWidget(self.scan_curr_box)
        
        self.curr_label = QLabel('Current Range (mA):')
        self.scan_curr_box.layout().addWidget(self.curr_label, 0, 0)
        self.curr_edit1 =  QLineEdit()
        self.scan_curr_box.layout().addWidget(self.curr_edit1, 0, 1)
        self.curr_edit2 =  QLineEdit()
        self.scan_curr_box.layout().addWidget(self.curr_edit2, 0, 2)
        self.curr_step_label = QLabel('Number of Steps:')
        self.scan_curr_box.layout().addWidget(self.curr_step_label, 1, 0)
        self.step_curr_edit =  QLineEdit()
        self.scan_curr_box.layout().addWidget(self.step_curr_edit, 1, 1)
        self.stat_temp_label = QLabel('Static Temperature ('+u"\u00b0"+'C):')
        self.scan_curr_box.layout().addWidget(self.stat_temp_label, 2, 0)
        self.stat_temp_edit =  QLineEdit()
        self.scan_curr_box.layout().addWidget(self.stat_temp_edit, 2, 1)
        
        self.start_curr_button = QPushButton("Run Current Search")      
        self.scan_curr_box.layout().addWidget(self.start_curr_button, 2, 2)
        self.start_curr_button.clicked.connect(self.scan_curr_pushed)
        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)
        
        
        #self.scan_temp_wid = pg.PlotWidget(title='Grating Temperature Scan ('+u"\u00b0"+'C)')
        self.scan_temp_wid = pg.PlotWidget(title='Grating Temperature Scan (nm)')
        self.scan_temp_wid.showGrid(True,True)
        self.temp_plot = self.scan_temp_wid.plot([], [], pen=self.temp_pen) 
        self.right.addWidget(self.scan_temp_wid)
        
        self.scan_curr_wid = pg.PlotWidget(title='Diode Current Scan (mA)')
        self.scan_curr_wid.showGrid(True,True)
        self.curr_plot = self.scan_curr_wid.plot([], [], pen=self.curr_pen) 
        self.right.addWidget(self.scan_curr_wid)



    def dis_pushed(self):
        '''Doc'''
        pass

    def scan_temp_pushed(self):
        self.start_curr_button.setEnabled(False)
        self.start_temp_button.setEnabled(False)
    
        self.scan_temps = []
        self.scan_waves = []
        self.scan_rs = []
    
        start = float(self.temp_edit1.text())
        stop = float(self.temp_edit2.text())
        curr = float(self.stat_curr_edit.text())
        step_size = (stop - start)/float(self.step_temp_edit.text())
        temp_list = np.arange(start, stop, step_size)
        
        try:
            self.scan_thread = ScanThread(self, 'temp', temp_list, curr)
            self.scan_thread.finished.connect(self.done_temp_scan)
            self.scan_thread.reply.connect(self.build_temp_scan)
            self.scan_thread.start()
        except Exception as e: 
            print('Exception starting run thread, lost connection: '+str(e))
        
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
        self.temp_plot.setData(self.scan_temps, self.scan_rs)
        #self.temp_plot.setData(self.scan_waves, self.scan_rs)
        
    def done_temp_scan(self):
        self.start_curr_button.setEnabled(True)
        self.start_temp_button.setEnabled(True)
        
         
    def scan_curr_pushed(self):
        '''Doc'''
        self.start_curr_button.setEnabled(False)
        self.start_temp_button.setEnabled(False)
    
        self.scan_currs = []
        self.scan_waves = []
        self.scan_rs = []
    
        start = float(self.curr_edit1.text())
        stop = float(self.curr_edit2.text())
        step_size = (stop - start)/float(self.step_curr_edit.text())
        curr_list = np.arange(start, stop, step_size)
        temp = float(self.stat_temp_edit.text())
        
        try:
            if 'wide' in self.parent.settings.get('sweep_mode', 'wide'):
                # The wavemeter updates at a few Hz, far slower than the
                # capture buffer, so it is polled on its own thread and
                # interpolated onto the current axis afterwards instead of
                # pacing every point of the sweep.
                self.sweeping = True
                self.wave_thread = WavemeterThread(
                    self.parent.meter, lambda: self.sweeping,
                    self.parent.settings.get('wave_poll', 0.2))
                self.wave_thread.start()

                self.scan_thread = SweepThread(
                    self, start, stop, temp, lambda: self.sweeping,
                    self.parent.settings,
                    duration=self.parent.settings.get('find_sweep_time', 30.0),
                    once=True)
                self.scan_thread.sweep.connect(self.build_curr_sweep)
                self.scan_thread.trace.connect(self.live_curr_trace)
                self.scan_thread.error.connect(self.curr_sweep_error)
                self.scan_thread.finished.connect(self.done_curr_sweep)
            else:
                self.scan_thread = ScanThread(self, 'curr', curr_list, temp)
                self.scan_thread.finished.connect(self.done_curr_scan)
                self.scan_thread.reply.connect(self.build_curr_scan)
            self.scan_thread.start()
        except Exception as e:
            print('Exception starting run thread, lost connection: '+str(e))

    def build_curr_sweep(self, result):
        '''Take one wide hardware sweep and attach interpolated wavelengths.'''
        currs = np.asarray(result['currs'], dtype=float)
        waves = self.wave_thread.interpolate(
            result['t_start'], result['duration'], currs,
            np.array([result['begin'], result['end']]))

        if not np.any(waves):
            print(f"Only {len(self.wave_thread.waves)} wavelength readings over "
                  f"{result['duration']:.0f} s -- increase find_sweep_time to "
                  f"resolve mode hops.")

        self.scan_currs = currs.tolist()
        self.scan_rs = np.asarray(result['r_mag'], dtype=float).tolist()
        self.scan_waves = waves.tolist()
        self.update_curr_plot()

    def live_curr_trace(self, partial):
        '''Show the wide sweep forming against current while it runs.'''
        try:
            self.curr_plot.setData(partial['currs'], partial['rs'])
        except Exception:
            pass

    def curr_sweep_error(self, message):
        print(f"Find sweep error: {message}")
        self.sweeping = False

    def done_curr_sweep(self):
        '''Stop the wavemeter thread and re-enable the controls.'''
        self.sweeping = False
        try:
            self.wave_thread.wait(3000)
        except Exception:
            pass
        self.done_curr_scan()
        
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

        Plots against wavelength when the meter produced readings, otherwise
        against current so a failed meter does not blank the plot.
        '''
        if np.any(self.scan_waves):
            self.curr_plot.setData(self.scan_waves, self.scan_rs)
        else:
            self.curr_plot.setData(self.scan_currs, self.scan_rs)

    def done_curr_scan(self):
        self.start_curr_button.setEnabled(True)
        self.start_temp_button.setEnabled(True)
       
    
class ScanThread(QThread):
    '''Thread class for temperature or current scan
    Args:
        list: List of temperatures or currents to scan through
        parent
    '''
    reply = pyqtSignal(tuple)     # reply signal
    finished = pyqtSignal()       # finished signal
    def __init__(self, parent, type, list, static):
        QThread.__init__(self)
        self.parent = parent  
        self.list = list
        self.static = static  
        self.type = type # 'temp' or 'curr'
                
    def __del__(self):
        self.wait()
        
    def run(self):
        '''Main scan loop
        '''         
        first_time = True
        self.parent.parent.meter.start_cont()
        for v in self.list:
            if 'temp' in self.type:
                self.parent.parent.probe.set_current(self.static)
                self.parent.parent.probe.set_temp(v)
                if first_time:
                    time.sleep(2)
                    first_time = False
                    for i in range(10):
                        wave = self.parent.parent.meter.read_wavelength(1)
                        time.sleep(0.2)                 
                    
                else:    
                    time.sleep(self.parent.settings['temp_scan_wait'])
                wave = self.parent.parent.meter.read_wavelength(1)
                #wave = 0
                x, y, r = self.parent.parent.lockin.read_all()
                self.reply.emit((v, wave, r))
            if 'curr' in self.type:
                self.parent.parent.probe.set_temp(self.static)
                self.parent.parent.probe.set_current(v)
                self.parent.parent.meter.read_wavelength(1)
                if first_time:
                    time.sleep(4)
                    first_time = False
                else:    
                    time.sleep(self.parent.settings['curr_scan_wait'])
                wave = self.parent.parent.meter.read_wavelength(1)
                #wave = 0
                x, y, r = self.parent.parent.lockin.read_all()
                self.reply.emit((v, wave, r))      
  
        self.parent.parent.meter.stop_cont()
        self.finished.emit()

        