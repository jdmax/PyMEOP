'''PyMEOP J.Maxwell 2021
'''

import dataclasses
import datetime
import os
import logging
import json
from PyQt5.QtWidgets import QMainWindow, QErrorMessage, QTabWidget, QLabel, QLineEdit
from PyQt5.QtCore import QThread, pyqtSignal
from logging.handlers import TimedRotatingFileHandler
import numpy as np

from app.gui_run_tab import RunTab
from app.gui_find_tab import FindTab
from app.instruments import ProbeLaser, WavelengthMeter, LockIn, SigGen
from app.core import fitting
from app.core.config import load_session, save_session

SESSION_FILE = 'app/saved_session.yaml'


class MainWindow(QMainWindow):
    '''Main window of application

    Args:
        settings: Validated Settings from app.core.config
    '''

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.error_dialog = QErrorMessage(self)
        self.status_bar = self.statusBar()
        self.status_bar.showMessage('Ready.')

        self.settings = settings
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
            self.status_bar.showMessage(f"Connected to probe laser at {self.settings.probe_ip}")
        except:
            print(f"Unable to connect to probe laser at {self.settings.probe_ip}")

        try:
            self.meter = WavelengthMeter(self.settings)
            self.status_bar.showMessage(f"Connected to wavelength meter at {self.settings.meter_ip}")
        except Exception as e:
            print(f"Unable to connect to wavelngh meter at {self.settings.meter_ip}, {e}")

        try:
            self.lockin = LockIn(self.settings)
            self.status_bar.showMessage(f"Connected to lock-in at {self.settings.lockin_ip}")
        except Exception as e:
            print(f"Unable to connect to Lock In at {self.settings.lockin_ip}, {e}")

        try:
            self.siggen = SigGen(self.settings)
            self.status_bar.showMessage(f"Connected to signal generator at {self.settings.siggen_ip}")
        except Exception as e:
            print(f"Unable to connect to Signal Generator at {self.settings.siggen_ip}, {e}")

    def save_session(self):
        '''Print session settings before app exit to a file for recall on restart'''
        saved_dict = {}

        for k, e in self.__dict__.items():  # go through all "tabs" and save all edit values
            if '_tab' in k:
                saved_dict.update({k: {}})
                for key, entry in e.__dict__.items():
                    if isinstance(entry, QLineEdit):
                        saved_dict[k].update({key: entry.text()})
        save_session(SESSION_FILE, saved_dict)

    def restore_session(self):
        '''Restore line edit text from previous session, skipping fields that no longer exist'''
        for tab_name, edits in load_session(SESSION_FILE).items():
            tab = getattr(self, tab_name, None)
            for key, text in edits.items():
                edit = getattr(tab, key, None)
                if isinstance(edit, QLineEdit):
                    edit.setText('' if text is None else str(text))
                else:
                    logging.info(f'Session field {tab_name}.{key} no longer exists, skipped.')

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
        self.eventfile_name = os.path.join(self.settings.event_dir, f'current_{self.eventfile_start}.txt')
        self.eventfile = open(self.eventfile_name, "w")
        self.eventfile_lines = 0
        logging.info(f"Opened new eventfile {self.eventfile_name}")

    def close_eventfile(self):
        '''Try to close and rename eventfile'''
        try:
            self.eventfile.close()
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            new = f'{self.eventfile_start}__{now.strftime("%Y-%m-%d_%H-%M-%S")}.txt'
            os.rename(self.eventfile_name, os.path.join(self.settings.event_dir, new))
            logging.info(f"Closed eventfile and moved to {new}.")
        except AttributeError:
            logging.info(f"Error closing eventfile.")

    def start_logger(self):
        '''Start logger
        '''
        logHandler = TimedRotatingFileHandler(os.path.join(self.settings.log_dir, "log"),
                                              when="midnight")  # setup logfiles
        logHandler.suffix = "%Y-%m-%d.txt"
        logFormatter = logging.Formatter('%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        logHandler.setFormatter(logFormatter)
        logger = logging.getLogger()
        logger.addHandler(logHandler)
        logger.setLevel(logging.INFO)
        logging.info(f"Started with settings {self.settings}")

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

        try:
            self.p1_zero = float(parent.run_tab.zero1_edit.text())
            self.p2_zero = float(parent.run_tab.zero2_edit.text())
        except Exception as e:
            print(e)
            self.p1_zero = 0.1
            self.p2_zero = 0.1

    def x_data(self):
        '''Return the x axis to fit and plot against, per the scan_x_axis setting'''

        if self.settings.scan_x_axis == 'wavelength':
            return np.array(self.waves, dtype=float)
        return np.array(self.currs, dtype=float)

    def fit_scan(self, seed=None):
        '''Fit the scan and work out polarization from the peak heights.

        Args:
            seed: Parameter list from the last good fit, or None to only estimate
        '''

        X = self.x_data()
        self.x_axis = X.tolist()
        self.set_fit(fitting.fit_scan(X, self.rs, seed))

        self.r = self.peak1 / self.peak2 if self.peak2 else np.nan
        self.r0 = self.p1_zero / self.p2_zero if self.p2_zero else np.nan
        ratio = self.r / self.r0 if self.r0 else np.nan
        self.pol = (ratio - 1) / (ratio + 1) if np.isfinite(ratio) and ratio != -1 else np.nan

    def no_fit(self, message):
        '''Fill in placeholder results so a failed fit still plots and writes an event'''

        self.x_axis = self.x_data().tolist()
        self.set_fit(fitting.no_fit(message))
        self.r = self.r0 = self.pol = np.nan

    def set_fit(self, result):
        '''Copy a FitResult onto the event attributes the GUI and eventfile use'''

        self.x_ref = result.x_ref
        if result.converged:
            self.p0 = result.p0
        self.pf = result.pf
        self.pcov = result.pcov
        self.pstd = result.pstd
        self.fit_good = result.ok
        self.fit_message = result.message
        self.fit = result.fit_curve
        self.rsq = result.rsq
        self.peak1 = result.peak1
        self.peak2 = result.peak2

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
            elif dataclasses.is_dataclass(entry):
                json_dict.update({key: dataclasses.asdict(entry)})
            elif key in exclude_list:
                pass
            else:
                json_dict.update({key: entry})
        for key, entry in json_dict.items():
            if isinstance(entry, np.ndarray):
                json_dict[key] = entry.tolist()
        json_record = json.dumps(json_dict)
        eventfile.write(json_record + '\n')  # write to file as json line


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
            try:
                self.event.no_fit(f'Fit failed: {e}')
            except Exception:
                self.event.fit_message = f'Fit failed: {e}'
                self.event.fit_good = False
        self.finished.emit()
