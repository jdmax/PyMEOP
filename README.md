# PyMEOP

Data acquisition for optical polarimetry of helium-3 polarized by metastability
exchange optical pumping (MEOP). PyMEOP scans a probe laser across two absorption
lines of the metastable atoms in the discharge, fits the two peaks, and reports the
nuclear polarization from the ratio of their heights. It runs as a PyQt5 desktop
application on the polarizer's control computer.

## Running it

```
pip install -r requirements.txt
python main.py
```

On Ubuntu, a Wayland error at startup means `qtwayland5` needs installing.

## Instruments

Each instrument is reached over the network at the address set in `config.yaml`:

| Setting | Instrument |
|---|---|
| `probe_ip` | Toptica DLC probe laser controller (current and temperature) |
| `meter_ip` | wavelength meter |
| `lockin_ip` | lock-in amplifier reading the probe absorption |
| `siggen_ip` | SRS SG380 signal generator driving the discharge |

The same file sets the scan timing, the scan x axis (`current` or `wavelength`), the
baseline under the peaks (`baseline_degree`: 1 straight, 2 quadratic), and the
data and log directories.

## Using it

The window has two tabs.

**Find Peaks** sweeps the probe laser's temperature or current over a wide range
to locate the absorption lines and choose the scan range.

**Run** does the measurement. Each scan steps the laser current over the peaks
while reading the lock-in, then fits two peaks on a baseline (`app/scanfit.py`).
The line shape is selectable: Gaussian for Doppler broadened lines at low pressure,
or Voigt once pressure broadening is comparable, around 100 mbar. With peak height
ratio `r` and the ratio `r0` measured with the gas unpolarized, the polarization is

```
P = (r/r0 - 1) / (r/r0 + 1)
```

The zero amplitudes are taken from the latest scan, or averaged over a range of
scans selected on the polarization history plot. The Run tab also
controls the discharge signal generator, including:

- a **Larmor drive**, which drives the discharge coil at the helium-3 Larmor
  frequency for the field entered, optionally swept in frequency, for a set time,
  then restores the discharge and resumes scanning;
- a **discharge-off relaxation** cycle, which switches the discharge off and on for
  set times to measure relaxation.

## Data

Each scan is written as one JSON line to an event file in `data/`, holding the raw
scan, the fit parameters and uncertainties, and the polarization. The file in use
is named `current_<start>.txt` and is renamed to `<start>__<end>.txt` when closed
(times in UTC). Logs go to `log/`.

## Data browser

`webapp/` holds a separate, read-only web app for browsing and refitting the
event files, locally or on a server that mirrors the data (scripts in `deploy/`).
See [webapp/README.md](webapp/README.md).

## References

- https://doi.org/10.1016/j.nima.2019.02.019
- https://doi.org/10.1016/j.nima.2021.165590
- https://doi.org/10.1016/j.nima.2023.168792
- https://doi.org/10.1016/j.nima.2025.170870
