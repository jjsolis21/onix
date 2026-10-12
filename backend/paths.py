"""
paths.py — Ónix FM
Única fuente de verdad para las rutas del sistema (base de datos y medios).
La usan api_server.py y audio_engine.py para que nunca apunten a sitios distintos.

RUTAS DE AUDIO EN LA BD (audios.archivo_path):
    Se guardan RELATIVAS a MEDIA_ROOT y con "/" (ej: "library/KAROL G/tema.mp3").
    Así la misma base funciona en cualquier PC o dentro de Docker: basta con
    que MEDIA_ROOT apunte a la carpeta donde están los audios.
"""

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.getenv("RADIO_DB_PATH", str(ROOT_DIR / "backend" / "api" / "radio_core.db")))

# Por defecto la carpeta media/ del proyecto. RADIO_MEDIA_ROOT la sobreescribe.
MEDIA_ROOT = Path(os.getenv("RADIO_MEDIA_ROOT", str(ROOT_DIR / "media"))).resolve()

# Lista de respaldo: audios que el motor pone al aire si se queda sin nada
# que sonar (biblioteca vacía, BD caída, todos los archivos fallan).
# Se leen directamente de la carpeta, sin pasar por la BD.
RESPALDO_DIR = MEDIA_ROOT / "respaldo"

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma")

# Prefijo absoluto que usaban las versiones anteriores al guardar rutas
_LEGACY_PREFIX = "/media/onix/"


def to_stored(path) -> str:
    """Convierte una ruta de disco al formato que se guarda en la BD."""
    p = Path(path).resolve()
    try:
        return p.relative_to(MEDIA_ROOT).as_posix()
    except ValueError:
        return p.as_posix()   # fuera de MEDIA_ROOT: se conserva absoluta


def normalize_stored(stored: str) -> str:
    """
    Lleva una ruta guardada (posiblemente antigua) al formato actual:
    quita el prefijo absoluto /media/onix/ o el de MEDIA_ROOT y unifica "/".
    """
    s = (stored or "").replace("\\", "/")
    idx = s.lower().find(_LEGACY_PREFIX)
    if idx != -1:
        return s[idx + len(_LEGACY_PREFIX):]
    root = MEDIA_ROOT.as_posix().rstrip("/") + "/"
    if s.lower().startswith(root.lower()):
        return s[len(root):]
    return s


def resolve_media(stored: str) -> Path:
    """Ruta real en disco de un archivo_path guardado en la BD."""
    s = normalize_stored(stored)
    p = Path(s)
    if p.is_absolute() or p.anchor:
        return p
    return MEDIA_ROOT / s
