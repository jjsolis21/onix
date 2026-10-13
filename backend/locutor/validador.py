"""Segunda barrera de "cero datos inventados": revisa el guion ya escrito.

Heurística: todo nombre propio (palabra con mayúscula que no abre frase) y toda cifra
deben aparecer en el texto permitido (metadatos, emisora, DJ, patrocinio, datos verificados).
"""
import re
import unicodedata

ETIQUETAS_VALIDAS = {"excited", "emphasis", "break"}
_ETIQUETA = re.compile(r"\[([^\]]*)\]")
_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+")
# Mayúsculas que no son datos: tratamientos y gentilicios habituales
_NEUTRAS = {"venezuela", "dios", "fm", "am", "dj"}
_SIGLAS = {
    "eeuu": "Estados Unidos", "eua": "Estados Unidos", "usa": "Estados Unidos", "onu": "Naciones Unidas",
    "ue": "Unión Europea", "oea": "Organización de Estados Americanos", "bcv": "Banco Central de Venezuela",
    "pdvsa": "Petróleos de Venezuela", "fanb": "Fuerza Armada Nacional Bolivariana",
    "tsj": "Tribunal Supremo de Justicia", "cne": "Consejo Nacional Electoral", "an": "Asamblea Nacional",
    "oms": "Organización Mundial de la Salud", "fifa": "Federación Internacional de Fútbol",
    "mlb": "Grandes Ligas", "lvbp": "Liga Venezolana de Béisbol Profesional", "ucab": "Universidad Católica Andrés Bello",
}


def _norm(palabra: str) -> str:
    sin = unicodedata.normalize("NFD", palabra.lower())
    return "".join(c for c in sin if unicodedata.category(c) != "Mn")


def validar_guion(texto: str, permitido: str, prohibidos: list[str] | None = None) -> list[str]:
    problemas = []
    for etiqueta in _ETIQUETA.findall(texto):
        if etiqueta.strip().lower() not in ETIQUETAS_VALIDAS:
            problemas.append(f"etiqueta no permitida: [{etiqueta}]")
    limpio = _ETIQUETA.sub(" ", texto)

    bajo = _norm(limpio)
    for frase in prohibidos or []:
        if frase.strip() and _norm(frase.strip()) in bajo:
            problemas.append(f"frase prohibida: {frase}")

    vocabulario = {_norm(p) for p in _PALABRA.findall(permitido)} | _NEUTRAS
    # Siglas del texto fuente que el locutor debe decir desplegadas ("EEUU" -> "Estados Unidos")
    siglas = {_norm(p).replace(".", "") for p in re.findall(r"[A-Za-zÁÉÍÓÚÑ.]+", permitido)}
    compacto = _norm(permitido).replace(".", "").replace(" ", "")
    for sigla, desplegada in _SIGLAS.items():
        if sigla in siglas or (len(sigla) > 3 and sigla in compacto):
            vocabulario |= {_norm(w) for w in desplegada.split()}
    for frase in re.split(r"[.!?¡¿…\n]+", limpio):
        palabras = _PALABRA.findall(frase)
        for i, palabra in enumerate(palabras):
            es_cifra = any(c.isdigit() for c in palabra)
            es_propio = i > 0 and palabra[0].isupper()
            if (es_cifra or es_propio) and _norm(palabra) not in vocabulario:
                problemas.append(f"dato fuera de los metadatos: {palabra}")
    return problemas
