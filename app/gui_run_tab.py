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

from app import scanfit

HE3_GAMMA = 32.43410    # 3He gyromagnetic ratio over 2 pi, MHz/T (shielded helion, CODATA)
N_OUT_MIN = 0.95        # lowest frequency on the SG380 type N output, MHz

   
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
        # fit components, drawn part transparent so they read as underneath the fit
        self.g1_pen = pg.mkPen(color=(250, 140, 0, 120), width=1.5)
        self.g2_pen = pg.mkPen(color=(190, 0, 250, 120), width=1.5)
        self.base_pen = pg.mkPen(color=(130, 130, 130, 120), width=1.5)
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
        self.last_good_pf = None    # parameters of last good fit, seeds the next scan's fit
        
        
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
        self.curr_lo_edit.setValidator(QDoubleValidator(3.0, 149.0, 4, notation=QDoubleValidator.StandardNotation))
        self.controls_box.layout().addWidget(self.curr_lo_edit, 0, 1)        
        self.curr_up_edit =  QLineEdit()
        self.curr_up_edit.setValidator(QDoubleValidator(3.0, 149.0, 4, notation=QDoubleValidator.StandardNotation))
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
        
        # Gaussian lines suit low pressure, where Doppler broadening dominates; at
        # around 100 mbar the collisional width is comparable and Voigt is needed
        self.shape_label = QLabel("Line Shape:")
        self.anal_box.layout().addWidget(self.shape_label, 0, 0)
        self.shape_combo = QComboBox()
        for key, name in scanfit.PROFILES.items():
            self.shape_combo.addItem(name, key)
        self.shape_combo.setToolTip("Peak shape for the fit, from the scan now running on. Gaussian for "
                                    "Doppler broadened lines at low pressure, Voigt once pressure "
                                    "broadening is comparable. Heights differ between the two, so "
                                    "set the zero amplitudes again after switching.")
        self.shape_combo.currentIndexChanged.connect(self.shape_changed)
        self.anal_box.layout().addWidget(self.shape_combo, 0, 1)

        self.fit_status = QLabel('')
        self.anal_box.layout().addWidget(self.fit_status, 1, 0, 1, 5)
        
        # self.pos_label = QLabel("Position:")
        # self.anal_box.layout().addWidget(self.pos_label, 4, 1)
        # self.sig_label = QLabel("Sigma:")
        # self.anal_box.layout().addWidget(self.sig_label, 4, 2)
        # self.hei_label = QLabel("Height:")
        # self.anal_box.layout().addWidget(self.hei_label, 4, 3)
        
        
        self.g1_label = QLabel("Peak 1:")
        self.anal_box.layout().addWidget(self.g1_label, 2, 0)
        self.g1_pos_edit =  QLineEdit()
        self.g1_pos_edit.setEnabled(False)
        self.g1_pos_edit.setPlaceholderText("Position")
        self.anal_box.layout().addWidget(self.g1_pos_edit, 2, 1)
        self.g1_sig_edit =  QLineEdit()
        self.g1_sig_edit.setEnabled(False)
        self.g1_sig_edit.setPlaceholderText("Sigma")
        self.anal_box.layout().addWidget(self.g1_sig_edit, 2, 2)
        self.g1_hei_edit =  QLineEdit()
        self.g1_hei_edit.setEnabled(False)
        self.g1_hei_edit.setPlaceholderText("Height")
        self.anal_box.layout().addWidget(self.g1_hei_edit, 2, 3)
        self.g1_gam_edit =  QLineEdit()
        self.g1_gam_edit.setEnabled(False)
        self.anal_box.layout().addWidget(self.g1_gam_edit, 2, 4)
        
        self.g2_label = QLabel("Peak 2:")
        self.anal_box.layout().addWidget(self.g2_label, 3, 0)
        self.g2_pos_edit =  QLineEdit()
        self.g2_pos_edit.setEnabled(False)
        self.g2_pos_edit.setPlaceholderText("Position")
        self.anal_box.layout().addWidget(self.g2_pos_edit, 3, 1)
        self.g2_sig_edit =  QLineEdit()
        self.g2_sig_edit.setEnabled(False)
        self.g2_sig_edit.setPlaceholderText("Sigma")
        self.anal_box.layout().addWidget(self.g2_sig_edit, 3, 2)
        self.g2_hei_edit =  QLineEdit()
        self.g2_hei_edit.setEnabled(False)
        self.g2_hei_edit.setPlaceholderText("Height")
        self.anal_box.layout().addWidget(self.g2_hei_edit, 3, 3)
        self.g2_gam_edit =  QLineEdit()
        self.g2_gam_edit.setEnabled(False)
        self.anal_box.layout().addWidget(self.g2_gam_edit, 3, 4)
        
        # a straight baseline has no curvature term, so say so rather than leaving
        # an empty box that looks like a reading that failed to arrive
        straight = str(self.parent.settings.get('baseline_degree', 2)) == '1'
        self.slope_label = QLabel("Baseline:")
        self.anal_box.layout().addWidget(self.slope_label, 4, 0)
        self.quad_edit =  QLineEdit()
        self.quad_edit.setEnabled(False)
        self.quad_edit.setPlaceholderText("n/a, straight" if straight else "Curvature")
        self.anal_box.layout().addWidget(self.quad_edit, 4, 1)
        self.slope_edit =  QLineEdit()
        self.slope_edit.setEnabled(False)
        self.slope_edit.setPlaceholderText("Slope")
        self.anal_box.layout().addWidget(self.slope_edit, 4, 2)
        self.int_edit =  QLineEdit()
        self.int_edit.setEnabled(False)
        self.int_edit.setPlaceholderText("Offset")
        self.anal_box.layout().addWidget(self.int_edit, 4, 3)
        self.show_gamma_boxes()


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



        # Populate Signal Generator box
        self.sg_box = QGroupBox('Discharge Signal Generator')
        self.sg_box.setLayout(QGridLayout())
        self.left.addWidget(self.sg_box)

        self.sg_freq_label = QLabel("Frequency (MHz):")
        self.sg_box.layout().addWidget(self.sg_freq_label, 0, 0)
        self.sg_freq_edit = QLineEdit()
        self.sg_freq_edit.setValidator(QDoubleValidator(0.0, 6000.0, 6, notation=QDoubleValidator.StandardNotation))
        self.sg_freq_edit.returnPressed.connect(self.set_freq_pushed)
        self.sg_box.layout().addWidget(self.sg_freq_edit, 0, 1)
        self.sg_freq_button = QPushButton("Set Frequency")
        self.sg_freq_button.clicked.connect(self.set_freq_pushed)
        self.sg_box.layout().addWidget(self.sg_freq_button, 0, 2)

        self.sg_amp_label = QLabel("Amplitude (Vpp):")
        self.sg_box.layout().addWidget(self.sg_amp_label, 1, 0)
        self.sg_amp_edit = QLineEdit()
        self.sg_amp_edit.setValidator(QDoubleValidator(0.0, 10.0, 4, notation=QDoubleValidator.StandardNotation))
        self.sg_amp_edit.returnPressed.connect(self.set_amp_pushed)
        self.sg_box.layout().addWidget(self.sg_amp_edit, 1, 1)
        self.sg_amp_button = QPushButton("Set Amplitude")
        self.sg_amp_button.clicked.connect(self.set_amp_pushed)
        self.sg_box.layout().addWidget(self.sg_amp_button, 1, 2)

        # Drive at the 3He Larmor frequency: stops the discharge and scans, drives
        # at the Larmor frequency for a time, then restores the discharge and scans
        self.sg_box.layout().addWidget(self.parent.divider(), 2, 0, 1, 3)
        self.larmor_field_label = QLabel("Field (T):")
        self.sg_box.layout().addWidget(self.larmor_field_label, 3, 0)
        self.larmor_field_edit = QLineEdit()
        self.larmor_field_edit.setValidator(QDoubleValidator(0.0, 20.0, 6, notation=QDoubleValidator.StandardNotation))
        self.larmor_field_edit.textChanged.connect(self.show_larmor_freq)
        self.sg_box.layout().addWidget(self.larmor_field_edit, 3, 1)
        self.larmor_freq_label = QLabel("")
        self.sg_box.layout().addWidget(self.larmor_freq_label, 3, 2)

        self.larmor_amp_label = QLabel("Drive Amplitude (Vpp):")
        self.sg_box.layout().addWidget(self.larmor_amp_label, 4, 0)
        self.larmor_amp_edit = QLineEdit()
        self.larmor_amp_edit.setValidator(QDoubleValidator(0.0, 10.0, 4, notation=QDoubleValidator.StandardNotation))
        self.sg_box.layout().addWidget(self.larmor_amp_edit, 4, 1)

        self.larmor_time_label = QLabel("Drive Time (s):")
        self.sg_box.layout().addWidget(self.larmor_time_label, 5, 0)
        self.larmor_time_edit = QLineEdit()
        self.larmor_time_edit.setValidator(QDoubleValidator(0.0, 3600.0, 3, notation=QDoubleValidator.StandardNotation))
        self.sg_box.layout().addWidget(self.larmor_time_edit, 5, 1)
        self.larmor_button = QPushButton("Drive at Larmor")
        self.larmor_button.clicked.connect(self.larmor_pushed)
        self.sg_box.layout().addWidget(self.larmor_button, 5, 2)

        self.larmor_status = QLabel("")
        self.sg_box.layout().addWidget(self.larmor_status, 6, 0, 1, 3)
        self.larmor_running = False


        # Populate Discharge Off Relaxation box
        self.rel_box = QGroupBox('Discharge-Off Relaxation')
        self.rel_box.setLayout(QVBoxLayout())
        self.left.addWidget(self.rel_box)
        self.rel_layout = QGridLayout()
        self.rel_box.layout().addLayout(self.rel_layout)

        self.dison_label = QLabel("Discharge On Time (s):")
        self.rel_layout.addWidget(self.dison_label, 0,0)
        self.dison_edit =  QLineEdit()
        self.dison_edit.setValidator(QDoubleValidator(3.0, 1000000, 10, notation=QDoubleValidator.StandardNotation))
        self.rel_layout.addWidget(self.dison_edit, 0, 1)

        self.disoff_label = QLabel("Discharge Off Time (s):")
        self.rel_layout.addWidget(self.disoff_label, 0,2)
        self.disoff_edit =  QLineEdit()
        self.disoff_edit.setValidator(QDoubleValidator(3.0, 1000000, 10, notation=QDoubleValidator.StandardNotation))
        self.rel_layout.addWidget(self.disoff_edit, 0, 3)

        self.disoff_button = QPushButton("Start",checkable=True)
        self.disoff_button.setEnabled(False)
        self.rel_layout.addWidget(self.disoff_button, 1, 3)
        self.disoff_button.clicked.connect(self.start_discharge_off_pushed)


        self.rel_box.layout().addWidget(self.parent.divider())

        self.note_layout = QGridLayout()
        self.rel_box.layout().addLayout(self.note_layout)
        self.dis_label = QLabel("Discharge off routine can be started once scans are running.")
        self.note_layout.addWidget(self.dis_label, 0,0)


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
        # components first, so the scan and the total fit draw on top of them
        self.base_plot = self.peak_wid.plot([], [], pen=self.base_pen, name='Background')
        self.g1_plot = self.peak_wid.plot([], [], pen=self.g1_pen, name='Peak 1')
        self.g2_plot = self.peak_wid.plot([], [], pen=self.g2_pen, name='Peak 2')
        self.peak_plot = self.peak_wid.plot([], [], pen=self.peak_pen, name='Scan')
        self.fit_plot = self.peak_wid.plot([], [], pen=self.fit_pen, name='Fit')
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

    def line_shape(self):
        '''The peak line shape picked, gauss or voigt'''
        return self.shape_combo.currentData()

    def show_gamma_boxes(self):
        '''Label the Lorentzian width boxes, which only a Voigt fit fills'''
        voigt = self.line_shape() == 'voigt'
        for edit in (self.g1_gam_edit, self.g2_gam_edit):
            edit.setPlaceholderText("Gamma" if voigt else "n/a, Gaussian")
            if not voigt:
                edit.setText("")

    def shape_changed(self):
        '''New line shape: the scan now running is the first one fit with it'''
        self.last_good_pf = None    # a fit of the other shape has the wrong parameters to seed from
        self.show_gamma_boxes()
        self.fit_status.setText(f"{self.shape_combo.currentText()} fit from the scan now running. "
                                "Set the zero amplitudes again under this shape.")
        self.fit_status.setStyleSheet("color: #aa6600")

    def scan_pushed(self):
        '''Start main loop if conditions met'''
               
        if self.scan_button.isChecked():        
            self.scan_button.setText('Stop')
            self.disoff_button.setEnabled(True)
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
        curr_list = np.linspace(start, stop, int(self.step_edit.text()))
        
        try:
            self.turn_on_discharge()
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
        if 'done' in status:     # got last part of scan, reset and send to event
            # starting parameters are estimated from the scan data by the event itself,
            # seeded with the last good fit since the peaks drift slowly during a run
            self.parent.end_event(self.scan_currs, self.scan_waves, self.scan_rs, self.scan_times,
                                  self.last_good_pf)
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
        event = self.parent.previous_event

        if len(event.x_axis) == len(event.rs):
            self.peak_plot.setData(event.x_axis, event.rs)
        for curve, ys in ((self.fit_plot, event.fit), (self.g1_plot, event.fit_g1),
                          (self.g2_plot, event.fit_g2), (self.base_plot, event.fit_base)):
            if len(ys) == len(event.x_axis):
                curve.setData(event.x_axis, ys)
            else:
                curve.setData([], [])    # no fit curve to show

        if not event.fit_good:    # show the scan, but don't seed from a bad fit or record its polarization
            self.fit_status.setText(event.fit_message)
            self.fit_status.setStyleSheet("color: #aa0000")
            return

        self.fit_status.setText(f"{scanfit.PROFILES[event.profile]} fit good, R2 = {event.rsq:.4f}")
        self.fit_status.setStyleSheet("color: #007700")
        self.last_good_pf = list(event.pf)    # starting point for the next scan's fit

        self.g1_pos_edit.setText(f"{event.pf[0]:.4f}")
        self.g1_sig_edit.setText(f"{event.pf[1]:.4f}")
        self.g1_hei_edit.setText(f"{event.pf[2]:.4f}")
        self.g2_pos_edit.setText(f"{event.pf[3]:.4f}")
        self.g2_sig_edit.setText(f"{event.pf[4]:.4f}")
        self.g2_hei_edit.setText(f"{event.pf[5]:.4f}")
        voigt = event.profile == 'voigt'
        self.g1_gam_edit.setText(f"{event.pf[6]:.4f}" if voigt else "")
        self.g2_gam_edit.setText(f"{event.pf[7]:.4f}" if voigt else "")
        # baseline coefficients, highest power first, referenced to mid-scan; there
        # is no curvature term at all when the baseline is set to straight
        coef = list(event.pf[scanfit.n_peak_pars(event.profile):])
        self.quad_edit.setText(f"{coef[0]:.3e}" if len(coef) > 2 else "")
        self.slope_edit.setText(f"{coef[-2]:.4f}")
        self.int_edit.setText(f"{coef[-1]:.4f}")

        self.peak1_edit.setText(f"{event.pf[2]:.4f}")
        self.peak2_edit.setText(f"{event.pf[5]:.4f}")

        self.pol_hist[event.stop_stamp] = event.pol*100
        time_list = list(self.pol_hist.keys())
        pol_list = [self.pol_hist[k] for k in self.pol_hist.keys()]
        self.pol_plot.setData(time_list, pol_list)
        self.pol_value.setText(f"{event.pol*100:.2f}%")

    def finish_scans(self):
        #if not self.relax_thread.isRunning():
        if self.larmor_running:    # scans stopped for a Larmor drive, which restarts them
            self.scan_button.setText("Larmor Drive")
            self.scan_button.setEnabled(False)
            return
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
        self.parent.siggen.enable_n(True)

    def set_freq_pushed(self):
        '''Send frequency from edit box to signal generator'''
        try:
            freq = float(self.sg_freq_edit.text())
            self.parent.siggen.set_freq(freq)
            self.parent.status_bar.showMessage(f"Set signal generator frequency to {freq} MHz")
        except Exception as e:
            self.parent.status_bar.showMessage(f"Failed to set signal generator frequency: {e}")
            print(f"Failed to set signal generator frequency: {e}")

    def set_amp_pushed(self):
        '''Send amplitude from edit box to signal generator'''
        try:
            amp = float(self.sg_amp_edit.text())
            self.parent.siggen.set_amp(amp)
            self.parent.status_bar.showMessage(f"Set signal generator amplitude to {amp} Vpp")
        except Exception as e:
            self.parent.status_bar.showMessage(f"Failed to set signal generator amplitude: {e}")
            print(f"Failed to set signal generator amplitude: {e}")

    def larmor_freq(self):
        '''3He Larmor frequency in MHz at the field in the edit box'''
        return HE3_GAMMA * float(self.larmor_field_edit.text())

    def show_larmor_freq(self):
        '''Show the Larmor frequency for the field entered'''
        try:
            freq = self.larmor_freq()
        except ValueError:
            self.larmor_freq_label.setText("")
            return
        self.larmor_freq_label.setText(f"Larmor: {freq:.6f} MHz")
        self.larmor_freq_label.setStyleSheet("" if freq >= N_OUT_MIN else "color: #aa0000")

    def larmor_pushed(self):
        '''Stop discharge and scans, drive at the Larmor frequency, then restart them'''
        if self.disoff_button.isChecked():
            self.larmor_status.setText("Stop the discharge-off relaxation before a Larmor drive.")
            return
        try:
            freq = self.larmor_freq()
            drive_amp = float(self.larmor_amp_edit.text())
            drive_time = float(self.larmor_time_edit.text())
            dis_freq = float(self.sg_freq_edit.text())
            dis_amp = float(self.sg_amp_edit.text())
        except ValueError:
            self.larmor_status.setText("Enter field, drive amplitude, drive time, and the discharge "
                                       "frequency and amplitude to return to.")
            return
        if freq < N_OUT_MIN:
            self.larmor_status.setText(f"Larmor frequency {freq:.4f} MHz is below the "
                                       f"{N_OUT_MIN} MHz floor of the type N output.")
            return

        restart = self.scan_button.isChecked()
        self.larmor_running = True
        self.larmor_button.setEnabled(False)
        self.disoff_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        if restart:
            self.scan_button.setChecked(False)    # scan thread stops at the end of this sweep
            self.scan_button.setText("Finishing...")
        else:
            self.scan_button.setText("Larmor Drive")

        self.larmor_thread = LarmorThread(self, freq, drive_amp, drive_time, dis_freq, dis_amp, restart)
        self.larmor_thread.status.connect(self.larmor_status.setText)
        self.larmor_thread.done.connect(self.larmor_finish)
        self.larmor_thread.start()

    def larmor_finish(self, restart):
        '''Larmor drive over, discharge settings restored: restart scans if they were running'''
        self.larmor_running = False
        self.larmor_button.setEnabled(True)
        self.scan_button.setEnabled(True)
        if restart:
            self.scan_button.setChecked(True)
            self.scan_button.setText('Stop')
            self.disoff_button.setEnabled(True)
            self.start_scan()
        else:
            self.scan_button.setText("Run Scan")

    def turn_off_laser(self):
        '''Turn off laser'''
        pass

    def relax_finish(self):
        self.finish_scans()
        self.disoff_button.setText("Start")
               
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
                except Exception as e:
                    print("error in lock in: ", e)
                    x,y,r = (0,0,0)
                #print("lock read time", datetime.datetime.now() - time2)
                #print("after lock", datetime.datetime.now() - time1)
                #if i%20 == 0:    
                #    print(f"point {i}:", curr, wave)
                self.reply.emit((curr, wave, float(x)*1000, datetime.datetime.now(), 'running'))    # turning lock-in V to mV
                
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

