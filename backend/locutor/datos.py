"""
datos.py — Locutor IA de Ónix FM
Datos verificados de artistas y canciones, para que el locutor presente con contenido («Danny Ocean, cantante
venezolano… "Me rehúso" se lanzó en dos mil diecisiete») sin inventar nada.

Regla: la IA solo puede decir lo que esté guardado aquí. Cada dato se busca UNA vez (Wikipedia en español para el
artista, MusicBrainz para el año de la canción), se guarda en `locutor_datos` y queda a la vista para corregirlo
o escribirlo a mano. Si la fuente no da una coincidencia clara no se guarda nada: el locutor hace la entrada corta.
"""

import json
import re
from backend.database import MariaDBConnection
import unicodedata
import urllib.parse
import urllib.request
from typing import Callable, Optional

UA = {"User-Agent": "OnixFM-Locutor/1.0 (datos de artistas para presentacion al aire)"}
TIEMPO = 10
MAX_DATO = 320
REINTENTAR_DIAS = 30            # un «sin datos» se vuelve a buscar pasado este tiempo
# El resumen debe hablar de música: así «Cinco Días» (un diario) no pasa por el artista «A.Cinco»
_MUSICA = re.compile(r"\b(cantante|cantautor|cantautora|banda|grupo|agrupaci[oó]n|m[uú]sico|m[uú]sica|compositor|"
                     r"compositora|rapero|rapera|d[uú]o|orquesta|dj|disc jockey|productor musical|int[eé]rprete|"
                     r"merengue|salsa|reguet[oó]n|vallenato|bachata)\b", re.I)
_PAREN = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def norm(texto: str) -> str:
    """Clave de comparación: sin tildes, sin signos, en minúsculas."""
    sin = unicodedata.normalize("NFD", (texto or "").lower())
    sin = "".join(ch for ch in sin if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", sin).strip()


def artista_principal(artista: str) -> str:
    """«Karol G ft Feid», «A & B», «A x B» → el primero (de quien se busca el dato)."""
    return re.split(r"\s+(?:ft\.?|feat\.?|featuring|con|x|y|&|vs\.?)\s+|,|;|/", artista or "", maxsplit=1, flags=re.I)[0].strip()


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIEMPO) as r:
        return json.loads(r.read().decode("utf-8"))


def _recortar(texto: str) -> str:
    """Una o dos frases, sin paréntesis (fechas y lugares de nacimiento que al aire estorban)."""
    limpio = " ".join(_PAREN.sub("", texto or "").split())
    frases = re.split(r"(?<=[.!?])\s+", limpio)
    out = ""
    for f in frases[:2]:
        if len(out) + len(f) + 1 > MAX_DATO:
            break
        out = (out + " " + f).strip()
    return out or limpio[:MAX_DATO].rsplit(" ", 1)[0]


