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
    res = {"titulo": "", "artista": "", "album": "", "anio": "", "genero": "", "bpm": "", "origen_datos": "nombre"}
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
        bpm = re.search(r"\d{2,3}", uno("bpm") or "")
        res["bpm"] = bpm.group(0) if bpm and 40 <= int(bpm.group(0)) <= 300 else ""
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
        # Análisis de difusión (como Jazler): días × horas de las últimas 5 semanas
        from datetime import date, timedelta
        hoy = date.today()
        matriz = {}
        for (d, h, n) in conn.execute(
                "SELECT substr(iniciado_en, 1, 10), CAST(strftime('%H', iniciado_en) AS INTEGER), COUNT(*)"
                " FROM historial_emision WHERE audio_id = ? AND iniciado_en >= date('now', 'localtime', '-34 days')"
                " GROUP BY 1, 2", (audio_id,)):
            if d and h is not None:
                matriz.setdefault(d, [0] * 24)[int(h)] = n
        difusion = [{"fecha": (hoy - timedelta(days=k)).isoformat(),
                     "dia": ((hoy - timedelta(days=k)).weekday() + 1) % 7,
                     "horas": matriz.get((hoy - timedelta(days=k)).isoformat(), [0] * 24)} for k in range(35)]
        primera = conn.execute("SELECT MIN(iniciado_en) FROM historial_emision WHERE audio_id = ?", (audio_id,)).fetchone()[0]
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
            "categoria_ident": a["categoria_ident"],
            "origen": a["origen"], "energia": a["energia"], "bpm": a["bpm"],
            "prioridad": a["prioridad"] if "prioridad" in claves else None,
            "coro": [a["cue_inicio_coro"] or 0, a["cue_final_coro"] or 0],
            "duracion": a["duracion"] or 0, "cue_inicio": a["cue_inicio"] or 0, "cue_intro": a["cue_intro"] or 0,
            "cue_mezcla": a["cue_mezcla"] or 0,
            "cue_outro": (a["cue_outro"] if "cue_outro" in claves else None) or 0,
            "repeticion_horas": a["repeticion_horas"] if "repeticion_horas" in claves else None,
            "cue_inicio_coro": a["cue_inicio_coro"] or 0, "cue_final_coro": a["cue_final_coro"] or 0,
            "fade_in": a["fade_in"] or 0, "fade_out": a["fade_out"] or 0, "ganancia_db": a["ganancia_db"],
            "marcas_auto": a["marcas_auto"] if "marcas_auto" in claves else None,
            "aprobada": a["aprobada"] if "aprobada" in claves else 1,
            "loudness_lufs": a["loudness_lufs"], "loudness_estado": a["loudness_estado"],
            "ganancia_db": a["ganancia_db"], "archivo_path": a["archivo_path"],
            "creado": a["created_at"],
            "meta_modo": a["meta_modo"] if "meta_modo" in claves else "",
            "meta_titulo": a["meta_titulo"] if "meta_titulo" in claves else "",
            "meta_artista": a["meta_artista"] if "meta_artista" in claves else "",
            "meta_extra1": a["meta_extra1"] if "meta_extra1" in claves else "",
            "meta_extra2": a["meta_extra2"] if "meta_extra2" in claves else "",
            "restr_desde": a["restr_desde"] if "restr_desde" in claves else None,
            "restr_hasta": a["restr_hasta"] if "restr_hasta" in claves else None,
            "restr_dias": a["restr_dias"] if "restr_dias" in claves else None,
            "max_por_dia": a["max_por_dia"] if "max_por_dia" in claves else None,
            "historial": {"total": total, "ultimos_30_dias": ult30, "ultimas": ultimas, "por_hora_30d": por_hora,
                          "desde": primera, "difusion": difusion},
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


def _borrar_archivo(ruta) -> bool:
    """
    Borra el archivo. En Windows puede estar abierto un instante (medición de volumen, preescucha): se reintenta
    unos segundos y, si sigue ocupado, se sigue intentando en segundo plano para que no quede huérfano en el disco.
    """
    import time as _t
    for _ in range(12):
        try:
            ruta.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            _t.sleep(0.25)

    def _despues():
        for _ in range(60):
            _t.sleep(2)
            try:
                ruta.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                continue
    import threading as _th
    _th.Thread(target=_despues, name="onix-borrado-pendiente", daemon=True).start()
    return False


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
        borrados, archivos, pendientes, omitidos = 0, 0, 0, []
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
                    if _borrar_archivo(ruta):
                        archivos += 1
                    else:
                        pendientes += 1
            except OSError:
                pass
        conn.commit()
        return {"borrados": borrados, "archivos_borrados": archivos, "archivos_pendientes": pendientes, "omitidos": omitidos}
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


