"""
voicetrack.py — Ónix FM
Voice Tracker: locuciones que entran SOBRE el final de una canción (la música baja) y dan paso a la
siguiente. Se graban con el micrófono del navegador o se hacen con voz de IA, y se colocan en la cola o el
programa como cualquier audio (el motor reconoce el tipo 'Locucion' y hace el talk-over).

    GET    /api/v1/voicetrack                 → locuciones recientes
    GET    /api/v1/voicetrack/voces           → voces de IA disponibles según las claves cargadas
    POST   /api/v1/voicetrack/texto           → {anterior_id?, siguiente_id?, indicacion?} → texto para leer
    POST   /api/v1/voicetrack/ia              → {texto, voz?, estilo?, titulo?} → crea la locución con voz de IA
    POST   /api/v1/voicetrack/grabar          → multipart: file (WAV), titulo → crea la locución grabada
    DELETE /api/v1/voicetrack/{id}            → a la Papelera
"""

import sqlite3
import wave
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.ia import proveedores
from backend.paths import DB_PATH, MEDIA_ROOT, to_stored

router = APIRouter(tags=["Voice Tracker"])

TIPO_VOZ = "Locucion"
medir_volumen = None                  # lo asigna api_server (avisa al normalizador)
MAX_BYTES = 40 * 1024 * 1024          # ~4 min de WAV estéreo 44,1 kHz
SISTEMA = ("Eres el locutor de una radio FM latina. Escribes lo que el locutor dice al aire entre dos canciones: "
           "español neutro, cercano y con energía, frases cortas, sin emojis, sin comillas, sin acotaciones "
           "entre paréntesis, sin inventar datos (fechas, premios, récords). Entre 15 y 45 palabras. "
           "Devuelve SOLO el texto a leer.")


def _conn():
    from backend.database import get_connection
    return get_connection()


def _emisora(c) -> str:
    try:
        r = c.execute("SELECT valor FROM config WHERE clave = 'emisora_nombre'").fetchone()
        return (r[0] if r and r[0] else "") or "ÓNIX FM"
    except sqlite3.OperationalError:
        return "ÓNIX FM"


def _cancion(c, aid: Optional[int]) -> Optional[dict]:
    if not aid:
        return None
    r = c.execute("SELECT titulo, artista FROM audios WHERE id = ?", (aid,)).fetchone()
    return {"titulo": r["titulo"] or "", "artista": r["artista"] or ""} if r else None


def plantilla(anterior: Optional[dict], siguiente: Optional[dict], emisora: str) -> str:
    """El texto de siempre, sin IA: lo que se usa si no hay claves o fallan todas."""
    partes = []
    if anterior:
        partes.append(f"Sonó {anterior['titulo']}" + (f", de {anterior['artista']}" if anterior["artista"] else "") + ".")
    partes.append(f"Seguimos en {emisora}.")
    if siguiente:
        partes.append(f"Ahora viene {siguiente['titulo']}" + (f", de {siguiente['artista']}" if siguiente["artista"] else "") + ".")
    return " ".join(partes)


def _guardar(c, wav: bytes, titulo: str, artista: str) -> dict:
    ahora = datetime.now()
    carpeta = MEDIA_ROOT / "VOCES" / ahora.strftime("%Y-%m")
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"vt_{ahora:%Y%m%d_%H%M%S_%f}.wav"
    ruta.write_bytes(wav)
    try:
        with wave.open(str(ruta), "rb") as w:
            dur = w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        ruta.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="El audio no es un WAV válido")
    if dur < 0.5:
        ruta.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="La locución es demasiado corta")
    titulo = (titulo or "").strip()[:120] or f"Voice track {ahora:%d/%m %H:%M}"
    cur = c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio)"
                    " VALUES (?, ?, ?, ?, ?)", (titulo, artista, int(round(dur)), to_stored(ruta), TIPO_VOZ))
    c.commit()
    if medir_volumen:                         # que se mida su volumen como cualquier audio
        try:
            medir_volumen()
        except Exception:
            pass
    return {"audio_id": cur.lastrowid, "titulo": titulo, "duracion": round(dur, 1), "archivo_path": to_stored(ruta)}


@router.get("/api/v1/voicetrack", summary="Locuciones (voice tracks) recientes")
def listar(limit: int = 40):
    c = _conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT id, titulo, artista, duracion, created_at, ultima_reproduccion FROM audios"
            " WHERE tipo_audio = ? AND activo = 1 ORDER BY id DESC LIMIT ?", (TIPO_VOZ, max(1, min(limit, 200))))]
    finally:
        c.close()


