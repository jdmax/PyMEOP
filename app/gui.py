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
from PyQt5.QtWidgets import QMainWindow, QErrorMessage, QTabWidget, QLabel, QWidget, QLineEdit
from PyQt5.QtGui import QIntValidator, QDoubleValidator, QValidator
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from logging.handlers import TimedRotatingFileHandler
import numpy as np
from scipy import optimize

from app.gui_run_tab import RunTab
from app.gui_find_tab import FindTab
from app.classes import Event
from app.instruments import ProbeLaser, WavelengthMeter, LockIn, SigGen


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
        self.find_tab = FindTab(self, self.status_bar)
        self.tab_widget.addTab(self.find_tab, "Find Peaks")

        self.restore_session()

        self.new_event()
        self.new_eventfile()

        try:
            self.probe = ProbeLaser(self.settings)
            self.status_bar.showMessage(f"Connected to probe laser at {self.settings['probe_ip']}")
        except Exception as e:
            print(f"Unable to connect to probe laser at {self.settings['probe_ip']}",e)

        try:
            self.meter = WavelengthMeter(self.settings)
            self.status_bar.showMessage(f"Connected to wavelength meter at {self.settings['meter_ip']}")
        except Exception as e:
            print(f"Unable to connect to wavelngh meter at {self.settings['meter_ip']}, {e}")

        try:
            self.lockin = LockIn(self.settings['lockin_ip'])
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
        with open('app/saved_session.yaml', 'w') as file:
            documents = yaml.dump(saved_dict, file)

    def restore_session(self):
        '''Restore settings from previous session'''
        with open('app/saved_session.yaml') as f:  # Load settings from YAML files
            restore_dict = yaml.load(f, Loader=yaml.FullLoader)
        try:
            for k, e in restore_dict.items():
                for key, entry in e.items():
                    self.__dict__[k].__dict__[key].setText(entry)  # set line edit text for each
        except Exception as ex:
            print('Failed to import previous session.', ex)

    def new_event(self):
        '''Create new event instance'''
        self.event = Event(self)

    def end_event(self, currs, waves, rs, times, params):

        self.event.currs = currs
        self.event.waves = waves
        self.event.rs = rs
        self.event.times = times

        self.event.stop_time = datetime.datetime.now(tz=datetime.timezone.utc)
        self.event.stop_stamp = self.event.stop_time.timestamp()
        self.previous_event = self.event  # set this as previous event
        self.new_event()  # start new event to accept next scan

        try:
            self.anal_thread = AnalThread(self, self.previous_event, params)
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
            os.rename(self.eventfile_name, os.path.join(self.config.settings["event_dir"], new))
            logging.info(f"Closed eventfile and moved to {new}.")
        except AttributeError:
            logging.info(f"Error closing eventfile.")

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

        self.p1_zero = float(parent.run_tab.zero1_edit.text())
        self.p2_zero = float(parent.run_tab.zero2_edit.text())

    def fit_scan(self, pars):
        '''Fit Scan data with linear and two gaussians, using starting params passed'''

        if 'wave' in self.parent.settings['scan_x_axis']:
            self.x_axis = self.waves
        else:
            self.x_axis = self.currs

        X = np.array(self.x_axis)
        Y = np.array(self.rs)
        self.pf, self.pcov = optimize.curve_fit(self.peaks, X, Y, p0=pars, maxfev = 10000) #changed max iteration to 10000
        self.pstd = np.sqrt(np.diag(self.pcov))
        self.fit = self.peaks(X, *self.pf)
        self.peak1 = self.pf[2]
        self.peak2 = self.pf[5]

        self.r = self.peak1 / self.peak2
        self.r0 = self.p1_zero / self.p2_zero
        self.pol = (self.r / self.r0 - 1) / (self.r / self.r0 + 1)

    def peaks(self, x, *p):
        g1 = p[2] * np.exp(-np.power((x - p[0]), 2) / (2 * np.power(p[1], 2)))
        g2 = p[5] * np.exp(-np.power((x - p[3]), 2) / (2 * np.power(p[4], 2)))
        lin = p[6] * x + p[7]
        return g1 + g2 + lin

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


class AnalThread(QThread):
    '''Thread class for running analysis
    '''
    finished = pyqtSignal()  # finished signal

    def __init__(self, parent, event, params):
        QThread.__init__(self)
        self.parent = parent
        self.event = event
        self.params = params

    def __del__(self):
        self.wait()

    def run(self):
        '''Main scan loop
        '''
        self.event.fit_scan(self.params)
        self.finished.emit()
