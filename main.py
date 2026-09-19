#!/usr/bin/python3
""" J.Maxwell 2021
"""
import sys

from PyQt5 import QtWidgets
from app.core.config import ConfigError, load_settings
from app.gui import MainWindow
# If there is a wayland error on Ubuntu, install qtwayland5

CONFIG_FILE = 'config.yaml'


def main():
    """Main executable calls main gui
    """
    app = QtWidgets.QApplication([])
    #app.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True) #enable highdpi scaling
    app.setApplicationName("MEOP Polarization Display")
    try:
        settings = load_settings(CONFIG_FILE)
    except ConfigError as e:
        print(e, file=sys.stderr)
        QtWidgets.QMessageBox.critical(None, 'PyMEOP config error', str(e))
        sys.exit(1)
    gui = MainWindow(settings)
    gui.show()
    app.exec_()

if __name__ == '__main__':
    main()
