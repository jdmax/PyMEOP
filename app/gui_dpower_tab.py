'''PyMEOP discharge power tab, J.Maxwell

RF power delivered to the discharge, from the SR844 lock-in and PWR-8GHS power
sensor IOCs, with the calibrations it needs and an interlock on the reflection.
The physics and the calibration file are in discharge_power.py.

A reading is made each time the lock-in IOC polls. The IOC sets X, Y, R, phase
and reference frequency in that order, with the unlock and overload bits before
them, so the frequency arriving means the rest of that poll is in. The latest
power sensor reading is paired with it.
'''
import cmath
import collections
import math
import time

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import QThread, QTimer, pyqtSignal, Qt
from PyQt5.QtGui import QDoubleValidator
from PyQt5.QtWidgets import (QWidget, QLabel, QGroupBox, QHBoxLayout, QVBoxLayout, QGridLayout, QLineEdit,
                             QComboBox, QPushButton, QScrollArea, QFrame)

from app import discharge_power as dp

RED, ORANGE, GREEN, GREY = "color: #aa0000", "color: #aa6600", "color: #007700", "color: #999999"
STALE_S = 5.0          # a reading older than this is not shown as current, nor logged with a scan
SENSOR_STALE_S = 5.0   # sensor reading older than this is not paired with a lock-in reading
SENSOR_STEADY_DB = 0.3  # sensor readings further apart than this mean the drive level is changing
SETTLE_GAMMA = 0.05    # Gamma moved more than this since the sensor read: wait for the next sensor reading
SENSOR_WINDOW_DB = 10  # warn when the sensor is further than this from its target level
TC_AM_PERIODS = 3      # warn when the lock-in time constant is under this many AM periods
INTERLOCK_MODES = (('off', 'Off'), ('reduce', 'Reduce drive'), ('rf_off', 'RF off'))


def num(edit, default=None):
    '''A line edit's number, or default if it is empty

    Raises:
        ValueError: it holds something that is not a number
    '''
    text = edit.text().strip()
    if not text:
        if default is None:
            raise ValueError
        return default
    return float(text)


def show_w(p):
    if p is None:
        return "—"
    if abs(p) < 1:
        return f"{p * 1e3:.1f} mW"
    return f"{p:.2f} W"


def show_c(z, digits=4):
    return f"{z.real:+.{digits}f} {'+' if z.imag >= 0 else '−'} {abs(z.imag):.{digits}f}i"


