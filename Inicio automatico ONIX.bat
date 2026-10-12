@echo off
REM Instala el inicio automatico de ONIX con Windows (y que se vuelva a abrir si se cierra).
REM Para quitarlo: powershell -ExecutionPolicy Bypass -File scripts\inicio_automatico.ps1 -Quitar
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File scripts\inicio_automatico.ps1 -Instalar
pause
