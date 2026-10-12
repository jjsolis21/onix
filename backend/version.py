"""
version.py — Ónix FM
Versión del código. La API y el motor (que puede correr como proceso aparte)
la informan en /api/v1/status: si no coinciden, el motor quedó con código
viejo y hay que reiniciarlo (scripts/iniciar_onix.ps1 -ReiniciarMotor).

Subirla en cada cambio del motor que la API o el Studio necesiten.
"""

VERSION = "2026.10.12.4"
