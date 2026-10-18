"""
respaldos.py — Ónix FM
Respaldo automático de la base de datos (relojes, grilla, eventos, pautas,
biblioteca con sus marcas, cartuchera, historial y configuración).

    GET  /api/v1/respaldos              → carpeta, ajustes y lista de copias
    POST /api/v1/respaldos              → respaldar ahora
    PUT  /api/v1/respaldos/config       → {"carpeta": "...", "conservar": 14}
    GET  /api/v1/respaldos/{archivo}    → descargar una copia

CÓMO
    · Vuelca la base MariaDB (backend/respaldo_maria.py) con una foto consistente
      aunque el motor esté escribiendo el historial en ese momento. La copia se
      verifica (cierre y conteos) antes de aceptarla.
    · Una copia al arrancar si la última tiene más de 23 h, y luego una por
      día (se revisa cada hora). Se conservan las `respaldo_conservar` más
      nuevas (14 por defecto) y se borran las más viejas.
    · Archivo: onix-AAAA-MM-DD_HHMM.sql.gz  (las copias viejas .db de SQLite se
      siguen listando, pero ya no se pueden restaurar en MariaDB).

LO QUE NO RESPALDA: los audios (media/). Son archivos grandes que hay que
copiar aparte. Restaurar: scripts\\restaurar_respaldo.ps1 (ver README).
"""

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.paths import DB_PATH, RESPALDOS_BD_DIR

log = logging.getLogger("onix.respaldos")
router = APIRouter(tags=["Respaldos"])

PATRON = re.compile(r"^onix-\d{4}-\d{2}-\d{2}_\d{4}(-\d+)?\.(sql\.gz|db)$")
EDAD_MAX_H = 23          # más vieja que esto: toca respaldar
CONSERVAR = 14
_lock = threading.Lock()
estado = {"ultimo_error": None}


def _cfg(conn, clave, defecto=""):
    r = conn.execute("SELECT valor FROM config WHERE clave = ?", (clave,)).fetchone()
    return r[0] if r and r[0] not in (None, "") else defecto


def ajustes() -> dict:
    from backend.database import get_connection
    conn = get_connection()
    try:
        carpeta = _cfg(conn, "respaldo_carpeta", "")
        try:
            conservar = max(1, int(float(_cfg(conn, "respaldo_conservar", CONSERVAR))))
        except ValueError:
            conservar = CONSERVAR
    finally:
        conn.close()
    return {"carpeta": Path(carpeta) if carpeta else RESPALDOS_BD_DIR,
            "personalizada": bool(carpeta), "conservar": conservar}


def listar(carpeta: Optional[Path] = None) -> list[dict]:
    carpeta = carpeta or ajustes()["carpeta"]
    try:
        archivos = [p for p in carpeta.iterdir() if p.is_file() and PATRON.match(p.name)]
    except OSError:
        return []
    archivos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"archivo": p.name, "tamano": p.stat().st_size,
             "fecha": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p in archivos]


def crear(motivo: str = "manual") -> dict:
    """Hace una copia ahora. Lanza OSError/ValueError/pymysql.Error si no se puede."""
    from backend.respaldo_maria import verificar, volcar
    with _lock:
        a = ajustes()
        carpeta: Path = a["carpeta"]
        carpeta.mkdir(parents=True, exist_ok=True)
        base = datetime.now().strftime("onix-%Y-%m-%d_%H%M")
        destino, n = carpeta / f"{base}.sql.gz", 2
        while destino.exists():
            destino, n = carpeta / f"{base}-{n}.sql.gz", n + 1
        temporal = destino.with_name(destino.name + ".tmp")
        try:
            volcar(temporal)
            verificar(temporal)            # cierre y conteos: una copia incompleta no se acepta
        except Exception:
            temporal.unlink(missing_ok=True)
            raise
        temporal.replace(destino)          # nunca queda una copia a medias con nombre válido
        for viejo in listar(carpeta)[a["conservar"]:]:
            try:
                (carpeta / viejo["archivo"]).unlink()
            except OSError:
                pass
        estado["ultimo_error"] = None
        log.info("[Respaldos] Copia %s (%s) en %s", destino.name, motivo, carpeta)
        return {"archivo": destino.name, "carpeta": str(carpeta), "tamano": destino.stat().st_size}


def edad_ultimo_h() -> Optional[float]:
    copias = listar()
    if not copias:
        return None
    return (datetime.now() - datetime.fromisoformat(copias[0]["fecha"])).total_seconds() / 3600


