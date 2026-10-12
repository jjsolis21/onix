"""
biblioteca.py — Ónix FM
Biblioteca musical, fase B: ficha de cada canción y carga inteligente.

    GET  /api/v1/biblioteca/calidad     → grupos para revisar: duplicados, sin
                                          clasificar, volumen raro, quemadas,
                                          olvidadas, con restricciones
    GET  /api/v1/biblioteca/papelera    → lo eliminado (se puede restaurar)
    POST /api/v1/biblioteca/restaurar   → {"ids": [...]}
    POST /api/v1/biblioteca/borrar      → {"ids": [...]} borra DEFINITIVAMENTE
                                          (fila y archivo) lo que está en la
                                          papelera; el historial de emisión
                                          conserva título y artista
    GET  /api/v1/biblioteca/{id}/ficha  → datos, historial de emisión, relojes
                                          que pueden elegirla, si está en el
                                          programa de hoy y posibles duplicados
    POST /api/v1/biblioteca/etiquetas   → lee las etiquetas del archivo (ID3:
                                          artista, título, álbum, año, género)
                                          y avisa si ya está en la biblioteca

Etiquetas: el navegador manda solo el COMIENZO del archivo (las etiquetas ID3v2
van al principio del MP3), así es rápido aunque se carguen cientos desde otra
PC. Si no hay etiquetas (o el formato las guarda al final, como algunos M4A),
se usa el nombre del archivo: "Artista - Título.mp3".

Duplicados: se compara artista + título "normalizados" (sin tildes,
mayúsculas, signos ni lo que va entre paréntesis: "(Remix)", "(feat. X)").
"""

import io
import json
import re
import sqlite3
import unicodedata
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.paths import DB_PATH, MEDIA_ROOT, resolve_media

router = APIRouter(tags=["Biblioteca"])

# api_server.py asigna aquí el motor (local o remoto)
engine = None

MAX_CABECERA = 2 * 1024 * 1024     # lo que se lee del comienzo del archivo