class DischargeTab(QWidget):
    '''Discharge power tab: forward, reflected, net and plasma power, Gamma, and
    the Gamma, forward and tank calibrations they come from'''

    def __init__(self, parent):
        super(QWidget, self).__init__(parent)
        self.parent = parent
        self.pvs = parent.pvs
        self.cfg = parent.discharge
        self.latest = None      # last reading, a dict from read()
        self.sensor_last = None  # (time received, dBm, ok) of the last sensor reading
        self.collect = None     # standard or forward measurement being averaged
        self.pending = {}       # load and open measurements not yet made into a calibration
        self.over_count = 0     # consecutive readings over the interlock limit
        self.tripped = False
        self.sweep_thread = None
        self.am_thread = None
        self.sweep_result = None
        self.sweep_points = []

        if self.cfg is None:
            layout = QVBoxLayout(self)
            msg = QLabel("Give the lock-in and sensor channels in the discharge section of config.yaml "
                         "to measure discharge power.")
            msg.setWordWrap(True)
            layout.addWidget(msg)
            layout.addStretch(1)
            return

        self.n = self.cfg['pvs']
        self.store = dp.CalStore(self.cfg['cal_file'])
        self.model = dp.Model(self.store)
        self.history = collections.deque()

        self.main = QHBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.left = QVBoxLayout(inner)
        scroll.setWidget(inner)
        self.main.addWidget(scroll)

        self.build_readings()
        self.build_interlock()
        self.build_instruments()
        self.build_gamma_cal()
        self.build_forward_cal()
        self.build_tank()
        self.build_am_check()
        self.left.addStretch(1)
        if self.store.error:
            self.cal_problem = QLabel(self.store.error)
            self.cal_problem.setStyleSheet(RED)
            self.cal_problem.setWordWrap(True)
            self.left.insertWidget(0, self.cal_problem)

        # wide enough for the boxes, since they don't scroll sideways
        scroll.setMinimumWidth(inner.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 4)

        self.build_plots()
        self.show_cal()

        self.pvs.changed.connect(self.pv_changed)
        self.pvs.connection.connect(lambda name, conn: self.pv_changed(name))
        for name in (self.n['lockin_Sens'], self.n['lockin_TC'], self.n['sensor_Freq']):
            self.pv_changed(name)

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(500)
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.log_reading)
        self.log_timer.start(int(parent.log_interval * 1000))

    # Building ---------------------------------------------------------------

    def box(self, title, layout=None):
        b = QGroupBox(title)
        b.setLayout(layout or QGridLayout())
        self.left.addWidget(b)
        return b, b.layout()

    def edit(self, lo, hi, decimals, placeholder="", tip=""):
        e = QLineEdit()
        e.setValidator(QDoubleValidator(lo, hi, decimals, notation=QDoubleValidator.StandardNotation))
        e.setPlaceholderText(placeholder)
        e.setToolTip(tip)
        return e

    def note(self, text=""):
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def build_readings(self):
        _, g = self.box('Discharge Power')
        big = "font: 16pt"
        self.value_labels = {}
        for col, (key, head, tip) in enumerate((
                ('p_fwd', 'Forward', 'Sensor reading times K_fwd'),
                ('p_refl', 'Reflected', 'P_fwd |Γ|²'),
                ('p_net', 'Net', 'P_fwd (1 − |Γ|²), into the matching box'),
                ('p_plasma', 'Plasma', 'P_net (1 − Q_L/Q_U), less what the tank itself dissipates'))):
            g.addWidget(QLabel(f"<b>{head}</b>"), 0, col)
            label = QLabel("—")
            label.setStyleSheet(big)
            label.setToolTip(tip)
            g.addWidget(label, 1, col)
            self.value_labels[key] = label
        self.gamma_label = self.note("|Γ| —")
        self.gamma_label.setToolTip("Reflection coefficient at the cable end, from the Γ calibration")
        g.addWidget(self.gamma_label, 2, 0, 1, 2)
        self.detune_label = self.note()
        self.detune_label.setToolTip("Im(Γ) is zero at resonance and its sign gives the direction of "
                                     "detuning. The direction is known once a sweep has been adopted.")
        g.addWidget(self.detune_label, 2, 2, 1, 2)
        self.raw_label = self.note()
        self.raw_label.setStyleSheet(GREY)
        g.addWidget(self.raw_label, 3, 0, 1, 4)
        self.reading_status = self.note()
        g.addWidget(self.reading_status, 4, 0, 1, 4)

    def build_interlock(self):
        _, g = self.box('Reflection Interlock')
        g.addWidget(QLabel("Action:"), 0, 0)
        self.interlock_combo = QComboBox()
        for key, name in INTERLOCK_MODES:
            self.interlock_combo.addItem(name, key)
        self.interlock_combo.setToolTip("What to do when |Γ| stays over the limit: set the SG380 to the safe "
                                        "amplitude, or turn its type N output off. Acts only on calibrated, "
                                        "valid readings, after "
                                        f"{self.cfg['interlock_samples']} in a row over the limit.")
        self.interlock_combo.currentIndexChanged.connect(self.show_interlock)
        g.addWidget(self.interlock_combo, 0, 1)
        g.addWidget(QLabel("|Γ| Limit:"), 0, 2)
        self.limit_edit = self.edit(0.0, 1.0, 3, "0 to 1")
        self.limit_edit.textChanged.connect(self.show_interlock)
        g.addWidget(self.limit_edit, 0, 3)
        g.addWidget(QLabel("Safe Amplitude (Vpp):"), 1, 0)
        self.safe_amp_edit = self.edit(0.0, 10.0, 4, "for Reduce drive")
        g.addWidget(self.safe_amp_edit, 1, 1)
        self.reset_button = QPushButton("Reset")
        self.reset_button.setEnabled(False)
        self.reset_button.setToolTip("Re-arm after a trip. The drive is left as the interlock set it.")
        self.reset_button.clicked.connect(self.reset_interlock)
        g.addWidget(self.reset_button, 1, 3)
        self.interlock_status = self.note()
        g.addWidget(self.interlock_status, 2, 0, 1, 4)

    def build_instruments(self):
        _, g = self.box('Instruments')
        # kept in a dict, not as attributes, so the saved session leaves them alone;
        # they always show what the lock-in reads back
        self.combos = {}
        for row, (key, head, labels, tip) in enumerate((
                ('lockin_Sens', "Lock-in Sensitivity:", dp.SENS_LABELS,
                 "Fixed, never autoranged. Γ is corrected for a range other than the calibration's once "
                 "the 25 Ω standard has been measured on both."),
                ('lockin_TC', "Time Constant:", dp.TC_LABELS, "Several times the AM period"))):
            g.addWidget(QLabel(head), row, 0)
            combo = QComboBox()
            combo.addItems(labels)
            combo.setToolTip(tip)
            combo.activated.connect(lambda i, key=key: self.put(self.n[key], i))
            g.addWidget(combo, row, 1)
            self.combos[key] = combo
        self.sensor_freq_label = self.note()
        g.addWidget(self.sensor_freq_label, 2, 0, 1, 2)
        self.match_button = QPushButton("Match to Drive")
        self.match_button.setToolTip("Set the sensor's compensation frequency to the lock-in's reference "
                                     "frequency")
        self.match_button.clicked.connect(self.match_sensor)
        g.addWidget(self.match_button, 2, 2)

        g.addWidget(QLabel("AM Depth (%):"), 3, 0)
        self.am_depth_edit = self.edit(0.0, 100.0, 2, "0, no AM",
                                       "Sinusoidal AM depth of the drive. The sensor reads the sidebands too, "
                                       "1 + m²/2 of the carrier the lock-in sees, so it is divided out of M.")
        g.addWidget(self.am_depth_edit, 3, 1)
        self.am_read_button = QPushButton("Read from SG380")
        self.am_read_button.clicked.connect(self.read_am)
        g.addWidget(self.am_read_button, 3, 2)
        g.addWidget(QLabel("AM Rate (Hz):"), 4, 0)
        self.am_rate_edit = self.edit(0.0, 1e7, 3, "for the time constant check")
        g.addWidget(self.am_rate_edit, 4, 1)
        self.instr_status = self.note()
        g.addWidget(self.instr_status, 5, 0, 1, 3)

    def build_gamma_cal(self):
        _, g = self.box('Γ Calibration')
        g.addWidget(self.note("At about 0.5 W from the Tomco, connect each standard at the end of the cable "
                              "that goes to the matching box. Use one lock-in sensitivity for the load and "
                              "open."), 0, 0, 1, 4)
        g.addWidget(QLabel("Average (s):"), 1, 0)
        self.avg_edit = self.edit(1.0, 600.0, 1, "10")
        g.addWidget(self.avg_edit, 1, 1)

        self.std_labels = {}
        self.measure_buttons = []
        for row, (kind, name) in enumerate((('load', '50 Ω Load'), ('open', 'Open')), start=2):
            g.addWidget(QLabel(f"{name}:"), row, 0)
            button = QPushButton("Measure")
            button.clicked.connect(lambda checked, kind=kind: self.start_collect(kind))
            g.addWidget(button, row, 1)
            self.measure_buttons.append(button)
            label = self.note()
            g.addWidget(label, row, 2, 1, 2)
            self.std_labels[kind] = label
        self.save_cal_button = QPushButton("Save Calibration")
        self.save_cal_button.clicked.connect(self.save_gamma_cal)
        g.addWidget(self.save_cal_button, 4, 1)
        self.gamma_cal_label = self.note()
        g.addWidget(self.gamma_cal_label, 5, 0, 1, 4)

        g.addWidget(QLabel("25 Ω (R₁, R₂ Ω):"), 6, 0)
        self.r1_edit = self.edit(1.0, 1e4, 4, "R₁ by DMM", "Leave both empty for a nominal 25 Ω")
        g.addWidget(self.r1_edit, 6, 1)
        self.r2_edit = self.edit(1.0, 1e4, 4, "R₂ by DMM")
        g.addWidget(self.r2_edit, 6, 2)
        self.r25_button = QPushButton("Measure")
        self.r25_button.setToolTip("On the calibration's sensitivity: checks Γ against the resistances. "
                                   "On another sensitivity: measures that range's gain, which then "
                                   "corrects Γ there.")
        self.r25_button.clicked.connect(lambda: self.start_collect('r25'))
        g.addWidget(self.r25_button, 6, 3)
        self.measure_buttons.append(self.r25_button)
        self.r25_label = self.note()
        g.addWidget(self.r25_label, 7, 0, 1, 4)
        self.collect_status = self.note()
        g.addWidget(self.collect_status, 8, 0, 1, 4)

    def build_forward_cal(self):
        _, g = self.box('Forward Calibration')
        g.addWidget(self.note("Tomco → coupler → 40 dB attenuator → pad → reference meter, at operating "
                              "power after thermal equilibrium. Enter the reference reading and the "
                              "attenuation to it, measured beforehand at low power."), 0, 0, 1, 4)
        g.addWidget(QLabel("Reference (dBm):"), 1, 0)
        self.ref_dbm_edit = self.edit(-100.0, 60.0, 3)
        g.addWidget(self.ref_dbm_edit, 1, 1)
        g.addWidget(QLabel("Attenuation (dB):"), 1, 2)
        self.atten_edit = self.edit(0.0, 120.0, 3)
        g.addWidget(self.atten_edit, 1, 3)
        self.fwd_button = QPushButton("Record K_fwd")
        self.fwd_button.setToolTip("Average the sensor for the Γ calibration's averaging time and set "
                                   "K_fwd = reference power / sensor power")
        self.fwd_button.clicked.connect(lambda: self.start_collect('forward'))
        g.addWidget(self.fwd_button, 2, 1)
        self.measure_buttons.append(self.fwd_button)
        self.cal_edits = {}     # filled from the calibration file, so not saved with the session
        g.addWidget(QLabel("or K_fwd (dB):"), 3, 0)
        self.cal_edits['k_db'] = self.edit(-50.0, 150.0, 3)
        g.addWidget(self.cal_edits['k_db'], 3, 1)
        set_k = QPushButton("Set")
        set_k.clicked.connect(self.set_k_fwd)
        g.addWidget(set_k, 3, 2)
        self.fwd_label = self.note()
        g.addWidget(self.fwd_label, 4, 0, 1, 4)

    def build_tank(self):
        _, g = self.box('Tank Q and Resonance Sweep')
        g.addWidget(QLabel("Q_U:"), 0, 0)
        self.cal_edits['q_u'] = self.edit(0.0, 1e6, 2, "unlit or bench")
        g.addWidget(self.cal_edits['q_u'], 0, 1)
        g.addWidget(QLabel("Q_L:"), 0, 2)
        self.cal_edits['q_l'] = self.edit(0.0, 1e6, 2, "lit")
        g.addWidget(self.cal_edits['q_l'], 0, 3)
        set_q = QPushButton("Set Q")
        set_q.clicked.connect(self.set_q)
        g.addWidget(set_q, 1, 3)
        self.q_label = self.note()
        g.addWidget(self.q_label, 1, 0, 1, 3)

        g.addWidget(QLabel("Sweep (MHz):"), 2, 0)
        self.sweep_lo_edit = self.edit(0.95, 6000.0, 6, "from")
        g.addWidget(self.sweep_lo_edit, 2, 1)
        self.sweep_hi_edit = self.edit(0.95, 6000.0, 6, "to")
        g.addWidget(self.sweep_hi_edit, 2, 2)
        self.sweep_steps_edit = self.edit(5, 1000, 0, "steps")
        g.addWidget(self.sweep_steps_edit, 2, 3)
        g.addWidget(QLabel("Settle (s), Readings:"), 3, 0)
        self.sweep_settle_edit = self.edit(0.0, 60.0, 2, "settle", "Wait after each step before reading, "
                                           "long enough for the lock-in to relock and settle")
        g.addWidget(self.sweep_settle_edit, 3, 1)
        self.sweep_samples_edit = self.edit(1, 100, 0, "3", "Lock-in readings averaged at each step")
        g.addWidget(self.sweep_samples_edit, 3, 2)
        self.sweep_button = QPushButton("Run Sweep", checkable=True)
        self.sweep_button.setToolTip("Steps the SG380 over the range, reads Γ at each, fits the absorbed "
                                     "fraction 1 − |Γ|², and returns the SG380 to its frequency. Off "
                                     "resonance most of the drive is reflected, so sweep at low power; the "
                                     "interlock stays armed and stops the sweep if it trips.")
        self.sweep_button.clicked.connect(self.sweep_pushed)
        g.addWidget(self.sweep_button, 3, 3)
        self.sweep_status = self.note()
        g.addWidget(self.sweep_status, 4, 0, 1, 4)
        self.use_ql_button = QPushButton("Use as Q_L")
        self.use_ql_button.setToolTip("For a sweep of the lit discharge")
        self.use_ql_button.clicked.connect(lambda: self.adopt_sweep('q_l'))
        g.addWidget(self.use_ql_button, 5, 2)
        self.use_qu_button = QPushButton("Use Q_U Estimate")
        self.use_qu_button.setToolTip("For a sweep of the unlit tank: Q_U = Q_L (1 + β)")
        self.use_qu_button.clicked.connect(lambda: self.adopt_sweep('q_u'))
        g.addWidget(self.use_qu_button, 5, 3)
        for b in (self.use_ql_button, self.use_qu_button):
            b.setEnabled(False)

    def build_am_check(self):
        _, g = self.box('Sensor AM Check')
        g.addWidget(self.note("The sensor is specified for CW. This turns the SG380's AM off and on at a "
                              "fixed carrier and compares the sensor's readings with 1 + m²/2. It "
                              "interrupts the probe lock-in's reference, so stop scans first."), 0, 0, 1, 3)
        g.addWidget(QLabel("Settle (s):"), 1, 0)
        self.am_settle_edit = self.edit(0.0, 60.0, 1, "3")
        g.addWidget(self.am_settle_edit, 1, 1)
        self.am_check_button = QPushButton("Check AM")
        self.am_check_button.clicked.connect(self.am_check_pushed)
        g.addWidget(self.am_check_button, 1, 2)
        self.am_check_label = self.note()
        g.addWidget(self.am_check_label, 2, 0, 1, 3)

    def build_plots(self):
        right = QGridLayout()
        self.main.addLayout(right, 1)
        pg.setConfigOptions(antialias=True, background='w', foreground='k')    # as the run tab's

        self.power_wid = pg.PlotWidget(title='Power (W)', axisItems={'bottom': pg.DateAxisItem()})
        self.power_wid.showGrid(True, True)
        self.power_wid.addLegend(offset=(0.5, 0))
        self.fwd_plot = self.power_wid.plot([], [], pen=pg.mkPen((250, 0, 0), width=1.5), name='Forward')
        self.net_plot = self.power_wid.plot([], [], pen=pg.mkPen((0, 0, 250), width=1.5), name='Net')
        self.plasma_plot = self.power_wid.plot([], [], pen=pg.mkPen((0, 170, 0), width=1.5), name='Plasma')
        right.addWidget(self.power_wid, 0, 0)

        self.gamma_wid = pg.PlotWidget(title='Γ', axisItems={'bottom': pg.DateAxisItem()})
        self.gamma_wid.showGrid(True, True)
        self.gamma_wid.addLegend(offset=(0.5, 0))
        self.abs_plot = self.gamma_wid.plot([], [], pen=pg.mkPen((0, 0, 0), width=1.5), name='|Γ|')
        self.im_plot = self.gamma_wid.plot([], [], pen=pg.mkPen((250, 140, 0), width=1.5), name='Im Γ')
        self.limit_line = pg.InfiniteLine(angle=0, pen=pg.mkPen((170, 0, 0), width=1, style=Qt.DashLine))
        self.limit_line.setVisible(False)
        self.gamma_wid.addItem(self.limit_line)
        right.addWidget(self.gamma_wid, 1, 0)

        self.plane_wid = pg.PlotWidget(title='Γ Plane')
        self.plane_wid.showGrid(True, True)
        self.plane_wid.setAspectLocked(True)
        t = np.linspace(0, 2 * np.pi, 361)
        self.plane_wid.plot(np.cos(t), np.sin(t), pen=pg.mkPen((130, 130, 130), width=1))
        self.locus_plot = self.plane_wid.plot([], [], pen=pg.mkPen((0, 0, 250, 150), width=1),
                                              symbol='o', symbolSize=4, symbolBrush=(0, 0, 250))
        self.trail_plot = self.plane_wid.plot([], [], pen=None, symbol='o', symbolSize=4,
                                              symbolBrush=(250, 0, 0, 80), symbolPen=None)
        self.now_plot = self.plane_wid.plot([], [], pen=None, symbol='o', symbolSize=10, symbolBrush=(250, 0, 0))
        self.plane_wid.setXRange(-1.1, 1.1)
        self.plane_wid.setYRange(-1.1, 1.1)
        right.addWidget(self.plane_wid, 0, 1)

        self.sweep_wid = pg.PlotWidget(title='Sweep')
        self.sweep_wid.showGrid(True, True)
        self.sweep_wid.addLegend(offset=(0.5, 0))
        self.sweep_wid.setLabel('bottom', 'MHz')
        self.absorbed_plot = self.sweep_wid.plot([], [], pen=None, symbol='o', symbolSize=5,
                                                 symbolBrush=(0, 0, 250), name='1 − |Γ|²')
        self.sweep_fit_plot = self.sweep_wid.plot([], [], pen=pg.mkPen((0, 0, 250), width=1.5), name='Fit')
        self.sweep_im_plot = self.sweep_wid.plot([], [], pen=pg.mkPen((250, 140, 0), width=1.5),
                                                 symbol='o', symbolSize=4, symbolBrush=(250, 140, 0), name='Im Γ')
        right.addWidget(self.sweep_wid, 1, 1)

    # PVs --------------------------------------------------------------------

    def value(self, key):
        return self.pvs.value(self.n[key])

    def put(self, name, value, source='user'):
        try:
            self.pvs.put(name, value, source)
            return True
        except Exception as e:
            self.instr_status.setText(str(e))
            self.instr_status.setStyleSheet(RED)
            return False

    def pv_changed(self, name):
        n = self.n
        if name == n['lockin_Freq']:
            self.read()
        elif name == n['sensor']:
            self.sensor_reading()
        elif name in (n['lockin_Sens'], n['lockin_TC']):
            key = 'lockin_Sens' if name == n['lockin_Sens'] else 'lockin_TC'
            v = self.value(key)
            combo = self.combos[key]
            combo.setEnabled(v is not None)
            if isinstance(v, (int, float)) and 0 <= int(v) < combo.count():
                combo.setCurrentIndex(int(v))
        elif name == n['sensor_Freq']:
            f = self.value('sensor_Freq')
            self.sensor_freq_label.setText("Sensor compensation: " +
                                           (f"{f:.4f} MHz" if isinstance(f, (int, float)) else "disconnected"))

    def sensor_reading(self):
        '''New power sensor reading: note it, and add it to a forward calibration under way'''
        dbm = self.value('sensor')
        ok = isinstance(dbm, (int, float)) and dp.SENSOR_FLOOR_DBM < dbm <= dp.SENSOR_MAX_DBM
        prev = self.sensor_last
        steady = ok and prev is not None and prev[2] and abs(dbm - prev[1]) < SENSOR_STEADY_DB
        x, y = self.value('lockin_X'), self.value('lockin_Y')   # the reflection this reading goes with
        xy = complex(x, y) if None not in (x, y) else None
        self.sensor_last = (time.time(), dbm, ok, steady, xy)
        c = self.collect
        if ok and c and c['kind'] == 'forward':
            c['dbms'].append(dbm)

    def am_depth(self):
        return num(self.am_depth_edit, 0.0) / 100

    # Readings ---------------------------------------------------------------

    def read(self):
        '''Make a reading from the lock-in poll just in and the latest sensor reading'''
        now = time.time()
        bad, warn = [], []
        x, y, r = self.value('lockin_X'), self.value('lockin_Y'), self.value('lockin_R')
        freq = self.value('lockin_Freq')
        sens, tc = self.value('lockin_Sens'), self.value('lockin_TC')
        if None in (x, y, r, freq, sens, tc):
            bad.append("lock-in disconnected")
        else:
            sens, tc = int(sens), int(tc)
        if self.value('lockin_Unlock'):
            bad.append("lock-in reference unlocked")
        if self.value('lockin_Overload'):
            bad.append("lock-in overload")
        if not bad and (self.pvs.severity(self.n['lockin_X']) or 0) >= 2:
            bad.append("lock-in front panel used")

        dbm = None
        if self.sensor_last is None or self.sensor_last[1] is None:
            bad.append("sensor disconnected")
        else:
            received, dbm, ok, steady, xy_ref = self.sensor_last
            if not ok:
                bad.append("sensor out of range" if dbm > dp.SENSOR_FLOOR_DBM else "sensor below range")
            elif now - received > SENSOR_STALE_S:
                bad.append("sensor reading stale")
            elif not steady:
                # the lock-in polls faster than the sensor, so just after a drive step it would be
                # divided by the power from before the step; wait for two sensor readings that agree
                bad.append("drive level settling")
            elif abs(dbm - self.cfg['sensor_target_dbm']) > SENSOR_WINDOW_DB:
                warn.append(f"sensor at {dbm:.1f} dBm, target {self.cfg['sensor_target_dbm']:g}")

        try:
            am = self.am_depth()
        except ValueError:
            am = 0.0
            warn.append("AM depth not a number, taken as 0")

        rec = {'stamp': now, 'ok': False, 'freq_hz': freq, 'x': x, 'y': y, 'r': r, 'sens': sens, 'tc': tc,
               'sensor_dbm': dbm, 'am_depth': am, 'm': None, 'gamma': None,
               'p_fwd': None, 'p_refl': None, 'p_net': None, 'p_plasma': None}
        if not bad:
            rec['m'] = dp.normalized(x, y, dbm, am)
            rec['gamma'], gwarn = self.model.gamma(rec['m'], sens)
            if rec['gamma'] is not None and xy_ref is not None:
                # the reflection moved since the sensor last read, maybe with the drive level;
                # hold off until the sensor catches up, rather than trust a stale pairing
                g_ref, _ = self.model.gamma(dp.normalized(xy_ref.real, xy_ref.imag, dbm, am), sens)
                if abs(rec['gamma'] - g_ref) > SETTLE_GAMMA:
                    bad.append("reflection changed since the last sensor reading")
                    rec['gamma'] = None
        if not bad:
            rec['ok'] = True
            warn += gwarn
            rec.update(self.model.powers(dbm, rec['gamma']))
            if self.store.forward is None:
                warn.append("no forward calibration")
            warn += self.condition_warnings(rec)
            c = self.collect
            if c and c['kind'] != 'forward':
                for key, v in (('ms', rec['m']), ('dbms', dbm), ('freqs', freq), ('sens', sens), ('tc', tc)):
                    c[key].append(v)
        rec['bad'], rec['warn'] = bad, warn
        self.latest = rec
        if rec['ok']:
            self.history.append(rec)
            cutoff = now - self.cfg['history_minutes'] * 60
            while self.history and self.history[0]['stamp'] < cutoff:
                self.history.popleft()
        self.check_interlock(rec)
        self.show_reading(rec)

    def condition_warnings(self, rec):
        '''Ways the conditions differ from the calibration's or from what the instruments need'''
        warn = []
        freq = rec['freq_hz']
        tol = self.cfg['recal_tolerance']
        cal = self.store.gamma
        if cal and abs(freq - cal['freq_hz']) > tol * cal['freq_hz']:
            warn.append(f"drive at {freq/1e6:.4f} MHz, Γ calibrated at {cal['freq_hz']/1e6:.4f} MHz: recalibrate")
        fwd = self.store.forward
        if fwd and fwd.get('freq_hz') and abs(freq - fwd['freq_hz']) > tol * fwd['freq_hz']:
            warn.append(f"K_fwd measured at {fwd['freq_hz']/1e6:.4f} MHz")
        if cal and not math.isclose(rec['am_depth'], cal.get('am_depth', 0.0), abs_tol=0.01):
            warn.append(f"AM depth {rec['am_depth']*100:.0f}%, calibrated at {cal.get('am_depth', 0)*100:.0f}%")
        sf = self.value('sensor_Freq')
        if isinstance(sf, (int, float)) and abs(sf * 1e6 - freq) > 0.05 * freq:
            warn.append(f"sensor compensation {sf:.3f} MHz, drive {freq/1e6:.3f} MHz")
        try:
            rate = num(self.am_rate_edit, 0.0)
        except ValueError:
            rate = 0.0
        if rate > 0 and rec['am_depth'] > 0 and dp.TC_S[rec['tc']] < TC_AM_PERIODS / rate:
            warn.append(f"time constant {dp.TC_LABELS[rec['tc']]} under {TC_AM_PERIODS} AM periods")
        return warn

    def show_reading(self, rec):
        for key, label in self.value_labels.items():
            label.setText(show_w(rec[key]))
            label.setStyleSheet("font: 16pt" + ("" if rec['ok'] else f"; {GREY}"))
        g = rec['gamma']
        if g is not None:
            vswr = (1 + abs(g)) / (1 - abs(g)) if abs(g) < 1 else float('inf')
            self.gamma_label.setText(f"|Γ| {abs(g):.4f}  ∠ {math.degrees(cmath.phase(g)):+.1f}°  VSWR {vswr:.2f}")
            text = f"Im Γ {g.imag:+.4f}"
            slope = self.store.tank.get('im_slope')
            if slope and abs(g.imag) > 0.005:
                text += ", drive above resonance" if g.imag * slope > 0 else ", drive below resonance"
            self.detune_label.setText(text)
        else:
            self.gamma_label.setText("|Γ| —")
            self.detune_label.setText("")
        if rec['freq_hz'] is not None and rec['x'] is not None:
            fs = f" ({rec['r'] / dp.SENS_V[rec['sens']] * 100:.0f}% of range)" if rec['sens'] is not None else ""
            sensor = f"{rec['sensor_dbm']:.2f} dBm" if rec['sensor_dbm'] is not None else "—"
            m = f", M {show_c(rec['m'], 3)} V/√W" if rec['m'] is not None else ""
            self.raw_label.setText(f"{rec['freq_hz']/1e6:.4f} MHz, sensor {sensor}, "
                                   f"R {rec['r']*1e3:.4g} mV{fs}{m}")
        if rec['bad']:
            self.reading_status.setText("Not valid: " + "; ".join(rec['bad']))
            self.reading_status.setStyleSheet(RED)
        elif rec['warn']:
            self.reading_status.setText("; ".join(rec['warn']))
            self.reading_status.setStyleSheet(ORANGE)
        else:
            self.reading_status.setText("OK")
            self.reading_status.setStyleSheet(GREEN)
        self.update_plots()

    def update_plots(self):
        h = self.history
        t = np.array([r['stamp'] for r in h])
        nan = float('nan')
        for plot, key in ((self.fwd_plot, 'p_fwd'), (self.net_plot, 'p_net'), (self.plasma_plot, 'p_plasma')):
            plot.setData(t, np.array([nan if r[key] is None else r[key] for r in h]), connect='finite')
        gs = np.array([nan if r['gamma'] is None else r['gamma'] for r in h], dtype=complex)
        self.abs_plot.setData(t, np.abs(gs), connect='finite')
        self.im_plot.setData(t, gs.imag, connect='finite')
        trail = gs[-120:]
        trail = trail[np.isfinite(trail)]
        self.trail_plot.setData(trail.real, trail.imag)
        g = self.latest['gamma'] if self.latest else None
        self.now_plot.setData([g.real] if g is not None else [], [g.imag] if g is not None else [])

    def tick(self):
        '''Finish a measurement whose time is up, and grey out a reading gone stale'''
        now = time.time()
        if self.collect and now >= self.collect['until']:
            self.finish_collect()
        if self.latest and now - self.latest['stamp'] > STALE_S:
            self.reading_status.setText(f"No lock-in reading for {now - self.latest['stamp']:.0f} s")
            self.reading_status.setStyleSheet(RED)
            for label in self.value_labels.values():
                label.setStyleSheet(f"font: 16pt; {GREY}")
        elif self.latest is None:
            self.reading_status.setText("Waiting for the lock-in")
            self.reading_status.setStyleSheet(GREY)

    def fresh(self):
        '''The latest reading if it is current and valid, else None'''
        rec = self.latest
        if rec and rec['ok'] and time.time() - rec['stamp'] < STALE_S:
            return rec
        return None

    def record(self):
        '''The latest valid reading as plain values, for the logs; None if there is none'''
        rec = self.fresh()
        if rec is None:
            return None
        out = {k: v for k, v in rec.items() if k not in ('ok', 'bad', 'm', 'gamma')}
        out['m'] = dp.pair(rec['m'])
        out['gamma'] = dp.pair(rec['gamma']) if rec['gamma'] is not None else None
        cal = self.store.gamma
        out['gamma_cal_time'] = cal['time'] if cal else None
        fwd = self.store.forward
        out['k_fwd'] = fwd['k_fwd'] if fwd else None
        out['q_u'], out['q_l'] = self.store.tank.get('q_u'), self.store.tank.get('q_l')
        return out

    def event_record(self):
        '''Discharge power to write with a scan'''
        if self.cfg is None:
            return None
        return self.record()

    def log_reading(self):
        rec = self.record()
        if rec is not None:
            self.parent.slowlog.write('discharge', **rec)

    # Interlock --------------------------------------------------------------

    def interlock_limit(self):
        try:
            limit = num(self.limit_edit)
        except ValueError:
            return None
        return limit if 0 < limit <= 1 else None

    def show_interlock(self):
        mode = self.interlock_combo.currentData()
        limit = self.interlock_limit()
        self.limit_line.setVisible(mode != 'off' and limit is not None)
        if limit is not None:
            self.limit_line.setValue(limit)
        if self.tripped:
            return
        self.over_count = 0
        if mode == 'off':
            self.interlock_status.setText("Off")
            self.interlock_status.setStyleSheet(GREY)
        elif limit is None:
            self.interlock_status.setText("Not armed: enter a |Γ| limit from 0 to 1.")
            self.interlock_status.setStyleSheet(RED)
        elif self.store.gamma is None:
            self.interlock_status.setText("Not armed: no Γ calibration.")
            self.interlock_status.setStyleSheet(RED)
        else:
            self.interlock_status.setText(f"Armed at |Γ| > {limit:g}")
            self.interlock_status.setStyleSheet(GREEN)

    def check_interlock(self, rec):
        mode = self.interlock_combo.currentData()
        limit = self.interlock_limit()
        if mode == 'off' or self.tripped or limit is None or not rec['ok'] or rec['gamma'] is None:
            return
        self.over_count = self.over_count + 1 if abs(rec['gamma']) > limit else 0
        if self.over_count >= self.cfg['interlock_samples']:
            self.trip(mode, abs(rec['gamma']), limit)

    def trip(self, mode, gabs, limit):
        '''Reduce the drive or turn it off, and latch until reset'''
        self.tripped = True
        self.reset_button.setEnabled(True)
        if self.sweep_thread and self.sweep_thread.isRunning():
            self.sweep_thread.stop = True
        siggen = getattr(self.parent, 'siggen', None)
        when = time.strftime('%H:%M:%S')
        action = None
        try:
            if siggen is None:
                raise RuntimeError("signal generator not connected")
            amp = None
            if mode == 'reduce':
                try:
                    amp = num(self.safe_amp_edit)
                except ValueError:
                    amp = None    # no safe amplitude, so turn it off instead
            if amp is not None:
                siggen.set_amp(amp)
                self.parent.slowlog.set('siggen.amp', amp, 'interlock')
                self.parent.run_tab.sg_amp_edit.setText(f"{amp:.4f}")
                action = f"drive reduced to {amp:g} Vpp"
            else:
                self.parent.run_tab.turn_off_discharge('interlock')
                action = "RF output turned off"
            msg = f"TRIPPED {when}: |Γ| {gabs:.3f} over {limit:g}, {action}."
        except Exception as e:
            msg = f"TRIPPED {when}: |Γ| {gabs:.3f} over {limit:g}, but could not act on the SG380: {e}"
        self.parent.slowlog.set('discharge.interlock', {'gamma_abs': gabs, 'limit': limit, 'action': action},
                                'interlock')
        self.interlock_status.setText(msg)
        self.interlock_status.setStyleSheet(RED + "; font-weight: bold")
        self.parent.status_bar.showMessage(msg)

    def reset_interlock(self):
        self.tripped = False
        self.reset_button.setEnabled(False)
        self.parent.slowlog.set('discharge.interlock', 'reset', 'user')
        self.show_interlock()

    # Instruments ------------------------------------------------------------

    def match_sensor(self):
        freq = self.latest['freq_hz'] if self.latest else None
        if not freq:
            self.instr_status.setText("No lock-in reference frequency to match.")
            self.instr_status.setStyleSheet(RED)
            return
        mhz = round(freq / 1e6, 4)
        if self.put(self.n['sensor_Freq'], mhz):
            self.instr_status.setText(f"Set sensor compensation to {mhz:g} MHz.")
            self.instr_status.setStyleSheet(GREEN)

    def read_am(self):
        siggen = getattr(self.parent, 'siggen', None)
        try:
            if siggen is None:
                raise RuntimeError("signal generator not connected")
            depth, rate, on = siggen.read_am()
        except Exception as e:
            self.instr_status.setText(f"Could not read AM from the SG380: {e}")
            self.instr_status.setStyleSheet(RED)
            return
        self.am_depth_edit.setText(f"{depth * 100:g}")
        self.am_rate_edit.setText(f"{rate:g}")
        self.instr_status.setText(f"SG380 AM {'on' if on else 'off'}: depth {depth*100:g}%, rate {rate:g} Hz.")
        self.instr_status.setStyleSheet("")

    # Calibration measurements -----------------------------------------------

    def start_collect(self, kind):
        '''Start averaging readings for a standard ('load', 'open', 'r25') or K_fwd ('forward')'''
        try:
            secs = num(self.avg_edit, 10.0)
            if kind == 'forward':
                num(self.ref_dbm_edit), num(self.atten_edit)
            if kind == 'r25':
                if self.store.gamma is None:
                    raise RuntimeError("Save a Γ calibration before measuring the 25 Ω standard.")
                self.r_ohms()
        except ValueError:
            self.show_collect("Enter the averaging time" + (", reference reading and attenuation"
                              if kind == 'forward' else "") + (", and both resistances or neither"
                              if kind == 'r25' else "") + ".", RED, kind)
            return
        except RuntimeError as e:
            self.show_collect(str(e), RED, kind)
            return
        self.collect = {'kind': kind, 'until': time.time() + secs, 'ms': [], 'dbms': [], 'freqs': [],
                        'sens': [], 'tc': []}
        for b in self.measure_buttons:
            b.setEnabled(False)
        names = {'load': '50 Ω load', 'open': 'open', 'r25': '25 Ω standard', 'forward': 'forward power'}
        self.show_collect(f"Measuring the {names[kind]} for {secs:g} s...", "", kind)

    def show_collect(self, msg, style, kind=None):
        label = self.fwd_label if kind == 'forward' else self.collect_status
        label.setText(msg)
        label.setStyleSheet(style)
        self.parent.status_bar.showMessage(msg)

    def r_ohms(self):
        '''The 25 ohm standard's two resistances, or () for nominal

        Raises:
            ValueError: only one is given, or one is not a number
        '''
        r1, r2 = self.r1_edit.text().strip(), self.r2_edit.text().strip()
        if not r1 and not r2:
            return ()
        return (float(r1), float(r2))

    def finish_collect(self):
        c = self.collect
        for b in self.measure_buttons:
            b.setEnabled(True)
        try:
            if c['kind'] == 'forward':
                self.finish_forward(c)
                return
            if len(c['ms']) < 3:
                raise ValueError(f"only {len(c['ms'])} valid readings; check the lock-in and sensor")
            if len(set(c['sens'])) > 1 or len(set(c['tc'])) > 1:
                raise ValueError("lock-in sensitivity or time constant changed during the measurement")
            meas = dp.standard_record(c['ms'], c['dbms'], np.mean(c['freqs']), c['sens'][0], c['tc'][0],
                                      self.am_depth())
            if c['kind'] in ('load', 'open'):
                self.pending[c['kind']] = meas
                self.std_labels[c['kind']].setText(f"M {show_c(dp.unpair(meas['m']), 4)} "
                                                   f"± {abs(dp.unpair(meas['sem'])):.1g}, "
                                                   f"{dp.SENS_LABELS[meas['sens']]}, {meas['sensor_dbm']:.1f} dBm")
                self.show_collect(f"Measured the {'50 Ω load' if c['kind'] == 'load' else 'open'} "
                                  f"from {meas['n']} readings.", GREEN)
            else:
                entry = self.store.add_r25(meas, self.r_ohms())
                self.parent.slowlog.set('discharge.r25', entry, 'user')
                self.show_r25()
                self.show_collect(f"Measured the 25 Ω standard from {meas['n']} readings.", GREEN)
        except Exception as e:
            self.show_collect(f"Measurement failed: {e}", RED, c['kind'])
        finally:
            self.collect = None

    def finish_forward(self, c):
        if len(c['dbms']) < 2:
            raise ValueError(f"only {len(c['dbms'])} valid sensor readings")
        sensor_w = float(np.mean([dp.dbm_to_w(d) for d in c['dbms']]))   # average power, not dBm
        ref_dbm, atten = num(self.ref_dbm_edit), num(self.atten_edit)
        p_true = dp.dbm_to_w(ref_dbm) * 10 ** (atten / 10)
        freq = self.latest['freq_hz'] if self.latest else None
        fwd = self.store.set_forward(p_true / sensor_w, freq, method='measured', ref_dbm=ref_dbm,
                                     atten_db=atten, sensor_dbm=dp.w_to_dbm(sensor_w), n=len(c['dbms']),
                                     am_depth=self.am_depth())
        self.parent.slowlog.set('discharge.k_fwd', fwd, 'user')
        self.show_cal()
        self.fwd_label.setText(self.fwd_label.text() + f"\nRecorded from {len(c['dbms'])} sensor readings: "
                               f"forward {show_w(p_true)}.")
        self.fwd_label.setStyleSheet(GREEN)

    def save_gamma_cal(self):
        if 'load' not in self.pending or 'open' not in self.pending:
            self.show_collect("Measure the 50 Ω load and the open first.", RED)
            return
        try:
            cal = self.store.set_gamma(self.pending['load'], self.pending['open'])
        except Exception as e:
            self.show_collect(f"Calibration not saved: {e}", RED)
            return
        self.pending = {}
        for label in self.std_labels.values():
            label.setText("")
        self.parent.slowlog.set('discharge.gamma_cal', {k: cal[k] for k in ('freq_hz', 'sens', 'tc', 'am_depth',
                                                                             'e_d', 'e_r')}, 'user')
        self.show_cal()
        self.show_collect("Saved the Γ calibration. Check it with the 25 Ω standard.", GREEN)

    def set_k_fwd(self):
        try:
            db = num(self.cal_edits['k_db'])
            freq = self.latest['freq_hz'] if self.latest else None
            fwd = self.store.set_forward(10 ** (db / 10), freq, method='entered')
        except Exception as e:
            self.fwd_label.setText(f"Enter K_fwd in dB. {e}")
            self.fwd_label.setStyleSheet(RED)
            return
        self.parent.slowlog.set('discharge.k_fwd', fwd, 'user')
        self.show_cal()

    def set_q(self):
        values = {}
        try:
            for key in ('q_u', 'q_l'):
                text = self.cal_edits[key].text().strip()
                values[key] = float(text) if text else None
        except ValueError:
            self.q_label.setText("Q_U and Q_L must be numbers, or empty.")
            self.q_label.setStyleSheet(RED)
            return
        self.store.set_tank(**values)
        self.parent.slowlog.set('discharge.tank', values, 'user')
        self.show_cal()

    def show_cal(self):
        '''Show the calibrations in use, from the calibration file'''
        cal = self.store.gamma
        if cal:
            e_d, e_r = dp.unpair(cal['e_d']), dp.unpair(cal['e_r'])
            directivity = -20 * math.log10(abs(e_d / e_r)) if abs(e_d) > 0 else float('inf')
            self.gamma_cal_label.setText(
                f"In use: {cal['time'][:19]} UTC at {cal['freq_hz']/1e6:.4f} MHz, {dp.SENS_LABELS[cal['sens']]}, "
                f"{dp.TC_LABELS[cal['tc']]}, AM {cal.get('am_depth', 0)*100:.0f}%\n"
                f"E_d {show_c(e_d)}, E_r {show_c(e_r)} V/√W, |E_d/E_r| {directivity:.1f} dB")
            self.gamma_cal_label.setStyleSheet("")
        else:
            self.gamma_cal_label.setText("No Γ calibration.")
            self.gamma_cal_label.setStyleSheet(RED)
        self.show_r25()

        fwd = self.store.forward
        if fwd:
            where = f" at {fwd['freq_hz']/1e6:.4f} MHz" if fwd.get('freq_hz') else ""
            how = (f", reference {fwd['ref_dbm']:g} dBm + {fwd['atten_db']:g} dB" if fwd.get('method') == 'measured'
                   else ", entered")
            self.fwd_label.setText(f"In use: K_fwd {fwd['k_fwd_db']:.3f} dB, {fwd['time'][:19]} UTC{where}{how}")
            self.fwd_label.setStyleSheet("")
            self.cal_edits['k_db'].setText(f"{fwd['k_fwd_db']:.3f}")
        else:
            self.fwd_label.setText("No forward calibration.")
            self.fwd_label.setStyleSheet(RED)

        tank = self.store.tank
        for key in ('q_u', 'q_l'):
            v = tank.get(key)
            self.cal_edits[key].setText(f"{v:g}" if v else "")
        q_u, q_l = tank.get('q_u'), tank.get('q_l')
        if q_u and q_l and 0 < q_l <= q_u:
            self.q_label.setText(f"Plasma takes 1 − Q_L/Q_U = {(1 - q_l / q_u)*100:.1f}% of the net power.")
            self.q_label.setStyleSheet("")
        elif q_u and q_l:
            self.q_label.setText("Q_L is above Q_U, so plasma power is not shown.")
            self.q_label.setStyleSheet(RED)
        else:
            self.q_label.setText("Set both Qs for plasma power.")
            self.q_label.setStyleSheet(GREY)
        self.show_interlock()

    def show_r25(self):
        cal = self.store.gamma
        lines = []
        if cal:
            checks = [c for c in cal['checks'] if c['kind'] == 'check']
            if checks:
                c = checks[-1]
                g = dp.unpair(c['gamma'])
                lines.append(f"Check {c['time'][11:19]}: Γ {show_c(g)}, expected {c['expected']:+.4f}, "
                             f"error {c['error']:.4f}")
            for sens, gain in sorted((int(k), dp.unpair(v)) for k, v in cal['range_gains'].items()):
                lines.append(f"Gain on {dp.SENS_LABELS[sens]}: {abs(gain):.4f} ∠ "
                             f"{math.degrees(cmath.phase(gain)):+.2f}°")
        self.r25_label.setText("\n".join(lines))

    # Resonance sweep --------------------------------------------------------

    def sweep_pushed(self):
        if not self.sweep_button.isChecked():
            if self.sweep_thread and self.sweep_thread.isRunning():
                self.sweep_thread.stop = True
                self.sweep_button.setText("Stopping...")
            return
        run_tab = self.parent.run_tab
        problem = None
        try:
            lo, hi = num(self.sweep_lo_edit), num(self.sweep_hi_edit)
            steps = int(num(self.sweep_steps_edit))
            settle = num(self.sweep_settle_edit)
            samples = int(num(self.sweep_samples_edit, 3))
        except ValueError:
            problem = "Enter the sweep range, steps and settle time."
        if not problem:
            if getattr(self.parent, 'siggen', None) is None:
                problem = "The signal generator is not connected."
            elif self.store.gamma is None:
                problem = "A sweep needs a Γ calibration."
            elif not lo < hi or steps < 5:
                problem = "The sweep needs a low and high frequency and at least 5 steps."
            elif run_tab.larmor_running or run_tab.disoff_button.isChecked() or (
                    self.am_thread and self.am_thread.isRunning()):
                problem = "Wait for the Larmor drive, relaxation or AM check to finish."
            elif self.tripped:
                problem = "Reset the interlock first."
        if problem:
            self.sweep_status.setText(problem)
            self.sweep_status.setStyleSheet(RED)
            self.sweep_button.setChecked(False)
            return
        self.sweep_points = []
        self.sweep_result = None
        for b in (self.use_ql_button, self.use_qu_button):
            b.setEnabled(False)
        self.sweep_fit_plot.setData([], [])
        self.sweep_button.setText("Stop Sweep")
        self.sweep_thread = SweepThread(self, np.linspace(lo, hi, steps) * 1e6, settle, max(samples, 1))
        self.sweep_thread.point.connect(self.sweep_point)
        self.sweep_thread.status.connect(self.sweep_status.setText)
        self.sweep_thread.done.connect(self.sweep_done)
        self.sweep_status.setStyleSheet("")
        self.sweep_thread.start()

    def sweep_point(self, freq, gamma):
        self.sweep_points.append((freq, gamma))
        f = np.array([p[0] for p in self.sweep_points]) / 1e6
        g = np.array([p[1] for p in self.sweep_points], dtype=complex)
        self.absorbed_plot.setData(f, 1 - np.abs(g) ** 2)
        self.sweep_im_plot.setData(f, g.imag)
        self.locus_plot.setData(g.real, g.imag)

    def sweep_done(self, msg):
        self.sweep_button.setChecked(False)
        self.sweep_button.setText("Run Sweep")
        pts = self.sweep_points
        try:
            res = dp.fit_resonance([p[0] for p in pts], [p[1] for p in pts])
        except ValueError as e:
            self.sweep_status.setText(f"{msg} {e}".strip())
            self.sweep_status.setStyleSheet(RED)
            return
        self.sweep_result = res
        self.sweep_fit_plot.setData(res['fit_f'] / 1e6, res['fit'])
        coupling = "over" if res['overcoupled'] else "under"
        err = f" ± {res['q_l_err']:.1f}" if math.isfinite(res['q_l_err']) else ""
        self.sweep_status.setText(
            f"{msg} Resonance {res['f0']/1e6:.5f} MHz, Q_L {res['q_l']:.1f}{err}, "
            f"|Γ₀| {abs(res['gamma0']):.3f}, {coupling}coupled (phase swing {res['phase_swing_deg']:.0f}°), "
            f"β {res['beta']:.2f}, so Q_U ≈ {res['q_u_est']:.1f} if the tank was unlit.".strip())
        self.sweep_status.setStyleSheet("")
        for b in (self.use_ql_button, self.use_qu_button):
            b.setEnabled(True)

    def adopt_sweep(self, key):
        res = self.sweep_result
        if res is None:
            return
        q = res['q_l'] if key == 'q_l' else res['q_u_est']
        values = {key: round(float(q), 2), 'f0_hz': float(res['f0'])}
        if res['im_slope']:
            values['im_slope'] = float(res['im_slope'])
        self.store.set_tank(**values)
        self.parent.slowlog.set('discharge.tank', values, 'sweep')
        self.show_cal()

    # AM check ---------------------------------------------------------------

    def am_check_pushed(self):
        run_tab = self.parent.run_tab
        problem = None
        try:
            settle = num(self.am_settle_edit, 3.0)
            secs = num(self.avg_edit, 10.0)
        except ValueError:
            problem = "Enter the settle and averaging times."
        if not problem:
            if getattr(self.parent, 'siggen', None) is None:
                problem = "The signal generator is not connected."
            elif run_tab.scan_button.isChecked() or run_tab.larmor_running or run_tab.disoff_button.isChecked():
                problem = "Stop scans, the Larmor drive and relaxation first."
            elif self.sweep_thread and self.sweep_thread.isRunning():
                problem = "Wait for the sweep to finish."
        if problem:
            self.am_check_label.setText(problem)
            self.am_check_label.setStyleSheet(RED)
            return
        self.am_check_button.setEnabled(False)
        self.am_thread = AMCheckThread(self, settle, secs)
        self.am_thread.status.connect(self.am_check_label.setText)
        self.am_thread.done.connect(self.am_check_done)
        self.am_check_label.setStyleSheet("")
        self.am_thread.start()

    def am_check_done(self, msg, ok):
        self.am_check_button.setEnabled(True)
        self.am_check_label.setText(msg)
        self.am_check_label.setStyleSheet(GREEN if ok else RED)
        self.parent.slowlog.set('discharge.am_check', msg, 'user')


