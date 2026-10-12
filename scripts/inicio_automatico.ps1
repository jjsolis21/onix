# inicio_automatico.ps1 — ONIX FM arranca solo con Windows y se vuelve a abrir si se cierra.
#
# Crea la tarea "ONIX FM" en el Programador de tareas de Windows, para el
# usuario actual:
#   · al iniciar sesión, y luego cada 5 minutos, ejecuta
#     iniciar_onix.ps1 -Fondo  → arranca SOLO lo que falte (motor y/o API).
#   · Si ONIX ya está en marcha no hace nada. Si el motor o la API se
#     cerraron (error, alguien cerró la ventana), vuelven en 5 minutos como
#     mucho. Queda anotado en logs\inicio.log.
#   · El modo (manual/automático) se recuerda: si estaba en automático,
#     vuelve sonando en automático.
#
# Uso (desde la carpeta de ONIX):
#   powershell -ExecutionPolicy Bypass -File scripts\inicio_automatico.ps1 -Instalar
#   powershell -ExecutionPolicy Bypass -File scripts\inicio_automatico.ps1 -Quitar
#   powershell -ExecutionPolicy Bypass -File scripts\inicio_automatico.ps1          (ver estado)
#
# IMPORTANTE: la tarea corre al INICIAR SESIÓN (el audio de Windows solo existe
# dentro de una sesión de usuario). Para que la PC vuelva al aire sola después
# de un corte de luz o una actualización, Windows debe iniciar sesión solo:
# ver las recomendaciones que imprime este script.

param(
    [switch]$Instalar,
    [switch]$Quitar,
    [int]$Puerto = 8001,
    [int]$PuertoMotor = 8766
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$nombre = "ONIX FM"
$lanzador = Join-Path $raiz "scripts\iniciar_onix.ps1"

function Estado {
    $t = Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Host "Inicio automático: NO instalado." -ForegroundColor Yellow
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $nombre
    Write-Host "Inicio automático: INSTALADO ($($t.State))." -ForegroundColor Green
    Write-Host "  Última ejecución: $($info.LastRunTime)  ·  resultado: $($info.LastTaskResult)"
    Write-Host "  Próxima revisión: $($info.NextRunTime)"
    $log = Join-Path $raiz "logs\inicio.log"
    if (Test-Path $log) {
        Write-Host "  Últimos reinicios automáticos (logs\inicio.log):"
        Get-Content $log -Tail 5 | ForEach-Object { Write-Host "    $_" }
    }
}

function Recomendaciones {
    Write-Host ""
    Write-Host "Para que la PC vuelva al aire SOLA tras un corte de luz o un reinicio:" -ForegroundColor Cyan
    Write-Host "  1. BIOS/UEFI: 'Restore on AC power loss' = Power On (enciende al volver la luz)."
    Write-Host "  2. Inicio de sesión automático de Windows: Win+R → netplwiz → desmarcar"
    Write-Host "     'Los usuarios deben escribir su nombre y contraseña' (o la herramienta Autologon de Microsoft)."
    Write-Host "  3. Que nunca se suspenda: powercfg /change standby-timeout-ac 0"
    Write-Host "                           powercfg /change hibernate-timeout-ac 0"
    Write-Host "  4. Windows Update: fijar 'horas activas' para que no reinicie en horario de emisión."
    Write-Host "Mientras Windows pida contraseña al arrancar, ONIX NO arrancará hasta que alguien inicie sesión." -ForegroundColor Yellow
}

if ($Quitar) {
    if (Get-ScheduledTask -TaskName $nombre -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $nombre -Confirm:$false
        Write-Host "Inicio automático quitado. ONIX ya no arranca solo ni se vuelve a abrir." -ForegroundColor Green
    } else {
        Write-Host "El inicio automático no estaba instalado."
    }
    exit 0
}

if (-not $Instalar) {
    Estado
    Recomendaciones
    exit 0
}

# ── Instalar ────────────────────────────────────────────────────────────────
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "No encuentro python. Instálalo (o abre esta ventana donde 'python --version' funcione)." -ForegroundColor Red
    exit 1
}
$python = $py.Source
if ($python -like "*WindowsApps*") {
    Write-Host "Ojo: 'python' apunta al acceso directo de la Microsoft Store ($python)." -ForegroundColor Yellow
    Write-Host "Si ONIX no arranca solo, instala Python desde python.org y vuelve a ejecutar esto." -ForegroundColor Yellow
}

$argumentos = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$lanzador`" -Fondo " +
              "-Puerto $Puerto -PuertoMotor $PuertoMotor -Python `"$python`""
$accion = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argumentos -WorkingDirectory $raiz

$usuario = "$env:USERDOMAIN\$env:USERNAME"
$alIniciar = New-ScheduledTaskTrigger -AtLogOn -User $usuario
# Revisión cada 5 minutos (durante 10 años: "para siempre" en la práctica)
$cada5 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)

$ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger @($alIniciar, $cada5) `
        -Settings $ajustes -Principal $principal -Force `
        -Description "Arranca ONIX FM al iniciar sesión y lo vuelve a abrir si se cierra (revisa cada 5 min)." | Out-Null
} catch {
    Write-Host "Windows no dejó crear la tarea: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Prueba otra vez con permisos: clic derecho en 'Inicio automatico ONIX.bat' → Ejecutar como administrador." -ForegroundColor Yellow
    exit 1
}

Write-Host "Inicio automático INSTALADO para $usuario." -ForegroundColor Green
Write-Host "  Python: $python"
Write-Host "  ONIX arrancará al iniciar sesión y se revisará cada 5 minutos."
Write-Host "  Para mantenimiento (que no se vuelva a abrir solo): ejecuta este script con -Quitar."
Start-ScheduledTask -TaskName $nombre
Write-Host "  Primera revisión lanzada ahora."
Recomendaciones
