#!/usr/bin/python3
''' J.Maxwell 2021
'''
from PyQt5 import QtCore
from PyQt5 import QtWidgets
from app.gui import MainWindow


def main():
    '''Main executable calls main gui
    '''
    app = QtWidgets.QApplication([])
    #app.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True) #enable highdpi scaling
    app.setApplicationName("JLab Polarization Display")
    gui = MainWindow()
    gui.show()
    app.exec_()

if __name__ == '__main__':
    main()
