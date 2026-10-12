'''PyMEOP, J.Maxwell
'''
from PyQt5.QtWidgets import QWidget, QLabel, QGroupBox, QVBoxLayout, QGridLayout, QLineEdit, QPushButton
from PyQt5.QtGui import QDoubleValidator

SEVERITY_STYLES = {0: "", 1: "color: #aa6600", 2: "color: #aa0000", 3: "color: #aa00aa"}
DISCONNECTED_STYLE = "color: #999999"


class SlowTab(QWidget):
    '''Slow controls tab: EPICS PVs from the config, grouped, with a setpoint box
    and Set button for each control'''

    def __init__(self, parent):
        super(QWidget, self).__init__(parent)
        self.parent = parent
        self.pvs = parent.pvs

        self.value_labels = {}   # PV name: labels showing its value
        self.setpoints = {}      # control PV name: setpoint edit box

        self.main = QVBoxLayout()
        self.setLayout(self.main)

        if parent.pv_problems:
            problems = QLabel("\n".join(parent.pv_problems))
            problems.setStyleSheet("color: #aa6600")
            problems.setWordWrap(True)
            self.main.addWidget(problems)
        if self.pvs.error:
            error = QLabel(self.pvs.error)
            error.setStyleSheet("color: #aa0000")
            error.setWordWrap(True)
            self.main.addWidget(error)
        if not self.pvs.specs:
            self.main.addWidget(QLabel("No process variables are listed in the epics section of config.yaml."))

        boxes = {}
        for spec in self.pvs.specs:
            if spec.group not in boxes:
                box = QGroupBox(spec.group)
                grid = QGridLayout()
                box.setLayout(grid)
                for col, head in enumerate(("", "Value", "Readback", "Setpoint")):
                    grid.addWidget(QLabel(f"<b>{head}</b>"), 0, col)
                grid.setColumnStretch(5, 1)
                self.main.addWidget(box)
                boxes[spec.group] = grid
            self.add_row(boxes[spec.group], spec)

        self.main.addStretch(1)
        self.status = QLabel(f"Logging every {parent.log_interval:g} s to {parent.slowlog.directory}/")
        self.status.setWordWrap(True)
        self.main.addWidget(self.status)

        self.pvs.changed.connect(self.show_value)
        self.pvs.connection.connect(lambda name, conn: self.show_value(name))
        for name in self.pvs.names():
            self.show_value(name)

    def add_row(self, grid, spec):
        '''One PV: its label, value, readback if it has one, and setpoint if a control'''

        row = grid.rowCount()
        grid.addWidget(QLabel(f"{spec.label}{f' ({spec.units})' if spec.units else ''}:"), row, 0)
        value = QLabel()
        value.setToolTip(spec.pv)
        grid.addWidget(value, row, 1)
        self.value_labels[spec.pv] = (value, spec)
        if spec.readback:
            readback = QLabel()
            readback.setToolTip(spec.readback)
            grid.addWidget(readback, row, 2)
            self.value_labels[spec.readback] = (readback, spec)
        if spec.control:
            lo, hi = spec.limits
            edit = QLineEdit()
            edit.setValidator(QDoubleValidator(lo, hi, 6))
            edit.setPlaceholderText(f"{lo:g} to {hi:g}")
            edit.setToolTip(f"Limits {lo:g} to {hi:g} {spec.units}".rstrip())
            grid.addWidget(edit, row, 3)
            button = QPushButton("Set")
            button.clicked.connect(lambda checked, name=spec.pv: self.set_pushed(name))
            grid.addWidget(button, row, 4)
            self.setpoints[spec.pv] = edit

    def show_value(self, name):
        '''Show a PV's latest value, colored by alarm severity, grey if disconnected'''

        if name not in self.value_labels:
            return
        label, spec = self.value_labels[name]
        if not self.pvs.connected.get(name):
            label.setText("disconnected")
            label.setStyleSheet(DISCONNECTED_STYLE)
            return
        label.setText(spec.show(self.pvs.value(name)))
        label.setStyleSheet(SEVERITY_STYLES.get(self.pvs.severity(name), ""))

    def set_pushed(self, name):
        '''Send the setpoint in the box to a control PV'''

        spec = self.pvs.controls[name]
        msg, style = "", "color: #aa0000"
        try:
            value = float(self.setpoints[name].text())
        except ValueError:
            msg = f"Enter a number to set {spec.label}."
        if not msg:
            try:
                self.pvs.put(name, value)
                msg, style = f"Set {spec.label} to {spec.show(value)} {spec.units}".rstrip(), "color: #007700"
            except ValueError as e:  # outside limits or not connected
                msg = str(e)
            except Exception as e:
                msg = f"Failed to set {spec.label}: {e}"
        self.status.setText(msg)
        self.status.setStyleSheet(style)
        self.parent.status_bar.showMessage(msg)