def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def normalizar(texto: str) -> str:
    """'Provenza (feat. X) – Remix' → 'provenza'. Para encontrar duplicados."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)            # (Remix), [Live], (feat. X)
    t = re.sub(r"\b(feat|ft|featuring)\b.*$", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def duplicados(conn, artista: str, titulo: str, excluir: Optional[int] = None) -> list[dict]:
    na, nt = normalizar(artista), normalizar(titulo)
    if not nt:
        return []
    # Filtro grueso en SQL (misma primera palabra del título) y fino en Python
    primera = nt.split()[0]
    filas = conn.execute(
        "SELECT id, titulo, artista, duracion, tipo_audio, activo FROM audios"
        " WHERE activo = 1 AND tipo_audio != 'Cuña' AND LOWER(titulo) LIKE ?",
        (f"%{primera}%",)).fetchall()
    out = []
    for r in filas:
        if r["id"] == excluir:
            continue
        if normalizar(r["titulo"]) == nt and (not na or normalizar(r["artista"]) == na):
            out.append({"id": r["id"], "titulo": r["titulo"], "artista": r["artista"],
                        "duracion": r["duracion"] or 0})
    return out


def _desde_nombre(nombre: str) -> dict:
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nombre or "").replace("_", " ").strip()
    base = re.sub(r"^\d{1,3}[\s.\-]+", "", base)       # "01 - ", "07. "
    if " - " in base:
        artista, titulo = base.split(" - ", 1)
        return {"artista": artista.strip(), "titulo": titulo.strip()}
    return {"artista": "", "titulo": base}


def leer_etiquetas(datos: bytes, nombre: str) -> dict:
    """Etiquetas del archivo (o del nombre). Nunca lanza."""
    res = {"titulo": "", "artista": "", "album": "", "anio": "", "genero": "", "origen_datos": "nombre"}
    try:
        import mutagen
        f = mutagen.File(io.BytesIO(datos), easy=True)
        if f is None:
            raise ValueError("formato no reconocido")
        tags = f.tags or {}

        def uno(k):
            v = tags.get(k)
            return (v[0] if isinstance(v, list) and v else (v or "")).strip() if v else ""
        res.update(titulo=uno("title"), artista=uno("artist") or uno("albumartist"),
                   album=uno("album"), genero=uno("genre"))
        anio = re.search(r"\d{4}", uno("date") or uno("year") or "")
        res["anio"] = anio.group(0) if anio else ""
        if res["titulo"] or res["artista"]:
            res["origen_datos"] = "etiquetas"
    except Exception:
        pass
    nom = _desde_nombre(nombre)
    res["titulo"] = res["titulo"] or nom["titulo"]
    res["artista"] = res["artista"] or nom["artista"]
    return res


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/api/v1/biblioteca/etiquetas", summary="Leer etiquetas y buscar duplicados")
async def etiquetas(file: UploadFile = File(...), nombre: str = Form("")):
    datos = await file.read(MAX_CABECERA)
    info = leer_etiquetas(datos, nombre or file.filename or "")
    conn = _conn()
    try:
        info["duplicados"] = duplicados(conn, info["artista"], info["titulo"])
    finally:
        conn.close()
    return info


def _describir_filtros(fila) -> str:
    try:
        f = json.loads(fila["filtros"] or "{}")
    except ValueError:
        f = {}
    partes = [str(v) for k, v in f.items() if v not in (None, "")]
    return " · ".join(partes) or "cualquier música"


@router.get("/api/v1/biblioteca/{audio_id}/ficha", summary="Ficha completa de una canción")
def ficha(audio_id: int):
    from backend.audio.planner import Planner
    conn = _conn()
    try:
        a = conn.execute("SELECT * FROM audios WHERE id = ?", (audio_id,)).fetchone()
        if not a:
            raise HTTPException(status_code=404, detail="Audio no encontrado")

        # Historial de emisión
        total = conn.execute("SELECT COUNT(*) FROM historial_emision WHERE audio_id = ?", (audio_id,)).fetchone()[0]
        ult30 = conn.execute("SELECT COUNT(*) FROM historial_emision WHERE audio_id = ?"
                             " AND iniciado_en >= datetime('now', 'localtime', '-30 days')", (audio_id,)).fetchone()[0]
        ultimas = [r[0] for r in conn.execute(
            "SELECT iniciado_en FROM historial_emision WHERE audio_id = ? ORDER BY id DESC LIMIT 8", (audio_id,))]
        por_hora = [0] * 24
        for (h, n) in conn.execute(
                "SELECT CAST(strftime('%H', iniciado_en) AS INTEGER), COUNT(*) FROM historial_emision"
                " WHERE audio_id = ? AND iniciado_en >= datetime('now', 'localtime', '-30 days') GROUP BY 1",
                (audio_id,)):
            if h is not None:
                por_hora[int(h)] = n

        # Relojes cuyas filas pueden elegirla
        p = Planner(DB_PATH)
        relojes = []
        for r in conn.execute("SELECT id, nombre, color FROM relojes ORDER BY nombre"):
            filas = []
            for i, f in enumerate(conn.execute(
                    "SELECT tipo_audio, filtros, audio_id FROM reloj_filas WHERE reloj_id = ? ORDER BY orden, id",
                    (r["id"],)).fetchall()):
                if f["audio_id"]:
                    puede = f["audio_id"] == audio_id
                else:
                    try:
                        filtros = json.loads(f["filtros"] or "{}")
                    except ValueError:
                        filtros = {}
                    clauses, params = p._where_filtros(filtros if isinstance(filtros, dict) else {})
                    where = " AND ".join(["a.id = ?", "a.tipo_audio = ?"] + clauses)
                    puede = bool(conn.execute(f"SELECT 1 FROM audios a WHERE {where}",
                                              [audio_id, f["tipo_audio"]] + params).fetchone())
                if puede:
                    filas.append({"fila": i + 1, "filtros": "audio fijo" if f["audio_id"] else _describir_filtros(f)})
            if filas:
                horas = conn.execute("SELECT COUNT(*) FROM reloj_grilla WHERE reloj_id = ?", (r["id"],)).fetchone()[0]
                relojes.append({"id": r["id"], "nombre": r["nombre"], "color": r["color"],
                                "filas": filas, "horas_en_grilla": horas})

        # ¿Está en lo que viene?
        en_programa = []
        if engine is not None:
            try:
                prog = engine.programa()
                en_programa = [i.get("hora", "")[:5] for i in prog.get("items", [])
                               if i.get("tipo") == "audio" and i.get("audio_id") == audio_id]
            except Exception:
                pass

        claves = a.keys()
        return {
            "id": a["id"], "titulo": a["titulo"], "artista": a["artista"], "album": a["album"],
            "anio": (a["fecha_lanzamiento"] or "")[:4], "tipo_audio": a["tipo_audio"],
            "cat1": a["subgenero"], "cat2": a["categoria"], "cat3": a["cat3"], "voz": a["genero_vocal"],
            "origen": a["origen"], "energia": a["energia"], "bpm": a["bpm"],
            "duracion": a["duracion"] or 0, "cue_inicio": a["cue_inicio"] or 0, "cue_intro": a["cue_intro"] or 0,
            "cue_mezcla": a["cue_mezcla"] or 0,
            "marcas_auto": a["marcas_auto"] if "marcas_auto" in claves else None,
            "aprobada": a["aprobada"] if "aprobada" in claves else 1,
            "loudness_lufs": a["loudness_lufs"], "loudness_estado": a["loudness_estado"],
            "ganancia_db": a["ganancia_db"], "archivo_path": a["archivo_path"],
            "creado": a["created_at"],
            "restr_desde": a["restr_desde"] if "restr_desde" in claves else None,
            "restr_hasta": a["restr_hasta"] if "restr_hasta" in claves else None,
            "restr_dias": a["restr_dias"] if "restr_dias" in claves else None,
            "max_por_dia": a["max_por_dia"] if "max_por_dia" in claves else None,
            "historial": {"total": total, "ultimos_30_dias": ult30, "ultimas": ultimas, "por_hora_30d": por_hora},
            "relojes": relojes, "en_programa": en_programa,
            "duplicados": duplicados(conn, a["artista"], a["titulo"], excluir=audio_id),
        }
    finally:
        conn.close()


# ── Papelera ─────────────────────────────────────────────────────────────────

class IdsIn(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=20000)


@router.get("/api/v1/biblioteca/papelera", summary="Audios eliminados (restaurables)")
def papelera():
    conn = _conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, titulo, artista, tipo_audio, duracion, updated_at FROM audios"
            " WHERE activo = 0 AND tipo_audio != 'Cuña' ORDER BY updated_at DESC, id DESC")]
    finally:
        conn.close()


@router.post("/api/v1/biblioteca/restaurar", summary="Sacar de la papelera")
def restaurar(body: IdsIn):
    conn = _conn()
    try:
        ph = ",".join("?" * len(body.ids))
        n = conn.execute(f"UPDATE audios SET activo = 1, updated_at = datetime('now') WHERE id IN ({ph})"
                         " AND activo = 0", body.ids).rowcount
        conn.commit()
        return {"restaurados": n}
    finally:
        conn.close()


@router.post("/api/v1/biblioteca/borrar", summary="Borrar definitivamente (solo desde la papelera)")
def borrar_definitivo(body: IdsIn):
    """
    Solo lo que ya está en la papelera. Se borra la fila y el archivo del disco
    (si está dentro de la carpeta de medios). No se borra lo que use una pauta.
    """
    conn = _conn()
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        ph = ",".join("?" * len(body.ids))
        filas = conn.execute(f"SELECT id, titulo, archivo_path FROM audios WHERE id IN ({ph}) AND activo = 0",
                             body.ids).fetchall()
        en_pauta = {r[0] for r in conn.execute(
            f"SELECT DISTINCT audio_id FROM pautas WHERE audio_id IN ({ph})", body.ids)}
        borrados, archivos, omitidos = 0, 0, []
        raiz = MEDIA_ROOT.resolve()
        for f in filas:
            if f["id"] in en_pauta:
                omitidos.append({"id": f["id"], "titulo": f["titulo"], "motivo": "está en una pauta"})
                continue
            conn.execute("DELETE FROM audios WHERE id = ?", (f["id"],))
            borrados += 1
            try:
                ruta = resolve_media(f["archivo_path"] or "").resolve()
                if raiz in ruta.parents and ruta.is_file():      # nunca fuera de la carpeta de medios
                    ruta.unlink()
                    archivos += 1
            except OSError:
                pass
        conn.commit()
        return {"borrados": borrados, "archivos_borrados": archivos, "omitidos": omitidos}
    finally:
        conn.close()


# ── Calidad de la biblioteca ─────────────────────────────────────────────────

@router.get("/api/v1/biblioteca/calidad", summary="Qué revisar en la biblioteca")
def calidad():
    """
    Grupos de ids para filtrar la tabla. Solo música activa.
      duplicados   misma canción (artista + título normalizados) más de una vez
      sin_clasificar  sin género o sin rotación
      volumen      no se pudo medir, o muy por fuera de lo normal (LUFS < -24 o > -7)
      quemadas     sonaron mucho más que el resto en los últimos 7 días
      olvidadas    aprobadas, cargadas hace más de 30 días y sin sonar en 30 días
      restringidas con franja, días o tope por día
    """
    conn = _conn()
    try:
        musica = conn.execute(
            "SELECT id, titulo, artista, subgenero, categoria, loudness_estado, loudness_lufs, aprobada,"
            " created_at, restr_desde, restr_hasta, restr_dias, max_por_dia FROM audios"
            " WHERE activo = 1 AND tipo_audio = 'Musica'").fetchall()
        grupos = {}
        for r in musica:
            grupos.setdefault((normalizar(r["artista"]), normalizar(r["titulo"])), []).append(r["id"])
        duplicados = [i for ids in grupos.values() if len(ids) > 1 for i in ids]
        sin_clas = [r["id"] for r in musica if not (r["subgenero"] or "").strip() or not (r["categoria"] or "").strip()]
        volumen = [r["id"] for r in musica if r["loudness_estado"] == "error" or
                   (r["loudness_lufs"] is not None and not -24 <= r["loudness_lufs"] <= -7)]
        semana = {r[0]: r[1] for r in conn.execute(
            "SELECT audio_id, COUNT(*) FROM historial_emision WHERE audio_id IS NOT NULL"
            " AND iniciado_en >= datetime('now', 'localtime', '-7 days') GROUP BY audio_id")}
        ids_musica = {r["id"] for r in musica}
        cuentas = [semana.get(i, 0) for i in ids_musica]
        quemadas = []
        if cuentas:
            media = sum(cuentas) / len(cuentas)
            var = sum((c - media) ** 2 for c in cuentas) / len(cuentas)
            umbral = max(media + 2 * var ** 0.5, media * 2, 3)
            quemadas = sorted((i for i in ids_musica if semana.get(i, 0) >= umbral),
                              key=lambda i: -semana.get(i, 0))
        ult30 = {r[0] for r in conn.execute(
            "SELECT DISTINCT audio_id FROM historial_emision WHERE audio_id IS NOT NULL"
            " AND iniciado_en >= datetime('now', 'localtime', '-30 days')")}
        olvidadas = [r["id"] for r in musica if r["aprobada"] and r["id"] not in ult30
                     and (r["created_at"] or "") < conn.execute("SELECT datetime('now', '-30 days')").fetchone()[0]]
        restringidas = [r["id"] for r in musica if r["restr_dias"] or r["max_por_dia"]
                        or (r["restr_desde"] is not None and r["restr_hasta"] is not None
                            and r["restr_desde"] != r["restr_hasta"])]
        return {"duplicados": duplicados, "sin_clasificar": sin_clas, "volumen": volumen,
                "quemadas": quemadas, "olvidadas": olvidadas, "restringidas": restringidas,
                "papelera": conn.execute("SELECT COUNT(*) FROM audios WHERE activo = 0 AND tipo_audio != 'Cuña'").fetchone()[0]}
    finally:
        conn.close()