class LarmorThread(QThread):
    '''Thread class for driving the discharge signal generator at the 3He Larmor frequency
    Args:
        parent
        freq: Larmor frequency, MHz
        drive_amp: amplitude to drive at, Vpp
        drive_time: seconds to drive for
        dis_freq, dis_amp: discharge frequency (MHz) and amplitude (Vpp) to restore after
        restart: whether scans were running, waits for them to stop first
    '''
    status = pyqtSignal(str)     # status text
    done = pyqtSignal(bool)      # finished, carries restart

    def __init__(self, parent, freq, drive_amp, drive_time, dis_freq, dis_amp, restart):
        QThread.__init__(self)
        self.parent = parent
        self.freq = freq
        self.drive_amp = drive_amp
        self.drive_time = drive_time
        self.dis_freq = dis_freq
        self.dis_amp = dis_amp
        self.restart = restart

    def __del__(self):
        self.wait()

    def run(self):
        '''Stop discharge, drive at Larmor, restore discharge settings
        '''
        siggen = self.parent.parent.siggen
        try:
            if self.restart:
                self.status.emit("Waiting for scan to finish.")
                while self.parent.scan_thread.isRunning():
                    time.sleep(0.1)

            siggen.enable_n(False)
            siggen.set_freq(self.freq)
            siggen.set_amp(self.drive_amp)
            siggen.enable_n(True)
            start = time.time()
            left = self.drive_time
            while left > 0:
                self.status.emit(f"Driving at {self.freq:.6f} MHz for {left:.1f} more seconds.")
                time.sleep(min(0.1, left))
                left = self.drive_time - (time.time() - start)
            self.status.emit(f"Drove at {self.freq:.6f} MHz, {self.drive_amp} Vpp for {self.drive_time} s.")
        except Exception as e:
            self.status.emit(f"Larmor drive failed: {e}")
            print(f"Larmor drive failed: {e}")
        finally:
            try:    # always leave the generator at the discharge settings, output off
                siggen.enable_n(False)
                siggen.set_freq(self.dis_freq)
                siggen.set_amp(self.dis_amp)
            except Exception as e:
                self.status.emit(f"Failed to restore discharge settings: {e}")
                print(f"Failed to restore discharge settings: {e}")
            self.done.emit(self.restart)
