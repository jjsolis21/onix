# iniciar_onix.ps1 — Arranca ONIX FM con el motor de audio en un proceso aparte.
#
# El motor queda en su propia ventana (minimizada). Cerrar o reiniciar la API
# NO corta el aire; la API se reconecta sola al motor.
#
# Uso (desde la carpeta del proyecto):
#   powershell -ExecutionPolicy Bypass -File scripts\iniciar_onix.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\iniciar_onix.ps1 -Puerto 8001
#
# Para reiniciar solo la API: Ctrl+C en esta ventana y volver a ejecutar el
# script (si el motor ya está corriendo, el segundo motor se cierra solo y
# la API se conecta al que ya suena).

param(
    [int]$Puerto = 8001,
    [int]$PuertoMotor = 8766
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$env:ONIX_PORT        = "$Puerto"
$env:ONIX_ENGINE      = "remoto"
$env:ONIX_ENGINE_PORT = "$PuertoMotor"

$motorActivo = Test-NetConnection -ComputerName 127.0.0.1 -Port $PuertoMotor `
    -InformationLevel Quiet -WarningAction SilentlyContinue
if ($motorActivo) {
    Write-Host "Motor de audio ya en marcha en el puerto $PuertoMotor."
} else {
    Write-Host "Arrancando motor de audio (puerto $PuertoMotor)..."
    Start-Process -FilePath "python" -ArgumentList "-m", "backend.audio.engine_service" `
        -WorkingDirectory $raiz -WindowStyle Minimized
    Start-Sleep -Seconds 2
}

Write-Host "Arrancando API en http://localhost:$Puerto  (Admin: http://localhost:$Puerto/admin/)"
python backend\api\api_server.py
