"""
sugerencias.py — Ónix FM
Une el análisis de la señal (analisis.py) y los datos de la canción (metadatos.py) en una lista de
SUGERENCIAS para el editor de canción. No guarda nada: cada sugerencia lleva el valor actual, el
propuesto, de dónde sale y qué tan seguro es; el operador decide cuáles aplicar.
"""

import logging
from typing import Callable, Optional

from backend.audio import analisis, metadatos

log = logging.getLogger("Sugerencias")

# (campo, etiqueta que ve el operador)
ETIQUETAS = {
    "titulo": "Título", "artista": "Artista", "album": "Álbum", "anio": "Año", "bpm": "BPM", "energia": "Energía",
    "cue_inicio": "Inicio", "cue_intro": "Intro", "cue_outro": "Outro", "cue_mezcla": "Mezcla",
}
NOMBRE_ENERGIA = {1: "1 · Lenta", 2: "2 · Suave", 3: "3 · Media", 4: "4 · Movida", 5: "5 · Rápida"}


def _igual(campo, a, b) -> bool:
    if campo.startswith("cue_"):
        return abs(float(a or 0) - float(b or 0)) < 0.3
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def sugerir(path, nombre_archivo: str, actual: dict, ffmpeg: Optional[str], clave_acoustid: str = "",
            en_linea: bool = True, http: Callable = metadatos.http_json) -> dict:
    """
    actual: valores hoy en la ficha (titulo, artista, album, anio, bpm, energia, cue_*).
    Devuelve {campos: [{campo, etiqueta, actual, sugerido, mostrar, fuente, confianza}], avisos: [...], en_linea: bool}
    """
    avisos: list[str] = []
    prop: dict[str, tuple] = {}                    # campo → (valor, fuente, confianza)

    def poner(campo, valor, fuente, confianza):
        if valor in (None, "", 0) and campo != "cue_inicio":
            return
        orden = {"baja": 0, "media": 1, "alta": 2}
        # Gana la fuente más confiable; con igual confianza, la primera (las etiquetas del archivo van antes)
        if campo not in prop or orden.get(confianza, 0) > orden.get(prop[campo][2], 0):
            prop[campo] = (valor, fuente, confianza)

    # 1. Etiquetas del archivo (y, si faltan, el nombre)
    tags = metadatos.desde_archivo(path)
    nom = metadatos.desde_nombre(nombre_archivo)
    for k in ("titulo", "artista", "album", "anio"):
        if tags.get(k):
            poner(k, tags[k], "Etiquetas del archivo", "alta")
    artista = tags.get("artista") or actual.get("artista") or nom["artista"]
    titulo = tags.get("titulo") or actual.get("titulo") or nom["titulo"]
    if not tags.get("titulo") and nom["titulo"]:
        poner("titulo", nom["titulo"], "Nombre del archivo", "baja")
    if not tags.get("artista") and nom["artista"]:
        poner("artista", nom["artista"], "Nombre del archivo", "baja")
    if tags.get("bpm"):
        poner("bpm", tags["bpm"], "Etiquetas del archivo", "alta")

    # 2-3. En línea: huella (AcoustID) y MusicBrainz
    conexion = False
    if en_linea:
        try:
            if clave_acoustid:
                h = metadatos.huella(path, ffmpeg)
                if h:
                    r = metadatos.buscar_acoustid(h[0], h[1], clave_acoustid, http)
                    conexion = True
                    if r:
                        for k in ("titulo", "artista", "album", "anio"):
                            poner(k, r.get(k), r["fuente"], r["confianza"])
                        artista, titulo = r["artista"] or artista, r["titulo"] or titulo
                else:
                    avisos.append("No se pudo sacar la huella del audio (hace falta ffmpeg con chromaprint o fpcalc): se buscó solo por texto.")
            else:
                avisos.append("Para reconocer audios sin etiquetas, registra una clave gratuita de AcoustID (acoustid.org/new-application) y pégala en el cuadro de abajo.")
            if titulo:
                r = metadatos.buscar_musicbrainz(artista, titulo, http)
                conexion = True
                if r:
                    # Con el título y artista ya conocidos, MusicBrainz aporta sobre todo álbum y año
                    for k in ("album", "anio", "titulo", "artista"):
                        poner(k, r.get(k), r["fuente"], r["confianza"])
                elif not any(k in prop for k in ("album", "anio")):
                    avisos.append("MusicBrainz no encontró esta canción: revisa que el título y el artista estén bien escritos.")
        except metadatos.SinConexion:
            avisos.append("Sin conexión a internet: se omitió la búsqueda en línea (álbum, año y reconocimiento).")
            conexion = False
    # 4. Señal: ritmo, energía, inicio, intro, outro, mezcla
    a = analisis.analizar(path, ffmpeg) if ffmpeg else None
    if not ffmpeg:
        avisos.append("ffmpeg no está instalado: no se pueden analizar el ritmo ni las marcas.")
    elif not a:
        avisos.append("No se pudo analizar el audio (¿archivo dañado o muy corto?).")
    else:
        if a["bpm"] and "bpm" not in prop:
            poner("bpm", a["bpm"], "Análisis del ritmo", a["bpm_confianza"] or "baja")
        bpm = prop.get("bpm", (a["bpm"],))[0]
        if bpm:
            conf = prop["bpm"][2] if "bpm" in prop else "baja"
            poner("energia", analisis.energia_de_bpm(bpm), f"Según el ritmo ({bpm} BPM)", "media" if conf in ("alta", "media") else "baja")
        poner("cue_inicio", a["cue_inicio"], "Análisis del volumen", "alta")
        poner("cue_mezcla", a["cue_mezcla"], "Análisis del volumen", "media")
        poner("cue_intro", a["cue_intro"], "Estimación: dónde entra la parte fuerte (ajústalo de oído)", "baja")
        poner("cue_outro", a["cue_outro"], "Estimación: dónde empieza el final (ajústalo de oído)", "baja")

    campos = []
    for campo, (valor, fuente, conf) in prop.items():
        act = actual.get(campo)
        campos.append({
            "campo": campo, "etiqueta": ETIQUETAS[campo], "actual": act, "sugerido": valor,
            "texto": NOMBRE_ENERGIA.get(valor, str(valor)) if campo == "energia" else None,
            "mostrar": not _igual(campo, act, valor),          # igual a lo que ya tiene: no hace falta proponerlo
            "fuente": fuente, "confianza": conf,
        })
    orden = list(ETIQUETAS)
    campos.sort(key=lambda c: orden.index(c["campo"]))
    return {"campos": campos, "avisos": avisos, "en_linea": conexion}
