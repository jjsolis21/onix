# restaurar_respaldo.ps1 — Vuelve la base de datos de ONIX a una copia de respaldo.
#
# Uso (desde la carpeta de ONIX):
#   powershell -ExecutionPolicy Bypass -File scripts\restaurar_respaldo.ps1
#       → muestra las copias y pregunta cuál restaurar
#   powershell -ExecutionPolicy Bypass -File scripts\restaurar_respaldo.ps1 -Archivo onix-2026-10-12_0300.db
#
# CORTA EL AIRE: cierra el motor y la API, reemplaza la base (guardando la
# actual como radio_core.antes-de-restaurar-*.db) y vuelve a arrancar ONIX.

param(
    [string]$Archivo = "",
    [int]$Puerto = 8001,
    [int]$PuertoMotor = 8766
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

python scripts\restaurar_respaldo.py
if (-not $Archivo) {
    $Archivo = Read-Host "Escribe el nombre de la copia a restaurar (Enter = cancelar)"
    if (-not $Archivo) { Write-Host "Cancelado."; exit 0 }
}
$ok = Read-Host "Esto CORTA EL AIRE unos segundos y reemplaza la base actual. ¿Seguir? (s/n)"
if ($ok -notin @("s", "S", "si", "SI", "sí", "SÍ")) { Write-Host "Cancelado."; exit 0 }

# Que el inicio automático no lo vuelva a abrir a mitad de la restauración
$tarea = Get-ScheduledTask -TaskName "ONIX FM" -ErrorAction SilentlyContinue
if ($tarea) { Disable-ScheduledTask -TaskName "ONIX FM" | Out-Null }

foreach ($p in @($Puerto, $PuertoMotor)) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        Write-Host "Cerrando ONIX (PID $($c.OwningProcess), puerto $p)..." -ForegroundColor Yellow
        Stop-Process -Id $c.OwningProcess -Force
    }
}
Start-Sleep -Seconds 2

python scripts\restaurar_respaldo.py $Archivo
$resultado = $LASTEXITCODE

if ($tarea) { Enable-ScheduledTask -TaskName "ONIX FM" | Out-Null }

if ($resultado -ne 0) {
    Write-Host "La base NO se cambió. Se vuelve a arrancar ONIX con la base que tenía." -ForegroundColor Red
}
& (Join-Path $raiz "scripts\iniciar_onix.ps1") -Fondo -Puerto $Puerto -PuertoMotor $PuertoMotor
Write-Host "ONIX arrancado (minimizado). Abre http://localhost:$Puerto/" -ForegroundColor Green
