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

The window has tabs for Run, Find Peaks, Larmor Drive, Relaxation, Discharge
Power and Slow Controls.

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

**Discharge Power** measures the RF power delivered to the discharge cell. A dual
directional coupler between the Tomco amplifier and the matching box sends the
forward wave to the PWR-8GHS power sensor and to the SR844 lock-in's reference,
and the reflected wave to the lock-in's signal input. The tab reads both through
their IOCs (`discharge` section of `config.yaml`) and computes

```
P_fwd    = P_sensor · K_fwd
M        = (X + iY) / √(P_sensor / (1 + m²/2))     reflected over forward, m = AM depth
Γ        = (M/g − E_d) / E_r                       g = lock-in range gain
P_net    = P_fwd (1 − |Γ|²)
P_plasma = P_net (1 − Q_L/Q_U)
```

A reading is made at each lock-in poll and is not used when the lock-in is unlocked,
overloaded or has been touched, when the sensor is out of range or stale, or while
the drive level is settling. The sensor polls more slowly than the lock-in, so after
a step in drive or reflection, readings wait for the sensor to catch up, about a
second. Warnings show when the drive frequency or AM depth has moved from the
calibration's, when the sensor's compensation frequency doesn't match the drive
(**Match to Drive** fixes it), or when the sensor is far from its −15 dBm target.

Calibrations are kept in `app/discharge_cal.yaml`, with a history of every one saved:

- **Γ calibration**, at about 0.5 W: connect a 50 Ω load, then an open, at the end of
  the cable that normally goes to the matching box, **Measure** each, then **Save
  Calibration**. Then measure the 25 Ω standard (two terminators on a tee, with their
  DMM resistances) on the same sensitivity to check Γ. Measured again on another
  sensitivity, it gives that range's gain relative to the calibration's, which then
  corrects Γ there, so calibrate at 0.5 W and operate at tens of W on another range.
  Pin the lock-in's reference phase in the IOC settings, since a changed phase
  invalidates E_d and E_r.
- **Forward calibration**: enter the reference meter reading and the attenuation to
  it and **Record K_fwd**, or type K_fwd in dB.
- **Tank Q**: type Q_U and Q_L, or take them from a **resonance sweep**, which steps
  the SG380, fits the absorbed fraction 1 − |Γ|² (width f₀/Q_L), and puts the SG380
  back where it was. From the Γ locus it also finds the coupling β, so a sweep of the
  unlit tank gives Q_U = Q_L(1 + β). Adopting a sweep also records which sign of Im Γ
  means the drive is above resonance, shown with the readings. Off resonance most of
  the drive is reflected, so sweep at low power.

**Sensor AM Check** turns the SG380's AM off and on and compares the sensor's ratio
with 1 + m²/2, since the sensor is specified for CW only. It interrupts the probe
lock-in's reference, so it refuses to run during scans.

The **reflection interlock** acts when |Γ| stays over its limit for
`interlock_samples` valid readings in a row: it sets the SG380 to the safe amplitude,
or turns its output off, then stays tripped until **Reset**. It only acts on
calibrated, valid readings, so it adds to the amplifier's own protection rather
than replacing it.

**Slow Controls** shows EPICS process variables listed under `epics: pvs:` in
`config.yaml`, grouped by their `group`, colored by alarm severity and greyed when
disconnected. A PV marked `control: true` gets a setpoint box and Set button, and
must have `limits`; a value outside them is refused. A `readback` PV can be shown
beside a control's setpoint. If the IOCs aren't reached by broadcast, set
`ca_addr_list`. This uses `pyepics`, which brings its own Channel Access libraries.

## Data

Each scan is written as one JSON line to an event file in `data/`, holding the raw
scan, the fit parameters and uncertainties, the polarization, and under `pvs` the
last value of every slow controls PV when the scan ended (null if disconnected),
and under `discharge` the latest discharge power reading (null if there is none).
The file in use is named `current_<start>.txt` and is renamed to
`<start>__<end>.txt` when closed (times in UTC). Logs go to `log/`.

Slow controls are also logged on their own, in `slowlog/` (`slow_dir`), named the
same way with a new file each UTC day. Each line is a JSON record with a `kind`:

- `snapshot`: every PV's last value, IOC timestamp and alarm severity, every
  `log_interval` seconds;
- `set`: a value changed from PyMEOP, with the old and new value and its `source`
  (`user`, `scan`, `larmor`, `relaxation`, `range`). This covers EPICS controls and
  PyMEOP's own settings: `siggen.freq`, `siggen.amp`, `siggen.output`, `siggen.fm`
  and `zero`, and the discharge power calibrations (`discharge.gamma_cal`,
  `discharge.k_fwd`, `discharge.tank`, ...) and interlock trips (source `interlock`);
- `discharge`: the latest discharge power reading, every `log_interval` seconds:
  X, Y, sensor dBm, M, Γ, the four powers, and the calibration they used;
- `connect` / `disconnect`: a PV came or went.

## Data browser

`webapp/` holds a separate, read-only web app for browsing and refitting the
event files, locally or on a server that mirrors the data (`start_server.sh` and
`stop_server.sh` here, `deploy/mirror_data.sh` for the mirroring).
See [webapp/README.md](webapp/README.md).

## References

- https://doi.org/10.1016/j.nima.2019.02.019
- https://doi.org/10.1016/j.nima.2021.165590
- https://doi.org/10.1016/j.nima.2023.168792
- https://doi.org/10.1016/j.nima.2025.170870