def buscar_artista(artista: str, get: Optional[Callable] = None) -> Optional[str]:
    """Quién es el artista, según Wikipedia en español. None si no hay una página que sea claramente suya."""
    get = get or _get_json
    nombre = artista_principal(artista)
    clave = norm(nombre)
    if len(clave) < 2:
        return None
    s = get("https://es.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": nombre, "format": "json", "srlimit": 3}))
    for hit in (s.get("query") or {}).get("search") or []:
        titulo = hit.get("title", "")
        if norm(_PAREN.sub("", titulo)) != clave:          # el título de la página debe ser el artista
            continue
        d = get("https://es.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(titulo.replace(" ", "_")))
        extracto = d.get("extract") or ""
        if d.get("type") != "standard" or not _MUSICA.search(f"{d.get('description') or ''} {extracto}"):
            continue
        return _recortar(extracto) or None
    return None


def buscar_anio(artista: str, titulo: str, get: Optional[Callable] = None) -> Optional[int]:
    """Año del primer lanzamiento de la canción, según MusicBrainz. None si no hay coincidencia clara."""
    get = get or _get_json
    a, t = artista_principal(artista), _PAREN.sub("", titulo or "").strip()
    if len(norm(a)) < 2 or len(norm(t)) < 2:
        return None
    q = f'recording:"{t}" AND artist:"{a}"'
    d = get("https://musicbrainz.org/ws/2/recording/?" + urllib.parse.urlencode({"query": q, "fmt": "json", "limit": 8}))
    anios = []
    for r in d.get("recordings") or []:
        if int(r.get("score") or 0) < 95 or norm(r.get("title", "")) != norm(t):
            continue
        creditos = " ".join(norm(c.get("name", "")) for c in r.get("artist-credit") or [])
        if norm(a) not in creditos:
            continue
        m = re.match(r"(\d{4})", r.get("first-release-date") or "")
        if m:
            anios.append(int(m.group(1)))
    return min(anios) if anios else None


# ── Caché en la base ────────────────────────────────────────────────────────

def guardar(c: MariaDBConnection, ambito: str, clave: str, etiqueta: str, dato: str, fuente: str,
            estado: str = "ok") -> None:
    c.execute(
        "INSERT INTO locutor_datos (ambito, clave, etiqueta, dato, fuente, estado) VALUES (?, ?, ?, ?, ?, ?)"
        " ON DUPLICATE KEY UPDATE etiqueta = VALUES(etiqueta), dato = VALUES(dato),"
        " fuente = VALUES(fuente), estado = VALUES(estado), revisado_en = NOW()",
        (ambito, clave, etiqueta, dato, fuente, estado))
    c.commit()


def _vigente(fila) -> bool:
    """¿Sirve lo guardado? Un dato (o un descarte a mano) sí; un «sin datos» solo durante REINTENTAR_DIAS."""
    if fila is None:
        return False
    return fila["estado"] != "sin_datos" or bool(fila["reciente"])


def datos_para(c: MariaDBConnection, artista: str, titulo: str, anio_biblioteca: Optional[str] = None,
               buscar: bool = True, get: Optional[Callable] = None) -> list:
    """
    Frases verificadas sobre esa canción y su artista (0, 1 o 2). Busca en internet solo lo que aún no está
    guardado; lo que la emisora escribió o descartó a mano se respeta siempre.
    """
    out = []
    a_clave = norm(artista_principal(artista))
    c_clave = f"{a_clave}|{norm(_PAREN.sub('', titulo or ''))}"
    reciente = f"(revisado_en >= DATE_SUB(NOW(), INTERVAL {REINTENTAR_DIAS} DAY)) AS reciente"

    def leer(ambito, clave):
        return c.execute(f"SELECT *, {reciente} FROM locutor_datos WHERE ambito = ? AND clave = ?",
                         (ambito, clave)).fetchone()

    if a_clave:
        f = leer("artista", a_clave)
        if not _vigente(f) and buscar:
            try:
                dato = buscar_artista(artista, get)
                guardar(c, "artista", a_clave, artista_principal(artista), dato or "", "wikipedia",
                        "ok" if dato else "sin_datos")
            except Exception:                         # red caída: se intentará en otra ocasión
                pass
            f = leer("artista", a_clave)
        if f is not None and f["estado"] == "ok" and f["dato"]:
            out.append(f["dato"])

    if a_clave and c_clave.split("|")[1]:
        f = leer("cancion", c_clave)
        if not _vigente(f) and buscar:
            etiqueta = f"{artista_principal(artista)} - {titulo}"
            m = re.search(r"(19|20)\d{2}", str(anio_biblioteca or ""))
            try:
                anio, fuente = (int(m.group(0)), "biblioteca") if m else (buscar_anio(artista, titulo, get), "musicbrainz")
                guardar(c, "cancion", c_clave, etiqueta,
                        f"La canción «{titulo}» se lanzó en el año {anio}." if anio else "", fuente,
                        "ok" if anio else "sin_datos")
            except Exception:
                pass
            f = leer("cancion", c_clave)
        if f is not None and f["estado"] == "ok" and f["dato"]:
            out.append(f["dato"])
    return out
