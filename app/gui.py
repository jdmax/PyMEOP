'''PyMEOP J.Maxwell 2021
'''

import datetime
import time
import socket
import sys
import os
import yaml
import pytz
import logging
import json
from PyQt5.QtWidgets import QMainWindow, QErrorMessage, QTabWidget, QLabel, QWidget, QLineEdit, QComboBox
from PyQt5.QtGui import QIntValidator, QDoubleValidator, QValidator
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from logging.handlers import TimedRotatingFileHandler
import numpy as np

from app.gui_run_tab import RunTab
from app.gui_find_tab import FindTab
from app.classes import Event
from app.instruments import ProbeLaser, WavelengthMeter, LockIn, SigGen
from app import scanfit
from app.scanfit import BASELINE_DEGREES, DEFAULT_BASELINE_DEGREE


class MainWindow(QMainWindow):
    '''Main window of application

    Attributes:

    '''

    def __init__(self, parent=None):
        super().__init__(parent)
        self.error_dialog = QErrorMessage(self)
        self.status_bar = self.statusBar()
        self.status_bar.showMessage('Ready.')

        self.config_filename = 'config.yaml'
        self.load_settings()
        self.start_logger()

        self.left = 100
        self.top = 100
        self.title = 'JLab Polarization Display'
        self.width = 1200
        self.height = 800
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)

        self.tab_widget = QTabWidget(self)
        self.setCentralWidget(self.tab_widget)

        # Make tabs
        self.run_tab = RunTab(self)
        self.tab_widget.addTab(self.run_tab, "Run")
        self.find_tab = FindTab(self)
        self.tab_widget.addTab(self.find_tab, "Find Peaks")

        self.restore_session()

        self.new_event()
        self.new_eventfile()

        try:
            self.probe = ProbeLaser(self.settings)
            self.status_bar.showMessage(f"Connected to probe laser at {self.settings['probe_ip']}")
        except:
            print(f"Unable to connect to probe laser at {self.settings['probe_ip']}")

        try:
            self.meter = WavelengthMeter(self.settings)
            self.status_bar.showMessage(f"Connected to wavelength meter at {self.settings['meter_ip']}")
        except Exception as e:
            print(f"Unable to connect to wavelngh meter at {self.settings['meter_ip']}, {e}")

        try:
            self.lockin = LockIn(self.settings)
            self.status_bar.showMessage(f"Connected to lock-in at {self.settings['lockin_ip']}")
        except Exception as e:
            print(f"Unable to connect to Lock In at {self.settings['lockin_ip']}, {e}")

        try:
            self.siggen = SigGen(self.settings)
            self.status_bar.showMessage(f"Connected to signal generator at {self.settings['siggen_ip']}")
        except Exception as e:
            print(f"Unable to connect to Signal Generator at {self.settings['siggen_ip']}, {e}")

        # try: 
        # self.labjack = LabJack(self.settings)
        # except Exception as e:
        # print(f"Unable to connect to LabJack at {self.settings['labjack_ip']}, {e}")

    def load_settings(self):
        '''Load settings from YAML config file'''

        with open(self.config_filename) as f:  # Load settings from YAML file
            self.config_dict = yaml.load(f, Loader=yaml.FullLoader)
        self.settings = self.config_dict['settings']  # dict of settings

        self.status_bar.showMessage(f"Loaded settings from {self.config_filename}.")

    def save_session(self):
        '''Print session settings before app exit to a file for recall on restart'''
        saved_dict = {}

        for k, e in self.__dict__.items():  # go through all "tabs" and save all edit values
            if '_tab' in k:
                saved_dict.update({k: {}})
                for key, entry in e.__dict__.items():
                    if isinstance(entry, QLineEdit):
                        saved_dict[k].update({key: entry.text()})
                    elif isinstance(entry, QComboBox):
                        saved_dict[k].update({key: entry.currentData()})
        with open('app/saved_session.yaml', 'w') as file:
            documents = yaml.dump(saved_dict, file)

    def restore_session(self):
        '''Restore settings from previous session'''
        with open('app/saved_session.yaml') as f:  # Load settings from YAML files
            restore_dict = yaml.load(f, Loader=yaml.FullLoader)

        try:
            for k, e in restore_dict.items():
                for key, entry in e.items():
                    widget = self.__dict__[k].__dict__.get(key)
                    if isinstance(widget, QComboBox):  # a choice, restored only if still offered
                        i = widget.findData(entry)
                        if i >= 0:
                            widget.setCurrentIndex(i)
                        continue
                    try:
                        self.__dict__[k].__dict__[key].setText(entry)  # set line edit text for each
                    except Exception as ex:
                        self.__dict__[k].__dict__[key].setText('')
                        print('Failed to import previous session.', ex)
        except Exception as ex:
            print('Failed to import previous session.', ex)

    def new_event(self):
        '''Create new event instance'''
        self.event = Event(self)

    def end_event(self, currs, waves, rs, times, seed=None):

        self.event.currs = currs
        self.event.waves = waves
        self.event.rs = rs
        self.event.times = times

        self.event.stop_time = datetime.datetime.now(tz=datetime.timezone.utc)
        self.event.stop_stamp = self.event.stop_time.timestamp()
        self.event.set_profile(self.event.line_shape())  # picks up a switch made mid-scan
        self.previous_event = self.event  # set this as previous event
        self.new_event()  # start new event to accept next scan

        try:
            self.anal_thread = AnalThread(self, self.previous_event, seed)
            self.anal_thread.finished.connect(self.finished_anal)
            self.anal_thread.start()
        except Exception as e:
            print('Exception starting run thread, lost connection: ' + str(e))

    def finished_anal(self):

        self.eventfile_lines += 1
        if self.eventfile_lines > 200:  # open new eventfile once the current one has a number of entries
            self.new_eventfile()
        self.run_tab.update_scan_plot()
        self.previous_event.print_event(self.eventfile)

    def new_eventfile(self):
        '''Open new eventfile'''
        self.close_eventfile()  # try to close previous eventfile
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        self.eventfile_start = now.strftime("%Y-%m-%d_%H-%M-%S")
        self.eventfile_name = os.path.join(self.settings["event_dir"], f'current_{self.eventfile_start}.txt')
        self.eventfile = open(self.eventfile_name, "w")
        self.eventfile_lines = 0
        logging.info(f"Opened new eventfile {self.eventfile_name}")

    def close_eventfile(self):
        '''Try to close and rename eventfile'''
        try:
            self.eventfile.close()
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            new = f'{self.eventfile_start}__{now.strftime("%Y-%m-%d_%H-%M-%S")}.txt'
            for attempt in range(10):
                try:
                    os.rename(self.eventfile_name, os.path.join(self.config.settings["event_dir"], new))
                    break
                except PermissionError:  # Windows refuses while the data browser is reading the file
                    if attempt == 9:
                        raise
                    time.sleep(0.05)
            logging.info(f"Closed eventfile and moved to {new}.")
        except (AttributeError, OSError) as e:
            logging.info(f"Error closing eventfile: {e}")

    def start_logger(self):
        '''Start logger
        '''
        logHandler = TimedRotatingFileHandler(os.path.join(self.settings['log_dir'], "log"),
                                              when="midnight")  # setup logfiles
        logHandler.suffix = "%Y-%m-%d.txt"
        logFormatter = logging.Formatter('%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        logHandler.setFormatter(logFormatter)
        logger = logging.getLogger()
        logger.addHandler(logHandler)
        logger.setLevel(logging.INFO)
        logging.info("Loaded config file")

    def divider(self):
        div = QLabel('')
        div.setStyleSheet(
            "QLabel {background-color: #eeeeee; padding: 0; margin: 0; border-bottom: 0 solid #eeeeee; border-top: 1 solid #eeeeee;}")
        div.setMaximumHeight(2)
        return div

    def closeEvent(self, event):
        '''Things to do on close of window ("events" here are not related to nmr data events)
        '''
        self.save_session()
        event.accept()


class Event():
    '''Data and method object for single event point. Takes config instance on init.
    '''

    def __init__(self, parent):
        self.parent = parent
        self.settings = parent.settings

        self.start_time = datetime.datetime.now(tz=datetime.timezone.utc)
        self.start_stamp = self.start_time.timestamp()

        self.currs = []
        self.waves = []
        self.rs = []
        self.times = []
        self.x_ref = 0.0  # baseline reference, set to mid-scan once there is data to fit

        # Straight or curved baseline under the peaks. A curved baseline follows a
        # sloping laser power envelope, but on a scan that is already flat the extra
        # term is free to bend into the peaks, so a straight one is steadier there.
        # Both are written with the baseline referenced to x_ref, see scanfit.parts().
        self.base_deg = self.baseline_degree()
        self.set_profile(self.line_shape())

        try:
            self.p1_zero = float(parent.run_tab.zero1_edit.text())
            self.p2_zero = float(parent.run_tab.zero2_edit.text())
        except Exception as e:
            print(e)
            self.p1_zero = 0.1
            self.p2_zero = 0.1

    def baseline_degree(self):
        '''Baseline polynomial degree from the config, falling back to quadratic.

        Returns:
            1 for a straight baseline or 2 for a quadratic one
        '''

        raw = self.settings.get('baseline_degree', DEFAULT_BASELINE_DEGREE)
        try:
            deg = int(raw)
        except (TypeError, ValueError):
            deg = None
        if deg not in BASELINE_DEGREES:
            logging.warning(f'baseline_degree {raw!r} is not one of {BASELINE_DEGREES}, '
                            f'using {DEFAULT_BASELINE_DEGREE}.')
            return DEFAULT_BASELINE_DEGREE
        return deg

    def line_shape(self):
        '''Peak line shape picked on the run tab, gaussian until there is one'''

        try:
            return scanfit.check_profile(self.parent.run_tab.line_shape())
        except AttributeError:  # the run tab is still being built
            return scanfit.DEFAULT_PROFILE

    def set_profile(self, profile):
        '''Set the peak line shape this scan will be fit with, gauss or voigt'''

        self.profile = scanfit.check_profile(profile)
        self.n_pars = scanfit.n_peak_pars(self.profile) + self.base_deg + 1

    def x_data(self):
        '''Return the x axis to fit and plot against, per the scan_x_axis setting'''

        if 'wave' in self.parent.settings['scan_x_axis']:
            return np.array(self.waves, dtype=float)
        return np.array(self.currs, dtype=float)

    def fit_scan(self, seed=None):
        '''Fit Scan data with two peaks on a straight or quadratic baseline.

        The fit itself is in scanfit.ScanFitter, shared with the data browser.

        Args:
            seed: Parameter list from the last good fit, or None to only estimate
        '''

        X = self.x_data()
        Y = np.array(self.rs, dtype=float)
        self.x_axis = X.tolist()
        self.fit_good = False
        self.fit_message = ''

        best = scanfit.ScanFitter(self.base_deg, self.profile).fit(X, Y, seed)
        self.x_ref = best['x_ref']
        self.fit_message = best['message']
        if best['pf'] is None:
            self.no_fit(X)
            return

        self.p0 = best['p0']
        self.pf = best['pf']
        self.pcov = best['pcov']
        self.pstd = best['pstd']
        self.fit_good = best['ok']
        self.fit = scanfit.model(X, self.pf, self.x_ref, self.profile)
        g1, g2, base = scanfit.parts(X, self.pf, self.x_ref, self.profile)
        # the peaks are drawn sitting on the baseline rather than about zero, so the
        # components stay in the range of the data instead of at the bottom of the plot
        self.fit_g1, self.fit_g2, self.fit_base = g1 + base, g2 + base, base
        self.rsq = best['rsq']
        self.peak1 = self.pf[2]
        self.peak2 = self.pf[5]
        if not self.fit_good:
            logging.warning(self.fit_message)

        self.r = self.peak1 / self.peak2 if self.peak2 else np.nan
        self.r0 = self.p1_zero / self.p2_zero if self.p2_zero else np.nan
        ratio = self.r / self.r0 if self.r0 else np.nan
        self.pol = (ratio - 1) / (ratio + 1) if np.isfinite(ratio) and ratio != -1 else np.nan

    def no_fit(self, X):
        '''Fill in placeholder results so a failed fit still plots and writes an event'''

        self.x_axis = X.tolist()
        self.fit_good = False
        self.pf = list(getattr(self, 'p0', [0.0] * self.n_pars))
        self.pstd = []
        self.pcov = []
        self.fit = []  # nothing to draw, rather than a fit curve that doesn't exist
        self.fit_g1, self.fit_g2, self.fit_base = [], [], []
        self.rsq = np.nan
        self.peak1 = np.nan
        self.peak2 = np.nan
        self.r = np.nan
        self.r0 = np.nan
        self.pol = np.nan
        logging.warning(self.fit_message)

    def print_event(self, eventfile):
        '''Print out all event attributes to eventfile, formatting to dict to write to json line.
        
        Args:
            eventfile: File object to write event to
        '''

        exclude_list = ['parent']
        json_dict = {}
        for key, entry in self.__dict__.items():  # filter event attributes for json dict
            if isinstance(entry, datetime.datetime):
                json_dict.update({key: entry.__str__()})  # datetime to string
            elif key in exclude_list:
                pass
            else:
                json_dict.update({key: entry})
        for key, entry in json_dict.items():
            if isinstance(entry, np.ndarray):
                json_dict[key] = entry.tolist()
        json_record = json.dumps(json_dict)
        eventfile.write(json_record + '\n')  # write to file as json line
        eventfile.flush()  # so the data browser sees each scan as soon as it is written


class AnalThread(QThread):
    '''Thread class for running analysis
    '''
    finished = pyqtSignal()  # finished signal

    def __init__(self, parent, event, seed=None):
        QThread.__init__(self)
        self.parent = parent
        self.event = event
        self.seed = seed

    def __del__(self):
        self.wait()

    def run(self):
        '''Main scan loop
        '''
        try:
            self.event.fit_scan(self.seed)
        except Exception as e:  # never leave the run tab waiting on a signal that won't come
            logging.exception('Exception fitting scan')
            self.event.fit_message = f'Fit failed: {e}'
            try:
                self.event.no_fit(self.event.x_data())
            except Exception:
                self.event.fit_good = False
        self.finished.emit()
