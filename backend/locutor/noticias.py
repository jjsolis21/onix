"""
noticias.py — Locutor IA de Ónix FM
Lee las fuentes RSS/Atom registradas y guarda los titulares nuevos. El locutor solo puede leer lo que está en
estas filas (titular y entradilla de la fuente): el contenido de un RSS es texto de terceros, material a
resumir y nunca instrucciones. Solo biblioteca estándar.
"""

import html
import re
from backend.database import MariaDBConnection
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Optional
from xml.etree import ElementTree

REVISAR_CADA_MIN = 10
MAX_BYTES = 2_000_000
MAX_RESUMEN = 600
_ETIQUETA = re.compile(r"<[^>]+>")

# Los RSS de los diarios mezclan esquelas y avisos con las noticias: eso nunca se lee al aire.
VETOS_FIJOS = ("condolencias", "nos unimos al dolor", "q.e.p.d", "obituario", "profundo pesar", "sepelio",
               "publirreportaje", "contenido patrocinado")


class ErrorFuente(Exception):
    pass


def _texto(valor: Optional[str], tope: int) -> str:
    limpio = html.unescape(_ETIQUETA.sub(" ", html.unescape(valor or "")))
    limpio = re.sub(r"\s+", " ", limpio).strip()
    if len(limpio) > tope:
        limpio = limpio[:tope].rsplit(" ", 1)[0] + "…"
    return limpio


def _fecha(valor: Optional[str]) -> Optional[str]:
    if not valor:
        return None
    try:
        momento = parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        try:
            momento = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _descargar(url: str) -> bytes:
    if not url.lower().startswith(("http://", "https://")):
        raise ErrorFuente("La dirección debe empezar por http:// o https://")
    req = urllib.request.Request(url, headers={"User-Agent": "OnixFM-Locutor/1.0 (lector RSS)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            datos = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise ErrorFuente(f"La fuente respondió HTTP {e.code}")
    except Exception as e:
        raise ErrorFuente(f"No se pudo descargar: {e}")
    if len(datos) > MAX_BYTES:
        raise ErrorFuente("La fuente es demasiado grande")
    return datos


def parsear(contenido: bytes) -> list:
    """RSS 2.0 o Atom → [{guid, titulo, resumen, enlace, publicada_en (UTC)}]."""
    # El módulo estándar no frena entidades que se expanden sin fin: un RSS no las necesita.
    if b"<!ENTITY" in contenido:
        raise ErrorFuente("No es un RSS/Atom válido: trae definiciones de entidades")
    try:
        raiz = ElementTree.fromstring(contenido)
    except ElementTree.ParseError as e:
        raise ErrorFuente(f"No es un RSS/Atom válido: {e}")

    def local(elem) -> str:
        return elem.tag.rsplit("}", 1)[-1].lower()

    entradas = []
    for nodo in raiz.iter():
        if local(nodo) not in ("item", "entry"):
            continue
        campos = {}
        for hijo in nodo:
            nombre = local(hijo)
            if nombre == "link" and hijo.get("href") and "link" not in campos:
                campos["link"] = hijo.get("href")
            elif nombre not in campos and (hijo.text or "").strip():
                campos[nombre] = hijo.text.strip()
        titulo = _texto(campos.get("title"), 200)
        if not titulo:
            continue
        enlace = campos.get("link", "")
        entradas.append({
            "guid": (campos.get("guid") or campos.get("id") or enlace or titulo)[:500],
            "titulo": titulo,
            "resumen": _texto(campos.get("description") or campos.get("summary") or campos.get("content"), MAX_RESUMEN),
            "enlace": enlace[:500],
            "publicada_en": _fecha(campos.get("pubdate") or campos.get("published") or campos.get("updated")
                                   or campos.get("date")),
        })
    return entradas


def leer_fuente(url: str) -> list:
    return parsear(_descargar(url))


def vetada(entrada: dict, vetos: list) -> bool:
    texto = f"{entrada['titulo']} {entrada['resumen']}".lower()
    return any(v.strip().lower() in texto for v in (*VETOS_FIJOS, *vetos) if v.strip())


def actualizar(c: MariaDBConnection, vetos: Optional[list] = None, forzar: bool = False,
               leer: Callable = leer_fuente) -> int:
    """Revisa las fuentes a las que ya les toca. Devuelve cuántas noticias nuevas entraron."""
    nuevas = 0
    filtro = "" if forzar else (f" AND (revisada_en IS NULL OR revisada_en < DATE_SUB(NOW(), INTERVAL {REVISAR_CADA_MIN} MINUTE))")
    for f in c.execute(f"SELECT * FROM locutor_fuentes WHERE activo = 1{filtro}").fetchall():
        try:
            entradas = leer(f["url"])
        except ErrorFuente as e:
            c.execute("UPDATE locutor_fuentes SET revisada_en = NOW(), error = ? WHERE id = ?",
                      (str(e)[:300], f["id"]))
            continue
        for e in entradas:
            estado = "descartada" if vetada(e, vetos or []) else "nueva"
            cur = c.execute(
                "INSERT IGNORE INTO locutor_noticias (fuente_id, guid, titulo, resumen, enlace, publicada_en, estado)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f["id"], e["guid"], e["titulo"], e["resumen"], e["enlace"], e["publicada_en"], estado))
            if cur.rowcount and estado == "nueva":
                nuevas += 1
        c.execute("UPDATE locutor_fuentes SET revisada_en = NOW(), error = NULL WHERE id = ?", (f["id"],))
    c.commit()
    return nuevas


def siguiente(c: MariaDBConnection, max_horas: int = 18) -> Optional[dict]:
    """La noticia nueva que toca: primero la que el dueño priorizó, luego la más reciente aún vigente."""
    return c.execute(
        "SELECT n.*, f.nombre AS fuente, f.cierre AS cierre FROM locutor_noticias n"
        " LEFT JOIN locutor_fuentes f ON f.id = n.fuente_id"
        " WHERE n.estado = 'nueva' AND COALESCE(f.activo, 1) = 1"
        " AND (n.prioridad > 0 OR TIMESTAMPDIFF(SECOND, COALESCE(n.publicada_en, n.creado_en), NOW()) / 3600.0 <= ?)"
        " ORDER BY n.prioridad DESC, COALESCE(n.publicada_en, n.creado_en) DESC LIMIT 1",
        (max_horas,)).fetchone()


def titulares_leidos(c: MariaDBConnection, horas: int = 24) -> list:
    return [r[0] for r in c.execute(
        "SELECT titulo FROM locutor_noticias WHERE estado = 'leida'"
        " AND TIMESTAMPDIFF(SECOND, creado_en, NOW()) / 3600.0 <= ?", (horas,))]


_SIN_PESO = {"para", "este", "esta", "desde", "hasta", "sobre", "entre", "tras", "ante", "como", "pero", "según",
             "segun", "años", "anos", "nuevo", "nueva", "tiene", "será", "sera", "hace", "dice"}


def _claves(titulo: str) -> set:
    plano = "".join(ch for ch in html.unescape(titulo).lower() if ch.isalnum() or ch.isspace())
    return {p for p in plano.split() if len(p) > 3 and p not in _SIN_PESO}


def es_repetida(titulo: str, ya_leidos: list) -> bool:
    """¿Es la misma historia que otra ya leída, aunque venga de otro medio con otro titular?"""
    nuevas = _claves(titulo)
    if len(nuevas) < 3:
        return False
    for leido in ya_leidos:
        otras = _claves(leido)
        if otras and len(nuevas & otras) / min(len(nuevas), len(otras)) >= 0.6:
            return True
    return False
