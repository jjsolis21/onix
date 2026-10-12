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
#
# Después de un "git pull" hay que reiniciar TAMBIÉN el motor (corta el aire):
#   powershell -ExecutionPolicy Bypass -File scripts\iniciar_onix.ps1 -ReiniciarMotor

param(
    [int]$Puerto = 8001,
    [int]$PuertoMotor = 8766,
    [switch]$ReiniciarMotor    # cierra el motor y el servidor anteriores para cargar el código nuevo
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$env:ONIX_PORT        = "$Puerto"
$env:ONIX_ENGINE      = "remoto"
$env:ONIX_ENGINE_PORT = "$PuertoMotor"

if ($ReiniciarMotor) {
    $viejo = Get-NetTCPConnection -LocalPort $PuertoMotor -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($viejo) {
        Write-Host "Cerrando el motor anterior (PID $($viejo.OwningProcess)) para cargar el código nuevo..." -ForegroundColor Yellow
        Stop-Process -Id $viejo.OwningProcess -Force
        Start-Sleep -Seconds 2
    }
}

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

# ¿El puerto de la API ya está ocupado? (típico: un ONIX anterior que sigue abierto)
$ocupado = Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ocupado -and $ReiniciarMotor) {
    # Al actualizar hay que cerrar TAMBIÉN el servidor viejo: si sigue abierto,
    # el Studio carga las páginas nuevas pero habla con el código anterior.
    $proc = Get-Process -Id $ocupado.OwningProcess -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -like "python*") {
        Write-Host "Cerrando el servidor ONIX anterior (PID $($ocupado.OwningProcess))..." -ForegroundColor Yellow
        Stop-Process -Id $ocupado.OwningProcess -Force
        Start-Sleep -Seconds 2
        $ocupado = $null
    }
}
if ($ocupado) {
    $proc = Get-Process -Id $ocupado.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "El puerto $Puerto ya lo usa '$($proc.ProcessName)' (PID $($ocupado.OwningProcess))." -ForegroundColor Red
    Write-Host "Si es un ONIX anterior (python), ciérralo:  Stop-Process -Id $($ocupado.OwningProcess) -Force" -ForegroundColor Yellow
    Write-Host "Si es otro programa, usa otro puerto:        -Puerto 8002" -ForegroundColor Yellow
    exit 1
}

Write-Host "Arrancando API en http://localhost:$Puerto  (Admin: http://localhost:$Puerto/admin/)"
python backend\api\api_server.py
