@echo off
REM Arranca ONIX FM (motor de audio + API en el puerto 8001).
REM Doble clic desde la carpeta del proyecto.
cd /d "%~dp0"
powershell -NoExit -ExecutionPolicy Bypass -File scripts\iniciar_onix.ps1 -Puerto 8001