# ── Carátula (imagen del disco guardada dentro del archivo) ─────────────────

def _extraer_caratula(ruta) -> Optional[tuple[bytes, str]]:
    """(bytes, tipo) de la carátula embebida en MP3 (ID3 APIC), M4A (covr) o FLAC."""
    try:
        import mutagen
        f = mutagen.File(str(ruta))
        if f is None:
            return None
        tags = getattr(f, "tags", None)
        if tags is not None and hasattr(tags, "getall"):               # MP3
            pics = tags.getall("APIC")
            if pics:
                return pics[0].data, pics[0].mime or "image/jpeg"
        if tags is not None and "covr" in tags:                         # M4A
            c = tags["covr"][0]
            return bytes(c), "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"
        pics = getattr(f, "pictures", None)                             # FLAC
        if pics:
            return pics[0].data, pics[0].mime or "image/jpeg"
    except Exception:
        return None
    return None


@router.get("/api/v1/audios/{audio_id}/caratula", summary="Carátula del disco (si el archivo la trae)")
def caratula(audio_id: int):
    """
    Se extrae una vez y se guarda en media/.caratulas/ (también el "no tiene",
    para no volver a leer el archivo). 404 = sin carátula.
    """
    from fastapi.responses import FileResponse
    cache = MEDIA_ROOT / ".caratulas"
    for ext in ("jpg", "png"):
        p = cache / f"{audio_id}.{ext}"
        if p.is_file():
            return FileResponse(p, headers={"Cache-Control": "max-age=86400"})
    if (cache / f"{audio_id}.none").exists():
        raise HTTPException(status_code=404, detail="Sin carátula")
    conn = _conn()
    try:
        r = conn.execute("SELECT archivo_path FROM audios WHERE id = ?", (audio_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise HTTPException(status_code=404, detail="Audio no encontrado")
    res = None
    try:
        ruta = resolve_media(r["archivo_path"] or "")
        res = _extraer_caratula(ruta) if ruta.is_file() else None
    except OSError:
        pass
    cache.mkdir(parents=True, exist_ok=True)
    if not res:
        (cache / f"{audio_id}.none").write_text("")
        raise HTTPException(status_code=404, detail="Sin carátula")
    datos, mime = res
    p = cache / f"{audio_id}.{'png' if 'png' in mime else 'jpg'}"
    p.write_bytes(datos)
    return FileResponse(p, headers={"Cache-Control": "max-age=86400"})


# ── Editor de canción: AutoCue y carátula ───────────────────────────────────

@router.post("/api/v1/biblioteca/{audio_id}/autocue", summary="Detectar inicio y mezcla ahora (no guarda)")
def autocue(audio_id: int):
    """El editor muestra las marcas propuestas; se guardan al presionar Guardar."""
    from backend.audio import marcas
    from backend.audio.loudness import find_ffmpeg
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="ffmpeg no está instalado: no se pueden detectar marcas")
    conn = _conn()
    try:
        r = conn.execute("SELECT archivo_path FROM audios WHERE id = ?", (audio_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise HTTPException(status_code=404, detail="Audio no encontrado")
    ruta = resolve_media(r["archivo_path"] or "")
    res = marcas.detectar(ruta, ffmpeg) if ruta.is_file() else None
    if not res:
        raise HTTPException(status_code=422, detail="No se pudo analizar el audio")
    return res


class ClaveAcoustIn(BaseModel):
    clave: str = Field("", max_length=64)


def _clave_acoustid() -> str:
    conn = _conn()
    try:
        r = conn.execute("SELECT valor FROM config WHERE clave = 'acoustid_clave'").fetchone()
    finally:
        conn.close()
    return (r["valor"] if r else "") or ""


@router.get("/api/v1/config/acoustid", summary="¿Hay clave de AcoustID? (nunca devuelve la clave)")
def acoustid_estado():
    return {"configurada": bool(_clave_acoustid())}


@router.put("/api/v1/config/acoustid", summary="Guardar la clave gratuita de AcoustID (vacía = quitarla)")
def acoustid_guardar(body: ClaveAcoustIn):
    clave = body.clave.strip()
    conn = _conn()
    try:
        conn.execute("INSERT INTO config (clave, valor) VALUES ('acoustid_clave', ?) "
                     "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave,))
        conn.commit()
    finally:
        conn.close()
    return {"configurada": bool(clave)}


class AnalisisConfIn(BaseModel):
    itunes: Optional[bool] = None
    deezer: Optional[bool] = None


def _fuentes_analisis() -> dict:
    conn = _conn()
    try:
        v = {r["clave"]: r["valor"] for r in conn.execute("SELECT clave, valor FROM config WHERE clave IN ('analisis_itunes', 'analisis_deezer')")}
    finally:
        conn.close()
    return {"itunes": v.get("analisis_itunes", "1") != "0", "deezer": v.get("analisis_deezer", "1") != "0"}


@router.get("/api/v1/config/analisis", summary="Fuentes extra del análisis (iTunes, Deezer) y si hay clave de AcoustID")
def analisis_config():
    return {**_fuentes_analisis(), "acoustid": bool(_clave_acoustid())}


@router.put("/api/v1/config/analisis", summary="Encender o apagar iTunes y Deezer en el análisis")
def analisis_config_guardar(body: AnalisisConfIn):
    conn = _conn()
    try:
        for clave, v in (("analisis_itunes", body.itunes), ("analisis_deezer", body.deezer)):
            if v is not None:
                conn.execute("INSERT INTO config (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                             (clave, "1" if v else "0"))
        conn.commit()
    finally:
        conn.close()
    return {**_fuentes_analisis(), "acoustid": bool(_clave_acoustid())}


class CaratulaUrlIn(BaseModel):
    url: str = Field(..., max_length=500)


@router.post("/api/v1/audios/{audio_id}/caratula-url", summary="Poner de carátula una imagen de iTunes o Deezer")
def caratula_desde_url(audio_id: int, body: CaratulaUrlIn):
    from backend.audio import metadatos
    conn = _conn()
    try:
        if not conn.execute("SELECT 1 FROM audios WHERE id = ? AND activo = 1", (audio_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Audio no encontrado")
    finally:
        conn.close()
    try:
        datos, ext = metadatos.descargar_imagen(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except metadatos.SinConexion:
        raise HTTPException(status_code=503, detail="No se pudo bajar la imagen (sin conexión)")
    cache = MEDIA_ROOT / ".caratulas"
    cache.mkdir(parents=True, exist_ok=True)
    for e in ("jpg", "png", "none"):
        (cache / f"{audio_id}.{e}").unlink(missing_ok=True)
    (cache / f"{audio_id}.{ext}").write_bytes(datos)
    return {"ok": True}


# Para poder probar sin internet: los tests cambian esta función
def _http_externo():
    from backend.audio import metadatos
    return metadatos.http_json


# ── Años que faltan: buscar y aplicar solo lo que la persona acepte ──────────────────────────────

class AniosBuscarIn(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=10)     # pocos por llamada: MusicBrainz admite 1 consulta/s


class AnioItem(BaseModel):
    id: int
    anio: int = Field(..., ge=1900, le=2100)


class AniosAplicarIn(BaseModel):
    items: list[AnioItem] = Field(..., min_length=1, max_length=500)


@router.get("/api/v1/biblioteca/anios/pendientes", summary="Canciones activas sin año de lanzamiento")
def anios_pendientes():
    c = _conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT id, titulo, artista FROM audios WHERE activo = 1 AND tipo_audio = 'Musica'"
            " AND COALESCE(TRIM(fecha_lanzamiento), '') = '' ORDER BY artista, titulo")]
    finally:
        c.close()


@router.post("/api/v1/biblioteca/anios/buscar", summary="Buscar el año de varias canciones (no guarda)")
def anios_buscar(body: AniosBuscarIn):
    """
    Por canción: el primer lanzamiento del MISMO artista en MusicBrainz (confianza «alta»); si no hay, iTunes
    (confianza «probable»: puede ser el año de una reedición). Sin coincidencia clara devuelve anio = null.
    """
    import time
    from backend.audio import metadatos
    from backend.locutor import datos
    c = _conn()
    try:
        filas = [dict(r) for r in c.execute(
            f"SELECT id, titulo, artista FROM audios WHERE id IN ({','.join('?' * len(body.ids))}) AND activo = 1",
            body.ids)]
    finally:
        c.close()
    out = []
    for n, f in enumerate(filas):
        if n:
            time.sleep(1.05)                                   # cortesía con MusicBrainz
        item = {**f, "anio": None, "fuente": None, "confianza": None, "error": None}
        try:
            anio = datos.buscar_anio(f["artista"], f["titulo"])
            if anio:
                item.update(anio=anio, fuente="MusicBrainz", confianza="alta")
            else:
                r = metadatos.buscar_itunes(datos.artista_principal(f["artista"]), f["titulo"], metadatos.http_json)
                if r and re.match(r"(19|20)\d{2}$", str(r.get("anio") or "")):
                    item.update(anio=int(r["anio"]), fuente="iTunes", confianza="probable")
        except Exception as e:                                 # sin red o servicio caído: se informa y se sigue
            item["error"] = str(e)[:120]
        out.append(item)
    return {"resultados": out}


@router.post("/api/v1/biblioteca/anios/aplicar", summary="Guardar los años aceptados (no pisa un año que ya exista)")
def anios_aplicar(body: AniosAplicarIn):
    c = _conn()
    try:
        n = 0
        for it in body.items:
            n += c.execute(
                "UPDATE audios SET fecha_lanzamiento = ?, updated_at = datetime('now') WHERE id = ? AND activo = 1"
                " AND COALESCE(TRIM(fecha_lanzamiento), '') = ''", (str(it.anio), it.id)).rowcount
        c.commit()
        return {"guardados": n, "omitidos": len(body.items) - n}
    finally:
        c.close()


@router.post("/api/v1/biblioteca/{audio_id}/analizar", summary="Sugerir título, artista, álbum, año, ritmo, energía y marcas (no guarda)")
def analizar(audio_id: int, en_linea: bool = True):
    """
    Une las etiquetas del archivo, la búsqueda en línea (MusicBrainz / AcoustID) y el análisis de
    la señal (ritmo, inicio, intro, outro, mezcla). Devuelve propuestas con su fuente y su confianza;
    el editor las muestra y el operador elige cuáles aplicar. No toca la ficha.
    """
    from backend.audio import sugerencias
    from backend.audio.loudness import find_ffmpeg
    conn = _conn()
    try:
        r = conn.execute("SELECT * FROM audios WHERE id = ? AND activo = 1", (audio_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise HTTPException(status_code=404, detail="Audio no encontrado")
    ruta = resolve_media(r["archivo_path"] or "")
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="No se encuentra el archivo de audio")
    tiene_cover = any((MEDIA_ROOT / ".caratulas" / f"{audio_id}.{e}").is_file() for e in ("jpg", "png")) or _extraer_caratula(ruta) is not None
    actual = {"tiene_caratula": tiene_cover, "titulo": r["titulo"], "artista": r["artista"], "album": r["album"],
              "anio": (r["fecha_lanzamiento"] or "")[:4], "bpm": r["bpm"], "energia": r["energia"],
              "cue_inicio": r["cue_inicio"], "cue_intro": r["cue_intro"], "cue_outro": r["cue_outro"], "cue_mezcla": r["cue_mezcla"]}
    out = sugerencias.sugerir(ruta, ruta.name, actual, find_ffmpeg(), _clave_acoustid(), en_linea, _http_externo(),
                            _fuentes_analisis())
    out["id"] = audio_id
    out["acoustid"] = bool(_clave_acoustid())
    out["fuentes"] = _fuentes_analisis()
    return out


@router.post("/api/v1/audios/{audio_id}/caratula", summary="Poner carátula: subir imagen o leerla de la etiqueta")
async def poner_caratula(audio_id: int, file: Optional[UploadFile] = File(None)):
    """Con archivo: esa imagen. Sin archivo: se vuelve a leer de la etiqueta del audio."""
    cache = MEDIA_ROOT / ".caratulas"
    cache.mkdir(parents=True, exist_ok=True)
    datos = await file.read(5 * 1024 * 1024) if file is not None else None
    if datos is not None:
        if datos[:3] == b"\xff\xd8\xff":
            ext = "jpg"
        elif datos[:8] == b"\x89PNG\r\n\x1a\n":
            ext = "png"
        else:
            raise HTTPException(status_code=422, detail="La imagen debe ser JPG o PNG")
    for e in ("jpg", "png", "none"):
        (cache / f"{audio_id}.{e}").unlink(missing_ok=True)
    if datos is None:
        caratula(audio_id)                              # extrae de nuevo de la etiqueta (o 404)
        return {"ok": True}
    (cache / f"{audio_id}.{ext}").write_bytes(datos)
    return {"ok": True}


@router.delete("/api/v1/audios/{audio_id}/caratula", summary="Quitar la carátula")
def quitar_caratula(audio_id: int):
    cache = MEDIA_ROOT / ".caratulas"
    cache.mkdir(parents=True, exist_ok=True)
    for ext in ("jpg", "png"):
        (cache / f"{audio_id}.{ext}").unlink(missing_ok=True)
    (cache / f"{audio_id}.none").write_text("")
    return {"ok": True}
