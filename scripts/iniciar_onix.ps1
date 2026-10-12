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
#
# -Fondo: arranca solo lo que falte (motor y/o API), los dos minimizados, y
# termina. Lo usa el inicio automático con Windows (scripts\inicio_automatico.ps1)
# cada 5 minutos: si ONIX ya está en marcha no hace nada; si algo se cerró, lo
# vuelve a abrir y lo anota en logs\inicio.log.

param(
    [int]$Puerto = 8001,
    [int]$PuertoMotor = 8766,
    [switch]$ReiniciarMotor,   # cierra el motor y el servidor anteriores para cargar el código nuevo
    [switch]$Fondo,            # arranca lo que falte, minimizado, y termina (inicio automático)
    [string]$Python = "python" # ruta completa de python.exe (el inicio automático la fija)
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

function Anotar([string]$texto) {
    # Registro del inicio automático: saber si ONIX se cerró y se volvió a abrir solo
    $carpeta = Join-Path $raiz "logs"
    New-Item -ItemType Directory -Force -Path $carpeta | Out-Null
    $linea = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $texto
    Add-Content -Path (Join-Path $carpeta "inicio.log") -Value $linea -Encoding UTF8
    Write-Host $texto
}

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
    if ($Fondo) { Anotar "El motor de audio no estaba en marcha: se arranca." }
    else { Write-Host "Arrancando motor de audio (puerto $PuertoMotor)..." }
    Start-Process -FilePath $Python -ArgumentList "-m", "backend.audio.engine_service" `
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
if ($ocupado -and $Fondo) {
    exit 0                     # la API ya está en marcha: nada que hacer
}
if ($ocupado) {
    $proc = Get-Process -Id $ocupado.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "El puerto $Puerto ya lo usa '$($proc.ProcessName)' (PID $($ocupado.OwningProcess))." -ForegroundColor Red
    Write-Host "Si es un ONIX anterior (python), ciérralo:  Stop-Process -Id $($ocupado.OwningProcess) -Force" -ForegroundColor Yellow
    Write-Host "Si es otro programa, usa otro puerto:        -Puerto 8002" -ForegroundColor Yellow
    exit 1
}

if ($Fondo) {
    Anotar "La API no estaba en marcha: se arranca (puerto $Puerto)."
    Start-Process -FilePath $Python -ArgumentList "backend\api\api_server.py" `
        -WorkingDirectory $raiz -WindowStyle Minimized
    exit 0
}

Write-Host "Arrancando API en http://localhost:$Puerto  (Admin: http://localhost:$Puerto/admin/)"
& $Python backend\api\api_server.py
