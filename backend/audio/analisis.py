"""
analisis.py — Ónix FM
Análisis de una canción para SUGERIR datos al operador (nunca se aplican solos):

    · BPM   → ritmo, con el que se propone la Energía (1 Lenta … 5 Rápida).
    · Inicio y mezcla (lo mismo que el AutoCue).
    · Intro y Outro: ESTIMACIONES con confianza baja. Separar la voz de la música no se
      puede hacer bien sin modelos pesados; aquí se busca dónde entra la parte fuerte y
      dónde empieza el final, que suele coincidir y deja el punto cerca para afinarlo de oído.

CÓMO: ffmpeg decodifica una sola vez a mono 8 kHz (como marcas.py, que usa 2 kHz); todo lo
demás es Python puro, sin numpy ni librerías nuevas.
"""

import logging
import math
import subprocess
from array import array
from typing import Optional

from backend.audio import marcas

log = logging.getLogger("Analisis")

TASA = 8000                    # Hz
FRAME = 0.01                   # s: la envolvente del ritmo se mide cada 10 ms
BPM_MIN, BPM_MAX = 70, 180     # el ritmo se lleva a este rango (se corrigen mitades y dobles)
ENERGIA_BPM = (75, 95, 115, 135)   # <75 → 1 · <95 → 2 · <115 → 3 · <135 → 4 · resto → 5


def decodificar(path, ffmpeg: str, max_seg: int = 900, timeout: float = 240) -> Optional[array]:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-v", "error", "-t", str(max_seg), "-i", str(path),
             "-ac", "1", "-ar", str(TASA), "-f", "s16le", "-"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("[Analisis] ffmpeg falló con %s: %s", path, exc)
        return None
    if proc.returncode != 0 or len(proc.stdout) < TASA * 4:
        return None
    m = array("h")
    m.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    return m


def envolvente(muestras: array, tasa: int = TASA, frame: float = FRAME) -> list[float]:
    """Energía (RMS lineal, 0..1) de cada ventana de `frame` segundos."""
    n = max(1, int(tasa * frame))
    out = []
    for i in range(0, len(muestras) - n + 1, n):
        t = muestras[i:i + n]
        out.append(math.sqrt(sum(x * x for x in t) / n) / 32768.0)
    return out


def a_db(env: list[float], por_nivel: int = 5) -> list[float]:
    """Niveles en dBFS cada `por_nivel` frames (5 × 10 ms = los 50 ms que usa marcas.calcular)."""
    out = []
    for i in range(0, len(env) - por_nivel + 1, por_nivel):
        p = sum(e * e for e in env[i:i + por_nivel]) / por_nivel
        rms = math.sqrt(p)
        out.append(20 * math.log10(rms) if rms > 1e-6 else -120.0)
    return out


