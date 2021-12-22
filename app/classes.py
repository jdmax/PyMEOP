#PyMEOP, J. Maxwell 2021

import datetime
import json
import pytz
import numpy as np

from scipy import optimize

class Event():
    '''Storage and methods for events
    '''
    
    def __init__(self):
        
        self.start_time =  datetime.datetime.now(tz=datetime.timezone.utc)        
        self.start_stamp = self.start_time.timestamp()