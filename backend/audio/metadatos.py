"""
metadatos.py — Ónix FM
Datos de la canción (título, artista, álbum, año) para SUGERIR al operador. Fuentes, de mayor a menor confianza:

    1. Etiquetas del archivo (ID3, Vorbis…).                       → sin internet
    2. AcoustID: reconoce la canción por su huella de audio       → internet + clave gratuita + huella
       (sirve aunque el archivo no traiga etiquetas ni nombre).      (ffmpeg con chromaprint o fpcalc)
    3. MusicBrainz: busca por artista y título (de las etiquetas   → internet, sin clave
       o del nombre del archivo) y completa álbum y año.

Nada se guarda aquí: devuelve propuestas y el editor deja que el operador elija.
Sin internet el análisis sigue con lo que hay en el equipo y lo avisa.
"""

import json
import logging
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from backend.version import VERSION

log = logging.getLogger("Metadatos")

UA = f"OnixFM/{VERSION} (automatizacion de radio; https://github.com/jjsolis21/onix)"
MB_URL = "https://musicbrainz.org/ws/2/recording/"
ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
TIMEOUT = 8


class SinConexion(Exception):
    pass


def http_json(url: str, timeout: float = TIMEOUT) -> dict:
    """GET que devuelve JSON. SinConexion si no hay red o el servicio no responde."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SinConexion(str(exc)) from exc


# ── 1. Etiquetas y nombre del archivo ───────────────────────────────────────

def desde_nombre(nombre: str) -> dict:
    base = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", nombre or "").replace("_", " ").strip()
    base = re.sub(r"^\d{1,3}[\s.\-]+", "", base)                      # "01 - ", "07. "
    base = re.sub(r"\s*[\(\[](official|video|lyrics?|letra|audio|hd|hq)[^\)\]]*[\)\]]", "", base, flags=re.I)
    if " - " in base:
        artista, titulo = base.split(" - ", 1)
        return {"artista": artista.strip(), "titulo": titulo.strip()}
    return {"artista": "", "titulo": base}


def desde_archivo(path) -> dict:
    """Etiquetas del archivo: titulo, artista, album, anio, bpm. Vacío si no hay."""
    res = {"titulo": "", "artista": "", "album": "", "anio": "", "bpm": None}
    try:
        import mutagen
        f = mutagen.File(str(path), easy=True)
        tags = (f.tags if f is not None else None) or {}

        def uno(k):
            v = tags.get(k)
            return (v[0] if isinstance(v, list) and v else (v or "")).strip() if v else ""
        res.update(titulo=uno("title"), artista=uno("artist") or uno("albumartist"), album=uno("album"))
        a = re.search(r"\d{4}", uno("date") or uno("year") or "")
        res["anio"] = a.group(0) if a else ""
        b = re.search(r"\d{2,3}", uno("bpm") or "")
        res["bpm"] = int(b.group(0)) if b and 40 <= int(b.group(0)) <= 300 else None
    except Exception as exc:
        log.debug("[Metadatos] sin etiquetas en %s: %s", path, exc)
    return res


# ── 3. MusicBrainz (texto) ──────────────────────────────────────────────────

def _escapar(s: str) -> str:
    return re.sub(r'(["\\])', r"\\\1", s)


def _mejor_grabacion(d: dict, minimo: int = 85) -> Optional[dict]:
    """De la respuesta de MusicBrainz o AcoustID, la grabación con mejor puntaje → título, artista, álbum, año."""
    mejor = None
    for rec in d.get("recordings", []):
        score = int(rec.get("score", 0))
        if score < minimo or (mejor and score <= mejor["score"]):
            continue
        creditos = rec.get("artist-credit") or []
        artista = "".join((c.get("name", "") + c.get("joinphrase", "")) for c in creditos if isinstance(c, dict)).strip()
        releases = rec.get("releases") or []
        # Un álbum "de verdad" antes que sencillos o recopilaciones
        def valor(r):
            g = r.get("release-group") or {}
            return (g.get("primary-type") == "Album" and not g.get("secondary-types"), r.get("status") == "Official")
        r0 = max(releases, key=valor) if releases else {}
        fecha = rec.get("first-release-date") or r0.get("date") or ""
        mejor = {"score": score, "titulo": rec.get("title", ""), "artista": artista,
                 "album": r0.get("title", ""),
                 "anio": fecha[:4] if re.match(r"\d{4}", fecha) else "", "mbid": rec.get("id", "")}
    return mejor


def buscar_musicbrainz(artista: str, titulo: str, http: Callable = http_json) -> Optional[dict]:
    if not titulo:
        return None
    q = f'recording:"{_escapar(titulo)}"' + (f' AND artist:"{_escapar(artista)}"' if artista else "")
    url = MB_URL + "?" + urllib.parse.urlencode({"query": q, "fmt": "json", "limit": 5})
    r = _mejor_grabacion(http(url))
    if r:
        r["fuente"] = "MusicBrainz"
        r["confianza"] = "alta" if r["score"] >= 97 and artista else "media"
    return r


# ── 2. AcoustID (huella) ────────────────────────────────────────────────────

def huella(path, ffmpeg: Optional[str]) -> Optional[tuple[int, str]]:
    """(duración en s, huella) con el chromaprint de ffmpeg o con fpcalc; None si no hay ninguno."""
    import shutil
    if ffmpeg:
        try:
            p = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-v", "error", "-t", "120", "-i", str(path),
                                "-f", "chromaprint", "-fp_format", "base64", "-"], capture_output=True, timeout=120)
            h = p.stdout.decode("ascii", "ignore").strip()
            if p.returncode == 0 and len(h) > 20:
                dur = 0
                try:
                    pr = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, timeout=30)
                    m = re.search(rb"Duration:\s*(\d+):(\d+):(\d+)", pr.stderr)
                    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else 0
                except Exception:
                    pass
                return (dur or 180), h
        except (OSError, subprocess.TimeoutExpired):
            pass
    fp = shutil.which("fpcalc") or shutil.which("fpcalc.exe")
    if fp:
        try:
            p = subprocess.run([fp, "-json", "-length", "120", str(path)], capture_output=True, timeout=120)
            j = json.loads(p.stdout.decode("utf-8", "ignore"))
            return int(j["duration"]), j["fingerprint"]
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            pass
    return None


def buscar_acoustid(duracion: int, h: str, clave: str, http: Callable = http_json) -> Optional[dict]:
    url = ACOUSTID_URL + "?" + urllib.parse.urlencode({
        "client": clave, "meta": "recordings releasegroups releases", "duration": duracion, "fingerprint": h, "format": "json"})
    d = http(url)
    if d.get("status") != "ok":
        return None
    mejor = None
    for res in d.get("results", []):
        score = float(res.get("score", 0))
        for rec in res.get("recordings", []):
            if mejor and score <= mejor["score"]:
                continue
            artista = ", ".join(a.get("name", "") for a in rec.get("artists", []))
            grupos = rec.get("releasegroups", [])
            alb = next((g for g in grupos if g.get("type") == "Album" and not g.get("secondarytypes")), grupos[0] if grupos else {})
            mejor = {"score": score, "titulo": rec.get("title", ""), "artista": artista, "album": alb.get("title", ""), "anio": ""}
    if mejor and mejor["score"] >= 0.85:
        mejor["fuente"] = "AcoustID (huella del audio)"
        mejor["confianza"] = "alta" if mejor["score"] >= 0.95 else "media"
        return mejor
    return None


# ── iTunes y Deezer (texto, gratis, sin clave) ──────────────────────────────
# Solo para el análisis de UNA canción (no para lotes). No reconocen el audio: buscan por artista y título.

ITUNES_URL = "https://itunes.apple.com/search"
DEEZER_URL = "https://api.deezer.com/search"
DEEZER_TRACK = "https://api.deezer.com/track/{}"
HOSTS_CARATULA = ("mzstatic.com", "dzcdn.net", "deezer.com")      # de dónde se acepta bajar una carátula


def _norm(s: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)                           # (feat. X) [Remastered]
    t = re.sub(r"\b(feat|ft|featuring)\b.*$", " ", t)
    t = re.sub(r"\s-\s(remaster|remastered|version|versi.n|live|en vivo|radio edit|single)\b.*$", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _parecido(a: str, b: str) -> float:
    import difflib
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b or a in b.split(" - ") or (len(a) > 4 and (a in b or b in a) and abs(len(a) - len(b)) <= 12):
        return 1.0 if a == b else 0.93
    return difflib.SequenceMatcher(None, a, b).ratio()


def _elegir(cands: list[tuple[str, str, dict]], artista: str, titulo: str) -> Optional[tuple[dict, float]]:
    """cands = [(título, artista, datos)] → el que mejor coincide con lo buscado, o None (se descarta lo dudoso)."""
    mejor, puntaje = None, 0.0
    for t, a, d in cands:
        pt = _parecido(t, titulo)
        pa = _parecido(a, artista) if artista else 1.0
        if pt >= 0.88 and pa >= 0.7 and pt + pa > puntaje:
            mejor, puntaje = d, pt + pa
    return (mejor, puntaje) if mejor else None


def buscar_itunes(artista: str, titulo: str, http: Callable = http_json) -> Optional[dict]:
    if not titulo:
        return None
    url = ITUNES_URL + "?" + urllib.parse.urlencode({"term": f"{artista} {titulo}".strip(), "media": "music", "entity": "song", "limit": 10})
    d = http(url)
    elegido = _elegir([(r.get("trackName", ""), r.get("artistName", ""), r) for r in d.get("results", [])], artista, titulo)
    if not elegido:
        return None
    r, punt = elegido
    album = r.get("collectionName", "") or ""
    if re.search(r"\s-\s(single|ep)$", album, re.I):
        album = ""                                                     # un sencillo no es el álbum de la canción
    fecha = r.get("releaseDate", "") or ""
    cover = (r.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
    return {"titulo": r.get("trackName", ""), "artista": r.get("artistName", ""), "album": album,
            "anio": fecha[:4] if re.match(r"\d{4}", fecha) else "", "caratula": cover, "fuente": "iTunes",
            "confianza": "alta" if punt >= 1.9 else "media"}


def buscar_deezer(artista: str, titulo: str, http: Callable = http_json) -> Optional[dict]:
    if not titulo:
        return None
    q = f'track:"{titulo}"' + (f' artist:"{artista}"' if artista else "")
    d = http(DEEZER_URL + "?" + urllib.parse.urlencode({"q": q, "limit": 10}))
    elegido = _elegir([(r.get("title", ""), (r.get("artist") or {}).get("name", ""), r) for r in d.get("data", [])], artista, titulo)
    if not elegido:
        return None
    r, punt = elegido
    bpm, fecha = None, ""
    try:
        t = http(DEEZER_TRACK.format(r["id"]))
        b = float(t.get("bpm") or 0)
        bpm = round(b) if 40 <= b <= 300 else None                     # Deezer pone 0 cuando no lo sabe
        fecha = t.get("release_date", "") or ""
    except (SinConexion, KeyError, ValueError, TypeError):
        pass
    alb = r.get("album") or {}
    return {"titulo": r.get("title", ""), "artista": (r.get("artist") or {}).get("name", ""), "album": alb.get("title", ""),
            "anio": fecha[:4] if re.match(r"\d{4}", fecha) else "", "bpm": bpm, "caratula": alb.get("cover_xl") or alb.get("cover_big") or "",
            "fuente": "Deezer", "confianza": "alta" if punt >= 1.9 else "media"}


class _SoloHostsPermitidos(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_permitido(newurl):
            raise urllib.error.URLError("redirección a un sitio no permitido")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def host_permitido(url: str) -> bool:
    p = urllib.parse.urlparse(url)
    h = (p.hostname or "").lower()
    return p.scheme == "https" and any(h == d or h.endswith("." + d) for d in HOSTS_CARATULA)


def descargar_imagen(url: str, maximo: int = 5 * 1024 * 1024, timeout: float = TIMEOUT) -> tuple[bytes, str]:
    """Baja una carátula SOLO de iTunes o Deezer (nunca de una dirección cualquiera). Devuelve (bytes, 'jpg'|'png')."""
    if not host_permitido(url):
        raise ValueError("Esa dirección no es de iTunes ni de Deezer")
    op = urllib.request.build_opener(_SoloHostsPermitidos())
    try:
        with op.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            datos = r.read(maximo + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise SinConexion(str(exc)) from exc
    if len(datos) > maximo:
        raise ValueError("La imagen es demasiado grande")
    if datos[:3] == b"\xff\xd8\xff":
        return datos, "jpg"
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        return datos, "png"
    raise ValueError("No es una imagen JPG o PNG")
