"""Limpia los metadatos de una pista antes de dárselos al DJ.

Las bibliotecas reales traen basura ("converto.io - Veneka (Video Oficial)", "BPM 117 PAPACITO [Editions 2025]").
Si después de limpiar no queda un artista y un título confiables, devuelve None y el DJ no nombra la canción.
"""
import re

_GRUPOS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_DOMINIO = re.compile(r"\b[\w-]+\.(?:io|com|net|org|cc|co|fm|me|tv|biz|info)\b", re.I)
_RUIDO = re.compile(
    r"\b(?:bpm\s*\d+|\d+\s*bpm|official\s+(?:music\s+)?video|video\s+oficial|audio\s+oficial|video\s*clip|"
    r"lyrics?(?:\s+video)?|con\s+letra|letra|extended|remix|intro|outro|clean|dirty|hd|hq|4k|mp3|wav)\b", re.I)
# Señales de que el campo no es un artista de verdad
_NO_ARTISTA = re.compile(r"\b(?:editions?|records?|dj\s*pool|remix(?:es)?|varios|various|unknown|desconocido|"
                         r"jingle|cu[ñn]a|promo|spot|track\s*\d+)\b", re.I)
_NO_TITULO = re.compile(r"\b(?:jingle|cu[ñn]a|promo|spot|track\s*\d+|[eé]xitos|mix|megamix|mixtape|enganchados?)\b", re.I)  # mezclas: no son una canción


def _limpiar(texto: str) -> str:
    texto = _GRUPOS.sub(" ", texto or "")
    texto = _DOMINIO.sub(" ", texto)
    texto = _RUIDO.sub(" ", texto)
    texto = re.sub(r"[_|]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"^[\s\-–—.,:]+|[\s\-–—.,:]+$", "", texto)
    if texto.isupper() or texto.islower():
        texto = texto.title()
    return texto


def limpiar_pista(artista: str | None, titulo: str | None) -> dict | None:
    if _NO_ARTISTA.search(artista or "") or _NO_TITULO.search(titulo or ""):
        return None
    artista, titulo = _limpiar(artista or ""), _limpiar(titulo or "")
    if " - " in titulo:  # resto de un nombre de archivo: no se sabe qué parte es el título
        return None
    for campo in (artista, titulo):
        if not (2 <= len(campo) <= 60) or not re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2}", campo):
            return None
    return {"artista": artista, "titulo": titulo}
