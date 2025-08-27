import serial
import time
import pandas as pd
import numpy as np

ser = serial.Serial(
    port = '/dev/ttyACM0',
    baudrate = 115200,
    timeout=1
)

#ser.close()

if ser.is_open:
    print('connected to ', ser.name)

time.sleep(1)

#ser.write(b'help\r')
#ser.write(b'data\r')
#ser.write(b'frequencies\r')

#time.sleep(2)

#print(ser.in_waiting)
#response = ser.readline().decode('utf-8').strip()
#response = ser.read_all().decode('utf-8').strip()

#print("Response: ", response)

#ser.write(b'frequencies\r')
#ser.write(b'cwfreq 22500000\r')
#ser.write(b'scan 22499999 22500001 50 7 \r')

start = 22499999
stop = 22500001
steps = 50
mode = 7
freq = 46000000

#message = f'scan {start} {stop} {steps} {mode} \r'
#message = f'cwfreq {freq}\r'
message = f'data 0 1\r'
message_encoded = message.encode('utf-8')
ser.write(message_encoded)

time.sleep(2)

print(ser.in_waiting)
#response = ser.readline().decode('utf-8').strip()
response = ser.read_all().decode('utf-8').strip().split('\n')
trimmed_response = response[1:-1]

rows = [line.split() for line in trimmed_response]

print("Response: ", response)

df = pd.DataFrame(rows, columns = ['S11_real', 'S11_imag'])

#df = pd.DataFrame(rows, columns = ['Frequency', 'S11_real', 'S11_imag', 'S21_real', 'S21_imag'])
df = df.apply(pd.to_numeric)
print(df.head())

values = np.sqrt(df['S11_real'].values**2 + df['S11_imag'].values**2)

#print(values)

print(20*np.log10(values))

#print(response.shape)

ser.close()