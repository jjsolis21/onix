"""
cartuchera.py — Ónix FM
Botones de la cartuchera como Soho: cargar un efecto desde cualquier archivo y editar cada botón
(título, color, marca in / out y volumen). Las paletas y la asignación por arrastre viven en api_server.

    PATCH  /api/v1/cartuchera/{banco}/{slot}          → {etiqueta?, color?, mark_in?, mark_out?, volumen?}
    POST   /api/v1/cartuchera/{banco}/{slot}/archivo  → multipart: file (+ etiqueta) · sube y asigna al botón
"""

import re
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.paths import DB_PATH, MEDIA_ROOT, to_stored

router = APIRouter(tags=["Cartuchera"])

EXTENSIONES = (".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma")
MAX_BYTES = 60 * 1024 * 1024
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _boton(c: sqlite3.Connection, banco: str, slot: int) -> None:
    r = c.execute("SELECT slots FROM paletas WHERE clave = ?", (banco,)).fetchone()
    if not r or not (1 <= slot <= r["slots"]):
        raise HTTPException(status_code=404, detail=f"Botón inexistente: {banco}/{slot}")


async def _avisar(banco: str, slot: int, **extra) -> None:
    from backend.api import api_server
    await api_server._manager.broadcast(api_server._ws_message("cartuchera_updated", {"banco": banco, "slot": slot}))
    if extra:
        await api_server._manager.broadcast(api_server._ws_message("library_updated", extra))


class BotonIn(BaseModel):
    etiqueta: Optional[str] = Field(default=None, max_length=40)
    color: Optional[str] = Field(default=None, max_length=7)          # "#RRGGBB" o "" para el color por defecto
    mark_in: Optional[float] = Field(default=None, ge=0)
    mark_out: Optional[float] = Field(default=None, ge=0)
    volumen: Optional[int] = Field(default=None, ge=0, le=150)         # % (100 = normal)
    quitar_marcas: bool = False                                        # vuelve a todo el archivo


@router.patch("/api/v1/cartuchera/{banco}/{slot}", summary="Editar un botón: título, color, marcas y volumen")
async def editar(banco: str, slot: int, b: BotonIn):
    c = _conn()
    try:
        _boton(c, banco, slot)
        fila = c.execute(
            "SELECT c.*, a.duracion FROM cartuchera c JOIN audios a ON a.id = c.audio_id"
            " WHERE c.banco = ? AND c.slot = ?", (banco, slot)).fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Ese botón está vacío: carga un efecto primero")
        if b.color is not None and b.color != "" and not _COLOR.match(b.color):
            raise HTTPException(status_code=422, detail="El color debe ser #RRGGBB")
        mi = None if b.quitar_marcas else (b.mark_in if b.mark_in is not None else fila["mark_in"])
        mo = None if b.quitar_marcas else (b.mark_out if b.mark_out is not None else fila["mark_out"])
        dur = float(fila["duracion"] or 0)
        if mi is not None and mo is not None and mo <= mi:
            raise HTTPException(status_code=422, detail="La marca de salida debe ser mayor que la de entrada")
        if dur and ((mi is not None and mi >= dur) or (mo is not None and mo > dur + 0.5)):
            raise HTTPException(status_code=422, detail=f"Las marcas deben estar dentro de los {dur:.1f} s del archivo")
        c.execute(
            "UPDATE cartuchera SET etiqueta = ?, color = ?, mark_in = ?, mark_out = ?, volumen = ?,"
            " updated_at = datetime('now') WHERE banco = ? AND slot = ?",
            ((b.etiqueta if b.etiqueta is not None else fila["etiqueta"]).strip(),
             b.color if b.color is not None else fila["color"], mi, mo,
             b.volumen if b.volumen is not None else fila["volumen"], banco, slot))
        c.commit()
    finally:
        c.close()
    await _avisar(banco, slot)
    return {"ok": True}


@router.post("/api/v1/cartuchera/{banco}/{slot}/archivo", status_code=201,
             summary="Cargar un efecto desde un archivo y dejarlo en el botón")
async def cargar_archivo(banco: str, slot: int, file: UploadFile = File(...), etiqueta: str = Form("")):
    from backend.api import api_server
    if not file.filename or not file.filename.lower().endswith(EXTENSIONES):
        raise HTTPException(status_code=415, detail="Se aceptan archivos de audio (mp3, wav, ogg, flac, aac, m4a, wma)")
    c = _conn()
    try:
        _boton(c, banco, slot)
    finally:
        c.close()
    carpeta = MEDIA_ROOT / "library" / "efectos"
    carpeta.mkdir(parents=True, exist_ok=True)
    dest = carpeta / api_server._normalize_filename(file.filename)
    n = 1
    while dest.exists():
        dest = carpeta / f"{Path(api_server._normalize_filename(file.filename)).stem}_{n}{dest.suffix}"
        n += 1
    try:
        with open(dest, "wb") as out:
            copiado = 0
            while True:
                trozo = file.file.read(1024 * 1024)
                if not trozo:
                    break
                copiado += len(trozo)
                if copiado > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="El archivo es demasiado grande (máximo 60 MB)")
                out.write(trozo)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except OSError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"No se pudo guardar el archivo: {e}")
    dur = api_server._probe_duration(dest)
    if dur <= 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="No se pudo leer el audio: ¿está dañado o es otro formato?")
    titulo = (Path(file.filename).stem.strip() or dest.stem).upper()[:120]
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio)"
            " VALUES (?, 'EFECTO', ?, ?, 'Efecto')", (titulo, dur, to_stored(dest)))
        aid = cur.lastrowid
        c.execute(
            "INSERT INTO cartuchera (banco, slot, audio_id, etiqueta) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(banco, slot) DO UPDATE SET audio_id = excluded.audio_id, etiqueta = excluded.etiqueta,"
            " mark_in = NULL, mark_out = NULL, updated_at = datetime('now')",
            (banco, slot, aid, (etiqueta or titulo).strip()[:40]))
        c.commit()
    finally:
        c.close()
    api_server._normalizador.avisar()
    await _avisar(banco, slot, action="create", id=aid, titulo=titulo)
    return {"banco": banco, "slot": slot, "audio_id": aid, "titulo": titulo, "duracion": dur,
            "archivo_path": to_stored(dest)}
