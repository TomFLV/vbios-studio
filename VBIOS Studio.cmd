@echo off
rem Start VBIOS Studio without a console window.
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m vbios_studio
