@echo off
REM Abre ONIX a las otras PCs de la red local (no a internet). Pide permisos de administrador.
cd /d "%~dp0"
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoExit -ExecutionPolicy Bypass -File \"%~dp0scripts\abrir_red_local.ps1\"'"
