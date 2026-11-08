# Build the portable single-file exe: dist\VBIOS Studio.exe (no Python or install needed on the target PC).
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
& $py -m pip install -q pyinstaller
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name 'VBIOS Studio' `
    --icon assets\vbios-studio.ico `
    --paths vendor --paths . `
    --add-data 'vbios_studio\presets;vbios_studio\presets' `
    --add-data 'LICENSE;.' --add-data 'CREDITS.md;.' --add-data 'vendor\licenses;vendor\licenses' `
    --hidden-import nvidia_vbios.windows_loader.win32 `
    --hidden-import nvidia_vbios.windows_loader.operations `
    --exclude-module gi --exclude-module tkinter `
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtCharts --exclude-module PySide6.QtPdf `
    packaging\entry.py
$exe = Join-Path $root 'dist\VBIOS Studio.exe'
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"$hash  VBIOS Studio.exe" | Set-Content (Join-Path $root 'dist\SHA256SUMS.txt') -Encoding ascii
"built $exe  ($([math]::Round((Get-Item $exe).Length / 1MB, 1)) MB)  sha256 $hash"