def revisar_y_respaldar():
    """Si la última copia es vieja (o no hay), hace una. Nunca lanza."""
    try:
        edad = edad_ultimo_h()
        if edad is None or edad >= EDAD_MAX_H:
            crear("automático")
    except Exception as exc:
        estado["ultimo_error"] = f"{type(exc).__name__}: {exc}"
        log.error("[Respaldos] No se pudo respaldar: %s", exc)


class RespaldoWorker:
    """Hilo que respalda al arrancar y luego una vez por día."""
    INTERVALO = 3600.0

    def __init__(self):
        self._parar = threading.Event()
        self._hilo: Optional[threading.Thread] = None

    def start(self):
        self._hilo = threading.Thread(target=self._run, name="Respaldos", daemon=True)
        self._hilo.start()

    def stop(self):
        self._parar.set()

    def _run(self):
        if self._parar.wait(30):           # deja arrancar al motor primero
            return
        while not self._parar.is_set():
            revisar_y_respaldar()
            self._parar.wait(self.INTERVALO)


# ── Endpoints ───────────────────────────────────────────────────────────────

class RespaldoConfig(BaseModel):
    carpeta:   Optional[str] = Field(None, max_length=400,
                                     description="Carpeta absoluta ('' = la de ONIX)")
    conservar: Optional[int] = Field(None, ge=1, le=365)


@router.get("/api/v1/respaldos", summary="Copias de la base de datos")
def ver():
    a = ajustes()
    copias = listar(a["carpeta"])
    return {"carpeta": str(a["carpeta"]), "personalizada": a["personalizada"],
            "conservar": a["conservar"], "copias": copias,
            "mismo_disco": mismo_disco(a["carpeta"]),
            "ultimo_error": estado["ultimo_error"]}


@router.post("/api/v1/respaldos", status_code=201, summary="Respaldar ahora")
def respaldar_ahora():
    try:
        return crear("manual")
    except Exception as exc:
        estado["ultimo_error"] = f"{type(exc).__name__}: {exc}"
        raise HTTPException(status_code=500, detail=f"No se pudo respaldar: {exc}")


@router.put("/api/v1/respaldos/config", summary="Carpeta y cantidad de copias")
def configurar(body: RespaldoConfig):
    from backend.database import get_connection
    conn = get_connection()
    try:
        if body.carpeta is not None:
            carpeta = body.carpeta.strip()
            if carpeta:
                p = Path(carpeta)
                if not p.is_absolute():
                    raise HTTPException(status_code=422,
                                        detail="Escribe la ruta completa, p. ej. D:\\Respaldos ONIX")
                try:
                    p.mkdir(parents=True, exist_ok=True)
                    prueba = p / ".onix-prueba"
                    prueba.write_text("ok")
                    prueba.unlink()
                except OSError as exc:
                    raise HTTPException(status_code=422, detail=f"No se puede escribir en esa carpeta: {exc}")
            conn.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('respaldo_carpeta', ?)", (carpeta,))
        if body.conservar is not None:
            conn.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('respaldo_conservar', ?)",
                         (str(body.conservar),))
        conn.commit()
    finally:
        conn.close()
    return ver()


@router.get("/api/v1/respaldos/{archivo}", summary="Descargar una copia")
def descargar(archivo: str):
    if not PATRON.match(archivo):
        raise HTTPException(status_code=404, detail="Copia no encontrada")
    ruta = ajustes()["carpeta"] / archivo
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Copia no encontrada")
    return FileResponse(ruta, filename=archivo, media_type="application/octet-stream")


def mismo_disco(carpeta: Path) -> bool:
    """¿La copia está en el mismo disco que la base? (no protege si el disco muere)."""
    try:
        from backend.database import DB_HOST, get_connection
        if DB_HOST not in ("localhost", "127.0.0.1", "::1"):
            return False                                  # MariaDB corre en otro equipo: cualquier copia local sí la protege
        conn = get_connection()
        try:
            datadir = conn.execute("SELECT @@datadir").fetchone()[0]
        finally:
            conn.close()
        a, b = Path(datadir).resolve(), Path(carpeta).resolve()
    except Exception:
        return True
    if a.drive or b.drive:                 # Windows: letra de unidad
        return a.drive.upper() == b.drive.upper()
    try:                                   # Linux/macOS: mismo dispositivo
        b.mkdir(parents=True, exist_ok=True)
        return a.parent.stat().st_dev == b.stat().st_dev
    except OSError:
        return True

