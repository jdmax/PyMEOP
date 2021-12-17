'''PyNMR, J.Maxwell 2020
'''
import datetime
import time
import math
from PyQt5.QtWidgets import QWidget, QLabel, QGroupBox, QHBoxLayout, QVBoxLayout, QGridLayout, QLineEdit, QSpacerItem, QSizePolicy, QComboBox, QPushButton, QProgressBar
from PyQt5.QtGui import QIntValidator, QDoubleValidator, QValidator
from PyQt5.QtCore import QThread, pyqtSignal, Qt
import pyqtgraph as pg
 
   
class FindTab(QWidget):
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
               
        # Find discharge box
        self.discharge_box = QGroupBox('Discharge')
        self.discharge_box.setLayout(QGridLayout())
        self.left.addWidget(self.discharge_box)
        
        self.freq_lo_label = QLabel('Frequency Range (MHz):')
        self.discharge_box.layout().addWidget(self.freq_lo_label, 0, 0)
        self.freq_lo_edit =  QLineEdit()
        self.freq_lo_edit.setValidator(QDoubleValidator(1, 100, 3, notation=QDoubleValidator.StandardNotation))
        self.discharge_box.layout().addWidget(self.freq_lo_edit, 0, 1)
        self.freq_up_edit =  QLineEdit()
        self.freq_up_edit.setValidator(QDoubleValidator(1, 100, 3, notation=QDoubleValidator.StandardNotation))
        self.discharge_box.layout().addWidget(self.freq_up_edit, 0, 2)
        
        self.volt_lo_label = QLabel('Voltage Range:')
        self.discharge_box.layout().addWidget(self.volt_lo_label, 1, 0)
        self.volt_lo_edit =  QLineEdit()
        self.volt_lo_edit.setValidator(QDoubleValidator(0, 1, 3, notation=QDoubleValidator.StandardNotation))
        self.discharge_box.layout().addWidget(self.volt_lo_edit, 1, 1)        
        self.volt_up_edit =  QLineEdit()
        self.volt_up_edit.setValidator(QDoubleValidator(0, 1, 3, notation=QDoubleValidator.StandardNotation))
        self.discharge_box.layout().addWidget(self.volt_up_edit, 1, 2)
        
        self.dis_button = QPushButton("Run Discharge Search")      
        self.discharge_box.layout().addWidget(self.dis_button, 2, 2)
        self.dis_button.clicked.connect(self.dis_pushed)
        
        
        
        # Find Temp peaks box
        self.probe_temp_box = QGroupBox('Probe Temperature Peaks')
        self.probe_temp_box.setLayout(QGridLayout())
        self.left.addWidget(self.probe_temp_box)
        
        self.temp_label = QLabel('Temperature:')
        self.probe_temp_box.layout().addWidget(self.temp_label, 0, 0)
        self.temp_edit =  QLineEdit()
        self.probe_temp_box.layout().addWidget(self.temp_edit, 0, 1)
        
        self.start_button = QPushButton("Run Temperature Search")      
        self.probe_temp_box.layout().addWidget(self.start_button, 0, 2)
        self.start_button.clicked.connect(self.probe_temp_pushed)
        
        
        self.right = QVBoxLayout()     # right part of main layout
        self.main.addLayout(self.right)
        
        
        self.base_wid = pg.PlotWidget(title='Graph or something')
        self.base_wid.showGrid(True,True)
        self.base_wid.addLegend(offset=(0.5, 0))
        self.right.addWidget(self.base_wid)



    def dis_pushed(self):
        '''Doc'''
        pass

    def probe_temp_pushed(self):
        '''Doc'''
        pass
        
        