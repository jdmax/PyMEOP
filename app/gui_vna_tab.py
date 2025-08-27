'''PyMEOP, J.Maxwell 2020
'''
import datetime
import time
import math

import matplotlib.pyplot as plt
from PyQt5.QtWidgets import QWidget, QLabel, QGroupBox, QHBoxLayout, QVBoxLayout, QGridLayout, QLineEdit, QSpacerItem, QSizePolicy, QComboBox, QPushButton, QProgressBar, QInputDialog, QTabWidget
from PyQt5.QtGui import QIntValidator, QDoubleValidator, QValidator
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer
import pyqtgraph as pg
import numpy as np
import pandas as pd
import skrf as rf
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class VNA_Tab(QWidget):
    '''Creates run tab. Starts threads for run and to update plots'''
    def __init__(self, parent):
        super(QWidget,self).__init__(parent)
        self.__dict__.update(parent.__dict__)
        self.timer = QTimer()
        self.timer.timeout.connect(self.heartbeat)
        self.timer.start(10000)

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

        self.frequencies = []
        self.amplitudes = []
        self.s11_real = []
        self.s11_imag = []
        self.s21_real = []
        self.s21_imag = []
        
        self.pol_hist = {}    # polarization history keyed on stop timestamp
        
        
        # Populate Run Tab
        self.main = QHBoxLayout()            # main layout
        self.setLayout(self.main)
        self.left = QVBoxLayout()     # left part of main layout
        self.main.addLayout(self.left)

        self.tabs = QTabWidget()

        # Populate Controls box
        self.controls_box = QGroupBox('VNA Frequency Scan')
        self.controls_box.setLayout(QGridLayout())
        self.controls_box2 = QGroupBox('VNA Fixed Frequency')
        self.controls_box2.setLayout(QGridLayout())
        #self.left.addWidget(self.controls_box)
        self.tabs.addTab(self.controls_box, "Frequency scanning")
        self.tabs.addTab(self.controls_box2, "Fixed frequency")
        self.left.addWidget(self.tabs)

        self.curr_label = QLabel('Frequency Range (MHz):')
        self.controls_box.layout().addWidget(self.curr_label, 0, 0)
        self.freq_lo_edit =  QLineEdit()
        self.freq_lo_edit.setValidator(QDoubleValidator(0.1, 51.0, 6, notation=QDoubleValidator.StandardNotation))
        self.controls_box.layout().addWidget(self.freq_lo_edit, 0, 1)
        self.freq_up_edit =  QLineEdit()
        self.freq_up_edit.setValidator(QDoubleValidator(0.1, 51.0, 6, notation=QDoubleValidator.StandardNotation))
        self.controls_box.layout().addWidget(self.freq_up_edit, 0, 2)
                
        self.step_label = QLabel('Number of Steps:')
        self.controls_box.layout().addWidget(self.step_label, 1, 0)
        self.step_edit =  QLineEdit()
        self.step_edit.setValidator(QIntValidator(11, 200))
        self.controls_box.layout().addWidget(self.step_edit, 1, 1)
        
        self.temp_label = QLabel('Temperature (C):')
        self.controls_box.layout().addWidget(self.temp_label, 2, 0)
        self.temp_edit =  QLineEdit()
        self.controls_box.layout().addWidget(self.temp_edit, 2, 1)
        
        
        self.scan_button = QPushButton("Run Scan",checkable=True)      
        self.controls_box.layout().addWidget(self.scan_button, 1, 2)
        self.scan_button.clicked.connect(self.scan_pushed)

        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)             
        
        self.run_wid = pg.PlotWidget()
        self.time_axis = pg.DateAxisItem(orientation='bottom')
        #self.run_wid = pg.PlotWidget(
        #    title='Running Scan' , axisItems={'bottom': self.time_axis}
        #)
        self.run_wid = pg.PlotWidget(
            title='Running Scan'
        )
        self.run_wid.setLabel('left', 'Signal', units = 'dB')
        self.run_wid.setLabel('bottom', 'Frequency', units = 'MHz')
        self.run_wid.showGrid(True,True)
        #self.run_wid.xlabel('Frequency (MHz)')
        self.run_wid.addLegend()
        #self.run_plot = self.run_wid.plot([], [], pen=self.run_pen)
        self.run_plot1 = self.run_wid.plot([], [], pen='red', name='S11 magnitude')
        self.run_plot2 = self.run_wid.plot([], [], pen='blue', name='S21 magnitude')
        self.right.addWidget(self.run_wid)

        self.save_vna_button = QPushButton("Save", checkable=True)
        self.right.addWidget(self.save_vna_button)
        self.save_vna_button.clicked.connect(self.save_pushed)
        
        #self.peak_wid = pg.PlotWidget(title='Probe Peaks')
        #self.peak_wid.showGrid(True,True)
        #self.peak_wid.addLegend(offset=(0.5, 0))
        #self.peak_plot = self.peak_wid.plot([], [], pen=self.peak_pen)
        #self.fit_plot = self.peak_wid.plot([], [], pen=self.fit_pen)
        #self.right.addWidget(self.peak_wid)

        self.canvas = SmithChartCanvas(self)
        self.right.addWidget(self.canvas)
        
        #self.pol_wid = pg.PlotWidget()
        #self.time2_axis = pg.DateAxisItem(orientation='bottom')
        #self.pol_wid = pg.PlotWidget(
        #    title='Polarization (%)', axisItems={'bottom': self.time2_axis}
        #)
        #self.pol_wid.showGrid(True,True)
        #self.pol_wid.addLegend(offset=(0.5, 0))
        #self.pol_plot = self.pol_wid.plot([], [], pen=self.peak_pen)
        #self.right.addWidget(self.pol_wid)


    def save_pushed(self):
        if self.save_vna_button.isChecked():
            path = '/home/poltar/PycharmProjects/PyMEOP/data2025/'
            self.save_vna_button.setText('Saving')
            self.save_vna_button.setEnabled(False)
            text, ok = QInputDialog.getText(self, "Enter filename", f"Save as: '{path}MM_DD_YYYY_VNA_[filename].dat'")
            date_str = datetime.datetime.now().strftime('%m_%d_%y')
            if ok and text:
                print(f'File saved! {path}{date_str}_VNA_{text}.dat')
                #pd.DataFrame([self.frequencies, self.amplitudes], columns=['Frequencies', 'Amplitudes']).to_csv(f'{path}{date_str}_VNA_{text}.dat', sep='\t')
                pd.DataFrame({'Frequencies_Hz': self.frequencies, 'Amplitude_dB': self.amplitudes, 's11_real': self.s11_real, 's11_imag': self.s11_imag, 's21_real': self.s21_real, 's21_imag': self.s21_imag}).to_csv(
                    f'{path}{date_str}_VNA_{text}.dat', sep='\t', index=False)
            else:
                print('User Canceled')
            self.save_vna_button.setText('Save data')
            self.save_vna_button.setEnabled(True)
            self.save_vna_button.setChecked(False)

    def scan_pushed(self):
        '''Start main loop if conditions met'''
               
        if self.scan_button.isChecked():        
            self.scan_button.setText('Stop')
            #self.disoff_button.setEnabled(True)
            self.start_scan()
                   
        else:
            try:
                if self.scan_thread.isRunning:
                    self.scan_button.setText('Finishing...')
                    self.scan_button.setEnabled(False)
            except:
                pass
            
    def start_scan(self):
        self.scan_button.setEnabled(False)
        start = int(float(self.freq_lo_edit.text())*1e6)
        stop = int(float(self.freq_up_edit.text())*1e6)
        steps = int(self.step_edit.text())
        freq_list = np.linspace(start, stop, int(self.step_edit.text()))

        try:
            #self.turn_on_discharge()
            #print(self.vna_frequency_scan(start, stop, 50, 7))

            self.frequencies, self.amplitudes, self.s11_real, self.s11_imag, self.s21_real, self.s21_imag = self.vna_frequency_scan(start, stop, steps, 7)
            #self.scan_thread = RunThread(self, freq_list, float(self.temp_edit.text()))
            #self.scan_thread.finished.connect(self.finish_scans)
            #self.scan_thread.reply.connect(self.build_scan)
            #self.scan_thread.start()
            self.update_run_plot()

            self.scan_button.setText("Run Scan")
            self.scan_button.setChecked(False)
            self.scan_button.setEnabled(True)

        except Exception as e: 
            print('Exception starting run thread, lost connection: '+str(e))
        
    def build_scan(self, tup):
        '''Take emit from thread and add point to data        
        '''
        curr, wave, r, time, status = tup     
        if 'done' in status:     # got last part of scan, reset and send to event
            try:
                curr_max = max(self.currs)
                curr_min = min(self.currs)
                p0 = curr_min + (curr_max - curr_min)*0.333
                p4 = curr_min + (curr_max - curr_min)*0.666
                mid = curr_min + (curr_max - curr_min)/2
                params =  [p0, 2, 1, p4, 2, 1, 0.1, 0.1, 0.1]
                bounds = ((0, 0, 0, mid-1, 0, 0, -np.inf, -np.inf, -np.inf),
                          (mid+1, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf))
            except ValueError:
                params = [0, 0, 0, 0, 0, 0, 0, 0, 0]
                bounds = ((-np.inf, -np.inf, -np.inf, -np.inf, -np.inf,-np.inf, -np.inf, -np.inf, -np.inf),
                          (np.inf, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf))
            # try:
            #     params =  [float(self.g1_pos_edit.text()),
            #         float(self.g1_sig_edit.text()),
            #         float(self.g1_hei_edit.text()),
            #         float(self.g2_pos_edit.text()),
            #         float(self.g2_sig_edit.text()),
            #         float(self.g2_hei_edit.text()),
            #         float(self.slope_edit.text()),
            #         float(self.int_edit.text())]
            # except ValueError:
            #     params = [0, 0, 0, 0, 0, 0, 0, 0]
            self.parent.end_event(self.scan_currs, self.scan_waves, self.scan_rs, self.scan_times, params, bounds)
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
        #self.run_plot.setData(self.frequencies/1e6, self.amplitudes)
        self.run_plot1.setData(self.frequencies / 1e6, self.amplitudes)
        self.run_plot2.setData(self.frequencies / 1e6, 20*np.log10(self.s21_real**2 + self.s21_imag**2))
        self.canvas.update_smith_chart(self.s11_real, self.s11_imag, self.s21_real, self.s21_imag)


    def update_scan_plot(self):
        '''Update tab with new data
        '''
        self.pol_hist[self.parent.previous_event.stop_stamp] = self.parent.previous_event.pol*100
        time_list = list(self.pol_hist.keys())
        pol_list = [self.pol_hist[k] for k in self.pol_hist.keys()]
        
        self.peak_plot.setData(self.parent.previous_event.x_axis, self.parent.previous_event.rs)
        self.fit_plot.setData(self.parent.previous_event.x_axis, self.parent.previous_event.fit)
        self.pol_plot.setData(time_list, pol_list)

        if float(self.freq_lo_edit.text()) < self.parent.previous_event.pf[0] < float(self.freq_up_edit.text()):
            self.g1_pos_edit.setText(f"{self.parent.previous_event.pf[0]:.4f}")
            self.g1_sig_edit.setText(f"{self.parent.previous_event.pf[1]:.4f}")
            self.g1_hei_edit.setText(f"{self.parent.previous_event.pf[2]:.4f}")
            self.g2_pos_edit.setText(f"{self.parent.previous_event.pf[3]:.4f}")
            self.g2_sig_edit.setText(f"{self.parent.previous_event.pf[4]:.4f}")
            self.g2_hei_edit.setText(f"{self.parent.previous_event.pf[5]:.4f}")
            self.quad_edit.setText(f"{self.parent.previous_event.pf[6]:.4f}")
            self.slope_edit.setText(f"{self.parent.previous_event.pf[7]:.4f}")
            self.int_edit.setText(f"{self.parent.previous_event.pf[8]:.4f}")
        
        self.peak1_edit.setText(f"{self.parent.previous_event.pf[2]:.4f}")
        self.peak2_edit.setText(f"{self.parent.previous_event.pf[5]:.4f}")
        
        self.pol_value.setText(f"{self.parent.previous_event.pol*100:.2f}%")

    def finish_scans(self):
        #if not self.relax_thread.isRunning():
        self.scan_button.setText("Run Scan")
        self.scan_button.setEnabled(True)
        print("scan thread outside", self.scan_thread.isRunning())
        
    def zero_pushed(self):
        '''Set current peak amplitudes as zero'''
        self.parent.event.p1_zero = float(self.peak1_edit.text())
        self.parent.event.p2_zero = float(self.peak2_edit.text())   
        self.zero1_edit.setText(self.peak1_edit.text())
        self.zero2_edit.setText(self.peak2_edit.text())

    def start_discharge_off_pushed(self):
        '''Start discharge off button pushed'''

        if self.disoff_button.isChecked():
            self.scan_button.setText("Running Relaxation")
            self.scan_button.setEnabled(False)
            self.disoff_button.setText("Running")
            self.start_discharge_off()
            self.turn_off_laser()
        else:
            try:
                if self.scan_thread.isRunning:
                    self.scan_button.setText('Finishing...')
                    self.scan_button.setChecked(False)
                    self.disoff_button.setText("Finishing...")
                    self.disoff_button.setEnabled(False)
                else:
                    self.scan_button.setChecked(False)
                    self.disoff_button.setText("Start")
                    self.disoff_button.setEnabled(False)
                    self.finish_scans()
            except:
                pass

    def start_discharge_off(self):
        '''Start discharge off measurement. Run while scans are running'''
        try:
            self.relax_thread = RelaxThread(self, float(self.dison_edit.text()), float(self.disoff_edit.text()))
            self.relax_thread.finished.connect(self.relax_finish)
            self.relax_thread.start()
        except Exception as e:
            print('Exception starting relax thread: '+str(e))


    def turn_off_discharge(self):
        '''Turn off signal generator'''
        self.parent.siggen.enable_n(False)

    def turn_on_discharge(self):
        '''Turn on signal generator'''
        #self.parent.siggen.enable_n(True)
        pass

    def vna_frequency_scan(self, start, stop, steps, mode):
        '''scans VNA'''
        #self.parent.siggen.enable_n(True)
        return self.parent.vna.perform_scan(start, stop, steps, mode)

    def turn_off_laser(self):
        '''Turn off laser'''
        pass

    def relax_finish(self):
        self.finish_scans()
        self.disoff_button.setText("Start")

    def heartbeat(self):
        print('heartbeat...')
        self.start_scan()
               
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
        if self.parent.parent.settings['scan_wave']: self.parent.parent.meter.start_cont()
        start_time = datetime.datetime.now()
        while self.parent.scan_button.isChecked():
            list = self.list if (self.scans % 2 == 0) else self.reverse_list  # use reverse list on odd iterations
            for i, curr in enumerate(list):
                self.parent.parent.probe.set_current(curr)
                if i == 0 and self.scans == 0:
                    time.sleep(0.5)
                else:                 
                    time.sleep(self.parent.settings['scan_wait'])
                #time1 = datetime.datetime.now()
                if self.parent.parent.settings['scan_wave']:
                    wave = self.parent.parent.meter.read_wavelength(1)
                else:
                    wave = 0
                #print("wave read time", datetime.datetime.now() - time1)
                #time2 = datetime.datetime.now()
                try:
                    x, y, r = self.parent.parent.lockin.read_all()
                    x = float(x)*1000# turning lock-in V to mV
                    y = float(y)*1000
                    r = float(r)*1000

                except Exception as e:
                    print("error in lock in: ", e)
                    x,y,r = (0,0,0)
                #print("lock read time", datetime.datetime.now() - time2)
                #print("after lock", datetime.datetime.now() - time1)
                #if i%20 == 0:    
                #    print(f"point {i}:", curr, wave)
                self.reply.emit((curr, wave, x, datetime.datetime.now(), 'running'))
                
            self.scans += 1   
            self.reply.emit((0, 0, 0, datetime.datetime.now(), 'done'))    
                
        if self.parent.parent.settings['scan_wave']: self.parent.parent.meter.stop_cont()
        self.parent.parent.probe.set_current(self.list[0])
        self.finished.emit()

