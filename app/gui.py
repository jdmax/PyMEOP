'''PyMEOP J.Maxwell 2021
'''

import datetime
import os
import yaml
import logging
import json
from PyQt5.QtWidgets import QMainWindow, QErrorMessage, QTabWidget, QLabel, QLineEdit
from PyQt5.QtCore import QThread, pyqtSignal
from logging.handlers import TimedRotatingFileHandler
import numpy as np
from scipy import optimize
from scipy.signal import find_peaks, peak_widths

from app.gui_run_tab import RunTab
from app.gui_find_tab import FindTab
from app.instruments import ProbeLaser, WavelengthMeter, LockIn, SigGen

N_PARS = 9  # two gaussians (position, sigma, height each) on a quadratic baseline
MIN_FIT_POINTS = 25  # below this the amplitude ratio is biased by several percent even
                     # when the fit converges and passes every other check
MIN_PEAK_SIGNIFICANCE = 3  # peak amplitude must be this many sigma above its own uncertainty
MIN_PEAK_POWER = 0.1  # peaks must cut this fraction of the residuals left by the bare baseline


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

        if 'wave' in self.parent.settings['scan_x_axis']:
            return np.array(self.waves, dtype=float)
        return np.array(self.currs, dtype=float)

    def fit_scan(self, seed=None):
        '''Fit Scan data with two gaussians on a quadratic baseline.

        Fits from the previous converged fit and from starting parameters estimated
        from this scan's own data, then keeps whichever result comes out better. The
        previous fit is the better start while the peaks drift slowly, but it goes
        stale if they move, so it is never trusted on its own.

        Args:
            seed: Parameter list from the last good fit, or None to only estimate
        '''

        X = self.x_data()
        Y = np.array(self.rs, dtype=float)
        self.x_axis = X.tolist()
        self.fit_good = False
        self.fit_message = ''

        good = np.isfinite(X) & np.isfinite(Y)
        if good.sum() < MIN_FIT_POINTS or not np.ptp(X[good]) > 0:
            self.fit_message = 'Too few usable scan points to fit.'
            self.no_fit(X)
            return

        order = np.argsort(X[good])
        x, y = X[good][order], Y[good][order]
        span = x[-1] - x[0]
        dx = np.median(np.diff(x))
        if not dx > 0:
            dx = span / len(x)
        self.x_ref = float(0.5 * (x[0] + x[-1]))  # baseline reference, see peaks()

        est = self.estimate_peaks(x, y, span, dx)
        starts = [est]
        if self.seed_usable(seed, x, span, dx):
            # take the peaks from the previous fit but always re-estimate the baseline:
            # it moves with laser power, independently of where the lines sit
            starts.insert(0, list(seed[:6]) + list(est[6:]))

        best = None
        for start in starts:
            trial = self.try_fit(x, y, start, span, dx)
            if trial and (best is None or self.better_fit(trial, best)):
                best = trial
        if best is None:
            self.fit_message = 'Fit did not converge from any starting point.'
            self.no_fit(X)
            return

        self.p0 = best['p0']
        self.pf = best['pf']
        self.pcov = best['pcov']
        self.pstd = best['pstd']
        self.fit_good, self.fit_message = best['ok'], best['message']
        self.fit = self.peaks(X, *self.pf)
        self.rsq = self.r_squared(y, self.peaks(x, *self.pf))
        self.peak1 = self.pf[2]
        self.peak2 = self.pf[5]
        if not self.fit_good:
            logging.warning(self.fit_message)

        self.r = self.peak1 / self.peak2 if self.peak2 else np.nan
        self.r0 = self.p1_zero / self.p2_zero if self.p2_zero else np.nan
        ratio = self.r / self.r0 if self.r0 else np.nan
        self.pol = (ratio - 1) / (ratio + 1) if np.isfinite(ratio) and ratio != -1 else np.nan

    def try_fit(self, x, y, start, span, dx):
        '''Run one fit from a given starting point and judge the result.

        Args:
            x: Sorted scan x axis values
            y: Scan signal values in the same order
            start: Nine starting parameters
            span: Width of the scan range
            dx: Typical step between scan points
        Returns:
            Dict of fit results, or None if the fit did not converge
        '''

        lower, upper = self.fit_bounds(start, x, span, dx)
        p0 = [float(np.clip(v, lo, hi)) for v, lo, hi in zip(start, lower, upper)]
        try:
            pf, pcov = optimize.curve_fit(  # x_scale='jac' since positions, widths and
                self.peaks, x, y, p0=p0, bounds=(lower, upper),  # amplitudes differ by
                x_scale='jac', max_nfev=2000)                    # orders of magnitude
        except (RuntimeError, ValueError) as e:
            logging.info(f'Fit from {p0} did not converge: {e}')
            return None

        pstd = np.sqrt(np.diag(pcov))
        ssr = float(np.sum((y - self.peaks(x, *pf)) ** 2))
        ok, message = self.check_fit(pf, pstd, ssr, x, y, (lower, upper))
        return {'p0': p0, 'pf': pf, 'pcov': pcov, 'pstd': pstd,
                'ssr': ssr, 'ok': ok, 'message': message}

    def better_fit(self, trial, best):
        '''Prefer a fit that passes the sanity checks, then the one with smaller residuals'''

        if trial['ok'] != best['ok']:
            return trial['ok']
        return trial['ssr'] < best['ssr']

    def no_fit(self, X):
        '''Fill in placeholder results so a failed fit still plots and writes an event'''

        self.x_axis = X.tolist()
        self.fit_good = False
        self.pf = list(getattr(self, 'p0', [0.0] * N_PARS))
        self.pstd = []
        self.pcov = []
        self.fit = []  # nothing to draw, rather than a fit curve that doesn't exist
        self.rsq = np.nan
        self.peak1 = np.nan
        self.peak2 = np.nan
        self.r = np.nan
        self.r0 = np.nan
        self.pol = np.nan
        logging.warning(self.fit_message)

    def seed_usable(self, seed, x, span, dx):
        '''True if the previous fit's peaks are worth trying as a start for this scan'''

        if seed is None or len(seed) < 6 or not np.all(np.isfinite(seed[:6])):
            return False
        pos1, sig1, hei1, pos2, sig2, hei2 = seed[:6]
        if not x[0] <= pos1 < pos2 <= x[-1]:  # both peaks must sit inside this scan range
            return False
        if not (dx <= sig1 <= span and dx <= sig2 <= span):
            return False
        return hei1 > 0 and hei2 > 0

    def estimate_peaks(self, x, y, span, dx):
        '''Estimate baseline and two gaussian parameters straight from sorted scan data.

        The peaks are located against a straight line, not against the quadratic the
        model will use. A quadratic guess that comes out too curved arcs away from the
        data at the ends of the scan and invents a peak there; a line cannot.

        Args:
            x: Sorted scan x axis values
            y: Scan signal values in the same order
            span: Width of the scan range
            dx: Typical step between scan points
        Returns:
            List of nine starting parameters
        '''

        xr = x - self.x_ref
        resid = y - np.polyval(self.baseline_guess(x, y, 1), xr)

        amp = resid.max()
        if not amp > 0:  # nothing above baseline, fall back to thirds of the range
            flat = abs(np.ptp(y)) or 1.0
            quad, slope, inter = self.baseline_guess(x, y, 2)
            return [x[0] + span / 3, span / 10, flat,
                    x[0] + 2 * span / 3, span / 10, flat, quad, slope, inter]

        idx, props = find_peaks(resid, prominence=0.2 * amp, distance=max(3, len(x) // 20))
        found = []
        if len(idx):
            widths = peak_widths(resid, idx, rel_height=0.5)[0]
            pick = np.argsort(props['prominences'])[::-1][:2]  # the two most prominent peaks
            found = [(x[i], self.width_to_sigma(widths[p], dx, span), resid[i])
                     for p, i in zip(pick, idx[pick])]

        if len(found) == 1:  # look for the second peak away from the one we have
            pos, sig, hei = found[0]
            away = np.abs(x - pos) > 3 * sig
            if away.any():
                j = int(np.argmax(np.where(away, resid, -np.inf)))
                found.append((x[j], sig, max(resid[j], 0.05 * amp)))
            else:
                found.append((pos + 4 * sig, sig, 0.05 * amp))
        elif not found:
            found = [(x[0] + span / 3, span / 10, amp), (x[0] + 2 * span / 3, span / 10, amp)]

        found.sort(key=lambda p: p[0])  # peak 1 is always the lower x peak, polarization sign depends on it
        (pos1, sig1, hei1), (pos2, sig2, hei2) = found
        if pos2 - pos1 < dx:  # keep the two gaussians distinguishable
            pos1, pos2 = pos1 - dx, pos2 + dx

        # now the peaks are placed, fit the quadratic to everything that isn't one
        off = (np.abs(x - pos1) > 3 * sig1) & (np.abs(x - pos2) > 3 * sig2)
        quad, slope, inter = self.baseline_guess(x, y, 2, off)
        base = np.polyval([quad, slope, inter], xr)  # heights measured off that baseline
        hei1 = max(np.interp(pos1, x, y - base), 0.05 * amp)
        hei2 = max(np.interp(pos2, x, y - base), 0.05 * amp)
        return [pos1, sig1, hei1, pos2, sig2, hei2, quad, slope, inter]

    def width_to_sigma(self, width, dx, span):
        '''Convert a peak width in samples to a gaussian sigma in x units'''

        return float(np.clip(width * dx / 2.3548, dx, span / 2))

    def fit_bounds(self, p0, x, span, dx):
        '''Bounds built around the starting guess, so the peaks stay separate and in range.

        Args:
            p0: Starting parameters
            x: Sorted scan x axis values
            span: Width of the scan range
            dx: Typical step between scan points
        Returns:
            Tuple of (lower, upper) bound lists
        '''

        margin = max(0.1 * span, 2 * dx)  # let a clipped peak sit just outside the window
        split = float(np.clip(0.5 * (p0[0] + p0[3]),  # peaks stay either side of their own midpoint
                              x[0] - margin + dx, x[-1] + margin - dx))
        lower = [x[0] - margin, dx, 0, split, dx, 0, -np.inf, -np.inf, -np.inf]
        upper = [split, span, np.inf, x[-1] + margin, span, np.inf, np.inf, np.inf, np.inf]
        return lower, upper

    def check_fit(self, pf, pstd, ssr, x, y, bounds):
        '''Sanity check a converged fit before its result is used or seeded forward.

        Args:
            pf: Fitted parameters
            pstd: Standard errors on the fitted parameters
            ssr: Sum of squared residuals of the fit
            x: Sorted scan x axis values the fit was made against
            y: Scan signal values in the same order
            bounds: The (lower, upper) bounds the fit ran under
        Returns:
            Tuple of (ok, message)
        '''

        if not np.all(np.isfinite(pf)):
            return False, 'Fit returned non-finite parameters.'
        pos1, sig1, hei1, pos2, sig2, hei2 = pf[:6]
        if hei1 <= 0 or hei2 <= 0:
            return False, 'Fit found a peak with no amplitude.'
        lower, upper = bounds
        edge = 0.002 * (upper[3] - lower[0])  # a parameter sitting on its own bound means
        if pos1 <= lower[0] + edge or pos2 >= upper[3] - edge:  # the fit wanted to go further
            return False, 'Fit pushed a peak off the end of the scan.'
        if sig1 >= upper[1] - 0.002 * upper[1] or sig2 >= upper[4] - 0.002 * upper[4]:
            return False, 'Fit widened a peak into the baseline.'
        if min(hei1, hei2) < 0.005 * max(hei1, hei2):
            return False, 'One peak has no amplitude next to the other.'
        if abs(pos2 - pos1) < 0.5 * (sig1 + sig2):
            return False, 'Fit collapsed both peaks onto one feature.'
        if not np.all(np.isfinite(pstd[:6])):
            return False, 'Fit uncertainties are undefined.'
        if pstd[2] * MIN_PEAK_SIGNIFICANCE > hei1 or pstd[5] * MIN_PEAK_SIGNIFICANCE > hei2:
            return False, 'Peak amplitudes are not significant above the noise.'
        xr = x - self.x_ref  # the null model is now the bare baseline, with no gaussians at all
        base_ssr = float(np.sum((y - np.polyval(np.polyfit(xr, y, 2), xr)) ** 2))
        if not ssr < (1 - MIN_PEAK_POWER) * base_ssr:
            return False, 'Peaks do not stand out from the curved baseline.'
        return True, 'Fit good.'

    def r_squared(self, y, fit):
        '''Coefficient of determination, as a quick number for fit quality'''

        ss_tot = np.sum((y - y.mean()) ** 2)
        if not ss_tot > 0:
            return np.nan
        return float(1 - np.sum((y - fit) ** 2) / ss_tot)

    def peaks(self, x, *p):
        '''Two gaussians on a quadratic baseline.

        The baseline is referenced to x_ref, the middle of the scan, so its three
        coefficients stay nearly independent of each other. Against raw current the
        x squared, x and constant terms are almost collinear and the covariance
        comes back singular.
        '''
        xr = x - self.x_ref
        g1 = p[2] * np.exp(-np.power((x - p[0]), 2) / (2 * np.power(p[1], 2)))
        g2 = p[5] * np.exp(-np.power((x - p[3]), 2) / (2 * np.power(p[4], 2)))
        base = p[6] * np.power(xr, 2) + p[7] * xr + p[8]
        return g1 + g2 + base

    def baseline_guess(self, x, y, deg, within=None):
        '''Robust polynomial baseline: fit, drop whatever stands above it, refit.

        The cut is at three robust sigma above the curve rather than at a fixed
        quota, so once the peaks are excluded it stops eating real baseline points
        at the ends of the scan, which would otherwise bend the curve.

        Args:
            x: Sorted scan x axis values
            y: Scan signal values in the same order
            deg: Polynomial degree
            within: Optional mask of points allowed to take part
        Returns:
            Polynomial coefficients about x_ref, highest power first
        '''

        xr = x - self.x_ref
        ok = np.ones(len(y), dtype=bool) if within is None else np.asarray(within)
        if ok.sum() < deg + 2:
            ok = np.ones(len(y), dtype=bool)
        coef = np.polyfit(xr[ok], y[ok], deg)
        for _ in range(4):
            resid = y - np.polyval(coef, xr)
            med = np.median(resid[ok])
            mad = np.median(np.abs(resid[ok] - med))
            if not mad > 0:
                break
            keep = ok & (resid < med + 3 * 1.4826 * mad)  # peaks are positive going, cut high only
            if keep.sum() < deg + 3:
                break
            coef = np.polyfit(xr[keep], y[keep], deg)
        return coef

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
