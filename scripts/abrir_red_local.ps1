# abrir_red_local.ps1 — Deja entrar a ONIX desde otras PCs de la RED LOCAL.
#
# Crea una regla del Firewall de Windows que permite el puerto de ONIX solo
# desde la subred local (no desde internet) y en redes Privadas o de Dominio.
# Necesita permisos de administrador (el .bat los pide solo).
#
#   powershell -ExecutionPolicy Bypass -File scripts\abrir_red_local.ps1            (abrir)
#   powershell -ExecutionPolicy Bypass -File scripts\abrir_red_local.ps1 -Cerrar    (quitar la regla)

param(
    [int]$Puerto = 8001,
    [switch]$Cerrar
)

$ErrorActionPreference = "Stop"
$nombre = "ONIX FM (red local)"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Hace falta ejecutarlo como administrador (clic derecho → Ejecutar como administrador)." -ForegroundColor Red
    exit 1
}

Get-NetFirewallRule -DisplayName $nombre -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Cerrar) {
    Write-Host "Regla quitada: ONIX ya no acepta conexiones de otras PCs." -ForegroundColor Green
    exit 0
}

New-NetFirewallRule -DisplayName $nombre -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort $Puerto -RemoteAddress LocalSubnet -Profile Private, Domain `
    -Description "Admin y Studio de ONIX FM desde la red de la emisora (no desde internet)." | Out-Null
Write-Host "Listo: el puerto $Puerto queda abierto SOLO para la red local." -ForegroundColor Green

# La regla vale en redes Privadas o de Dominio. Si Windows marcó la red como
# Pública (pasa a menudo), la otra PC no podrá entrar.
$perfiles = Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -ne "Disconnected" }
foreach ($p in $perfiles) {
    if ($p.NetworkCategory -eq "Public") {
        Write-Host ""
        Write-Host "La red '$($p.Name)' está marcada como PÚBLICA: Windows no dejará entrar a la otra PC." -ForegroundColor Yellow
        $r = Read-Host "¿Cambiarla a Privada? Hazlo solo si es la red de la emisora (s/n)"
        if ($r -in @("s", "S", "si", "sí")) {
            Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
            Write-Host "Red '$($p.Name)' ahora es Privada." -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "Desde la otra PC, escribe en el navegador:" -ForegroundColor Cyan
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    ForEach-Object { Write-Host "  http://$($_.IPAddress):$Puerto/admin/" }