def estimar_bpm(env: list[float], fps: float = 1 / FRAME) -> Optional[dict]:
    """
    Ritmo por autocorrelación de los golpes (subidas bruscas de energía).
    Devuelve {bpm, confianza} o None si no hay ritmo claro.
    """
    if len(env) < fps * 20:
        return None
    # Tramo del medio (hasta 90 s): sin la introducción ni el final, que despistan
    a = int(len(env) * 0.1)
    b = min(len(env) - int(len(env) * 0.1), a + int(fps * 90))
    seg = env[a:b]
    comp = [math.log1p(80 * x) for x in seg]                       # comprime: los golpes suaves también cuentan
    media = 0.0
    k = int(fps * 0.5)
    onset = []
    for i in range(1, len(comp)):
        media = comp[max(0, i - k):i + 1]
        onset.append(max(0.0, comp[i] - comp[i - 1] - 0.5 * (sum(media) / len(media) - comp[i - 1])))
    n = len(onset)
    if n < fps * 15 or max(onset) <= 1e-6:
        return None
    lag_min, lag_max = int(fps * 60 / 200), int(fps * 60 / 55)
    ac = {}
    for lag in range(lag_min, min(lag_max * 2, n // 2) + 1):
        s = 0.0
        for i in range(n - lag):
            s += onset[i] * onset[i + lag]
        ac[lag] = s / (n - lag)
    cand = [(v, l) for l, v in ac.items() if lag_min <= l <= lag_max]
    if not cand:
        return None
    # Un ritmo real se repite al doble y a la mitad: se suma su refuerzo para elegir el pulso
    def puntaje(l):
        return ac[l] + 0.5 * ac.get(l * 2, 0) + 0.25 * ac.get(l // 2, 0)
    mejor = max((l for _, l in cand), key=puntaje)
    # Si el pulso al doble de velocidad es casi tan marcado, ese es el ritmo (evita dar 75 a un tema de 150)
    ambiguo = False
    while mejor // 2 >= lag_min and ac.get(mejor // 2, 0) >= 0.85 * ac[mejor] and 60 * fps / (mejor // 2) <= BPM_MAX:
        mejor //= 2
    if ac.get(mejor * 2, 0) >= 0.5 * ac[mejor] or ac.get(mejor // 2, 0) >= 0.5 * ac[mejor]:
        ambiguo = True                                # el ritmo podría ser la mitad o el doble: no se afirma con seguridad
    # afinado parabólico alrededor del pico
    l = mejor
    if l - 1 in ac and l + 1 in ac:
        y0, y1, y2 = ac[l - 1], ac[l], ac[l + 1]
        d = (y0 - 2 * y1 + y2)
        l = l + (0.5 * (y0 - y2) / d if d else 0)
    bpm = 60 * fps / l
    while bpm > BPM_MAX:
        bpm /= 2
    while bpm < BPM_MIN:
        bpm *= 2
    promedio = sum(v for v in ac.values()) / len(ac)
    prom = ac[mejor] / promedio if promedio > 0 else 0
    conf = "alta" if prom > 2.6 else "media" if prom > 1.8 else "baja"
    if ambiguo and conf == "alta":
        conf = "media"
    return {"bpm": round(bpm), "confianza": conf}


def energia_de_bpm(bpm: float) -> int:
    for n, tope in enumerate(ENERGIA_BPM, start=1):
        if bpm < tope:
            return n
    return 5


def _suave_1s(niveles: list[float]) -> list[float]:
    k = int(1 / marcas.VENTANA)
    return [sum(niveles[i:i + k]) / k for i in range(0, len(niveles) - k + 1, k)]


def estimar_intro_outro(niveles: list[float], base: dict, bpm: Optional[int]) -> dict:
    """
    Intro: cuándo entra la parte fuerte (≈ la voz). Outro: cuándo empieza el final.
    `niveles` son dBFS cada 50 ms y `base` el resultado de marcas.calcular().
    """
    res = {"cue_intro": None, "cue_outro": None}
    if not base:
        return res
    normal = base["volumen_normal_db"]
    s = _suave_1s(niveles)                                   # un nivel por segundo
    ini = int(base["cue_inicio"] or 0)
    fin = int(base["fin_sonido"])
    # Intro: el primer momento, en los primeros 60 s, en que el nivel llega a «normal − 3 dB» y se sostiene 4 s
    for t in range(ini + 3, min(60, len(s) - 4)):
        if all(x >= normal - 3 for x in s[t:t + 4]) and (s[ini + 1] < normal - 3 or t > ini + 8):
            intro = float(t)
            if bpm:                                          # se pega al compás: grupos de 8 tiempos desde el inicio
                comp = 8 * 60 / bpm
                c = round((intro - ini) / comp) * comp + ini
                if abs(c - intro) <= 1.0 and c > ini + 2:
                    intro = c
            res["cue_intro"] = round(intro, 1)
            break
    # Outro: dónde empieza el final = última vez que el nivel está en «normal − 3 dB» antes de caer
    for t in range(min(fin, len(s)) - 1, ini + 10, -1):
        if s[t] >= normal - 3:
            if 3 <= fin - t <= 75:
                res["cue_outro"] = float(t + 1)
            break
    return res


def analizar(path, ffmpeg: str) -> Optional[dict]:
    """
    {bpm, bpm_confianza, energia, cue_inicio, cue_mezcla, cue_intro, cue_outro}  (todo opcional) o None si no se pudo decodificar.
    """
    m = decodificar(path, ffmpeg)
    if not m:
        return None
    env = envolvente(m)
    niveles = a_db(env)
    base = marcas.calcular(niveles)
    r = estimar_bpm(env)
    bpm = r["bpm"] if r else None
    io_ = estimar_intro_outro(niveles, base, bpm) if base else {"cue_intro": None, "cue_outro": None}
    return {
        "bpm": bpm, "bpm_confianza": r["confianza"] if r else None, "energia": energia_de_bpm(bpm) if bpm else None,
        "cue_inicio": base["cue_inicio"] if base else None, "cue_mezcla": base["cue_mezcla"] if base else None,
        "cue_intro": io_["cue_intro"], "cue_outro": io_["cue_outro"],
        "duracion_s": round(len(m) / TASA, 1),
    }
