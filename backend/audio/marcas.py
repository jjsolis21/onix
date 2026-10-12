"""
marcas.py — Ónix FM
Detección automática de marcas al cargar canciones (como Jazler SoHo).

QUÉ DETECTA
    · cue_inicio: dónde empieza el sonido (se salta el silencio del principio).
    · cue_mezcla: dónde conviene que entre la siguiente canción: el punto en
      que el final cae 12 dB por debajo del volumen normal de la canción.
      Sirve para fade-outs (entra mientras se apaga) y para finales secos
      (entra justo al terminar, sin el silencio de cola).
QUÉ NO DETECTA
    · El intro (cuándo entra la voz): separar la voz de la música falla
      demasiado; se sigue marcando a mano en el editor de onda.

CÓMO
    ffmpeg decodifica a mono 2 kHz (rápido: la forma del volumen no
    necesita más) y se mide el nivel cada 50 ms, en Python puro (sin numpy).

REGLAS DE APLICACIÓN (audios.marcas_auto)
    NULL → sin analizar · 1 → marcas automáticas puestas · 0 → el usuario
    marcó a mano (no se tocan) · -1 → no se pudo analizar.
    Solo música, solo si el audio no tenía inicio ni mezcla marcados, y solo
    con la opción `marcas_auto` activada (Admin › Motor de audio).
"""

import logging
import math
import sqlite3
import subprocess
from array import array
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("Marcas")

TASA = 2000                  # Hz
VENTANA = 0.05               # s
UMBRAL_SONIDO_DB = -40.0     # por debajo: silencio
CAIDA_MEZCLA_DB = 12.0       # cuánto por debajo del volumen normal se marca la mezcla
SUAVIZADO_S = 1.0            # media móvil para que un golpe suelto no decida


def _niveles(path, ffmpeg: str, timeout: float = 180) -> Optional[list[float]]:
    """Nivel (dBFS) de cada ventana de 50 ms, o None si no se pudo decodificar."""
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-v", "error", "-i", str(path),
             "-ac", "1", "-ar", str(TASA), "-f", "s16le", "-"],
            capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("[Marcas] ffmpeg falló con %s: %s", path, exc)
        return None
    if proc.returncode != 0 or len(proc.stdout) < TASA:
        return None
    muestras = array("h")
    muestras.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    n = int(TASA * VENTANA)
    niveles = []
    for i in range(0, len(muestras) - n + 1, n):
        trozo = muestras[i:i + n]
        rms = math.sqrt(sum(x * x for x in trozo) / n) / 32768.0
        niveles.append(20 * math.log10(rms) if rms > 1e-6 else -120.0)
    return niveles


def calcular(niveles: list[float]) -> Optional[dict]:
    """Marcas a partir de los niveles (separado para poder probarlo sin audio)."""
    con_sonido = [i for i, db in enumerate(niveles) if db > UMBRAL_SONIDO_DB]
    if len(con_sonido) * VENTANA < 5:               # menos de 5 s con sonido: nada fiable
        return None
    primero, ultimo = con_sonido[0], con_sonido[-1]
    dur_sonido = (ultimo - primero) * VENTANA
    # Volumen normal: percentil 70 de lo que suena (la parte "fuerte" típica)
    orden = sorted(niveles[primero:ultimo + 1])
    normal = orden[int(len(orden) * 0.7)]
    umbral = normal - CAIDA_MEZCLA_DB
    k = max(1, int(SUAVIZADO_S / VENTANA))
    mezcla_i = ultimo
    for i in range(ultimo, primero, -1):            # desde el final hacia atrás
        tramo = niveles[max(primero, i - k + 1):i + 1]
        if sum(tramo) / len(tramo) >= umbral:
            mezcla_i = i
            break
    inicio = max(0.0, primero * VENTANA - 0.05)
    mezcla = (mezcla_i + 1) * VENTANA
    # Una mezcla antes de la mitad de la canción es casi seguro un error
    # (p. ej. un tema que baja mucho al final por diseño): no se marca.
    if mezcla - inicio < dur_sonido * 0.5:
        mezcla = None
    return {"cue_inicio": round(inicio, 2) if inicio >= 0.15 else 0.0,
            "cue_mezcla": round(mezcla, 2) if mezcla else None,
            "fin_sonido": round((ultimo + 1) * VENTANA, 2), "volumen_normal_db": round(normal, 1)}


def detectar(path, ffmpeg: str) -> Optional[dict]:
    niveles = _niveles(path, ffmpeg)
    return calcular(niveles) if niveles else None


def procesar_pendientes(db_path: str, resolve: Callable[[str], Path], ffmpeg: str,
                        seguir: Callable[[], bool] = lambda: True, limite: int = 0) -> int:
    """Analiza la música sin marcas automáticas. Devuelve cuántos audios procesó."""
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        r = conn.execute("SELECT valor FROM config WHERE clave = 'marcas_auto'").fetchone()
    finally:
        conn.close()
    if r and str(r[0]).strip() in ("0", "false", ""):
        return 0
    hechos = 0
    while seguir():
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT id, archivo_path, cue_inicio, cue_mezcla FROM audios"
                " WHERE activo = 1 AND tipo_audio = 'Musica' AND marcas_auto IS NULL"
                " ORDER BY id LIMIT 1").fetchone()
            if not row:
                break
            audio_id, archivo, cue_in, cue_mix = row
            if (cue_in or 0) > 0 or (cue_mix or 0) > 0:
                # Ya tenía marcas puestas a mano: se respetan
                conn.execute("UPDATE audios SET marcas_auto = 0 WHERE id = ?", (audio_id,))
                conn.commit()
                continue
        finally:
            conn.close()
        path = resolve(archivo)
        res = detectar(path, ffmpeg) if path.is_file() else None
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            if res:
                conn.execute(
                    "UPDATE audios SET cue_inicio = ?, cue_mezcla = ?, marcas_auto = 1"
                    " WHERE id = ? AND marcas_auto IS NULL",
                    (res["cue_inicio"], res["cue_mezcla"], audio_id))
                log.info("[Marcas] audio id=%d: inicio %.2fs, mezcla %s", audio_id,
                         res["cue_inicio"], f"{res['cue_mezcla']:.2f}s" if res["cue_mezcla"] else "—")
            else:
                conn.execute("UPDATE audios SET marcas_auto = -1 WHERE id = ?", (audio_id,))
            conn.commit()
        finally:
            conn.close()
        hechos += 1
        if limite and hechos >= limite:
            break
    return hechos
