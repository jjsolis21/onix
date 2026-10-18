"""
loudness.py — Ónix FM
Medición de sonoridad (EBU R128 / LUFS) para normalizar el volumen.

Cada audio se mide UNA vez con ffmpeg (filtro ebur128) y se guarda en la BD:
    audios.loudness_lufs  → sonoridad integrada (LUFS)
    audios.pico_db        → pico verdadero (dBTP)
    audios.loudness_estado → 'ok' | 'error' (NULL = pendiente de medir)

El motor calcula la ganancia al poner el audio al aire:
    ganancia = objetivo (config normalizacion_lufs) - loudness_lufs
    limitada para que el pico no pase de -1 dBTP y como mucho +6 dB.
Así cambiar el objetivo no obliga a volver a medir la biblioteca.

ffmpeg debe estar instalado (en Windows: `winget install Gyan.FFmpeg`) o
indicado con ONIX_FFMPEG. Sin ffmpeg los audios suenan sin normalizar.

NormalizadorWorker mide en segundo plano los audios pendientes, para que
subir un archivo no espere al análisis y la biblioteca existente se
complete sola.
"""

import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("onix.loudness")

_ROOT = Path(__file__).resolve().parent.parent.parent

_RE_I    = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.MULTILINE)
_RE_PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.MULTILINE)

# Por debajo de esto el audio es prácticamente silencio: no se normaliza.
LUFS_MINIMO = -70.0


def find_ffmpeg() -> Optional[str]:
    """ONIX_FFMPEG > ffmpeg en la carpeta del proyecto > ffmpeg del PATH."""
    env = os.getenv("ONIX_FFMPEG")
    if env and Path(env).is_file():
        return env
    for cand in (_ROOT / "ffmpeg.exe", _ROOT / "bin" / "ffmpeg.exe",
                 _ROOT / "ffmpeg", _ROOT / "bin" / "ffmpeg"):
        if cand.is_file():
            return str(cand)
    return shutil.which("ffmpeg")


def medir(path, ffmpeg: Optional[str] = None, timeout: float = 300) -> Optional[dict]:
    """
    Mide un archivo. Devuelve {"lufs": float, "pico_db": float|None} o None
    si no se pudo (sin ffmpeg, archivo ilegible o silencio).
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-i", str(path),
             "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("[Loudness] ffmpeg falló con %s: %s", path, exc)
        return None

    salida = proc.stderr
    # El resumen está al final; los valores por bloque también empiezan con "I:".
    resumen = salida[salida.rfind("Summary:"):] if "Summary:" in salida else ""
    m_i = _RE_I.search(resumen)
    if proc.returncode != 0 or not m_i or m_i.group(1) == "-inf":
        log.warning("[Loudness] Sin medida para %s (código %s)", path, proc.returncode)
        return None
    lufs = float(m_i.group(1))
    if lufs <= LUFS_MINIMO:
        return None
    m_p = _RE_PEAK.search(resumen)
    pico = float(m_p.group(1)) if m_p and m_p.group(1) != "-inf" else None
    return {"lufs": lufs, "pico_db": pico}


def ganancia_db(lufs: Optional[float], pico_db: Optional[float], objetivo: float,
                max_boost: float = 6.0, techo_pico: float = -1.0) -> float:
    """Ganancia a aplicar para llevar `lufs` al objetivo sin pasar el techo de pico."""
    if lufs is None:
        return 0.0
    g = objetivo - lufs
    if pico_db is not None:
        g = min(g, techo_pico - pico_db)
    return max(-30.0, min(max_boost, g))


class NormalizadorWorker:
    """
    Hilo que mide los audios con loudness_estado NULL. Se despierta con
    avisar() (al subir un audio) o cada `intervalo` segundos.
    """

    def __init__(self, db_path, resolve: Callable[[str], Path], intervalo: float = 60.0):
        self.db_path   = str(db_path)
        self.resolve   = resolve
        self.intervalo = intervalo
        self.ffmpeg    = find_ffmpeg()
        self._evento   = threading.Event()
        self._running  = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if not self.ffmpeg:
            log.warning("[Loudness] ffmpeg no encontrado: los audios no se normalizan. "
                        "Instálalo (winget install Gyan.FFmpeg) o define ONIX_FFMPEG.")
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="Normalizador", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._evento.set()
        if self._thread:
            self._thread.join(timeout=5)

    def avisar(self):
        self._evento.set()

    def _loop(self):
        while self._running:
            try:
                self.procesar_pendientes()
            except Exception as exc:
                log.error("[Loudness] Error en el normalizador: %s", exc)
            try:
                # Misma pasada: marcas automáticas de la música (inicio y mezcla)
                if self.ffmpeg:
                    from backend.audio import marcas
                    marcas.procesar_pendientes(self.db_path, self.resolve, self.ffmpeg,
                                               seguir=lambda: self._running)
            except Exception as exc:
                log.error("[Marcas] Error detectando marcas: %s", exc)
            self._evento.wait(self.intervalo)
            self._evento.clear()

    def procesar_pendientes(self, limite: int = 0) -> int:
        """Mide los pendientes (todos, o hasta `limite`). Devuelve cuántos procesó."""
        if not self.ffmpeg:
            return 0
        from backend.database import get_connection
        hechos = 0
        while True:
            if self._thread and not self._running:   # apagando
                break
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT id, archivo_path FROM audios"
                    " WHERE activo = 1 AND loudness_estado IS NULL ORDER BY id LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            if not row:
                break
            audio_id, archivo = row
            path = self.resolve(archivo)
            res = medir(path, self.ffmpeg) if path.is_file() else None
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE audios SET loudness_lufs = ?, pico_db = ?, loudness_estado = ?"
                    " WHERE id = ?",
                    (res["lufs"] if res else None, res["pico_db"] if res else None,
                     "ok" if res else "error", audio_id),
                )
                conn.commit()
            finally:
                conn.close()
            if res:
                log.info("[Loudness] audio id=%d: %.1f LUFS, pico %s dBTP",
                         audio_id, res["lufs"], res["pico_db"])
            hechos += 1
            if limite and hechos >= limite:
                break
        return hechos