class RelaxThread(QThread):
    '''Thread class for running discharge off relaxation
    Args:
        parent
        on_time
        off_time
    '''
    reply = pyqtSignal(tuple)  # reply signal
    finished = pyqtSignal()  # finished signal

    def __init__(self, parent, on_time, off_time):
        QThread.__init__(self)
        self.parent = parent
        self.on_time = on_time
        self.off_time = off_time

    def __del__(self):
        self.wait()

    def run(self):
        '''Main relaxation loop
        '''

        while self.parent.disoff_button.isChecked():
            if self.parent.scan_button.isChecked():
                start = time.time()
                while time.time() - start < self.on_time:
                    time.sleep(0.5)
                    left = self.on_time - (time.time() - start)
                    self.parent.dis_label.setText(f"Running discharge on for {int(left)} more seconds.")
                self.parent.scan_button.setChecked(False)
                i=0
                while self.parent.scan_thread.isRunning():
                    i+=1
                    self.parent.dis_label.setText(f"Waiting for scan to finish.")
                    time.sleep(0.5)
                self.parent.scan_button.setText("Waiting...")
                self.parent.turn_off_discharge()
            else:
                start = time.time()
                while time.time() - start < self.off_time:
                    time.sleep(0.5)
                    left = self.off_time - (time.time() - start)
                    self.parent.dis_label.setText(f"Running discharge off for {int(left)} more seconds.")
                self.parent.scan_button.setChecked(True)
                self.parent.scan_button.setText("Running Relaxation")
                self.parent.turn_on_discharge()
                self.parent.start_scan()

        self.finished.emit()


