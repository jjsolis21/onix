"""Saludos y complacencias: filtro automático de lo que escribe un oyente.

El texto de un oyente es contenido de terceros: nunca se lee sin pasar este filtro, y el locutor lo trata
como material a leer, no como instrucciones.
"""
import re
import unicodedata

MAX_TEXTO = 300
_GROSERIAS = ("mierda", "coño", "puta", "puto", "marico", "maldito", "mamaguevo", "güevon", "guevon", "verga",
              "pendejo", "carajo", "hijo de", "mmg")
_POLITICA = ("chavista", "opositor", "dictador", "dictadura", "maduro", "guaido", "elecciones", "golpe de estado")
_ENLACE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|net|org|ve|io|xyz|info)\b", re.I)
_TELEFONO = re.compile(r"(?:\d[\s.-]?){7,}")


def _norm(texto: str) -> str:
    sin = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin if unicodedata.category(c) != "Mn")


def revisar(campos: list[str], prohibidos: list[str] | None = None) -> str | None:
    """Devuelve el motivo del rechazo, o None si el mensaje se puede leer al aire."""
    texto = " ".join(c for c in campos if c)
    if len(texto) > MAX_TEXTO + 200:
        return "Mensaje demasiado largo"
    if _ENLACE.search(texto):
        return "Contiene un enlace o dirección web"
    if _TELEFONO.search(texto):
        return "Contiene un número de teléfono"
    plano = _norm(texto)
    for lista, motivo in ((_GROSERIAS, "Lenguaje ofensivo"), (_POLITICA, "Contenido político")):
        if any(re.search(r"(?<![a-z])" + re.escape(_norm(p)) + r"(?![a-z])", plano) for p in lista):
            return motivo
    for frase in prohibidos or []:
        if frase.strip() and _norm(frase.strip()) in plano:
            return f"Contiene una frase vetada por la emisora: {frase}"
    return None
