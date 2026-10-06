# PyMEOP

Data acquisition and display software for measuring polarization in metastability
exchange optical pumping (MEOP) of helium-3. A PyQt5 application drives the probe
laser, wavelength meter, lock-in amplifier and signal generator, scans the probe
over the absorption peaks, fits them and reports the polarization.

## Running it

```
pip install -r requirements.txt
python main.py
```

Instrument addresses and scan settings are in `config.yaml`. Event files are
written to `data/` and logs to `log/`.

A read-only web browser for the recorded event files is in `webapp/`; see
[webapp/README.md](webapp/README.md).

## References

- https://doi.org/10.1016/j.nima.2019.02.019
- https://doi.org/10.1016/j.nima.2021.165590
- https://doi.org/10.1016/j.nima.2023.168792
- https://doi.org/10.1016/j.nima.2025.170870
