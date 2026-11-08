# Credits

VBIOS Studio is a new user interface. The work that makes it possible is not ours.

## The engine: panchovix

Everything that reads, validates, edits, relocates and loads a VBIOS is by **panchovix**. It is bundled unchanged in
`vendor/nvidia_vbios`:

| Project | Version | What it does here |
|---|---|---|
| [NVIDIA VBIOS Vix Editor](https://github.com/Panchovix/nvidia-vbios-vix-editor) | 0.1.43 | ROM parsing, the field registry and its hardware notes, staging and validation, checksum repair, Ada/Ampere/Turing loader payloads |
| [NVIDIA Windows VBIOS Vix Loader](https://github.com/Panchovix/nvidia-windows-vbios-vix-loader) | 0.4.1 | Payload checks, the driver registry override (load/unload), NvAPI verify |

Copyright (C) 2026 panchovix, GPL-3.0-or-later.

The two packages share the `nvidia_vbios` namespace. They are merged into one tree here: the editor's tree, plus the
loader's `payload.py` and `windows_loader/` (their `families.py` files are identical). No upstream file was changed.

If VBIOS Studio is useful to you, the credit and any support belong upstream first. The upstream projects take issues
and pull requests.

## The original research: pantsoftime

The project family builds on **pantsoftime**'s original GB202 VBIOS editor and `vbios_override` work, published on
[overclock.net](https://www.overclock.net). Without it, none of this would exist.

panchovix also thanks **b00nza**, author of [mVolt+](https://github.com/b00nz/mVolt), whose BIOS override on Blackwell
uses the same registry value.

## Sensor interfaces: LibreHardwareMonitor

The Sensors page reads hot spot and memory junction temperatures, core voltage, power topology (% of TDP), video
and bus loads, fan RPM, and base and boost clocks. It does this through private NVAPI interfaces, using the interface
IDs and structure layouts documented in
[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (`Interop/NvApi.cs`,
`Hardware/Gpu/NvidiaGpu.cs`, MPL-2.0). VBIOS Studio re-implements those calls in Python (`vbios_studio/sensors.py`);
no LibreHardwareMonitor source file is included. Everything else on that page comes from NVIDIA's documented NVML
library.

## VBIOS Studio

The Qt window, profiles, dashboard and elevated helper are by Tom (TomFLV), 2026. They are
GPL-3.0-or-later, the same license as the code they build on.

## Third-party components

- [Qt for Python (PySide6)](https://www.qt.io/qt-for-python), LGPL-3.0. Installed from PyPI when running from source.
  The portable exe bundles its unmodified libraries, which it unpacks to a temporary folder at start. The build
  script (`packaging/build.ps1`) rebuilds it, with any PySide6 version, from this source.
- [PyInstaller](https://pyinstaller.org) packs the portable exe (GPL-2.0 with its bootloader exception).
- The portable exe also contains the CPython runtime (PSF License).
- The upstream third-party notices are in `vendor/licenses/`.

NVIDIA and GeForce are trademarks of NVIDIA Corporation. This project is not affiliated with or endorsed by NVIDIA,
panchovix or pantsoftime.
