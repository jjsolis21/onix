"""
red.py — Ónix FM
Acceso desde otras PCs de la red local (p. ej. el jefe de programación).

    GET /api/v1/red → direcciones con las que se entra a ONIX desde la red

Para que otra PC pueda entrar, el Firewall de Windows de la PC del estudio
debe dejar pasar el puerto: «Abrir ONIX en la red local.bat»
(scripts/abrir_red_local.ps1), que lo abre SOLO para la red local.
"""

import os
import socket

from fastapi import APIRouter

router = APIRouter(tags=["Red"])


def direcciones_locales() -> list[str]:
    """IPv4 de esta PC en la red local (sin 127.x)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        # Truco habitual: "conectar" un socket UDP no envía nada, pero
        # revela la IP de la tarjeta que sale a la red.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    privadas = [ip for ip in ips if not ip.startswith("127.")]
    # Primero las privadas típicas de una oficina (192.168.x, 10.x, 172.16-31.x)
    return sorted(privadas, key=lambda ip: (not ip.startswith(("192.168.", "10.", "172.")), ip))


@router.get("/api/v1/red", summary="Direcciones para entrar desde la red local")
def red():
    puerto = int(os.getenv("ONIX_PORT", "8000"))
    ips = direcciones_locales()
    return {"equipo": socket.gethostname(), "puerto": puerto, "ips": ips,
            "admin": [f"http://{ip}:{puerto}/admin/" for ip in ips],
            "studio": [f"http://{ip}:{puerto}/" for ip in ips]}
