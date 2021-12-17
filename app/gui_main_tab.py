'''PyNMR, J.Maxwell 2020
'''
import datetime
import time
import math
from PyQt5.QtWidgets import QWidget, QLabel, QGroupBox, QHBoxLayout, QVBoxLayout, QGridLayout, QLineEdit, QSpacerItem, QSizePolicy, QComboBox, QPushButton, QProgressBar
from PyQt5.QtGui import QIntValidator, QDoubleValidator, QValidator
from PyQt5.QtCore import QThread, pyqtSignal, Qt
import pyqtgraph as pg
 
   
class MainTab(QWidget):
    '''Creates main tab. Starts threads for run and to update plots'''
    def __init__(self, parent):
        super(QWidget,self).__init__(parent)
        self.__dict__.update(parent.__dict__)
        
        self.parent = parent
               
        # pyqtgrph styles        
        pg.setConfigOptions(antialias=True)
        self.raw_pen = pg.mkPen(color=(250, 0, 0), width=1.5)
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        
        
        # Populate Run Tab
        self.main = QHBoxLayout()            # main layout
        self.setLayout(self.main)
        self.left = QVBoxLayout()     # left part of main layout
        self.main.addLayout(self.left)
               
        # Populate Controls box
        self.controls_box = QGroupBox('Controls')
        self.controls_box.setLayout(QGridLayout())
        self.left.addWidget(self.controls_box)
        
        self.temp_lo_label = QLabel('Temperature Lower Limit:')
        self.controls_box.layout().addWidget(self.temp_lo_label, 0, 0)
        self.temp_lo_edit =  QLineEdit()
        self.temp_lo_edit.setValidator(QDoubleValidator(3.0, 45.0, 3, notation=QDoubleValidator.StandardNotation))
        self.controls_box.layout().addWidget(self.temp_lo_edit, 0, 1)
        
        self.temp_up_label = QLabel('Temperature Upper Limit:')
        self.controls_box.layout().addWidget(self.temp_up_label, 1, 0)
        self.temp_up_edit =  QLineEdit()
        self.temp_up_edit.setValidator(QDoubleValidator(3.0, 45.0, 3, notation=QDoubleValidator.StandardNotation))
        self.controls_box.layout().addWidget(self.temp_up_edit, 1, 1)
        
        self.temp_sw_button = QPushButton("Run Temperature Sweep")      
        self.controls_box.layout().addWidget(self.temp_sw_button, 1, 2)
        self.temp_sw_button.clicked.connect(self.temp_sw_pushed)
        
        self.temp_label = QLabel('Temperature:')
        self.controls_box.layout().addWidget(self.temp_label, 2, 0)
        self.temp_edit =  QLineEdit()
        self.controls_box.layout().addWidget(self.temp_edit, 2, 1)
        
        self.start_button = QPushButton("Start Sweeps")      
        self.controls_box.layout().addWidget(self.start_button, 2, 2)
        self.start_button.clicked.connect(self.start_pushed)
        
        self.read_button = QPushButton("Read Voltage")      
        self.controls_box.layout().addWidget(self.read_button, 3, 2)
        self.read_button.clicked.connect(self.read_pushed)
        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)
        
        
        self.base_wid = pg.PlotWidget(title='Graph or something')
        self.base_wid.showGrid(True,True)
        self.base_wid.addLegend(offset=(0.5, 0))
        self.right.addWidget(self.base_wid)



    def temp_sw_pushed(self):
        '''Doc'''
        print(f"{float(self.temp_lo_edit.text())} to {float(self.temp_up_edit.text())}")

    def start_pushed(self):
        '''Doc'''
        print(self.parent.probe.set_temp(self.temp_edit.text()))
        
    def read_pushed(self):
        '''Doc'''
        print(self.parent.labjack.read_back())
        
        