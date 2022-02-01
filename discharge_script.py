#!/usr/bin/python3

import yaml
from app.instruments import LabJack, SigGen


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


    siggen.set_freq(15)
    siggen.set_amp(0.666)
    print(labjack.read_back())


if __name__ == '__main__':
    main()
