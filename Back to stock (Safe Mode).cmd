@echo off
rem Remove the VBIOS override without the window (for Safe Mode). Right-click -> Run as administrator, then restart.
cd /d "%~dp0"
set PYTHONPATH=%~dp0vendor
"%~dp0.venv\Scripts\python.exe" -m nvidia_vbios.windows_loader unload --gpu 0
echo.
echo If that failed: regedit, HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\NNNN
echo (the subkey whose DriverDesc is your GPU), delete "vbios" and "VixLoaderPreviousEnableGpuFirmware", restart.
pause
