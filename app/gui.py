'''PyMEOP J.Maxwell 2021
'''

import dataclasses
import datetime
import os
import logging
from PyQt5.QtWidgets import QMainWindow, QErrorMessage, QTabWidget, QLabel, QLineEdit
from PyQt5.QtCore import QThread, pyqtSignal
from logging.handlers import TimedRotatingFileHandler

from app.gui_run_tab import RunTab
from app.gui_find_tab import FindTab
from app.instruments import ProbeLaser, WavelengthMeter, LockIn, SigGen
from app.core.config import load_session, save_session
from app.core.event import Event
from app.core.storage import EventWriter

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

        self.writer = EventWriter(self.settings.event_dir,
                                  header={'settings': dataclasses.asdict(self.settings)})
        self.new_event()

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
        self.event = Event(x_axis_name=self.settings.scan_x_axis)

    def end_event(self, currs, waves, rs, times, seed=None):

        self.event.currs = currs
        self.event.waves = waves
        self.event.rs = rs
        self.event.times = times
        self.event.p1_zero, self.event.p2_zero = self.run_tab.zero_amplitudes()

        self.event.stop_time = datetime.datetime.now(tz=datetime.timezone.utc)
        self.previous_event = self.event  # set this as previous event
        self.new_event()  # start new event to accept next scan

        try:
            self.anal_thread = AnalThread(self, self.previous_event, seed)
            self.anal_thread.finished.connect(self.finished_anal)
            self.anal_thread.start()
        except Exception as e:
            print('Exception starting run thread, lost connection: ' + str(e))

    def finished_anal(self):

        self.run_tab.update_scan_plot(self.previous_event)
        self.writer.write(self.previous_event.to_record())

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
        self.writer.close()
        event.accept()


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
            self.event.analyze(self.seed)
        except Exception as e:  # never leave the run tab waiting on a signal that won't come
            logging.exception('Exception fitting scan')
            self.event.fail(f'Fit failed: {e}')
        self.finished.emit()