class SweepThread(QThread):
    '''Step the SG380 over a frequency range and read Gamma at each step, then put
    the SG380 back where it was. Readings come from the tab's latest, so the tab's
    validity checks apply; the thread only waits for ones made after it settled.'''
    point = pyqtSignal(float, object)
    status = pyqtSignal(str)
    done = pyqtSignal(str)

    def __init__(self, tab, freqs, settle, samples):
        QThread.__init__(self)
        self.tab = tab
        self.freqs = freqs
        self.settle = settle
        self.samples = samples
        self.stop = False

    def run(self):
        tab = self.tab
        siggen = tab.parent.siggen
        log = tab.parent.slowlog
        try:
            start_mhz = siggen.read_freq()
        except Exception as e:
            self.done.emit(f"Could not read the SG380 frequency: {e}")
            return
        msg = ""
        try:
            for i, f in enumerate(self.freqs):
                if self.stop or tab.tripped:
                    msg = "Interlock tripped, sweep stopped." if tab.tripped else "Sweep stopped."
                    break
                siggen.set_freq(f / 1e6)
                log.set('siggen.freq', f / 1e6, 'sweep')
                t0 = time.time()
                self.status.emit(f"Step {i + 1} of {len(self.freqs)}: {f/1e6:.5f} MHz")
                self.msleep(int(self.settle * 1000))
                got, last = [], None
                deadline = time.time() + max(10.0, 3 * self.settle)
                while len(got) < self.samples and time.time() < deadline and not self.stop:
                    rec = tab.latest
                    if (rec is not None and rec is not last and rec['stamp'] > t0 + self.settle
                            and rec['ok'] and rec['gamma'] is not None):
                        got.append(rec['gamma'])
                        last = rec
                    self.msleep(50)
                if got:
                    self.point.emit(float(f), complex(np.mean(got)))
                else:
                    self.status.emit(f"No valid reading at {f/1e6:.5f} MHz, skipped")
        except Exception as e:
            msg = f"Sweep failed: {e}."
        finally:
            try:
                siggen.set_freq(start_mhz)
                log.set('siggen.freq', start_mhz, 'sweep')
                msg += f" SG380 back at {start_mhz:.6f} MHz."
            except Exception as e:
                msg += f" Could not put the SG380 back to {start_mhz:.6f} MHz: {e}."
        self.done.emit(msg.strip())


