"""
Configuración común de las pruebas de Ónix FM.

Las variables de entorno se fijan ANTES de importar el proyecto: backend/paths.py
lee RADIO_DB_PATH / RADIO_MEDIA_ROOT al importarse y el motor elige el backend
de audio (mock = sin sonido) también al importarse.

Ejecutar desde la raíz del proyecto:
    python -m pytest
"""

import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="onix-tests-"))

os.environ["RADIO_DB_PATH"]      = str(_TMP / "api.db")
os.environ["RADIO_MEDIA_ROOT"]   = str(_TMP / "media")
os.environ["RADIO_BACKUP_DIR"]   = str(_TMP / "respaldos")
os.environ["ONIX_AUDIO_BACKEND"] = "mock"

for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402


def make_wav(path: Path, seconds: float = 2.0, amplitude: float = 0.5,
             freq: float = 440.0, rate: int = 22050) -> Path:
    """Escribe un WAV mono de 16 bits con un tono senoidal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    peak = int(32767 * amplitude)
    data = b"".join(
        struct.pack("<h", int(peak * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(frames)
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return path


@pytest.fixture
def tmp_db(tmp_path):
    """Base de datos nueva y migrada, aislada por prueba."""
    from init_db import initialize_database
    db = tmp_path / "radio.db"
    initialize_database(str(db))
    return db
