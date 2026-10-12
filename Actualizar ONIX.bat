@echo off
REM Baja los cambios nuevos (git pull) y reinicia ONIX con el codigo nuevo.
REM ATENCION: reinicia tambien el motor de audio (corta el aire unos segundos).
cd /d "%~dp0"
git pull
if errorlevel 1 (
  echo.
  echo No se pudieron bajar los cambios. Revisa el mensaje de arriba.
  pause
  exit /b 1
)
pip install -q -r requirements.txt
powershell -NoExit -ExecutionPolicy Bypass -File scripts\iniciar_onix.ps1 -Puerto 8001 -ReiniciarMotor
