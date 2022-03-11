#!/usr/bin/python3

import yaml
from app.instruments import LabJack, SigGen
import numpy as np
import time
import matplotlib.pyplot as plt
from matplotlib import cm
from mpl_toolkits.mplot3d import axes3d


def main():

    config_filename = 'config.yaml'
    with open(config_filename) as f:                           # Load settings from YAML file
       config_dict = yaml.load(f, Loader=yaml.FullLoader)
    settings = config_dict['settings']                    # dict of settings

    try: 
        siggen = SigGen(settings)
        print(f"Connected to signal generator at {settings['siggen_ip']}")
    except Exception as e:
        print(f"Unable to connect to Signal Generator at {settings['siggen_ip']}, {e}")

    try: 
        labjack = LabJack(settings)
        print(f"Connected to LabJack at {settings['labjack_ip']}")
    except Exception as e:
        print(f"Unable to connect to LabJack at {settings['labjack_ip']}, {e}")


    ###set scan range for frequency and amplitude
    freq_min = 9
    freq_max = 15
    freq_step = 1
    freq_nstep = int((freq_max-freq_min)/freq_step)+1
    freq_list = np.linspace(freq_min, freq_max, freq_nstep)
    print(f"Frequency scan list: {freq_list}")
    
    amp_min = 0.03
    amp_max = 0.12
    amp_step = 0.01
    amp_nstep = int((amp_max-amp_min)/amp_step)+1
    amp_list = np.linspace(amp_max, amp_min, amp_nstep)
    print(f"Amplitude scan list: {amp_list}")
    
    
    ###initialization
    siggen.set_amp(0.001)
    time.sleep(1)
    background = labjack.read_back()[0]
    print(f"background = {background}")
    discharge_pick = 1000
    freq_pick = 0
    amp_pick = 0
    data = np.zeros((freq_nstep, amp_nstep))
   
    ###scan over frequencies and amplitudes
    for i_freq in range(0, freq_nstep):
        siggen.set_freq(12)
        siggen.set_amp(1.5)
        time.sleep(3)
        siggen.set_freq(freq_list[i_freq])
        for i_amp in range(0, amp_nstep):
            siggen.set_amp(amp_list[i_amp])
            time.sleep(1)
            print(freq_list[i_freq], amp_list[i_amp], labjack.read_back()[0])
            data[i_freq, i_amp] = labjack.read_back()[0] - background
            # siggen.set_amp(0.001)
            # time.sleep(0.5)
            if data[i_freq, i_amp]>0.03 and data[i_freq, i_amp]<discharge_pick:
                discharge_pick = data[i_freq, i_amp]
                freq_pick = freq_list[i_freq]
                amp_pick = amp_list[i_amp]            
    print(data)
    print(f"freq_pick = {freq_pick}, amp_pick = {amp_pick}")
    
    ###plot discharge mapping resuls
    # plt.figure()
    # X,Y = np.meshgrid(freq_list, amp_list)
    # ax = plt.subplot(111, projection='3d')
    # ax.set_xlabel('frequency (MHz)')
    # ax.set_ylabel('Vpp')
    # ax.set_zlabel('discharge level')
    # ax.plot_surface(X, Y, data, cmap=cm.coolwarm)
    # plt.show()

   
if __name__ == '__main__':
    main()