class AMCheckThread(QThread):
    '''Read the sensor with the SG380's AM off then on, at the same carrier, and
    compare the ratio with 1 + m^2/2. Modulation is put back as it was.'''
    status = pyqtSignal(str)
    done = pyqtSignal(str, bool)

    def __init__(self, tab, settle, secs):
        QThread.__init__(self)
        self.tab = tab
        self.settle = settle
        self.secs = secs

    def average(self):
        '''Mean sensor power in W over secs, from readings that arrive in that time'''
        t_end = time.time() + self.secs
        watts, last = [], None
        while time.time() < t_end:
            s = self.tab.sensor_last
            if s is not None and s is not last and s[2]:
                watts.append(dp.dbm_to_w(s[1]))
                last = s
            self.msleep(50)
        if len(watts) < 2:
            raise RuntimeError("too few sensor readings")
        return float(np.mean(watts))

    def run(self):
        siggen = self.tab.parent.siggen
        log = self.tab.parent.slowlog
        try:
            saved = siggen.read_modulation()
            depth, rate, on = siggen.read_am()
        except Exception as e:
            self.done.emit(f"Could not read the SG380 modulation: {e}", False)
            return
        if not on or depth <= 0:
            self.done.emit("The SG380's AM is off; turn it on at the operating depth first.", False)
            return
        try:
            siggen.enable_modulation(False)
            log.set('siggen.modulation', False, 'am_check')
            self.status.emit("AM off, settling...")
            self.msleep(int(self.settle * 1000))
            self.status.emit("AM off, averaging the sensor...")
            p_off = self.average()
            siggen.enable_modulation(True)
            log.set('siggen.modulation', True, 'am_check')
            self.status.emit("AM on, settling...")
            self.msleep(int(self.settle * 1000))
            self.status.emit("AM on, averaging the sensor...")
            p_on = self.average()
        except Exception as e:
            self.done.emit(f"AM check failed: {e}", False)
            return
        finally:
            try:
                siggen.restore_modulation(saved)
                log.set('siggen.modulation', 'restored', 'am_check')
            except Exception:
                pass
        ratio, expected = p_on / p_off, dp.am_factor(depth)
        off_by = (ratio / expected - 1) * 100
        ok = abs(off_by) < 2
        self.done.emit(f"AM {depth*100:g}%: sensor on/off {ratio:.4f}, expected 1 + m²/2 = {expected:.4f}, "
                       f"{off_by:+.2f}%." + ("" if ok else " The sensor does not follow AM here: calibrate at "
                                             "the operating AM depth."), ok)