@router.get("/api/v1/voicetrack/voces", summary="Voces de IA que se pueden usar con las claves cargadas")
def voces():
    c = _conn()
    try:
        claves = [dict(r) for r in c.execute("SELECT * FROM ia_claves WHERE activo = 1 ORDER BY orden, id")]
    except sqlite3.OperationalError:
        claves = []
    finally:
        c.close()
    provs = {k["proveedor"] for k in claves}
    propias, vistas = [], set()
    for k in claves:                      # voces propias (Fish Audio) primero, con su nombre
        if k["proveedor"] == "fish":
            for nombre, vid in proveedores.voces_de(k):
                if vid not in vistas:
                    vistas.add(vid)
                    propias.append({"proveedor": "fish", "voz": f"fish:{vid}", "nombre": nombre})
    catalogo = [{"proveedor": p, "voz": v, "nombre": v} for p in ("gemini", "openai") if p in provs for v in proveedores.VOCES[p]]
    return {"voces": propias + catalogo, "texto_ia": bool(provs - set(proveedores.SOLO_VOZ))}


class TextoIn(BaseModel):
    anterior_id:  Optional[int] = None
    siguiente_id: Optional[int] = None
    indicacion:   str = Field(default="", max_length=500)


@router.post("/api/v1/voicetrack/texto", summary="Escribir lo que dice el locutor (IA, o plantilla si no hay)")
def texto(b: TextoIn):
    c = _conn()
    try:
        ant, sig, emisora = _cancion(c, b.anterior_id), _cancion(c, b.siguiente_id), _emisora(c)
    finally:
        c.close()
    pedido = [f"Emisora: {emisora}."]
    if ant:
        pedido.append(f"Acaba de sonar: «{ant['titulo']}» de {ant['artista'] or 'artista desconocido'}.")
    if sig:
        pedido.append(f"Lo que sigue: «{sig['titulo']}» de {sig['artista'] or 'artista desconocido'}.")
    if b.indicacion.strip():
        pedido.append(f"Indicación del locutor: {b.indicacion.strip()}")
    try:
        r = proveedores.generar_texto(" ".join(pedido), SISTEMA)
        return {"texto": " ".join(r["texto"].split())[:600], "origen": "ia", "proveedor": r["proveedor"]}
    except proveedores.IAError as e:
        return {"texto": plantilla(ant, sig, emisora), "origen": "plantilla", "aviso": str(e)}


class VozIn(BaseModel):
    texto:  str = Field(..., min_length=2, max_length=1500)
    voz:    str = Field(default="", max_length=40)
    estilo: str = Field(default="", max_length=300)
    titulo: str = Field(default="", max_length=120)


@router.post("/api/v1/voicetrack/ia", status_code=201, summary="Crear la locución con voz de IA")
def con_ia(b: VozIn):
    try:
        r = proveedores.generar_voz(" ".join(b.texto.split()), b.voz, b.estilo)
    except proveedores.IAError as e:
        raise HTTPException(status_code=503, detail=str(e))
    c = _conn()
    try:
        out = _guardar(c, r["wav"], b.titulo or ("IA · " + " ".join(b.texto.split())[:60]), "LOCUTOR IA")
    finally:
        c.close()
    return {**out, "proveedor": r["proveedor"]}


@router.post("/api/v1/voicetrack/grabar", status_code=201, summary="Guardar una locución grabada (WAV)")
async def grabar(file: UploadFile = File(...), titulo: str = Form(""), locutor: str = Form("")):
    datos = await file.read(MAX_BYTES + 1)
    if len(datos) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="La grabación es demasiado larga (máximo ~4 minutos)")
    if not datos.startswith(b"RIFF"):
        raise HTTPException(status_code=415, detail="La grabación debe llegar en WAV")
    c = _conn()
    try:
        return _guardar(c, datos, titulo, (locutor or "").strip()[:60] or "VOICE TRACK")
    finally:
        c.close()


@router.delete("/api/v1/voicetrack/{aid}", summary="Mandar una locución a la Papelera")
def quitar(aid: int):
    c = _conn()
    try:
        if not c.execute("UPDATE audios SET activo = 0 WHERE id = ? AND tipo_audio = ?", (aid, TIPO_VOZ)).rowcount:
            raise HTTPException(status_code=404, detail="Locución no encontrada")
        c.commit()
        return {"ok": True}
    finally:
        c.close()