class SmithChartCanvas(FigureCanvas):
    def __init__(self, parent=None):
        fig = Figure(figsize=(6,6))
        super().__init__(fig)
        self.setParent(parent)

        self.ax = fig.add_subplot(111)
        self.plot_data()

    def plot_data(self):
        #gamma = np.linspace(0, 0.9, 200)*np.exp(1j*np.linspace(0, 2*np.pi, 200))
        gamma = np.linspace(-1, 1, 200)
        net = rf.Network()
        net.s = gamma.reshape(-1, 1, 1)
        net.frequency = rf.Frequency(1, 200, 200, unit='GHz')
        net.plot_s_smith(m=0, n=0, ax=self.ax, label='S11')
        net.plot_s_smith(m=0, n=0, ax=self.ax, label='S21')

        resistances = [0.2, 0.5, 1, 2, 5]
        for r in resistances:
            g = (r - 1) / (r + 1)
            self.ax.text(g, 0.02, fr"{r}$\Omega$", color='black', ha='center', va='bottom', fontsize=9)

        reactances = [0.2, 0.5, 1, 2, 5]
        for x in resistances:
            g = (1j*x -1) / (1j*x +1)
            self.ax.text(g.real + 0.02, g.imag+0.1, fr"{x}i", color='black', ha='center', va='top', fontsize=9)
            self.ax.text(g.real + 0.02, -g.imag-0.1, fr"-{x}i", color='black', ha='center', va='top', fontsize=9)

        self.figure.subplots_adjust(left=0.0, right = 1.0, top = 1.0, bottom = 0.0)


    def update_smith_chart(self, s11_real, s11_imag, s21_real, s21_imag):
        def build_network(gamma):
            net = rf.Network()
            net.s = gamma.reshape(-1, 1, 1)
            net.frequency = rf.Frequency(1, 200, len(gamma), unit='GHz')
            return net
        self.ax.clear()

        #gamma = np.linspace(-1, 1, 200)
        gamma1 = s11_real + 1j*s11_imag
        gamma2 = s21_real + 1j*s21_imag
        net1 = build_network(gamma1)
        net2 = build_network(gamma2)
        net1.plot_s_smith(m=0, n=0, ax=self.ax, label='S11')
        net2.plot_s_smith(m=0, n=0, ax=self.ax, label='S21')

        resistances = [0.2, 0.5, 1, 2, 5]
        for r in resistances:
            g = (r - 1) / (r + 1)
            self.ax.text(g, 0.02, fr"{r}$\Omega$", color='black', ha='center', va='bottom', fontsize=9)

        reactances = [0.2, 0.5, 1, 2, 5]
        for x in resistances:
            g = (1j * x - 1) / (1j * x + 1)
            self.ax.text(g.real + 0.02, g.imag + 0.1, fr"{x}i", color='black', ha='center', va='top', fontsize=9)
            self.ax.text(g.real + 0.02, -g.imag - 0.1, fr"-{x}i", color='black', ha='center', va='top', fontsize=9)

        self.figure.subplots_adjust(left=0.0, right=1.0, top=1.0, bottom=0.0)
        self.draw()


