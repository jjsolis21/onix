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
