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

# ── Base de datos de las pruebas ────────────────────────────────────────────────────────────────────────────
# NUNCA la base real (onix_db). Por defecto las pruebas usan la base de PRUEBAS de MariaDB (`onix_test`, el mismo motor que
# producción).
# Con pytest-xdist cada worker usa SU base (onix_test_gw0, onix_test_gw1…) para no pisarse.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
os.environ["DB_NAME"] = "onix_test" + (f"_{_WORKER}" if _WORKER else "")   # aunque el .env diga onix_db, las pruebas jamás la usan
assert os.environ["DB_NAME"] != "onix_db"


def _asegurar_base_de_worker() -> None:
    """Crea la base del worker (si no existe) copiando el esquema de `onix_test`."""
    if not _WORKER:
        return
    import pymysql
    from backend.database import DB_HOST, DB_PORT, DB_PASSWORD, DB_USER
    base_madre, base = "onix_test", os.environ["DB_NAME"]
    kw = dict(host=DB_HOST, port=int(DB_PORT), user=DB_USER, password=DB_PASSWORD, charset="utf8mb4", autocommit=True)
    c = pymysql.connect(**kw)
    try:
        cur = c.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{base}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'", (base,))
        existentes = {r[0] for r in cur.fetchall()}
        cur.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'", (base_madre,))
        tablas = [r[0] for r in cur.fetchall()]
        if set(tablas) <= existentes:
            return
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for t in tablas:
            if t in existentes:
                continue
            cur.execute(f"SHOW CREATE TABLE `{base_madre}`.`{t}`")
            ddl = cur.fetchone()[1]
            cur.execute(f"USE `{base}`")
            cur.execute(ddl)
            cur.execute(f"USE `{base_madre}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    finally:
        c.close()


_asegurar_base_de_worker()
# El TestClient cuenta como "la PC del estudio" (entra sin contraseña).
# Las pruebas de acceso remoto usan TestClient(client=("192.168.1.50", …)).
os.environ["ONIX_HOSTS_LOCALES"] = "testclient"

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
    def _muestras(n: int) -> bytes:
        return b"".join(struct.pack("<h", int(peak * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))

    if frames > rate * 10 and float(freq).is_integer():
        # Con frecuencia entera la onda se repite exacta cada segundo: se calcula uno y se repite (los de 600 s tardaban 9 s cada uno)
        bloque = _muestras(rate)
        data = bloque * (frames // rate) + bloque[: 2 * (frames % rate)]
    else:
        data = _muestras(frames)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return path


@pytest.fixture(autouse=True)
def _sin_internet_para_los_datos_del_locutor(monkeypatch):
    """Ninguna prueba sale a Wikipedia ni a MusicBrainz: las que prueban los datos traen su propia fuente falsa."""
    from backend.locutor import datos

    def sin_red(url):
        raise OSError("las pruebas no salen a internet")
    monkeypatch.setattr(datos, "_get_json", sin_red)


def _tablas(c) -> list[str]:
    return [list(r.values())[0] for r in c.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'").fetchall()]


_SEMILLA: dict = {}


def _semilla_de_fabrica(existentes: set) -> dict:
    """Filas con las que nace una instalación nueva (categorías, secciones de tanda, config…), leídas de init_db."""
    import sqlite3
    from init_db import initialize_database
    ruta = _TMP / "semilla.db"
    if not ruta.exists():
        initialize_database(str(ruta))
    lit = sqlite3.connect(str(ruta))
    lit.row_factory = sqlite3.Row
    semilla = {}
    try:
        for t in lit.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            t = t[0]
            if t not in existentes:
                continue
            filas = [dict(f) for f in lit.execute(f"SELECT * FROM `{t}`").fetchall()]
            if filas:
                semilla[t] = filas
    finally:
        lit.close()
    return semilla


def _cerrar_conexiones_ajenas(c) -> None:
    """KILL de todas las demás conexiones a esta base de pruebas (la propia no se toca)."""
    for r in c.execute("SELECT id FROM information_schema.processlist WHERE db = DATABASE() AND id <> CONNECTION_ID()").fetchall():
        try:
            c.execute(f"KILL {int(list(r.values())[0] if hasattr(r, 'values') else r[0])}")
        except Exception:
            pass


def resetear_base() -> None:
    """Deja `onix_test` como recién creada: sin datos de prueba y con las filas semilla (categorías, secciones, config…)."""
    from backend.database import get_connection
    c = get_connection()
    try:
        # Una prueba anterior (o un hilo de fondo de su servidor: normalizador, Locutor, respaldos) puede tener una conexión con
        # transacción abierta: el TRUNCATE esperaría ese bloqueo y la prueba fallaría. Se cierran las conexiones ajenas de ESTA
        # base de pruebas antes de limpiar y, si aparece un bloqueo nuevo, se vuelve a intentar.
        c.execute("SET SESSION lock_wait_timeout = 5")
        _cerrar_conexiones_ajenas(c)
        if not _SEMILLA:                                   # las filas de fábrica salen de una base nueva creada por init_db
            _SEMILLA.update(_semilla_de_fabrica(set(_tablas(c))))
        c.execute("SET FOREIGN_KEY_CHECKS = 0")
        # Una tabla vacía pero con el contador de ids avanzado (la prueba insertó y borró filas) también se reinicia:
        # algunas pruebas dependen del id («evento #1»).
        avanzadas = {r[0] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND auto_increment > 1").fetchall()}
        for t in _tablas(c):
            if t in avanzadas or t in _SEMILLA or c.execute(f"SELECT 1 FROM `{t}` LIMIT 1").fetchone() is not None:
                for intento in range(6):
                    try:
                        c.execute(f"TRUNCATE TABLE `{t}`")          # también reinicia los AUTO_INCREMENT
                        break
                    except Exception as exc:
                        if intento == 5 or not any(str(k) in str(exc) for k in ("1205", "1213")):
                            raise
                        _cerrar_conexiones_ajenas(c)                 # apareció otra conexión con bloqueos: se cierra y se reintenta
        for t, filas in _SEMILLA.items():
            cols = list(filas[0].keys())
            c.executemany(f"INSERT INTO `{t}` ({', '.join('`' + k + '`' for k in cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                          [tuple(f[k] for k in cols) for f in filas])
        c.execute("SET FOREIGN_KEY_CHECKS = 1")
        c.commit()
    finally:
        c.close()


@pytest.fixture(scope="session", autouse=True)
def _base_limpia_al_empezar():
    """Cada corrida parte de `onix_test` sin restos de la anterior (las pruebas de la API comparten esa base)."""
    from backend.migraciones_maria import migrar
    migrar()                                               # la base de pruebas siempre con el esquema al día
    resetear_base()
    yield


@pytest.fixture(scope="module", autouse=True)
def _base_limpia_por_archivo():
    """Cada archivo de pruebas empieza con la base limpia: antes heredaba lo que dejó el archivo anterior del mismo worker."""
    resetear_base()
    yield


@pytest.fixture
def tmp_db(tmp_path):
    """Base de datos limpia para esta prueba: se reinicia la base de pruebas de MariaDB."""
    resetear_base()
    return tmp_path / "radio.db"                           # la ruta ya no se usa (queda por compatibilidad con las pruebas)
